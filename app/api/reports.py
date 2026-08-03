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
from app.services.reports import get_company_id
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
  stmt = (
    select(Report)
    .options(
      selectinload(Report.fields),
      selectinload(Report.company),
      selectinload(Report.project_links).selectinload(
        ReportProjectLink.project
      ),
    )
  )

  if q:
    search = f"%{q.lower()}%"

    stmt = stmt.where(
      func.lower(Report.name).like(search)
    )

  if project_id:
    stmt = stmt.where(
      exists(
        select(1)
        .select_from(ReportProjectLink)
        .where(
          ReportProjectLink.report_id == Report.id,
          ReportProjectLink.project_id == project_id,
        )
      )
    )

  if current_user.role == "admin":
    pass
  elif current_user.company_id:
    company_report_access = (
      Report.company_id == current_user.company_id
    )

    guest_project_access = exists(
      select(1)
      .select_from(ReportProjectLink)
      .join(
        Project,
        Project.id == ReportProjectLink.project_id,
      )
      .join(
        ProjectGuestLink,
        ProjectGuestLink.project_id == Project.id,
      )
      .where(
        ReportProjectLink.report_id == Report.id,
        ProjectGuestLink.user_id == current_user.id,
        Project.company_id != current_user.company_id,
      )
    )

    stmt = stmt.where(
      or_(
        company_report_access,
        guest_project_access,
      )
    )
  else:
    guest_project_access = exists(
      select(1)
      .select_from(ReportProjectLink)
      .join(
        Project,
        Project.id == ReportProjectLink.project_id,
      )
      .join(
        ProjectGuestLink,
        ProjectGuestLink.project_id == Project.id,
      )
      .where(
        ReportProjectLink.report_id == Report.id,
        ProjectGuestLink.user_id == current_user.id,
      )
    )

    stmt = stmt.where(guest_project_access)

  stmt = (
    stmt
    .order_by(Report.updated_at.desc())
    .limit(25)
  )

  result = await db.execute(stmt)

  reports = result.scalars().unique().all()

  for report in reports:
    report.fields.sort(key=lambda f: f.order)

    project_ids = [
      link.project_id
      for link in report.project_links
    ]

    project_names = [
      link.project.name
      for link in report.project_links
      if link.project
    ]

    response.append(
      ReportWithCompanyAndProjectName(
        id=report.id,
        name=report.name,
        template_id=report.template_id,
        status=report.status,
        created_at=report.created_at,
        updated_at=report.updated_at,
        fields=report.fields,
        company_id=report.company_id,
        company_name=report.company.name if report.company else None,
        project_ids=project_ids,
        project_name=project_names[0] if project_names else None,
        photo_count=0,
      )
    )

  return response

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

  company_id = payload.company_id

  project = None

  if payload.project_id:
    project = await db.get(Project, payload.project_id)

    if not project:
      raise HTTPException(
        status_code=404,
        detail="Project not found",
      )

    if project.company_id != company_id:
      raise HTTPException(
        status_code=400,
        detail="Project does not belong to the specified company",
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

  if project:
    await require_project_access(
      current_user,
      project.id,
      company_id,
      db,
    )
  else:
    require_company_member(
      current_user,
      company_id,
    )

  if project and current_user.role != "admin":
    if project.status != ProjectStatus.active:
      raise HTTPException(
        status_code=403,
        detail="Reports can only be created for active projects",
      )

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

  if project:
    report_project_link = ReportProjectLink(
      report_id=report.id,
      project_id=project.id,
    )

    db.add(report_project_link)

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
    .options(
      selectinload(Report.fields),
      selectinload(Report.project_links),
    )
  )
  result = await db.execute(stmt)
  report = result.scalar_one()
  report.fields.sort(key=lambda f: f.order)

  report_read = ReportRead(
    id=report.id,
    name=report.name,
    template_id=report.template_id,
    status=report.status,
    created_at=report.created_at,
    updated_at=report.updated_at,
    fields=report.fields,
    company_id=report.company_id,
    project_ids=[
      link.project_id
      for link in report.project_links
    ],
    photo_count=0,
  )

  return report_read

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
    own_company_stmt = (
      select(ReportTemplate)
      .join(CompanyReportTemplate)
      .where(CompanyReportTemplate.company_id == current_user.company_id)
      .options(selectinload(ReportTemplate.fields))
    )
    own_company_result = await db.execute(own_company_stmt)
    own_templates = own_company_result.scalars().unique().all()

    guest_stmt = (
      select(ReportTemplate)
      .join(
        CompanyReportTemplate,
        CompanyReportTemplate.report_template_id == ReportTemplate.id,
      )
      .join(
        Project,
        Project.company_id == CompanyReportTemplate.company_id,
      )
      .join(
        ProjectGuestLink,
        ProjectGuestLink.project_id == Project.id,
      )
      .where(
        ProjectGuestLink.user_id == current_user.id,
        CompanyReportTemplate.company_id != current_user.company_id,
      )
      .options(selectinload(ReportTemplate.fields))
    )
    guest_result = await db.execute(guest_stmt)
    guest_templates = guest_result.scalars().unique().all()

    templates_by_id = {
      template.id: template
      for template in own_templates
    }

    for template in guest_templates:
      templates_by_id.setdefault(template.id, template)

    all_templates = list(templates_by_id.values())

    all_templates.sort(
      key=lambda template: template.updated_at,
      reverse=True,
    )

    return all_templates

  guest_stmt = (
    select(ReportTemplate)
    .join(
      CompanyReportTemplate,
      CompanyReportTemplate.report_template_id == ReportTemplate.id,
    )
    .join(
      Project,
      Project.company_id == CompanyReportTemplate.company_id,
    )
    .join(
      ProjectGuestLink,
      ProjectGuestLink.project_id == Project.id,
    )
    .where(
      ProjectGuestLink.user_id == current_user.id,
    )
    .options(selectinload(ReportTemplate.fields))
  )
  guest_result = await db.execute(guest_stmt)
  templates = guest_result.scalars().unique().all()

  templates_by_id = {
    template.id: template
    for template in templates
  }

  all_templates = list(templates_by_id.values())

  all_templates.sort(
    key=lambda template: template.updated_at,
    reverse=True,
  )

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

  if effective_company_id:
    company = await db.get(Company, effective_company_id)

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
      and effective_company_id is None
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
    select(Report)
    .where(
      Report.template_id == template_id,
    )
    .options(
      selectinload(Report.fields),
      selectinload(Report.image_links)
        .selectinload(ReportImageLink.image),
      selectinload(Report.project_links)
        .selectinload(ReportProjectLink.project),
      joinedload(Report.creator),
    )
  )

  if project_id:
    stmt = stmt.where(
      exists(
        select(1)
        .select_from(ReportProjectLink)
        .where(
          ReportProjectLink.report_id == Report.id,
          ReportProjectLink.project_id == project_id,
        )
      )
    )

  stmt = stmt.order_by(
    Report.created_at.desc()
  )

  result = await db.execute(stmt)
  reports = result.scalars().unique().all()

  filtered_reports: list[Report] = []

  for report in reports:
    if current_user.role != "admin":
      require_company_member(
        current_user,
        report.company_id,
      )

    filtered_reports.append(report)

  field_names: set[str] = set()

  for report in filtered_reports:
    for field in report.fields:
      field_names.add(field.name)

  sorted_field_names = sorted(field_names)

  export_rows: list[dict] = []

  for report in filtered_reports:
    row = {
      "報告書名": report.name,
    }

    if not project_id:
      project_names = [
        link.project.name
        for link in report.project_links
        if link.project
      ]

      row["プロジェクト"] = "、".join(project_names)

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
    .where(Report.id == report_id)
    .options(
      selectinload(Report.fields),
      selectinload(Report.image_links)
        .selectinload(ReportImageLink.image),
      selectinload(Report.project_links)
        .selectinload(ReportProjectLink.project),
      selectinload(Report.company),
    )
  )

  result = await db.execute(stmt)
  report = result.scalar_one_or_none()

  if report is None:
    raise HTTPException(
      status_code=404,
      detail="Report not found",
    )

  company_id = report.company_id

  if current_user.role != "admin":
    if report.project_links:
      has_project_access = False

      for link in report.project_links:
        try:
          await require_project_access(
            current_user,
            link.project_id,
            company_id,
            db,
          )
          has_project_access = True
          break
        except HTTPException as exc:
          if exc.status_code != status.HTTP_403_FORBIDDEN:
            raise

      if not has_project_access:
        raise HTTPException(
          status_code=status.HTTP_403_FORBIDDEN,
          detail="You do not have access to this report",
        )

    else:
      require_company_member(
        current_user,
        company_id,
      )

  report.fields.sort(
    key=lambda f: f.order
  )

  report.photo_count = len(
    report.image_links
  )

  report.project_ids = [
    link.project_id
    for link in report.project_links
  ]

  report.project_name = (
    report.project_names[0]
    if report.project_names
    else None
  )

  report.company_name = (
    report.company.name
    if report.company
    else None
  )

  if report.project_links:
    has_active_project = any(
      link.project
      and link.project.status == ProjectStatus.active
      for link in report.project_links
    )

    report.disabled = not has_active_project

  else:
    report.disabled = False

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
    company_id=company_id,
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
    await sync_report_line_images(
      report=report,
      conversation_segments=conversation_segments,
      db=db,
    )

    changed_fields = await extract_report_fields_from_line_conversations(
      conversation_segments=conversation_segments,
      fields=report.fields,
      output_language=payload.output_language,
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