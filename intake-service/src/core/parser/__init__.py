"""Parser registry and factory."""
from pathlib import Path
from typing import Optional

from src.core.parser.base import BaseParser, ParsedContent
from src.core.parser.markdown import MarkdownParser
from src.core.parser.pdf import PDFParser
from src.core.parser.spreadsheet import SpreadsheetParser
from src.core.parser.image import ImageParser


class ParserRegistry:
    """Registry of file parsers."""

    def __init__(self):
        self._parsers: list[BaseParser] = [
            MarkdownParser(),
            PDFParser(),
            SpreadsheetParser(),
            ImageParser(),
        ]

    def get_parser(self, file_path: Path) -> Optional[BaseParser]:
        """Get the appropriate parser for a file."""
        for parser in self._parsers:
            if parser.can_parse(file_path):
                return parser
        return None

    def parse(self, file_path: Path) -> ParsedContent:
        """Parse a file using the appropriate parser."""
        parser = self.get_parser(file_path)
        if parser:
            return parser.parse(file_path)
        return ParsedContent(
            text="",
            tables=[],
            metadata={},
            error=f"No parser available for {file_path.suffix}",
        )


# Global instance
parser_registry = ParserRegistry()