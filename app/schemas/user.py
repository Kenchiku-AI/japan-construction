from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field

class UserBase(BaseModel):
  email: EmailStr
  is_active: bool = True

class UserCreate(UserBase):
  password: str = Field(..., min_length=6)

class UserRead(UserBase):
  id: str
  created_at: datetime
  updated_at: datetime

  class Config:
    from_attributes = True

class CompanyRole(BaseModel):
  company_id: str
  company_name: str
  role: str

  class Config:
    from_attributes = True

class UserWithCompanies(UserRead):
  companies: List[CompanyRole] = []

  class Config:
    from_attributes = True