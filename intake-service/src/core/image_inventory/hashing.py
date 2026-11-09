"""Image hashing utilities for deduplication and similarity detection."""
import hashlib
import imagehash
from PIL import Image
from pathlib import Path
from typing import Optional, Tuple


def sha256_file(file_path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    hasher = hashlib.sha256()
    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def sha256_bytes(data: bytes) -> str:
    """Compute SHA-256 hash of bytes."""
    return hashlib.sha256(data).hexdigest()


def perceptual_hash(file_path: Path, hash_size: int = 8) -> Optional[str]:
    """Compute perceptual hash (dhash) of an image.
    
    Args:
        file_path: Path to image file
        hash_size: Size of hash (8 = 64 bits, 16 = 256 bits)
    
    Returns:
        Hex string of perceptual hash, or None if failed
    """
    try:
        with Image.open(file_path) as img:
            # Convert to RGB if needed
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            # Compute difference hash (dhash)
            phash = imagehash.dhash(img, hash_size=hash_size)
            return str(phash)
    except Exception:
        return None


def perceptual_hash_from_bytes(data: bytes, hash_size: int = 8) -> Optional[str]:
    """Compute perceptual hash from image bytes."""
    try:
        from io import BytesIO
        img = Image.open(BytesIO(data))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        phash = imagehash.dhash(img, hash_size=hash_size)
        return str(phash)
    except Exception:
        return None


def hamming_distance(hash1: str, hash2: str) -> int:
    """Calculate Hamming distance between two perceptual hashes.
    
    Lower distance = more similar. Typical thresholds:
    - 0-5: Very similar / likely same image
    - 6-10: Similar / possibly same subject
    - 11-15: Somewhat similar
    - 16+: Different
    """
    if len(hash1) != len(hash2):
        return -1
    return sum(c1 != c2 for c1, c2 in zip(hash1, hash2))


def similarity_score(hash1: str, hash2: str) -> float:
    """Calculate similarity score (0.0 to 1.0) from perceptual hashes."""
    distance = hamming_distance(hash1, hash2)
    if distance < 0:
        return 0.0
    max_bits = len(hash1) * 4  # Each hex char = 4 bits
    return 1.0 - (distance / max_bits)


def get_image_dimensions(file_path: Path) -> Optional[Tuple[int, int]]:
    """Get image dimensions (width, height)."""
    try:
        with Image.open(file_path) as img:
            return (img.width, img.height)
    except Exception:
        return None


def is_valid_image(file_path: Path) -> bool:
    """Check if file is a valid image."""
    try:
        with Image.open(file_path) as img:
            img.verify()
        return True
    except Exception:
        return False


# Visual similarity thresholds
class SimilarityThresholds:
    """Thresholds for perceptual hash similarity."""
    EXACT = 0      # Hamming distance 0 = identical
    VERY_SIMILAR = 5    # Likely same image (different compression/crop)
    SIMILAR = 10        # Same subject, different angle/lighting
    SOMEWHAT_SIMILAR = 15
    DIFFERENT = 16
    
    @classmethod
    def classify(cls, distance: int) -> str:
        if distance <= cls.EXACT:
            return "identical"
        elif distance <= cls.VERY_SIMILAR:
            return "very_similar"
        elif distance <= cls.SIMILAR:
            return "similar"
        elif distance <= cls.SOMEWHAT_SIMILAR:
            return "somewhat_similar"
        else:
            return "different"