"""Capability-probe honesty test for the ADR-054 ``watch_intent`` flag (WS1).

Closes the B3 coverage gap surfaced by the WS1 verification: the existing
capabilities tests only assert the ``watch_intent`` key is present and is a bool —
nothing pinned that the probe actually *flips* with the real presence of the
``WatchIntent`` table in ``HISTORY_DB``. A probe that silently returned a constant
would have passed those tests while lying to the UI. These cases toggle the table
and assert the probe (and the served capability) tracks reality.
"""

import pathlib
import sqlite3

from javdb.storage import db as _db
from apps.api.routers import capabilities as _caps


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_WATCH_INTENT_DDL = (
    _REPO_ROOT / "javdb/migrations/d1/2026_06_13_add_watch_intent.sql"
).read_text(encoding="utf-8")


def _history_db(tmp_path, *, with_table: bool, name: str = "history.db") -> str:
    """Create a temp sqlite HISTORY_DB, optionally with the WatchIntent table.

    ``name`` lets one test materialise both states as distinct files so the
    present-table case never leaks into the absent-table case.
    """
    path = str(tmp_path / name)
    conn = sqlite3.connect(path)
    if with_table:
        conn.executescript(_WATCH_INTENT_DDL)
        conn.commit()
    conn.close()
    return path


def test_probe_true_when_table_present(tmp_path, monkeypatch):
    monkeypatch.setattr(_db, "HISTORY_DB_PATH", _history_db(tmp_path, with_table=True))
    assert _caps._watch_intent_enabled() is True


def test_probe_false_when_table_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(_db, "HISTORY_DB_PATH", _history_db(tmp_path, with_table=False))
    assert _caps._watch_intent_enabled() is False


def test_served_capability_reflects_table_presence(tmp_path, monkeypatch):
    """The probe must reach the capabilities payload (honest gating, both states)."""
    monkeypatch.setattr(
        _db, "HISTORY_DB_PATH", _history_db(tmp_path, with_table=True, name="present.db")
    )
    assert _caps.build_capabilities().features.watch_intent is True

    monkeypatch.setattr(
        _db, "HISTORY_DB_PATH", _history_db(tmp_path, with_table=False, name="absent.db")
    )
    assert _caps.build_capabilities().features.watch_intent is False
