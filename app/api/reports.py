import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, func, exists
from sqlalchemy.orm import selectinload, aliased, joinedload

from app.db.session import get_db
from app.db.models import (
  Company,
  Project,
  ProjectStatus,
  User,
  Report,
  ReportField,
  ReportTemplate,
  ReportTemplateField,
  ReportStatus,
  ReportImageLink,
  Image,
  ImageTag,
  ImageTagLink,
  CompanyReportTemplate,
  ProjectGuestLink,
  LineConversation,
  LineMessage,
  LineMessageType,
  ReportProjectLink,
)
from app.schemas.report import (
  ReportCreate, 
  ReportRead,
  ReportWithCompanyAndProjectName,
  ReportDetail,
  ReportTemplateCreate, 
  ReportTemplateRead, 
  ReportUpdate,
  ReportTemplateUpdate,
  ReportSpeechRequest,
  ReportSpeechResponse,
  ShareReportTemplateRequest,
  ReportImageCreate,
  ReportImageUpdate,
  ReportImageTagCreate,
  ReportLineConversationRequest,
)
from app.core.dependencies import (
  get_current_user,
  require_company_member,
  require_company_manager,
  require_project_access,
)
from app.services.openai import (
  transcribe_and_extract_json,
  extract_report_fields_from_line_conversations,
)
from app.services.images import create_image_from_line_message
from app.services.billing import can_use_billed_features
from app.services.s3 import s3_client, BUCKET_NAME

logger = logging.getLogger(__name__)

router = APIRouter(
  prefix="/reports",
  tags=["reports"]
)

@router.get(
  "",
  response_model=list[ReportWithCompanyAndProjectName],
)
async def list_reports(
  q: str | None = None,
  project_id: UUID | None = None,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  company_alias = aliased(Company)

  stmt = (
    select(
      Report,
      company_alias.name.label("company_name"),
      Project.name.label("project_name"),
    )
    .outerjoin(
      company_alias,
      company_alias.id == Report.company_id,
    )
    .outerjoin(
      ReportProjectLink,
      ReportProjectLink.report_id == Report.id,
    )
    .outerjoin(
      Project,
      Project.id == ReportProjectLink.project_id,
    )
  )

  # ---------------------------------------------------------
  # Access control
  #
  # Admins see everything.
  #
  # Other users can see:
  #   1. Reports belonging to their company
  #   2. Reports linked to projects where they are a guest
  # ---------------------------------------------------------

  if current_user.role != "admin":
    guest_access_exists = (
      select(1)
      .select_from(ReportProjectLink)
      .join(
        ProjectGuestLink,
        ProjectGuestLink.project_id == ReportProjectLink.project_id,
      )
      .where(
        ReportProjectLink.report_id == Report.id,
        ProjectGuestLink.user_id == current_user.id,
      )
      .exists()
    )

    stmt = stmt.where(
      or_(
        Report.company_id == current_user.company_id,
        guest_access_exists,
      )
    )

  if q:
    stmt = stmt.where(
      func.lower(Report.name).like(
        f"%{q.lower()}%"
      )
    )

  if project_id:
    stmt = stmt.where(
      ReportProjectLink.project_id == project_id,
    )

  stmt = (
    stmt
    .order_by(Report.updated_at.desc())
    .limit(25)
    .options(
      selectinload(Report.fields),
      selectinload(Report.project_links),
    )
  )

  result = await db.execute(stmt)

  # A Report can have multiple project links, so the SQL query
  # can return multiple rows for the same Report.
  seen = set()
  reports = []

  for report, company_name, project_name in result.all():
    if report.id in seen:
      continue

    seen.add(report.id)

    report.company_name = company_name

    # Convenience field for existing frontend usage.
    # This should NOT be treated as the authoritative
    # representation of the report's project relationships.
    report.project_name = project_name

    report.project_ids = [
      link.project_id
      for link in report.project_links
    ]

    report.fields.sort(key=lambda f: f.order)

    reports.append(report)

  return reports

@router.post(
  "",
  response_model=ReportRead,
  status_code=status.HTTP_201_CREATED,
)
async def create_report(
  payload: ReportCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  # ---------------------------------------------------------
  # Determine the company from the requested relationship.
  #
  # If project_id is supplied, the project determines the
  # company. Otherwise company_id must be supplied directly.
  # ---------------------------------------------------------

  project = None

  if payload.project_id:
    project = await db.get(
      Project,
      payload.project_id,
    )

    if not project:
      raise HTTPException(
        status_code=404,
        detail="Project not found",
      )

    company_id = project.company_id

    # -------------------------------------------------------
    # Project reports require the user to have access to the
    # specific project.
    # -------------------------------------------------------

    if current_user.role != "admin":
      await require_project_access(
        current_user,
        payload.project_id,
        company_id,
        db,
      )

  else:
    if not payload.company_id:
      raise HTTPException(
        status_code=400,
        detail="company_id is required when project_id is not provided",
      )

    company_id = payload.company_id

    # -------------------------------------------------------
    # Company-only reports only require company membership.
    # -------------------------------------------------------

    if current_user.role != "admin":
      require_company_member(
        current_user,
        company_id,
      )

  # ---------------------------------------------------------
  # Check billing status.
  # ---------------------------------------------------------

  allowed, reason = await can_use_billed_features(
    company_id,
    db,
  )

  if not allowed and current_user.role != "admin":
    raise HTTPException(
      status_code=402,
      detail=reason,
    )

  # ---------------------------------------------------------
  # Load the report template.
  # ---------------------------------------------------------

  stmt = (
    select(ReportTemplate)
    .where(
      ReportTemplate.id == payload.template_id,
    )
    .options(
      selectinload(ReportTemplate.fields),
    )
  )

  result = await db.execute(stmt)

  template = result.scalar_one_or_none()

  if not template:
    raise HTTPException(
      status_code=404,
      detail="Report template not found",
    )

  # ---------------------------------------------------------
  # Make sure the template is available to this company.
  #
  # Global templates can be used by any company.
  # Company-specific templates require a link to this company.
  # ---------------------------------------------------------

  template_company_link = (
    await db.execute(
      select(CompanyReportTemplate).where(
        CompanyReportTemplate.report_template_id == template.id,
        CompanyReportTemplate.company_id == company_id,
      )
    )
  ).scalar_one_or_none()

  if not template_company_link and not template.is_global:
    raise HTTPException(
      status_code=403,
      detail="Template does not belong to this company",
    )

  # ---------------------------------------------------------
  # Non-admin users can only create reports for active projects.
  #
  # Company-only reports retain the existing requirement that
  # the company has at least one active project.
  # ---------------------------------------------------------

  if current_user.role != "admin":
    if project:
      if project.status != ProjectStatus.active:
        raise HTTPException(
          status_code=403,
          detail="Reports can only be created for active projects",
        )

    else:
      active_project_stmt = (
        select(Project.id)
        .where(
          Project.company_id == company_id,
          Project.status == ProjectStatus.active,
        )
        .limit(1)
      )

      active_project_result = await db.execute(
        active_project_stmt
      )

      if not active_project_result.first():
        raise HTTPException(
          status_code=403,
          detail=(
            "Company must have at least one active project "
            "before creating reports"
          ),
        )

  # ---------------------------------------------------------
  # Create the Report.
  #
  # company_id is directly stored on Report because every
  # report belongs to exactly one company.
  # ---------------------------------------------------------

  report = Report(
    name=payload.name,
    company_id=company_id,
    template_id=template.id,
    created_at=datetime.now(timezone.utc),
    updated_at=datetime.now(timezone.utc),
    created_by=current_user.id,
  )

  db.add(report)

  await db.flush()

  # ---------------------------------------------------------
  # Create the optional project relationship separately.
  #
  # This keeps Report extensible. Future relationships can
  # follow the same pattern:
  #
  #   ReportOrderLink
  #   ReportCustomerLink
  #   ReportSiteLink
  #   etc.
  # ---------------------------------------------------------

  if payload.project_id:
    db.add(
      ReportProjectLink(
        report_id=report.id,
        project_id=payload.project_id,
      )
    )

  # ---------------------------------------------------------
  # Create fields from the template.
  # ---------------------------------------------------------

  for template_field in template.fields:
    db.add(
      ReportField(
        name=template_field.name,
        description=template_field.description,
        value="",
        report_id=report.id,
        order=template_field.order,
      )
    )

  await db.commit()

  # ---------------------------------------------------------
  # Reload the report with all relationships required by the
  # response.
  #
  # IMPORTANT: project_links must be loaded here. Otherwise
  # project_ids will be empty even though the link was created.
  # ---------------------------------------------------------

  stmt = (
    select(Report)
    .where(
      Report.id == report.id,
    )
    .options(
      selectinload(Report.fields),
      selectinload(Report.image_links)
        .selectinload(ReportImageLink.image),
      selectinload(Report.project_links),
    )
  )

  result = await db.execute(stmt)

  report = result.scalar_one()

  report.fields.sort(key=lambda f: f.order)

  report.photo_count = len(report.image_links)

  # ---------------------------------------------------------
  # Convert the link-table relationship into the API-friendly
  # project_ids representation.
  # ---------------------------------------------------------

  report.project_ids = [
    link.project_id
    for link in report.project_links
  ]

  return report

@router.get(
  "/templates",
  response_model=list[ReportTemplateRead],
)
async def list_report_templates(
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
  company_id: UUID | None = Query(None),
):
  if current_user.role == "admin":
    if company_id:
      company = await db.get(
        Company,
        company_id,
      )

      if not company:
        raise HTTPException(
          status_code=status.HTTP_400_BAD_REQUEST,
          detail=f"Company {company_id} does not exist",
        )

      stmt = (
        select(ReportTemplate)
        .join(
          CompanyReportTemplate,
          CompanyReportTemplate.report_template_id
          == ReportTemplate.id,
        )
        .where(
          CompanyReportTemplate.company_id == company_id,
        )
        .options(
          selectinload(ReportTemplate.fields),
        )
        .order_by(
          ReportTemplate.updated_at.desc(),
        )
      )

    else:
      stmt = (
        select(ReportTemplate)
        .where(
          ReportTemplate.is_global.is_(True),
        )
        .options(
          selectinload(ReportTemplate.fields),
        )
        .order_by(
          ReportTemplate.updated_at.desc(),
        )
      )

    result = await db.execute(stmt)

    templates = result.scalars().all()

    for template in templates:
      template.fields.sort(key=lambda f: f.order)

    return templates

  if current_user.company_id:
    own_company_result = await db.execute(
      select(ReportTemplate)
      .join(
        CompanyReportTemplate,
        CompanyReportTemplate.report_template_id
        == ReportTemplate.id,
      )
      .where(
        CompanyReportTemplate.company_id
        == current_user.company_id,
      )
      .options(
        selectinload(ReportTemplate.fields),
      )
    )

    guest_result = await db.execute(
      select(ReportTemplate)
      .join(
        CompanyReportTemplate,
        CompanyReportTemplate.report_template_id
        == ReportTemplate.id,
      )
      .join(
        Company,
        Company.id == CompanyReportTemplate.company_id,
      )
      .join(
        Project,
        Project.company_id == Company.id,
      )
      .join(
        ProjectGuestLink,
        ProjectGuestLink.project_id == Project.id,
      )
      .where(
        ProjectGuestLink.user_id == current_user.id,
        Company.id != current_user.company_id,
      )
      .options(
        selectinload(ReportTemplate.fields),
      )
    )

    seen = set()
    all_templates = []

    for template in (
      list(own_company_result.scalars().all())
      + list(guest_result.scalars().all())
    ):
      if template.id not in seen:
        seen.add(template.id)
        all_templates.append(template)

  else:
    guest_result = await db.execute(
      select(ReportTemplate)
      .join(
        CompanyReportTemplate,
        CompanyReportTemplate.report_template_id
        == ReportTemplate.id,
      )
      .join(
        Company,
        Company.id == CompanyReportTemplate.company_id,
      )
      .join(
        Project,
        Project.company_id == Company.id,
      )
      .join(
        ProjectGuestLink,
        ProjectGuestLink.project_id == Project.id,
      )
      .where(
        ProjectGuestLink.user_id == current_user.id,
      )
      .options(
        selectinload(ReportTemplate.fields),
      )
    )

    seen = set()
    all_templates = []

    for template in guest_result.scalars().all():
      if template.id not in seen:
        seen.add(template.id)
        all_templates.append(template)

  all_templates = sorted(
    all_templates,
    key=lambda t: t.updated_at,
    reverse=True,
  )

  for template in all_templates:
    template.fields.sort(key=lambda f: f.order)

  return all_templates

@router.post(
  "/templates",
  response_model=ReportTemplateRead,
  status_code=status.HTTP_201_CREATED,
)
async def create_report_template(
  payload: ReportTemplateCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
  company_id: UUID | None = Query(None),
):
  if current_user.role not in {"admin", "manager"}:
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Only admins or managers can create templates",
    )

  if (
    current_user.role == "manager"
    and not current_user.company_id
  ):
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="Manager must belong to a company",
    )

  effective_company_id = (
    company_id
    if current_user.role == "admin"
    else current_user.company_id
  )

  if current_user.role == "admin" and effective_company_id:
    company = await db.get(
      Company,
      effective_company_id,
    )

    if not company:
      raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Company {effective_company_id} does not exist",
      )

  template = ReportTemplate(
    name=payload.name,
    description=payload.description,
    is_global=(
      current_user.role == "admin"
      and not effective_company_id
    ),
    fields=[],
  )

  for field in payload.fields:
    template.fields.append(
      ReportTemplateField(
        name=field.name,
        description=field.description,
        order=field.order,
      )
    )

  db.add(template)

  await db.flush()

  if effective_company_id:
    db.add(
      CompanyReportTemplate(
        company_id=effective_company_id,
        report_template_id=template.id,
      )
    )

  await db.commit()

  stmt = (
    select(ReportTemplate)
    .options(
      selectinload(ReportTemplate.fields),
    )
    .where(
      ReportTemplate.id == template.id,
    )
  )

  result = await db.execute(stmt)

  template = result.scalar_one()

  template.fields.sort(key=lambda f: f.order)

  return template

@router.get(
  "/exports/template/{template_id}",
)
async def export_reports_by_template(
  template_id: UUID,
  project_id: UUID | None = Query(None),
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = (
    select(
      Report,
      Project.name.label("project_name"),
    )
    .outerjoin(
      ReportProjectLink,
      ReportProjectLink.report_id == Report.id,
    )
    .outerjoin(
      Project,
      Project.id == ReportProjectLink.project_id,
    )
    .where(
      Report.template_id == template_id,
    )
    .options(
      selectinload(Report.fields),
      selectinload(Report.image_links)
        .selectinload(ReportImageLink.image),
      joinedload(Report.creator),
    )
  )

  # ---------------------------------------------------------
  # Access filtering happens in SQL rather than calling
  # require_report_access() once per report.
  # ---------------------------------------------------------

  if current_user.role != "admin":
    guest_access_exists = (
      select(1)
      .select_from(ReportProjectLink)
      .join(
        ProjectGuestLink,
        ProjectGuestLink.project_id == ReportProjectLink.project_id,
      )
      .where(
        ReportProjectLink.report_id == Report.id,
        ProjectGuestLink.user_id == current_user.id,
      )
      .exists()
    )

    stmt = stmt.where(
      or_(
        Report.company_id == current_user.company_id,
        guest_access_exists,
      )
    )

  if project_id:
    stmt = stmt.where(
      ReportProjectLink.project_id == project_id,
    )

  stmt = stmt.order_by(
    Report.created_at.desc(),
  )

  result = await db.execute(stmt)

  rows = result.all()

  # ---------------------------------------------------------
  # A report can have multiple project links.
  # Deduplicate before exporting.
  # ---------------------------------------------------------

  seen = set()
  filtered_reports = []

  for report, project_name in rows:
    if report.id in seen:
      continue

    seen.add(report.id)

    filtered_reports.append(
      (report, project_name)
    )

  field_names: set[str] = set()

  for report, _project_name in filtered_reports:
    for field in report.fields:
      field_names.add(field.name)

  sorted_field_names = sorted(field_names)

  export_rows: list[dict] = []

  for report, project_name in filtered_reports:
    row = {
      "報告書名": report.name,
    }

    if not project_id:
      row["プロジェクト"] = (
        project_name or ""
      )

    row["作成日時"] = report.created_at.strftime(
      "%Y-%m-%d %H:%M"
    )

    row["作成者"] = " ".join(
      filter(
        None,
        [
          (
            report.creator.first_name
            if report.creator
            else None
          ),
          (
            report.creator.last_name
            if report.creator
            else None
          ),
        ],
      )
    )

    row["画像数"] = len(report.image_links)

    for field_name in sorted_field_names:
      row[field_name] = ""

    for field in report.fields:
      row[field.name] = field.value or ""

    export_rows.append(row)

  return export_rows

@router.get(
  "/{report_id}",
  response_model=ReportDetail,
)
async def get_report(
  report_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = (
    select(Report)
    .where(
      Report.id == report_id,
    )
    .options(
      selectinload(Report.fields),
      selectinload(Report.image_links)
        .selectinload(ReportImageLink.image),
      selectinload(Report.project_links),
      joinedload(Report.creator),
    )
  )

  result = await db.execute(stmt)

  report = result.scalar_one_or_none()

  if report is None:
    raise HTTPException(
      status_code=404,
      detail="Report not found",
    )

  # All Report authorization now goes through this helper.
  await require_report_access(
    current_user,
    report,
    db,
  )

  report.fields.sort(key=lambda f: f.order)

  report.photo_count = len(report.image_links)

  report.project_ids = [
    link.project_id
    for link in report.project_links
  ]

  report.disabled = False

  # ---------------------------------------------------------
  # Determine whether the report is disabled.
  #
  # If the report has project links, it is disabled if all
  # associated projects are inactive.
  #
  # Company-only reports are disabled when the company has
  # no active projects.
  # ---------------------------------------------------------

  if report.project_links:
    project_ids = [
      link.project_id
      for link in report.project_links
    ]

    active_project_stmt = (
      select(Project.id)
      .where(
        Project.id.in_(project_ids),
        Project.status == ProjectStatus.active,
      )
      .limit(1)
    )

    active_project_result = await db.execute(
      active_project_stmt
    )

    if not active_project_result.first():
      report.disabled = True

  else:
    active_project_stmt = (
      select(Project.id)
      .where(
        Project.company_id == report.company_id,
        Project.status == ProjectStatus.active,
      )
      .limit(1)
    )

    active_project_result = await db.execute(
      active_project_stmt
    )

    if not active_project_result.first():
      report.disabled = True

  # ---------------------------------------------------------
  # If your response still expects company_name/project_name,
  # populate those values here.
  # ---------------------------------------------------------

  company = await db.get(
    Company,
    report.company_id,
  )

  report.company_name = (
    company.name
    if company
    else None
  )

  if report.project_links:
    first_project_id = report.project_links[0].project_id

    project = await db.get(
      Project,
      first_project_id,
    )

    report.project_name = (
      project.name
      if project
      else None
    )
  else:
    report.project_name = None

  return report

@router.patch(
  "/{report_id}",
  response_model=ReportRead,
)
async def update_report(
  report_id: UUID,
  payload: ReportUpdate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = (
    select(Report)
    .where(
      Report.id == report_id,
    )
    .options(
      selectinload(Report.fields),
      selectinload(Report.image_links)
        .selectinload(ReportImageLink.image),
      selectinload(Report.project_links),
    )
  )

  result = await db.execute(stmt)

  report = result.scalar_one_or_none()

  if not report:
    raise HTTPException(
      status_code=404,
      detail="Report not found",
    )

  # ---------------------------------------------------------
  # Billing check is now based directly on Report.company_id.
  # ---------------------------------------------------------

  allowed, reason = await can_use_billed_features(
    report.company_id,
    db,
  )

  if not allowed and current_user.role != "admin":
    raise HTTPException(
      status_code=402,
      detail=reason,
    )

  # ---------------------------------------------------------
  # Centralized report authorization.
  #
  # This handles:
  # - company membership
  # - project access
  # - project guest access
  # - future report relationships
  # ---------------------------------------------------------

  await require_report_access(
    current_user,
    report,
    db,
  )

  # ---------------------------------------------------------
  # Closed reports can only have their status changed by
  # managers/admins.
  # ---------------------------------------------------------

  if report.status != ReportStatus.open:
    if current_user.role not in {"admin", "manager"}:
      raise HTTPException(
        status_code=403,
        detail="This report is closed and cannot be edited",
      )

    non_status_changes = (
      payload.name is not None
      or payload.field_values is not None
    )

    if non_status_changes:
      raise HTTPException(
        status_code=403,
        detail="A closed report can only have its status changed",
      )

  # ---------------------------------------------------------
  # A report associated with projects can only be edited if
  # at least one linked project is active.
  #
  # A company-only report can only be edited if the company
  # has at least one active project.
  # ---------------------------------------------------------

  if current_user.role != "admin":
    if report.project_links:
      project_ids = [
        link.project_id
        for link in report.project_links
      ]

      active_project_stmt = (
        select(Project.id)
        .where(
          Project.id.in_(project_ids),
          Project.status == ProjectStatus.active,
        )
        .limit(1)
      )

      active_project_result = await db.execute(
        active_project_stmt
      )

      if not active_project_result.first():
        raise HTTPException(
          status_code=403,
          detail="Reports for inactive projects cannot be updated",
        )

    else:
      active_project_stmt = (
        select(Project.id)
        .where(
          Project.company_id == report.company_id,
          Project.status == ProjectStatus.active,
        )
        .limit(1)
      )

      active_project_result = await db.execute(
        active_project_stmt
      )

      if not active_project_result.first():
        raise HTTPException(
          status_code=403,
          detail="Company must have at least one active project to update reports",
        )

  # ---------------------------------------------------------
  # Apply requested changes.
  # ---------------------------------------------------------

  if payload.name is not None:
    report.name = payload.name

  if payload.status is not None:
    if current_user.role not in {"admin", "manager"}:
      raise HTTPException(
        status_code=403,
        detail="Only managers and admins can change report status",
      )

    if (
      current_user.role == "manager"
      and current_user.company_id != report.company_id
    ):
      raise HTTPException(
        status_code=403,
        detail="Cannot update status for reports outside your company",
      )

    report.status = payload.status

  if payload.field_values:
    field_map = {
      field.id: field
      for field in report.fields
    }

    for field_id, value in payload.field_values.items():
      if field_id not in field_map:
        raise HTTPException(
          status_code=400,
          detail=f"Field {field_id} does not belong to this report",
        )

      field_map[field_id].value = value

  report.updated_at = datetime.now(timezone.utc)

  await db.commit()

  # ---------------------------------------------------------
  # Re-fetch so the response contains the current persisted
  # state, including all project links.
  # ---------------------------------------------------------

  stmt = (
    select(Report)
    .where(
      Report.id == report.id,
    )
    .options(
      selectinload(Report.fields),
      selectinload(Report.image_links)
        .selectinload(ReportImageLink.image),
      selectinload(Report.project_links),
    )
  )

  result = await db.execute(stmt)

  report = result.scalar_one()

  report.fields.sort(key=lambda f: f.order)

  report.photo_count = len(report.image_links)

  # ---------------------------------------------------------
  # Populate relationship IDs for the API response.
  #
  # This is important because project_links is an ORM
  # relationship, while project_ids is the API representation
  # of that relationship.
  # ---------------------------------------------------------

  report.project_ids = [
    link.project_id
    for link in report.project_links
  ]

  return report

@router.get("/{report_id}/images")
async def list_report_images(
  report_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = (
    select(Report)
    .where(
      Report.id == report_id,
    )
  )

  result = await db.execute(stmt)

  report = result.scalar_one_or_none()

  if not report:
    raise HTTPException(
      status_code=404,
      detail="Report not found",
    )

  await require_report_access(
    current_user,
    report,
    db,
  )

  stmt_images = (
    select(Image)
    .join(
      ReportImageLink,
      ReportImageLink.image_id == Image.id,
    )
    .where(
      ReportImageLink.report_id == report_id,
    )
    .options(
      selectinload(Image.tag_links)
        .selectinload(ImageTagLink.tag),
    )
  )

  result = await db.execute(stmt_images)

  images = result.scalars().all()

  image_list = []

  for img in images:
    download_url = s3_client.generate_presigned_url(
      "get_object",
      Params={
        "Bucket": BUCKET_NAME,
        "Key": img.image_url,
      },
      ExpiresIn=86400,
    )

    tags = [
      {
        "tag_id": link.tag.id,
        "link_id": link.id,
        "name": link.tag.name,
      }
      for link in img.tag_links
    ]

    image_list.append({
      "id": img.id,
      "report_id": report_id,
      "status": img.status,
      "download_url": download_url,
      "created_at": img.created_at,
      "created_by": img.created_by,
      "width": img.width,
      "height": img.height,
      "description": img.description,
      "tags": tags,
    })

  return image_list

@router.post("/{report_id}/images")
async def create_report_image(
  report_id: UUID,
  payload: ReportImageCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = (
    select(Report)
    .where(
      Report.id == report_id,
    )
  )

  result = await db.execute(stmt)

  report = result.scalar_one_or_none()

  if report is None:
    raise HTTPException(
      status_code=404,
      detail="Report not found",
    )

  # CHANGED:
  # company_id is directly available on Report.
  company_id = report.company_id

  allowed, reason = await can_use_billed_features(
    company_id,
    db,
  )

  if not allowed and current_user.role != "admin":
    raise HTTPException(
      status_code=402,
      detail=reason,
    )

  require_report_open(report)

  # CHANGED:
  # Centralized access check.
  await require_report_access(
    current_user,
    report,
    db,
  )

  image_id = uuid4()

  key = f"images/{image_id}.jpg"

  upload_url = s3_client.generate_presigned_url(
    "put_object",
    Params={
      "Bucket": BUCKET_NAME,
      "Key": key,
      "ContentType": "image/jpeg",
      "CacheControl": "public, max-age=31536000, immutable",
    },
    ExpiresIn=300,
  )

  download_url = s3_client.generate_presigned_url(
    "get_object",
    Params={
      "Bucket": BUCKET_NAME,
      "Key": key,
    },
    ExpiresIn=86400,
  )

  image = Image(
    id=image_id,
    company_id=company_id,
    created_by=current_user.id,
    image_url=key,
    status="pending",
    description=None,
    width=payload.width,
    height=payload.height,
  )

  db.add(image)

  await db.flush()

  db.add(
    ReportImageLink(
      report_id=report.id,
      image_id=image.id,
    )
  )

  await db.commit()

  await db.refresh(image)

  return {
    "id": image_id,
    "status": "pending",
    "upload_url": upload_url,
    "download_url": download_url,
    "created_at": image.created_at,
    "width": payload.width,
    "height": payload.height,
    "tags": [],
  }

@router.get(
  "/{report_id}/images/{image_id}/status"
)
async def get_report_image_status(
  report_id: UUID,
  image_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = (
    select(Report)
    .where(
      Report.id == report_id,
    )
  )

  result = await db.execute(stmt)

  report = result.scalar_one_or_none()

  if not report:
    raise HTTPException(
      status_code=404,
      detail="Report not found",
    )

  # CHANGED:
  await require_report_access(
    current_user,
    report,
    db,
  )

  stmt = (
    select(Image)
    .join(
      ReportImageLink,
      ReportImageLink.image_id == Image.id,
    )
    .where(
      Image.id == image_id,
      ReportImageLink.report_id == report_id,
    )
  )

  result = await db.execute(stmt)

  image = result.scalar_one_or_none()

  if not image:
    raise HTTPException(
      status_code=404,
      detail="Image not found",
    )

  if image.status != "completed":
    return {
      "id": image.id,
      "status": image.status,
      "description": image.description,
      "tags": [],
    }

  stmt_tags = (
    select(ImageTagLink)
    .where(
      ImageTagLink.image_id == image_id,
    )
    .options(
      selectinload(ImageTagLink.tag),
    )
  )

  result = await db.execute(stmt_tags)

  links = result.scalars().all()

  tags = [
    {
      "tag_id": link.tag.id,
      "link_id": link.id,
      "name": link.tag.name,
    }
    for link in links
  ]

  return {
    "id": image.id,
    "status": image.status,
    "description": image.description,
    "tags": tags,
  }

@router.patch(
  "/{report_id}/images/{image_id}",
)
async def update_report_image(
  report_id: UUID,
  image_id: UUID,
  payload: ReportImageUpdate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = (
    select(Report)
    .where(
      Report.id == report_id,
    )
  )

  result = await db.execute(stmt)

  report = result.scalar_one_or_none()

  if not report:
    raise HTTPException(
      status_code=404,
      detail="Report not found",
    )

  company_id = report.company_id

  allowed, reason = await can_use_billed_features(
    company_id,
    db,
  )

  if not allowed and current_user.role != "admin":
    raise HTTPException(
      status_code=402,
      detail=reason,
    )

  require_report_open(report)

  await require_report_access(
    current_user,
    report,
    db,
  )

  stmt = (
    select(Image)
    .join(
      ReportImageLink,
      ReportImageLink.image_id == Image.id,
    )
    .where(
      Image.id == image_id,
      ReportImageLink.report_id == report_id,
    )
    .options(
      selectinload(Image.tag_links)
        .selectinload(ImageTagLink.tag),
    )
  )

  result = await db.execute(stmt)

  image = result.scalar_one_or_none()

  if not image:
    raise HTTPException(
      status_code=404,
      detail="Image not found",
    )

  if payload.description is not None:
    image.description = payload.description

  await db.commit()

  tags = [
    {
      "tag_id": link.tag.id,
      "link_id": link.id,
      "name": link.tag.name,
    }
    for link in image.tag_links
  ]

  return {
    "id": image.id,
    "report_id": report_id,
    "description": image.description,
    "status": image.status,
    "width": image.width,
    "height": image.height,
    "created_at": image.created_at,
    "tags": tags,
  }

@router.post(
  "/{report_id}/images/{image_id}/tags",
  status_code=status.HTTP_201_CREATED,
)
async def create_report_image_tag(
  report_id: UUID,
  image_id: UUID,
  payload: ReportImageTagCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = (
    select(Report)
    .where(
      Report.id == report_id,
    )
  )

  result = await db.execute(stmt)

  report = result.scalar_one_or_none()

  if not report:
    raise HTTPException(
      status_code=404,
      detail="Report not found",
    )

  company_id = report.company_id

  allowed, reason = await can_use_billed_features(
    company_id,
    db,
  )

  if not allowed and current_user.role != "admin":
    raise HTTPException(
      status_code=402,
      detail=reason,
    )

  require_report_open(report)

  await require_report_access(
    current_user,
    report,
    db,
  )

  stmt = (
    select(Image)
    .join(
      ReportImageLink,
      ReportImageLink.image_id == Image.id,
    )
    .where(
      Image.id == image_id,
      ReportImageLink.report_id == report_id,
    )
  )

  result = await db.execute(stmt)

  image = result.scalar_one_or_none()

  if not image:
    raise HTTPException(
      status_code=404,
      detail="Image not found",
    )

  stmt = select(ImageTag).where(
    ImageTag.id == payload.tag_id,
    ImageTag.company_id == company_id,
  )

  result = await db.execute(stmt)

  tag = result.scalar_one_or_none()

  if not tag:
    raise HTTPException(
      status_code=404,
      detail="Tag not found",
    )

  stmt = select(ImageTagLink).where(
    ImageTagLink.image_id == image_id,
    ImageTagLink.tag_id == payload.tag_id,
  )

  result = await db.execute(stmt)

  existing = result.scalar_one_or_none()

  if existing:
    raise HTTPException(
      status_code=409,
      detail="Tag already attached to this image",
    )

  link = ImageTagLink(
    id=uuid4(),
    image_id=image_id,
    tag_id=payload.tag_id,
  )

  db.add(link)

  await db.commit()

  return {
    "tag_id": tag.id,
    "link_id": link.id,
    "name": tag.name,
  }

@router.delete(
  "/{report_id}/images/{image_id}/tags/{link_id}",
  status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_report_image_tag(
  report_id: UUID,
  image_id: UUID,
  link_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = (
    select(Report)
    .where(
      Report.id == report_id,
    )
  )

  result = await db.execute(stmt)

  report = result.scalar_one_or_none()

  if not report:
    raise HTTPException(
      status_code=404,
      detail="Report not found",
    )

  company_id = report.company_id

  allowed, reason = await can_use_billed_features(
    company_id,
    db,
  )

  if not allowed and current_user.role != "admin":
    raise HTTPException(
      status_code=402,
      detail=reason,
    )

  require_report_open(report)

  await require_report_access(
    current_user,
    report,
    db,
  )

  stmt = (
    select(Image)
    .join(
      ReportImageLink,
      ReportImageLink.image_id == Image.id,
    )
    .where(
      Image.id == image_id,
      ReportImageLink.report_id == report_id,
    )
  )

  result = await db.execute(stmt)

  image = result.scalar_one_or_none()

  if not image:
    raise HTTPException(
      status_code=404,
      detail="Image not found",
    )

  stmt = select(ImageTagLink).where(
    ImageTagLink.id == link_id,
    ImageTagLink.image_id == image_id,
  )

  result = await db.execute(stmt)

  link = result.scalar_one_or_none()

  if not link:
    raise HTTPException(
      status_code=404,
      detail="Tag link not found",
    )

  await db.delete(link)

  await db.commit()

  return None

@router.post(
  "/{report_id}/speech",
  response_model=ReportSpeechResponse,
)
async def report_speech(
  report_id: UUID,
  payload: ReportSpeechRequest,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = (
    select(Report)
    .where(
      Report.id == report_id,
    )
    .options(
      selectinload(Report.fields),
    )
  )

  result = await db.execute(stmt)

  report = result.scalar_one_or_none()

  if report is None:
    raise HTTPException(
      status_code=404,
      detail="Report not found",
    )

  allowed, reason = await can_use_billed_features(
    report.company_id,
    db,
  )

  if not allowed and current_user.role != "admin":
    raise HTTPException(
      status_code=402,
      detail=reason,
    )

  require_report_open(report)

  await require_report_access(
    current_user,
    report,
    db,
  )

  try:
    changed_fields = await transcribe_and_extract_json(
      speech_text=payload.text,
      fields=report.fields,
      output_language=payload.output_language,
    )

    return ReportSpeechResponse(
      field_values=changed_fields,
    )

  except Exception as e:
    raise HTTPException(
      status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
      detail=f"Error extracting JSON: {str(e)}",
    )

@router.post(
  "/{report_id}/line-conversations",
  response_model=ReportSpeechResponse,
)
async def report_line_conversations(
  report_id: UUID,
  payload: ReportLineConversationRequest,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = (
    select(Report)
    .where(
      Report.id == report_id,
    )
    .options(
      selectinload(Report.fields),
    )
  )

  result = await db.execute(stmt)

  report = result.scalar_one_or_none()

  if report is None:
    raise HTTPException(
      status_code=404,
      detail="Report not found",
    )

  allowed, reason = await can_use_billed_features(
    report.company_id,
    db,
  )

  if not allowed and current_user.role != "admin":
    raise HTTPException(
      status_code=402,
      detail=reason,
    )

  require_report_open(report)

  await require_report_access(
    current_user,
    report,
    db,
  )

  conversation_segments = []

  for conversation_range in payload.conversations:
    conversation_result = await db.execute(
      select(LineConversation)
      .options(
        selectinload(LineConversation.project),
      )
      .where(
        LineConversation.id == conversation_range.conversation_id,
        LineConversation.company_id == report.company_id,
      )
    )

    conversation = conversation_result.scalar_one_or_none()

    if not conversation:
      raise HTTPException(
        status_code=404,
        detail=(
          f"Conversation "
          f"{conversation_range.conversation_id} "
          f"not found"
        ),
      )

    messages_result = await db.execute(
      select(LineMessage)
      .where(
        LineMessage.conversation_id == conversation.id,
        func.coalesce(
          LineMessage.line_timestamp,
          LineMessage.created_at,
        ) >= conversation_range.start_time,
        func.coalesce(
          LineMessage.line_timestamp,
          LineMessage.created_at,
        ) <= conversation_range.end_time,
      )
      .order_by(
        LineMessage.line_timestamp.desc().nullslast(),
        LineMessage.created_at.desc(),
      )
      .limit(500)
    )

    messages = list(
      reversed(
        messages_result.scalars().all()
      )
    )

    logger.info(
      "Retrieved %d messages from conversation '%s' (%s)",
      len(messages),
      conversation.name,
      conversation.id,
    )

    for message in messages:
      logger.info(
        "[%s] %s: %s",
        message.line_timestamp
        or message.created_at,
        message.line_user_id
        or message.sender_line_user_id,
        message.text,
      )

    conversation_segments.append(
      (
        conversation,
        messages,
      )
    )

  try:
    await sync_report_line_images(
      report=report,
      conversation_segments=conversation_segments,
      db=db,
    )

    changed_fields = (
      await extract_report_fields_from_line_conversations(
        conversation_segments=conversation_segments,
        fields=report.fields,
        output_language=payload.output_language,
      )
    )

    return ReportSpeechResponse(
      field_values=changed_fields,
    )

  except Exception:
    logger.exception(
      "Error processing LINE conversations for report %s",
      report.id,
    )

    raise

async def sync_report_line_images(
  *,
  report: Report,
  conversation_segments: list[
    tuple[LineConversation, list[LineMessage]]
  ],
  db: AsyncSession,
):
  for _, messages in conversation_segments:
    for message in messages:

      if message.message_type != LineMessageType.image:
        continue

      image = await create_image_from_line_message(
        line_message_id=message.id,
      )

      if image is None:
        continue

      await _ensure_report_image_link(
        report_id=report.id,
        image_id=image.id,
        db=db,
      )

async def _ensure_report_image_link(
  *,
  report_id: UUID,
  image_id: UUID,
  db: AsyncSession,
):
  existing = await db.execute(
    select(ReportImageLink).where(
      ReportImageLink.report_id == report_id,
      ReportImageLink.image_id == image_id,
    )
  )

  if existing.scalar_one_or_none():
    return

  db.add(
    ReportImageLink(
      report_id=report_id,
      image_id=image_id,
    )
  )

  await db.commit()

@router.delete(
  "/{report_id}",
  status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_report(
  report_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = (
    select(Report)
    .where(
      Report.id == report_id,
    )
    .options(
      selectinload(Report.fields),
    )
  )

  result = await db.execute(stmt)

  report = result.scalar_one_or_none()

  if report is None:
    raise HTTPException(
      status_code=404,
      detail="Report not found",
    )

  allowed, reason = await can_use_billed_features(
    report.company_id,
    db,
  )

  if not allowed and current_user.role != "admin":
    raise HTTPException(
      status_code=402,
      detail=reason,
    )

  await require_report_access(
    current_user,
    report,
    db,
  )

  # ---------------------------------------------------------
  # Admins: can delete anything
  # Managers: can delete reports they can access
  # Everyone else: can only delete reports they created
  # ---------------------------------------------------------

  if current_user.role != "admin":
    if (
      current_user.role != "manager"
      and report.created_by != current_user.id
    ):
      raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="You can only delete reports you created",
      )

  # ReportProjectLink and other cascading link records should
  # be deleted automatically by the Report relationship / FK.
  await db.delete(report)

  await db.commit()

  return None

@router.delete(
  "/{report_id}/images/{image_id}"
)
async def delete_report_image(
  report_id: UUID,
  image_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = (
    select(Report)
    .where(
      Report.id == report_id,
    )
  )

  result = await db.execute(stmt)

  report = result.scalar_one_or_none()

  if report is None:
    raise HTTPException(
      status_code=404,
      detail="Report not found",
    )

  # CHANGED:
  await require_report_access(
    current_user,
    report,
    db,
  )

  require_report_open(report)

  stmt = (
    select(ReportImageLink)
    .where(
      ReportImageLink.report_id == report_id,
      ReportImageLink.image_id == image_id,
    )
  )

  result = await db.execute(stmt)

  link = result.scalar_one_or_none()

  if link is None:
    raise HTTPException(
      status_code=404,
      detail="Image not found",
    )

  await db.delete(link)

  await db.commit()

  # TODO: Delete image from S3 only when no remaining links reference the Image.

  return {
    "success": True,
  }

@router.get(
  "/templates/{report_template_id}",
  response_model=ReportTemplateRead,
)
async def get_report_template(
  report_template_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role == "admin":
    result = await db.execute(
      select(ReportTemplate)
      .where(
        ReportTemplate.id == report_template_id,
      )
      .options(
        selectinload(ReportTemplate.fields),
      )
    )

    template = result.scalar_one_or_none()

  elif current_user.role == "manager":
    result = await db.execute(
      select(ReportTemplate)
      .join(
        CompanyReportTemplate,
        CompanyReportTemplate.report_template_id
        == ReportTemplate.id,
      )
      .where(
        ReportTemplate.id == report_template_id,
        CompanyReportTemplate.company_id
        == current_user.company_id,
      )
      .options(
        selectinload(ReportTemplate.fields),
      )
    )

    template = result.scalar_one_or_none()

  elif current_user.company_id:
    # First check the user's own company.
    result = await db.execute(
      select(ReportTemplate)
      .join(
        CompanyReportTemplate,
        CompanyReportTemplate.report_template_id
        == ReportTemplate.id,
      )
      .where(
        ReportTemplate.id == report_template_id,
        CompanyReportTemplate.company_id
        == current_user.company_id,
      )
      .options(
        selectinload(ReportTemplate.fields),
      )
    )

    template = result.scalar_one_or_none()

    # Then check guest access to other companies' projects.
    if not template:
      result = await db.execute(
        select(ReportTemplate)
        .join(
          CompanyReportTemplate,
          CompanyReportTemplate.report_template_id
          == ReportTemplate.id,
        )
        .join(
          Company,
          Company.id == CompanyReportTemplate.company_id,
        )
        .join(
          Project,
          Project.company_id == Company.id,
        )
        .join(
          ProjectGuestLink,
          ProjectGuestLink.project_id == Project.id,
        )
        .where(
          ReportTemplate.id == report_template_id,
          ProjectGuestLink.user_id == current_user.id,
        )
        .options(
          selectinload(ReportTemplate.fields),
        )
      )

      template = result.scalar_one_or_none()

  else:
    result = await db.execute(
      select(ReportTemplate)
      .join(
        CompanyReportTemplate,
        CompanyReportTemplate.report_template_id
        == ReportTemplate.id,
      )
      .join(
        Company,
        Company.id == CompanyReportTemplate.company_id,
      )
      .join(
        Project,
        Project.company_id == Company.id,
      )
      .join(
        ProjectGuestLink,
        ProjectGuestLink.project_id == Project.id,
      )
      .where(
        ReportTemplate.id == report_template_id,
        ProjectGuestLink.user_id == current_user.id,
      )
      .options(
        selectinload(ReportTemplate.fields),
      )
    )

    template = result.scalar_one_or_none()

  if not template:
    raise HTTPException(
      status_code=404,
      detail="Report template not found",
    )

  template.fields.sort(
    key=lambda f: f.order,
  )

  return template

@router.patch(
  "/templates/{report_template_id}",
  response_model=ReportTemplateRead,
)
async def update_report_template(
  report_template_id: UUID,
  payload: ReportTemplateUpdate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = (
    select(ReportTemplate)
    .options(selectinload(ReportTemplate.fields))
    .where(ReportTemplate.id == report_template_id)
  )
  result = await db.execute(stmt)
  template = result.scalar_one_or_none()

  if not template:
    raise HTTPException(status_code=404, detail="Template not found")

  if template.is_global:
    if current_user.role != "admin":
      raise HTTPException(
        status_code=403,
        detail="Only admins can update global templates",
      )
  else:
    if current_user.role not in {"admin", "manager"}:
      raise HTTPException(status_code=403, detail="Not authorized")

    if current_user.role == "manager":
      link_stmt = select(CompanyReportTemplate).where(
        CompanyReportTemplate.company_id == current_user.company_id,
        CompanyReportTemplate.report_template_id == template.id,
      )
      link = (await db.execute(link_stmt)).scalar_one_or_none()

      if not link:
        raise HTTPException(
          status_code=403,
          detail="Cannot edit template belonging to another company",
        )

  if payload.name is not None:
    template.name = payload.name

  if payload.description is not None:
    template.description = payload.description

  if payload.fields is not None:
    for f in template.fields:
      await db.delete(f)

    await db.flush()

    template.fields = [
      ReportTemplateField(
        name=f.name,
        description=f.description,
        order=f.order
      )
      for f in payload.fields
    ]

  await db.commit()

  result = await db.execute(stmt)
  return result.scalar_one()

@router.post(
  "/templates/share",
  status_code=status.HTTP_201_CREATED,
)
async def share_report_template(
  payload: ShareReportTemplateRequest,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role != "admin":
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Only admins can share report templates",
    )

  template_stmt = select(ReportTemplate).where(
    ReportTemplate.id == payload.template_id
  )
  template = (await db.execute(template_stmt)).scalar_one_or_none()

  if not template:
    raise HTTPException(status_code=404, detail="Report template not found")

  if not template.is_global:
    raise HTTPException(
      status_code=400,
      detail="Only global templates can be shared with companies",
    )

  company_stmt = select(Company).where(Company.id == payload.company_id)
  company = (await db.execute(company_stmt)).scalar_one_or_none()

  if not company:
    raise HTTPException(status_code=404, detail="Company not found")

  existing_stmt = select(CompanyReportTemplate).where(
    CompanyReportTemplate.company_id == payload.company_id,
    CompanyReportTemplate.report_template_id == payload.template_id,
  )
  existing = (await db.execute(existing_stmt)).scalar_one_or_none()

  if existing:
    raise HTTPException(
      status_code=409,
      detail="Company already has this template",
    )

  link = CompanyReportTemplate(
    company_id=payload.company_id,
    report_template_id=payload.template_id,
  )

  db.add(link)
  await db.commit()

  return {"message": "Template shared successfully"}

@router.delete(
  "/templates/{report_template_id}",
  status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_report_template(
  report_template_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = (
    select(ReportTemplate)
    .options(selectinload(ReportTemplate.fields))
    .where(ReportTemplate.id == report_template_id)
  )

  result = await db.execute(stmt)
  template: ReportTemplate | None = result.scalar_one_or_none()

  if not template:
    raise HTTPException(status_code=404, detail="Template not found")

  if template.is_global:
    if current_user.role != "admin":
      raise HTTPException(
        status_code=403,
        detail="Only admins can delete global templates",
      )
  else:
    if current_user.role not in {"admin", "manager"}:
      raise HTTPException(status_code=403, detail="Not authorized")

    if current_user.role == "manager":
      link_stmt = select(CompanyReportTemplate).where(
        CompanyReportTemplate.company_id == current_user.company_id,
        CompanyReportTemplate.report_template_id == template.id,
      )
      link = (await db.execute(link_stmt)).scalar_one_or_none()

      if not link:
        raise HTTPException(
          status_code=403,
          detail="Cannot delete template belonging to another company",
        )

  link_stmt = select(CompanyReportTemplate).where(
    CompanyReportTemplate.report_template_id == template.id
  )
  links = (await db.execute(link_stmt)).scalars().all()

  for link in links:
    await db.delete(link)

  for field in template.fields:
    await db.delete(field)

  await db.delete(template)

  await db.commit()

  return None

def require_report_open(report: Report) -> None:
  if report.status != ReportStatus.open:
    raise HTTPException(
        status_code=403,
        detail="This report is closed and cannot be edited",
    )

async def require_report_access(
  user: User,
  report: Report,
  db: AsyncSession,
):
  if user.role == "admin":
    return

  if user.company_id == report.company_id:
    return

  stmt = (
    select(ReportProjectLink.id)
    .join(
      ProjectGuestLink,
      ProjectGuestLink.project_id == ReportProjectLink.project_id,
    )
    .where(
      ReportProjectLink.report_id == report.id,
      ProjectGuestLink.user_id == user.id,
    )
    .limit(1)
  )

  result = await db.execute(stmt)

  if result.scalar_one_or_none() is None:
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="You do not have access to this report",
    )