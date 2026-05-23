"""Unit tests for /api/ops/* endpoints (Phase 2, Task 3a).

All external integrations (qBittorrent, PikPak, SMTP) are mocked — no real
network calls are made.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
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
# qBittorrent — qb/torrents
# ---------------------------------------------------------------------------


class TestQbTorrents:
    def test_returns_200_with_torrent_list(self, admin_client):
        """GET /api/ops/qb/torrents → 200 with items and total."""
        fake_torrents = [
            {
                "hash": "abc123",
                "name": "Movie A",
                "size": 1073741824,
                "progress": 1.0,
                "state": "seeding",
                "category": "JavDB",
                "added_on": 1700000000,
                "completion_on": 1700001000,
            },
            {
                "hash": "def456",
                "name": "Movie B",
                "size": 2147483648,
                "progress": 0.5,
                "state": "downloading",
                "category": "JavDB",
                "added_on": 1700002000,
                "completion_on": 0,
            },
        ]
        mock_qb = MagicMock()
        mock_qb.get_torrents.return_value = fake_torrents

        with patch("javdb.integrations.qb.client.QBittorrentClient", return_value=mock_qb):
            resp = admin_client.get("/api/ops/qb/torrents")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert len(body["items"]) == 2
        assert body["items"][0]["hash"] == "abc123"
        assert body["items"][0]["name"] == "Movie A"
        assert body["items"][1]["hash"] == "def456"

    def test_qb_unreachable_returns_502(self, admin_client):
        """Connection failure in QBittorrentClient constructor → 502."""
        with patch(
            "javdb.integrations.qb.client.QBittorrentClient",
            side_effect=Exception("Connection refused"),
        ):
            resp = admin_client.get("/api/ops/qb/torrents")

        assert resp.status_code == 502
        body = resp.json()
        assert body["detail"]["error"]["code"] == "ops.qb.unreachable"

    def test_readonly_user_can_read(self, readonly_client):
        """Readonly users can access GET /api/ops/qb/torrents."""
        mock_qb = MagicMock()
        mock_qb.get_torrents.return_value = []

        with patch("javdb.integrations.qb.client.QBittorrentClient", return_value=mock_qb):
            resp = readonly_client.get("/api/ops/qb/torrents")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["items"] == []


# ---------------------------------------------------------------------------
# qBittorrent — qb/filter-small
# ---------------------------------------------------------------------------


class TestQbFilterSmall:
    def test_dry_run_returns_200_with_dry_run_true(self, admin_client):
        """POST /api/ops/qb/filter-small dry_run=True returns 200 with dry_run echoed."""
        mock_result = {
            "filtered_count": 3,
            "torrents_scanned": 5,
            "dry_run": True,
            "details": [
                {
                    "torrent_name": "Fake Torrent",
                    "files_filtered": 3,
                    "size_saved_bytes": 1024,
                    "local_files_deleted": 0,
                    "local_size_deleted_bytes": 0,
                }
            ],
        }
        with patch(
            "javdb.integrations.qb.file_filter.run_file_filter",
            return_value=mock_result,
        ):
            resp = admin_client.post(
                "/api/ops/qb/filter-small",
                json={"min_size_mb": 100.0, "days": 2, "dry_run": True},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["dry_run"] is True
        assert body["filtered_count"] == 3
        assert body["torrents_scanned"] == 5
        assert len(body["details"]) == 1

    def test_qb_unreachable_raises_502(self, admin_client):
        """RuntimeError with 'connect' in message → 502."""
        with patch(
            "javdb.integrations.qb.file_filter.run_file_filter",
            side_effect=RuntimeError("Cannot connect to qBittorrent"),
        ):
            resp = admin_client.post(
                "/api/ops/qb/filter-small",
                json={"min_size_mb": 100.0, "days": 2, "dry_run": True},
            )
        assert resp.status_code == 502

    def test_requires_admin_role(self, readonly_client):
        """Readonly users get 403 on admin-only endpoint."""
        resp = readonly_client.post(
            "/api/ops/qb/filter-small",
            json={"min_size_mb": 100.0, "days": 2, "dry_run": True},
        )
        assert resp.status_code in (401, 403)

    def test_unauthenticated_gets_401_or_403(self, anon_client):
        """Anonymous requests to admin endpoint are rejected."""
        resp = anon_client.post(
            "/api/ops/qb/filter-small",
            json={"min_size_mb": 100.0, "days": 2, "dry_run": True},
        )
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# PikPak — pikpak/transfer
# ---------------------------------------------------------------------------


class TestPikPakTransfer:
    def test_dry_run_returns_200(self, admin_client):
        """POST /api/ops/pikpak/transfer dry_run=True → 200 with unknown counts."""
        with patch(
            "javdb.integrations.pikpak.bridge.pikpak_bridge",
            return_value=None,
        ):
            resp = admin_client.post(
                "/api/ops/pikpak/transfer",
                json={"days": 7, "dry_run": True},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["dry_run"] is True
        # pikpak_bridge returns None; counts are unknown and must be None
        assert body["transferred"] is None
        assert body["failed"] is None
        assert body["skipped"] is None

    def test_pikpak_error_returns_500(self, admin_client):
        """Exception from pikpak_bridge → 500."""
        with patch(
            "javdb.integrations.pikpak.bridge.pikpak_bridge",
            side_effect=RuntimeError("PikPak API down"),
        ):
            resp = admin_client.post(
                "/api/ops/pikpak/transfer",
                json={"days": 7, "dry_run": True},
            )
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# PikPak — pikpak/queue
# ---------------------------------------------------------------------------


class TestPikPakQueue:
    def test_empty_db_returns_200_with_empty_items(self, admin_client):
        """GET /api/ops/pikpak/queue on empty DB returns 200 and items=[]."""
        resp = admin_client.get("/api/ops/pikpak/queue")
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert isinstance(body["items"], list)
        assert body["total"] == 0

    def test_returns_seeded_rows(self, admin_client):
        """Rows inserted into PikpakHistory appear in GET /api/ops/pikpak/queue."""
        import javdb.storage.db.db_connection as _conn_mod

        # Insert a row directly via SQL using the isolated DB path.
        with _conn_mod.get_db(_conn_mod.OPERATIONS_DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO PikpakHistory
                (TorrentHash, TorrentName, Category, TransferStatus, DateTimeAddedToQb)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("abc123", "Test Movie", "Daily", "transferred", "2026-01-01T00:00:00Z"),
            )

        resp = admin_client.get("/api/ops/pikpak/queue")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["torrent_hash"] == "abc123"
        assert item["torrent_name"] == "Test Movie"
        assert item["datetime_added_to_qb"] == "2026-01-01T00:00:00Z"

    def test_readonly_user_can_read(self, readonly_client):
        """Readonly users can access GET /api/ops/pikpak/queue."""
        resp = readonly_client.get("/api/ops/pikpak/queue")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Email — email/history
# ---------------------------------------------------------------------------


class TestEmailHistory:
    def test_empty_db_returns_200(self, admin_client):
        """GET /api/ops/email/history on empty DB returns 200 with empty items."""
        resp = admin_client.get("/api/ops/email/history")
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert body["items"] == []
        assert body.get("next_cursor") is None

    def test_returns_seeded_rows(self, admin_client):
        """Rows from OperationsRepo.append_email_history appear in GET."""
        from javdb.storage.repos.operations_repo import OperationsRepo

        repo = OperationsRepo()
        repo.append_email_history(
            session_id="sess-001",
            recipient="ops@example.com",
            subject="Daily Report",
            status="sent",
        )

        resp = admin_client.get("/api/ops/email/history")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 1
        item = body["items"][0]
        assert item["subject"] == "Daily Report"
        assert item["status"] == "sent"
        assert item["session_id"] == "sess-001"

    def test_readonly_user_can_read(self, readonly_client):
        """Readonly users can access GET /api/ops/email/history."""
        resp = readonly_client.get("/api/ops/email/history")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Email — email/{id}/resend
# ---------------------------------------------------------------------------


class TestEmailResend:
    def test_missing_id_returns_404(self, admin_client):
        """POST /api/ops/email/9999/resend on missing id returns 404."""
        resp = admin_client.post("/api/ops/email/9999/resend")
        assert resp.status_code == 404

    def test_resend_success(self, admin_client):
        """Successful resend returns 200 with status='resent'."""
        from javdb.storage.repos.operations_repo import OperationsRepo

        repo = OperationsRepo()
        repo.append_email_history(
            session_id=None,
            recipient="ops@example.com",
            subject="Alert",
            status="failed",
            error="SMTP timeout",
        )
        rows, _ = repo.list_email_history()
        record_id = rows[0]["Id"]

        with patch(
            "javdb.integrations.notify.email.send_email",
            return_value=True,
        ):
            resp = admin_client.post(f"/api/ops/email/{record_id}/resend")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "resent"
        assert body["id"] == record_id

    def test_resend_requires_admin(self, readonly_client):
        """Readonly user cannot resend email."""
        resp = readonly_client.post("/api/ops/email/1/resend")
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Email — email/test
# ---------------------------------------------------------------------------


class TestEmailTest:
    def test_send_test_email_success(self, admin_client):
        """POST /api/ops/email/test → 200 when send_email returns True."""
        with patch(
            "javdb.integrations.notify.email.send_email",
            return_value=True,
        ):
            resp = admin_client.post("/api/ops/email/test", json={})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "sent"

    def test_send_test_email_failure_returns_500(self, admin_client):
        """POST /api/ops/email/test → 500 when send_email returns False."""
        with patch(
            "javdb.integrations.notify.email.send_email",
            return_value=False,
        ):
            resp = admin_client.post("/api/ops/email/test", json={})
        assert resp.status_code == 500

    def test_send_test_email_exception_returns_500(self, admin_client):
        """POST /api/ops/email/test → 500 when send_email raises."""
        with patch(
            "javdb.integrations.notify.email.send_email",
            side_effect=OSError("SMTP refused"),
        ):
            resp = admin_client.post("/api/ops/email/test", json={})
        assert resp.status_code == 500

    def test_requires_admin_role(self, readonly_client):
        """Readonly user cannot send test email."""
        resp = readonly_client.post("/api/ops/email/test", json={})
        assert resp.status_code in (401, 403)
