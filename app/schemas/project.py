from datetime import datetime
from pydantic import BaseModel
from typing import Optional, List
from uuid import UUID

class ProjectBase(BaseModel):
  name: str
  description: str | None = None

class ProjectCreate(ProjectBase):
  company_id: UUID

class ProjectUpdate(ProjectBase):
  name: str | None = None
  description: str | None = None
  status: str | None = None

class ProjectRead(ProjectBase):
  id: UUID
  company_id: UUID
  status: str

  model_config = {
    "from_attributes": True
  }

class ProjectWithCompanyName(ProjectRead):
  company_name: str | None = None

class ProjectReportRead(BaseModel):
  id: UUID
  name: str
  created_at: datetime
  updated_at: datetime

  model_config = {
    "from_attributes": True
  }

class ProjectWithReports(ProjectWithCompanyName):
  reports: List[ProjectReportRead] = []
  