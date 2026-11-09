#!/usr/bin/env python3
"""Run migration to add content_hash columns"""

import os
from supabase import create_client

url = os.environ['SUPABASE_URL']
key = os.environ['SUPABASE_SERVICE_KEY']
client = create_client(url, key)

# Check if columns exist
print("Checking current schema...")
result = client.table('suppliers').select('*').limit(1).execute()
if result.data:
    cols = list(result.data[0].keys())
    print(f'Suppliers columns: {cols}')
    has_content_hash = 'content_hash' in cols
    print(f'Suppliers has content_hash: {has_content_hash}')

result = client.table('products').select('*').limit(1).execute()
if result.data:
    cols = list(result.data[0].keys())
    print(f'Products columns: {cols}')
    has_content_hash = 'content_hash' in cols
    print(f'Products has content_hash: {has_content_hash}')

print('\nPlease run this SQL in Supabase SQL Editor:')
print('''
ALTER TABLE suppliers ADD COLUMN IF NOT EXISTS content_hash TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_suppliers_content_hash ON suppliers(content_hash);

ALTER TABLE products ADD COLUMN IF NOT EXISTS content_hash TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_products_content_hash ON products(content_hash);
''')