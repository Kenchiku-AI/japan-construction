from agents import Runner
from agents.run import RunConfig
from agents.sandbox import SandboxRunConfig, SandboxAgent

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
):
  agent = build_form_agent()

  result = await Runner.run(
    agent,
    prompt,
    run_config=RunConfig(
      sandbox=SandboxRunConfig(
        client=sandbox_client,
      ),
    ),
  )

  return result