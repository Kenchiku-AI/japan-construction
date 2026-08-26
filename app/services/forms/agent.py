from pathlib import Path
from typing import Any
from uuid import UUID
import importlib.metadata

from agents import Runner
from agents.run import RunConfig
from agents.sandbox import (
  Manifest,
  SandboxAgent,
  SandboxPathGrant,
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
  print("=== FORM SANDBOX DEBUG START ===")

  try:
    agents_version = importlib.metadata.version(
      "openai-agents",
    )
    print(
      f"OpenAI Agents SDK version: {agents_version}",
    )
  except Exception as e:
    print(
      f"Could not determine OpenAI Agents SDK version: "
      f"{type(e).__name__}: {e}",
    )

  try:
    vercel_version = importlib.metadata.version(
      "vercel",
    )
    print(
      f"Vercel Python package version: {vercel_version}",
    )
  except Exception as e:
    print(
      f"Could not determine Vercel Python package version: "
      f"{type(e).__name__}: {e}",
    )

  agent = build_form_agent()

  workspace = workspace.resolve()
  input_dir = (workspace / "input").resolve()
  output_dir = output_dir.resolve()

  print("--- HOST PATHS ---")
  print(f"Host workspace: {workspace}")
  print(f"Host input directory: {input_dir}")
  print(f"Host output directory: {output_dir}")
  print(
    f"Host workspace exists: {workspace.exists()}",
  )
  print(
    f"Host input exists: {input_dir.exists()}",
  )
  print(
    f"Host output exists: {output_dir.exists()}",
  )

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
    extra_path_grants=(
      SandboxPathGrant(
        path=str(input_dir),
        read_only=True,
      ),
    ),
    entries={
      "input": LocalDir(
        src=input_dir,
      ),
      "output": Dir(),
    },
  )

  print("--- MANIFEST ---")
  print(
    f"  root: {manifest.root}",
  )
  print(
    f"  entries: {manifest.entries}",
  )
  print(
    f"  extra_path_grants: {manifest.extra_path_grants}",
  )

  for grant in manifest.extra_path_grants:
    print(
      f"  path grant: "
      f"path={grant.path}, "
      f"read_only={grant.read_only}, "
      f"host_path={grant.host_path}",
    )

  for name, entry in manifest.entries.items():
    print(
      f"  entry {name}: "
      f"type={type(entry).__name__}, "
      f"value={entry}",
    )

  context = FormAgentContext(
    db=db,
    company_id=company_id,
    project_id=project_id,
  )

  print("--- CREATING SANDBOX ---")

  try:
    sandbox = await sandbox_client.create(
      manifest=manifest,
      options=VercelSandboxClientOptions(
        allow_s3_credential_exposure=False,
      ),
    )
  except Exception as e:
    print(
      f"ERROR creating sandbox: "
      f"{type(e).__name__}: {e}",
    )
    raise

  print("Sandbox created successfully.")
  print(
    f"Sandbox type: {type(sandbox)}",
  )

  print("--- SANDBOX RELATIVE PATHS AFTER CREATE ---")

  diagnostic_paths = [
    Path("."),
    Path("input"),
    Path("output"),
    Path("workspace"),
    Path("workspace/input"),
    Path("workspace/output"),
  ]

  for path in diagnostic_paths:
    print(
      f"--- LS {path} ---",
    )

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

  print("=== SANDBOX CREATE DEBUG END ===")

  try:
    print("--- STARTING FORM AGENT ---")

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

    print("Form agent runner completed.")

    print("--- AGENT RESULT ---")
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
      print(
        f"--- POST-AGENT LS {path} ---",
      )

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

    print("--- COLLECTING OUTPUT FILES ---")

    await _collect_sandbox_output_files(
      sandbox,
      output_dir,
    )

    print(
      "Sandbox output files collected successfully.",
    )

    return result

  finally:
    print("Closing sandbox...")

    try:
      await sandbox.aclose()
      print("Sandbox closed.")
    except Exception as e:
      print(
        f"ERROR closing sandbox: "
        f"{type(e).__name__}: {e}",
      )

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

    print(
      f"Found {len(entries)} entries in "
      f"{sandbox_path}",
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

        file_contents = file_obj.read()

        print(
          f"  file size: {len(file_contents)} bytes",
        )

        with destination_path.open(
          "wb",
        ) as destination:
          destination.write(
            file_contents,
          )

  try:
    await collect_directory(
      Path("output"),
      output_dir,
    )

  except Exception as e:
    print(
      f"ERROR collecting sandbox output: "
      f"{type(e).__name__}: {e}",
    )
    raise

  print(
    "Sandbox output files collected successfully.",
  )