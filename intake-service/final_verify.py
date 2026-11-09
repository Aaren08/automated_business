import os
from dotenv import load_dotenv
load_dotenv('/mnt/d/n8n/intake-service/.env')
from supabase import create_client

url = os.environ.get('SUPABASE_URL')
key = os.environ.get('SUPABASE_SERVICE_KEY')
client = create_client(url, key)

print('=== POST-MIGRATION VERIFICATION ===')
print()

# 1. Products with internal_sku
r = client.table('products').select('count', count='exact').limit(1).execute()
print(f'Total products: {r.count}')

r = client.table('products').select('count', count='exact').not_.is_('internal_sku', 'null').execute()
print(f'Products with internal_sku: {r.count}')

r = client.table('products').select('count', count='exact').is_('internal_sku', 'null').execute()
print(f'Products WITHOUT internal_sku: {r.count}')
print()

# 2. Duplicate internal_sku check
r = client.table('products').select('internal_sku').not_.is_('internal_sku', 'null').execute()
from collections import Counter
skus = [row['internal_sku'] for row in r.data]
dupes = {sku: count for sku, count in Counter(skus).items() if count > 1}
if dupes:
    print(f'DUPLICATE internal_sku values: {len(dupes)}')
    for sku, count in sorted(dupes.items(), key=lambda x: -x[1])[:10]:
        print(f'  {sku}: {count} records')
else:
    print('No duplicate internal_sku values')
print()

# 3. product_source_items table
try:
    r = client.table('product_source_items').select('count', count='exact').limit(1).execute()
    print(f'product_source_items: EXISTS, {r.count} rows')
except Exception as e:
    print(f'product_source_items: DOES NOT EXIST')
print()

# 4. price_history package_id column
try:
    r = client.table('price_history').select('package_id').limit(1).execute()
    print(f'price_history.package_id column: EXISTS')
except Exception as e:
    print(f'price_history.package_id column: DOES NOT EXIST')
print()

# 5. content_hash column still on products
try:
    r = client.table('products').select('content_hash').limit(1).execute()
    print(f'products.content_hash column: EXISTS')
except Exception as e:
    print(f'products.content_hash column: DOES NOT EXIST')
print()

print('=== VERIFICATION COMPLETE ===')
