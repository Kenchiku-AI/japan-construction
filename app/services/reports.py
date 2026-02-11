from datetime import datetime, timedelta
from uuid import UUID
from calendar import monthrange
from typing import Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.report import Report, ReportTemplate, ReportUniqueBy, ReportParentType

async def can_create_report(
  db: AsyncSession,
  template: ReportTemplate,
  parent_id: str,
  created_at: datetime,
) -> bool:
  if not template.unique_by:
    return True

  period_start, period_end = compute_period(template.unique_by, created_at)

  stmt = (
    select(Report)
    .where(
      Report.template_id == template.id,
      Report.parent_id == parent_id,
      Report.created_at >= period_start,
      Report.created_at < period_end,
    )
  )

  result = await db.execute(stmt)
  existing = result.scalar_one_or_none()

  return existing is None

def compute_period(unique_by: ReportUniqueBy, dt: datetime) -> Tuple[datetime, datetime]:
  if unique_by == ReportUniqueBy.day:
    start = datetime(dt.year, dt.month, dt.day)
    end = start + timedelta(days=1)
  elif unique_by == ReportUniqueBy.week:
    start = datetime(dt.year, dt.month, dt.day) - timedelta(days=dt.weekday())
    end = start + timedelta(days=7)
  elif unique_by == ReportUniqueBy.month:
    start = datetime(dt.year, dt.month, 1)
    days_in_month = monthrange(dt.year, dt.month)[1]
    end = start + timedelta(days=days_in_month)
  elif unique_by == ReportUniqueBy.year:
    start = datetime(dt.year, 1, 1)
    end = datetime(dt.year + 1, 1, 1)
  else:
    raise ValueError(f"Unsupported unique_by value: {unique_by}")

  return start, end

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