import inspect

from agents.extensions.sandbox import VercelSandboxClient
from agents.sandbox import Manifest
from agents.sandbox.entries import Dir, LocalDir


def get_form_sandbox_client() -> VercelSandboxClient:

  print("VERCEL SANDBOX")

  print("CLIENT CREATE:")
  print(inspect.getsource(VercelSandboxClient.create))

  print("MANIFEST:")
  print(inspect.getsource(Manifest))

  print("LOCAL DIR:")
  print(inspect.getsource(LocalDir))

  print("DIR:")
  print(inspect.getsource(Dir))

  return VercelSandboxClient()