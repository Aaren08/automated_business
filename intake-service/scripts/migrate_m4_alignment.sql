-- M4 Remediation: Internal SKU, Source-Product Mapping, and Migration Safety
-- NON-DESTRUCTIVE: preserves all historical data. Does NOT delete, merge, or rewrite existing product records.
--
-- This migration:
--   1. Adds package-support columns to batches (monthly_batch, package_name)
--   2. Adds internal_sku column to products (nullable, unique index allows multiple NULLs)
--   3. Creates product_source_items mapping table (durable source-to-product identity)
--   4. Drops old supplier_sku uniqueness constraint (supplier_sku is source-provided, not unique)
--   5. Drops content_hash unique index (was causing unstable product identity)
--   6. Adds price_history idempotency safeguards via unique partial index
--
-- Prerequisites (run these read-only diagnostics BEFORE applying):
--   scripts/diagnose_product_identity.sql
--
-- Transaction safety:
--   All statements below are wrapped in an explicit BEGIN / COMMIT block so
--   the migration is atomic when executed through psql, Supabase SQL Editor,
--   or any PostgreSQL client that honors transaction control.
--   Statements that cannot run inside a transaction (none identified in this
--   file) must be extracted and executed separately with their own safeguards.
--
-- Do NOT claim automatic atomicity merely because statements appear in one file.
-- This transaction wrapper has NOT been tested against a live PostgreSQL instance.
-- Verify against a restored staging copy before production use.

BEGIN;

-- ========================================================================
-- 1. Package-support columns on batches table
-- ========================================================================
ALTER TABLE batches
ADD COLUMN IF NOT EXISTS monthly_batch TEXT,
ADD COLUMN IF NOT EXISTS package_name TEXT;

CREATE INDEX IF NOT EXISTS idx_batches_monthly_batch ON batches(monthly_batch);
CREATE INDEX IF NOT EXISTS idx_batches_package_name ON batches(package_name);

-- ========================================================================
-- 2. internal_sku column on products (canonical identity, nullable initially)
-- ========================================================================
-- supplier_id is already nullable in the original M4 schema; no change needed.
ALTER TABLE products ADD COLUMN IF NOT EXISTS internal_sku TEXT;

-- Unique index on internal_sku. PostgreSQL allows multiple NULLs, so existing
-- rows without internal_sku are not affected by this constraint.
CREATE UNIQUE INDEX IF NOT EXISTS idx_products_internal_sku ON products(internal_sku);

-- ========================================================================
-- 3. product_source_items mapping table (durable source-to-product identity)
-- ========================================================================
-- Supports many-to-many: one batch_item can map to multiple products,
-- one product can be mapped from multiple batch_items.
-- UNIQUE (batch_item_id, candidate_key) ensures a source-item-candidate
-- maps to at most ONE canonical product.
CREATE TABLE IF NOT EXISTS product_source_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    batch_item_id UUID NOT NULL REFERENCES batch_items(id) ON DELETE CASCADE,
    candidate_key TEXT NOT NULL,            -- e.g., "table:0,row:2" or "line:5"
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),

    UNIQUE (batch_item_id, candidate_key)
);

CREATE INDEX IF NOT EXISTS idx_product_source_items_product ON product_source_items(product_id);
CREATE INDEX IF NOT EXISTS idx_product_source_items_batch_item ON product_source_items(batch_item_id);
CREATE INDEX IF NOT EXISTS idx_product_source_items_candidate_key ON product_source_items(candidate_key);

-- Updated at trigger for product_source_items
DROP TRIGGER IF EXISTS product_source_items_updated_at ON product_source_items;
CREATE TRIGGER product_source_items_updated_at
    BEFORE UPDATE ON product_source_items
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ========================================================================
-- 4. Drop old supplier uniqueness constraints
-- ========================================================================
-- Original M4 schema: UNIQUE (supplier_id, supplier_sku) ->
--   auto-generated name: products_supplier_id_supplier_sku_key
-- Prior alignment migration may have created: products_supplier_sku_key
-- Drop both if present (IF EXISTS makes this safe).
ALTER TABLE products DROP CONSTRAINT IF EXISTS products_supplier_id_supplier_sku_key;
ALTER TABLE products DROP CONSTRAINT IF EXISTS products_supplier_sku_key;

-- Drop old non-unique index on (supplier_id, supplier_sku) from M4 schema
DROP INDEX IF EXISTS idx_products_supplier_sku;

-- ========================================================================
-- 5. Drop content_hash unique index (unstable identity)
-- ========================================================================
DROP INDEX IF EXISTS idx_products_content_hash;

-- ========================================================================
-- 6. Price-history idempotency safeguard
-- ========================================================================
-- Unique partial index prevents duplicate price observations for the same
-- (product, package, source_batch_item, effective_date, price_amount).
-- Rows with NULL source_batch_item_id are excluded from this constraint.
CREATE UNIQUE INDEX IF NOT EXISTS idx_price_history_idempotent
ON price_history (product_id, package_id, source_batch_item_id, effective_date, price_amount)
WHERE source_batch_item_id IS NOT NULL;

-- ========================================================================
-- 7. Indexes for stable lookups
-- ========================================================================
CREATE INDEX IF NOT EXISTS idx_products_supplier_sku_lookup ON products(supplier_sku);
CREATE INDEX IF NOT EXISTS idx_products_canonical_name ON products(canonical_name);
CREATE INDEX IF NOT EXISTS idx_products_monthly_batch ON products(monthly_batch);

-- content_hash column remains for change detection (no unique index).
-- Suppliers table preserved for historical data (no changes).

-- ========================================================================
-- 8. Updated at trigger for batches (package support)
-- ========================================================================
DROP TRIGGER IF EXISTS batches_updated_at ON batches;
CREATE TRIGGER batches_updated_at
    BEFORE UPDATE ON batches
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

COMMIT;


-- ========================================================================
-- ROLLBACK / RECOVERY PLAN
-- ========================================================================
-- If the migration must be rolled back, run the following in reverse order:
--
--   DROP TRIGGER IF EXISTS batches_updated_at ON batches;
--   DROP INDEX IF EXISTS idx_batches_monthly_batch;
--   DROP INDEX IF EXISTS idx_batches_package_name;
--   DROP INDEX IF EXISTS idx_products_monthly_batch;
--   DROP INDEX IF EXISTS idx_products_canonical_name;
--   DROP INDEX IF EXISTS idx_products_supplier_sku_lookup;
--   DROP INDEX IF EXISTS idx_price_history_idempotent;
--   DROP INDEX IF EXISTS idx_product_source_items_candidate_key;
--   DROP INDEX IF EXISTS idx_product_source_items_batch_item;
--   DROP INDEX IF EXISTS idx_product_source_items_product;
--   DROP TRIGGER IF EXISTS product_source_items_updated_at ON product_source_items;
--   DROP TABLE IF EXISTS product_source_items;
--   DROP INDEX IF EXISTS idx_products_internal_sku;
--   ALTER TABLE products DROP COLUMN IF EXISTS internal_sku;
--   ALTER TABLE batches DROP COLUMN IF EXISTS monthly_batch;
--   ALTER TABLE batches DROP COLUMN IF EXISTS package_name;
--   DROP TRIGGER IF EXISTS batches_updated_at ON batches;
--
-- Constraint restoration (conditional — review preflight results first):
--   The following constraints were dropped by the migration and are NOT
--   automatically recreated by rollback, because restoring them could fail
--   if existing data violates uniqueness:
--
--   1. products_supplier_id_supplier_sku_key — UNIQUE (supplier_id, supplier_sku)
--      Preflight (must return 0 rows before restoring):
--        SELECT supplier_id, supplier_sku, COUNT(*)
--        FROM products
--        WHERE supplier_id IS NOT NULL AND supplier_sku IS NOT NULL
--        GROUP BY supplier_id, supplier_sku
--        HAVING COUNT(*) > 1;
--      If safe:
--        ALTER TABLE products ADD CONSTRAINT products_supplier_id_supplier_sku_key
--          UNIQUE (supplier_id, supplier_sku);
--
--   2. products_supplier_sku_key — UNIQUE (supplier_sku)
--      Preflight (must return 0 rows before restoring):
--        SELECT supplier_sku, COUNT(*)
--        FROM products
--        WHERE supplier_sku IS NOT NULL
--        GROUP BY supplier_sku
--        HAVING COUNT(*) > 1;
--      If safe:
--        ALTER TABLE products ADD CONSTRAINT products_supplier_sku_key
--          UNIQUE (supplier_sku);
--
-- Data loss warning (rollback):
--   Dropping products.internal_sku (via ALTER TABLE ... DROP COLUMN) removes
--   all backfilled internal_sku values. These cannot be recovered from rollback
--   alone. Before rollback, export backfilled values if needed:
--     COPY (SELECT id, internal_sku FROM products WHERE internal_sku IS NOT NULL)
--     TO '/path/to/internal_sku_backup.csv' WITH (FORMAT csv, HEADER true);
--
-- Transaction safety:
--   The migration statements are wrapped in BEGIN / COMMIT (above). The
--   rollback statements are NOT wrapped — execute each rollback statement
--   individually and verify before proceeding to the next. A failed rollback
--   statement leaves the schema in a partially rolled-back state.
--
-- This transaction wrapper has NOT been tested against a live PostgreSQL
-- instance. Verify against a restored staging copy before production use.


-- ========================================================================
-- POST-MIGRATION BACKFILL (separate step, see scripts/backfill_internal_sku.sql)
-- ========================================================================
-- After applying the migration, existing product rows will have NULL internal_sku.
-- A separate backfill process populates internal_sku using the same deterministic
-- formula as _compute_internal_sku in src/core/extractor/pipeline.py.
--
-- Do NOT backfill as part of this migration to avoid long-running transactions
-- on large product tables. Backfill is retry-safe: WHERE internal_sku IS NULL
-- ensures already-processed rows are skipped.
