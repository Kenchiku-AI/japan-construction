from pydantic import BaseModel, EmailStr, Field

class LoginRequest(BaseModel):
  email: EmailStr
  password: str = Field(..., min_length=8)

class TokenSchema(BaseModel):
  access_token: str
  refresh_token: str
  token_type: str = "bearer"

class TokenPayload(BaseModel):
  refresh_token: str