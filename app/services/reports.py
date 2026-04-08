from datetime import datetime, timedelta
from uuid import UUID
from calendar import monthrange
from typing import Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.report import Report, ReportTemplate, ReportParentType
from app.db.models.project import Project

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