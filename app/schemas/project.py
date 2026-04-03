from datetime import datetime
from pydantic import BaseModel
from typing import Optional, List
from uuid import UUID

from app.schemas.company import CompanyRead

class ProjectBase(BaseModel):
  name: str
  description: str | None = None

class ProjectCreate(ProjectBase):
  company_id: UUID

class ProjectUpdate(ProjectBase):
  name: str | None = None
  description: str | None = None

class ProjectRead(ProjectBase):
  id: UUID
  status: str

  model_config = {
    "from_attributes": True
  }

class ProjectWithCompany(ProjectRead):
  company: CompanyRead

class ProjectReportRead(BaseModel):
  id: UUID
  name: str
  created_at: datetime
  updated_at: datetime

  model_config = {
    "from_attributes": True
  }

class ProjectWithReports(ProjectRead):
  reports: List[ProjectReportRead] = []
  