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

# Amazon Linux 2023 package names (dnf), not apt/Debian names.
DNF_PACKAGES = [
  "libreoffice",
  "poppler-utils",
  "ImageMagick",
  "tesseract",
  "tesseract-langpack-jpn",
  "google-noto-cjk-fonts",
  "file",
  "python3-pip",
]

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

echo "=== sudo check (passwordless?) ==="
sudo -n true 2>&1 && echo "sudo: passwordless OK" || echo "sudo: NOT available without password"

echo "=== package manager check ==="
command -v dnf 2>&1 || echo "dnf: not found"
command -v apt-get 2>&1 || echo "apt-get: not found"

echo "=== contents of cwd ==="
ls -la .

echo "=== contents of $HOME ==="
ls -la "$HOME" 2>&1 || echo "(no access or does not exist)"

echo "=== looking for uploaded scripts/ dir ==="
find / -maxdepth 4 -type d -name "scripts" 2>/dev/null

echo "=== looking for form-convert anywhere ==="
find / -maxdepth 6 -name "form-convert*" 2>/dev/null
"""

INSTALL_COMMAND = """
set -euo pipefail

export PATH="{bin_dir}:$PATH"

echo "=== installing system packages via dnf ==="
sudo dnf install -y {packages}

echo "=== checking which packages actually landed ==="
for pkg in {packages}; do
  dnf list installed "$pkg" >/dev/null 2>&1 \\
    && echo "OK   $pkg" \\
    || echo "MISSING $pkg (install may have skipped/renamed it)"
done

pip3 install --no-cache-dir openpyxl python-docx

chmod +x {bin_dir}/form-convert
chmod +x {bin_dir}/form-inspect
chmod +x {bin_dir}/form-verify
chmod +x {bin_dir}/inspect_excel.py

echo "=== install complete ==="
command -v form-convert
command -v form-inspect
command -v form-verify
python3 -c "import openpyxl, docx; print('python deps ok')"
""".format(bin_dir=BIN_DIR, packages=" ".join(DNF_PACKAGES))


async def _run_and_log(session, label: str, *cmd: str) -> "object":
  """Runs a command, logs stdout/stderr regardless of outcome, and does
  NOT raise — used for diagnostics/checks we want visibility into even
  on failure. Returns the raw result in case the caller wants exit_code."""
  result = await session.exec(*cmd)
  stdout = result.stdout.decode(errors="replace")
  stderr = result.stderr.decode(errors="replace")
  logger.info(
    "[%s] exit_code=%s\nstdout:\n%s\nstderr:\n%s",
    label, result.exit_code, stdout, stderr,
  )
  return result


async def provision_dependencies(session) -> None:
  """Uploads form-convert/inspect/verify + inspect_excel.py and installs
  every system/python dependency they need. Raises on any failure."""

  # --- Diagnostics: confirm cwd, PATH, sudo access, and which package
  # manager actually exists before assuming anything about the image. ---
  diag = await _run_and_log(session, "diagnostics", "sh", "-lc", DIAGNOSTIC_COMMAND)
  if diag.exit_code != 0:
    logger.warning("Diagnostics command exited non-zero (%s) -- continuing anyway", diag.exit_code)

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

    # Confirm the file actually landed where we think before cp'ing it.
    check = await _run_and_log(
      session, f"post-upload-check:{filename}", "sh", "-lc",
      f'pwd; ls -la "{workspace_path}" 2>&1 || echo "NOT FOUND at {workspace_path}"',
    )
    if check.exit_code != 0:
      raise RuntimeError(f"Uploaded file {filename} not found at expected path {workspace_path}")

    result = await session.exec("cp", str(workspace_path), f"{BIN_DIR}/{filename}")
    if result.exit_code != 0:
      stderr = result.stderr.decode(errors="replace")
      raise RuntimeError(f"Failed to install {filename}: {stderr}")

  logger.info("Installing system + python dependencies ...")

  result = await _run_and_log(session, "install", "sh", "-lc", INSTALL_COMMAND)

  if result.exit_code != 0:
    raise RuntimeError(f"Dependency install failed with exit code {result.exit_code}")