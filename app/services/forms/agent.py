from pathlib import Path
from typing import Any
from uuid import UUID

from agents import Runner
from agents.run import RunConfig
from agents.sandbox import (
  Manifest,
  SandboxAgent,
  SandboxRunConfig,
)
from agents.sandbox.entries import LocalDir, Dir
from agents.extensions.sandbox import VercelSandboxClientOptions
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.forms.prompts import FORM_AGENT_INSTRUCTIONS
from app.services.forms.tools import (
  FormAgentContext,
  get_company_information,
  get_custom_object,
  get_form_data,
  get_project_information,
  get_project_users,
)


def build_form_agent() -> SandboxAgent:
  return SandboxAgent(
    name="Kenchiku AI Form Agent",
    instructions=FORM_AGENT_INSTRUCTIONS,
    tools=[
      get_company_information,
      get_project_information,
      get_project_users,
      get_custom_object,
      get_form_data,
    ],
  )


async def run_form_agent(
  form_job: FormJob,
  input_dir: Path,
  output_dir: Path,
) -> dict:
  print("=== FORM SANDBOX DEBUG START ===")

  workspace = input_dir.parent

  print(
    f"Host workspace: {workspace}",
  )
  print(
    f"Host input directory: {input_dir}",
  )
  print(
    f"Host output directory: {output_dir}",
  )

  print("--- HOST WORKSPACE FILES ---")

  for path in workspace.rglob("*"):
    if path.is_file():
      try:
        size = path.stat().st_size
      except Exception:
        size = "unknown"

      print(
        f"  file: {path.relative_to(workspace)} "
        f"({size} bytes)",
      )

  print("--- HOST INPUT FILES ---")

  input_files = [
    path
    for path in input_dir.rglob("*")
    if path.is_file()
  ]

  if not input_files:
    print("  [empty]")

  for path in input_files:
    print(
      f"  file: {path.relative_to(input_dir)} "
      f"({path.stat().st_size} bytes)",
    )

  print("--- SANDBOX MANIFEST ---")
  print("  root: /workspace")
  print("  input: LocalDir")
  print(
    f"  input source: {input_dir}",
  )
  print("  output: Dir")

  sandbox = None

  try:
    print("--- CREATING SANDBOX ---")

    sandbox = await sandbox_client.create(
      config=SandboxConfig(
        root=Path("/workspace"),
        input=LocalDir(
          source=input_dir,
        ),
        output=Dir(),
      ),
    )

    print(
      "Sandbox created successfully.",
    )

    print("--- SANDBOX WORKSPACE ROOT ---")

    try:
      entries = await sandbox.ls(
        Path("/workspace"),
      )

      if not entries:
        print("  [empty]")

      for entry in entries:
        print(
          f"  {entry.type}: {entry.name}",
        )

    except Exception as e:
      print(
        f"  ERROR: {e}",
      )

    print("--- SANDBOX INPUT DIRECTORY ---")

    sandbox_input_path = Path(
      "/workspace/input",
    )

    try:
      entries = await sandbox.ls(
        sandbox_input_path,
      )

      if not entries:
        print("  [empty]")

      for entry in entries:
        print(
          f"  {entry.type}: {entry.name}",
        )

    except Exception as e:
      print(
        f"  ERROR: {e}",
      )

    print("--- SANDBOX OUTPUT DIRECTORY BEFORE AGENT ---")

    sandbox_output_path = Path(
      "/workspace/output",
    )

    try:
      entries = await sandbox.ls(
        sandbox_output_path,
      )

      if not entries:
        print("  [empty]")

      for entry in entries:
        print(
          f"  {entry.type}: {entry.name}",
        )

    except Exception as e:
      print(
        f"  ERROR: {e}",
      )

    print(
      "=== SANDBOX CREATE DEBUG END ===",
    )

    print("Starting form agent...")

    result = await Runner.run(
      form_agent,
      input=(
        "Process the provided form file. "
        "The input files are available in the "
        "/workspace/input directory. "
        "Place all completed/generated files in "
        "the /workspace/output directory."
      ),
      context={
        "form_job": form_job,
      },
      sandbox=sandbox,
    )

    print(
      "Form agent completed successfully.",
    )

    print("=== SANDBOX POST-AGENT DEBUG START ===")

    print("--- SANDBOX WORKSPACE ROOT ---")

    try:
      entries = await sandbox.ls(
        Path("/workspace"),
      )

      if not entries:
        print("  [empty]")

      for entry in entries:
        print(
          f"  {entry.type}: {entry.name}",
        )

    except Exception as e:
      print(
        f"  ERROR: {e}",
      )

    print("--- SANDBOX INPUT DIRECTORY ---")

    try:
      entries = await sandbox.ls(
        Path("/workspace/input"),
      )

      if not entries:
        print("  [empty]")

      for entry in entries:
        print(
          f"  {entry.type}: {entry.name}",
        )

    except Exception as e:
      print(
        f"  ERROR: {e}",
      )

    print("--- SANDBOX OUTPUT DIRECTORY ---")

    try:
      entries = await sandbox.ls(
        Path("/workspace/output"),
      )

      if not entries:
        print("  [empty]")

      for entry in entries:
        print(
          f"  {entry.type}: {entry.name}",
        )

    except Exception as e:
      print(
        f"  ERROR: {e}",
      )

    print(
      "=== SANDBOX POST-AGENT DEBUG END ===",
    )

    await _collect_sandbox_output_files(
      sandbox,
      output_dir,
    )

    return {
      "result": result,
    }

  finally:
    if sandbox is not None:
      print("Closing sandbox...")

      try:
        await sandbox.close()
        print("Sandbox closed.")
      except Exception as e:
        print(
          f"Error closing sandbox: {e}",
        )

    print(
      "=== FORM SANDBOX DEBUG END ===",
    )


async def _collect_sandbox_output_files(
  sandbox,
  output_dir: Path,
) -> None:
  print("=== SANDBOX OUTPUT COLLECTION START ===")

  output_dir.mkdir(
    parents=True,
    exist_ok=True,
  )

  async def collect_directory(
    sandbox_path: Path,
    local_path: Path,
  ) -> None:
    print(
      f"Collecting sandbox directory: {sandbox_path}",
    )

    try:
      entries = await sandbox.ls(
        sandbox_path,
      )
    except Exception as e:
      print(
        f"ERROR listing sandbox directory "
        f"{sandbox_path}: {e}",
      )
      return

    if not entries:
      print(
        f"  [empty]: {sandbox_path}",
      )
      return

    for entry in entries:
      source_path = sandbox_path / entry.name
      destination_path = local_path / entry.name

      print(
        f"  {entry.type}: {source_path}",
      )

      if entry.type == "directory":
        destination_path.mkdir(
          parents=True,
          exist_ok=True,
        )

        await collect_directory(
          source_path,
          destination_path,
        )

      elif entry.type == "file":
        try:
          file_obj = await sandbox.read(
            source_path,
          )

          with destination_path.open(
            "wb",
          ) as destination:
            destination.write(
              file_obj.read(),
            )

          print(
            f"    -> copied to {destination_path}",
          )

        except Exception as e:
          print(
            f"    ERROR reading {source_path}: {e}",
          )

  # The sandbox manifest says the sandbox root is /workspace.
  # Do NOT use Path("output"), because the SDK resolves that
  # relative path to /vercel/sandbox/output.
  sandbox_output_path = Path(
    "/workspace/output",
  )

  try:
    entries = await sandbox.ls(
      sandbox_output_path,
    )

    if not entries:
      print(
        "Sandbox output directory exists but is empty.",
      )
    else:
      print(
        f"Sandbox output directory contains "
        f"{len(entries)} entries.",
      )

      await collect_directory(
        sandbox_output_path,
        output_dir,
      )

  except Exception as e:
    print(
      f"Sandbox output directory does not exist "
      f"or could not be accessed: {e}",
    )

  print("=== SANDBOX OUTPUT COLLECTION END ===")