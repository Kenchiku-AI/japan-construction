from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, EmailStr, model_validator
from typing import Optional
from app.schemas.user import UserRole


class InvitationBase(BaseModel):
  email: EmailStr
  company_id: Optional[UUID] = None
  project_id: Optional[UUID] = None
  role: UserRole

  @model_validator(mode="after")
  def check_target(self) -> "InvitationBase":
    if not self.company_id and not self.project_id:
      raise ValueError("Either company_id or project_id must be provided")
    if self.company_id and self.project_id:
      raise ValueError("Cannot specify both company_id and project_id")
    return self


class InvitationCreate(InvitationBase):
    pass


class InvitationRead(InvitationBase):
  id: UUID
  expires_at: datetime

  model_config = {
    "from_attributes": True
  }


class InvitationAccept(BaseModel):
  token: str


class InvitationTokenResponse(BaseModel):
  invite_token: str