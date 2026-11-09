# M9: n8n Reliability Integration — Product Intelligence Batch Monitor

## Overview

This n8n workflow automates the Product Intelligence intake pipeline and wires into the M8 reliability hardening (bounded retries, dead-lettering, concurrency guards, stale-lock recovery):

1. **Cron trigger** (every 30 minutes) discovers and registers new batches via `POST /scan`
2. Lists all batches via `GET /batches`
3. Routes each batch by status:
   - `completed` → notify success (terminal)
   - `failed` → notify dead-lettered (terminal per M8; n8n does **NOT** retry)
   - `awaiting_review` → fetch unmatched images → notify for human review
   - `processing` → fetch job status detail → poll again next cycle
   - `discovered` → `POST /process` to start processing
   - `pending` → `POST /process` to retry (M8 enforces retry limits)

## Files

| File | Purpose |
|------|---------|
| `workflows/product-intelligence-scan.json` | n8n workflow definition |
| `README.md` | This documentation |

## API Endpoints Used

All endpoints are existing FastAPI routes (no new endpoints added):

| Node | Endpoint | Method | Auth |
|------|----------|--------|------|
| Scan for Batches | `/api/v1/scan` | POST | `X-API-Key` |
| List All Batches | `/api/v1/batches` | GET | `X-API-Key` |
| Process Batch | `/api/v1/batches/{batch_id}/process` | POST | `X-API-Key` |
| Get Job Status Detail | `/api/v1/batches/{batch_id}/status` | GET | `X-API-Key` |
| Get Unmatched Images | `/api/v1/batches/{batch_id}/image-matches` | GET | `X-API-Key` |

Status values consumed (from `src/api/schemas.py`):
- Batch: `discovered`, `processing`, `completed`, `failed`, `awaiting_review`
- Job: `pending`, `running`, `completed`, `failed`

## Docker Network Configuration

The `intake` service is internal-only (no published ports) and reachable by n8n via the Compose service name:

```
INTAKE_URL=http://intake:8000
```

Both services share the default Compose network when run together:

```bash
docker compose -f compose.yml -f compose.override.yml up -d
```

## Environment Variables

Add to `.env` (already added):

```bash
# Intake service URL for n8n workflow (reachable via Docker service name)
INTAKE_URL=http://intake:8000
```

`INTAKE_API_KEY` is already present in `.env` and is used by the workflow's `X-API-Key` header via `{{ $env.INTAKE_API_KEY }}`.

## Workflow Logic

```
Cron Trigger (every 30 min)
    ├─▶ POST /scan          → register new/changed batches (idempotent)
    └─▶ GET /batches        → list all batches (splits into items)
        ├─ status = completed      → Notify: Batch Completed          (terminal)
        ├─ status = failed         → Notify: Batch Dead-Lettered      (terminal, no retry)
        ├─ status = awaiting_review→ GET /image-matches → Notify: Review Required
        ├─ status = processing     → GET /status → Status: Still Processing (poll next tick)
        ├─ status = discovered     → POST /process → Notify: Processing Started
        ├─ status = pending        → POST /process → Notify: Processing Started (M8 retry)
        └─ default                 → (dropped)
```

## Retry, Dead-Letter & Overlap Behavior (M8 integration)

- **Retry routing**: n8n calls `POST /process` only for `discovered` (first run) and `pending` (retry) batches. It never calls `/process` for `completed`, `failed`, or `awaiting_review`.
- **M8 enforces all limits**: Each `/process` call for a `pending` job is one attempt. M8's `increment_job_retry` increments `retry_count` (max `MAX_RETRIES=3`); when exhausted it dead-letters the job (`status=failed`, `result_summary.dead_letter=true`) and marks the batch `failed`. On the next tick n8n sees `failed` and notifies dead-lettered without retrying.
- **No endless requeue**: n8n does not track retry state itself (no second retry system). Once a job is dead-lettered (`failed`), n8n stops routing it to `/process`.
- **Concurrency guard**: M8's `/process` returns the existing job idempotently when one is `running` (non-stale), and recovers stale locks (TTL 30 min). n8n relies on this rather than implementing its own locking.
- **Bounded polling**: Each cron tick performs one scan and one batch list. No recursive polling. Terminal states produce a notification each tick they appear.
- **Duplicate workflow executions**: n8n cron creates independent executions per tick. Overlapping executions are harmless because all state changes go through M8's idempotent/guarded endpoints; they simply re-read the same Supabase state.
- **API errors/timeouts**: HTTP Request nodes use a 30s request timeout. A failing `/process` for one batch surfaces in that n8n execution; the batch stays in its current state and is re-evaluated on the next tick.

## Notifications

The `Set` nodes prepare a notification payload. Connect these nodes to your preferred channel:

- **Email**: n8n Email node (SMTP)
- **Slack**: n8n Slack node (webhook)
- **Webhook**: n8n HTTP Request node to your alert endpoint
- **n8n Chat**: n8n built-in chat notifications

Example payload (dead-lettered):
```json
{
  "batch_id": "abc123",
  "status": "failed",
  "error_message": "...",
  "notification_type": "batch_dead_lettered",
  "notified_at": "2026-10-18T..."
}
```

Example payload (processing started / retry):
```json
{
  "batch_id": "abc123",
  "status": "pending",
  "job_id": "...",
  "job_type": "process",
  "retry_count": 1,
  "notification_type": "processing_started",
  "notified_at": "2026-10-18T..."
}
```

## Human Review Routing

Batches in `awaiting_review` status have product images that were matched to image assets but not yet linked to a canonical product. The workflow fetches them via `GET /batches/{batch_id}/image-matches` and passes them to the notification node. A human reviewer can then:

1. Open the intake service API (or a future review UI)
2. Use `POST /batches/{batch_id}/image-matches/{item_id}/approve` with `product_id` to approve a match
3. Use `POST /batches/{batch_id}/image-matches/{item_id}/reject` to reject

## Importing the Workflow

1. Open n8n at `http://localhost:5678`
2. Go to **Workflows** → **Import**
3. Select `n8n/workflows/product-intelligence-scan.json`
4. The workflow is imported as **inactive** — review and activate it
5. Ensure `INTAKE_URL` and `INTAKE_API_KEY` are available as environment variables on the n8n container

## Manual Processing Trigger

If a batch is `discovered` or `pending` but not yet processing, trigger processing manually:

```bash
curl -X POST http://intake:8000/api/v1/batches/{batch_id}/process \
  -H "X-API-Key: ${INTAKE_API_KEY}"
```

For a `pending` (retryable) batch, this triggers another M8 retry attempt (bounded by `MAX_RETRIES`).

## Testing

Run the validation tests:

```bash
cd intake-service
pytest tests/unit/test_n8n_workflow.py -v
```

## Notes

- The workflow is imported `active: false`. Activate it after verifying connectivity.
- Credentials (`INTAKE_API_KEY`) are passed via environment variables, not hardcoded in the workflow.
- No new infrastructure is introduced — the workflow uses the existing n8n service and intake API.
- Supabase Storage (M6) is used by the intake service for image uploads; n8n does not interact with storage directly.
- End-to-end execution against a live n8n + Supabase stack has NOT been verified in this environment; the workflow structure, endpoint references, auth, and routing are validated by unit tests.
