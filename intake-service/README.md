# Product Intelligence Intake Service

FastAPI service for ingesting supplier product data from Obsidian vault.

## Architecture

```
Obsidian Vault (bind mount, read-only)
    │
    ▼
Intake Service (FastAPI, port 8000)
    │
    ├──▶ Supabase (PostgreSQL) - Structured data
    │
    └──▶ S3-compatible Storage - Original files & processed images
```

## Quick Start

### Prerequisites
- Docker Desktop with file sharing enabled for `C:\`
- Supabase project with service role key
- S3-compatible object storage (Supabase Storage used here)

### Configuration

Copy `.env.example` to `.env` and fill in values:

```bash
# Obsidian supplier inbox (Windows path)
OBSIDIAN_SUPPLIER_PATH=C:\Users\Administrator\Documents\Obsidian Vault\Business Automation\Product Intelligence\00_Supplier Inbox

# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your-service-role-key

# S3-compatible (Supabase Storage)
S3_ENDPOINT=https://your-project.storage.supabase.co/storage/v1/s3
S3_ACCESS_KEY=your-access-key
S3_SECRET_KEY=your-secret-key
S3_BUCKET=product-intelligence

# Service auth
INTAKE_API_KEY=your-random-32-char-hex
```

### Development

```bash
# Build and run with docker compose (from parent directory)
docker compose -f compose.yml -f compose.override.yml up --build intake

# Or run locally
pip install -e ".[dev]"
uvicorn src.main:app --reload
```

### API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/healthz` | Health check (no auth) |
| POST | `/api/v1/scan` | Discover batches with `_READY` marker |
| GET | `/api/v1/batches` | List batches |
| GET | `/api/v1/batches/{id}` | Batch details |
| GET | `/api/v1/batches/{id}/status` | Processing status |
| POST | `/api/v1/batches/{id}/process` | Trigger processing |
| POST | `/api/v1/batches/{id}/review/{item_id}/approve` | Approve review item |
| POST | `/api/v1/batches/{id}/review/{item_id}/reject` | Reject review item |
| GET | `/api/v1/jobs/{job_id}` | Job details |

All endpoints except `/healthz` require `X-API-Key` header.

### Batch Structure

```
Batch-2026-11/
├── Supplier.md          # Supplier info + product descriptions
├── _READY               # Marker file (created manually when ready)
├── images/              # Product images
├── catalogs/            # PDF catalogs
└── spreadsheets/        # Excel files (.xlsx, .xls)
```

### Processing Pipeline

1. **Scan** - n8n cron calls `POST /scan`, service finds `_READY` batches
2. **Validate** - Check completeness, acquire lock
3. **Parse** - Extract text from markdown, PDF, spreadsheets
4. **OCR** - Tesseract for scanned PDFs/images
5. **Extract** - Normalize to product schema
6. **Inventory** - Hash images, generate thumbnails, contact sheets
7. **Match** - Associate products with images (confidence scoring)
8. **Store** - Upsert to Supabase, upload to S3
9. **Notify** - n8n polls status, routes low-confidence to human review

### Testing

```bash
# Unit tests
pytest tests/unit -v

# Integration tests (requires services)
pytest tests/integration -v

# Lint
ruff check src tests
mypy src
```

## Milestones

- [x] M0: Foundation - FastAPI skeleton, healthcheck, config, Docker
- [ ] M1: Batch Discovery - Scan inbox, `_READY` detection, Supabase registry
- [ ] M2: Parsing Pipeline - Markdown, PDF, spreadsheet parsers
- [ ] M3: OCR & Images - Tesseract, image inventory, thumbnails
- [ ] M4: Extraction - Product schema normalization, upsert
- [ ] M5: Image Matching - Confidence scoring, review queue
- [ ] M6: Object Storage - S3 upload, originals preservation
- [ ] M7: n8n Integration - Cron workflow, status polling, review routing
- [ ] M8: Reliability - Retries, dead-letter, lock cleanup, observability
- [ ] M9: AI Assistance (optional) - LLM extraction hints
- [ ] M10: Documentation - API docs, runbooks, handoff