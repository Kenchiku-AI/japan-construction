from datetime import datetime
from typing import List, Optional
from app.schemas.project import ProjectRead
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

class UserBase(BaseModel):
  email: EmailStr

class UserCreate(UserBase):
  password: str = Field(..., min_length=6)

class UserRead(UserBase):
  id: UUID
  created_at: datetime
  updated_at: datetime
  is_active: bool

  class Config:
    from_attributes = True

class UserWithProjects(BaseModel):
  id: UUID
  email: str
  is_active: bool
  created_at: datetime
  updated_at: datetime
  projects: List[ProjectRead]

  class Config:
    from_attributes = True