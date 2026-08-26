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
  input_dir = (workspace / "input").resolve()
  output_dir = output_dir.resolve()

  print("=== SANDBOX CREATE DEBUG START ===")

  print(f"Host workspace: {workspace}")
  print(f"Host input directory: {input_dir}")
  print(f"Host output directory: {output_dir}")

  print("--- HOST INPUT FILES ---")

  if input_dir.exists():
    host_input_entries = list(input_dir.iterdir())

    if not host_input_entries:
      print("  [empty]")

    for entry in host_input_entries:
      if entry.is_file():
        print(
          f"  file: {entry.name} "
          f"({entry.stat().st_size} bytes)",
        )
      elif entry.is_dir():
        print(
          f"  directory: {entry.name}",
        )
  else:
    print(
      f"  ERROR: host input directory does not exist: "
      f"{input_dir}",
    )

  manifest = Manifest(
    root="/workspace",
    entries={
      "input": LocalDir(
        src=input_dir,
      ),
      "output": Dir(),
    },
  )

  print("--- MANIFEST OBJECT DEBUG ---")

  print(
    f"manifest: {manifest}",
  )

  print(
    f"manifest.root: {manifest.root}",
  )

  print(
    f"manifest.entries: {manifest.entries}",
  )

  for name, entry in manifest.entries.items():
    print(
      f"manifest entry {name}: "
      f"type={type(entry).__name__}, "
      f"value={entry}",
    )

  print("--- CREATING SANDBOX ---")

  context = FormAgentContext(
    db=db,
    company_id=company_id,
    project_id=project_id,
  )

  sandbox = await sandbox_client.create(
    manifest=manifest,
    options=VercelSandboxClientOptions(
      allow_s3_credential_exposure=False,
    ),
  )

  print("--- SANDBOX OBJECT DEBUG ---")

  print(
    f"sandbox type: {type(sandbox)}",
  )

  print(
    f"sandbox repr: {sandbox!r}",
  )

  try:
    print(
      f"sandbox attributes: {sorted(dir(sandbox))}",
    )
  except Exception as e:
    print(
      f"Could not inspect sandbox attributes: {e}",
    )

  print("Sandbox created successfully.")

  print("=== SANDBOX FILESYSTEM DIAGNOSTIC START ===")

  diagnostic_paths = [
    Path("."),
    Path("/"),
    Path("/workspace"),
    Path("/vercel"),
    Path("/vercel/sandbox"),
    Path("/vercel/sandbox/workspace"),
    Path("/tmp"),
  ]

  for path in diagnostic_paths:
    print(f"--- SANDBOX LS: {path} ---")

    try:
      entries = await sandbox.ls(path)

      print(
        f"  SUCCESS: {len(entries)} entries",
      )

      if not entries:
        print("  [empty]")

      for entry in entries:
        print(
          f"  {entry.type}: {entry.name}",
        )

    except Exception as e:
      print(
        f"  ERROR: {type(e).__name__}: {e}",
      )

  print("=== SANDBOX FILESYSTEM DIAGNOSTIC END ===")

  print("=== SANDBOX ENVIRONMENT DIAGNOSTIC START ===")

  try:
    result = await sandbox.run(
      "pwd",
    )

    print("--- pwd ---")
    print(
      result.stdout if hasattr(result, "stdout") else result,
    )

  except Exception as e:
    print(
      f"pwd ERROR: {type(e).__name__}: {e}",
    )

  try:
    result = await sandbox.run(
      "sh",
      "-c",
      "echo SHELL_PWD=$PWD && id && whoami",
    )

    print("--- identity ---")
    print(
      result.stdout if hasattr(result, "stdout") else result,
    )

  except Exception as e:
    print(
      f"identity ERROR: {type(e).__name__}: {e}",
    )

  print("=== SANDBOX ENVIRONMENT DIAGNOSTIC END ===")

  print("--- SANDBOX INPUT DIRECTORY ---")

  try:
    sandbox_input_entries = await sandbox.ls(
      Path("input"),
    )

    if not sandbox_input_entries:
      print("  [empty]")

    for entry in sandbox_input_entries:
      print(
        f"  {entry.type}: {entry.name}",
      )

  except Exception as e:
    print(
      f"  ERROR: {e}",
    )

  print("=== SANDBOX CREATE DEBUG END ===")

  print("Starting form agent...")

  try:
    print("=== BEFORE AGENT RUN ===")
    print(f"Agent prompt:\n{prompt}")
    print("=== END AGENT PROMPT ===")

    result = await Runner.run(
      agent,
      prompt,
      context=context,
      run_config=RunConfig(
        sandbox=SandboxRunConfig(
          session=sandbox,
        ),
      ),
    )

    print("=== AGENT RESULT DEBUG START ===")

    print(
      f"Result type: {type(result)}",
    )

    print(
      f"Result repr: {result!r}",
    )

    try:
      print(
        f"Final output:\n{result.final_output}",
      )
    except Exception as e:
      print(
        f"Could not read final_output: {type(e).__name__}: {e}",
      )

    print("=== AGENT RESULT DEBUG END ===")

    print("Form agent completed successfully.")

    print("=== POST-AGENT FILESYSTEM SNAPSHOT START ===")

    post_agent_paths = [
      Path("."),
      Path("/"),
      Path("/workspace"),
      Path("/workspace/input"),
      Path("/workspace/output"),
      Path("/vercel"),
      Path("/vercel/sandbox"),
      Path("/vercel/sandbox/input"),
      Path("/vercel/sandbox/output"),
    ]

    for path in post_agent_paths:
      print(f"--- POST-AGENT LS: {path} ---")

      try:
        entries = await sandbox.ls(path)

        print(
          f"  SUCCESS: {len(entries)} entries",
        )

        if not entries:
          print("  [empty]")

        for entry in entries:
          print(
            f"  {entry.type}: {entry.name}",
          )

      except Exception as e:
        print(
          f"  ERROR: {type(e).__name__}: {e}",
        )

    print("=== POST-AGENT FILESYSTEM SNAPSHOT END ===")

    await _collect_sandbox_output_files(
      sandbox,
      output_dir,
    )

    print("Sandbox output files collected successfully.")

    return result

  finally:
    print("Closing sandbox...")
    await sandbox.aclose()
    print("Sandbox closed.")
    print("=== FORM SANDBOX DEBUG END ===")


async def _collect_sandbox_output_files(
  sandbox,
  output_dir: Path,
) -> None:
  print("=== SANDBOX OUTPUT DEBUG START ===")

  print("--- Sandbox workspace root ---")

  try:
    entries = await sandbox.ls(
      Path("."),
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

  print("--- Sandbox manifest paths ---")

  for path in [
    Path("input"),
    Path("output"),
  ]:
    print(f"--- {path} ---")

    try:
      entries = await sandbox.ls(
        path,
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

  print("=== SANDBOX OUTPUT DEBUG END ===")

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

    entries = await sandbox.ls(
      sandbox_path,
    )

    for entry in entries:
      source_path = sandbox_path / entry.name
      destination_path = local_path / entry.name

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
        print(
          f"Collecting file: {source_path}",
        )

        file_obj = await sandbox.read(
          source_path,
        )

        with destination_path.open(
          "wb",
        ) as destination:
          destination.write(
            file_obj.read(),
          )

  try:
    await collect_directory(
      Path("output"),
      output_dir,
    )

  except Exception as e:
    print(
      f"ERROR collecting sandbox output: {e}",
    )
    raise

  print("Sandbox output files collected successfully.")