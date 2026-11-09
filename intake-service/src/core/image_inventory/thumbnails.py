"""Thumbnail generation for images."""
import io
from pathlib import Path
from typing import Optional, Tuple, BinaryIO
from PIL import Image, ImageOps, ExifTags


# Thumbnail sizes
THUMBNAIL_SMALL = (150, 150)   # For grid/contact sheets
THUMBNAIL_MEDIUM = (300, 300)  # For detail views
THUMBNAIL_LARGE = (600, 600)   # For zoom

# Quality settings
JPEG_QUALITY = 85
WEBP_QUALITY = 80


def generate_thumbnail(
    image_data: bytes,
    size: Tuple[int, int] = THUMBNAIL_SMALL,
    format: str = "JPEG",
    quality: int = JPEG_QUALITY,
) -> bytes:
    """Generate thumbnail from image bytes.
    
    Args:
        image_data: Raw image bytes
        size: Target size (max_width, max_height)
        format: Output format (JPEG, WEBP, PNG)
        quality: Quality for lossy formats (1-100)
    
    Returns:
        Thumbnail bytes
    """
    img = Image.open(io.BytesIO(image_data))
    
    # Handle EXIF orientation
    img = _apply_exif_orientation(img)
    
    # Convert to RGB for JPEG/WEBP
    if format in ("JPEG", "WEBP") and img.mode in ("RGBA", "LA", "P"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        background.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
        img = background
    elif format in ("JPEG", "WEBP") and img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    
    # Generate thumbnail (maintains aspect ratio)
    img.thumbnail(size, Image.Resampling.LANCZOS)
    
    # Save to bytes
    output = io.BytesIO()
    save_kwargs = {"format": format}
    if format in ("JPEG", "WEBP"):
        save_kwargs["quality"] = quality
        save_kwargs["optimize"] = True
    elif format == "PNG":
        save_kwargs["optimize"] = True
    
    img.save(output, **save_kwargs)
    return output.getvalue()


def generate_thumbnail_from_file(
    file_path: Path,
    size: Tuple[int, int] = THUMBNAIL_SMALL,
    format: str = "JPEG",
    quality: int = JPEG_QUALITY,
) -> Optional[bytes]:
    """Generate thumbnail from file path."""
    try:
        with file_path.open("rb") as f:
            data = f.read()
        return generate_thumbnail(data, size, format, quality)
    except Exception:
        return None


def generate_multiple_thumbnails(
    image_data: bytes,
    sizes: dict[str, Tuple[int, int]] = None,
) -> dict[str, bytes]:
    """Generate multiple thumbnail sizes at once."""
    if sizes is None:
        sizes = {
            "small": THUMBNAIL_SMALL,
            "medium": THUMBNAIL_MEDIUM,
            "large": THUMBNAIL_LARGE,
        }
    
    results = {}
    for name, size in sizes.items():
        results[name] = generate_thumbnail(image_data, size)
    return results


def _apply_exif_orientation(img: Image.Image) -> Image.Image:
    """Apply EXIF orientation tag to correct image rotation."""
    try:
        exif = img._getexif()
        if exif is None:
            return img
        
        orientation_key = None
        for key, val in ExifTags.TAGS.items():
            if val == "Orientation":
                orientation_key = key
                break
        
        if orientation_key is None or orientation_key not in exif:
            return img
        
        orientation = exif[orientation_key]
        
        if orientation == 2:
            img = img.transpose(Image.FLIP_LEFT_RIGHT)
        elif orientation == 3:
            img = img.transpose(Image.ROTATE_180)
        elif orientation == 4:
            img = img.transpose(Image.FLIP_TOP_BOTTOM)
        elif orientation == 5:
            img = img.transpose(Image.FLIP_LEFT_RIGHT).transpose(Image.ROTATE_90)
        elif orientation == 6:
            img = img.transpose(Image.ROTATE_270)
        elif orientation == 7:
            img = img.transpose(Image.FLIP_LEFT_RIGHT).transpose(Image.ROTATE_270)
        elif orientation == 8:
            img = img.transpose(Image.ROTATE_90)
        
        return img
    except Exception:
        return img


def get_image_info(image_data: bytes) -> dict:
    """Extract basic image metadata."""
    try:
        img = Image.open(io.BytesIO(image_data))
        return {
            "width": img.width,
            "height": img.height,
            "mode": img.mode,
            "format": img.format,
        }
    except Exception:
        return {}


def convert_to_webp(image_data: bytes, quality: int = WEBP_QUALITY) -> bytes:
    """Convert image to WebP format for efficient storage."""
    img = Image.open(io.BytesIO(image_data))
    img = _apply_exif_orientation(img)
    
    if img.mode in ("RGBA", "LA", "P"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        background.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
        img = background
    elif img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    
    output = io.BytesIO()
    img.save(output, format="WEBP", quality=quality, method=6)
    return output.getvalue()