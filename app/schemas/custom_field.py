from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class CustomFieldDefinitionCreate(BaseModel):
  company_id: UUID
  key: str
  name: str
  description: Optional[str] = None


class CustomFieldDefinitionUpdate(BaseModel):
  key: Optional[str] = None
  name: Optional[str] = None
  description: Optional[str] = None


class CustomFieldDefinitionRead(BaseModel):
  id: UUID
  company_id: UUID
  key: str
  name: str
  description: Optional[str] = None
  created_at: datetime
  updated_at: datetime

  model_config = {
    "from_attributes": True
  }


class CustomFieldCreate(BaseModel):
  custom_field_definition_id: UUID
  value: Optional[str] = None


class CustomFieldUpdate(BaseModel):
  value: Optional[str] = None


class CustomFieldRead(BaseModel):
  id: UUID
  company_id: UUID
  custom_field_definition_id: UUID
  value: Optional[str] = None
  definition: CustomFieldDefinitionRead
  created_at: datetime
  updated_at: datetime

  model_config = {
    "from_attributes": True
  }