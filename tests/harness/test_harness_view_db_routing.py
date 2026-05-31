"""Regression guard: harness view methods must read from the correct logical DB.

``HarnessResult.events()`` reads ``PipelineEvent`` (reports DB, ADR-036) and
``acquisition_outcomes()`` reads ``AcquisitionOutcome`` (operations DB, ADR-033).
Both previously called ``get_db()`` with no argument, which defaults to
``HISTORY_DB_PATH`` — the wrong DB. The query hit "no such table", the
surrounding ``try/except`` swallowed it, and the method always returned ``[]``.

These tests would NOT catch that bug as-is: the autouse ``_isolate_sqlite``
fixture collapses all three logical DB paths onto one file, so a read through
the history connection still finds the table. To make the bug observable we
repoint ``HISTORY_DB_PATH`` at a fresh, table-less file. The buggy code then
sees "no such table" and returns ``[]``; the fixed code (reports/operations
path) finds the seeded row.
"""

import javdb.storage.db._db_connection as _db_conn_mod
from javdb.storage import db as _db
from javdb.storage.db import get_db


def _diverge_history_db(monkeypatch, tmp_path):
    """Point HISTORY_DB_PATH at its own empty (table-less) file so a history
    connection can no longer accidentally serve reports/operations tables."""
    monkeypatch.setattr(_db_conn_mod, "HISTORY_DB_PATH", str(tmp_path / "history_only.db"))


def test_events_reads_from_reports_db(pipeline_harness, monkeypatch, tmp_path):
    # Seed an event into the reports DB (mirrors javdb.pipeline.events.store).
    with get_db(_db.REPORTS_DB_PATH) as conn:
        conn.execute(
            "INSERT INTO PipelineEvent "
            "(event_type, session_id, entity_type, created_at) VALUES (?, ?, ?, ?)",
            ("scrape.completed", "sess-1", "movie", "2026-05-31T00:00:00Z"),
        )

    _diverge_history_db(monkeypatch, tmp_path)

    # Buggy (get_db() -> history) returns []; fixed (reports DB) returns the row.
    assert pipeline_harness.events() == ["scrape.completed"]


def test_acquisition_outcomes_reads_from_operations_db(pipeline_harness, monkeypatch, tmp_path):
    # Seed an outcome into the operations DB.
    with get_db(_db.OPERATIONS_DB_PATH) as conn:
        conn.execute(
            "INSERT INTO AcquisitionOutcome (qb_hash, state) VALUES (?, ?)",
            ("abc123", "completed"),
        )

    _diverge_history_db(monkeypatch, tmp_path)

    # Buggy (get_db() -> history) returns []; fixed (operations DB) returns the row.
    assert pipeline_harness.acquisition_outcomes() == [{"qb_hash": "abc123", "state": "completed"}]
