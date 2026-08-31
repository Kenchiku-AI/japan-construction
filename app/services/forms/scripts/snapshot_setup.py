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

# All pip-installable -- no apt/dnf, no system package dependency at all.
PIP_PACKAGES = [
  "openpyxl",
  "xlrd",
  "python-docx",
  "python-pptx",
  "pandas",
  "odfpy",
  "pymupdf",
  "pypdf",
  "Pillow",
  "boto3",
  "httpx",
]

DIAGNOSTIC_COMMAND = """
echo "=== whoami / id ==="
whoami
id

echo "=== pwd ==="
pwd

echo "=== python3 / pip3 ==="
command -v python3
python3 --version
command -v pip3
pip3 --version

echo "=== CLOUDCONVERT_API_KEY configured? ==="
if [ -n "${CLOUDCONVERT_API_KEY:-}" ]; then
  echo "CLOUDCONVERT_API_KEY is set"
else
  echo "CLOUDCONVERT_API_KEY is NOT set -- legacy-format conversion will fail"
fi

echo "=== AWS credentials reachable? ==="
python3 -c "
import boto3
try:
    identity = boto3.client('sts').get_caller_identity()
    print('AWS credentials OK:', identity.get('Account'))
except Exception as exc:
    print('AWS credentials NOT available:', exc)
" 2>&1 || echo "boto3 not yet installed -- will check again post-install"
"""

INSTALL_COMMAND = """
set -euo pipefail

export PATH="{bin_dir}:$PATH"

pip3 install --no-cache-dir {packages}

echo "=== checking which packages actually installed ==="
python3 -c "
import importlib
mods = {{
    'openpyxl': 'openpyxl',
    'xlrd': 'xlrd',
    'python-docx': 'docx',
    'python-pptx': 'pptx',
    'pandas': 'pandas',
    'odfpy': 'odf',
    'pymupdf': 'fitz',
    'pypdf': 'pypdf',
    'Pillow': 'PIL',
    'boto3': 'boto3',
    'httpx': 'httpx',
}}
for pkg, mod in mods.items():
    try:
        importlib.import_module(mod)
        print(f'OK   {{pkg}}')
    except ImportError as exc:
        print(f'MISSING {{pkg}}: {{exc}}')
"

chmod +x {bin_dir}/form-convert
chmod +x {bin_dir}/form-inspect
chmod +x {bin_dir}/form-verify
chmod +x {bin_dir}/inspect_excel.py

echo "=== install complete ==="
command -v form-convert
command -v form-inspect
command -v form-verify
""".format(bin_dir=BIN_DIR, packages=" ".join(PIP_PACKAGES))


async def _run_and_log(session, label: str, *cmd: str):
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
  every Python dependency they need. No system packages (apt/dnf)
  required -- everything is a pip wheel. Legacy-format conversion
  (XLS/DOC/PPT/ODS <-> modern formats, and ->PDF) is delegated to
  CloudConvert via CLOUDCONVERT_API_KEY; OCR is delegated to AWS
  Textract, which needs AWS credentials reachable from the sandbox.
  Raises on any failure."""

  diag = await _run_and_log(session, "diagnostics", "sh", "-lc", DIAGNOSTIC_COMMAND)
  if diag.exit_code != 0:
    logger.warning("Diagnostics exited non-zero (%s) -- continuing anyway", diag.exit_code)

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

  logger.info("Installing python dependencies ...")

  result = await _run_and_log(session, "install", "sh", "-lc", INSTALL_COMMAND)
  if result.exit_code != 0:
    raise RuntimeError(f"Dependency install failed with exit code {result.exit_code}")