"""Base parser interface."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ParsedContent:
    """Result of parsing a file."""
    text: str
    tables: list[list[list[str]]]  # List of tables, each table is list of rows
    metadata: dict[str, Any]
    error: str | None = None


class BaseParser(ABC):
    """Abstract base class for file parsers."""

    @abstractmethod
    def can_parse(self, file_path: Path) -> bool:
        """Check if this parser can handle the file."""
        pass

    @abstractmethod
    def parse(self, file_path: Path) -> ParsedContent:
        """Parse the file and return structured content."""
        pass