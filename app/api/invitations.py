from datetime import datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.dependencies import get_current_user, require_company_manager
from app.core.security import hash_token, generate_invite_token
from app.db.session import get_db
from app.db.models import User, Company, Invitation
from app.schemas.invitation import InvitationCreate, InvitationRead
from app.services.email import send_invitation_email

router = APIRouter(
  prefix="/invitations",
  tags=["invitations"]
)

INVITE_EXPIRATION_HOURS = 48

@router.post("", response_model=InvitationRead)
async def invite_user(
  payload: InvitationCreate,
  background_tasks: BackgroundTasks,
  current_user: User = Depends(get_current_user),
  db: AsyncSession = Depends(get_db),
) -> InvitationRead:
  require_company_manager(current_user, payload.company_id)

  company = await db.get(Company, payload.company_id)
  if not company:
    raise HTTPException(status_code=404, detail="Company not found")

  result = await db.execute(select(User).filter(User.email == payload.email))
  user = result.scalars().first()

  if user and user.company_id is not None:
    raise HTTPException(
      status_code=409,
      detail="User already belongs to a company",
    )

  token = generate_invite_token()
  hashed_token = hash_token(token)
  expires_at = datetime.utcnow() + timedelta(hours=INVITE_EXPIRATION_HOURS)

  invitation = Invitation(
    email = payload.email,
    token_hash = hashed_token,
    company_id=company.id,
    role=payload.role,
    expires_at=expires_at
  )
  db.add(invitation)
  await db.commit()
  await db.refresh(invitation)

  background_tasks.add_task(send_invitation_email, payload.email, company.name, token)

  return InvitationRead(
    id=invitation.id,
    email=invitation.email,
    company_id=company.id,
    role=invitation.role,
    expires_at=expires_at,
  )