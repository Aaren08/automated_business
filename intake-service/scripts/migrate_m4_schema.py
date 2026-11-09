# M4: Products & Price History Schema Migration
# Run this in Supabase SQL Editor

M4_SCHEMA = """
-- 1. Products table (canonical product catalog)
CREATE TABLE IF NOT EXISTS products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_id UUID REFERENCES suppliers(id),
    batch_id UUID REFERENCES batches(id),
    
    -- Supplier-specific identifiers
    supplier_sku TEXT,                    -- Supplier's product code/SKU
    supplier_product_name TEXT,           -- Original name from supplier
    
    -- Canonical product info
    canonical_name TEXT NOT NULL,         -- Normalized product name
    description TEXT,
    category TEXT,
    brand TEXT,
    model TEXT,
    
    -- Attributes (JSON for flexibility)
    attributes JSONB DEFAULT '{}',        -- size, color, material, etc.
    
    -- Pricing (can have multiple prices over time)
    price_amount NUMERIC(12, 4),
    price_currency CHAR(3),               -- ISO 4217 (USD, CAD, etc.)
    price_unit TEXT,                      -- per unit, per kg, per box, etc.
    price_min_order_qty INTEGER,
    price_valid_from DATE,
    price_valid_to DATE,
    
    -- Availability
    availability_status TEXT,             -- in_stock, out_of_stock, made_to_order, unknown
    availability_quantity INTEGER,
    availability_lead_time_days INTEGER,
    
    -- Shipping/Delivery
    delivery_terms TEXT,                  -- FOB, CIF, EXW, etc.
    delivery_origin TEXT,
    delivery_destination TEXT,
    shipping_weight_kg NUMERIC(10, 3),
    shipping_dimensions_cm JSONB,         -- {l, w, h}
    
    -- Packaging
    package_quantity INTEGER,             -- units per inner pack
    master_carton_quantity INTEGER,       -- units per master carton
    package_type TEXT,                    -- box, bag, drum, etc.
    
    -- Provenance
    source_batch_item_id UUID REFERENCES batch_items(id),
    extraction_confidence NUMERIC(3, 2),  -- 0.00-1.00
    extraction_notes TEXT,                -- AI notes, warnings
    extraction_method TEXT,               -- markdown, pdf_table, spreadsheet, ocr, ai
    
    -- Status
    review_status TEXT DEFAULT 'auto_approved', -- auto_approved, pending_review, approved, rejected
    is_active BOOLEAN DEFAULT true,
    
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    
    -- Constraints
    UNIQUE (supplier_id, supplier_sku)
);

-- 2. Product Images junction table (many-to-many)
CREATE TABLE IF NOT EXISTS product_product_images (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID REFERENCES products(id) ON DELETE CASCADE,
    product_image_id UUID REFERENCES product_images(id) ON DELETE CASCADE,
    role TEXT DEFAULT 'primary',          -- primary, alternate, detail, packaging, label
    sort_order INTEGER DEFAULT 0,
    match_confidence NUMERIC(3, 2),       -- 0.00-1.00
    match_method TEXT,                    -- filename, sku, ocr, ai, manual
    match_reasoning TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    
    UNIQUE (product_id, product_image_id)
);

-- 3. Price History (append-only audit trail)
CREATE TABLE IF NOT EXISTS price_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID REFERENCES products(id) ON DELETE CASCADE,
    batch_id UUID REFERENCES batches(id),
    
    -- Price snapshot
    price_amount NUMERIC(12, 4),
    price_currency CHAR(3),
    price_unit TEXT,
    price_min_order_qty INTEGER,
    
    -- Context
    effective_date DATE NOT NULL,
    source_batch_item_id UUID REFERENCES batch_items(id),
    change_reason TEXT,                   -- new_listing, price_update, promotion, etc.
    
    created_at TIMESTAMPTZ DEFAULT now()
);

-- 4. Supplier Profiles (enhanced)
ALTER TABLE suppliers 
ADD COLUMN IF NOT EXISTS contact_name TEXT,
ADD COLUMN IF NOT EXISTS email TEXT,
ADD COLUMN IF NOT EXISTS phone TEXT,
ADD COLUMN IF NOT EXISTS address TEXT,
ADD COLUMN IF NOT EXISTS website TEXT,
ADD COLUMN IF NOT EXISTS payment_terms TEXT,
ADD COLUMN IF NOT EXISTS delivery_areas TEXT[],     -- array of regions
ADD COLUMN IF NOT EXISTS delivery_schedule TEXT,
ADD COLUMN IF NOT EXISTS notes TEXT;

-- 5. Indexes
CREATE INDEX IF NOT EXISTS idx_products_supplier ON products(supplier_id);
CREATE INDEX IF NOT EXISTS idx_products_supplier_sku ON products(supplier_id, supplier_sku);
CREATE INDEX IF NOT EXISTS idx_products_canonical_name ON products(canonical_name);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
CREATE INDEX IF NOT EXISTS idx_products_status ON products(review_status, is_active);
CREATE INDEX IF NOT EXISTS idx_products_batch ON products(batch_id);
CREATE INDEX IF NOT EXISTS idx_product_images_product ON product_product_images(product_id);
CREATE INDEX IF NOT EXISTS idx_product_images_asset ON product_product_images(product_image_id);
CREATE INDEX IF NOT EXISTS idx_price_history_product ON price_history(product_id, effective_date DESC);
CREATE INDEX IF NOT EXISTS idx_price_history_batch ON price_history(batch_id);

-- 6. Updated at triggers
DROP TRIGGER IF EXISTS products_updated_at ON products;
CREATE TRIGGER products_updated_at
    BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS suppliers_updated_at ON suppliers;
CREATE TRIGGER suppliers_updated_at
    BEFORE UPDATE ON suppliers
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
"""

if __name__ == "__main__":
    print(M4_SCHEMA)