#!/usr/bin/env python3
"""Diagnose product duplicates in the database"""

import os
from supabase import create_client
from collections import defaultdict

url = os.environ['SUPABASE_URL']
key = os.environ['SUPABASE_SERVICE_KEY']
client = create_client(url, key)

# Get all products with their details
result = client.table('products').select('*').execute()
print(f'Total products: {len(result.data)}')
print()

# Group by canonical_name to find duplicates
groups = defaultdict(list)
for p in result.data:
    groups[p['canonical_name']].append(p)

print('=== Duplicate Groups (by canonical_name) ===')
for name, products in groups.items():
    if len(products) > 1:
        print(f'\n{name} ({len(products)} records):')
        for p in products:
            print(f'  ID: {p["id"][:8]} | SKU: {p["supplier_sku"]} | Supplier: {p["supplier_id"][:8]} | Price: {p["price_amount"]} {p["price_currency"]} | Conf: {p.get("extraction_confidence")} | Batch: {p["batch_id"][:8]} | Source: {p.get("extraction_method")} | Created: {p["created_at"]}')
    else:
        p = products[0]
        if p['supplier_sku'] is None:
            print(f'{name} (single, no SKU): supplier={p["supplier_id"][:8]} batch={p["batch_id"][:8]} method={p.get("extraction_method")}')

# Also check by supplier_sku
print('\n\n=== Duplicate Groups (by supplier_sku) ===')
sku_groups = defaultdict(list)
for p in result.data:
    if p['supplier_sku']:
        sku_groups[p['supplier_sku']].append(p)

for sku, products in sku_groups.items():
    if len(products) > 1:
        print(f'\nSKU: {sku} ({len(products)} records):')
        for p in products:
            print(f'  ID: {p["id"][:8]} | Name: {p["canonical_name"]} | Supplier: {p["supplier_id"][:8]} | Price: {p["price_amount"]} {p["price_currency"]} | Batch: {p["batch_id"][:8]} | Created: {p["created_at"]}')

# Check batches
print('\n\n=== Batch Summary ===')
batch_result = client.table('batches').select('*').execute()
for b in batch_result.data:
    print(f'Batch: {b["batch_id"]} | Source: {b["source_path"]} | Status: {b["status"]} | Products: {b["total_products"]} | Created: {b["created_at"]}')

# Check suppliers
print('\n\n=== Suppliers ===')
supplier_result = client.table('suppliers').select('*').execute()
for s in supplier_result.data:
    print(f'Supplier: {s["name"]} | ID: {s["id"][:8]} | Delivery: {s.get("delivery_areas")}')