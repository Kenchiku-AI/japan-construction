import io
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / "sandbox" / "scripts"

SCRIPT_FILES = [
  "form-convert",
  "form-inspect",
  "form-verify",
  "inspect_excel.py",
]

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

pip3 install --no-cache-dir --break-system-packages openpyxl python-docx

chmod +x /usr/local/bin/form-convert
chmod +x /usr/local/bin/form-inspect
chmod +x /usr/local/bin/form-verify

echo "=== install complete ==="
command -v form-convert
command -v form-inspect
command -v form-verify
python3 -c "import openpyxl, docx; print('python deps ok')"
"""


async def provision_dependencies(session) -> None:
  """Uploads form-convert/inspect/verify + inspect_excel.py and installs
  every system/python dependency they need. Raises on any failure."""

  for filename in SCRIPT_FILES:
    local_path = SCRIPTS_DIR / filename
    dest_path = Path("/usr/local/bin") / filename

    logger.info("Uploading %s -> %s", local_path, dest_path)

    data = local_path.read_bytes()

    await session.write(dest_path, io.BytesIO(data))

  logger.info("Installing system + python dependencies ...")

  result = await session.exec(INSTALL_COMMAND, shell=True)

  stdout = result.stdout.decode(errors="replace")
  stderr = result.stderr.decode(errors="replace")

  logger.info("Install stdout:\n%s", stdout)

  if stderr:
    logger.warning("Install stderr:\n%s", stderr)

  if result.exit_code != 0:
    raise RuntimeError(
      f"Dependency install failed with exit code {result.exit_code}"
    )