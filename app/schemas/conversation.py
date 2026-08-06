from datetime import datetime
from uuid import UUID
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.db.models.conversation_item import ConversationItemStatus


class ConversationCreate(BaseModel):
  name: str
  company_id: UUID
  project_id: UUID | None = None
  item_type_ids: list[UUID] = []

class ConversationUpdate(BaseModel):
  name: str | None = None
  project_id: UUID | None = None
  item_type_ids: list[UUID] | None = None

class ConversationItemCreate(BaseModel):
  project_id: UUID
  conversation_item_type_id: UUID
  name: str
  description: str | None = None
  assignee_id: UUID | None = None

class ConversationItemUpdate(BaseModel):
  name: str | None = None
  description: str | None = None
  status: ConversationItemStatus | None = None
  assignee_id: UUID | None = None

class ConversationItemAssigneeRead(BaseModel):
  id: UUID
  first_name: str | None
  last_name: str | None

  model_config = ConfigDict(from_attributes=True)

class ConversationItemRead(BaseModel):
  id: UUID
  conversation_id: UUID | None = None
  conversation_item_type_id: UUID
  name: str
  assignee: ConversationItemAssigneeRead | None
  description: str
  source_message_text: str | None = None
  status: ConversationItemStatus
  created_at: datetime
  updated_at: datetime

  model_config = {
    "from_attributes": True
  }

class ConversationItemTypeCreate(BaseModel):
  name: str
  description: str | None = None

class ConversationItemTypeUpdate(BaseModel):
  name: str | None = None
  description: str | None = None

class ConversationItemTypeRead(BaseModel):
  id: UUID
  name: str
  description: str | None = None

  model_config = {"from_attributes": True}

class ConversationRead(BaseModel):
  id: UUID
  name: str
  line_link_code: str
  line_chat_id: str | None = None
  project_id: UUID | None = None
  item_types: list[ConversationItemTypeRead] = []
  last_message_text: str | None = None
  created_at: datetime
  updated_at: datetime

  model_config = {"from_attributes": True}