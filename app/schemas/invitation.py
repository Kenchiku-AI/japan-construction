# app/schemas/invitations.py
from datetime import datetime
from pydantic import BaseModel, EmailStr
from typing import Optional
from app.schemas.user import UserRole

class InvitationBase(BaseModel):
  email: EmailStr
  company_id: str
  role: UserRole

class InvitationCreate(InvitationBase):
  pass

class InvitationRead(InvitationBase):
  id: str
  expires_at: datetime

  model_config = {
    "from_attributes": True
  }

class InvitationAccept(BaseModel):
  token: str

class InvitationTokenResponse(BaseModel):
  invite_token: str
