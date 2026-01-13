from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, EmailStr

class CompanyRole(str, Enum):
  manager = "manager"
  member = "member"

class CompanyBase(BaseModel):
  name: str

class CompanyCreate(CompanyBase):
  manager_email: Optional[EmailStr] = None
  pass

class CompanyUpdate(BaseModel):
  name: str | None = None

class CompanyUserBase(BaseModel):
  role: CompanyRole

class CompanyUserRead(CompanyUserBase):
  user_id: str
  email: EmailStr

  class Config:
    from_attributes = True

class CompanyRead(CompanyBase):
  id: str
  created_at: datetime
  updated_at: datetime
  users: List[CompanyUserRead] = []

  class Config:
    from_attributes = True
