from pathlib import Path

from agents import Runner
from agents.run import RunConfig
from agents.sandbox import (
  Manifest,
  SandboxAgent,
  SandboxPathGrant,
  SandboxRunConfig,
)
from agents.sandbox.entries import LocalDir

from app.services.forms.prompts import FORM_AGENT_INSTRUCTIONS
from app.services.forms.tools import (
  get_company_information,
  get_project_information,
  get_project_users,
  search_company_data,
  get_custom_object,
)


def build_form_agent() -> SandboxAgent:
  return SandboxAgent(
    name="Kenchiku AI Form Agent",
    instructions=FORM_AGENT_INSTRUCTIONS,
    tools=[
      get_company_information,
      get_project_information,
      get_project_users,
      search_company_data,
      get_custom_object,
    ],
  )


async def run_form_agent(
  prompt: str,
  sandbox_client,
  workspace: Path,
):
  agent = build_form_agent()

  manifest = Manifest(
    root="/workspace",
    extra_path_grants=(
      SandboxPathGrant(
        path="/tmp",
        read_only=True,
      ),
    ),
    entries={
      ".": LocalDir(
        src=workspace,
      ),
    },
  )

  result = await Runner.run(
    agent,
    prompt,
    run_config=RunConfig(
      sandbox=SandboxRunConfig(
        client=sandbox_client,
        manifest=manifest,
      ),
    ),
  )

  return result