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

        image_input_files = [
          input_file
          for input_file in input_files
          if input_file.suffix.lower()
          in {
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".gif",
          }
        ]

        if image_input_files:
          image_edits = agent_output.get(
            "image_edits",
            [],
          )

          logger.info(
            "Image form returned %d edit(s)",
            len(image_edits),
          )

          for input_file in image_input_files:
            self._apply_image_edits(
              input_file=input_file,
              image_edits=image_edits,
              output_dir=output_dir,
            )

        pdf_input_files = [
          input_file
          for input_file in input_files
          if input_file.suffix.lower()
          == ".pdf"
        ]

        if pdf_input_files:
          pdf_edits = agent_output.get(
            "pdf_edits",
            [],
          )

          logger.info(
            "PDF form returned %d edit(s)",
            len(pdf_edits),
          )

          for input_file in pdf_input_files:
            self._apply_pdf_edits(
              input_file=input_file,
              pdf_edits=pdf_edits,
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
          output_files = list(
            output_dir.iterdir(),
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
      has_image = any(
        input_file.suffix.lower()
        in {
          ".jpg",
          ".jpeg",
          ".png",
          ".webp",
          ".gif",
        }
        for input_file in input_files
      )

      has_pdf = any(
        input_file.suffix.lower()
        == ".pdf"
        for input_file in input_files
      )

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
          (
            input_file,
            uploaded,
          ),
        )

      content = [
        {
          "type": "input_text",
          "text": prompt,
        },
      ]

      code_interpreter_file_ids = []

      for input_file, uploaded in uploaded_files:

        suffix = input_file.suffix.lower()

        if suffix in {
          ".jpg",
          ".jpeg",
          ".png",
          ".webp",
          ".gif",
        }:

          logger.info(
            "Adding image to OpenAI vision input: %s",
            input_file.name,
          )

          content.append(
            {
              "type": "input_image",
              "file_id": uploaded.id,
            }
          )

        else:

          logger.info(
            "Adding document to OpenAI input: %s",
            input_file.name,
          )

          content.append(
            {
              "type": "input_file",
              "file_id": uploaded.id,
            }
          )

          if suffix not in {
            ".pdf",
          }:
            code_interpreter_file_ids.append(
              uploaded.id,
            )

      if has_image:

        image_instruction = """
IMPORTANT: One or more uploaded files are images.

For image forms, DO NOT attempt to create, modify, or save an output
image using Code Interpreter.

Instead, inspect the original image visually and determine exactly
where each requested value should be placed.

Return an "image_edits" array in your JSON response.

Each image edit must contain:

- "filename": the original image filename
- "text": the exact text to place
- "x": left coordinate in pixels
- "y": top coordinate in pixels
- "width": width of the field in pixels
- "height": height of the field in pixels

Coordinates must refer to the ORIGINAL uploaded image's pixel dimensions.

The x/y coordinates identify the upper-left corner of the field
where the text should be placed.

Do not resize the coordinate system.

Use the actual visible blank field on the form, not an approximate
location elsewhere on the page.

The text must be placed INSIDE the corresponding blank field.

Do not fabricate fields or coordinates.

Only create an image_edit when there is enough visual evidence to
identify the correct field.

If requested information cannot be located confidently, put that
information in "missing_data" instead.

For multiple images, keep edits associated with the correct
filename.

The image itself will be edited later by the application using
Pillow. Your job is to identify the correct placement and return
precise coordinates.

Your response MUST be valid JSON with this structure:

{
  "summary": "...",
  "completed": true,
  "files": [],
  "image_edits": [
    {
      "filename": "original.jpg",
      "text": "Joe Smith",
      "x": 100,
      "y": 200,
      "width": 250,
      "height": 40
    }
  ],
  "missing_data": [],
  "recommendations": []
}

For an image job, "files" MUST remain an empty array because the
application, rather than Code Interpreter, creates the completed
image.
"""

        content.append(
          {
            "type": "input_text",
            "text": image_instruction,
          }
        )

      if has_pdf:

        pdf_instruction = """
IMPORTANT: One or more uploaded files are PDF forms.

The application, NOT Code Interpreter, will create the completed
PDF.

Your job is to inspect each PDF carefully and determine where each
requested value belongs.

You may use both the PDF's extracted text and its visual page
layout.

For every value that should be inserted into a PDF, return a
"pdf_edits" entry.

Each PDF edit MUST contain:

- "filename": the original PDF filename
- "page": the 1-based page number
- "text": the exact text to insert
- "x0": left coordinate of the field
- "y0": top coordinate of the field
- "x1": right coordinate of the field
- "y1": bottom coordinate of the field

IMPORTANT COORDINATE RULES:

Coordinates must be expressed in PDF points.

The coordinate system must use the TOP-LEFT corner of the page as
the origin.

x0/y0 is the upper-left corner of the field.

x1/y1 is the lower-right corner of the field.

Do NOT use pixel coordinates.

Do NOT use normalized coordinates such as 0.0 to 1.0.

Use the actual dimensions and coordinate system of the uploaded PDF.

Identify the actual blank field where the value belongs.

Do not simply place the text near the label.

For example, if the PDF contains:

氏名: ____________________

and the requested value is:

Joe Smith

the bounding box should cover the blank area after 氏名:, not the
label itself.

Pay particular attention to:

- Japanese labels
- tables
- checkboxes
- signatures
- dates
- addresses
- company names
- employee names
- numeric fields
- multi-line fields

Do not fabricate fields.

Only return a pdf_edit when you can confidently identify the
corresponding field.

If requested information cannot be located confidently, put that
information in "missing_data".

The application will use PyMuPDF to insert the text into the
returned bounding boxes.

Do not create a completed PDF yourself.

Do not use Code Interpreter to create the PDF.

Your response MUST be valid JSON with this structure:

{
  "summary": "...",
  "completed": true,
  "files": [],
  "pdf_edits": [
    {
      "filename": "form.pdf",
      "page": 1,
      "text": "Joe Smith",
      "x0": 125,
      "y0": 210,
      "x1": 305,
      "y1": 240
    }
  ],
  "missing_data": [],
  "recommendations": []
}

For a PDF job, "files" MUST remain an empty array because the
application, rather than Code Interpreter, creates the completed
PDF.
"""

        content.append(
          {
            "type": "input_text",
            "text": pdf_instruction,
          }
        )

      tools = []

      if code_interpreter_file_ids:

        tools.append(
          {
            "type": "code_interpreter",
            "container": {
              "type": "auto",
              "file_ids": code_interpreter_file_ids,
            },
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
        tools=tools,
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
        ) == "message":

          logger.info(
            "OpenAI message output: %r",
            item,
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

      if not has_image and not has_pdf:

        self._extract_output_files_from_response(
          response=response,
          agent_output=agent_output,
          output_dir=output_dir,
        )

      return agent_output

    finally:

      for _, uploaded in uploaded_files:

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
      "========== EXTRACTING CODE INTERPRETER OUTPUT FILES =========="
    )

    # Prefer the files the agent said it created when determining
    # which filenames we ultimately expect.
    reported_files = agent_output.get(
      "files",
      [],
    )

    logger.info(
      "Agent reported files: %s",
      reported_files,
    )

    container_ids = []

    for item in response.output:
      if getattr(
        item,
        "type",
        None,
      ) != "code_interpreter_call":
        continue

      container_id = getattr(
        item,
        "container_id",
        None,
      )

      if (
        container_id
        and container_id not in container_ids
      ):
        container_ids.append(
          container_id,
        )

    if not container_ids:
      logger.warning(
        "No Code Interpreter container IDs found in response.",
      )
      return

    extracted_files = []

    for container_id in container_ids:

      logger.info(
        "Listing files in Code Interpreter container: %s",
        container_id,
      )

      response_files = (
        self.openai.containers.files.list(
          container_id,
        )
      )

      for container_file in response_files.data:

        file_id = getattr(
          container_file,
          "id",
          None,
        )

        file_path = getattr(
          container_file,
          "path",
          None,
        )

        source = getattr(
          container_file,
          "source",
          None,
        )

        logger.info(
          "Container file: id=%s path=%s source=%s",
          file_id,
          file_path,
          source,
        )

        if not file_id or not file_path:
          continue

        # Only retrieve files that the agent placed in its
        # designated output directory.
        if not file_path.startswith(
          "/mnt/data/output/",
        ):
          continue

        filename = Path(
          file_path,
        ).name

        if not filename:
          continue

        local_path = (
          output_dir / filename
        )

        logger.info(
          "Downloading generated output file: %s -> %s",
          file_path,
          local_path,
        )

        file_content = (
          self.openai.containers.files.content.retrieve(
            container_id=container_id,
            file_id=file_id,
          )
        )

        with open(
          local_path,
          "wb",
        ) as f:
          f.write(
            file_content.content,
          )

        extracted_files.append(
          str(local_path),
        )

        logger.info(
          "Successfully extracted output file: %s",
          local_path,
        )

    logger.info(
      "Extracted %d output file(s): %s",
      len(extracted_files),
      extracted_files,
    )

    logger.info(
      "========== END EXTRACTING CODE INTERPRETER OUTPUT FILES =========="
    )

  def _apply_image_edits(
    self,
    input_file: Path,
    image_edits: list[dict],
    output_dir: Path,
  ) -> Path:

    from PIL import Image, ImageDraw, ImageFont

    output_dir.mkdir(
      parents=True,
      exist_ok=True,
    )

    logger.info(
      "Applying %d image edit(s) to %s",
      len(image_edits),
      input_file.name,
    )

    image = Image.open(
      input_file,
    )

    image.load()

    original_width, original_height = image.size

    logger.info(
      "Original image dimensions: %dx%d",
      original_width,
      original_height,
    )

    if image.mode not in {
      "RGB",
      "RGBA",
    }:
      image = image.convert(
        "RGB",
      )

    draw = ImageDraw.Draw(
      image,
    )

    font_path = (
      "/usr/share/fonts/truetype/noto/NotoSansJP-Regular.ttf"
    )

    if font_path:
      logger.info(
        "Using image form font: %s",
        font_path,
      )
    else:
      logger.warning(
        "No suitable system font found; using Pillow default font.",
      )

    applied_count = 0

    for edit in image_edits:

      filename = edit.get(
        "filename",
      )

      if filename and filename != input_file.name:
        continue

      text = edit.get(
        "text",
      )

      x = edit.get(
        "x",
      )

      y = edit.get(
        "y",
      )

      width = edit.get(
        "width",
      )

      height = edit.get(
        "height",
      )

      if not text:
        logger.warning(
          "Skipping image edit with no text: %s",
          edit,
        )
        continue

      try:
        x = float(x)
        y = float(y)
        width = float(width)
        height = float(height)
      except (
        TypeError,
        ValueError,
      ):
        logger.warning(
          "Skipping image edit with invalid coordinates: %s",
          edit,
        )
        continue

      if width <= 0 or height <= 0:
        logger.warning(
          "Skipping image edit with invalid dimensions: %s",
          edit,
        )
        continue

      if (
        x < 0
        or y < 0
        or x >= original_width
        or y >= original_height
      ):
        logger.warning(
          "Skipping image edit outside image bounds: %s",
          edit,
        )
        continue

      max_width = min(
        width,
        original_width - x,
      )

      max_height = min(
        height,
        original_height - y,
      )

      if font_path:
        font_size = max(
          8,
          int(
            max_height * 0.70,
          ),
        )

        while font_size >= 8:

          font = ImageFont.truetype(
            font_path,
            font_size,
          )

          bbox = draw.textbbox(
            (0, 0),
            str(text),
            font=font,
          )

          text_width = (
            bbox[2] - bbox[0]
          )

          text_height = (
            bbox[3] - bbox[1]
          )

          if (
            text_width <= max_width
            and text_height <= max_height
          ):
            break

          font_size -= 1

        if font_size < 8:
          font = ImageFont.truetype(
            font_path,
            8,
          )

      else:
        font = ImageFont.load_default()

        bbox = draw.textbbox(
          (0, 0),
          str(text),
          font=font,
        )

        text_width = (
          bbox[2] - bbox[0]
        )

        text_height = (
          bbox[3] - bbox[1]
        )

      bbox = draw.textbbox(
        (0, 0),
        str(text),
        font=font,
      )

      text_width = (
        bbox[2] - bbox[0]
      )

      text_height = (
        bbox[3] - bbox[1]
      )

      text_x = (
        x
        + max(
          0,
          (max_width - text_width) / 2,
        )
      )

      text_y = (
        y
        + max(
          0,
          (max_height - text_height) / 2,
        )
        - bbox[1]
      )

      logger.info(
        "Applying image edit: text=%r x=%s y=%s width=%s height=%s",
        text,
        x,
        y,
        width,
        height,
      )

      draw.text(
        (
          text_x,
          text_y,
        ),
        str(text),
        font=font,
        fill=(0, 0, 0),
      )

      applied_count += 1

    output_filename = (
      f"{input_file.stem}_completed"
      f"{input_file.suffix}"
    )

    output_path = (
      output_dir / output_filename
    )

    image.save(
      output_path,
      quality=95,
    )

    logger.info(
      "Saved completed image: %s",
      output_path,
    )

    logger.info(
      "Applied %d/%d image edit(s)",
      applied_count,
      len(image_edits),
    )

    return output_path

  
  def _apply_pdf_edits(
    self,
    input_file: Path,
    pdf_edits: list[dict],
    output_dir: Path,
  ) -> Path:

    import fitz

    output_dir.mkdir(
      parents=True,
      exist_ok=True,
    )

    logger.info(
      "Applying %d PDF edit(s) to %s",
      len(pdf_edits),
      input_file.name,
    )

    document = fitz.open(
      input_file,
    )

    font_path = (
      "/usr/share/fonts/truetype/noto/NotoSansJP-Regular.ttf"
    )

    if not font_path:
      document.close()

      raise ValueError(
        "Required NotoSansJP-Regular.ttf font was not found.",
      )

    logger.info(
      "Using PDF form font: %s",
      font_path,
    )

    applied_count = 0

    for edit in pdf_edits:

      filename = edit.get(
        "filename",
      )

      if filename and filename != input_file.name:
        continue

      page_number = edit.get(
        "page",
      )

      text = edit.get(
        "text",
      )

      try:

        page_number = int(
          page_number,
        )

        x0 = float(
          edit.get("x0"),
        )

        y0 = float(
          edit.get("y0"),
        )

        x1 = float(
          edit.get("x1"),
        )

        y1 = float(
          edit.get("y1"),
        )

      except (
        TypeError,
        ValueError,
      ):

        logger.warning(
          "Skipping PDF edit with invalid values: %s",
          edit,
        )

        continue

      if not text:
        continue

      page_index = (
        page_number - 1
      )

      if (
        page_index < 0
        or page_index >= document.page_count
      ):

        logger.warning(
          "Skipping PDF edit with invalid page: %s",
          edit,
        )

        continue

      page = document[
        page_index
      ]

      rect = fitz.Rect(
        x0,
        y0,
        x1,
        y1,
      )

      font_size = max(
        6,
        rect.height * 0.70,
      )

      logger.info(
        "Applying PDF edit: page=%s text=%r bbox=%s font_size=%s",
        page_number,
        text,
        rect,
        font_size,
      )

      page.insert_textbox(
        rect,
        str(text),
        fontname="NotoSansJP",
        fontfile=font_path,
        fontsize=font_size,
        color=(0, 0, 0),
        align=fitz.TEXT_ALIGN_CENTER,
        overlay=True,
      )

      applied_count += 1

    output_path = (
      output_dir
      / f"{input_file.stem}_completed.pdf"
    )

    document.save(
      output_path,
      garbage=4,
      deflate=True,
    )

    document.close()

    logger.info(
      "Saved completed PDF: %s",
      output_path,
    )

    logger.info(
      "Applied %d/%d PDF edit(s)",
      applied_count,
      len(pdf_edits),
    )

    return output_path

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

The FORM DESCRIPTION may identify the specific Kenchiku entity that the
form should be completed for.

The description may identify the target entity:

- directly by name
- by entity type
- by role
- by occupation
- by referring to an explicit custom relationship

Examples:

- "Fill this out for the user Joe Smith."
- "Fill this out for Joe Smith."
- "Fill this out for the supervisor."
- "Complete this for the project supervisor."
- "Fill this out for the subcontractor."
- "Complete this for the crane assigned to this project."

When the FORM DESCRIPTION explicitly identifies a target entity, resolve
that entity before selecting other entities to provide form values.

If the description identifies an entity by name, use that named entity
as the target.

If the description refers to a role or relationship, use the explicit
relationships in the Kenchiku graph to resolve the target entity.

For example, if the graph contains:

P-12345678 --[supervisor]--> U-87654321

and U-87654321 is Joe Smith, and the FORM DESCRIPTION says:

"Fill this out for the supervisor."

then Joe Smith is the target entity for the form.

The relationship definition and its semantic meaning should be used when
determining whether a relationship matches the description.

The wording does not need to exactly match the relationship name if the
relationship definition clearly establishes the same meaning.

For example, "project supervisor" may refer to a relationship named
"supervisor" when the relationship definition establishes that meaning.

A role mentioned alongside a person's name describes that person and
does not create a separate target entity.

For example:

"Fill this out for Joe Smith, the supervisor."

means:

Target entity: Joe Smith
Role/context: supervisor

Use Joe Smith as the target entity.

Once the target entity has been identified, use that entity's own fields
and its explicit relationships to determine the values that should be
entered into the form.

If no target entity is specified in the description, use the primary
project and its explicit relationships to determine the appropriate
entities.

Do not assume that every company user or object is relevant to the
project.

Relationships are explicit.

Relationship direction matters.

Always interpret a relationship using the actual source and target shown
in the graph.

Do not infer relationships from:

- shared company
- similar names
- email addresses
- similar custom field values
- proximity in the graph
- appearing in the same graph section

Only explicit relationships establish an association.

If multiple entities could satisfy the description and the correct entity
cannot be determined reliably, do not guess. Leave the affected fields
unresolved and report the ambiguity in missing_data.

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