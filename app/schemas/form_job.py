from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.db.models.form_job import (
  FormJobOrigin,
  FormJobStatus,
)


class FormJobFileResponse(BaseModel):
  id: UUID
  filename: str
  content_type: str | None
  is_input: bool
  created_at: datetime

  model_config = {
    "from_attributes": True,
  }


class FormJobCreateResponse(BaseModel):
  id: UUID
  status: FormJobStatus
  upload_url: str
  filename: str
  created_at: datetime


class FormJobResponse(BaseModel):
  id: UUID
  company_id: UUID
  project_id: UUID | None
  instructions: str | None
  status: FormJobStatus
  origin: FormJobOrigin
  error: str | None
  files: list[FormJobFileResponse]
  created_at: datetime
  updated_at: datetime

  model_config = {
    "from_attributes": True,
  }