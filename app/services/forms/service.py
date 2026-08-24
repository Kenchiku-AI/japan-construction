import json
import tempfile
from pathlib import Path

from sqlalchemy import select

from app.db.models.form_job import (
  FormJob,
  FormJobFile,
  FormJobStatus,
)
from app.services.forms.agent import run_form_agent
from app.services.forms.sandbox import get_form_sandbox_client
from app.services.forms.storage import FormStorage


class FormJobService:
  def __init__(
    self,
    db,
    storage: FormStorage,
  ):
    self.db = db
    self.storage = storage

  async def process(
    self,
    form_job_id,
  ) -> None:

    job = await self.db.scalar(
      select(FormJob)
      .where(FormJob.id == form_job_id)
    )

    if job is None:
      raise ValueError(
        f"Form job {form_job_id} not found"
      )

    if job.status != FormJobStatus.pending:
      logger.info(
        "Skipping form job %s with status %s",
        form_job_id,
        job.status,
      )
      return

    job.status = FormJobStatus.processing

    await self.db.commit()

    try:
      with tempfile.TemporaryDirectory() as temp_dir:

        workspace = Path(temp_dir)

        input_dir = workspace / "input"
        output_dir = workspace / "output"

        input_dir.mkdir()
        output_dir.mkdir()

        input_files = await self._download_input_files(
          job,
          input_dir,
        )

        prompt = self._build_prompt(
          job,
          input_files,
        )

        sandbox_client = get_form_sandbox_client()

        result = await run_form_agent(
          prompt=prompt,
          sandbox_client=sandbox_client,
          workspace=workspace,
        )

        await self._collect_output_files(
          job,
          output_dir,
        )

        job.result_json = json.dumps(
          {
            "output": result.final_output,
          },
          ensure_ascii=False,
        )

        job.status = FormJobStatus.completed

        await self.db.commit()

    except Exception as exc:
      job.status = FormJobStatus.failed
      job.error = str(exc)

      await self.db.commit()

      raise

  async def _collect_output_files(
    self,
    job: FormJob,
    output_dir: Path,
  ) -> list[FormJobFile]:
    output_files = []

    for local_path in output_dir.rglob("*"):
      if not local_path.is_file():
        continue

      relative_path = local_path.relative_to(
        output_dir,
      )

      s3_key = (
        f"form-jobs/"
        f"{job.id}/"
        f"output/"
        f"{relative_path}"
      )

      content_type = None

      if relative_path.suffix.lower() == ".pdf":
        content_type = "application/pdf"

      self.storage.upload_file(
        local_path=local_path,
        s3_key=s3_key,
        content_type=content_type,
      )

      job_file = FormJobFile(
        form_job_id=job.id,
        filename=relative_path.name,
        content_type=content_type,
        s3_key=s3_key,
        is_input=False,
      )

      self.db.add(
        job_file,
      )

      output_files.append(
        job_file,
      )

    await self.db.flush()

    return output_files

  async def _download_input_files(
    self,
    job: FormJob,
    input_dir: Path,
  ) -> list[Path]:

    files = []

    for file in job.files:
      if not file.is_input:
        continue

      local_path = input_dir / file.filename

      self.storage.download_file(
        file.s3_key,
        local_path,
      )

      files.append(local_path)

    return files

  def _build_prompt(
    self,
    job: FormJob,
    input_files: list[Path],
  ) -> str:

    file_list = "\n".join(
      f"- /workspace/input/{path.name}"
      for path in input_files
    )

    return f"""
Complete the form files provided in the workspace.

Company ID:
{job.company_id}

Project ID:
{job.project_id or "Not specified"}

User instructions:
{job.instructions or "No additional instructions."}

Input files:
{file_list}

Read the input files from:

/workspace/input/

Save all completed files to:

/workspace/output/

Use the Kenchiku tools to retrieve information as necessary.

Do not invent missing information.
"""