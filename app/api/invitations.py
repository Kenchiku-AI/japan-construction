import secrets
from datetime import datetime, timedelta
from uuid import uuid4

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.security import generate_invite_token, hash_token
from app.core.dependencies import require_company_manager
from app.db.models import User, Company, Invitation, Project
from app.db.models.project_guest_link import ProjectGuestLink
from app.db.models.password_reset_token import PasswordResetToken
from app.schemas.invitation import InvitationCreate
from app.services.email import (
  send_invitation_email,
  send_guest_invitation_email,
  send_project_guest_access_email,
)

INVITE_EXPIRATION_HOURS = 48

async def create_invitation(
  payload: InvitationCreate,
  db: AsyncSession,
  current_user: User,
  background_tasks: BackgroundTasks,
) -> Invitation | ProjectGuestLink:
  if payload.project_id and not payload.company_id:
    project = await db.get(Project, payload.project_id)

    if not project:
      raise HTTPException(status_code=404, detail="Project not found")

    require_company_manager(current_user, project.company_id)

    existing_user = (
      await db.execute(select(User).where(User.email == payload.email))
    ).scalar_one_or_none()

    if existing_user:
      existing_link = (
        await db.execute(
          select(ProjectGuestLink).where(
            ProjectGuestLink.user_id == existing_user.id,
            ProjectGuestLink.project_id == payload.project_id,
          )
        )
      ).scalar_one_or_none()

      if existing_link:
        raise HTTPException(
          status_code=409,
          detail="User is already a guest on this project",
        )

      link = ProjectGuestLink(
        user_id=existing_user.id,
        project_id=payload.project_id,
      )
      db.add(link)
      await db.commit()

      background_tasks.add_task(
        send_project_guest_access_email,
        email=existing_user.email,
        project_name=project.name,
      )

      return link

    else:
      new_user = User(
        email=payload.email,
        role="user",
        company_id=None,
      )
      db.add(new_user)
      await db.flush()

      link = ProjectGuestLink(
        user_id=new_user.id,
        project_id=payload.project_id,
      )
      db.add(link)
      await db.flush()

      token = secrets.token_urlsafe(32)
      reset_entry = PasswordResetToken(
        id=str(uuid4()),
        user_id=new_user.id,
        token_hash=hash_token(token),
        expires_at=datetime.utcnow() + timedelta(hours=24),
      )
      db.add(reset_entry)
      await db.commit()

      background_tasks.add_task(
        send_guest_invitation_email,
        email=payload.email,
        project_name=project.name,
        set_password_token=token,
      )

      return link

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

  invitation = Invitation(
    email=payload.email,
    token_hash=hashed_token,
    company_id=company.id,
    role=payload.role,
    expires_at=datetime.utcnow() + timedelta(hours=INVITE_EXPIRATION_HOURS),
  )

  db.add(invitation)
  await db.commit()
  await db.refresh(invitation)

  background_tasks.add_task(
    send_invitation_email,
    payload.email,
    company.name,
    token,
  )

  return invitation

@router.post("/accept-invitation")
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