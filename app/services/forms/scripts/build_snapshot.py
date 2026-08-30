"""
One-time (or on-change) bootstrap: creates a sandbox, installs system
dependencies + the form-convert/form-inspect/form-verify scripts, then
stops the session so the SDK persists that filesystem state to S3 under
FORM_AGENT_SNAPSHOT_ID.

Usage:
    python -m scripts.build_snapshot
"""

import asyncio
import io
import logging
from pathlib import Path

from agents.extensions.sandbox import VercelSandboxClientOptions

from app.services.forms.sandbox import (
  get_form_agent_snapshot,
  get_form_sandbox_client,
)
from app.services.forms.snapshot_setup import provision_dependencies

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"

SCRIPT_FILES = [
  "form-convert",
  "form-inspect",
  "form-verify",
  "inspect_excel.py",
]

# Runs once inside the sandbox to install everything form-convert /
# form-inspect / form-verify / inspect_excel.py depend on.
INSTALL_COMMAND = """
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

apt-get update

apt-get install -y --no-install-recommends \\
  libreoffice \\
  poppler-utils \\
  imagemagick \\
  tesseract-ocr \\
  tesseract-ocr-jpn \\
  fonts-noto-cjk \\
  file \\
  python3-pip

# Ubuntu 24+/Debian's system Python blocks unmanaged pip installs (PEP 668);
# --break-system-packages is safe here since this is a throwaway sandbox fs.
pip3 install --no-cache-dir --break-system-packages openpyxl python-docx

chmod +x /usr/local/bin/form-convert
chmod +x /usr/local/bin/form-inspect
chmod +x /usr/local/bin/form-verify

echo "=== install complete ==="
form-convert 2>&1 || true
command -v form-inspect
command -v form-verify
python3 -c "import openpyxl, docx; print('python deps ok')"
"""


async def main() -> None:
  client = get_form_sandbox_client()
  snapshot = get_form_agent_snapshot()

  logger.info(
    "Creating bootstrap sandbox for snapshot %r ...",
    snapshot.id,
  )

  try:
    session = await client.create(
      snapshot=snapshot,
      options=VercelSandboxClientOptions(
        timeout_ms=600_000,  # installs can be slow; give it room
      ),
    )
  except Exception:
    logger.exception(
      "client.create(snapshot=...) failed on the very first (empty) "
      "snapshot. If this is a 'snapshot not restorable' style error, "
      "the SDK does not fall back to an empty workspace automatically "
      "and this bootstrap script needs to create with snapshot=None "
      "first, then persist explicitly. Re-run with logging.DEBUG on "
      "'openai.agents' to see the underlying cause."
    )
    raise

  try:
    await session.start()

    await provision_dependencies(session)

    logger.info(
      "Stopping session -- this persists the workspace to snapshot %r.",
      snapshot.id,
    )

    # stop() is what triggers RemoteSnapshot.persist() per the SDK's
    # BaseSandboxSession docs. If your SDK version requires an explicit
    # call instead (e.g. session.snapshot()), check
    # agents.sandbox.session.base_sandbox_session for the exact method.
    await session.stop()

    logger.info("Snapshot %r built and persisted successfully.", snapshot.id)

  finally:
    await session.shutdown()
    await client.delete(session)


if __name__ == "__main__":
  asyncio.run(main())