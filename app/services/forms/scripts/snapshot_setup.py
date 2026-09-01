import io
import logging
import os
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

# ---------------------------------------------------------------------------
# LibreOffice
# ---------------------------------------------------------------------------
#
# The LibreOffice archive is NOT stored in Git.
#
# It is stored in a private S3 bucket and downloaded into the sandbox while
# creating the snapshot.
#
# The Vercel Sandbox must have AWS credentials that allow s3:GetObject on
# this object.
#
LIBREOFFICE_ARCHIVE = (
  "LibreOffice_26.8.0_Linux_x86-64_rpm.tar.gz"
)

LIBREOFFICE_S3_BUCKET = os.environ.get(
  "S3_BUCKET"
)

LIBREOFFICE_S3_KEY = os.environ.get(
  "LIBREOFFICE_S3_KEY",
  f"libreoffice/{LIBREOFFICE_ARCHIVE}",
)

# Location inside the sandbox where the archive will be downloaded.
LIBREOFFICE_DOWNLOAD_DIR = Path("/tmp/libreoffice")

LIBREOFFICE_DOWNLOAD_PATH = (
  LIBREOFFICE_DOWNLOAD_DIR / LIBREOFFICE_ARCHIVE
)


# ---------------------------------------------------------------------------
# Python dependencies
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

DIAGNOSTIC_COMMAND = """
set +e

echo "========================================"
echo "=== SYSTEM INFORMATION"
echo "========================================"

echo "--- whoami / id ---"
whoami
id

echo "--- pwd ---"
pwd

echo "--- uname ---"
uname -a

echo "--- architecture ---"
uname -m
uname -p

echo "--- OS release ---"
cat /etc/os-release 2>&1 || true

echo "--- disk space ---"
df -h 2>&1 || true

echo "--- memory ---"
free -h 2>&1 || true


echo ""
echo "========================================"
echo "=== PYTHON / PIP"
echo "========================================"

echo "--- python3 ---"
command -v python3
python3 --version

echo "--- pip ---"
python3 -m pip --version 2>&1 || echo "pip module not available"


echo ""
echo "========================================"
echo "=== EXISTING LIBREOFFICE"
echo "========================================"

echo "--- command -v ---"
command -v libreoffice 2>&1 || true
command -v soffice 2>&1 || true

echo "--- version ---"
libreoffice --version 2>&1 || true
soffice --version 2>&1 || true

echo "--- search common locations ---"
find /usr /opt /home -type f \
  \\( -name "libreoffice" -o -name "soffice" \\) \
  2>/dev/null | head -100


echo ""
echo "========================================"
echo "=== DNF LIBREOFFICE CHECK"
echo "========================================"

echo "--- dnf search ---"
dnf search libreoffice 2>&1 || true

echo "--- dnf list ---"
dnf list available '*libreoffice*' 2>&1 || true

echo "--- installed RPMs ---"
rpm -qa | grep -i libreoffice 2>&1 || true


echo ""
echo "========================================"
echo "=== PACKAGE INSTALLATION TOOLS"
echo "========================================"

echo "--- rpm ---"
rpm --version 2>&1 || true

echo "--- dnf ---"
dnf --version 2>&1 || true

echo "--- tar ---"
tar --version 2>&1 | head -3 || true

echo "--- curl ---"
curl --version 2>&1 | head -3 || true


echo ""
echo "========================================"
echo "=== AWS CREDENTIALS"
echo "========================================"

python3 -c "
try:
    import boto3

    try:
        identity = boto3.client('sts').get_caller_identity()
        print('AWS credentials OK:', identity.get('Account'))
    except Exception as exc:
        print('AWS credentials NOT available:', exc)

except Exception as exc:
    print('boto3 not installed yet:', type(exc).__name__, exc)
" 2>&1 || true


echo ""
echo "========================================"
echo "=== END DIAGNOSTICS"
echo "========================================"
"""


# ---------------------------------------------------------------------------
# Installation command
# ---------------------------------------------------------------------------
#
# The Python process downloads the LibreOffice archive into /tmp before
# running this shell command.
#
INSTALL_COMMAND = f"""
set -euo pipefail

export PATH="{BIN_DIR}:$PATH"

echo "========================================"
echo "=== INSTALLING PYTHON PACKAGES"
echo "========================================"

echo "=== bootstrapping pip if needed ==="

python3 -m ensurepip --upgrade 2>&1 \
  || echo "ensurepip not needed/available, continuing"

python3 -m pip install --upgrade pip

echo "=== installing Python packages ==="

python3 -m pip install \
  --no-cache-dir \
  {" ".join(PIP_PACKAGES)}


echo ""
echo "========================================"
echo "=== VERIFYING LIBREOFFICE ARCHIVE"
echo "========================================"

LO_ARCHIVE="{LIBREOFFICE_DOWNLOAD_PATH}"

if [ ! -f "$LO_ARCHIVE" ]; then
  echo "ERROR: LibreOffice archive was not downloaded:"
  echo "$LO_ARCHIVE"
  exit 1
fi

echo "LibreOffice archive:"
ls -lh "$LO_ARCHIVE"

echo ""
echo "Archive type:"
file "$LO_ARCHIVE" 2>&1 || true

echo ""
echo "Archive size:"
du -h "$LO_ARCHIVE"


echo ""
echo "========================================"
echo "=== VERIFYING SANDBOX ARCHITECTURE"
echo "========================================"

ARCH="$(uname -m)"

echo "Architecture: $ARCH"

if [ "$ARCH" != "x86_64" ]; then
  echo "ERROR: This snapshot setup currently expects x86_64."
  echo "Detected architecture: $ARCH"
  exit 1
fi


echo ""
echo "========================================"
echo "=== EXTRACTING LIBREOFFICE"
echo "========================================"

TMP_DIR="$(mktemp -d)"

echo "Temporary directory:"
echo "$TMP_DIR"

echo "Extracting archive..."

tar -xzf "$LO_ARCHIVE" -C "$TMP_DIR"

echo ""
echo "Extracted contents:"
find "$TMP_DIR" -maxdepth 2 -type d -print


echo ""
echo "========================================"
echo "=== LOCATING LIBREOFFICE RPM DIRECTORY"
echo "========================================"

LO_DIR="$(find "$TMP_DIR" \
  -maxdepth 1 \
  -type d \
  -name 'LibreOffice_*_rpm' \
  -print \
  -quit)"

if [ -z "$LO_DIR" ]; then
  echo "ERROR: Could not find extracted LibreOffice directory."

  echo "Extracted directory contents:"
  find "$TMP_DIR" -maxdepth 3 -print

  exit 1
fi

echo "LibreOffice directory:"
echo "$LO_DIR"

if [ ! -d "$LO_DIR/RPMS" ]; then
  echo "ERROR: LibreOffice RPMS directory not found:"
  echo "$LO_DIR/RPMS"

  find "$LO_DIR" -maxdepth 3 -print

  exit 1
fi


echo ""
echo "========================================"
echo "=== LIBREOFFICE RPMs"
echo "========================================"

echo "RPM files:"
find "$LO_DIR/RPMS" \
  -maxdepth 1 \
  -type f \
  -name '*.rpm' \
  -print

echo ""
echo "RPM count:"
find "$LO_DIR/RPMS" \
  -maxdepth 1 \
  -type f \
  -name '*.rpm' \
  | wc -l


echo ""
echo "========================================"
echo "=== INSTALLING LIBREOFFICE"
echo "========================================"

cd "$LO_DIR/RPMS"

echo "Installing LibreOffice RPMs with dnf..."

dnf install -y ./*.rpm


echo ""
echo "========================================"
echo "=== LOCATING LIBREOFFICE EXECUTABLE"
echo "========================================"

LIBREOFFICE_BIN="$(command -v libreoffice || true)"

if [ -z "$LIBREOFFICE_BIN" ]; then
  LIBREOFFICE_BIN="$(command -v soffice || true)"
fi

if [ -z "$LIBREOFFICE_BIN" ]; then
  echo "ERROR: LibreOffice was installed but executable was not found."

  echo ""
  echo "Searching for executable:"

  find /usr /opt /home \
    -type f \
    \\( -name "libreoffice" -o -name "soffice" \\) \
    -print 2>/dev/null || true

  exit 1
fi

echo "LibreOffice executable:"
echo "$LIBREOFFICE_BIN"

echo ""
echo "LibreOffice version:"
"$LIBREOFFICE_BIN" --version


echo ""
echo "========================================"
echo "=== TESTING LIBREOFFICE HEADLESS MODE"
echo "========================================"

TEST_DIR="$(mktemp -d)"
TEST_PROFILE="$(mktemp -d)"

echo "Test output directory:"
echo "$TEST_DIR"

echo "Test LibreOffice profile:"
echo "$TEST_PROFILE"


echo ""
echo "--- creating test XLSX ---"

python3 - "$TEST_DIR/test.xlsx" <<'PY'
import sys

from openpyxl import Workbook


output_path = sys.argv[1]

workbook = Workbook()
worksheet = workbook.active

worksheet["A1"] = "LibreOffice test"
worksheet["A2"] = "日本語テスト"
worksheet["B1"] = 123
worksheet["B2"] = "=B1*2"

workbook.save(output_path)

print("Created:", output_path)
PY

if [ ! -f "$TEST_DIR/test.xlsx" ]; then
  echo "ERROR: Failed to create XLSX test file."
  exit 1
fi

ls -lh "$TEST_DIR/test.xlsx"


echo ""
echo "--- XLSX -> PDF ---"

"$LIBREOFFICE_BIN" \
  --headless \
  --nologo \
  --nodefault \
  --nofirststartwizard \
  --norestore \
  "-env:UserInstallation=file://$TEST_PROFILE" \
  --convert-to pdf \
  --outdir "$TEST_DIR" \
  "$TEST_DIR/test.xlsx"

if [ ! -f "$TEST_DIR/test.pdf" ]; then
  echo "ERROR: XLSX -> PDF conversion failed."

  echo ""
  echo "Test directory:"
  find "$TEST_DIR" -maxdepth 2 -type f -print

  exit 1
fi

echo "XLSX -> PDF succeeded:"
ls -lh "$TEST_DIR/test.pdf"


echo ""
echo "--- PDF validation ---"

python3 - "$TEST_DIR/test.pdf" <<'PY'
import sys

import fitz


pdf_path = sys.argv[1]

document = fitz.open(pdf_path)

print("PDF pages:", len(document))

if len(document) == 0:
  raise RuntimeError(
    "Generated PDF contains zero pages."
  )

text = "".join(
  page.get_text()
  for page in document
)

print("Extracted PDF text:")
print(text[:1000])

if "LibreOffice test" not in text:
  raise RuntimeError(
    "Expected test text was not found in generated PDF."
  )
PY


echo ""
echo "========================================"
echo "=== CLEANING UP LIBREOFFICE TEST"
echo "========================================"

rm -rf "$TEST_DIR"
rm -rf "$TEST_PROFILE"
rm -rf "$TMP_DIR"

echo "LibreOffice installation and test succeeded."


echo ""
echo "========================================"
echo "=== CHECKING PYTHON PACKAGES"
echo "========================================"

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

    print(
      "OK   "
      + pkg
      + " -> "
      + str(location)
    )

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


echo ""
echo "========================================"
echo "=== VERIFYING INSTALLED PACKAGES"
echo "========================================"

python3 -m pip show \
  openpyxl \
  xlrd \
  python-docx \
  python-pptx \
  pandas \
  odfpy \
  pymupdf \
  pypdf \
  Pillow \
  boto3 \
  httpx || true


echo ""
echo "========================================"
echo "=== VERIFYING LIBREOFFICE"
echo "========================================"

echo "--- executable ---"
command -v libreoffice || true
command -v soffice || true

echo "--- version ---"
libreoffice --version 2>&1 || soffice --version 2>&1 || true


echo ""
echo "========================================"
echo "=== VERIFYING COMMAND-LINE SCRIPTS"
echo "========================================"

command -v form-convert || true
command -v form-inspect || true
command -v form-verify || true


echo ""
echo "========================================"
echo "=== INSTALL COMPLETE"
echo "========================================"
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
  """Uploads conversion/inspection scripts, downloads LibreOffice from
  private S3 storage, and installs all dependencies into the sandbox.

  Python dependencies are installed with pip.

  LibreOffice is downloaded from S3 during snapshot creation and installed
  locally from its Linux x86-64 RPM distribution.

  User documents are never sent to CloudConvert or another third-party
  conversion service.

  The LibreOffice installation is verified with an actual XLSX -> PDF
  conversion before snapshot creation continues.

  OCR is delegated to AWS Textract, which needs AWS credentials reachable
  from the sandbox.

  Raises on any failure.
  """

  # ------------------------------------------------------------------
  # Diagnostics
  # ------------------------------------------------------------------

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

  # ------------------------------------------------------------------
  # Validate S3 configuration.
  # ------------------------------------------------------------------

  if not LIBREOFFICE_S3_BUCKET:
    raise RuntimeError(
      "LIBREOFFICE_S3_BUCKET is not set. "
      "Set it to the S3 bucket containing the LibreOffice archive."
    )

  logger.info(
    "LibreOffice S3 bucket: %s",
    LIBREOFFICE_S3_BUCKET,
  )

  logger.info(
    "LibreOffice S3 key: %s",
    LIBREOFFICE_S3_KEY,
  )

  # ------------------------------------------------------------------
  # Create required directories.
  # ------------------------------------------------------------------

  mkdir_result = await session.exec(
    "mkdir",
    "-p",
    BIN_DIR,
    str(LIBREOFFICE_DOWNLOAD_DIR),
  )

  if mkdir_result.exit_code != 0:
    stderr = mkdir_result.stderr.decode(errors="replace")

    raise RuntimeError(
      "Failed to create required directories: "
      f"{stderr}"
    )

  # ------------------------------------------------------------------
  # Upload Python/shell scripts.
  # ------------------------------------------------------------------

  for filename in SCRIPT_FILES:
    local_path = SCRIPTS_DIR / filename
    workspace_path = Path("scripts") / filename

    if not local_path.exists():
      raise RuntimeError(
        f"Required script does not exist: {local_path}"
      )

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
      (
        f'pwd; '
        f'ls -lh "{workspace_path}" 2>&1 '
        f'|| echo "NOT FOUND at {workspace_path}"'
      ),
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

  # ------------------------------------------------------------------
  # Download LibreOffice from S3.
  #
  # We do this using boto3 from the Python process running the snapshot
  # provisioning code. This avoids:
  #
  #   1. putting the 241 MB archive in Git
  #   2. Git LFS
  #   3. public URLs
  #   4. curl/network downloads from inside the sandbox
  #
  # The S3 object should remain private.
  # ------------------------------------------------------------------

  logger.info(
    "Downloading LibreOffice from private S3..."
  )

  logger.info(
    "S3 location: s3://%s/%s",
    LIBREOFFICE_S3_BUCKET,
    LIBREOFFICE_S3_KEY,
  )

  try:
    import boto3
  except Exception as exc:
    raise RuntimeError(
      "boto3 is required to download LibreOffice from S3 "
      "during snapshot provisioning."
    ) from exc

  try:
    s3 = boto3.client("s3")

    # First verify credentials/account access.
    sts = boto3.client("sts")

    identity = sts.get_caller_identity()

    logger.info(
      "AWS credentials available. Account: %s",
      identity.get("Account"),
    )

    # Download directly to a local temporary file on the machine running
    # snapshot_setup.py.
    #
    # This is intentional: we don't want the 241 MB archive committed to
    # Git or uploaded through session.write().
    local_download_path = (
      SCRIPTS_DIR.parent.parent.parent.parent
      / ".libreoffice-download"
      / LIBREOFFICE_ARCHIVE
    )

    local_download_path.parent.mkdir(
      parents=True,
      exist_ok=True,
    )

    logger.info(
      "Downloading LibreOffice archive locally to: %s",
      local_download_path,
    )

    s3.download_file(
      LIBREOFFICE_S3_BUCKET,
      LIBREOFFICE_S3_KEY,
      str(local_download_path),
    )

    if not local_download_path.exists():
      raise RuntimeError(
        "S3 download completed but local archive does not exist: "
        f"{local_download_path}"
      )

    archive_size = local_download_path.stat().st_size

    logger.info(
      "LibreOffice archive downloaded: %.2f MB",
      archive_size / (1024 * 1024),
    )

    if archive_size < 100 * 1024 * 1024:
      raise RuntimeError(
        "LibreOffice archive appears unexpectedly small: "
        f"{archive_size} bytes"
      )

    # Upload the archive into the sandbox.
    logger.info(
      "Uploading LibreOffice archive to sandbox: %s",
      LIBREOFFICE_DOWNLOAD_PATH,
    )

    await session.write(
      Path(
        str(LIBREOFFICE_DOWNLOAD_PATH)
      ),
      io.BytesIO(
        local_download_path.read_bytes()
      ),
    )

    logger.info(
      "LibreOffice archive uploaded to sandbox."
    )

    # Remove the local temporary copy after it has been uploaded.
    try:
      local_download_path.unlink()
      logger.info(
        "Removed temporary local LibreOffice archive."
      )
    except Exception as exc:
      logger.warning(
        "Could not remove temporary local LibreOffice archive: %s",
        exc,
      )

  except Exception as exc:
    raise RuntimeError(
      "Failed to download LibreOffice archive from S3. "
      f"s3://{LIBREOFFICE_S3_BUCKET}/{LIBREOFFICE_S3_KEY}: "
      f"{type(exc).__name__}: {exc}"
    ) from exc

  # ------------------------------------------------------------------
  # Make scripts executable.
  # ------------------------------------------------------------------

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

  # ------------------------------------------------------------------
  # Install Python dependencies and LibreOffice.
  # ------------------------------------------------------------------

  logger.info(
    "Installing Python dependencies and LibreOffice ..."
  )

  result = await _run_and_log(
    session,
    "install",
    "sh",
    "-lc",
    INSTALL_COMMAND,
  )

  if result.exit_code != 0:
    raise RuntimeError(
      "Dependency install failed with exit code "
      f"{result.exit_code}"
    )