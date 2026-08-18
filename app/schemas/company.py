from datetime import datetime
from enum import Enum
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr

from app.schemas.user import UserRole
from app.schemas.conversation import ConversationItemTypeRead
from app.schemas.custom_field import CustomFieldRead

class CompanyCreate(BaseModel):
  name: str
  corporate_number: Optional[str] = None
  manager_email: Optional[EmailStr] = None
  pass

class CompanyRead(BaseModel):
  id: UUID
  name: str
  corporate_number: Optional[str]
  line_channel_secret_last4: Optional[str]
  line_channel_access_token_last5: Optional[str]
  line_channel_access_token_invalid: bool
  billing_plan_id: Optional[UUID] = None
  paid_features_force_disabled: bool
  created_at: datetime
  updated_at: datetime

  model_config = {
    "from_attributes": True
  }

class CompanyCreateResponse(BaseModel):
  company: CompanyRead
  invitation_id: UUID | None = None

class CompanyWithMetrics(BaseModel):
  id: UUID
  name: str
  corporate_number: Optional[str]
  active_projects_count: int
  new_projects_count: int
  finished_projects_count: int
  recent_reports_count: int
  active_project_guests_count: int
  employees_count: int
  billing_plan_id: Optional[UUID] = None
  created_at: datetime
  updated_at: datetime

class CompanyProjectRead(BaseModel):
  id: UUID
  name: str
  description: str

  model_config = {
    "from_attributes": True
  }

class CompanyUpdate(BaseModel):
  name: str | None = None
  corporate_number: str | None = None
  line_channel_secret: str | None = None
  line_channel_access_token: str | None = None
  billing_plan_id: Optional[UUID] = None
  paid_features_force_disabled: bool | None = None

class CompanyUserRead(BaseModel):
  id: UUID
  first_name: str
  last_name: str
  email: EmailStr
  role: UserRole

  model_config = {
    "from_attributes": True
  }

class CompanyWithLists(CompanyRead):
  payment_method_name: Optional[str]
  is_payment_method_valid: bool
  free_trial_days_left: int | None = None
  projects: List[CompanyProjectRead]
  users: List[CompanyUserRead]
  custom_fields: List[CustomFieldRead] = []

  model_config = {
    "from_attributes": True
  }

class ImageTagCreate(BaseModel):
  name: str
  description: str

class ImageTagUpdate(BaseModel):
  name: str | None = None
  description: str | None = None
