from fastapi import Depends, WebSocket, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID

from app.core.config import settings
from app.db.session import get_db
from app.db.models import User

oauth2_scheme = OAuth2PasswordBearer(
  tokenUrl="/auth/login",
  auto_error=False,
)

async def get_current_user(
  request: Request,
  token: str = Depends(oauth2_scheme),
  db: AsyncSession = Depends(get_db),
) -> User:
  access_token = request.cookies.get("accessToken") or token

  if not access_token:
    raise HTTPException(
      status_code=status.HTTP_401_UNAUTHORIZED,
      detail="Missing access token",
    )

  try:
    payload = jwt.decode(access_token, settings.SECRET_KEY, algorithms=["HS256"])
    user_id: str | None = payload.get("sub")
    if not user_id:
      raise HTTPException(status_code=401, detail="Invalid token")
  except JWTError:
    raise HTTPException(status_code=401, detail="Invalid token")

  result = await db.execute(select(User).where(User.id == user_id))
  user = result.scalar_one_or_none()

  if not user:
    raise HTTPException(status_code=401, detail="User not found")

  return user

async def get_current_user_ws(token: str, db: AsyncSession) -> User:
  if not token:
    raise RuntimeError("Missing access token")

  try:
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    user_id = payload.get("sub")
  except JWTError:
    raise RuntimeError("Invalid access token")

  user = await db.get(User, user_id)
  if not user:
    raise RuntimeError("User not found")

  return user

def require_company_member(user: User, company_id: UUID):
  if user.role == "admin":
    return

  if user.company_id != company_id:
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Not a member of this company",
    )

def require_company_manager(user: User, company_id: UUID):
  if user.role == "admin":
    return
    
  require_company_member(user, company_id)

  if user.role != "manager":
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Manager privileges required",
    )

