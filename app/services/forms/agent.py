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
  import importlib.metadata

  print("=== PACKAGE VERSION DEBUG START ===")

  try:
    agents_version = importlib.metadata.version("openai-agents")
    print(
      f"OpenAI Agents SDK version: {agents_version}",
    )
  except Exception as e:
    print(
      f"Could not determine OpenAI Agents SDK version: "
      f"{type(e).__name__}: {e}",
    )

  try:
    vercel_version = importlib.metadata.version("vercel")
    print(
      f"Vercel Python package version: {vercel_version}",
    )
  except Exception as e:
    print(
      f"Could not determine Vercel Python package version: "
      f"{type(e).__name__}: {e}",
    )

  print("=== PACKAGE VERSION DEBUG END ===")

  agent = build_form_agent()

  workspace = workspace.resolve()
  input_dir = (workspace / "input").resolve()
  output_dir = output_dir.resolve()

  print("=== SANDBOX DEBUG START ===")

  print(f"Host workspace: {workspace}")
  print(f"Host input directory: {input_dir}")
  print(f"Host output directory: {output_dir}")

  print("--- HOST INPUT FILES ---")

  if input_dir.exists():
    entries = list(input_dir.iterdir())

    if not entries:
      print("  [empty]")

    for entry in entries:
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

  print("--- MANIFEST ---")
  print(f"  root: {manifest.root}")
  print(f"  entries: {manifest.entries}")

  for name, entry in manifest.entries.items():
    print(
      f"  {name}: "
      f"type={type(entry).__name__}, "
      f"value={entry}",
    )

  context = FormAgentContext(
    db=db,
    company_id=company_id,
    project_id=project_id,
  )

  print("--- CREATING SANDBOX ---")

  sandbox = await sandbox_client.create(
    manifest=manifest,
    options=VercelSandboxClientOptions(
      allow_s3_credential_exposure=False,
    ),
  )

  print("Sandbox created successfully.")
  print(f"Sandbox type: {type(sandbox)}")

  print("--- SANDBOX RELATIVE PATHS ---")

  diagnostic_paths = [
    Path("."),
    Path("input"),
    Path("output"),
    Path("workspace"),
    Path("workspace/input"),
    Path("workspace/output"),
  ]

  for path in diagnostic_paths:
    print(f"--- LS {path} ---")

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

  print("--- SANDBOX SHELL WORKING DIRECTORY ---")

  try:
    result = await sandbox.run(
      "pwd",
    )

    print(
      result.stdout
      if hasattr(result, "stdout")
      else result,
    )

  except Exception as e:
    print(
      f"  ERROR: {type(e).__name__}: {e}",
    )

  print("=== SANDBOX CREATE DEBUG END ===")

  try:
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

    try:
      print(
        f"Final output:\n{result.final_output}",
      )
    except Exception as e:
      print(
        f"Could not read final_output: "
        f"{type(e).__name__}: {e}",
      )

    print("=== AGENT RESULT DEBUG END ===")

    print("--- POST-AGENT SANDBOX PATHS ---")

    post_agent_paths = [
      Path("."),
      Path("input"),
      Path("output"),
      Path("workspace"),
      Path("workspace/input"),
      Path("workspace/output"),
    ]

    for path in post_agent_paths:
      print(f"--- POST-AGENT LS {path} ---")

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