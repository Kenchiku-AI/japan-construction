from fastapi import Depends, WebSocket, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from sqlalchemy.orm import Session
from uuid import UUID

from app.core.config import settings
from app.db.session import get_db
from app.db.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

def get_current_user(
  token: str = Depends(oauth2_scheme),
  db: Session = Depends(get_db),
):
  try:
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
    user_id = payload.get("sub")
  except JWTError:
    raise HTTPException(status_code=401, detail="Invalid token")

  user = db.get(User, user_id)
  if not user:
    raise HTTPException(status_code=401, detail="User not found")

  return user

async def get_current_user_ws(ws: WebSocket) -> User:
  token = (
    ws.cookies.get("access_token")
    or ws.query_params.get("access_token")
  )

  if not token:
    await ws.close(code=status.WS_1008_POLICY_VIOLATION)
    raise RuntimeError("Missing access token")

  try:
    payload = jwt.decode(
      token,
      settings.SECRET_KEY,
      algorithms=["HS256"],
    )
    user_id = payload.get("sub")
  except JWTError:
    await ws.close(code=status.WS_1008_POLICY_VIOLATION)
    raise RuntimeError("Invalid access token")

  if not user_id:
    await ws.close(code=status.WS_1008_POLICY_VIOLATION)
    raise RuntimeError("Invalid token payload")

  db: Session = next(get_db())
  user = db.get(User, user_id)

  if not user:
    await ws.close(code=status.WS_1008_POLICY_VIOLATION)
    raise RuntimeError("User not found")

  ws.state.user = user
  return user

def require_company_member(user: User, company_id: UUID):
  if user.role == "admin":
    return
    
  role = user.role_in_company(company_id)

  if role is None:
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Not a member of this company",
    )

def require_company_manager(user: User, company_id: UUID):
  if user.role == "admin":
    return

  role = user.role_in_company(company_id)

  if role != "manager":
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Manager privileges required",
    )

