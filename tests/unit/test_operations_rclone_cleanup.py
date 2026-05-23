"""Unit tests for /api/ops/rclone/* and /api/ops/cleanup/* endpoints (Task 3b).

All external side-effects (rclone remote access, real session rollbacks,
MovieClaim coordinator) are mocked — no real network calls or DB mutations
beyond the isolated SQLite fixture.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures (mirror the pattern from test_operations_endpoints.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(_isolate_sqlite):
    from apps.api.services.runtime import app, _jwt_encode

    token = _jwt_encode({"sub": "admin", "role": "admin", "typ": "access"}, 3600)
    csrf = "test-csrf-value"
    client = TestClient(app, cookies={"csrf_token": csrf})
    client.headers.update(
        {"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf}
    )
    return client


@pytest.fixture
def readonly_client(_isolate_sqlite):
    from apps.api.services.runtime import app, _jwt_encode

    token = _jwt_encode({"sub": "viewer", "role": "readonly", "typ": "access"}, 3600)
    csrf = "test-csrf-value"
    client = TestClient(app, cookies={"csrf_token": csrf})
    client.headers.update(
        {"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf}
    )
    return client


@pytest.fixture
def anon_client(_isolate_sqlite):
    from apps.api.services.runtime import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# Rclone — GET /api/ops/rclone/last
# ---------------------------------------------------------------------------


class TestRcloneLast:
    def test_empty_db_returns_200_with_zeroed_counts(self, admin_client):
        """Empty DB → 200 with all counts zero and last_scan_time None."""
        resp = admin_client.get("/api/ops/rclone/last")
        assert resp.status_code == 200
        body = resp.json()
        assert body["inventory_count"] == 0
        assert body["last_scan_time"] is None
        assert body["dedup_pending"] == 0
        assert body["dedup_completed"] == 0
        assert body["total_freed_bytes"] == 0

    def test_returns_seeded_inventory_count(self, admin_client):
        """Seeded RcloneInventory rows appear in inventory_count."""
        from javdb.storage.db import get_db, OPERATIONS_DB_PATH

        with get_db(OPERATIONS_DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO RcloneInventory
                (VideoCode, SensorCategory, SubtitleCategory, FolderPath,
                 FolderSize, FileCount, DateTimeScanned)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("ABC-001", "Sensor", "Sub", "2025/Actor/ABC-001/v1",
                 1073741824, 3, "2025-01-15 10:00:00"),
            )
            conn.execute(
                """
                INSERT INTO RcloneInventory
                (VideoCode, SensorCategory, SubtitleCategory, FolderPath,
                 FolderSize, FileCount, DateTimeScanned)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("DEF-002", "Sensor", "Sub", "2025/Actor2/DEF-002/v1",
                 2147483648, 5, "2025-01-20 12:00:00"),
            )

        resp = admin_client.get("/api/ops/rclone/last")
        assert resp.status_code == 200
        body = resp.json()
        assert body["inventory_count"] == 2
        assert body["last_scan_time"] == "2025-01-20 12:00:00"

    def test_dedup_counts_and_freed_bytes(self, admin_client):
        """Seeded DedupRecords are counted correctly."""
        from javdb.storage.db import get_db, OPERATIONS_DB_PATH

        with get_db(OPERATIONS_DB_PATH) as conn:
            # pending (IsDeleted=0)
            conn.execute(
                """
                INSERT INTO DedupRecords
                (VideoCode, ExistingGdrivePath, ExistingFolderSize, IsDeleted)
                VALUES (?, ?, ?, ?)
                """,
                ("ABC-001", "2025/Actor/ABC-001/old", 500_000_000, 0),
            )
            # completed (IsDeleted=1)
            conn.execute(
                """
                INSERT INTO DedupRecords
                (VideoCode, ExistingGdrivePath, ExistingFolderSize, IsDeleted)
                VALUES (?, ?, ?, ?)
                """,
                ("DEF-002", "2025/Actor2/DEF-002/old", 1_000_000_000, 1),
            )
            conn.execute(
                """
                INSERT INTO DedupRecords
                (VideoCode, ExistingGdrivePath, ExistingFolderSize, IsDeleted)
                VALUES (?, ?, ?, ?)
                """,
                ("GHI-003", "2025/Actor3/GHI-003/old", 2_000_000_000, 1),
            )

        resp = admin_client.get("/api/ops/rclone/last")
        assert resp.status_code == 200
        body = resp.json()
        assert body["dedup_pending"] == 1
        assert body["dedup_completed"] == 2
        assert body["total_freed_bytes"] == 3_000_000_000

    def test_readonly_user_can_access(self, readonly_client):
        """Readonly users can access GET /api/ops/rclone/last."""
        resp = readonly_client.get("/api/ops/rclone/last")
        assert resp.status_code == 200

    def test_unauthenticated_rejected(self, anon_client):
        """Unauthenticated requests are rejected."""
        resp = anon_client.get("/api/ops/rclone/last")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Rclone — POST /api/ops/rclone/run
# ---------------------------------------------------------------------------


class TestRcloneRun:
    def test_valid_scan_report_dry_run_returns_200(self, admin_client):
        """POST with scan=True, report=True, dry_run=True → 200."""
        mock_result = {
            "phase_results": {
                "scan": {"exit_code": 0, "total_rows": 100, "error_count": 0},
                "report": {"exit_code": 0},
            },
            "dry_run": True,
        }
        with patch(
            "javdb.integrations.rclone.manager.run_rclone_manager",
            return_value=mock_result,
        ):
            resp = admin_client.post(
                "/api/ops/rclone/run",
                json={"scan": True, "report": True, "execute": False, "dry_run": True},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["dry_run"] is True
        assert "scan" in body["phase_results"]
        assert "report" in body["phase_results"]

    def test_execute_without_report_returns_422(self, admin_client):
        """execute=True with report=False → 422 validation error."""
        resp = admin_client.post(
            "/api/ops/rclone/run",
            json={"scan": False, "report": False, "execute": True, "dry_run": True},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["detail"]["error"]["code"] == "ops.rclone.invalid_flags"

    def test_execute_with_scan_but_no_report_returns_422(self, admin_client):
        """execute=True, scan=True, report=False → 422 (scan doesn't excuse missing report)."""
        resp = admin_client.post(
            "/api/ops/rclone/run",
            json={"scan": True, "report": False, "execute": True, "dry_run": True},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["detail"]["error"]["code"] == "ops.rclone.invalid_flags"

    def test_nothing_to_do_returns_422(self, admin_client):
        """scan=False, report=False, execute=False → 422."""
        resp = admin_client.post(
            "/api/ops/rclone/run",
            json={"scan": False, "report": False, "execute": False, "dry_run": True},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["detail"]["error"]["code"] == "ops.rclone.nothing_to_do"

    def test_manager_runtime_error_returns_500(self, admin_client):
        """RuntimeError from run_rclone_manager → 500."""
        with patch(
            "javdb.integrations.rclone.manager.run_rclone_manager",
            side_effect=RuntimeError("No remote configured"),
        ):
            resp = admin_client.post(
                "/api/ops/rclone/run",
                json={"scan": True, "report": True, "execute": False, "dry_run": True},
            )
        assert resp.status_code == 500
        body = resp.json()
        assert body["detail"]["error"]["code"] == "ops.rclone.failed"

    def test_requires_admin_role(self, readonly_client):
        """Readonly users get 403 on admin-only endpoint."""
        resp = readonly_client.post(
            "/api/ops/rclone/run",
            json={"scan": True, "report": True, "execute": False, "dry_run": True},
        )
        assert resp.status_code in (401, 403)

    def test_unauthenticated_rejected(self, anon_client):
        """Unauthenticated requests are rejected."""
        resp = anon_client.post(
            "/api/ops/rclone/run",
            json={"scan": True, "report": True, "execute": False, "dry_run": True},
        )
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Cleanup — POST /api/ops/cleanup/stale-sessions
# ---------------------------------------------------------------------------


class TestCleanupStaleSessions:
    def test_dry_run_returns_200_with_dry_run_true(self, admin_client):
        """POST dry_run=True → 200 with dry_run=True in response."""
        mock_result = {
            "sessions_found": 2,
            "sessions_cleaned": 0,
            "sessions_failed": 0,
            "dry_run": True,
            "details": [
                {"session_id": "sess-001", "status": "in_progress", "action": "rollback", "would_apply": True},
                {"session_id": "sess-002", "status": "finalizing", "action": "resume_commit", "would_apply": True},
            ],
        }
        with patch(
            "apps.cli.db.cleanup_stale_in_progress.run_stale_cleanup",
            return_value=mock_result,
        ):
            resp = admin_client.post(
                "/api/ops/cleanup/stale-sessions",
                json={"older_than_hours": 48.0, "dry_run": True},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["dry_run"] is True
        assert body["sessions_found"] == 2
        assert body["sessions_cleaned"] == 0
        assert len(body["details"]) == 2

    def test_invalid_scope_returns_422(self, admin_client):
        """A scope outside the allowed enum is rejected before the handler runs."""
        resp = admin_client.post(
            "/api/ops/cleanup/stale-sessions",
            json={"scope": "bogus"},
        )
        assert resp.status_code == 422

    def test_non_positive_older_than_hours_returns_422(self, admin_client):
        """older_than_hours must be strictly greater than 0."""
        resp = admin_client.post(
            "/api/ops/cleanup/stale-sessions",
            json={"older_than_hours": -1},
        )
        assert resp.status_code == 422

    def test_apply_returns_200_with_sessions_cleaned(self, admin_client):
        """POST dry_run=False → 200 with sessions_cleaned count."""
        mock_result = {
            "sessions_found": 1,
            "sessions_cleaned": 1,
            "sessions_failed": 0,
            "dry_run": False,
            "details": [
                {"session_id": "sess-001", "status": "in_progress", "action": "rollback", "counts": {}},
            ],
        }
        with patch(
            "apps.cli.db.cleanup_stale_in_progress.run_stale_cleanup",
            return_value=mock_result,
        ):
            resp = admin_client.post(
                "/api/ops/cleanup/stale-sessions",
                json={"older_than_hours": 24.0, "dry_run": False},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["dry_run"] is False
        assert body["sessions_found"] == 1
        assert body["sessions_cleaned"] == 1
        assert body["sessions_failed"] == 0

    def test_empty_result_returns_200(self, admin_client):
        """No stale sessions → 200 with zeroed counts."""
        mock_result = {
            "sessions_found": 0,
            "sessions_cleaned": 0,
            "sessions_failed": 0,
            "dry_run": True,
            "details": [],
        }
        with patch(
            "apps.cli.db.cleanup_stale_in_progress.run_stale_cleanup",
            return_value=mock_result,
        ):
            resp = admin_client.post(
                "/api/ops/cleanup/stale-sessions",
                json={"dry_run": True},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["sessions_found"] == 0
        assert body["details"] == []

    def test_db_error_returns_500(self, admin_client):
        """RuntimeError from run_stale_cleanup → 500."""
        with patch(
            "apps.cli.db.cleanup_stale_in_progress.run_stale_cleanup",
            side_effect=RuntimeError("Failed to init DB: connection refused"),
        ):
            resp = admin_client.post(
                "/api/ops/cleanup/stale-sessions",
                json={"dry_run": True},
            )
        assert resp.status_code == 500
        body = resp.json()
        assert body["detail"]["error"]["code"] == "ops.cleanup.stale_failed"

    def test_requires_admin_role(self, readonly_client):
        """Readonly users get 403 on admin-only endpoint."""
        resp = readonly_client.post(
            "/api/ops/cleanup/stale-sessions",
            json={"dry_run": True},
        )
        assert resp.status_code in (401, 403)

    def test_unauthenticated_rejected(self, anon_client):
        """Unauthenticated requests are rejected."""
        resp = anon_client.post(
            "/api/ops/cleanup/stale-sessions",
            json={"dry_run": True},
        )
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Cleanup — POST /api/ops/cleanup/claim-stages
# ---------------------------------------------------------------------------


class TestCleanupClaimStages:
    def test_returns_200_with_zero_counts_when_not_configured(self, admin_client):
        """When coordinator is not configured → 200 with zeroed counts."""
        mock_result = {
            "shards_processed": 0,
            "stages_reaped": 0,
            "details": [],
        }
        with patch(
            "apps.cli.db.sweep_claim_stages.run_claim_stage_sweep",
            return_value=mock_result,
        ):
            resp = admin_client.post(
                "/api/ops/cleanup/claim-stages",
                json={},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["shards_processed"] == 0
        assert body["stages_reaped"] == 0
        assert body["details"] == []

    def test_returns_200_with_shard_results(self, admin_client):
        """Successful sweep → 200 with shard counts."""
        mock_result = {
            "shards_processed": 3,
            "stages_reaped": 12,
            "details": [
                {"shard_date": "2026-05-21", "removed": 5, "cutoff_ms": 21600000},
                {"shard_date": "2026-05-20", "removed": 4, "cutoff_ms": 21600000},
                {"shard_date": "2026-05-19", "removed": 3, "cutoff_ms": 21600000},
            ],
        }
        with patch(
            "apps.cli.db.sweep_claim_stages.run_claim_stage_sweep",
            return_value=mock_result,
        ):
            resp = admin_client.post(
                "/api/ops/cleanup/claim-stages",
                json={"older_than_hours": 6.0},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["shards_processed"] == 3
        assert body["stages_reaped"] == 12
        assert len(body["details"]) == 3

    def test_custom_shard_dates_passed_through(self, admin_client):
        """shard_dates from request body are passed to run_claim_stage_sweep."""
        captured = {}

        def fake_sweep(shard_dates=None, older_than_hours=6.0):
            captured["shard_dates"] = shard_dates
            return {"shards_processed": 0, "stages_reaped": 0, "details": []}

        with patch(
            "apps.cli.db.sweep_claim_stages.run_claim_stage_sweep",
            side_effect=fake_sweep,
        ):
            resp = admin_client.post(
                "/api/ops/cleanup/claim-stages",
                json={"shard_dates": ["2026-05-01", "2026-05-02"]},
            )
        assert resp.status_code == 200
        assert captured["shard_dates"] == ["2026-05-01", "2026-05-02"]

    def test_requires_admin_role(self, readonly_client):
        """Readonly users get 403 on admin-only endpoint."""
        resp = readonly_client.post(
            "/api/ops/cleanup/claim-stages",
            json={},
        )
        assert resp.status_code in (401, 403)

    def test_unauthenticated_rejected(self, anon_client):
        """Unauthenticated requests are rejected."""
        resp = anon_client.post(
            "/api/ops/cleanup/claim-stages",
            json={},
        )
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Direct helper validation — run_rclone_manager guard
# ---------------------------------------------------------------------------


class TestRunRcloneManagerValidation:
    """Verify run_rclone_manager raises ValueError for invalid flag combos.

    These tests call the helper directly (no HTTP layer) to confirm the
    stricter guard added for Violation 2 of the spec review.
    """

    def test_execute_with_scan_no_report_raises(self):
        """execute=True, scan=True, report=False must raise ValueError."""
        from javdb.integrations.rclone.manager import run_rclone_manager

        with pytest.raises(ValueError, match="execute=True requires report=True"):
            run_rclone_manager(scan=True, report=False, execute=True, dry_run=True)

    def test_execute_no_scan_no_report_raises(self):
        """execute=True, scan=False, report=False must raise ValueError."""
        from javdb.integrations.rclone.manager import run_rclone_manager

        with pytest.raises(ValueError, match="execute=True requires report=True"):
            run_rclone_manager(scan=False, report=False, execute=True, dry_run=True)

    def test_nothing_to_do_raises(self):
        """scan=False, report=False, execute=False must raise ValueError."""
        from javdb.integrations.rclone.manager import run_rclone_manager

        with pytest.raises(ValueError, match="At least one of"):
            run_rclone_manager(scan=False, report=False, execute=False, dry_run=True)
