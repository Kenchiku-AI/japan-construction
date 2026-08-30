import io
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SCRIPTS_DIR = Path(__file__).resolve().parent

SCRIPT_FILES = [
  "form-convert",
  "form-inspect",
  "form-verify",
  "inspect_excel.py",
]

INSTALL_COMMAND = """
export PATH="/home/vercel-sandbox/.local/bin:$PATH"

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

chmod +x /home/vercel-sandbox/.local/bin/form-convert
chmod +x /home/vercel-sandbox/.local/bin/form-inspect
chmod +x /home/vercel-sandbox/.local/bin/form-verify
chmod +x /home/vercel-sandbox/.local/bin/inspect_excel.py

echo "=== install complete ==="
command -v form-convert
command -v form-inspect
command -v form-verify
python3 -c "import openpyxl, docx; print('python deps ok')"
"""


async def provision_dependencies(session) -> None:
  """Uploads form-convert/inspect/verify + inspect_excel.py and installs
  every system/python dependency they need. Raises on any failure."""

  bin_dir = "/home/vercel-sandbox/.local/bin"

  mkdir_result = await session.exec("mkdir", "-p", bin_dir)

  if mkdir_result.exit_code != 0:
    stderr = mkdir_result.stderr.decode(errors="replace")
    raise RuntimeError(f"Failed to create {bin_dir}: {stderr}")

  for filename in SCRIPT_FILES:
    local_path = SCRIPTS_DIR / filename
    workspace_path = Path("scripts") / filename

    logger.info("Uploading %s -> %s", local_path, workspace_path)

    data = local_path.read_bytes()

    await session.write(workspace_path, io.BytesIO(data))

    result = await session.exec(
      "cp",
      str(workspace_path),
      f"{bin_dir}/{filename}",
    )

    if result.exit_code != 0:
      stderr = result.stderr.decode(errors="replace")
      raise RuntimeError(f"Failed to install {filename}: {stderr}")

      raise RuntimeError(
        f"Failed to install {filename}: {stderr}"
      )

  logger.info("Installing system + python dependencies ...")

  result = await session.exec(
    "sh",
    "-lc",
    INSTALL_COMMAND,
  )

  stdout = result.stdout.decode(errors="replace")
  stderr = result.stderr.decode(errors="replace")

  logger.info("Install stdout:\n%s", stdout)

  if stderr:
    logger.warning("Install stderr:\n%s", stderr)

  if result.exit_code != 0:
    raise RuntimeError(
      f"Dependency install failed with exit code {result.exit_code}"
    )