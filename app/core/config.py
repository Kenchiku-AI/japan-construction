from pydantic_settings import BaseSettings

class Settings(BaseSettings):
  SECRET_KEY: str
  ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
  REFRESH_TOKEN_EXPIRE_DAYS: int = 30
  ALGORITHM: str = "HS256"
  DATABASE_URL: str
  FRONTEND_URL: str = "http://localhost:3000"
  SENDGRID_API_KEY: str
  FROM_EMAIL: str

  class Config:
    env_file = ".env"

settings = Settings()