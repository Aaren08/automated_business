-- M3: Image Inventory Schema Migration
-- Run this in Supabase SQL Editor

-- 1. Add image-specific columns to batch_items
ALTER TABLE batch_items 
ADD COLUMN IF NOT EXISTS perceptual_hash TEXT,
ADD COLUMN IF NOT EXISTS thumbnail_path TEXT,
ADD COLUMN IF NOT EXISTS width INTEGER,
ADD COLUMN IF NOT EXISTS height INTEGER;

-- 2. Create product_images table for canonical image assets (cross-batch deduplication)
CREATE TABLE IF NOT EXISTS product_images (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_hash TEXT NOT NULL,                    -- SHA-256 of original file (exact duplicate detection)
    perceptual_hash TEXT,                       -- dhash/phash for visual similarity
    original_filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    width INTEGER,
    height INTEGER,
    file_size BIGINT,
    
    -- Storage paths (Supabase Storage)
    original_path TEXT NOT NULL,                -- Path in Supabase Storage bucket
    thumbnail_path TEXT,                        -- Path in Supabase Storage bucket
    
    -- Provenance
    first_seen_batch_id UUID REFERENCES batches(id),  -- First batch where this image appeared
    first_seen_batch_item_id UUID REFERENCES batch_items(id),  -- First batch_item
    
    -- Deduplication tracking
    reference_count INTEGER DEFAULT 1,          -- How many batch_items reference this asset
    
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Indexes for deduplication lookups
CREATE INDEX IF NOT EXISTS idx_product_images_file_hash ON product_images(file_hash);
CREATE INDEX IF NOT EXISTS idx_product_images_perceptual_hash ON product_images(perceptual_hash);
CREATE INDEX IF NOT EXISTS idx_product_images_first_seen_batch ON product_images(first_seen_batch_id);

-- 3. Create batch_contact_sheets table for contact sheet metadata
CREATE TABLE IF NOT EXISTS batch_contact_sheets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id UUID REFERENCES batches(id) ON DELETE CASCADE,
    storage_path TEXT NOT NULL,                 -- Path in Supabase Storage
    image_count INTEGER NOT NULL,
    grid_cols INTEGER,
    grid_rows INTEGER,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_batch_contact_sheets_batch_id ON batch_contact_sheets(batch_id);

-- 4. Updated at trigger for product_images
DROP TRIGGER IF EXISTS product_images_updated_at ON product_images;
CREATE TRIGGER product_images_updated_at
    BEFORE UPDATE ON product_images
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();