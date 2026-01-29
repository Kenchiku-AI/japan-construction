from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from datetime import date

from app.db.session import get_db
from app.db.models.user import User
from app.schemas.user import UserWithProjects
from app.core.dependencies import get_current_user

router = APIRouter(
  prefix="/users",
  tags=["users"],
)

@router.get("/me", response_model=UserWithProjects)
async def read_current_user(
  current_user: User = Depends(get_current_user),
):
  today = date.today()
  projects_data = []

  if current_user.company:
    for project in current_user.company.projects:
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
              weather=todays_report_obj.weather
            )
            if todays_report_obj
            else None
          ),
        )
      )

  return UserWithProjects(
    id=current_user.id,
    first_name=current_user.first_name,
    last_name=current_user.last_name,
    email=current_user.email,
    created_at=current_user.created_at,
    updated_at=current_user.updated_at,
    projects=projects_data,
  )