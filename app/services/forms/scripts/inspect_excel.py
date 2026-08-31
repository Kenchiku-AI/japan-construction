#!/usr/bin/env python3

import json
import sys
from pathlib import Path

import xlrd
from openpyxl import load_workbook


def inspect_xlsx(path: Path) -> dict:
  workbook = load_workbook(path, data_only=False, read_only=False)

  sheets = []

  for worksheet in workbook.worksheets:
    cells = []

    for row in worksheet.iter_rows():
      for cell in row:
        if cell.value is None:
          continue

        value = cell.value
        if not isinstance(value, (str, int, float, bool)):
          value = str(value)

        cells.append({
          "coordinate": cell.coordinate,
          "value": value,
          "data_type": cell.data_type,
        })

    merged_cells = [str(r) for r in worksheet.merged_cells.ranges]

    column_dimensions = {}
    for key, dimension in worksheet.column_dimensions.items():
      if dimension.width is not None or dimension.hidden:
        column_dimensions[key] = {"width": dimension.width, "hidden": dimension.hidden}

    row_dimensions = {}
    for key, dimension in worksheet.row_dimensions.items():
      if dimension.height is not None or dimension.hidden:
        row_dimensions[str(key)] = {"height": dimension.height, "hidden": dimension.hidden}

    sheets.append({
      "name": worksheet.title,
      "max_row": worksheet.max_row,
      "max_column": worksheet.max_column,
      "merged_cells": merged_cells,
      "freeze_panes": str(worksheet.freeze_panes) if worksheet.freeze_panes else None,
      "column_dimensions": column_dimensions,
      "row_dimensions": row_dimensions,
      "cells": cells,
    })

  return {"filename": path.name, "sheet_count": len(sheets), "sheets": sheets}


def _col_letter(col_idx: int) -> str:
  letters = ""
  col_idx += 1
  while col_idx > 0:
    col_idx, remainder = divmod(col_idx - 1, 26)
    letters = chr(65 + remainder) + letters
  return letters


def inspect_xls(path: Path) -> dict:
  """Reads a legacy .xls directly with xlrd -- no conversion step
  needed just to inspect. Note: xlrd exposes values + merged ranges
  but not full style fidelity (fonts/fills/number formats) the way
  openpyxl does for XLSX -- usually fine for inspection. Editing an
  XLS losslessly still goes through form-convert first."""

  book = xlrd.open_workbook(str(path), formatting_info=False)
  sheets = []

  for sheet_index in range(book.nsheets):
    sheet = book.sheet_by_index(sheet_index)
    cells = []

    for row_idx in range(sheet.nrows):
      for col_idx in range(sheet.ncols):
        cell = sheet.cell(row_idx, col_idx)
        if cell.value in ("", None):
          continue

        value = cell.value
        if not isinstance(value, (str, int, float, bool)):
          value = str(value)

        cells.append({
          "coordinate": f"{_col_letter(col_idx)}{row_idx + 1}",
          "value": value,
          "data_type": xlrd.sheet.ctype_text.get(cell.ctype, "unknown"),
        })

    merged_cells = [
      f"{_col_letter(c1)}{r1 + 1}:{_col_letter(c2 - 1)}{r2}"
      for (r1, r2, c1, c2) in sheet.merged_cells
    ]

    sheets.append({
      "name": sheet.name,
      "max_row": sheet.nrows,
      "max_column": sheet.ncols,
      "merged_cells": merged_cells,
      "freeze_panes": None,
      "column_dimensions": {},
      "row_dimensions": {},
      "cells": cells,
    })

  return {"filename": path.name, "sheet_count": len(sheets), "sheets": sheets}


def main() -> None:
  if len(sys.argv) != 2:
    print("Usage: inspect_excel.py <file>", file=sys.stderr)
    sys.exit(1)

  source = Path(sys.argv[1]).resolve()
  if not source.exists():
    raise FileNotFoundError(f"File does not exist: {source}")

  suffix = source.suffix.lower()

  if suffix == ".xls":
    result = inspect_xls(source)
  elif suffix in {".xlsx", ".xlsm"}:
    result = inspect_xlsx(source)
  else:
    raise ValueError(f"Unsupported spreadsheet format: {suffix}")

  print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
  main()