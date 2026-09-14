import json
import logging
import mimetypes
import tempfile
from pathlib import Path
import cv2
import numpy as np

from openai import OpenAI
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageDraw, ImageFont
import base64
import os

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
        normalized_dir = workspace / "normalized"
        cleaned_dir = workspace / "cleaned"
        output_dir = workspace / "output"

        input_dir.mkdir()
        normalized_dir.mkdir()
        cleaned_dir.mkdir()
        output_dir.mkdir()

        input_files = await self._download_input_files(
          job,
          input_dir,
        )

        if not input_files:
          raise ValueError(
            "The form job does not contain any input files."
          )

        input_files = self._normalize_input_files(
          input_files=input_files,
          output_dir=normalized_dir,
        )

        input_files = self._clean_image_files(
          input_files=input_files,
          output_dir=cleaned_dir,
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

        logger.info(
          "FORM AGENT OUTPUT:\n%s",
          json.dumps(
            agent_output,
            ensure_ascii=False,
            indent=2,
          ),
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

          image_orientation = agent_output.get(
            "image_orientation",
            {},
          )

          image_edits = agent_output.get(
            "image_edits",
            [],
          )

          logger.info(
            "Image form returned %d edit(s)",
            len(image_edits),
          )

          for input_file in image_input_files:

            orientation = image_orientation.get(
              input_file.name,
              {},
            )

            rotation_degrees = orientation.get(
              "rotation_degrees",
              0,
            )

            try:
              rotation_degrees = int(
                rotation_degrees,
              )
            except (
              TypeError,
              ValueError,
            ):
              logger.warning(
                "Invalid rotation_degrees for %s: %r. "
                "Using 0.",
                input_file.name,
                rotation_degrees,
              )

              rotation_degrees = 0

            if rotation_degrees not in {
              0,
              90,
              180,
              270,
            }:
              logger.warning(
                "Unsupported rotation_degrees for %s: %r. "
                "Using 0.",
                input_file.name,
                rotation_degrees,
              )

              rotation_degrees = 0

            if rotation_degrees != 0:

              logger.info(
                "OpenAI requested %d° clockwise rotation "
                "for image %s",
                rotation_degrees,
                input_file.name,
              )

              self._rotate_image_for_form_editing(
                input_file=input_file,
                rotation_degrees=rotation_degrees,
              )

            else:

              logger.info(
                "OpenAI determined no rotation is required "
                "for image %s",
                input_file.name,
              )

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
            "PDF EDITS EXTRACTED: count=%d edits=%s",
            len(pdf_edits),
            json.dumps(
              pdf_edits,
              ensure_ascii=False,
              indent=2,
            ),
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

  def _normalize_input_files(
    self,
    input_files: list[Path],
    output_dir: Path,
  ) -> list[Path]:
    import subprocess
    import tempfile

    normalized_files = []

    output_dir.mkdir(
      parents=True,
      exist_ok=True,
    )

    for input_file in input_files:
      suffix = input_file.suffix.lower()

      if suffix not in {
        ".doc",
        ".xls",
      }:
        normalized_files.append(
          input_file,
        )
        continue

      if suffix == ".doc":
        target_extension = ".docx"
        convert_format = "docx"
      else:
        target_extension = ".xlsx"
        convert_format = "xlsx"

      expected_output = (
        output_dir
        / f"{input_file.stem}{target_extension}"
      )

      with tempfile.TemporaryDirectory() as profile_dir:
        command = [
          "libreoffice",
          "--headless",
          "--nologo",
          "--nodefault",
          "--norestore",
          "--nolockcheck",
          f"-env:UserInstallation=file://{profile_dir}",
          "--convert-to",
          convert_format,
          "--outdir",
          str(output_dir),
          str(input_file),
        ]

        logger.info(
          "Converting legacy Office file %s → %s",
          input_file.name,
          expected_output.name,
        )

        try:
          result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
          )
        except subprocess.TimeoutExpired as exc:
          raise ValueError(
            f"LibreOffice timed out while converting "
            f"{input_file.name}"
          ) from exc

      if result.returncode != 0:
        logger.error(
          "LibreOffice conversion failed for %s. "
          "returncode=%s stdout=%s stderr=%s",
          input_file,
          result.returncode,
          result.stdout,
          result.stderr,
        )

        raise ValueError(
          f"LibreOffice failed to convert "
          f"{input_file.name}: "
          f"{result.stderr.strip()}"
        )

      if not expected_output.exists():
        logger.error(
          "LibreOffice reported success but output file "
          "does not exist: %s. stdout=%s stderr=%s",
          expected_output,
          result.stdout,
          result.stderr,
        )

        raise ValueError(
          f"LibreOffice did not produce the expected "
          f"output file for {input_file.name}"
        )

      logger.info(
        "LibreOffice conversion successful: %s → %s",
        input_file.name,
        expected_output.name,
      )

      normalized_files.append(
        expected_output,
      )

    return normalized_files

  def _image_exif_summary(self, input_file: Path) -> dict:
    """Return non-sensitive camera EXIF signals for logging only.

    EXIF is useful evidence that an image came from a camera, but it is not
    reliable enough to be the decision-maker because mobile apps and upload
    pipelines can strip metadata.
    """
    try:
      with Image.open(input_file) as image:
        exif = image.getexif()

        camera_make = exif.get(271)
        camera_model = exif.get(272)
        lens_model = exif.get(42036)
        date_time_original = exif.get(36867)
        focal_length = exif.get(37386)
        exposure_time = exif.get(33434)
        iso = exif.get(34855)

        camera_signals = sum(
          value is not None
          for value in [
            camera_make,
            camera_model,
            lens_model,
            date_time_original,
            focal_length,
            exposure_time,
            iso,
          ]
        )

        return {
          "camera_signals": camera_signals,
          "camera_make": str(camera_make) if camera_make else None,
          "camera_model": str(camera_model) if camera_model else None,
          "lens_model": str(lens_model) if lens_model else None,
        }
    except Exception:
      logger.exception(
        "Could not inspect EXIF metadata for image: %s",
        input_file,
      )
      return {
        "camera_signals": 0,
        "camera_make": None,
        "camera_model": None,
        "lens_model": None,
      }

  def _clean_image_files(
    self,
    input_files: list[Path],
    output_dir: Path,
  ) -> list[Path]:

    logger.info(
      "========== IMAGE CLEANING START =========="
    )

    output_dir.mkdir(
      parents=True,
      exist_ok=True,
    )

    image_suffixes = {
      ".jpg",
      ".jpeg",
      ".png",
      ".webp",
      ".gif",
    }

    cleaned_files = []

    for input_file in input_files:
      logger.info(
        "IMAGE CLEANING CANDIDATE: "
        "filename=%s suffix=%s content_type=%s size=%s",
        input_file.name,
        input_file.suffix,
        # whatever content type you have available here
        getattr(input_file, "content_type", None),
        input_file.stat().st_size,
      )

      if input_file.suffix.lower() not in image_suffixes:
        cleaned_files.append(
          input_file,
        )
        continue

      cleaned_path = (
        output_dir
        / f"{input_file.stem}.jpg"
      )

      logger.info(
        "CALLING _clean_form_image(): "
        "input=%s output=%s",
        input_file,
        cleaned_path,
      )

      self._clean_form_image(
        input_file=input_file,
        output_file=cleaned_path,
      )

      logger.info(
        "CLEANED IMAGE RESULT: "
        "input=%s output=%s exists=%s size=%s",
        input_file,
        cleaned_path,
        cleaned_path.exists(),
        cleaned_path.stat().st_size if cleaned_path.exists() else None,
      )

      cleaned_files.append(
        cleaned_path,
      )

    logger.info(
      "CLEANED DIRECTORY CONTENTS: %s",
      [
        {
          "name": p.name,
          "suffix": p.suffix,
          "size": p.stat().st_size,
        }
        for p in output_dir.iterdir()
        if p.is_file()
      ],
    )

    logger.info(
      "========== IMAGE CLEANING END =========="
    )

    return cleaned_files



  def _order_quad_points(
    self,
    points: np.ndarray,
  ) -> np.ndarray:
    """
    Return points in:
      top-left
      top-right
      bottom-right
      bottom-left
    order.
    """
    points = np.asarray(points, dtype=np.float32)

    if points.shape != (4, 2):
      raise ValueError(
        f"Expected 4 points, got shape={points.shape}"
      )

    center = points.mean(axis=0)

    angles = np.arctan2(
      points[:, 1] - center[1],
      points[:, 0] - center[0],
    )

    points = points[
      np.argsort(angles)
    ]

    # Rotate so top-left is first.
    start = np.argmin(
      points[:, 0] + points[:, 1]
    )

    points = np.roll(
      points,
      -start,
      axis=0,
    )

    # Ensure clockwise order.
    cross = np.cross(
      points[1] - points[0],
      points[2] - points[1],
    )

    if cross < 0:
      points = np.array(
        [
          points[0],
          points[3],
          points[2],
          points[1],
        ],
        dtype=np.float32,
      )

    return points.astype(np.float32)

  def _line_from_points(
    self,
    p1: np.ndarray,
    p2: np.ndarray,
  ) -> np.ndarray:
    """
    Return normalized line coefficients:
      ax + by + c = 0
    """
    x1, y1 = p1
    x2, y2 = p2

    a = y1 - y2
    b = x2 - x1
    c = x1 * y2 - x2 * y1

    norm = np.hypot(a, b)

    if norm < 1e-8:
      raise ValueError("Degenerate line")

    return np.array(
      [
        a / norm,
        b / norm,
        c / norm,
      ],
      dtype=np.float64,
    )

  def _intersect_lines(
    self,
    line1: np.ndarray,
    line2: np.ndarray,
  ) -> np.ndarray | None:
    """
    Intersect two lines represented as:
      ax + by + c = 0
    """
    a1, b1, c1 = line1
    a2, b2, c2 = line2

    denominator = (
      a1 * b2
      - a2 * b1
    )

    if abs(denominator) < 1e-8:
      return None

    x = (
      b1 * c2
      - b2 * c1
    ) / denominator

    y = (
      c1 * a2
      - c2 * a1
    ) / denominator

    return np.array(
      [x, y],
      dtype=np.float32,
    )

  def _line_angle(
    self,
    p1: np.ndarray,
    p2: np.ndarray,
  ) -> float:
    """
    Return line angle in radians, normalized to [0, pi).
    """
    angle = np.arctan2(
      p2[1] - p1[1],
      p2[0] - p1[0],
    )

    if angle < 0:
      angle += np.pi

    return angle

  def _angle_difference(
    self,
    a: float,
    b: float,
  ) -> float:
    """
    Smallest difference between two unoriented line angles.
    """
    diff = abs(a - b) % np.pi

    return min(
      diff,
      np.pi - diff,
    )

  def _clean_form_image(self, input_file: Path, output_file: Path) -> None:
    """Normalize a form image before the form agent places any text.

    Strategy:
      1. OpenCV gets the first chance to find a physical document boundary.
      2. If OpenCV cannot find one confidently, OpenAI vision determines whether
         the image is a screenshot/other image or a physical document and, for
         a physical document, returns its four corners.
      3. OpenCV performs the actual perspective transform.
      4. OpenCV performs deterministic deskew/resize/contrast/sharpening.

    OpenAI is used for visual judgment, not pixel manipulation.
    """
    logger.info(
      "========== _clean_form_image START =========="
    )

    def order_points(points: np.ndarray) -> np.ndarray:
      points = np.asarray(points, dtype=np.float32)

      rect = np.zeros((4, 2), dtype=np.float32)

      sums = points.sum(axis=1)
      differences = np.diff(points, axis=1).reshape(-1)

      rect[0] = points[np.argmin(sums)]
      rect[2] = points[np.argmax(sums)]
      rect[1] = points[np.argmin(differences)]
      rect[3] = points[np.argmax(differences)]

      return rect

    def _perspective_crop(
      self,
      image: np.ndarray,
      corners: np.ndarray,
    ) -> np.ndarray:
      corners = self._order_quad_points(
        corners
      )

      top_left, top_right, bottom_right, bottom_left = corners

      top_width = np.linalg.norm(
        top_right - top_left
      )

      bottom_width = np.linalg.norm(
        bottom_right - bottom_left
      )

      left_height = np.linalg.norm(
        bottom_left - top_left
      )

      right_height = np.linalg.norm(
        bottom_right - top_right
      )

      output_width = int(
        round(
          (top_width + bottom_width)
          / 2.0
        )
      )

      output_height = int(
        round(
          (left_height + right_height)
          / 2.0
        )
      )

      if (
        output_width < 200
        or output_height < 200
      ):
        logger.warning(
          "Perspective crop dimensions too small: %sx%s",
          output_width,
          output_height,
        )
        return image

      destination = np.array(
        [
          [0, 0],
          [output_width - 1, 0],
          [output_width - 1, output_height - 1],
          [0, output_height - 1],
        ],
        dtype=np.float32,
      )

      matrix = cv2.getPerspectiveTransform(
        corners.astype(np.float32),
        destination,
      )

      warped = cv2.warpPerspective(
        image,
        matrix,
        (
          output_width,
          output_height,
        ),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
      )

      logger.info(
        "Perspective crop applied: "
        "input=%sx%s output=%sx%s corners=%s",
        image.shape[1],
        image.shape[0],
        output_width,
        output_height,
        corners.tolist(),
      )

      return warped


    def _detect_form_boundary_with_lines(
      self,
      image: np.ndarray,
    ) -> tuple[np.ndarray | None, float]:
      """
      Detect the large rectangular printed-form boundary.

      Unlike physical-paper detection, this does not require the
      paper itself to be visible. It looks for the large rectangle
      created by the form/table lines.

      Returns:
        (corners, confidence)

      corners are in ORIGINAL IMAGE coordinates:
        top-left
        top-right
        bottom-right
        bottom-left
      """

      original_height, original_width = image.shape[:2]

      # ------------------------------------------------------------
      # Work at a manageable resolution.
      # ------------------------------------------------------------

      max_dimension = 1800

      scale = min(
        1.0,
        max_dimension / max(
          original_width,
          original_height,
        ),
      )

      if scale < 1.0:
        working = cv2.resize(
          image,
          (
            int(round(original_width * scale)),
            int(round(original_height * scale)),
          ),
          interpolation=cv2.INTER_AREA,
        )
      else:
        working = image.copy()

      height, width = working.shape[:2]

      gray = cv2.cvtColor(
        working,
        cv2.COLOR_BGR2GRAY,
      )

      # ------------------------------------------------------------
      # Normalize lighting.
      #
      # This matters a lot for photographs of white paper.
      # ------------------------------------------------------------

      background = cv2.GaussianBlur(
        gray,
        (0, 0),
        25,
      )

      normalized = cv2.divide(
        gray,
        background,
        scale=255,
      )

      normalized = cv2.GaussianBlur(
        normalized,
        (3, 3),
        0,
      )

      # ------------------------------------------------------------
      # Build multiple edge maps.
      # ------------------------------------------------------------

      edge_maps = []

      # Normal Canny.
      edge_maps.append(
        cv2.Canny(
          normalized,
          30,
          100,
        )
      )

      # Adaptive threshold is useful when the paper has uneven
      # illumination.
      adaptive = cv2.adaptiveThreshold(
        normalized,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        31,
        8,
      )

      edge_maps.append(adaptive)

      # ------------------------------------------------------------
      # Emphasize long horizontal and vertical form lines.
      # ------------------------------------------------------------

      horizontal_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (
          max(20, width // 25),
          3,
        ),
      )

      vertical_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (
          3,
          max(20, height // 25),
        ),
      )

      horizontal_maps = []
      vertical_maps = []

      for edges in edge_maps:
        horizontal = cv2.morphologyEx(
          edges,
          cv2.MORPH_OPEN,
          horizontal_kernel,
        )

        vertical = cv2.morphologyEx(
          edges,
          cv2.MORPH_OPEN,
          vertical_kernel,
        )

        horizontal_maps.append(horizontal)
        vertical_maps.append(vertical)

      # Combine the maps.
      horizontal = cv2.bitwise_or(
        horizontal_maps[0],
        horizontal_maps[1],
      )

      vertical = cv2.bitwise_or(
        vertical_maps[0],
        vertical_maps[1],
      )

      # Close small gaps in the printed lines.
      horizontal = cv2.morphologyEx(
        horizontal,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(
          cv2.MORPH_RECT,
          (31, 3),
        ),
      )

      vertical = cv2.morphologyEx(
        vertical,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(
          cv2.MORPH_RECT,
          (3, 31),
        ),
      )

      # ------------------------------------------------------------
      # Detect line segments.
      # ------------------------------------------------------------

      line_segments = []

      for edge_map in (
        horizontal,
        vertical,
      ):
        lines = cv2.HoughLinesP(
          edge_map,
          rho=1,
          theta=np.pi / 180,
          threshold=max(
            50,
            int(min(width, height) * 0.10),
          ),
          minLineLength=max(
            80,
            int(max(width, height) * 0.25),
          ),
          maxLineGap=max(
            20,
            int(max(width, height) * 0.04),
          ),
        )

        if lines is None:
          continue

        for line in lines[:, 0]:
          x1, y1, x2, y2 = (
            line.astype(np.float32)
          )

          length = np.hypot(
            x2 - x1,
            y2 - y1,
          )

          if length < width * 0.20:
            continue

          angle = self._line_angle(
            np.array([x1, y1]),
            np.array([x2, y2]),
          )

          line_segments.append(
            {
              "p1": np.array(
                [x1, y1],
                dtype=np.float32,
              ),
              "p2": np.array(
                [x2, y2],
                dtype=np.float32,
              ),
              "angle": angle,
              "length": float(length),
            }
          )

      if len(line_segments) < 4:
        logger.info(
          "Form line detection found fewer than 4 usable lines"
        )
        return None, 0.0

      # ------------------------------------------------------------
      # Find the dominant two line directions.
      # ------------------------------------------------------------

      angles = np.array(
        [
          segment["angle"]
          for segment in line_segments
        ],
        dtype=np.float64,
      )

      lengths = np.array(
        [
          segment["length"]
          for segment in line_segments
        ],
        dtype=np.float64,
      )

      # Weighted angle histogram.
      bins = 180

      histogram = np.zeros(
        bins,
        dtype=np.float64,
      )

      for angle, length in zip(
        angles,
        lengths,
      ):
        index = int(
          round(
            angle
            * 180
            / np.pi
          )
        ) % bins

        histogram[index] += length

      # Smooth histogram.
      histogram = cv2.GaussianBlur(
        histogram.reshape(1, -1),
        (1, 11),
        0,
      ).reshape(-1)

      dominant_index = int(
        np.argmax(histogram)
      )

      dominant_angle = (
        dominant_index
        * np.pi
        / 180
      )

      # Find a second direction approximately 90 degrees away.
      target_second = (
        dominant_angle
        + np.pi / 2
      ) % np.pi

      angle_distances = np.array(
        [
          self._angle_difference(
            angle,
            target_second,
          )
          for angle in angles
        ]
      )

      second_candidates = np.where(
        angle_distances < np.deg2rad(25)
      )[0]

      if len(second_candidates) == 0:
        logger.info(
          "Could not find second dominant form-line direction"
        )
        return None, 0.0

      second_index = second_candidates[
        np.argmax(
          lengths[second_candidates]
        )
      ]

      second_angle = angles[
        second_index
      ]

      # ------------------------------------------------------------
      # Separate the lines into the two families.
      # ------------------------------------------------------------

      family_a = []
      family_b = []

      for segment in line_segments:
        distance_a = self._angle_difference(
          segment["angle"],
          dominant_angle,
        )

        distance_b = self._angle_difference(
          segment["angle"],
          second_angle,
        )

        if distance_a <= distance_b:
          if distance_a < np.deg2rad(18):
            family_a.append(segment)
        else:
          if distance_b < np.deg2rad(18):
            family_b.append(segment)

      if len(family_a) < 2 or len(family_b) < 2:
        logger.info(
          "Insufficient lines in one of the two form directions: "
          "A=%d B=%d",
          len(family_a),
          len(family_b),
        )
        return None, 0.0

      # ------------------------------------------------------------
      # Represent each line in normalized ax + by + c = 0 form.
      # ------------------------------------------------------------

      def make_line(segment):
        return self._line_from_points(
          segment["p1"],
          segment["p2"],
        )

      lines_a = [
        (
          make_line(segment),
          segment,
        )
        for segment in family_a
      ]

      lines_b = [
        (
          make_line(segment),
          segment,
        )
        for segment in family_b
      ]

      # ------------------------------------------------------------
      # Find the two outermost lines in each family.
      # ------------------------------------------------------------

      def select_outer_pair(
        candidates,
      ):
        best_pair = None
        best_score = -1.0

        for i in range(len(candidates)):
          line_i, segment_i = candidates[i]

          for j in range(i + 1, len(candidates)):
            line_j, segment_j = candidates[j]

            a1, b1, c1 = line_i
            a2, b2, c2 = line_j

            # Make normals point in the same direction.
            if (
              a1 * a2
              + b1 * b2
              < 0
            ):
              a2 = -a2
              b2 = -b2
              c2 = -c2

            distance = abs(
              c1 - c2
            ) / max(
              np.hypot(a1, b1),
              1e-8,
            )

            # Require meaningful separation.
            if distance < min(width, height) * 0.15:
              continue

            score = (
              distance
              * min(
                segment_i["length"],
                segment_j["length"],
              )
            )

            if score > best_score:
              best_score = score
              best_pair = (
                line_i,
                line_j,
              )

        return best_pair

      outer_a = select_outer_pair(
        lines_a
      )

      outer_b = select_outer_pair(
        lines_b
      )

      if outer_a is None or outer_b is None:
        logger.info(
          "Could not identify outer form lines"
        )
        return None, 0.0

      a1, a2 = outer_a
      b1, b2 = outer_b

      # ------------------------------------------------------------
      # Intersections give us the four corners.
      # ------------------------------------------------------------

      p1 = self._intersect_lines(
        a1,
        b1,
      )

      p2 = self._intersect_lines(
        a2,
        b1,
      )

      p3 = self._intersect_lines(
        a2,
        b2,
      )

      p4 = self._intersect_lines(
        a1,
        b2,
      )

      if any(
        point is None
        for point in (
          p1,
          p2,
          p3,
          p4,
        )
      ):
        logger.info(
          "Form line intersections failed"
        )
        return None, 0.0

      corners = np.array(
        [
          p1,
          p2,
          p3,
          p4,
        ],
        dtype=np.float32,
      )

      # ------------------------------------------------------------
      # Scale back to ORIGINAL image coordinates.
      # ------------------------------------------------------------

      if scale != 1.0:
        corners /= scale

      corners = self._order_quad_points(
        corners
      )

      # ------------------------------------------------------------
      # Validate the quadrilateral.
      # ------------------------------------------------------------

      x = corners[:, 0]
      y = corners[:, 1]

      if (
        np.any(x < -original_width * 0.10)
        or np.any(y < -original_height * 0.10)
        or np.any(x > original_width * 1.10)
        or np.any(y > original_height * 1.10)
      ):
        logger.info(
          "Detected form corners extend too far outside image: %s",
          corners.tolist(),
        )
        return None, 0.0

      area = abs(
        cv2.contourArea(
          corners.reshape(-1, 1, 2)
        )
      )

      image_area = (
        original_width
        * original_height
      )

      area_ratio = (
        area / image_area
      )

      if area_ratio < 0.15:
        logger.info(
          "Detected form is too small: area_ratio=%.3f",
          area_ratio,
        )
        return None, 0.0

      # Check side lengths.
      tl, tr, br, bl = corners

      top = np.linalg.norm(
        tr - tl
      )

      bottom = np.linalg.norm(
        br - bl
      )

      left = np.linalg.norm(
        bl - tl
      )

      right = np.linalg.norm(
        br - tr
      )

      if min(
        top,
        bottom,
        left,
        right,
      ) < 100:
        return None, 0.0

      # Width/height consistency.
      width_ratio = (
        min(top, bottom)
        / max(top, bottom)
      )

      height_ratio = (
        min(left, right)
        / max(left, right)
      )

      if (
        width_ratio < 0.65
        or height_ratio < 0.65
      ):
        logger.info(
          "Detected quadrilateral has inconsistent opposite sides: "
          "width_ratio=%.3f height_ratio=%.3f",
          width_ratio,
          height_ratio,
        )
        return None, 0.0

      confidence = min(
        1.0,
        (
          area_ratio * 0.55
          + width_ratio * 0.20
          + height_ratio * 0.20
          + min(
            1.0,
            best_score
            / (
              min(width, height)
              * max(width, height)
            ),
          ) * 0.05
        ),
      )

      logger.info(
        "FORM BOUNDARY DETECTED: "
        "confidence=%.3f corners=%s",
        confidence,
        corners.tolist(),
      )

      return corners, confidence


    def detect_document_with_opencv(
      image: np.ndarray,
    ) -> tuple[np.ndarray | None, float]:
      """Fast deterministic document-boundary detection.

      This intentionally runs before the vision fallback so ordinary document
      photos do not incur an OpenAI vision call when OpenCV can solve them.
      """
      detection_image = image.copy()

      original_height, original_width = image.shape[:2]
      detection_max_dimension = 1500
      detection_scale = min(
        1.0,
        detection_max_dimension / max(
          original_width,
          original_height,
        ),
      )

      if detection_scale < 1.0:
        detection_image = cv2.resize(
          detection_image,
          (
            max(1, int(original_width * detection_scale)),
            max(1, int(original_height * detection_scale)),
          ),
          interpolation=cv2.INTER_AREA,
        )

      detection_height, detection_width = detection_image.shape[:2]
      image_area = float(detection_width * detection_height)

      gray = cv2.cvtColor(
        detection_image,
        cv2.COLOR_BGR2GRAY,
      )
      gray = cv2.GaussianBlur(
        gray,
        (5, 5),
        0,
      )

      edges = cv2.Canny(
        gray,
        30,
        120,
      )

      kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (9, 9),
      )

      edges = cv2.morphologyEx(
        edges,
        cv2.MORPH_CLOSE,
        kernel,
      )

      contours, _ = cv2.findContours(
        edges,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
      )

      best_candidate = None
      best_score = 0.0

      for contour in contours:
        contour_area = cv2.contourArea(contour)

        if contour_area < image_area * 0.15:
          continue

        perimeter = cv2.arcLength(
          contour,
          True,
        )

        if perimeter <= 0:
          continue

        approximation = cv2.approxPolyDP(
          contour,
          0.025 * perimeter,
          True,
        )

        if len(approximation) != 4:
          continue

        if not cv2.isContourConvex(approximation):
          continue

        points = approximation.reshape(4, 2).astype(np.float32)
        rect = order_points(points)

        width_a = np.linalg.norm(rect[2] - rect[3])
        width_b = np.linalg.norm(rect[1] - rect[0])
        height_a = np.linalg.norm(rect[1] - rect[2])
        height_b = np.linalg.norm(rect[0] - rect[3])

        max_width = max(width_a, width_b)
        max_height = max(height_a, height_b)

        if max_width <= 0 or max_height <= 0:
          continue

        rectangular_area = max_width * max_height
        rectangularity = contour_area / rectangular_area

        if rectangularity < 0.55:
          continue

        x_values = points[:, 0]
        y_values = points[:, 1]

        touches_left = np.any(x_values <= detection_width * 0.01)
        touches_right = np.any(x_values >= detection_width * 0.99)
        touches_top = np.any(y_values <= detection_height * 0.01)
        touches_bottom = np.any(y_values >= detection_height * 0.99)

        edge_count = sum(
          [
            touches_left,
            touches_right,
            touches_top,
            touches_bottom,
          ]
        )

        if edge_count >= 3:
          continue

        area_score = contour_area / image_area
        score = (
          area_score * 0.70
          + rectangularity * 0.30
        )

        if score > best_score:
          best_score = score
          best_candidate = points

      if best_candidate is None:
        logger.info(
          "OpenCV document boundary detection found no candidate"
        )
        return None, 0.0

      if detection_scale != 1.0:
        best_candidate = best_candidate / detection_scale

      logger.info(
        "OpenCV document boundary detected: score=%.3f corners=%s",
        best_score,
        best_candidate.tolist(),
      )

      return best_candidate, best_score

    def detect_document_with_vision(
      image: np.ndarray,
      image_width: int,
      image_height: int,
    ) -> dict | None:
      """Use OpenAI only when OpenCV cannot confidently find the page.

      The model performs visual classification and geometry detection. It does
      not edit pixels. OpenCV applies the returned geometry.
      """
      try:
        success, encoded = cv2.imencode(
          ".jpg",
          image,
          [cv2.IMWRITE_JPEG_QUALITY, 95],
        )

        if not success:
          logger.warning(
            "Could not JPEG-encode image for OpenAI vision detection"
          )
          return None

        encoded_image = base64.b64encode(
          encoded.tobytes()
        ).decode("utf-8")

        image_data_url = (
          f"data:image/jpeg;base64,{encoded_image}"
        )

        prompt = f"""
You are the image-geometry detector for a construction-document processing
pipeline.

Analyze the supplied image and classify it as exactly one of:
- "camera_document": a photograph of a physical paper/document
- "screenshot": a screenshot or digitally rendered screen image
- "other": neither of the above or too ambiguous to determine

Original image dimensions:
width = {image_width}
height = {image_height}

If the image is a camera photograph of a physical document, identify the FOUR
physical outer corners of the main sheet of paper. Do NOT use an inner table,
printed border, text block, or form field as the document boundary.

Return ONLY valid JSON in exactly this structure:

{{
  "image_type": "camera_document",
  "confidence": 0.95,
  "document_detected": true,
  "corners": [
    {{"x": 100, "y": 100}},
    {{"x": 700, "y": 100}},
    {{"x": 700, "y": 900}},
    {{"x": 100, "y": 900}}
  ]
}}

The corners MUST be ordered:
1. top-left
2. top-right
3. bottom-right
4. bottom-left

Coordinates MUST be pixel coordinates in the ORIGINAL image.

For a screenshot or non-document image, return:

{{
  "image_type": "screenshot",
  "confidence": 0.98,
  "document_detected": false,
  "corners": []
}}

Do not invent corners. If you cannot confidently identify a physical sheet,
return document_detected=false.

If the image is a camera photo but the physical document boundary is not
confidently visible, classify it as camera_document but return an empty corners
array and document_detected=false.
"""

        response = self.openai.responses.create(
          model="gpt-5.6-luna",
          reasoning={
            "effort": "medium",
          },
          input=[
            {
              "role": "user",
              "content": [
                {
                  "type": "input_text",
                  "text": prompt,
                },
                {
                  "type": "input_image",
                  "image_url": image_data_url,
                },
              ],
            }
          ],
        )

        response_text = response.output_text.strip()

        logger.info(
          "VISION IMAGE GEOMETRY RESPONSE: %s",
          response_text,
        )

        try:
          result = json.loads(response_text)
        except json.JSONDecodeError:
          logger.warning(
            "Vision image geometry returned invalid JSON"
          )
          return None

        image_type = str(
          result.get("image_type", "other")
        ).strip().lower()

        try:
          confidence = float(
            result.get("confidence", 0.0)
          )
        except (TypeError, ValueError):
          confidence = 0.0

        logger.info(
          "VISION IMAGE CLASSIFICATION: type=%s confidence=%.3f",
          image_type,
          confidence,
        )

        if image_type == "screenshot":
          logger.info(
            "OpenAI classified the image as a screenshot; "
            "no perspective correction will be applied."
          )
          return {
            "image_type": "screenshot",
            "confidence": confidence,
            "corners": None,
          }

        if image_type != "camera_document":
          logger.info(
            "OpenAI classified %s as %s; "
            "no document boundary will be applied.",
            image_type,
          )
          return {
            "image_type": image_type,
            "confidence": confidence,
            "corners": None,
          }

        if not result.get("document_detected"):
          logger.info(
            "OpenAI identified the image as a camera document but could not "
            "confidently identify its physical boundary."
          )
          return {
            "image_type": "camera_document",
            "confidence": confidence,
            "corners": None,
          }

        if confidence < 0.70:
          logger.info(
            "Vision document confidence too low: %.3f",
            confidence,
          )
          return {
            "image_type": "camera_document",
            "confidence": confidence,
            "corners": None,
          }

        corners = result.get("corners")

        if not isinstance(corners, list) or len(corners) != 4:
          logger.warning(
            "Vision document detection returned invalid corner count"
          )
          return {
            "image_type": "camera_document",
            "confidence": confidence,
            "corners": None,
          }

        parsed_corners = []

        for corner in corners:
          if not isinstance(corner, dict):
            logger.warning(
              "Vision document corner was not an object: %r",
              corner,
            )
            return {
              "image_type": "camera_document",
              "confidence": confidence,
              "corners": None,
            }

          try:
            x = float(corner["x"])
            y = float(corner["y"])
          except (KeyError, TypeError, ValueError):
            logger.warning(
              "Vision document corner had invalid coordinates: %r",
              corner,
            )
            return {
              "image_type": "camera_document",
              "confidence": confidence,
              "corners": None,
            }

          x = max(
            0.0,
            min(float(image_width - 1), x),
          )
          y = max(
            0.0,
            min(float(image_height - 1), y),
          )

          parsed_corners.append([x, y])

        corners_array = np.asarray(
          parsed_corners,
          dtype=np.float32,
        )

        contour = corners_array.reshape(-1, 1, 2)

        if not cv2.isContourConvex(contour):
          logger.warning(
            "Vision document corners are not convex"
          )
          return {
            "image_type": "camera_document",
            "confidence": confidence,
            "corners": None,
          }

        area = abs(cv2.contourArea(contour))
        image_area = float(image_width * image_height)

        if image_area <= 0:
          return None

        area_ratio = area / image_area

        if area_ratio < 0.15:
          logger.warning(
            "Vision document area too small: %.3f",
            area_ratio,
          )
          return {
            "image_type": "camera_document",
            "confidence": confidence,
            "corners": None,
          }

        logger.info(
          "VISION DOCUMENT DETECTED: confidence=%.3f area_ratio=%.3f corners=%s",
          confidence,
          area_ratio,
          corners_array.tolist(),
        )

        return {
          "image_type": "camera_document",
          "confidence": confidence,
          "corners": corners_array,
        }

      except Exception:
        logger.exception(
          "Vision image geometry detection failed"
        )
        return None

    # ------------------------------------------------------------------
    # Load image and normalize EXIF orientation.
    # ------------------------------------------------------------------
    try:
      with Image.open(input_file) as pil_image:
        logger.info(
          "PIL OPENED IMAGE: format=%s mode=%s size=%s width=%s height=%s",
          pil_image.format,
          pil_image.mode,
          pil_image.size,
          pil_image.width,
          pil_image.height,
        )

        exif_summary = self._image_exif_summary(input_file)

        logger.info(
          "IMAGE EXIF CAMERA SIGNALS: signals=%s make=%r model=%r lens=%r",
          exif_summary.get("camera_signals"),
          exif_summary.get("camera_make"),
          exif_summary.get("camera_model"),
          exif_summary.get("lens_model"),
        )

        pil_image = ImageOps.exif_transpose(pil_image)

        logger.info(
          "AFTER EXIF TRANSPOSE: size=%s width=%s height=%s",
          pil_image.size,
          pil_image.width,
          pil_image.height,
        )

        if pil_image.mode != "RGB":
          pil_image = pil_image.convert("RGB")

        image = np.array(pil_image)

    except Exception:
      logger.exception(
        "Failed to open image for document cleanup: %s",
        input_file,
      )
      raise

    image = cv2.cvtColor(
      image,
      cv2.COLOR_RGB2BGR,
    )

    logger.info(
      "CONVERTED TO OPENCV: shape=%s dtype=%s",
      image.shape,
      image.dtype,
    )

    original_height, original_width = image.shape[:2]

    logger.info(
      "Original image dimensions: %sx%s",
      original_width,
      original_height,
    )

    # ------------------------------------------------------------------
    # 1. OpenCV gets first chance at document detection.
    # ------------------------------------------------------------------
    document_corners, document_score = (
      self._detect_form_boundary_with_lines(
        image
      )
    )

    if document_corners is not None:
      logger.info(
        "Applying detected form-boundary perspective correction "
        "(score=%.3f)",
        document_score,
      )

      image = self._perspective_crop(
        image,
        document_corners,
      )
    else:
      logger.info(
        "Could not confidently detect form boundary; "
        "leaving original perspective unchanged."
      )

      vision_result = detect_document_with_vision(
        image,
        original_width,
        original_height,
      )

      if vision_result is not None:
        image_type = vision_result.get(
          "image_type",
          "unknown",
        )
        vision_corners = vision_result.get(
          "corners",
        )

        if vision_corners is not None:
          document_corners = vision_corners
          logger.info(
            "Using OpenAI document corners because OpenCV could not "
            "reliably find the page."
          )
        elif image_type == "screenshot":
          logger.info(
            "Image classified as screenshot; keeping original framing "
            "and skipping document perspective correction."
          )
        else:
          logger.info(
            "OpenAI did not provide usable document corners; "
            "keeping original image framing."
          )
      else:
        logger.info(
          "OpenAI image geometry detection unavailable/failed; "
          "keeping original image framing."
        )

    # A screenshot is already a digitally rendered image. Do not apply
    # document-specific perspective, deskew, lighting, or sharpening to it.
    if image_type == "screenshot" and document_corners is None:
      output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
      )

      screenshot_rgb = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2RGB,
      )

      Image.fromarray(screenshot_rgb).save(
        output_file,
        "JPEG",
        quality=95,
      )

      logger.info(
        "Saved screenshot without document cleanup: %s (%sx%s)",
        output_file,
        image.shape[1],
        image.shape[0],
      )

      logger.info(
        "========== _clean_form_image END =========="
      )
      return

    else:
      image_type = "camera_document"
      logger.info(
        "Using OpenCV document boundary score %.3f; "
        "OpenAI geometry fallback was not needed.",
        opencv_score,
      )

    # ------------------------------------------------------------------
    # 3. Apply the physical document perspective correction.
    # ------------------------------------------------------------------
    if document_corners is not None:
      logger.info(
        "Document boundary detected; applying perspective correction."
      )

      image = perspective_crop(
        image,
        document_corners,
      )
    else:
      logger.info(
        "No confident physical document boundary detected; "
        "keeping original image framing."
      )

    # ------------------------------------------------------------------
    # 4. Small residual deskew.
    # ------------------------------------------------------------------
    gray = cv2.cvtColor(
      image,
      cv2.COLOR_BGR2GRAY,
    )

    gray = cv2.GaussianBlur(
      gray,
      (5, 5),
      0,
    )

    edges = cv2.Canny(
      gray,
      50,
      150,
    )

    lines = cv2.HoughLinesP(
      edges,
      1,
      np.pi / 180,
      threshold=max(
        50,
        min(image.shape[:2]) // 5,
      ),
      minLineLength=max(
        100,
        min(image.shape[:2]) // 4,
      ),
      maxLineGap=20,
    )

    angles = []

    if lines is not None:
      lines = np.asarray(lines).reshape(-1, 4)

      for line in lines:
        x1, y1, x2, y2 = line

        dx = float(x2 - x1)
        dy = float(y2 - y1)

        if dx == 0 and dy == 0:
          continue

        angle = np.degrees(
          np.arctan2(dy, dx)
        )

        while angle > 90:
          angle -= 180

        while angle < -90:
          angle += 180

        if abs(angle) <= 20:
          angles.append(float(angle))

    if len(angles) >= 3:
      median_angle = float(
        np.median(
          np.asarray(
            angles,
            dtype=np.float32,
          )
        )
      )
    else:
      median_angle = 0.0

    if (
      abs(median_angle) >= 0.7
      and abs(median_angle) <= 8.0
    ):
      height, width = image.shape[:2]
      center = (
        width / 2.0,
        height / 2.0,
      )

      rotation_matrix = cv2.getRotationMatrix2D(
        center,
        median_angle,
        1.0,
      )

      cos = abs(rotation_matrix[0, 0])
      sin = abs(rotation_matrix[0, 1])

      new_width = int(
        height * sin + width * cos
      )
      new_height = int(
        height * cos + width * sin
      )

      rotation_matrix[0, 2] += (
        new_width / 2 - center[0]
      )
      rotation_matrix[1, 2] += (
        new_height / 2 - center[1]
      )

      image = cv2.warpAffine(
        image,
        rotation_matrix,
        (new_width, new_height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
      )

      logger.info(
        "Deskewed form by %.2f degrees",
        median_angle,
      )
    else:
      logger.info(
        "No significant deskew required (median angle %.2f degrees)",
        median_angle,
      )

    # ------------------------------------------------------------------
    # 5. Prevent excessively large output images.
    # ------------------------------------------------------------------
    height, width = image.shape[:2]
    max_dimension = 3000

    if max(height, width) > max_dimension:
      scale = max_dimension / max(height, width)
      new_width = int(round(width * scale))
      new_height = int(round(height * scale))

      image = cv2.resize(
        image,
        (new_width, new_height),
        interpolation=cv2.INTER_AREA,
      )

      logger.info(
        "Image resized: %sx%s -> %sx%s",
        width,
        height,
        new_width,
        new_height,
      )

    # ------------------------------------------------------------------
    # 6. Gentle lighting normalization / local contrast.
    # ------------------------------------------------------------------
    lab = cv2.cvtColor(
      image,
      cv2.COLOR_BGR2LAB,
    )

    l_channel, a_channel, b_channel = cv2.split(lab)

    background = cv2.GaussianBlur(
      l_channel,
      (0, 0),
      25,
    )

    normalized_l = cv2.divide(
      l_channel,
      background,
      scale=255,
    )

    clahe = cv2.createCLAHE(
      clipLimit=2.0,
      tileGridSize=(8, 8),
    )

    enhanced_l = clahe.apply(
      normalized_l,
    )

    # ------------------------------------------------------------------
    # 7. Mild sharpening.
    # ------------------------------------------------------------------
    blurred = cv2.GaussianBlur(
      enhanced_l,
      (0, 0),
      1.0,
    )

    sharpened_l = cv2.addWeighted(
      enhanced_l,
      1.15,
      blurred,
      -0.15,
      0,
    )

    # ------------------------------------------------------------------
    # 8. Recombine while preserving original colors.
    # ------------------------------------------------------------------
    cleaned_lab = cv2.merge(
      [
        sharpened_l,
        a_channel,
        b_channel,
      ],
    )

    cleaned = cv2.cvtColor(
      cleaned_lab,
      cv2.COLOR_LAB2BGR,
    )

    # ------------------------------------------------------------------
    # 9. Save.
    # ------------------------------------------------------------------
    output_file.parent.mkdir(
      parents=True,
      exist_ok=True,
    )

    logger.info(
      "SAVING CLEANED IMAGE: output=%s image_type=%s",
      output_file,
      image_type,
    )

    success = cv2.imwrite(
      str(output_file),
      cleaned,
      [
        cv2.IMWRITE_JPEG_QUALITY,
        92,
      ],
    )

    logger.info(
      "cv2.imwrite RESULT: success=%s output_exists=%s output_size=%s",
      success,
      output_file.exists(),
      output_file.stat().st_size if output_file.exists() else 0,
    )

    if not success or not output_file.exists():
      raise RuntimeError(
        f"Failed to save cleaned image: {output_file}"
      )

    logger.info(
      "Cleaned image saved: %s (%sx%s)",
      output_file,
      cleaned.shape[1],
      cleaned.shape[0],
    )

    logger.info(
      "========== _clean_form_image END =========="
    )

  def _rotate_image_for_form_editing(
    self,
    input_file: Path,
    rotation_degrees: int,
  ) -> None:
    image = cv2.imread(
      str(input_file),
      cv2.IMREAD_COLOR,
    )

    if image is None:
      raise ValueError(
        f"Could not read image for rotation: {input_file}"
      )

    if rotation_degrees == 90:

      image = cv2.rotate(
        image,
        cv2.ROTATE_90_CLOCKWISE,
      )

    elif rotation_degrees == 180:

      image = cv2.rotate(
        image,
        cv2.ROTATE_180,
      )

    elif rotation_degrees == 270:

      image = cv2.rotate(
        image,
        cv2.ROTATE_90_COUNTERCLOCKWISE,
      )

    elif rotation_degrees != 0:

      raise ValueError(
        f"Unsupported image rotation: "
        f"{rotation_degrees} degrees"
      )

    success = cv2.imwrite(
      str(input_file),
      image,
      [
        cv2.IMWRITE_JPEG_QUALITY,
        92,
      ],
    )

    if not success:
      raise ValueError(
        f"Could not save rotated image: {input_file}"
      )

    logger.info(
      "Rotated image %s by %d° clockwise",
      input_file.name,
      rotation_degrees,
    )

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

Before determining any text coordinates, you MUST first determine
whether each image is correctly oriented for reading and form-field
placement.

The uploaded photograph may contain a form that has been rotated
90 degrees relative to the image.

For each image, inspect the ACTUAL FORM CONTENT, including:

- Japanese text direction
- English text direction
- form labels
- table structure
- field labels
- headings
- checkboxes
- signatures
- dates
- other visually meaningful form elements

Do NOT determine orientation solely from whether the image dimensions
are portrait or landscape.

A portrait image may contain a landscape form rotated 90 degrees.
Likewise, a landscape image may contain a portrait form rotated 90
degrees.

Determine the orientation in which the form content is naturally
readable and the form fields should be positioned.

For every image, return an "image_orientation" object containing:

- "needs_rotation": boolean
- "rotation_degrees": one of 0, 90, 180, or 270

The rotation_degrees value specifies the clockwise rotation that the
APPLICATION must apply to the uploaded image BEFORE placing any text.

Use:

0   = image is already correctly oriented
90  = rotate clockwise 90 degrees
180 = rotate clockwise 180 degrees
270 = rotate clockwise 270 degrees

If the image is already correctly oriented, return:

"needs_rotation": false
"rotation_degrees": 0

If the form is sideways, return the rotation required to make the
form naturally readable.

IMPORTANT:

Make the orientation determination BEFORE determining any x/y
coordinates.

All image-edit coordinates MUST correspond to the image AFTER the
specified rotation has been applied.

Do not return coordinates for the unrotated image when
rotation_degrees is non-zero.

After determining orientation, return the image edits.

Each image edit must contain:

- "filename": the original image filename
- "text": the exact text to place
- "x": left coordinate in pixels
- "y": top coordinate in pixels
- "width": width of the field in pixels
- "height": height of the field in pixels

Coordinates must refer to the image AFTER the application applies
rotation_degrees.

Do not resize the coordinate system.

The x/y coordinates identify the upper-left corner of the field
where the text should be placed.

The text must be placed INSIDE the corresponding blank field.

Do not fabricate fields or coordinates.

Only create an image_edit when there is enough visual evidence to
identify the correct field.

If requested information cannot be located confidently, put that
information in "missing_data" instead.

For multiple images, keep orientation information and edits
associated with the correct filename.

The image itself will be rotated and edited later by the application.
Your job is to determine the required rotation and precise placement
coordinates.

Your response MUST be valid JSON with this structure:

{
  "summary": "...",
  "completed": true,
  "files": [],
  "image_orientation": {
    "filename.jpg": {
      "needs_rotation": false,
      "rotation_degrees": 0,
    }
  },
  "image_edits": [
    {
      "filename": "filename.jpg",
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

============================================================
TABLE ROW/COLUMN INTEGRITY — CRITICAL
============================================================

When the form contains a table, you MUST first determine the
table's row and column structure before determining any
coordinates.

For worker rosters, employee lists, personnel lists, or similar
tables:

- Each worker/person occupies ONE HORIZONTAL ROW.
- Each attribute of that worker occupies the appropriate COLUMN
  within that same row.
- Never transpose the table.
- Never arrange multiple workers vertically by attribute.
- Never arrange multiple workers horizontally across columns
  unless the actual printed form is structured that way.

For example, if the form has columns:

No. | 氏名 | 氏名（ふりがな） | 職種 | 生年月日 | 電話番号

and three workers, the output MUST conceptually correspond to:

Row 1:
worker 1 No. + worker 1 name + worker 1 furigana +
worker 1 job + worker 1 birth date + worker 1 phone

Row 2:
worker 2 No. + worker 2 name + worker 2 furigana +
worker 2 job + worker 2 birth date + worker 2 phone

Row 3:
worker 3 No. + worker 3 name + worker 3 furigana +
worker 3 job + worker 3 birth date + worker 3 phone

BEFORE returning coordinates, verify for EVERY edit:

1. Which horizontal table row does this value belong to?
2. Which column does this value belong to?
3. Does the x coordinate place it inside that column?
4. Does the y coordinate place it inside that worker's row?
5. Are all values belonging to the same worker aligned
   horizontally within the same row?

If a worker has multiple values, all of those values MUST share
approximately the same row y-coordinate.

NEVER transpose rows and columns.
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
        model="gpt-5.6-luna",
        reasoning={
          "effort": "medium",
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
          input_files=input_files,
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
    input_files: list[Path],
  ) -> None:

    import re

    output_dir.mkdir(
      parents=True,
      exist_ok=True,
    )

    logger.info(
      "========== EXTRACTING CODE INTERPRETER OUTPUT FILES =========="
    )

    # ------------------------------------------------------------
    # Determine which files the agent says it created.
    #
    # The model may return either:
    #
    #   /mnt/data/example.docx
    #
    # or:
    #
    #   [example.docx](sandbox:/mnt/data/example.docx)
    #
    # Normalize both forms to the actual filename.
    # ------------------------------------------------------------

    reported_files = agent_output.get(
      "files",
      [],
    )

    logger.info(
      "Agent reported files: %s",
      reported_files,
    )

    reported_filenames = set()

    for file_entry in reported_files:

      if not file_entry:
        continue

      file_entry = str(
        file_entry
      ).strip()

      # ----------------------------------------------------------
      # Handle Markdown links such as:
      #
      # [作業員名簿_5089.docx](sandbox:/mnt/data/作業員名簿_5089.docx)
      #
      # Prefer the actual sandbox path inside the link.
      # ----------------------------------------------------------

      sandbox_match = re.search(
        r"\(sandbox:/mnt/data/([^)]+)\)",
        file_entry,
      )

      if sandbox_match:

        filename = Path(
          sandbox_match.group(1)
        ).name

      else:

        # --------------------------------------------------------
        # Handle ordinary filesystem paths.
        # --------------------------------------------------------

        filename = Path(
          file_entry
        ).name

        # --------------------------------------------------------
        # Handle a Markdown link if it wasn't a sandbox link.
        #
        # Example:
        #
        # [example.docx](...)
        # --------------------------------------------------------

        markdown_match = re.match(
          r"\[([^\]]+)\]\(",
          file_entry,
        )

        if markdown_match:

          filename = Path(
            markdown_match.group(1)
          ).name

      if not filename:
        continue

      # ----------------------------------------------------------
      # Remove accidental surrounding whitespace.
      # ----------------------------------------------------------

      filename = filename.strip()

      if not filename:
        continue

      reported_filenames.add(
        filename,
      )

    logger.info(
      "Agent reported filenames: %s",
      reported_filenames,
    )

    editable_originals = [
      f for f in input_files
      if f.suffix.lower() not in {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif"}
    ]

    originals_by_suffix: dict[str, list[Path]] = {}
    for f in editable_originals:
      originals_by_suffix.setdefault(f.suffix.lower(), []).append(f)

    # ------------------------------------------------------------
    # Find Code Interpreter containers.
    # ------------------------------------------------------------

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

    # ------------------------------------------------------------
    # Inspect each Code Interpreter container.
    # ------------------------------------------------------------

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
        file_id = getattr(container_file, "id", None)
        file_path = getattr(container_file, "path", None)
        source = getattr(container_file, "source", None)

        logger.info(
          "Container file: id=%s path=%s source=%s",
          file_id, file_path, source,
        )

        if not file_id or not file_path:
          continue

        # Only ever accept files the model actually generated. Input files
        # mounted for the model to read (source == "user") must never be
        # treated as output, even if the model's "files" list mistakenly
        # names one.
        if source != "assistant":
          logger.warning(
            "Skipping container file with source=%r (not model-generated): %s",
            source, file_path,
          )
          continue

        container_name = Path(file_path).name
        suffix = Path(container_name).suffix.lower()

        candidates = originals_by_suffix.get(suffix, [])

        if len(candidates) == 1:
          # Unambiguous: exactly one original of this type was sent in.
          # Ignore whatever name the model gave it — use the real one.
          target_name = candidates[0].name
        elif len(candidates) > 1:
          # Multiple same-suffix inputs: try to disambiguate via the
          # model's reported filename (best-effort), else log and skip
          # rather than silently mis-attributing.
          matches = [c for c in candidates if c.name in reported_filenames or container_name in reported_filenames]
          if len(matches) == 1:
            target_name = matches[0].name
          else:
            logger.warning(
              "Cannot unambiguously match generated file %s to one of %s; skipping.",
              container_name, [c.name for c in candidates],
            )
            continue
        else:
          logger.warning(
            "Generated file %s has no matching input suffix; skipping.",
            container_name,
          )
          continue

        local_path = output_dir / target_name

        logger.info(
          "Downloading generated output file: %s -> %s",
          file_path,
          local_path,
        )

        try:

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

        except Exception:

          logger.exception(
            "Failed to extract container file: %s",
            file_path,
          )

    logger.info(
      "Extracted %d output file(s): %s",
      len(extracted_files),
      extracted_files,
    )

    # ------------------------------------------------------------
    # Detect files that were reported but could not be extracted.
    # ------------------------------------------------------------

    extracted_filenames = {
      Path(
        file_path
      ).name
      for file_path in extracted_files
    }

    missing_reported_files = (
      reported_filenames
      - extracted_filenames
    )

    if missing_reported_files:

      logger.warning(
        "Agent reported output files that could not be extracted: %s",
        sorted(
          missing_reported_files,
        ),
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
      f"{input_file.stem}"
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

    applied_count = 0

    # ------------------------------------------------------------
    # Inspect AcroForm widgets
    # ------------------------------------------------------------

    widgets_by_name = {}

    for page_index in range(
      document.page_count
    ):

      page = document[
        page_index
      ]

      widgets = page.widgets()

      if not widgets:
        continue

      for widget in widgets:

        field_name = widget.field_name

        if not field_name:
          continue

        widgets_by_name.setdefault(
          field_name,
          [],
        ).append(
          (
            page_index,
            widget,
          )
        )

        logger.info(
          "Found PDF form field: page=%d name=%r type=%s value=%r",
          page_index + 1,
          field_name,
          widget.field_type,
          widget.field_value,
        )

    has_acroform = bool(
      widgets_by_name
    )

    if has_acroform:

      logger.info(
        "PDF contains %d AcroForm field name(s).",
        len(widgets_by_name),
      )

    else:

      logger.info(
        "PDF contains no AcroForm widgets. "
        "Coordinate-based PDF edits will be used.",
      )

    # ------------------------------------------------------------
    # Apply edits
    # ------------------------------------------------------------

    for edit in pdf_edits:

      logger.info(
        "PROCESSING PDF EDIT: %s",
        json.dumps(
          edit,
          ensure_ascii=False,
        ),
      )

      filename = edit.get(
        "filename",
      )

      if filename and filename != input_file.name:
        continue

      # ----------------------------------------------------------
      # AcroForm edit
      # ----------------------------------------------------------

      field_name = edit.get(
        "field_name",
      )

      if field_name:

        if field_name not in widgets_by_name:

          logger.warning(
            "AcroForm field %r was not found in %s",
            field_name,
            input_file.name,
          )

          continue

        value = edit.get(
          "value",
        )

        if value is None:

          value = edit.get(
            "text",
          )

        if value is None:

          logger.warning(
            "Skipping AcroForm edit with no value: %s",
            edit,
          )

          continue

        widgets = widgets_by_name[
          field_name
        ]

        # --------------------------------------------------------
        # Determine field type from first widget.
        #
        # Radio buttons are special because multiple widgets can
        # share the same field name and represent one logical field.
        # --------------------------------------------------------

        field_type = widgets[0][1].field_type

        # --------------------------------------------------------
        # Radio button
        # --------------------------------------------------------

        if field_type == (
          fitz.PDF_WIDGET_TYPE_RADIOBUTTON
        ):

          requested_value = str(
            value
          ).strip()

          logger.info(
            "Applying radio-button group: "
            "field=%r requested_value=%r widgets=%d",
            field_name,
            requested_value,
            len(widgets),
          )

          selected_widget = None
          selected_on_state = None

          # ------------------------------------------------------
          # Inspect every widget in the radio group.
          #
          # For PDFs like:
          #
          # /Opt [email phone]
          # /Kids [widget_email widget_phone]
          #
          # the widget order corresponds to the option order.
          #
          # The actual widget appearance values may instead be:
          #
          # email -> "0"
          # phone -> "1"
          #
          # button_states() exposes those actual appearance values.
          # ------------------------------------------------------

          for widget_index, (
            page_index,
            widget,
          ) in enumerate(
            widgets
          ):

            option_value = None

            # ----------------------------------------------------
            # Try to obtain the radio widget's actual "on" state.
            # ----------------------------------------------------

            try:

              button_states = widget.button_states()

              normal_states = (
                button_states.get(
                  "normal",
                  [],
                )
              )

              for state in normal_states:

                if str(
                  state
                ) != "Off":

                  option_value = str(
                    state
                  )

                  break

            except Exception:

              logger.exception(
                "Could not inspect radio button states: "
                "field=%r page=%d widget_index=%d",
                field_name,
                page_index + 1,
                widget_index,
              )

            # ----------------------------------------------------
            # The logical value may already equal the actual PDF
            # appearance value.
            # ----------------------------------------------------

            if (
              option_value is not None
              and requested_value == option_value
            ):

              selected_widget = widget
              selected_on_state = option_value

              logger.info(
                "Matched radio value directly: "
                "field=%r value=%r on_state=%r",
                field_name,
                requested_value,
                option_value,
              )

              break

            # ----------------------------------------------------
            # If the PDF exposes field options, use the widget
            # position to map:
            #
            #   option[0] -> widget[0]
            #   option[1] -> widget[1]
            #
            # PyMuPDF may expose these through the widget object.
            # ----------------------------------------------------

            field_value = widget.field_value

            logger.info(
              "Radio widget inspection: "
              "field=%r page=%d widget_index=%d "
              "field_value=%r option_value=%r",
              field_name,
              page_index + 1,
              widget_index,
              field_value,
              option_value,
            )

          # ------------------------------------------------------
          # Try matching against the widget's current field value
          # as a fallback.
          # ------------------------------------------------------

          if selected_widget is None:

            for (
              page_index,
              widget,
            ) in widgets:

              current_value = widget.field_value

              if (
                current_value is not None
                and str(
                  current_value
                ).strip()
                == requested_value
              ):

                selected_widget = widget

                try:

                  button_states = (
                    widget.button_states()
                  )

                  normal_states = (
                    button_states.get(
                      "normal",
                      [],
                    )
                  )

                  for state in normal_states:

                    if str(
                      state
                    ) != "Off":

                      selected_on_state = str(
                        state
                      )

                      break

                except Exception:

                  pass

                logger.info(
                  "Matched radio value against "
                  "current widget value: "
                  "field=%r value=%r",
                  field_name,
                  requested_value,
                )

                break

          # ------------------------------------------------------
          # For PDFs where the human-readable values are stored in
          # /Opt and the widget appearance states are numeric
          # ("0", "1", etc.), use the option ordering.
          #
          # PyMuPDF's widget object does not always expose /Opt
          # directly, so inspect the underlying PDF field through
          # the widget's xref.
          # ------------------------------------------------------

          if selected_widget is None:

            try:

              # Find the parent field object.
              #
              # Radio children contain /Parent pointing to the
              # logical field. The parent contains /Opt.
              widget_xref = selected_widget.xref if selected_widget else None

              if widget_xref is None:

                # Find the first radio widget xref.
                widget_xref = widgets[0][1].xref

              widget_source = document.xref_object(
                widget_xref,
                compressed=False,
              )

              parent_match = None

              import re

              parent_match = re.search(
                r"/Parent\s+(\d+)\s+0\s+R",
                widget_source,
              )

              if parent_match:

                parent_xref = int(
                  parent_match.group(1)
                )

                parent_source = document.xref_object(
                  parent_xref,
                  compressed=False,
                )

                opt_match = re.search(
                  r"/Opt\s*\[(.*?)\]",
                  parent_source,
                  re.DOTALL,
                )

                if opt_match:

                  opt_contents = (
                    opt_match.group(1)
                  )

                  opt_values = re.findall(
                    r"<FEFF([0-9A-Fa-f]+)>",
                    opt_contents,
                  )

                  decoded_options = []

                  for hex_value in opt_values:

                    try:

                      decoded_options.append(
                        bytes.fromhex(
                          hex_value
                        ).decode(
                          "utf-16-be"
                        )
                      )

                    except Exception:

                      decoded_options.append(
                        None
                      )

                  logger.info(
                    "Radio group options: "
                    "field=%r options=%r",
                    field_name,
                    decoded_options,
                  )

                  for option_index, option in enumerate(
                    decoded_options
                  ):

                    if (
                      option is None
                      or option.strip()
                      != requested_value
                    ):

                      continue

                    if option_index >= len(
                      widgets
                    ):

                      break

                    selected_page_index, selected_widget_candidate = (
                      widgets[
                        option_index
                      ]
                    )

                    selected_widget = (
                      selected_widget_candidate
                    )

                    try:

                      button_states = (
                        selected_widget.button_states()
                      )

                      normal_states = (
                        button_states.get(
                          "normal",
                          [],
                        )
                      )

                      for state in normal_states:

                        if str(
                          state
                        ) != "Off":

                          selected_on_state = str(
                            state
                          )

                          break

                    except Exception:

                      selected_on_state = None

                    logger.info(
                      "Matched radio option by /Opt ordering: "
                      "field=%r requested=%r "
                      "option_index=%d on_state=%r",
                      field_name,
                      requested_value,
                      option_index,
                      selected_on_state,
                    )

                    break

            except Exception:

              logger.exception(
                "Failed to inspect underlying PDF "
                "radio-button options: field=%r",
                field_name,
              )

          # ------------------------------------------------------
          # Apply the radio selection.
          # ------------------------------------------------------

          if selected_widget is None:

            logger.warning(
              "Could not map radio-button value %r "
              "to a widget in field %r",
              requested_value,
              field_name,
            )

            continue

          if selected_on_state is None:

            logger.warning(
              "Could not determine the on-state for "
              "radio-button field %r value %r",
              field_name,
              requested_value,
            )

            continue

          try:

            # Turn every widget in the group off first.
            for (
              page_index,
              widget,
            ) in widgets:

              try:

                widget.field_value = "Off"
                widget.update()

                logger.info(
                  "Turned radio widget off: "
                  "field=%r page=%d",
                  field_name,
                  page_index + 1,
                )

              except Exception:

                logger.exception(
                  "Failed to turn radio widget off: "
                  "field=%r page=%d",
                  field_name,
                  page_index + 1,
                )

            # Turn the requested widget on using its actual PDF
            # appearance state (for example "0" or "1").
            selected_widget.field_value = (
              selected_on_state
            )

            selected_widget.update()

            logger.info(
              "Selected radio option: "
              "field=%r requested=%r actual_on_state=%r",
              field_name,
              requested_value,
              selected_on_state,
            )

            applied_count += 1

          except Exception:

            logger.exception(
              "Failed to apply radio-button edit: %s",
              edit,
            )

          continue

        # --------------------------------------------------------
        # Non-radio AcroForm fields
        # --------------------------------------------------------

        applied_this_edit = False

        for (
          page_index,
          widget,
        ) in widgets:

          field_type = widget.field_type

          logger.info(
            "Applying AcroForm edit: "
            "page=%d field=%r type=%s value=%r",
            page_index + 1,
            field_name,
            field_type,
            value,
          )

          try:

            # ----------------------------------------------------
            # Text / multiline text
            # ----------------------------------------------------

            if field_type in (
              fitz.PDF_WIDGET_TYPE_TEXT,
            ):

              widget.field_value = str(
                value
              )

              widget.update()

              applied_this_edit = True

            # ----------------------------------------------------
            # Checkbox
            # ----------------------------------------------------

            elif field_type == (
              fitz.PDF_WIDGET_TYPE_CHECKBOX
            ):

              if isinstance(
                value,
                bool,
              ):

                checked = value

              else:

                checked = str(
                  value
                ).strip().lower() in {
                  "true",
                  "1",
                  "yes",
                  "y",
                  "on",
                  "checked",
                  "check",
                  "✓",
                  "☑",
                }

              widget.field_value = (
                checked
              )

              widget.update()

              applied_this_edit = True

            # ----------------------------------------------------
            # Combo box / dropdown
            # ----------------------------------------------------

            elif field_type == (
              fitz.PDF_WIDGET_TYPE_COMBOBOX
            ):

              widget.field_value = str(
                value
              )

              widget.update()

              applied_this_edit = True

            # ----------------------------------------------------
            # List box
            # ----------------------------------------------------

            elif field_type == (
              fitz.PDF_WIDGET_TYPE_LISTBOX
            ):

              widget.field_value = str(
                value
              )

              widget.update()

              applied_this_edit = True

            else:

              logger.warning(
                "Unsupported AcroForm field type %s "
                "for field %r",
                field_type,
                field_name,
              )

          except Exception:

            logger.exception(
              "Failed to apply AcroForm edit: %s",
              edit,
            )

        if applied_this_edit:

          applied_count += 1

        continue

      # ----------------------------------------------------------
      # Coordinate-based overlay fallback
      # ----------------------------------------------------------

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
        "Applying coordinate PDF edit: "
        "page=%s text=%r bbox=%s font_size=%s",
        page_number,
        text,
        rect,
        font_size,
      )

      if font_path:

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

      else:

        page.insert_textbox(
          rect,
          str(text),
          fontsize=font_size,
          color=(0, 0, 0),
          align=fitz.TEXT_ALIGN_CENTER,
          overlay=True,
        )

      applied_count += 1

    # ------------------------------------------------------------
    # Save completed PDF
    # ------------------------------------------------------------

    output_path = (
      output_dir
      / input_file.name
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

    logger.info(
      "========== IMAGE DOWNLOAD START =========="
    )


    for file in job.files:

      if not file.is_input:
        continue

      local_path = (
        input_dir / file.filename
      )

      logger.info(
        "DOWNLOADING INPUT FILE: "
        "filename=%s content_type=%s is_input=%s output_path=%s",
        file.filename,
        file.content_type,
        file.is_input,
        local_path,
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
        "DOWNLOADED INPUT FILE: "
        "path=%s exists=%s size=%s bytes",
        local_path,
        local_path.exists(),
        local_path.stat().st_size if local_path.exists() else None,
      )

    logger.info(
      "========== IMAGE DOWNLOAD END =========="
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

For documents that the application edits directly, return explicit
machine-readable edit instructions as required below.

For non-PDF documents that you are responsible for directly editing,
actually modify the document and save the completed file.

For PDFs, do NOT create or modify the completed PDF yourself.
Instead, return explicit machine-readable PDF edits using the
PDF instructions below. The application will apply those edits and
create the completed PDF.

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
DATABASE IDS VS. FORM VALUES — CRITICAL
============================================================

Kenchiku database IDs are INTERNAL SYSTEM IDENTIFIERS ONLY.

NEVER use a Kenchiku database UUID or internal database ID as a value
entered into the uploaded form.

This rule has ABSOLUTE PRIORITY whenever selecting a value to actually
write into a form.

Database IDs are provided in the Kenchiku data graph only so that you can
identify entities and follow relationships.

They are NOT form values.

For example, if a company has:

- internal database ID: 550e8400-e29b-41d4-a716-446655440000
- Company ID: ABC123456

and the form asks for:

「会社ID」
「Company ID」
「事業者ID」

you MUST enter:

ABC123456

You MUST NOT enter:

550e8400-e29b-41d4-a716-446655440000

Similarly, if a user, project, company, or custom object has an internal
UUID, NEVER copy that UUID into a form unless the uploaded form explicitly
requires the Kenchiku internal database UUID itself.

The following are INTERNAL identifiers and must never be entered into
forms as ordinary values:

- company database UUID
- user database UUID
- project database UUID
- custom object database UUID
- relationship record UUID
- custom field record UUID
- any other UUID or internal database primary key

When a form asks for an ID, identifier, registration number, or similar
field, first determine what TYPE of identifier the form is requesting.

Prefer an explicit human/business-facing identifier from the entity's
fields, such as:

- Company ID
- 事業者ID
- 会社ID
- 技能者ID
- 建設業許可番号
- 法人番号
- 保険番号
- 登録番号
- その他の explicitly stored business identifiers

The label on the form determines the semantic meaning of the requested
identifier.

DO NOT assume that a field labeled "ID" means the database UUID.

If the entity contains a field whose name clearly corresponds to the
identifier requested by the form, use that field's value.

For example:

Form field:
「事業者ID」

Entity data:
database UUID: 550e8400-e29b-41d4-a716-446655440000
Company ID: 1234567890

Correct form value:
1234567890

Incorrect form value:
550e8400-e29b-41d4-a716-446655440000

If the form requests an identifier and no appropriate human/business-facing
identifier exists in the available authoritative data, leave the field
unresolved and report it in missing_data.

NEVER substitute an internal database UUID merely because the form asks
for an "ID".

Internal database IDs may be used freely for:

- identifying entities
- resolving relationships
- selecting the correct entity
- reasoning about the Kenchiku graph

Internal database IDs must NOT be used as:

- form field values
- displayed company IDs
- displayed user IDs
- displayed project IDs
- registration numbers
- business identifiers
- worker identifiers
- any other human-facing document value

Before producing every form value, explicitly distinguish:

1. INTERNAL ENTITY ID
   → used only for reasoning/entity selection.

2. FORM-FACING VALUE
   → the actual value that should be written into the document.

Only the second may be entered into the form.

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
FORM-SPECIFIC INSTRUCTIONS
============================================================

The FORM DESCRIPTION may contain instructions that modify how the
identified entity's information should be entered into the form.

These instructions are authoritative for this form job and must be
followed when determining the values to enter.

The FORM DESCRIPTION may specify:

- alternate names or preferred names
- different values for specific form fields
- formatting requirements
- abbreviations
- how a name should be written
- which value to use when multiple authoritative values exist
- field-specific substitutions
- other explicit instructions about how authoritative data should be
  represented on this particular form

These instructions do NOT change the underlying Kenchiku data.

They only determine how that data should be represented or entered
for this specific form.

For example, if the FORM DESCRIPTION says:

"Fill this form out for the user Joe Smith but for the first name values
use the name Joseph instead of Joe."

Then:

- Target entity: Joe Smith
- Underlying Kenchiku data remains unchanged.
- For fields representing the person's first name, use "Joseph".
- For fields representing the person's full name, use the appropriate
  full-name representation based on the instruction.
- Do not interpret "Joseph" as identifying a different user.

The instruction applies only to this form job.

If the FORM DESCRIPTION explicitly instructs you to use a particular
value for a particular type of form field, follow that instruction
instead of automatically copying the corresponding value from
Kenchiku.

However, do not invent unrelated information.

If a FORM DESCRIPTION instruction is ambiguous, report the ambiguity
in "missing_data" rather than guessing.

FORM DESCRIPTION instructions may override the representation of
authoritative data for this form, but they do not override factual
constraints. Do not fabricate information that the description does
not explicitly provide.

============================================================
DATA ACCURACY
============================================================

Never fabricate factual information.

Do not guess factual entity-specific values such as:

- names
- addresses
- phone numbers
- historical dates
- qualifications
- licenses
- insurance information
- registration numbers
- identification numbers
- project information
- company information
- employment information

However, values explicitly permitted by the REASONABLE FORM-DERIVED
VALUES section are exceptions to this rule. In particular, a clear form
creation/preparation date SHOULD be populated using today's date.

============================================================
REASONABLE FORM-DERIVED VALUES
============================================================

The Kenchiku data graph is authoritative for factual business and
person-specific information.

However, some administrative or contextual fields have values that can be
determined directly from the form and the current processing context.

You SHOULD complete these fields when their meaning is clear.

------------------------------------------------------------
CURRENT DATE / FORM CREATION DATE — IMPORTANT
------------------------------------------------------------

Be reasonably AGGRESSIVE when filling fields that clearly represent the
date on which this form is being created, prepared, completed, or filled
out.

If a form contains a field or label such as:

- 作成日
- 作成年月日
- 作成年月日
- （年 月 日 作成）
- 年 月 日 作成
- 作成
- 作成日：
- 作成年月日：
- 作成年月日（　）
- Date Created
- Created
- Date Prepared
- Prepared Date
- Completion Date
- Date Completed

and the form does not provide a different explicit date, use TODAY'S
CURRENT DATE.

For example, if today's date is 2026年9月12日 and the form contains:

（ 年 月 日 作成 ）

fill it with:

2026年9月12日

or the equivalent formatting required by the form, such as:

2026 / 09 / 12

2026年09月12日

令和8年9月12日

Use the format that best matches the surrounding form.

A blank creation-date field should generally be completed with today's
date when the field clearly means "the date this document was created or
prepared."

Do NOT leave an obvious form-creation date blank merely because the date
does not appear in the Kenchiku data graph.

The current date is a processing-context value, not an invented
historical fact.

------------------------------------------------------------
DATE SEMANTICS
------------------------------------------------------------

Distinguish carefully between these types of dates:

1. FORM CREATION / PREPARATION DATE

Examples:
- 作成日
- 作成年月日
- （年 月 日 作成）
- Date Created
- Date Prepared

→ Use today's current date when no explicit date is provided.

2. FORM COMPLETION DATE

Examples:
- 完成日
- 完了日
- Date Completed

→ If the field clearly means the date this form is being completed,
use today's current date.

3. SUBMISSION DATE

Examples:
- 提出日
- 提出年月日
- Date Submitted
- Submission Date

→ Do NOT automatically use today's date unless the context clearly
indicates that the document is being submitted today or the form is
explicitly asking for the current submission date.

4. EVENT DATE / HISTORICAL DATE

Examples:
- 入社日
- 雇入年月日
- 生年月日
- 資格取得日
- 契約日
- 工事開始日
- 工事完了日
- 保険加入日

→ NEVER infer today's date. Use authoritative data or leave unresolved.

When the label clearly indicates document creation or preparation,
favor completing it with today's date rather than leaving it blank.

Only avoid using today's date when the field clearly represents a
different kind of date, such as a historical event date or a submission
date that cannot be established.

------------------------------------------------------------
OTHER REASONABLE DERIVATIONS
------------------------------------------------------------

You MAY also determine values when they are strongly implied by the form
itself and do not require inventing a fact about a person, company,
project, or other entity.

Examples include:

- today's date for a clear form creation/preparation field
- today's date for a clear current form completion field
- derived values such as age when a date of birth is available
- values that can be directly calculated from authoritative data
- simple formatting or representation choices required by the form

Do NOT use this rule to invent factual information about the people,
companies, projects, or other entities represented in the form.

In particular, do NOT infer or fabricate:

- names
- addresses
- phone numbers
- insurance types
- identification numbers
- qualifications
- licenses
- employment information
- project information
- company information
- historical dates
- event dates

When a field is clearly a form-creation/preparation date, however,
TODAY'S DATE SHOULD BE USED unless the form provides a different
explicit creation date.

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

Use the Code Interpreter environment to inspect the uploaded
documents and determine the required edits.

For PDFs, provide explicit machine-readable PDF edits in your output
when the application will apply those edits.

Do not assume that describing an edit means that the application has
applied it.

For non-PDF documents that you are responsible for directly editing,
actually modify the document and save the completed file.

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

For Word documents (.docx):

- The uploaded document is the actual form that must be completed.
- Use Code Interpreter to inspect and edit the document.
- Use Python and python-docx when appropriate.
- Inspect paragraphs, runs, tables, cells, headers, footers, and
  document structure as necessary.
- Identify the actual location corresponding to each requested value.
- Preserve the existing document structure and formatting.
- Preserve existing text unless the form clearly requires replacement.
- Do not recreate the document from scratch unless absolutely necessary.
- Do not flatten the document into plain text.
- Do not convert the document into a PDF as a substitute for completing
  the Word document.
- Fill the appropriate existing fields, table cells, blank lines, or
  other form locations.
- Preserve formatting such as:
  - fonts
  - font sizes
  - bold and italic formatting
  - alignment
  - paragraph spacing
  - table structure
  - cell formatting
  - borders
  - page layout
  - headers
  - footers
- Preserve Japanese text and Japanese document formatting.
- Save the completed document as a .docx file in the output directory.

Use Python and python-docx when appropriate, but first inspect the
document structure to determine how the form is constructed.

Do not assume that a blank area visible in the document corresponds
to a normal paragraph or table cell.

If the form uses Word-specific structures that python-docx cannot
safely edit, use an appropriate available mechanism rather than
recreating the document.

After editing, reopen the saved .docx and verify that it can be read
successfully and that the requested values were actually inserted.

The completed .docx file is the primary output of the task.

For PDF:

- determine whether the PDF contains AcroForm fields
- inspect the actual PDF form fields and their field names
- inspect text and page structure
- inspect scanned pages visually when necessary
- use OCR when necessary

============================================================
PDF ACROFORM FIELDS
============================================================

If the PDF contains editable AcroForm fields, use the actual AcroForm
field names as the required targets for edits whenever a corresponding
field exists.

For every AcroForm field that should be changed, return a PDF edit using
this structure:

{{
  "filename": "example.pdf",
  "field_name": "applicant.name",
  "value": "佐藤 健一"
}}

The "field_name" must be the actual field name from the PDF.

Do not invent field names.

Do not use page coordinates for an AcroForm field when the actual
AcroForm field is available.

Examples:

{{
  "filename": "sample-form.pdf",
  "field_name": "applicant.name",
  "value": "佐藤 健一"
}}

{{
  "filename": "sample-form.pdf",
  "field_name": "applicant.notes",
  "value": "現場作業員"
}}

For checkboxes, use a boolean value when possible:

{{
  "filename": "sample-form.pdf",
  "field_name": "applicant.subscribe",
  "value": true
}}

For radio buttons and dropdowns, use the actual option value represented
by the PDF field.

Only create an edit when you can reliably determine the correct value.

Preserve existing AcroForm values unless the form clearly requires
replacement.

If an AcroForm field cannot be reliably matched to the requested data,
leave it unchanged and report the missing or ambiguous information in
"missing_data".

If the PDF does not contain AcroForm fields, use coordinate-based
edits for fields that can be reliably located.

Coordinate-based PDF edits must use this structure:

{{
  "filename": "example.pdf",
  "page": 1,
  "x0": 100,
  "y0": 200,
  "x1": 300,
  "y1": 230,
  "text": "佐藤 健一"
}}

Use coordinate-based edits only when an actual editable AcroForm field
does not exist for the target field.

The PDF edit information is machine-readable input to the application.
Do not merely describe what should be filled.

When PDF edits are needed, return explicit field_name/value edits or
explicit coordinate/text edits.

Do not claim that a PDF field was updated unless you have actually
identified the corresponding field and provided an explicit edit for it.

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
ACROFORM FIELD SELECTION — HIGHEST PRIORITY
============================================================

If the uploaded PDF contains AcroForm fields, you MUST use those
AcroForm fields for editing.

If an AcroForm field exists, the PDF field name is the authoritative
machine-readable target.

You MUST output the exact field_name reported by the PDF.

Do not convert, simplify, translate, rename, or reinterpret the field
name.

For example, if the PDF reports:

applicant.name

then the edit MUST contain:

{{
  "field_name": "applicant.name"
}}

Do not output a visual description such as "the applicant name field",
"Name", "氏名", or coordinates instead of the actual field_name.

The presence of an AcroForm field takes absolute priority over
coordinate-based editing.

You MUST NOT use x0, y0, x1, y1, page coordinates, or visual text
placement for a value when a matching AcroForm field exists.

For example, if the PDF contains these fields:

- applicant.name
- applicant.notes
- applicant.subscribe
- applicant.contact
- applicant.country

and the task requires entering the worker's name, you MUST return:

{{
  "filename": "sample-form.pdf",
  "field_name": "applicant.name",
  "value": "佐藤 健一"
}}

You MUST NOT return:

{{
  "filename": "sample-form.pdf",
  "page": 1,
  "x0": 180,
  "y0": 126,
  "x1": 468,
  "y1": 148,
  "text": "佐藤 健一"
}}

The second format is WRONG when an AcroForm field exists.

For every piece of information you need to enter into the PDF:

1. First determine whether an AcroForm field corresponds to that
   information.
2. If a matching AcroForm field exists, use its EXACT field_name.
3. Return an edit using "field_name" and "value".
4. Only use coordinate-based editing when NO suitable AcroForm field
   exists anywhere in the PDF.

NEVER choose coordinate editing merely because the field is visually
located at the desired position.

The actual AcroForm field name is the authoritative target.

If you can identify the field but cannot determine its appropriate
value, do not replace it with a coordinate edit. Report the missing
information in "missing_data".

For PDF forms containing AcroForm fields, coordinate edits should
normally be ZERO unless the information genuinely has no corresponding
AcroForm field.

============================================================
ACROFORM EXAMPLE
============================================================

Suppose the uploaded PDF contains these actual AcroForm fields:

applicant.name
applicant.notes
applicant.subscribe
applicant.contact
applicant.country

If the target worker is 佐藤 健一 and the form requires the worker's
name, the required PDF edit is:

{{
  "filename": "sample-form.pdf",
  "field_name": "applicant.name",
  "value": "佐藤 健一"
}}

The model MUST NOT instead return a coordinate edit such as:

{{
  "filename": "sample-form.pdf",
  "page": 1,
  "x0": 180,
  "y0": 126,
  "x1": 468,
  "y1": 148,
  "text": "佐藤 健一"
}}

The coordinate version is incorrect because the PDF already contains
the editable AcroForm field applicant.name.

The application will apply the field_name/value instruction directly
to the AcroForm field.

Therefore, for every value that belongs to an existing AcroForm field,
the model MUST produce a field_name/value PDF edit.

============================================================
IMPORTANT EDITING RULE
============================================================

Do NOT merely describe how the form should be filled.

For non-PDF documents that you are responsible for directly editing,
actually edit the uploaded document and save the completed document.

For PDFs, do NOT create or modify the completed PDF yourself.

Instead, return the explicit machine-readable PDF edits required by the
PDF instructions above.

The application will apply those PDF edits and create the completed PDF.

Do NOT return a hypothetical table of values instead of performing the
required document processing.

For PDF jobs, the "pdf_edits" array is the primary output of the task.

For image jobs, the "image_edits" array is the primary output of the
task.

For non-PDF document jobs such as Word and Excel, the completed document
file is the primary output of the task.

============================================================
VERIFICATION
============================================================

After processing the document, verify the work that you are responsible
for performing.

For non-PDF documents that you directly edit:

1. Confirm that the completed output file exists.
2. Confirm that it can be opened and read successfully.
3. Confirm that the intended fields were populated.
4. Confirm that values are in the correct locations.
5. Confirm that Japanese text renders correctly.
6. Confirm that existing labels remain intact.
7. Confirm that existing values remain intact.
8. Confirm that no information was invented.
9. Confirm that the input file was not modified.
10. Confirm that no internal Kenchiku database UUID or internal database
    primary key was used as a human-facing form value.
11. For every field containing "ID", "番号", "識別番号", or similar
    terminology, confirm that the value corresponds to the semantic
    identifier requested by the form rather than an internal database ID.
12. Confirm that obvious form creation/preparation date fields such as
    作成日, 作成年月日, and （年 月 日 作成） were populated with today's
    date when no conflicting date was explicitly provided.

For PDFs:

1. Confirm that the PDF was inspected.
2. Confirm that each requested value was matched to the correct
   AcroForm field or coordinate location.
3. Confirm that each PDF edit is explicitly included in "pdf_edits".
4. Confirm that the field names used in AcroForm edits are the actual
   field names found in the PDF.
5. Confirm that no information was invented.
6. Do not claim that the completed PDF file was created or modified.
   The application will apply the PDF edits and create the completed
   PDF after the agent finishes.

For images:

1. Confirm that the image was inspected.
2. Confirm that each requested value was matched to the correct
   location.
3. Confirm that each image edit is explicitly included in "image_edits".
4. Confirm that no information was invented.
5. Do not claim that the final image file was created or modified.
   The application will apply the image edits after the agent finishes.

For documents where visual layout matters, visually inspect the
document before completing the task.

Pay particular attention to:

- merged cells
- row heights
- column widths
- tables
- checkboxes
- text clipping
- Japanese characters
- page breaks
- PDF field locations
- image dimensions

Do not declare that a document was successfully completed merely because
you identified the values that should be entered.

For PDFs and images, successful processing means that the required
machine-readable edits have been produced.

For directly edited documents, successful processing means that the
completed document has actually been created and verified.

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
OUTPUT FILENAMES
============================================================

For documents that you directly edit, preserve the original uploaded
filename.

Do not add suffixes such as:

- "_completed"
- "_edited"
- "_filled"
- "_processed"
- "_final"

For example:

Input:
施工体制台帳.docx

Output:
施工体制台帳.docx

The completed document should use the same filename as the uploaded
document unless the FORM DESCRIPTION explicitly instructs otherwise.

Do not create multiple versions of the same document with different
filenames.

============================================================
OUTPUT LANGUAGE — JAPANESE ONLY
============================================================

ALL model-generated output MUST be in Japanese.

This is a strict requirement. NEVER return English prose, explanations,
summaries, recommendations, status messages, or error messages.

Use Japanese for:
- "summary"
- "missing_data"
- "recommendations"
- Any descriptions of what was found or changed
- Any explanations of problems or limitations
- Any other human-readable text in the response

The machine-readable JSON keys MUST remain exactly as specified in the
output schema (for example: "summary", "completed", "files",
"missing_data", and "recommendations"). Do not translate JSON keys.

Values inside the JSON MUST be Japanese unless they are:
- File paths
- File names
- Actual document content that must be preserved exactly
- Proper nouns, names, addresses, company names, or other source data
  that are originally written in another language
- Technical identifiers such as PDF field names

If source documents contain English text, preserve the original English
when copying actual source data into the document or into a field where
the source value itself must be preserved. However, all explanations
about that data MUST be written in Japanese.

If you are uncertain how to express something in Japanese, still write
the response in Japanese. Do NOT fall back to English.

Before returning your final response, verify that all human-readable
output is Japanese.

============================================================
FINAL RESPONSE
============================================================

Return ONLY valid JSON.

All human-readable JSON values MUST be written in Japanese.

The JSON keys below MUST remain in English exactly as shown.

Success example:

{{
  "summary": "フォームを正常に処理しました。",
  "completed": true,
  "files": [
    "output/example.xlsx"
  ],
  "missing_data": [],
  "recommendations": []
}}

Failure example:

{{
  "summary": "フォームを安全に処理できませんでした。",
  "completed": false,
  "files": [],
  "missing_data": [
    "申請者の住所が確認できませんでした。"
  ],
  "recommendations": [
    "申請者の住所を確認してから再度処理してください。"
  ]
}}

NEVER produce an English value such as:
"Unable to process the form."
Instead, write:
"フォームを安全に処理できませんでした。"
"""
