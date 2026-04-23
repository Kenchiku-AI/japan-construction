from pydantic_settings import BaseSettings

class Settings(BaseSettings):
  ENV: str
  DATABASE_URL: str
  SECRET_KEY: str
  SUPER_USER_EMAIL: str
  ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
  REFRESH_TOKEN_EXPIRE_DAYS: int = 7
  NO_REPLY_EMAIL: str
  SUPPORT_EMAIL: str
  OPENAI_API_KEY: str
  AWS_REGION: str
  S3_BUCKET: str
  SQS_QUEUE_URL: str
  ALGORITHM: str = "HS256"
  WEB_CLIENT_URL: str = "http://localhost:3000"
  SECURE_COOKIE: bool = False # Set this to True in prod
  REDIS_URL: str = "redis://redis:6379"

  class Config:
    env_file = ".env"

settings = Settings()