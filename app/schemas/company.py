from datetime import datetime
from enum import Enum
from typing import List

from pydantic import BaseModel, EmailStr

class CompanyRole(str, Enum):
  admin = "admin"
  member = "member"

class CompanyBase(BaseModel):
  name: str

class CompanyCreate(CompanyBase):
  pass

class CompanyUpdate(BaseModel):
  name: str | None = None

class CompanyUserBase(BaseModel):
  role: CompanyRole

class CompanyUserRead(CompanyUserBase):
  user_id: int
  email: EmailStr

  class Config:
    from_attributes = True

class CompanyRead(CompanyBase):
  id: int
  created_at: datetime
  updated_at: datetime
  users: List[CompanyUserRead] = []

  class Config:
    from_attributes = True
