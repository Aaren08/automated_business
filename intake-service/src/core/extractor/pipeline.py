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
- Durable source-to-product mapping via product_source_document_links table: a
  source document can map to multiple products, and a product can be mapped
  from multiple source documents.
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
from src.config import settings

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
        source_document_map: Optional[dict[str, str]] = None,
    ) -> dict:
        """Process a single intake package: extract products, normalize, write to DB.

        Uses the universal product schema:
        - Persists raw source content to product_source_documents (done in routes.py).
        - Creates product_extraction_runs with status tracking.
        - Maps extraction results to products, variants, prices, options, facts,
          fulfilment terms, and media links.
        - Preserves unclassified content and raw price text.
        - Never discards source data on extraction failure.
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
            "source_documents_persisted": 0,
            "extraction_runs_created": 0,
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

            # 3. Create extraction run records for each source document
            extraction_runs: dict[str, str] = {}
            sd_map = source_document_map or await storage.get_source_document_map(package_internal_id)
            for item in package_items:
                item_id = str(item.get("id"))
                source_doc_id = sd_map.get(item_id)
                if not source_doc_id:
                    continue
                if settings.AI_EXTRACTION_ENABLED and item.get("file_type") == "markdown":
                    # Owned by the AI workflow (begin_ai_extraction/apply_ai_extraction).
                    # Creating a regex run here is what produced the misleading product_count=0 runs.
                    continue
                # Collect decoding errors and unclassified content from the item
                metadata = item.get("parsed_content", {}).get("metadata", {}) or {}
                decoding_errors = item.get("decoding_errors") or metadata.get("decoding_errors")
                unclassified = metadata.get("unclassified_content") or []

                run = await storage.insert_product_extraction_run({
                    "source_document_id": source_doc_id,
                    "batch_id": package_internal_id,
                    "status": "running",
                    "extractor_name": "regex",
                    "extractor_version": "1.0.0",
                    "warnings": decoding_errors if decoding_errors else [],
                    "unclassified_content": unclassified if unclassified else [],
                })
                extraction_runs[source_doc_id] = str(run["id"])
                results["source_documents_persisted"] += 1
                results["extraction_runs_created"] += 1

            # 4. Normalize and upsert products with internal_sku identity.
            # IMPORTANT: reuse the real batch_item_id -> source_document_id map. The previous code rebuilt it
            # from item["source_document_id"], a field that does not exist on batch_items, which emptied the
            # map: no links, no child rows, and every run reported product_count=0.
            source_document_map = sd_map

            upsert_results = await self._normalize_and_upsert_products(
                storage, product_candidates, package_internal_id, monthly_batch,
                package_name, extraction_runs, source_document_map
            )
            results["products_upserted"] = upsert_results["upserted"]
            results["products_failed"] = upsert_results["failed"]
            results["products_flagged_review"] = upsert_results["flagged_review"]
            results["price_history_entries"] = upsert_results["price_entries"]
            results["image_links_created"] = upsert_results["image_links"]

            # 5. Match product images to canonical products
            image_match_results = await self._match_package_images(
                storage, package_internal_id
            )
            results["image_links_created"] += image_match_results["matched"]
            results["images_auto_approved"] = image_match_results["auto_approved"]
            results["images_flagged_for_review"] = image_match_results["flagged_for_review"]
            results["images_unmatched"] = image_match_results["unmatched"]

            # 6. Mark extraction runs truthfully with the full extracted payload.
            #    Build the per-source-document extracted payload from the
            #    candidates extracted from each source document (audit #8).
            candidates_by_sd: dict[str, list[dict]] = {}
            for c in product_candidates:
                sd = source_document_map.get(str(c.source_batch_item_id)) if c.source_batch_item_id else None
                if sd:
                    candidates_by_sd.setdefault(sd, []).append({
                        "canonical_name": c.canonical_name,
                        "supplier_sku": c.supplier_sku,
                        "supplier_product_name": c.supplier_product_name,
                        "category": c.category,
                        "brand": c.brand,
                        "model": c.model,
                        "price_amount": c.price_amount,
                        "price_currency": c.price_currency,
                        "price_unit": c.price_unit,
                        "attributes": c.attributes,
                        "raw_source_text": c.raw_source_text,
                        "source_location": c.source_location,
                        "extraction_method": c.extraction_method,
                        "extraction_confidence": c.extraction_confidence,
                    })

            failed_source_docs = upsert_results.get("failed_source_doc_ids", set())
            failed = upsert_results["failed"]
            flagged = upsert_results["flagged_review"]

            for source_doc_id, run_id in extraction_runs.items():
                docs_candidates = candidates_by_sd.get(source_doc_id, [])
                payload = {
                    "products": docs_candidates,
                    "product_count": len(docs_candidates),
                    "source_document_id": source_doc_id,
                }
                # Truthful status per the audit contract (#13).
                if source_doc_id in failed_source_docs:
                    status = "failed"
                elif flagged:
                    status = "needs_review"
                elif len(docs_candidates) > 0:
                    status = "succeeded"
                else:
                    # Source document persisted but no product could be
                    # extracted from it (e.g. no recognizable product content).
                    # Keep the source + run for review rather than dropping it.
                    status = "partial"
                try:
                    await storage.update_product_extraction_run(UUID(run_id), {
                        "status": status,
                        "extracted_payload": payload,
                        "completed_at": datetime.utcnow().isoformat(),
                        "confidence": None,
                    })
                except Exception as e:
                    logger.warning("extraction_run_update_failed",
                                   run_id=run_id, error=str(e))

            # 7. Update package with product count
            await storage.update_package(package_id, {
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
            if settings.AI_EXTRACTION_ENABLED:
                return []  # AI workflow owns markdown; unclassified lines are never products
            return product_extractor.extract_from_markdown(parsed, file_name)
        elif file_type == "pdf":
            return product_extractor.extract_from_pdf(parsed, file_name)
        elif file_type == "spreadsheet":
            return product_extractor.extract_from_spreadsheet(parsed, file_name)
        elif file_type == "image":
            # OCR text stays on batch_items.ocr_text. Raw OCR lines are not product identities
            # (observed: 'is ee', 'eer pay eae').
            return []
        return []

    async def _normalize_and_upsert_products(
        self,
        storage,
        candidates: list[ProductCandidate],
        package_internal_id: str,
        monthly_batch: str,
        package_name: str,
        extraction_runs: dict[str, str],
        source_document_map: dict[str, str],
    ) -> dict:
        """Normalize candidates and upsert to database with internal_sku identity.

        Identity resolution order:
        1. Check product_source_document_links mapping by source_document_id
           → if found, use existing product_id and its persisted internal_sku.
        2. If no source mapping, compute internal_sku from stable attributes
           and look up by internal_sku → if found, update existing product.
        3. If no internal_sku match, check supplier_sku for ambiguity
           (multiple products sharing supplier_sku → flag for review).
        4. If no match at all, insert new product with computed internal_sku.

        After product is resolved, create product_source_document_links mapping AND
        universal product schema child records: variants, prices, options,
        facts, fulfilment terms, media links.

        Source-document mapping:
        The source_document_id (derived from the info.md batch item) is the
        durable link between a source document and its product. It is stable
        across reprocessing of the same source document, so the mapping is
        found on repeat runs and the existing product is updated.
        internal_sku (from stable attributes) is the durable product identity,
        not the extraction position.

        internal_sku is generated once on insert and NEVER changed on update.
        supplier_sku comes exclusively from source data, never computed.
        """
        results = {
            "upserted": 0,
            "failed": 0,
            "flagged_review": 0,
            "price_entries": 0,
            "image_links": 0,
            "variants_created": 0,
            "options_created": 0,
            "facts_created": 0,
            "fulfilment_terms_created": 0,
            "media_links_created": 0,
            "source_document_links_created": 0,
            # Track which source documents had failures so extraction-run
            # status can be set truthfully per source document (audit #13).
            "failed_source_doc_ids": set(),
        }

        for candidate in candidates:
            try:
                batch_item_id = str(candidate.source_batch_item_id) if candidate.source_batch_item_id else None
                candidate_key = candidate.source_location or f"unknown:{candidate.extraction_method}"
                source_doc_id = source_document_map.get(batch_item_id) if batch_item_id else None
                extraction_run_id = extraction_runs.get(source_doc_id) if source_doc_id else None

                if not batch_item_id:
                    logger.error("product_upsert_failed",
                                 error="No source_batch_item_id on candidate")
                    results["failed"] += 1
                    continue

                # Normalize
                normalized = product_normalizer.normalize(candidate)

                # Identity gate: an unclassified/price-only fragment is not a product.
                _name = (normalized.supplier_product_name or normalized.canonical_name or "").strip()
                if len(_name) < 3 or len(_name.split()) < 2 or (candidate.extraction_confidence or 0) < 0.4:
                    results["rejected_no_identity"] = results.get("rejected_no_identity", 0) + 1
                    logger.info("candidate_rejected_no_identity", name=_name, location=candidate.source_location)
                    continue

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
                # Prefer source_document_id (set in production by routes.py when
                # persisting source documents); fall back to batch_item_id when
                # source_document_id is not yet available (e.g. tests).
                lookup_key = source_doc_id or batch_item_id
                existing_mapping = await storage.get_product_by_source_document(
                    lookup_key
                ) if lookup_key else None

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
                        package_internal_id, candidate, source_document_map
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
                                package_internal_id, candidate, source_document_map
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
                                package_internal_id, candidate, source_document_map
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
                                source_doc_id=source_doc_id,
                                extraction_run_id=extraction_run_id,
                                source_document_map=source_document_map,
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
                                source_doc_id=source_doc_id,
                                extraction_run_id=extraction_run_id,
                                source_document_map=source_document_map,
                            )
                            if product:
                                results["upserted"] += 1

                # ------------------------------------------------------------------
                # Step 3: Record source-to-product mapping
                # ------------------------------------------------------------------
                # Link product to source document and extraction run
                if product and source_doc_id:
                    product_id = str(product["id"])
                    try:
                        await storage.insert_product_source_document_link({
                            "product_id": product_id,
                            "source_document_id": source_doc_id,
                            "extraction_run_id": extraction_run_id,
                            "relationship": "derived_from",
                        })
                        results["source_document_links_created"] += 1
                    except Exception as e:
                        logger.warning("source_document_link_creation_failed",
                                       product_id=product_id,
                                       source_document_id=source_doc_id,
                                       error=str(e))

                    # Create universal product schema child records
                    try:
                        variant_results = await self._create_product_variants(
                            storage, product_id, normalized, batch_item_id,
                            source_doc_id, candidate
                        )
                        results["variants_created"] += variant_results["variants_created"]
                        results["options_created"] += variant_results["options_created"]
                        results["media_links_created"] += variant_results["media_links_created"]
                    except Exception as e:
                        logger.warning("variant_creation_failed",
                                       product_id=product_id, error=str(e))

                    try:
                        price_results = await self._create_product_price_entries(
                            storage, product_id, normalized, batch_item_id,
                            source_doc_id, extraction_run_id, candidate
                        )
                        results["price_entries"] += price_results["price_entries"]
                    except Exception as e:
                        logger.warning("price_entry_creation_failed",
                                       product_id=product_id, error=str(e))

                    try:
                        fact_results = await self._create_product_facts(
                            storage, product_id, normalized, batch_item_id,
                            source_doc_id, candidate
                        )
                        results["facts_created"] += fact_results["facts_created"]
                    except Exception as e:
                        logger.warning("fact_creation_failed",
                                       product_id=product_id, error=str(e))

                    try:
                        fulfilment_results = await self._create_product_fulfilment_terms(
                            storage, product_id, normalized, batch_item_id,
                            source_doc_id, candidate
                        )
                        results["fulfilment_terms_created"] += fulfilment_results["fulfilment_terms_created"]
                    except Exception as e:
                        logger.warning("fulfilment_terms_creation_failed",
                                       product_id=product_id, error=str(e))

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
                            "batch_id": str(package_internal_id),
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
                # Record which source document failed so its extraction run
                # can be marked truthfully (audit #13).
                failed_sd = source_document_map.get(
                    str(candidate.source_batch_item_id)
                ) if candidate.source_batch_item_id else None
                if failed_sd:
                    results["failed_source_doc_ids"].add(failed_sd)
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
        source_doc_id: Optional[str] = None,
        extraction_run_id: Optional[str] = None,
        source_document_map: Optional[dict[str, str]] = None,
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
        source_document_map: Optional[dict[str, str]] = None,
    ) -> dict:
        """Build mutable-field update data (excludes internal_sku, id, created_at).

        Adds structured_data JSONB for flexible cross-industry attributes.
        """
        structured_data = self._build_structured_data(normalized, candidate)
        return {
            "supplier_sku": supplier_sku,
            "batch_id": str(package_internal_id),
            "monthly_batch": monthly_batch,
            "supplier_product_name": normalized.supplier_product_name,
            "canonical_name": self._fallback_canonical_name(normalized),
            "description": normalized.description,
            "category": normalized.category,
            "brand": normalized.brand,
            "model": normalized.model,
            "attributes": normalized.attributes,
            "structured_data": structured_data,
            "product_type": normalized.category,
            "source_title": self._fallback_canonical_name(normalized),
            "publication_status": "draft",
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
            "batch_id": str(package_internal_id),
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
        structured_data = self._build_structured_data(normalized, candidate)
        return {
            "internal_sku": internal_sku,
            "supplier_sku": supplier_sku,
            "batch_id": str(package_internal_id),
            "monthly_batch": monthly_batch,
            "supplier_product_name": normalized.supplier_product_name,
            "canonical_name": self._fallback_canonical_name(normalized),
            "description": normalized.description,
            "category": normalized.category,
            "brand": normalized.brand,
            "model": normalized.model,
            "attributes": normalized.attributes,
            "structured_data": structured_data,
            "product_type": normalized.category,
            "source_title": self._fallback_canonical_name(normalized),
            "publication_status": "draft",
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
            "extraction_confidence": candidate.extraction_confidence,
            "extraction_notes": normalized.validation_warnings + normalized.validation_errors,
            "extraction_method": candidate.extraction_method,
            "review_status": "auto_approved" if not normalized.validation_errors else "pending_review",
            "is_active": True,
        }



    def _fallback_canonical_name(self, normalized: NormalizedProduct) -> str:
        """Return a non-null canonical_name fallback chain.

        The live products table has canonical_name NOT NULL. Extraction may
        yield no name (price-only table rows, unclassified lines). Rather than
        fail the insert, fall back through supplier_product_name, supplier_sku,
        and finally a generic placeholder so draft products can still be saved.
        """
        if normalized.canonical_name:
            return normalized.canonical_name
        if normalized.supplier_product_name:
            return normalized.supplier_product_name
        if normalized.supplier_sku:
            return normalized.supplier_sku
        return "Untitled Product"

    def _build_structured_data(
        self,
        normalized: NormalizedProduct,
        candidate: ProductCandidate,
    ) -> dict[str, Any]:
        """Build flexible JSONB structured_data from normalized product data.

        Contains flexible cross-industry attributes not mapped to fixed SQL
        columns. All keys are preserved intact for open-ended product types.
        """
        data: dict[str, Any] = {}

        # Core identity fields
        if normalized.supplier_sku:
            data["supplier_sku"] = normalized.supplier_sku
        if normalized.supplier_product_name:
            data["supplier_product_name"] = normalized.supplier_product_name

        # Extraction provenance
        data["extraction_method"] = candidate.extraction_method
        data["extraction_confidence"] = candidate.extraction_confidence
        data["source_file"] = candidate.source_file
        data["source_location"] = candidate.source_location

        # Stable attributes as structured data (redundant with fixed columns
        # but preserved here for cross-industry querying)
        stable_attrs = {}
        for key in ["size", "color", "material", "dimensions", "weight"]:
            if key in normalized.attributes and normalized.attributes[key]:
                stable_attrs[key] = normalized.attributes[key]
        if stable_attrs:
            data["stable_attributes"] = stable_attrs

        # Shipping info
        shipping = {}
        if normalized.shipping_weight_kg:
            shipping["weight_kg"] = float(normalized.shipping_weight_kg)
        if normalized.shipping_dimensions_cm:
            shipping["dimensions_cm"] = normalized.shipping_dimensions_cm
        if normalized.package_quantity:
            shipping["package_quantity"] = normalized.package_quantity
        if normalized.master_carton_quantity:
            shipping["master_carton_quantity"] = normalized.master_carton_quantity
        if normalized.package_type:
            shipping["package_type"] = normalized.package_type
        if shipping:
            data["shipping"] = shipping

        # Delivery terms
        delivery = {}
        if normalized.delivery_terms:
            delivery["terms"] = normalized.delivery_terms
        if normalized.delivery_origin:
            delivery["origin"] = normalized.delivery_origin
        if normalized.delivery_destination:
            delivery["destination"] = normalized.delivery_destination
        if delivery:
            data["delivery"] = delivery

        # Availability
        availability = {}
        if normalized.availability_status:
            availability["status"] = normalized.availability_status
        if normalized.availability_quantity:
            availability["quantity"] = normalized.availability_quantity
        if normalized.availability_lead_time_days:
            availability["lead_time_days"] = normalized.availability_lead_time_days
        if availability:
            data["availability"] = availability

        # Price info (structured)
        price_info = {}
        if normalized.price_amount:
            price_info["amount"] = float(normalized.price_amount)
        if normalized.price_currency:
            price_info["currency"] = normalized.price_currency
        if normalized.price_unit:
            price_info["unit"] = normalized.price_unit
        if normalized.price_min_order_qty:
            price_info["min_order_qty"] = normalized.price_min_order_qty
        if price_info:
            data["price"] = price_info

        # Extraction validation info
        if normalized.validation_errors:
            data["validation_errors"] = normalized.validation_errors
        if normalized.validation_warnings:
            data["validation_warnings"] = normalized.validation_warnings

        # Raw extraction notes
        extraction_notes = normalized.validation_warnings + normalized.validation_errors
        if extraction_notes:
            data["extraction_notes"] = extraction_notes

        return data

    async def _create_product_variants(
        self,
        storage,
        product_id: str,
        normalized: NormalizedProduct,
        batch_item_id: str,
        source_doc_id: Optional[str],
        candidate: ProductCandidate,
    ) -> dict:
        """Create product variant records from normalized attributes.

        Variants represent configurations, sizes, colours, models, dimensions.
        Returns dict with counts of variants, options, media links created.
        """
        results = {"variants_created": 0, "options_created": 0, "media_links_created": 0}

        # Build variant from attributes
        attributes = dict(normalized.attributes) if normalized.attributes else {}
        variant_data = {
            "product_id": product_id,
            "supplier_variant_code": normalized.supplier_sku,
            "variant_name": normalized.supplier_product_name or normalized.canonical_name,
            "configuration": candidate.source_location,
            "attributes": attributes,
            "source_label": candidate.source_file,
            "is_available": normalized.availability_status != "out_of_stock" if normalized.availability_status else True,
            "sort_order": 0,
        }
        try:
            await storage.insert_product_variant(variant_data)
            results["variants_created"] += 1
        except Exception as e:
            logger.warning("variant_insert_failed", product_id=product_id, error=str(e))

        return results

    async def _create_product_price_entries(
        self,
        storage,
        product_id: str,
        normalized: NormalizedProduct,
        batch_item_id: str,
        source_doc_id: Optional[str],
        extraction_run_id: Optional[str],
        candidate: ProductCandidate,
    ) -> dict:
        """Create flexible price entry records from normalized product data.

        Amount/currency nullable; raw_price_text always retained.
        Returns dict with count of price entries created.
        """
        results = {"price_entries": 0}

        if not normalized.price_amount:
            return results

        price_data = {
            "product_id": product_id,
            "price_kind": normalized.price_unit or "unit",
            "price_label": normalized.price_unit or "unit",
            "price_amount": float(normalized.price_amount),
            "currency_code": normalized.price_currency,
            "raw_price_text": candidate.raw_source_text or "",
            "pricing_unit": normalized.price_unit,
            "source_document_id": source_doc_id,
            "source_excerpt": candidate.raw_source_text,
            "confidence": normalized.extraction_confidence,
            "needs_review": bool(normalized.validation_errors),
        }
        try:
            await storage.insert_product_price_entry(price_data)
            results["price_entries"] += 1
        except Exception as e:
            logger.warning("price_entry_insert_failed", product_id=product_id, error=str(e))

        return results

    async def _create_product_options(
        self,
        storage,
        product_id: str,
        normalized: NormalizedProduct,
        batch_item_id: str,
        source_doc_id: Optional[str],
        candidate: ProductCandidate,
    ) -> dict:
        """Create optional/add-on item records from normalized data."""
        results = {"options_created": 0}

        # Options from attributes
        for key, value in (normalized.attributes or {}).items():
            if key in ("size", "color", "material", "dimensions", "weight"):
                continue  # already in stable_attributes/structured_data
            option_data = {
                "product_id": product_id,
                "option_name": key,
                "option_group": "attributes",
                "description": str(value),
                "option_type": "attribute",
                "attributes": {key: value},
                "source_document_id": source_doc_id,
                "source_excerpt": candidate.raw_source_text,
                "sort_order": 0,
            }
            try:
                await storage.insert_product_option(option_data)
                results["options_created"] += 1
            except Exception as e:
                logger.warning("option_insert_failed", product_id=product_id, error=str(e))

        return results

    async def _create_product_facts(
        self,
        storage,
        product_id: str,
        normalized: NormalizedProduct,
        batch_item_id: str,
        source_doc_id: Optional[str],
        candidate: ProductCandidate,
    ) -> dict:
        """Create fact records from normalized product data."""
        results = {"facts_created": 0}

        # Facts from availability
        if normalized.availability_status:
            fact_data = {
                "product_id": product_id,
                "fact_group": "availability",
                "fact_key": "status",
                "fact_value": normalized.availability_status,
                "value_text": normalized.availability_status,
                "raw_text": normalized.availability_status,
                "source_document_id": source_doc_id,
                "source_excerpt": candidate.raw_source_text,
                "ordinal": 0,
            }
            try:
                await storage.insert_product_fact(fact_data)
                results["facts_created"] += 1
            except Exception as e:
                logger.warning("fact_insert_failed", product_id=product_id, error=str(e))

        # Facts from brand/model/category
        fact_fields = [
            ("brand", "brand"),
            ("model", "model"),
            ("category", "category"),
        ]
        for ordinal, (fact_key, attr_name) in enumerate(fact_fields, 1):
            value = getattr(normalized, attr_name, None)
            if value:
                fact_data = {
                    "product_id": product_id,
                    "fact_group": "identity",
                    "fact_key": fact_key,
                    "fact_value": value,
                    "value_text": value,
                    "raw_text": value,
                    "source_document_id": source_doc_id,
                    "source_excerpt": candidate.raw_source_text,
                    "ordinal": ordinal,
                }
                try:
                    await storage.insert_product_fact(fact_data)
                    results["facts_created"] += 1
                except Exception as e:
                    logger.warning("fact_insert_failed", product_id=product_id, error=str(e))

        return results

    async def _create_product_fulfilment_terms(
        self,
        storage,
        product_id: str,
        normalized: NormalizedProduct,
        batch_item_id: str,
        source_doc_id: Optional[str],
        candidate: ProductCandidate,
    ) -> dict:
        """Create fulfilment terms records from normalized delivery data."""
        results = {"fulfilment_terms_created": 0}

        terms = {}
        if normalized.delivery_terms:
            terms["terms"] = normalized.delivery_terms
        if normalized.delivery_origin:
            terms["origin"] = normalized.delivery_origin
        if normalized.delivery_destination:
            terms["destination"] = normalized.delivery_destination

        if not terms:
            return results

        fulfilment_data = {
            "product_id": product_id,
            "term_type": "delivery",
            "areas": [],
            "schedules": {},
            "charges": [],
            "payment_methods": [],
            "conditions": {},
            "raw_terms": candidate.raw_source_text,
            "source_document_id": source_doc_id,
        }
        fulfilment_data.update(terms)

        try:
            await storage.insert_product_fulfilment_terms(fulfilment_data)
            results["fulfilment_terms_created"] += 1
        except Exception as e:
            logger.warning("fulfilment_terms_insert_failed", product_id=product_id, error=str(e))

        return results

    async def _create_product_media_links(
        self,
        storage,
        product_id: str,
        normalized: NormalizedProduct,
        batch_item_id: str,
        source_doc_id: Optional[str],
        candidate: ProductCandidate,
    ) -> dict:
        """Create product-media link records from normalized data."""
        results = {"media_links_created": 0}

        # Media links would typically come from image inventory, not product data.
        # This is a placeholder for future integration with product_images table.
        return results

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
