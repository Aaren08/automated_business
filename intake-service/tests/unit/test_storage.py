"""Focused tests for M6: Supabase Storage integration.

Covers:
- Successful upload of original image + generated thumbnails
- Storage path persistence (schema column `original_path`, not `object_storage_key`)
- Upload failure handling (StorageError)
- Safe retries on transient failures
- Idempotent skip-if-exists on reprocessing
"""
import os
import sys
from types import SimpleNamespace

# Ensure `src` package is importable when running pytest from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from unittest.mock import patch

import pytest

from src.core.image_inventory.storage import (
    StorageError,
    storage_client,
    SupabaseStorageClient,
)


class MockBucket:
    """In-memory stand-in for a Supabase Storage bucket."""

    def __init__(self):
        self.upload_call_count = 0
        self.upload_side_effects = None  # list of exceptions to raise in order
        self.upload_response = SimpleNamespace(error=None)
        self.download_raises = True  # True => object does not exist yet

    def upload(self, path, file, file_options):
        self.upload_call_count += 1
        if self.upload_side_effects:
            raise self.upload_side_effects.pop(0)
        return self.upload_response

    def get_public_url(self, path):
        return f"https://storage.supabase.co/storage/v1/object/public/product-intelligence/{path}"

    def download(self, path):
        if self.download_raises:
            raise Exception("object not found")
        return b"existing-object-bytes"


class MockStorageAPI:
    def __init__(self, bucket):
        self._bucket = bucket

    def from_(self, bucket_name):
        return self._bucket


class MockClient:
    def __init__(self, bucket):
        self.storage = MockStorageAPI(bucket)


@pytest.fixture
def client_and_bucket():
    """Provide a fresh mock Supabase client wired into the global storage_client."""
    bucket = MockBucket()
    client = MockClient(bucket)
    storage_client._client = client
    try:
        yield bucket, client
    finally:
        storage_client._client = None


FAKE_THUMBS = {
    "small": b"thumb-small-bytes",
    "medium": b"thumb-medium-bytes",
    "large": b"thumb-large-bytes",
}


def test_storage_error_is_exception_subclass():
    """StorageError should be catchable as a generic Exception."""
    assert issubclass(StorageError, Exception)


@pytest.mark.asyncio
async def test_upload_image_asset_success(client_and_bucket):
    """Original + thumbnails upload and return schema-aligned storage paths."""
    bucket, _ = client_and_bucket

    with patch(
        "src.core.image_inventory.storage.generate_multiple_thumbnails",
        return_value=FAKE_THUMBS,
    ):
        result = await storage_client.upload_image_asset(
            original_bytes=b"fake-image-bytes",
            filename="photo.jpg",
            file_hash="abc123def456",
            batch_id="batch-001",
        )

    # One upload for original + one per thumbnail
    assert bucket.upload_call_count == 1 + len(FAKE_THUMBS), bucket.upload_call_count

    # Schema column is `original_path` (NOT `object_storage_key`)
    assert result["original_path"] == "assets/abc123def456/original.jpg"
    assert result["original_url"].endswith(
        "/product-intelligence/assets/abc123def456/original.jpg"
    )
    assert result["thumbnails"] == {
        "small": "assets/abc123def456/thumb_small.jpg",
        "medium": "assets/abc123def456/thumb_medium.jpg",
        "large": "assets/abc123def456/thumb_large.jpg",
    }
    assert result["size"] == len(b"fake-image-bytes")
    assert "skipped" not in result or result.get("skipped") is not True


@pytest.mark.asyncio
async def test_upload_image_asset_persists_original_path_column(client_and_bucket):
    """Result uses `original_path` key matching the product_images.original_path column."""
    bucket, _ = client_and_bucket

    with patch(
        "src.core.image_inventory.storage.generate_multiple_thumbnails",
        return_value=FAKE_THUMBS,
    ):
        result = await storage_client.upload_image_asset(
            original_bytes=b"img",
            filename="image.PNG",
            file_hash="hash999",
            batch_id="batch-002",
        )

    # Confirms we persist `original_path`, not a non-existent `object_storage_key`
    assert "original_path" in result
    assert "object_storage_key" not in result
    assert result["original_path"].startswith("assets/hash999/")
    assert result["original_path"].endswith(".png")


@pytest.mark.asyncio
async def test_upload_image_asset_failure_raises_storage_error(client_and_bucket):
    """A Supabase upload error must surface as StorageError, not silently fail."""
    bucket, _ = client_and_bucket
    bucket.upload_side_effects = [StorageError("bucket unavailable")] * 3

    with patch(
        "src.core.image_inventory.storage.generate_multiple_thumbnails",
        return_value=FAKE_THUMBS,
    ):
        with pytest.raises(StorageError):
            await storage_client.upload_image_asset(
                original_bytes=b"img",
                filename="photo.jpg",
                file_hash="fail-hash",
                batch_id="batch-003",
            )


@pytest.mark.asyncio
async def test_upload_image_asset_retries_then_succeeds(client_and_bucket):
    """Transient upload failures are retried with exponential backoff and succeed."""
    bucket, _ = client_and_bucket
    # Fail twice, then succeed on the third attempt (stop_after_attempt(3))
    bucket.upload_side_effects = [StorageError("glitch"), StorageError("glitch")]

    with patch(
        "src.core.image_inventory.storage.generate_multiple_thumbnails",
        return_value=FAKE_THUMBS,
    ):
        result = await storage_client.upload_image_asset(
            original_bytes=b"img",
            filename="photo.jpg",
            file_hash="retry-hash",
            batch_id="batch-004",
        )

    # Original upload retried 3 times; thumbnails uploaded once each
    assert bucket.upload_call_count == 3 + len(FAKE_THUMBS), bucket.upload_call_count
    assert result["original_path"] == "assets/retry-hash/original.jpg"
    assert result["thumbnails"]["small"] == "assets/retry-hash/thumb_small.jpg"


@pytest.mark.asyncio
async def test_upload_skip_if_exists_idempotent(client_and_bucket):
    """Re-processing skips re-upload when object already exists (idempotent)."""
    bucket, _ = client_and_bucket
    bucket.download_raises = False  # object already exists in bucket

    with patch(
        "src.core.image_inventory.storage.generate_multiple_thumbnails",
        return_value=FAKE_THUMBS,
    ):
        result = await storage_client.upload_image_asset(
            original_bytes=b"img",
            filename="photo.jpg",
            file_hash="existing-hash",
            batch_id="batch-005",
        )

    # All objects already exist -> nothing re-uploaded (idempotent skip)
    assert bucket.upload_call_count == 0, bucket.upload_call_count
    assert result["original_path"] == "assets/existing-hash/original.jpg"
    assert result["thumbnails"] == {
        "small": "assets/existing-hash/thumb_small.jpg",
        "medium": "assets/existing-hash/thumb_medium.jpg",
        "large": "assets/existing-hash/thumb_large.jpg",
    }


@pytest.mark.asyncio
async def test_upload_file_sync_retry_exhaustion_raises(client_and_bucket):
    """upload_file_sync raises StorageError after exhausting retries."""
    bucket, _ = client_and_bucket
    bucket.upload_side_effects = [StorageError("down"), StorageError("down"), StorageError("down")]

    with pytest.raises(StorageError):
        storage_client.upload_file_sync(
            b"img",
            "assets/x/original.jpg",
            "image/jpeg",
        )

    assert bucket.upload_call_count == 3
