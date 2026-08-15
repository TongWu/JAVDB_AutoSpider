"""Unit tests for subscription_monitor diff/persist logic (ADR-054 WS2)."""

import pathlib
import json
import sqlite3
import subprocess

import pytest

import javdb.pipeline.subscription_monitor as monitor
from javdb.storage.sessions.commit import CommitResult
from javdb.pipeline.subscription_monitor import (
    ScrapedWork,
    commit_spider_session,
    load_actor_works_from_history,
    process_actor,
)
from javdb.storage.repos.subscription_repo import (
    ActorSubscriptionRepo,
    NewWorksRepo,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DDL = (
    _REPO_ROOT
    / "javdb/migrations/d1/2026_06_14_add_actor_subscription_new_works.sql"
).read_text(encoding="utf-8")
_HISTORY_DDL = """
CREATE TABLE MovieHistory (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    VideoCode TEXT NOT NULL,
    Href TEXT NOT NULL UNIQUE,
    ActorLink TEXT,
    SupportingActors TEXT,
    DateTimeCreated TEXT
);
CREATE TABLE MovieMetadata (
    href TEXT PRIMARY KEY,
    title TEXT,
    video_code TEXT,
    release_date TEXT
);
"""


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "history.db")
    conn = sqlite3.connect(path)
    conn.executescript(_DDL)
    conn.executescript(_HISTORY_DDL)
    conn.commit()
    conn.close()
    return path


def test_process_actor_persists_only_unseen_works_before_cursor(db_path):
    subs = ActorSubscriptionRepo(db_path=db_path)
    works = NewWorksRepo(db_path=db_path)
    subs.upsert(actor_href="/actors/A", actor_name="A")
    subs.advance_cursor("/actors/A", last_seen_href="/v/cursor")

    scraped = [
        ScrapedWork(
            video_code="NEW-1",
            href="/v/new1",
            title="New",
            release_date="2026-06-10",
        ),
        ScrapedWork(video_code="OLD-1", href="/v/old1", title="Old"),
        ScrapedWork(video_code="CURSOR", href="/v/cursor", title="Cursor"),
        ScrapedWork(video_code="TOO-OLD", href="/v/too-old", title="Too Old"),
    ]
    added = process_actor(
        actor_href="/actors/A",
        scraped=scraped,
        seen_video_codes={"OLD-1"},
        subs_repo=subs,
        new_works_repo=works,
    )
    assert added == 1
    items, total = works.list()
    assert total == 1
    assert items[0]["video_code"] == "NEW-1"
    assert items[0]["actor_href"] == "/actors/A"

    row = subs.get("/actors/A")
    assert row["last_seen_href"] == "/v/new1"
    assert row["last_checked_at"]


def test_process_actor_is_idempotent(db_path):
    subs = ActorSubscriptionRepo(db_path=db_path)
    works = NewWorksRepo(db_path=db_path)
    subs.upsert(actor_href="/actors/A", actor_name="A")

    scraped = [
        ScrapedWork(video_code="NEW-1", href="/v/new1", title="New", release_date=None)
    ]
    first = process_actor(
        actor_href="/actors/A",
        scraped=scraped,
        seen_video_codes=set(),
        subs_repo=subs,
        new_works_repo=works,
    )
    second = process_actor(
        actor_href="/actors/A",
        scraped=scraped,
        seen_video_codes=set(),
        subs_repo=subs,
        new_works_repo=works,
    )
    assert first == 1
    assert second == 0
    _, total = works.list()
    assert total == 1


def test_process_actor_empty_scrape_advances_nothing(db_path):
    subs = ActorSubscriptionRepo(db_path=db_path)
    works = NewWorksRepo(db_path=db_path)
    subs.upsert(actor_href="/actors/A", actor_name="A")

    added = process_actor(
        actor_href="/actors/A",
        scraped=[],
        seen_video_codes=set(),
        subs_repo=subs,
        new_works_repo=works,
    )
    assert added == 0
    row = subs.get("/actors/A")
    assert row["last_checked_at"]
    assert row["last_seen_href"] is None


def test_load_actor_works_from_history_reads_newest_first(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO MovieHistory (VideoCode, Href, ActorLink, DateTimeCreated) "
        "VALUES (?, ?, ?, ?)",
        ("A-1", "https://javdb.com/v/a1", "https://javdb.com/actors/A", "2026-06-01"),
    )
    conn.execute(
        "INSERT INTO MovieHistory (VideoCode, Href, ActorLink, DateTimeCreated) "
        "VALUES (?, ?, ?, ?)",
        ("A-2", "/v/a2", "/actors/A", "2026-06-02"),
    )
    conn.execute(
        "INSERT INTO MovieHistory (VideoCode, Href, ActorLink, DateTimeCreated) "
        "VALUES (?, ?, ?, ?)",
        ("B-1", "/v/b1", "/actors/B", "2026-06-03"),
    )
    conn.commit()
    conn.close()

    works = load_actor_works_from_history("/actors/A", db_path=db_path)

    assert [(w.video_code, w.href) for w in works] == [
        ("A-2", "/v/a2"),
        ("A-1", "/v/a1"),
    ]


def test_load_actor_works_from_history_matches_supporting_actors(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO MovieHistory "
        "(VideoCode, Href, ActorLink, SupportingActors, DateTimeCreated) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            "SUP-1",
            "/v/supporting",
            "/actors/lead",
            json.dumps(
                [
                    {
                        "name": "A",
                        "link": "https://javdb.com/actors/A",
                    }
                ]
            ),
            "2026-06-03",
        ),
    )
    conn.execute(
        "INSERT INTO MovieHistory "
        "(VideoCode, Href, ActorLink, SupportingActors, DateTimeCreated) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            "SUP-2",
            "/v/supporting-href",
            "/actors/lead",
            json.dumps([{"name": "A", "href": "/actors/A"}]),
            "2026-06-05",
        ),
    )
    conn.execute(
        "INSERT INTO MovieHistory "
        "(VideoCode, Href, ActorLink, SupportingActors, DateTimeCreated) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            "OTHER-1",
            "/v/other",
            "/actors/lead",
            json.dumps([{"name": "B", "href": "https://javdb.com/actors/B"}]),
            "2026-06-04",
        ),
    )
    conn.commit()
    conn.close()

    works = load_actor_works_from_history("/actors/A", db_path=db_path)

    assert [(w.video_code, w.href) for w in works] == [
        ("SUP-2", "/v/supporting-href"),
        ("SUP-1", "/v/supporting"),
    ]


def test_load_actor_works_from_history_preserves_metadata(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO MovieHistory (VideoCode, Href, ActorLink, DateTimeCreated) "
        "VALUES (?, ?, ?, ?)",
        ("A-1", "/v/a1", "/actors/A", "2026-06-01"),
    )
    conn.execute(
        "INSERT INTO MovieMetadata (href, title, video_code, release_date) "
        "VALUES (?, ?, ?, ?)",
        ("https://javdb.com/v/a1", "Movie A", "A-1", "2026-05-30"),
    )
    conn.commit()
    conn.close()

    works = load_actor_works_from_history("/actors/A", db_path=db_path)

    assert [(w.video_code, w.href, w.title, w.release_date) for w in works] == [
        ("A-1", "/v/a1", "Movie A", "2026-05-30"),
    ]


def test_run_subscription_monitor_commits_spider_session_before_diff(
    db_path,
    monkeypatch,
):
    """Pending-mode spider rows must be promoted before NewWorks diffing."""
    subs = ActorSubscriptionRepo(db_path=db_path)
    subs.upsert(actor_href="/actors/A", actor_name="A")
    calls = []

    def fake_scrape(actor_href, *, use_proxy=False):
        calls.append(("scrape", actor_href, use_proxy))
        return "SESSION-1"

    def fake_commit(session_id):
        calls.append(("commit", session_id))
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO MovieHistory (VideoCode, Href, ActorLink, DateTimeCreated) "
            "VALUES (?, ?, ?, ?)",
            ("NEW-1", "/v/new1", "/actors/A", "2026-06-15"),
        )
        conn.commit()
        conn.close()

    monkeypatch.setattr(monitor, "scrape_actor", fake_scrape)
    monkeypatch.setattr(monitor, "commit_spider_session", fake_commit)

    added = monitor.run_subscription_monitor(use_proxy=True, db_path=db_path)

    assert added == 1
    assert calls == [
        ("scrape", "/actors/A", True),
        ("commit", "SESSION-1"),
    ]
    items, total = NewWorksRepo(db_path=db_path).list()
    assert total == 1
    assert items[0]["video_code"] == "NEW-1"


def test_shared_release_lands_in_both_actors_feeds(db_path, monkeypatch):
    """Regression: two subscribed actors sharing one new release.

    The baseline used to be read at the top of each loop iteration, i.e. after
    the previous actor's scrape had already committed the shared work into
    MovieHistory with this actor in SupportingActors. The second actor's feed row
    was therefore skipped as 'already seen', defeating the composite NewWorks
    identity that exists to preserve shared releases.
    """
    subs = ActorSubscriptionRepo(db_path=db_path)
    subs.upsert(actor_href="/actors/A", actor_name="A")
    subs.upsert(actor_href="/actors/B", actor_name="B")

    def fake_commit(session_id):
        # Actor A's scrape commits the co-starring work; B is a supporting actor.
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT OR IGNORE INTO MovieHistory "
            "(VideoCode, Href, ActorLink, SupportingActors, DateTimeCreated) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "SHARED-1",
                "/v/shared1",
                "/actors/A",
                json.dumps([{"name": "B", "href": "/actors/B"}]),
                "2026-06-15",
            ),
        )
        conn.commit()
        conn.close()

    monkeypatch.setattr(
        monitor, "scrape_actor", lambda actor_href, *, use_proxy=False: "SESSION-1"
    )
    monkeypatch.setattr(monitor, "commit_spider_session", fake_commit)

    added = monitor.run_subscription_monitor(db_path=db_path)

    assert added == 2
    items, total = NewWorksRepo(db_path=db_path).list()
    assert total == 2
    assert {item["actor_href"] for item in items} == {"/actors/A", "/actors/B"}
    assert {item["video_code"] for item in items} == {"SHARED-1"}


def test_run_subscription_monitor_fails_when_commit_fails(db_path, monkeypatch):
    """A post-scrape commit failure must fail the workflow, not go green."""
    subs = ActorSubscriptionRepo(db_path=db_path)
    subs.upsert(actor_href="/actors/A", actor_name="A")

    monkeypatch.setattr(
        monitor,
        "scrape_actor",
        lambda actor_href, *, use_proxy=False: "SESSION-1",
    )

    def fail_commit(session_id):
        raise RuntimeError(f"commit failed for {session_id}")

    monkeypatch.setattr(monitor, "commit_spider_session", fail_commit)

    with pytest.raises(RuntimeError, match="commit failed for SESSION-1"):
        monitor.run_subscription_monitor(db_path=db_path)

    _, total = NewWorksRepo(db_path=db_path).list()
    assert total == 0


def test_commit_spider_session_uses_claim_fanout(monkeypatch):
    captured = []

    def fake_commit_session(req):
        captured.append(req)
        return CommitResult(session_id=req.session_id, new_state="committed")

    monkeypatch.setattr(monitor, "commit_session", fake_commit_session)

    commit_spider_session("SESSION-1")

    assert len(captured) == 1
    assert captured[0].session_id == "SESSION-1"
    assert captured[0].fanout_claims is True


def test_commit_spider_session_requires_session_id():
    with pytest.raises(RuntimeError, match="Spider result did not include a session_id"):
        commit_spider_session(None)


def test_run_subscription_monitor_raises_when_all_actors_fail(db_path, monkeypatch):
    """If every actor scrape fails the monitor must exit non-zero (systemic failure)."""
    subs = ActorSubscriptionRepo(db_path=db_path)
    subs.upsert(actor_href="/actors/A", actor_name="A")
    subs.upsert(actor_href="/actors/B", actor_name="B")

    def always_fail(actor_href, *, use_proxy=False):
        raise subprocess.CalledProcessError(1, "apps.cli.spider")

    monkeypatch.setattr(monitor, "scrape_actor", always_fail)

    with pytest.raises(RuntimeError, match="All 2 actor scrape"):
        monitor.run_subscription_monitor(db_path=db_path)

    _, total = NewWorksRepo(db_path=db_path).list()
    assert total == 0


def test_run_subscription_monitor_partial_failure_does_not_raise(db_path, monkeypatch):
    """A partial per-actor failure must not raise; only total failure is systemic."""
    subs = ActorSubscriptionRepo(db_path=db_path)
    subs.upsert(actor_href="/actors/A", actor_name="A")
    subs.upsert(actor_href="/actors/B", actor_name="B")

    def scrape_one_fails(actor_href, *, use_proxy=False):
        if actor_href == "/actors/A":
            raise subprocess.CalledProcessError(1, "apps.cli.spider")
        return "SESSION-B"

    def fake_commit(session_id):
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO MovieHistory (VideoCode, Href, ActorLink, DateTimeCreated) "
            "VALUES (?, ?, ?, ?)",
            ("B-1", "/v/b1", "/actors/B", "2026-06-15"),
        )
        conn.commit()
        conn.close()

    monkeypatch.setattr(monitor, "scrape_actor", scrape_one_fails)
    monkeypatch.setattr(monitor, "commit_spider_session", fake_commit)

    added = monitor.run_subscription_monitor(db_path=db_path)
    assert added == 1


def test_run_subscription_monitor_non_called_process_error_skips_actor(
    db_path, monkeypatch
):
    """FileNotFoundError/ValueError from read_spider_result must skip the actor,
    not abort the monitor with a misleading 'commit failed' message."""
    subs = ActorSubscriptionRepo(db_path=db_path)
    subs.upsert(actor_href="/actors/A", actor_name="A")
    subs.upsert(actor_href="/actors/B", actor_name="B")

    def scrape_a_raises_file_not_found(actor_href, *, use_proxy=False):
        if actor_href == "/actors/A":
            raise FileNotFoundError("spider-result.json not found")
        return "SESSION-B"

    def fake_commit(session_id):
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO MovieHistory (VideoCode, Href, ActorLink, DateTimeCreated) "
            "VALUES (?, ?, ?, ?)",
            ("B-1", "/v/b1", "/actors/B", "2026-06-15"),
        )
        conn.commit()
        conn.close()

    monkeypatch.setattr(monitor, "scrape_actor", scrape_a_raises_file_not_found)
    monkeypatch.setattr(monitor, "commit_spider_session", fake_commit)

    added = monitor.run_subscription_monitor(db_path=db_path)
    assert added == 1
    _, total = NewWorksRepo(db_path=db_path).list()
    assert total == 1
