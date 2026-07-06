import logging
from fastapi import BackgroundTasks
from sqlalchemy import select, delete, and_, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.user import UserCompanyRead, UserProjectRead, UserWithCompanyAndProjects
from app.db.models.user import User, UserLineLink
from app.db.models.company import Company
from app.db.models.project import Project
from app.db.models.project_guest_link import ProjectGuestLink
from app.services.billing import has_payment_method
from app.services.email import send_line_link_confirmation_email

logger = logging.getLogger(__name__)

async def build_user_with_company_and_projects(
  user: User,
  db: AsyncSession,
) -> UserWithCompanyAndProjects:
  projects_data: list[UserProjectRead] = []
  company = None
  guest_projects = []

  if user.role == "admin":
    projects_result = await db.execute(
      select(Project).order_by(Project.updated_at.desc()).limit(25)
    )
    projects = projects_result.scalars().all()

  elif user.company_id:
    user_result = await db.execute(
      select(User)
      .options(selectinload(User.company))
      .where(User.id == user.id)
    )
    user = user_result.scalar_one()
    company = user.company

    projects_result = await db.execute(
      select(Project)
      .where(Project.company_id == company.id)
      .order_by(Project.updated_at.desc())
      .limit(25)
    )
    projects = projects_result.scalars().all()

    guest_result = await db.execute(
      select(Project)
      .join(ProjectGuestLink, ProjectGuestLink.project_id == Project.id)
      .where(ProjectGuestLink.user_id == user.id)
      .order_by(Project.updated_at.desc())
    )
    guest_projects = guest_result.scalars().all()

  else:
    projects = []

    guest_result = await db.execute(
      select(Project)
      .join(ProjectGuestLink, ProjectGuestLink.project_id == Project.id)
      .where(ProjectGuestLink.user_id == user.id)
      .order_by(Project.updated_at.desc())
    )
    guest_projects = guest_result.scalars().all()

  seen: set = set()
  
  for project in list(projects) + list(guest_projects):
    if project.id not in seen:
      seen.add(project.id)
      projects_data.append(UserProjectRead(
        id=project.id,
        name=project.name,
        description=project.description,
        status=project.status,
      ))

  needs_payment_method = False

  # TEMPORARILY ALLOWING USE WITHOUT PAYMENT METHOD
  # if company:
  #   needs_payment_method = not await has_payment_method(company)

  return UserWithCompanyAndProjects(
    id=user.id,
    first_name=user.first_name,
    last_name=user.last_name,
    email=user.email,
    role=user.role,
    line_link_code=user.line_link_code,
    created_at=user.created_at,
    updated_at=user.updated_at,
    company=(
      UserCompanyRead(
        id=company.id,
        name=company.name,
        corporate_number=company.corporate_number,
        needs_payment_method=needs_payment_method
      )
      if company
      else None
    ),
    projects=projects_data,
  )

async def link_line_user(
  sender_id: str,
  candidate_code: str,
  company: Company,
  background_tasks: BackgroundTasks,
  db: AsyncSession,
  group_id: str | None = None,
) -> bool:
  """Returns True if a U- code was found and handled (even if unrecognized), False otherwise."""
  if not candidate_code.startswith("U-"):
    return False

  user_result = await db.execute(
    select(User).where(User.line_link_code == candidate_code)
  )
  user_by_code = user_result.scalar_one_or_none()

  if user_by_code:
    await db.execute(
      delete(UserLineLink).where(
        or_(
          and_(
            UserLineLink.user_id == user_by_code.id,
            UserLineLink.company_id == company.id,
          ),
          and_(
            UserLineLink.company_id == company.id,
            UserLineLink.line_user_id == sender_id,
          ),
        )
      )
    )
    db.add(UserLineLink(
      user_id=user_by_code.id,
      company_id=company.id,
      line_user_id=sender_id,
    ))
    await db.commit()

    logger.info(
      "LINE account linked | company_id=%s user_id=%s sender_id=%s group_id=%s",
      company.id, user_by_code.id, sender_id, group_id,
    )

    background_tasks.add_task(
      send_line_link_confirmation_email,
      email=user_by_code.email,
      company_name=company.name,
    )
  else:
    logger.warning(
      "LINE unrecognized user code | company_id=%s sender_id=%s code=%s group_id=%s",
      company.id, sender_id, candidate_code, group_id,
    )

  return True