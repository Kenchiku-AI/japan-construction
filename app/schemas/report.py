from typing import Dict, List, Optional
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel

from app.models.report import ReportParentType, ReportFieldType

class ReportCreate(BaseModel):
  name: str
  parent_id: UUID
  template_id: UUID

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