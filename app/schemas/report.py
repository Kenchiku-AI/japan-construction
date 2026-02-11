from typing import Dict, List, Optional
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel

from app.db.models.report import ReportParentType, ReportFieldType, ReportUniqueBy

class ReportCreate(BaseModel):
  name: str
  parent_id: UUID
  template_id: UUID
  field_values: Optional[Dict[UUID, str]] = None

class ReportUpdate(BaseModel):
  name: str
  field_values: Optional[Dict[UUID, str]] = None

class ReportFieldRead(BaseModel):
  template_field_id: UUID
  type: ReportFieldType
  value: str

  model_config = {
    "from_attributes": True
  }

class ReportRead(BaseModel):
  id: UUID
  name: str
  template_id: UUID
  parent_id: UUID
  parent_type: ReportParentType
  created_at: datetime
  updated_at: datetime
  fields: List[ReportFieldRead]

  model_config = {
    "from_attributes": True
  }

class ReportTemplateFieldCreate(BaseModel):
  name: str
  description: Optional[str] = None
  type: ReportFieldType

class ReportTemplateCreate(BaseModel):
  name: str
  description: Optional[str] = None
  parent_type: ReportParentType
  unique_by: Optional[ReportUniqueBy] = None
  fields: List[ReportTemplateFieldCreate]
  company_id: Optional[UUID] = None

class ReportTemplateRead(BaseModel):
  id: UUID
  name: str
  description: Optional[str] = None
  parent_type: ReportParentType
  unique_by: Optional[ReportUniqueBy] = None
  is_global: bool
  fields: List[ReportTemplateFieldCreate]
  