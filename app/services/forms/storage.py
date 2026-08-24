from pathlib import Path

import boto3

from app.core.config import settings


class FormStorage:
  def __init__(
    self,
    bucket_name: str,
  ):
    self.bucket_name = bucket_name
    self.s3 = boto3.client(
      "s3",
      region_name=settings.AWS_REGION,
    )

  def upload_file(
    self,
    local_path: Path,
    s3_key: str,
    content_type: str | None = None,
  ) -> None:
    extra_args = {}

    if content_type:
      extra_args["ContentType"] = content_type

    self.s3.upload_file(
      str(local_path),
      self.bucket_name,
      s3_key,
      ExtraArgs=extra_args or None,
    )

  def upload_fileobj(
    self,
    file_obj,
    s3_key: str,
    content_type: str | None = None,
  ) -> None:
    extra_args = {}

    if content_type:
      extra_args["ContentType"] = content_type

    self.s3.upload_fileobj(
      file_obj,
      self.bucket_name,
      s3_key,
      ExtraArgs=extra_args or None,
    )

  def download_file(
    self,
    s3_key: str,
    local_path: Path,
  ) -> None:
    local_path.parent.mkdir(
      parents=True,
      exist_ok=True,
    )

    self.s3.download_file(
      self.bucket_name,
      s3_key,
      str(local_path),
    )

  def create_upload_url(
    self,
    s3_key: str,
    content_type: str,
    expires_in: int = 300,
  ) -> str:
    return self.s3.generate_presigned_url(
      "put_object",
      Params={
        "Bucket": self.bucket_name,
        "Key": s3_key,
        "ContentType": content_type,
      },
      ExpiresIn=expires_in,
    )

  def create_download_url(
    self,
    s3_key: str,
    expires_in: int = 3600,
  ) -> str:
    return self.s3.generate_presigned_url(
      "get_object",
      Params={
        "Bucket": self.bucket_name,
        "Key": s3_key,
      },
      ExpiresIn=expires_in,
    )

  def delete_file(
    self,
    s3_key: str,
  ) -> None:
    self.s3.delete_object(
      Bucket=self.bucket_name,
      Key=s3_key,
    )