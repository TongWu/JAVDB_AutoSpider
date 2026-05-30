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
