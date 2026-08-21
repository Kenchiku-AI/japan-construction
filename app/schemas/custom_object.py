from datetime import datetime
from typing import Optional, List
from uuid import UUID

from pydantic import BaseModel

from app.schemas.custom_field import CustomFieldDefinitionRead, CustomFieldRead
from app.schemas.custom_relationship import CustomRelationshipDefinitionRead, CustomRelationshipRead

class CustomObjectDefinitionCreate(BaseModel):
  company_id: UUID
  name: str
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

class CustomObjectDefinitionDetailRead(BaseModel):
  id: UUID
  company_id: UUID
  name: str
  description: Optional[str] = None
  fields: List[CustomFieldDefinitionRead]
  relationships: List[CustomRelationshipDefinitionRead]
  created_at: datetime
  updated_at: datetime

  model_config = {
    "from_attributes": True,
  }


class CustomObjectCreate(BaseModel):
  company_id: UUID
  custom_object_definition_id: UUID
  fields: dict[UUID, Optional[str]] = {}
  relationships: dict[UUID, UUID] = {}


class CustomObjectUpdate(BaseModel):
  custom_object_definition_id: Optional[UUID] = None
  name: Optional[str] = None
  description: Optional[str] = None


class CustomObjectRead(BaseModel):
  id: UUID
  company_id: UUID
  definition: CustomObjectDefinitionRead
  fields: List[CustomFieldRead]
  relationships: List[CustomRelationshipRead]
  created_at: datetime
  updated_at: datetime

  model_config = {
    "from_attributes": True,
  }