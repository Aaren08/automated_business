"""PDF parser using PyMuPDF and pdfplumber."""
import fitz  # PyMuPDF
import pdfplumber
from pathlib import Path
from typing import Any

from src.core.parser.base import BaseParser, ParsedContent


class PDFParser(BaseParser):
    """Parses PDF files for text and tables."""

    def can_parse(self, file_path: Path) -> bool:
        return file_path.suffix.lower() == ".pdf"

    def parse(self, file_path: Path) -> ParsedContent:
        try:
            # Try pdfplumber first for better table extraction
            return self._parse_with_pdfplumber(file_path)
        except Exception as e:
            # Fallback to PyMuPDF
            try:
                return self._parse_with_pymupdf(file_path)
            except Exception as e2:
                return ParsedContent(
                    text="",
                    tables=[],
                    metadata={},
                    error=f"Both parsers failed: pdfplumber={e}, pymupdf={e2}",
                )

    def _parse_with_pdfplumber(self, file_path: Path) -> ParsedContent:
        text_parts = []
        tables = []

        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                # Extract text
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)

                # Extract tables
                page_tables = page.extract_tables()
                if page_tables:
                    for table in page_tables:
                        if table:
                            # Clean up table cells
                            clean_table = [
                                [cell.strip() if cell else "" for cell in row]
                                for row in table
                            ]
                            tables.append(clean_table)

        return ParsedContent(
            text="\n\n".join(text_parts),
            tables=tables,
            metadata={"page_count": len(pdfplumber.open(file_path).pages)},
        )

    def _parse_with_pymupdf(self, file_path: Path) -> ParsedContent:
        text_parts = []

        doc = fitz.open(file_path)
        for page_num in range(doc.page_count):
            page = doc[page_num]
            text = page.get_text()
            if text.strip():
                text_parts.append(text)
        doc.close()

        return ParsedContent(
            text="\n\n".join(text_parts),
            tables=[],
            metadata={"page_count": doc.page_count},
        )