#!/usr/bin/env python3
"""Update supplier content hashes with new algorithm"""

import os
import hashlib
from supabase import create_client

url = os.environ['SUPABASE_URL']
key = os.environ['SUPABASE_SERVICE_KEY']
client = create_client(url, key)


def compute_hash(data: dict) -> str:
    def _serialize_value(value):
        if isinstance(value, dict):
            items = [f"{k}:{_serialize(v)}" for k, v in sorted(value.items())]
            return "{" + ",".join(items) + "}"
        elif isinstance(value, list):
            items = [_serialize(v) for v in value]
            items.sort()
            return "[" + ",".join(items) + "]"
        else:
            return str(value)

    def _serialize(v):
        if isinstance(v, dict):
            items = [f"{k}:{_serialize(v)}" for k, v in sorted(v.items())]
            return "{" + ",".join(items) + "}"
        elif isinstance(v, list):
            items = [_serialize(v) for v in v]
            items.sort()
            return "[" + ",".join(items) + "]"
        else:
            return str(v)

    parts = []
    for key in sorted(data.keys()):
        value = data[key]
        if value is not None:
            if isinstance(value, dict):
                items = [f"{k}:{_serialize(v)}" for k, v in sorted(value.items())]
                parts.append(f"{key}:{{{','.join(items)}}}")
            elif isinstance(value, list):
                items = [_serialize(v) for v in value]
                items.sort()
                parts.append(f"{key}:[{','.join(items)}]")
            else:
                parts.append(f"{key}:{value}")

    content = "|".join(parts)
    return hashlib.sha256(content.encode()).hexdigest()[:32]


result = client.table('suppliers').select('id, name, delivery_areas, contact_name, email, phone, address, content_hash').execute()

for s in result.data:
    data = {k: v for k, v in s.items() if k in ['name', 'delivery_areas', 'contact_name', 'email', 'phone', 'address'] and v is not None}
    new_hash = compute_hash(data)
    old_hash = s.get('content_hash')
    if old_hash != new_hash:
        print(f'Updating {s["name"]}: {old_hash} -> {new_hash}')
        client.table('suppliers').update({'content_hash': new_hash}).eq('id', s['id']).execute()
    else:
        print(f'No change for {s["name"]}')