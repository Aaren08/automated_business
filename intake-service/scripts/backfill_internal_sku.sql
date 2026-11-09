-- M4 Remediation: Backfill internal_sku for existing products
-- RETRY-SAFE: WHERE internal_sku IS NULL ensures already-processed rows are skipped.
-- NON-DESTRUCTIVE: does NOT delete, merge, or rewrite historical product records.
-- Does NOT automatically merge records with similar names — each product gets
-- its own internal_sku based on its stored attributes.
--
-- Prerequisites: Run migrate_m4_alignment.sql first (adds internal_sku column
-- and unique index). Then run this backfill as a separate step.
--
-- Requires pgcrypto extension for SHA-256. Enable if not present.

-- Enable pgcrypto if not already available (needed for digest/sha256)
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- -----------------------------------------------------------------------
-- Function: compute_internal_sku_sql
-- Replicates _compute_internal_sku from src/core/extractor/pipeline.py
-- in PL/pgSQL using PostgreSQL digest().
-- -----------------------------------------------------------------------
CREATE OR REPLACE FUNCTION compute_internal_sku_sql(
    p_canonical_name TEXT,
    p_model TEXT,
    p_brand TEXT,
    p_category TEXT,
    p_attributes JSONB,
    p_salt TEXT DEFAULT NULL
) RETURNS TEXT AS $$
DECLARE
    parts TEXT[];
    content TEXT;
    stable_key TEXT;
    stable_val TEXT;
    hash_hex TEXT;
BEGIN
    -- Build parts array (same order as Python)
    IF p_brand IS NOT NULL THEN
        parts := array_append(parts, upper(trim(p_brand)));
    END IF;
    IF p_canonical_name IS NOT NULL THEN
        parts := array_append(parts, upper(trim(p_canonical_name)));
    END IF;
    IF p_model IS NOT NULL THEN
        parts := array_append(parts, upper(trim(p_model)));
    END IF;
    IF p_category IS NOT NULL THEN
        parts := array_append(parts, upper(trim(p_category)));
    END IF;

    -- Stable attributes: size, color, material, dimensions, weight
    FOR stable_key IN SELECT * FROM unnest(ARRAY['size','color','material','dimensions','weight'])
    LOOP
        stable_val := p_attributes->>stable_key;
        IF stable_val IS NOT NULL AND stable_val <> '' THEN
            parts := array_append(parts, stable_key || ':' || stable_val);
        END IF;
    END LOOP;

    content := array_to_string(parts, '|');
    IF content = '' THEN
        content := 'UNKNOWN_PRODUCT';
    END IF;
    IF p_salt IS NOT NULL THEN
        content := content || '|' || p_salt;
    END IF;

    hash_hex := encode(digest(content, 'sha256'), 'hex');
    RETURN 'INT-' || upper(substring(hash_hex from 1 for 12));
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- -----------------------------------------------------------------------
-- Backfill: update all products with NULL internal_sku
-- -----------------------------------------------------------------------
DO $$
DECLARE
    rec RECORD;
    attempt INTEGER := 0;
    max_attempts INTEGER := 5;
    salt TEXT;
    computed_sku TEXT;
    conflict_count INTEGER;
BEGIN
    FOR rec IN
        SELECT id, canonical_name, model, brand, category, attributes
        FROM products
        WHERE internal_sku IS NULL
        ORDER BY created_at
    LOOP
        attempt := 0;
        salt := NULL;
        computed_sku := compute_internal_sku_sql(
            rec.canonical_name, rec.model, rec.brand, rec.category,
            rec.attributes, salt
        );

        -- Try up to max_attempts times, handling collisions
        WHILE attempt < max_attempts LOOP
            attempt := attempt + 1;

            -- Check if this SKU is already taken (by a previously backfilled row
            -- or by a row that already had internal_sku)
            SELECT COUNT(*) INTO conflict_count
            FROM products
            WHERE internal_sku = computed_sku;

            IF conflict_count = 0 THEN
                -- Safe to assign
                UPDATE products
                SET internal_sku = computed_sku
                WHERE id = rec.id;
                EXIT;
            ELSE
                -- Collision: retry with a disambiguating salt
                salt := 'dup' || attempt;
                computed_sku := compute_internal_sku_sql(
                    rec.canonical_name, rec.model, rec.brand, rec.category,
                    rec.attributes, salt
                );
            END IF;
        END LOOP;

        IF attempt >= max_attempts THEN
            -- Could not find a unique SKU after max_attempts; leave NULL
            -- and flag for manual review
            UPDATE products
            SET extraction_notes = COALESCE(extraction_notes, '') ||
                ' internal_sku_backfill_failed: could not generate unique SKU'
            WHERE id = rec.id;
        END IF;
    END LOOP;
END $$;

-- -----------------------------------------------------------------------
-- Verification queries (read-only)
-- -----------------------------------------------------------------------
SELECT COUNT(*) AS products_still_null_internal_sku
FROM products
WHERE internal_sku IS NULL;

SELECT COUNT(*) AS products_with_internal_sku
FROM products
WHERE internal_sku IS NOT NULL;

-- Check for any remaining collisions (should be 0)
SELECT internal_sku, COUNT(*) AS cnt
FROM products
WHERE internal_sku IS NOT NULL
GROUP BY internal_sku
HAVING COUNT(*) > 1;

-- -----------------------------------------------------------------------
-- Cleanup: drop the helper function (optional, keeps schema clean)
-- -----------------------------------------------------------------------
-- DROP FUNCTION IF EXISTS compute_internal_sku_sql(TEXT, TEXT, TEXT, TEXT, JSONB, TEXT);
