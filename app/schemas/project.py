from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional, List
from uuid import UUID

from app.schemas.conversation import (
  ConversationItemTypeRead,
  ConversationItemRead,
)
from app.schemas.user import UserRead
from app.schemas.custom_field import CustomFieldRead

class ProjectBase(BaseModel):
  name: str
  description: str | None = None

class ProjectCreate(ProjectBase):
  company_id: UUID

class ProjectCustomFieldUpdate(BaseModel):
  custom_field_definition_id: UUID
  value: Optional[str] = None

class ProjectUpdate(ProjectBase):
  name: str | None = None
  description: str | None = None
  status: str | None = None
  custom_fields: List[ProjectCustomFieldUpdate] = Field(default_factory=list)

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

class ProjectConversationRead(BaseModel):
  id: UUID
  name: str
  line_link_code: str
  line_chat_id: str | None = None
  project_id: UUID | None = None
  item_types: List[ConversationItemTypeRead] = []
  last_message_text: str | None = None
  created_at: datetime
  updated_at: datetime
  
  model_config = {"from_attributes": True}

class ConversationItemsGroupedRead(BaseModel):
  conversation_item_type_id: UUID
  conversation_item_type_name: str
  items: List[ConversationItemRead] = []

  model_config = {
    "from_attributes": True
  }
  
class ProjectWithLists(ProjectWithCompanyName):
  users: List[UserRead] = Field(default_factory=list)
  reports: List[ProjectReportRead] = Field(default_factory=list)
  conversations: List[ProjectConversationRead] = Field(default_factory=list)
  conversation_items: List[ConversationItemsGroupedRead] = Field(default_factory=list)
  custom_fields: List[CustomFieldRead] = Field(default_factory=list)

class ProjectSetUsers(BaseModel):
  user_ids: list[UUID]