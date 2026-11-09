-- M4 Remediation: Read-Only Diagnostic SQL for Product Identity
-- Run these queries BEFORE applying the migration to staging/production.
-- ALL QUERIES ARE READ-ONLY. No data is modified.

-- ========================================================================
-- 1. Product count and basic inventory
-- ========================================================================
SELECT COUNT(*) AS total_products FROM products;
SELECT COUNT(*) AS products_with_internal_sku FROM products WHERE internal_sku IS NOT NULL;
SELECT COUNT(*) AS products_without_internal_sku FROM products WHERE internal_sku IS NULL;
SELECT COUNT(*) AS products_with_supplier_sku FROM products WHERE supplier_sku IS NOT NULL;
SELECT COUNT(*) AS products_without_supplier_sku FROM products WHERE supplier_sku IS NULL;
SELECT COUNT(*) AS products_with_content_hash FROM products WHERE content_hash IS NOT NULL;

-- ========================================================================
-- 2. Duplicate supplier_sku values (potential conflicts)
--    These would be ambiguous under a supplier_sku uniqueness constraint.
-- ========================================================================
SELECT supplier_sku,
       COUNT(*) AS product_count,
       ARRAY_AGG(id) AS product_ids,
       ARRAY_AGG(canonical_name) AS canonical_names,
       ARRAY_AGG(brand) AS brands,
       ARRAY_AGG(model) AS models
FROM products
WHERE supplier_sku IS NOT NULL
GROUP BY supplier_sku
HAVING COUNT(*) > 1
ORDER BY product_count DESC, supplier_sku;

-- ========================================================================
-- 3. Duplicate internal_sku values (if column already exists)
-- ========================================================================
SELECT internal_sku,
       COUNT(*) AS product_count,
       ARRAY_AGG(id) AS product_ids,
       ARRAY_AGG(canonical_name) AS canonical_names
FROM products
WHERE internal_sku IS NOT NULL
GROUP BY internal_sku
HAVING COUNT(*) > 1
ORDER BY product_count DESC, internal_sku;

-- ========================================================================
-- 4. Null supplier_sku products (can be stored safely under internal_sku key)
-- ========================================================================
SELECT id, canonical_name, brand, model, category, internal_sku, supplier_sku
FROM products
WHERE supplier_sku IS NULL
ORDER BY created_at DESC
LIMIT 50;

-- ========================================================================
-- 5. Products with same canonical_name but different internal attributes
--    (potentially distinct products that should NOT be merged)
-- ========================================================================
SELECT canonical_name,
       COUNT(*) AS product_count,
       ARRAY_AGG(DISTINCT brand) AS brands,
       ARRAY_AGG(DISTINCT model) AS models,
       ARRAY_AGG(DISTINCT internal_sku) AS internal_skus
FROM products
GROUP BY canonical_name
HAVING COUNT(*) > 1
ORDER BY product_count DESC, canonical_name;

-- ========================================================================
-- 6. Price history volume per product (identify products with many price changes)
-- ========================================================================
SELECT p.id,
       p.canonical_name,
       p.internal_sku,
       p.supplier_sku,
       COUNT(ph.id) AS price_history_entries,
       MIN(ph.effective_date) AS first_price_date,
       MAX(ph.effective_date) AS last_price_date
FROM products p
LEFT JOIN price_history ph ON ph.product_id = p.id
GROUP BY p.id, p.canonical_name, p.internal_sku, p.supplier_sku
ORDER BY price_history_entries DESC
LIMIT 50;

-- ========================================================================
-- 7. Dependent records for products that would be affected by migration
--    (price_history, product_product_images, batch_items references)
-- ========================================================================
SELECT
    p.id AS product_id,
    p.canonical_name,
    p.internal_sku,
    p.supplier_sku,
    (SELECT COUNT(*) FROM price_history ph WHERE ph.product_id = p.id) AS price_history_count,
    (SELECT COUNT(*) FROM product_product_images ppi WHERE ppi.product_id = p.id) AS image_link_count,
    (SELECT COUNT(*) FROM batch_items bi WHERE bi.id = p.source_batch_item_id) AS source_item_exists
FROM products p
ORDER BY p.created_at DESC
LIMIT 100;

-- ========================================================================
-- 8. Supplier table status (historical data preserved)
-- ========================================================================
SELECT COUNT(*) AS total_suppliers FROM suppliers;
SELECT name, id FROM suppliers ORDER BY name LIMIT 20;

-- ========================================================================
-- 9. Check for content_hash duplicates (informational only)
-- ========================================================================
SELECT content_hash,
       COUNT(*) AS product_count,
       ARRAY_AGG(id) AS product_ids
FROM products
WHERE content_hash IS NOT NULL
GROUP BY content_hash
HAVING COUNT(*) > 1
ORDER BY product_count DESC;

-- ========================================================================
-- 10. Products with NULL internal_sku after migration check
--     (these will need backfill in a separate step)
-- ========================================================================
SELECT COUNT(*) AS products_needing_internal_sku_backfill
FROM products
WHERE internal_sku IS NULL;
