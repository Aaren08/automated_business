#!/usr/bin/env python3
"""Check supplier content_hash"""

import os
from supabase import create_client

url = os.environ['SUPABASE_URL']
key = os.environ['SUPABASE_SERVICE_KEY']
client = create_client(url, key)

result = client.table('suppliers').select('id, name, content_hash').eq('name', 'Dallas & Fort worth').execute()
for s in result.data:
    ch = s.get('content_hash')
    print(f'{s["name"]}: content_hash={ch}')