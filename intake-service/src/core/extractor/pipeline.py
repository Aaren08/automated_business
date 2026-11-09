"""Extraction pipeline - orchestrates product extraction, normalization, and database writes.

Aligned with business workflow:
- info.md is treated as a product-information document (not supplier identity).
- No supplier entity is inferred or created from document content.
- Multiple product candidates are extracted where source describes multiple products/variants.
- PDF tables, spreadsheet rows, Markdown text/tables, and OCR are complementary sources.
- Original values and source provenance are preserved.
- Product photographs in inventory/ are distinguished from information-bearing images in image/.

Product identity:
- internal_sku: our canonical identity, generated once from stable attributes, never recomputed.
- supplier_sku: exclusively the identifier explicitly supplied by a source document.
- Upsert conflict key is internal_sku, never supplier_sku.
- Durable source-to-product mapping via product_source_items table: a source batch item
  can map to multiple products, and a product can be mapped from multiple source items.
"""

import hashlib
import structlog
from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from src.core.parser import parser_registry
from src.core.extractor import (
    product_extractor, ProductCandidate,
    product_normalizer, NormalizedProduct,
)
from src.core.extractor.image_matcher import ImageMatcher
from src.core.storage.supabase import supabase_storage, get_supabase

logger = structlog.get_logger()


def _compute_internal_sku(
    canonical_name: Optional[str],
    model: Optional[str],
    brand: Optional[str],
    category: Optional[str],
    attributes: dict[str, Any],
    salt: Optional[str] = None,
) -> str:
    """Generate a deterministic internal SKU from stable product identity attributes.

    This SKU is derived ONLY from attributes that define product identity:
    brand, canonical_name, model, category, and stable physical attributes
    (size, color, material, dimensions, weight).

    It is NEVER derived from price, description, delivery location, or any
    mutable content. Once generated for a product, it is persisted and never
    recomputed or changed on subsequent reprocessing.

    Args:
        salt: Optional disambiguator appended when two products would otherwise
              collide (e.g., both have empty attributes). Ensures distinct products
              never share an internal_sku.

    Returns a string prefixed with "INT-".
    """
    parts = []
    if brand:
        parts.append(str(brand).strip().upper())
    if canonical_name:
        parts.append(str(canonical_name).strip().upper())
    if model:
        parts.append(str(model).strip().upper())
    if category:
        parts.append(str(category).strip().upper())

    stable_attrs = ["size", "color", "material", "dimensions", "weight"]
    for key in stable_attrs:
        if key in attributes and attributes[key]:
            parts.append(f"{key}:{attributes[key]}")

    content = "|".join(parts)
    if not content:
        content = "UNKNOWN_PRODUCT"
    if salt:
        content = f"{content}|{salt}"
    return f"INT-{hashlib.sha256(content.encode()).hexdigest()[:12].upper()}"


def _compute_content_hash(data: dict) -> str:
    """Compute deterministic content hash from data dict (for change detection only).

    This hash is used to detect whether a package's content has changed since
    last processing. It is NOT used as a product identity key.
    """
    def _serialize_value(value: Any) -> str:
        if isinstance(value, dict):
            items = [f"{k}:{_serialize_value(v)}" for k, v in sorted(value.items())]
            return "{" + ",".join(items) + "}"
        elif isinstance(value, list):
            items = [_serialize_value(v) for v in value]
            items.sort()
            return "[" + ",".join(items) + "]"
        else:
            return str(value)

    parts = []
    for key in sorted(data.keys()):
        value = data.get(key)
        if value is not None:
            parts.append(f"{key}:{_serialize_value(value)}")

    content = "|".join(parts)
    return hashlib.sha256(content.encode()).hexdigest()[:32]


class ExtractionPipeline:
    """Orchestrates the full extraction pipeline for an intake package."""

    def __init__(self, inbox_path: str = "/data/inbox"):
        self.inbox_path = inbox_path

    async def process_package(
        self,
        package_id: str,
        package_internal_id: str,
        monthly_batch: str,
        package_name: str,
        inbox_path: str = "/data/inbox",
    ) -> dict:
        """Process a single intake package: extract products, normalize, write to DB.

        Does NOT create or reference supplier entities.
        """
        results = {
            "package_id": package_id,
            "products_extracted": 0,
            "products_upserted": 0,
            "products_failed": 0,
            "products_flagged_review": 0,
            "price_history_entries": 0,
            "image_links_created": 0,
            "errors": [],
        }

        async with get_supabase() as storage:
            # 1. Get package items
            package_items = await storage.get_package_items(package_internal_id)
            if not package_items:
                results["errors"].append("No package items found")
                return results

            # 2. Extract product candidates from all package items
            product_candidates = await self._extract_products(
                storage, package_items, package_internal_id, inbox_path
            )
            results["products_extracted"] = len(product_candidates)

            # 3. Normalize and upsert products with internal_sku identity
            upsert_results = await self._normalize_and_upsert_products(
                storage, product_candidates, package_internal_id, monthly_batch, package_name
            )
            results["products_upserted"] = upsert_results["upserted"]
            results["products_failed"] = upsert_results["failed"]
            results["products_flagged_review"] = upsert_results["flagged_review"]
            results["price_history_entries"] = upsert_results["price_entries"]
            results["image_links_created"] = upsert_results["image_links"]

            # 4. Match product images to canonical products
            image_match_results = await self._match_package_images(
                storage, package_internal_id
            )
            results["image_links_created"] += image_match_results["matched"]
            results["images_auto_approved"] = image_match_results["auto_approved"]
            results["images_flagged_for_review"] = image_match_results["flagged_for_review"]
            results["images_unmatched"] = image_match_results["unmatched"]

            # 5. Update package with product count
            await storage.update_package(package_internal_id, {
                "total_products": upsert_results["upserted"],
                "status": "completed",
                "completed_at": datetime.utcnow().isoformat(),
            })

        return results

    async def _extract_products(
        self,
        storage,
        package_items: list[dict],
        package_internal_id: str,
        inbox_path: str,
    ) -> list[ProductCandidate]:
        """Extract product candidates from all package items."""
        all_candidates = []

        for item in package_items:
            if item.get("processing_status") != "completed":
                continue

            parsed_content = item.get("parsed_content")
            if not parsed_content:
                continue

            from src.core.parser.base import ParsedContent
            parsed = ParsedContent(
                text=parsed_content.get("text", ""),
                tables=parsed_content.get("tables", []),
                metadata=parsed_content.get("metadata", {}),
                error=parsed_content.get("error"),
            )

            file_name = item.get("file_name", "")
            file_type = item.get("file_type", "")
            file_path = item.get("file_path", "")
            item_id = str(item.get("id"))

            # Extract based on file type
            candidates = self._extract_from_parsed(
                parsed, file_name, file_type, file_path, item_id
            )

            for c in candidates:
                c.source_batch_item_id = item_id

            all_candidates.extend(candidates)

        return all_candidates

    def _extract_from_parsed(
        self, parsed, file_name: str, file_type: str, file_path: str, item_id: str
    ) -> list[ProductCandidate]:
        """Extract products from parsed content based on file type."""
        if file_type == "markdown":
            return product_extractor.extract_from_markdown(parsed, file_name)
        elif file_type == "pdf":
            return product_extractor.extract_from_pdf(parsed, file_name)
        elif file_type == "spreadsheet":
            return product_extractor.extract_from_spreadsheet(parsed, file_name)
        elif file_type == "image":
            return product_extractor.extract_from_ocr(parsed, file_name)
        return []

    async def _normalize_and_upsert_products(
        self,
        storage,
        candidates: list[ProductCandidate],
        package_internal_id: str,
        monthly_batch: str,
        package_name: str,
    ) -> dict:
        """Normalize candidates and upsert to database with internal_sku identity.

        Identity resolution order:
        1. Check product_source_items mapping by (batch_item_id, candidate_key)
           → if found, use existing product_id and its persisted internal_sku.
        2. If no source mapping, compute internal_sku from stable attributes
           and look up by internal_sku → if found, update existing product.
        3. If no internal_sku match, check supplier_sku for ambiguity
           (multiple products sharing supplier_sku → flag for review).
        4. If no match at all, insert new product with computed internal_sku.

        After product is resolved, create product_source_items mapping.

        Candidate-key stability:
        candidate_key (e.g. "table:0,row:2" or "line:5") is derived from
        extraction position. It is NOT stable when source content changes:
        inserting/removing lines shifts line numbers; inserting/removing
        table rows shifts row numbers. When a candidate_key changes between
        extractions, the source mapping is not found and the pipeline falls
        through to Step 2 (internal_sku lookup). If the internal_sku matches,
        the existing product is updated. If not, a new product is created.
        This is the correct behavior: internal_sku (from stable attributes)
        is the durable identity, not the extraction position.

        internal_sku is generated once on insert and NEVER changed on update.
        supplier_sku comes exclusively from source data, never computed.
        """
        results = {
            "upserted": 0,
            "failed": 0,
            "flagged_review": 0,
            "price_entries": 0,
            "image_links": 0,
        }

        for candidate in candidates:
            try:
                batch_item_id = str(candidate.source_batch_item_id) if candidate.source_batch_item_id else None
                candidate_key = candidate.source_location or f"unknown:{candidate.extraction_method}"

                if not batch_item_id:
                    logger.error("product_upsert_failed",
                                 error="No source_batch_item_id on candidate")
                    results["failed"] += 1
                    continue

                # Normalize
                normalized = product_normalizer.normalize(candidate)

                if normalized.validation_errors:
                    logger.warning("product_validation_errors",
                                   internal_sku=None,
                                   supplier_sku=normalized.supplier_sku,
                                   errors=normalized.validation_errors)

                # supplier_sku comes exclusively from source data
                supplier_sku = normalized.supplier_sku

                # ------------------------------------------------------------------
                # Step 1: Check durable source-to-product mapping first
                # ------------------------------------------------------------------
                existing_mapping = await storage.get_product_by_source_item(
                    batch_item_id, candidate_key
                )

                product = None
                internal_sku = None

                if existing_mapping:
                    # A previous processing run mapped this source item candidate
                    # to this canonical product. Use the existing product's identity.
                    product_id = existing_mapping["id"]
                    product = existing_mapping
                    internal_sku = product.get("internal_sku")
                    logger.info("source_mapping_found",
                                batch_item_id=batch_item_id,
                                candidate_key=candidate_key,
                                product_id=product_id,
                                internal_sku=internal_sku)

                    # Update mutable fields only; internal_sku is preserved by update_product
                    update_data = self._build_update_data(
                        normalized, supplier_sku, monthly_batch, package_name,
                        package_internal_id, candidate
                    )
                    await storage.update_product(UUID(product_id), update_data)
                    results["upserted"] += 1

                else:
                    # ------------------------------------------------------------------
                    # Step 2: No source mapping. Compute internal_sku and look it up.
                    # ------------------------------------------------------------------
                    internal_sku = _compute_internal_sku(
                        normalized.canonical_name,
                        normalized.model,
                        normalized.brand,
                        normalized.category,
                        normalized.attributes,
                    )

                    existing = await storage.get_product_by_internal_sku(internal_sku)

                    if existing:
                        # Case 2a: internal_sku match → update existing product.
                        product_id = existing["id"]
                        internal_sku = existing.get("internal_sku")
                        existing_supplier_skus = existing.get("supplier_sku")

                        # Detect ambiguous case: supplier_sku matches a different product
                        if supplier_sku and existing_supplier_skus and supplier_sku != existing_supplier_skus:
                            # Same supplier_sku but different internal_sku — ambiguous, flag for review
                            update_data = self._build_update_data(
                                normalized, supplier_sku, monthly_batch, package_name,
                                package_internal_id, candidate
                            )
                            update_data["review_status"] = "pending_review"
                            update_data["extraction_notes"] = (
                                f"Ambiguous: supplier_sku {supplier_sku} matches existing product "
                                f"with different internal_sku {internal_sku}."
                            )
                            await storage.update_product(UUID(product_id), update_data)
                            results["flagged_review"] += 1
                        else:
                            # Straightforward update — preserve internal_sku
                            update_data = self._build_update_data(
                                normalized, supplier_sku, monthly_batch, package_name,
                                package_internal_id, candidate
                            )
                            await storage.update_product(UUID(product_id), update_data)
                            results["upserted"] += 1

                        product = await storage.get_product_by_id(UUID(product_id))

                    else:
                        # Case 2b: no internal_sku match.
                        # Check supplier_sku for ambiguity across products.
                        supplier_matches = []
                        if supplier_sku:
                            supplier_matches = await storage.get_product_by_supplier_sku(supplier_sku)

                        if supplier_matches:
                            # Same supplier_sku exists on one or more products with
                            # different internal_sku → ambiguous, flag for review.
                            # Do NOT merge; insert as new product with distinct internal_sku.
                            existing_ids = [str(m["id"]) for m in supplier_matches]
                            product = await self._insert_product_safe(
                                storage, internal_sku, supplier_sku, normalized, monthly_batch,
                                package_name, package_internal_id, candidate,
                                review_status="pending_review",
                                extraction_notes=(
                                    f"Ambiguous: supplier_sku {supplier_sku} already assigned to "
                                    f"products {', '.join(existing_ids)} with different internal_skus."
                                ),
                                batch_item_id=batch_item_id,
                                candidate_key=candidate_key,
                                results=results,
                            )
                            if product:
                                results["flagged_review"] += 1
                        else:
                            # No conflict — insert new product with computed internal_sku.
                            product = await self._insert_product_safe(
                                storage, internal_sku, supplier_sku, normalized, monthly_batch,
                                package_name, package_internal_id, candidate,
                                batch_item_id=batch_item_id,
                                candidate_key=candidate_key,
                                results=results,
                            )
                            if product:
                                results["upserted"] += 1

                # ------------------------------------------------------------------
                # Step 3: Record source-to-product mapping
                # ------------------------------------------------------------------
                if product and batch_item_id:
                    product_id = str(product["id"])
                    try:
                        await storage.create_product_source_mapping(
                            product_id, batch_item_id, candidate_key
                        )
                    except Exception as e:
                        # Mapping creation is best-effort; don't fail the product
                        logger.warning("mapping_creation_failed",
                                       product_id=product_id,
                                       batch_item_id=batch_item_id,
                                       candidate_key=candidate_key,
                                       error=str(e))

                # ------------------------------------------------------------------
                # Step 4: Add price history entry (idempotent)
                # ------------------------------------------------------------------
                if product and normalized.price_amount is not None:
                    effective_date = datetime.utcnow().date().isoformat()
                    already_exists = await storage.price_history_exists(
                        product_id=str(product["id"]),
                        package_id=package_internal_id,
                        source_batch_item_id=batch_item_id,
                        effective_date=effective_date,
                        price_amount=float(normalized.price_amount),
                    )
                    if not already_exists:
                        history_data = {
                            "product_id": str(product["id"]),
                            "package_id": str(package_internal_id),
                            "price_amount": float(normalized.price_amount),
                            "price_currency": normalized.price_currency,
                            "price_unit": normalized.price_unit,
                            "price_min_order_qty": normalized.price_min_order_qty,
                            "effective_date": effective_date,
                            "source_batch_item_id": batch_item_id,
                            "change_reason": "new_listing",
                        }
                        await storage.add_price_history(history_data)
                        results["price_entries"] += 1

            except Exception as e:
                logger.error("product_upsert_failed",
                             internal_sku=internal_sku if 'internal_sku' in locals() else None,
                             supplier_sku=candidate.supplier_sku,
                             error=str(e))
                results["failed"] += 1

        return results

    async def _insert_product_safe(
        self,
        storage,
        internal_sku: str,
        supplier_sku: Optional[str],
        normalized: NormalizedProduct,
        monthly_batch: str,
        package_name: str,
        package_internal_id: str,
        candidate: ProductCandidate,
        batch_item_id: Optional[str] = None,
        candidate_key: Optional[str] = None,
        review_status: Optional[str] = None,
        extraction_notes: Optional[str] = None,
        results: Optional[dict] = None,
    ):
        """Insert a new product with collision-safe internal_sku handling.

        If the unique constraint on internal_sku is violated (e.g., two products
        in the same batch computed to the same SKU because both have empty
        attributes), retry with a disambiguating salt.

        Returns the inserted product dict, or None on failure.
        """
        attempt = 0
        salt = None
        product = None
        while product is None and attempt < 5:
            attempt += 1
            try:
                product_dict = self._build_product_data(
                    internal_sku, supplier_sku, normalized, monthly_batch,
                    package_name, package_internal_id, candidate, salt=salt
                )
                if review_status is not None:
                    product_dict["review_status"] = review_status
                if extraction_notes is not None:
                    product_dict["extraction_notes"] = extraction_notes
                product = await storage.insert_product(product_data=product_dict)
            except Exception as e:
                if "internal_sku" in str(e).lower() or "duplicate" in str(e).lower():
                    salt = f"dup{attempt}"
                    internal_sku = _compute_internal_sku(
                        normalized.canonical_name,
                        normalized.model,
                        normalized.brand,
                        normalized.category,
                        normalized.attributes,
                        salt=salt,
                    )
                    logger.warning("internal_sku_collision_retry",
                                   batch_item_id=batch_item_id,
                                   candidate_key=candidate_key,
                                   attempt=attempt,
                                   salt=salt)
                    continue
                raise
        if product is None and results is not None:
            results["failed"] += 1
        return product

    def _build_update_data(
        self,
        normalized: NormalizedProduct,
        supplier_sku: Optional[str],
        monthly_batch: str,
        package_name: str,
        package_internal_id: str,
        candidate: ProductCandidate,
    ) -> dict:
        """Build mutable-field update data (excludes internal_sku, id, created_at)."""
        return {
            "supplier_sku": supplier_sku,
            "package_id": str(package_internal_id),
            "monthly_batch": monthly_batch,
            "package_name": package_name,
            "supplier_product_name": normalized.supplier_product_name,
            "canonical_name": normalized.canonical_name,
            "description": normalized.description,
            "category": normalized.category,
            "brand": normalized.brand,
            "model": normalized.model,
            "attributes": normalized.attributes,
            "price_amount": float(normalized.price_amount) if normalized.price_amount else None,
            "price_currency": normalized.price_currency,
            "price_unit": normalized.price_unit,
            "price_min_order_qty": normalized.price_min_order_qty,
            "price_valid_from": normalized.price_valid_from,
            "price_valid_to": normalized.price_valid_to,
            "availability_status": normalized.availability_status,
            "availability_quantity": normalized.availability_quantity,
            "availability_lead_time_days": normalized.availability_lead_time_days,
            "delivery_terms": normalized.delivery_terms,
            "delivery_origin": normalized.delivery_origin,
            "delivery_destination": normalized.delivery_destination,
            "shipping_weight_kg": float(normalized.shipping_weight_kg) if normalized.shipping_weight_kg else None,
            "shipping_dimensions_cm": normalized.shipping_dimensions_cm,
            "package_quantity": normalized.package_quantity,
            "master_carton_quantity": normalized.master_carton_quantity,
            "package_type": normalized.package_type,
            "source_batch_item_id": str(candidate.source_batch_item_id) if candidate.source_batch_item_id else None,
            "extraction_confidence": normalized.extraction_confidence if hasattr(normalized, 'extraction_confidence') else candidate.extraction_confidence,
            "extraction_notes": normalized.validation_warnings + normalized.validation_errors,
            "extraction_method": candidate.extraction_method,
            "review_status": "auto_approved" if not normalized.validation_errors else "pending_review",
            "is_active": True,
        }

    def _build_product_data(
        self,
        internal_sku: str,
        supplier_sku: Optional[str],
        normalized: NormalizedProduct,
        monthly_batch: str,
        package_name: str,
        package_internal_id: str,
        candidate: ProductCandidate,
        salt: Optional[str] = None,
    ) -> dict:
        """Build full product data for insert.

        If salt is provided, internal_sku is recomputed with the salt to avoid
        unique-constraint collisions when two products would otherwise share
        the same internal_sku (e.g., both have empty attributes).
        """
        if salt:
            internal_sku = _compute_internal_sku(
                normalized.canonical_name,
                normalized.model,
                normalized.brand,
                normalized.category,
                normalized.attributes,
                salt=salt,
            )
        return {
            "internal_sku": internal_sku,
            "supplier_sku": supplier_sku,
            "package_id": str(package_internal_id),
            "monthly_batch": monthly_batch,
            "package_name": package_name,
            "supplier_product_name": normalized.supplier_product_name,
            "canonical_name": normalized.canonical_name,
            "description": normalized.description,
            "category": normalized.category,
            "brand": normalized.brand,
            "model": normalized.model,
            "attributes": normalized.attributes,
            "price_amount": float(normalized.price_amount) if normalized.price_amount else None,
            "price_currency": normalized.price_currency,
            "price_unit": normalized.price_unit,
            "price_min_order_qty": normalized.price_min_order_qty,
            "price_valid_from": normalized.price_valid_from,
            "price_valid_to": normalized.price_valid_to,
            "availability_status": normalized.availability_status,
            "availability_quantity": normalized.availability_quantity,
            "availability_lead_time_days": normalized.availability_lead_time_days,
            "delivery_terms": normalized.delivery_terms,
            "delivery_origin": normalized.delivery_origin,
            "delivery_destination": normalized.delivery_destination,
            "shipping_weight_kg": float(normalized.shipping_weight_kg) if normalized.shipping_weight_kg else None,
            "shipping_dimensions_cm": normalized.shipping_dimensions_cm,
            "package_quantity": normalized.package_quantity,
            "master_carton_quantity": normalized.master_carton_quantity,
            "package_type": normalized.package_type,
            "source_batch_item_id": str(candidate.source_batch_item_id) if candidate.source_batch_item_id else None,
            "extraction_confidence": normalized.extraction_confidence if hasattr(normalized, 'extraction_confidence') else candidate.extraction_confidence,
            "extraction_notes": normalized.validation_warnings + normalized.validation_errors,
            "extraction_method": candidate.extraction_method,
            "review_status": "auto_approved" if not normalized.validation_errors else "pending_review",
            "is_active": True,
        }

    async def _match_package_images(
        self,
        storage,
        package_internal_id: str,
    ) -> dict:
        """Match product images in this package to canonical products.

        Uses the image matcher to associate product photographs with their
        canonical product records. High-confidence matches are auto-approved;
        low-confidence matches are flagged for human review.

        Returns dict with match statistics.
        """
        matcher = ImageMatcher()
        return await matcher.match_images_for_batch(
            storage, package_internal_id
        )


# Global instance
extraction_pipeline = ExtractionPipeline()
