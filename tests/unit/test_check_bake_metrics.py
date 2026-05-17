"""Unit coverage for `apps.cli.ops.check_bake_metrics`.

Focus is on the bits that have non-trivial logic and would silently
regress: window/since resolution, the critical-fields predicate +
(run_id, attempt) dedup in the pause-trigger counter, the orphan-audit
cross-DB join, and the audit-session-count threshold.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from apps.cli.ops import check_bake_metrics as mod


# ── _resolve_window ────────────────────────────────────────────────────


def _ns(**overrides) -> argparse.Namespace:
    defaults = dict(
        since=None, window_days=30, reports_db=Path("reports.db"),
        history_db=Path("history.db"), jsonl=Path("d.jsonl"), json=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_resolve_window_default_uses_30_days():
    since_str, since_dt, days = mod._resolve_window(_ns())
    now = datetime.now(tz=timezone.utc)
    # ~30 days ago, allow a few seconds of slop for the call sequence.
    assert days == 30
    assert (now - since_dt) - timedelta(days=30) < timedelta(seconds=5)
    # SQLite string is ``%Y-%m-%d %H:%M:%S`` (no T, no Z, no tz).
    parsed = datetime.strptime(since_str, "%Y-%m-%d %H:%M:%S")
    assert parsed.tzinfo is None


def test_resolve_window_honours_explicit_since_date():
    since_str, since_dt, days = mod._resolve_window(_ns(since="2026-05-16"))
    assert since_dt == datetime(2026, 5, 16, tzinfo=timezone.utc)
    # window_days is computed from now - since; on 2026-05-16 itself or
    # later it must be ≥ 1 (we clamp the lower bound).
    assert days >= 1


def test_resolve_window_honours_iso_timestamp_with_z():
    _, since_dt, _ = mod._resolve_window(
        _ns(since="2026-05-16T14:30:00Z"),
    )
    assert since_dt == datetime(2026, 5, 16, 14, 30, tzinfo=timezone.utc)


def test_resolve_window_clamps_below_one_day():
    """A fractional window (e.g. anchored to today) must still report
    at least 1 day so the threshold scaling never returns 0."""
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    _, _, days = mod._resolve_window(_ns(since=today))
    assert days >= 1


# ── check_audit_session_count ──────────────────────────────────────────


def _make_reports_db(tmp_path: Path, sessions: list) -> Path:
    """Create a minimal reports.db with the columns the check reads."""
    path = tmp_path / "reports.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE ReportSessions("
        " Id TEXT PRIMARY KEY,"
        " WriteMode TEXT,"
        " Status TEXT,"
        " DateTimeCreated TEXT"
        ")"
    )
    conn.executemany(
        "INSERT INTO ReportSessions(Id, WriteMode, Status, DateTimeCreated) "
        "VALUES (?, ?, ?, ?)",
        sessions,
    )
    conn.commit()
    conn.close()
    return path


def test_audit_session_count_passes_when_zero(tmp_path):
    db = _make_reports_db(tmp_path, [
        ("s1", "pending", "committed", "2026-05-16 12:00:00"),
        ("s2", "pending", "committed", "2026-05-15 12:00:00"),
    ])
    result = mod.check_audit_session_count(db, since="2026-05-10 00:00:00")
    assert result.passed is True
    assert result.actual == 0
    assert result.samples == []


def test_audit_session_count_fails_when_audit_session_in_window(tmp_path):
    db = _make_reports_db(tmp_path, [
        ("audit-1", "audit", "committed", "2026-05-16 12:00:00"),
        ("audit-2", "audit", "failed", "2026-05-15 12:00:00"),
        ("pending-1", "pending", "committed", "2026-05-14 12:00:00"),
    ])
    result = mod.check_audit_session_count(db, since="2026-05-10 00:00:00")
    assert result.passed is False
    assert result.actual == 2
    assert set(result.samples) == {"audit-1", "audit-2"}


def test_audit_session_count_excludes_pre_window_audit(tmp_path):
    db = _make_reports_db(tmp_path, [
        ("old-audit", "audit", "committed", "2026-04-01 12:00:00"),
        ("new-pending", "pending", "committed", "2026-05-16 12:00:00"),
    ])
    result = mod.check_audit_session_count(db, since="2026-05-10 00:00:00")
    assert result.passed is True
    assert result.actual == 0


def test_audit_session_count_reports_missing_db(tmp_path):
    result = mod.check_audit_session_count(
        tmp_path / "absent.db", since="2026-05-10 00:00:00",
    )
    assert result.passed is False
    assert result.actual == -1
    assert "not found" in result.detail


# ── check_orphan_audit_rows ────────────────────────────────────────────


def _make_history_db(tmp_path: Path, movie_rows: list, torrent_rows: list) -> Path:
    path = tmp_path / "history.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE MovieHistoryAudit("
        " Id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " SessionId TEXT NOT NULL,"
        " OldRowJson TEXT"
        ")"
    )
    conn.execute(
        "CREATE TABLE TorrentHistoryAudit("
        " Id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " SessionId TEXT NOT NULL,"
        " OldRowJson TEXT"
        ")"
    )
    for sid in movie_rows:
        conn.execute(
            "INSERT INTO MovieHistoryAudit(SessionId, OldRowJson) VALUES (?, '{}')",
            (sid,),
        )
    for sid in torrent_rows:
        conn.execute(
            "INSERT INTO TorrentHistoryAudit(SessionId, OldRowJson) VALUES (?, '{}')",
            (sid,),
        )
    conn.commit()
    conn.close()
    return path


def test_orphan_audit_rows_passes_when_no_committed_owners(tmp_path):
    reports = _make_reports_db(tmp_path, [
        ("alive", "audit", "in_progress", "2026-05-16 12:00:00"),
    ])
    history = _make_history_db(tmp_path, ["alive"], ["alive"])
    result = mod.check_orphan_audit_rows(history, reports)
    assert result.passed is True
    assert result.actual == 0


def test_orphan_audit_rows_fails_when_audit_outlives_committed_session(tmp_path):
    reports = _make_reports_db(tmp_path, [
        ("done", "audit", "committed", "2026-05-16 12:00:00"),
        ("alive", "audit", "in_progress", "2026-05-16 13:00:00"),
    ])
    history = _make_history_db(
        tmp_path,
        movie_rows=["done", "done", "alive"],
        torrent_rows=["done"],
    )
    result = mod.check_orphan_audit_rows(history, reports)
    assert result.passed is False
    # 2 MovieHistoryAudit + 1 TorrentHistoryAudit owned by 'done' (committed).
    assert result.actual == 3
    assert "MovieHistoryAudit orphans=2" in result.detail
    assert "TorrentHistoryAudit orphans=1" in result.detail


def test_orphan_audit_rows_passes_when_audit_tables_already_dropped(tmp_path):
    """ADR-005 PR-5 drops the audit tables. Once that ships, the check
    must still return ``passed=True`` (the gate's purpose is satisfied
    in the extreme — there are no audit rows to be orphaned)."""
    reports = _make_reports_db(tmp_path, [])
    # history.db with NO audit tables — only a placeholder so the file exists.
    path = tmp_path / "history.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE Placeholder(x INTEGER)")
    conn.commit()
    conn.close()
    result = mod.check_orphan_audit_rows(path, reports)
    assert result.passed is True
    assert result.actual == 0
    assert "dropped" in result.detail.lower()


# ── check_pause_trigger_count ─────────────────────────────────────────


def _write_jsonl(path: Path, records: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def test_pause_trigger_count_passes_when_no_critical_records(tmp_path):
    jsonl = tmp_path / "d.jsonl"
    now = _now_utc()
    _write_jsonl(jsonl, [
        {
            "kind": "pending_session_verify",
            "ts": _iso(now),
            "run_id": "100", "run_attempt": "1",
            "pending_residual_count": 0,
            "derived_recompute_drift": 0,
            "cleanup_path_mismatch_count": 0,
        },
        {"kind": "rollback_summary", "ts": _iso(now)},  # wrong kind
    ])
    result = mod.check_pause_trigger_count(
        jsonl, since_ts=now - timedelta(hours=1), window_days=30,
    )
    assert result.passed is True
    assert result.actual == 0


def test_pause_trigger_count_counts_distinct_runs(tmp_path):
    jsonl = tmp_path / "d.jsonl"
    now = _now_utc()
    _write_jsonl(jsonl, [
        # Same (run_id, attempt) appears twice — count as 1.
        {
            "kind": "pending_session_verify",
            "ts": _iso(now),
            "run_id": "100", "run_attempt": "1",
            "pending_residual_count": 1,
            "derived_recompute_drift": 0,
            "cleanup_path_mismatch_count": 0,
        },
        {
            "kind": "pending_session_verify",
            "ts": _iso(now),
            "run_id": "100", "run_attempt": "1",
            "pending_residual_count": 0,
            "derived_recompute_drift": 2,
            "cleanup_path_mismatch_count": 0,
        },
        # Different attempt — count as a separate trigger.
        {
            "kind": "pending_session_verify",
            "ts": _iso(now),
            "run_id": "100", "run_attempt": "2",
            "pending_residual_count": 0,
            "derived_recompute_drift": 0,
            "cleanup_path_mismatch_count": 3,
        },
    ])
    result = mod.check_pause_trigger_count(
        jsonl, since_ts=now - timedelta(hours=1), window_days=30,
    )
    assert result.actual == 2


def test_pause_trigger_count_skips_records_before_since(tmp_path):
    jsonl = tmp_path / "d.jsonl"
    now = _now_utc()
    long_ago = now - timedelta(days=90)
    _write_jsonl(jsonl, [
        {
            "kind": "pending_session_verify",
            "ts": _iso(long_ago),
            "run_id": "ancient", "run_attempt": "1",
            "pending_residual_count": 5,
            "derived_recompute_drift": 0,
            "cleanup_path_mismatch_count": 0,
        },
        {
            "kind": "pending_session_verify",
            "ts": _iso(now),
            "run_id": "recent", "run_attempt": "1",
            "pending_residual_count": 7,
            "derived_recompute_drift": 0,
            "cleanup_path_mismatch_count": 0,
        },
    ])
    result = mod.check_pause_trigger_count(
        jsonl, since_ts=now - timedelta(hours=1), window_days=30,
    )
    assert result.actual == 1
    assert "session=" in result.samples[0]


def test_pause_trigger_count_threshold_scales_with_window(tmp_path):
    """1/month threshold scales to ceil(N/30) for an N-day window — so
    a 1-day window allows 1 trigger; a 30-day window allows 1; a
    60-day window allows 2."""
    jsonl = tmp_path / "d.jsonl"
    _write_jsonl(jsonl, [])  # empty, just to exercise the threshold path
    now = _now_utc()
    for days, expected_threshold in [(1, 1), (30, 1), (45, 2), (60, 2), (90, 3)]:
        result = mod.check_pause_trigger_count(
            jsonl, since_ts=now - timedelta(days=days), window_days=days,
        )
        assert result.threshold == expected_threshold, (
            f"window_days={days}: got {result.threshold}, "
            f"expected {expected_threshold}"
        )


def test_pause_trigger_count_passes_when_jsonl_missing(tmp_path):
    """No jsonl on a fresh checkout is the bake-yet-to-start signal,
    not a failure."""
    result = mod.check_pause_trigger_count(
        tmp_path / "no_such_file.jsonl",
        since_ts=_now_utc() - timedelta(days=1),
    )
    assert result.passed is True
    assert result.actual == 0
    assert "no jsonl" in result.detail.lower()


# ── main() integration smoke ──────────────────────────────────────────


def test_main_returns_0_when_all_pass(tmp_path, capsys):
    reports = _make_reports_db(tmp_path, [
        ("s1", "pending", "committed", "2026-05-16 12:00:00"),
    ])
    history = _make_history_db(tmp_path, [], [])
    jsonl = tmp_path / "d.jsonl"
    _write_jsonl(jsonl, [])
    rc = mod.main([
        "--reports-db", str(reports),
        "--history-db", str(history),
        "--jsonl", str(jsonl),
        "--since", "2026-05-10",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "RESULT: PASS" in out


def test_main_returns_1_when_any_check_fails(tmp_path, capsys):
    reports = _make_reports_db(tmp_path, [
        ("audit-leaker", "audit", "in_progress", "2026-05-16 12:00:00"),
    ])
    history = _make_history_db(tmp_path, [], [])
    jsonl = tmp_path / "d.jsonl"
    _write_jsonl(jsonl, [])
    rc = mod.main([
        "--reports-db", str(reports),
        "--history-db", str(history),
        "--jsonl", str(jsonl),
        "--since", "2026-05-10",
    ])
    assert rc == 1
    out = capsys.readouterr().out
    assert "RESULT: FAIL" in out
    assert "audit-leaker" in out


def test_main_emits_json_when_requested(tmp_path, capsys):
    reports = _make_reports_db(tmp_path, [])
    history = _make_history_db(tmp_path, [], [])
    jsonl = tmp_path / "d.jsonl"
    _write_jsonl(jsonl, [])
    rc = mod.main([
        "--reports-db", str(reports),
        "--history-db", str(history),
        "--jsonl", str(jsonl),
        "--since", "2026-05-10",
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True
    assert {c["name"] for c in payload["checks"]} == {
        "audit_session_count",
        "orphan_audit_rows",
        "pause_trigger_count",
    }
