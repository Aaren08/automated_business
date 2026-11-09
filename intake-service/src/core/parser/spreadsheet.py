"""Spreadsheet parser for .xlsx and .xls files."""
import openpyxl
import xlrd
from pathlib import Path
from typing import Any

from src.core.parser.base import BaseParser, ParsedContent


class SpreadsheetParser(BaseParser):
    """Parses Excel files for tabular data."""

    def can_parse(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in {".xlsx", ".xls"}

    def parse(self, file_path: Path) -> ParsedContent:
        try:
            if file_path.suffix.lower() == ".xlsx":
                return self._parse_xlsx(file_path)
            else:
                return self._parse_xls(file_path)
        except Exception as e:
            return ParsedContent(
                text="",
                tables=[],
                metadata={},
                error=f"Spreadsheet parsing failed: {e}",
            )

    def _parse_xlsx(self, file_path: Path) -> ParsedContent:
        tables = []
        text_parts = []
        sheet_names = []

        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        for sheet_name in wb.sheetnames:
            sheet_names.append(sheet_name)
            ws = wb[sheet_name]
            table = []
            for row in ws.iter_rows(values_only=True):
                if any(cell is not None for cell in row):
                    clean_row = [str(cell).strip() if cell is not None else "" for cell in row]
                    table.append(clean_row)
            if table:
                tables.append(table)
                text_parts.append(f"Sheet: {sheet_name}\n" + "\n".join("\t".join(r) for r in table))
        wb.close()

        return ParsedContent(
            text="\n\n".join(text_parts),
            tables=tables,
            metadata={"sheet_names": sheet_names, "sheet_count": len(sheet_names)},
        )

    def _parse_xls(self, file_path: Path) -> ParsedContent:
        tables = []
        text_parts = []
        sheet_names = []

        wb = xlrd.open_workbook(file_path)
        for sheet_name in wb.sheet_names():
            sheet_names.append(sheet_name)
            ws = wb.sheet_by_name(sheet_name)
            table = []
            for row_idx in range(ws.nrows):
                row = []
                for col_idx in range(ws.ncols):
                    cell = ws.cell(row_idx, col_idx)
                    row.append(str(cell.value).strip() if cell.value != "" else "")
                if any(cell for cell in row):
                    table.append(row)
            if table:
                tables.append(table)
                text_parts.append(f"Sheet: {sheet_name}\n" + "\n".join("\t".join(r) for r in table))

        return ParsedContent(
            text="\n\n".join(text_parts),
            tables=tables,
            metadata={"sheet_names": sheet_names, "sheet_count": len(sheet_names)},
        )