from datetime import datetime, timezone
from uuid import UUID
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.report import Report, ReportProjectLink, ReportStatus
from app.db.models.project import Project
from app.db.models.user import User
from app.db.models.project_guest_link import ProjectGuestLink
from app.db.session import AsyncSessionLocal
from app.services.openai import transcribe_and_extract_json

logger = logging.getLogger(__name__)

async def handle_line_group_message(
  text: str,
  user: User,
  project_id: UUID,
) -> None:
  async with AsyncSessionLocal() as db:
    stmt = (
      select(Report)
      .join(
        ReportProjectLink,
        ReportProjectLink.report_id == Report.id,
      )
      .where(
        ReportProjectLink.project_id == project_id,
        Report.status == ReportStatus.open,
      )
      .options(
        selectinload(Report.fields),
      )
      .order_by(
        Report.created_at.desc(),
      )
      .limit(1)
    )

    result = await db.execute(stmt)
    report = result.scalars().first()

    if report is None:
      logger.info(
        "LINE group message: no open report for project | project_id=%s user_id=%s",
        project_id,
        user.id,
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
        report.id,
        user.id,
      )
      return

    field_map = {
      str(field.id): field
      for field in report.fields
    }

    for field_id, value in changed_fields.items():
      if field_id in field_map:
        field_map[field_id].value = value

    report.updated_at = datetime.now(timezone.utc)

    await db.commit()

    logger.info(
      "LINE group message: updated %d field(s) on report_id=%s | user_id=%s",
      len(changed_fields),
      report.id,
      user.id,
    )