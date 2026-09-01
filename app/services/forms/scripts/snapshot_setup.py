import asyncio
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
# It lives in a private S3 bucket. Rather than downloading it through the
# form-worker host process (which OOM'd a 512MB Fargate task pulling a
# 241MB file into memory) or exposing long-lived AWS credentials inside the
# sandbox, we generate a short-lived presigned GET URL on the host and have
# the sandbox `curl` it directly. The archive bytes never pass through the
# worker process's memory or disk.
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
LIBREOFFICE_WORKSPACE_DIR = Path("libreoffice")

LIBREOFFICE_WORKSPACE_PATH = (
  LIBREOFFICE_WORKSPACE_DIR / LIBREOFFICE_ARCHIVE
)

# How long the presigned URL is valid for. Keep this tight -- it only needs
# to survive one curl download, not the full provisioning run.
LIBREOFFICE_PRESIGN_EXPIRY_SECONDS = 900  # 15 minutes

# Path (inside the sandbox workspace) where we briefly stage the presigned
# URL before curl consumes it. Removed immediately after use so it never
# lingers in the sandbox filesystem or gets persisted into the snapshot.
LIBREOFFICE_URL_ENV_PATH = Path("libreoffice/.lo_url.env")


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
echo "=== END DIAGNOSTICS"
echo "========================================"
"""


# ---------------------------------------------------------------------------
# LibreOffice download command (runs INSIDE the sandbox)
# ---------------------------------------------------------------------------
#
# This is executed separately from INSTALL_COMMAND, immediately after we
# stage the presigned URL file, so that:
#
#   1. The URL file's lifetime is as short as possible (written, sourced,
#      deleted, all in one shell invocation).
#   2. The presigned URL is never interpolated into INSTALL_COMMAND's
#      f-string, so it can never end up in _run_and_log's stdout/stderr
#      logging for the big install step.
#
# `set -x` is intentionally OMITTED here (unlike some of our other debug
# commands) so the sourced URL is never echoed to logs.
#
LIBREOFFICE_DOWNLOAD_COMMAND = f"""
set -euo pipefail

echo "========================================"
echo "=== DOWNLOADING LIBREOFFICE VIA PRESIGNED URL"
echo "========================================"

URL_FILE="{LIBREOFFICE_URL_ENV_PATH}"

if [ ! -f "$URL_FILE" ]; then
  echo "ERROR: presigned URL file not found at $URL_FILE"
  exit 1
fi

# shellcheck disable=SC1090
. "$URL_FILE"

# Remove the URL file immediately -- it must not survive into the
# snapshot and should exist on disk for as little time as possible.
rm -f "$URL_FILE"

if [ -z "${{LO_URL:-}}" ]; then
  echo "ERROR: LO_URL was not set after sourcing $URL_FILE"
  exit 1
fi

mkdir -p "{LIBREOFFICE_WORKSPACE_DIR}"

echo "Downloading LibreOffice archive to {LIBREOFFICE_WORKSPACE_PATH} ..."

curl \
  --fail \
  --show-error \
  --location \
  --retry 3 \
  --retry-delay 2 \
  --connect-timeout 30 \
  --max-time 600 \
  --output "{LIBREOFFICE_WORKSPACE_PATH}" \
  "$LO_URL"

# Belt-and-suspenders: make sure the URL variable doesn't linger in this
# shell's environment any longer than necessary.
unset LO_URL

if [ ! -f "{LIBREOFFICE_WORKSPACE_PATH}" ]; then
  echo "ERROR: curl reported success but archive is missing:"
  echo "{LIBREOFFICE_WORKSPACE_PATH}"
  exit 1
fi

echo "Downloaded archive:"
ls -lh "{LIBREOFFICE_WORKSPACE_PATH}"

SIZE_BYTES="$(stat -c '%s' "{LIBREOFFICE_WORKSPACE_PATH}" 2>/dev/null || stat -f '%z' "{LIBREOFFICE_WORKSPACE_PATH}")"

echo "Downloaded size (bytes): $SIZE_BYTES"

if [ "$SIZE_BYTES" -lt 104857600 ]; then
  echo "ERROR: LibreOffice archive appears unexpectedly small: $SIZE_BYTES bytes"
  echo "This usually means the presigned URL returned an XML error body"
  echo "instead of the archive (e.g. expired URL, wrong key/bucket)."
  echo "First 2KB of downloaded content for debugging:"
  head -c 2048 "{LIBREOFFICE_WORKSPACE_PATH}" || true
  exit 1
fi

echo "LibreOffice archive download verified."
"""


# ---------------------------------------------------------------------------
# Installation command
# ---------------------------------------------------------------------------
#
# By the time this runs, the LibreOffice archive is already sitting in
# the sandbox workspace (downloaded by LIBREOFFICE_DOWNLOAD_COMMAND above).
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

LO_ARCHIVE="libreoffice/{LIBREOFFICE_ARCHIVE}"

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

echo "Checking for passwordless sudo access..."

if ! sudo -n true 2>/dev/null; then
  echo "ERROR: sudo is not available (or requires a password) for the"
  echo "current user. Package installation requires root privileges."
  echo ""
  echo "Current user:"
  whoami
  id
  echo ""
  echo "sudo -n true output:"
  sudo -n true
  exit 1
fi

echo "sudo is available. Installing LibreOffice RPMs with dnf..."

sudo dnf install -y ./*.rpm


echo ""
echo "========================================"
echo "=== LOCATING LIBREOFFICE EXECUTABLE"
echo "========================================"

LIBREOFFICE_BIN="$(command -v libreoffice || true)"

if [ -z "$LIBREOFFICE_BIN" ]; then
  LIBREOFFICE_BIN="$(command -v soffice || true)"
fi

# The RPM distribution installs into /opt/libreofficeNN.N/program/soffice,
# which is NOT on PATH by default -- so command -v will normally miss it
# even on a successful install. Fall back to a direct filesystem search.
if [ -z "$LIBREOFFICE_BIN" ]; then
  echo "libreoffice/soffice not found on PATH, searching /opt directly..."

  LIBREOFFICE_BIN="$(find /opt \
    -maxdepth 4 \
    -type f \
    -path '*/program/soffice' \
    -print \
    -quit)"
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

# Symlink it onto PATH via BIN_DIR so downstream scripts (form-convert,
# form-inspect, form-verify) can invoke `soffice` / `libreoffice` at
# job-run time without hardcoding the /opt/libreofficeNN.N path, which
# will change on every LibreOffice version bump.
echo ""
echo "Symlinking into {BIN_DIR} ..."

ln -sf "$LIBREOFFICE_BIN" "{BIN_DIR}/soffice"
ln -sf "$LIBREOFFICE_BIN" "{BIN_DIR}/libreoffice"

ls -lh "{BIN_DIR}/soffice" "{BIN_DIR}/libreoffice"

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

echo ""
echo "=== REMOVING LIBREOFFICE ARCHIVE ==="

rm -rf "libreoffice"

echo "LibreOffice archive removed from sandbox workspace."

echo ""
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


def _generate_libreoffice_presigned_url() -> str:
  """Generates a short-lived presigned GET URL for the LibreOffice archive.

  Runs boto3 synchronously -- callers should invoke this via
  asyncio.to_thread so it doesn't block the event loop.

  This is the ONLY AWS/S3 interaction the host process performs for
  LibreOffice provisioning. No archive bytes ever pass through this
  process; boto3 here just signs a URL.
  """

  import boto3

  s3 = boto3.client("s3")

  return s3.generate_presigned_url(
    "get_object",
    Params={
      "Bucket": LIBREOFFICE_S3_BUCKET,
      "Key": LIBREOFFICE_S3_KEY,
    },
    ExpiresIn=LIBREOFFICE_PRESIGN_EXPIRY_SECONDS,
  )


async def provision_dependencies(session) -> None:
  """Uploads conversion/inspection scripts, downloads LibreOffice into the
  sandbox via a presigned S3 URL, and installs all dependencies.

  Python dependencies are installed with pip.

  LibreOffice is downloaded FROM INSIDE THE SANDBOX via `curl` against a
  short-lived presigned URL generated by the host process. The archive's
  241MB of bytes are never read into the host (form-worker) process's
  memory -- only a signed URL string is generated and handed to the
  sandbox. This avoids the OOM the host process previously hit trying to
  buffer the whole archive before re-uploading it via session.write().

  It also avoids exposing long-lived AWS credentials inside the sandbox:
  the presigned URL is scoped to a single GET of a single object and
  expires after LIBREOFFICE_PRESIGN_EXPIRY_SECONDS.

  User documents are never sent to CloudConvert or another third-party
  conversion service.

  The LibreOffice installation is verified with an actual XLSX -> PDF
  conversion before snapshot creation continues.

  OCR is delegated to AWS Textract, which needs AWS credentials reachable
  from the sandbox at *job run* time (unrelated to this function).

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
    "sh",
    "-lc",
    f'rm -rf "libreoffice" && mkdir -p "{BIN_DIR}" "libreoffice"',
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
  # Generate a presigned URL and hand it to the sandbox.
  #
  # boto3 here does NOT download the archive -- it only signs a URL.
  # This is a fast, constant-memory operation regardless of archive size.
  # ------------------------------------------------------------------

  logger.info(
    "Generating presigned URL for LibreOffice archive "
    "(expires in %ss)...",
    LIBREOFFICE_PRESIGN_EXPIRY_SECONDS,
  )

  try:
    presigned_url = await asyncio.to_thread(
      _generate_libreoffice_presigned_url,
    )
  except Exception as exc:
    raise RuntimeError(
      "Failed to generate presigned URL for LibreOffice archive "
      f"s3://{LIBREOFFICE_S3_BUCKET}/{LIBREOFFICE_S3_KEY}: "
      f"{type(exc).__name__}: {exc}"
    ) from exc

  logger.info(
    "Presigned URL generated. NOT logging the URL itself "
    "(it is a bearer credential for the object)."
  )

  # Stage the URL inside the sandbox as a tiny env file. This is a KB of
  # data, not 241MB, so session.write() here carries no meaningful memory
  # cost on the host side.
  url_env_contents = f"LO_URL='{presigned_url}'\n".encode("utf-8")

  await session.write(
    LIBREOFFICE_URL_ENV_PATH,
    io.BytesIO(url_env_contents),
  )

  # Drop our own reference to the URL string as soon as we're done with it.
  del presigned_url
  del url_env_contents

  # ------------------------------------------------------------------
  # Have the sandbox download the archive itself via curl.
  #
  # This step deliberately does NOT go through _run_and_log, since that
  # helper logs full stdout/stderr -- if curl ever echoed the resolved
  # URL (e.g. on a redirect chain with -v) we don't want that in logs.
  # LIBREOFFICE_DOWNLOAD_COMMAND itself avoids `set -x` for the same
  # reason.
  # ------------------------------------------------------------------

  logger.info(
    "Downloading LibreOffice archive inside sandbox via curl..."
  )

  download_result = await session.exec(
    "sh",
    "-lc",
    LIBREOFFICE_DOWNLOAD_COMMAND,
  )

  download_stdout = download_result.stdout.decode(errors="replace")
  download_stderr = download_result.stderr.decode(errors="replace")

  logger.info(
    "LibreOffice download stdout:\n%s",
    download_stdout,
  )

  if download_stderr:
    logger.warning(
      "LibreOffice download stderr:\n%s",
      download_stderr,
    )

  if download_result.exit_code != 0:
    raise RuntimeError(
      "Failed to download LibreOffice archive inside sandbox via "
      f"presigned URL. Exit code: {download_result.exit_code}"
    )

  logger.info(
    "LibreOffice archive downloaded successfully inside sandbox."
  )

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