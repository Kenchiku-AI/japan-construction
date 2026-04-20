from fastapi import APIRouter, Depends

from app.core.dependencies import get_current_user
from app.core.config import settings
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

@router.post("/create-admin", response_model=TokenSchema)
async def create_admin(
  payload: AdminCreate,
  current_user: User = Depends(get_current_user),
  db: AsyncSession = Depends(get_db),
):
  if current_user.email != settings.SUPER_USER_EMAIL:
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Only super user can create admins",
    )

  result = await db.execute(
    select(User).where(User.email == payload.email)
  )
  existing_user = result.scalar_one_or_none()

  if existing_user:
    raise HTTPException(
      status_code=400,
      detail="Email already registered",
    )

  user = User(
    email=payload.email,
    first_name=payload.first_name,
    last_name=payload.last_name,
    role="admin"
  )

  db.add(user)
  await db.commit()
  await db.refresh(user)

  return {"success": True}