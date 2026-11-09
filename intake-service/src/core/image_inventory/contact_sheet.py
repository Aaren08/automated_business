"""Contact sheet generation for batch image grids."""
import io
import math
from pathlib import Path
from typing import Optional, List, Tuple
from PIL import Image, ImageDraw, ImageFont

from src.core.image_inventory.thumbnails import generate_thumbnail, THUMBNAIL_SMALL


def calculate_grid_layout(
    image_count: int,
    max_cols: int = 5,
    thumb_size: Tuple[int, int] = THUMBNAIL_SMALL,
    padding: int = 10,
    label_height: int = 30,
) -> Tuple[int, int, int, int]:
    """Calculate contact sheet grid dimensions.
    
    Returns:
        (grid_cols, grid_rows, sheet_width, sheet_height)
    """
    cols = min(max_cols, image_count)
    rows = math.ceil(image_count / cols)
    
    thumb_w, thumb_h = thumb_size
    sheet_width = cols * thumb_w + (cols + 1) * padding
    sheet_height = rows * (thumb_h + label_height) + (rows + 1) * padding
    
    return cols, rows, sheet_width, sheet_height


def generate_contact_sheet(
    image_data_list: List[Tuple[bytes, str]],  # (image_bytes, filename)
    max_cols: int = 5,
    thumb_size: Tuple[int, int] = THUMBNAIL_SMALL,
    padding: int = 10,
    label_height: int = 30,
    background_color: Tuple[int, int, int] = (255, 255, 255),
    text_color: Tuple[int, int, int] = (0, 0, 0),
    font_size: int = 10,
) -> Optional[bytes]:
    """Generate a contact sheet from multiple images.
    
    Args:
        image_data_list: List of (image_bytes, filename) tuples
        max_cols: Maximum columns in grid
        thumb_size: Thumbnail size
        padding: Padding between images
        label_height: Height reserved for filename label
        background_color: Background RGB
        text_color: Text RGB
        font_size: Font size for labels
    
    Returns:
        Contact sheet image bytes, or None if failed
    """
    if not image_data_list:
        return None
    
    image_count = len(image_data_list)
    cols, rows, sheet_width, sheet_height = calculate_grid_layout(
        image_count, max_cols, thumb_size, padding, label_height
    )
    
    # Create blank contact sheet
    sheet = Image.new("RGB", (sheet_width, sheet_height), background_color)
    draw = ImageDraw.Draw(sheet)
    
    # Try to load a font
    font = None
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
    except Exception:
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
    
    # Place images in grid
    for idx, (img_bytes, filename) in enumerate(image_data_list):
        row = idx // cols
        col = idx % cols
        
        x = padding + col * (thumb_size[0] + padding)
        y = padding + row * (thumb_size[1] + label_height + padding)
        
        # Generate thumbnail
        thumb_bytes = generate_thumbnail(img_bytes, thumb_size)
        if thumb_bytes is None:
            # Draw placeholder
            placeholder = Image.new("RGB", thumb_size, (200, 200, 200))
            sheet.paste(placeholder, (x, y))
        else:
            thumb = Image.open(io.BytesIO(thumb_bytes))
            sheet.paste(thumb, (x, y))
        
        # Draw filename label
        label_y = y + thumb_size[1] + 2
        # Truncate long filenames
        display_name = filename
        if font:
            # Estimate max chars that fit
            max_chars = thumb_size[0] // (font_size // 2)
            if len(display_name) > max_chars:
                display_name = display_name[:max_chars - 3] + "..."
        else:
            max_chars = thumb_size[0] // 6
            if len(display_name) > max_chars:
                display_name = display_name[:max_chars - 3] + "..."
        
        if font:
            draw.text((x, label_y), display_name, fill=text_color, font=font)
        else:
            draw.text((x, label_y), display_name, fill=text_color)
    
    # Save to bytes
    output = io.BytesIO()
    sheet.save(output, format="JPEG", quality=85, optimize=True)
    return output.getvalue()


def generate_contact_sheet_from_paths(
    image_paths: List[Path],
    max_cols: int = 5,
    thumb_size: Tuple[int, int] = THUMBNAIL_SMALL,
) -> Optional[bytes]:
    """Generate contact sheet from file paths."""
    image_data_list = []
    for path in image_paths:
        try:
            with path.open("rb") as f:
                data = f.read()
            image_data_list.append((data, path.name))
        except Exception:
            continue
    
    if not image_data_list:
        return None
    
    return generate_contact_sheet(image_data_list, max_cols, thumb_size)


def generate_contact_sheet_from_batch_items(
    batch_items: List[dict],
    inbox_path: Path,
    max_cols: int = 5,
) -> Optional[bytes]:
    """Generate contact sheet from batch items with image file_type."""
    image_data_list = []
    for item in batch_items:
        if item.get("file_type") != "image":
            continue
        file_path = inbox_path / item["file_path"]
        try:
            with file_path.open("rb") as f:
                data = f.read()
            image_data_list.append((data, item["file_name"]))
        except Exception:
            continue
    
    if not image_data_list:
        return None
    
    return generate_contact_sheet(image_data_list, max_cols)