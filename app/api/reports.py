import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, func
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
  ReportParentType,
  ReportStatus,
  ReportImageLink,
  Image,
  ImageTag,
  ImageTagLink,
  CompanyReportTemplate,
  ProjectGuestLink,
  LineConversation,
  LineMessage,
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
  require_company_manager,
  require_project_access,
)
from app.services.reports import get_company_id
from app.services.openai import (
  transcribe_and_extract_json,
  extract_report_fields_from_line_conversations,
)
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
  search = f"%{q.lower()}%" if q else None
  company_alias = aliased(Company)

  if current_user.role == "admin":
    stmt = (
      select(
        Report,
        company_alias.name.label("company_name"),
        Project.name.label("project_name"),
      )
      .outerjoin(
        Project,
        (Report.parent_type == ReportParentType.project)
        & (Report.parent_id == Project.id),
      )
      .outerjoin(
        company_alias,
        or_(
          (
            (Report.parent_type == ReportParentType.company)
            & (Report.parent_id == company_alias.id)
          ),
          (
            (Report.parent_type == ReportParentType.project)
            & (Project.company_id == company_alias.id)
          ),
        ),
      )
    )

    if search:
      stmt = stmt.where(
        func.lower(Report.name).like(search)
      )

    if project_id:
      stmt = stmt.where(
        Report.parent_type == ReportParentType.project,
        Report.parent_id == project_id,
      )

    stmt = (
      stmt
      .order_by(Report.updated_at.desc())
      .limit(25)
      .options(selectinload(Report.fields))
    )

    result = await db.execute(stmt)

    rows = result.all()

    reports = []

    for report, company_name, project_name in rows:
      report.company_name = company_name
      report.project_name = project_name
      reports.append(report)

    return reports

  if current_user.company_id:
    company_stmt = (
      select(Report, Project.name.label("project_name"))
      .outerjoin(
        Project,
        (Report.parent_type == ReportParentType.project)
        & (Report.parent_id == Project.id)
      )
      .where(
        or_(
          (Report.parent_type == ReportParentType.company)
          & (Report.parent_id == current_user.company_id),
          (Report.parent_type == ReportParentType.project)
          & (Project.company_id == current_user.company_id),
        )
      )
    )

    if project_id:
      company_stmt = company_stmt.where(
        Report.parent_type == ReportParentType.project,
        Report.parent_id == project_id,
      )

    company_result = await db.execute(
      company_stmt.options(selectinload(Report.fields))
    )

    guest_stmt = (
      select(Report, Project.name.label("project_name"))
      .join(
        Project,
        (Report.parent_type == ReportParentType.project)
        & (Report.parent_id == Project.id)
      )
      .join(ProjectGuestLink, ProjectGuestLink.project_id == Project.id)
      .where(
        ProjectGuestLink.user_id == current_user.id,
        Project.company_id != current_user.company_id,
      )
    )

    if project_id:
      guest_stmt = guest_stmt.where(
        Report.parent_id == project_id
      )

    guest_result = await db.execute(
      guest_stmt.options(selectinload(Report.fields))
    )

    seen = set()
    all_rows = []
    for row in list(company_result.all()) + list(guest_result.all()):
      if row[0].id not in seen:
        seen.add(row[0].id)
        all_rows.append(row)

    if search:
      all_rows = [row for row in all_rows if search.strip("%").lower() in row[0].name.lower()]

    all_rows = sorted(all_rows, key=lambda row: row[0].updated_at, reverse=True)[:25]

  else:
    stmt = (
      select(Report, Project.name.label("project_name"))
      .join(
        Project,
        (Report.parent_type == ReportParentType.project)
        & (Report.parent_id == Project.id)
      )
      .join(ProjectGuestLink, ProjectGuestLink.project_id == Project.id)
      .where(ProjectGuestLink.user_id == current_user.id)
    )

    if project_id:
      stmt = stmt.where(
        Report.parent_id == project_id
      )

    result = await db.execute(
      stmt.options(selectinload(Report.fields))
    )

    all_rows = result.all()

    if search:
      all_rows = [row for row in all_rows if search.strip("%").lower() in row[0].name.lower()]

    all_rows = sorted(all_rows, key=lambda row: row[0].updated_at, reverse=True)[:25]

  reports = []
  for report, project_name in all_rows:
      report.project_name = project_name
      reports.append(report)

  return reports

@router.post("", response_model=ReportRead, status_code=status.HTTP_201_CREATED)
async def create_report(
  payload: ReportCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = (
    select(ReportTemplate)
    .where(ReportTemplate.id == payload.template_id)
    .options(selectinload(ReportTemplate.fields))
  )
  result = await db.execute(stmt)
  template: ReportTemplate = result.scalar_one_or_none()

  if not template:
    raise HTTPException(status_code=404, detail="Report template not found")

  parent_type = template.parent_type if hasattr(template, "parent_type") else ReportParentType.project

  company_id = await get_company_id(
    parent_type=parent_type,
    parent_id=payload.parent_id,
    db=db
  )

  allowed, reason = await can_use_billed_features(company_id, db)
  if not allowed and current_user.role != "admin":
    raise HTTPException(status_code=402, detail=reason)

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

  if parent_type == ReportParentType.project:
    await require_project_access(current_user, payload.parent_id, company_id, db)
  else:
    require_company_manager(current_user, company_id)

  if current_user.role != "admin":
    if parent_type == ReportParentType.project:
      project = await db.get(Project, payload.parent_id)

      if not project:
        raise HTTPException(
          status_code=404,
          detail="Project not found",
        )

      if project.status != ProjectStatus.active:
        raise HTTPException(
          status_code=403,
          detail="Reports can only be created for active projects",
        )

    elif parent_type == ReportParentType.company:
      active_project_stmt = (
        select(Project.id)
        .where(
          Project.company_id == company_id,
          Project.status == ProjectStatus.active,
        )
        .limit(1)
      )

      active_project_result = await db.execute(active_project_stmt)
      active_project = active_project_result.first()

      if not active_project:
        raise HTTPException(
          status_code=403,
          detail="Company must have at least one active project before creating reports",
        )

  report = Report(
    name=payload.name,
    template_id=template.id,
    parent_id=payload.parent_id,
    parent_type=parent_type,
    created_at=datetime.now(timezone.utc),
    updated_at=datetime.now(timezone.utc),
    created_by=current_user.id,
  )

  db.add(report)
  await db.flush()

  for template_field in template.fields:
    report_field = ReportField(
      name=template_field.name,
      description=template_field.description,
      value="",
      report_id=report.id,
      order=template_field.order,
    )
    db.add(report_field)

  await db.commit()
  
  stmt = (
    select(Report)
    .where(Report.id == report.id)
    .options(selectinload(Report.fields))
  )
  result = await db.execute(stmt)
  report = result.scalar_one()

  report.company_id = company_id
  report.fields.sort(key=lambda f: f.order)
  report.photo_count = 0

  return report

@router.get(
  "/templates",
  response_model=list[ReportTemplateRead],
)
async def list_report_templates(
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
  company_id: Optional[UUID] = Query(None),
):
  if current_user.role == "admin":
    if company_id:
      company = await db.get(Company, company_id)
      if not company:
        raise HTTPException(
          status_code=status.HTTP_400_BAD_REQUEST,
          detail=f"Company {company_id} does not exist",
        )  

      stmt = (
        select(ReportTemplate)
        .join(CompanyReportTemplate)
        .where(CompanyReportTemplate.company_id == company_id)
        .options(selectinload(ReportTemplate.fields))
        .order_by(ReportTemplate.updated_at.desc())
      )
    else:
      stmt = (
        select(ReportTemplate)
        .where(ReportTemplate.is_global.is_(True))
        .options(selectinload(ReportTemplate.fields))
        .order_by(ReportTemplate.updated_at.desc())
      )

    result = await db.execute(stmt)
    return result.scalars().all()

  if current_user.company_id:
    own_company_result = await db.execute(
      select(ReportTemplate)
      .join(CompanyReportTemplate)
      .where(CompanyReportTemplate.company_id == current_user.company_id)
      .options(selectinload(ReportTemplate.fields))
    )

    guest_result = await db.execute(
      select(ReportTemplate)
      .join(CompanyReportTemplate, CompanyReportTemplate.report_template_id == ReportTemplate.id)
      .join(Company, Company.id == CompanyReportTemplate.company_id)
      .join(Project, Project.company_id == Company.id)
      .join(ProjectGuestLink, ProjectGuestLink.project_id == Project.id)
      .where(
        ProjectGuestLink.user_id == current_user.id,
        ReportTemplate.parent_type == ReportParentType.project,
        Company.id != current_user.company_id,  # exclude own company, already covered above
      )
      .options(selectinload(ReportTemplate.fields))
    )

    seen = set()
    all_templates = []
    for template in list(own_company_result.scalars().all()) + list(guest_result.scalars().all()):
      if template.id not in seen:
        seen.add(template.id)
        all_templates.append(template)

    all_templates = sorted(all_templates, key=lambda t: t.updated_at, reverse=True)

  else:
    guest_result = await db.execute(
      select(ReportTemplate)
      .join(CompanyReportTemplate, CompanyReportTemplate.report_template_id == ReportTemplate.id)
      .join(Company, Company.id == CompanyReportTemplate.company_id)
      .join(Project, Project.company_id == Company.id)
      .join(ProjectGuestLink, ProjectGuestLink.project_id == Project.id)
      .where(
        ProjectGuestLink.user_id == current_user.id,
        ReportTemplate.parent_type == ReportParentType.project,
      )
      .options(selectinload(ReportTemplate.fields))
    )

    seen = set()
    all_templates = []
    for template in guest_result.scalars().all():
      if template.id not in seen:
        seen.add(template.id)
        all_templates.append(template)

    all_templates = sorted(all_templates, key=lambda t: t.updated_at, reverse=True)

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
  company_id: Optional[UUID] = Query(None),
):
  if current_user.role not in {"admin", "manager"}:
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Only admins or managers can create templates",
    )
  
  if current_user.role == "manager" and not current_user.company_id:
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="Manager must belong to a company",
    )

  effective_company_id = (
    company_id if current_user.role == "admin"
    else current_user.company_id
  )

  if current_user.role == "admin" and effective_company_id:
    company = await db.get(Company, effective_company_id)
    if not company:
      raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Company {effective_company_id} does not exist",
      )

  template = ReportTemplate(
    name=payload.name,
    description=payload.description,
    is_global=current_user.role == "admin" and not effective_company_id,
    parent_type=payload.parent_type,
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
    .options(selectinload(ReportTemplate.fields))
    .where(ReportTemplate.id == template.id)
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
      Project,
      (Report.parent_type == ReportParentType.project)
      & (Report.parent_id == Project.id),
    )
    .where(Report.template_id == template_id)
    .options(
      selectinload(Report.fields),
      selectinload(Report.image_links)
        .selectinload(ReportImageLink.image),
      joinedload(Report.creator),
    )
  )

  if project_id:
    stmt = stmt.where(
      Report.parent_id == project_id,
      Report.parent_type == ReportParentType.project,
    )

  stmt = stmt.order_by(Report.created_at.desc())

  result = await db.execute(stmt)

  rows = result.all()

  filtered_reports: list[
    tuple[Report, str | None]
  ] = []

  for report, project_name in rows:
    company_id = await get_company_id(
      parent_type=report.parent_type,
      parent_id=report.parent_id,
      db=db,
    )

    if current_user.role != "admin":
      require_company_manager(
        current_user,
        company_id,
      )

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
  company_alias = aliased(Company)

  if current_user.role == "admin":
    stmt = (
      select(
        Report,
        company_alias.name.label("company_name"),
        Project.name.label("project_name"),
      )
      .outerjoin(
        Project,
        (Report.parent_type == ReportParentType.project)
        & (Report.parent_id == Project.id),
      )
      .outerjoin(
        company_alias,
        or_(
          (
            (Report.parent_type == ReportParentType.company)
            & (Report.parent_id == company_alias.id)
          ),
          (
            (Report.parent_type == ReportParentType.project)
            & (Project.company_id == company_alias.id)
          ),
        ),
      )
      .where(Report.id == report_id)
      .options(
        selectinload(Report.fields),
        selectinload(Report.image_links)
          .selectinload(ReportImageLink.image),
      )
    )

    result = await db.execute(stmt)

    row = result.one_or_none()

    if row is None:
      raise HTTPException(404, "Report not found")

    report, company_name, project_name = row

    report.company_name = company_name
    report.project_name = project_name

  else:
    stmt = (
      select(
        Report,
        Project.name.label("project_name"),
      )
      .outerjoin(
        Project,
        (Report.parent_type == ReportParentType.project)
        & (Report.parent_id == Project.id),
      )
      .where(Report.id == report_id)
      .options(
        selectinload(Report.fields),
        selectinload(Report.image_links)
          .selectinload(ReportImageLink.image),
      )
    )

    result = await db.execute(stmt)

    row = result.one_or_none()

    if row is None:
      raise HTTPException(404, "Report not found")

    report, project_name = row

    report.project_name = project_name

  company_id = await get_company_id(
    parent_type=report.parent_type,
    parent_id=report.parent_id,
    db=db,
  )

  if current_user.role != "admin":
    if report.parent_type == ReportParentType.project:
      await require_project_access(current_user, report.parent_id, company_id, db)
    else:
      require_company_manager(current_user, company_id)

  report.company_id = company_id
  report.fields.sort(key=lambda f: f.order)
  report.photo_count = len(report.image_links)
  report.disabled = False

  if report.parent_type == ReportParentType.project:
    project = await db.get(Project, report.parent_id)

    if not project or project.status != ProjectStatus.active:
      report.disabled = True

  elif report.parent_type == ReportParentType.company:
    active_project_stmt = (
      select(Project.id)
      .where(
        Project.company_id == company_id,
        Project.status == ProjectStatus.active,
      )
      .limit(1)
    )

    active_project_result = await db.execute(active_project_stmt)
    active_project = active_project_result.first()

    if not active_project:
      report.disabled = True

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
    .where(Report.id == report_id)
    .options(
      selectinload(Report.fields),
      selectinload(Report.image_links)
        .selectinload(ReportImageLink.image),
    )
  )
  result = await db.execute(stmt)
  report: Report | None = result.scalar_one_or_none()

  if not report:
    raise HTTPException(status_code=404, detail="Report not found")

  company_id = await get_company_id(
    parent_type=report.parent_type,
    parent_id=report.parent_id,
    db=db,
  )

  allowed, reason = await can_use_billed_features(company_id, db)
  if not allowed and current_user.role != "admin":
    raise HTTPException(status_code=402, detail=reason)

  if report.status != ReportStatus.open:
    if current_user.role not in {"admin", "manager"}:
      raise HTTPException(
        status_code=403,
        detail="This report is closed and cannot be edited",
      )
    
    non_status_changes = payload.name is not None or payload.field_values is not None
    if non_status_changes:
      raise HTTPException(
        status_code=403,
        detail="A closed report can only have its status changed",
      )

  if current_user.role != "admin":
    if report.parent_type == ReportParentType.project:
      await require_project_access(current_user, report.parent_id, company_id, db)

      project = await db.get(Project, report.parent_id)

      if not project:
        raise HTTPException(
          status_code=404,
          detail="Project not found",
        )

      if project.status != ProjectStatus.active:
        raise HTTPException(
          status_code=403,
          detail="Reports for inactive projects cannot be updated",
        )

    elif report.parent_type == ReportParentType.company:
      require_company_manager(current_user, company_id)

      active_project_stmt = (
        select(Project.id)
        .where(
          Project.company_id == company_id,
          Project.status == ProjectStatus.active,
        )
        .limit(1)
      )

      active_project_result = await db.execute(active_project_stmt)
      active_project = active_project_result.first()

      if not active_project:
        raise HTTPException(
          status_code=403,
          detail="Company must have at least one active project to update reports",
        )

  if payload.name is not None:
    report.name = payload.name

  if payload.status is not None:
    if current_user.role not in {"admin", "manager"}:
      raise HTTPException(
        status_code=403,
        detail="Only managers and admins can change report status",
      )
    if current_user.role == "manager" and current_user.company_id != company_id:
      raise HTTPException(
        status_code=403,
        detail="Cannot update status for reports outside your company",
      ) 
    report.status = payload.status

  report.updated_at = datetime.now(timezone.utc)

  if payload.field_values:
    field_map = {f.id: f for f in report.fields}

    for field_id, value in payload.field_values.items():
      if field_id not in field_map:
        raise HTTPException(
          status_code=400,
          detail=f"Field {field_id} does not belong to this report",
        )

      field_map[field_id].value = value

  await db.commit()

  stmt = (
    select(Report)
    .where(Report.id == report.id)
    .options(
      selectinload(Report.fields),
      selectinload(Report.image_links)
        .selectinload(ReportImageLink.image),
    )
  )
  result = await db.execute(stmt)
  report = result.scalar_one()

  report.company_id = company_id
  report.fields.sort(key=lambda f: f.order)
  report.photo_count = len(report.images)

  return report

@router.get("/{report_id}/images")
async def list_report_images(
  report_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = select(Report).where(Report.id == report_id)
  result = await db.execute(stmt)
  report: Report | None = result.scalar_one_or_none()
  if not report:
    raise HTTPException(404, "Report not found")

  company_id = await get_company_id(
    parent_type=report.parent_type,
    parent_id=report.parent_id,
    db=db,
  )

  if not company_id:
    raise HTTPException(500, "Report does not have an associated company")
  if current_user.role != "admin":
    if report.parent_type == ReportParentType.project:
      await require_project_access(current_user, report.parent_id, company_id, db)
    else:
      require_company_manager(current_user, company_id)

  stmt_images = (
    select(Image)
    .join(ReportImageLink)
    .where(
      ReportImageLink.report_id == report_id,
    )
    .options(
      selectinload(Image.tag_links)
      .selectinload(ImageTagLink.tag)
    )
  )
  result = await db.execute(stmt_images)
  images: list[Image] = result.scalars().all()

  image_list = []

  for img in images:
    download_url = s3_client.generate_presigned_url(
      "get_object",
      Params={"Bucket": BUCKET_NAME, "Key": img.image_url},
      ExpiresIn=86400,
    )

    tags = [
      {
        "tag_id": link.tag.id,
        "link_id": link.id,
        "name": link.tag.name
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
      "tags": tags
    })

  return image_list

@router.post("/{report_id}/images")
async def create_report_image(
  report_id: UUID,
  payload: ReportImageCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = select(Report).where(Report.id == report_id)
  result = await db.execute(stmt)
  report = result.scalar_one_or_none()

  if report is None:
    raise HTTPException(404, "Report not found")

  company_id = await get_company_id(
    parent_type=report.parent_type,
    parent_id=report.parent_id,
    db=db,
  )

  allowed, reason = await can_use_billed_features(company_id, db)
  if not allowed and current_user.role != "admin":
    raise HTTPException(status_code=402, detail=reason)

  require_report_open(report)

  if current_user.role != "admin":
    if report.parent_type == ReportParentType.project:
      await require_project_access(current_user, report.parent_id, company_id, db)
    else:
      require_company_manager(current_user, company_id)

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
    Params={"Bucket": BUCKET_NAME, "Key": key},
    ExpiresIn=86400,
  )

  image = Image(
    id=image_id,
    created_by=current_user.id,
    image_url=key,
    status="pending",
    description=None,
    width=payload.width,
    height=payload.height
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
    "tags": []
  }

@router.get("/{report_id}/images/{image_id}/status")
async def get_report_image_status(
  report_id: UUID,
  image_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = select(Report).where(Report.id == report_id)
  result = await db.execute(stmt)
  report: Report | None = result.scalar_one_or_none()

  if not report:
    raise HTTPException(404, "Report not found")

  company_id = await get_company_id(
    parent_type=report.parent_type,
    parent_id=report.parent_id,
    db=db,
  )

  if not company_id:
    raise HTTPException(500, "Report does not have an associated company")

  if current_user.role != "admin":
    if report.parent_type == ReportParentType.project:
      await require_project_access(current_user, report.parent_id, company_id, db)
    else:
      require_company_manager(current_user, company_id)

  stmt = (
    select(Image)
    .join(ReportImageLink)
    .where(
      Image.id == image_id,
      ReportImageLink.report_id == report_id,
    )
  )

  result = await db.execute(stmt)
  image: Image | None = result.scalar_one_or_none()

  if not image:
    raise HTTPException(404, "Image not found")

  if image.status != "completed":
    return {
      "id": image.id,
      "status": image.status,
      "description": image.description,
      "tags": [],
    }

  stmt_tags = (
    select(ImageTagLink)
    .where(ImageTagLink.image_id == image_id)
    .options(selectinload(ImageTagLink.tag))
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
  stmt = select(Report).where(Report.id == report_id)
  result = await db.execute(stmt)
  report: Report | None = result.scalar_one_or_none()

  if not report:
    raise HTTPException(404, "Report not found")

  company_id = await get_company_id(
    parent_type=report.parent_type,
    parent_id=report.parent_id,
    db=db,
  )

  allowed, reason = await can_use_billed_features(company_id, db)
  if not allowed and current_user.role != "admin":
    raise HTTPException(status_code=402, detail=reason)

  require_report_open(report)

  if current_user.role != "admin":
    if report.parent_type == ReportParentType.project:
      await require_project_access(current_user, report.parent_id, company_id, db)
    else:
      require_company_manager(current_user, company_id)

  stmt = (
    select(Image)
    .join(ReportImageLink)
    .where(
      Image.id == image_id,
      ReportImageLink.report_id == report_id,
    )
    .options(
      selectinload(Image.tag_links).selectinload(ImageTagLink.tag)
    )
  )
  result = await db.execute(stmt)
  image: Image | None = result.scalar_one_or_none()

  if not image:
    raise HTTPException(404, "Image not found")

  if payload.description is not None:
    image.description = payload.description

  await db.commit()

  tags = [
    {
      "tag_id": link.tag.id,
      "link_id": link.id, 
      "name": link.tag.name
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
  stmt = select(Report).where(Report.id == report_id)
  result = await db.execute(stmt)
  report: Report | None = result.scalar_one_or_none()

  if not report:
    raise HTTPException(404, "Report not found")

  company_id = await get_company_id(
    parent_type=report.parent_type,
    parent_id=report.parent_id,
    db=db,
  )

  allowed, reason = await can_use_billed_features(company_id, db)
  if not allowed and current_user.role != "admin":
    raise HTTPException(status_code=402, detail=reason)

  require_report_open(report)

  if current_user.role != "admin":
    if report.parent_type == ReportParentType.project:
      await require_project_access(current_user, report.parent_id, company_id, db)
    else:
      require_company_manager(current_user, company_id)

  stmt = (
    select(Image)
    .join(ReportImageLink)
    .where(
      Image.id == image_id,
      ReportImageLink.report_id == report_id,
    )
  )
  result = await db.execute(stmt)
  image = result.scalar_one_or_none()

  if not image:
    raise HTTPException(404, "Image not found")

  stmt = select(ImageTag).where(
    ImageTag.id == payload.tag_id,
    ImageTag.company_id == company_id,
  )
  result = await db.execute(stmt)
  tag = result.scalar_one_or_none()

  if not tag:
    raise HTTPException(404, "Tag not found")

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
    "name": tag.name
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
  stmt = select(Report).where(Report.id == report_id)
  result = await db.execute(stmt)
  report: Report | None = result.scalar_one_or_none()

  if not report:
    raise HTTPException(404, "Report not found")

  company_id = await get_company_id(
    parent_type=report.parent_type,
    parent_id=report.parent_id,
    db=db,
  )

  allowed, reason = await can_use_billed_features(company_id, db)
  if not allowed and current_user.role != "admin":
    raise HTTPException(status_code=402, detail=reason)

  require_report_open(report)

  if current_user.role != "admin":
    if report.parent_type == ReportParentType.project:
      await require_project_access(current_user, report.parent_id, company_id, db)
    else:
      require_company_manager(current_user, company_id)

  stmt = (
    select(Image)
    .join(ReportImageLink)
    .where(
      Image.id == image_id,
      ReportImageLink.report_id == report_id,
    )
  )
  result = await db.execute(stmt)
  image = result.scalar_one_or_none()

  if not image:
    raise HTTPException(404, "Image not found")

  stmt = select(ImageTagLink).where(
    ImageTagLink.id == link_id,
    ImageTagLink.image_id == image_id,
  )
  result = await db.execute(stmt)
  link = result.scalar_one_or_none()

  if not link:
    raise HTTPException(404, "Tag link not found")

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
    .where(Report.id == report_id)
    .options(selectinload(Report.fields))
  )

  result = await db.execute(stmt)
  report: Report | None = result.scalar_one_or_none()

  if report is None:
    raise HTTPException(404, "Report not found")

  company_id = await get_company_id(
    parent_type=report.parent_type,
    parent_id=report.parent_id,
    db=db,
  )

  allowed, reason = await can_use_billed_features(company_id, db)
  if not allowed and current_user.role != "admin":
    raise HTTPException(status_code=402, detail=reason)

  require_report_open(report)

  if current_user.role != "admin":
    if report.parent_type == ReportParentType.project:
      await require_project_access(current_user, report.parent_id, company_id, db)
    else:
      require_company_manager(current_user, company_id)

  try:
    changed_fields = await transcribe_and_extract_json(
      speech_text=payload.text,
      fields=report.fields,
      output_language=payload.output_language
    )
    return ReportSpeechResponse(field_values=changed_fields)
  except Exception as e:
    raise HTTPException(
      status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
      detail=f"Error extracting JSON: {str(e)}"
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
    .where(Report.id == report_id)
    .options(
      selectinload(Report.fields),
    )
  )

  result = await db.execute(stmt)
  report: Report | None = result.scalar_one_or_none()

  if report is None:
    raise HTTPException(
      status_code=404,
      detail="Report not found",
    )

  company_id = await get_company_id(
    parent_type=report.parent_type,
    parent_id=report.parent_id,
    db=db,
  )

  allowed, reason = await can_use_billed_features(company_id, db)
  if not allowed and current_user.role != "admin":
    raise HTTPException(
      status_code=402,
      detail=reason,
    )

  require_report_open(report)

  if current_user.role != "admin":
    if report.parent_type == ReportParentType.project:
      await require_project_access(
        current_user,
        report.parent_id,
        company_id,
        db,
      )
    else:
      require_company_manager(
        current_user,
        company_id,
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
        LineConversation.company_id == company_id,
      )
    )

    conversation = conversation_result.scalar_one_or_none()

    if not conversation:
      raise HTTPException(
        status_code=404,
        detail=f"Conversation {conversation_range.conversation_id} not found",
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

    messages = list(reversed(messages_result.scalars().all()))

    logger.info(
      "Retrieved %d messages from conversation '%s' (%s)",
      len(messages),
      conversation.name,
      conversation.id,
    )

    for message in messages:
      logger.info(
        "[%s] %s: %s",
        message.line_timestamp or message.created_at,
        message.line_user_id or message.sender_line_user_id,
        message.text,
      )

    conversation_segments.append(
      (
        conversation,
        messages,
      )
    )

  try:
    changed_fields = await extract_report_fields_from_line_conversations(
      conversation_segments=conversation_segments,
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
    .where(Report.id == report_id)
    .options(selectinload(Report.fields))
  )

  result = await db.execute(stmt)
  report: Report | None = result.scalar_one_or_none()

  if report is None:
    raise HTTPException(status_code=404, detail="Report not found")

  company_id = await get_company_id(
    parent_type=report.parent_type,
    parent_id=report.parent_id,
    db=db,
  )

  allowed, reason = await can_use_billed_features(company_id, db)
  if not allowed and current_user.role != "admin":
    raise HTTPException(status_code=402, detail=reason)

  if current_user.role != "admin":
    if report.parent_type == ReportParentType.project:
      await require_project_access(current_user, report.parent_id, company_id, db)

      if current_user.role != "manager" and report.created_by != current_user.id:
        raise HTTPException(
          status_code=status.HTTP_403_FORBIDDEN,
          detail="You can only delete reports you created",
        )
    else:
      require_company_manager(current_user, company_id)

  for field in report.fields:
    await db.delete(field)

  await db.delete(report)

  await db.commit()

  return None

@router.delete("/{report_id}/images/{image_id}")
async def delete_report_image(
  report_id: UUID,
  image_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = (
    select(Report)
    .where(Report.id == report_id)
  )

  result = await db.execute(stmt)
  report = result.scalar_one_or_none()

  if report is None:
    raise HTTPException(404, "Report not found")

  company_id = await get_company_id(
    parent_type=report.parent_type,
    parent_id=report.parent_id,
    db=db,
  )

  require_report_open(report)

  if current_user.role != "admin":
    if report.parent_type == ReportParentType.project:
      await require_project_access(
        current_user,
        report.parent_id,
        company_id,
        db,
      )
    else:
      require_company_manager(
        current_user,
        company_id,
      )

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
    raise HTTPException(404, "Image not found")

  await db.delete(link)
  await db.commit()

  # TODO: Delete image from S3 when no links to line messages are confirmed

  return {"success": True}

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
      .where(ReportTemplate.id == report_template_id)
      .options(selectinload(ReportTemplate.fields))
    )
    template = result.scalar_one_or_none()

  elif current_user.role == "manager":
    result = await db.execute(
      select(ReportTemplate)
      .join(CompanyReportTemplate, CompanyReportTemplate.report_template_id == ReportTemplate.id)
      .where(
        ReportTemplate.id == report_template_id,
        CompanyReportTemplate.company_id == current_user.company_id,
      )
      .options(selectinload(ReportTemplate.fields))
    )
    template = result.scalar_one_or_none()

  elif current_user.company_id:
    result = await db.execute(
      select(ReportTemplate)
      .join(CompanyReportTemplate, CompanyReportTemplate.report_template_id == ReportTemplate.id)
      .where(
        ReportTemplate.id == report_template_id,
        CompanyReportTemplate.company_id == current_user.company_id,
      )
      .options(selectinload(ReportTemplate.fields))
    )
    template = result.scalar_one_or_none()

    if not template:
      result = await db.execute(
        select(ReportTemplate)
        .join(CompanyReportTemplate, CompanyReportTemplate.report_template_id == ReportTemplate.id)
        .join(Company, Company.id == CompanyReportTemplate.company_id)
        .join(Project, Project.company_id == Company.id)
        .join(ProjectGuestLink, ProjectGuestLink.project_id == Project.id)
        .where(
          ReportTemplate.id == report_template_id,
          ProjectGuestLink.user_id == current_user.id,
          ReportTemplate.parent_type == ReportParentType.project,
        )
        .options(selectinload(ReportTemplate.fields))
      )
      template = result.scalar_one_or_none()

  else:
    result = await db.execute(
      select(ReportTemplate)
      .join(CompanyReportTemplate, CompanyReportTemplate.report_template_id == ReportTemplate.id)
      .join(Company, Company.id == CompanyReportTemplate.company_id)
      .join(Project, Project.company_id == Company.id)
      .join(ProjectGuestLink, ProjectGuestLink.project_id == Project.id)
      .where(
        ReportTemplate.id == report_template_id,
        ProjectGuestLink.user_id == current_user.id,
        ReportTemplate.parent_type == ReportParentType.project,
      )
      .options(selectinload(ReportTemplate.fields))
    )
    template = result.scalar_one_or_none()

  if not template:
    raise HTTPException(status_code=404, detail="Report template not found")

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

  if payload.parent_type is not None:
    template.parent_type = payload.parent_type

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