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
from agents.sandbox.entries import LocalDir
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
      "output": LocalDir(
        src=output_dir,
      ),
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
          source_path,
          destination_path,
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
    Path("output"),
    output_dir,
  )