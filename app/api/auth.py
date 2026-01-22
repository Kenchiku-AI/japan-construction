from datetime import timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.db.session import get_db
from app.db.models.user import User
from app.db.models.refresh_token import RefreshToken
from app.core.security import (
  verify_password,
  create_access_token,
  create_refresh_token,
  hash_token,
  hash_password,
)
from app.schemas.auth import LoginRequest, TokenSchema, TokenPayload
from app.schemas.user import UserCreate, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])

@router.post("/login", response_model=TokenSchema)
async def login(
  payload: LoginRequest,
  db: AsyncSession = Depends(get_db),
):
  result = await db.execute(
    select(User).where(User.email == payload.email)
  )
  user = result.scalar_one_or_none()

  if not user or not verify_password(form_data.password, user.hashed_password):
    raise HTTPException(
      status_code=status.HTTP_401_UNAUTHORIZED,
      detail="Invalid email or password",
    )

  access_token = create_access_token({"sub": str(user.id)})
  refresh_token_plain = create_refresh_token()
  hashed_refresh = hash_token(refresh_token_plain)

  db.add(
    RefreshToken(
      id=uuid4(),
      user_id=user.id,
      hashed_token=hashed_refresh,
    )
  )
  await db.commit()

  return TokenSchema(
    access_token=access_token,
    refresh_token=refresh_token_plain,
    token_type="bearer",
  )

@router.post("/refresh", response_model=TokenSchema)
async def refresh_token(
  payload: TokenPayload,
  db: AsyncSession = Depends(get_db),
):
  result = await db.execute(
    select(RefreshToken).where(
      RefreshToken.hashed_token == hash_token(payload.refresh_token)
    )
  )
  db_token = result.scalar_one_or_none()

  if not db_token:
    raise HTTPException(status_code=401, detail="Invalid refresh token")

  user_result = await db.execute(
    select(User).where(User.id == db_token.user_id)
  )
  user = user_result.scalar_one()

  access_token = create_access_token({"sub": str(user.id)})

  return TokenSchema(
    access_token=access_token,
    refresh_token=payload.refresh_token,
    token_type="bearer",
  )

@router.post("/signup", response_model=UserRead)
async def signup(
  user_in: UserCreate,
  db: AsyncSession = Depends(get_db),
):
  result = await db.execute(
    select(User).where(User.email == user_in.email)
  )
  existing_user = result.scalar_one_or_none()

  if existing_user:
    raise HTTPException(
      status_code=400,
      detail="Email already registered",
    )

  user = User(
    email=user_in.email,
    hashed_password=hash_password(user_in.password),
    is_active=True,
    role="user"
  )

  db.add(user)
  await db.commit()
  await db.refresh(user)

  return user