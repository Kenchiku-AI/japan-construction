import inspect
from agents.extensions.sandbox import VercelSandboxClient

def get_form_sandbox_client() -> VercelSandboxClient:
  print("VERCEL SANDBOX")
  print(inspect.signature(VercelSandboxClient.create))
  print(inspect.signature(VercelSandboxClient))
  return VercelSandboxClient()