from datetime import datetime, timedelta
from fastapi import HTTPException, status, APIRouter, Depends, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from uuid import uuid4, UUID
import secrets

from app.core.dependencies import get_current_user
from app.core.config import settings
from app.core.security import hash_token, generate_unique_line_link_code
from app.db.models.user import User
from app.db.models.password_reset_token import PasswordResetToken
from app.db.session import get_db
from app.schemas.user import UserWithCompanyAndProjects, UserBase, UserUpdate, UserWithCompanyIdAndRole
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

@router.get("/{user_id}", response_model=UserWithCompanyIdAndRole)
async def get_user(
  user_id: UUID,
  current_user: User = Depends(get_current_user),
  db: AsyncSession = Depends(get_db),
):
  if current_user.role != "admin" and current_user.id != user_id:
    target_company_id = (
      await db.execute(select(User.company_id).where(User.id == user_id))
    ).scalar_one_or_none()

    is_same_company = (
      current_user.company_id is not None
      and current_user.company_id == target_company_id
    )

    if not is_same_company:
      shared_project = (
        await db.execute(
          select(Project.id)
          .join(ProjectGuestLink, ProjectGuestLink.project_id == Project.id)
          .where(
            or_(
              and_(
                Project.company_id == current_user.company_id,
                ProjectGuestLink.user_id == user_id,
              ),
              and_(
                ProjectGuestLink.user_id == user_id,
                Project.id.in_(
                  select(ProjectGuestLink.project_id)
                  .where(ProjectGuestLink.user_id == current_user.id)
                ),
              ),
              and_(
                ProjectGuestLink.user_id == current_user.id,
                Project.company_id == target_company_id,
              ),
            )
          )
          .limit(1)
        )
      ).first()

      if not shared_project:
        raise HTTPException(
          status_code=status.HTTP_403_FORBIDDEN,
          detail="Not authorized to access this user",
        )

  result = await db.execute(select(User).where(User.id == user_id))
  user = result.scalar_one_or_none()

  if not user:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="User not found",
    )

  return user

@router.patch("/{user_id}", response_model=UserWithCompanyIdAndRole)
async def patch_user(
  user_id: UUID,
  payload: UserUpdate,
  current_user: User = Depends(get_current_user),
  db: AsyncSession = Depends(get_db),
):
  result = await db.execute(select(User).where(User.id == user_id))
  user = result.scalar_one_or_none()

  if not user:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="User not found",
    )

  update_data = payload.model_dump(exclude_unset=True, exclude_none=True)
  is_own_account = current_user.id == user_id

  if user.role == "admin" and current_user.role != "admin":
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Only admins can update an admin's account",
    )

  if current_user.role == "manager":
    if update_data.get("role") == "admin":
      raise HTTPException(
        status_code=403,
        detail="Cannot assign admin role",
      )

    if is_own_account:
      pass
    else:
      if current_user.company_id != user.company_id:
        raise HTTPException(
          status_code=status.HTTP_403_FORBIDDEN,
          detail="Managers can only manage users in their company",
        )

      non_role_fields = {k: v for k, v in update_data.items() if k != "role"}
      if non_role_fields:
        raise HTTPException(
          status_code=status.HTTP_403_FORBIDDEN,
          detail="Managers can only update the role of other users",
        )

      if "role" in update_data:
        if user.role == "manager":
          raise HTTPException(
            status_code=403,
            detail="Managers cannot change another manager's role",
          )

  elif current_user.role == "user":
    if not is_own_account:
      raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Not authorized to update this user",
      )
    if "role" in update_data:
      raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Users cannot change their own role",
      )

  if "email" in update_data and update_data["email"] != user.email:
    existing = await db.execute(
      select(User).where(User.email == update_data["email"])
    )
    if existing.scalar_one_or_none():
      raise HTTPException(
        status_code=400,
        detail="Email already in use",
      )

  for field, value in update_data.items():
    setattr(user, field, value)

  await db.commit()
  await db.refresh(user)

  return user

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

  line_link_code = await generate_unique_line_link_code(db)
  user = User(
    email=payload.email,
    first_name=payload.first_name,
    last_name=payload.last_name,
    role="admin",
    line_link_code=line_link_code
  )
  db.add(user)
  await db.flush()

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

@router.delete("/{user_id}/company", status_code=status.HTTP_204_NO_CONTENT)
async def remove_user_from_company(
  user_id: UUID,
  current_user: User = Depends(get_current_user),
  db: AsyncSession = Depends(get_db),
):
  if current_user.role == "user":
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Not authorized to remove users from a company",
    )

  result = await db.execute(
    select(User).where(User.id == user_id)
  )
  target = result.scalar_one_or_none()

  if not target:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="User not found",
    )

  if target.company_id is None:
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="User is not a member of any company",
    )

  if target.role == "admin":
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Cannot remove an admin from a company",
    )

  if current_user.role == "manager":
    if current_user.company_id != target.company_id:
      raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Managers can only remove users from their own company",
      )

  target.company_id = None
  target.role = "user"

  await db.commit()