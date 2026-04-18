from pydantic import BaseModel, EmailStr, Field

class LoginRequest(BaseModel):
  email: EmailStr
  password: str

class TokenSchema(BaseModel):
  access_token: str
  refresh_token: str
  token_type: str = "bearer"

class TokenPayload(BaseModel):
  refresh_token: str

class ForgotPasswordRequest(BaseModel):
  email: str

class ResetPasswordRequest(BaseModel):
  token: str
  new_password: str