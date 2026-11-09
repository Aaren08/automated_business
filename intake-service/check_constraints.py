import os
from dotenv import load_dotenv
load_dotenv('/mnt/d/n8n/intake-service/.env')
from supabase import create_client

url = os.environ.get('SUPABASE_URL')
key = os.environ.get('SUPABASE_SERVICE_KEY')
client = create_client(url, key)

# Get constraint names on products
print('=== Constraints on products ===')
r = client.table('information_schema.table_constraints').select('constraint_name, constraint_type').eq('table_name', 'products').execute()
if r.data:
    for row in r.data:
        print(f"  {row['constraint_name']}: {row['constraint_type']}")
else:
    print('No table_constraints access')
print()

# Get indexes on products
print('=== Indexes on products ===')
r = client.table('information_schema.statistics').select('index_name').eq('table_name', 'products').execute()
if r.data:
    indexes = set(row['index_name'] for row in r.data)
    for idx in sorted(indexes):
        print(f'  {idx}')
else:
    print('No statistics access')
print()

# Check pgcrypto extension
print('=== pgcrypto extension ===')
r = client.table('pg_extension').select('extname').eq('extname', 'pgcrypto').execute()
if r.data:
    print(f'pgcrypto: INSTALLED')
else:
    print('pgcrypto: NOT INSTALLED')