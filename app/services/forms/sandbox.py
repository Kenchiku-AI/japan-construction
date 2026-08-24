from agents.extensions.sandbox import VercelSandboxClient


def get_form_sandbox_client() -> VercelSandboxClient:
  return VercelSandboxClient()