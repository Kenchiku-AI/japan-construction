from fastapi import APIRouter, Depends

from app.core.dependencies import get_current_user
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.user import UserWithCompanyAndProjects
from app.services.users import build_user_with_company_and_projects
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(
  prefix="/users",
  tags=["users"],
)

@router.get("/me", response_model=UserWithCompanyAndProjects)
async def read_current_user(
  current_user: User = Depends(get_current_user),
  db: AsyncSession = Depends(get_db),
):
  return await build_user_with_company_and_projects(current_user, db)