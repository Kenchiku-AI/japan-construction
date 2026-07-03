from datetime import datetime
from uuid import UUID
from typing import Optional

from pydantic import BaseModel

from app.db.models.work_item import WorkItemStatus

class WorkItemCreate(BaseModel):
  project_id: UUID
  name: str
  description: str | None = None
  assignee_id: UUID | None = None
  scheduled_date: datetime | None = None

class WorkItemUpdate(BaseModel):
  name: str | None = None
  description: str | None = None
  status: WorkItemStatus | None = None
  assignee_id: UUID | None = None
  scheduled_date: datetime | None = None

class WorkItemRead(BaseModel):
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