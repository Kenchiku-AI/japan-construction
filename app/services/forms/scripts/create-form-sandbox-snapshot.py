from vercel.sandbox import Sandbox


DEPENDENCIES = [
  "libreoffice",
  "libreoffice-calc",
  "libreoffice-writer",
  "libreoffice-impress",
  "poppler-utils",
  "imagemagick",
  "tesseract-ocr",
  "tesseract-ocr-jpn",
  "file",
  "unzip",
  "zip",
  "curl",
  "ca-certificates",
  "fonts-noto-cjk",
]


FORM_CONVERT = r"""#!/usr/bin/env bash

set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: form-convert <input> <output-directory>" >&2
  exit 1
fi

INPUT="$1"
OUTPUT_DIR="$2"

if [ ! -f "$INPUT" ]; then
  echo "Input file does not exist: $INPUT" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

EXTENSION="${INPUT##*.}"
EXTENSION="$(printf '%s' "$EXTENSION" | tr '[:upper:]' '[:lower:]')"

case "$EXTENSION" in
  xls)
    FORMAT="xlsx"
    ;;
  xlsx)
    FORMAT="xls"
    ;;
  xlsm)
    FORMAT="xlsx"
    ;;
  doc)
    FORMAT="docx"
    ;;
  docx)
    FORMAT="pdf"
    ;;
  ppt)
    FORMAT="pptx"
    ;;
  pptx)
    FORMAT="pdf"
    ;;
  ods)
    FORMAT="xlsx"
    ;;
  *)
    echo "Unsupported conversion from .$EXTENSION" >&2
    exit 1
    ;;
esac

libreoffice \
  --headless \
  --convert-to "$FORMAT" \
  --outdir "$OUTPUT_DIR" \
  "$INPUT"

EXPECTED="$OUTPUT_DIR/$(basename "${INPUT%.*}").$FORMAT"

if [ ! -f "$EXPECTED" ]; then
  echo "Conversion did not produce expected file:"
  echo "$EXPECTED"
  exit 1
fi

echo "$EXPECTED"
"""


FORM_INSPECT = r"""#!/usr/bin/env bash

set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: form-inspect <file>" >&2
  exit 1
fi

FILE="$1"

if [ ! -f "$FILE" ]; then
  echo "File does not exist: $FILE" >&2
  exit 1
fi

TYPE="$(file -b "$FILE")"
EXTENSION="${FILE##*.}"
EXTENSION="$(printf '%s' "$EXTENSION" | tr '[:upper:]' '[:lower:]')"

echo "============================================================"
echo "FILE INSPECTION"
echo "============================================================"
echo
echo "Path:"
echo "$FILE"
echo
echo "Extension:"
echo ".$EXTENSION"
echo
echo "Detected type:"
echo "$TYPE"
echo

case "$EXTENSION" in
  xls|xlsx|xlsm)
    echo "============================================================"
    echo "SPREADSHEET STRUCTURE"
    echo "============================================================"
    echo

    python /usr/local/bin/inspect_excel.py "$FILE"
    ;;

  pdf)
    echo "============================================================"
    echo "PDF TEXT"
    echo "============================================================"
    echo

    pdftotext -layout "$FILE" - || true

    echo
    echo "============================================================"
    echo "PDF METADATA"
    echo "============================================================"
    echo

    pdfinfo "$FILE" || true
    ;;

  docx)
    echo "============================================================"
    echo "DOCX CONTENT"
    echo "============================================================"
    echo

    python - "$FILE" <<'PY'
from pathlib import Path
from docx import Document
import json
import sys

path = Path(sys.argv[1])
document = Document(path)

paragraphs = [
  paragraph.text
  for paragraph in document.paragraphs
  if paragraph.text.strip()
]

tables = []

for table in document.tables:
  rows = []

  for row in table.rows:
    rows.append([
      cell.text
      for cell in row.cells
    ])

  tables.append(rows)

print(
  json.dumps(
    {
      "paragraphs": paragraphs,
      "tables": tables,
    },
    ensure_ascii=False,
    indent=2,
  )
)
PY
    ;;

  doc)
    echo "Legacy DOC format detected."
    echo "Use form-convert to convert it to DOCX or PDF."
    ;;

  png|jpg|jpeg|tif|tiff|bmp|webp)
    echo "============================================================"
    echo "IMAGE INFORMATION"
    echo "============================================================"
    echo

    identify "$FILE" || true

    echo
    echo "============================================================"
    echo "OCR"
    echo "============================================================"
    echo

    tesseract \
      "$FILE" \
      stdout \
      -l jpn+eng \
      --psm 6 \
      2>/dev/null || true
    ;;

  csv)
    echo "============================================================"
    echo "CSV CONTENT"
    echo "============================================================"
    echo

    python - "$FILE" <<'PY'
import csv
import json
import sys

path = sys.argv[1]

with open(
  path,
  "r",
  encoding="utf-8-sig",
  newline="",
) as file:
  reader = csv.reader(file)
  rows = list(reader)

print(
  json.dumps(
    {
      "rows": rows,
      "row_count": len(rows),
    },
    ensure_ascii=False,
    indent=2,
  )
)
PY
    ;;

  *)
    echo "No specialized inspector exists for .$EXTENSION."
    echo
    echo "Use file-specific conversion or inspection utilities."
    ;;
esac
"""


FORM_VERIFY = r"""#!/usr/bin/env bash

set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: form-verify <file>" >&2
  exit 1
fi

FILE="$1"

if [ ! -f "$FILE" ]; then
  echo "File does not exist: $FILE" >&2
  exit 1
fi

EXTENSION="${FILE##*.}"
EXTENSION="$(printf '%s' "$EXTENSION" | tr '[:upper:]' '[:lower:]')"

echo "Verifying:"
echo "$FILE"
echo

case "$EXTENSION" in
  xls)
    TEMP_DIR="$(mktemp -d)"

    trap 'rm -rf "$TEMP_DIR"' EXIT

    libreoffice \
      --headless \
      --convert-to xlsx \
      --outdir "$TEMP_DIR" \
      "$FILE" >/tmp/form-verify.log 2>&1

    CONVERTED="$TEMP_DIR/$(basename "${FILE%.*}").xlsx"

    if [ ! -f "$CONVERTED" ]; then
      cat /tmp/form-verify.log >&2
      echo "XLS verification failed." >&2
      exit 1
    fi

    python /usr/local/bin/inspect_excel.py "$CONVERTED" \
      >/dev/null

    echo "XLS verification passed."
    ;;

  xlsx|xlsm)
    python /usr/local/bin/inspect_excel.py "$FILE" \
      >/dev/null

    echo "Spreadsheet verification passed."
    ;;

  pdf)
    pdfinfo "$FILE" >/dev/null
    echo "PDF verification passed."
    ;;

  docx)
    python - "$FILE" <<'PY'
from docx import Document
import sys

Document(sys.argv[1])
print("DOCX verification passed.")
PY
    ;;

  png|jpg|jpeg|tif|tiff|bmp|webp)
    identify "$FILE" >/dev/null
    echo "Image verification passed."
    ;;

  *)
    echo "Basic file verification passed."
    ;;
esac
"""


INSPECT_EXCEL = r"""#!/usr/bin/env python3

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from openpyxl import load_workbook


def convert_xls_to_xlsx(path: Path, output_dir: Path) -> Path:
  result = subprocess.run(
    [
      "libreoffice",
      "--headless",
      "--convert-to",
      "xlsx",
      "--outdir",
      str(output_dir),
      str(path),
    ],
    capture_output=True,
    text=True,
  )

  if result.returncode != 0:
    raise RuntimeError(
      "LibreOffice failed to convert XLS to XLSX.\n"
      f"stdout: {result.stdout}\n"
      f"stderr: {result.stderr}"
    )

  converted = output_dir / f"{path.stem}.xlsx"

  if not converted.exists():
    raise RuntimeError(
      "LibreOffice reported success but did not create "
      f"{converted}"
    )

  return converted


def inspect_xlsx(path: Path) -> dict:
  workbook = load_workbook(
    path,
    data_only=False,
    read_only=False,
  )

  sheets = []

  for worksheet in workbook.worksheets:
    cells = []

    for row in worksheet.iter_rows():
      for cell in row:
        if cell.value is None:
          continue

        value = cell.value

        if not isinstance(
          value,
          (str, int, float, bool),
        ):
          value = str(value)

        cells.append({
          "coordinate": cell.coordinate,
          "value": value,
          "data_type": cell.data_type,
        })

    merged_cells = [
      str(cell_range)
      for cell_range in worksheet.merged_cells.ranges
    ]

    column_dimensions = {}

    for key, dimension in worksheet.column_dimensions.items():
      if (
        dimension.width is not None
        or dimension.hidden
      ):
        column_dimensions[key] = {
          "width": dimension.width,
          "hidden": dimension.hidden,
        }

    row_dimensions = {}

    for key, dimension in worksheet.row_dimensions.items():
      if (
        dimension.height is not None
        or dimension.hidden
      ):
        row_dimensions[str(key)] = {
          "height": dimension.height,
          "hidden": dimension.hidden,
        }

    sheets.append({
      "name": worksheet.title,
      "max_row": worksheet.max_row,
      "max_column": worksheet.max_column,
      "merged_cells": merged_cells,
      "freeze_panes": (
        str(worksheet.freeze_panes)
        if worksheet.freeze_panes
        else None
      ),
      "column_dimensions": column_dimensions,
      "row_dimensions": row_dimensions,
      "cells": cells,
    })

  return {
    "filename": path.name,
    "sheet_count": len(sheets),
    "sheets": sheets,
  }


def main() -> None:
  if len(sys.argv) != 2:
    print(
      "Usage: inspect_excel.py <file>",
      file=sys.stderr,
    )
    sys.exit(1)

  source = Path(sys.argv[1]).resolve()

  if not source.exists():
    raise FileNotFoundError(
      f"File does not exist: {source}"
    )

  suffix = source.suffix.lower()

  with tempfile.TemporaryDirectory() as temp_dir:
    temp_path = Path(temp_dir)

    if suffix == ".xls":
      inspection_path = convert_xls_to_xlsx(
        source,
        temp_path,
      )
    elif suffix in {".xlsx", ".xlsm"}:
      inspection_path = source
    else:
      raise ValueError(
        f"Unsupported spreadsheet format: {suffix}"
      )

    result = inspect_xlsx(inspection_path)

    print(
      json.dumps(
        result,
        ensure_ascii=False,
        indent=2,
      )
    )


if __name__ == "__main__":
  main()
"""


async def run() -> None:
  sandbox = await Sandbox.create()

  try:
    # Install OS dependencies.
    result = await sandbox.run_command(
      "apt-get",
      ["update"],
    )

    if result.exit_code != 0:
      raise RuntimeError(await result.stderr())

    result = await sandbox.run_command(
      "apt-get",
      ["install", "-y", *DEPENDENCIES],
    )

    if result.exit_code != 0:
      raise RuntimeError(await result.stderr())

    # Install Python dependencies required by the inspectors.
    result = await sandbox.run_command(
      "pip",
      [
        "install",
        "--break-system-packages",
        "openpyxl",
        "python-docx",
      ],
    )

    if result.exit_code != 0:
      raise RuntimeError(await result.stderr())

    # Write our scripts into the image.
    scripts = {
      "/usr/local/bin/form-convert": FORM_CONVERT,
      "/usr/local/bin/form-inspect": FORM_INSPECT,
      "/usr/local/bin/form-verify": FORM_VERIFY,
      "/usr/local/bin/inspect_excel.py": INSPECT_EXCEL,
    }

    for path, content in scripts.items():
      result = await sandbox.write_files([
        {
          "path": path,
          "content": content.encode("utf-8"),
        },
      ])

      # Depending on the SDK version, write_files may return
      # an object without exit_code. The important part is that
      # the write completed successfully.
      if result is None:
        continue

    # Make everything executable.
    for path in scripts:
      result = await sandbox.run_command(
        "chmod",
        ["+x", path],
      )

      if result.exit_code != 0:
        raise RuntimeError(await result.stderr())

    # Verify that the tools are actually available.
    for command in [
      "libreoffice",
      "pdftotext",
      "pdfinfo",
      "identify",
      "tesseract",
      "file",
      "form-convert",
      "form-inspect",
      "form-verify",
      "inspect_excel.py",
    ]:
      result = await sandbox.run_command(
        "which",
        [command],
      )

      if result.exit_code != 0:
        raise RuntimeError(
          f"Required command not found: {command}"
        )

    # Create the snapshot.
    snapshot = await sandbox.snapshot()

    print(f"Snapshot ID: {snapshot.snapshot_id}")

  finally:
    # snapshot() stops the sandbox, but this is harmless if
    # the snapshot operation already stopped it.
    try:
      await sandbox.stop()
    except Exception:
      pass


if __name__ == "__main__":
  import asyncio

  asyncio.run(run())