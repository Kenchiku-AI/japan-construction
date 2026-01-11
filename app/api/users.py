from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from app.db.session import get_db
from app.db.models.user import User
from app.schemas.user import UserRead, UserWithCompanies
from app.core.dependencies import get_current_user

router = APIRouter(
  prefix="/users",
  tags=["users"],
)

@router.get("/me", response_model=UserWithCompanies)
def read_current_user(
  current_user: User = Depends(get_current_user),
):
  return UserWithCompanies(
    id=current_user.id,
    email=current_user.email,
    is_active=current_user.is_active,
    created_at=current_user.created_at,
    updated_at=current_user.updated_at,
    companies=[
      {
        "company_id": cu.company_id,
        "company_name": cu.company.name,
        "role": cu.role,
      }
      for cu in current_user.company_users
    ],
  )