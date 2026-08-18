from datetime import datetime
from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

class UserRole(str, Enum):
  admin = "admin"
  manager = "manager"
  user = "user"

class CustomFieldDefinitionRead(BaseModel):
  id: UUID
  key: str
  name: str
  description: Optional[str] = None

  model_config = {
    "from_attributes": True
  }

class CustomFieldRead(BaseModel):
  id: Optional[UUID] = None
  value: Optional[str] = None
  definition: CustomFieldDefinitionRead

  model_config = {
    "from_attributes": True
  }

class UserBase(BaseModel):
  email: EmailStr
  first_name: Optional[str] = None
  last_name: Optional[str] = None

class UserCreate(UserBase):
  password: str = Field(..., min_length=8)
  invitation_token: str

class UserWithCompanyIdAndRole(UserBase):
  company_id: Optional[UUID] = None
  role: UserRole
  custom_fields: List[CustomFieldRead] = Field(default_factory=list)

  model_config = {
    "from_attributes": True
  }

class UserCustomFieldUpdate(BaseModel):
  custom_field_definition_id: UUID
  value: Optional[str] = None

class UserUpdate(BaseModel):
  email: Optional[EmailStr] = None
  first_name: Optional[str] = None
  last_name: Optional[str] = None
  role: Optional[UserRole] = None
  custom_fields: List[UserCustomFieldUpdate] = Field(default_factory=list)

class UserRead(UserBase):
  id: UUID
  custom_fields: List[CustomFieldRead] = Field(default_factory=list)
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
  custom_fields: List[CustomFieldRead] = Field(default_factory=list)

  model_config = {
    "from_attributes": True
  }