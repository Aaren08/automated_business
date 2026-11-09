"""Image parser with OCR using Tesseract."""
import pytesseract
from PIL import Image
from pathlib import Path
from typing import Any

from src.core.parser.base import BaseParser, ParsedContent


class ImageParser(BaseParser):
    """Parses images using OCR."""

    def can_parse(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".tiff", ".tif"}

    def parse(self, file_path: Path) -> ParsedContent:
        try:
            # Configure Tesseract
            pytesseract.pytesseract.tesseract_cmd = "/usr/bin/tesseract"

            # Open image
            img = Image.open(file_path)

            # Convert to RGB if needed
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")

            # Run OCR
            text = pytesseract.image_to_string(img)

            # Also get detailed data
            data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)

            return ParsedContent(
                text=text.strip(),
                tables=[],
                metadata={
                    "width": img.width,
                    "height": img.height,
                    "mode": img.mode,
                    "ocr_confidence": sum(data.get("conf", [0])) / max(len(data.get("conf", [1])), 1),
                },
            )
        except Exception as e:
            return ParsedContent(
                text="",
                tables=[],
                metadata={},
                error=f"Image OCR failed: {e}",
            )