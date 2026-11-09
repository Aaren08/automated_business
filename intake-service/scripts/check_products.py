#!/usr/bin/env python3
"""Check product deduplication"""

import os
from supabase import create_client
from collections import defaultdict

url = os.environ['SUPABASE_URL']
key = os.environ['SUPABASE_SERVICE_KEY']
client = create_client(url, key)

# Check products
result = client.table('products').select('*').execute()
print(f'Total products: {len(result.data)}')

# Group by content_hash
groups = defaultdict(list)
for p in result.data:
    if p['content_hash']:
        groups[p['content_hash']].append(p)

print()
print('=== Duplicate Groups (by content_hash) ===')
for chash, products in groups.items():
    if len(products) > 1:
        print(f'\nContent hash {chash[:8]}... ({len(products)} records):')
        for p in products:
            print(f'  ID: {p["id"][:8]} | Name: {p["canonical_name"]} | SKU: {p["supplier_sku"]} | Supplier: {p["supplier_id"][:8]} | Price: {p["price_amount"]} {p["price_currency"]} | Batch: {p["batch_id"][:8]} | Created: {p["created_at"]}')

# Count products with SKU
with_sku = sum(1 for p in result.data if p['supplier_sku'])
print(f'\nProducts with SKU: {with_sku} / {len(result.data)}')

# Check suppliers
result = client.table('suppliers').select('*').execute()
print(f'\nSuppliers: {len(result.data)}')
for s in result.data:
    ch = s.get('content_hash')
    print(f'  {s["name"]} (content_hash: {ch[:8] if ch else "N/A"}...)')