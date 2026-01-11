import secrets
import hashlib

from datetime import datetime, timedelta
from typing import Optional, Any, Dict

from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.core.config import settings
from app.db.models.user import User
from app.db.session import SessionLocal

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
  return pwd_context.hash(password)

def verify_password(password: str, hashed_password: str) -> bool:
  return pwd_context.verify(password, hashed_password)

def hash_token(token: str) -> str:
  return pwd_context.hash(token)

def verify_token(token: str, hashed_token: str) -> bool:
  return pwd_context.verify(token, hashed_token)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
  to_encode = data.copy()
  expire = datetime.utcnow() + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
  to_encode.update({"exp": expire})
  encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
  return encoded_jwt

def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
  to_encode = data.copy()
  expire = datetime.utcnow() + (expires_delta or timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS))
  to_encode.update({"exp": expire})
  encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
  return encoded_jwt

def decode_token(token: str) -> Dict[str, Any]:
  try:
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    return payload
  except JWTError:
    raise HTTPException(
      status_code=status.HTTP_401_UNAUTHORIZED,
      detail="Could not validate token",
      headers={"WWW-Authenticate": "Bearer"},
    )

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")

def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
  payload = decode_token(token)
  username: str = payload.get("sub")
  if username is None:
    raise HTTPException(status_code=401, detail="Invalid authentication credentials")
  
  db = SessionLocal()
  user = db.query(User).filter(User.username == username).first()
  db.close()
  if not user:
    raise HTTPException(status_code=401, detail="User not found")
  return user

def generate_access_token_for_user(user: User) -> str:
  return create_access_token({"sub": user.username})

def generate_refresh_token_for_user(user: User) -> str:
  return create_refresh_token({"sub": user.username})

def generate_invite_token() -> str:
  return secrets.token_urlsafe(32)

def hash_token(token: str) -> str:
  return hashlib.sha256(token.encode()).hexdigest()