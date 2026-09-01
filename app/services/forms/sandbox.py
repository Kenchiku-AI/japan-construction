import io
import logging
import inspect
import tarfile
from typing import Any

from agents.extensions.sandbox import VercelSandboxClient
from agents.sandbox import RemoteSnapshot
from agents.sandbox.session import Dependencies

from app.core.config import settings

logger = logging.getLogger(__name__)

FORM_AGENT_SNAPSHOT_ID = "form-agent-snapshot-v1.0.0"

SNAPSHOT_CLIENT_DEPENDENCY_KEY = "kenchiku.form_agent.s3_snapshot_client"


class S3SnapshotClient:
  """Adapter satisfying RemoteSnapshot's upload/download/exists contract."""

  def __init__(self, *, bucket: str, prefix: str) -> None:
    import boto3

    self._bucket = bucket
    self._prefix = prefix.rstrip("/")
    self._s3 = boto3.client("s3")

  def upload(self, snapshot_id: str, data: io.IOBase) -> None:
    logger.info(
      "S3SnapshotClient.upload() called for snapshot %r",
      snapshot_id,
    )

    # Make sure we're reading from the beginning.
    data.seek(0)

    tar_bytes = data.read()

    logger.info(
      "SNAPSHOT UPLOAD TAR: snapshot=%r bytes=%d",
      snapshot_id,
      len(tar_bytes),
    )

    try:
      with tarfile.open(
        fileobj=io.BytesIO(tar_bytes),
        mode="r:*",
      ) as tar:
        names = tar.getnames()

        logger.info(
          "SNAPSHOT UPLOAD TAR: snapshot=%r entries=%d",
          snapshot_id,
          len(names),
        )

        for name in names[:100]:
          logger.info(
            "SNAPSHOT UPLOAD TAR ENTRY: %s",
            name,
          )

        important_names = [
          name
          for name in names
          if any(
            target in name
            for target in (
              "form-convert",
              "form-inspect",
              "form-verify",
              "form-ocr",
              "inspect_excel.py",
              ".local",
            )
          )
        ]

        logger.info(
          "SNAPSHOT UPLOAD IMPORTANT ENTRIES: %s",
          important_names,
        )

    except Exception:
      logger.exception(
        "Could not inspect snapshot tar before S3 upload.",
      )

    # Rewind because we consumed the stream above.
    data.seek(0)

    self._s3.upload_fileobj(
      data,
      self._bucket,
      self._object_key(snapshot_id),
    )

    logger.info(
      "S3SnapshotClient.upload() completed for snapshot %r",
      snapshot_id,
    )

    # Verify exactly what S3 received.
    metadata = self._s3.head_object(
      Bucket=self._bucket,
      Key=self._object_key(snapshot_id),
    )

    logger.info(
      "SNAPSHOT S3 AFTER UPLOAD: snapshot=%r "
      "key=%r ContentLength=%s ETag=%s LastModified=%s",
      snapshot_id,
      self._object_key(snapshot_id),
      metadata.get("ContentLength"),
      metadata.get("ETag"),
      metadata.get("LastModified"),
    )

  def download(self, snapshot_id: str) -> io.IOBase:
    key = self._object_key(snapshot_id)

    metadata = self._s3.head_object(
      Bucket=self._bucket,
      Key=key,
    )

    logger.info(
      "SNAPSHOT S3 BEFORE DOWNLOAD: snapshot=%r "
      "key=%r ContentLength=%s ETag=%s LastModified=%s",
      snapshot_id,
      key,
      metadata.get("ContentLength"),
      metadata.get("ETag"),
      metadata.get("LastModified"),
    )

    buffer = io.BytesIO()

    self._s3.download_fileobj(
      self._bucket,
      key,
      buffer,
    )

    downloaded_bytes = buffer.getvalue()

    logger.info(
      "SNAPSHOT DOWNLOAD COMPLETE: snapshot=%r bytes=%d "
      "S3_ContentLength=%s",
      snapshot_id,
      len(downloaded_bytes),
      metadata.get("ContentLength"),
    )

    try:
      with tarfile.open(
        fileobj=io.BytesIO(downloaded_bytes),
        mode="r:*",
      ) as tar:
        names = tar.getnames()

        logger.info(
          "SNAPSHOT DOWNLOAD TAR: snapshot=%r entries=%d",
          snapshot_id,
          len(names),
        )

        for name in names[:100]:
          logger.info(
            "SNAPSHOT DOWNLOAD TAR ENTRY: %s",
            name,
          )

        important_names = [
          name
          for name in names
          if any(
            target in name
            for target in (
              "form-convert",
              "form-inspect",
              "form-verify",
              "form-ocr",
              "inspect_excel.py",
              ".local",
            )
          )
        ]

        logger.info(
          "SNAPSHOT DOWNLOAD IMPORTANT ENTRIES: %s",
          important_names,
        )

    except Exception:
      logger.exception(
        "Could not inspect downloaded snapshot tar.",
      )

    buffer.seek(0)

    return buffer

  def exists(self, snapshot_id: str) -> bool:
    from botocore.exceptions import ClientError

    try:
      self._s3.head_object(
        Bucket=self._bucket,
        Key=self._object_key(snapshot_id),
      )
    except ClientError as exc:
      code = exc.response.get("Error", {}).get("Code")

      if code in {"404", "NoSuchKey", "NotFound"}:
        return False

      raise

    return True

  def _object_key(self, snapshot_id: str) -> str:
    return f"{self._prefix}/{snapshot_id}.tar"


def build_snapshot_client() -> S3SnapshotClient:
  return S3SnapshotClient(
    bucket=settings.S3_BUCKET,
    prefix="form-agent-snapshots",
  )


def get_form_agent_snapshot() -> RemoteSnapshot:
  """The fixed-id snapshot every sandbox session (bootstrap and job runs)
  should be created with, so they all share the same base filesystem."""

  return RemoteSnapshot(
    id=FORM_AGENT_SNAPSHOT_ID,
    client_dependency_key=SNAPSHOT_CLIENT_DEPENDENCY_KEY,
  )


def get_form_sandbox_client() -> VercelSandboxClient:
  snapshot_client = build_snapshot_client()

  logger.info(
    "BUILD SNAPSHOT CLIENT: type=%s id=%s",
    type(snapshot_client).__name__,
    id(snapshot_client),
  )

  dependencies = Dependencies().bind_value(
    SNAPSHOT_CLIENT_DEPENDENCY_KEY,
    snapshot_client,
  )

  logger.info(
    "BUILD VERCEL SANDBOX CLIENT: dependency_key=%r",
    SNAPSHOT_CLIENT_DEPENDENCY_KEY,
  )

  client = VercelSandboxClient(
    dependencies=dependencies,
  )

  logger.info(
    "BUILD VERCEL SANDBOX CLIENT COMPLETE: type=%s id=%s",
    type(client).__name__,
    id(client),
  )

  return client