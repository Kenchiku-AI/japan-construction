# app/schemas/invitations.py
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, EmailStr
from typing import Optional

class CompanyRole(str, Enum):
  admin = "admin"
  member = "member"

class InvitationBase(BaseModel):
  email: EmailStr
  company_id: int
  role: CompanyRole = CompanyRole.member

class InvitationCreate(InvitationBase):
  pass

class InvitationAccept(BaseModel):
  token: str

class InvitationRead(BaseModel):
  id: int
  email: EmailStr
  company_id: int
  role: CompanyRole
  accepted: bool
  created_at: datetime
  updated_at: datetime

  class Config:
    orm_mode = True

class InvitationTokenResponse(BaseModel):
    invite_token: str
