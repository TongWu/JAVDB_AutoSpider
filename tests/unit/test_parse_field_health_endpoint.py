# tests/unit/test_parse_field_health_endpoint.py
import sqlite3

import pytest

from javdb.storage.repos.parse_run_field_fill_repo import ParseRunFieldFillRepo

_DDL = """
CREATE TABLE ParseRunFieldFill (
  session_id TEXT NOT NULL, page_type TEXT NOT NULL, field TEXT NOT NULL,
  fill_rate REAL NOT NULL, sample_count INTEGER NOT NULL,
  committed INTEGER NOT NULL DEFAULT 0, observed_at TEXT,
  PRIMARY KEY (session_id, page_type, field)
);
"""


def _row(conn, sid, field, rate, observed_at, committed=1, sample=100):
    conn.execute(
        "INSERT INTO ParseRunFieldFill "
        "(session_id, page_type, field, fill_rate, sample_count, committed, observed_at) "
        "VALUES (?, 'index', ?, ?, ?, ?, ?)",
        [sid, field, rate, sample, committed, observed_at],
    )


@pytest.fixture
def repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return c, ParseRunFieldFillRepo(c)


def test_compute_parse_field_health_with_injected_repo(repo):
    from apps.api.routers.diagnostics import _compute_parse_field_health
    conn, r = repo
    _row(conn, "S1", "href", 0.05, "2026-06-03T00:00:00Z")  # critical -> critical_drift
    items = _compute_parse_field_health(repo=r)
    href = {i.field: i for i in items}["href"]
    assert href.status == "critical_drift"
    assert href.severity == "critical"
    assert href.page_type == "index"


def test_soft_field_single_run_shows_no_baseline(repo):
    # A soft field with only ONE committed run must read as no_baseline, not ok:
    # the displayed row must be excluded from its own baseline (pure-historical
    # semantics, matching the gate detector). Before the fix, baseline() included
    # the current row -> baseline == current -> threshold below it -> "ok".
    from apps.api.routers.diagnostics import _compute_parse_field_health
    conn, r = repo
    _row(conn, "S1", "rate", 0.10, "2026-06-03T00:00:00Z")  # soft field, lone run
    items = {i.field: i for i in _compute_parse_field_health(repo=r)}
    assert items["rate"].status == "no_baseline"
    assert items["rate"].baseline is None


def test_route_is_registered():
    from apps.api.services.runtime import app
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/api/diag/parse-field-health" in paths
