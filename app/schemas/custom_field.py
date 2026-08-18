from datetime import datetime
from typing import Optional, List
from uuid import UUID

from pydantic import BaseModel

from app.db.models.custom_field_definition import CustomFieldDataType


class CustomFieldDefinitionCreate(BaseModel):
  company_id: UUID
  key: str
  name: str
  description: Optional[str] = None
  data_type: CustomFieldDataType = CustomFieldDataType.text


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
  data_type: CustomFieldDataType
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

class CustomObjectWithFieldsRead(BaseModel):
  id: UUID
  name: str
  description: str | None
  fields: list[CustomFieldDefinitionRead]

class CustomFieldDefinitionsResponse(BaseModel):
  project_fields: List[CustomFieldDefinitionRead]
  user_fields: List[CustomFieldDefinitionRead]
  company_fields: List[CustomFieldDefinitionRead]
  custom_objects: List[CustomObjectWithFieldsRead]