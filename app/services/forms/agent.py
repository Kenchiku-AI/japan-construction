import io
import logging
import inspect
import os
from importlib.metadata import distributions, version
from pathlib import Path
from typing import Any
from uuid import UUID
from vercel.sandbox import Sandbox

from agents import Runner
from agents.run import RunConfig
from agents.sandbox import (
  SandboxAgent,
  SandboxRunConfig,
)
import agents.sandbox.snapshot as snapshot_module
from agents.extensions.sandbox import (
  VercelSandboxClientOptions,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.forms.prompts import (
  FORM_AGENT_INSTRUCTIONS,
)


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
  logger.info(
    "Vercel Sandbox class: %s",
    Sandbox,
  )

  logger.info(
    "Vercel Sandbox constructor/signature: %s",
    inspect.signature(Sandbox),
  )

  logger.info(
    "Vercel Sandbox methods: %s",
    [
      name
      for name in dir(Sandbox)
      if not name.startswith("_")
    ],
  )

  logger.info(
    "vercel package version: %s",
    version("vercel"),
  )

  try:
    logger.info(
      "Vercel Sandbox.create signature: %s",
      inspect.signature(
        Sandbox.create,
      ),
    )
  except Exception:
    logger.exception(
      "Could not inspect Sandbox.create signature."
    )

  try:
    logger.info(
      "Vercel Sandbox.create annotations: %s",
      getattr(
        Sandbox.create,
        "__annotations__",
        None,
      ),
    )
  except Exception:
    logger.exception(
      "Could not inspect Sandbox.create annotations."
    )

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

  sandbox = None

  try:
    logger.info(
      "Creating sandbox for form agent.",
    )

    sandbox = await sandbox_client.create(
      options=VercelSandboxClientOptions(
        allow_s3_credential_exposure=False,
      ),
    )

    logger.info(
      "Vercel sandbox created successfully."
    )

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

    check_result = await sandbox.exec(
      "sh",
      "-lc",
      "echo '=== sandbox identity ==='; "
      "pwd; "
      "echo '=== scripts ==='; "
      "ls -la /workspace/scripts 2>&1 || true; "
      "echo '=== PATH ==='; "
      "echo \"$PATH\"; "
      "echo '=== form commands ==='; "
      "command -v form-convert 2>&1 || true; "
      "command -v form-inspect 2>&1 || true; "
      "command -v form-verify 2>&1 || true",
    )

    logger.info(
      "Sandbox environment check:\n%s",
      check_result.stdout.decode(errors="replace"),
    )

    if check_result.stderr:
      logger.info(
        "Sandbox environment check stderr:\n%s",
        check_result.stderr.decode(errors="replace"),
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
        await sandbox_client.delete(sandbox)

        logger.info(
          "Vercel sandbox deleted successfully.",
        )

      except Exception:
        logger.exception(
          "Error deleting form agent sandbox.",
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