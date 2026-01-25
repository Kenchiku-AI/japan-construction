from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from app.db.session import get_db
from app.db.models.user import User
from app.schemas.user import UserWithProjects
from app.core.dependencies import get_current_user

router = APIRouter(
  prefix="/users",
  tags=["users"],
)

@router.get("/me", response_model=UserWithProjects)
def read_current_user(
  current_user: User = Depends(get_current_user),
):
  today = date.today()
  projects_data = []

  for project in current_user.projects:
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
            title=todays_report_obj.title,
            created_at=todays_report_obj.created_at,
          )
          if todays_report_obj
          else None
        ),
      )
    )

  return UserWithProjects(
    id=current_user.id,
    email=current_user.email,
    is_active=current_user.is_active,
    created_at=current_user.created_at,
    updated_at=current_user.updated_at,
    projects=projects_data,
  )