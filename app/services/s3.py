import boto3

from app.core.config import settings

s3_client = boto3.client(
  "s3",
  region_name=settings.AWS_REGION,
  aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
  aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
)

BUCKET_NAME = settings.S3_BUCKET