from __future__ import annotations

import sqlite3
from typing import NoReturn

from apps.api.routers import capabilities as _caps
from javdb.storage import db as _db


_POLICY_DDL = """
CREATE TABLE OpsAlertPolicy (
  policy_id TEXT PRIMARY KEY,
  incident_type TEXT NOT NULL,
  min_confidence TEXT NOT NULL DEFAULT 'medium',
  enabled INTEGER NOT NULL DEFAULT 1,
  channels_json TEXT NOT NULL DEFAULT '[]',
  updated_by TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""

_EVENT_DDL = """
CREATE TABLE OpsAlertEvent (
  alert_id TEXT PRIMARY KEY,
  incident_id TEXT NOT NULL,
  policy_id TEXT,
  status TEXT NOT NULL DEFAULT 'fired',
  reason TEXT,
  fired_at TEXT NOT NULL
);
"""


def _reports_db(tmp_path, *, policy: bool = True, event: bool = True) -> str:
    path = str(tmp_path / f"reports-{policy}-{event}.db")
    conn = sqlite3.connect(path)
    if policy:
        conn.executescript(_POLICY_DDL)
    if event:
        conn.executescript(_EVENT_DDL)
    conn.commit()
    conn.close()
    return path


def test_ops_alerting_probe_true_when_both_tables_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(_db, "REPORTS_DB_PATH", _reports_db(tmp_path))

    assert _caps._ops_alerting_enabled() is True


def test_ops_alerting_probe_false_when_policy_table_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(_db, "REPORTS_DB_PATH", _reports_db(tmp_path, policy=False))

    assert _caps._ops_alerting_enabled() is False


def test_ops_alerting_probe_false_when_event_table_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(_db, "REPORTS_DB_PATH", _reports_db(tmp_path, event=False))

    assert _caps._ops_alerting_enabled() is False


def test_ops_alerting_probe_false_when_probe_raises(monkeypatch):
    def _raise(*_args, **_kwargs) -> NoReturn:
        raise RuntimeError("boom")

    monkeypatch.setattr(_db, "get_db", _raise)

    assert _caps._ops_alerting_enabled() is False


def test_served_ops_alerting_capability_reflects_probe(monkeypatch):
    monkeypatch.setattr(_caps, "_ops_alerting_enabled", lambda: True)
    assert _caps.build_capabilities().features.ops_alerting is True

    monkeypatch.setattr(_caps, "_ops_alerting_enabled", lambda: False)
    assert _caps.build_capabilities().features.ops_alerting is False
