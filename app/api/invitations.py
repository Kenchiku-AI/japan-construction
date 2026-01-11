from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, status
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import secrets

from app.db.session import get_db
from app.db.models import User, Company, CompanyUser, Invitation
from app.schemas.invitation import InvitationCreate, InvitationRead
from app.core.dependencies import get_current_user
from app.core.security import hash_token, generate_invite_token
from app.core.email import send_invitation_email

router = APIRouter(
  prefix="/invitations",
  tags=["invitations"]
)

INVITE_EXPIRATION_HOURS = 48

@router.post("/invite", response_model=InvitationRead)
def invite_user(
  payload: InvitationCreate,
  current_user: User = Depends(get_current_user),
  db: Session = Depends(get_db)
):
  company = db.get(Company, payload.company_id)
  if not company:
    raise HTTPException(status_code=404, detail="Company not found")

  admin_relation = db.query(CompanyUser).filter(
    CompanyUser.user_id == current_user.id,
    CompanyUser.company_id == company.id,
    CompanyUser.role == "admin"
  ).first()
  if not admin_relation:
    raise HTTPException(status_code=403, detail="Forbidden: must be admin of this company")

  user = db.query(User).filter(User.email == payload.email).first()

  token = generate_invite_token()
  hashed_token = hash_token(token)
  expires_at = datetime.utcnow() + timedelta(hours=INVITE_EXPIRATION_HOURS)

  if not user:
    user = User(
      email=payload.email,
      full_name=payload.full_name or "",
      is_active=False,
      role="user"
    )
    db.add(user)
    db.flush()

  invitation = Invitation(
    token=hashed_token,
    user_id=user.id,
    company_id=company.id,
    created_by=current_user.id,
    expires_at=expires_at
  )
  db.add(invitation)
  db.commit()
  db.refresh(invitation)

  background_tasks.add_task(send_invitation_email, user.email, company.name, token)

  return InvitationRead(
    id=invitation.id,
    email=user.email,
    company_id=company.id,
    expires_at=expires_at,
    created_by=current_user.id
  )

@router.post("/accept/{token}", response_model=InvitationRead)
def accept_invitation(token: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
  hashed_token = hash_token(token)
  invitation = db.query(Invitation).filter(Invitation.token == hashed_token).first()

  if not invitation:
    raise HTTPException(status_code=404, detail="Invitation not found or invalid")
  if invitation.expires_at < datetime.utcnow():
    raise HTTPException(status_code=400, detail="Invitation expired")

  user = db.get(User, invitation.user_id)
  company = db.get(Company, invitation.company_id)

  existing_relation = db.query(CompanyUser).filter(
    CompanyUser.user_id == user.id,
    CompanyUser.company_id == company.id
  ).first()
  if not existing_relation:
    relation = CompanyUser(user_id=user.id, company_id=company.id, role="user")
    db.add(relation)

  if not user.is_active:
    user.is_active = True

  db.delete(invitation)
  db.commit()
  db.refresh(invitation)

  return InvitationRead(
    id=invitation.id,
    email=user.email,
    company_id=company.id,
    expires_at=invitation.expires_at,
    created_by=invitation.created_by
  )
