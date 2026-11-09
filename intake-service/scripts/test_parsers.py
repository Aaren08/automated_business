from src.core.parser import parser_registry
from pathlib import Path

images_dir = Path('/data/inbox/Batch-2026-11/images')
for img in images_dir.iterdir():
    if img.is_file():
        result = parser_registry.parse(img)
        text_preview = result.text[:80] if result.text else "(no text)"
        conf = result.metadata.get("ocr_confidence", 0)
        print(f'{img.name}: {text_preview} (conf: {conf:.1f})')