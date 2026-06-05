"""BFR-017 guard: D1 schema drift on the rollback/pending columns.

A ``javdb/migrations/d1/*.sql`` migration (``ReportSessions.CommittedAt``)
shipped in code but was never applied to remote D1, so every
``commit_session`` / ``rollback`` failed with ``no such column`` *after* a
full scrape. These tests pin the pre-flight guard
(:func:`find_missing_rollback_columns` + :func:`health_check.check_d1_schema`)
that now aborts the run before the spider does any work.
"""
from __future__ import annotations

import os
import re
import sqlite3

import pytest

from javdb.storage.db import get_db
from javdb.storage.db._db_migrations import (
    ROLLBACK_COLUMN_SPECS,
    find_missing_rollback_columns,
)


@pytest.fixture(autouse=True)
def _preserve_cwd():
    """Importing ``javdb.infra.health_check`` chdir()s to the repo root as an
    import side effect; restore cwd so it can't leak into sibling tests."""
    cwd = os.getcwd()
    yield
    os.chdir(cwd)


def _reportsessions_cols() -> list[str]:
    return [c for (t, c, _ddl) in ROLLBACK_COLUMN_SPECS if t == "ReportSessions"]


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeD1Conn:
    """Minimal D1-like connection returning dict rows (as the real D1 client
    does), used to prove the audit tolerates dict row shapes, not just the
    tuples a bare sqlite3 cursor yields."""

    def __init__(self, tables: dict[str, list[str]]):
        self._tables = tables
        self.closed = False

    def execute(self, sql: str, params=()):
        if "sqlite_master" in sql:
            name = params[0] if params else ""
            return _FakeCursor([{"x": 1}] if name in self._tables else [])
        if "table_info" in sql:
            m = re.search(r"table_info\('([^']+)'\)", sql)
            table = m.group(1) if m else ""
            return _FakeCursor([{"name": c} for c in self._tables.get(table, [])])
        return _FakeCursor([])

    def close(self):
        self.closed = True


# ── find_missing_rollback_columns ────────────────────────────────────────

def test_specs_match_real_schema_no_drift():
    """The unified init_db schema must satisfy every spec; otherwise the
    constant has drifted from the DDL and the guard would cry wolf."""
    with get_db() as conn:
        assert find_missing_rollback_columns(conn) == []


def test_detects_missing_committedat_the_bfr017_shape():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE ReportSessions (Id TEXT, Status TEXT)")
    missing = find_missing_rollback_columns(conn)
    assert ("ReportSessions", "CommittedAt") in missing
    conn.close()


def test_full_reportsessions_not_flagged():
    conn = sqlite3.connect(":memory:")
    cols = ", ".join(f"{c} TEXT" for c in _reportsessions_cols())
    conn.execute(f"CREATE TABLE ReportSessions (Id TEXT, {cols})")
    missing = find_missing_rollback_columns(conn)
    assert all(t != "ReportSessions" for (t, _c) in missing)
    conn.close()


def test_absent_table_not_flagged():
    """A connection lacking a table entirely yields no false positives."""
    conn = sqlite3.connect(":memory:")
    assert find_missing_rollback_columns(conn) == []
    conn.close()


def test_tolerates_dict_rows_like_d1():
    conn = _FakeD1Conn({"ReportSessions": ["Id", "Status"]})  # CommittedAt absent
    missing = find_missing_rollback_columns(conn)
    assert ("ReportSessions", "CommittedAt") in missing


# ── health_check.check_d1_schema ─────────────────────────────────────────

def test_check_d1_schema_skips_non_d1_backend(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
    from javdb.infra import health_check
    ok, msg = health_check.check_d1_schema()
    assert ok is True
    assert "Skipped" in msg


def test_check_d1_schema_reports_drift(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "d1")
    # ReportSessions present but missing CommittedAt — the BFR-017 shape.
    fake = _FakeD1Conn({
        "ReportSessions": ["Id", "Status", "RunId", "RunAttempt",
                           "FailureReason", "WriteMode"],
    })
    monkeypatch.setattr(
        "javdb.storage.d1_client.make_d1_connection",
        lambda logical: fake,
    )
    from javdb.infra import health_check
    ok, msg = health_check.check_d1_schema()
    assert ok is False
    assert "CommittedAt" in msg
    assert fake.closed is True


def test_check_d1_schema_passes_when_present(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "d1")
    full = _FakeD1Conn({
        "ReportSessions": _reportsessions_cols(),
        "MovieHistory": ["SessionId"],
        "TorrentHistory": ["SessionId"],
        "PikpakHistory": ["SessionId"],
        "DedupRecords": ["SessionId"],
        "InventoryAlignNoExactMatch": ["SessionId"],
    })
    monkeypatch.setattr(
        "javdb.storage.d1_client.make_d1_connection",
        lambda logical: full,
    )
    from javdb.infra import health_check
    ok, msg = health_check.check_d1_schema()
    assert ok is True, msg
