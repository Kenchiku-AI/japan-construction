import secrets
import hashlib

from datetime import datetime, timedelta
from typing import Optional, Any, Dict

from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status

from app.core.config import settings
from app.db.models.user import User

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

def generate_access_token_for_user(user: User) -> str:
  return create_access_token({"sub": user.username})

def generate_refresh_token_for_user(user: User) -> str:
  return create_refresh_token({"sub": user.username})

def generate_invite_token() -> str:
  return secrets.token_urlsafe(32)

def hash_token(token: str) -> str:
  return hashlib.sha256(token.encode()).hexdigest()