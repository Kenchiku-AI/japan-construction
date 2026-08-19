from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from app.db.models.custom_relationship_definition import (
  CustomRelationshipCardinality,
  CustomRelationshipEntityType,
)

class CustomRelationshipDefinitionCreate(BaseModel):
  company_id: UUID
  key: str
  name: str
  description: Optional[str] = None
  source_entity_type: CustomRelationshipEntityType
  source_custom_object_definition_id: Optional[UUID] = None
  target_entity_type: CustomRelationshipEntityType
  target_custom_object_definition_id: Optional[UUID] = None
  cardinality: CustomRelationshipCardinality = (
    CustomRelationshipCardinality.many
  )


class CustomRelationshipDefinitionUpdate(BaseModel):
  key: Optional[str] = None
  name: Optional[str] = None
  description: Optional[str] = None
  source_entity_type: Optional[CustomRelationshipEntityType] = None
  source_custom_object_definition_id: Optional[UUID] = None
  target_entity_type: Optional[CustomRelationshipEntityType] = None
  target_custom_object_definition_id: Optional[UUID] = None
  cardinality: Optional[CustomRelationshipCardinality] = None


class CustomRelationshipDefinitionRead(BaseModel):
  id: UUID
  company_id: UUID
  key: str
  name: str
  description: Optional[str] = None
  source_entity_type: CustomRelationshipEntityType
  source_custom_object_definition_id: Optional[UUID] = None
  target_entity_type: CustomRelationshipEntityType
  target_custom_object_definition_id: Optional[UUID] = None
  cardinality: CustomRelationshipCardinality
  sort_order: int
  created_at: datetime
  updated_at: datetime

  model_config = {
    "from_attributes": True,
  }


class CustomRelationshipCreate(BaseModel):
  custom_relationship_definition_id: UUID
  source_entity_id: UUID
  target_entity_id: UUID


class CustomRelationshipUpdate(BaseModel):
  source_entity_id: Optional[UUID] = None
  target_entity_id: Optional[UUID] = None


class CustomRelationshipRead(BaseModel):
  id: UUID
  company_id: UUID
  custom_relationship_definition_id: UUID

  source_entity_type: CustomRelationshipEntityType
  source_entity_id: UUID

  target_entity_type: CustomRelationshipEntityType
  target_entity_id: UUID

  created_at: datetime
  updated_at: datetime

  model_config = {
    "from_attributes": True,
  }