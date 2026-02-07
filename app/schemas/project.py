from datetime import datetime
from pydantic import BaseModel
from typing import List, Optional
from uuid import UUID

from app.schemas.daily_report import DailyReportRead
from app.schemas.company import CompanyRead

class ProjectBase(BaseModel):
  name: str
  description: str | None = None

class ProjectCreate(ProjectBase):
  company_id: UUID

class ProjectUpdate(ProjectBase):
  pass

class ProjectRead(ProjectBase):
  id: UUID
  status: str
  daily_reports: List[DailyReportRead]

  model_config = {
    "from_attributes": True
  }

class ProjectWithCompany(ProjectRead):
  company: CompanyRead
  