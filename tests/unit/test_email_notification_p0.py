"""P0-5 / P0-6 regression tests for ``email_notification.py``.

* **P0-5** — when SMTP send fails, ``main()`` must exit with a non-zero
  status code so the CI workflow surfaces the failure. Previously the
  script always returned ``sys.exit(0)``, which made the pipeline look
  "notified" even when no email was ever delivered.
* **P0-6** — under STORAGE_BACKEND=dual, stats must be sourced from the
  canonical SQLite mirror (never D1), and any drift recorded in
  ``reports/D1/d1_drift.jsonl`` since 00:00 UTC must surface as a
  banner at the **top** of the email body.

These tests exercise the real helpers in
``javdb.integrations.notify.email`` rather than
local re-implementations so changes to the production code path are
caught here.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

# Add project root so the spider imports resolve.
project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if project_root not in sys.path:
    sys.path.insert(0, project_root)


# ── P0-6: drift advisory builder ─────────────────────────────────────────


def test_drift_advisory_returns_empty_when_jsonl_missing(tmp_path):
    from javdb.integrations.notify.email import (
        _build_dual_drift_advisory,
    )

    # No reports/D1 directory at all — banner must be empty.
    advisory = _build_dual_drift_advisory(str(tmp_path))
    assert advisory == ""


def test_drift_advisory_returns_empty_when_jsonl_has_no_today_records(tmp_path):
    from javdb.integrations.notify.email import (
        _build_dual_drift_advisory,
    )

    drift_dir = tmp_path / "D1"
    drift_dir.mkdir()
    jsonl = drift_dir / "d1_drift.jsonl"
    # Stale record from a year ago — must be ignored.
    jsonl.write_text(json.dumps({
        "ts": "2025-01-01T00:00:00Z",
        "db": "history",
        "committed": True,
        "failure_count": 1,
        "uncommitted_d1_writes": 0,
        "first_failed_sql": "INSERT INTO foo VALUES (?)",
        "first_error": "boom",
    }) + "\n", encoding="utf-8")

    advisory = _build_dual_drift_advisory(str(tmp_path))
    assert advisory == ""


def test_drift_advisory_surfaces_todays_records(tmp_path):
    from javdb.integrations.notify.email import (
        _build_dual_drift_advisory,
    )

    drift_dir = tmp_path / "D1"
    drift_dir.mkdir()
    jsonl = drift_dir / "d1_drift.jsonl"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT12:00:00Z")
    jsonl.write_text(
        json.dumps({
            "ts": today,
            "db": "history",
            "committed": True,
            "failure_count": 2,
            "uncommitted_d1_writes": 3,
            "first_failed_sql": "INSERT INTO ReportSessions ...",
            "first_error": "RuntimeError: simulated",
        }) + "\n", encoding="utf-8",
    )

    advisory = _build_dual_drift_advisory(str(tmp_path))
    assert "D1 DRIFT ADVISORY" in advisory
    assert "cumulative D1 write failures today: 2" in advisory
    assert "rows D1 kept after SQLite rollback today: 3" in advisory
    assert "sync_d1_to_sqlite.py" in advisory
    assert "ReportSessions" in advisory


def test_drift_advisory_returns_empty_for_clean_pending_verify(tmp_path):
    """A pending_session_verify record with zero residuals is informational, not drift."""
    from javdb.integrations.notify.email import (
        _build_dual_drift_advisory,
    )

    drift_dir = tmp_path / "D1"
    drift_dir.mkdir()
    jsonl = drift_dir / "d1_drift.jsonl"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT12:00:00Z")
    jsonl.write_text(
        json.dumps({
            "ts": today,
            "kind": "pending_session_verify",
            "session_id": "20260519T210553.000000Z-0000-0000",
            "pending_residual_count": 0,
            "failure_count": 0,
            "uncommitted_d1_writes": 0,
            "derived_recompute_drift": 0,
        }) + "\n", encoding="utf-8",
    )

    advisory = _build_dual_drift_advisory(str(tmp_path))
    assert advisory == "", (
        "Clean pending_session_verify records must not trigger the drift advisory"
    )


# ── P0-6: SQLite-local stats readers ─────────────────────────────────────


def test_local_stats_getter_uses_sqlite_regardless_of_backend(monkeypatch, tmp_path):
    """``db_get_spider_stats_local`` always opens SQLite directly.

    Even when the configured backend would normally route reads to D1,
    this helper must skip the dual layer entirely. We initialise the
    schema under ``STORAGE_BACKEND=sqlite`` (so no D1 wiring is needed)
    and then flip the env var to ``dual`` before calling the ``_local``
    getter; if the helper had silently routed through :class:`DualConnection`
    it would have tried to contact a non-existent D1 endpoint and the
    test would have raised ``ValueError: No D1 logical-name mapping``.
    """
    import sqlite3

    monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
    from javdb.storage.db import db as db_mod

    test_db = tmp_path / "reports.db"
    # Lay down just enough schema for the read path under test. Using
    # raw SQLite avoids dragging in the full migration / DualConnection
    # bootstrap, which would require D1 mappings.
    conn = sqlite3.connect(str(test_db))
    conn.executescript(
        "CREATE TABLE SpiderStats (Id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "SessionId INTEGER, TotalDiscovered INTEGER);"
    )
    conn.execute(
        "INSERT INTO SpiderStats (SessionId, TotalDiscovered) VALUES (?, ?)",
        (12345, 999),
    )
    conn.commit()
    conn.close()

    # Now pretend we're in dual mode. The ``_local`` reader must NOT
    # care: it must always open raw SQLite.
    monkeypatch.setenv("STORAGE_BACKEND", "dual")

    out = db_mod.db_get_spider_stats_local(12345, db_path=str(test_db))
    assert out is not None
    assert out["TotalDiscovered"] == 999


# ── P0-5: main() exit code on SMTP failure ───────────────────────────────


def test_main_exits_nonzero_on_smtp_failure(monkeypatch, tmp_path):
    """End-to-end smoke: SMTP failure must produce a non-zero exit.

    Patching the heavy collaborators is unavoidable here because the
    ``main()`` function pulls in a lot of file-IO / state setup, but
    the key contract — "SMTP raised → sys.exit(2)" — is what we lock
    down. The rest of the function is exercised by
    ``test_email_notification_extended.py``.
    """
    from javdb.integrations.notify import email as en

    # Stub send_email to return False so the new exit path triggers.
    monkeypatch.setattr(en, "send_email", lambda *a, **kw: False)
    # Stub everything else main() needs so the call doesn't actually
    # walk real files / dbs. We only care that the exit code propagates.
    monkeypatch.setattr(en, "_resolve_default_verify_jsonl", lambda x: None)
    monkeypatch.setattr(en, "_resolve_default_health_snapshot", lambda x: None)
    monkeypatch.setattr(en, "find_proxy_ban_html_files", lambda d=None: [])
    monkeypatch.setattr(en, "extract_proxy_ban_summary", lambda *a, **k: {})
    monkeypatch.setattr(en, "analyze_spider_log", lambda p: (False, None, False))
    monkeypatch.setattr(en, "analyze_uploader_log", lambda p: (False, None, False))
    monkeypatch.setattr(en, "analyze_pikpak_log", lambda p: (False, None, False))
    monkeypatch.setattr(en, "analyze_pipeline_log", lambda p: (False, None, False))
    monkeypatch.setattr(en, "check_workflow_job_status", lambda: (False, []))
    monkeypatch.setattr(en, "extract_spider_statistics", lambda p: None)
    monkeypatch.setattr(en, "extract_uploader_statistics", lambda p: None)
    monkeypatch.setattr(en, "extract_pikpak_statistics", lambda p: None)
    monkeypatch.setattr(en, "extract_dedup_statistics", lambda *a, **k: None)
    monkeypatch.setattr(en, "find_latest_adhoc_csv", lambda d: None)
    monkeypatch.setattr(en, "find_latest_daily_csv", lambda d: None)
    monkeypatch.setattr(en, "_load_pending_verify_records", lambda *a, **k: [])
    # No dry-run so the SMTP path runs.
    monkeypatch.setattr(
        sys, "argv",
        ["email_notification", "--mode", "daily"],
    )

    with pytest.raises(SystemExit) as exc_info:
        en.main()

    # P0-5 contract: must NOT be 0 when send_email returns False.
    assert exc_info.value.code != 0, (
        "P0-5 regression: email_notification main() returned 0 even though "
        "send_email reported failure."
    )
    assert exc_info.value.code == 2, (
        f"expected exit code 2 on SMTP failure, got {exc_info.value.code}"
    )


def test_main_exits_zero_when_email_succeeds(monkeypatch):
    """Mirror of the test above: ``True`` return → exit 0."""
    from javdb.integrations.notify import email as en

    monkeypatch.setattr(en, "send_email", lambda *a, **kw: True)
    monkeypatch.setattr(en, "_resolve_default_verify_jsonl", lambda x: None)
    monkeypatch.setattr(en, "_resolve_default_health_snapshot", lambda x: None)
    monkeypatch.setattr(en, "find_proxy_ban_html_files", lambda d=None: [])
    monkeypatch.setattr(en, "extract_proxy_ban_summary", lambda *a, **k: {})
    monkeypatch.setattr(en, "analyze_spider_log", lambda p: (False, None, False))
    monkeypatch.setattr(en, "analyze_uploader_log", lambda p: (False, None, False))
    monkeypatch.setattr(en, "analyze_pikpak_log", lambda p: (False, None, False))
    monkeypatch.setattr(en, "analyze_pipeline_log", lambda p: (False, None, False))
    monkeypatch.setattr(en, "check_workflow_job_status", lambda: (False, []))
    monkeypatch.setattr(en, "extract_spider_statistics", lambda p: None)
    monkeypatch.setattr(en, "extract_uploader_statistics", lambda p: None)
    monkeypatch.setattr(en, "extract_pikpak_statistics", lambda p: None)
    monkeypatch.setattr(en, "extract_dedup_statistics", lambda *a, **k: None)
    monkeypatch.setattr(en, "find_latest_adhoc_csv", lambda d: None)
    monkeypatch.setattr(en, "find_latest_daily_csv", lambda d: None)
    monkeypatch.setattr(en, "_load_pending_verify_records", lambda *a, **k: [])
    monkeypatch.setattr(
        sys, "argv",
        ["email_notification", "--mode", "daily"],
    )

    with pytest.raises(SystemExit) as exc_info:
        en.main()
    assert exc_info.value.code == 0


# ── Drift advisory backend gating ────────────────────────────────────────


def _install_main_stubs(monkeypatch, en, captured_body):
    """Stub the heavy collaborators main() touches and capture the body.

    Returns nothing; ``captured_body`` (a list) gets the email body
    string appended to it when ``send_email`` is invoked, so tests can
    assert against the final rendered body.
    """
    def _capture_send(subject, body, attachments, dry_run):
        captured_body.append(body)
        return True

    monkeypatch.setattr(en, "send_email", _capture_send)
    monkeypatch.setattr(en, "_resolve_default_verify_jsonl", lambda x: None)
    monkeypatch.setattr(en, "_resolve_default_health_snapshot", lambda x: None)
    monkeypatch.setattr(en, "find_proxy_ban_html_files", lambda d=None: [])
    monkeypatch.setattr(en, "extract_proxy_ban_summary", lambda *a, **k: {})
    monkeypatch.setattr(en, "analyze_spider_log", lambda p: (False, None, False))
    monkeypatch.setattr(en, "analyze_uploader_log", lambda p: (False, None, False))
    monkeypatch.setattr(en, "analyze_pikpak_log", lambda p: (False, None, False))
    monkeypatch.setattr(en, "analyze_pipeline_log", lambda p: (False, None, False))
    monkeypatch.setattr(en, "check_workflow_job_status", lambda: (False, []))
    monkeypatch.setattr(en, "extract_spider_statistics", lambda p: None)
    monkeypatch.setattr(en, "extract_uploader_statistics", lambda p: None)
    monkeypatch.setattr(en, "extract_pikpak_statistics", lambda p: None)
    monkeypatch.setattr(en, "extract_dedup_statistics", lambda *a, **k: None)
    monkeypatch.setattr(en, "find_latest_adhoc_csv", lambda d: None)
    monkeypatch.setattr(en, "find_latest_daily_csv", lambda d: None)
    monkeypatch.setattr(en, "_load_pending_verify_records", lambda *a, **k: [])


def _seed_drift_jsonl(tmp_path):
    """Drop a single 'real drift' record for today into reports/D1/d1_drift.jsonl."""
    drift_dir = tmp_path / "D1"
    drift_dir.mkdir()
    jsonl = drift_dir / "d1_drift.jsonl"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT12:00:00Z")
    jsonl.write_text(
        json.dumps({
            "ts": today,
            "db": "history",
            "committed": True,
            "failure_count": 2,
            "uncommitted_d1_writes": 3,
            "first_failed_sql": "INSERT INTO ReportSessions ...",
            "first_error": "RuntimeError: simulated",
        }) + "\n",
        encoding="utf-8",
    )


def test_drift_advisory_not_prepended_in_d1_only_mode(monkeypatch, tmp_path):
    """STORAGE_BACKEND=d1: no SQLite write path exists, so drift is impossible.

    Even if ``d1_drift.jsonl`` carries today's records (operational audit
    tooling like ``commit_session._emit_pending_verify`` writes to the
    same file), the email body must NOT prepend the DRIFT ADVISORY banner.
    """
    from javdb.integrations.notify import email as en

    _seed_drift_jsonl(tmp_path)
    monkeypatch.setenv("STORAGE_BACKEND", "d1")
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))

    captured = []
    _install_main_stubs(monkeypatch, en, captured)
    monkeypatch.setattr(sys, "argv", ["email_notification", "--mode", "daily"])

    with pytest.raises(SystemExit) as exc_info:
        en.main()
    assert exc_info.value.code == 0
    assert captured, "send_email was not invoked"
    assert "D1 DRIFT ADVISORY" not in captured[0], (
        "d1-only mode must not surface SQLite-vs-D1 drift banner; "
        "drift is a dual-mode-only concept."
    )


def test_drift_advisory_prepended_in_dual_mode(monkeypatch, tmp_path):
    """STORAGE_BACKEND=dual: drift banner must still surface (mirror test)."""
    from javdb.integrations.notify import email as en

    _seed_drift_jsonl(tmp_path)
    monkeypatch.setenv("STORAGE_BACKEND", "dual")
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))

    captured = []
    _install_main_stubs(monkeypatch, en, captured)
    monkeypatch.setattr(sys, "argv", ["email_notification", "--mode", "daily"])

    with pytest.raises(SystemExit) as exc_info:
        en.main()
    assert exc_info.value.code == 0
    assert captured, "send_email was not invoked"
    assert "D1 DRIFT ADVISORY" in captured[0], (
        "dual mode must continue to surface the drift advisory banner."
    )
