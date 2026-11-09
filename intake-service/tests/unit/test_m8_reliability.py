"""Focused tests for M8: Reliability Hardening.

Validates:
- Bounded retries with retry_count tracking (reuses M6 MAX_RETRIES tuning)
- Dead-letter handling when retries are exhausted
- Stale-lock recovery (job stuck running/pending beyond TTL)
- Concurrency guard (no duplicate execution of the same batch)
- Permanent failure remains diagnosable + recoverable
- Idempotent reprocessing (no duplicate jobs)
"""
import os
import sys
import types
from datetime import datetime, timedelta
from uuid import UUID, uuid4

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from fastapi import BackgroundTasks, HTTPException

from src.core.storage.supabase import (
    MAX_RETRIES,
    STALE_LOCK_TTL_SECONDS,
    get_job_retry_count,
    is_job_stale_lock,
)


# ---------------------------------------------------------------------------
# Mock storage (in-memory stand-in for SupabaseStorage)
# ---------------------------------------------------------------------------
class MockStorage:
    def __init__(self):
        self.packages = {}   # external batch_id -> package dict (keyed by external id)
        self.jobs = {}       # job_id -> job dict
        self.update_calls = []  # (table, id, updates) log for assertions

    def _new_job_id(self):
        return str(uuid4())

    # ---- Package ----
    async def get_package_by_id(self, batch_id):
        return self.packages.get(batch_id)

    async def update_package(self, package_id, updates):
        pkg = self.packages.get(package_id)
        if pkg is None:
            pkg = {}
            self.packages[package_id] = pkg
        pkg.update(updates)
        self.update_calls.append(("packages", package_id, dict(updates)))
        return pkg

    async def get_package_items(self, package_internal_id):
        return []

    # ---- Jobs ----
    async def create_job(self, job_data):
        job_id = self._new_job_id()
        job = dict(job_data)
        job["id"] = job_id
        if "created_at" not in job:
            job["created_at"] = datetime.utcnow().isoformat()
        self.jobs[job_id] = job
        self.update_calls.append(("processing_jobs", job_id, dict(job_data)))
        return job

    async def get_job(self, job_id):
        return self.jobs.get(str(job_id))

    async def update_job(self, job_id, updates):
        key = str(job_id)
        job = self.jobs.get(key)
        if job is None:
            raise Exception(f"Job not found: {job_id}")
        job.update(updates)
        self.update_calls.append(("processing_jobs", key, dict(updates)))
        return job

    async def get_latest_job_for_batch(self, batch_id):
        package = await self.get_package_by_id(batch_id)
        if not package:
            return None
        internal = package["id"]
        candidates = [j for j in self.jobs.values() if j.get("batch_id") == internal]
        if not candidates:
            return None
        return sorted(candidates, key=lambda j: j.get("created_at", ""), reverse=True)[0]

    async def get_active_job_for_batch(self, batch_id):
        package = await self.get_package_by_id(batch_id)
        if not package:
            return None
        internal = package["id"]
        candidates = [
            j for j in self.jobs.values()
            if j.get("batch_id") == internal and j.get("status") in ("pending", "running")
        ]
        if not candidates:
            return None
        return sorted(candidates, key=lambda j: j.get("created_at", ""), reverse=True)[0]

    async def increment_job_retry(self, job_id, max_retries=MAX_RETRIES):
        job = await self.get_job(job_id)
        if not job:
            return False
        params = job.get("input_params") or {}
        retry_count = get_job_retry_count(job) + 1
        params["retry_count"] = retry_count
        params["max_retries"] = max_retries
        await self.update_job(job_id, {"input_params": params})
        return retry_count < max_retries

    async def mark_job_dead_letter(self, job_id, error_message=None):
        job = await self.get_job(job_id)
        summary = dict(job.get("result_summary") or {}) if job else {}
        summary["dead_letter"] = True
        summary["dead_lettered_at"] = datetime.utcnow().isoformat()
        summary["retry_count"] = get_job_retry_count(job)
        updates = {
            "status": "failed",
            "completed_at": datetime.utcnow().isoformat(),
            "result_summary": summary,
        }
        if error_message is not None:
            updates["error_message"] = error_message
        return await self.update_job(job_id, updates)


def make_package(batch_id, internal_id=None):
    return {
        "id": internal_id or str(uuid4()),
        "batch_id": batch_id,
        "source_path": batch_id,
        "status": "discovered",
    }


# ---------------------------------------------------------------------------
# Storage helper tests (pure functions + MockStorage methods)
# ---------------------------------------------------------------------------
class TestJobRetryCount:
    def test_default_retry_count_is_zero(self):
        assert get_job_retry_count(None) == 0
        assert get_job_retry_count({}) == 0
        assert get_job_retry_count({"input_params": {}}) == 0
        assert get_job_retry_count({"input_params": {"retry_count": 2}}) == 2

    def test_is_job_stale_lock_none_and_terminal(self):
        assert is_job_stale_lock(None) is False
        assert is_job_stale_lock({"status": "completed", "started_at": datetime.utcnow().isoformat()}) is False
        assert is_job_stale_lock({"status": "failed", "started_at": datetime.utcnow().isoformat()}) is False

    def test_is_job_stale_lock_recent_is_false(self):
        recent = datetime.utcnow() - timedelta(seconds=10)
        job = {"status": "running", "started_at": recent.isoformat()}
        assert is_job_stale_lock(job) is False

    def test_is_job_stale_lock_old_is_true(self):
        old = datetime.utcnow() - timedelta(seconds=STALE_LOCK_TTL_SECONDS + 60)
        job = {"status": "running", "started_at": old.isoformat()}
        assert is_job_stale_lock(job) is True

    def test_is_job_stale_lock_pending_old_is_true(self):
        old = datetime.utcnow() - timedelta(seconds=STALE_LOCK_TTL_SECONDS + 60)
        job = {"status": "pending", "started_at": old.isoformat()}
        assert is_job_stale_lock(job) is True

    @pytest.mark.asyncio
    async def test_increment_job_retry_remains(self):
        storage = MockStorage()
        job = await storage.create_job({
            "batch_id": "internal-1", "job_type": "process", "status": "pending",
            "input_params": {"retry_count": 0, "max_retries": MAX_RETRIES},
        })
        remains = await storage.increment_job_retry(UUID(job["id"]))
        assert remains is True
        updated = await storage.get_job(UUID(job["id"]))
        assert updated["input_params"]["retry_count"] == 1

    @pytest.mark.asyncio
    async def test_increment_job_retry_exhausted(self):
        storage = MockStorage()
        job = await storage.create_job({
            "batch_id": "internal-1", "job_type": "process", "status": "pending",
            "input_params": {"retry_count": MAX_RETRIES - 1, "max_retries": MAX_RETRIES},
        })
        remains = await storage.increment_job_retry(UUID(job["id"]))
        assert remains is False
        updated = await storage.get_job(UUID(job["id"]))
        assert updated["input_params"]["retry_count"] == MAX_RETRIES

    @pytest.mark.asyncio
    async def test_mark_job_dead_letter(self):
        storage = MockStorage()
        job = await storage.create_job({
            "batch_id": "internal-1", "job_type": "process", "status": "running",
            "input_params": {"retry_count": MAX_RETRIES},
            "result_summary": {},
        })
        result = await storage.mark_job_dead_letter(UUID(job["id"]), error_message="boom")
        assert result["status"] == "failed"
        assert result["result_summary"]["dead_letter"] is True
        assert result["error_message"] == "boom"
        assert "dead_lettered_at" in result["result_summary"]

    @pytest.mark.asyncio
    async def test_get_active_job_for_batch(self):
        storage = MockStorage()
        pkg = make_package("batch-x", internal_id="internal-x")
        storage.packages["batch-x"] = pkg
        await storage.create_job({
            "batch_id": "internal-x", "job_type": "process", "status": "completed",
            "input_params": {},
        })
        active = await storage.get_active_job_for_batch("batch-x")
        assert active is None

        await storage.create_job({
            "batch_id": "internal-x", "job_type": "process", "status": "running",
            "input_params": {},
        })
        active = await storage.get_active_job_for_batch("batch-x")
        assert active is not None
        assert active["status"] == "running"


# ---------------------------------------------------------------------------
# Routes: process_package_files retry / dead-letter
# ---------------------------------------------------------------------------
class TestProcessPackageFilesReliability:
    def _patch(self, storage, extraction_raises=None, inventory_raises=None):
        import src.api.routes as routes_mod
        import src.core.image_inventory as inv_mod

        class Ctx:
            async def __aenter__(self):
                return storage
            async def __aexit__(self, *args):
                pass

        original_get_supabase = routes_mod.get_supabase
        original_extraction = routes_mod.extraction_pipeline.process_package
        original_inventory = inv_mod.process_batch_image_inventory
        original_scan = routes_mod.BatchScanner.scan_packages

        async def mock_extraction(*args, **kwargs):
            if extraction_raises:
                raise extraction_raises
            return {"products_upserted": 1}

        async def mock_inventory(*args, **kwargs):
            if inventory_raises:
                raise inventory_raises
            return {"new_assets": 1}

        def mock_scan(self):
            return [types.SimpleNamespace(package_id="batch-x")]

        routes_mod.get_supabase = lambda: Ctx()
        routes_mod.extraction_pipeline.process_package = mock_extraction
        inv_mod.process_batch_image_inventory = mock_inventory
        routes_mod.BatchScanner.scan_packages = mock_scan

        def restore():
            routes_mod.get_supabase = original_get_supabase
            routes_mod.extraction_pipeline.process_package = original_extraction
            inv_mod.process_batch_image_inventory = original_inventory
            routes_mod.BatchScanner.scan_packages = original_scan

        return restore

    @pytest.mark.asyncio
    async def test_successful_processing_completes_job(self):
        from src.api.routes import process_package_files
        storage = MockStorage()
        internal = str(uuid4())
        storage.packages["batch-x"] = make_package("batch-x", internal_id=internal)
        job = await storage.create_job({
            "batch_id": internal, "job_type": "process", "status": "pending",
            "input_params": {"retry_count": 0, "max_retries": MAX_RETRIES},
            "started_at": datetime.utcnow().isoformat(),
        })

        restore = self._patch(storage)
        try:
            await process_package_files("batch-x", internal)
        finally:
            restore()

        job_after = await storage.get_job(UUID(job["id"]))
        assert job_after["status"] == "completed"
        assert job_after["result_summary"]["dead_letter"] is False
        pkg = storage.packages["batch-x"]
        assert pkg["status"] == "completed"

    @pytest.mark.asyncio
    async def test_retry_then_dead_letter(self):
        from src.api.routes import process_package_files
        storage = MockStorage()
        internal = str(uuid4())
        storage.packages["batch-x"] = make_package("batch-x", internal_id=internal)
        job = await storage.create_job({
            "batch_id": internal, "job_type": "process", "status": "pending",
            "input_params": {"retry_count": 0, "max_retries": MAX_RETRIES},
            "started_at": datetime.utcnow().isoformat(),
        })

        # Extraction always fails. Each process_package_files invocation = 1 attempt.
        # n8n re-triggers /process for pending (retryable) jobs up to MAX_RETRIES.
        restore = self._patch(storage, extraction_raises=Exception("transient boom"))
        try:
            for attempt in range(1, MAX_RETRIES + 1):
                await process_package_files("batch-x", internal)
                job_after = await storage.get_job(UUID(job["id"]))
                if attempt < MAX_RETRIES:
                    # Still retryable: back to pending, retry_count incremented
                    assert job_after["status"] == "pending", job_after
                    assert job_after["input_params"]["retry_count"] == attempt
                else:
                    # Exhausted: dead letter
                    assert job_after["status"] == "failed", job_after
                    assert job_after["result_summary"]["dead_letter"] is True
                    assert job_after["input_params"]["retry_count"] == MAX_RETRIES
        finally:
            restore()

        job_after = await storage.get_job(UUID(job["id"]))
        assert job_after["status"] == "failed"
        assert job_after["result_summary"]["dead_letter"] is True
        pkg = storage.packages["batch-x"]
        assert pkg["status"] == "failed"

    @pytest.mark.asyncio
    async def test_inventory_failure_is_non_fatal(self):
        from src.api.routes import process_package_files
        storage = MockStorage()
        internal = str(uuid4())
        storage.packages["batch-x"] = make_package("batch-x", internal_id=internal)
        job = await storage.create_job({
            "batch_id": internal, "job_type": "process", "status": "pending",
            "input_params": {"retry_count": 0, "max_retries": MAX_RETRIES},
            "started_at": datetime.utcnow().isoformat(),
        })

        # Inventory fails but extraction succeeds -> job still completes
        restore = self._patch(storage, inventory_raises=Exception("inventory down"))
        try:
            await process_package_files("batch-x", internal)
        finally:
            restore()

        job_after = await storage.get_job(UUID(job["id"]))
        assert job_after["status"] == "completed"


# ---------------------------------------------------------------------------
# Routes: process_batch concurrency guard + stale lock recovery
# ---------------------------------------------------------------------------
class TestProcessBatchConcurrency:
    def _patch_process_batch(self, storage):
        import src.api.routes as routes_mod

        class Ctx:
            async def __aenter__(self):
                return storage
            async def __aexit__(self, *args):
                pass

        original_get_supabase = routes_mod.get_supabase
        routes_mod.get_supabase = lambda: Ctx()

        def restore():
            routes_mod.get_supabase = original_get_supabase
        return restore

    @pytest.mark.asyncio
    async def test_no_duplicate_job_when_active(self):
        from src.api.routes import process_batch
        storage = MockStorage()
        internal = str(uuid4())
        storage.packages["batch-x"] = make_package("batch-x", internal_id=internal)
        # Pre-existing active job
        await storage.create_job({
            "batch_id": internal, "job_type": "process", "status": "running",
            "input_params": {"retry_count": 0, "max_retries": MAX_RETRIES},
            "started_at": datetime.utcnow().isoformat(),
        })
        jobs_before = len(storage.jobs)

        restore = self._patch_process_batch(storage)
        try:
            result = await process_batch("batch-x", BackgroundTasks())
        finally:
            restore()

        assert result.status == "running"
        assert len(storage.jobs) == jobs_before  # no new job created

    @pytest.mark.asyncio
    async def test_stale_lock_recovery_creates_new_job(self):
        from src.api.routes import process_batch
        storage = MockStorage()
        internal = str(uuid4())
        storage.packages["batch-x"] = make_package("batch-x", internal_id=internal)
        old = datetime.utcnow() - timedelta(seconds=STALE_LOCK_TTL_SECONDS + 60)
        stale_job = await storage.create_job({
            "batch_id": internal, "job_type": "process", "status": "running",
            "input_params": {"retry_count": 0, "max_retries": MAX_RETRIES},
            "started_at": old.isoformat(),
        })
        jobs_before = len(storage.jobs)

        restore = self._patch_process_batch(storage)
        try:
            result = await process_batch("batch-x", BackgroundTasks())
        finally:
            restore()

        # Stale job dead-lettered
        stale_after = await storage.get_job(UUID(stale_job["id"]))
        assert stale_after["status"] == "failed"
        assert stale_after["result_summary"]["dead_letter"] is True
        # New job created
        assert len(storage.jobs) == jobs_before + 1
        assert result.status == "pending"

    @pytest.mark.asyncio
    async def test_batch_not_found_returns_404(self):
        from src.api.routes import process_batch
        storage = MockStorage()
        restore = self._patch_process_batch(storage)
        try:
            with pytest.raises(HTTPException) as exc:
                await process_batch("does-not-exist", BackgroundTasks())
            assert exc.value.status_code == 404
        finally:
            restore()
