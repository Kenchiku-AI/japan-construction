from datetime import datetime
from enum import Enum
from typing import List, Optional
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
  invitation_token: str

class UserRead(UserBase):
  id: UUID
  created_at: datetime
  updated_at: datetime

  model_config = {
    "from_attributes": True
  }

class UserCompanyRead(BaseModel):
  id: UUID
  name: str
  corporate_number: Optional[str]

  model_config = {
    "from_attributes": True
  }

class UserProjectRead(BaseModel):
  id: UUID
  name: str
  description: Optional[str] = None

  model_config = {
    "from_attributes": True
  }

class UserWithCompanyAndProjects(BaseModel):
  id: UUID
  first_name: Optional[str]
  last_name: Optional[str]
  email: str
  created_at: datetime
  updated_at: datetime
  role: UserRole
  company: Optional[UserCompanyRead] = None
  projects: List[UserProjectRead]

  model_config = {
    "from_attributes": True
  }