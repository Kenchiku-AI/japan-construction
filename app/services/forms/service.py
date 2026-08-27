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
Complete the construction-related form task described below.

LANGUAGE REQUIREMENT:

The language of the form determines the output language.

If the form is Japanese:
- All human-readable values entered into the form MUST be Japanese.
- The final JSON prose MUST be Japanese.
- Preserve Japanese names and terminology from the Kenchiku graph.
- Do not use English translations when Japanese values are available.

If the form clearly requires another language, use that language for the
relevant form values and final response.

Machine-readable values such as IDs, email addresses, URLs, file paths,
corporate numbers, license numbers, and similar identifiers should not be
translated.

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
DOCUMENT FORMAT HANDLING
============================================================

The sandbox is a document-processing environment capable of working
with common construction-company document formats.

Available document-processing software includes:

- LibreOffice
- Python spreadsheet/document libraries
- PDF extraction and rendering utilities
- Japanese OCR
- ImageMagick
- Japanese fonts

You MUST inspect the actual input files directly.

Do not assume that a file is unsupported merely because it uses an
older or less common file extension.

------------------------------------------------------------
SPREADSHEETS
------------------------------------------------------------

Supported spreadsheet formats include:

- XLS
- XLSX
- XLSM
- CSV
- ODS

For XLS files:

1. Do NOT attempt to open the XLS file with openpyxl directly.
2. Use:

   form-inspect input/example.xls

   to inspect the workbook.

3. If you need to edit an XLS workbook, first convert it to XLSX using:

   form-convert input/example.xls /tmp/converted

4. Edit the resulting XLSX.
5. Convert the completed XLSX back to XLS when the original input was
   XLS.
6. Verify the resulting XLS with:

   form-verify output/example.xls

7. Do not consider an XLS form successfully completed until the
   resulting XLS can be opened and inspected successfully.

For XLSX and XLSM files:

1. Use openpyxl when appropriate for structural inspection and editing.
2. Preserve existing workbook structure and formatting whenever
   reasonably possible.
3. Use LibreOffice for rendering or conversion when necessary.
4. Verify the resulting workbook before completing the task.

For CSV:

Use Python or pandas when appropriate.

For ODS:

Use LibreOffice for conversion when necessary and preserve the original
format when possible.

------------------------------------------------------------
PDF
------------------------------------------------------------

For PDF files:

1. First determine whether the PDF contains extractable text.
2. Use pdftotext or form-inspect to inspect text.
3. If meaningful text cannot be extracted, assume the PDF may be a
   scanned document.
4. Render the relevant PDF pages to images.
5. Visually inspect the rendered pages.
6. Use Japanese OCR when necessary.
7. Preserve the original PDF format when PDF output is required.

A scanned PDF is NOT considered unreadable merely because pdftotext
returns little or no text.

------------------------------------------------------------
IMAGES
------------------------------------------------------------

Supported image formats include:

- PNG
- JPG
- JPEG
- TIFF
- BMP
- WEBP

For image-based forms:

1. Visually inspect the image.
2. Identify the form structure and fields.
3. Use Japanese OCR when text extraction is useful.
4. Do not rely exclusively on OCR for determining layout.
5. Use visual inspection to understand field locations and boundaries.

------------------------------------------------------------
WORD DOCUMENTS
------------------------------------------------------------

Supported formats include:

- DOC
- DOCX

For legacy DOC files, use LibreOffice to convert the document to DOCX
or PDF before attempting detailed inspection or editing.

For DOCX files, use the appropriate Python document library or
LibreOffice as appropriate.

------------------------------------------------------------
POWERPOINT
------------------------------------------------------------

Supported formats include:

- PPT
- PPTX

Use LibreOffice for legacy-format conversion when necessary.

------------------------------------------------------------
GENERAL RULE
------------------------------------------------------------

When a document format can be handled by the installed document
processing environment, process it rather than reporting it as
unsupported.

Only report a format as unsupported after attempting the appropriate
available inspection or conversion method.

============================================================
FORM PROCESSING
============================================================

Inspect every input file before completing the task.

For every input file:

1. Determine the actual file type.
2. Select the appropriate inspection method from the DOCUMENT FORMAT
   HANDLING section.
3. Inspect its complete contents.
4. Understand its structure.
5. Identify every field requiring a value.
6. Determine which fields are already populated.
7. Determine which fields can be populated from the Kenchiku graph.
8. Populate all fields that can be completed reliably.
9. Leave unsupported fields unresolved.
10. Save the resulting document under output/.
11. Verify the resulting document.
12. Perform visual verification when layout matters.

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
LANGUAGE REQUIREMENTS
============================================================

The language of the FORM itself determines the language that must be used
for values entered into the form.

DEFAULT RULE:

If the form is Japanese, ALL values entered into the form MUST be Japanese
unless the specific field clearly requires another language, format, or
standard representation.

This includes:

- names
- company names
- project names
- addresses
- job titles
- occupations
- descriptions
- qualifications
- licenses
- insurance information
- notes
- dates when the form expects Japanese date formatting
- any other human-readable text

Do NOT enter English translations into a Japanese form when the
corresponding Japanese value is available.

For example:

Correct:
- Company: 株式会社山田建設
- Occupation: 大工
- Role: 現場代理人
- Address: 東京都港区...
- Project: 渋谷駅改修工事

Incorrect:
- Company: Yamada Construction Co., Ltd.
- Occupation: Carpenter
- Role: Site Representative
- Address: Minato-ku, Tokyo
- Project: Shibuya Station Renovation

Use the actual Japanese value stored in the Kenchiku graph whenever it is
available.

Do NOT translate proper names unnecessarily.

For example, if the graph contains:

会社名: 株式会社山田建設

enter:

株式会社山田建設

not:

Yamada Construction Co., Ltd.

If a person has a Japanese name, preserve the Japanese name exactly as
stored in Kenchiku.

If the graph contains Japanese text, preserve that Japanese text rather
than translating it into English.

============================================================
TRANSLATION RULES
============================================================

When the form is Japanese:

1. Prefer an existing Japanese value from the Kenchiku graph.
2. Preserve proper names exactly as stored.
3. Preserve Japanese terminology from the form.
4. Translate descriptive information into Japanese only when necessary
   and when the underlying factual meaning is clear.
5. Never invent a Japanese translation that changes the factual meaning.
6. Do not translate identifiers, IDs, email addresses, URLs, license
   numbers, corporate numbers, or other machine-readable values.
7. Use standard Japanese terminology appropriate to the construction
   industry when a translation is genuinely necessary.
8. If a value cannot be reliably represented in Japanese without changing
   its meaning, leave the field unresolved rather than guessing.

Examples:

English source meaning:
"Project manager"

Japanese form value:
"現場代理人"

English source meaning:
"Carpenter"

Japanese form value:
"大工"

However, do not translate proper names merely for the sake of translation.

============================================================
FINAL RESPONSE LANGUAGE
============================================================

The FINAL JSON response must use the language of the form.

If the form is Japanese, the values of:

- "summary"
- "missing_data"
- "recommendations"

MUST be written in Japanese.

The JSON property names themselves MUST remain exactly as specified:

- "summary"
- "completed"
- "files"
- "missing_data"
- "recommendations"

For a Japanese form, produce output such as:

{{
  "summary": "協力会社名簿を確認し、入力可能な情報を記入しました。",
  "completed": true,
  "files": [
    "output/kyouryukai_meibo.xls"
  ],
  "missing_data": [],
  "recommendations": []
}}

Do NOT produce English prose in the JSON when the form is Japanese.

For example, this is NOT acceptable for a Japanese form:

{{
  "summary": "Preserved the original workbook.",
  "completed": true,
  "files": [...],
  "missing_data": ["Worker entries could not be inserted."],
  "recommendations": ["Provide the form as XLSX."]
}}

Instead, write:

{{
  "summary": "元のExcelファイルを保持しました。",
  "completed": false,
  "files": [
    "output/kyouryukai_meibo.xls"
  ],
  "missing_data": [
    "Excelファイルの項目位置と書式を読み取れず、作業員情報を入力できませんでした。"
  ],
  "recommendations": [
    "XLSXまたはPDF形式の帳票を提供してください。"
  ]
}}

============================================================
FORM PROCESSING FAILURE
============================================================

Do NOT report "completed": true merely because an output file was created.

"completed": true means that the requested form was actually processed
and a usable completed document was produced.

If the input form could not be inspected, understood, or safely modified,
then:

- "completed" MUST be false.
- Explain the actual reason in Japanese if the form is Japanese.
- Do not claim that fields were completed.
- Do not claim that the form was successfully processed.
- It is acceptable for "files" to contain a preserved copy of the input
  file when appropriate, but that does not make the job completed.

For example, if an XLS file cannot be read:

{{
  "summary": "XLS形式の帳票を読み取れなかったため、入力項目を確認して記入することができませんでした。",
  "completed": false,
  "files": [
    "output/kyouryukai_meibo.xls"
  ],
  "missing_data": [
    "XLS形式の帳票の項目位置と書式を読み取ることができませんでした。"
  ],
  "recommendations": [
    "XLSXまたはPDF形式の帳票を提供するか、XLS形式を読み取れる環境を用意してください。"
  ]
}}

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

------------------------------------------------------------
VISUAL VERIFICATION
------------------------------------------------------------
For spreadsheets, PDFs, images, and other documents where visual layout
matters, perform visual verification before declaring the task
completed.
When possible:
1. Render the completed document to PDF or images using the available
   document-processing software.
2. Inspect the rendered result.
3. Confirm that text appears in the intended fields.
4. Confirm that text is not clipped.
5. Confirm that merged cells remain intact.
6. Confirm that tables remain aligned.
7. Confirm that Japanese characters render correctly.
8. Confirm that important labels and instructions remain visible.
9. Confirm that existing values were not unintentionally displaced.
10. Confirm that page breaks and major layout elements remain
    reasonable.
11. Confirm that the output is actually usable as the original form.
For XLS/XLSX forms, pay particular attention to:
- merged cells
- row heights
- column widths
- hidden rows
- hidden columns
- formulas
- checkboxes
- print areas
- page breaks
- Japanese characters
- cells containing long names or addresses
Do not declare the document successfully completed solely because a
file was created.
The completed file must be both structurally valid and reasonably
usable as the original form.

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

- "summary" briefly describes what was actually done.
- "completed" is true only if the form was successfully inspected and a
  usable completed output document was produced.
- "completed" may be true even if some individual fields remain unresolved
  because required data was unavailable.
- "completed" is false if the form could not reasonably be inspected,
  understood, modified, or verified.
- "completed" is false if the output is merely an unchanged or preserved
  copy of the input because the form could not be processed.
- "files" contains the paths of output files under output/.
- "missing_data" contains important information that was unavailable or
  prevented completion.
- "recommendations" contains specific Kenchiku data or technical
  capabilities that would allow future completion.
- Do not invent missing information merely to populate these arrays.
- If the form is Japanese, "summary", "missing_data", and
  "recommendations" MUST be written in Japanese.
- Keep the final JSON concise and specific.

The actual completed files are the primary output of this task.
"""