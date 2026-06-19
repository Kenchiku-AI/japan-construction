from datetime import datetime, timedelta
from uuid import uuid4
from typing import Optional
import secrets

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, status, BackgroundTasks
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
  get_cookie_settings,
  generate_unique_line_link_code,
)
from app.db.models.refresh_token import RefreshToken
from app.db.models.user import User
from app.db.models.company import Company
from app.db.models.invitation import Invitation
from app.db.models.password_reset_token import PasswordResetToken
from app.db.session import get_db
from app.schemas.auth import LoginRequest, TokenPayload, TokenSchema, ForgotPasswordRequest, ResetPasswordRequest
from app.schemas.user import UserCreate
from app.services.users import build_user_with_company_and_projects
from app.services.email import send_password_reset_email

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
    user_with_projects = await build_user_with_company_and_projects(user, db)
    response = JSONResponse(
      content=user_with_projects.model_dump(mode="json")
    )

    cookie_settings = get_cookie_settings()

    response.set_cookie(
      key="accessToken",
      value=access_token,
      **cookie_settings,
    )
    response.set_cookie(
      key="refreshToken",
      value=refresh_token,
      **cookie_settings,
    )
    return response

  return TokenSchema(
    access_token=access_token,
    refresh_token=refresh_token,
    token_type="bearer",
  )

@router.post("/refresh", response_model=TokenSchema)
async def refresh_token(
  request: Request,
  db: AsyncSession = Depends(get_db),
  payload: Optional[TokenPayload] = Body(None),
):
  refresh_token: str | None = None

  if "refreshToken" in request.cookies:
    refresh_token = request.cookies.get("refreshToken")
  elif payload and payload.refresh_token:
    refresh_token = payload.refresh_token

  if not refresh_token:
    raise HTTPException(
      status_code=401,
      detail="Missing refresh token",
    )

  result = await db.execute(
    select(RefreshToken).where(
      RefreshToken.token_hash == hash_token(refresh_token)
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

  if "refreshToken" in request.cookies:
    cookie_settings = get_cookie_settings()
    response = JSONResponse(content={"token_type": "bearer"})
    response.set_cookie(
      key="accessToken",
      value=access_token,
      **cookie_settings,
    )
    return response

  return TokenSchema(
    access_token=access_token,
    refresh_token=refresh_token,
    token_type="bearer",
  )

@router.post("/signup", response_model=TokenSchema)
async def signup(
  payload: UserCreate,
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

  hashed_token = hash_token(payload.invitation_token)
  result = await db.execute(select(Invitation).filter(Invitation.token_hash == hashed_token))
  invitation = result.scalars().first()

  if not invitation:
    raise HTTPException(status_code=404, detail="Invitation not found or invalid")

  if invitation.expires_at < datetime.utcnow():
    raise HTTPException(status_code=400, detail="Invitation expired")

  company = await db.get(Company, invitation.company_id)
  if not company:
    raise HTTPException(status_code=404, detail="Company not found")

  user = User(
    email=payload.email,
    first_name=payload.first_name,
    last_name=payload.last_name,
    hashed_password=hash_password(payload.password),
    company_id=company.id,
    role=invitation.role,
    line_link_code=generate_unique_line_link_code(db),
  )

  db.add(user)
  await db.commit()
  await db.refresh(user)

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
    user_with_projects = await build_user_with_company_and_projects(user, db)
    response = JSONResponse(
      content=user_with_projects.model_dump(mode="json")
    )

    cookie_settings = get_cookie_settings()

    response.set_cookie(
      key="accessToken",
      value=access_token,
      **cookie_settings,
    )
    response.set_cookie(
      key="refreshToken",
      value=refresh_token,
      **cookie_settings,
    )
    return response

  return TokenSchema(
    access_token=access_token,
    refresh_token=refresh_token,
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

  is_local = settings.ENV == "local"
  domain = None if is_local else ".kenchiku.ai"

  response.delete_cookie("accessToken", path="/", domain=domain)
  response.delete_cookie("refreshToken", path="/", domain=domain)
  
  return response

@router.post("/forgot-password")
async def forgot_password(
  background_tasks: BackgroundTasks,
  payload: ForgotPasswordRequest,
  db: AsyncSession = Depends(get_db),
):
  result = await db.execute(
    select(User).where(User.email == payload.email)
  )
  user = result.scalar_one_or_none()

  if not user:
    return {"success": True}

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

@router.post("/reset-password")
async def reset_password(
  payload: ResetPasswordRequest,
  db: AsyncSession = Depends(get_db),
):
  hashed_token = hash_token(payload.token)

  result = await db.execute(
    select(PasswordResetToken).where(
      PasswordResetToken.token_hash == hashed_token
    )
  )
  db_token = result.scalar_one_or_none()

  if not db_token:
    raise HTTPException(status_code=404, detail="Invalid token")

  if db_token.expires_at < datetime.utcnow():
    raise HTTPException(status_code=400, detail="Token expired")

  user = await db.get(User, db_token.user_id)

  user.hashed_password = hash_password(payload.new_password)

  await db.delete(db_token)
  await db.commit()

  return {"success": True}