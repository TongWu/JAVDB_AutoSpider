"""Unit tests for /api/migrations/* endpoints.

Tests cover:
- Admin-only access (readonly → 403, anon → 401)
- GET /api/migrations lists SQL files with applied state
- POST /api/migrations/{id}/run returns SQL preview in dry_run mode
- POST with dry_run=false returns 501
- 404 for non-existent migration
- Path traversal is blocked
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client():
    from apps.api.services.runtime import app, _jwt_encode

    token = _jwt_encode({"sub": "admin", "role": "admin", "typ": "access"}, 3600)
    csrf = "test-csrf"
    c = TestClient(app, cookies={"csrf_token": csrf})
    c.headers.update({"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf})
    return c


@pytest.fixture
def readonly_client():
    from apps.api.services.runtime import app, _jwt_encode

    token = _jwt_encode({"sub": "viewer", "role": "readonly", "typ": "access"}, 3600)
    csrf = "test-csrf"
    c = TestClient(app, cookies={"csrf_token": csrf})
    c.headers.update({"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf})
    return c


@pytest.fixture
def anon_client():
    from apps.api.services.runtime import app

    return TestClient(app)


@pytest.fixture
def migrations_dir(tmp_path):
    """Create a temp migrations directory with sample SQL files."""
    d = tmp_path / "d1"
    d.mkdir()
    (d / "0042_system_state_table.sql").write_text(
        "CREATE TABLE IF NOT EXISTS system_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
    )
    (d / "2026_05_04_add_rollback_columns_history.sql").write_text(
        "ALTER TABLE MovieHistory ADD COLUMN session_id TEXT;\n"
        "ALTER TABLE MovieHistory ADD COLUMN write_mode TEXT;"
    )
    return d


# ---------------------------------------------------------------------------
# TestListMigrations
# ---------------------------------------------------------------------------


class TestListMigrations:
    def test_admin_can_list(self, admin_client, migrations_dir, monkeypatch):
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        with patch(
            "apps.api.routers.migrations._get_applied_migrations",
            return_value={},
        ):
            resp = admin_client.get("/api/migrations/")
        assert resp.status_code == 200
        data = resp.json()
        assert "migrations" in data

    def test_readonly_returns_403(self, readonly_client, migrations_dir, monkeypatch):
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        resp = readonly_client.get("/api/migrations/")
        assert resp.status_code == 403

    def test_anon_returns_401(self, anon_client, migrations_dir, monkeypatch):
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        resp = anon_client.get("/api/migrations/")
        assert resp.status_code == 401

    def test_returns_migration_items(self, admin_client, migrations_dir, monkeypatch):
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        applied = {"0042_system_state_table": "2024-01-01 00:00:00"}
        with patch(
            "apps.api.routers.migrations._get_applied_migrations",
            return_value=applied,
        ):
            resp = admin_client.get("/api/migrations/")
        assert resp.status_code == 200
        items = resp.json()["migrations"]
        assert len(items) == 2

        # Sorted by filename — 0042 comes first
        item = items[0]
        assert item["id"] == "0042_system_state_table"
        assert item["filename"] == "0042_system_state_table.sql"
        assert item["applied"] is True
        assert item["applied_at"] == "2024-01-01 00:00:00"

        # Second item is not applied
        item2 = items[1]
        assert item2["id"] == "2026_05_04_add_rollback_columns_history"
        assert item2["applied"] is False
        assert item2["applied_at"] is None

    def test_empty_dir(self, admin_client, tmp_path, monkeypatch):
        import apps.api.routers.migrations as migrations_module

        non_existent = tmp_path / "does_not_exist"
        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", non_existent)
        with patch(
            "apps.api.routers.migrations._get_applied_migrations",
            return_value={},
        ):
            resp = admin_client.get("/api/migrations/")
        assert resp.status_code == 200
        assert resp.json()["migrations"] == []


# ---------------------------------------------------------------------------
# TestRunMigration
# ---------------------------------------------------------------------------


class TestRunMigration:
    def test_dry_run_returns_sql_preview(self, admin_client, migrations_dir, monkeypatch):
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        resp = admin_client.post(
            "/api/migrations/0042_system_state_table/run",
            json={"dry_run": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["migration_id"] == "0042_system_state_table"
        assert data["dry_run"] is True
        assert "CREATE TABLE" in data["sql_preview"]
        assert data["statements"] >= 1

    def test_dry_run_default(self, admin_client, migrations_dir, monkeypatch):
        """Omitting dry_run field should default to dry_run=True."""
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        # Send empty body — dry_run defaults to True
        resp = admin_client.post(
            "/api/migrations/0042_system_state_table/run",
            json={},
        )
        assert resp.status_code == 200
        assert resp.json()["dry_run"] is True

    def test_not_found_returns_404(self, admin_client, migrations_dir, monkeypatch):
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        resp = admin_client.post(
            "/api/migrations/nonexistent_migration/run",
            json={"dry_run": True},
        )
        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert detail["error"]["code"] == "migrations.not_found"

    def test_non_dry_run_returns_501(self, admin_client, migrations_dir, monkeypatch):
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        resp = admin_client.post(
            "/api/migrations/0042_system_state_table/run",
            json={"dry_run": False},
        )
        assert resp.status_code == 501
        detail = resp.json()["detail"]
        assert detail["error"]["code"] == "migrations.remote_execution_not_supported"
        assert "Wrangler CLI" in detail["error"]["message"]

    def test_readonly_returns_403(self, readonly_client, migrations_dir, monkeypatch):
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        resp = readonly_client.post(
            "/api/migrations/0042_system_state_table/run",
            json={"dry_run": True},
        )
        assert resp.status_code == 403

    def test_anon_returns_401(self, anon_client, migrations_dir, monkeypatch):
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        resp = anon_client.post(
            "/api/migrations/0042_system_state_table/run",
            json={"dry_run": True},
        )
        # CSRF middleware fires first on POST (returns 403) before auth (401)
        assert resp.status_code in (401, 403)

    def test_path_traversal_blocked(self, admin_client, migrations_dir, monkeypatch):
        """Path traversal IDs like ../../../etc/passwd must be rejected."""
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        # URL-encode the traversal so FastAPI passes it as path param
        # FastAPI will 404 on multi-segment paths; single-level traversal gets 400 or 404
        resp = admin_client.post(
            "/api/migrations/..%2F..%2Fetc%2Fpasswd/run",
            json={"dry_run": True},
        )
        # Must not be 200 — either 400 (invalid id) or 404 (not found after resolve)
        assert resp.status_code in (400, 404)
