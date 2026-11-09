#!/usr/bin/env python3
"""Check content_hash for suppliers and products"""

import os
from supabase import create_client

url = os.environ['SUPABASE_URL']
key = os.environ['SUPABASE_SERVICE_KEY']
client = create_client(url, key)

# Check suppliers with content_hash
result = client.table('suppliers').select('id, name, content_hash').execute()
for s in result.data:
    ch = s.get('content_hash')
    print(f'{s["name"]}: content_hash={ch}')

# Check products with content_hash
result = client.table('products').select('id, canonical_name, content_hash, supplier_sku').execute()
with_hash = [p for p in result.data if p.get('content_hash')]
print(f'\nProducts with content_hash: {len(with_hash)} / {len(result.data)}')

for p in result.data[:10]:
    ch = p.get('content_hash')
    if ch:
        print(f'  {p["canonical_name"]}: {ch[:16]}...')
    else:
        print(f'  {p["canonical_name"]}: NO HASH')