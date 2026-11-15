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
        # Read raw bytes first - preserve original content unchanged
        raw_bytes = file_path.read_bytes()

        # Strict UTF-8 decoding: never silently replace invalid bytes
        decoding_errors: list[str] = []
        try:
            content = raw_bytes.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError as e:
            # Record the error but do not silently corrupt the content.
            # Decode with errors="replace" only as a last resort so the file
            # can still be parsed, but flag it for review.
            decoding_errors.append(
                f"Strict UTF-8 decode failed: {e}. "
                f"Falling back to replacement decoding - content may be partially corrupted."
            )
            content = raw_bytes.decode("utf-8", errors="replace")
            encoding = "utf-8 (replacement)"

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
            raw_bytes=raw_bytes,
            encoding=encoding,
            decoding_errors=decoding_errors if decoding_errors else None,
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
        """Extract supplier name, contact, products from text.

        All source content is accounted for: product price lines go into
        ``products_raw``, structured tables into ``product_tables``,
        delivery-area lines into ``delivery_areas``, and every remaining
        line/block that is not clearly product, price, or delivery content is
        retained in ``unclassified_content`` so nothing is dropped.
        """
        info = {
            "supplier_name": None,
            "contact": {},
            "products_raw": [],
            "delivery_areas": [],
            "unclassified_content": [],
            "notes": "",
        }

        lines = text.split("\n")

        # Try to find supplier name (first non-empty line or heading)
        for line in lines:
            line = line.strip()
            if line and not line.startswith("*") and not line.startswith("-"):
                info["supplier_name"] = line[:200]
                break

        # Build a set of table cell text so table content is not double-counted
        # as unclassified when it is already captured structurally.
        table_text: set[str] = set()
        for table in tables or []:
            for row in table:
                for cell in row:
                    if cell and cell.strip():
                        table_text.add(cell.strip())

        delivery_keywords = ["deliver", "delivery", "ship", "shipping", "dallas", "houston", "austin", "waco", "oklahoma"]
        price_pattern = re.compile(r"[\$\€\£]\s*\d+(?:[.,]\d{2})?|\d+(?:[.,]\d{2})?\s*[\$\€\£]")

        for line_no, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped:
                continue

            # Price lines are product candidates (captured in products_raw).
            if price_pattern.search(stripped):
                info["products_raw"].append(stripped)
                continue

            # Delivery / geographic coverage lines -> fulfilment terms, not products.
            if any(kw in stripped.lower() for kw in delivery_keywords):
                info["delivery_areas"].append(stripped)
                continue

            # Table cell content is already captured structurally.
            if stripped in table_text:
                continue

            # Obvious short headers / marketing / closing noise -> unclassified
            # but retained; not turned into a product candidate.
            info["unclassified_content"].append({
                "type": "line",
                "line_no": line_no,
                "text": stripped[:500],
            })

        # Use tables for structured product data
        if tables:
            info["product_tables"] = tables

        return info