# app/schemas/invitations.py
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, EmailStr
from typing import Optional

class UserRole(str, Enum):
  admin = "admin"
  manager = "manager"
  user = "user"

class InvitationBase(BaseModel):
  email: EmailStr
  company_id: str
  role: UserRole

class InvitationCreate(InvitationBase):
  pass

class InvitationRead(InvitationBase):
  id: str
  expires_at: datetime

  class Config:
    orm_mode = True

class InvitationAccept(BaseModel):
  token: str

class InvitationTokenResponse(BaseModel):
  invite_token: str
