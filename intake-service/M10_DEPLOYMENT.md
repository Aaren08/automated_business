# M10: End-to-End Validation & Production Readiness

## Overview

M10 validates the complete Product Intelligence intake pipeline (M0–M9) and
documents production deployment, configuration, and operational procedures.

This document covers:
1. **Deployment** — Docker Compose, environment variables, Supabase connectivity
2. **Workflow import & activation** — n8n workflow setup
3. **Troubleshooting** — common failure modes and resolution
4. **Manual dead-letter recovery** — how to reprocess dead-lettered batches
5. **Validation results** — test coverage and what was/wasn't verified end-to-end

---

## 1. Deployment

### Prerequisites

- Docker Desktop with Docker Compose v2
- File sharing enabled for the drive containing `OBSIDIAN_SUPPLIER_PATH`
- A Supabase project with service role key and S3 credentials

### Stack startup

From the repository root (`D:\n8n`):

```bash
docker compose -f compose.yml -f compose.override.yml up -d --build intake
```

This builds and starts the `intake` service (FastAPI, port 8000, internal-only)
alongside n8n and the sandbox services. The intake service is **not** exposed
to the host; n8n reaches it via the Compose service name `intake:8000`.

### Verify health

```bash
# From inside the intake container or any container on the same network
curl -s http://intake:8000/healthz
# Expected: {"status":"ok","service":"product-intake","version":"0.1.0"}
```

### Supabase schema

Apply the M3 image inventory schema (creates `product_images`,
`batch_contact_sheets`, image columns on `batch_items`) before first run:

```bash
# Execute migrate_m3_schema.py SQL in Supabase SQL Editor
python scripts/migrate_m3_schema.py
```

The M4 alignment migration (`migrate_m4_alignment.sql`) adds `internal_sku`,
`product_source_items`, and price-history idempotency safeguards.

---

## 2. Required Environment Variables

The `.env` file at the repository root must contain:

| Variable | Description | Example |
|----------|-------------|---------|
| `N8N_VERSION` | n8n image version | `2.42.1` |
| `INTAKE_URL` | Intake service URL for n8n | `http://intake:8000` |
| `INTAKE_API_KEY` | Shared secret for n8n→intake auth | `a1b2c3...` |
| `SUPABASE_URL` | Supabase project URL | `https://*.supabase.co` |
| `SUPABASE_SERVICE_KEY` | Supabase service role key | `sb_secret_...` |
| `S3_ENDPOINT` | Supabase Storage S3 endpoint | `https://*.storage.supabase.co/storage/v1/s3` |
| `S3_ACCESS_KEY` | S3 access key | `...` |
| `S3_SECRET_KEY` | S3 secret key | `...` |
| `S3_BUCKET` | Storage bucket name | `product-intelligence` |
| `OBSIDIAN_SUPPLIER_PATH` | Windows path to supplier inbox | `C:\...\00_Supplier Inbox` |

The `intake-service/.env` is a template; the root `.env` is the source of truth
for Docker Compose variable substitution.

### Security notes

- `SUPABASE_SERVICE_KEY` and `S3_SECRET_KEY` are credentials — never commit
  `.env` to version control (it is gitignored).
- The intake service publishes **no ports** to the host; it is internal-only
  via the Compose network.
- n8n publishes only port `5678` (the only internet-facing port).

---

## 3. n8n Workflow Import & Activation

### Import

1. Open n8n at `http://localhost:5678`
2. Go to **Workflows** → **Import**
3. Select `n8n/workflows/product-intelligence-scan.json`
4. The workflow is imported **inactive** (`active: false`)

### Activate

Review the workflow, then toggle it **Active**. The cron trigger runs every
30 minutes and will:

1. `POST /scan` — discover `_READY` batches and register them
2. `GET /batches` — list all batches
3. Route each batch by status (completed / failed / awaiting_review /
   processing / discovered / pending)
4. For `discovered` and `pending` batches, call `POST /process` (M8 handles
   retry limits, concurrency guards, stale-lock recovery, dead-lettering)

### Notifications

Connect the `Notify:*` Set nodes to your preferred channel:
- **Email**: n8n Email node (SMTP)
- **Slack**: n8n Slack node (webhook)
- **Webhook**: n8n HTTP Request node to your alert endpoint
- **n8n Chat**: n8n built-in chat notifications

---

## 4. Troubleshooting

| Symptom | Likely cause | Resolution |
|---------|-------------|------------|
| `intake` container unhealthy | Supabase unreachable, bad env vars | Check `docker logs intake`; verify `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `INTAKE_API_KEY` in `.env` |
| `401 Invalid API key` | Wrong/missing `X-API-Key` | Ensure `INTAKE_API_KEY` matches between `.env` and n8n workflow auth |
| Batch stuck in `processing` | Job running longer than 30 min TTL | M8 stale-lock recovery auto-reaps on next `/process` call; or wait for n8n cron |
| Batch stuck in `failed` | Retries exhausted (dead-lettered) | See dead-letter recovery below |
| n8n can't reach intake | `INTAKE_URL` wrong, containers on different networks | Verify `INTAKE_URL=http://intake:8000`; both services must use same Compose project |
| Images not uploaded | Supabase Storage bucket missing/private | Create `product-intelligence` bucket; verify `S3_ACCESS_KEY`/`S3_SECRET_KEY` |

### Logs

```bash
docker logs intake --tail 100
docker logs n8n --tail 100
```

---

## 5. Manual Dead-Letter Recovery

When a batch is dead-lettered (`status=failed`, `result_summary.dead_letter=true`),
n8n stops routing it to `/process`. To recover:

### Step 1: Diagnose

```bash
curl -s -H "X-API-Key: $INTAKE_API_KEY" \
  http://intake:8000/api/v1/batches/{batch_id}/status
```

Check `error_message` and `result_summary.retry_count` / `dead_lettered_at`.

### Step 2: Fix the root cause

Common causes:
- Supabase Storage bucket full or permissions wrong → fix bucket
- Extraction error from malformed source file → fix the source file in the inbox
- Transient network error → usually self-heals on retry

### Step 3: Re-trigger processing

```bash
curl -X POST -H "X-API-Key: $INTAKE_API_KEY" \
  http://intake:8000/api/v1/batches/{batch_id}/process
```

M8's concurrency guard will create a new job (the dead-lettered one is
terminal). `retry_count` resets to 0 on the new job, giving up to
`MAX_RETRIES=3` more attempts.

### Step 4: Monitor

Poll `/batches/{batch_id}/status` until `status=completed` or `status=failed`
again. If it fails again, the root cause was not fixed.

---

## 6. Validation Results

### Test coverage (unit + integration)

| Test file | Tests | Coverage |
|-----------|-------|----------|
| `tests/unit/test_health.py` | 1 | Health endpoint schema |
| `tests/unit/test_storage.py` | 7 | M6: upload success/failure/retry/idempotent skip |
| `tests/unit/test_m4_alignment.py` | 32 | M4: batch discovery, SKU stability, price history |
| `tests/unit/test_m8_reliability.py` | 15 | M8: retry count, dead-letter, stale lock, concurrency |
| `tests/unit/test_n8n_workflow.py` | 40 | M7/M9: workflow structure, endpoints, auth, routing |
| `tests/integration/test_e2e_lifecycle.py` | 14 | M10: scan→process→complete/fail, retry exhaustion, dead-letter, stale lock, concurrency, status endpoints, auth |

**Total: 109 tests, all passing.**

### Verified end-to-end behavior

| Behavior | Verified by | How |
|----------|-------------|-----|
| Scan discovers and registers batches | `TestScanLifecycle` | Mocked storage, real route handler |
| Scan skips unchanged batches | `TestScanLifecycle` | Hash comparison |
| Process creates job and queues | `TestProcessLifecycle` | Real `process_batch` route |
| Processing completes successfully | `TestProcessLifecycle` | `process_package_files` background task |
| Retry exhaustion → dead-letter | `TestProcessLifecycle` | 3 attempts then `dead_letter=true` |
| Inventory failure is non-fatal | `TestProcessLifecycle` | Job still completes |
| Concurrency guard (no duplicate jobs) | `TestConcurrencyAndRecovery` | Existing running job returned |
| Stale-lock recovery | `TestConcurrencyAndRecovery` | TTL-exceeded job dead-lettered, new job created |
| Batch not found → 404 | `TestConcurrencyAndRecovery` | HTTPException |
| Status endpoints reflect DB state | `TestStatusEndpoints` | `completed`/`failed` + `dead_letter` visible |
| API key auth enforced | `TestRouteSchemaAndAuth` | 401 without/invalid key |
| JobStatus schema conformance | `TestRouteSchemaAndAuth` | UUID, status enum, result_summary |
| M8 helper functions | `TestM8Helpers` | retry_count, stale_lock, dead_letter |
| n8n workflow structure | `TestWorkflowStructure` | Valid JSON, node types, cron interval |
| n8n endpoints match real API | `TestEndpoints` | Paths verified against `routes.py` |
| n8n auth configured | `TestAuthentication` | `INTAKE_API_KEY` env var, not hardcoded |
| n8n retry routing | `TestRetryRouting` | Only discovered/pending → `/process` |
| Terminal states not retried | `TestTerminalStateNonRetry` | completed/failed never → `/process` |

### NOT verified end-to-end

- **Live Docker Compose stack** — Docker Desktop was available but the full
  stack (n8n + sandbox services + intake + Supabase) was not started; no
  live HTTP requests were exchanged between n8n and intake.
- **Live Supabase instance** — All storage calls are mocked; no actual
  Supabase Storage bucket or PostgreSQL database was used.
- **Live n8n workflow execution** — The workflow JSON was validated for
  structure and endpoint references, but not executed in a running n8n
  instance.

These require a running Supabase project and Docker Compose stack to verify.

---

## 7. Remaining Production Blockers

1. **Live smoke test not performed** — A full `docker compose up` + Supabase +
   n8n workflow execution is needed to confirm end-to-end operation.
2. **Bucket creation** — The `product-intelligence` Supabase Storage bucket
   must be created manually (private by default) before image uploads work.
3. **Schema migration** — M3 and M4 SQL migrations must be applied to the
   Supabase database before first run.
4. **Human review UI** — The approve/reject endpoints exist but return
   placeholders (`TODO: Implement in M5`); a review UI is not yet built.
5. **n8n workflow activation** — The workflow is imported `active: false`;
   an operator must review and activate it.

---

## 8. Configuration Validation Checklist

Before production use, verify:

- [ ] `.env` contains all required variables (see §2)
- [ ] `INTAKE_URL=http://intake:8000` is set for n8n→intake connectivity
- [ ] Supabase Storage bucket `product-intelligence` exists and is private
- [ ] M3 schema migration applied (`product_images`, `batch_contact_sheets`)
- [ ] M4 alignment migration applied (`internal_sku`, `product_source_items`)
- [ ] `OBSIDIAN_SUPPLIER_PATH` points to a valid, readable directory
- [ ] Docker file sharing enabled for the inbox path's drive
- [ ] n8n workflow imported and reviewed (still `active: false`)
- [ ] `docker compose -f compose.yml -f compose.override.yml up -d` succeeds
- [ ] `curl http://intake:8000/healthz` returns `{"status":"ok",...}`
- [ ] Test suite passes: `pytest tests/` (109 tests)
