# tests/unit/test_index_sentinel_observe.py
import sqlite3
from dataclasses import dataclass

from javdb.ops.sentinel import field_health, service
from javdb.storage.repos.parse_run_field_fill_repo import ParseRunFieldFillRepo

_DDL = """
CREATE TABLE ParseRunFieldFill (
  session_id TEXT NOT NULL, page_type TEXT NOT NULL, field TEXT NOT NULL,
  fill_rate REAL NOT NULL, sample_count INTEGER NOT NULL,
  committed INTEGER NOT NULL DEFAULT 0, observed_at TEXT,
  PRIMARY KEY (session_id, page_type, field)
);
"""


@dataclass
class _Entry:
    href: str = ""
    video_code: str = ""
    title: str = ""
    rate: str = ""
    comment_count: str = ""
    release_date: str = ""


def test_start_observe_persist_roundtrip():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    repo = ParseRunFieldFillRepo(c)

    acc = field_health.start_run()
    acc.observe("index", [_Entry(href="/v/1", video_code="A-1", title="t", rate="4.0")])
    n = service.persist_run(acc.fill_rates(), session_id="S1", repo=repo)

    assert n >= 1
    got = {f.field: f for f in repo.get_fills("S1")}
    assert got["href"].fill_rate == 1.0
    assert got["rate"].fill_rate == 1.0


def test_persist_requires_explicit_session_then_writes_once_supplied():
    """Regression for the ADR-035 ordering bug (PR #141 review), now under
    ADR-046 D2 (session is explicit, never ambient).

    The index fetch fills the accumulator BEFORE the report session exists, so a
    ``field_health.persist_run()`` with no ``session_id`` is a no-op until run
    service supplies one. run_service therefore must persist AFTER the session is
    created, threading it in explicitly. This pins both halves: no-op without a
    session id, and that the buffered accumulator still persists once supplied.
    """
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    repo = ParseRunFieldFillRepo(c)

    acc = field_health.start_run()  # index fetch runs before the session exists
    acc.observe("index", [_Entry(href="/v/1", video_code="A-1", title="t", rate="4.0")])

    # No session supplied yet -> persist is a no-op (the original bug's symptom).
    assert field_health.persist_run(repo=repo) == 0
    assert c.execute("SELECT COUNT(*) FROM ParseRunFieldFill").fetchone()[0] == 0

    # run_service threads the session id in explicitly, THEN persists.
    assert field_health.persist_run(session_id="S1", repo=repo) >= 1

    got = {f.field: f for f in repo.get_fills("S1")}
    assert got["href"].fill_rate == 1.0


def test_persist_ignores_ambient_global_session():
    """ADR-046 P5: ``service.persist_run`` resolves the session ONLY from the
    explicit ``session_id`` param — the process-global is never consulted. With
    a global session active but no explicit param, persist must be a no-op."""
    from javdb.storage.db import set_active_session_id

    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    repo = ParseRunFieldFillRepo(c)

    acc = field_health.start_run()
    acc.observe("index", [_Entry(href="/v/1", video_code="A-1", title="t", rate="4.0")])
    fills = acc.fill_rates()

    try:
        set_active_session_id("AMBIENT")  # a run is "active" via the global
        # No explicit session_id -> no-op (the ambient value is ignored).
        assert service.persist_run(fills, repo=repo) == 0
        assert c.execute("SELECT COUNT(*) FROM ParseRunFieldFill").fetchone()[0] == 0
    finally:
        set_active_session_id(None)
