import importlib.metadata
import io
from pathlib import Path
from typing import Any
from uuid import UUID

from agents import Runner
from agents.run import RunConfig
from agents.sandbox import (
  SandboxAgent,
  SandboxRunConfig,
)
from agents.extensions.sandbox import (
  VercelSandboxClientOptions,
)
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


def _debug_agent_result(result: Any) -> None:
  print("--- AGENT RESULT DEBUG ---")

  print(
    f"Result type: {type(result)}",
  )

  try:
    print(
      f"Final output:\n{result.final_output}",
    )
  except Exception as e:
    print(
      "Could not read final_output: "
      f"{type(e).__name__}: {e}",
    )

  try:
    new_items = result.new_items

    print(
      f"Result new_items count: "
      f"{len(new_items)}",
    )

    for index, item in enumerate(new_items):
      print(
        f"--- RESULT ITEM {index} ---",
      )
      print(
        f"Item type: {type(item)}",
      )

      try:
        print(
          f"Item repr: {repr(item)}",
        )
      except Exception as e:
        print(
          "Could not repr item: "
          f"{type(e).__name__}: {e}",
        )

  except Exception as e:
    print(
      "Could not inspect result.new_items: "
      f"{type(e).__name__}: {e}",
    )

  print("--- AGENT RESULT DEBUG END ---")


async def _debug_sandbox_files(
  sandbox: Any,
  label: str,
  max_depth: str = "5",
) -> None:
  print(
    f"--- SANDBOX FILESYSTEM DEBUG: {label} ---",
  )

  try:
    find_result = await sandbox.exec(
      "find",
      ".",
      "-maxdepth",
      max_depth,
      "-print",
    )

    print(
      f"find exit code: {find_result.exit_code}",
    )

    print(
      "find stdout:\n"
      f"{find_result.stdout.decode(errors='replace')}",
    )

    print(
      "find stderr:\n"
      f"{find_result.stderr.decode(errors='replace')}",
    )

  except Exception as e:
    print(
      "Could not inspect sandbox filesystem: "
      f"{type(e).__name__}: {e}",
    )

  print(
    f"--- SANDBOX FILESYSTEM DEBUG END: {label} ---",
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
      "Could not determine OpenAI Agents SDK version: "
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
      "Could not determine Vercel Python package version: "
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
  print(f"Host workspace exists: {workspace.exists()}")
  print(f"Host input exists: {input_dir.exists()}")
  print(f"Host output exists: {output_dir.exists()}")

  print("--- HOST INPUT FILES ---")

  host_input_files = []

  if input_dir.exists():
    entries = sorted(
      input_dir.iterdir(),
      key=lambda path: path.name,
    )

    if not entries:
      print("  [empty]")

    for entry in entries:
      if entry.is_file():
        size = entry.stat().st_size

        print(
          f"  file: {entry.name} "
          f"({size} bytes)",
        )

        host_input_files.append(entry)

      elif entry.is_dir():
        print(
          f"  directory: {entry.name}",
        )
  else:
    print(
      "  ERROR: host input directory does not exist: "
      f"{input_dir}",
    )

  if not host_input_files:
    raise ValueError(
      "No input files were found in the host input directory."
    )

  context = FormAgentContext(
    db=db,
    company_id=company_id,
    project_id=project_id,
  )

  print("--- CREATING VERCEL SANDBOX ---")

  try:
    sandbox = await sandbox_client.create(
      options=VercelSandboxClientOptions(
        allow_s3_credential_exposure=False,
      ),
    )
  except Exception as e:
    print(
      "ERROR creating sandbox: "
      f"{type(e).__name__}: {e}",
    )
    raise

  print("Sandbox created successfully.")
  print(
    f"Sandbox type: {type(sandbox)}",
  )

  try:
    print("--- SANDBOX FILESYSTEM SETUP ---")

    mkdir_result = await sandbox.exec(
      "mkdir",
      "-p",
      "input",
      "output",
    )

    print(
      f"mkdir exit code: {mkdir_result.exit_code}",
    )

    if mkdir_result.stdout:
      print(
        "mkdir stdout: "
        f"{mkdir_result.stdout.decode(errors='replace')}",
      )

    if mkdir_result.stderr:
      print(
        "mkdir stderr: "
        f"{mkdir_result.stderr.decode(errors='replace')}",
      )

    if mkdir_result.exit_code != 0:
      raise RuntimeError(
        "Failed to create input/output directories "
        "inside sandbox. "
        f"Exit code: {mkdir_result.exit_code}"
      )

    print(
      "Successfully created sandbox input/ and output/.",
    )

    print("--- UPLOADING INPUT FILES TO SANDBOX ---")

    for host_file in host_input_files:
      sandbox_path = Path("input") / host_file.name

      print(
        f"Uploading host file: {host_file}",
      )

      print(
        f"  Sandbox destination: {sandbox_path}",
      )

      file_bytes = host_file.read_bytes()

      print(
        f"  Host file size: {len(file_bytes)} bytes",
      )

      await sandbox.write(
        sandbox_path,
        io.BytesIO(file_bytes),
      )

      print(
        f"  Upload completed: {sandbox_path}",
      )

      print(
        f"  Verifying uploaded file: {sandbox_path}",
      )

      uploaded_file = await sandbox.read(
        sandbox_path,
      )

      uploaded_bytes = uploaded_file.read()

      print(
        f"  Sandbox file size: "
        f"{len(uploaded_bytes)} bytes",
      )

      if len(uploaded_bytes) != len(file_bytes):
        raise RuntimeError(
          "Sandbox upload verification failed for "
          f"{host_file.name}: "
          f"host={len(file_bytes)} bytes, "
          f"sandbox={len(uploaded_bytes)} bytes"
        )

      if uploaded_bytes != file_bytes:
        raise RuntimeError(
          "Sandbox upload verification failed for "
          f"{host_file.name}: contents differ"
        )

      print(
        f"  VERIFIED: {sandbox_path}",
      )

    print("--- SANDBOX FILESYSTEM VERIFICATION ---")

    pwd_result = await sandbox.exec(
      "pwd",
    )

    print(
      f"pwd exit code: {pwd_result.exit_code}",
    )

    print(
      "pwd stdout:\n"
      f"{pwd_result.stdout.decode(errors='replace')}",
    )

    print(
      "pwd stderr:\n"
      f"{pwd_result.stderr.decode(errors='replace')}",
    )

    print("--- SANDBOX INPUT LIST ---")

    input_ls = await sandbox.exec(
      "ls",
      "-lah",
      "input",
    )

    print(
      f"input ls exit code: {input_ls.exit_code}",
    )

    print(
      "input ls stdout:\n"
      f"{input_ls.stdout.decode(errors='replace')}",
    )

    print(
      "input ls stderr:\n"
      f"{input_ls.stderr.decode(errors='replace')}",
    )

    print("--- SANDBOX OUTPUT LIST ---")

    output_ls = await sandbox.exec(
      "ls",
      "-lah",
      "output",
    )

    print(
      f"output ls exit code: {output_ls.exit_code}",
    )

    print(
      "output ls stdout:\n"
      f"{output_ls.stdout.decode(errors='replace')}",
    )

    print(
      "output ls stderr:\n"
      f"{output_ls.stderr.decode(errors='replace')}",
    )

    await _debug_sandbox_files(
      sandbox,
      "BEFORE AGENT",
      "3",
    )

    print(
      "=== SANDBOX FILE MATERIALIZATION VERIFIED ===",
    )

    print("--- STARTING FORM AGENT ---")

    print("--- AGENT PROMPT ---")
    print(prompt)
    print("--- AGENT PROMPT END ---")

    print(
      "Starting Runner.run with max_turns=20",
    )

    result = None

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
        max_turns=30,
      )

      print(
        "Form agent runner completed successfully.",
      )

      _debug_agent_result(result)

    except Exception as e:
      print("--- AGENT RUN ERROR ---")
      print(
        f"Error type: {type(e).__name__}",
      )
      print(
        f"Error message: {e}",
      )

      print(
        "Agent exception repr: "
        f"{repr(e)}",
      )

      print("--- AGENT RUN ERROR END ---")

      print(
        "Inspecting sandbox after agent exception...",
      )

      await _debug_sandbox_files(
        sandbox,
        "AFTER AGENT EXCEPTION",
        "5",
      )

      print(
        "Inspecting sandbox output directory "
        "after agent exception...",
      )

      try:
        error_output_ls = await sandbox.exec(
          "ls",
          "-lah",
          "output",
        )

        print(
          "output ls after exception:\n"
          f"{error_output_ls.stdout.decode(errors='replace')}",
        )

        print(
          "output ls stderr after exception:\n"
          f"{error_output_ls.stderr.decode(errors='replace')}",
        )

      except Exception as output_error:
        print(
          "Could not inspect output after agent "
          "exception: "
          f"{type(output_error).__name__}: "
          f"{output_error}",
        )

      raise

    print("--- POST-AGENT FILESYSTEM ---")

    await _debug_sandbox_files(
      sandbox,
      "AFTER AGENT SUCCESS",
      "5",
    )

    print("--- POST-AGENT OUTPUT DIRECTORY ---")

    post_output_ls = await sandbox.exec(
      "ls",
      "-lah",
      "output",
    )

    print(
      f"post-agent output ls exit code: "
      f"{post_output_ls.exit_code}",
    )

    print(
      "post-agent output ls stdout:\n"
      f"{post_output_ls.stdout.decode(errors='replace')}",
    )

    print(
      "post-agent output ls stderr:\n"
      f"{post_output_ls.stderr.decode(errors='replace')}",
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

  except Exception as e:
    print(
      "--- FORM AGENT / SANDBOX ERROR ---",
    )

    print(
      f"Error type: {type(e).__name__}",
    )

    print(
      f"Error message: {e}",
    )

    print(
      f"Error repr: {repr(e)}",
    )

    print(
      "--- FORM AGENT / SANDBOX ERROR END ---",
    )

    raise

  finally:
    print("Closing sandbox...")

    try:
      await sandbox.aclose()
      print("Sandbox closed.")
    except Exception as e:
      print(
        "ERROR closing sandbox: "
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

  print("--- COLLECT SANDBOX OUTPUT DEBUG ---")

  print(
    f"Local output directory: {output_dir}",
  )

  print("--- FINDING SANDBOX OUTPUT FILES ---")

  find_result = await sandbox.exec(
    "find",
    "output",
    "-type",
    "f",
    "-print",
  )

  print(
    f"find exit code: {find_result.exit_code}",
  )

  stdout = find_result.stdout.decode(
    errors="replace",
  )

  stderr = find_result.stderr.decode(
    errors="replace",
  )

  print(
    f"find stdout:\n{stdout}",
  )

  print(
    f"find stderr:\n{stderr}",
  )

  if find_result.exit_code != 0:
    raise RuntimeError(
      "Failed to enumerate sandbox output files: "
      f"{stderr}"
    )

  sandbox_files = [
    line.strip()
    for line in stdout.splitlines()
    if line.strip()
  ]

  if not sandbox_files:
    print(
      "WARNING: Sandbox output directory contains "
      "no files.",
    )

    return

  print(
    f"Found {len(sandbox_files)} sandbox output files.",
  )

  for sandbox_file_string in sandbox_files:
    sandbox_path = Path(
      sandbox_file_string,
    )

    print(
      f"Collecting sandbox file: {sandbox_path}",
    )

    try:
      relative_path = sandbox_path.relative_to(
        Path("output"),
      )
    except ValueError:
      raise RuntimeError(
        "Sandbox returned a file outside output/: "
        f"{sandbox_path}"
      )

    destination_path = (
      output_dir / relative_path
    )

    destination_path.parent.mkdir(
      parents=True,
      exist_ok=True,
    )

    print(
      f"  Destination: {destination_path}",
    )

    file_obj = await sandbox.read(
      sandbox_path,
    )

    file_contents = file_obj.read()

    print(
      f"  Read {len(file_contents)} bytes "
      "from sandbox.",
    )

    with destination_path.open(
      "wb",
    ) as destination:
      destination.write(
        file_contents,
      )

    final_size = destination_path.stat().st_size

    print(
      f"  Wrote {destination_path} "
      f"({final_size} bytes)",
    )

  print(
    "--- COLLECT SANDBOX OUTPUT DEBUG COMPLETE ---",
  )