from typing import Dict, List, Optional
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel

from app.db.models.report import ReportParentType, ReportFieldType, ReportUniqueBy

class ReportCreate(BaseModel):
  name: str
  parent_id: UUID
  template_id: UUID

class ReportUpdate(BaseModel):
  name: Optional[str] = None
  field_values: Optional[Dict[UUID, str]] = None

class ReportFieldRead(BaseModel):
  id: UUID
  type: ReportFieldType
  name: str
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
  company_id: Optional[UUID] = None

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

class ReportTemplateFieldRead(BaseModel):
  id: UUID
  name: str
  description: Optional[str] = None
  type: ReportFieldType

class ReportTemplateRead(BaseModel):
  id: UUID
  name: str
  description: Optional[str] = None
  parent_type: ReportParentType
  unique_by: Optional[ReportUniqueBy] = None
  is_global: bool
  fields: List[ReportTemplateFieldRead]

class ReportTemplateFieldUpdate(BaseModel):
  name: str
  description: str
  type: str


class ReportTemplateUpdate(BaseModel):
  name: str | None = None
  description: str | None = None
  parent_type: str | None = None
  unique_by: str | None = None
  fields: list[ReportTemplateFieldUpdate] | None = None

class ShareReportTemplateRequest(BaseModel):
  company_id: UUID
  template_id: UUID
  