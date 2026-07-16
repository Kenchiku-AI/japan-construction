import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.security import generate_invite_token, hash_token
from app.core.dependencies import require_company_manager
from app.db.models import User, Company, Invitation, Project
from app.db.models.project_guest_link import ProjectGuestLink
from app.db.models.password_reset_token import PasswordResetToken
from app.schemas.invitation import CompanyInvitationCreate, ProjectGuestInvitationCreate
from app.services.email import (
  send_company_created_email,
  send_existing_user_invitation_email,
  send_guest_invitation_email,
  send_project_guest_access_email,
)

INVITE_EXPIRATION_HOURS = 48

async def create_company_invitation(
  payload: CompanyInvitationCreate,
  db: AsyncSession,
  current_user: User | None,
  background_tasks: BackgroundTasks,
) -> Invitation:
  if current_user is not None:
    require_company_manager(current_user, payload.company_id)

  company = await db.get(Company, payload.company_id)
  if not company:
    raise HTTPException(status_code=404, detail="Company not found")

  result = await db.execute(select(User).where(User.email == payload.email))
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
    expires_at=datetime.now(timezone.utc) + timedelta(hours=INVITE_EXPIRATION_HOURS),
  )

  db.add(invitation)
  await db.commit()
  await db.refresh(invitation)

  if user:
    background_tasks.add_task(
      send_existing_user_invitation_email,
      payload.email,
      company.name,
      token,
    )
  else:
    background_tasks.add_task(
      send_company_created_email,
      payload.email,
      company.name,
      token,
    )

  return invitation

async def create_project_guest_invitation(
  payload: ProjectGuestInvitationCreate,
  db: AsyncSession,
  current_user: User,
  background_tasks: BackgroundTasks,
) -> ProjectGuestLink:
  project = await db.get(Project, payload.project_id)
  if not project:
    raise HTTPException(status_code=404, detail="Project not found")

  require_company_manager(current_user, project.company_id)

  company = await db.get(Company, project.company_id)
  if not company:
    raise HTTPException(status_code=404, detail="Company not found")

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
      company_name=company.name,
    )

    return link

  else:
    new_user = User(
      email=payload.email,
      role="user",
      company_id=None,
      first_name=payload.first_name,
      last_name=payload.last_name
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
      expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db.add(reset_entry)
    await db.commit()

    background_tasks.add_task(
      send_guest_invitation_email,
      email=payload.email,
      project_name=project.name,
      company_name=company.name,
      set_password_token=token,
    )

    return link