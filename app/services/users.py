from datetime import date
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.user import UserCompanyRead, UserProjectRead, UserWithCompanyAndProjects
from app.schemas.project import DailyReportRead
from app.db.models.user import User
from app.db.models.project import Project

async def build_user_with_company_and_projects(
  user: User,
  db: AsyncSession,
) -> UserWithCompanyAndProjects:
  today = date.today()
  projects_data: list[UserProjectRead] = []

  if user.role == "admin":
    result = await db.execute(
      select(Project)
      .order_by(Project.updated_at.desc())
      .limit(25)
    )
    projects = result.scalars().all()
  elif user.company:
    result = await db.execute(
      select(Project)
      .where(Project.company_id == user.company.id)
      .order_by(Project.updated_at.desc())
      .limit(25)
    )
    projects = result.scalars().all()
  else:
    projects = []

  for project in projects:
    todays_report_obj = next(
      (r for r in project.daily_reports if r.created_at.date() == today),
      None
    )

    projects_data.append(
      UserProjectRead(
        id=project.id,
        name=project.name,
        todays_report=(
          DailyReportRead(
            id=todays_report_obj.id,
            project_id=todays_report_obj.project_id,
            start_time=todays_report_obj.start_time,
            end_time=todays_report_obj.end_time,
            work_performed=todays_report_obj.work_performed,
            weather=todays_report_obj.weather,
          )
          if todays_report_obj
          else None
        ),
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
        id=user.company.id,
        name=user.company.name,
        corporate_number=user.company.corporate_number,
      )
      if user.company
      else None
    ),
    projects=projects_data,
  )
