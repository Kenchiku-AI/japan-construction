import json
from pydantic import field_validator
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
  ENV: str
  DATABASE_URL: str
  SECRET_KEY: str
  SUPER_USER_EMAIL: str
  ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
  REFRESH_TOKEN_EXPIRE_DAYS: int = 7
  NO_REPLY_EMAIL: str
  SUPPORT_EMAIL: str
  OPENAI_API_KEY: str
  STRIPE_SECRET_KEY: str
  STRIPE_WEBHOOK_SECRET: str
  STRIPE_TRIAL_PERIOD_MINUTES: int = 43200
  LINE_ENCRYPTION_KEY: str
  AWS_REGION: str
  S3_BUCKET: str
  SQS_QUEUE_URL: str
  SQS_FORM_QUEUE_URL: str
  ALGORITHM: str = "HS256"
  WEB_CLIENT_URL: str = "http://localhost:3000"
  VERCEL_PROJECT_ID: str
  VERCEL_TEAM_ID: str
  VERCEL_SANDBOX_IMAGE: str

  @field_validator("*", mode="before")
  @classmethod
  def unwrap_json_secrets(cls, v, info):
    if isinstance(v, str) and v.startswith("{"):
      try:
        data = json.loads(v)
        if isinstance(data, dict):
          return data.get(info.field_name, v)
      except json.JSONDecodeError:
        pass
    return v

  class Config:
    env_file = ".env"

settings = Settings()