from datetime import date
from app.schemas.user import UserWithProjects
from app.schemas.project import ProjectRead, DailyReportRead
from app.schemas.company import CompanyRead
from app.db.models.user import User

def build_user_with_projects(user: User) -> UserWithProjects:
  today = date.today()
  projects_data = []

  if user.company:
    for project in user.company.projects:
      todays_report_obj = next(
        (r for r in project.daily_reports if r.created_at.date() == today),
        None
      )

      projects_data.append(
        ProjectRead(
          id=project.id,
          name=project.name,
          company=CompanyRead(
            id=project.company.id,
            name=project.company.name,
          ),
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

  return UserWithProjects(
    id=user.id,
    first_name=user.first_name,
    last_name=user.last_name,
    email=user.email,
    created_at=user.created_at,
    updated_at=user.updated_at,
    projects=projects_data,
  )
