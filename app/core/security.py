import secrets
import hashlib
from datetime import datetime, timedelta
from typing import Optional
from jose import jwt
from passlib.context import CryptContext
from app.core.config import settings
from cryptography.fernet import Fernet, InvalidToken

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

fernet = Fernet(settings.LINE_ENCRYPTION_KEY)

def hash_password(password: str) -> str:
  return pwd_context.hash(password)

def verify_password(password: str, hashed_password: Optional[str]) -> bool:
  if not hashed_password:
    return False
  try:
    return pwd_context.verify(password, hashed_password)
  except Exception:
    return False

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

def generate_invite_token() -> str:
  return secrets.token_urlsafe(32)

def hash_token(token: str) -> str:
  return hashlib.sha256(token.encode()).hexdigest()

def get_cookie_settings():
  is_local = settings.ENV == "local"

  return {
    "httponly": True,
    "secure": not is_local,
    "samesite": "lax" if is_local else "none",
    "domain": None if is_local else ".kenchiku.ai",
    "path": "/",
  }

def encrypt_secret(plaintext: str) -> str:
  return fernet.encrypt(plaintext.encode()).decode()

def decrypt_secret(ciphertext: str) -> str:
  try:
    return fernet.decrypt(ciphertext.encode()).decode()
  except InvalidToken:
    raise ValueError("Could not decrypt secret — invalid key or corrupted data")