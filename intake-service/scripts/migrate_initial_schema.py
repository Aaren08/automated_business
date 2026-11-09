"""Initial database schema for Product Intelligence."""
# This migration creates the core tables for batch processing.
# Run with: supabase db push (if using Supabase CLI) or execute SQL directly.

INITIAL_SCHEMA = """
-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Suppliers table
CREATE TABLE IF NOT EXISTS suppliers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    contact_name TEXT,
    email TEXT,
    phone TEXT,
    address TEXT,
    website TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Batches table (one per supplier inbox submission)
CREATE TABLE IF NOT EXISTS batches (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id TEXT UNIQUE NOT NULL,  -- deterministic hash
    supplier_id UUID REFERENCES suppliers(id),
    source_path TEXT NOT NULL,      -- e.g., "Batch-2026-11"
    status TEXT NOT NULL DEFAULT 'discovered', -- discovered, processing, completed, failed, awaiting_review
    supplier_md_hash TEXT,          -- SHA256 of Supplier.md for change detection
    total_files INTEGER DEFAULT 0,
    total_images INTEGER DEFAULT 0,
    total_products INTEGER DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Batch Items (each file in batch)
CREATE TABLE IF NOT EXISTS batch_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id UUID REFERENCES batches(id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,        -- Relative to batch root
    file_name TEXT NOT NULL,
    file_size BIGINT NOT NULL,
    mime_type TEXT NOT NULL,
    file_hash TEXT NOT NULL,        -- SHA256
    file_type TEXT NOT NULL,        -- markdown, pdf, image, spreadsheet
    parsed_content JSONB,           -- Extracted text/tables
    ocr_text TEXT,                  -- OCR result if applicable
    processing_status TEXT DEFAULT 'pending',
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Processing Jobs (audit trail)
CREATE TABLE IF NOT EXISTS processing_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    batch_id UUID REFERENCES batches(id),
    job_type TEXT NOT NULL,         -- scan, process, retry, review
    status TEXT NOT NULL,           -- pending, running, completed, failed
    input_params JSONB,
    result_summary JSONB,
    error_message TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_batches_status ON batches(status);
CREATE INDEX IF NOT EXISTS idx_batches_batch_id ON batches(batch_id);
CREATE INDEX IF NOT EXISTS idx_batch_items_batch_id ON batch_items(batch_id);
CREATE INDEX IF NOT EXISTS idx_batch_items_file_hash ON batch_items(file_hash);
CREATE INDEX IF NOT EXISTS idx_processing_jobs_batch_id ON processing_jobs(batch_id);
CREATE INDEX IF NOT EXISTS idx_processing_jobs_status ON processing_jobs(status);

-- Updated at trigger function
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Triggers for updated_at
DROP TRIGGER IF UPDATE suppliers_updated_at ON suppliers;
CREATE TRIGGER suppliers_updated_at
    BEFORE UPDATE ON suppliers
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF UPDATE batches_updated_at ON batches;
CREATE TRIGGER batches_updated_at
    BEFORE UPDATE ON batches
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
"""


if __name__ == "__main__":
    print(INITIAL_SCHEMA)