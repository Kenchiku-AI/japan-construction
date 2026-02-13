from datetime import datetime, time
from uuid import UUID
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.db.models import (
  Project,
  User,
  Report,
  ReportField,
  ReportTemplate,
  ReportTemplateField,
  ReportParentType,
  CompanyReportTemplate,
)
from app.schemas.report import ReportCreate, ReportRead, ReportTemplateCreate, ReportTemplateRead, ReportUpdate
from app.core.dependencies import (
  get_current_user,
  get_current_user_ws,
  require_company_manager,
  require_company_member,
)
from app.services.reports import can_create_report, get_company_id
from app.services.openai import transcribe_audio, get_json_from_speech, get_prompt_from_fields

router = APIRouter(
  prefix="/reports",
  tags=["reports"]
)

@router.get(
  "",
  response_model=list[ReportRead],
)
async def list_reports(
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role == "admin":
    stmt = (
      select(Report)
      .order_by(Report.updated_at.desc())
      .limit(25)
      .options(selectinload(Report.fields))
    )

    result = await db.execute(stmt)
    return result.scalars().all()

  if not current_user.company_id:
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="User is not associated with a company",
    )

  require_company_manager(current_user, current_user.company_id)

  stmt = (
    select(Report)
    .outerjoin(
      Project,
      (Report.parent_type == ReportParentType.project)
      & (Report.parent_id == Project.id),
    )
    .where(
      or_(
        (
          (Report.parent_type == ReportParentType.company)
          & (Report.parent_id == current_user.company_id)
        ),
        (
          (Report.parent_type == ReportParentType.project)
          & (Project.company_id == current_user.company_id)
        ),
      )
    )
    .order_by(Report.updated_at.desc())
    .limit(25)
    .options(selectinload(Report.fields))
  )

  result = await db.execute(stmt)
  return result.scalars().all()

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
  require_company_manager(current_user, company_id)

  if not await can_create_report(db, template, payload.parent_id, datetime.utcnow()):
    raise HTTPException(
      status_code=400,
      detail=f"A report for this period ({template.unique_by}) already exists",
    )

  report = Report(
    name=payload.name,
    template_id=template.id,
    parent_id=payload.parent_id,
    parent_type=parent_type,
    created_at=datetime.utcnow(),
    updated_at=datetime.utcnow(),
  )

  db.add(report)
  await db.flush()

  for template_field in template.fields:
    report_field = ReportField(
      name=template_field.name,
      description=template_field.description,
      type=template_field.type,
      value="",
      report_id=report.id,
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

  return report

#
# TODO: add update report
#

@router.get(
  "/templates",
  response_model=list[ReportTemplateRead],
)
async def list_report_templates(
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role == "admin":
    stmt = (
      select(ReportTemplate)
      .where(ReportTemplate.is_global.is_(True))
      .options(selectinload(ReportTemplate.fields))
      .order_by(ReportTemplate.updated_at.desc())
    )

    result = await db.execute(stmt)
    return result.scalars().all()

  if not current_user.company_id:
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="User is not associated with a company",
    )

  require_company_manager(current_user, current_user.company_id)

  stmt = (
    select(ReportTemplate)
    .join(CompanyReportTemplate)
    .where(CompanyReportTemplate.company_id == current_user.company_id)
    .options(selectinload(ReportTemplate.fields))
    .order_by(ReportTemplate.updated_at.desc())
  )

  result = await db.execute(stmt)
  return result.scalars().all()

@router.post(
  "/templates",
  response_model=ReportTemplateRead,
  status_code=status.HTTP_201_CREATED,
)
async def create_report_template(
  payload: ReportTemplateCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if payload.company_id:
    require_company_manager(current_user, payload.company_id)
  else:
    if current_user.role != "admin":
      raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Only admins can create global report templates",
      )

  template = ReportTemplate(
    name=payload.name,
    description=payload.description,
    unique_by=payload.unique_by,
    is_global=payload.company_id is None,
    parent_type=payload.parent_type,
    fields=[],
  )

  for field in payload.fields:
    template.fields.append(
      ReportTemplateField(
        name=field.name,
        description=field.description,
        type=field.type,
      )
    )

  db.add(template)
  await db.flush()

  if payload.company_id:
    db.add(
      CompanyReportTemplate(
        company_id=payload.company_id,
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

  return template

@router.get(
  "/{report_id}",
  response_model=ReportRead,
)
async def get_report(
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
    raise HTTPException(404, "Report not found")

  company_id = await get_company_id(
    parent_type=report.parent_type,
    parent_id=report.parent_id,
    db=db,
  )

  if current_user.role != "admin":
    require_company_manager(current_user, company_id)

  report.company_id = company_id

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
    .options(selectinload(Report.fields))
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

  if current_user.role != "admin":
    require_company_manager(current_user, company_id)

  if payload.name is not None:
    report.name = payload.name

  report.updated_at = datetime.utcnow()

  # TODO: template_field_id no longer exists - fix this
  if payload.field_values:
    field_map = {f.template_field_id: f for f in report.fields}

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
    .options(selectinload(Report.fields))
  )
  result = await db.execute(stmt)
  report = result.scalar_one()

  report.company_id = company_id

  return report

@router.websocket("/{report_id}/audio")
async def report_audio(
  ws: WebSocket,
  report_id: UUID,
  db: AsyncSession = Depends(get_db),
):
  await ws.accept()

  current_user = await get_current_user_ws(ws)

  report = await db.get(Report, report_id)
  if not report:
    await ws.close(code=1008)
    return

  company_id = await get_company_id(
    parent_type=report.parent_type,
    parent_id=report.parent_id,
    db=db
  )
  require_company_manager(current_user, company_id)

  async def on_complete(text: str):
    stmt = select(ReportTemplateField).where(
      ReportTemplateField.template_id == report.template_id
    )
    result = await db.execute(stmt)
    fields: list[ReportTemplateField] = result.scalars().all()
    
    prompt = get_prompt_from_fields(fields)
    json = await get_json_from_speech(text, prompt)

    # TO DO: Fill in report fields based on json returned
    #
    # EXAMPLE:
    #
    # if "start_time" in json:
    #   report.start_time = time.fromisoformat(json["start_time"])

    await db.commit()
    await db.refresh(report)

    await ws.send_json({
      "type": "report_updated",
      "payload": {"id": str(report.id)},
    })

  await transcribe_audio(ws, on_complete)

@router.get(
  "/templates/{report_template_id}",
  response_model=ReportRead,
)
async def get_report_template(
  report_template_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = (
    select(ReportTemplate)
    .where(ReportTemplate.id == report_template_id)
    .options(selectinload(ReportTemplate.fields))
  )
  # TODO: If user is admin, get report template by id
  #       if not, check if manager and if so, confirm CompanyReportTemplate for user's company_id exists
  # stmt = (
  #   select(ReportTemplate)
  #   .join(CompanyReportTemplate)
  #   .where(CompanyReportTemplate.company_id == current_user.company_id)
  #   .options(selectinload(ReportTemplate.fields))
  #   .order_by(ReportTemplate.updated_at.desc())
  # )
  result = await db.execute(stmt)
  template: ReportTemplate = result.scalar_one_or_none()

  if not template:
    raise HTTPException(status_code=404, detail="Report template not found")

  return template

#
# TODO: add update report template
#