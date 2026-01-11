from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from sqlalchemy.orm import Session

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

def require_company_member(user: User, company_id: int):
  role = user.role_in_company(company_id)
  if role is None:
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Not a member of this company",
    )
  return role

def require_company_admin(user: User, company_id: int):
  role = require_company_member(user, company_id)
  if role != "admin":
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Admin privileges required",
    )

