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

BIN_DIR = "/home/vercel-sandbox/.local/bin"

DIAGNOSTIC_COMMAND = """
echo "=== whoami / id ==="
whoami
id

echo "=== pwd (default cwd for session.exec) ==="
pwd

echo "=== HOME ==="
echo "$HOME"

echo "=== PATH ==="
echo "$PATH"

echo "=== contents of cwd ==="
ls -la .

echo "=== contents of $HOME ==="
ls -la "$HOME" 2>&1 || echo "(no access or does not exist)"

echo "=== contents of /home ==="
ls -la /home 2>&1 || echo "(no access or does not exist)"

echo "=== contents of /vercel/sandbox (if present) ==="
ls -la /vercel/sandbox 2>&1 || echo "(no access or does not exist)"

echo "=== looking for uploaded scripts/ dir ==="
find / -maxdepth 4 -type d -name "scripts" 2>/dev/null

echo "=== looking for form-convert anywhere ==="
find / -maxdepth 6 -name "form-convert*" 2>/dev/null
"""

INSTALL_COMMAND = """
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
export PATH="{bin_dir}:$PATH"

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

chmod +x {bin_dir}/form-convert
chmod +x {bin_dir}/form-inspect
chmod +x {bin_dir}/form-verify
chmod +x {bin_dir}/inspect_excel.py

echo "=== install complete ==="
command -v form-convert
command -v form-inspect
command -v form-verify
python3 -c "import openpyxl, docx; print('python deps ok')"
""".format(bin_dir=BIN_DIR)


async def _run_and_log(session, label: str, *cmd: str) -> None:
  """Runs a command, logs stdout/stderr regardless of outcome, and does
  NOT raise — used for diagnostics we want visibility into even on failure."""
  result = await session.exec(*cmd)
  stdout = result.stdout.decode(errors="replace")
  stderr = result.stderr.decode(errors="replace")
  logger.info("[%s] exit_code=%s\nstdout:\n%s\nstderr:\n%s", label, result.exit_code, stdout, stderr)


async def provision_dependencies(session) -> None:
  """Uploads form-convert/inspect/verify + inspect_excel.py and installs
  every system/python dependency they need. Raises on any failure."""

  # --- Diagnostics: figure out the sandbox's actual layout before we
  # assume anything about paths. Safe to leave in; cheap to run. ---
  await _run_and_log(session, "diagnostics", "sh", "-lc", DIAGNOSTIC_COMMAND)

  mkdir_result = await session.exec("mkdir", "-p", BIN_DIR)
  if mkdir_result.exit_code != 0:
    stderr = mkdir_result.stderr.decode(errors="replace")
    raise RuntimeError(f"Failed to create {BIN_DIR}: {stderr}")

  for filename in SCRIPT_FILES:
    local_path = SCRIPTS_DIR / filename
    workspace_path = Path("scripts") / filename

    logger.info("Uploading %s -> %s", local_path, workspace_path)

    data = local_path.read_bytes()
    await session.write(workspace_path, io.BytesIO(data))

    # Confirm the file actually landed where we think it did before cp'ing it.
    await _run_and_log(session, f"post-upload-check:{filename}", "sh", "-lc",
                        f'pwd; ls -la "{workspace_path}" 2>&1 || echo "NOT FOUND at {workspace_path}"')

    result = await session.exec("cp", str(workspace_path), f"{BIN_DIR}/{filename}")
    if result.exit_code != 0:
      stderr = result.stderr.decode(errors="replace")
      raise RuntimeError(f"Failed to install {filename}: {stderr}")

  logger.info("Installing system + python dependencies ...")

  result = await session.exec("sh", "-lc", INSTALL_COMMAND)

  stdout = result.stdout.decode(errors="replace")
  stderr = result.stderr.decode(errors="replace")

  logger.info("Install stdout:\n%s", stdout)
  if stderr:
    logger.warning("Install stderr:\n%s", stderr)

  if result.exit_code != 0:
    raise RuntimeError(f"Dependency install failed with exit code {result.exit_code}")