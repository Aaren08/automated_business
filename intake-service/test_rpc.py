import os
from dotenv import load_dotenv
load_dotenv('/mnt/d/n8n/intake-service/.env')
from supabase import create_client

url = os.environ.get('SUPABASE_URL')
key = os.environ.get('SUPABASE_SERVICE_KEY')
client = create_client(url, key)

# Try various function names that might execute SQL
for func_name in ['pg_query', 'exec_sql', 'run_sql', 'execute_sql', 'db_exec', 'sql_exec']:
    try:
        r = client.rpc(func_name, {'query': 'SELECT 1 as test'}).execute()
        print(f"rpc('{func_name}'): SUCCESS - {r.data}")
    except Exception as e:
        print(f"rpc('{func_name}'): FAILED - {str(e)[:100]}")
print()

# Try with different param names
for func_name in ['pg_query', 'exec_sql', 'run_sql']:
    try:
        r = client.rpc(func_name, {'q': 'SELECT 1 as test'}).execute()
        print(f"rpc('{func_name}', {{'q': ...}}): SUCCESS - {r.data}")
    except Exception as e:
        print(f"rpc('{func_name}', {{'q': ...}}): FAILED - {str(e)[:100]}")
print()

# Try with 'query' param but different format
try:
    r = client.rpc('pg_query', {'query': 'SELECT 1 as test'}).execute()
    print(f"rpc('pg_query', {{'query': ...}}): SUCCESS - {r.data}")
except Exception as e:
    print(f"rpc('pg_query', {{'query': ...}}): FAILED - {str(e)[:100]}")