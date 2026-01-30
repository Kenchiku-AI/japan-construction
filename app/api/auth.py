from datetime import datetime, timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.config import settings
from app.core.security import (
  verify_password,
  create_access_token,
  create_refresh_token,
  hash_password,
  hash_token,
)
from app.db.models.refresh_token import RefreshToken
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.auth import LoginRequest, TokenPayload, TokenSchema
from app.schemas.user import UserCreate
from app.services.users import build_user_with_projects

router = APIRouter(prefix="/auth", tags=["auth"])

@router.post("/login", response_model=TokenSchema)
async def login(
  payload: LoginRequest,
  db: AsyncSession = Depends(get_db),
  x_client_type: str | None = Header(default=None),
):
  result = await db.execute(
    select(User).where(User.email == payload.email)
  )
  user = result.scalar_one_or_none()

  if not user or not verify_password(payload.password, user.hashed_password):
    raise HTTPException(
      status_code=status.HTTP_401_UNAUTHORIZED,
      detail="Invalid email or password",
    )

  access_token = create_access_token({"sub": str(user.id)})
  refresh_token = create_refresh_token({"sub": str(user.id)})
  hashed_refresh_token = hash_token(refresh_token)

  db.add(
    RefreshToken(
      id=uuid4(),
      user_id=user.id,
      token_hash=hashed_refresh_token,
      expires_at=datetime.utcnow() + timedelta(days=30),
    )
  )
  await db.commit()

  if x_client_type == "web":
    response = JSONResponse(
      content=build_user_with_projects(user).model_dump(mode="json")
    )
    response.set_cookie(
      key="accessToken",
      value=access_token,
      httponly=True,
      secure=settings.SECURE_COOKIE,
      samesite="lax",
      path="/",
    )
    response.set_cookie(
      key="refreshToken",
      value=refresh_token,
      httponly=True,
      secure=settings.SECURE_COOKIE,
      samesite="lax",
      path="/",
    )
    return response

  return TokenSchema(
    access_token=access_token,
    refresh_token=refresh_token_plain,
    token_type="bearer",
  )

@router.post("/refresh", response_model=TokenSchema)
async def refresh_token(
  payload: TokenPayload,
  request: Request,
  db: AsyncSession = Depends(get_db),
  x_client_type: str | None = Header(default=None),
):
  refresh_token: str | None = None

  if x_client_type == "web":
    refresh_token = request.cookies.get("refreshToken")

    if not refresh_token:
      raise HTTPException(
        status_code=401,
        detail="Missing refresh token cookie",
      )
  else:
    if not payload or not payload.refresh_token:
      raise HTTPException(
        status_code=401,
        detail="Missing refresh token in body",
      )
    
    refresh_token = payload.refresh_token

  result = await db.execute(
    select(RefreshToken).where(
      RefreshToken.hashed_token == hash_token(refresh_token)
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

  if x_client_type == "web":
    response = JSONResponse(content={"token_type": "bearer"})
    response.set_cookie(
        key="accessToken",
        value=access_token,
        httponly=True,
        secure=settings.SECURE_COOKIE,
        samesite="lax",
        path="/",
    )
    return response

  return TokenSchema(
    access_token=access_token,
    refresh_token=payload.refresh_token,
    token_type="bearer",
  )

@router.post("/signup", response_model=TokenSchema)
async def signup(
  payload: UserCreate,
  request: Request,
  db: AsyncSession = Depends(get_db),
  x_client_type: str | None = Header(default=None),
):
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
    hashed_password=hash_password(payload.password),
    role="user"
  )

  db.add(user)
  await db.commit()
  await db.refresh(user)

  access_token = create_access_token({"sub": str(user.id)})
  refresh_token_plain = create_refresh_token({"sub": str(user.id)})
  hashed_refresh_token = hash_token(refresh_token_plain)

  db.add(
    RefreshToken(
      id=uuid4(),
      user_id=user.id,
      token_hash=hashed_refresh_token,
      expires_at=datetime.utcnow() + timedelta(days=30),
    )
  )
  await db.commit()

  if x_client_type == "web":
    response = JSONResponse(content={"token_type": "bearer"})
    response.set_cookie(
      key="accessToken",
      value=access_token,
      httponly=True,
      secure=settings.SECURE_COOKIE,
      samesite="lax",
      path="/",
    )
    response.set_cookie(
      key="refreshToken",
      value=refresh_token_plain,
      httponly=True,
      secure=settings.SECURE_COOKIE,
      samesite="lax",
      path="/",
    )
    return response

  return TokenSchema(
    access_token=access_token,
    refresh_token=refresh_token_plain,
    token_type="bearer",
  )

@router.post("/logout")
async def logout(
  request: Request,
  db: AsyncSession = Depends(get_db),
):
  refresh_token = request.cookies.get("refreshToken")

  if refresh_token:
    await db.execute(
      delete(RefreshToken).where(
        RefreshToken.token_hash == hash_token(refresh_token)
      )
    )
    await db.commit()

  response = JSONResponse(content={"success": True})
  response.delete_cookie("accessToken", path="/")
  response.delete_cookie("refreshToken", path="/")
  
  return response