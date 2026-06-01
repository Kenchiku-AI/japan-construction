from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, EmailStr, model_validator
from typing import Optional
from app.schemas.user import UserRole

class CompanyInvitationCreate(BaseModel):
  email: EmailStr
  company_id: UUID
  role: UserRole

class ProjectGuestInvitationCreate(BaseModel):
  email: EmailStr
  project_id: UUID
  first_name: Optional[str] = None
  last_name: Optional[str] = None

class ProjectGuestInvitationRead(BaseModel):
  id: UUID
  project_id: UUID
  user_id: UUID

  model_config = {"from_attributes": True}

class InvitationRead(BaseModel):
  id: UUID
  email: EmailStr
  company_id: UUID
  role: UserRole
  expires_at: datetime

  model_config = {"from_attributes": True}

class InvitationAccept(BaseModel):
  token: str