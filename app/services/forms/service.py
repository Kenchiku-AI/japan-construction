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

        # The job may have been deleted while the agent was running.
        # Re-check the database before collecting/uploading any output.
        job_exists = await self.db.scalar(
          select(FormJob.id)
          .where(
            FormJob.id == form_job_id,
          )
        )

        if job_exists is None:
          logger.info(
            "Form job %s was deleted while the agent was running. "
            "Discarding agent output.",
            form_job_id,
          )

          return

        output_files = await self._collect_output_files(
          job,
          output_dir,
        )

        if not output_files:
          raise ValueError(
            "The form agent did not produce any output files."
          )

        # The agent is instructed to return a JSON object containing
        # summary, completed, missing_data, recommendations, etc.
        #
        # Store that object directly while preserving the existing
        # "output" key for compatibility.
        try:
          agent_output = json.loads(
            result.final_output,
          )

          if not isinstance(agent_output, dict):
            raise ValueError(
              "Agent final output is not a JSON object."
            )

        except (json.JSONDecodeError, ValueError):
          logger.warning(
            "Form agent returned non-JSON output for job %s",
            form_job_id,
          )

          agent_output = {
            "summary": result.final_output,
            "completed": True,
            "missing_data": [],
            "recommendations": [],
          }

        job.result_json = json.dumps(
          {
            "output": agent_output,
          },
          ensure_ascii=False,
        )

        logger.info(
          "Form agent final output for job %s: %s",
          form_job_id,
          json.dumps(
            agent_output,
            ensure_ascii=False,
          ),
        )

        job_exists = await self.db.scalar(
          select(FormJob.id)
          .where(
            FormJob.id == form_job_id,
          )
        )

        if job_exists is None:
          logger.info(
            "Form job %s was deleted before completion. "
            "Discarding result.",
            form_job_id,
          )

          return

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
      f"- input/{path.name}"
      for path in input_files
    )

    return f"""
Complete the Japanese construction-related form task described below.

FORM JOB

Form name:
{job.name}

Form description:
{job.description or "No description provided."}

INPUT FILES

The following input files are available:

{file_list}

TASK

Inspect all of the input files and complete the forms according to the
form job description and the Kenchiku data available through your tools.

This may be a Japanese construction-industry form such as a 協力会社名簿,
作業員名簿, 労務安全書類, グリーンファイル, construction company
roster, worker roster, qualification list, safety document, subcontractor
document, or another construction-related administrative form.

Do not assume the exact type of form. Determine its actual purpose by
inspecting the document.

IMPORTANT:

- Inspect every input file.
- Do not modify any file under input/.
- Use Kenchiku tools when information is needed.
- Prefer authoritative Kenchiku data.
- Do not invent information.
- Do not guess missing values.
- Preserve existing values unless they need to be changed according to
  the task.
- Preserve the original Japanese labels and instructions.
- Preserve the original layout and formatting as much as reasonably
  possible.
- Complete every field that can be populated reliably.
- Leave fields unresolved when the required information is unavailable or
  ambiguous.
- Do not add explanatory English text to the form.

MISSING DATA IS NOT A JOB FAILURE.

If the available Kenchiku data is insufficient to fully complete the form,
do NOT fail the job.

Instead:

1. Complete every field that can be completed reliably.
2. Leave unsupported fields unresolved.
3. Save the resulting document under output/.
4. In your final JSON result, clearly identify the missing information.
5. In your final JSON result, recommend the specific Kenchiku data that
   should be added to make future completion possible.

Do not fabricate people, companies, dates, addresses, qualifications,
licenses, insurance information, project information, or any other
factual information.

FINAL RESPONSE

Your final response MUST be valid JSON with this structure:

{{
  "summary": "Brief description of what you did.",
  "completed": true,
  "files": [
    "output/example.xlsx"
  ],
  "missing_data": [],
  "recommendations": []
}}

The JSON must accurately describe the work you actually performed.

All completed files MUST be saved under:

output/

Do not save the completed documents anywhere else.
"""