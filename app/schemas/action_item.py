from datetime import datetime
from uuid import UUID
from typing import Optional

from pydantic import BaseModel

from app.db.models.action_item import ActionItemStatus

class ActionItemCreate(BaseModel):
  project_id: UUID
  name: str
  description: str | None = None
  assignee_id: UUID | None = None
  scheduled_date: datetime | None = None

class ActionItemUpdate(BaseModel):
  name: str | None = None
  description: str | None = None
  status: ActionItemStatus | None = None
  assignee_id: UUID | None = None
  scheduled_date: datetime | None = None

class ActionItemRead(BaseModel):
  id: UUID
  project_id: UUID
  name: str
  description: str | None = None
  source_message_text: str | None = None
  status: ActionItemStatus
  assignee_id: UUID | None = None
  scheduled_date: datetime | None = None
  created_at: datetime
  updated_at: datetime

  model_config = {
    "from_attributes": True
  }