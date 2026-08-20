from datetime import datetime
from typing import Optional, List
from uuid import UUID

from pydantic import BaseModel, model_validator

from app.db.models.custom_field_definition import (
  CustomFieldDataType,
  CustomFieldEntityType,
)

from app.schemas.custom_relationship import CustomRelationshipDefinitionRead


class CustomFieldDefinitionCreate(BaseModel):
  company_id: UUID
  name: str
  description: Optional[str] = None
  data_type: CustomFieldDataType = CustomFieldDataType.text
  entity_type: CustomFieldEntityType
  custom_object_definition_id: Optional[UUID] = None

  @model_validator(mode="after")
  def validate_entity_type(self):
    if self.entity_type == CustomFieldEntityType.custom_object:
      if self.custom_object_definition_id is None:
        raise ValueError(
          "custom_object_definition_id is required for custom_object fields"
        )
    elif self.custom_object_definition_id is not None:
      raise ValueError(
        "custom_object_definition_id can only be set for custom_object fields"
      )

    return self


class CustomFieldDefinitionUpdate(BaseModel):
  name: Optional[str] = None
  description: Optional[str] = None

class CustomFieldDefinitionSortOrderUpdate(BaseModel):
  id: UUID
  sort_order: int

class CustomFieldDefinitionRead(BaseModel):
  id: UUID
  company_id: UUID
  name: str
  description: Optional[str] = None
  data_type: CustomFieldDataType
  entity_type: CustomFieldEntityType
  custom_object_definition_id: Optional[UUID] = None
  sort_order: int
  created_at: datetime
  updated_at: datetime

  model_config = {
    "from_attributes": True,
  }


class CustomFieldCreate(BaseModel):
  custom_field_definition_id: UUID
  value: Optional[str] = None


class CustomFieldUpdate(BaseModel):
  value: Optional[str] = None


class CustomFieldRead(BaseModel):
  id: UUID | None = None
  value: Optional[str] = None
  definition: CustomFieldDefinitionRead

  model_config = {
    "from_attributes": True,
  }


class CustomFieldDefinitionsResponse(BaseModel):
  project_fields: List[CustomFieldDefinitionRead]
  project_relationships: List[CustomRelationshipDefinitionRead]
  user_fields: List[CustomFieldDefinitionRead]
  user_relationships: List[CustomRelationshipDefinitionRead]
  company_fields: List[CustomFieldDefinitionRead]
  company_relationships: List[CustomRelationshipDefinitionRead]