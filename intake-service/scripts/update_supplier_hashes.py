#!/usr/bin/env python3
"""Update existing suppliers with content_hash"""

import os
from supabase import create_client
from collections import defaultdict

url = os.environ['SUPABASE_URL']
key = os.environ['SUPABASE_SERVICE_KEY']
client = create_client(url, key)

# Get all suppliers without content_hash
result = client.table('suppliers').select('id, name, delivery_areas, contact_name, email, phone, address').execute()

for s in result.data:
    if not s.get('content_hash'):
        # Compute content_hash
        supplier_data = {
            'name': s['name'],
            'delivery_areas': s.get('delivery_areas'),
            'contact_name': s.get('contact_name'),
            'email': s.get('email'),
            'phone': s.get('phone'),
            'address': s.get('address'),
        }
        
        # Compute content_hash
        import hashlib
        key_fields = ['name', 'delivery_areas', 'contact_name', 'email', 'phone', 'address']
        parts = []
        for field in key_fields:
            value = supplier_data.get(field)
            if value is not None:
                if isinstance(value, list):
                    parts.append(f'{field}:[{",".join(sorted(value))}]')
                else:
                    parts.append(f'{field}:{value}')
        content = '|'.join(parts)
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:32]
        
        # Update supplier
        try:
            result = client.table('suppliers').update({'content_hash': content_hash}).eq('id', s['id']).execute()
            print(f'Updated {s["name"]} with content_hash: {content_hash[:16]}...')
        except Exception as e:
            print(f'Error updating {s["name"]}: {e}')