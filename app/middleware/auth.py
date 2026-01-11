from fastapi import Request
from fastapi.responses import JSONResponse
from jose import jwt, JWTError
from app.core.config import settings

async def auth_middleware(request: Request, call_next):
  # if request.url.path.startswith("/auth"):
  #   return await call_next(request)

  # token = request.headers.get("Authorization")
  # if not token:
  #   return JSONResponse(status_code=401, content={"detail": "Missing token"})

  # try:
  #   scheme, _, value = token.partition(" ")
  #   jwt.decode(value, settings.secret_key, algorithms=["HS256"])
  # except JWTError:
  #   return JSONResponse(status_code=401, content={"detail": "Invalid token"})

  return await call_next(request)