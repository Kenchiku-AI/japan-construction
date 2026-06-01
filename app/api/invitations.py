from datetime import datetime
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.core.security import hash_token, create_access_token, get_cookie_settings
from app.db.session import get_db
from app.db.models import User, Project, ProjectGuestLink, Company, Invitation
from app.schemas.invitation import (
  CompanyInvitationCreate,
  ProjectGuestInvitationCreate,
  ProjectGuestInvitationRead,
  InvitationRead,
  InvitationAccept,
)
from app.services.invitations import create_company_invitation, create_project_guest_invitation
from app.services.users import build_user_with_company_and_projects

router = APIRouter(
  prefix="/invitations",
  tags=["invitations"]
)

@router.post("/company", response_model=InvitationRead)
async def invite_to_company(
  payload: CompanyInvitationCreate,
  background_tasks: BackgroundTasks,
  current_user: User = Depends(get_current_user),
  db: AsyncSession = Depends(get_db),
):
  invitation = await create_company_invitation(
    payload, db, current_user, background_tasks
  )
  return InvitationRead(
    id=invitation.id,
    email=invitation.email,
    company_id=invitation.company_id,
    role=invitation.role,
    expires_at=invitation.expires_at,
  )

@router.post("/project-guest", response_model=ProjectGuestInvitationRead)
async def invite_project_guest(
  payload: ProjectGuestInvitationCreate,
  background_tasks: BackgroundTasks,
  current_user: User = Depends(get_current_user),
  db: AsyncSession = Depends(get_db),
):
  link = await create_project_guest_invitation(
    payload, db, current_user, background_tasks
  )
  return ProjectGuestInvitationRead(
    id=link.id,
    project_id=link.project_id,
    user_id=link.user_id,
  )

@router.post("/accept")
async def accept_invitation(
  payload: InvitationAccept,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
  x_client_type: str | None = Header(default=None),
):
  hashed_token = hash_token(payload.token)
  result = await db.execute(
    select(Invitation).where(Invitation.token_hash == hashed_token)
  )
  invitation = result.scalars().first()

  if not invitation:
    raise HTTPException(status_code=404, detail="Invitation not found or invalid")

  if invitation.expires_at < datetime.utcnow():
    raise HTTPException(status_code=400, detail="Invitation expired")

  if invitation.email != current_user.email:
    raise HTTPException(
      status_code=403,
      detail="This invitation was not issued to your account",
    )

  if current_user.company_id is not None:
    raise HTTPException(
      status_code=400,
      detail="You already belong to a company",
    )

  company = await db.get(Company, invitation.company_id)
  if not company:
    raise HTTPException(status_code=404, detail="Company not found")

  current_user.company_id = company.id
  current_user.role = invitation.role

  await db.execute(
    delete(ProjectGuestLink)
    .where(
      ProjectGuestLink.user_id == current_user.id,
      ProjectGuestLink.project_id.in_(
        select(Project.id).where(Project.company_id == company.id)
      ),
    )
  )

  await db.delete(invitation)
  await db.commit()
  await db.refresh(current_user)

  if x_client_type == "web":
    user_with_projects = await build_user_with_company_and_projects(current_user, db)
    response = JSONResponse(content=user_with_projects.model_dump(mode="json"))
    cookie_settings = get_cookie_settings()
    response.set_cookie(key="accessToken", value=create_access_token({"sub": str(current_user.id)}), **cookie_settings)
    return response

  return {"success": True}