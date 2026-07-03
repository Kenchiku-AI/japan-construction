from datetime import datetime
from pydantic import BaseModel
from typing import Optional, List
from uuid import UUID

from app.db.models.work_item import WorkItemStatus

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
  line_link_code: str

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

class ProjectWorkItemRead(BaseModel):
  id: UUID
  project_id: UUID
  name: str
  description: str | None = None
  status: WorkItemStatus
  assignee_id: UUID | None = None
  scheduled_date: datetime | None = None
  created_at: datetime
  updated_at: datetime

  model_config = {
    "from_attributes": True
  }
  
class ProjectWithReportsAndWorkItems(ProjectWithCompanyName):
  reports: List[ProjectReportRead] = []
  work_items: List[ProjectWorkItemRead] = []