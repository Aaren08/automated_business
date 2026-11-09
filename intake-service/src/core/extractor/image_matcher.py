"""Image matching - matches product images to canonical products.

Matching strategy (in priority order):
1. Filename matching - product name/SKU found in image filename
2. OCR text matching - text found in image matches product name/SKU
3. Perceptual hash similarity - visually similar to already-matched images

Each match produces a confidence score:
- >= 0.80: Auto-approve match
- 0.50 - 0.80: Flag for human review
- < 0.30: No match
"""

import re
import structlog
from typing import Any, Optional
from uuid import UUID

from src.core.storage.supabase import supabase_storage, get_supabase
from src.core.image_inventory.hashing import (
    perceptual_hash,
    hamming_distance,
    similarity_score,
    SimilarityThresholds,
)

logger = structlog.get_logger()


class ImageMatchCandidate:
    """A candidate match between an image and a product."""

    def __init__(
        self,
        product_id: str,
        image_id: str,
        confidence: float,
        match_method: str,
        match_reasoning: str,
    ):
        self.product_id = product_id
        self.image_id = image_id
        self.confidence = confidence
        self.match_method = match_method
        self.match_reasoning = match_reasoning


class ImageMatcher:
    """Matches product images to canonical products."""

    # Confidence thresholds
    AUTO_APPROVE_THRESHOLD = 0.80   # Auto-approve match
    REVIEW_THRESHOLD = 0.50         # Flag for human review
    MIN_MATCH_THRESHOLD = 0.30      # Below this = no match

    async def match_images_for_batch(
        self,
        storage,
        batch_internal_id: str,
    ) -> dict:
        """Match all unmatched product images in a batch to canonical products.

        Returns dict with counts of auto-approved, flagged, and unmatched images.
        """
        results = {
            "matched": 0,
            "auto_approved": 0,
            "flagged_for_review": 0,
            "unmatched": 0,
            "errors": [],
        }

        # Get unmatched package items (images) for this batch
        image_items = await storage.get_unmatched_product_images(batch_internal_id)

        for item in image_items:
            try:
                match = await self._match_single_image(
                    storage, item
                )

                if match:
                    # Create product_product_images link
                    await storage.link_product_image({
                        "product_id": match.product_id,
                        "product_image_id": match.image_id,
                        "role": "primary",
                        "sort_order": 0,
                        "match_confidence": match.confidence,
                        "match_method": match.match_method,
                        "match_reasoning": match.match_reasoning,
                    })

                    # Update batch_item with match confidence
                    await storage.update_batch_item_match(
                        item["id"],
                        match.confidence,
                        match.match_method,
                        match.match_reasoning,
                    )

                    results["matched"] += 1

                    if match.confidence >= self.AUTO_APPROVE_THRESHOLD:
                        results["auto_approved"] += 1
                    elif match.confidence >= self.REVIEW_THRESHOLD:
                        results["flagged_for_review"] += 1
                    else:
                        results["unmatched"] += 1
                else:
                    results["unmatched"] += 1

            except Exception as e:
                logger.error("image_match_failed",
                             image_id=item.get("id"),
                             error=str(e))
                results["errors"].append({
                    "image_id": item.get("id"),
                    "error": str(e),
                })

        return results

    async def _match_single_image(
        self,
        storage,
        image_item: dict,
    ) -> Optional[ImageMatchCandidate]:
        """Match a single product image to a canonical product."""

        image_id = str(image_item["id"])
        image_name = image_item.get("original_filename", "")
        image_phash = image_item.get("perceptual_hash")
        ocr_text = image_item.get("ocr_text") or ""

        # Step 1: Try filename matching
        candidate = await self._match_by_filename(
            storage, file_name=image_name, image_id=image_id
        )
        if candidate:
            return candidate

        # Step 2: Try OCR text matching
        if ocr_text:
            candidate = await self._match_by_ocr_text(
                storage, ocr_text=ocr_text, image_id=image_id
            )
            if candidate:
                return candidate

        # Step 3: Try perceptual hash similarity
        if image_phash:
            candidate = await self._match_by_perceptual_hash(
                storage, image_phash=image_phash, image_id=image_id
            )
            if candidate:
                return candidate

        return None

    async def _match_by_filename(
        self,
        storage,
        file_name: str,
        image_id: str,
    ) -> Optional[ImageMatchCandidate]:
        """Match product by filename containing SKU or product name."""

        # Extract alphanumeric tokens from filename (without extension)
        name_without_ext = file_name.rsplit(".", 1)[0] if "." in file_name else file_name
        tokens = re.findall(r"[A-Za-z0-9]+", name_without_ext)

        # Build search terms - try full name and individual tokens
        search_terms = []
        full_name = " ".join(tokens)
        if full_name:
            search_terms.append(full_name)
        # Also try individual tokens (for SKU-like matches)
        search_terms.extend(tokens)

        # Try to match against products by canonical_name or supplier_sku
        for term in search_terms:
            if len(term) < 3:
                continue

            # Try canonical_name match (case-insensitive)
            r = await storage.client.table("products").select("id, canonical_name, supplier_sku").ilike("canonical_name", f"%{term}%").limit(5).execute()
            if r.data:
                for match in r.data:
                    confidence = self._compute_filename_match_confidence(
                        term, match["canonical_name"], match.get("supplier_sku")
                    )
                    if confidence >= self.MIN_MATCH_THRESHOLD:
                        return ImageMatchCandidate(
                            product_id=str(match["id"]),
                            image_id=image_id,
                            confidence=confidence,
                            match_method="filename",
                            match_reasoning=f"Filename '{file_name}' matches canonical_name '{match['canonical_name']}'",
                        )

            # Try supplier_sku match
            r = await storage.client.table("products").select("id, canonical_name, supplier_sku").eq("supplier_sku", term).limit(5).execute()
            if r.data:
                for match in r.data:
                    return ImageMatchCandidate(
                        product_id=str(match["id"]),
                        image_id=image_id,
                        confidence=0.95,
                        match_method="filename",
                        match_reasoning=f"Filename '{file_name}' matches supplier_sku '{match['supplier_sku']}'",
                    )

        return None

    def _compute_filename_match_confidence(
        self,
        search_term: str,
        canonical_name: Optional[str],
        supplier_sku: Optional[str],
    ) -> float:
        """Compute confidence for filename-based match."""

        if not canonical_name:
            return 0.0

        search_lower = search_term.lower()
        canonical_lower = canonical_name.lower()

        # Exact match
        if search_lower == canonical_lower:
            return 0.95

        # Contains match
        if search_lower in canonical_lower or canonical_lower in search_lower:
            shorter = min(len(search_lower), len(canonical_lower))
            longer = max(len(search_lower), len(canonical_lower))
            ratio = shorter / longer if longer > 0 else 0
            return 0.8 * ratio

        # Word overlap
        search_words = set(search_lower.split())
        canonical_words = set(canonical_lower.split())
        if search_words and canonical_words:
            overlap = len(search_words & canonical_words) / len(search_words | canonical_words)
            return 0.6 * overlap

        return 0.0

    async def _match_by_ocr_text(
        self,
        storage,
        ocr_text: str,
        image_id: str,
    ) -> Optional[ImageMatchCandidate]:
        """Match product by OCR text found in image."""

        # Extract potential product names/SKUs from OCR text
        sku_pattern = re.compile(r'\b[A-Z0-9]{3,20}\b')
        skus = sku_pattern.findall(ocr_text)

        # Try matching by SKU first (highest confidence)
        for sku in skus:
            r = await storage.client.table("products").select("id, canonical_name, supplier_sku").eq("supplier_sku", sku).limit(5).execute()
            if r.data:
                for match in r.data:
                    return ImageMatchCandidate(
                        product_id=str(match["id"]),
                        image_id=image_id,
                        confidence=0.85,
                        match_method="ocr",
                        match_reasoning=f"OCR text contains supplier_sku '{sku}'",
                    )

        # Try matching by canonical_name keywords
        words = re.findall(r'[A-Za-z]{3,}', ocr_text)
        for word in words:
            if len(word) < 3:
                continue
            r = await storage.client.table("products").select("id, canonical_name").ilike("canonical_name", f"%{word}%").limit(5).execute()
            if r.data:
                for match in r.data:
                    confidence = self._compute_text_match_confidence(
                        word, match["canonical_name"]
                    )
                    if confidence >= self.MIN_MATCH_THRESHOLD:
                        return ImageMatchCandidate(
                            product_id=str(match["id"]),
                            image_id=image_id,
                            confidence=confidence,
                            match_method="ocr",
                            match_reasoning=f"OCR text contains '{word}' which matches canonical_name '{match['canonical_name']}'",
                        )

        return None

    def _compute_text_match_confidence(
        self,
        search_term: str,
        canonical_name: str,
    ) -> float:
        """Compute confidence for OCR text match."""

        if not canonical_name:
            return 0.0

        search_lower = search_term.lower()
        canonical_lower = canonical_name.lower()

        # Exact match
        if search_lower == canonical_lower:
            return 0.90

        # Contains match
        if search_lower in canonical_lower or canonical_lower in search_lower:
            shorter = min(len(search_lower), len(canonical_lower))
            longer = max(len(search_lower), len(canonical_lower))
            ratio = shorter / longer if longer > 0 else 0
            return 0.75 * ratio

        # Word overlap
        search_words = set(search_lower.split())
        canonical_words = set(canonical_lower.split())
        if search_words and canonical_words:
            overlap = len(search_words & canonical_words) / len(search_words | canonical_words)
            return 0.6 * overlap

        return 0.0

    async def _match_by_perceptual_hash(
        self,
        storage,
        image_phash: str,
        image_id: str,
    ) -> Optional[ImageMatchCandidate]:
        """Match product by perceptual hash similarity to already-matched images."""

        # Find visually similar images that are already matched to products
        r = await storage.client.table("product_images").select(
            "id, product_id, perceptual_hash, match_confidence"
        ).not_.is_("perceptual_hash", "null").not_.is_("product_id", "null").execute()

        if not r.data:
            return None

        best_match = None
        best_confidence = 0.0

        for asset in r.data:
            asset_phash = asset.get("perceptual_hash")
            if not asset_phash:
                continue

            distance = hamming_distance(image_phash, asset_phash)
            if distance > SimilarityThresholds.SIMILAR:
                continue

            confidence = similarity_score(image_phash, asset_phash)
            confidence *= 0.85  # Reduce confidence since this is indirect matching

            if confidence > best_confidence and confidence >= self.MIN_MATCH_THRESHOLD:
                best_confidence = confidence
                best_match = asset

        if best_match:
            return ImageMatchCandidate(
                product_id=str(best_match["product_id"]),
                image_id=image_id,
                confidence=best_confidence,
                match_method="perceptual_hash",
                match_reasoning=f"Image visually similar to already-matched image (confidence {best_confidence:.2f})",
            )

        return None