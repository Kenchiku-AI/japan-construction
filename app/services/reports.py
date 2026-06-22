from datetime import datetime, timezone, timedelta
from uuid import UUID
from calendar import monthrange
from typing import Tuple
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.report import Report, ReportTemplate, ReportParentType, ReportStatus
from app.db.models.project import Project
from app.db.models.user import User
from app.db.models.project_guest_link import ProjectGuestLink
from app.services.openai import select_report_by_context, transcribe_and_extract_json

logger = logging.getLogger(__name__)

async def get_company_id(
  parent_type: ReportParentType,
  parent_id: UUID,
  db: AsyncSession
) -> UUID:
  if parent_type == ReportParentType.company:
    return parent_id
  elif parent_type == ReportParentType.project:
    stmt = select(Project).where(Project.id == parent_id)
    result = await db.execute(stmt)
    project = result.scalar_one_or_none()

    if not project:
      raise ValueError(f"Project with id {parent_id} not found")

    return project.company_id
  else:
    raise ValueError(f"Unsupported parent_type: {parent_type}")

async def handle_line_message(
  text: str,
  user: User,
  company_id: UUID,
  db: AsyncSession,
) -> None:
  report = await find_applicable_report(user, company_id, text, db)

  if report is None:
    logger.info(
      "LINE message: no applicable open report found | user_id=%s company_id=%s",
      user_id, company_id,
    )
    return

  changed_fields = await transcribe_and_extract_json(
    speech_text=text,
    fields=report.fields,
    output_language="日本語",
  )

  if not changed_fields:
    logger.info(
      "LINE message: no fields matched | report_id=%s user_id=%s",
      report.id, user_id,
    )
    return

  field_map = {str(f.id): f for f in report.fields}
  for field_id, value in changed_fields.items():
    if field_id in field_map:
      field_map[field_id].value = value

  report.updated_at = datetime.now(timezone.utc)
  await db.commit()

  logger.info(
    "LINE message: updated %d field(s) on report_id=%s | user_id=%s",
    len(changed_fields), report.id, user_id,
  )

async def find_applicable_report(
  user: User,
  company_id: UUID,
  message_text: str,
  db: AsyncSession,
) -> Report | None:
  company_reports_stmt = (
    select(Report)
    .join(Project, Report.parent_id == Project.id)
    .where(
      Project.company_id == company_id,
      Report.parent_type == ReportParentType.project,
      Report.status == ReportStatus.open,
    )
    .options(selectinload(Report.fields))
    .order_by(Report.updated_at.desc())
    .limit(5)
  )

  guest_reports_stmt = (
    select(Report)
    .join(Project, Report.parent_id == Project.id)
    .join(ProjectGuestLink, ProjectGuestLink.project_id == Project.id)
    .where(
      ProjectGuestLink.user_id == user.id,
      Report.parent_type == ReportParentType.project,
      Report.status == ReportStatus.open,
    )
    .options(selectinload(Report.fields))
    .order_by(Report.updated_at.desc())
    .limit(5)
  )

  if user.company_id == company_id:
    result = await db.execute(company_reports_stmt)
    reports = result.scalars().all()
  else:
    result = await db.execute(guest_reports_stmt)
    reports = result.scalars().all()

  if not reports:
    return None
  if len(reports) == 1:
    return reports[0]

  return await select_report_by_context(message_text, reports)