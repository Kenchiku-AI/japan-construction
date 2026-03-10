from pydantic_settings import BaseSettings

class Settings(BaseSettings):
  SECRET_KEY: str
  ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
  REFRESH_TOKEN_EXPIRE_DAYS: int = 30
  ALGORITHM: str = "HS256"
  DATABASE_URL: str
  WEB_CLIENT_URL: str = "http://localhost:3000"
  SECURE_COOKIE: bool = False # Set this to True in prod
  SENDGRID_API_KEY: str
  FROM_EMAIL: str
  OPENAI_API_KEY: str
  AWS_ACCESS_KEY_ID: str
  AWS_SECRET_ACCESS_KEY: str
  AWS_REGION: str
  S3_BUCKET: str

  class Config:
    env_file = ".env"

settings = Settings()