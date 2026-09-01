import asyncio
import io
import logging
import inspect
import tarfile
from pathlib import Path
from typing import Any
from uuid import UUID

from agents import Runner
from agents.run import RunConfig
from agents.sandbox import (
  SandboxAgent,
  SandboxRunConfig
)
from agents.extensions.sandbox import (
  VercelSandboxClientOptions,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.forms.prompts import (
  FORM_AGENT_INSTRUCTIONS,
)
from app.services.forms.sandbox import (
  FORM_AGENT_SNAPSHOT_ID,
  build_snapshot_client,
  get_form_agent_snapshot,
)
from app.services.forms.scripts.snapshot_setup import provision_dependencies


logger = logging.getLogger(__name__)


def build_form_agent() -> SandboxAgent:
  return SandboxAgent(
    name="Kenchiku AI Form Agent",
    instructions=FORM_AGENT_INSTRUCTIONS,
  )


async def run_form_agent(
  prompt: str,
  sandbox_client: Any,
  workspace: Path,
  output_dir: Path,
  db: AsyncSession,
  company_id: UUID,
  project_id: UUID | None,
):
  agent = build_form_agent()

  workspace = workspace.resolve()
  input_dir = (
    workspace / "input"
  ).resolve()
  output_dir = output_dir.resolve()

  host_input_files = []

  if input_dir.exists():
    entries = sorted(
      input_dir.iterdir(),
      key=lambda path: path.name,
    )

    for entry in entries:
      if entry.is_file():
        host_input_files.append(entry)

  if not host_input_files:
    raise ValueError(
      "No input files were found in the host input directory."
    )

  logger.info(
    "Starting form agent for company %s, project %s.",
    company_id,
    project_id,
  )

  logger.info(
    "Form agent prompt characters: %d",
    len(prompt),
  )

  snapshot_client = build_snapshot_client()

  snapshot_exists = await asyncio.to_thread(
    snapshot_client.exists,
    FORM_AGENT_SNAPSHOT_ID,
  )

  logger.info(
    "INITIAL SNAPSHOT EXISTS: snapshot=%r exists=%s",
    FORM_AGENT_SNAPSHOT_ID,
    snapshot_exists,
  )

  # ------------------------------------------------------------------
  # BOOTSTRAP PHASE
  #
  # If the snapshot does not exist, create a completely fresh sandbox
  # without a snapshot, install the dependencies, then close it.
  #
  # aclose() -> stop() -> persist snapshot -> S3 upload
  # ------------------------------------------------------------------

  if not snapshot_exists:
    logger.warning(
      "Snapshot %r not found. "
      "Creating a temporary bootstrap sandbox without a snapshot.",
      FORM_AGENT_SNAPSHOT_ID,
    )

    bootstrap_sandbox = None

    try:
      logger.info(
        "BOOTSTRAP: creating sandbox WITHOUT snapshot."
      )

      bootstrap_sandbox = await sandbox_client.create(
        options=VercelSandboxClientOptions(
          allow_s3_credential_exposure=False,
          timeout_ms=600_000,
          runtime="python3.13",
        ),
      )

      logger.info(
        "BOOTSTRAP: sandbox created."
      )

      logger.info(
        "BOOTSTRAP: installing dependencies."
      )

      await provision_dependencies(
        bootstrap_sandbox,
      )

      logger.info(
        "BOOTSTRAP: dependency installation complete."
      )

      # --------------------------------------------------------------
      # Verify exactly what exists before snapshot persistence.
      # --------------------------------------------------------------

      bootstrap_check = await bootstrap_sandbox.exec(
        "sh",
        "-lc",
        """
set -x

echo "=== BOOTSTRAP IDENTITY ==="
whoami
id
pwd

echo "=== BOOTSTRAP PATH ==="
echo "$PATH"

echo "=== BOOTSTRAP PYTHON ==="
command -v python3 || true
python3 --version || true
python3 -m pip --version || true

echo "=== BOOTSTRAP SCRIPTS ==="
find /home/vercel-sandbox \
  -type f \
  \\( \
    -name "form-convert" -o \
    -name "form-inspect" -o \
    -name "form-verify" -o \
    -name "inspect_excel.py" \
  \\) \
  -print 2>/dev/null || true

echo "=== BOOTSTRAP WORKSPACE ==="
find . \
  -maxdepth 4 \
  -print \
  2>/dev/null || true

echo "=== BOOTSTRAP PACKAGE CHECK ==="
python3 - <<'PY'
import importlib

mods = {
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
}

for package, module in mods.items():
  try:
    imported = importlib.import_module(module)
    print(
      f"OK      {package} -> "
      f"{getattr(imported, '__file__', 'unknown')}"
    )
  except Exception as exc:
    print(
      f"MISSING {package} -> "
      f"{type(exc).__name__}: {exc}"
    )
PY

echo "=== BOOTSTRAP DONE ==="
        """,
      )

      logger.info(
        "BOOTSTRAP verification exit code: %s",
        bootstrap_check.exit_code,
      )

      logger.info(
        "BOOTSTRAP verification stdout:\n%s",
        bootstrap_check.stdout.decode(
          errors="replace",
        ),
      )

      logger.info(
        "BOOTSTRAP verification stderr:\n%s",
        bootstrap_check.stderr.decode(
          errors="replace",
        ),
      )

      if bootstrap_check.exit_code != 0:
        raise RuntimeError(
          "Bootstrap dependency verification failed."
        )

      # --------------------------------------------------------------
      # IMPORTANT:
      #
      # Do NOT create input/ or output/ here.
      #
      # Do NOT upload the user's job files here.
      #
      # Closing this sandbox will persist its workspace as the
      # dependency snapshot.
      # --------------------------------------------------------------

      logger.info(
        "BOOTSTRAP: closing sandbox to persist snapshot."
      )

    finally:
      if bootstrap_sandbox is not None:
        try:
          await bootstrap_sandbox.aclose()

          logger.info(
            "BOOTSTRAP: sandbox closed. "
            "Snapshot persistence should now be complete."
          )

        except Exception:
          logger.exception(
            "BOOTSTRAP: error closing bootstrap sandbox."
          )
          raise

    # --------------------------------------------------------------
    # Verify that the snapshot now exists in S3.
    # --------------------------------------------------------------

    snapshot_exists = await asyncio.to_thread(
      snapshot_client.exists,
      FORM_AGENT_SNAPSHOT_ID,
    )

    logger.info(
      "POST-BOOTSTRAP SNAPSHOT EXISTS: snapshot=%r exists=%s",
      FORM_AGENT_SNAPSHOT_ID,
      snapshot_exists,
    )

    if not snapshot_exists:
      raise RuntimeError(
        "Bootstrap sandbox closed successfully, but the expected "
        f"snapshot {FORM_AGENT_SNAPSHOT_ID!r} was not found in S3."
      )

  # ------------------------------------------------------------------
  # JOB SANDBOX
  #
  # At this point the snapshot MUST exist.
  #
  # Create a fresh sandbox from it, explicitly start it, and let the
  # SDK hydrate the workspace from S3.
  # ------------------------------------------------------------------

  logger.info(
    "Creating JOB sandbox from snapshot %r.",
    FORM_AGENT_SNAPSHOT_ID,
  )

  snapshot = get_form_agent_snapshot()

  logger.info(
    "CREATING JOB SANDBOX WITH SNAPSHOT: "
    "type=%s id=%r client_dependency_key=%r",
    type(snapshot).__name__,
    snapshot.id,
    snapshot.client_dependency_key,
  )

  sandbox = None

  try:
    sandbox = await sandbox_client.create(
      snapshot=snapshot,
      options=VercelSandboxClientOptions(
        allow_s3_credential_exposure=False,
        timeout_ms=300_000,
        runtime="python3.13",
      ),
    )

    logger.info(
      "JOB SANDBOX: created."
    )

    await sandbox.start()

    logger.info(
      "JOB SANDBOX: started. Snapshot should now be hydrated."
    )

    # --------------------------------------------------------------
    # Verify that the snapshot actually restored.
    # --------------------------------------------------------------

    check_result = await sandbox.exec(
      "sh",
      "-lc",
      """
set -x

echo "=== IDENTITY ==="
whoami
id
pwd

echo "=== PATH ==="
echo "$PATH"

echo "=== HOME ==="
echo "HOME=$HOME"

echo "=== PYTHON ==="
command -v python3 || true
python3 --version || true

echo "=== PIP ==="
python3 -m pip --version || true

echo "=== EXPECTED SCRIPT DIRECTORY ==="
ls -la /home/vercel-sandbox/.local/bin 2>&1 || true

echo "=== EXPECTED SCRIPTS ==="
for f in \
  /home/vercel-sandbox/.local/bin/form-convert \
  /home/vercel-sandbox/.local/bin/form-inspect \
  /home/vercel-sandbox/.local/bin/form-verify \
  /home/vercel-sandbox/.local/bin/inspect_excel.py
do
  if [ -f "$f" ]; then
    echo "FOUND: $f"
    ls -l "$f"
  else
    echo "MISSING: $f"
  fi
done

echo "=== SEARCH FOR OUR SCRIPTS ==="
find /home /tmp /workspace /app /usr/local/bin \
  -type f \
  \\( \
    -name "form-convert" -o \
    -name "form-inspect" -o \
    -name "form-verify" -o \
    -name "inspect_excel.py" \
  \\) \
  -print 2>/dev/null || true

echo "=== PYTHON PACKAGES ==="
python3 - <<'PY'
import importlib

mods = {
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
}

for package, module in mods.items():
  try:
    imported = importlib.import_module(module)
    print(
      f"OK      {package} -> "
      f"{getattr(imported, '__file__', 'unknown')}"
    )
  except Exception as exc:
    print(
      f"MISSING {package} -> "
      f"{type(exc).__name__}: {exc}"
    )
PY

echo "=== PYTHON SITE-PACKAGES ==="
python3 - <<'PY'
import site
import sys

print("sys.executable:", sys.executable)

print("sys.path:")
for path in sys.path:
  print("  ", path)

print("site-packages:")
try:
  for path in site.getsitepackages():
    print("  ", path)
except Exception as exc:
  print("ERROR:", exc)

try:
  print("user-site:", site.getusersitepackages())
except Exception as exc:
  print("user-site ERROR:", exc)
PY

echo "=== PIP PACKAGE LIST ==="
python3 -m pip list 2>&1 || true

echo "=== COMMAND LOOKUP ==="
export PATH="/home/vercel-sandbox/.local/bin:$PATH"

command -v form-convert || true
command -v form-inspect || true
command -v form-verify || true

echo "=== DIRECT EXECUTION TEST ==="
/home/vercel-sandbox/.local/bin/form-convert --help 2>&1 || true
/home/vercel-sandbox/.local/bin/form-inspect --help 2>&1 || true
/home/vercel-sandbox/.local/bin/form-verify --help 2>&1 || true

echo "=== WORKSPACE BEFORE JOB FILES ==="
find . \
  -maxdepth 4 \
  -print \
  2>/dev/null || true

echo "=== DONE ==="
      """,
    )

    stdout = check_result.stdout.decode(
      errors="replace",
    )

    stderr = check_result.stderr.decode(
      errors="replace",
    )

    logger.info(
      "Sandbox dependency check stdout:\n%s",
      stdout,
    )

    if stderr:
      logger.warning(
        "Sandbox dependency check stderr:\n%s",
        stderr,
      )

    if check_result.exit_code != 0:
      logger.warning(
        "Sandbox dependency diagnostic returned exit code %s. "
        "Continuing so the full diagnostic output can be inspected.",
        check_result.exit_code,
      )

    # --------------------------------------------------------------
    # ONLY NOW create the job directories.
    # They therefore cannot contaminate the dependency snapshot.
    # --------------------------------------------------------------

    mkdir_result = await sandbox.exec(
      "mkdir",
      "-p",
      "input",
      "output",
    )

    if mkdir_result.exit_code != 0:
      stderr = mkdir_result.stderr.decode(
        errors="replace",
      )

      raise RuntimeError(
        "Failed to create input/output directories "
        "inside sandbox. "
        f"Exit code: {mkdir_result.exit_code}. "
        f"Error: {stderr}"
      )

    # --------------------------------------------------------------
    # Upload this job's input files.
    # --------------------------------------------------------------

    for host_file in host_input_files:
      sandbox_path = (
        Path("input")
        / host_file.name
      )

      file_bytes = host_file.read_bytes()

      await sandbox.write(
        sandbox_path,
        io.BytesIO(file_bytes),
      )

      uploaded_file = await sandbox.read(
        sandbox_path,
      )

      uploaded_bytes = uploaded_file.read()

      if len(uploaded_bytes) != len(file_bytes):
        raise RuntimeError(
          "Sandbox upload verification failed for "
          f"{host_file.name}: "
          f"host={len(file_bytes)} bytes, "
          f"sandbox={len(uploaded_bytes)} bytes"
        )

      if uploaded_bytes != file_bytes:
        raise RuntimeError(
          "Sandbox upload verification failed for "
          f"{host_file.name}: contents differ"
        )

    logger.info(
      "Starting form agent with %d input file(s).",
      len(host_input_files),
    )

    # --------------------------------------------------------------
    # Run agent.
    # --------------------------------------------------------------

    try:
      result = await Runner.run(
        agent,
        prompt,
        run_config=RunConfig(
          sandbox=SandboxRunConfig(
            session=sandbox,
          ),
        ),
        max_turns=50,
      )

    except Exception:
      logger.exception(
        "Form agent execution failed.",
      )
      raise

    logger.info(
      "Form agent execution completed successfully.",
    )

    await _collect_sandbox_output_files(
      sandbox,
      output_dir,
    )

    logger.info(
      "Sandbox output files collected successfully.",
    )

    return result

  except Exception:
    logger.exception(
      "Form agent / sandbox processing failed.",
    )
    raise

  finally:
    if sandbox is not None:
      try:
        await sandbox.aclose()
      except Exception:
        logger.exception(
          "Error closing form agent sandbox.",
        )


async def _collect_sandbox_output_files(
  sandbox,
  output_dir: Path,
) -> None:
  output_dir.mkdir(
    parents=True,
    exist_ok=True,
  )

  find_result = await sandbox.exec(
    "find",
    "output",
    "-type",
    "f",
    "-print",
  )

  stdout = find_result.stdout.decode(
    errors="replace",
  )

  stderr = find_result.stderr.decode(
    errors="replace",
  )

  if find_result.exit_code != 0:
    raise RuntimeError(
      "Failed to enumerate sandbox output files: "
      f"{stderr}"
    )

  sandbox_files = [
    line.strip()
    for line in stdout.splitlines()
    if line.strip()
  ]

  if not sandbox_files:
    logger.warning(
      "Sandbox output directory contains no files.",
    )

    return

  logger.info(
    "Found %d sandbox output file(s).",
    len(sandbox_files),
  )

  for sandbox_file_string in sandbox_files:
    sandbox_path = Path(
      sandbox_file_string,
    )

    try:
      relative_path = sandbox_path.relative_to(
        Path("output"),
      )
    except ValueError:
      raise RuntimeError(
        "Sandbox returned a file outside output/: "
        f"{sandbox_path}"
      )

    destination_path = (
      output_dir / relative_path
    )

    destination_path.parent.mkdir(
      parents=True,
      exist_ok=True,
    )

    file_obj = await sandbox.read(
      sandbox_path,
    )

    file_contents = file_obj.read()

    with destination_path.open(
      "wb",
    ) as destination:
      destination.write(
        file_contents,
      )