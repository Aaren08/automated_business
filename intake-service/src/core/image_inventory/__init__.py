"""Image Inventory Module - Core image processing pipeline.

This module handles:
- SHA-256 and perceptual hashing for deduplication
- Thumbnail generation (multiple sizes)
- Contact sheet generation for batch overview
- Supabase Storage upload for originals and thumbnails
- Cross-batch exact duplicate detection
- Visual similarity detection (for review flagging)
"""
import asyncio
import structlog
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID

from src.config import settings
from src.core.storage.supabase import supabase_storage, get_supabase
from src.core.image_inventory.hashing import (
    sha256_file,
    perceptual_hash,
    hamming_distance,
    similarity_score,
    get_image_dimensions,
    is_valid_image,
    SimilarityThresholds,
)
from src.core.image_inventory.thumbnails import (
    generate_thumbnail,
    generate_thumbnail_from_file,
    generate_multiple_thumbnails,
)
from src.core.image_inventory.contact_sheet import (
    generate_contact_sheet,
    generate_contact_sheet_from_batch_items,
)
from src.core.image_inventory.storage import storage_client

logger = structlog.get_logger()


async def _run_sync(func, *args, **kwargs):
    """Run synchronous function in thread pool."""
    return await asyncio.to_thread(func, *args, **kwargs)


class ImageInventory:
    """Manages image processing and storage for a batch."""
    
    def __init__(self, inbox_path: str = "/data/inbox"):
        self.inbox_path = Path(inbox_path)
    
    async def process_batch_images(
        self,
        batch_id: str,
        batch_internal_id: str,
        batch_items: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Process all images in a batch.
        
        Returns:
            Dict with processing results and any errors
        """
        results = {
            "processed": 0,
            "failed": 0,
            "duplicates": 0,
            "new_assets": 0,
            "contact_sheet_path": None,
            "errors": [],
        }
        
        image_items = [item for item in batch_items if item.get("file_type") == "image"]
        
        if not image_items:
            logger.info("no_images_in_batch", batch_id=batch_id)
            return results
                
        # Get batch source path for correct file resolution
        async with get_supabase() as storage:
            batch = await storage.get_batch_by_id(batch_id)
            if not batch:
                results["errors"].append({"error": "Batch not found"})
                return results
            source_path = batch["source_path"]
        
        # Collect image data for contact sheet
        contact_sheet_data = []
        
        async with get_supabase() as storage:
            for item in image_items:
                try:
                    result = await self._process_single_image(
                        batch_id, batch_internal_id, item, storage, source_path
                    )
                    
                    if result.get("duplicate"):
                        results["duplicates"] += 1
                    else:
                        results["new_assets"] += 1
                    
                    results["processed"] += 1
                    
                    # Add to contact sheet data
                    file_path = self.inbox_path / source_path / item["file_path"]
                    try:
                        with file_path.open("rb") as f:
                            contact_sheet_data.append((f.read(), item["file_name"]))
                    except Exception:
                        pass
                    
                except Exception as e:
                    results["failed"] += 1
                    results["errors"].append({
                        "file": item["file_name"],
                        "error": str(e),
                    })
                    logger.error("image_processing_failed", 
                                batch_id=batch_id, 
                                file=item["file_name"],
                                error=str(e))
            
            # Generate and upload contact sheet
            if contact_sheet_data:
                try:
                    contact_sheet_bytes = generate_contact_sheet(contact_sheet_data)
                    if contact_sheet_bytes:
                        cs_result = await storage_client.upload_batch_contact_sheet(
                            contact_sheet_bytes, source_path
                        )
                        results["contact_sheet_path"] = cs_result["path"]
                        # Update batch with contact sheet path
                        await storage.update_batch(batch_id, {
                            "contact_sheet_path": cs_result["path"]
                        })
                        # Insert contact sheet metadata
                        batch_rec = storage.client.table("batches").select("id").eq("batch_id", batch_id).limit(1).execute()
                        if batch_rec.data:
                            storage.client.table("batch_contact_sheets").insert({
                                "batch_id": batch_rec.data[0]["id"],
                                "storage_path": cs_result["path"],
                                "image_count": len(contact_sheet_data),
                                "grid_cols": 5,
                                "grid_rows": (len(contact_sheet_data) + 4) // 5,
                            }).execute()
                        else:
                            logger.warning("contact_sheet_batch_not_found", batch_id=batch_id)
                except Exception as e:
                    logger.warning("contact_sheet_failed", batch_id=batch_id, error=str(e))
        
        return results
    
    async def _process_single_image(
        self,
        batch_id: str,
        batch_internal_id: str,
        item: Dict[str, Any],
        storage: "SupabaseStorage",
        source_path: str,
    ) -> Dict[str, Any]:
        """Process a single image file."""
        file_path = self.inbox_path / source_path / item["file_path"]
        file_hash = item["file_hash"]
        # Read image bytes
        with file_path.open("rb") as f:
            image_bytes = f.read()
        
        # Verify image is valid
        if not is_valid_image(file_path):
            return {"duplicate": False, "error": "Invalid image file"}
        
        # Get dimensions
        width, height = get_image_dimensions(file_path) or (None, None)
        
        # Compute perceptual hash
        phash = perceptual_hash(file_path)
        
        # Check for exact duplicate in product_images
        async with get_supabase() as s:
            existing_asset = await self._find_asset_by_hash(s, file_hash)
        
        if existing_asset:
            # Exact duplicate found - increment reference count
            await self._increment_asset_reference(s, existing_asset["id"])
            
            # Update batch_item with asset reference
            storage.client.table("batch_items").update({
                "product_image_id": existing_asset["id"],
                "perceptual_hash": phash,
                "width": width,
                "height": height,
            }).eq("id", item["id"]).execute()
            
            return {"duplicate": True, "asset_id": existing_asset["id"]}
        
        # New asset - upload to storage (organized under the package folder)
        upload_result = await storage_client.upload_image_asset(
            image_bytes,
            item["file_name"],
            source_path,
            item["file_path"],
        )
        
        # Create product_images record
        asset_data = {
            "file_hash": file_hash,
            "perceptual_hash": phash,
            "original_filename": item["file_name"],
            "mime_type": item["mime_type"],
            "width": width,
            "height": height,
            "file_size": item["file_size"],
            "original_path": upload_result["original_path"],
            "thumbnail_path": upload_result["thumbnails"].get("small"),
            "first_seen_batch_id": batch_internal_id,
            "first_seen_batch_item_id": item["id"],
            "reference_count": 1,
        }
        
        async with get_supabase() as s:
            result = s.client.table("product_images").insert(asset_data).execute()
            if result.data:
                asset_id = result.data[0]["id"]
                
                # Update batch_item with asset reference
                storage.client.table("batch_items").update({
                    "product_image_id": asset_id,
                    "perceptual_hash": phash,
                    "width": width,
                    "height": height,
                    "thumbnail_path": upload_result["thumbnails"].get("small"),
                }).eq("id", item["id"]).execute()
                
                return {"duplicate": False, "asset_id": asset_id}
        
        return {"duplicate": False, "error": "Failed to create asset record"}
    
    async def _find_asset_by_hash(self, storage: "SupabaseStorage", file_hash: str) -> Optional[Dict]:
        """Find existing asset by SHA-256 hash."""
        try:
            result = storage.client.table("product_images").select("*").eq("file_hash", file_hash).limit(1).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            logger.warning("find_asset_by_hash_error", file_hash=file_hash, error=str(e))
            return None
    
    async def _increment_asset_reference(self, storage: "SupabaseStorage", asset_id: str):
        """Increment reference count for an asset."""
        try:
            result = storage.client.table("product_images").select("reference_count").eq("id", asset_id).execute()
            if result.data:
                current = result.data[0].get("reference_count", 1)
                storage.client.table("product_images").update({
                    "reference_count": current + 1
                }).eq("id", asset_id).execute()
        except Exception as e:
            logger.warning("increment_asset_reference_error", asset_id=asset_id, error=str(e))
    
    async def find_similar_images(
        self,
        perceptual_hash: str,
        threshold: int = SimilarityThresholds.SIMILAR,
        exclude_asset_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Find visually similar images across all batches."""
        if not perceptual_hash:
            return []
        
        async with get_supabase() as storage:
            result = await self._find_similar_images(storage, perceptual_hash, threshold, exclude_asset_id)
            return result
    
    async def _find_similar_images(
        self,
        storage: "SupabaseStorage",
        perceptual_hash: str,
        threshold: int,
        exclude_asset_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        query = storage.client.table("product_images").select("*").not_.is_("perceptual_hash", "null")
        if exclude_asset_id:
            query = query.neq("id", exclude_asset_id)
        
        result = query.execute()
        
        similar = []
        for asset in result.data:
            if asset.get("perceptual_hash"):
                distance = hamming_distance(perceptual_hash, asset["perceptual_hash"])
                if distance <= threshold:
                    asset["similarity_score"] = similarity_score(perceptual_hash, asset["perceptual_hash"])
                    asset["hamming_distance"] = distance
                    asset["similarity_class"] = SimilarityThresholds.classify(distance)
                    similar.append(asset)
        
        similar.sort(key=lambda x: x["similarity_score"], reverse=True)
        return similar
    
    async def _increment_asset_reference(self, storage: "SupabaseStorage", asset_id: str):
        """Increment reference count for an asset."""
        # Get current count
        result = storage.client.table("product_images").select("reference_count").eq("id", asset_id).execute()
        if result.data:
            current = result.data[0].get("reference_count", 1)
            storage.client.table("product_images").update({
                "reference_count": current + 1
            }).eq("id", asset_id).execute()
    
    async def find_similar_images(
        self,
        perceptual_hash: str,
        threshold: int = SimilarityThresholds.SIMILAR,
        exclude_asset_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Find visually similar images across all batches.
        
        Args:
            perceptual_hash: dhash of query image
            threshold: Maximum Hamming distance
            exclude_asset_id: Asset ID to exclude from results
        
        Returns:
            List of similar assets with similarity scores
        """
        if not perceptual_hash:
            return []
        
        async with get_supabase() as storage:
            query = storage.client.table("product_images").select("*").not_.is_("perceptual_hash", "null")
            if exclude_asset_id:
                query = query.neq("id", exclude_asset_id)
            
            result = query.execute()
            
            similar = []
            for asset in result.data:
                if asset.get("perceptual_hash"):
                    distance = hamming_distance(perceptual_hash, asset["perceptual_hash"])
                    if distance <= threshold:
                        asset["similarity_score"] = similarity_score(perceptual_hash, asset["perceptual_hash"])
                        asset["hamming_distance"] = distance
                        asset["similarity_class"] = SimilarityThresholds.classify(distance)
                        similar.append(asset)
            
            # Sort by similarity (highest first)
            similar.sort(key=lambda x: x["similarity_score"], reverse=True)
            return similar


# Global instance
image_inventory = ImageInventory()


async def process_batch_image_inventory(
    batch_id: str,
    batch_internal_id: str,
    batch_items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Convenience function to process batch image inventory."""
    return await image_inventory.process_batch_images(batch_id, batch_internal_id, batch_items)