#!/usr/bin/env python3
"""Check supplier content hash computation"""

import sys
sys.path.insert(0, '/app')

from src.core.parser import parser_registry
from src.core.extractor import supplier_extractor
from src.core.extractor.pipeline import _compute_content_hash
from pathlib import Path
from supabase import create_client
import os

result = parser_registry.parse(Path('/data/inbox/Batch-2026-11/supplier.md'))
candidate = supplier_extractor.extract(result, 'supplier.md')

supplier_data = {
    'name': candidate.name,
    'delivery_areas': candidate.delivery_areas,
    'contact_name': candidate.contact_name,
    'email': candidate.email,
    'phone': candidate.phone,
    'address': candidate.address,
}

hash_val = _compute_content_hash({
    'name': candidate.name,
    'delivery_areas': candidate.delivery_areas,
    'contact_name': candidate.contact_name,
    'email': candidate.email,
    'phone': candidate.phone,
    'address': candidate.address,
})

print(f'Computed hash: {hash_val}')

url = os.environ['SUPABASE_URL']
key = os.environ['SUPABASE_SERVICE_KEY']
client = create_client(url, key)

result = client.table('suppliers').select('id, name, content_hash').eq('name', 'Dallas & Fort worth').execute()
for s in result.data:
    ch = s.get('content_hash')
    print(f'DB hash: {ch}')
    print(f'Match: {ch == hash_val}')