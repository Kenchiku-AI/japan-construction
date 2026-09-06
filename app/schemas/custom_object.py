from datetime import datetime
from typing import Optional, List
from uuid import UUID

from pydantic import BaseModel, Field

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
  fields: dict[UUID, Optional[str]] = Field(default_factory=dict)
  relationships: dict[UUID, list[UUID]] = Field(default_factory=dict)


class CustomObjectUpdate(BaseModel):
  custom_object_definition_id: UUID | None = None
  fields: dict[UUID, str] | None = None
  relationships: dict[UUID, list[UUID]] | None = None


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

class CustomObjectsByDefinitionsRequest(BaseModel):
  company_id: UUID
  definition_ids: List[UUID]


class CustomObjectListItemRead(BaseModel):
  id: UUID
  name: str
  subtitle: str | None = None

  model_config = {
    "from_attributes": True,
  }


class CustomObjectsByDefinitionRead(BaseModel):
  name: str
  objects: List[CustomObjectListItemRead]


class CustomObjectsByDefinitionsRequest(BaseModel):
  company_id: UUID
  definition_ids: List[UUID]