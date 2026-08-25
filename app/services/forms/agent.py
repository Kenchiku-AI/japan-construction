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

  manifest = Manifest(
    root="/workspace",
    entries={
      "input": LocalDir(
        src=workspace / "input",
      ),
      "output": Dir(),
    },
  )

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

  try:
    print("=== SANDBOX DEBUG START ===")

    print("Manifest root:")
    print(manifest.root)

    print("Manifest entries:")
    print(manifest.entries)

    print("Local workspace:")
    print(workspace)

    print("Local input directory:")
    print(workspace / "input")

    print("Local input exists:")
    print((workspace / "input").exists())

    print("Local input files:")
    for path in (workspace / "input").rglob("*"):
      print(f"  {path} | file={path.is_file()}")

    print("Local output directory:")
    print(workspace / "output")

    print("Local output exists:")
    print((workspace / "output").exists())

    print("=== SANDBOX ROOT ===")
    try:
      print(await sandbox.ls(Path("/")))
    except Exception as exc:
      print(f"ERROR listing /: {type(exc).__name__}: {exc}")

    print("=== SANDBOX /workspace ===")
    try:
      print(await sandbox.ls(Path("/workspace")))
    except Exception as exc:
      print(
        f"ERROR listing /workspace: "
        f"{type(exc).__name__}: {exc}"
      )

    print("=== SANDBOX /workspace/input ===")
    try:
      print(
        await sandbox.ls(
          Path("/workspace/input"),
        )
      )
    except Exception as exc:
      print(
        f"ERROR listing /workspace/input: "
        f"{type(exc).__name__}: {exc}"
      )

    print("=== SANDBOX /workspace/output ===")
    try:
      print(
        await sandbox.ls(
          Path("/workspace/output"),
        )
      )
    except Exception as exc:
      print(
        f"ERROR listing /workspace/output: "
        f"{type(exc).__name__}: {exc}"
      )

    print("=== SANDBOX RELATIVE . ===")
    try:
      print(await sandbox.ls(Path(".")))
    except Exception as exc:
      print(
        f"ERROR listing .: "
        f"{type(exc).__name__}: {exc}"
      )

    print("=== SANDBOX RELATIVE input ===")
    try:
      print(await sandbox.ls(Path("input")))
    except Exception as exc:
      print(
        f"ERROR listing input: "
        f"{type(exc).__name__}: {exc}"
      )

    print("=== SANDBOX RELATIVE output ===")
    try:
      print(await sandbox.ls(Path("output")))
    except Exception as exc:
      print(
        f"ERROR listing output: "
        f"{type(exc).__name__}: {exc}"
      )

    print("=== SANDBOX DEBUG END ===")

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

    await _collect_sandbox_output_files(
      sandbox,
      output_dir,
    )

    return result

  finally:
    await sandbox.aclose()


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
          Path("/workspace/output"),
          output_dir,
        )

      elif entry.type == "file":
        file_obj = await sandbox.read(
          source_path,
        )

        with destination_path.open(
          "wb",
        ) as destination:
          destination.write(
            file_obj.read(),
          )

  await collect_directory(
    Path("/workspace/output"),
    output_dir,
  )