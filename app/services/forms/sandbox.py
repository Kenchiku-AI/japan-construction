import io
import logging
import inspect
from typing import Any

from agents.extensions.sandbox import VercelSandboxClient
from agents.sandbox import RemoteSnapshot
from agents.sandbox.session import Dependencies

from app.core.config import settings

logger = logging.getLogger(__name__)

# Bump this whenever scripts/ or the installed system packages change,
# so job runs never silently pick up a stale snapshot.
FORM_AGENT_SNAPSHOT_ID = "form-agent-snapshot-v2"

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

    self._s3.upload_fileobj(
      data,
      self._bucket,
      self._object_key(snapshot_id),
    )

    logger.info(
      "S3SnapshotClient.upload() completed for snapshot %r",
      snapshot_id,
    )

  def download(self, snapshot_id: str) -> io.IOBase:
    buffer = io.BytesIO()

    self._s3.download_fileobj(
      self._bucket,
      self._object_key(snapshot_id),
      buffer,
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

  dependencies = Dependencies().bind_value(
    SNAPSHOT_CLIENT_DEPENDENCY_KEY,
    snapshot_client,
  )

  return VercelSandboxClient(dependencies=dependencies)