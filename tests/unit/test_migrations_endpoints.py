"""Unit tests for /api/migrations/* endpoints.

Tests cover:
- Admin-only access (readonly → 403, anon → 401)
- GET /api/migrations lists SQL files with applied state
- POST /api/migrations/{id}/run returns SQL preview in dry_run mode
- POST with dry_run=false applies the migration against D1 and records it
- 404 for non-existent migration
- Path traversal is blocked
"""

from __future__ import annotations

import sqlite3
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


class _FakePort:
    """Just enough of D1AccessPort for the runner to count requests sent.

    ``summary`` is a **method** here because it is one on the real class. An
    earlier version of this fake exposed a plain dict, so the runner's
    `summary[...]` subscript passed here and raised TypeError in production —
    see test_http_posts_reads_a_real_port.
    """

    def __init__(self, http_posts=0):
        self._http_posts = http_posts

    def summary(self):
        return {"http_posts": self._http_posts}

    def sent(self, n=1):
        """Model n requests actually leaving the process."""
        self._http_posts += n


class _FakeD1:
    """Stand-in for make_d1_connection(); records which logical DB was targeted."""

    last_logical: str | None = None

    def __init__(self, logical, conn, http_posts=0):
        _FakeD1.last_logical = logical
        self._conn = conn
        # The runner samples this around batch_execute to tell "never sent" and
        # "sent once and rejected" from "sent, fate unknown".
        self._port = _FakePort(http_posts)

    def execute(self, sql, params=()):
        return self._conn.execute(sql)

    def batch_execute(self, statements):
        # Real D1 runs the batch atomically. A fake that wants to model that
        # (i.e. fail as a unit) defines batch_execute itself; the simpler fakes
        # that only care which SQL arrived keep their per-statement execute.
        batch = getattr(self._conn, "batch_execute", None)
        if batch is not None:
            return batch(statements)
        return [self._conn.execute(sql) for sql, _ in statements]


@pytest.fixture(autouse=True)
def _reset_fake_d1():
    """``last_logical`` is class state, so a prior test's value would let an
    assertion pass even when the code under test never opened a connection."""
    _FakeD1.last_logical = None
    yield
    _FakeD1.last_logical = None


def _failing_d1(exc, *, sends=1):
    """make_d1_connection stand-in that records `sends` requests actually
    leaving the process before raising `exc`.

    `sends` is what the runner keys its release decision on: 0 means the batch
    never left (an open circuit rejects before the POST), 1 plus a rejection
    means D1 said no and rolled back, anything else means its fate is unknown.
    """

    def _make(logical):
        fake = _FakeD1(logical, None)

        class _Conn:
            def batch_execute(self, statements):
                fake._port.sent(sends)
                raise exc

        fake._conn = _Conn()
        return fake

    return _make

@pytest.fixture
def migrations_dir(tmp_path):
    """Create a temp migrations directory with sample SQL files."""
    d = tmp_path / "d1"
    d.mkdir()
    (d / "0042_system_state_table.sql").write_text(
        "CREATE TABLE IF NOT EXISTS system_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
    )
    (d / "2026_05_04_add_rollback_columns_history.sql").write_text(
        "-- Apply with:\n"
        "--   wrangler d1 execute javdb-history --remote \\\n"
        "--     --file=javdb/migrations/d1/2026_05_04_add_rollback_columns_history.sql\n"
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
# TestAppliedMarkerReads
# ---------------------------------------------------------------------------


class TestAppliedMarkerReads:
    """Regression: D1 returns dict rows, so positional row[0]/row[1] raised
    KeyError and the swallow-all wrapper turned it into {}. Under d1/dual that
    made every marker invisible — the listing always showed 'unapplied' and the
    already-applied guard could never fire, the one backend where it matters."""

    @staticmethod
    def _patch_conn(monkeypatch, rows):
        class _Cur:
            def fetchall(self):
                return rows

        class _Conn:
            def execute(self, sql, params=()):
                return _Cur()

        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection", lambda logical: _Conn()
        )

    def test_reads_d1_dict_rows(self, monkeypatch):
        import apps.api.routers.migrations as migrations_module

        self._patch_conn(
            monkeypatch,
            [{"key": "migration_applied:0042_system_state_table", "value": "2026-01-01"}],
        )
        assert migrations_module._read_applied_migrations() == {
            "0042_system_state_table": "2026-01-01"
        }

    def test_reads_sqlite_tuple_rows(self, monkeypatch):
        import apps.api.routers.migrations as migrations_module

        self._patch_conn(
            monkeypatch,
            [("migration_applied:0042_system_state_table", "2026-01-01")],
        )
        assert migrations_module._read_applied_migrations() == {
            "0042_system_state_table": "2026-01-01"
        }

    def test_absent_ledger_table_reads_as_empty_not_error(self, monkeypatch):
        """Bootstrap: on a fresh D1 the system_state table does not exist yet.
        Raising there would make 0042_system_state_table — the migration that
        CREATEs the ledger — impossible to run through this endpoint."""
        import apps.api.routers.migrations as migrations_module

        class _Conn:
            def execute(self, sql, params=()):
                raise sqlite3.OperationalError("no such table: system_state")

        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection", lambda logical: _Conn()
        )
        assert migrations_module._read_applied_migrations() == {}

    def test_other_read_failures_still_propagate(self, monkeypatch):
        """Only the absent-ledger case is benign; a transport failure must still
        reach the apply path so it can fail closed."""
        import apps.api.routers.migrations as migrations_module

        class _Conn:
            def execute(self, sql, params=()):
                raise RuntimeError("D1 unreachable")

        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection", lambda logical: _Conn()
        )
        with pytest.raises(RuntimeError, match="D1 unreachable"):
            migrations_module._read_applied_migrations()

    def test_lenient_wrapper_swallows_for_listing_only(self, monkeypatch):
        import apps.api.routers.migrations as migrations_module

        def _boom():
            raise RuntimeError("D1 unreachable")

        monkeypatch.setattr(migrations_module, "_read_applied_migrations", _boom)
        assert migrations_module._get_applied_migrations() == {}


class TestHttpPosts:
    """Regression: `_http_posts` read `port.summary[...]`, but
    `D1AccessPort.summary` is a *method*. Subscripting the bound method raised
    TypeError into the swallow, so it always returned None against a real
    connection — every failure was treated as possibly-sent and the claim was
    never released. The unit tests passed the whole time because the fake
    exposed a plain dict. So this one talks to the real class."""

    @staticmethod
    def _real_port():
        from javdb.storage.d1_port import D1AccessPort, D1PortConfig

        return D1AccessPort(
            url="https://example.invalid/query",
            headers={},
            config=D1PortConfig(
                timeout=1,
                batch_limit=50,
                max_retries=3,
                retry_base_sec=0.0,
                retry_max_sleep_sec=0.0,
            ),
            post_request=lambda *a, **k: pytest.fail("no request expected"),
        )

    def test_http_posts_reads_a_real_port(self):
        import apps.api.routers.migrations as migrations_module

        class _Conn:
            pass

        conn = _Conn()
        conn._port = self._real_port()
        # A fresh port has sent nothing — and crucially this is 0, not None.
        assert migrations_module._http_posts(conn) == 0

        conn._port._summary["http_posts"] += 2
        assert migrations_module._http_posts(conn) == 2

    def test_missing_port_reads_as_unknown(self):
        import apps.api.routers.migrations as migrations_module

        class _Conn:
            pass

        assert migrations_module._http_posts(_Conn()) is None


class TestClaimMigration:
    """The claim is what makes two concurrent applies safe, so it is exercised
    against a real primary key rather than a stub that just returns a verdict."""

    @staticmethod
    def _patch_ledger(monkeypatch, conn):
        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection", lambda logical: conn
        )

    def test_second_claim_of_the_same_migration_is_taken(self, monkeypatch):
        import apps.api.routers.migrations as migrations_module

        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE system_state (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)"
        )
        self._patch_ledger(monkeypatch, conn)

        assert migrations_module._claim_migration("m1", "2026-01-01T00:00:00Z") == "claimed"
        assert migrations_module._claim_migration("m1", "2026-01-01T00:00:01Z") == "taken"
        # The winner's timestamp survives; the loser never overwrites it.
        assert migrations_module._read_applied_migrations() == {
            "m1": "2026-01-01T00:00:00Z"
        }

    def test_released_claim_can_be_taken_again(self, monkeypatch):
        import apps.api.routers.migrations as migrations_module

        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE system_state (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)"
        )
        self._patch_ledger(monkeypatch, conn)

        migrations_module._claim_migration("m1", "2026-01-01T00:00:00Z")
        assert migrations_module._release_claim("m1") is True
        assert migrations_module._claim_migration("m1", "2026-01-02T00:00:00Z") == "claimed"

    def test_absent_ledger_reports_no_ledger_not_failure(self, monkeypatch):
        """The bootstrap migration CREATEs system_state, so there is nothing to
        claim against — that must not be reported as a claim failure."""
        import apps.api.routers.migrations as migrations_module

        self._patch_ledger(monkeypatch, sqlite3.connect(":memory:"))
        assert (
            migrations_module._claim_migration("0042", "2026-01-01T00:00:00Z")
            == "no_ledger"
        )

    def test_transport_failure_propagates(self, monkeypatch):
        """Anything that is neither a collision nor an absent table leaves the
        concurrency question unanswered, so it must reach the 503 path."""
        import apps.api.routers.migrations as migrations_module

        class _Conn:
            def execute(self, sql, params=()):
                raise RuntimeError("D1 unreachable")

        self._patch_ledger(monkeypatch, _Conn())
        with pytest.raises(RuntimeError, match="D1 unreachable"):
            migrations_module._claim_migration("m1", "2026-01-01T00:00:00Z")


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

    def test_dry_run_statement_count_excludes_comments(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """The header comment block is not an executable statement."""
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        resp = admin_client.post(
            "/api/migrations/2026_05_04_add_rollback_columns_history/run",
            json={"dry_run": True},
        )
        assert resp.status_code == 200
        assert resp.json()["statements"] == 2  # two ALTERs, comments dropped

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

    def test_apply_executes_statements_and_records_applied(
        self, admin_client, migrations_dir, monkeypatch
    ):
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        executed: list[str] = []
        recorded: list[tuple[str, str]] = []

        class _Conn:
            def execute(self, sql):
                executed.append(sql)

        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection",
            lambda logical: _FakeD1(logical, _Conn()),
        )
        monkeypatch.setattr(
            migrations_module, "_read_applied_migrations", lambda: {}
        )
        monkeypatch.setattr(
            migrations_module,
            "_claim_migration",
            lambda mid, at: (recorded.append((mid, at)), "claimed")[1],
        )
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")

        resp = admin_client.post(
            "/api/migrations/2026_05_04_add_rollback_columns_history/run",
            json={"dry_run": False, "acknowledge_unrecorded": True},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["applied"] is True
        assert data["dry_run"] is False
        assert data["statements"] == 2
        # Both ALTERs ran, and the comment header did not.
        assert len(executed) == 2
        assert all(s.startswith("ALTER TABLE") for s in executed)
        # Routed to the database the wrangler header names.
        assert _FakeD1.last_logical == "history"
        assert [mid for mid, _ in recorded] == [
            "2026_05_04_add_rollback_columns_history"
        ]

    def test_apply_refuses_when_backend_is_sqlite(
        self, admin_client, migrations_dir, monkeypatch
    ):
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "sqlite")
        resp = admin_client.post(
            "/api/migrations/2026_05_04_add_rollback_columns_history/run",
            json={"dry_run": False, "acknowledge_unrecorded": True},
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"]["code"] == "migrations.backend_not_d1"

    def test_apply_refuses_already_applied(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """ALTER TABLE ADD COLUMN is not idempotent — replay must be refused."""
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")
        monkeypatch.setattr(
            migrations_module,
            "_read_applied_migrations",
            lambda: {"2026_05_04_add_rollback_columns_history": "2026-01-01T00:00:00Z"},
        )
        resp = admin_client.post(
            "/api/migrations/2026_05_04_add_rollback_columns_history/run",
            json={"dry_run": False, "acknowledge_unrecorded": True},
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"]["code"] == "migrations.already_applied"

    def test_apply_refuses_when_target_db_unresolved(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """0042 has no wrangler header — never guess which production DB to mutate."""
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")
        monkeypatch.setattr(migrations_module, "_read_applied_migrations", lambda: {})
        resp = admin_client.post(
            "/api/migrations/0042_system_state_table/run",
            json={"dry_run": False, "acknowledge_unrecorded": True},
        )
        assert resp.status_code == 400
        assert (
            resp.json()["detail"]["error"]["code"] == "migrations.target_db_unresolved"
        )

    def test_rejected_batch_rolls_back_whole_and_releases_the_claim(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """Statements go out as one atomic D1 batch, so a rejection leaves
        nothing applied — and the pre-execution claim must be released so a
        retry is possible once the cause is fixed."""
        import apps.api.routers.migrations as migrations_module
        from javdb.storage.d1_client import D1PermanentError

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        released: list = []

        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection",
            _failing_d1(D1PermanentError("duplicate column name: write_mode")),
        )
        monkeypatch.setattr(migrations_module, "_read_applied_migrations", lambda: {})
        monkeypatch.setattr(
            migrations_module, "_claim_migration", lambda mid, at: "claimed"
        )
        monkeypatch.setattr(
            migrations_module,
            "_release_claim",
            lambda mid: (released.append(mid), True)[1],
        )
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")

        resp = admin_client.post(
            "/api/migrations/2026_05_04_add_rollback_columns_history/run",
            json={"dry_run": False, "acknowledge_unrecorded": True},
        )
        assert resp.status_code == 502
        message = resp.json()["detail"]["error"]["message"]
        assert "rolled back whole" in message
        assert "no schema change" in message
        assert released == ["2026_05_04_add_rollback_columns_history"]

    def test_rejected_batch_says_so_when_the_claim_cannot_be_released(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """A stuck claim makes an unapplied migration list as applied. That is the
        safe direction, but the operator has to be told to clear it."""
        import apps.api.routers.migrations as migrations_module
        from javdb.storage.d1_client import D1PermanentError

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)

        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection",
            _failing_d1(D1PermanentError("no such table: MovieHistory")),
        )
        monkeypatch.setattr(migrations_module, "_read_applied_migrations", lambda: {})
        monkeypatch.setattr(
            migrations_module, "_claim_migration", lambda mid, at: "claimed"
        )
        monkeypatch.setattr(migrations_module, "_release_claim", lambda mid: False)
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")

        resp = admin_client.post(
            "/api/migrations/2026_05_04_add_rollback_columns_history/run",
            json={"dry_run": False, "acknowledge_unrecorded": True},
        )
        assert resp.status_code == 502
        assert "clear the marker" in resp.json()["detail"]["error"]["message"]

    def test_connection_failure_releases_the_claim(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """Opening the client is the one failure that provably sent nothing —
        missing D1 credentials, typically. The claim must not leak there, or the
        migration lists as applied having never run."""
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        released: list = []

        def _boom(logical):
            raise RuntimeError("D1_API_TOKEN is not configured")

        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection", _boom
        )
        monkeypatch.setattr(migrations_module, "_read_applied_migrations", lambda: {})
        monkeypatch.setattr(
            migrations_module, "_claim_migration", lambda mid, at: "claimed"
        )
        monkeypatch.setattr(
            migrations_module,
            "_release_claim",
            lambda mid: (released.append(mid), True)[1],
        )
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")

        resp = admin_client.post(
            "/api/migrations/2026_05_04_add_rollback_columns_history/run",
            json={"dry_run": False, "acknowledge_unrecorded": True},
        )
        assert resp.status_code == 503
        assert resp.json()["detail"]["error"]["code"] == "migrations.connection_failed"
        assert released == ["2026_05_04_add_rollback_columns_history"]

    def test_unexpected_execution_error_keeps_the_claim(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """Once the batch is in flight, an unexpected exception says *less* about
        the outcome than a D1Error does, not more — so it lands in the
        unknown-outcome branch rather than escaping as a bare 500."""
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)

        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection",
            _failing_d1(KeyError("results")),
        )
        monkeypatch.setattr(migrations_module, "_read_applied_migrations", lambda: {})
        monkeypatch.setattr(
            migrations_module, "_claim_migration", lambda mid, at: "claimed"
        )
        monkeypatch.setattr(
            migrations_module,
            "_release_claim",
            lambda mid: pytest.fail("must not release a claim on an unknown outcome"),
        )
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")

        resp = admin_client.post(
            "/api/migrations/2026_05_04_add_rollback_columns_history/run",
            json={"dry_run": False, "acknowledge_unrecorded": True},
        )
        assert resp.status_code == 502
        assert resp.json()["detail"]["error"]["code"] == "migrations.outcome_unknown"

    def test_rejection_after_a_retry_keeps_the_claim(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """Regression: the transport retries transient failures on its own, and a
        migration batch is not idempotent. If attempt 1 committed and only its
        response was lost, attempt 2 comes back with 'duplicate column name' — a
        permanent error describing the retry, not the migration. Trusting it
        would release the claim on a migration that already landed."""
        import apps.api.routers.migrations as migrations_module
        from javdb.storage.d1_client import D1PermanentError

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)

        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection",
            # Two requests: the first committed and lost its response, the
            # re-send came back rejected.
            _failing_d1(D1PermanentError("duplicate column name: session_id"), sends=2),
        )
        monkeypatch.setattr(migrations_module, "_read_applied_migrations", lambda: {})
        monkeypatch.setattr(
            migrations_module, "_claim_migration", lambda mid, at: "claimed"
        )
        monkeypatch.setattr(
            migrations_module,
            "_release_claim",
            lambda mid: pytest.fail("must not release after a retried batch"),
        )
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")

        resp = admin_client.post(
            "/api/migrations/2026_05_04_add_rollback_columns_history/run",
            json={"dry_run": False, "acknowledge_unrecorded": True},
        )
        assert resp.status_code == 502
        error = resp.json()["detail"]["error"]
        assert error["code"] == "migrations.outcome_unknown"
        assert "may have committed" in error["message"]

    def test_unreadable_request_counter_is_treated_as_sent(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """A counter we cannot read cannot establish that nothing left the
        process, so the conservative branch wins."""
        import apps.api.routers.migrations as migrations_module
        from javdb.storage.d1_client import D1PermanentError

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)

        class _Conn:
            """No _port at all — _http_posts returns None."""

            def batch_execute(self, statements):
                raise D1PermanentError("duplicate column name: session_id")

        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection", lambda logical: _Conn()
        )
        monkeypatch.setattr(migrations_module, "_read_applied_migrations", lambda: {})
        monkeypatch.setattr(
            migrations_module, "_claim_migration", lambda mid, at: "claimed"
        )
        monkeypatch.setattr(
            migrations_module,
            "_release_claim",
            lambda mid: pytest.fail("must not release on an unreadable counter"),
        )
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")

        resp = admin_client.post(
            "/api/migrations/2026_05_04_add_rollback_columns_history/run",
            json={"dry_run": False, "acknowledge_unrecorded": True},
        )
        assert resp.status_code == 502
        assert resp.json()["detail"]["error"]["code"] == "migrations.outcome_unknown"

    def test_transient_failure_keeps_the_claim_because_the_outcome_is_unknown(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """Regression: a timeout is not a rollback. D1 may have committed the
        batch and lost the response on the way back, so releasing the claim
        would green-light replaying a migration that already landed — and some
        of them DROP tables. The claim stays; the operator checks the schema."""
        import apps.api.routers.migrations as migrations_module
        from javdb.storage.d1_client import D1TransientError

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)

        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection",
            _failing_d1(D1TransientError("read timeout waiting for D1 response")),
        )
        monkeypatch.setattr(migrations_module, "_read_applied_migrations", lambda: {})
        monkeypatch.setattr(
            migrations_module, "_claim_migration", lambda mid, at: "claimed"
        )
        monkeypatch.setattr(
            migrations_module,
            "_release_claim",
            lambda mid: pytest.fail("must not release a claim on an unknown outcome"),
        )
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")

        resp = admin_client.post(
            "/api/migrations/2026_05_04_add_rollback_columns_history/run",
            json={"dry_run": False, "acknowledge_unrecorded": True},
        )
        assert resp.status_code == 502
        error = resp.json()["detail"]["error"]
        assert error["code"] == "migrations.outcome_unknown"
        assert "NOT a rollback" in error["message"]

    def test_circuit_open_before_any_request_releases_the_claim(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """Regression: an open circuit rejects in breaker.acquire(), BEFORE the
        POST, so the batch provably never left. Keeping the claim there marks a
        migration that never ran as applied, forever, for no safety gain."""
        import apps.api.routers.migrations as migrations_module
        from javdb.storage.d1_client import D1CircuitOpenError

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        released: list = []

        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection",
            _failing_d1(D1CircuitOpenError("circuit open past max-open window"), sends=0),
        )
        monkeypatch.setattr(migrations_module, "_read_applied_migrations", lambda: {})
        monkeypatch.setattr(
            migrations_module, "_claim_migration", lambda mid, at: "claimed"
        )
        monkeypatch.setattr(
            migrations_module,
            "_release_claim",
            lambda mid: (released.append(mid), True)[1],
        )
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")

        resp = admin_client.post(
            "/api/migrations/2026_05_04_add_rollback_columns_history/run",
            json={"dry_run": False, "acknowledge_unrecorded": True},
        )
        assert resp.status_code == 502
        error = resp.json()["detail"]["error"]
        assert error["code"] == "migrations.execution_failed"
        assert "No request reached" in error["message"]
        assert released == ["2026_05_04_add_rollback_columns_history"]

    def test_circuit_open_after_a_request_keeps_the_claim(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """The same exception on a later attempt is the opposite case: a request
        already went out and its fate is unknown, so the claim stays."""
        import apps.api.routers.migrations as migrations_module
        from javdb.storage.d1_client import D1CircuitOpenError

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)

        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection",
            _failing_d1(D1CircuitOpenError("circuit open past max-open window")),
        )
        monkeypatch.setattr(migrations_module, "_read_applied_migrations", lambda: {})
        monkeypatch.setattr(
            migrations_module, "_claim_migration", lambda mid, at: "claimed"
        )
        monkeypatch.setattr(
            migrations_module,
            "_release_claim",
            lambda mid: pytest.fail("must not release a claim on an unknown outcome"),
        )
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")

        resp = admin_client.post(
            "/api/migrations/2026_05_04_add_rollback_columns_history/run",
            json={"dry_run": False, "acknowledge_unrecorded": True},
        )
        assert resp.status_code == 502
        assert resp.json()["detail"]["error"]["code"] == "migrations.outcome_unknown"

    def test_concurrent_claim_is_refused_without_executing(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """Two admins racing the same migration serialize on the ledger's primary
        key. The loser must not run the statements a second time."""
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        monkeypatch.setattr(migrations_module, "_read_applied_migrations", lambda: {})
        monkeypatch.setattr(
            migrations_module, "_claim_migration", lambda mid, at: "taken"
        )
        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection",
            lambda logical: pytest.fail("must not execute when the claim is taken"),
        )
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")

        resp = admin_client.post(
            "/api/migrations/2026_05_04_add_rollback_columns_history/run",
            json={"dry_run": False, "acknowledge_unrecorded": True},
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"]["code"] == "migrations.already_applied"

    def test_unclaimable_ledger_refuses_before_executing(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """If the claim cannot be written at all, a concurrent apply cannot be
        ruled out — refuse rather than execute unguarded."""
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        monkeypatch.setattr(migrations_module, "_read_applied_migrations", lambda: {})

        def _boom(mid, at):
            raise RuntimeError("D1 unreachable")

        monkeypatch.setattr(migrations_module, "_claim_migration", _boom)
        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection",
            lambda logical: pytest.fail("must not execute when the claim failed"),
        )
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")

        resp = admin_client.post(
            "/api/migrations/2026_05_04_add_rollback_columns_history/run",
            json={"dry_run": False, "acknowledge_unrecorded": True},
        )
        assert resp.status_code == 503
        assert resp.json()["detail"]["error"]["code"] == "migrations.claim_failed"

    def test_script_too_large_for_one_batch_is_refused(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """Above D1's batch limit the runner could only chunk, and chunks are not
        atomic — exactly the half-applied schema this endpoint refuses to create."""
        import apps.api.routers.migrations as migrations_module
        from javdb.storage.d1_client import _BATCH_LIMIT

        (migrations_dir / "huge.sql").write_text(
            "-- wrangler d1 execute javdb-reports --remote --file=huge.sql\n"
            + "".join(
                f"ALTER TABLE T ADD COLUMN c{i} TEXT;\n" for i in range(_BATCH_LIMIT + 1)
            )
        )
        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        monkeypatch.setattr(migrations_module, "_read_applied_migrations", lambda: {})
        monkeypatch.setattr(
            migrations_module,
            "_claim_migration",
            lambda mid, at: pytest.fail("must not claim what it will not run"),
        )
        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection",
            lambda logical: pytest.fail("must not execute a non-atomic script"),
        )
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")

        resp = admin_client.post(
            "/api/migrations/huge/run",
            json={"dry_run": False, "acknowledge_unrecorded": True},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "migrations.not_atomic"

    def test_script_of_exactly_one_batch_is_allowed(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """Boundary on the `>` comparison: exactly _BATCH_LIMIT statements still
        fit one atomic batch, so they must go out as a single call."""
        import apps.api.routers.migrations as migrations_module
        from javdb.storage.d1_client import _BATCH_LIMIT

        (migrations_dir / "exact.sql").write_text(
            "-- wrangler d1 execute javdb-reports --remote --file=exact.sql\n"
            + "".join(
                f"ALTER TABLE T ADD COLUMN c{i} TEXT;\n" for i in range(_BATCH_LIMIT)
            )
        )
        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        monkeypatch.setattr(migrations_module, "_read_applied_migrations", lambda: {})
        monkeypatch.setattr(
            migrations_module, "_claim_migration", lambda mid, at: "claimed"
        )
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")
        batches: list = []

        class _Conn:
            def batch_execute(self, statements):
                batches.append(list(statements))
                # D1 returns one cursor per statement; the runner checks the count.
                return [object() for _ in statements]

        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection",
            lambda logical: _FakeD1(logical, _Conn()),
        )

        resp = admin_client.post(
            "/api/migrations/exact/run",
            json={"dry_run": False, "acknowledge_unrecorded": True},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["statements"] == _BATCH_LIMIT
        assert len(batches) == 1
        assert len(batches[0]) == _BATCH_LIMIT

    def test_pragma_migration_is_refused(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """A PRAGMA is a no-op inside a transaction, and everything here runs as
        one batch. 2026_05_13_session_id_to_text_reports.sql opens with
        `PRAGMA foreign_keys = OFF` so it can DROP a referenced parent table;
        batched, the DROP would fail on the constraint and take the batch with
        it. Refuse with the reason rather than fail confusingly at execution."""
        import apps.api.routers.migrations as migrations_module

        (migrations_dir / "pragma.sql").write_text(
            "-- wrangler d1 execute javdb-reports --remote --file=pragma.sql\n"
            "PRAGMA foreign_keys = OFF;\n"
            "DROP TABLE ReportSessions;\n"
            "PRAGMA foreign_keys = ON;\n"
        )
        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        monkeypatch.setattr(migrations_module, "_read_applied_migrations", lambda: {})
        monkeypatch.setattr(
            migrations_module,
            "_claim_migration",
            lambda mid, at: pytest.fail("must not claim what it will not run"),
        )
        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection",
            lambda logical: pytest.fail("must not execute a PRAGMA migration"),
        )
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")

        resp = admin_client.post(
            "/api/migrations/pragma/run",
            json={"dry_run": False, "acknowledge_unrecorded": True},
        )
        assert resp.status_code == 400
        error = resp.json()["detail"]["error"]
        assert error["code"] == "migrations.pragma_unsupported"
        assert "Wrangler" in error["message"]

    def test_real_pragma_migration_in_the_repo_is_refused(
        self, admin_client, monkeypatch
    ):
        """Pins the concrete case: the shipped session-id-to-TEXT migration for
        the reports database must not be applicable through this endpoint."""
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_read_applied_migrations", lambda: {})
        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection",
            lambda logical: pytest.fail("must not execute a PRAGMA migration"),
        )
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")

        resp = admin_client.post(
            "/api/migrations/2026_05_13_session_id_to_text_reports/run",
            json={"dry_run": False, "acknowledge_unrecorded": True},
        )
        assert resp.status_code == 400
        assert (
            resp.json()["detail"]["error"]["code"] == "migrations.pragma_unsupported"
        )

    def test_drop_table_migration_is_refused(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """Same root cause as the PRAGMA case, without the tell. A table rebuild
        DROPs a parent and recreates it, which only works while foreign keys are
        relaxed — and this runner cannot relax them, because the PRAGMA that
        would is inert inside its batch."""
        import apps.api.routers.migrations as migrations_module

        (migrations_dir / "rebuild.sql").write_text(
            "-- wrangler d1 execute javdb-history --remote --file=rebuild.sql\n"
            "CREATE TABLE MovieHistory_new (Id INTEGER PRIMARY KEY);\n"
            "INSERT INTO MovieHistory_new SELECT Id FROM MovieHistory;\n"
            "DROP TABLE MovieHistory;\n"
            "ALTER TABLE MovieHistory_new RENAME TO MovieHistory;\n"
        )
        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        monkeypatch.setattr(migrations_module, "_read_applied_migrations", lambda: {})
        monkeypatch.setattr(
            migrations_module,
            "_claim_migration",
            lambda mid, at: pytest.fail("must not claim what it will not run"),
        )
        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection",
            lambda logical: pytest.fail("must not execute a table rebuild"),
        )
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")

        resp = admin_client.post(
            "/api/migrations/rebuild/run",
            json={"dry_run": False, "acknowledge_unrecorded": True},
        )
        assert resp.status_code == 400
        error = resp.json()["detail"]["error"]
        assert error["code"] == "migrations.drop_table_unsupported"
        assert "Wrangler" in error["message"]

    def test_real_history_rebuild_migration_is_refused(
        self, admin_client, monkeypatch
    ):
        """Pins the concrete trap Codex found: the history rebuild DROPs
        MovieHistory while TorrentHistory still REFERENCES it, and carries no
        PRAGMA, so the PRAGMA check alone would wave it through."""
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_read_applied_migrations", lambda: {})
        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection",
            lambda logical: pytest.fail("must not execute a table rebuild"),
        )
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")

        resp = admin_client.post(
            "/api/migrations/2026_05_13_session_id_to_text_history/run",
            json={"dry_run": False, "acknowledge_unrecorded": True},
        )
        assert resp.status_code == 400
        assert (
            resp.json()["detail"]["error"]["code"]
            == "migrations.drop_table_unsupported"
        )

    def test_short_batch_result_is_not_reported_as_applied(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """No exception is not evidence of success. D1 returns one cursor per
        statement, and `batch_execute` — unlike `execute` — does not reject a
        success response with an empty result list. Reporting applied: true on
        that would keep a marker blocking the retry with nothing to show."""
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)

        class _Conn:
            def batch_execute(self, statements):
                return []  # success: true, empty result

        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection",
            lambda logical: _FakeD1(logical, _Conn()),
        )
        monkeypatch.setattr(migrations_module, "_read_applied_migrations", lambda: {})
        monkeypatch.setattr(
            migrations_module, "_claim_migration", lambda mid, at: "claimed"
        )
        monkeypatch.setattr(
            migrations_module,
            "_release_claim",
            lambda mid: pytest.fail("must not release a claim on an unknown outcome"),
        )
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")

        resp = admin_client.post(
            "/api/migrations/2026_05_04_add_rollback_columns_history/run",
            json={"dry_run": False, "acknowledge_unrecorded": True},
        )
        assert resp.status_code == 502
        error = resp.json()["detail"]["error"]
        assert error["code"] == "migrations.outcome_unknown"
        assert "no evidence" in error["message"]

    def test_static_validation_refuses_before_the_acknowledgement(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """A missing wrangler header is decidable from the file alone, so it must
        not hide behind the acknowledgement — otherwise the operator learns to
        set the flag reflexively just to see the real error."""
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        monkeypatch.setattr(migrations_module, "_read_applied_migrations", lambda: {})
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")

        resp = admin_client.post(
            "/api/migrations/0042_system_state_table/run",
            json={"dry_run": False},
        )
        assert resp.status_code == 400
        assert (
            resp.json()["detail"]["error"]["code"] == "migrations.target_db_unresolved"
        )

    def test_apply_refuses_when_applied_state_is_unreadable(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """Fail closed: unknown applied-state must not be treated as 'not applied',
        or a non-idempotent migration gets replayed against production."""
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")

        def _boom():
            raise RuntimeError("D1 unreachable")

        monkeypatch.setattr(migrations_module, "_read_applied_migrations", _boom)
        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection",
            lambda logical: pytest.fail("must not execute when state is unknown"),
        )
        resp = admin_client.post(
            "/api/migrations/2026_05_04_add_rollback_columns_history/run",
            json={"dry_run": False, "acknowledge_unrecorded": True},
        )
        assert resp.status_code == 503
        assert (
            resp.json()["detail"]["error"]["code"]
            == "migrations.applied_state_unreadable"
        )

    def test_applied_but_unrecorded_returns_500_and_says_do_not_rerun(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """The inverse of 502: the schema DID change, only the bookkeeping
        failed, so the operator must NOT re-run it.

        Only the bootstrap migration reaches this — it CREATEs system_state, so
        there is no ledger to claim against beforehand and it is recorded after
        the fact. Every other migration claims first and never gets here.
        """
        import apps.api.routers.migrations as migrations_module

        (migrations_dir / "bootstrap.sql").write_text(
            "-- wrangler d1 execute javdb-operations --remote --file=bootstrap.sql\n"
            "CREATE TABLE IF NOT EXISTS system_state (key TEXT PRIMARY KEY, "
            "value TEXT NOT NULL, updated_at TEXT NOT NULL);\n"
        )
        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)

        class _Conn:
            def execute(self, sql):
                return None

        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection",
            lambda logical: _FakeD1(logical, _Conn()),
        )
        monkeypatch.setattr(migrations_module, "_read_applied_migrations", lambda: {})
        monkeypatch.setattr(
            migrations_module, "_claim_migration", lambda mid, at: "no_ledger"
        )
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")

        def _boom(mid, at):
            raise RuntimeError("system_state write rejected")

        monkeypatch.setattr(migrations_module, "_record_applied", _boom)

        resp = admin_client.post(
            "/api/migrations/bootstrap/run",
            json={"dry_run": False, "acknowledge_unrecorded": True},
        )
        assert resp.status_code == 500
        error = resp.json()["detail"]["error"]
        assert error["code"] == "migrations.record_failed"
        assert "Do NOT re-run" in error["message"]

    def test_comment_only_migration_returns_400_empty(
        self, admin_client, migrations_dir, monkeypatch
    ):
        import apps.api.routers.migrations as migrations_module

        (migrations_dir / "comments_only.sql").write_text(
            "-- Apply with:\n"
            "--   wrangler d1 execute javdb-reports --remote \\\n"
            "--     --file=javdb/migrations/d1/comments_only.sql\n"
            "-- nothing executable here\n"
        )
        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        monkeypatch.setattr(migrations_module, "_read_applied_migrations", lambda: {})
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")
        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection",
            lambda logical: pytest.fail("must not execute an empty migration"),
        )

        resp = admin_client.post(
            "/api/migrations/comments_only/run",
            json={"dry_run": False, "acknowledge_unrecorded": True},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "migrations.empty"

    def test_unterminated_literal_is_refused_in_both_modes(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """An unterminated literal is the one shape the splitter cannot resolve;
        refuse rather than ship half a statement to production D1. Refused in
        dry-run too, so the preview's statement count is never quietly wrong."""
        import apps.api.routers.migrations as migrations_module

        (migrations_dir / "quoted.sql").write_text(
            "-- wrangler d1 execute javdb-reports --remote\n"
            "ALTER TABLE T ADD COLUMN c TEXT DEFAULT 'oops;\n"
        )
        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection",
            lambda logical: pytest.fail("must not execute an unparseable migration"),
        )
        for dry_run in (True, False):
            resp = admin_client.post(
                "/api/migrations/quoted/run", json={"dry_run": dry_run}
            )
            assert resp.status_code == 400, dry_run
            assert resp.json()["detail"]["error"]["code"] == "migrations.unparseable"


    def test_absent_ledger_refuses_everything_but_the_bootstrap_migration(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """On a fresh operations DB every migration is unclaimable, not just the
        one that creates the ledger. Running an ordinary migration there would
        execute with no concurrency protection and then fail to record anyway."""
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        monkeypatch.setattr(migrations_module, "_read_applied_migrations", lambda: {})
        monkeypatch.setattr(
            migrations_module, "_claim_migration", lambda mid, at: "no_ledger"
        )
        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection",
            lambda logical: pytest.fail("must not execute without a claimable ledger"),
        )
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")

        resp = admin_client.post(
            "/api/migrations/2026_05_04_add_rollback_columns_history/run",
            json={"dry_run": False, "acknowledge_unrecorded": True},
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"]["code"] == "migrations.ledger_absent"

    def test_bootstrap_migration_may_run_without_a_ledger(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """The chicken-and-egg case: the migration that CREATEs system_state has
        no ledger to claim against, so it is the one exception."""
        import apps.api.routers.migrations as migrations_module

        (migrations_dir / "bootstrap.sql").write_text(
            "-- wrangler d1 execute javdb-operations --remote --file=bootstrap.sql\n"
            "CREATE TABLE IF NOT EXISTS system_state (key TEXT PRIMARY KEY, "
            "value TEXT NOT NULL, updated_at TEXT NOT NULL);\n"
        )
        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        monkeypatch.setattr(migrations_module, "_read_applied_migrations", lambda: {})
        monkeypatch.setattr(
            migrations_module, "_claim_migration", lambda mid, at: "no_ledger"
        )
        recorded: list = []
        monkeypatch.setattr(
            migrations_module, "_record_applied", lambda mid, at: recorded.append(mid)
        )
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")

        class _Conn:
            def batch_execute(self, statements):
                return [object() for _ in statements]

        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection",
            lambda logical: _FakeD1(logical, _Conn()),
        )

        resp = admin_client.post(
            "/api/migrations/bootstrap/run",
            json={"dry_run": False, "acknowledge_unrecorded": True},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["applied"] is True
        # Recorded after the fact, since there was nothing to claim beforehand.
        assert recorded == ["bootstrap"]

    def test_unrecorded_migration_is_refused_without_acknowledgement(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """The upgraded-database trap: every historical migration was applied
        with Wrangler, which writes no marker, so an established D1 has a
        fully-migrated schema and no markers at all. Applying on that basis
        would replay a non-idempotent migration."""
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        monkeypatch.setattr(migrations_module, "_read_applied_migrations", lambda: {})
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")
        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection",
            lambda logical: pytest.fail("must not execute an unrecorded migration"),
        )

        resp = admin_client.post(
            "/api/migrations/2026_05_04_add_rollback_columns_history/run",
            json={"dry_run": False},
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"]["code"] == "migrations.unrecorded"

    def test_other_markers_do_not_waive_the_acknowledgement(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """Regression: the guard used to key on the ledger being *empty*, so the
        very first API apply wrote a marker and every remaining wrangler-applied
        file silently lost the speed bump while still listing as unapplied. Some
        of those rebuild tables from a hardcoded column list and DROP the
        originals. A marker for a *different* migration says nothing about this
        one."""
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        monkeypatch.setattr(
            migrations_module,
            "_read_applied_migrations",
            lambda: {"some_other_migration": "2026-01-01T00:00:00Z"},
        )
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")
        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection",
            lambda logical: pytest.fail("must not execute an unrecorded migration"),
        )

        resp = admin_client.post(
            "/api/migrations/2026_05_04_add_rollback_columns_history/run",
            json={"dry_run": False},
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"]["code"] == "migrations.unrecorded"

    def test_acknowledged_migration_applies(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """The acknowledgement is the whole gate: with it, the apply proceeds."""
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        monkeypatch.setattr(
            migrations_module,
            "_read_applied_migrations",
            lambda: {"some_other_migration": "2026-01-01T00:00:00Z"},
        )
        monkeypatch.setattr(
            migrations_module, "_claim_migration", lambda mid, at: "claimed"
        )
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")

        class _Conn:
            def execute(self, sql):
                return None

        monkeypatch.setattr(
            "javdb.storage.d1_client.make_d1_connection",
            lambda logical: _FakeD1(logical, _Conn()),
        )

        resp = admin_client.post(
            "/api/migrations/2026_05_04_add_rollback_columns_history/run",
            json={"dry_run": False, "acknowledge_unrecorded": True},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["applied"] is True

    def test_dry_run_never_needs_the_acknowledgement(
        self, admin_client, migrations_dir, monkeypatch
    ):
        """The gate protects the schema, not the preview — a dry run changes
        nothing, so demanding the flag there would be pure friction."""
        import apps.api.routers.migrations as migrations_module

        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)
        monkeypatch.setattr("javdb.infra.config.storage_backend", lambda: "d1")

        resp = admin_client.post(
            "/api/migrations/2026_05_04_add_rollback_columns_history/run",
            json={"dry_run": True},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["statements"] == 2

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

    def test_symlink_out_of_the_migrations_dir_is_refused(
        self, admin_client, migrations_dir, tmp_path, monkeypatch
    ):
        """The ID allowlist stops separators, but a symlink planted in the
        migrations directory still names a file outside it — and a dry run hands
        the caller that file's contents verbatim. Containment is therefore
        re-checked on the resolved path."""
        import apps.api.routers.migrations as migrations_module

        outside = tmp_path / "not_a_migration.sql"
        outside.write_text("SELECT 'private-contents';")
        (migrations_dir / "escape.sql").symlink_to(outside)
        monkeypatch.setattr(migrations_module, "_MIGRATIONS_DIR", migrations_dir)

        resp = admin_client.post(
            "/api/migrations/escape/run",
            json={"dry_run": True},
        )

        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "migrations.invalid_id"
        assert "private-contents" not in resp.text


class TestStatementSplitting:
    """`--` and `;` are only structure OUTSIDE a literal.

    The previous splitter stripped comments by regex and then checked the quote
    count came out even. That passes for two literals each containing `--`
    (their quotes rebalance while both are truncated) and for a `;` inside a
    literal (the quotes never move), and both ship half a statement to D1.
    """

    def test_comment_marker_inside_a_literal_is_not_a_comment(self):
        from apps.api.routers.migrations import _statements

        out = _statements("ALTER TABLE T ADD COLUMN c TEXT DEFAULT 'a--b';")
        assert out == ["ALTER TABLE T ADD COLUMN c TEXT DEFAULT 'a--b'"]

    def test_two_literals_with_comment_markers_do_not_rebalance_into_silence(self):
        from apps.api.routers.migrations import _statements

        out = _statements(
            "INSERT INTO T VALUES ('a--b');\nINSERT INTO T VALUES ('c--d');\n"
        )
        assert out == [
            "INSERT INTO T VALUES ('a--b')",
            "INSERT INTO T VALUES ('c--d')",
        ]

    def test_semicolon_inside_a_literal_does_not_split(self):
        from apps.api.routers.migrations import _statements

        out = _statements("INSERT INTO T VALUES ('a;b');")
        assert out == ["INSERT INTO T VALUES ('a;b')"]

    def test_escaped_quote_does_not_end_the_literal(self):
        from apps.api.routers.migrations import _statements

        out = _statements("INSERT INTO T VALUES ('it''s; fine');")
        assert out == ["INSERT INTO T VALUES ('it''s; fine')"]

    def test_real_comments_are_still_dropped(self):
        from apps.api.routers.migrations import _statements

        out = _statements(
            "-- header comment\nALTER TABLE T ADD COLUMN c TEXT;  -- trailing\n"
        )
        assert out == ["ALTER TABLE T ADD COLUMN c TEXT"]

    def test_unterminated_literal_raises(self):
        from apps.api.routers.migrations import _statements

        with pytest.raises(ValueError, match="unterminated"):
            _statements("INSERT INTO T VALUES ('oops;")
