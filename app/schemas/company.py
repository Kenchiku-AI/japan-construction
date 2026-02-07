from datetime import datetime
from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr

from app.schemas.daily_report import DailyReportRead
from app.schemas.user import UserRole

class CompanyCreate(BaseModel):
  name: str
  corporate_number: str
  manager_email: Optional[EmailStr] = None
  pass

class CompanyUpdate(BaseModel):
  name: str | None = None

class CompanyUserRead(BaseModel):
  user_id: UUID
  first_name: str
  last_name: str
  email: EmailStr

  class Config:
    from_attributes = True

class CompanyRead(BaseModel):
  id: UUID
  name: str
  corporate_number: Optional[str]
  created_at: datetime
  updated_at: datetime

  model_config = {
    "from_attributes": True
  }

class CompanyProjectRead(BaseModel):
  id: UUID
  name: str
  description: str

  model_config = {
    "from_attributes": True
  }

class CompanyUserRead(BaseModel):
  id: UUID
  first_name: str
  last_name: str
  email: EmailStr
  role: UserRole

  model_config = {
    "from_attributes": True
  }

class CompanyWithProjectsAndUsers(CompanyRead):
  projects: List[CompanyProjectRead]
  users: List[CompanyUserRead]

  model_config = {
    "from_attributes": True
  }
