import asyncio
import asyncpg
import os

url = os.environ['SUPABASE_URL']
key = os.environ['SUPABASE_SERVICE_KEY']

project_id = 'vfworiwvplpwppwqqkrx'
db_url = f'postgresql://postgres:{key}@db.{project_id}.supabase.co:5432/postgres'


async def create_tables():
    try:
        conn = await asyncpg.connect(db_url)
        print('Connected to database')

        await conn.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
        print('uuid-ossp extension created')

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS suppliers (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name TEXT NOT NULL,
                contact_name TEXT,
                email TEXT,
                phone TEXT,
                address TEXT,
                website TEXT,
                notes TEXT,
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        print('suppliers table created')

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS batches (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                batch_id TEXT UNIQUE NOT NULL,
                supplier_id UUID REFERENCES suppliers(id),
                source_path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'discovered',
                supplier_md_hash TEXT,
                total_files INTEGER DEFAULT 0,
                total_images INTEGER DEFAULT 0,
                total_products INTEGER DEFAULT 0,
                error_message TEXT,
                started_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT now(),
                updated_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        print('batches table created')

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS batch_items (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                batch_id UUID REFERENCES batches(id) ON DELETE CASCADE,
                file_path TEXT NOT NULL,
                file_name TEXT NOT NULL,
                file_size BIGINT NOT NULL,
                mime_type TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                file_type TEXT NOT NULL,
                parsed_content JSONB,
                ocr_text TEXT,
                processing_status TEXT DEFAULT 'pending',
                error_message TEXT,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        print('batch_items table created')

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS processing_jobs (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                batch_id UUID REFERENCES batches(id),
                job_type TEXT NOT NULL,
                status TEXT NOT NULL,
                input_params JSONB,
                result_summary JSONB,
                error_message TEXT,
                started_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
        print('processing_jobs table created')

        await conn.execute('CREATE INDEX IF NOT EXISTS idx_batches_status ON batches(status)')
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_batches_batch_id ON batches(batch_id)')
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_batch_items_batch_id ON batch_items(batch_id)')
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_batch_items_file_hash ON batch_items(file_hash)')
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_processing_jobs_batch_id ON processing_jobs(batch_id)')
        await conn.execute('CREATE INDEX IF NOT EXISTS idx_processing_jobs_status ON processing_jobs(status)')
        print('Indexes created')

        await conn.execute("""
            CREATE OR REPLACE FUNCTION update_updated_at_column()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = now();
                RETURN NEW;
            END;
            $$ language 'plpgsql'
        """)
        print('Trigger function created')

        await conn.execute("""
            DROP TRIGGER IF EXISTS suppliers_updated_at ON suppliers;
            CREATE TRIGGER suppliers_updated_at
                BEFORE UPDATE ON suppliers
                FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()
        """)
        await conn.execute("""
            DROP TRIGGER IF EXISTS batches_updated_at ON batches;
            CREATE TRIGGER batches_updated_at
                BEFORE UPDATE ON batches
                FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()
        """)
        print('Triggers created')

        await conn.close()
        print('All tables created successfully!')

    except Exception as e:
        print(f'Error: {e}')
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(create_tables())