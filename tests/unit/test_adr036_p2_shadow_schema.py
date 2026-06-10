# tests/unit/test_adr036_p2_shadow_schema.py
"""
Verifies AcquisitionOutcomeShadow table exists with expected columns in
both the D1 migration SQL and the _REPORTS_DDL SQLite mirror.
"""
import sqlite3
from pathlib import Path

import pytest

_MIGRATION_PATH = (
    Path(__file__).parent.parent.parent
    / "javdb/migrations/d1/2026_06_10_add_acquisition_outcome_shadow.sql"
)

_EXPECTED_COLUMNS = {
    "qb_hash", "href", "video_code", "category",
    "state", "queued_at", "completed_at", "session_id", "updated_at",
}


def _build_schema(sql: str) -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.executescript(sql)
    return c


def _column_names(conn: sqlite3.Connection, table: str) -> set:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def test_d1_migration_file_exists():
    assert _MIGRATION_PATH.exists(), f"Migration file not found: {_MIGRATION_PATH}"


def test_d1_migration_creates_table_with_expected_columns():
    sql = _MIGRATION_PATH.read_text(encoding="utf-8")
    conn = _build_schema(sql)
    cols = _column_names(conn, "AcquisitionOutcomeShadow")
    assert _EXPECTED_COLUMNS <= cols, f"Missing columns: {_EXPECTED_COLUMNS - cols}"


def test_local_ddl_mirror_creates_table_with_expected_columns(_isolate_sqlite):
    """The autouse _isolate_sqlite fixture runs init_db, which applies _REPORTS_DDL.
    If AcquisitionOutcomeShadow is in _REPORTS_DDL, the table exists."""
    conn = sqlite3.connect(_isolate_sqlite)
    cols = _column_names(conn, "AcquisitionOutcomeShadow")
    assert _EXPECTED_COLUMNS <= cols, (
        "AcquisitionOutcomeShadow missing from _REPORTS_DDL mirror. "
        f"Missing columns: {_EXPECTED_COLUMNS - cols}"
    )
    conn.close()


def test_state_column_allows_queued_and_completed(_isolate_sqlite):
    """Shadow state only permits queued|completed (not the richer authoritative set).

    Note: href is NOT NULL so must be supplied explicitly; the SQLite init path
    removes DEFAULT clauses via _migrate_defaults_to_null (schema version upgrade).
    """
    conn = sqlite3.connect(_isolate_sqlite)
    conn.execute(
        "INSERT INTO AcquisitionOutcomeShadow "
        "(qb_hash, href, state, updated_at) VALUES (?, ?, ?, ?)",
        ["hash-q", "", "queued", "2026-06-10T00:00:00Z"],
    )
    conn.execute(
        "INSERT INTO AcquisitionOutcomeShadow "
        "(qb_hash, href, state, updated_at) VALUES (?, ?, ?, ?)",
        ["hash-c", "", "completed", "2026-06-10T00:00:00Z"],
    )
    conn.commit()

    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO AcquisitionOutcomeShadow "
            "(qb_hash, href, state, updated_at) VALUES (?, ?, ?, ?)",
            ["hash-x", "", "in_library", "2026-06-10T00:00:00Z"],
        )
        conn.commit()
    conn.close()
