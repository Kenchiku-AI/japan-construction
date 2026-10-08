import json
import logging
import mimetypes
import tempfile
from pathlib import Path
import cv2
import numpy as np
import math
import re
import shutil
from typing import Any
from openai import OpenAI
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageDraw, ImageFont
import base64
import os
import fitz
import io

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

        for input_file in image_input_files:

          rotation_degrees = self._detect_image_orientation(
            input_file,
          )

          if rotation_degrees != 0:

            logger.info(
              "Rotating %s by %d° clockwise before running "
              "the form-filling agent",
              input_file.name,
              rotation_degrees,
            )

            self._rotate_image_for_form_editing(
              input_file=input_file,
              rotation_degrees=rotation_degrees,
            )

          else:

            logger.info(
              "No rotation needed for %s",
              input_file.name,
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
          image_edits = agent_output.get(
            "image_edits",
            [],
          )

          logger.info(
            "Image form returned %d edit(s)",
            len(image_edits),
          )

          for input_file in image_input_files:

            pdf_path = getattr(self, "_image_pdf_paths", {}).get(
              input_file.name,
            )

            if pdf_path is not None:
              self._apply_pdf_edits(
                input_file=pdf_path,
                pdf_edits=agent_output.get("pdf_edits", []),
                output_dir=output_dir,
              )
            else:
              self._build_editable_pdf_from_image(
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
  

  @staticmethod
  def _order_quad_points(
    points: np.ndarray,
  ) -> np.ndarray:
    """
    Return quadrilateral points in this order:

      top-left
      top-right
      bottom-right
      bottom-left
    """
    points = np.asarray(
      points,
      dtype=np.float32,
    )

    if points.shape != (4, 2):
      raise ValueError(
        f"Expected 4 points, got shape {points.shape}"
      )

    ordered = np.zeros(
      (4, 2),
      dtype=np.float32,
    )

    sums = points.sum(axis=1)
    diffs = np.diff(
      points,
      axis=1,
    ).reshape(-1)

    ordered[0] = points[np.argmin(sums)]
    ordered[2] = points[np.argmax(sums)]
    ordered[1] = points[np.argmin(diffs)]
    ordered[3] = points[np.argmax(diffs)]

    return ordered

  @staticmethod
  def _inset_quad_corners(
    corners: np.ndarray,
    inset_ratio: float = 0.02,
  ) -> np.ndarray:
    """
    Shrink a quadrilateral inward, toward its own centroid, by
    inset_ratio of its average side length.

    This keeps the physical paper/form edge line itself just
    outside the cropped output, so the perspective-corrected image
    doesn't show a dark border.
    """
    corners = np.asarray(corners, dtype=np.float32)

    centroid = corners.mean(axis=0)

    top_width = np.linalg.norm(corners[1] - corners[0])
    bottom_width = np.linalg.norm(corners[2] - corners[3])
    left_height = np.linalg.norm(corners[3] - corners[0])
    right_height = np.linalg.norm(corners[2] - corners[1])

    average_side = float(
      np.mean(
        [top_width, bottom_width, left_height, right_height]
      )
    )

    inset_amount = average_side * inset_ratio

    inset_corners = np.zeros_like(corners)

    for index, corner in enumerate(corners):
      direction = centroid - corner
      distance = np.linalg.norm(direction)

      if distance < 1e-6:
        inset_corners[index] = corner
        continue

      unit_direction = direction / distance
      inset_corners[index] = corner + unit_direction * inset_amount

    return inset_corners


  def _perspective_crop(
    self,
    image: np.ndarray,
    corners: np.ndarray,
  ) -> np.ndarray:
    """
    Perspective-correct the detected form boundary.

    The input corners must be ordered:

      top-left
      top-right
      bottom-right
      bottom-left
    """
    corners = self._order_quad_points(
      corners
    )

    top_width = np.linalg.norm(
      corners[1] - corners[0]
    )

    bottom_width = np.linalg.norm(
      corners[2] - corners[3]
    )

    left_height = np.linalg.norm(
      corners[3] - corners[0]
    )

    right_height = np.linalg.norm(
      corners[2] - corners[1]
    )

    output_width = max(
      1,
      int(
        round(
          max(
            top_width,
            bottom_width,
          )
        )
      ),
    )

    output_height = max(
      1,
      int(
        round(
          max(
            left_height,
            right_height,
          )
        )
      ),
    )

    destination = np.array(
      [
        [0, 0],
        [output_width - 1, 0],
        [
          output_width - 1,
          output_height - 1,
        ],
        [
          0,
          output_height - 1,
        ],
      ],
      dtype=np.float32,
    )

    transform = cv2.getPerspectiveTransform(
      corners,
      destination,
    )

    warped = cv2.warpPerspective(
      image,
      transform,
      (
        output_width,
        output_height,
      ),
      flags=cv2.INTER_CUBIC,
      borderMode=cv2.BORDER_REPLICATE,
    )

    logger.info(
      "Perspective correction: "
      "%dx%d -> %dx%d",
      image.shape[1],
      image.shape[0],
      warped.shape[1],
      warped.shape[0],
    )

    return warped


  @staticmethod
  def _quad_edge_contrast(gray, quad, band=6, samples=60):
    """Mean |inside - outside| brightness along the quad's edges."""
    h, w = gray.shape[:2]
    centroid = quad.mean(axis=0)
    diffs = []
    total = 0

    for i in range(4):
      p, q = quad[i], quad[(i + 1) % 4]
      edge = q - p
      length = float(np.linalg.norm(edge))
      if length < 1:
        continue
      normal = np.array([-edge[1], edge[0]]) / length
      mid = (p + q) / 2
      if np.dot(normal, centroid - mid) < 0:
        normal = -normal
      for t in np.linspace(0.05, 0.95, samples):
        total += 1
        pt = p + edge * t
        a = pt + normal * band
        b = pt - normal * band
        ax, ay = int(round(a[0])), int(round(a[1]))
        bx, by = int(round(b[0])), int(round(b[1]))
        if not (0 <= ax < w and 0 <= ay < h and 0 <= bx < w and 0 <= by < h):
          continue
        diffs.append(abs(int(gray[ay, ax]) - int(gray[by, bx])))

    if total == 0 or len(diffs) < 0.4 * total:
      return 0.0
    return float(np.mean(diffs))

  def _detect_paper_corners(self, image):
    """
    Find the 4 corners of the sheet of paper in a photo.
    Returns float32 (4,2) ordered TL,TR,BR,BL in original coordinates,
    or None when no confident sheet is found (or it fills the frame).
    """
    h, w = image.shape[:2]
    scale = min(1.0, 1000.0 / max(h, w))
    small = (
      cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
      if scale < 1.0 else image.copy()
    )
    sh, sw = small.shape[:2]
    img_area = float(sh * sw)

    blur = cv2.GaussianBlur(small, (7, 7), 0)
    gray = cv2.cvtColor(blur, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(blur, cv2.COLOR_BGR2HSV)

    masks = []

    _, m1 = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    masks.append(m1)

    v, s_ = hsv[:, :, 2], hsv[:, :, 1]
    _, vm = cv2.threshold(v, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if (vm > 0).any():
      sat_thr = min(110, int(np.percentile(s_[vm > 0], 80)) + 15)
    else:
      sat_thr = 80
    masks.append((((vm > 0) & (s_ <= sat_thr)) * 255).astype(np.uint8))

    local = cv2.adaptiveThreshold(
      gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY,
      max(51, (min(sh, sw) // 4) | 1), -8,
    )
    masks.append(local)

    edges = cv2.Canny(gray, 30, 100)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    masks.append(cv2.morphologyEx(
      edges, cv2.MORPH_CLOSE, np.ones((21, 21), np.uint8)))

    k = max(9, int(0.02 * max(sh, sw)) | 1)
    best, best_score = None, 0.0

    for mask in masks:
      m = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
      m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
      contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
      contours = sorted(contours, key=cv2.contourArea, reverse=True)[:4]

      for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < 0.15 * img_area:
          continue

        hull = cv2.convexHull(cnt)
        hull_area = cv2.contourArea(hull)
        peri = cv2.arcLength(hull, True)
        approx = cv2.approxPolyDP(hull, 0.02 * peri, True)

        if len(approx) == 4:
          quad = approx.reshape(4, 2).astype(np.float32)
          rectangularity = 1.0
        else:
          rect = cv2.minAreaRect(hull)
          quad = cv2.boxPoints(rect).astype(np.float32)
          rect_area = rect[1][0] * rect[1][1]
          if rect_area <= 0:
            continue
          rectangularity = hull_area / rect_area
          if rectangularity < 0.88:
            continue

        quad = self._order_quad_points(quad)
        if not cv2.isContourConvex(quad.reshape(-1, 1, 2)):
          continue

        sides = [np.linalg.norm(quad[(i + 1) % 4] - quad[i]) for i in range(4)]
        if min(sides) < 0.2 * min(sh, sw):
          continue
        if max(sides[0], sides[2]) / max(1.0, min(sides[0], sides[2])) > 1.8:
          continue
        if max(sides[1], sides[3]) / max(1.0, min(sides[1], sides[3])) > 1.8:
          continue

        quad_area = cv2.contourArea(quad.reshape(-1, 1, 2))
        ratio = quad_area / img_area
        if ratio < 0.2 or ratio > 0.97:
          continue

        contrast = self._quad_edge_contrast(gray, quad)
        if contrast < 8.0:
          continue

        # The surroundings must look different from the paper itself;
        # otherwise this is just printed content on a full-frame page.
        qmask = np.zeros((sh, sw), np.uint8)
        cv2.fillConvexPoly(qmask, quad.astype(np.int32), 255)
        ring_px = max(15, int(0.04 * max(sh, sw)))
        ring = cv2.dilate(qmask, np.ones((ring_px, ring_px), np.uint8)) & ~qmask
        if int((ring > 0).sum()) >= 0.02 * img_area:
          lab = cv2.cvtColor(blur, cv2.COLOR_BGR2LAB).astype(np.float32)
          inside = np.median(lab[qmask > 0], axis=0)
          outside = np.median(lab[ring > 0], axis=0)
          if float(np.linalg.norm(inside - outside)) < 14.0:
            continue

        score = ratio * rectangularity * min(1.0, contrast / 30.0)
        if score > best_score:
          best, best_score = quad, score

    if best is None:
      return None

    logger.info(
      "Paper corners detected: score=%.3f quad=%s", best_score, best.tolist()
    )
    return (best / scale).astype(np.float32)

  def _form_line_clusters(self, gray):
    """Long, merged straight lines (near-horizontal and near-vertical)."""
    h, w = gray.shape
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(blur, 40, 120)
    segs = cv2.HoughLinesP(
      edges, 1, np.pi / 360, threshold=60,
      minLineLength=int(0.12 * min(h, w)),
      maxLineGap=int(0.02 * max(h, w)),
    )
    if segs is None:
      return [], []

    fams = {"h": [], "v": []}
    for x1, y1, x2, y2 in segs.reshape(-1, 4):
      ang = math.degrees(math.atan2(y2 - y1, x2 - x1))
      while ang >= 90:
        ang -= 180
      while ang < -90:
        ang += 180
      if abs(ang) <= 20:
        fams["h"].append((x1, y1, x2, y2, ang))
      elif abs(abs(ang) - 90) <= 20:
        fams["v"].append((x1, y1, x2, y2, ang if ang > 0 else ang + 180))

    def merge(items):
      n = len(items)
      parent = list(range(n))

      def find(i):
        while parent[i] != i:
          parent[i] = parent[parent[i]]
          i = parent[i]
        return i

      def dist_to_line(px, py, it):
        x1, y1, x2, y2, _ = it
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy) or 1.0
        return abs(dy * (px - x1) - dx * (py - y1)) / length

      for i in range(n):
        for j in range(i + 1, n):
          a, b = items[i], items[j]
          da = abs(a[4] - b[4])
          if min(da, 180 - da) > 1.2:
            continue
          if (
            dist_to_line((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, a) > 6
            or dist_to_line((a[0] + a[2]) / 2, (a[1] + a[3]) / 2, b) > 6
          ):
            continue
          parent[find(i)] = find(j)

      groups = {}
      for i in range(n):
        groups.setdefault(find(i), []).append(items[i])

      lines = []
      for grp in groups.values():
        pts = np.array(
          [[g[0], g[1]] for g in grp] + [[g[2], g[3]] for g in grp],
          dtype=np.float32,
        )
        vx, vy, x0, y0 = cv2.fitLine(
          pts, cv2.DIST_L2, 0, 0.01, 0.01
        ).flatten()
        d = np.array([vx, vy])
        t = (pts - np.array([x0, y0])) @ d
        p0 = np.array([x0, y0]) + d * t.min()
        p1 = np.array([x0, y0]) + d * t.max()
        lines.append({
          "p0": p0,
          "p1": p1,
          "length": float(t.max() - t.min()),
          "mid": (p0 + p1) / 2,
          "angle": math.degrees(math.atan2(p1[1] - p0[1], p1[0] - p0[0])),
        })
      return lines

    return merge(fams["h"]), merge(fams["v"])

  @staticmethod
  def _line_intersection(a, b):
    p, r = a["p0"], a["p1"] - a["p0"]
    q, s = b["p0"], b["p1"] - b["p0"]
    den = r[0] * s[1] - r[1] * s[0]
    if abs(den) < 1e-9:
      return None
    t = ((q[0] - p[0]) * s[1] - (q[1] - p[1]) * s[0]) / den
    return p + r * t

  def _detect_rule_quad(self, image):
    """
    Four corners formed by the outermost long printed horizontal and
    vertical rules (original coordinates, TL,TR,BR,BL), or None.
    """
    h0, w0 = image.shape[:2]
    scale = min(1.0, 1600.0 / max(h0, w0))
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if scale < 1.0:
      gray = cv2.resize(
        gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA
      )
    h, w = gray.shape

    hl, vl = self._form_line_clusters(gray)

    hl = [
      l for l in hl
      if l["length"] >= 0.3 * w and 0.03 * h < l["mid"][1] < 0.97 * h
    ]
    vl = [
      l for l in vl
      if l["length"] >= 0.3 * h and 0.03 * w < l["mid"][0] < 0.97 * w
    ]
    if len(hl) < 2 or len(vl) < 2:
      return None

    def keep_dominant(lines, tol):
      ang = np.array([
        l["angle"] if abs(l["angle"]) < 90 else l["angle"] - 180
        for l in lines
      ])
      wts = np.array([l["length"] for l in lines])
      order = np.argsort(ang)
      med = ang[order][np.searchsorted(np.cumsum(wts[order]), wts.sum() / 2)]
      return [l for l, a in zip(lines, ang) if abs(a - med) <= tol]

    hl = keep_dominant(hl, 4.0)
    for l in vl:
      l["angle"] = l["angle"] - 90 if l["angle"] > 0 else l["angle"] + 90
    vl = keep_dominant(vl, 4.0)
    if len(hl) < 2 or len(vl) < 2:
      return None

    top = min(hl, key=lambda l: l["mid"][1])
    bottom = max(hl, key=lambda l: l["mid"][1])
    left = min(vl, key=lambda l: l["mid"][0])
    right = max(vl, key=lambda l: l["mid"][0])

    if bottom["mid"][1] - top["mid"][1] < 0.25 * h:
      return None
    if right["mid"][0] - left["mid"][0] < 0.25 * w:
      return None

    pts = [
      self._line_intersection(top, left),
      self._line_intersection(top, right),
      self._line_intersection(bottom, right),
      self._line_intersection(bottom, left),
    ]
    if any(p is None for p in pts):
      return None

    quad = np.array(pts, dtype=np.float32)
    if not cv2.isContourConvex(quad.reshape(-1, 1, 2)):
      return None
    if (quad < -0.2 * max(h, w)).any() or (quad > 1.2 * max(h, w)).any():
      return None

    return (quad / scale).astype(np.float32)

  def _rectify_with_rules(self, image):
    """
    Remove tilt/perspective so the printed rules are exactly horizontal
    and vertical. The whole image is warped (nothing is cut away except
    blank wedges the warp creates). Returns (image, applied).
    """
    quad = self._detect_rule_quad(image)
    if quad is None:
      return image, False

    tl, tr, br, bl = quad
    w_top, w_bot = np.linalg.norm(tr - tl), np.linalg.norm(br - bl)
    h_left, h_right = np.linalg.norm(bl - tl), np.linalg.norm(br - tr)

    def tilt(p, q, axis):
      d = q - p
      a = abs(math.degrees(math.atan2(d[1], d[0])))
      a = min(a, 180 - a)
      return a if axis == "h" else abs(90 - a)

    skew = max(
      tilt(tl, tr, "h"), tilt(bl, br, "h"),
      tilt(tl, bl, "v"), tilt(tr, br, "v"),
    )
    keystone = max(
      abs(w_top - w_bot) / max(w_top, w_bot),
      abs(h_left - h_right) / max(h_left, h_right),
    )

    if skew < 0.35 and keystone < 0.015:
      return image, False

    ow, oh = max(w_top, w_bot), max(h_left, h_right)
    c = quad.mean(axis=0)
    dst = np.array([
      [c[0] - ow / 2, c[1] - oh / 2], [c[0] + ow / 2, c[1] - oh / 2],
      [c[0] + ow / 2, c[1] + oh / 2], [c[0] - ow / 2, c[1] + oh / 2],
    ], dtype=np.float32)

    H = cv2.getPerspectiveTransform(quad, dst)
    h0, w0 = image.shape[:2]
    corners = np.array(
      [[0, 0], [w0, 0], [w0, h0], [0, h0]], dtype=np.float32
    )
    wc = cv2.perspectiveTransform(corners.reshape(-1, 1, 2), H).reshape(-1, 2)

    # Largest axis-aligned rectangle fully inside the warped frame.
    ix0, ix1 = max(wc[0][0], wc[3][0]), min(wc[1][0], wc[2][0])
    iy0, iy1 = max(wc[0][1], wc[1][1]), min(wc[2][1], wc[3][1])

    margin = 0.01 * max(ow, oh)
    contains_rules = (
      ix0 <= dst[0][0] - margin and ix1 >= dst[2][0] + margin
      and iy0 <= dst[0][1] - margin and iy1 >= dst[2][1] + margin
    )

    if contains_rules and ix1 > ix0 and iy1 > iy0:
      x0, y0, x1, y1 = ix0, iy0, ix1, iy1
    else:
      x0, y0 = wc[:, 0].min(), wc[:, 1].min()
      x1, y1 = wc[:, 0].max(), wc[:, 1].max()

    T = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]], dtype=np.float64)
    out = cv2.warpPerspective(
      image, T @ H, (int(round(x1 - x0)), int(round(y1 - y0))),
      flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT,
      borderValue=(255, 255, 255),
    )

    logger.info(
      "Rectified with printed rules: skew=%.2f deg keystone=%.3f",
      skew, keystone,
    )
    return out, True

  @staticmethod
  def _trim_dark_borders(image, max_frac=0.10):
    """Trim dark strips at the image edges (table/background wedges)."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    centre = float(np.median(gray[h // 4: 3 * h // 4, w // 4: 3 * w // 4]))
    limit = 0.72 * centre
    step = max(2, int(0.004 * max(h, w)))

    def trim(profile, total):
      n, cap = 0, int(max_frac * total)
      while n < cap and profile(n, step) < limit:
        n += step
      return n

    top = trim(lambda n, s: gray[n:n + s, :].mean(), h)
    bottom = trim(lambda n, s: gray[h - n - s:h - n, :].mean(), h)
    left = trim(lambda n, s: gray[:, n:n + s].mean(), w)
    right = trim(lambda n, s: gray[:, w - n - s:w - n].mean(), w)

    return image[top:h - bottom, left:w - right]


  def _clean_form_image(
    self,
    input_file: Path,
    output_file: Path,
  ) -> None:
    """
    Crop a photographed form to the sheet's four corners (when visible),
    straighten it using the printed rules, trim dark edges, and whiten.
    """
    image = cv2.imread(str(input_file))

    if image is None:
      raise ValueError(f"Could not read image: {input_file}")

    # 1. Crop to the paper's four corners (skipped when the paper fills
    #    the frame or no confident sheet is found).
    corners = self._detect_paper_corners(image)

    if corners is not None:
      corners = self._inset_quad_corners(corners, inset_ratio=0.004)
      logger.info("Cropping to paper corners: %s", corners.tolist())
      image = self._perspective_crop(image, corners)
    else:
      logger.info("No paper corners found; keeping the full frame")

    # 2. Straighten using the printed rules, then trim dark edge strips.
    image, rectified = self._rectify_with_rules(image)
    image = self._trim_dark_borders(image)

    # Fallback: small residual deskew when no rule quad was usable.
    if not rectified:
      gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
      edges = cv2.Canny(gray, 50, 150, apertureSize=3)
      lines = cv2.HoughLinesP(
        edges, rho=1, theta=np.pi / 180,
        threshold=max(50, int(min(image.shape[:2]) * 0.10)),
        minLineLength=max(50, int(min(image.shape[:2]) * 0.20)),
        maxLineGap=max(10, int(min(image.shape[:2]) * 0.03)),
      )

      if lines is not None:
        angles = []
        for x1, y1, x2, y2 in lines.reshape(-1, 4):
          dx, dy = float(x2 - x1), float(y2 - y1)
          if abs(dx) < 1e-6:
            continue
          angle = math.degrees(math.atan2(dy, dx))
          while angle >= 90:
            angle -= 180
          while angle < -90:
            angle += 180
          if abs(angle) <= 10.0:
            angles.append(angle)

        if angles:
          median_angle = float(np.median(angles))
          if 0.25 <= abs(median_angle) <= 10.0:
            height, width = image.shape[:2]
            matrix = cv2.getRotationMatrix2D(
              (width / 2.0, height / 2.0), median_angle, 1.0
            )
            image = cv2.warpAffine(
              image, matrix, (width, height), flags=cv2.INTER_CUBIC,
              borderMode=cv2.BORDER_REPLICATE,
            )

    # 3. Limit very large images.
    height, width = image.shape[:2]
    if max(width, height) > 3000:
      f = 3000 / max(width, height)
      image = cv2.resize(
        image, (max(1, int(width * f)), max(1, int(height * f))),
        interpolation=cv2.INTER_AREA,
      )

    # 4. Whitening / contrast enhancement.
    image = cv2.fastNlMeansDenoisingColored(
      image, None, h=7, hColor=7, templateWindowSize=7, searchWindowSize=21
    )
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    background = cv2.GaussianBlur(l_channel, (0, 0), 31)
    l_channel = cv2.divide(l_channel, background, scale=255)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    l_channel = clahe.apply(l_channel)
    image = cv2.cvtColor(
      cv2.merge((l_channel, a_channel, b_channel)), cv2.COLOR_LAB2BGR
    )

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    h_ch, s_ch, v_ch = cv2.split(hsv)
    s_ch = cv2.multiply(s_ch, 0.6).astype(np.uint8)
    mask = v_ch > 200
    v_ch[mask] = np.clip(
      v_ch[mask].astype(np.int32) + 25, 0, 255
    ).astype(np.uint8)
    image = cv2.cvtColor(cv2.merge((h_ch, s_ch, v_ch)), cv2.COLOR_HSV2BGR)

    output_file.parent.mkdir(parents=True, exist_ok=True)

    if not cv2.imwrite(
      str(output_file), image, [cv2.IMWRITE_JPEG_QUALITY, 95]
    ):
      raise ValueError(f"Could not write cleaned image: {output_file}")


  def _text_axis_prior(self, image):
    """0 if text lines run horizontally, 90 if vertically, None if unsure."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    s = 1200.0 / max(gray.shape)
    if s < 1.0:
      gray = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    h, w = gray.shape
    ink = cv2.adaptiveThreshold(
      gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 12)
    hl = cv2.morphologyEx(
      ink, cv2.MORPH_OPEN,
      cv2.getStructuringElement(cv2.MORPH_RECT, (max(15, w // 15), 1)))
    vl = cv2.morphologyEx(
      ink, cv2.MORPH_OPEN,
      cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(15, h // 15))))
    lines = cv2.dilate(cv2.bitwise_or(hl, vl), np.ones((5, 5), np.uint8))
    text = cv2.bitwise_and(ink, cv2.bitwise_not(lines)).astype(np.float32) / 255.0
    rows, cols = text.sum(axis=1), text.sum(axis=0)
    if rows.mean() < 1e-6 or cols.mean() < 1e-6:
      return None
    ratio = (rows.std() / rows.mean()) / max(cols.std() / cols.mean(), 1e-6)
    if ratio > 1.2:
      return 0
    if ratio < 1 / 1.2:
      return 90
    return None

  def _orientation_contact_sheet(self, image, order, cell=800):
    """2x2 sheet of the image rotated clockwise by each angle in `order`."""
    rot_flags = {
      0: None,
      90: cv2.ROTATE_90_CLOCKWISE,
      180: cv2.ROTATE_180,
      270: cv2.ROTATE_90_COUNTERCLOCKWISE,
    }
    sheet = np.full((cell * 2, cell * 2, 3), 255, np.uint8)

    for i, degrees in enumerate(order):
      rotated = (
        image if rot_flags[degrees] is None
        else cv2.rotate(image, rot_flags[degrees])
      )
      h, w = rotated.shape[:2]
      f = (cell - 20) / max(h, w)
      thumb = cv2.resize(
        rotated, (max(1, int(w * f)), max(1, int(h * f))),
        interpolation=cv2.INTER_AREA)
      th, tw = thumb.shape[:2]
      ox = (i % 2) * cell + (cell - tw) // 2
      oy = (i // 2) * cell + (cell - th) // 2
      sheet[oy:oy + th, ox:ox + tw] = thumb
      x0, y0 = (i % 2) * cell, (i // 2) * cell
      cv2.rectangle(sheet, (x0 + 4, y0 + 4), (x0 + cell - 4, y0 + cell - 4),
                    (180, 180, 180), 2)
      cv2.rectangle(sheet, (x0 + 8, y0 + 8), (x0 + 78, y0 + 78),
                    (255, 255, 255), -1)
      cv2.putText(sheet, "ABCD"[i], (x0 + 18, y0 + 66),
                  cv2.FONT_HERSHEY_SIMPLEX, 2.2, (0, 0, 255), 5, cv2.LINE_AA)

    return sheet

  def _detect_image_orientation(self, input_file: Path) -> int:
    """
    Clockwise rotation (0/90/180/270) that makes the form upright.

    The model is shown all four rotations side by side (labelled A-D) and
    picks the upright one. Up to three votes with differently ordered
    sheets cancel position bias; a deterministic text-axis check breaks
    ties.
    """
    image = cv2.imread(str(input_file))

    if image is None:
      logger.warning("Orientation: could not read %s", input_file)
      return 0

    orders = [
      [0, 90, 180, 270],
      [270, 180, 90, 0],
      [180, 0, 270, 90],
    ]

    prompt = (
      "This sheet shows the SAME photo of a printed Japanese construction "
      "form rotated four different ways, labelled A, B, C and D. Exactly "
      "one of them is upright: the printed text reads normally from left "
      "to right, the form title and header are at the top, and the text "
      "is neither sideways nor upside down (and not mirrored). Judge by "
      "the actual printed text. Return ONLY JSON like {\"upright\": \"B\"}."
    )

    votes = []

    for order in orders:
      try:
        sheet = self._orientation_contact_sheet(image, order)
        ok, encoded = cv2.imencode(
          ".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 88]
        )
        if not ok:
          continue

        data_url = (
          "data:image/jpeg;base64,"
          + base64.b64encode(encoded.tobytes()).decode()
        )

        response = self.openai.responses.create(
          model="gpt-5.6-luna",
          input=[{
            "role": "user",
            "content": [
              {"type": "input_text", "text": prompt},
              {"type": "input_image", "image_url": data_url,
               "detail": "high"},
            ],
          }],
        )

        text = (getattr(response, "output_text", "") or "").strip()
        text = re.sub(
          r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE
        )
        letter = str(json.loads(text).get("upright", "")).strip().upper()[:1]

        if letter in ("A", "B", "C", "D"):
          votes.append(order["ABCD".index(letter)])

      except Exception:
        logger.exception("Orientation vote failed for %s", input_file)

      if len(votes) >= 2 and votes[0] == votes[1]:
        break

    logger.info("Orientation votes for %s: %s", input_file.name, votes)

    if votes:
      winner = max(set(votes), key=votes.count)
      if votes.count(winner) >= 2 or len(votes) == 1:
        return winner

    prior = self._text_axis_prior(image)
    candidates = [v for v in votes if prior is None or v % 180 == prior]

    return candidates[0] if candidates else 0


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


  _IMG_PAGE_LONG_EDGE_PT = 1190.0

  def _image_to_pdf(self, image_path: Path, work_dir: Path):
    """One-page PDF with the image as background. Returns (path, w_pt, h_pt)."""
    work_dir.mkdir(parents=True, exist_ok=True)

    with Image.open(image_path) as im:
      w, h = im.size

    scale = self._IMG_PAGE_LONG_EDGE_PT / max(w, h)
    pw, ph = w * scale, h * scale

    doc = fitz.open()
    page = doc.new_page(width=pw, height=ph)
    page.insert_image(fitz.Rect(0, 0, pw, ph), filename=str(image_path))

    pdf_path = work_dir / f"{image_path.stem}.pdf"
    doc.save(pdf_path, garbage=3, deflate=True)
    doc.close()

    return pdf_path, pw, ph

  def _image_layout(self, image_path: Path, page_w: float, page_h: float):
    """
    Detect printed rules, enclosed cells and loose text regions in an
    image. Everything is returned in PDF points of the page that will
    carry the image.
    """
    gray = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
      return None

    f = min(1.0, 2000.0 / max(gray.shape))
    if f < 1.0:
      gray = cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)

    H, W = gray.shape
    ppx, ppy = W / page_w, H / page_h

    block = max(15, int(0.02 * max(H, W)) | 1)
    ink = cv2.adaptiveThreshold(
      gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV,
      block, 10)

    # drop speckle
    n, labels, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    keep = np.zeros(n, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= 4
    ink = (keep[labels] * 255).astype(np.uint8)

    gap = max(5, int(0.005 * W))
    Lh = max(30, int(0.03 * W))
    Lv = max(20, int(0.012 * H))

    def rules(close_k, open_k, horizontal):
      m = cv2.morphologyEx(
        ink, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, close_k))
      m = cv2.morphologyEx(
        m, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, open_k))
      cnt, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
      out = []
      pad = 3

      for i in range(1, cnt):
        x, y, w, h, _a = st[i]
        thickness, length = (h, w) if horizontal else (w, h)

        if thickness > 8 or length < (Lh if horizontal else Lv):
          continue

        band = ink[y:y + h, x:x + w]
        cov = float((band.max(axis=0 if horizontal else 1) > 0).mean())
        if cov < 0.8:
          continue

        # Short rules must be solid; only long ones may be dotted.
        long_min = (0.03 * W) if horizontal else (0.03 * H)
        if length < long_min and cov < 0.93:
          continue

        # A real rule is isolated on both sides; slivers of text are not.
        if horizontal:
          a = ink[max(0, y - pad):y, x:x + w]
          b = ink[y + h:y + h + pad, x:x + w]
        else:
          a = ink[y:y + h, max(0, x - pad):x]
          b = ink[y:y + h, x + w:x + w + pad]
        side_a = float((a > 0).mean()) if a.size else 0.0
        side_b = float((b > 0).mean()) if b.size else 0.0
        if max(side_a, side_b) > 0.14:
          continue

        # dotted = many separate ink runs along the rule
        line = (band.max(axis=0 if horizontal else 1) > 0).astype(np.int8)
        runs = int((np.diff(line) == 1).sum()) + int(line[0])
        dotted = runs >= 6 * (length / 100.0) and cov < 0.985

        out.append((i, x, y, w, h, dotted))

      return lab, out

    hlab, hrules = rules((gap, 1), (Lh, 1), True)
    vlab, vrules = rules((1, gap), (1, Lv), False)

    rule_mask = np.zeros_like(ink)
    solid_h, dot_h, solid_v, dot_v = [], [], [], []

    for i, x, y, w, h, dotted in hrules:
      rule_mask[hlab == i] = 255
      seg = ((y + h / 2) / ppy, x / ppx, (x + w) / ppx)
      (dot_h if dotted else solid_h).append(seg)

    for i, x, y, w, h, dotted in vrules:
      rule_mask[vlab == i] = 255
      seg = ((x + w / 2) / ppx, y / ppy, (y + h) / ppy)
      (dot_v if dotted else solid_v).append(seg)

    text_ink = cv2.bitwise_and(
      ink,
      cv2.bitwise_not(
        cv2.dilate(rule_mask, np.ones((3, 3), np.uint8), iterations=2)
      ),
    )

    # cells via the same flood fill used for vector PDFs
    tmp = fitz.open()
    page = tmp.new_page(width=page_w, height=page_h)
    cells = self._detect_cells(page, solid_h, solid_v, dot_h, dot_v)
    tmp.close()

    def to_px(r, inset=0):
      return (
        max(0, int(r.x0 * ppx) + inset), max(0, int(r.y0 * ppy) + inset),
        min(W, int(r.x1 * ppx) - inset), min(H, int(r.y1 * ppy) - inset),
      )

    cell_has_ink = []
    for r in cells:
      x0, y0, x1, y1 = to_px(r, 2)
      roi = text_ink[y0:y1, x0:x1]
      cell_has_ink.append(
        bool(roi.size and (roi > 0).sum() >= max(25, 0.003 * roi.size))
      )

    # loose text outside any cell
    outside = text_ink.copy()
    for r in cells:
      x0, y0, x1, y1 = to_px(r, -2)
      outside[max(0, y0):y1, max(0, x0):x1] = 0

    n, _, st, _ = cv2.connectedComponentsWithStats(outside, connectivity=8)
    hs = [
      st[i, cv2.CC_STAT_HEIGHT] for i in range(1, n)
      if 6 <= st[i, cv2.CC_STAT_HEIGHT] <= 60
    ]
    hmed = float(np.median(hs)) if hs else 12.0

    merged = cv2.dilate(
      outside,
      cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (int(max(10, 2.0 * hmed)), max(3, int(0.3 * hmed)))),
    )
    n, lab, st, _ = cv2.connectedComponentsWithStats(merged, connectivity=8)

    clusters = []
    for i in range(1, n):
      x, y, w, h, _a = st[i]
      if h > 6 * hmed or h < 5 or w < 5:
        continue
      if int((outside[y:y + h, x:x + w] > 0).sum()) < 20:
        continue
      clusters.append(
        fitz.Rect(x / ppx, y / ppy, (x + w) / ppx, (y + h) / ppy)
      )

    return {
      "W": W, "H": H, "ppx": ppx, "ppy": ppy,
      "solid_h": solid_h, "solid_v": solid_v,
      "dot_h": dot_h, "dot_v": dot_v,
      "cells": cells, "cell_has_ink": cell_has_ink,
      "clusters": clusters, "text_ink": text_ink,
    }

  def _ocr_overlay(self, image_path: Path, layout, long_edge=2600):
    img = cv2.imread(str(image_path))
    h0, w0 = img.shape[:2]
    f = long_edge / max(h0, w0)
    img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    ph, pw = layout["H"] / layout["ppy"], layout["W"] / layout["ppx"]
    sx, sy = img.shape[1] / pw, img.shape[0] / ph

    ids = []

    for i, (r, has) in enumerate(zip(layout["cells"], layout["cell_has_ink"])):
      if not has:
        continue
      cid = f"c{i}"
      ids.append(cid)
      p0 = (int(r.x0 * sx), int(r.y0 * sy))
      cv2.rectangle(img, p0, (int(r.x1 * sx), int(r.y1 * sy)), (0, 0, 255), 1)
      cv2.putText(img, cid, (p0[0] + 2, p0[1] + 12),
                  cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 255), 1, cv2.LINE_AA)

    for j, r in enumerate(layout["clusters"]):
      tid = f"t{j}"
      ids.append(tid)
      p0 = (int(r.x0 * sx), int(r.y0 * sy))
      cv2.rectangle(img, p0, (int(r.x1 * sx), int(r.y1 * sy)), (255, 0, 0), 1)
      cv2.putText(img, tid, (p0[0], max(10, p0[1] - 3)),
                  cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 0, 0), 1, cv2.LINE_AA)

    ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])

    return (
      "data:image/jpeg;base64," + base64.b64encode(enc.tobytes()).decode(),
      ids,
    )

  def _ocr_layout_items(self, data_url: str, ids: list[str]) -> dict:
    """Transcribe the printed text inside each labelled box."""
    texts: dict[str, str] = {}

    for start in range(0, len(ids), 80):
      chunk = ids[start:start + 80]
      prompt = (
        "This is an image of a Japanese form. Red boxes are table cells "
        "labelled c<number>; blue boxes are free-text regions labelled "
        "t<number>. For EACH id below, transcribe the PRINTED text inside "
        "that box exactly. Keep fill-in placeholders such as 年 月 日, "
        "年, 歳, （　）, ～ exactly as printed. Vertical text is read top "
        "to bottom. Use an empty string if the box has no printed text "
        "(for example only handwriting or noise).\n"
        "Return ONLY JSON: {\"items\": [{\"id\": \"c12\", \"text\": "
        "\"...\"}]} covering exactly these ids: " + json.dumps(chunk)
      )
      try:
        response = self.openai.responses.create(
          model="gpt-5.6-luna",
          input=[{
            "role": "user",
            "content": [
              {"type": "input_text", "text": prompt},
              {"type": "input_image", "image_url": data_url,
               "detail": "high"},
            ],
          }],
        )
        raw = (getattr(response, "output_text", "") or "").strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE)
        for item in json.loads(raw).get("items", []):
          texts[str(item.get("id"))] = str(item.get("text", ""))
      except Exception:
        logger.exception("Layout OCR chunk failed")

    return texts

  def _glyph_rects(self, layout, rect, text):
    """Place each character of `text` on the ink blobs inside `rect`."""
    text = re.sub(r"\s+", "", text)
    if not text:
      return []

    ppx, ppy = layout["ppx"], layout["ppy"]
    ti = layout["text_ink"]
    x0, y0 = max(0, int(rect.x0 * ppx)), max(0, int(rect.y0 * ppy))
    x1 = min(layout["W"], int(rect.x1 * ppx))
    y1 = min(layout["H"], int(rect.y1 * ppy))
    roi = ti[y0:y1, x0:x1]
    if roi.size == 0 or not (roi > 0).any():
      return []

    ys, xs = np.nonzero(roi)
    bx0, bx1, by0, by1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1

    vertical = (y1 - y0) > 1.3 * (x1 - x0) and len(text) >= 2

    n, _, st, _ = cv2.connectedComponentsWithStats(roi, connectivity=8)
    hs = [
      st[i, cv2.CC_STAT_HEIGHT] for i in range(1, n)
      if st[i, cv2.CC_STAT_AREA] >= 6
    ]
    hmed = float(np.median(hs)) if hs else 10.0
    k = max(1, int(0.22 * hmed))
    merged = cv2.dilate(
      roi,
      cv2.getStructuringElement(
        cv2.MORPH_RECT, (1, k) if vertical else (k, 1)),
    )
    n, lab, _, _ = cv2.connectedComponentsWithStats(merged, connectivity=8)

    blobs = []
    for i in range(1, n):
      yy, xx = np.nonzero((lab == i) & (roi > 0))
      if len(xx) < 6:
        continue
      blobs.append((xx.min(), yy.min(), xx.max() + 1, yy.max() + 1))
    blobs.sort(key=lambda b: (b[1] if vertical else b[0]))

    if len(blobs) != len(text):
      # Fall back to even spacing across the ink's bounding box.
      blobs = []
      if vertical:
        step = (by1 - by0) / len(text)
        for i in range(len(text)):
          blobs.append((bx0, by0 + i * step, bx1, by0 + (i + 1) * step))
      else:
        step = (bx1 - bx0) / len(text)
        for i in range(len(text)):
          blobs.append((bx0 + i * step, by0, bx0 + (i + 1) * step, by1))

    out = []
    for ch, (a, b, c, d) in zip(text, blobs):
      out.append((ch, fitz.Rect(
        (x0 + a) / ppx, (y0 + b) / ppy, (x0 + c) / ppx, (y0 + d) / ppy)))
    return out

  @staticmethod
  def _insert_pseudo_char(page, font, ch, r):
    fs = max(5.0, min(14.0, r.height))
    adv = font.text_length(ch, fontsize=fs)
    x = r.x0 + (r.width - adv) / 2.0
    baseline = (
      r.y0 + r.height / 2.0 + (font.ascender + font.descender) / 2.0 * fs
    )
    page.insert_text(
      fitz.Point(x, baseline), ch, fontsize=fs, fontname="japan"
    )

  def _fields_from_image_layout(self, layout, texts, page_w, page_h):
    """Rebuild a vector 'pseudo page' and run the normal PDF extractor."""
    doc = fitz.open()
    page = doc.new_page(width=page_w, height=page_h)
    font = fitz.Font("japan")

    for y, x0, x1 in layout["solid_h"]:
      page.draw_line((x0, y), (x1, y), color=(0, 0, 0), width=0.8)
    for x, y0, y1 in layout["solid_v"]:
      page.draw_line((x, y0), (x, y1), color=(0, 0, 0), width=0.8)
    for y, x0, x1 in layout["dot_h"]:
      page.draw_line((x0, y), (x1, y), color=(0, 0, 0), width=0.8,
                     dashes="[1.5 1.5] 0")
    for x, y0, y1 in layout["dot_v"]:
      page.draw_line((x, y0), (x, y1), color=(0, 0, 0), width=0.8,
                     dashes="[1.5 1.5] 0")

    for i, r in enumerate(layout["cells"]):
      text = texts.get(f"c{i}", "")
      if not text.strip():
        continue
      inner = fitz.Rect(r.x0 + 0.7, r.y0 + 0.7, r.x1 - 0.7, r.y1 - 0.7)
      for ch, cr in self._glyph_rects(layout, inner, text):
        self._insert_pseudo_char(page, font, ch, cr)

    for j, r in enumerate(layout["clusters"]):
      text = texts.get(f"t{j}", "")
      if not text.strip():
        continue
      for ch, cr in self._glyph_rects(layout, r, text):
        self._insert_pseudo_char(page, font, ch, cr)

    fields = self._extract_pdf_fields(page, 1)
    doc.close()

    return fields

  def _prepare_image_fields(self, input_file: Path, work_dir: Path) -> bool:
    """
    Detect fillable fields in an uploaded image and register a catalog
    for it. Returns True when the field-catalog path can be used.
    """
    if not hasattr(self, "_pdf_field_catalogs"):
      self._pdf_field_catalogs = {}
    if not hasattr(self, "_image_pdf_paths"):
      self._image_pdf_paths = {}
    if not hasattr(self, "_image_overlays"):
      self._image_overlays = {}

    try:
      pdf_path, pw, ph = self._image_to_pdf(input_file, work_dir)
      layout = self._image_layout(input_file, pw, ph)

      if layout is None or len(layout["cells"]) < 3:
        logger.info(
          "Image %s: too few cells for field detection", input_file.name
        )
        return False

      data_url, ids = self._ocr_overlay(input_file, layout)
      texts = self._ocr_layout_items(data_url, ids)
      fields = self._fields_from_image_layout(layout, texts, pw, ph)

      if len(fields) < 3:
        return False

      with fitz.open(pdf_path) as doc:
        overlay = self._render_field_overlay(doc[0], fields)

    except Exception:
      logger.exception("Image field detection failed for %s", input_file)
      return False

    self._pdf_field_catalogs[pdf_path.name] = {f["id"]: f for f in fields}
    self._image_pdf_paths[input_file.name] = pdf_path
    self._image_overlays[pdf_path.name] = (overlay, fields)

    logger.info(
      "Image %s: detected %d fillable field(s)", input_file.name, len(fields)
    )
    return True

  def _build_image_agent_inputs(self, input_file: Path) -> list[dict]:
    pdf_path = self._image_pdf_paths[input_file.name]
    overlay, fields = self._image_overlays[pdf_path.name]

    return [
      {
        "type": "input_text",
        "text": (
          f"Form file: {pdf_path.name} (an uploaded image). The following "
          f"image shows every detected fillable region outlined in red and "
          f"tagged with the number part of its id (e.g. '12' means field "
          f"id 'f1_12')."
        ),
      },
      {"type": "input_image", "image_url": overlay, "detail": "high"},
      {
        "type": "input_text",
        "text": (
          f"FIELD CATALOG for {pdf_path.name}:\n"
          + self._format_field_catalog(fields)
        ),
      },
    ]


  async def _run_openai_form_agent(
    self,
    prompt: str,
    input_files: list[Path],
    output_dir: Path,
  ) -> dict:

    uploaded_files = []

    try:
      image_suffixes = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
      image_work_dir = output_dir.parent / "image_pdfs"

      image_field_files = set()

      for input_file in input_files:
        if (
          input_file.suffix.lower() in image_suffixes
          and self._prepare_image_fields(input_file, image_work_dir)
        ):
          image_field_files.add(input_file)

      has_any_image = any(
        f.suffix.lower() in image_suffixes for f in input_files
      )

      has_image = any(
        f.suffix.lower() in image_suffixes and f not in image_field_files
        for f in input_files
      )

      has_pdf = any(
        input_file.suffix.lower()
        == ".pdf"
        for input_file in input_files
      )

      for input_file in input_files:
        if (
          input_file.suffix.lower() == ".pdf"
          or input_file in image_field_files
        ):
          continue

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

          with Image.open(input_file) as pil_image:
            image_width, image_height = pil_image.size

          content.append(
            {
              "type": "input_text",
              "text": (
                f"The following image is {input_file.name}. "
                f"It is already correctly oriented — do not "
                f"assume any further rotation. "
                f"Image dimensions: width={image_width}, "
                f"height={image_height}."
              ),
            }
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

      for input_file in input_files:
        if input_file in image_field_files:
          content.extend(self._build_image_agent_inputs(input_file))

      if has_image:

        image_instruction = """
IMPORTANT: One or more uploaded files are images.

For image forms, DO NOT attempt to create, modify, or save an output
image using Code Interpreter.

IMPORTANT:

The uploaded image is already in its final, correctly-oriented
form. Do not attempt to determine or apply any rotation.

All image-edit coordinates MUST correspond exactly to the image as
shown to you, using the stated width/height as your coordinate
space.

Return the image edits.

Some field values are not sourced directly from Kenchiku entity data,
but are instead legitimately DERIVED per the REASONABLE FORM-DERIVED
VALUES rules above — for example, today's date for a form
creation/preparation field, or an age calculated from a known date of
birth. These derived values are a normal, sufficient basis for an
image_edit, exactly like a value pulled directly from entity data.

When you identify the location of a field whose value should be filled
using a REASONABLE FORM-DERIVED VALUES rule, you MUST still produce a
normal image_edit for it. Do not treat it as unresolved or place it in
missing_data merely because it lacks a corresponding Kenchiku entity
value — a value permitted under REASONABLE FORM-DERIVED VALUES is
sufficient evidence to fill the field.

This does NOT extend to inventing or guessing genuine factual data
(names, addresses, phone numbers, qualifications, historical dates,
etc.) that the REASONABLE FORM-DERIVED VALUES rules do not explicitly
permit. Only use this exception for values that section actually
allows.

Each image edit must contain:

- "filename": the original image filename
- "text": the exact text to place
- "x": left coordinate in pixels
- "y": top coordinate in pixels
- "width": width of the field in pixels
- "height": height of the field in pixels

Do not resize the coordinate system.

The x/y coordinates identify the upper-left corner of the field
where the text should be placed.

The text must be placed INSIDE the corresponding blank field.

Do not fabricate fields or coordinates.

Only create an image_edit when there is enough visual evidence to
identify the correct field.

If requested information cannot be located confidently, put that
information in "missing_data" instead.

For multiple images, keep edits associated with the correct
filename.

The image itself will be edited later by the application, using the
coordinates you provide.

Your response MUST be valid JSON with this structure:

{
  "summary": "...",
  "completed": true,
  "files": [],
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

      pdf_renders: list[dict] = []
      modes = set()

      if image_field_files:
        modes.add("cells")

      if has_pdf:
        for pdf_file in input_files:
          if pdf_file.suffix.lower() != ".pdf":
            continue

          pdf_content, renders, mode = self._build_pdf_agent_inputs(
            pdf_file,
          )

          content.extend(pdf_content)
          pdf_renders.extend(renders)
          modes.add(mode)

      if "cells" in modes:
        content.append(
          {
            "type": "input_text",
            "text": self._cell_prompt_instructions(),
          }
        )

      if "coords" in modes:
        content.append(
          {
            "type": "input_text",
            "text": self._pdf_prompt_instructions(),
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

      if pdf_renders:
        self._convert_pdf_edit_pixels_to_points(
          agent_output,
          pdf_renders,
        )

      if has_pdf or image_field_files:
        self._resolve_cell_edits(
          agent_output,
          input_files,
        )

      if not has_any_image and not has_pdf:
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

    font_path = Path(
      "/usr/share/fonts/truetype/noto/NotoSansJP-Regular.ttf"
    )

    if font_path.exists():
      logger.info(
        "Using image form font: %s",
        font_path,
      )
    else:
      logger.warning(
        "Japanese font not found: %s",
        font_path,
      )
      font_path = None

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

      def wrap_text_to_width(
        text_value: str,
        font_obj: ImageFont.FreeTypeFont,
        max_line_width: float,
      ) -> list[str]:
        """
        Greedily wrap text_value into lines that each fit within
        max_line_width, breaking on whitespace when possible and
        falling back to character-by-character breaks for scripts
        (like Japanese) that don't use spaces.
        """
        if not text_value:
          return [""]

        has_spaces = " " in text_value

        units = text_value.split(" ") if has_spaces else list(text_value)
        separator = " " if has_spaces else ""

        lines = []
        current_line = ""

        for unit in units:
          candidate = (
            current_line + separator + unit
            if current_line
            else unit
          )

          candidate_bbox = draw.textbbox(
            (0, 0),
            candidate,
            font=font_obj,
          )

          candidate_width = candidate_bbox[2] - candidate_bbox[0]

          if candidate_width <= max_line_width or not current_line:
            current_line = candidate
          else:
            lines.append(current_line)
            current_line = unit

        if current_line:
          lines.append(current_line)

        return lines

      def measure_wrapped_block(
        text_value: str,
        font_obj: ImageFont.FreeTypeFont,
        max_line_width: float,
      ) -> tuple[list[str], float, float]:
        lines = wrap_text_to_width(
          text_value,
          font_obj,
          max_line_width,
        )

        line_height = font_obj.getbbox("Ag")[3] - font_obj.getbbox("Ag")[1]
        line_spacing = line_height * 1.15

        block_width = max(
          (
            draw.textbbox((0, 0), line, font=font_obj)[2]
            - draw.textbbox((0, 0), line, font=font_obj)[0]
          )
          for line in lines
        )

        block_height = line_spacing * len(lines)

        return lines, block_width, block_height

      min_font_size = 8

      def fits_on_one_line(
        text_value: str,
        font_obj: ImageFont.FreeTypeFont,
      ) -> bool:
        line_bbox = draw.textbbox(
          (0, 0),
          text_value,
          font=font_obj,
        )

        line_width = line_bbox[2] - line_bbox[0]
        line_height = line_bbox[3] - line_bbox[1]

        return (
          line_width <= max_width
          and line_height <= max_height
        )

      if font_path:
        starting_font_size = max(
          min_font_size,
          int(max_height * 0.70),
        )

        font = None
        lines = None

        # Step 1: shrink to find the largest size that fits on ONE
        # line. Only fall back to wrapping if even min_font_size
        # can't fit the text on a single line.
        for candidate_size in range(
          starting_font_size,
          min_font_size - 1,
          -1,
        ):
          candidate_font = ImageFont.truetype(
            font_path,
            candidate_size,
          )

          if fits_on_one_line(str(text), candidate_font):
            font = candidate_font
            lines = [str(text)]
            break

        if font is None:

          # Step 2: doesn't fit on one line even at min_font_size.
          # Wrap at min_font_size, then try growing the font back up
          # since multiple lines share the available height.
          font = ImageFont.truetype(
            font_path,
            min_font_size,
          )

          lines, _, _ = measure_wrapped_block(
            str(text),
            font,
            max_width,
          )

          for candidate_size in range(
            min_font_size + 1,
            starting_font_size + 1,
          ):
            candidate_font = ImageFont.truetype(
              font_path,
              candidate_size,
            )

            candidate_lines, candidate_width, candidate_height = (
              measure_wrapped_block(
                str(text),
                candidate_font,
                max_width,
              )
            )

            if (
              candidate_width <= max_width
              and candidate_height <= max_height
            ):
              font = candidate_font
              lines = candidate_lines
            else:
              break

      else:
        font = ImageFont.load_default()

        lines, block_width, block_height = measure_wrapped_block(
          str(text),
          font,
          max_width,
        )

      logger.info(
        "Applying image edit: text=%r x=%s y=%s width=%s height=%s lines=%d",
        text,
        x,
        y,
        width,
        height,
        len(lines),
      )

      line_bbox = font.getbbox("Ag")
      line_height = line_bbox[3] - line_bbox[1]
      line_spacing = line_height * 1.15

      total_text_height = line_spacing * len(lines)

      start_y = (
        y
        + max(
          0,
          (max_height - total_text_height) / 2,
        )
      )

      for line_index, line in enumerate(lines):

        line_bbox = draw.textbbox(
          (0, 0),
          line,
          font=font,
        )

        line_width = line_bbox[2] - line_bbox[0]

        line_x = (
          x
          + max(
            0,
            (max_width - line_width) / 2,
          )
        )

        line_y = (
          start_y
          + line_index * line_spacing
          - line_bbox[1]
        )

        draw.text(
          (
            line_x,
            line_y,
          ),
          line,
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


  def _render_pdf_pages_for_model(
    self,
    input_file: Path,
    long_edge_px: int = 2000,
    grid_units: int = 50,
  ) -> list[dict]:
    """
    Render every page to a PNG with a labelled grid in NORMALIZED
    units (0-1000 on each axis). Normalized coordinates are immune
    to any internal resizing the vision model applies.
    """

    renders: list[dict] = []

    font_path = Path(
      "/usr/share/fonts/truetype/noto/NotoSansJP-Regular.ttf"
    )

    try:
      if font_path.exists():
        font = ImageFont.truetype(str(font_path), 30)
      else:
        font = ImageFont.load_default(size=30)
    except Exception:
      font = ImageFont.load_default()

    document = fitz.open(input_file)

    try:
      for page_index in range(document.page_count):
        page = document[page_index]
        page_rect = page.rect

        zoom = long_edge_px / max(
          page_rect.width,
          page_rect.height,
        )

        pixmap = page.get_pixmap(
          matrix=fitz.Matrix(zoom, zoom),
          alpha=False,
        )

        width_px = pixmap.width
        height_px = pixmap.height

        image = Image.frombytes(
          "RGB",
          (width_px, height_px),
          pixmap.samples,
        ).convert("RGBA")

        overlay = Image.new(
          "RGBA",
          image.size,
          (255, 255, 255, 0),
        )

        draw = ImageDraw.Draw(overlay)

        red = (255, 0, 0, 255)

        for unit in range(0, 1001, grid_units):
          major = unit % 100 == 0

          x = round(unit / 1000 * (width_px - 1))
          y = round(unit / 1000 * (height_px - 1))

          line_fill = (255, 0, 0, 120 if major else 50)
          line_width = 2 if major else 1

          draw.line(
            [(x, 0), (x, height_px)],
            fill=line_fill,
            width=line_width,
          )
          draw.line(
            [(0, y), (width_px, y)],
            fill=line_fill,
            width=line_width,
          )

          label = str(unit)
          tx = min(x + 3, width_px - 80)
          ty = min(y + 2, height_px - 40)

          # x labels on top and bottom edges
          draw.text((tx, 2), label, fill=red, font=font)
          draw.text(
            (tx, height_px - 38),
            label,
            fill=red,
            font=font,
          )

          # y labels on left and right edges
          draw.text((3, ty), label, fill=red, font=font)
          draw.text(
            (width_px - 80, ty),
            label,
            fill=red,
            font=font,
          )

        # Interior "x,y" labels (normalized units).
        for ux in range(100, 1000, 200):
          for uy in range(100, 1000, 200):
            draw.text(
              (
                round(ux / 1000 * (width_px - 1)) + 4,
                round(uy / 1000 * (height_px - 1)) + 4,
              ),
              f"{ux},{uy}",
              fill=(0, 0, 255, 255),
              font=font,
            )

        image = Image.alpha_composite(
          image,
          overlay,
        ).convert("RGB")

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")

        renders.append(
          {
            "filename": input_file.name,
            "page": page_index + 1,
            "page_count": document.page_count,
            "width_px": width_px,
            "height_px": height_px,
            "page_width_pt": float(page_rect.width),
            "page_height_pt": float(page_rect.height),
            "origin_x": float(page_rect.x0),
            "origin_y": float(page_rect.y0),
            "png_bytes": buffer.getvalue(),
          }
        )

        logger.info(
          "Rendered PDF page for model: file=%s page=%d "
          "px=%dx%d pt=%.2fx%.2f rotation=%d",
          input_file.name,
          page_index + 1,
          width_px,
          height_px,
          page_rect.width,
          page_rect.height,
          page.rotation,
        )

    finally:
      document.close()

    return renders


  def _build_pdf_agent_content(
    self,
    input_file: Path,
  ) -> tuple[list[dict], list[dict]]:
    renders = self._render_pdf_pages_for_model(input_file)

    content: list[dict] = []

    document = fitz.open(input_file)

    def clamp(value: float) -> int:
      return int(max(0, min(1000, round(value))))

    try:
      field_lines: list[str] = []

      for render in renders:
        page = document[render["page"] - 1]

        for widget in (page.widgets() or []):
          if not widget.field_name:
            continue

          visual = fitz.Rect(widget.rect) * page.rotation_matrix
          visual.normalize()

          x0 = clamp(
            (visual.x0 - render["origin_x"])
            / render["page_width_pt"] * 1000
          )
          y0 = clamp(
            (visual.y0 - render["origin_y"])
            / render["page_height_pt"] * 1000
          )
          x1 = clamp(
            (visual.x1 - render["origin_x"])
            / render["page_width_pt"] * 1000
          )
          y1 = clamp(
            (visual.y1 - render["origin_y"])
            / render["page_height_pt"] * 1000
          )

          field_lines.append(
            f"- page={render['page']} "
            f"field_name={widget.field_name!r} "
            f"type={widget.field_type_string} "
            f"current_value={widget.field_value!r} "
            f"approx_rect=[{x0},{y0},{x1},{y1}]"
          )

      for render in renders:
        content.append(
          {
            "type": "input_text",
            "text": (
              f"PDF file: {render['filename']} — page "
              f"{render['page']} of {render['page_count']}.\n"
              f"The next image is this page with a red coordinate "
              f"grid.\n"
              f"COORDINATES ARE NORMALIZED: x runs 0 to 1000 from "
              f"the left edge to the right edge of the image, and "
              f"y runs 0 to 1000 from the top edge to the bottom "
              f"edge. (0,0) is top-left, (1000,1000) is "
              f"bottom-right. Grid lines are every 50 units; the "
              f"red numbers on the image edges are these units. "
              f"Blue labels show x,y at some intersections.\n"
              f"Do NOT use pixels. Read positions from the grid "
              f"labels, and return x0,y0,x1,y1 in these 0-1000 "
              f"units."
            ),
          }
        )

        data_url = (
          "data:image/png;base64,"
          + base64.b64encode(render["png_bytes"]).decode("utf-8")
        )

        content.append(
          {
            "type": "input_image",
            "image_url": data_url,
            "detail": "high",
          }
        )

        render.pop("png_bytes", None)

      if field_lines:
        content.append(
          {
            "type": "input_text",
            "text": (
              f"Existing AcroForm fields in {input_file.name} "
              f"(use these EXACT field_name values; rects are "
              f"approximate, in the same 0-1000 units):\n"
              + "\n".join(field_lines)
            ),
          }
        )
      else:
        content.append(
          {
            "type": "input_text",
            "text": (
              f"{input_file.name} contains NO AcroForm fields. "
              f"Use coordinate-based edits."
            ),
          }
        )

    finally:
      document.close()

    return content, renders


  def _convert_pdf_edit_pixels_to_points(
    self,
    agent_output: dict,
    renders: list[dict],
  ) -> None:
    """
    Convert coordinate edits in agent_output["pdf_edits"] from the
    model's normalized 0-1000 space to PDF points (visual page
    coordinates). AcroForm edits (no coordinates) are left alone.
    """
    pdf_edits = agent_output.get("pdf_edits") or []

    renders_by_key = {
      (render["filename"], render["page"]): render
      for render in renders
    }

    filenames = {render["filename"] for render in renders}

    for edit in pdf_edits:
      if not isinstance(edit, dict):
        continue

      if edit.get("coordinate_space") == "pdf_points":
        continue

      if not all(k in edit for k in ("x0", "y0", "x1", "y1")):
        continue

      filename = edit.get("filename")

      if filename not in filenames:
        if len(filenames) == 1:
          filename = next(iter(filenames))
          edit["filename"] = filename
        else:
          logger.warning(
            "Cannot resolve filename for coordinate edit: %s",
            edit,
          )
          continue

      try:
        page_number = int(edit.get("page") or 1)
      except (TypeError, ValueError):
        page_number = 1

      render = renders_by_key.get((filename, page_number))

      if render is None:
        logger.warning(
          "No render metadata for edit (file=%s page=%s): %s",
          filename,
          page_number,
          edit,
        )
        continue

      try:
        n0x = float(edit["x0"])
        n0y = float(edit["y0"])
        n1x = float(edit["x1"])
        n1y = float(edit["y1"])
      except (TypeError, ValueError):
        logger.warning(
          "Invalid normalized coordinates in edit: %s",
          edit,
        )
        continue

      left, right = sorted((n0x, n1x))
      top, bottom = sorted((n0y, n1y))

      left = max(0.0, min(left, 1000.0))
      right = max(0.0, min(right, 1000.0))
      top = max(0.0, min(top, 1000.0))
      bottom = max(0.0, min(bottom, 1000.0))

      edit["page"] = page_number
      edit["normalized_rect"] = [left, top, right, bottom]
      edit["x0"] = (
        render["origin_x"]
        + left / 1000.0 * render["page_width_pt"]
      )
      edit["x1"] = (
        render["origin_x"]
        + right / 1000.0 * render["page_width_pt"]
      )
      edit["y0"] = (
        render["origin_y"]
        + top / 1000.0 * render["page_height_pt"]
      )
      edit["y1"] = (
        render["origin_y"]
        + bottom / 1000.0 * render["page_height_pt"]
      )
      edit["coordinate_space"] = "pdf_points"

      logger.info(
        "Converted edit %r normalized=[%.0f,%.0f,%.0f,%.0f] -> "
        "points=[%.1f,%.1f,%.1f,%.1f]",
        edit.get("field_name"),
        left,
        top,
        right,
        bottom,
        edit["x0"],
        edit["y0"],
        edit["x1"],
        edit["y1"],
      )


  @staticmethod
  def _collect_ruling_lines(
    page,
  ) -> tuple[list[tuple], list[tuple]]:
    """
    Collect printed horizontal/vertical ruling lines from the page's
    vector drawings.

    Returns:
      horizontals: [(y, x_min, x_max), ...]
      verticals:   [(x, y_min, y_max), ...]
    """
    horizontals: list[tuple] = []
    verticals: list[tuple] = []

    try:
      drawings = page.get_drawings()
    except Exception:
      logger.exception("get_drawings() failed")
      return horizontals, verticals

    def add_segment(x_a, y_a, x_b, y_b) -> None:
      if abs(y_a - y_b) <= 0.75 and abs(x_a - x_b) >= 6:
        horizontals.append(
          ((y_a + y_b) / 2.0, min(x_a, x_b), max(x_a, x_b))
        )
      elif abs(x_a - x_b) <= 0.75 and abs(y_a - y_b) >= 6:
        verticals.append(
          ((x_a + x_b) / 2.0, min(y_a, y_b), max(y_a, y_b))
        )

    def add_rect(r) -> None:
      if r.height <= 1.5 and r.width >= 6:
        y = (r.y0 + r.y1) / 2.0
        horizontals.append((y, r.x0, r.x1))
      elif r.width <= 1.5 and r.height >= 6:
        x = (r.x0 + r.x1) / 2.0
        verticals.append((x, r.y0, r.y1))
      elif r.width > 6 and r.height > 6:
        horizontals.append((r.y0, r.x0, r.x1))
        horizontals.append((r.y1, r.x0, r.x1))
        verticals.append((r.x0, r.y0, r.y1))
        verticals.append((r.x1, r.y0, r.y1))

    for drawing in drawings:
      for item in drawing.get("items", []):
        op = item[0]

        try:
          if op == "l":
            p1, p2 = item[1], item[2]
            add_segment(p1.x, p1.y, p2.x, p2.y)
          elif op == "re":
            add_rect(item[1])
          elif op == "qu":
            add_rect(item[1].rect)
        except Exception:
          continue

    return horizontals, verticals


  def _snap_pdf_rect_to_ruling_lines(
    self,
    page,
    rect,
    line_cache: dict,
    tolerance: float = 10.0,
    inset: float = 1.0,
  ):
    """
    Nudge each edge of `rect` onto a nearby printed ruling line
    (within `tolerance` points) so small model errors land inside
    the real cell. Edges with no nearby line are left alone. If the
    snapped rect looks implausible, the original is returned.

    Only runs for unrotated pages (get_drawings coordinates are not
    guaranteed to be in visual space on rotated pages).
    """

    if page.rotation != 0:
      return rect

    cache_key = page.number

    if cache_key not in line_cache:
      line_cache[cache_key] = self._collect_ruling_lines(page)

    horizontals, verticals = line_cache[cache_key]

    def nearest_horizontal(target_y: float) -> float | None:
      best = None
      for y, lx0, lx1 in horizontals:
        overlap = min(rect.x1, lx1) - max(rect.x0, lx0)
        if overlap < 0.5 * rect.width:
          continue
        distance = abs(y - target_y)
        if distance <= tolerance and (
          best is None or distance < best[0]
        ):
          best = (distance, y)
      return best[1] if best else None

    def nearest_vertical(target_x: float) -> float | None:
      best = None
      for x, ly0, ly1 in verticals:
        overlap = min(rect.y1, ly1) - max(rect.y0, ly0)
        if overlap < 0.5 * rect.height:
          continue
        distance = abs(x - target_x)
        if distance <= tolerance and (
          best is None or distance < best[0]
        ):
          best = (distance, x)
      return best[1] if best else None

    new_x0, new_y0, new_x1, new_y1 = (
      rect.x0,
      rect.y0,
      rect.x1,
      rect.y1,
    )

    top = nearest_horizontal(rect.y0)
    bottom = nearest_horizontal(rect.y1)
    left = nearest_vertical(rect.x0)
    right = nearest_vertical(rect.x1)

    if top is not None:
      new_y0 = top + inset
    if bottom is not None:
      new_y1 = bottom - inset
    if left is not None:
      new_x0 = left + inset
    if right is not None:
      new_x1 = right - inset

    snapped = fitz.Rect(new_x0, new_y0, new_x1, new_y1)

    original_area = max(rect.width * rect.height, 1e-6)
    snapped_area = snapped.width * snapped.height

    if (
      snapped.width < 4
      or snapped.height < 4
      or not (0.5 <= snapped_area / original_area <= 2.0)
    ):
      logger.info(
        "Snap rejected (implausible): original=%s snapped=%s",
        rect,
        snapped,
      )
      return rect

    logger.info(
      "Snapped rect: %s -> %s (top=%s bottom=%s left=%s right=%s)",
      rect,
      snapped,
      top,
      bottom,
      left,
      right,
    )

    return snapped


  def _add_editable_pdf_text_field(
    self,
    page,
    rect,
    field_name: str,
    value: str,
    font_name: str,
    font_size: float,
    multiline: bool = False,
    pad_x: float = 2.5,
  ) -> None:
    """
    Add a real editable AcroForm text field to an existing PDF.

    `rect` is the VISIBLE text area in visual page coordinates. Viewers
    such as macOS Preview clip text to the widget rectangle and inset it
    by ~2 pt, so the widget is widened by `pad_x` on each side to keep
    the full text visible without changing the text's size or position.
    """

    widget_rect = fitz.Rect(rect)

    widget_rect.x0 -= pad_x
    widget_rect.x1 += pad_x

    if page.rotation:
      widget_rect = widget_rect * page.derotation_matrix
      widget_rect.normalize()

    widget = fitz.Widget()

    widget.field_name = field_name
    widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    widget.rect = widget_rect
    widget.field_value = str(value)

    if multiline:
      widget.field_flags = fitz.PDF_TX_FIELD_IS_MULTILINE

    widget.text_font = font_name
    widget.text_fontsize = max(3, float(font_size))

    widget.border_color = None
    widget.border_width = 0
    widget.fill_color = None
    widget.text_color = (0, 0, 0)

    added = page.add_widget(widget)

    doc = page.parent
    xref = getattr(added, "xref", 0) or widget.xref

    if not xref:
      # Fallback: find the widget we just created by its field name.
      for w in page.widgets() or []:
        if w.field_name == field_name:
          xref = w.xref
          widget = w
          break

    try:
      if xref:
        doc.xref_set_key(xref, "Q", "1")
        widget.xref = xref
        widget.update()
        logger.info(
          "Widget %r Q=%s", field_name, doc.xref_get_key(xref, "Q")
        )
      else:
        logger.warning("No xref found for widget %r; not centered", field_name)
    except Exception:
      logger.exception("Could not center widget %r", field_name)

    logger.info(
      "Created editable PDF field: field_name=%r rect=%s "
      "widget_rect=%s value=%r font=%r fontsize=%.2f multiline=%s",
      field_name,
      rect,
      widget_rect,
      value,
      font_name,
      font_size,
      multiline,
    )


  _PLACEHOLDER_CHARS = set("年月日歳才（）()［］[]～〜~:：/／-－—、,.。")

  # ------------------------------------------------------------------
  # geometry
  # ------------------------------------------------------------------
  def _form_lines(self, page):
    """Solid and dotted horizontal/vertical rules from vector drawings."""
    solid_h, solid_v, dot_h, dot_v, tiny = [], [], [], [], []

    for dr in page.get_drawings():
      dashes = dr.get("dashes")
      dashed = bool(dashes) and dashes not in ("[] 0", "[] 0.0")
      stroked = "s" in str(dr.get("type", ""))

      for item in dr.get("items", []):
        op = item[0]
        try:
          if op == "l":
            p1, p2 = item[1], item[2]
            if abs(p1.y - p2.y) <= 0.75 and abs(p1.x - p2.x) >= 4:
              seg = ((p1.y + p2.y) / 2, min(p1.x, p2.x), max(p1.x, p2.x))
              (dot_h if dashed else solid_h).append(seg)
            elif abs(p1.x - p2.x) <= 0.75 and abs(p1.y - p2.y) >= 4:
              seg = ((p1.x + p2.x) / 2, min(p1.y, p2.y), max(p1.y, p2.y))
              (dot_v if dashed else solid_v).append(seg)
          elif op in ("re", "qu"):
            r = item[1] if op == "re" else item[1].rect
            w, h = r.width, r.height
            if w <= 2.0 and h <= 2.0:
              tiny.append(((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))
            elif h <= 2.0 and w >= 4:
              solid_h.append(((r.y0 + r.y1) / 2, r.x0, r.x1))
            elif w <= 2.0 and h >= 4:
              solid_v.append(((r.x0 + r.x1) / 2, r.y0, r.y1))
            elif w > 4 and h > 4 and stroked:
              solid_h += [(r.y0, r.x0, r.x1), (r.y1, r.x0, r.x1)]
              solid_v += [(r.x0, r.y0, r.y1), (r.x1, r.y0, r.y1)]
        except Exception:
          continue

    # Dotted rules drawn as rows of tiny squares.
    def runs(points, key, along):
      groups = {}
      for p in points:
        groups.setdefault(round(p[key] * 2) / 2, []).append(p[along])
      out = []
      for k, vals in groups.items():
        vals.sort()
        start, prev, count = vals[0], vals[0], 1
        for v in vals[1:] + [None]:
          if v is not None and v - prev <= 8:
            prev, count = v, count + 1
            continue
          if count >= 6:
            out.append((k, start, prev))
          if v is not None:
            start, prev, count = v, v, 1
      return out

    dot_h += runs(tiny, 1, 0)
    dot_v += runs(tiny, 0, 1)

    return solid_h, solid_v, dot_h, dot_v

  def _detect_cells(self, page, solid_h, solid_v, dot_h, dot_v):
    """Enclosed rectangular regions (table cells) via raster flood fill."""

    s = 3.0
    ox, oy = page.rect.x0, page.rect.y0
    W = int(page.rect.width * s) + 2
    H = int(page.rect.height * s) + 2
    img = np.full((H, W), 255, np.uint8)
    pad = 0.8

    def P(x, y):
      return int(round((x - ox) * s)), int(round((y - oy) * s))

    for y, x0, x1 in solid_h + dot_h:
      cv2.line(img, P(x0 - pad, y), P(x1 + pad, y), 0, 3)
    for x, y0, y1 in solid_v + dot_v:
      cv2.line(img, P(x, y0 - pad), P(x, y1 + pad), 0, 3)

    n, _, stats, _ = cv2.connectedComponentsWithStats(
      (img == 255).astype(np.uint8), connectivity=4
    )

    cells = []
    for i in range(1, n):
      x, y, w, h, area = stats[i]
      if x <= 0 or y <= 0 or x + w >= W - 1 or y + h >= H - 1:
        continue
      if area / float(w * h) < 0.92:
        continue
      r = fitz.Rect(
        ox + x / s, oy + y / s, ox + (x + w) / s, oy + (y + h) / s
      )
      if r.width < 8 or r.height < 5:
        continue
      if (
        r.width > 0.6 * page.rect.width
        and r.height > 0.5 * page.rect.height
      ):
        continue
      cells.append(r)

    return cells

  # ------------------------------------------------------------------
  # text helpers
  # ------------------------------------------------------------------
  @staticmethod
  def _page_chars(page):
    chars = []
    raw = page.get_text("rawdict")
    for b in raw.get("blocks", []):
      if b.get("type") != 0:
        continue
      for l in b.get("lines", []):
        for sp in l.get("spans", []):
          for ch in sp.get("chars", []):
            if ch["c"].strip() == "":
              continue
            chars.append({"c": ch["c"], "r": fitz.Rect(ch["bbox"])})
    return chars

  @staticmethod
  def _text_in(chars, rect):
    sel = []
    for ch in chars:
      r = ch["r"]
      cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
      if rect.x0 <= cx <= rect.x1 and rect.y0 <= cy <= rect.y1:
        sel.append(ch)

    if not sel:
      return "", []

    sel.sort(key=lambda c: ((c["r"].y0 + c["r"].y1) / 2, c["r"].x0))

    lines, cur = [], [sel[0]]
    for ch in sel[1:]:
      cy = (ch["r"].y0 + ch["r"].y1) / 2
      py = (cur[-1]["r"].y0 + cur[-1]["r"].y1) / 2
      if abs(cy - py) <= 3.0:
        cur.append(ch)
      else:
        lines.append(cur)
        cur = [ch]
    lines.append(cur)

    out = []
    for ln in lines:
      ln.sort(key=lambda c: c["r"].x0)
      s = ln[0]["c"]
      for a, b in zip(ln, ln[1:]):
        if b["r"].x0 - a["r"].x1 > 0.25 * a["r"].height:
          s += " "
        s += b["c"]
      out.append(s)

    text = " ".join(out)
    text = re.sub(r"(?<=[^\x00-\x7f]) (?=[^\x00-\x7f])", "", text)
    return text.strip(), sel

  def _is_placeholder(self, text):
    return all(c in self._PLACEHOLDER_CHARS or c.isspace() for c in text)

  @staticmethod
  def _left_label(chars, band, max_gap=40.0, max_span=110.0):
    """Contiguous printed text immediately left of a field, same line."""
    cand = []
    for ch in chars:
      r = ch["r"]
      cy = (r.y0 + r.y1) / 2
      if band.y0 - 3 <= cy <= band.y1 + 0.5 and r.x1 <= band.x0 + 1:
        cand.append(ch)

    cand.sort(key=lambda c: -c["r"].x1)

    taken = []
    for ch in cand:
      if not taken:
        if band.x0 - ch["r"].x1 > 40:
          break
        taken.append(ch)
      elif taken[-1]["r"].x0 - ch["r"].x1 <= max_gap:
        taken.append(ch)
      else:
        break
      if band.x0 - taken[-1]["r"].x0 > max_span:
        break

    taken.sort(key=lambda c: c["r"].x0)
    return "".join(c["c"] for c in taken)

  # ------------------------------------------------------------------
  # field extraction
  # ------------------------------------------------------------------
  def _extract_pdf_fields(self, page, page_number=1):
    if page.rotation != 0:
      return []

    chars = self._page_chars(page)
    solid_h, solid_v, dot_h, dot_v = self._form_lines(page)
    rects = self._detect_cells(page, solid_h, solid_v, dot_h, dot_v)

    cells = []
    for r in rects:
      text, sel = self._text_in(
        chars,
        fitz.Rect(r.x0 + 0.5, r.y0 + 0.5, r.x1 - 0.5, r.y1 - 0.5),
      )
      cells.append({
        "r": r,
        "text": text,
        "chars": sel,
        "fillable": self._is_placeholder(text) and r.width >= 12,
      })

    text_cells = [c for c in cells if not c["fillable"] and c["text"]]
    fillable = [c for c in cells if c["fillable"]]

    def xov(a, b):
      return min(a.x1, b.x1) - max(a.x0, b.x0)

    # Stacks: fillable cells joined vertically by dotted rules.
    def dotted_below(c):
      r = c["r"]
      for y, x0, x1 in dot_h:
        if (
          abs(y - r.y1) <= 2.0
          and min(x1, r.x1) - max(x0, r.x0) >= 0.5 * r.width
        ):
          return True
      return False

    for c in fillable:
      c["dot_below"] = dotted_below(c)
      c["chain"] = [c]

    for c in sorted(fillable, key=lambda c: (c["r"].y0, c["r"].x0)):
      if not c["dot_below"]:
        continue
      for d in fillable:
        if (
          abs(d["r"].y0 - c["r"].y1) <= 2.5
          and abs(d["r"].x0 - c["r"].x0) <= 2.5
          and abs(d["r"].x1 - c["r"].x1) <= 2.5
        ):
          chain = c["chain"] + [d]
          for m in chain:
            m["chain"] = chain
          break

    for c in fillable:
      chain = sorted(c["chain"], key=lambda m: m["r"].y0)
      c["stack_k"] = chain.index(c) + 1
      c["stack_n"] = len(chain)
      c["top"] = chain[0]["r"].y0

    bands, last = [], None
    for t in sorted({round(c["top"], 1) for c in fillable}):
      if last is None or t - last > 2.0:
        bands.append(t)
      last = t

    def band_of(top):
      for i, b in enumerate(bands):
        if abs(top - b) <= 2.0:
          return i + 1
      return 0

    fields = []

    for c in fillable:
      r = c["r"]

      col_top = min(
        d["r"].y0
        for d in fillable
        if xov(d["r"], r) >= 0.5 * min(d["r"].width, r.width)
      )

      cand = [
        t for t in text_cells
        if t["r"].y1 <= col_top + 2.0
        and xov(t["r"], r) >= 0.5 * min(t["r"].width, r.width)
      ]
      cand.sort(key=lambda t: -t["r"].y1)

      accepted, edge = [], col_top
      for t in cand:
        if edge - t["r"].y1 <= 6.0:   # contiguous header stack only
          accepted.append(t)
          edge = t["r"].y0
      accepted.sort(key=lambda t: t["r"].y0)

      same = [
        t for t in accepted
        if abs(t["r"].x0 - r.x0) <= 3 and abs(t["r"].x1 - r.x1) <= 3
      ]
      group = [t["text"] for t in accepted if t not in same]

      if same and len(same) == c["stack_n"]:
        label = same[c["stack_k"] - 1]["text"]
      else:
        label = " / ".join(t["text"] for t in same)

      left = ""
      lefts = [
        t for t in text_cells
        if t["r"].x1 <= r.x0 + 2.0
        and r.x0 - t["r"].x1 <= 40
        and min(t["r"].y1, r.y1) - max(t["r"].y0, r.y0)
        >= 0.5 * min(t["r"].height, r.height)
      ]
      if lefts:
        left = max(lefts, key=lambda t: t["r"].x1)["text"]

      anchors = [a for a in c["chars"] if a["c"] in "年月日歳"]
      opens = [a for a in c["chars"] if a["c"] in "（("]
      closes = [a for a in c["chars"] if a["c"] in "）)"]

      fields.append({
        "kind": "cell",
        "page": page_number,
        "rect": r,
        "label": label,
        "group": " / ".join(group),
        "left": left,
        "band": band_of(c["top"]),
        "stack": f"{c['stack_k']}/{c['stack_n']}",
        "printed": re.sub(r"\s+", "", c["text"]),
        "anchors": anchors,
        "bracket": (
          (opens[0]["r"], closes[-1]["r"]) if opens and closes else None
        ),
        "printed_chars": c["chars"],
      })

    # Underline fields: long rules that are not the border of any cell.
    all_cells = [c["r"] for c in cells]

    merged = {}
    for y, x0, x1 in solid_h:
      if x1 - x0 >= 35:
        merged.setdefault(round(y), []).append((x0, x1, y))

    for segs in merged.values():
      segs.sort()
      cur, joined = list(segs[0]), []
      for x0, x1, y in segs[1:]:
        if x0 <= cur[1] + 3:
          cur[1] = max(cur[1], x1)
        else:
          joined.append(tuple(cur))
          cur = [x0, x1, y]
      joined.append(tuple(cur))

      for x0, x1, y in joined:
        if x1 - x0 < 35:
          continue

        is_border = any(
          (abs(cr.y0 - y) <= 2.5 or abs(cr.y1 - y) <= 2.5)
          and min(cr.x1, x1) - max(cr.x0, x0)
          >= 0.3 * min(cr.width, x1 - x0)
          for cr in all_cells
        )
        if is_border:
          continue

        band = fitz.Rect(x0 + 1, y - 11, x1 - 1, y - 0.8)

        # Stay clear of printed text at either end of the line.
        for ch in chars:
          cr = ch["r"]
          if min(cr.y1, band.y1) - max(cr.y0, band.y0) < 0.5 * cr.height:
            continue
          if cr.x1 > band.x0 and cr.x0 < band.x0 + 25:
            band.x0 = max(band.x0, cr.x1 + 1)
          elif cr.x0 < band.x1 and cr.x1 > band.x1 - 25:
            band.x1 = min(band.x1, cr.x0 - 1)

        if band.width < 20:
          continue

        fields.append({
          "kind": "underline",
          "page": page_number,
          "rect": band,
          "label": "",
          "group": "",
          "left": self._left_label(chars, band),
          "right": self._text_in(
            chars,
            fitz.Rect(band.x1, band.y0 - 3, band.x1 + 40, band.y1 + 0.5),
          )[0],
          "above": self._text_in(
            chars,
            fitz.Rect(band.x0 - 80, band.y0 - 30, band.x1, band.y0),
          )[0],
          "band": 0,
          "stack": "",
          "printed": "",
          "anchors": [],
          "bracket": None,
          "printed_chars": [],
        })

    # Loose "年 月 日" slots that are not inside any cell.
    line_groups = []
    for ch in sorted(chars, key=lambda c: (c["r"].y0 + c["r"].y1) / 2):
      cy = (ch["r"].y0 + ch["r"].y1) / 2
      if line_groups and abs(cy - line_groups[-1][0]) <= 3:
        line_groups[-1][1].append(ch)
      else:
        line_groups.append([cy, [ch]])

    for _, ln in line_groups:
      ln.sort(key=lambda c: c["r"].x0)
      for i in range(len(ln) - 2):
        a, b, d = ln[i], ln[i + 1], ln[i + 2]

        if (a["c"], b["c"], d["c"]) != ("年", "月", "日"):
          continue

        # Real fill-in slots have blank space between the characters;
        # a printed word like 雇入年月日 does not.
        wdt = a["r"].width
        if (
          b["r"].x0 - a["r"].x1 < wdt
          or d["r"].x0 - b["r"].x1 < wdt
        ):
          continue

        center = fitz.Point(
          (a["r"].x0 + a["r"].x1) / 2,
          (a["r"].y0 + a["r"].y1) / 2,
        )
        if any(cr.contains(center) for cr in all_cells):
          continue

        prev = ln[i - 1]["r"].x1 + 0.5 if i > 0 else a["r"].x0 - 40
        slot = fitz.Rect(
          max(prev, a["r"].x0 - 40), a["r"].y0, d["r"].x1, a["r"].y1
        )

        fields.append({
          "kind": "date_slot",
          "page": page_number,
          "rect": slot,
          "label": "",
          "group": "",
          "left": self._left_label(chars, slot),
          "right": self._text_in(
            chars,
            fitz.Rect(slot.x1, slot.y0, slot.x1 + 60, slot.y1),
          )[0],
          "above": self._text_in(
            chars,
            fitz.Rect(
              slot.x0 - 100, slot.y0 - 30, slot.x1 + 100, slot.y0
            ),
          )[0],
          "band": 0,
          "stack": "",
          "printed": "年月日",
          "anchors": [a, b, d],
          "bracket": None,
          "printed_chars": [],
        })

    heights = [c["r"].height for c in chars if c["r"].height > 0]
    char_h = float(np.median(heights)) if heights else 9.0
    for f in fields:
      f["char_h"] = char_h

    fields.sort(key=lambda f: (round(f["rect"].y0 / 4), f["rect"].x0))
    for i, f in enumerate(fields, 1):
      f["id"] = f"f{page_number}_{i}"

    return fields

  # ------------------------------------------------------------------
  # catalog + overlay image for the model
  # ------------------------------------------------------------------
  def _format_field_catalog(self, fields):
    lines = []
    for f in fields:
      r = f["rect"]
      parts = [
        f["id"],
        f["kind"],
        "rect=%d,%d,%d,%d" % (r.x0, r.y0, r.x1, r.y1),
      ]
      if f["band"]:
        parts.append(f"band={f['band']}")
      if f["stack"] and f["stack"] != "1/1":
        parts.append(f"stack={f['stack']}")
      for key in ("label", "group", "left", "above", "right", "printed"):
        if f.get(key):
          parts.append(f'{key}="{f[key]}"')
      lines.append(" | ".join(parts))
    return "\n".join(lines)

  def _render_field_overlay(self, page, fields, long_edge_px=2600):
    zoom = long_edge_px / max(page.rect.width, page.rect.height)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    draw = ImageDraw.Draw(img)

    try:
      font = ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13
      )
    except Exception:
      font = ImageFont.load_default()

    for f in fields:
      r = f["rect"]
      x0 = (r.x0 - page.rect.x0) * zoom
      y0 = (r.y0 - page.rect.y0) * zoom
      x1 = (r.x1 - page.rect.x0) * zoom
      y1 = (r.y1 - page.rect.y0) * zoom
      draw.rectangle([x0, y0, x1, y1], outline=(255, 0, 0), width=1)
      draw.text(
        (x0 + 2, y0 + 1),
        f["id"].split("_", 1)[1],
        fill=(220, 0, 0),
        font=font,
      )

    buf = io.BytesIO()
    img.save(buf, format="PNG")

    return (
      "data:image/png;base64,"
      + base64.b64encode(buf.getvalue()).decode("utf-8")
    )

  # ------------------------------------------------------------------
  # field + text -> exact coordinate edits
  # ------------------------------------------------------------------
  @staticmethod
  def _ymd(text):
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", str(text))
    if not m:
      return None
    return m.group(1), str(int(m.group(2))), str(int(m.group(3)))

  def _fit_font(self, text, width, height, max_size=8.0):
    """Returns (font_size, needs_multiline)."""

    font_file = Path(
      "/usr/share/fonts/truetype/noto/NotoSansJP-Regular.ttf"
    )
    try:
      font = fitz.Font(fontfile=str(font_file))
    except Exception:
      font = fitz.Font("japan")

    single = min(max_size, max(3.5, height * 0.8))

    while single > 5.0 and font.text_length(text, fontsize=single) > width:
      single -= 0.25

    if font.text_length(text, fontsize=single) <= width:
      return single, False

    size = min(max_size, max(3.5, height * 0.8))

    while size > 3.5:
      lines, cur = 1, 0.0
      for ch in text:
        cw = font.text_length(ch, fontsize=size)
        if cur + cw > width:
          lines, cur = lines + 1, cw
        else:
          cur += cw
      if lines * size * 1.2 <= height:
        break
      size -= 0.25

    return size, True

  def _expand_field_edit(self, field, text, filename):
    text = str(text).strip()
    if not text:
      return []

    cap = field.get("char_h", 9.0)

    base = field["id"]
    rect = field["rect"]

    def edit(name, r, value, size, multiline=False):
      return dict(
        filename=filename,
        page=field["page"],
        field_name=name,
        x0=r.x0, y0=r.y0, x1=r.x1, y1=r.y1,
        text=str(value),
        coordinate_space="pdf_points",
        exact=True,
        font_size=size,
        multiline=multiline,
      )

    anchors = field["anchors"]
    by_char = {}
    for a in anchors:
      by_char.setdefault(a["c"], a["r"])

    # Date: numbers go before the printed 年 / 月 / 日.
    ymd = self._ymd(text)
    if ymd and all(k in by_char for k in "年月日"):
      ar, mr, dr = by_char["年"], by_char["月"], by_char["日"]
      top = min(ar.y0, mr.y0, dr.y0) - 1
      bot = max(ar.y1, mr.y1, dr.y1) + 1
      left = max(
        rect.x0 + (0.5 if field["kind"] == "cell" else 0.0),
        ar.x0 - 34,
      )
      parts = [
        ("y", fitz.Rect(left, top, ar.x0 - 0.3, bot), ymd[0]),
        ("m", fitz.Rect(ar.x1 + 0.3, top, mr.x0 - 0.3, bot), ymd[1]),
        ("d", fitz.Rect(mr.x1 + 0.3, top, dr.x0 - 0.3, bot), ymd[2]),
      ]
      return [
        edit(f"{base}_{k}", r, v, cap)
        for k, r, v in parts
        if r.width > 2
      ]

    # A single printed unit (年 / 歳): number goes just before it.
    if len(anchors) == 1 and field["kind"] == "cell":
      m = re.search(r"\d+", text)
      if m:
        a = anchors[0]["r"]
        r = fitz.Rect(
          max(rect.x0 + 0.5, a.x0 - 18), a.y0 - 1, a.x0 - 0.5, a.y1 + 1
        )
        return [edit(base, r, m.group(0), cap)]

    # Ordinary text field.
    x0, x1 = rect.x0 + 1.0, rect.x1 - 1.0
    if field.get("bracket"):
      opening, closing = field["bracket"]
      x0, x1 = opening.x1 + 1.0, closing.x0 - 1.0

    y0, y1 = rect.y0 + 0.5, rect.y1 - 0.5
    printed = field.get("printed_chars") or []
    if printed:
      y0 = min(c["r"].y0 for c in printed) - 1.5
      y1 = max(c["r"].y1 for c in printed) + 1.5
    elif rect.height > 14:
      mid = (rect.y0 + rect.y1) / 2
      y0, y1 = mid - 6, mid + 6

    box = fitz.Rect(x0, y0, x1, y1)
    size, multiline = self._fit_font(text, box.width - 1, box.height, max_size=cap)

    if multiline:
      box = fitz.Rect(
        rect.x0 + 1, rect.y0 + 1, rect.x1 - 1, rect.y1 - 1
      )
      size, _ = self._fit_font(text, box.width - 1, box.height, max_size=cap)
      return [edit(base, box, text, size, True)]

    return [edit(base, box, text, size)]

  # ------------------------------------------------------------------
  # integration with the agent
  # ------------------------------------------------------------------
  def _build_pdf_agent_inputs(self, input_file: Path):
    """
    Returns (content_items, renders, mode).

    mode "cells":  vector PDF without AcroForm fields -> field catalog.
    mode "coords": anything else -> the gridded-image coordinate path.
    """

    if not hasattr(self, "_pdf_field_catalogs"):
      self._pdf_field_catalogs = {}

    fields, overlays, has_widgets = [], [], False

    try:
      with fitz.open(input_file) as doc:
        has_widgets = any(
          next(iter(page.widgets() or []), None) is not None
          for page in doc
        )
        if not has_widgets:
          for index, page in enumerate(doc, 1):
            page_fields = self._extract_pdf_fields(page, index)
            fields.extend(page_fields)
            if page_fields:
              overlays.append(
                (index, self._render_field_overlay(page, page_fields))
              )
    except Exception:
      logger.exception("Field extraction failed for %s", input_file)
      fields, overlays, has_widgets = [], [], True

    if has_widgets or len(fields) < 3:
      content, renders = self._build_pdf_agent_content(input_file)
      return content, renders, "coords"

    self._pdf_field_catalogs[input_file.name] = {
      f["id"]: f for f in fields
    }

    logger.info(
      "PDF %s: detected %d fillable field(s)",
      input_file.name,
      len(fields),
    )

    content = [
      {
        "type": "input_text",
        "text": (
          f"PDF file: {input_file.name}. Each following image is one "
          f"page; every detected fillable region is outlined in red "
          f"and tagged with the number part of its id (e.g. '12' on "
          f"page 1 means field id 'f1_12')."
        ),
      }
    ]

    for page_number, data_url in overlays:
      content.append(
        {"type": "input_text", "text": f"Page {page_number}:"}
      )
      content.append(
        {"type": "input_image", "image_url": data_url, "detail": "high"}
      )

    content.append(
      {
        "type": "input_text",
        "text": (
          f"FIELD CATALOG for {input_file.name}:\n"
          + self._format_field_catalog(fields)
        ),
      }
    )

    return content, [], "cells"

  def _cell_prompt_instructions(self) -> str:
    return """
============================================================
PDF EDITING BY FIELD ID — CRITICAL
============================================================

For PDFs with a FIELD CATALOG you do NOT return coordinates. You pick
fields by id and give the text; the application positions it exactly.

Return edits in a "cell_edits" array:

{
  "filename": "example.pdf",
  "cell": "f1_14",
  "text": "2017/04/01"
}

(leave "pdf_edits", "image_edits" and "files" empty for these PDFs)

CATALOG KEYS
- kind: cell (table cell) | underline (line to write on) |
  date_slot (printed 年 月 日 outside a table)
- rect: x0,y0,x1,y1 in PDF points (only to understand layout/order)
- label: header text of the column this cell belongs to
- group: wider header spanning several columns
- left / above / right: printed text next to a field without a header
- band: cells in the same table row share the same band number
- stack=k/n: this cell is the k-th of n sub-lines inside one row block;
  if a header lists several labels, the k-th sub-line pairs with the
  k-th label (top to bottom)
- printed: text already printed inside the field (年月日, 年, 歳, （　）)

HOW TO CHOOSE
1. Work out the structure first. Repeated record rows (e.g. one worker
   per row) are the repeating band numbers; each record uses ONE band.
   Use the first band that has the table's data cells for record 1, the
   next band for record 2, and so on. Never mix records across bands.
2. Match each value to the field whose label / stack position / nearby
   text means that value.
3. Printed units are handled by the application: for a field whose
   printed text is 年月日, give a full date as YYYY/MM/DD; for printed
   年 or 歳, give the number (e.g. "9", "34"). Do not add the unit.
4. One value per field. Never put two different items into one field.
   If a field's printed text is （　）, give only what goes inside.
5. If a value belongs in a field that is not in the catalog (e.g. a
   circled choice), do not invent an id: list it in "missing_data".
6. Never guess. Unknown values go to "missing_data". Never output
   internal database UUIDs.

Final JSON keys: summary, completed, files (empty), cell_edits,
missing_data, recommendations. Human-readable text in Japanese.
"""


  def _compact_repeating_cell_edits(
    self,
    cell_edits: list[dict],
    catalogs: dict,
  ) -> list[dict]:
    """
    Compact populated repeating PDF rows upward.

    Important:
    - This does NOT remove any rows.
    - This does NOT suppress blank editable fields.
    - It only moves populated values from a later completely-populated
      row into an earlier completely-empty structurally identical row.

    Example:

        row 1: empty
        row 2: 佐藤
        row 3: 鈴木
        row 4: empty

    becomes:

        row 1: 佐藤
        row 2: 鈴木
        row 3: empty
        row 4: empty

    The subsequent _blank_field_edits() pass will make all of those
    remaining empty fields editable.
    """

    if not cell_edits:
      return cell_edits

    def round_value(value, places=1):
      try:
        return round(float(value), places)
      except (TypeError, ValueError):
        return value

    def field_layout_key(field):
      """
      Identify the logical field within a repeating row while ignoring
      its vertical position and generated field id.
      """

      rect = field["rect"]

      return (
        field.get("kind", ""),
        round_value(rect.x0),
        round_value(rect.x1),
        field.get("label", ""),
        field.get("group", ""),
        field.get("stack", ""),
        field.get("printed", ""),
        field.get("left", ""),
      )

    result = list(cell_edits)

    for filename, catalog in catalogs.items():

      # ------------------------------------------------------------
      # Group fields by repeating band.
      # ------------------------------------------------------------

      bands = {}

      for field_id, field in catalog.items():
        band = field.get("band", 0)

        if not band:
          continue

        bands.setdefault(band, []).append(
          (field_id, field)
        )

      if len(bands) < 2:
        continue

      # ------------------------------------------------------------
      # Build a structural signature for each band.
      #
      # Repeating worker rows should have the same collection of
      # logical fields, even though their y coordinates / ids differ.
      # ------------------------------------------------------------

      band_signatures = {}

      for band, fields in bands.items():
        signature = tuple(
          sorted(
            field_layout_key(field)
            for _, field in fields
          )
        )

        band_signatures[band] = signature

      # Only consider signatures that occur more than once.
      repeated_families = {}

      for band, signature in band_signatures.items():
        repeated_families.setdefault(
          signature,
          [],
        ).append(band)

      # ------------------------------------------------------------
      # Determine which catalog field ids were populated by the agent.
      # ------------------------------------------------------------

      populated_ids = set()

      for edit in result:
        if not isinstance(edit, dict):
          continue

        if edit.get("filename") not in (None, filename):
          continue

        cell_id = str(
          edit.get("cell", "")
        ).strip()

        if cell_id in catalog:
          populated_ids.add(cell_id)

      # ------------------------------------------------------------
      # Process each repeated family independently.
      # ------------------------------------------------------------

      for signature, family_bands in repeated_families.items():

        if len(family_bands) < 2:
          continue

        family_bands = sorted(family_bands)

        # Map field id -> band.
        field_to_band = {}

        # Map (band, structural field key) -> field id.
        field_lookup = {}

        for band in family_bands:
          for field_id, field in bands[band]:

            key = field_layout_key(field)

            field_to_band[field_id] = band
            field_lookup[(band, key)] = field_id

        # ----------------------------------------------------------
        # A band is occupied if the agent populated ANY field in it.
        #
        # We intentionally consider partially populated rows occupied.
        # That prevents us from accidentally moving another worker into
        # a row that already contains some information.
        # ----------------------------------------------------------

        occupied = {
          band: False
          for band in family_bands
        }

        for field_id in populated_ids:
          band = field_to_band.get(field_id)

          if band in occupied:
            occupied[band] = True

        # ----------------------------------------------------------
        # Walk top-to-bottom.
        #
        # Whenever we find an occupied row after one or more empty rows,
        # move it to the earliest available empty row.
        # ----------------------------------------------------------

        empty_bands = []

        for source_band in family_bands:

          if not occupied[source_band]:
            empty_bands.append(source_band)
            continue

          if not empty_bands:
            continue

          target_band = empty_bands.pop(0)

          logger.info(
            "Compacting PDF repeating row: "
            "filename=%s source_band=%s -> target_band=%s",
            filename,
            source_band,
            target_band,
          )

          source_field_ids = {
            field_id
            for field_id, field in bands[source_band]
          }

          # --------------------------------------------------------
          # Rewrite each cell edit belonging to source_band so that
          # it targets the equivalent field in target_band.
          # --------------------------------------------------------

          for index, edit in enumerate(result):

            if not isinstance(edit, dict):
              continue

            edit_filename = edit.get("filename")

            if edit_filename not in (None, filename):
              continue

            cell_id = str(
              edit.get("cell", "")
            ).strip()

            if cell_id not in source_field_ids:
              continue

            source_field = catalog[cell_id]

            key = field_layout_key(source_field)

            target_id = field_lookup.get(
              (target_band, key)
            )

            if not target_id:
              logger.warning(
                "Could not compact PDF field %s: "
                "no equivalent target field found in band %s",
                cell_id,
                target_band,
              )
              continue

            new_edit = dict(edit)
            new_edit["cell"] = target_id

            result[index] = new_edit

          # The source row is now effectively free to receive a later
          # populated row, so add it to the available-empty queue.
          empty_bands.append(source_band)

    return result


  def _resolve_cell_edits(
    self,
    agent_output: dict,
    input_files,
  ) -> None:
    """
    Turn agent_output["cell_edits"] into exact coordinate pdf_edits.

    For repeating PDF rows, populated rows are first compacted upward
    when an earlier structurally identical row is completely empty.

    IMPORTANT:
    Every detected field that is not populated still receives an empty
    editable field via _blank_field_edits().
    """

    catalogs = getattr(
      self,
      "_pdf_field_catalogs",
      {},
    )

    if not catalogs:
      return

    cell_edits = agent_output.get(
      "cell_edits"
    ) or []

    # --------------------------------------------------------------
    # Compact populated repeating rows BEFORE converting cell edits
    # into coordinate edits.
    # --------------------------------------------------------------

    cell_edits = self._compact_repeating_cell_edits(
      cell_edits=cell_edits,
      catalogs=catalogs,
    )

    # Keep the normalized version in agent_output for logging/debugging.
    agent_output["cell_edits"] = cell_edits

    pdf_edits = agent_output.setdefault(
      "pdf_edits",
      [],
    )

    filled = {
      name: set()
      for name in catalogs
    }

    populated_count = 0

    # --------------------------------------------------------------
    # Convert populated cell edits into exact coordinate edits.
    # --------------------------------------------------------------

    for ce in cell_edits:

      if not isinstance(ce, dict):
        continue

      cell_id = str(
        ce.get("cell", "")
      ).strip()

      filename = ce.get(
        "filename"
      )

      if filename not in catalogs:
        filename = next(
          (
            n
            for n in catalogs
            if cell_id in catalogs[n]
          ),
          None,
        )

      if (
        filename is None
        or cell_id not in catalogs[filename]
      ):
        logger.warning(
          "Unknown field id from agent: %r",
          cell_id,
        )
        continue

      edits = self._expand_field_edit(
        catalogs[filename][cell_id],
        ce.get("text", ""),
        filename,
      )

      if edits:
        filled[filename].add(
          cell_id
        )

        pdf_edits.extend(
          edits
        )

        populated_count += 1

    # --------------------------------------------------------------
    # IMPORTANT:
    #
    # Do NOT limit this to one blank row.
    #
    # Every catalog field not populated above gets an editable blank
    # field.
    # --------------------------------------------------------------

    blank_count = 0

    for filename, catalog in catalogs.items():

      for cell_id, field in catalog.items():

        if cell_id in filled[filename]:
          continue

        blank_edits = self._blank_field_edits(
          field,
          filename,
        )

        pdf_edits.extend(
          blank_edits
        )

        if blank_edits:
          blank_count += len(
            blank_edits
          )

    logger.info(
      "Resolved %d populated cell edit(s); "
      "added %d empty editable field(s); "
      "total pdf edits=%d",
      populated_count,
      blank_count,
      len(pdf_edits),
    )


  def _build_editable_pdf_from_image(
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
      "========== IMAGE TO EDITABLE PDF START =========="
    )

    logger.info(
      "Input image: %s",
      input_file,
    )

    with Image.open(
      input_file
    ) as pil_image:
      pil_image.load()

      image_width, image_height = (
        pil_image.size
      )

    logger.info(
      "Image dimensions: width=%d height=%d",
      image_width,
      image_height,
    )

    document = fitz.open()

    page = document.new_page(
      width=image_width,
      height=image_height,
    )

    # ------------------------------------------------------------
    # Preserve the original image as the PDF background.
    # ------------------------------------------------------------

    page.insert_image(
      fitz.Rect(
        0,
        0,
        image_width,
        image_height,
      ),
      filename=str(
        input_file
      ),
    )

    # ------------------------------------------------------------
    # Japanese font.
    # ------------------------------------------------------------

    font_path = Path(
      "/usr/share/fonts/truetype/noto/NotoSansJP-Regular.ttf"
    )

    font_name = "NotoSansJP"

    if font_path.exists():
      try:
        page.insert_font(
          fontfile=str(
            font_path
          ),
          fontname=font_name,
        )
      except Exception:
        logger.debug(
          "Font %r may already be registered.",
          font_name,
        )

    else:
      logger.warning(
        "Japanese font not found for editable PDF widgets: %s",
        font_path,
      )

      font_name = "helv"

    applied_count = 0
    skipped_count = 0

    used_field_names = set()

    # ------------------------------------------------------------
    # Add editable fields.
    # ------------------------------------------------------------

    for index, edit in enumerate(
      image_edits
    ):
      logger.info(
        "PROCESSING IMAGE EDIT #%d: %s",
        index + 1,
        edit,
      )

      filename = edit.get(
        "filename"
      )

      if (
        filename
        and filename != input_file.name
      ):
        logger.warning(
          "Skipping image edit because filename does not match: "
          "edit_filename=%r input_filename=%r",
          filename,
          input_file.name,
        )

        skipped_count += 1
        continue

      text = edit.get(
        "text"
      )

      if text is None:
        text = edit.get(
          "value"
        )

      if text is None:
        logger.warning(
          "Skipping image edit with no text/value: %s",
          edit,
        )

        skipped_count += 1
        continue

      x = edit.get(
        "x"
      )

      y = edit.get(
        "y"
      )

      width = edit.get(
        "width"
      )

      height = edit.get(
        "height"
      )

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
          "Skipping editable PDF field with invalid coordinates: %s",
          edit,
        )

        skipped_count += 1
        continue

      if (
        width <= 0
        or height <= 0
      ):
        logger.warning(
          "Skipping editable PDF field with invalid dimensions: %s",
          edit,
        )

        skipped_count += 1
        continue

      rect = fitz.Rect(
        x,
        y,
        x + width,
        y + height,
      )

      # ----------------------------------------------------------
      # Generate a unique field name.
      # ----------------------------------------------------------

      requested_field_name = (
        str(
          edit.get(
            "field_name"
          )
          or ""
        ).strip()
      )

      if not requested_field_name:
        requested_field_name = (
          f"kenchiku_image_field_{index + 1}"
        )

      field_name = (
        requested_field_name
      )

      suffix = 2

      while field_name in used_field_names:
        field_name = (
          f"{requested_field_name}_{suffix}"
        )
        suffix += 1

      used_field_names.add(
        field_name
      )

      # ----------------------------------------------------------
      # Determine font size.
      # ----------------------------------------------------------

      font_size = max(
        4,
        min(
          12,
          height * 0.60,
        ),
      )

      logger.info(
        "Creating editable image PDF field: "
        "field_name=%r text=%r rect=%s font_size=%.2f",
        field_name,
        text,
        rect,
        font_size,
      )

      try:
        widget = fitz.Widget()

        widget.field_name = (
          field_name
        )

        widget.field_type = (
          fitz.PDF_WIDGET_TYPE_TEXT
        )

        widget.rect = rect

        widget.field_value = str(
          text
        )

        widget.text_font = (
          font_name
        )

        widget.text_fontsize = (
          float(font_size)
        )

        widget.text_color = (
          0,
          0,
          0,
        )

        widget.border_color = None
        widget.fill_color = None

        added = page.add_widget(widget)

        doc = page.parent
        xref = getattr(added, "xref", 0) or widget.xref

        if not xref:
          # Fallback: find the widget we just created by its field name.
          for w in page.widgets() or []:
            if w.field_name == field_name:
              xref = w.xref
              widget = w
              break

        try:
          if xref:
            doc.xref_set_key(xref, "Q", "1")
            widget.xref = xref
            widget.update()
            logger.info(
              "Widget %r Q=%s", field_name, doc.xref_get_key(xref, "Q")
            )
          else:
            logger.warning("No xref found for widget %r; not centered", field_name)
        except Exception:
          logger.exception("Could not center widget %r", field_name)

        applied_count += 1

      except Exception:
        logger.exception(
          "Failed to create editable image PDF field: %s",
          edit,
        )

        skipped_count += 1

    # ------------------------------------------------------------
    # Save.
    # ------------------------------------------------------------

    output_path = (
      output_dir
      / f"{input_file.stem}.pdf"
    )

    document.need_appearances(True)

    document.save(
      output_path,
      garbage=4,
      deflate=True,
    )

    document.close()

    logger.info(
      "Saved editable PDF with %d field(s): %s",
      applied_count,
      output_path,
    )

    logger.info(
      "IMAGE PDF SUMMARY: "
      "requested=%d applied=%d skipped=%d",
      len(image_edits),
      applied_count,
      skipped_count,
    )

    # ------------------------------------------------------------
    # Verify widgets after saving.
    # ------------------------------------------------------------

    try:
      verify_document = fitz.open(
        output_path
      )

      widget_count = 0

      for page_index in range(
        verify_document.page_count
      ):
        page = verify_document[
          page_index
        ]

        widgets = page.widgets()

        if not widgets:
          continue

        for widget in widgets:
          widget_count += 1

          logger.info(
            "IMAGE OUTPUT PDF WIDGET: "
            "page=%d field=%r rect=%s value=%r",
            page_index + 1,
            widget.field_name,
            widget.rect,
            widget.field_value,
          )

      logger.info(
        "IMAGE OUTPUT PDF VERIFIED: "
        "widgets=%d size=%d",
        widget_count,
        output_path.stat().st_size,
      )

      verify_document.close()

    except Exception:
      logger.exception(
        "Failed to verify editable image PDF: %s",
        output_path,
      )

    logger.info(
      "========== IMAGE TO EDITABLE PDF END =========="
    )

    return output_path


  def _apply_pdf_edits(
    self,
    input_file: Path,
    pdf_edits: list[dict],
    output_dir: Path,
  ) -> Path:
    output_dir.mkdir(
      parents=True,
      exist_ok=True,
    )

    logger.info(
      "========== PDF EDIT START =========="
    )

    logger.info(
      "Applying %d PDF edit(s) to %s",
      len(pdf_edits),
      input_file.name,
    )

    logger.info(
      "PDF INPUT: path=%s size=%d",
      input_file,
      input_file.stat().st_size,
    )

    document = fitz.open(
      input_file,
    )

    logger.info(
      "PDF PAGE COUNT: %d",
      document.page_count,
    )

    # ------------------------------------------------------------
    # Log page geometry.
    # ------------------------------------------------------------

    for page_index in range(
      document.page_count
    ):
      page = document[
        page_index
      ]

      logger.info(
        "PDF PAGE GEOMETRY: "
        "page=%d rect=%s width=%.2f height=%.2f rotation=%d",
        page_index + 1,
        page.rect,
        page.rect.width,
        page.rect.height,
        page.rotation,
      )

    # ------------------------------------------------------------
    # Japanese font.
    # ------------------------------------------------------------

    font_path = Path(
      "/usr/share/fonts/truetype/noto/NotoSansJP-Regular.ttf"
    )

    if font_path.exists():
      font_name = "NotoSansJP"

      logger.info(
        "Using PDF form font: %s",
        font_path,
      )

    else:
      logger.warning(
        "Japanese font not found for PDF editing: %s",
        font_path,
      )

      font_path = None
      font_name = "helv"

    # ------------------------------------------------------------
    # Register the font on every page where we may create widgets.
    # ------------------------------------------------------------

    if font_path:
      for page_index in range(
        document.page_count
      ):
        page = document[
          page_index
        ]

        try:
          page.insert_font(
            fontfile=str(
              font_path
            ),
            fontname=font_name,
          )
        except Exception:
          logger.debug(
            "PDF font %r may already be registered on page %d.",
            font_name,
            page_index + 1,
          )

    # ------------------------------------------------------------
    # Inspect existing AcroForm widgets.
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
          "Found PDF form field: "
          "page=%d name=%r type=%s value=%r",
          page_index + 1,
          field_name,
          widget.field_type,
          widget.field_value,
        )

    logger.info(
      "Existing AcroForm field count: %d",
      len(widgets_by_name),
    )

    # ------------------------------------------------------------
    # Helper for generating a unique field name.
    # ------------------------------------------------------------

    def make_unique_field_name(
      requested_name: str,
    ) -> str:
      base_name = (
        requested_name.strip()
        if requested_name
        else "kenchiku_pdf_field"
      )

      if base_name not in widgets_by_name:
        return base_name

      suffix = 2

      while (
        f"{base_name}_{suffix}"
        in widgets_by_name
      ):
        suffix += 1

      return f"{base_name}_{suffix}"

    # ------------------------------------------------------------
    # Helper for determining the radio button on-state.
    # ------------------------------------------------------------

    def get_radio_on_state(
      widget,
    ) -> str | None:
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
            return str(
              state
            )

      except Exception:
        logger.exception(
          "Could not inspect radio button states."
        )

      return None

    # ------------------------------------------------------------
    # Helper for selecting an existing radio button.
    # ------------------------------------------------------------

    def apply_radio_edit(
      field_name: str,
      value,
      widgets: list[tuple[int, object]],
    ) -> bool:
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

      # ----------------------------------------------------------
      # First look for a direct appearance-state match.
      # ----------------------------------------------------------

      for (
        page_index,
        widget,
      ) in widgets:
        on_state = get_radio_on_state(
          widget
        )

        logger.info(
          "Radio widget inspection: "
          "field=%r page=%d field_value=%r on_state=%r",
          field_name,
          page_index + 1,
          widget.field_value,
          on_state,
        )

        if (
          on_state is not None
          and on_state == requested_value
        ):
          selected_widget = widget
          selected_on_state = on_state
          break

      # ----------------------------------------------------------
      # Try the current field value.
      # ----------------------------------------------------------

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
            selected_on_state = (
              get_radio_on_state(
                widget
              )
            )

            break

      # ----------------------------------------------------------
      # Inspect /Opt if necessary.
      # ----------------------------------------------------------

      if selected_widget is None:
        try:
          first_widget = widgets[
            0
          ][1]

          widget_source = (
            document.xref_object(
              first_widget.xref,
              compressed=False,
            )
          )

          parent_match = re.search(
            r"/Parent\s+(\d+)\s+0\s+R",
            widget_source,
          )

          if parent_match:
            parent_xref = int(
              parent_match.group(
                1
              )
            )

            parent_source = (
              document.xref_object(
                parent_xref,
                compressed=False,
              )
            )

            opt_match = re.search(
              r"/Opt\s*\[(.*?)\]",
              parent_source,
              re.DOTALL,
            )

            if opt_match:
              opt_contents = (
                opt_match.group(
                  1
                )
              )

              # Handle UTF-16BE hexadecimal PDF strings.
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

              for (
                option_index,
                option,
              ) in enumerate(
                decoded_options
              ):
                if (
                  option is None
                  or option.strip()
                  != requested_value
                ):
                  continue

                if (
                  option_index
                  >= len(widgets)
                ):
                  break

                selected_page_index, selected_widget = (
                  widgets[
                    option_index
                  ]
                )

                selected_on_state = (
                  get_radio_on_state(
                    selected_widget
                  )
                )

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
            "Failed to inspect radio /Opt values: "
            "field=%r",
            field_name,
          )

      if selected_widget is None:
        logger.warning(
          "Could not map radio-button value %r "
          "to field %r",
          requested_value,
          field_name,
        )

        return False

      if selected_on_state is None:
        logger.warning(
          "Could not determine radio-button on-state: "
          "field=%r requested=%r",
          field_name,
          requested_value,
        )

        return False

      # ----------------------------------------------------------
      # Turn all options off.
      # ----------------------------------------------------------

      for (
        page_index,
        widget,
      ) in widgets:
        try:
          widget.field_value = "Off"
          widget.update()

        except Exception:
          logger.exception(
            "Failed to turn radio option off: "
            "field=%r page=%d",
            field_name,
            page_index + 1,
          )

      # ----------------------------------------------------------
      # Turn requested option on.
      # ----------------------------------------------------------

      try:
        selected_widget.field_value = (
          selected_on_state
        )

        selected_widget.update()

        logger.info(
          "Selected radio option: "
          "field=%r requested=%r on_state=%r",
          field_name,
          requested_value,
          selected_on_state,
        )

        return True

      except Exception:
        logger.exception(
          "Failed to select radio option: "
          "field=%r requested=%r",
          field_name,
          requested_value,
        )

        return False

    ruling_line_cache: dict = {}
    applied_count = 0
    skipped_count = 0

    # ------------------------------------------------------------
    # Apply every agent edit.
    # ------------------------------------------------------------

    for edit_index, edit in enumerate(
      pdf_edits
    ):
      logger.info(
        "PROCESSING PDF EDIT #%d: %s",
        edit_index + 1,
        json.dumps(
          edit,
          ensure_ascii=False,
        ),
      )

      filename = edit.get(
        "filename"
      )

      if (
        filename
        and filename != input_file.name
      ):
        logger.warning(
          "Skipping PDF edit because filename does not match: "
          "edit_filename=%r input_filename=%r",
          filename,
          input_file.name,
        )

        skipped_count += 1
        continue

      # ==========================================================
      # Existing AcroForm field
      # ==========================================================

      field_name = edit.get(
        "field_name"
      )

      if (
        field_name
        and field_name in widgets_by_name
      ):
        value = edit.get(
          "value"
        )

        if value is None:
          value = edit.get(
            "text"
          )

        if value is None:
          logger.warning(
            "Skipping AcroForm edit with no value: %s",
            edit,
          )

          skipped_count += 1
          continue

        widgets = widgets_by_name[
          field_name
        ]

        field_type = widgets[
          0
        ][1].field_type

        logger.info(
          "Applying existing AcroForm field: "
          "field=%r type=%s value=%r",
          field_name,
          field_type,
          value,
        )

        # --------------------------------------------------------
        # Radio button
        # --------------------------------------------------------

        if field_type == (
          fitz.PDF_WIDGET_TYPE_RADIOBUTTON
        ):
          if apply_radio_edit(
            field_name,
            value,
            widgets,
          ):
            applied_count += 1
          else:
            skipped_count += 1

          continue

        applied_this_edit = False

        # --------------------------------------------------------
        # Text / checkbox / combo / list.
        # --------------------------------------------------------

        for (
          page_index,
          widget,
        ) in widgets:
          try:
            if field_type == (
              fitz.PDF_WIDGET_TYPE_TEXT
            ):
              widget.field_value = str(
                value
              )

            elif field_type == (
              fitz.PDF_WIDGET_TYPE_CHECKBOX
            ):
              if isinstance(
                value,
                bool,
              ):
                checked = value
              else:
                checked = (
                  str(
                    value
                  )
                  .strip()
                  .lower()
                  in {
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
                )

              widget.field_value = (
                checked
              )

            elif field_type == (
              fitz.PDF_WIDGET_TYPE_COMBOBOX
            ):
              widget.field_value = str(
                value
              )

            elif field_type == (
              fitz.PDF_WIDGET_TYPE_LISTBOX
            ):
              widget.field_value = str(
                value
              )

            else:
              logger.warning(
                "Unsupported AcroForm field type: "
                "field=%r type=%s",
                field_name,
                field_type,
              )

              continue

            widget.update()

            applied_this_edit = True

            logger.info(
              "Updated existing AcroForm field: "
              "page=%d field=%r value=%r",
              page_index + 1,
              field_name,
              value,
            )

          except Exception:
            logger.exception(
              "Failed to update existing AcroForm field: "
              "field=%r page=%d",
              field_name,
              page_index + 1,
            )

        if applied_this_edit:
          applied_count += 1
        else:
          skipped_count += 1

        continue

      # ==========================================================
      # Agent specified a field_name, but the PDF does not contain
      # that field.
      #
      # If coordinates are also present, create the field at those
      # coordinates.
      # ==========================================================

      if (
        field_name
        and field_name not in widgets_by_name
      ):
        logger.info(
          "Agent supplied field_name=%r but no existing "
          "AcroForm field was found. "
          "Will create a new editable field if coordinates exist.",
          field_name,
        )

      # ==========================================================
      # New coordinate-based editable field
      # ==========================================================

      page_number = edit.get(
        "page"
      )

      text = edit.get(
        "text"
      )

      if text is None:
        text = edit.get(
          "value"
        )

      try:
        page_number = int(
          page_number
        )

        x0 = float(
          edit.get("x0")
        )

        y0 = float(
          edit.get("y0")
        )

        x1 = float(
          edit.get("x1")
        )

        y1 = float(
          edit.get("y1")
        )

      except (
        TypeError,
        ValueError,
      ):
        logger.warning(
          "Skipping coordinate PDF edit with invalid "
          "coordinates: %s",
          edit,
        )

        skipped_count += 1
        continue

      if text is None:
        logger.warning(
          "Skipping coordinate PDF edit with no text/value: %s",
          edit,
        )

        skipped_count += 1
        continue

      page_index = (
        page_number - 1
      )

      if (
        page_index < 0
        or page_index >= document.page_count
      ):
        logger.warning(
          "Skipping coordinate PDF edit with invalid page: "
          "page=%r edit=%s",
          page_number,
          edit,
        )

        skipped_count += 1
        continue

      page = document[
        page_index
      ]

      rect = fitz.Rect(
        min(x0, x1),
        min(y0, y1),
        max(x0, x1),
        max(y0, y1),
      )

      if (
        rect.width <= 0
        or rect.height <= 0
      ):
        logger.warning(
          "Skipping coordinate PDF edit with invalid rectangle: "
          "page=%d rect=%s text=%r",
          page_number,
          rect,
          text,
        )

        skipped_count += 1
        continue

      if not edit.get("exact"):
        rect = self._snap_pdf_rect_to_ruling_lines(
          page=page,
          rect=rect,
          line_cache=ruling_line_cache,
        )

      text_value = str(
        text
      )

      # ----------------------------------------------------------
      # Calculate an appropriate font size.
      # ----------------------------------------------------------

      multiline = bool(edit.get("multiline"))

      if edit.get("font_size"):
        font_size = float(edit["font_size"])
      else:
        font_size = min(
          12.0,
          max(
            4.0,
            rect.height * 0.65,
          ),
        )

      # ----------------------------------------------------------
      # Measure text and shrink until it fits horizontally.
      # ----------------------------------------------------------

      try:
        if font_path:
          measure_font = fitz.Font(
            fontfile=str(
              font_path
            ),
          )
        else:
          measure_font = fitz.Font(
            "helv"
          )

        text_width = (
          measure_font.text_length(
            text_value,
            fontsize=font_size,
          )
        )

        while (
          text_width > rect.width - 0.5
          and font_size > 3.0
          and not multiline
        ):
          font_size -= 0.25

          text_width = (
            measure_font.text_length(
              text_value,
              fontsize=font_size,
            )
          )

      except Exception:
        logger.exception(
          "Failed to measure PDF text: %r",
          text_value,
        )

        text_width = (
          len(text_value)
          * font_size
        )

      # ----------------------------------------------------------
      # Use agent-provided field_name when available.
      # Otherwise create a stable unique field name.
      # ----------------------------------------------------------

      requested_field_name = (
        str(field_name).strip()
        if field_name
        else ""
      )

      if not requested_field_name:
        requested_field_name = (
          f"kenchiku_pdf_field_{edit_index + 1}"
        )

      actual_field_name = (
        make_unique_field_name(
          requested_field_name
        )
      )

      logger.info(
        "CREATING NEW EDITABLE PDF FIELD: "
        "edit_index=%d page=%d "
        "field_name=%r text=%r "
        "rect=%s width=%.2f height=%.2f "
        "font_size=%.2f text_width=%.2f",
        edit_index,
        page_number,
        actual_field_name,
        text_value,
        rect,
        rect.width,
        rect.height,
        font_size,
        text_width,
      )

      try:
        self._add_editable_pdf_text_field(
          page=page,
          rect=rect,
          field_name=actual_field_name,
          value=text_value,
          font_name=font_name,
          font_size=font_size,
          multiline=multiline,
        )

        # Keep our in-memory registry current so another edit cannot
        # accidentally reuse the same field name.
        widgets_by_name.setdefault(
          actual_field_name,
          [],
        ).append(
          (
            page_index,
            None,
          )
        )

        applied_count += 1

      except Exception:
        logger.exception(
          "Failed to create editable PDF field: %s",
          edit,
        )

        skipped_count += 1

    # ------------------------------------------------------------
    # Save completed PDF.
    # ------------------------------------------------------------

    output_path = (
      output_dir
      / input_file.name
    )

    document.need_appearances(True)

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
      "PDF EDIT SUMMARY: "
      "requested=%d applied=%d skipped=%d",
      len(pdf_edits),
      applied_count,
      skipped_count,
    )

    # ------------------------------------------------------------
    # Reopen the output and verify that widgets actually exist.
    # ------------------------------------------------------------

    try:
      verify_document = fitz.open(
        output_path
      )

      output_widget_count = 0

      for page_index in range(
        verify_document.page_count
      ):
        page = verify_document[
          page_index
        ]

        widgets = page.widgets()

        if not widgets:
          continue

        for widget in widgets:
          output_widget_count += 1

          logger.info(
            "OUTPUT PDF WIDGET: "
            "page=%d field=%r type=%s rect=%s value=%r",
            page_index + 1,
            widget.field_name,
            widget.field_type,
            widget.rect,
            widget.field_value,
          )

      logger.info(
        "OUTPUT PDF VERIFIED: "
        "pages=%d widgets=%d size=%d",
        verify_document.page_count,
        output_widget_count,
        output_path.stat().st_size,
      )

      verify_document.close()

    except Exception:
      logger.exception(
        "Failed to verify output PDF: %s",
        output_path,
      )

    if (
      pdf_edits
      and applied_count == 0
    ):
      logger.error(
        "NO PDF EDITS WERE APPLIED: "
        "requested=%d skipped=%d output=%s",
        len(pdf_edits),
        skipped_count,
        output_path,
      )

    logger.info(
      "========== PDF EDIT END =========="
    )

    return output_path


  def _blank_field_edits(self, field, filename):
    """Empty editable field(s) for a detected field (same geometry rules)."""

    anchors = field["anchors"]
    chars = {a["c"] for a in anchors}

    if all(k in chars for k in "年月日"):
      sample = "2000/01/01"
    elif len(anchors) == 1 and field["kind"] == "cell":
      sample = "0"
    else:
      sample = "x"

    rect = field["rect"]

    if (
      field["kind"] == "cell"
      and sample == "x"
      and not field.get("bracket")
      and not field.get("printed_chars")
      and rect.height >= 30
    ):
      return [dict(
        filename=filename, page=field["page"], field_name=field["id"],
        x0=rect.x0 + 1, y0=rect.y0 + 1, x1=rect.x1 - 1, y1=rect.y1 - 1,
        text="", coordinate_space="pdf_points", exact=True,
        font_size=field.get("char_h", 9.0), multiline=True,
      )]

    edits = self._expand_field_edit(field, sample, filename)
    for e in edits:
      e["text"] = ""
    return edits


  def _pdf_prompt_instructions(
    self,
  ) -> str:
    return """
============================================================
PDF EDITING — CRITICAL
============================================================

One or more uploaded files are PDF forms. For each PDF you are given:

- one gridded IMAGE per page (with the exact pixel width/height stated
  in the text just before the image), and
- a list of the PDF's real AcroForm fields, if any.

The application, NOT you, creates the completed PDF. You return
machine-readable edits in "pdf_edits". Every value written into the PDF
becomes an EDITABLE AcroForm field.

------------------------------------------------------------
1. EXISTING ACROFORM FIELDS — HIGHEST PRIORITY
------------------------------------------------------------

If a listed AcroForm field corresponds to the value, use its EXACT
field_name. Never use coordinates for a value that has a matching
AcroForm field.

{
  "filename": "sample-form.pdf",
  "field_name": "applicant.name",
  "value": "佐藤 健一"
}

Checkboxes use true/false. Radio buttons and dropdowns use the logical
option value.

------------------------------------------------------------
2. NEW EDITABLE FIELDS (coordinates)
------------------------------------------------------------

If no AcroForm field exists for the value, return:

{
  "filename": "example.pdf",
  "page": 1,
  "field_name": "worker_1_name",
  "x0": 215,
  "y0": 290,
  "x1": 330,
  "y1": 312,
  "text": "佐藤 健一"
}

field_name is REQUIRED, unique within the PDF, descriptive,
machine-readable and stable (e.g. worker_1_name, worker_1_birth_date,
company_name, form_created_date). Never use the displayed value as the
field_name.

------------------------------------------------------------
3. COORDINATE SYSTEM — NORMALIZED 0-1000 (OVERRIDES ANY OTHER INSTRUCTION)
------------------------------------------------------------

x0, y0, x1, y1 are NORMALIZED coordinates of the page image:

- x: 0 = left edge of the image, 1000 = right edge
- y: 0 = top edge of the image, 1000 = bottom edge
- origin = top-left; x increases right, y increases downward
- NOT pixels, NOT PDF points

Read positions from the red grid labels on the image (every 50 units).
Do not estimate pixel positions.

HOW TO MEASURE:
1. Find the target cell/blank.
2. Locate its LEFT, RIGHT, TOP and BOTTOM borders against the nearest
   grid lines and interpolate between them.
3. Return a rectangle just INSIDE those borders.
4. Re-check: all four edges must lie on the borders of ONE cell
   (one worker block, one printed line, one column).

------------------------------------------------------------
4. THE RECTANGLE MUST BE THE BLANK AREA
------------------------------------------------------------

The rectangle must cover the writable blank, never the printed label.
For "氏名: ________" the rectangle covers the blank after 氏名, not the
word 氏名.

Never place a value on top of large printed title text (for example the
form title). A "（　年　月　日 作成）" creation-date field is a small blank
line below the title, not the title itself.

Many cells contain pre-printed "年　月　日" or "年　歳" slots. Treat the
whole slot (the full printed "年 月 日" span) as the blank area and
write the full date into it.

------------------------------------------------------------
5. TABLES — ROW/COLUMN INTEGRITY
------------------------------------------------------------

BEFORE choosing any coordinates, work out the table structure:

- Identify the header rows and the data rows.
- Identify how many printed LINES each record (worker) occupies. In
  worker rosters each worker is often a BLOCK of 2 printed lines
  separated by a dotted line (e.g. the upper line holds 雇入年月日 /
  生年月日 / 電話 / 健康診断日, and the lower line holds 経験年数 /
  年齢 / 家族連絡先 / 血圧). Each printed line is a separate target.
- Each worker occupies ONE horizontal block; each attribute belongs to
  its COLUMN within that block.
- Never transpose rows and columns, and never put one worker's value
  in another worker's block.
- Never place worker data in the document header or in the column
  header cells. Data starts BELOW the header rows.

For every edit verify: which worker block? which printed line inside
the block? which column? Are the rectangle's y-values inside that
line's borders and the x-values inside that column's borders?

All values for the same worker and same printed line must share
approximately the same y0/y1.

------------------------------------------------------------
6. COMPLETENESS, EXISTING VALUES, MISSING DATA
------------------------------------------------------------

- Every value that should be entered must produce an edit.
- If a value cannot be reliably determined or located, do NOT guess:
  report it in "missing_data".
- Preserve existing values, labels, headers, instructions, tables and
  footers. Do not overwrite existing values unless clearly required.
- Do NOT claim you created or modified the PDF. Return edits only.

------------------------------------------------------------
7. PDF OUTPUT
------------------------------------------------------------

Return the edits in the "pdf_edits" array of the final JSON (alongside
summary / completed / files / missing_data / recommendations). "files"
stays an empty array for PDF jobs.
"""


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

    current_date = datetime.now(
      ZoneInfo("Asia/Tokyo")
    ).strftime("%Y年%-m月%-d日")


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
PROCESSING CONTEXT
============================================================

Current date in Japan:
{current_date}

IMPORTANT:
- Treat the above date as the authoritative current processing date.
- When a form asks for the date it is being created, prepared, or
  completed, use this date unless the form explicitly provides a
  different creation/completion date.
- In particular, fields such as 作成日, 作成年月日,
  （年 月 日 作成）, Date Created, or Date Prepared should use:
  {current_date}
- This date is provided by the application and does not need to come
  from the Kenchiku data graph.
- Do NOT put this date into historical/event fields such as 生年月日,
  入社日, 雇入年月日, 資格取得日, 工事開始日, 工事完了日, etc.

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

→ Use the CURRENT DATE IN JAPAN provided in the PROCESSING CONTEXT
section when no explicit date is provided.

2. FORM COMPLETION DATE

Examples:
- 完成日
- 完了日
- Date Completed

→ If the field clearly means the date this form is being completed,
use the Current date in Japan provided in the PROCESSING CONTEXT section above.

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
