from datetime import datetime
from pydantic import BaseModel

class ProjectBase(BaseModel):
  name: str
  description: str | None = None

class ProjectCreate(ProjectBase):
  pass

class ProjectUpdate(ProjectBase):
  pass

class ProjectRead(ProjectBase):
  id: int
  company_id: int
  created_at: datetime
  updated_at: datetime

  class Config:
    from_attributes = True
