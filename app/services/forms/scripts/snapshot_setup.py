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

# Pin the LibreOffice version used in the snapshot.
#
# The official LibreOffice distribution provides RPM archives for
# both x86_64 and aarch64.
LIBREOFFICE_VERSION = "25.2.5"

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

echo "=== python3 ==="
command -v python3
python3 --version

echo "=== pip availability (module, not standalone binary) ==="
python3 -m pip --version 2>&1 || echo "pip module not available -- will bootstrap via ensurepip"

echo "=== LibreOffice ==="
if command -v libreoffice >/dev/null 2>&1; then
  echo "libreoffice found:"
  libreoffice --version
elif command -v soffice >/dev/null 2>&1; then
  echo "soffice found:"
  soffice --version
else
  echo "LibreOffice is NOT installed"
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


INSTALL_COMMAND = f"""
set -euo pipefail

export PATH="{BIN_DIR}:$PATH"

echo "=== bootstrapping pip if needed ==="
python3 -m ensurepip --upgrade 2>&1 || echo "ensurepip not needed/available, continuing"
python3 -m pip install --upgrade pip

echo "=== installing Python packages ==="
python3 -m pip install --no-cache-dir {" ".join(PIP_PACKAGES)}

echo "=== detecting system architecture ==="
ARCH="$(uname -m)"
echo "Architecture: $ARCH"

case "$ARCH" in
  x86_64)
    LO_ARCH="x86-64"
    ;;
  aarch64)
    LO_ARCH="aarch64"
    ;;
  *)
    echo "Unsupported architecture: $ARCH"
    exit 1
    ;;
esac

echo "LibreOffice architecture: $LO_ARCH"

echo "=== installing LibreOffice {LIBREOFFICE_VERSION} ==="

LO_VERSION="{LIBREOFFICE_VERSION}"
LO_ARCHIVE="LibreOffice_${{LO_VERSION}}_Linux_${{LO_ARCH}}_rpm.tar.gz"
LO_URL="https://download.documentfoundation.org/libreoffice/stable/${{LO_VERSION}}/rpm/${{LO_ARCH}}/${{LO_ARCHIVE}}"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "Downloading:"
echo "$LO_URL"

cd "$TMP_DIR"

curl \
  --fail \
  --silent \
  --show-error \
  --location \
  --retry 3 \
  --retry-delay 2 \
  -o "$LO_ARCHIVE" \
  "$LO_URL"

echo "=== extracting LibreOffice ==="

tar -xzf "$LO_ARCHIVE"

LO_DIR="$(find "$TMP_DIR" -maxdepth 1 -type d -name 'LibreOffice_*_rpm' | head -n 1)"

if [ -z "$LO_DIR" ]; then
  echo "Could not find extracted LibreOffice directory"
  exit 1
fi

echo "LibreOffice directory: $LO_DIR"

echo "=== installing LibreOffice RPMs ==="

cd "$LO_DIR/RPMS"

# Use dnf for dependency resolution where possible.
dnf install -y ./*.rpm

echo "=== locating LibreOffice executable ==="

LIBREOFFICE_BIN="$(command -v libreoffice || true)"

if [ -z "$LIBREOFFICE_BIN" ]; then
  LIBREOFFICE_BIN="$(command -v soffice || true)"
fi

if [ -z "$LIBREOFFICE_BIN" ]; then
  echo "LibreOffice installed but executable was not found"
  exit 1
fi

echo "LibreOffice executable: $LIBREOFFICE_BIN"

echo "=== LibreOffice version ==="
"$LIBREOFFICE_BIN" --version

echo "=== checking Python packages ==="

python3 -c '
import importlib
import sys
import site

print("Python executable:", sys.executable)
print("Python version:", sys.version)

print("sys.path:")
for path in sys.path:
    print("  ", path)

print("site-packages:")
try:
    for path in site.getsitepackages():
        print("  ", path)
except Exception as exc:
    print("Could not determine site-packages:", exc)

mods = {{
    "openpyxl": "openpyxl",
    "xlrd": "xlrd",
    "python-docx": "docx",
    "python-pptx": "pptx",
    "pandas": "pandas",
    "odfpy": "odf",
    "pymupdf": "fitz",
    "pypdf": "pypdf",
    "Pillow": "PIL",
    "boto3": "boto3",
    "httpx": "httpx",
}}

for pkg, mod in mods.items():
    try:
        imported = importlib.import_module(mod)
        location = getattr(imported, "__file__", "unknown")
        print("OK   " + pkg + " -> " + str(location))
    except Exception as exc:
        print(
            "MISSING "
            + pkg
            + ": "
            + type(exc).__name__
            + ": "
            + str(exc)
        )
'

echo "=== verifying installed packages with pip ==="
python3 -m pip show openpyxl xlrd python-docx python-pptx pandas odfpy pymupdf pypdf Pillow boto3 httpx || true

echo "=== verifying LibreOffice ==="
command -v libreoffice || true
command -v soffice || true
libreoffice --version 2>&1 || soffice --version 2>&1 || true

echo "=== verifying command-line scripts ==="
command -v form-convert || true
command -v form-inspect || true
command -v form-verify || true

echo "=== install complete ==="
"""


async def _run_and_log(session, label: str, *cmd: str):
  result = await session.exec(*cmd)

  stdout = result.stdout.decode(errors="replace")
  stderr = result.stderr.decode(errors="replace")

  logger.info(
    "[%s] exit_code=%s\\nstdout:\\n%s\\nstderr:\\n%s",
    label,
    result.exit_code,
    stdout,
    stderr,
  )

  return result


async def provision_dependencies(session) -> None:
  """Uploads form-convert/inspect/verify + inspect_excel.py and installs
  every dependency they need.

  Python dependencies are installed with pip.

  LibreOffice is installed once into the sandbox snapshot from the
  official LibreOffice RPM distribution. It is then used locally for
  Office-to-Office and Office-to-PDF conversions.

  No user documents are sent to CloudConvert or another third-party
  conversion service.

  OCR is delegated to AWS Textract, which needs AWS credentials
  reachable from the sandbox.

  Raises on any failure.
  """

  diag = await _run_and_log(
    session,
    "diagnostics",
    "sh",
    "-lc",
    DIAGNOSTIC_COMMAND,
  )

  if diag.exit_code != 0:
    logger.warning(
      "Diagnostics exited non-zero (%s) -- continuing anyway",
      diag.exit_code,
    )

  mkdir_result = await session.exec(
    "mkdir",
    "-p",
    BIN_DIR,
  )

  if mkdir_result.exit_code != 0:
    stderr = mkdir_result.stderr.decode(errors="replace")

    raise RuntimeError(
      f"Failed to create {BIN_DIR}: {stderr}"
    )

  for filename in SCRIPT_FILES:
    local_path = SCRIPTS_DIR / filename
    workspace_path = Path("scripts") / filename

    logger.info(
      "Uploading %s -> %s",
      local_path,
      workspace_path,
    )

    data = local_path.read_bytes()

    await session.write(
      workspace_path,
      io.BytesIO(data),
    )

    check = await _run_and_log(
      session,
      f"post-upload-check:{filename}",
      "sh",
      "-lc",
      f'pwd; ls -la "{workspace_path}" 2>&1 || echo "NOT FOUND at {workspace_path}"',
    )

    if check.exit_code != 0:
      raise RuntimeError(
        f"Uploaded file {filename} not found at expected path "
        f"{workspace_path}"
      )

    result = await session.exec(
      "cp",
      str(workspace_path),
      f"{BIN_DIR}/{filename}",
    )

    if result.exit_code != 0:
      stderr = result.stderr.decode(errors="replace")

      raise RuntimeError(
        f"Failed to install {filename}: {stderr}"
      )

  chmod_result = await session.exec(
    "chmod",
    "+x",
    f"{BIN_DIR}/form-convert",
    f"{BIN_DIR}/form-inspect",
    f"{BIN_DIR}/form-verify",
  )

  if chmod_result.exit_code != 0:
    stderr = chmod_result.stderr.decode(errors="replace")

    raise RuntimeError(
      f"Failed to make form scripts executable: {stderr}"
    )

  logger.info("Installing Python dependencies and LibreOffice ...")

  result = await _run_and_log(
    session,
    "install",
    "sh",
    "-lc",
    INSTALL_COMMAND,
  )

  if result.exit_code != 0:
    raise RuntimeError(
      f"Dependency install failed with exit code "
      f"{result.exit_code}"
    )