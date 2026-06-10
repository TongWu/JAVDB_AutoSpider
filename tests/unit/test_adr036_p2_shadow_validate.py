# tests/unit/test_adr036_p2_shadow_validate.py
"""Tests for shadow vs authoritative cross-validation."""
import io
import sqlite3
from unittest.mock import patch

import pytest

from javdb.ops.reconcile.shadow_validate import (
    ShadowValidateResult,
    compare_shadow_to_authoritative,
)

_SHADOW_DDL = """
CREATE TABLE AcquisitionOutcomeShadow (
  qb_hash      TEXT PRIMARY KEY NOT NULL,
  href         TEXT NOT NULL DEFAULT '',
  video_code   TEXT,
  category     TEXT,
  state        TEXT NOT NULL DEFAULT 'queued'
      CHECK (state IN ('queued','completed')),
  queued_at    TEXT,
  completed_at TEXT,
  session_id   TEXT,
  updated_at   TEXT NOT NULL
);
"""
_AUTH_DDL = """
CREATE TABLE AcquisitionOutcome (
  qb_hash       TEXT PRIMARY KEY NOT NULL,
  href          TEXT NOT NULL DEFAULT '',
  video_code    TEXT,
  category      TEXT,
  state         TEXT NOT NULL DEFAULT 'queued'
    CHECK (state IN ('queued','downloading','completed','in_library','stalled','failed')),
  queued_at     TEXT,
  completed_at  TEXT,
  landed_at     TEXT,
  last_seen_at  TEXT,
  session_id    TEXT
);
"""


@pytest.fixture
def shadow_conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_SHADOW_DDL)
    return c


@pytest.fixture
def auth_conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_AUTH_DDL)
    return c


def _insert_shadow(conn, qb_hash, state="queued"):
    conn.execute(
        "INSERT INTO AcquisitionOutcomeShadow (qb_hash, state, updated_at) "
        "VALUES (?, ?, '2026-06-10T00:00:00Z')",
        [qb_hash, state],
    )
    conn.commit()


def _insert_auth(conn, qb_hash, state="queued"):
    conn.execute(
        "INSERT INTO AcquisitionOutcome (qb_hash, state) VALUES (?, ?)",
        [qb_hash, state],
    )
    conn.commit()


def test_clean_when_both_match(shadow_conn, auth_conn):
    _insert_shadow(shadow_conn, "h1", "queued")
    _insert_auth(auth_conn, "h1", "queued")
    result = compare_shadow_to_authoritative(
        shadow_conn=shadow_conn, auth_conn=auth_conn
    )
    assert result.is_clean
    assert result.missing_from_shadow == []
    assert result.missing_from_auth == []
    assert result.state_mismatches == []


def test_missing_from_shadow(shadow_conn, auth_conn):
    _insert_auth(auth_conn, "h2", "queued")
    result = compare_shadow_to_authoritative(
        shadow_conn=shadow_conn, auth_conn=auth_conn
    )
    assert "h2" in result.missing_from_shadow
    assert not result.is_clean


def test_missing_from_auth(shadow_conn, auth_conn):
    _insert_shadow(shadow_conn, "h3", "queued")
    result = compare_shadow_to_authoritative(
        shadow_conn=shadow_conn, auth_conn=auth_conn
    )
    assert "h3" in result.missing_from_auth
    assert not result.is_clean


def test_state_mismatch_queued_vs_completed(shadow_conn, auth_conn):
    _insert_shadow(shadow_conn, "h4", "queued")
    _insert_auth(auth_conn, "h4", "completed")
    result = compare_shadow_to_authoritative(
        shadow_conn=shadow_conn, auth_conn=auth_conn
    )
    assert len(result.state_mismatches) == 1
    assert result.state_mismatches[0]["qb_hash"] == "h4"
    assert not result.is_clean


def test_auth_in_library_not_flagged_as_mismatch(shadow_conn, auth_conn):
    """Shadow queued, auth in_library — this is expected; not a mismatch."""
    _insert_shadow(shadow_conn, "h5", "queued")
    _insert_auth(auth_conn, "h5", "in_library")
    result = compare_shadow_to_authoritative(
        shadow_conn=shadow_conn, auth_conn=auth_conn
    )
    # h5 is in both; auth is in_library so we don't compare states
    assert result.state_mismatches == []


def test_auth_downloading_not_flagged(shadow_conn, auth_conn):
    _insert_shadow(shadow_conn, "h6", "queued")
    _insert_auth(auth_conn, "h6", "downloading")
    result = compare_shadow_to_authoritative(
        shadow_conn=shadow_conn, auth_conn=auth_conn
    )
    assert result.state_mismatches == []


def test_counts_are_correct(shadow_conn, auth_conn):
    _insert_shadow(shadow_conn, "h7", "queued")
    _insert_shadow(shadow_conn, "h8", "completed")
    _insert_auth(auth_conn, "h7", "queued")
    _insert_auth(auth_conn, "h8", "completed")
    _insert_auth(auth_conn, "h9", "in_library")
    result = compare_shadow_to_authoritative(
        shadow_conn=shadow_conn, auth_conn=auth_conn
    )
    assert result.shadow_total == 2
    assert result.auth_total == 3
    assert result.is_clean


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

def test_cli_main_exits_zero_and_prints_report(capsys):
    """CLI main() exits 0 when projection is clean and prints a JSON report."""
    from apps.cli.ops.shadow_validate import main

    clean_result = ShadowValidateResult(
        shadow_total=3,
        auth_total=3,
        missing_from_shadow=[],
        missing_from_auth=[],
        state_mismatches=[],
        errors=[],
    )
    with patch(
        "apps.cli.ops.shadow_validate.compare_shadow_to_authoritative",
        return_value=clean_result,
    ):
        rc = main([])

    assert rc == 0
    captured = capsys.readouterr()
    import json
    report = json.loads(captured.out)
    assert report["is_clean"] is True
    assert report["shadow_total"] == 3
    assert report["auth_total"] == 3


def test_cli_main_exits_one_on_discrepancies(capsys):
    """CLI main() exits 1 when discrepancies are found."""
    from apps.cli.ops.shadow_validate import main

    dirty_result = ShadowValidateResult(
        shadow_total=2,
        auth_total=2,
        missing_from_shadow=["abc123"],
        missing_from_auth=[],
        state_mismatches=[],
        errors=[],
    )
    with patch(
        "apps.cli.ops.shadow_validate.compare_shadow_to_authoritative",
        return_value=dirty_result,
    ):
        rc = main([])

    assert rc == 1
    captured = capsys.readouterr()
    import json
    report = json.loads(captured.out)
    assert report["is_clean"] is False
    assert "abc123" in report["missing_from_shadow"]


def test_errors_collected_and_result_not_clean():
    """A failure in _load_rows is captured in errors and makes is_clean False."""
    with patch(
        "javdb.ops.reconcile.shadow_validate._load_rows",
        side_effect=RuntimeError("DB connection failed"),
    ):
        result = compare_shadow_to_authoritative()
    assert len(result.errors) == 1
    assert "DB connection failed" in result.errors[0]
    assert result.is_clean is False


def test_cli_main_exits_two_on_errors(capsys):
    """CLI main() exits 2 when the comparison reports errors."""
    from apps.cli.ops.shadow_validate import main

    error_result = ShadowValidateResult(errors=["boom"])
    with patch(
        "apps.cli.ops.shadow_validate.compare_shadow_to_authoritative",
        return_value=error_result,
    ):
        rc = main([])
    assert rc == 2
    import json
    report = json.loads(capsys.readouterr().out)
    assert report["is_clean"] is False
    assert report["errors"] == ["boom"]
