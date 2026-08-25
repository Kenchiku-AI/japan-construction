from pathlib import Path
from typing import Any

from agents.extensions.sandbox import VercelSandboxClient
from agents.extensions.sandbox import VercelSandboxClientOptions
from agents.sandbox import Manifest
from agents.sandbox.entries import LocalDir


class FormVercelSandboxClient(VercelSandboxClient):
  async def create(
    self,
    snapshot=None,
    manifest: Manifest | None = None,
    options: VercelSandboxClientOptions | None = None,
  ):
    session = await super().create(
      snapshot=snapshot,
      manifest=manifest,
      options=options,
    )

    if manifest is not None:
      await self._materialize_local_dirs(
        session,
        manifest,
      )

    return session

  async def _materialize_local_dirs(
    self,
    session,
    manifest: Manifest,
  ) -> None:
    root = Path(manifest.root)

    for relative_path, entry in manifest.iter_entries():
      if not isinstance(entry, LocalDir):
        continue

      destination = root / relative_path

      await entry.apply(
        session=session,
        dest=destination,
      )


def get_form_sandbox_client() -> FormVercelSandboxClient:
  return FormVercelSandboxClient()