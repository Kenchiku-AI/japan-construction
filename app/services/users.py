from datetime import date
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.user import UserCompanyRead, UserProjectRead, UserWithCompanyAndProjects
from app.db.models.user import User
from app.db.models.project import Project

async def build_user_with_company_and_projects(
  user: User,
  db: AsyncSession,
) -> UserWithCompanyAndProjects:
  today = date.today()
  projects_data: list[UserProjectRead] = []

  company = None

  if user.role == "admin":
    projects_result = await db.execute(
      select(Project)
      .order_by(Project.updated_at.desc())
      .limit(25)
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
  else:
    projects = []

  for project in projects:
    projects_data.append(
      UserProjectRead(
        id=project.id,
        name=project.name,
        description=project.description,
        status=project.status
      )
    )

  return UserWithCompanyAndProjects(
    id=user.id,
    first_name=user.first_name,
    last_name=user.last_name,
    email=user.email,
    role=user.role,
    created_at=user.created_at,
    updated_at=user.updated_at,
    company=(
      UserCompanyRead(
        id=company.id,
        name=company.name,
        corporate_number=company.corporate_number,
      )
      if company
      else None
    ),
    projects=projects_data,
  )
