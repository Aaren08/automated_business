import os
from dotenv import load_dotenv
load_dotenv('/mnt/d/n8n/intake-service/.env')
from supabase import create_client

url = os.environ.get('SUPABASE_URL')
key = os.environ.get('SUPABASE_SERVICE_KEY')
client = create_client(url, key)

# Try rpc to get constraint info
try:
    r = client.rpc('pg_constraint_query', {'table_name': 'products'}).execute()
    print('RPC result:', r.data)
except Exception as e:
    print(f'RPC failed: {e}')

# Try to get indexes via information_schema
try:
    r = client.table('pg_indexes').select('indexname, indexdef').eq('tablename', 'products').execute()
    print(f'pg_indexes: {r.data}')
except Exception as e:
    print(f'pg_indexes failed: {e}')