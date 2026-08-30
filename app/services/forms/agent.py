import asyncio
import io
import logging
import inspect
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

  # snapshot_exists = await asyncio.to_thread(
  #   snapshot_client.exists,
  #   FORM_AGENT_SNAPSHOT_ID,
  # )

  snapshot_exists = False

  sandbox = None

  try:
    if not snapshot_exists:
      logger.warning(
        "Snapshot %r not found in S3. Bootstrapping it now from this "
        "run's sandbox -- this run will take longer than usual.",
        FORM_AGENT_SNAPSHOT_ID,
      )

      sandbox = await sandbox_client.create(
        snapshot=get_form_agent_snapshot(),
        options=VercelSandboxClientOptions(
          allow_s3_credential_exposure=False,
          timeout_ms=600_000,
        ),
      )

      await provision_dependencies(sandbox)

      try:
        snapshot_id = await sandbox.snapshot()

        logger.info(
          "Snapshot persisted successfully. Snapshot ID: %r",
          snapshot_id,
        )

        logger.info(
          "Snapshot persisted. Store this ID: %r",
          FORM_AGENT_SNAPSHOT_ID,
        )
      except AttributeError:
        logger.exception(
          "sandbox.snapshot() does not exist on this SDK version -- "
          "dependencies were installed but NOT persisted. This run's "
          "sandbox will still be used below, but every future run will "
          "re-bootstrap until this is fixed."
        )
    else:
      logger.info(
        "Creating sandbox for form agent from snapshot %r.",
        FORM_AGENT_SNAPSHOT_ID,
      )

      sandbox = await sandbox_client.create(
        snapshot=get_form_agent_snapshot(),
        options=VercelSandboxClientOptions(
          allow_s3_credential_exposure=False,
          timeout_ms=300_000,
        ),
      )

      logger.info("Vercel sandbox created successfully from snapshot.")

    check_result = await sandbox.exec(
      "sh", "-lc",
      """
set -x
ls -la /usr/local/bin
echo "PATH=$PATH"
command -v form-convert
command -v form-inspect
command -v form-verify
      """,
    )

    stdout = check_result.stdout.decode(errors="replace")
    stderr = check_result.stderr.decode(errors="replace")

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
      raise RuntimeError(
        "Sandbox dependencies missing after creation/provisioning."
      )

    mkdir_result = await sandbox.exec("mkdir", "-p", "input", "output")

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