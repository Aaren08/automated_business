"""Markdown parser for Supplier.md files."""
import re
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt

from src.core.parser.base import BaseParser, ParsedContent


class MarkdownParser(BaseParser):
    """Parses markdown files, extracting text and structured data."""

    def can_parse(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in {".md", ".markdown"}

    def parse(self, file_path: Path) -> ParsedContent:
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = file_path.read_text(encoding="utf-8", errors="replace")

        md = MarkdownIt()
        tokens = md.parse(content)

        # Extract plain text
        text = self._tokens_to_text(tokens)

        # Extract tables
        tables = self._extract_tables(tokens)

        # Extract structured supplier info
        metadata = self._extract_supplier_info(text, tables)

        return ParsedContent(
            text=text,
            tables=tables,
            metadata=metadata,
        )

    def _tokens_to_text(self, tokens: list) -> str:
        """Convert markdown tokens to plain text."""
        text_parts = []
        for token in tokens:
            if token.type == "inline":
                text_parts.append(token.content)
            elif token.type in {"heading_open", "paragraph_open"}:
                pass
            elif token.children:
                text_parts.append(self._tokens_to_text(token.children))
        return "\n".join(text_parts)

    def _extract_tables(self, tokens: list) -> list[list[list[str]]]:
        """Extract tables from markdown tokens."""
        tables = []
        current_table: list[list[str]] = []
        in_table = False

        for token in tokens:
            if token.type == "table_open":
                in_table = True
                current_table = []
            elif token.type == "table_close":
                if current_table:
                    tables.append(current_table)
                in_table = False
            elif in_table and token.type == "tr":
                row = []
                for child in token.children or []:
                    if child.type == "td" or child.type == "th":
                        cell_text = ""
                        for inline in child.children or []:
                            cell_text += inline.content
                        row.append(cell_text.strip())
                if row:
                    current_table.append(row)

        return tables

    def _extract_supplier_info(self, text: str, tables: list[list[list[str]]]) -> dict[str, Any]:
        """Extract supplier name, contact, products from text."""
        info = {
            "supplier_name": None,
            "contact": {},
            "products_raw": [],
            "delivery_areas": [],
            "notes": "",
        }

        lines = text.split("\n")

        # Try to find supplier name (first non-empty line or heading)
        for line in lines:
            line = line.strip()
            if line and not line.startswith("*") and not line.startswith("-"):
                info["supplier_name"] = line[:200]
                break

        # Extract product lines (lines with prices)
        price_pattern = re.compile(r"[\$\€\£]\s*\d+(?:[.,]\d{2})?|\d+(?:[.,]\d{2})?\s*[\$\€\£]")
        for line in lines:
            if price_pattern.search(line):
                info["products_raw"].append(line.strip())

        # Extract delivery areas
        delivery_keywords = ["deliver", "delivery", "ship", "shipping", "dallas", "houston", "austin", "waco", "oklahoma"]
        for line in lines:
            if any(kw in line.lower() for kw in delivery_keywords):
                info["delivery_areas"].append(line.strip())

        # Use tables for structured product data
        if tables:
            info["product_tables"] = tables

        return info