from fastapi import Request
from fastapi.responses import JSONResponse
from jose import jwt, JWTError
from app.core.config import settings

PUBLIC_PATHS = [
  "/auth",
  "/docs",
  "/openapi.json",
]

async def auth_middleware(request: Request, call_next):
  path = request.url.path
  if any(
    path == p or path.startswith(p.rstrip("/") + "/")
    for p in PUBLIC_PATHS
  ):
    return await call_next(request)

  token = request.headers.get("Authorization")
  if not token:
    return JSONResponse(status_code=401, content={"detail": "Missing token"})

  try:
    scheme, _, value = token.partition(" ")
    jwt.decode(value, settings.SECRET_KEY, algorithms=["HS256"])
  except JWTError:
    return JSONResponse(status_code=401, content={"detail": "Invalid token"})

  return await call_next(request)