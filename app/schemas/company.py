from datetime import datetime
from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr

class CompanyCreate(BaseModel):
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
  created_at: datetime
  updated_at: datetime
  users: List[CompanyUserRead] = []

  class Config:
    from_attributes = True
