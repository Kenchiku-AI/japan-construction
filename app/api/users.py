from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from uuid import uuid4
import secrets

from app.core.dependencies import get_current_user
from app.core.config import settings
from app.core.security import hash_token
from app.db.models.user import User
from app.db.models.password_reset_token import PasswordResetToken
from app.db.session import get_db
from app.schemas.user import UserWithCompanyAndProjects, UserBase
from app.services.users import build_user_with_company_and_projects
from app.services.email import send_password_reset_email

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

@router.post("/create-admin")
async def create_admin(
  payload: UserBase,
  background_tasks: BackgroundTasks,
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

  token = secrets.token_urlsafe(32)
  hashed_token = hash_token(token)
  expires_at = datetime.utcnow() + timedelta(minutes=30)
  
  reset_entry = PasswordResetToken(
    id=str(uuid4()),
    user_id=user.id,
    token_hash=hashed_token,
    expires_at=expires_at,
  )

  db.add(reset_entry)
  await db.commit()

  background_tasks.add_task(
    send_password_reset_email,
    user.email,
    token,
  )

  return {"success": True}