from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class CustomObjectDefinitionCreate(BaseModel):
  company_id: UUID
  name: str
  key: str
  description: Optional[str] = None


class CustomObjectDefinitionUpdate(BaseModel):
  name: Optional[str] = None
  key: Optional[str] = None
  description: Optional[str] = None


class CustomObjectDefinitionRead(BaseModel):
  id: UUID
  name: str
  description: Optional[str] = None
  created_at: datetime
  updated_at: datetime

  model_config = {
    "from_attributes": True,
  }


class CustomObjectCreate(BaseModel):
  company_id: UUID
  custom_object_definition_id: UUID
  name: str
  description: Optional[str] = None


class CustomObjectUpdate(BaseModel):
  custom_object_definition_id: Optional[UUID] = None
  name: Optional[str] = None
  description: Optional[str] = None


class CustomObjectRead(BaseModel):
  id: UUID
  company_id: UUID
  custom_object_definition_id: UUID
  name: str
  description: Optional[str] = None
  definition: CustomObjectDefinitionRead
  created_at: datetime
  updated_at: datetime

  model_config = {
    "from_attributes": True,
  }