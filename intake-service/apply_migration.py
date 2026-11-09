import os
from dotenv import load_dotenv
load_dotenv('/mnt/d/n8n/intake-service/.env')
from supabase import create_client

url = os.environ.get('SUPABASE_URL')
key = os.environ.get('SUPABASE_SERVICE_KEY')
client = create_client(url, key)

print(f"Connected to Supabase: {url}")
print()

# Phase 1: Read-only diagnostics - get current state
print("=== PHASE 1: PRE-FLIGHT DIAGNOSTICS ===")
print()

# Products count
r = client.table('products').select('count', count='exact').limit(1).execute()
print(f"Products: {r.count} rows")

# Batches count
r = client.table('batches').select('count', count='exact').limit(1).execute()
print(f"Batches: {r.count} rows")

# Batch items count
r = client.table('batch_items').select('count', count='exact').limit(1).execute()
print(f"Batch items: {r.count} rows")

# Suppliers count
r = client.table('suppliers').select('count', count='exact').limit(1).execute()
print(f"Suppliers: {r.count} rows")

# Price history count
r = client.table('price_history').select('count', count='exact').limit(1).execute()
print(f"Price history: {r.count} rows")

# Check if internal_sku exists
try:
    r = client.table('products').select('internal_sku').limit(1).execute()
    print(f"internal_sku column: EXISTS")
except Exception as e:
    print(f"internal_sku column: DOES NOT EXIST")

# Check if product_source_items exists
try:
    r = client.table('product_source_items').select('count', count='exact').limit(1).execute()
    print(f"product_source_items: EXISTS, {r.count} rows")
except Exception as e:
    print(f"product_source_items: DOES NOT EXIST")

print()
print("=== PRE-FLIGHT COMPLETE ===")