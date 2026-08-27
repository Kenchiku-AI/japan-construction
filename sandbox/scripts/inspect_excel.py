#!/usr/bin/env python3

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