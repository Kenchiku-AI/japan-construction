from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ConversationCreate(BaseModel):
  name: str
  company_id: UUID
  project_id: UUID | None = None
  item_type_ids: list[UUID] = []


class ConversationUpdate(BaseModel):
  name: str | None = None
  project_id: UUID | None = None
  item_type_ids: list[UUID] | None = None


class ConversationItemTypeRead(BaseModel):
  id: UUID
  company_id: UUID
  name: str
  description: str | None = None
  created_at: datetime
  updated_at: datetime

  model_config = {"from_attributes": True}
  

class ConversationRead(BaseModel):
  id: UUID
  name: str
  company_id: UUID
  project_id: UUID | None = None
  line_link_code: str
  line_group_id: str | None = None
  item_types: list[ConversationItemTypeRead]
  created_at: datetime
  updated_at: datetime

  model_config = {"from_attributes": True}


class ConversationItemTypeCreate(BaseModel):
  name: str
  description: str | None = None


class ConversationItemTypeUpdate(BaseModel):
  name: str | None = None
  description: str | None = None