from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.db.models.form_job import (
  FormJobOrigin,
  FormJobStatus,
)


class FormJobCreate(BaseModel):
  company_id: UUID
  name: str
  description: str | None = None
  project_id: UUID | None = None
  filename: str
  content_type: str


class FormJobCreateResponse(BaseModel):
  id: UUID
  name: str
  status: FormJobStatus
  upload_url: str
  filename: str
  created_at: datetime


class FormJobFileResponse(BaseModel):
  id: UUID
  filename: str
  content_type: str | None
  is_input: bool
  created_at: datetime

  model_config = {
    "from_attributes": True,
  }


class FormJobResponse(BaseModel):
  id: UUID
  company_id: UUID
  project_id: UUID | None
  name: str
  description: str | None
  status: FormJobStatus
  origin: FormJobOrigin
  error: str | None
  files: list[FormJobFileResponse]
  created_at: datetime
  updated_at: datetime

  model_config = {
    "from_attributes": True,
  }

class FormJobDownloadFile(BaseModel):
  id: UUID
  filename: str
  content_type: str | None = None
  download_url: str


class FormJobDownloadResponse(BaseModel):
  form_job_id: UUID
  files: list[FormJobDownloadFile]