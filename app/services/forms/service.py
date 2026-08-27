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
from app.services.forms.company_graph import build_company_graph
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

        prompt = await self._build_prompt(
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

        job.result_json = {
          "output": agent_output,
        }

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

  async def _build_prompt(
    self,
    job: FormJob,
    input_files: list[Path],
  ) -> str:

    file_list = "\n".join(
      f"- input/{path.name}"
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

The project with this ID is the primary project for this form job.

The Kenchiku graph identifies it with [PRIMARY PROJECT].

Start entity selection from this project and follow its explicit
relationships when determining which users, companies, and custom
objects are relevant.
"""

    return f"""
Complete the Japanese construction-related form task described below.

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

The following input files are available inside the sandbox:

{file_list}

You MUST inspect the input files directly.

============================================================
KENCHIKU DATA GRAPH
============================================================

The complete Kenchiku company data graph is included below.

This graph was generated directly from the Kenchiku database before
this agent run.

The graph is authoritative.

DO NOT attempt to retrieve Kenchiku company, project, user, custom
object, custom field, or relationship data through tools.

There are no Kenchiku data tools available to this agent.

Use the graph below as the source of truth.

{company_graph}

{project_context}

============================================================
HOW TO USE THE GRAPH
============================================================

The graph contains:

1. Company information.
2. Project information.
3. User information.
4. Custom object definitions.
5. Custom object instances.
6. Custom field definitions.
7. Custom field values.
8. Custom relationship definitions.
9. Custom relationship instances.

Each entity has a stable short graph ID:

C-XXXXXXXX = company
P-XXXXXXXX = project
U-XXXXXXXX = user
O-XXXXXXXX = custom object

These IDs allow you to connect information across the graph.

For example:

P-12345678 --[現場作業員]--> U-87654321

means that the project P-12345678 is explicitly related to user
U-87654321 through the relationship 現場作業員.

The relationship definition explains the semantic meaning of that
relationship.

============================================================
ENTITY SELECTION
============================================================

When deciding which Kenchiku entity should provide a form value:

1. Start with the primary project when one is provided.
2. Examine relationships directly connected to that project.
3. Determine the semantic meaning of each relationship from the
   relationship definition.
4. Follow relevant relationships to users, companies, and custom
   objects.
5. Read the custom fields on those entities.
6. Use company information when company-level information is required.
7. Use the entity's own fields and relationships to determine whether
   it actually matches the form field.

Do NOT assume that every entity belonging to the company is relevant
to the current project.

============================================================
RELATIONSHIP RULES
============================================================

Relationships are explicit application data.

Relationship direction matters.

An outgoing relationship means:

A --[RELATIONSHIP]--> B

A is the source and B is the target.

An incoming relationship means:

A <--[RELATIONSHIP]-- B

B is the source and A is the target.

The graph may show the same relationship under both connected entities
so that it can be discovered from either side.

Always use the actual source and target shown in the relationship edge
index when interpreting direction.

Do not infer relationships from:

- shared company
- similar names
- matching email addresses
- similar custom field values
- proximity in the graph
- the fact that two users belong to the same company
- the fact that two entities appear in the same section

Only explicit relationships establish an association.

============================================================
CUSTOM FIELD RULES
============================================================

Custom field definitions explain what a custom field means.

A custom field value belongs only to the entity where the value is
shown.

For example:

FIELD:
フリガナ

Meaning:
氏名のフリガナ

USER:
U-12345678

Custom fields:
フリガナ: ヤマダ タロウ

means that ヤマダ タロウ is the furigana value for that specific user.

Do not transfer a custom field value from one entity to another.

============================================================
DATA ACCURACY
============================================================

Never fabricate information.

Never guess missing factual values.

If a required value cannot be determined reliably from the graph or
the input document, leave the form field unresolved.

Do not invent:

- names
- addresses
- phone numbers
- dates
- qualifications
- license numbers
- insurance information
- project information
- company information
- employment information
- registration numbers
- identification numbers
- any other factual information

If multiple entities could satisfy a field, use:

1. primary project context
2. explicit relationship semantics
3. entity type
4. custom field meaning
5. form context

to determine the correct entity.

If the correct entity still cannot be determined reliably, leave the
field unresolved.

============================================================
FORM PROCESSING
============================================================

Inspect every input file before completing the task.

For every input file:

1. Determine the file type.
2. Inspect its complete contents.
3. Understand its structure.
4. Identify every field requiring a value.
5. Determine which fields are already populated.
6. Determine which fields can be populated from the Kenchiku graph.
7. Populate all fields that can be completed reliably.
8. Leave unsupported fields unresolved.
9. Verify the completed document.

Do not repeatedly inspect the same document once you have enough
information to understand its structure.

============================================================
EXISTING VALUES
============================================================

Preserve existing values.

Do not overwrite an existing value unless the task specifically
requires a different value.

Do not delete:

- labels
- headers
- instructions
- tables
- footers
- static explanatory text

Preserve the original form structure and formatting as much as
reasonably possible.

============================================================
JAPANESE FORM HANDLING
============================================================

Complete Japanese construction forms in Japanese unless the form clearly
requires another language.

Preserve the terminology used by the form.

Pay attention to the actual meaning of each Japanese field.

For example, distinguish carefully between:

- 会社名
- 事業者名
- 元請会社
- 下請会社
- 協力会社
- 所属会社
- 現場名
- 工事名称
- 工事場所
- 工事期間
- 作業内容
- 職種
- 作業員氏名
- 現場代理人
- 主任技術者
- 監理技術者
- 安全衛生責任者
- 資格
- 免許
- 技能講習
- 特別教育
- 雇用保険
- 健康保険
- 厚生年金
- 労災保険

Do not populate a field merely because its label resembles a field in
Kenchiku.

Understand the actual meaning of the form field first.

============================================================
MISSING DATA
============================================================

Missing data is NOT a job failure.

If information is unavailable:

1. Complete everything that can be completed reliably.
2. Leave unsupported fields unresolved.
3. Save the resulting document under output/.
4. Report the missing information in the final JSON.
5. Recommend specific Kenchiku data that would allow future completion.

Do not use placeholders such as:

N/A
不明
未定
なし

unless the form specifically calls for such a value.

============================================================
OUTPUT
============================================================

All completed documents MUST be written under:

output/

Never write completed documents elsewhere.

Never modify files under input/.

Preserve the original file format whenever possible.

============================================================
VERIFICATION
============================================================

Before finishing, verify every output document individually.

Verify:

1. The file exists under output/.
2. The file can be opened/read.
3. The expected file was created.
4. Intended fields were populated.
5. Values are in the correct locations.
6. Japanese text is appropriate.
7. Existing labels and instructions remain intact.
8. Existing values that should remain were preserved.
9. No unsupported information was invented.
10. The input file was not modified.
11. Formatting and structure were preserved as much as reasonably possible.

============================================================
FINAL RESPONSE
============================================================

Your final response MUST be valid JSON.

Do not wrap the JSON in Markdown.

Use exactly this structure:

{{
  "summary": "Brief description of what you did.",
  "completed": true,
  "files": [
    "output/example.xlsx"
  ],
  "missing_data": [],
  "recommendations": []
}}

Rules:

- "summary" briefly describes what was done.
- "completed" is true if the requested output documents were produced,
  even if some fields remain unresolved because data was missing.
- "completed" is false only if the form could not reasonably be processed
  or no usable output could be produced.
- "files" contains the paths of completed files under output/.
- "missing_data" contains important unavailable information.
- "recommendations" contains specific Kenchiku data that should be added
  to improve future completion.
- Use an empty array when there is no missing information or no
  recommendations.
- Do not invent missing information merely to populate these arrays.
- Keep the final JSON concise and specific.

The actual completed files are the primary output of this task.
"""