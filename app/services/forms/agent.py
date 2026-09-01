import asyncio
import io
import logging
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
from app.services.forms.scripts.snapshot_setup import (
  RUNTIME_ARCHIVE_NAME,
  provision_dependencies,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Deterministic format conversion
# ---------------------------------------------------------------------------
#
# Some input formats (legacy XLS, DOC, PPT, ODS) cannot be edited directly
# by openpyxl/python-docx/python-pptx and must first be converted to a
# modern equivalent via LibreOffice (form-convert). This used to be left
# entirely to the agent's judgment via prompt instructions. In production
# this proved unreliable: the agent silently gave up on an XLS file
# ("編集環境の制約により入力欄へ記入できませんでした") without any
# evidence it ever attempted form-convert.
#
# There is exactly one correct way to handle these conversions -- no
# judgment call is involved -- so they are now performed deterministically
# here, outside the agent's tool loop entirely. The agent only ever sees
# an already-converted, directly editable file.
#
# ROUND_TRIP_CONVERSIONS: the original extension is the format the
# customer expects back, so after the agent edits the modern equivalent,
# we convert its output back to the original extension automatically.
ROUND_TRIP_CONVERSIONS = {
  "xls": "xlsx",
  "ods": "xlsx",
}

# ONE_WAY_CONVERSIONS: the modern equivalent is an acceptable final output
# format on its own, so no reverse conversion is performed.
ONE_WAY_CONVERSIONS = {
  "doc": "docx",
  "ppt": "pptx",
}

EDITABLE_FORMAT = {
  **ROUND_TRIP_CONVERSIONS,
  **ONE_WAY_CONVERSIONS,
}


def build_form_agent() -> SandboxAgent:
  return SandboxAgent(
    name="Kenchiku AI Form Agent",
    instructions=FORM_AGENT_INSTRUCTIONS,
  )


async def _run_form_convert(
  sandbox,
  input_relative_path: str,
  output_dir_relative: str,
) -> str:
  """Runs form-convert inside the sandbox for a single file.

  Returns the filename (not full path) of the produced output file on
  success. Raises RuntimeError with full stdout/stderr context on any
  failure.
  """

  result = await sandbox.exec(
    "form-convert",
    input_relative_path,
    output_dir_relative,
  )

  stdout = result.stdout.decode(errors="replace")
  stderr = result.stderr.decode(errors="replace")

  if result.exit_code != 0:
    raise RuntimeError(
      "form-convert failed for "
      f"{input_relative_path!r} (exit_code={result.exit_code}). "
      f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )

  produced_line = stdout.strip().splitlines()[-1] if stdout.strip() else ""

  if not produced_line:
    raise RuntimeError(
      "form-convert reported success but printed no output path for "
      f"{input_relative_path!r}. stdout:\n{stdout}\nstderr:\n{stderr}"
    )

  return Path(produced_line).name


async def _preconvert_input_files(
  sandbox,
  host_input_files: list[Path],
) -> dict[str, dict[str, Any]]:
  """Deterministically converts any legacy-format input files to an
  editable modern format, before the agent ever runs.

  Returns a mapping of:

    converted_filename -> {
      "original_filename": str,
      "original_extension": str,
      "round_trip": bool,
    }

  for every file that required conversion. Files that didn't need
  conversion (already-editable formats, PDFs, images, etc.) are simply
  omitted from the returned mapping.
  """

  conversion_map: dict[str, dict[str, Any]] = {}

  for host_file in host_input_files:
    extension = host_file.suffix.lower().lstrip(".")
    target_format = EDITABLE_FORMAT.get(extension)

    if target_format is None:
      continue

    logger.info(
      "Deterministically pre-converting %s (.%s -> .%s).",
      host_file.name,
      extension,
      target_format,
    )

    converted_name = await _run_form_convert(
      sandbox,
      f"input/{host_file.name}",
      "input",
    )

    conversion_map[converted_name] = {
      "original_filename": host_file.name,
      "original_extension": extension,
      "round_trip": extension in ROUND_TRIP_CONVERSIONS,
    }

    logger.info(
      "Pre-converted input/%s -> input/%s",
      host_file.name,
      converted_name,
    )

  return conversion_map


def _build_conversion_prompt_addendum(
  conversion_map: dict[str, dict[str, Any]],
) -> str:
  """Builds a short, explicit prompt section telling the agent exactly
  which pre-converted files to edit and exactly what to save them as.

  Returns an empty string if no conversions were performed.
  """

  if not conversion_map:
    return ""

  lines = [
    "",
    "=" * 60,
    "AUTOMATIC FORMAT CONVERSION (already performed)",
    "=" * 60,
    "",
    "The following input files have ALREADY been automatically "
    "converted to an editable format before you started. This was done "
    "deterministically outside your control.",
    "",
    "For these specific files:",
    "",
    "- Do NOT call form-convert on them.",
    "- Do NOT attempt to convert your output back to the original "
    "format yourself.",
    "- The system will automatically handle any reverse conversion "
    "after you finish, where applicable.",
    "",
  ]

  for converted_name, info in conversion_map.items():
    stem = Path(converted_name).stem
    suffix = Path(converted_name).suffix  # includes leading "."

    lines.append(
      f"- input/{info['original_filename']} was converted to "
      f"input/{converted_name}."
    )
    lines.append(
      f"  Edit input/{converted_name} directly. The original "
      f"input/{info['original_filename']} is kept only for reference "
      f"and must not be modified."
    )

    if info["round_trip"]:
      lines.append(
        f"  Save your completed work as EXACTLY output/{stem}{suffix} "
        f"-- the system will automatically convert this back to "
        f".{info['original_extension']} afterward. Do not save it "
        f"under any other filename or extension."
      )
    else:
      lines.append(
        f"  Save your completed work as output/{stem}{suffix}. This is "
        f"the final output format for this file -- no further "
        f"conversion will be performed."
      )

    lines.append("")

  return "\n".join(lines)


async def _postconvert_output_files(
  sandbox,
  conversion_map: dict[str, dict[str, Any]],
) -> dict[str, str]:
  """Deterministically converts round-trip output files back to their
  original extension, after the agent has finished.

  For every entry in conversion_map with round_trip=True, checks whether
  the agent actually produced the expected editable-format output file.
  If so, converts it back to the original extension, removes the
  intermediate editable-format file from the sandbox (so it isn't also
  collected as a redundant separate output), and records the rename.

  If the agent did NOT produce the expected file for a given input, that
  input is skipped (the agent may have legitimately been unable to
  complete it) -- this only converts what the agent actually saved.

  Returns a mapping of old sandbox path -> new sandbox path (e.g.
  "output/x.xlsx" -> "output/x.xls") for every file actually converted,
  so callers can patch up any text (e.g. the agent's own final JSON
  output) that references the intermediate filename.
  """

  path_replacements: dict[str, str] = {}

  for converted_name, info in conversion_map.items():
    if not info["round_trip"]:
      continue

    stem = Path(converted_name).stem
    editable_ext = Path(converted_name).suffix.lstrip(".")
    output_relative = f"output/{stem}.{editable_ext}"

    check = await sandbox.exec(
      "sh",
      "-lc",
      f'[ -f "{output_relative}" ] && echo FOUND || echo MISSING',
    )

    check_stdout = check.stdout.decode(errors="replace").strip()

    if "FOUND" not in check_stdout:
      logger.warning(
        "Expected agent output %s not found -- skipping automatic "
        "reverse conversion for %s. The agent may not have completed "
        "this file.",
        output_relative,
        info["original_filename"],
      )
      continue

    logger.info(
      "Deterministically reverse-converting %s (.%s -> .%s).",
      output_relative,
      editable_ext,
      info["original_extension"],
    )

    reverse_converted_name = await _run_form_convert(
      sandbox,
      output_relative,
      "output",
    )

    # Remove the intermediate editable-format file so it isn't also
    # collected/uploaded as a separate, redundant output file.
    await sandbox.exec(
      "rm",
      "-f",
      output_relative,
    )

    new_relative = f"output/{reverse_converted_name}"

    logger.info(
      "Reverse-converted %s -> %s (removed intermediate %s)",
      output_relative,
      new_relative,
      output_relative,
    )

    path_replacements[output_relative] = new_relative

  return path_replacements


async def run_form_agent(
  prompt: str,
  sandbox_client: Any,
  workspace: Path,
  output_dir: Path,
  db: AsyncSession,
  company_id: UUID,
  project_id: UUID | None,
) -> tuple[Any, dict[str, str]]:
  """Runs the form agent.

  Returns a tuple of (agent_run_result, path_replacements). path_replacements
  maps any intermediate editable-format output path to its final,
  reverse-converted path (e.g. "output/x.xlsx" -> "output/x.xls"), for any
  file that was automatically round-trip-converted. Callers that inspect
  the agent's own final JSON output should apply these replacements to
  that text before parsing, since the agent's JSON will still reference
  the intermediate filename it actually saved.
  """

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
        snapshot=get_form_agent_snapshot(),
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

echo "=== BOOTSTRAP SCRIPTS ==="
find /home/vercel-sandbox \
  -type f \
  \\( \
    -name "form-convert" -o \
    -name "form-inspect" -o \
    -name "form-ocr" -o \
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
      # dependency snapshot. Note "workspace" here specifically means
      # the sandbox's workspace directory (relative session.write()
      # paths) -- NOT $HOME or /opt. Anything provision_dependencies()
      # installed outside the workspace (the LibreOffice /opt install,
      # $HOME/.local pip packages and cp'd scripts) only survives this
      # boundary because provision_dependencies() already packaged it
      # into RUNTIME_ARCHIVE_NAME inside the workspace before returning.
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

        finally:
          try:
            await sandbox_client.delete(bootstrap_sandbox)

            logger.info(
              "BOOTSTRAP: sandbox deleted."
            )

          except Exception:
            logger.exception(
              "BOOTSTRAP: error deleting bootstrap sandbox."
            )

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
    # Restore everything that lives outside the workspace directory.
    #
    # The snapshot mechanism only hydrates the workspace (this is why
    # provision_dependencies() only ever showed "./scripts" and
    # "./libreoffice" as tar entries, never anything under $HOME or
    # /opt). provision_dependencies() packaged $HOME/.local, the
    # LibreOffice /opt install, and the /usr/local/bin symlinks into
    # RUNTIME_ARCHIVE_NAME, placed at the workspace root, specifically
    # so it WOULD get captured by the snapshot. Extract it back to "/"
    # now, before anything else runs, so the rest of this function
    # (and the agent itself) sees a filesystem that actually matches
    # what bootstrap provisioned.
    # --------------------------------------------------------------

    logger.info(
      "JOB SANDBOX: restoring runtime archive (%s) to /.",
      RUNTIME_ARCHIVE_NAME,
    )

    restore_result = await sandbox.exec(
      "sh",
      "-lc",
      f"""
set -euo pipefail

RUNTIME_ARCHIVE="{RUNTIME_ARCHIVE_NAME}"

if [ ! -f "$RUNTIME_ARCHIVE" ]; then
  echo "ERROR: runtime archive not found in restored workspace: $RUNTIME_ARCHIVE"
  echo "Workspace contents:"
  find . -maxdepth 3 -print
  exit 1
fi

echo "Runtime archive found:"
ls -lh "$RUNTIME_ARCHIVE"

echo "Extracting to / ..."
sudo tar -xzf "$RUNTIME_ARCHIVE" -C /

echo "Verifying restored paths..."

if [ ! -d "$HOME/.local" ]; then
  echo "ERROR: $HOME/.local was not restored."
  exit 1
fi

if ! command -v form-convert >/dev/null 2>&1; then
  echo "ERROR: form-convert not found on PATH after restore."
  echo "PATH=$PATH"
  find "$HOME/.local" -maxdepth 2 -print
  exit 1
fi

if ! command -v soffice >/dev/null 2>&1; then
  echo "ERROR: soffice not found on PATH after restore."
  echo "PATH=$PATH"
  exit 1
fi

echo "Runtime restore verified: form-convert and soffice are on PATH."

# The archive itself isn't needed once extracted -- remove it so it
# doesn't sit alongside this job's input/output directories.
rm -f "$RUNTIME_ARCHIVE"
      """,
    )

    restore_stdout = restore_result.stdout.decode(errors="replace")
    restore_stderr = restore_result.stderr.decode(errors="replace")

    logger.info(
      "JOB SANDBOX runtime restore stdout:\n%s",
      restore_stdout,
    )

    if restore_stderr:
      logger.warning(
        "JOB SANDBOX runtime restore stderr:\n%s",
        restore_stderr,
      )

    if restore_result.exit_code != 0:
      raise RuntimeError(
        "Failed to restore runtime archive in job sandbox. "
        f"Exit code: {restore_result.exit_code}"
      )

    logger.info(
      "JOB SANDBOX: runtime restored successfully."
    )

    # --------------------------------------------------------------
    # Verify that the snapshot actually restored, using a CLEAN PATH
    # (i.e. exactly what the agent's own exec_command tool calls will
    # see, with no manual PATH export). This is the check that would
    # have caught the earlier InvalidManifestPathError bug before it
    # ever reached a real job.
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

echo "=== CLEAN-PATH TOOL RESOLUTION (matches agent exec_command) ==="
env -i PATH="/usr/bin:/bin:/usr/local/bin" sh -lc '
  command -v form-convert || echo "MISSING (clean PATH): form-convert"
  command -v form-inspect || echo "MISSING (clean PATH): form-inspect"
  command -v form-verify || echo "MISSING (clean PATH): form-verify"
  command -v form-ocr || echo "MISSING (clean PATH): form-ocr"
  command -v soffice || echo "MISSING (clean PATH): soffice"
  command -v libreoffice || echo "MISSING (clean PATH): libreoffice"
'

echo "=== PYTHON ==="
command -v python3 || true
python3 --version || true

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
      "Uploaded %d input file(s). Starting deterministic pre-conversion.",
      len(host_input_files),
    )

    # --------------------------------------------------------------
    # DETERMINISTIC FORMAT CONVERSION (pre-agent)
    #
    # Convert any legacy-format inputs (XLS, DOC, PPT, ODS) to an
    # editable modern format BEFORE the agent runs, and tell the agent
    # exactly which file to edit via a prompt addendum. This removes
    # format-conversion judgment calls from the agent's tool loop
    # entirely.
    # --------------------------------------------------------------

    conversion_map = await _preconvert_input_files(
      sandbox,
      host_input_files,
    )

    if conversion_map:
      logger.info(
        "Deterministic pre-conversion complete for %d file(s): %s",
        len(conversion_map),
        {
          name: info["original_filename"]
          for name, info in conversion_map.items()
        },
      )
    else:
      logger.info(
        "No input files required deterministic pre-conversion."
      )

    prompt = prompt + _build_conversion_prompt_addendum(conversion_map)

    logger.info(
      "Starting form agent with %d input file(s) "
      "(%d pre-converted).",
      len(host_input_files),
      len(conversion_map),
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

    # --------------------------------------------------------------
    # DETERMINISTIC FORMAT CONVERSION (post-agent)
    #
    # For any round-trip conversions (XLS, ODS), convert the agent's
    # edited output back to the original extension now, while the
    # sandbox (and LibreOffice inside it) is still available.
    # --------------------------------------------------------------

    path_replacements = await _postconvert_output_files(
      sandbox,
      conversion_map,
    )

    if path_replacements:
      logger.info(
        "Deterministic post-conversion complete: %s",
        path_replacements,
      )

    await _collect_sandbox_output_files(
      sandbox,
      output_dir,
    )

    logger.info(
      "Sandbox output files collected successfully.",
    )

    return result, path_replacements

  except Exception:
    logger.exception(
      "Form agent / sandbox processing failed.",
    )
    raise

  finally:
    if sandbox is not None:
      try:
        await sandbox.shutdown()
      except Exception:
        logger.exception(
          "Error shutting down job sandbox.",
        )

      finally:
        try:
          await sandbox_client.delete(sandbox)

          logger.info(
            "JOB SANDBOX: deleted."
          )

        except Exception:
          logger.exception(
            "Error deleting job sandbox."
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