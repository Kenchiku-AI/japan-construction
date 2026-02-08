from datetime import datetime
from pydantic import BaseModel
from typing import Optional
from uuid import UUID

from app.schemas.company import CompanyRead

class ProjectBase(BaseModel):
  name: str
  description: str | None = None

class ProjectCreate(ProjectBase):
  company_id: UUID

class ProjectUpdate(ProjectBase):
  pass

class ProjectRead(ProjectBase):
  id: UUID
  status: str

  model_config = {
    "from_attributes": True
  }

class ProjectWithCompany(ProjectRead):
  company: CompanyRead