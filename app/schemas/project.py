from datetime import datetime
from pydantic import BaseModel
from app.schemas.daily_report import DailyReportRead
from app.schemas.company import CompanyRead
from typing import Optional
from uuid import UUID

class ProjectBase(BaseModel):
  name: str
  description: str | None = None

class ProjectCreate(ProjectBase):
  pass

class ProjectUpdate(ProjectBase):
  pass

class ProjectRead(BaseModel):
  id: UUID
  name: str
  description: str
  todays_report: Optional[DailyReportRead] = None
  company: CompanyRead

  class Config:
    orm_mode = True

class CompanyRead(BaseModel):
  id: UUID
  name: str

  class Config:
    orm_mode = True
