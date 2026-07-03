from datetime import datetime, timezone
from uuid import UUID
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.report import Report, ReportParentType, ReportStatus
from app.db.models.project import Project
from app.db.models.user import User
from app.db.models.project_guest_link import ProjectGuestLink
from app.db.session import AsyncSessionLocal
from app.services.openai import filter_reports_by_context, transcribe_and_extract_json

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
) -> None:
  async with AsyncSessionLocal() as db:
    report = await find_applicable_report(user, company_id, text, db)

    if report is None:
      logger.info(
        "LINE message: no applicable open report found | user_id=%s company_id=%s",
        user.id, company_id,
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
        report.id, user.id,
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
      len(changed_fields), report.id, user.id,
    )

async def handle_line_group_message(
  text: str,
  user: User,
  project_id: UUID,
) -> None:
  async with AsyncSessionLocal() as db:
    stmt = (
      select(Report)
      .where(
        Report.parent_id == project_id,
        Report.parent_type == ReportParentType.project,
        Report.status == ReportStatus.open,
      )
      .options(selectinload(Report.fields))
      .order_by(Report.created_at.desc())
      .limit(1)
    )
    result = await db.execute(stmt)
    report = result.scalars().first()

    if report is None:
      logger.info(
        "LINE group message: no open report for project | project_id=%s user_id=%s",
        project_id, user.id,
      )
      return

    changed_fields = await transcribe_and_extract_json(
      speech_text=text,
      fields=report.fields,
      output_language="日本語",
    )

    if not changed_fields:
      logger.info(
        "LINE group message: no fields matched | report_id=%s user_id=%s",
        report.id, user.id,
      )
      return

    field_map = {str(f.id): f for f in report.fields}
    for field_id, value in changed_fields.items():
      if field_id in field_map:
        field_map[field_id].value = value

    report.updated_at = datetime.now(timezone.utc)
    await db.commit()

    logger.info(
      "LINE group message: updated %d field(s) on report_id=%s | user_id=%s",
      len(changed_fields), report.id, user.id,
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
    .limit(10)
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
    .limit(10)
  )

  if user.company_id == company_id:
    result = await db.execute(company_reports_stmt)
  else:
    result = await db.execute(guest_reports_stmt)

  reports = result.scalars().all()

  if not reports:
    return None

  project_ids = [r.parent_id for r in reports]
  project_result = await db.execute(
    select(Project).where(Project.id.in_(project_ids))
  )
  project_map = {p.id: p for p in project_result.scalars().all()}

  applicable = await filter_reports_by_context(message_text, reports, project_map)

  if not applicable:
    return None

  return max(applicable, key=lambda r: r.created_at)