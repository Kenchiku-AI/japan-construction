import json
import tempfile
import mimetypes
import logging
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.db.models.form_job import (
  FormJob,
  FormJobFile,
  FormJobStatus,
)
from app.services.forms.agent import run_form_agent
from app.services.forms.sandbox import get_form_sandbox_client
from app.services.forms.storage import FormStorage

logger = logging.getLogger(__name__)

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

    result = await self.db.execute(
      update(FormJob)
      .where(
        FormJob.id == form_job_id,
        FormJob.status == FormJobStatus.pending,
      )
      .values(
        status=FormJobStatus.processing,
      )
    )

    if result.rowcount != 1:
      job = await self.db.scalar(
        select(FormJob)
        .where(FormJob.id == form_job_id)
      )

      if job is None:
        raise ValueError(
          f"Form job {form_job_id} not found"
        )

      logger.info(
        "Skipping form job %s with status %s",
        form_job_id,
        job.status,
      )

      await self.db.rollback()

      return

    await self.db.commit()

    try:
      job = await self.db.scalar(
        select(FormJob)
        .options(
          selectinload(FormJob.files),
        )
        .where(FormJob.id == form_job_id)
      )

      if job is None:
        raise ValueError(
          f"Form job {form_job_id} not found"
        )

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
          output_dir=output_dir,
          db=self.db,
          company_id=job.company_id,
          project_id=job.project_id,
        )

        output_files = await self._collect_output_files(
          job,
          output_dir,
        )

        if not output_files:
          raise ValueError(
            "The form agent did not produce any output files."
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
      await self.db.rollback()

      job = await self.db.scalar(
        select(FormJob)
        .options(
          selectinload(FormJob.files),
        )
        .where(FormJob.id == form_job_id)
      )

      if job is not None:
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

      content_type, _ = mimetypes.guess_type(
        local_path.name,
      )

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

Form name:
{job.name}

Form description:
{job.description or "No description provided."}

Input files:
{file_list}

Use the Kenchiku tools to retrieve information as necessary.

Save all completed documents to:

/workspace/output/

Do not invent missing information.
"""