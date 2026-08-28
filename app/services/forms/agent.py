import inspect
import io
import logging
from pathlib import Path
from typing import Any
from uuid import UUID

from agents import Runner
from agents.run import RunConfig
from agents.sandbox import (
  SandboxAgent,
  SandboxRunConfig,
)
from agents.sandbox.session import SandboxSession
from sqlalchemy.ext.asyncio import AsyncSession
from vercel import sandbox as sb

from app.core.config import settings
from app.services.forms.prompts import FORM_AGENT_INSTRUCTIONS


logger = logging.getLogger(__name__)


def build_form_agent() -> SandboxAgent:
  return SandboxAgent(
    name="Kenchiku AI Form Agent",
    instructions=FORM_AGENT_INSTRUCTIONS,
  )


async def run_form_agent(
  prompt: str,
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

  sandbox = None
  sandbox_session = None

  try:
    logger.info(
      "Creating Vercel sandbox for form agent."
    )

    logger.info(
      "Creating Vercel sandbox with image=%r project_id=%r team_id=%r",
      settings.VERCEL_SANDBOX_IMAGE,
      settings.VERCEL_PROJECT_ID,
      settings.VERCEL_TEAM_ID,
    )

    logger.info(
      "create_sandbox signature: %s",
      inspect.signature(sb.create_sandbox),
    )

    sandbox = await sb.create_sandbox(
      image=settings.VERCEL_SANDBOX_IMAGE,
      project_id=settings.VERCEL_PROJECT_ID,
    )

    logger.info(
      "Vercel sandbox created successfully. type=%s",
      type(sandbox),
    )

    logger.info(
      "Vercel sandbox object repr: %r",
      sandbox,
    )

    logger.info(
      "Vercel sandbox public attributes: %s",
      [
        attr
        for attr in dir(sandbox)
        if not attr.startswith("_")
      ],
    )

    sandbox_session = VercelSandboxSessionAdapter(
      sandbox,
    )

    logger.info(
      "Vercel sandbox wrapped in OpenAI SandboxSession adapter."
    )

    mkdir_result = await sandbox_session.exec(
      "mkdir",
      "-p",
      "input",
      "output",
    )

    if mkdir_result.returncode != 0:
      stderr_raw = getattr(mkdir_result, "stderr", b"")
      stderr = (
        stderr_raw.decode(errors="replace") 
        if isinstance(stderr_raw, bytes) 
        else str(stderr_raw)
      )
      raise RuntimeError(
        "Failed to create input/output directories inside sandbox. "
        f"Exit status: {mkdir_result.returncode}. Error: {stderr}"
      )

    for host_file in host_input_files:
      sandbox_path = (
        Path("input")
        / host_file.name
      )

      file_bytes = host_file.read_bytes()

      await sandbox_session.write_file(
        str(sandbox_path),
        file_bytes,
      )

      uploaded_file = await sandbox_session.read_file(
        str(sandbox_path),
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

    check_result = await sandbox_session.exec(
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
      check_result.stdout.decode(
        errors="replace",
      ),
    )

    if check_result.stderr:
      logger.info(
        "Sandbox environment check stderr:\n%s",
        check_result.stderr.decode(
          errors="replace",
        ),
      )

    result = await Runner.run(
      agent,
      prompt,
      run_config=RunConfig(
        sandbox=SandboxRunConfig(
          session=sandbox_session,
        ),
      ),
      max_turns=50,
    )

    logger.info(
      "Form agent execution completed successfully."
    )

    await _collect_sandbox_output_files(
      sandbox_session,
      output_dir,
    )

    logger.info(
      "Sandbox output files collected successfully."
    )

    return result

  except Exception:
    logger.exception(
      "Form agent / sandbox processing failed."
    )
    raise

  finally:
    if sandbox_session is not None:
      try:
        await sandbox_session.close()

        logger.info(
          "Vercel sandbox closed successfully."
        )

      except Exception:
        logger.exception(
          "Error closing form agent sandbox."
        )


async def _collect_sandbox_output_files(
  sandbox_session: SandboxSession,
  output_dir: Path,
) -> None:
  output_dir.mkdir(
    parents=True,
    exist_ok=True,
  )

  find_result = await sandbox_session.exec(
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
      "Sandbox output directory contains no files."
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

    file_obj = await sandbox_session.read_file(
      str(sandbox_path),
    )

    file_contents = file_obj.read()

    with destination_path.open(
      "wb",
    ) as destination:
      destination.write(
        file_contents,
      )


class VercelSandboxSessionAdapter(
  SandboxSession,
):
  def __init__(
    self,
    sandbox: sb.Sandbox,
  ):
    self.sandbox = sandbox

  async def exec(
    self,
    command: str,
    *args: str,
  ):
    result = await self.sandbox.run_process(
      command,
      list(args),
    )

    return result

  async def write_file(
    self,
    path: str,
    content: str | bytes,
  ):
    if isinstance(content, str):
      content = content.encode()

    await self.sandbox.fs.write(path, content)

  async def read_file(self, path: str):
    class FileReaderAdapter:
      def __init__(self, data: bytes):
        self.data = data
      def read(self) -> bytes:
        return self.data

    file_bytes = await self.sandbox.fs.read(path)
    return FileReaderAdapter(file_bytes)

  async def close(self):
    await self.sandbox.stop()