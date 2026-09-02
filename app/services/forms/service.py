import json
import logging
import mimetypes
import tempfile
from pathlib import Path

from openai import OpenAI
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.db.models.form_job import (
  FormJob,
  FormJobFile,
  FormJobStatus,
)
from app.services.forms.company_graph import build_company_graph
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

    self.openai = OpenAI(
      api_key=settings.OPENAI_API_KEY,
    )


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
        .where(
          FormJob.id == form_job_id,
        )
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
        .where(
          FormJob.id == form_job_id,
        )
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

        if not input_files:
          raise ValueError(
            "The form job does not contain any input files."
          )

        prompt = await self._build_prompt(
          job,
          input_files,
        )

        logger.info(
          "Starting OpenAI form processing for job %s",
          form_job_id,
        )

        agent_output = await self._run_openai_form_agent(
          prompt=prompt,
          input_files=input_files,
          output_dir=output_dir,
        )

        # The job may have been deleted while OpenAI was processing
        # the document.
        job_exists = await self.db.scalar(
          select(FormJob.id)
          .where(
            FormJob.id == form_job_id,
          )
        )

        if job_exists is None:
          logger.info(
            "Form job %s was deleted while OpenAI was processing. "
            "Discarding output.",
            form_job_id,
          )

          return

        output_files = await self._collect_output_files(
          job,
          output_dir,
        )

        if not output_files:
          raise ValueError(
            "OpenAI did not produce any output files."
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

        job.result_json = {
          "output": agent_output,
        }

        logger.info(
          "OpenAI form output for job %s: %s",
          form_job_id,
          json.dumps(
            agent_output,
            ensure_ascii=False,
          ),
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
        .where(
          FormJob.id == form_job_id,
        )
      )

      if job is not None:
        job.status = FormJobStatus.failed
        job.error = str(exc)

        await self.db.commit()

      raise


  async def _run_openai_form_agent(
    self,
    prompt: str,
    input_files: list[Path],
    output_dir: Path,
  ) -> dict:

    uploaded_files = []

    try:
      for input_file in input_files:

        logger.info(
          "Uploading form file to OpenAI: %s",
          input_file.name,
        )

        with input_file.open(
          "rb",
        ) as file_handle:

          uploaded = self.openai.files.create(
            file=file_handle,
            purpose="user_data",
          )

        uploaded_files.append(
          uploaded,
        )

      content = [
        {
          "type": "input_text",
          "text": prompt,
        },
      ]

      for uploaded in uploaded_files:
        content.append(
          {
            "type": "input_file",
            "file_id": uploaded.id,
          }
        )

      response = self.openai.responses.create(
        model="gpt-5.6",
        reasoning={
          "effort": "high",
        },
        include=[
          "code_interpreter_call.outputs",
        ],
        tools=[
          {
            "type": "code_interpreter",
            "container": {
              "type": "auto",
              "file_ids": [
                uploaded.id
                for uploaded in uploaded_files
              ],
            },
          },
        ],
        input=[
          {
            "role": "user",
            "content": content,
          },
        ],
      )

      logger.info(
        "OpenAI response status: %s",
        response.status,
      )

      for index, item in enumerate(
        response.output,
      ):
        logger.info(
          "OpenAI response output[%s]: type=%s",
          index,
          getattr(
            item,
            "type",
            None,
          ),
        )

        if getattr(
          item,
          "type",
          None,
        ) == "code_interpreter_call":
          logger.info(
            "Code Interpreter container_id=%s outputs=%s",
            getattr(
              item,
              "container_id",
              None,
            ),
            getattr(
              item,
              "outputs",
              None,
            ),
          )

      logger.info(
        "OpenAI form processing completed",
      )

      raw_output = response.output_text

      agent_output = self._parse_agent_output(
        raw_output,
      )

      self._extract_output_files_from_response(
        response=response,
        agent_output=agent_output,
        output_dir=output_dir,
      )

      return agent_output

    finally:
      for uploaded in uploaded_files:
        try:
          self.openai.files.delete(
            uploaded.id,
          )
        except Exception:
          logger.exception(
            "Failed to delete OpenAI file %s",
            uploaded.id,
          )


  def _extract_output_files_from_response(
    self,
    response,
    agent_output: dict,
    output_dir: Path,
  ) -> None:

    output_dir.mkdir(
      parents=True,
      exist_ok=True,
    )

    logger.info(
      "========== CODE INTERPRETER OUTPUT DEBUG =========="
    )

    logger.info(
      "Agent reported files: %s",
      agent_output.get("files"),
    )

    for item in response.output:

      logger.info(
        "Response output item: type=%s",
        getattr(
          item,
          "type",
          None,
        ),
      )

      logger.info(
        "Response output item repr: %r",
        item,
      )

      if getattr(
        item,
        "type",
        None,
      ) == "code_interpreter_call":

        container_id = getattr(
          item,
          "container_id",
          None,
        )

        logger.info(
          "Code Interpreter container ID: %s",
          container_id,
        )

        if not container_id:
          continue

        response_files = (
          self.openai.containers.files.list(
            container_id,
          )
        )

        logger.info(
          "Container file list response: %r",
          response_files,
        )

        for container_file in response_files.data:

          logger.info(
            "CONTAINER FILE: id=%s filename=%s repr=%r",
            getattr(
              container_file,
              "id",
              None,
            ),
            getattr(
              container_file,
              "filename",
              None,
            ),
            container_file,
          )

    logger.info(
      "========== END CODE INTERPRETER OUTPUT DEBUG =========="
    )

    return


  def _parse_agent_output(
    self,
    raw_output: str,
  ) -> dict:

    if not raw_output:
      return {
        "summary": "フォーム処理が完了しました。",
        "completed": True,
        "files": [],
        "missing_data": [],
        "recommendations": [],
      }

    cleaned = raw_output.strip()

    if cleaned.startswith(
      "```json"
    ):
      cleaned = cleaned[
        len("```json"):
      ].strip()

    if cleaned.endswith(
      "```"
    ):
      cleaned = cleaned[
        :-len("```")
      ].strip()

    try:
      parsed = json.loads(
        cleaned,
      )

      if not isinstance(
        parsed,
        dict,
      ):
        raise ValueError(
          "OpenAI output was not a JSON object."
        )

      return parsed

    except (
      json.JSONDecodeError,
      ValueError,
    ):

      logger.warning(
        "OpenAI returned non-JSON form output.",
      )

      return {
        "summary": raw_output,
        "completed": True,
        "files": [],
        "missing_data": [],
        "recommendations": [],
      }


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

      logger.info(
        "Uploaded form output: %s",
        s3_key,
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

      local_path = (
        input_dir / file.filename
      )

      local_path.parent.mkdir(
        parents=True,
        exist_ok=True,
      )

      self.storage.download_file(
        file.s3_key,
        local_path,
      )

      files.append(
        local_path,
      )

      logger.info(
        "Downloaded form input: %s",
        file.s3_key,
      )

    return files


  async def _build_prompt(
    self,
    job: FormJob,
    input_files: list[Path],
  ) -> str:

    file_list = "\n".join(
      f"- {path.name}"
      for path in input_files
    )

    company_graph = await build_company_graph(
      db=self.db,
      company_id=job.company_id,
      project_id=job.project_id,
    )

    project_context = ""

    if job.project_id is not None:
      project_context = f"""
============================================================
PRIMARY PROJECT
============================================================

Primary project database ID:

{job.project_id}

This is the primary project for the form.

Start entity selection from this project and follow its explicit
relationships when determining which users, companies, and custom
objects are relevant.
"""

    return f"""
You are the document-processing agent for Kenchiku AI.

Your task is to complete a real construction-company form using the
provided Kenchiku data.

You have been given one or more uploaded documents.

You MUST inspect the actual uploaded documents.

You MUST modify the documents themselves.

You MUST create the completed documents as output files.

============================================================
FORM JOB
============================================================

Form name:
{job.name}

Form description:
{job.description or "No description provided."}

============================================================
INPUT FILES
============================================================

{file_list}

============================================================
KENCHIKU DATA GRAPH
============================================================

The following Kenchiku data is authoritative.

Do not invent information.

Do not retrieve additional Kenchiku information.

{company_graph}

{project_context}

============================================================
ENTITY SELECTION
============================================================

When determining which Kenchiku entity should provide a value:

1. Start with the primary project when one exists.
2. Follow explicit relationships from that project.
3. Use the semantic meaning of each relationship.
4. Follow relevant relationships to users, companies, and custom objects.
5. Use custom fields belonging to the relevant entity.
6. Use company information for company-level fields.

Do not assume that every company user or object is relevant to the
project.

Relationships are explicit.

Do not infer relationships from:

- shared company
- similar names
- email addresses
- similar custom field values
- proximity in the graph
- appearing in the same graph section

Only explicit relationships establish an association.

============================================================
DATA ACCURACY
============================================================

Never fabricate factual information.

Never guess:

- names
- addresses
- phone numbers
- dates
- qualifications
- licenses
- insurance information
- project information
- company information
- employment information
- registration numbers
- identification numbers

If a required value cannot be determined reliably, leave it unresolved
and report it in missing_data.

Missing data is not itself a processing failure.

Complete everything that can be completed reliably.

============================================================
LANGUAGE
============================================================

The form's language determines the language of entered values.

If the form is Japanese:

- Use Japanese human-readable values.
- Preserve Japanese names exactly as stored.
- Preserve Japanese company names exactly as stored.
- Preserve Japanese project names exactly as stored.
- Use Japanese construction terminology.
- Do not unnecessarily translate Japanese names into English.
- Final JSON prose must also be Japanese.

Do not translate:

- IDs
- email addresses
- URLs
- license numbers
- corporate numbers
- other machine-readable identifiers

============================================================
DOCUMENT PROCESSING
============================================================

Use the Code Interpreter environment to inspect and edit the uploaded
documents.

You may use Python and appropriate document libraries.

For Excel:

- inspect workbook structure
- inspect sheets
- inspect cells
- inspect merged cells
- preserve formatting
- preserve formulas where appropriate
- preserve print settings where possible
- fill the correct cells
- save the completed workbook

For Word:

- inspect paragraphs
- inspect tables
- identify form fields
- preserve formatting
- fill the correct locations
- save the completed document

For PDF:

- determine whether it contains editable form fields
- inspect text and page structure
- inspect scanned pages visually
- use OCR when necessary
- fill AcroForm fields when available
- otherwise overlay text/checkmarks at the appropriate locations
- preserve the PDF

For images:

- visually inspect the form
- identify field locations
- use OCR when useful
- overlay values at the correct locations
- preserve the original image dimensions and layout

For legacy XLS/DOC/PPT formats:

- use an available conversion mechanism when necessary
- preserve the original format if reasonably possible
- if the original format cannot safely be preserved, produce the most
  appropriate usable format and clearly report that fact

============================================================
IMPORTANT EDITING RULE
============================================================

Do NOT merely describe how the form should be filled.

Actually edit the uploaded document.

Do NOT return a hypothetical table of values instead of a completed
document.

The completed document is the primary output of this task.

============================================================
EXISTING VALUES
============================================================

Preserve existing values.

Do not overwrite an existing value unless the form clearly requires
replacement.

Do not delete:

- labels
- headers
- instructions
- tables
- footers
- static explanatory text

Preserve the original structure and formatting.

============================================================
OUTPUT
============================================================

Every completed document MUST be written into the output directory.

Do not write completed documents elsewhere.

Do not modify the original input files.

Use clear filenames.

For example:

output/completed_form.xlsx

or:

output/施工体制台帳_completed.xlsx

============================================================
VERIFICATION
============================================================

After editing every document:

1. Confirm that the output file exists.
2. Confirm that it can be opened/read.
3. Confirm that intended fields were populated.
4. Confirm that values are in the correct locations.
5. Confirm that Japanese text renders correctly.
6. Confirm that existing labels remain intact.
7. Confirm that existing values remain intact.
8. Confirm that no information was invented.
9. Confirm that the input file was not modified.

For documents where visual layout matters, visually inspect the
completed document.

Pay particular attention to:

- merged cells
- row heights
- column widths
- tables
- checkboxes
- text clipping
- Japanese characters
- page breaks
- PDF layout
- image dimensions

Do not declare success simply because a file was created.

============================================================
FINAL RESPONSE
============================================================

After the actual files have been created, return ONLY valid JSON.

Use exactly:

{{
  "summary": "Brief description of what was actually completed.",
  "completed": true,
  "files": [
    "output/example.xlsx"
  ],
  "missing_data": [],
  "recommendations": []
}}

If the form is Japanese, summary, missing_data, and recommendations must
be Japanese.

"completed" is true only when a usable completed document was actually
created.

If the document could not be safely processed:

{{
  "summary": "フォームを安全に処理できませんでした。",
  "completed": false,
  "files": [],
  "missing_data": [
    "具体的な理由"
  ],
  "recommendations": [
    "必要な対応"
  ]
}}

Do not claim success merely because an output file exists.
"""