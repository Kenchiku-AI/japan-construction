from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.db.session import get_db
from app.db.models import User
from app.schemas.invitation import InvitationCreate, InvitationRead
from app.services.invitations import create_invitation

router = APIRouter(
  prefix="/invitations",
  tags=["invitations"]
)

@router.post("", response_model=InvitationRead)
async def invite_user(
  payload: InvitationCreate,
  background_tasks: BackgroundTasks,
  current_user: User = Depends(get_current_user),
  db: AsyncSession = Depends(get_db),
):
  invitation = await create_invitation(
    payload,
    db,
    current_user,
    background_tasks,
  )

  return InvitationRead(
    id=invitation.id,
    email=invitation.email,
    company_id=invitation.company_id,
    role=invitation.role,
    expires_at=invitation.expires_at,
  )