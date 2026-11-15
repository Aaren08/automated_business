"""Batch discovery and validation logic for the nested monthly-batch structure.

Business workflow:
- 00 - Supplier Inbox/ contains monthly batches such as BATCH-2026-10/.
- Each monthly batch contains numbered product packages: 1/, 2/, 3/, etc.
- Each package MUST contain:
    o info.md, which contains PRODUCT information (not supplier identity).
    o inventory/, which contains product images.
- Optional package contents include catalog.pdf, .xls/.xlsx files, and image/
  containing additional product information in image form.
- Supplier contact information is not required. info.md is renamed from supplier.md.
"""

import hashlib
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.config import settings


@dataclass
class PackageInfo:
    """Discovered intake package information."""

    package_id: str
    package_name: str  # e.g., "1", "2", "3"
    source_path: str  # relative to inbox root, e.g., "BATCH-2026-10/1"
    monthly_batch: str  # e.g., "BATCH-2026-10"
    info_md_path: str
    info_md_hash: str
    inventory_dir: str
    total_files: int
    total_images: int
    ready_marker_path: str
    discovered_at: datetime


@dataclass
class BatchInfo:
    """Discovered monthly batch information."""

    batch_id: str
    batch_name: str  # e.g., "BATCH-2026-10"
    source_path: str  # relative to inbox root
    packages: list[PackageInfo] = field(default_factory=list)
    total_packages: int = 0
    ready_packages: int = 0
    discovered_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class PackageFile:
    """File within an intake package."""

    relative_path: str
    file_name: str
    file_size: int
    mime_type: str
    file_hash: str
    file_type: str


class BatchScanner:
    """Scans supplier inbox for ready monthly batches and numbered packages.

    Directory layout:
        inbox/
        └── BATCH-2026-10/           <- monthly batch
            ├── _READY               <- batch readiness marker (optional)
            ├── 1/                   <- numbered intake package
            │   ├── _READY           <- package readiness marker
            │   ├── info.md          <- mandatory: product information
            │   ├── inventory/       <- mandatory: product images
            │   ├── catalog.pdf      <- optional
            │   ├── price_list.xlsx  <- optional
            │   └── image/           <- optional: info-bearing images
            └── 2/
                ├── _READY
                ├── info.md
                └── inventory/
    """

    READY_MARKER = "_READY"
    INFO_MD = "info.md"  # renamed from supplier.md
    INVENTORY_DIR = "inventory"
    IMAGE_INFO_DIR = "image"
    ALLOWED_EXTENSIONS = {ext.lower() for ext in settings.allowed_extensions_set}
    IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".tif"}

    def __init__(self, inbox_path: Optional[str] = None):
        # Resolve the inbox from the configured Obsidian supplier path first,
        # falling back to the Docker-mounted /data/inbox for local/dev runs.
        resolved = inbox_path or settings.OBSIDIAN_SUPPLIER_PATH or "/data/inbox"
        self.inbox_path = Path(resolved).resolve()

    # ------------------------------------------------------------------
    # Batch discovery (monthly folders)
    # ------------------------------------------------------------------
    def scan_batches(self) -> list[BatchInfo]:
        """Discover all monthly batches with at least one ready package.

        Returns list of BatchInfo objects, each containing its ready packages.
        """
        if not self.inbox_path.exists():
            raise FileNotFoundError(f"Inbox path does not exist: {self.inbox_path}")

        batches = []
        for entry in sorted(self.inbox_path.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith("."):
                continue

            batch_info = self._validate_monthly_batch(entry)
            if batch_info and batch_info.packages:
                batches.append(batch_info)

        return batches

    def _validate_monthly_batch(self, batch_dir: Path) -> Optional[BatchInfo]:
        """Validate a monthly batch directory and discover ready packages."""
        batch_name = batch_dir.name

        # Discover numbered packages inside the monthly batch
        packages = []
        for entry in sorted(batch_dir.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith("."):
                continue
            if not self._is_numbered_package(entry.name):
                continue

            package_info = self._validate_package(entry, batch_name)
            if package_info:
                packages.append(package_info)

        if not packages:
            return None

        batch_id = self._generate_batch_id(batch_name)

        return BatchInfo(
            batch_id=batch_id,
            batch_name=batch_name,
            source_path=str(batch_dir.relative_to(self.inbox_path)),
            packages=packages,
            total_packages=len(packages),
            ready_packages=len(packages),
        )

    def _is_numbered_package(self, name: str) -> bool:
        """Check if a directory name is a numbered package (1, 2, 3, ...)."""
        try:
            int(name)
            return True
        except ValueError:
            return False

    # ------------------------------------------------------------------
    # Package validation
    # ------------------------------------------------------------------
    def _validate_package(
        self, package_dir: Path, monthly_batch: str
    ) -> Optional[PackageInfo]:
        """Validate an intake package has mandatory files.

        Mandatory: info.md + inventory/ directory
        Optional: catalog.pdf, .xls/.xlsx, image/ directory
        """
        # Readiness marker at package level
        ready_marker = package_dir / self.READY_MARKER
        if not ready_marker.exists() or not ready_marker.is_file():
            return None

        # Find info.md case-insensitively
        info_md = None
        for f in package_dir.iterdir():
            if f.is_file() and f.name.lower() == self.INFO_MD:
                info_md = f
                break
        if not info_md:
            return None

        # inventory/ directory is mandatory
        inventory_dir = package_dir / self.INVENTORY_DIR
        if not inventory_dir.exists() or not inventory_dir.is_dir():
            return None

        # Read and hash info.md
        info_md_content = info_md.read_bytes()
        info_md_hash = hashlib.sha256(info_md_content).hexdigest()

        # Collect all valid files in the package
        files = self._collect_package_files(package_dir)
        total_files = len(files)
        total_images = sum(1 for f in files if f.file_type == "image")

        package_id = self._generate_package_id(monthly_batch, package_dir.name, info_md_hash)

        return PackageInfo(
            package_id=package_id,
            package_name=package_dir.name,
            source_path=str(package_dir.relative_to(self.inbox_path)),
            monthly_batch=monthly_batch,
            info_md_path=str(info_md.relative_to(package_dir)),
            info_md_hash=info_md_hash,
            inventory_dir=str(inventory_dir.relative_to(package_dir)),
            total_files=total_files,
            total_images=total_images,
            ready_marker_path=str(ready_marker.relative_to(self.inbox_path)),
            discovered_at=datetime.utcnow(),
        )

    def _collect_package_files(self, package_dir: Path) -> list[PackageFile]:
        """Collect all valid files in a package directory."""
        files = []
        for file_path in package_dir.rglob("*"):
            if not file_path.is_file():
                continue
            name_lower = file_path.name.lower()
            # Skip ready markers
            if name_lower == self.READY_MARKER or name_lower.startswith("_ready"):
                continue
            # info.md is collected as a regular markdown package file so its
            # content flows through parsing -> raw source persistence ->
            # product extraction into the Supabase universal product tables.
            # (Previously skipped here with "handled separately", but it was
            # never handled separately, so info.md content never reached Supabase.)
            if file_path.name.startswith("."):
                continue
            if file_path.suffix.lower() not in self.ALLOWED_EXTENSIONS:
                continue

            relative = file_path.relative_to(package_dir)
            file_hash = self._hash_file(file_path)
            file_type = self._classify_file(file_path)

            files.append(
                PackageFile(
                    relative_path=str(relative),
                    file_name=file_path.name,
                    file_size=file_path.stat().st_size,
                    mime_type=self._get_mime_type(file_path),
                    file_hash=file_hash,
                    file_type=file_type,
                )
            )
        return files

    def _classify_file(self, file_path: Path) -> str:
        """Classify file by extension."""
        ext = file_path.suffix.lower()
        if ext in self.IMAGE_EXTENSIONS:
            return "image"
        if ext == ".pdf":
            return "pdf"
        if ext in {".xlsx", ".xls"}:
            return "spreadsheet"
        if ext == ".md":
            return "markdown"
        return "unknown"

    def _get_mime_type(self, file_path: Path) -> str:
        """Get MIME type from extension."""
        ext = file_path.suffix.lower()
        mime_map = {
            ".pdf": "application/pdf",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".tiff": "image/tiff",
            ".tif": "image/tiff",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".xls": "application/vnd.ms-excel",
            ".md": "text/markdown",
        }
        return mime_map.get(ext, "application/octet-stream")

    def _hash_file(self, file_path: Path) -> str:
        """Compute SHA256 hash of file."""
        hasher = hashlib.sha256()
        with file_path.open("rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    # ------------------------------------------------------------------
    # Deterministic IDs
    # ------------------------------------------------------------------
    def _generate_batch_id(self, batch_name: str) -> str:
        """Generate deterministic batch ID from monthly batch name."""
        return hashlib.sha256(batch_name.encode()).hexdigest()[:32]

    def _generate_package_id(
        self, monthly_batch: str, package_name: str, info_md_hash: str
    ) -> str:
        """Generate deterministic package ID from batch + package name.

        The package_id is stable based on location (monthly_batch + package_name),
        not content. Content changes are tracked separately via supplier_md_hash.
        """
        content = f"{monthly_batch}:{package_name}"
        return hashlib.sha256(content.encode()).hexdigest()[:32]

    # ------------------------------------------------------------------
    # Convenience: flat list of all ready packages across batches
    # ------------------------------------------------------------------
    def scan_packages(self) -> list[PackageInfo]:
        """Return a flat list of all ready packages across all monthly batches."""
        packages = []
        for batch in self.scan_batches():
            packages.extend(batch.packages)
        return packages
