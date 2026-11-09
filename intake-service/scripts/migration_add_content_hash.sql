-- Migration: Add content_hash columns for deduplication
-- Run this in Supabase SQL Editor

-- 1. Add content_hash to suppliers table
ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS content_hash TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_suppliers_content_hash ON suppliers(content_hash);

-- 2. Add content_hash to products table
ALTER TABLE products ADD COLUMN IF NOT EXISTS content_hash TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_products_content_hash ON products(content_hash);

-- 3. Add supplier_sku generation helper (optional - for future use)
-- ALTER TABLE products ADD COLUMN IF NOT EXISTS generated_sku BOOLEAN DEFAULT FALSE;