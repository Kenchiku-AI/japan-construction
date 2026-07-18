from typing import Dict, List, Optional
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel

from app.db.models.report import ReportParentType, ReportStatus

class ReportCreate(BaseModel):
  name: str
  parent_id: UUID
  template_id: UUID

class ReportUpdate(BaseModel):
  name: Optional[str] = None
  field_values: Optional[Dict[UUID, str]] = None
  status: ReportStatus | None = None

class ReportFieldRead(BaseModel):
  id: UUID
  name: str
  value: str | None = None
  order: int

  model_config = {
    "from_attributes": True
  }

class ReportRead(BaseModel):
  id: UUID
  name: str
  template_id: UUID
  parent_id: UUID
  parent_type: ReportParentType
  status: ReportStatus
  created_at: datetime
  updated_at: datetime
  fields: List[ReportFieldRead]
  company_id: Optional[UUID] = None
  photo_count: int = 0

  model_config = {
    "from_attributes": True
  }

class ReportWithCompanyAndProjectName(ReportRead):
  company_name: str | None = None
  project_name: str | None = None

class ReportDetail(ReportWithCompanyAndProjectName):
  disabled: bool

class ReportTemplateFieldCreate(BaseModel):
  name: str
  description: Optional[str] = None
  order: int

class ReportTemplateCreate(BaseModel):
  name: str
  description: Optional[str] = None
  parent_type: ReportParentType
  fields: List[ReportTemplateFieldCreate]

class ReportTemplateFieldRead(BaseModel):
  id: UUID
  name: str
  description: Optional[str] = None
  order: int

class ReportTemplateRead(BaseModel):
  id: UUID
  name: str
  description: Optional[str] = None
  parent_type: ReportParentType
  is_global: bool
  fields: List[ReportTemplateFieldRead]

class ReportTemplateFieldUpdate(BaseModel):
  name: str
  description: str
  order: int

class ReportTemplateUpdate(BaseModel):
  name: str | None = None
  description: str | None = None
  parent_type: str | None = None
  fields: list[ReportTemplateFieldUpdate] | None = None

class ShareReportTemplateRequest(BaseModel):
  company_id: UUID
  template_id: UUID
  
class ReportSpeechRequest(BaseModel):
  text: str
  output_language: str

class ReportSpeechResponse(BaseModel):
  field_values: dict

class ReportImageCreate(BaseModel):
  width: int
  height: int

class ReportImageUpdate(BaseModel):
  description: Optional[str] = None

class ReportImageTagCreate(BaseModel):
  tag_id: UUID

class ReportLineConversationRange(BaseModel):
  conversation_id: UUID
  start_time: datetime
  end_time: datetime

class ReportLineConversationRequest(BaseModel):
  conversations: list[ReportLineConversationRange]
  output_language: str = "ja"