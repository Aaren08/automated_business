"""Supabase client and database operations."""
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional
from uuid import UUID

from supabase import Client, create_client
from tenacity import stop_after_attempt, wait_exponential

from src.config import settings

# ---- Reliability configuration (reuses M6 tenacity retry tuning) ----
MAX_RETRIES = 3
STALE_LOCK_TTL_SECONDS = 30 * 60  # 30 minutes
RETRY_STOP = stop_after_attempt(MAX_RETRIES)
RETRY_WAIT = wait_exponential(multiplier=1, min=1, max=10)

RUNNING_STATUSES = ("pending", "running")


def get_job_retry_count(job: Optional[dict]) -> int:
    """Read retry_count from a job's input_params (default 0)."""
    if not job:
        return 0
    params = job.get("input_params") or {}
    try:
        return int(params.get("retry_count", 0) or 0)
    except (TypeError, ValueError):
        return 0


def is_job_stale_lock(job: Optional[dict]) -> bool:
    """A job is a stale lock when it is pending/running and started_at is older than the TTL.

    started_at is the existing lock timestamp column; no new schema field is introduced.
    """
    if not job:
        return False
    if job.get("status") not in RUNNING_STATUSES:
        return False
    started_at = job.get("started_at")
    if not started_at:
        return False
    try:
        started = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return False
    if started.tzinfo is not None:
        started = started.replace(tzinfo=None)
    return (datetime.utcnow() - started).total_seconds() > STALE_LOCK_TTL_SECONDS


class SupabaseStorage:
    """Manages Supabase connections and batch/product operations."""

    def __init__(self):
        self._client: Optional[Client] = None

    @property
    def client(self) -> Client:
        """Lazy-initialize Supabase client."""
        if self._client is None:
            self._client = create_client(
                settings.SUPABASE_URL,
                settings.SUPABASE_SERVICE_KEY,
            )
        return self._client

    async def close(self):
        """Close any open connections."""
        # Supabase Python client doesn't require explicit close
        pass

    # ===== Batch Operations =====

    async def create_batch(self, batch_data: dict) -> dict:
        """Insert a new batch record."""
        result = self.client.table("batches").insert(batch_data).execute()
        if not result.data:
            raise Exception("Failed to create batch")
        return result.data[0]

    async def get_batch_by_id(self, batch_id: str) -> Optional[dict]:
        """Get batch by batch_id."""
        result = self.client.table("batches").select("*").eq("batch_id", batch_id).execute()
        return result.data[0] if result.data else None

    async def update_batch(self, batch_id: str, updates: dict) -> dict:
        """Update batch record."""
        result = self.client.table("batches").update(updates).eq("batch_id", batch_id).execute()
        if not result.data:
            raise Exception(f"Batch not found: {batch_id}")
        return result.data[0]

    async def upsert_batch(self, batch_data: dict) -> dict:
        """Upsert batch by batch_id (idempotent)."""
        result = self.client.table("batches").upsert(batch_data, on_conflict="batch_id").execute()
        if not result.data:
            raise Exception("Failed to upsert batch")
        return result.data[0]

    async def list_batches(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """List batches with pagination."""
        result = (
            self.client.table("batches")
            .select("*")
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
        return result.data or []

    # ===== Batch Items Operations =====

    async def create_batch_items(self, batch_id: str, items: list[dict]) -> list[dict]:
        """Insert batch items."""
        for item in items:
            item["batch_id"] = batch_id
        result = self.client.table("batch_items").insert(items).execute()
        return result.data or []

    async def upsert_batch_items(self, batch_id: str, items: list[dict]) -> list[dict]:
        """Upsert batch items by file_hash (idempotent)."""
        for item in items:
            item["batch_id"] = batch_id
        result = self.client.table("batch_items").upsert(items, on_conflict="batch_id,file_hash").execute()
        return result.data or []

    async def get_batch_items(self, batch_id: str) -> list[dict]:
        """Get all items for a batch."""
        result = self.client.table("batch_items").select("*").eq("batch_id", batch_id).execute()
        return result.data or []

    # ===== Supplier Operations =====

    async def upsert_supplier(self, supplier_data: dict) -> dict:
        """Upsert supplier by content_hash (idempotent)."""
        # Use content_hash for deduplication if available
        if supplier_data.get("content_hash"):
            result = self.client.table("suppliers").upsert(
                supplier_data, on_conflict="content_hash"
            ).execute()
        else:
            # Fallback to name-based upsert
            result = self.client.table("suppliers").upsert(supplier_data, on_conflict="name").execute()
        if not result.data:
            raise Exception("Failed to upsert supplier")
        return result.data[0]

    async def get_supplier_by_name(self, name: str) -> Optional[dict]:
        """Get supplier by name."""
        result = self.client.table("suppliers").select("*").eq("name", name).execute()
        return result.data[0] if result.data else None

    async def get_supplier_by_id(self, supplier_id: UUID) -> Optional[dict]:
        """Get supplier by ID."""
        result = self.client.table("suppliers").select("*").eq("id", str(supplier_id)).execute()
        return result.data[0] if result.data else None

    # ===== Package Operations =====

    async def create_package(self, package_data: dict) -> dict:
        """Insert a new package record (stored in batches table)."""
        result = self.client.table("batches").insert(package_data).execute()
        if not result.data:
            raise Exception("Failed to create package")
        return result.data[0]

    async def get_package_by_id(self, package_id: str) -> Optional[dict]:
        """Get package by package_id (batch_id)."""
        result = self.client.table("batches").select("*").eq("batch_id", package_id).execute()
        return result.data[0] if result.data else None

    async def update_package(self, package_id: str, updates: dict) -> dict:
        """Update package record."""
        result = self.client.table("batches").update(updates).eq("batch_id", package_id).execute()
        if not result.data:
            raise Exception(f"Package not found: {package_id}")
        return result.data[0]

    async def upsert_package(self, package_data: dict) -> dict:
        """Upsert package by package_id (idempotent)."""
        result = self.client.table("batches").upsert(package_data, on_conflict="batch_id").execute()
        if not result.data:
            raise Exception("Failed to upsert package")
        return result.data[0]

    async def list_packages(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """List packages with pagination."""
        result = (
            self.client.table("batches")
            .select("*")
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
        return result.data or []

    async def upsert_package_items(self, package_id: str, items: list[dict]) -> list[dict]:
        """Upsert package items by file_hash (idempotent)."""
        for item in items:
            item["batch_id"] = package_id
        result = self.client.table("batch_items").upsert(items, on_conflict="batch_id,file_hash").execute()
        return result.data or []

    async def get_package_items(self, package_id: str) -> list[dict]:
        """Get all items for a package."""
        result = self.client.table("batch_items").select("*").eq("batch_id", package_id).execute()
        return result.data or []

    # ===== Product Operations =====

    async def insert_product(self, product_data: dict) -> dict:
        """Insert a new product record.

        internal_sku, if provided, must be unique. The caller is responsible
        for ensuring no existing product has the same internal_sku.
        """
        result = self.client.table("products").insert(product_data).execute()
        if not result.data:
            raise Exception("Failed to insert product")
        return result.data[0]

    async def get_product_by_id(self, product_id: UUID) -> Optional[dict]:
        """Get product by ID."""
        result = self.client.table("products").select("*").eq("id", str(product_id)).execute()
        return result.data[0] if result.data else None

    async def get_product_by_internal_sku(self, internal_sku: str) -> Optional[dict]:
        """Get product by canonical internal_sku."""
        result = self.client.table("products").select("*").eq("internal_sku", internal_sku).execute()
        return result.data[0] if result.data else None

    async def get_product_by_supplier_sku(self, supplier_sku: str) -> list[dict]:
        """Get all products by source-provided supplier_sku.

        supplier_sku is NOT a canonical identity key. Multiple products may
        share the same supplier_sku (from different sources). Returns all
        matches so the pipeline can detect ambiguity.
        """
        result = self.client.table("products").select("*").eq("supplier_sku", supplier_sku).execute()
        return result.data or []

    async def get_product_by_canonical_name(self, canonical_name: str) -> Optional[dict]:
        """Get product by canonical_name for conservative matching review."""
        result = self.client.table("products").select("*").eq("canonical_name", canonical_name).execute()
        return result.data[0] if result.data else None

    async def list_products(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """List products with pagination."""
        result = (
            self.client.table("products")
            .select("*")
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
        return result.data or []

    async def update_product(self, product_id: UUID, updates: dict) -> dict:
        """Update product record.

        internal_sku, if present in updates, is silently stripped to protect
        canonical identity immutability.
        """
        updates = {k: v for k, v in updates.items() if k != "internal_sku"}
        result = self.client.table("products").update(updates).eq("id", str(product_id)).execute()
        if not result.data:
            raise Exception(f"Product not found: {product_id}")
        return result.data[0]

    # ===== Product-to-Source-Item Mapping =====

    async def get_product_by_source_item(
        self, batch_item_id: str, candidate_key: str
    ) -> Optional[dict]:
        """Find the canonical product mapped to a source item candidate.

        Looks up the product_source_items mapping by (batch_item_id, candidate_key).
        Returns the product record if a mapping exists, None otherwise.

        candidate_key is a stable identifier within a source item (e.g.,
        "table:0,row:2" or "line:5"), not the candidate's position in a list.
        """
        result = (
            self.client.table("product_source_items")
            .select("*, products(*)")
            .eq("batch_item_id", batch_item_id)
            .eq("candidate_key", candidate_key)
            .execute()
        )
        if not result.data:
            return None
        mapping = result.data[0]
        return mapping.get("products")

    async def create_product_source_mapping(
        self, product_id: str, batch_item_id: str, candidate_key: str
    ) -> dict:
        """Record that a source item candidate maps to a canonical product.

        Idempotent: upserts on (product_id, batch_item_id, candidate_key).
        """
        result = self.client.table("product_source_items").upsert(
            {
                "product_id": product_id,
                "batch_item_id": batch_item_id,
                "candidate_key": candidate_key,
            },
            on_conflict="batch_item_id,candidate_key",
        ).execute()
        if not result.data:
            raise Exception("Failed to create product-source mapping")
        return result.data[0]

    async def list_source_items_for_product(self, product_id: str) -> list[dict]:
        """List all source items mapped to a canonical product."""
        result = (
            self.client.table("product_source_items")
            .select("*")
            .eq("product_id", product_id)
            .execute()
        )
        return result.data or []

    # ===== Price History Idempotency =====

    async def price_history_exists(
        self,
        product_id: str,
        package_id: str,
        source_batch_item_id: str,
        effective_date: str,
        price_amount: float,
    ) -> bool:
        """Check if a price-history observation already exists (idempotency check).

        Returns True if an identical price observation already exists, so the
        pipeline can skip inserting a duplicate.
        """
        result = (
            self.client.table("price_history")
            .select("id")
            .eq("product_id", product_id)
            .eq("package_id", package_id)
            .eq("source_batch_item_id", source_batch_item_id)
            .eq("effective_date", effective_date)
            .eq("price_amount", str(price_amount))
            .execute()
        )
        return len(result.data or []) > 0

    async def add_price_history(self, history_data: dict) -> dict:
        """Add price history entry."""
        result = self.client.table("price_history").insert(history_data).execute()
        if not result.data:
            raise Exception("Failed to add price history")
        return result.data[0]

    # ===== Product-to-Source-Item Image Matching =====

    async def get_unmatched_product_images(
        self, batch_internal_id: str
    ) -> list[dict]:
        """Get product images (batch_items) that haven't been matched to a product yet.

        Looks for batch_items with file_type='image' that don't have a
        product_image_id set (meaning they haven't been processed into
        product_images yet).
        """
        result = (
            self.client.table("batch_items")
            .select("*, product_images(*)")
            .eq("batch_id", batch_internal_id)
            .eq("file_type", "image")
            .is_("product_image_id", "null")
            .execute()
        )
        return result.data or []

    async def update_batch_item_match(
        self,
        batch_item_id: str,
        match_confidence: float,
        match_method: str,
        match_reasoning: str,
    ) -> dict:
        """Update a batch_item with image match information."""
        result = (
            self.client.table("batch_items")
            .update({
                "extraction_confidence": match_confidence,
                "extraction_method": match_method,
                "extraction_notes": match_reasoning,
            })
            .eq("id", batch_item_id)
            .execute()
        )
        if not result.data:
            raise Exception(f"Failed to update batch_item {batch_item_id}")
        return result.data[0]

    # ===== Product-Image Relationships =====

    async def link_product_image(self, link_data: dict) -> dict:
        """Link product to image asset."""
        result = self.client.table("product_product_images").upsert(
            link_data, on_conflict="product_id,product_image_id"
        ).execute()
        if not result.data:
            raise Exception("Failed to link product-image")
        return result.data[0]

    async def get_product_images(self, product_id: UUID) -> list[dict]:
        """Get all images linked to a product."""
        result = (
            self.client.table("product_product_images")
            .select("*, product_images(*)")
            .eq("product_id", str(product_id))
            .execute()
        )
        return result.data or []

    async def unlink_product_image(self, product_id: UUID, product_image_id: UUID) -> bool:
        """Remove product-image link."""
        result = self.client.table("product_product_images").delete().eq("product_id", str(product_id)).eq("product_image_id", str(product_image_id)).execute()
        return len(result.data) > 0 if result.data else False

    # ===== Price History =====

    async def add_price_history(self, history_data: dict) -> dict:
        """Add price history entry."""
        result = self.client.table("price_history").insert(history_data).execute()
        if not result.data:
            raise Exception("Failed to add price history")
        return result.data[0]

    async def get_price_history(self, product_id: UUID, limit: int = 50) -> list[dict]:
        """Get price history for a product."""
        result = (
            self.client.table("price_history")
            .select("*")
            .eq("product_id", str(product_id))
            .order("effective_date", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []

    # ===== Product-to-Source-Item Image Matching =====

    async def get_unmatched_product_images(
        self, batch_internal_id: str
    ) -> list[dict]:
        """Get product images (batch_items) that haven't been matched to a product yet.

        Looks for batch_items with file_type='image' that have a product_image_id
        set (processed by image inventory) but don't have a product_product_images
        link yet (not yet matched to a canonical product).
        """
        result = (
            self.client.table("batch_items")
            .select("*, product_images(*)")
            .eq("batch_id", batch_internal_id)
            .eq("file_type", "image")
            .not_.is_("product_image_id", "null")
            .execute()
        )
        items = result.data or []

        # Filter out items that already have a product_product_images link
        unmatched = []
        for item in items:
            product_images = item.get("product_product_images") or []
            if not product_images:
                unmatched.append(item)

        return unmatched

    async def update_batch_item_match(
        self,
        batch_item_id: str,
        match_confidence: float,
        match_method: str,
        match_reasoning: str,
    ) -> dict:
        """Update a batch_item with image match information."""
        result = (
            self.client.table("batch_items")
            .update({
                "extraction_confidence": match_confidence,
                "extraction_method": match_method,
                "extraction_notes": match_reasoning,
            })
            .eq("id", batch_item_id)
            .execute()
        )
        if not result.data:
            raise Exception(f"Failed to update batch_item {batch_item_id}")
        return result.data[0]

    # ===== Processing Jobs =====

    async def create_job(self, job_data: dict) -> dict:
        """Create a processing job record."""
        result = self.client.table("processing_jobs").insert(job_data).execute()
        if not result.data:
            raise Exception("Failed to create job")
        return result.data[0]

    async def get_job(self, job_id: UUID) -> Optional[dict]:
        """Get job by ID."""
        result = self.client.table("processing_jobs").select("*").eq("id", str(job_id)).execute()
        return result.data[0] if result.data else None

    async def update_job(self, job_id: UUID, updates: dict) -> dict:
        """Update job record."""
        result = self.client.table("processing_jobs").update(updates).eq("id", str(job_id)).execute()
        if not result.data:
            raise Exception(f"Job not found: {job_id}")
        return result.data[0]

    async def get_latest_job_for_batch(self, batch_id: str) -> Optional[dict]:
        """Get latest job for a batch (by external batch_id)."""
        # First get the internal batch UUID
        batch = await self.get_batch_by_id(batch_id)
        if not batch:
            return None
        internal_batch_id = batch["id"]

        result = (
            self.client.table("processing_jobs")
            .select("*")
            .eq("batch_id", internal_batch_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None

    async def get_active_job_for_batch(self, batch_id: str) -> Optional[dict]:
        """Return the latest pending/running job for a batch (concurrency guard).

        Reuses existing columns only (status, started_at); no new schema fields.
        """
        batch = await self.get_batch_by_id(batch_id)
        if not batch:
            return None
        result = (
            self.client.table("processing_jobs")
            .select("*")
            .eq("batch_id", batch["id"])
            .in_("status", list(RUNNING_STATUSES))
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None

    async def increment_job_retry(
        self, job_id: UUID, max_retries: int = MAX_RETRIES
    ) -> bool:
        """Increment retry_count on a job's input_params.

        Returns True if retries remain (job should be retried), False if exhausted.
        """
        job = await self.get_job(job_id)
        if not job:
            return False
        params = job.get("input_params") or {}
        retry_count = get_job_retry_count(job) + 1
        params["retry_count"] = retry_count
        params["max_retries"] = max_retries
        await self.update_job(job_id, {"input_params": params})
        return retry_count < max_retries

    async def mark_job_dead_letter(
        self, job_id: UUID, error_message: Optional[str] = None
    ) -> dict:
        """Mark a job as dead-lettered (permanently failed, no further retries).

        Persists dead_letter flag and timestamp in result_summary; keeps
        error_message for diagnosis. No new schema fields.
        """
        job = await self.get_job(job_id)
        summary = dict(job.get("result_summary") or {}) if job else {}
        summary["dead_letter"] = True
        summary["dead_lettered_at"] = datetime.utcnow().isoformat()
        summary["retry_count"] = get_job_retry_count(job)
        updates = {
            "status": "failed",
            "completed_at": datetime.utcnow().isoformat(),
            "result_summary": summary,
        }
        if error_message is not None:
            updates["error_message"] = error_message
        return await self.update_job(job_id, updates)


# Global instance
supabase_storage = SupabaseStorage()


@asynccontextmanager
async def get_supabase():
    """Async context manager for Supabase operations."""
    try:
        yield supabase_storage
    finally:
        await supabase_storage.close()