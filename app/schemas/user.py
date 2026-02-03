from datetime import datetime
from enum import Enum
from typing import List, Optional
from app.schemas.project import ProjectRead
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class UserRole(str, Enum):
  admin = "admin"
  manager = "manager"
  user = "user"

class UserBase(BaseModel):
  email: EmailStr
  first_name: Optional[str] = None
  last_name: Optional[str] = None

class UserCreate(UserBase):
  password: str = Field(..., min_length=8)

class UserRead(UserBase):
  id: UUID
  created_at: datetime
  updated_at: datetime

  class Config:
    from_attributes = True

class UserWithProjects(BaseModel):
  id: UUID
  first_name: str
  last_name: str
  email: str
  created_at: datetime
  updated_at: datetime
  role: UserRole 
  projects: List[ProjectRead]

  class Config:
    from_attributes = True