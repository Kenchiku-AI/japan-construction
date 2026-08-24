from datetime import datetime
from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.schemas.custom_field import CustomFieldRead
from app.schemas.custom_relationship import CustomRelationshipRead

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

class UserWithCompanyIdAndRole(UserBase):
  id: UUID
  company_id: Optional[UUID] = None
  role: UserRole
  custom_fields: List[CustomFieldRead] = Field(default_factory=list)
  custom_relationships: List[CustomRelationshipRead] = Field(default_factory=list)

  model_config = {
    "from_attributes": True
  }

class UserUpdate(BaseModel):
  email: Optional[EmailStr] = None
  first_name: Optional[str] = None
  last_name: Optional[str] = None
  role: Optional[UserRole] = None

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
  status: str

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