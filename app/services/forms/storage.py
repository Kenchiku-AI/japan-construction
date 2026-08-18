from pathlib import Path

import boto3


class FormStorage:
  def __init__(
    self,
    bucket_name: str,
  ):
    self.bucket_name = bucket_name
    self.s3 = boto3.client("s3")

  def upload_file(
    self,
    local_path: Path,
    s3_key: str,
  ) -> None:
    self.s3.upload_file(
      str(local_path),
      self.bucket_name,
      s3_key,
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

  def delete_file(
    self,
    s3_key: str,
  ) -> None:
    self.s3.delete_object(
      Bucket=self.bucket_name,
      Key=s3_key,
    )