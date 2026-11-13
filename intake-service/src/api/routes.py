"""API routes for the intake service."""
import structlog
from fastapi import APIRouter, HTTPException, BackgroundTasks
from uuid import uuid4, UUID
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from src.core.batch import BatchScanner, PackageInfo
from src.core.parser import parser_registry
from src.core.storage.supabase import (
    MAX_RETRIES,
    get_job_retry_count,
    is_job_stale_lock,
    supabase_storage,
    get_supabase,
)
from src.core.extractor import extraction_pipeline
from src.api.schemas import (
    BatchScanRequest,
    BatchScanResponse,
    BatchStatus,
    JobStatus,
    BatchItemStatus,
)

router = APIRouter()
logger = structlog.get_logger()

# Initialize scanner
scanner = BatchScanner()


async def process_package_files(package_id: str, package_internal_id: str, inbox_path: str = "/data/inbox"):
    """Background task to parse files and extract products for a package.

    Reliability (M8): bounded retries with dead-lettering, stale-lock safe,
    and structured logging. Reuses M6 tenacity retry tuning (MAX_RETRIES).
    """
    logger = structlog.get_logger().bind(
        package_id=package_id,
        package_internal_id=package_internal_id,
    )

    async with get_supabase() as storage:
        job = await storage.get_latest_job_for_batch(package_id)
        if not job:
            logger.error("no_job_for_package")
            return

        job_id = job["id"]
        retry_count = get_job_retry_count(job)

        try:
            # Mark the job running (lock timestamp is the existing started_at column)
            await storage.update_job(UUID(job_id), {"status": "running"})
            await storage.update_package(
                package_id, {"status": "processing", "error_message": None}
            )

            package = await storage.get_package_by_id(package_id)
            if not package:
                raise Exception(f"Package not found: {package_id}")

            source_path = package["source_path"]
            monthly_batch = package.get("monthly_batch", "")
            package_name = package.get("package_name", "")

            # Collect and parse files
            temp_scanner = BatchScanner(inbox_path)
            package_info = None
            for p in temp_scanner.scan_packages():
                if p.package_id == package_id:
                    package_info = p
                    break

            if not package_info:
                raise Exception(f"Package not found in inbox: {package_id}")

            # Parse each file in the package
            package_path = Path(inbox_path) / source_path
            files = temp_scanner._collect_package_files(package_path)

            items_data = []
            for f in files:
                file_path = package_path / f.relative_path
                parsed = parser_registry.parse(file_path)

                item_data = {
                    "batch_id": package_internal_id,
                    "file_path": f.relative_path,
                    "file_name": f.file_name,
                    "file_size": f.file_size,
                    "mime_type": f.mime_type,
                    "file_hash": f.file_hash,
                    "file_type": f.file_type,
                    "parsed_content": {
                        "text": parsed.text,
                        "tables": parsed.tables,
                        "metadata": parsed.metadata,
                    } if not parsed.error else None,
                    "ocr_text": parsed.text if f.file_type == "image" else None,
                    "processing_status": "completed" if not parsed.error else "failed",
                    "error_message": parsed.error,
                }
                items_data.append(item_data)

            # Store package items (upsert by file_hash to handle reprocessing)
            if items_data:
                await storage.upsert_package_items(package_internal_id, items_data)

            # Get stored items with IDs for image inventory
            stored_items = await storage.get_package_items(package_internal_id)

            # Process image inventory (hashing, thumbnails, dedup, storage).
            # Non-fatal: a failure here is logged but does not fail the package.
            try:
                from src.core.image_inventory import process_batch_image_inventory
                inventory_result = await process_batch_image_inventory(
                    package_id, package_internal_id, stored_items
                )
                logger.info("image_inventory_completed",
                            job_id=job_id,
                            **inventory_result)
            except Exception as e:
                logger.warning("image_inventory_failed",
                               package_id=package_id, job_id=job_id, error=str(e))

            # Run extraction pipeline (products only, no supplier).
            # Failures here ARE tracked: they go through bounded retry/dead-letter.
            extraction_result = await extraction_pipeline.process_package(
                package_id, package_internal_id, monthly_batch, package_name, inbox_path
            )
            logger.info("extraction_completed",
                        job_id=job_id,
                        **extraction_result)

            # Update job status to completed
            await storage.update_job(UUID(job_id), {
                "status": "completed",
                "completed_at": datetime.utcnow().isoformat(),
                "result_summary": {
                    "files_processed": len(items_data),
                    "dead_letter": False,
                    "retry_count": retry_count,
                },
            })
            await storage.update_package(package_id, {
                "status": "completed",
                "completed_at": datetime.utcnow().isoformat(),
            })

            logger.info("package_processing_completed",
                        job_id=job_id,
                        files=len(items_data), retry_count=retry_count)

        except Exception as e:
            logger.error("package_processing_failed",
                         error=str(e), retry_count=retry_count)

            retries_remain = await storage.increment_job_retry(UUID(job_id))
            if retries_remain:
                # Retryable: set back to pending so the next process/scan trigger retries
                await storage.update_job(UUID(job_id), {"status": "pending"})
                await storage.update_package(package_id, {
                    "status": "discovered",
                    "error_message": str(e),
                })
                logger.warning("package_processing_retry_scheduled",
                               error=str(e))
            else:
                # Exhausted: dead letter so failed work remains diagnosable + recoverable
                await storage.mark_job_dead_letter(UUID(job_id), error_message=str(e))
                await storage.update_package(package_id, {
                    "status": "failed",
                    "error_message": str(e),
                })
                logger.error("package_processing_dead_lettered",
                             job_id=job_id,
                             error=str(e), retry_count=retry_count)


@router.post("/scan", response_model=BatchScanResponse, tags=["batches"])
async def scan_packages(request: BatchScanRequest, background_tasks: BackgroundTasks):
    """Discover and register intake packages with _READY marker.

    Discovers monthly batches and their numbered packages.
    Each package must contain info.md and inventory/ to be ready.
    """
    logger.info("scan_started", force=request.force)

    discovered_packages = scanner.scan_packages()
    logger.info("packages_discovered", count=len(discovered_packages))

    queued = []
    skipped = []

    async with get_supabase() as storage:
        for package_info in discovered_packages:
            # Check if package already exists
            existing = await storage.get_package_by_id(package_info.package_id)

            if existing and not request.force:
                # Check if content changed (info.md hash)
                if existing.get("supplier_md_hash") == package_info.info_md_hash:
                    skipped.append(package_info.source_path)
                    logger.info("package_skipped_unchanged",
                                package_id=package_info.package_id,
                                source=package_info.source_path)
                    continue
                else:
                    logger.info("package_content_changed",
                                package_id=package_info.package_id,
                                source=package_info.source_path)

            # Prepare package data for upsert
            package_data = {
                "batch_id": package_info.package_id,
                "source_path": package_info.source_path,
                "monthly_batch": package_info.monthly_batch,
                "package_name": package_info.package_name,
                "supplier_md_hash": package_info.info_md_hash,
                "total_files": package_info.total_files,
                "total_images": package_info.total_images,
                "total_products": 0,
                "status": "discovered",
                "started_at": package_info.discovered_at.isoformat(),
            }

            try:
                await storage.upsert_package(package_data)
                queued.append(package_info.source_path)
                logger.info("package_registered",
                            package_id=package_info.package_id,
                            source=package_info.source_path)
            except Exception as e:
                logger.error("package_registration_failed",
                             package_id=package_info.package_id, error=str(e))
                skipped.append(package_info.source_path)

    return BatchScanResponse(
        batches_found=len(discovered_packages),
        batches_queued=queued,
        batches_skipped=skipped,
    )


@router.get("/batches", response_model=List[BatchStatus], tags=["batches"])
async def list_batches(limit: int = 50, offset: int = 0):
    """List packages/batches with pagination (backward compatible)."""
    async with get_supabase() as storage:
        batches = await storage.list_batches(limit=limit, offset=offset)

    return [
        BatchStatus(
            batch_id=b["batch_id"],
            status=b["status"],
            supplier_name=None,
            total_files=b["total_files"],
            total_images=b["total_images"],
            total_products=b["total_products"],
            error_message=b.get("error_message"),
            started_at=b.get("started_at"),
            completed_at=b.get("completed_at"),
            items=[],
        )
        for b in batches
    ]


@router.get("/batches/{batch_id}", response_model=BatchStatus, tags=["batches"])
async def get_batch(batch_id: str):
    """Get package details and status (backward compatible endpoint)."""
    async with get_supabase() as storage:
        batch = await storage.get_package_by_id(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")

        items = await storage.get_package_items(batch["id"])

    return BatchStatus(
        batch_id=batch["batch_id"],
        status=batch["status"],
        supplier_name=None,
        total_files=batch["total_files"],
        total_images=batch["total_images"],
        total_products=batch["total_products"],
        error_message=batch.get("error_message"),
        started_at=batch.get("started_at"),
        completed_at=batch.get("completed_at"),
        items=[
            BatchItemStatus(
                file_name=i["file_name"],
                file_type=i["file_type"],
                processing_status=i["processing_status"],
                extracted_products=0,
                error_message=i.get("error_message"),
            )
            for i in items
        ],
    )


@router.get("/batches/{batch_id}/status", response_model=JobStatus, tags=["batches"])
async def get_batch_status(batch_id: str):
    """Get processing job status for a package (backward compatible endpoint)."""
    async with get_supabase() as storage:
        job = await storage.get_latest_job_for_batch(batch_id)
        if not job:
            raise HTTPException(status_code=404, detail="No job found for batch")

    return JobStatus(
        job_id=UUID(job["id"]),
        batch_id=job["batch_id"],
        job_type=job["job_type"],
        status=job["status"],
        started_at=job.get("started_at"),
        completed_at=job.get("completed_at"),
        error_message=job.get("error_message"),
        result_summary=job.get("result_summary"),
    )


@router.post("/batches/{batch_id}/process", response_model=JobStatus, tags=["batches"])
async def process_batch(batch_id: str, background_tasks: BackgroundTasks):
    """Manually trigger package processing (retry, backward compatible endpoint).

    Reliability (M8): concurrency guard prevents duplicate execution of the
    same batch; stale-lock recovery reaps jobs stuck in running/pending beyond
    the TTL so failed work can be retried.
    """
    async with get_supabase() as storage:
        batch = await storage.get_package_by_id(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")

        # Concurrency guard + stale-lock recovery.
        # - running (not stale): a process is in progress -> no duplicate.
        # - running (stale): previous run exceeded TTL -> dead-letter it, retry.
        # - pending: queued or retryable -> re-queue on the existing job.
        active_job = await storage.get_active_job_for_batch(batch_id)
        if active_job:
            status = active_job.get("status")
            if status == "running":
                if is_job_stale_lock(active_job):
                    await storage.mark_job_dead_letter(
                        UUID(active_job["id"]),
                        error_message="Stale lock recovered: previous run exceeded TTL",
                    )
                    active_job = None  # fall through to create a fresh job
                else:
                    # Already being processed; return existing (idempotent).
                    return JobStatus(
                        job_id=UUID(active_job["id"]),
                        batch_id=batch_id,
                        job_type=active_job.get("job_type", "process"),
                        status=active_job.get("status", "pending"),
                        started_at=active_job.get("started_at"),
                        completed_at=active_job.get("completed_at"),
                        error_message=active_job.get("error_message"),
                        result_summary=active_job.get("result_summary"),
                    )
            # status == "pending": reuse the existing job (retry-safe, no new job)

        if active_job is None:
            now = datetime.utcnow().isoformat()
            job_data = {
                "batch_id": batch["id"],
                "job_type": "process",
                "status": "pending",
                "input_params": {
                    "force": True,
                    "retry_count": 0,
                    "max_retries": MAX_RETRIES,
                },
                "started_at": now,
            }
            job = await storage.create_job(job_data)
        else:
            job = active_job

    # Queue background processing
    background_tasks.add_task(process_package_files, batch_id, batch["id"])

    return JobStatus(
        job_id=UUID(job["id"]),
        batch_id=batch_id,
        job_type="process",
        status="pending",
        started_at=job.get("started_at"),
        completed_at=job.get("completed_at"),
        error_message=job.get("error_message"),
        result_summary=job.get("result_summary"),
    )

    return JobStatus(
        job_id=UUID(job["id"]),
        batch_id=batch_id,
        job_type="process",
        status="pending",
        started_at=job.get("started_at"),
        completed_at=job.get("completed_at"),
        error_message=job.get("error_message"),
        result_summary=job.get("result_summary"),
    )


@router.post("/batches/{batch_id}/review/{item_id}/approve", tags=["review"])
async def approve_review_item(batch_id: str, item_id: str):
    """Approve a human-review item."""
    # TODO: Implement in M5
    return {"status": "approved", "batch_id": batch_id, "item_id": item_id}


@router.post("/batches/{batch_id}/review/{item_id}/reject", tags=["review"])
async def reject_review_item(batch_id: str, item_id: str):
    """Reject a human-review item."""
    return {"status": "rejected", "batch_id": batch_id, "item_id": item_id}


# ===== Image Match Review =====

@router.get("/batches/{batch_id}/image-matches", tags=["image-matching"])
async def list_unmatched_images(batch_id: str):
    """List unmatched product images for a batch.

    Returns batch_items that are images (have product_image_id set via image
    inventory) but don't have a product_product_images link yet (not yet
    matched to a canonical product).
    """
    async with get_supabase() as storage:
        # Verify batch exists
        batch = await storage.get_package_by_id(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")

        # Get unmatched image items
        unmatched = await storage.get_unmatched_product_images(batch["id"])

        return {
            "batch_id": batch_id,
            "unmatched_images": unmatched,
            "count": len(unmatched),
        }


@router.post("/batches/{batch_id}/image-matches/{item_id}/approve", tags=["image-matching"])
async def approve_image_match(
    batch_id: str,
    item_id: str,
    product_id: str,
):
    """Approve an image-product match, creating the product_product_images link.

    The batch_item must have a product_image_id (set by image inventory).
    The product_id identifies which canonical product this image matches.
    """
    async with get_supabase() as storage:
        # Verify batch exists
        batch = await storage.get_package_by_id(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")

        # Get the batch_item
        batch_items = await storage.get_package_items(batch["id"])
        batch_item = None
        for item in batch_items:
            if str(item["id"]) == item_id:
                batch_item = item
                break

        if not batch_item:
            raise HTTPException(status_code=404, detail="Batch item not found")

        product_image_id = batch_item.get("product_image_id")
        if not product_image_id:
            raise HTTPException(
                status_code=400,
                detail="Batch item has no product_image_id (image not yet processed)",
            )

        # Check if link already exists
        existing = await storage.client.table("product_product_images").select("*").eq("product_id", product_id).eq("product_image_id", product_image_id).execute()
        if existing.data:
            return {"status": "already_linked", "batch_id": batch_id, "item_id": item_id, "product_id": product_id}

        # Create the product_product_images link
        link_data = {
            "product_id": product_id,
            "product_image_id": product_image_id,
            "role": "primary",
            "sort_order": 0,
            "match_confidence": 1.0,
            "match_method": "manual",
            "match_reasoning": "Manually approved by user",
        }
        result = await storage.link_product_image(link_data)

        # Update batch_item match info
        await storage.update_batch_item_match(
            item_id,
            1.0,
            "manual",
            "Manually approved by user",
        )

        return {
            "status": "approved",
            "batch_id": batch_id,
            "item_id": item_id,
            "product_id": product_id,
            "product_image_id": product_image_id,
        }


@router.post("/batches/{batch_id}/image-matches/{item_id}/reject", tags=["image-matching"])
async def reject_image_match(batch_id: str, item_id: str):
    """Reject an image match.

    The image remains unmatched and can be reviewed later.
    """
    async with get_supabase() as storage:
        # Verify batch exists
        batch = await storage.get_package_by_id(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")

        # Get the batch_item
        batch_items = await storage.get_package_items(batch["id"])
        batch_item = None
        for item in batch_items:
            if str(item["id"]) == item_id:
                batch_item = item
                break

        if not batch_item:
            raise HTTPException(status_code=404, detail="Batch item not found")

        # Mark the batch_item as rejected for image matching
        await storage.update_batch_item_match(
            item_id,
            0.0,
            "rejected",
            "Manually rejected by user",
        )

        return {"status": "rejected", "batch_id": batch_id, "item_id": item_id}


# ===== Image Match Review =====

@router.get("/batches/{batch_id}/image-matches", tags=["image-matching"])
async def list_unmatched_images(batch_id: str):
    """List unmatched product images for a batch that need review.

    Returns batch_items that are images (have product_image_id set via image
    inventory) but don't have a product_product_images link yet.
    """
    async with get_supabase() as storage:
        # Verify batch exists
        batch = await storage.get_package_by_id(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")

        # Get unmatched image items
        unmatched = await storage.get_unmatched_product_images(batch["id"])

        return {
            "batch_id": batch_id,
            "unmatched_images": unmatched,
            "count": len(unmatched),
        }


@router.post("/batches/{batch_id}/image-matches/{item_id}/approve", tags=["image-matching"])
async def approve_image_match(batch_id: str, item_id: str):
    """Approve an image-product match, creating the product_product_images link.

    The batch_item must have a product_image_id (from image inventory processing)
    and the product_id must be provided in the request body.
    """
    async with get_supabase() as storage:
        # Verify batch exists
        batch = await storage.get_package_by_id(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")

        # Get the batch_item
        batch_items = await storage.get_package_items(batch["id"])
        batch_item = None
        for item in batch_items:
            if str(item["id"]) == item_id:
                batch_item = item
                break

        if not batch_item:
            raise HTTPException(status_code=404, detail="Batch item not found")

        product_image_id = batch_item.get("product_image_id")
        if not product_image_id:
            raise HTTPException(status_code=400, detail="Batch item has no product_image_id")

        # The product_id should be provided in the request body
        # For now, we'll use a simple approach: the client provides product_id
        # In a real implementation, this would come from the UI selection

        # For now, return an error indicating the client needs to provide product_id
        raise HTTPException(
            status_code=400,
            detail="Must provide product_id to approve match",
        )


@router.post("/batches/{batch_id}/image-matches/{item_id}/reject", tags=["image-matching"])
async def reject_image_match(batch_id: str, item_id: str):
    """Reject an image match.

    The image will remain unmatched and can be reviewed later.
    """
    async with get_supabase() as storage:
        # Verify batch exists
        batch = await storage.get_package_by_id(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail="Batch not found")

        # Get the batch_item
        batch_items = await storage.get_package_items(batch["id"])
        batch_item = None
        for item in batch_items:
            if str(item["id"]) == item_id:
                batch_item = item
                break

        if not batch_item:
            raise HTTPException(status_code=404, detail="Batch item not found")

        # Mark the batch_item as rejected for image matching
        await storage.update_batch_item_match(
            item_id,
            0.0,
            "rejected",
            "Manually rejected by user",
        )

        return {"status": "rejected", "batch_id": batch_id, "item_id": item_id}


@router.get("/jobs/{job_id}", response_model=JobStatus, tags=["jobs"])
async def get_job(job_id: str):
    """Get detailed job log/progress."""
    async with get_supabase() as storage:
        job = await storage.get_job(UUID(job_id))
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

    return JobStatus(
        job_id=UUID(job["id"]),
        batch_id=job["batch_id"],
        job_type=job["job_type"],
        status=job["status"],
        started_at=job.get("started_at"),
        completed_at=job.get("completed_at"),
        error_message=job.get("error_message"),
        result_summary=job.get("result_summary"),
    )
