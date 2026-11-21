"""Supabase Storage operations for image assets (synchronous Supabase client)."""
import io
import os
import asyncio
from pathlib import Path
from typing import Optional, BinaryIO
from datetime import datetime
from uuid import uuid4

from supabase import create_client
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.config import settings
from src.core.image_inventory.thumbnails import generate_thumbnail, generate_multiple_thumbnails


class StorageError(Exception):
    """Raised when a Supabase Storage operation fails."""


class SupabaseStorageClient:
    """Handles uploads to Supabase Storage (synchronous client wrapped for async)."""
    
    def __init__(self):
        self._client = None
    
    @property
    def client(self):
        if self._client is None:
            self._client = create_client(
                settings.SUPABASE_URL,
                settings.SUPABASE_SERVICE_KEY,
            )
        return self._client
    
    def _get_bucket(self):
        """Get storage bucket."""
        return self.client.storage.from_(settings.S3_BUCKET)
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    def upload_file_sync(
        self,
        file_bytes: bytes,
        storage_path: str,
        content_type: str = "application/octet-stream",
        upsert: bool = True,
    ) -> dict:
        """Upload file to Supabase Storage (synchronous).

        Safe to retry: uploads use upsert=True, so re-uploading the same path
        produces the same result without creating duplicates.

        Args:
            file_bytes: File content
            storage_path: Path in bucket (e.g., "BATCH-2025-11/1/inventory/photo1.jpg")
            content_type: MIME type
            upsert: Whether to overwrite existing

        Returns:
            Upload response with public URL
        """
        bucket = self._get_bucket()

        response = bucket.upload(
            path=storage_path,
            file=file_bytes,
            file_options={
                "content-type": content_type,
                "upsert": str(upsert).lower(),
            },
        )

        if hasattr(response, "error") and response.error:
            raise StorageError(f"Upload failed: {response.error}")

        # Get public URL (constructed URL; does not make the bucket public)
        public_url = bucket.get_public_url(storage_path)

        return {
            "path": storage_path,
            "public_url": public_url,
            "size": len(file_bytes),
        }
    
    async def upload_file(
        self,
        file_bytes: bytes,
        storage_path: str,
        content_type: str = "application/octet-stream",
        upsert: bool = True,
    ) -> dict:
        """Async wrapper for upload_file_sync."""
        return await asyncio.to_thread(
            self.upload_file_sync, file_bytes, storage_path, content_type, upsert
        )

    async def _upload_if_not_exists(
        self,
        file_bytes: bytes,
        storage_path: str,
        content_type: str = "application/octet-stream",
    ) -> dict:
        """Upload file unless it already exists in the bucket.

        Idempotent: on reprocessing, existing objects are skipped (no
        unnecessary re-upload). If the existence check itself fails, we fall
        through to a normal (idempotent upsert) upload.
        """
        try:
            exists = await self.file_exists(storage_path)
        except Exception:
            exists = False
        if exists:
            public_url = await self.get_public_url(storage_path)
            return {
                "path": storage_path,
                "public_url": public_url,
                "size": len(file_bytes),
                "skipped": True,
            }
        return await self.upload_file(
            file_bytes, storage_path, content_type, upsert=True
        )

    async def upload_image_asset(
        self,
        original_bytes: bytes,
        filename: str,
        storage_dir: str,
        relative_path: str,
    ) -> dict:
        """Upload original image and thumbnails as an asset.

        Storage layout mirrors the supplier inbox so each numbered package
        (1, 2, 3, ...) lives in its own folder under its monthly batch and
        can be deleted independently:

            {storage_dir}/{relative_path}                                    <- original
            {storage_dir}/thumbs/{relative_path}.thumb_{size}.jpg            <- thumbnails

        Example:
            BATCH-2025-11/1/inventory/photo1.jpg
            BATCH-2025-11/1/thumbs/inventory/photo1.jpg.thumb_small.jpg

        Args:
            original_bytes: File content
            filename: Original file name (used for extension / MIME type)
            storage_dir: Package storage folder, e.g. "BATCH-2025-11/1"
            relative_path: Path of the file within the package, e.g. "inventory/photo1.jpg"

        Returns:
            Dict with original_path, original_url, thumbnails, size
        """
        ext = Path(filename).suffix.lower()
        if ext not in {".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif"}:
            ext = ".jpg"

        # Upload original (idempotent: skipped if already stored)
        original_path = f"{storage_dir}/{relative_path}"
        original_result = await self._upload_if_not_exists(
            original_bytes, original_path,
            content_type=self._get_mime_type(ext),
        )

        # Generate and upload thumbnails (idempotent: skipped if already stored)
        thumbs = generate_multiple_thumbnails(original_bytes)
        thumb_results = {}

        for size_name, thumb_bytes in thumbs.items():
            thumb_path = f"{storage_dir}/thumbs/{relative_path}.thumb_{size_name}.jpg"
            thumb_result = await self._upload_if_not_exists(
                thumb_bytes, thumb_path, "image/jpeg"
            )
            thumb_results[size_name] = thumb_result["path"]

        return {
            "original_path": original_result["path"],
            "original_url": original_result["public_url"],
            "thumbnails": thumb_results,
            "size": original_result["size"],
        }

    async def upload_batch_contact_sheet(
        self,
        contact_sheet_bytes: bytes,
        storage_dir: str,
    ) -> dict:
        """Upload contact sheet for a batch/package.

        Stored at {storage_dir}/contact_sheet.jpg, e.g.
        BATCH-2025-11/1/contact_sheet.jpg
        """
        path = f"{storage_dir}/contact_sheet.jpg"
        return await self.upload_file(
            contact_sheet_bytes, path, "image/jpeg"
        )
    
    def _get_mime_type(self, ext: str) -> str:
        mime_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".tiff": "image/tiff",
            ".tif": "image/tiff",
        }
        return mime_map.get(ext.lower(), "application/octet-stream")
    
    async def file_exists(self, storage_path: str) -> bool:
        """Check if file exists in storage."""
        return await asyncio.to_thread(self._file_exists_sync, storage_path)
    
    def _file_exists_sync(self, storage_path: str) -> bool:
        bucket = self._get_bucket()
        try:
            bucket.download(storage_path)
            return True
        except Exception:
            return False
    
    async def delete_file(self, storage_path: str) -> bool:
        """Delete file from storage."""
        return await asyncio.to_thread(self._delete_file_sync, storage_path)
    
    def _delete_file_sync(self, storage_path: str) -> bool:
        bucket = self._get_bucket()
        try:
            bucket.remove([storage_path])
            return True
        except Exception:
            return False
    
    async def get_public_url(self, storage_path: str) -> str:
        """Get public URL for a file."""
        return await asyncio.to_thread(self._get_public_url_sync, storage_path)
    
    def _get_public_url_sync(self, storage_path: str) -> str:
        bucket = self._get_bucket()
        return bucket.get_public_url(storage_path)


# Global instance
storage_client = SupabaseStorageClient()