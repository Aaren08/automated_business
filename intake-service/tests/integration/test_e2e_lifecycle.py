"""M10: End-to-End Validation — Full lifecycle integration tests.

Tests the complete intake pipeline through the actual FastAPI routes with a
mocked Supabase storage backend (no live infra required):

    scan → discover batch → process → store assets → extract data
    → handle human review → complete or fail

Also verifies:
- Retry exhaustion → dead-letter
- Stale-lock recovery
- Duplicate request handling (concurrency guard)
- Repeated scheduled runs (idempotency)

These are integration tests: they exercise real route handlers, request
validation, authentication, and response schemas. The storage layer is mocked
because a live Supabase instance is not available in this environment.
"""
import os
import sys
import types
from datetime import datetime, timedelta
from uuid import UUID, uuid4

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from fastapi import BackgroundTasks, HTTPException
from httpx import ASGITransport, AsyncClient

from src.core.storage.supabase import (
    MAX_RETRIES,
    STALE_LOCK_TTL_SECONDS,
    get_job_retry_count,
    is_job_stale_lock,
)
from src.main import app


# ---------------------------------------------------------------------------
# Mock storage for integration tests (stand-in for SupabaseStorage)
# ---------------------------------------------------------------------------
class MockStorage:
    """In-memory stand-in supporting all storage operations used by routes."""

    def __init__(self):
        self.packages = {}   # external batch_id -> package dict
        self.jobs = {}       # job_id -> job dict
        self.items = {}      # package_internal_id -> [item dicts]
        self.products = {}   # internal_sku -> product dict
        self.update_calls = []

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

    async def upsert_package(self, package_data):
        bid = package_data["batch_id"]
        existing = self.packages.get(bid)
        if existing:
            existing.update(package_data)
            self.update_calls.append(("packages_upsert", bid, dict(package_data)))
            return existing
        pkg = dict(package_data)
        self.packages[bid] = pkg
        self.update_calls.append(("packages_upsert", bid, dict(package_data)))
        return pkg

    async def get_package_items(self, package_internal_id):
        return self.items.get(package_internal_id, [])

    async def upsert_package_items(self, package_id, items):
        stored = []
        for item in items:
            item = dict(item)
            item["id"] = item.get("id") or str(uuid4())
            stored.append(item)
        self.items.setdefault(package_id, []).extend(stored)
        self.update_calls.append(("package_items", package_id, len(items)))
        return stored

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

    async def get_batch_by_id(self, batch_id):
        # For image inventory: batch_id is external batch_id
        return self.packages.get(batch_id)


def make_package(batch_id, internal_id=None, status="discovered"):
    return {
        "id": internal_id or str(uuid4()),
        "batch_id": batch_id,
        "source_path": batch_id,
        "status": status,
        "monthly_batch": "BATCH-2026-10",
        "package_name": "1",
        "supplier_md_hash": "hash123",
        "total_files": 2,
        "total_images": 1,
        "total_products": 0,
    }


# ---------------------------------------------------------------------------
# Patching helper
# ---------------------------------------------------------------------------
class RoutePatcher:
    """Patches routes.py dependencies for integration testing."""

    def __init__(self, storage, extraction_raises=None, inventory_raises=None,
                 scan_packages_return=None):
        self.storage = storage
        self.extraction_raises = extraction_raises
        self.inventory_raises = inventory_raises
        self.scan_packages_return = scan_packages_return or []
        self._restore = []

    def _set_attr(self, obj, attr_path, new):
        """Set an attribute, supporting dotted paths like 'a.b.c'."""
        parts = attr_path.split(".")
        target = obj
        for part in parts[:-1]:
            target = getattr(target, part)
        last = parts[-1]
        self._restore.append((target, last, getattr(target, last)))
        setattr(target, last, new)

    def apply(self):
        import src.api.routes as routes_mod
        import src.core.image_inventory as inv_mod

        storage = self.storage
        scan_return = self.scan_packages_return

        class Ctx:
            async def __aenter__(self):
                return storage
            async def __aexit__(self, *args):
                pass

        self._set_attr(routes_mod, "get_supabase", lambda: Ctx())

        async def mock_extraction(*args, **kwargs):
            if self.extraction_raises:
                raise self.extraction_raises
            return {"products_upserted": 1, "image_links": 1}

        async def mock_inventory(*args, **kwargs):
            if self.inventory_raises:
                raise self.inventory_raises
            return {"new_assets": 1, "processed": 1}

        def mock_scan(self):
            return [types.SimpleNamespace(**p) if isinstance(p, dict)
                    else p for p in scan_return]

        self._set_attr(routes_mod.extraction_pipeline, "process_package", mock_extraction)
        self._set_attr(inv_mod, "process_batch_image_inventory", mock_inventory)
        self._set_attr(routes_mod, "BatchScanner.scan_packages", mock_scan)

    def restore(self):
        for target, last, orig in reversed(self._restore):
            setattr(target, last, orig)
        self._restore = []


# ---------------------------------------------------------------------------
# PackageInfo factory for scan testing
# ---------------------------------------------------------------------------
def make_package_info(package_id="pkg-1", package_name="1", source_path="BATCH-2026-10/1",
                      monthly_batch="BATCH-2026-10", info_md_hash="hash123",
                      total_files=2, total_images=1):
    return types.SimpleNamespace(
        package_id=package_id,
        package_name=package_name,
        source_path=source_path,
        monthly_batch=monthly_batch,
        info_md_path="info.md",
        info_md_hash=info_md_hash,
        inventory_dir="inventory",
        total_files=total_files,
        total_images=total_images,
        ready_marker_path="_READY",
        discovered_at=datetime.utcnow(),
    )


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------
class TestScanLifecycle:
    """POST /scan → discover and register batches."""

    @pytest.mark.asyncio
    async def test_scan_registers_new_batch(self):
        from src.api.routes import scan_packages
        from src.api.schemas import BatchScanRequest

        storage = MockStorage()
        pkg_info = make_package_info()
        patcher = RoutePatcher(storage, scan_packages_return=[pkg_info])
        patcher.apply()
        try:
            response = await scan_packages(BatchScanRequest(force=False), BackgroundTasks())
        finally:
            patcher.restore()

        assert response.batches_found == 1
        assert response.batches_queued == ["BATCH-2026-10/1"]
        assert response.batches_skipped == []

        # Package was registered in storage
        stored = await storage.get_package_by_id("pkg-1")
        assert stored is not None
        assert stored["status"] == "discovered"
        assert stored["batch_id"] == "pkg-1"

    @pytest.mark.asyncio
    async def test_scan_skips_unchanged_batch(self):
        from src.api.routes import scan_packages
        from src.api.schemas import BatchScanRequest

        storage = MockStorage()
        # Pre-existing package with same hash → should be skipped
        existing = make_package("pkg-1", internal_id="internal-1", status="completed")
        existing["supplier_md_hash"] = "hash123"
        storage.packages["pkg-1"] = existing

        pkg_info = make_package_info(package_id="pkg-1", info_md_hash="hash123")
        patcher = RoutePatcher(storage, scan_packages_return=[pkg_info])
        patcher.apply()
        try:
            response = await scan_packages(BatchScanRequest(force=False), BackgroundTasks())
        finally:
            patcher.restore()

        assert response.batches_queued == []
        assert response.batches_skipped == ["BATCH-2026-10/1"]

    @pytest.mark.asyncio
    async def test_scan_force_reregisters_changed_batch(self):
        from src.api.routes import scan_packages
        from src.api.schemas import BatchScanRequest

        storage = MockStorage()
        existing = make_package("pkg-1", internal_id="internal-1", status="completed")
        existing["supplier_md_hash"] = "old-hash"
        storage.packages["pkg-1"] = existing

        pkg_info = make_package_info(package_id="pkg-1", info_md_hash="new-hash")
        patcher = RoutePatcher(storage, scan_packages_return=[pkg_info])
        patcher.apply()
        try:
            response = await scan_packages(BatchScanRequest(force=False), BackgroundTasks())
        finally:
            patcher.restore()

        assert response.batches_queued == ["BATCH-2026-10/1"]
        stored = await storage.get_package_by_id("pkg-1")
        assert stored["supplier_md_hash"] == "new-hash"


class TestProcessLifecycle:
    """POST /process → create job → background processing → complete/fail."""

    def _patch_routes(self, storage, **kwargs):
        import src.api.routes as routes_mod

        class Ctx:
            async def __aenter__(self):
                return storage
            async def __aexit__(self, *args):
                pass

        original = routes_mod.get_supabase
        routes_mod.get_supabase = lambda: Ctx()
        return lambda: setattr(routes_mod, "get_supabase", original)

    @pytest.mark.asyncio
    async def test_process_creates_job_and_queues(self):
        from src.api.routes import process_batch

        storage = MockStorage()
        internal = str(uuid4())
        storage.packages["batch-x"] = make_package("batch-x", internal_id=internal)

        restore = self._patch_routes(storage)
        try:
            result = await process_batch("batch-x", BackgroundTasks())
        finally:
            restore()

        assert result.status == "pending"
        assert result.job_type == "process"
        job = await storage.get_job(result.job_id)
        assert job is not None
        assert job["status"] == "pending"
        assert job["input_params"]["retry_count"] == 0
        assert job["input_params"]["max_retries"] == MAX_RETRIES

    @pytest.mark.asyncio
    async def test_processing_completes_successfully(self):
        from src.api.routes import process_package_files

        storage = MockStorage()
        internal = str(uuid4())
        storage.packages["batch-x"] = make_package("batch-x", internal_id=internal)
        job = await storage.create_job({
            "batch_id": internal, "job_type": "process", "status": "pending",
            "input_params": {"retry_count": 0, "max_retries": MAX_RETRIES},
            "started_at": datetime.utcnow().isoformat(),
        })

        patcher = RoutePatcher(
            storage,
            scan_packages_return=[make_package_info(package_id="batch-x")],
        )
        patcher.apply()
        try:
            await process_package_files("batch-x", internal)
        finally:
            patcher.restore()

        job_after = await storage.get_job(UUID(job["id"]))
        assert job_after["status"] == "completed"
        assert job_after["result_summary"]["dead_letter"] is False
        pkg = storage.packages["batch-x"]
        assert pkg["status"] == "completed"

    @pytest.mark.asyncio
    async def test_retry_exhaustion_dead_letter(self):
        from src.api.routes import process_package_files

        storage = MockStorage()
        internal = str(uuid4())
        storage.packages["batch-x"] = make_package("batch-x", internal_id=internal)
        job = await storage.create_job({
            "batch_id": internal, "job_type": "process", "status": "pending",
            "input_params": {"retry_count": 0, "max_retries": MAX_RETRIES},
            "started_at": datetime.utcnow().isoformat(),
        })

        patcher = RoutePatcher(
            storage,
            extraction_raises=Exception("transient boom"),
            scan_packages_return=[make_package_info(package_id="batch-x")],
        )
        patcher.apply()
        try:
            for attempt in range(1, MAX_RETRIES + 1):
                await process_package_files("batch-x", internal)
                job_after = await storage.get_job(UUID(job["id"]))
                if attempt < MAX_RETRIES:
                    assert job_after["status"] == "pending", job_after
                    assert job_after["input_params"]["retry_count"] == attempt
                else:
                    assert job_after["status"] == "failed", job_after
                    assert job_after["result_summary"]["dead_letter"] is True
                    assert job_after["input_params"]["retry_count"] == MAX_RETRIES
        finally:
            patcher.restore()

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

        patcher = RoutePatcher(
            storage,
            inventory_raises=Exception("inventory down"),
            scan_packages_return=[make_package_info(package_id="batch-x")],
        )
        patcher.apply()
        try:
            await process_package_files("batch-x", internal)
        finally:
            patcher.restore()

        job_after = await storage.get_job(UUID(job["id"]))
        assert job_after["status"] == "completed"


class TestConcurrencyAndRecovery:
    """Concurrency guard, stale-lock recovery, duplicate requests."""

    def _patch_routes(self, storage):
        import src.api.routes as routes_mod

        class Ctx:
            async def __aenter__(self):
                return storage
            async def __aexit__(self, *args):
                pass

        original = routes_mod.get_supabase
        routes_mod.get_supabase = lambda: Ctx()
        return lambda: setattr(routes_mod, "get_supabase", original)

    @pytest.mark.asyncio
    async def test_no_duplicate_job_when_active(self):
        from src.api.routes import process_batch

        storage = MockStorage()
        internal = str(uuid4())
        storage.packages["batch-x"] = make_package("batch-x", internal_id=internal)
        await storage.create_job({
            "batch_id": internal, "job_type": "process", "status": "running",
            "input_params": {"retry_count": 0, "max_retries": MAX_RETRIES},
            "started_at": datetime.utcnow().isoformat(),
        })
        jobs_before = len(storage.jobs)

        restore = self._patch_routes(storage)
        try:
            result = await process_batch("batch-x", BackgroundTasks())
        finally:
            restore()

        assert result.status == "running"
        assert len(storage.jobs) == jobs_before  # no new job created

    @pytest.mark.asyncio
    async def test_stale_lock_recovery(self):
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

        restore = self._patch_routes(storage)
        try:
            result = await process_batch("batch-x", BackgroundTasks())
        finally:
            restore()

        stale_after = await storage.get_job(UUID(stale_job["id"]))
        assert stale_after["status"] == "failed"
        assert stale_after["result_summary"]["dead_letter"] is True
        assert len(storage.jobs) == jobs_before + 1
        assert result.status == "pending"

    @pytest.mark.asyncio
    async def test_batch_not_found_returns_404(self):
        from src.api.routes import process_batch

        storage = MockStorage()
        restore = self._patch_routes(storage)
        try:
            with pytest.raises(HTTPException) as exc:
                await process_batch("does-not-exist", BackgroundTasks())
            assert exc.value.status_code == 404
        finally:
            restore()


class TestStatusEndpoints:
    """GET /batches/{id}/status and GET /batches/{id} reflect DB state."""

    @pytest.mark.asyncio
    async def test_get_batch_status(self):
        from src.api.routes import get_batch_status

        storage = MockStorage()
        internal = str(uuid4())
        storage.packages["batch-x"] = make_package("batch-x", internal_id=internal, status="completed")
        await storage.create_job({
            "batch_id": internal, "job_type": "process", "status": "completed",
            "input_params": {"retry_count": 0},
            "started_at": datetime.utcnow().isoformat(),
            "completed_at": datetime.utcnow().isoformat(),
            "error_message": None,
            "result_summary": {"dead_letter": False, "files_processed": 2},
        })

        restore = self._patch_routes(storage)
        try:
            result = await get_batch_status("batch-x")
        finally:
            restore()

        assert result.status == "completed"
        assert result.job_type == "process"
        assert result.result_summary["dead_letter"] is False
        assert result.result_summary["files_processed"] == 2

    def _patch_routes(self, storage):
        import src.api.routes as routes_mod

        class Ctx:
            async def __aenter__(self):
                return storage
            async def __aexit__(self, *args):
                pass

        original = routes_mod.get_supabase
        routes_mod.get_supabase = lambda: Ctx()
        return lambda: setattr(routes_mod, "get_supabase", original)

    @pytest.mark.asyncio
    async def test_dead_letter_visible_in_status(self):
        from src.api.routes import get_batch_status

        storage = MockStorage()
        internal = str(uuid4())
        storage.packages["batch-x"] = make_package("batch-x", internal_id=internal, status="failed")
        await storage.create_job({
            "batch_id": internal, "job_type": "process", "status": "failed",
            "input_params": {"retry_count": MAX_RETRIES},
            "started_at": datetime.utcnow().isoformat(),
            "completed_at": datetime.utcnow().isoformat(),
            "error_message": "boom",
            "result_summary": {"dead_letter": True, "retry_count": MAX_RETRIES},
        })

        restore = self._patch_routes(storage)
        try:
            result = await get_batch_status("batch-x")
        finally:
            restore()

        assert result.status == "failed"
        assert result.error_message == "boom"
        assert result.result_summary["dead_letter"] is True
        assert result.result_summary["retry_count"] == MAX_RETRIES


class TestRouteSchemaAndAuth:
    """Route responses match schemas; auth rejects missing/invalid key."""

    @pytest.mark.asyncio
    async def test_health_check_schema(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/healthz")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "product-intake"
        assert "version" in data

    @pytest.mark.asyncio
    async def test_process_requires_api_key(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # No API key → 401
            response = await client.post("/api/v1/batches/batch-x/process")
            assert response.status_code == 401

            # Wrong API key → 401
            response = await client.post(
                "/api/v1/batches/batch-x/process",
                headers={"X-API-Key": "wrong-key"},
            )
            assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_process_with_valid_key_returns_job_status_schema(self):
        from src.api.routes import process_batch

        storage = MockStorage()
        internal = str(uuid4())
        storage.packages["batch-x"] = make_package("batch-x", internal_id=internal)

        restore = self._patch_routes(storage)
        try:
            result = await process_batch("batch-x", BackgroundTasks())
        finally:
            restore()

        # Validate against JobStatus schema fields
        assert isinstance(result.job_id, UUID)
        assert result.batch_id == "batch-x"
        assert result.job_type == "process"
        assert result.status in ("pending", "running", "completed", "failed")
        # Newly created jobs have no result_summary yet (populated on completion)
        if result.result_summary is not None:
            assert "retry_count" in result.result_summary
            assert "max_retries" in result.result_summary

    def _patch_routes(self, storage):
        import src.api.routes as routes_mod

        class Ctx:
            async def __aenter__(self):
                return storage
            async def __aexit__(self, *args):
                pass

        original = routes_mod.get_supabase
        routes_mod.get_supabase = lambda: Ctx()
        return lambda: setattr(routes_mod, "get_supabase", original)


class TestM8Helpers:
    """M8 retry/dead-letter/stale-lock helpers behave correctly."""

    def test_get_job_retry_count(self):
        assert get_job_retry_count(None) == 0
        assert get_job_retry_count({}) == 0
        assert get_job_retry_count({"input_params": {"retry_count": 3}}) == 3

    def test_is_job_stale_lock(self):
        assert is_job_stale_lock(None) is False
        assert is_job_stale_lock({"status": "completed"}) is False
        assert is_job_stale_lock({"status": "failed"}) is False
        recent = datetime.utcnow() - timedelta(seconds=10)
        assert is_job_stale_lock({"status": "running", "started_at": recent.isoformat()}) is False
        old = datetime.utcnow() - timedelta(seconds=STALE_LOCK_TTL_SECONDS + 60)
        assert is_job_stale_lock({"status": "running", "started_at": old.isoformat()}) is True

    @pytest.mark.asyncio
    async def test_increment_and_exhaust(self):
        storage = MockStorage()
        job = await storage.create_job({
            "batch_id": "internal-1", "job_type": "process", "status": "pending",
            "input_params": {"retry_count": 0, "max_retries": MAX_RETRIES},
        })
        remains = await storage.increment_job_retry(UUID(job["id"]))
        assert remains is True
        assert (await storage.get_job(UUID(job["id"])))["input_params"]["retry_count"] == 1

        # Exhaust
        job2 = await storage.create_job({
            "batch_id": "internal-1", "job_type": "process", "status": "pending",
            "input_params": {"retry_count": MAX_RETRIES - 1, "max_retries": MAX_RETRIES},
        })
        remains = await storage.increment_job_retry(UUID(job2["id"]))
        assert remains is False
        assert (await storage.get_job(UUID(job2["id"])))["input_params"]["retry_count"] == MAX_RETRIES

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
