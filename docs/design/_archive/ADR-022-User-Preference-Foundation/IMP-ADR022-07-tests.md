# ADR-022 Phase 7 — Unit Tests

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Write unit tests for `MetadataRepo`, `PreferenceRepo`, the B2 preference gate, and the B3 score computation. All tests use in-memory SQLite fixtures and `unittest.mock` — no real D1 connection required.

**Architecture:** Each test file creates its own SQLite DB via a `tmp_path` pytest fixture. Repo classes accept `db_path` so tests pass the temp path directly. Gate tests use `@patch` to avoid touching the DB.

**Tech Stack:** Python 3.11, pytest, `unittest.mock`.

**Related:** [ADR-022](ADR-022-user-preference-foundation.md) · [IMP-ADR022-02](IMP-ADR022-02-metadata-repo.md) · [IMP-ADR022-03](IMP-ADR022-03-preference-repo.md) · [IMP-ADR022-04](IMP-ADR022-04-upload-gate.md)

**Depends on:** IMP-ADR022-02, IMP-ADR022-03, IMP-ADR022-04 (implementation must exist before tests can import it).

**Blocks:** Nothing — tests are a gate on IMP completion, not a prerequisite.

---

## Status — ✅ Implemented

All three test files exist and pass: `tests/unit/test_metadata_repo.py` (9),
`tests/unit/test_preference_repo.py` (14), and `tests/unit/test_preference_gate.py` (12)
— **35 passed** locally (the plan estimated 29; code review + BFR-010 added the extras).
Fixtures were hardened to `read_text()` the canonical D1 migration DDL instead of inline
`CREATE TABLE` statements, preventing fixture/schema drift. See the divergence note
below.

---

> **⚠ Divergence note (recorded during implementation, 2026-05-30).** Corrections
> to the verbatim test code below:
> 1. **`cfg()` mocking (gate tests).** `@patch('javdb.infra.config.cfg',
>    return_value=False/True)` patches `cfg` for *every* key (incl. `LOG_LEVEL`
>    consumed at import time), which breaks the uploader import. Replaced with
>    `side_effect=` helpers (`_cfg_side_effect_disabled/_enabled`) that return the
>    gate flag for `PREFERENCE_GATE_ENABLED` and pass through `default` for all
>    other keys. Assertions are unchanged.
> 2. **`upsert_preference` keyword args.** `PreferenceRepo.upsert_preference` is
>    keyword-only (`def upsert_preference(self, *, ...)`). Three test calls that
>    used positional args were converted to keyword args.
> 3. **Gate test count is 7, not 6.** `TestPreferenceGate` defines 7 methods; the
>    DoD's "6 tests" was a miscount. Total new tests: 8 + 14 + 7 = **29**.
> 4. **Regression fix surfaced by the suite.** The full `pytest tests/unit/` run
>    exposed a *pre-existing* parity guard failing because Phase 1 had not updated
>    the local `_HISTORY_DDL` — fixed under IMP-ADR022-01 (see its divergence
>    note), not here.

## Task 1 — MetadataRepo unit tests

**Files:**
- Create: `tests/unit/test_metadata_repo.py`

- [x] **Step 1: Create the test file**

```python
"""Unit tests for MetadataRepo (ADR-022)."""

import json
import sqlite3

import pytest

from javdb.storage.repos.metadata_repo import MetadataRepo


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test_history.db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE MovieMetadata (
            href              TEXT PRIMARY KEY,
            title             TEXT,
            video_code        TEXT,
            release_date      TEXT,
            duration_minutes  INTEGER,
            rate              REAL,
            comment_count     INTEGER,
            review_count      INTEGER,
            want_count        INTEGER,
            watched_count     INTEGER,
            maker             TEXT,
            publisher         TEXT,
            series            TEXT,
            directors         TEXT,
            categories        TEXT,
            poster_url        TEXT,
            fanart_urls       TEXT,
            trailer_url       TEXT,
            created_at        TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            updated_at        TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        )
    """)
    conn.commit()
    conn.close()
    return path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _Link:
    def __init__(self, name: str, href: str):
        self.name = name
        self.href = href


def _minimal_detail(**overrides) -> dict:
    base = {
        'title': 'Test Movie',
        'video_code': 'TEST-001',
        'release_date': '2025-01-15',
        'duration': '120 分鍾',
        'rate': '4.2',
        'comment_count': '101',
        'review_count': 5,
        'want_count': 200,
        'watched_count': 800,
        'maker': _Link('TestMaker', '/makers/001'),
        'publisher': None,
        'series': None,
        'directors': [_Link('Director A', '/directors/abc')],
        'tags': [_Link('熟女', '/tags?c4=15')],
        'poster_url': 'https://example.com/cover.jpg',
        'fanart_urls': ['https://example.com/p1.jpg'],
        'trailer_url': None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMetadataRepoUpsert:

    def test_upsert_stores_scalar_fields(self, db_path):
        repo = MetadataRepo(db_path=db_path)
        repo.upsert('/video/TEST-001', _minimal_detail())
        row = repo.get('/video/TEST-001')

        assert row is not None
        assert row['title'] == 'Test Movie'
        assert row['video_code'] == 'TEST-001'
        assert row['release_date'] == '2025-01-15'
        assert row['duration_minutes'] == 120
        assert row['rate'] == pytest.approx(4.2)
        assert row['comment_count'] == 101
        assert row['review_count'] == 5
        assert row['want_count'] == 200
        assert row['watched_count'] == 800
        assert row['poster_url'] == 'https://example.com/cover.jpg'

    def test_upsert_serialises_maker_as_json(self, db_path):
        repo = MetadataRepo(db_path=db_path)
        repo.upsert('/video/TEST-001', _minimal_detail())
        row = repo.get('/video/TEST-001')

        maker = json.loads(row['maker'])
        assert maker['name'] == 'TestMaker'
        assert maker['href'] == '/makers/001'

    def test_upsert_serialises_directors_as_json_array(self, db_path):
        repo = MetadataRepo(db_path=db_path)
        repo.upsert('/video/TEST-001', _minimal_detail())
        row = repo.get('/video/TEST-001')

        directors = json.loads(row['directors'])
        assert len(directors) == 1
        assert directors[0]['href'] == '/directors/abc'

    def test_upsert_serialises_categories_from_tags_field(self, db_path):
        repo = MetadataRepo(db_path=db_path)
        repo.upsert('/video/TEST-001', _minimal_detail())
        row = repo.get('/video/TEST-001')

        categories = json.loads(row['categories'])
        assert categories[0]['name'] == '熟女'

    def test_upsert_overwrites_on_conflict(self, db_path):
        repo = MetadataRepo(db_path=db_path)
        repo.upsert('/video/TEST-002', _minimal_detail(
            video_code='TEST-002', title='Old Title', rate='3.0'
        ))
        repo.upsert('/video/TEST-002', _minimal_detail(
            video_code='TEST-002', title='New Title', rate='4.5'
        ))
        row = repo.get('/video/TEST-002')

        assert row['title'] == 'New Title'
        assert row['rate'] == pytest.approx(4.5)

    def test_upsert_null_optional_fields(self, db_path):
        repo = MetadataRepo(db_path=db_path)
        detail = _minimal_detail(
            maker=None, publisher=None, series=None,
            directors=[], tags=[], fanart_urls=[], trailer_url=None,
        )
        repo.upsert('/video/TEST-003', detail)
        row = repo.get('/video/TEST-003')

        assert row['maker'] is None
        assert row['trailer_url'] is None


class TestMetadataRepoGet:

    def test_get_returns_none_for_missing_href(self, db_path):
        repo = MetadataRepo(db_path=db_path)
        assert repo.get('/video/MISSING') is None

    def test_get_returns_dict(self, db_path):
        repo = MetadataRepo(db_path=db_path)
        repo.upsert('/video/TEST-004', _minimal_detail(video_code='TEST-004'))
        row = repo.get('/video/TEST-004')
        assert isinstance(row, dict)
```

- [x] **Step 2: Run the tests**

```bash
pytest tests/unit/test_metadata_repo.py -v
```

Expected: 8 tests, all PASS.

- [x] **Step 3: Commit**

```bash
git add tests/unit/test_metadata_repo.py
git commit -m "test(storage): add MetadataRepo unit tests (ADR-022)"
```

---

## Task 2 — PreferenceRepo unit tests

**Files:**
- Create: `tests/unit/test_preference_repo.py`

- [x] **Step 1: Create the test file**

```python
"""Unit tests for PreferenceRepo (ADR-022)."""

import json
import sqlite3

import pytest

from javdb.storage.repos.preference_repo import PreferenceRepo


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test_pref.db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE MovieRatings (
            href        TEXT PRIMARY KEY,
            video_code  TEXT NOT NULL,
            rating      INTEGER
                CHECK (rating IS NULL OR (rating >= 1 AND rating <= 5)),
            tags        TEXT NOT NULL DEFAULT '[]',
            notes       TEXT,
            rated_at    TEXT,
            updated_at  TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        )
    """)
    conn.execute("""
        CREATE TABLE ContentPreferences (
            content_type  TEXT NOT NULL,
            content_id    TEXT NOT NULL,
            content_name  TEXT NOT NULL,
            hearted       INTEGER NOT NULL DEFAULT 0,
            weight        REAL NOT NULL DEFAULT 1.0,
            updated_at    TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            PRIMARY KEY (content_type, content_id)
        )
    """)
    conn.commit()
    conn.close()
    return path


# ---------------------------------------------------------------------------
# MovieRatings tests
# ---------------------------------------------------------------------------

class TestMovieRatings:

    def test_upsert_creates_row(self, db_path):
        repo = PreferenceRepo(db_path=db_path)
        row = repo.upsert_rating(
            href='/video/ABC-001', rating=4,
            tags=['quality_high', 'plot_good'], notes='Great',
        )
        assert row['rating'] == 4
        assert json.loads(row['tags']) == ['quality_high', 'plot_good']
        assert row['notes'] == 'Great'

    def test_upsert_overwrites_existing_row(self, db_path):
        repo = PreferenceRepo(db_path=db_path)
        repo.upsert_rating(href='/video/ABC-002', rating=3, tags=[], notes=None)
        repo.upsert_rating(href='/video/ABC-002', rating=5, tags=['would_rewatch'], notes='Updated')
        row = repo.get_rating('/video/ABC-002')
        assert row['rating'] == 5
        assert json.loads(row['tags']) == ['would_rewatch']

    def test_upsert_allows_null_rating(self, db_path):
        repo = PreferenceRepo(db_path=db_path)
        row = repo.upsert_rating(href='/video/ABC-003', rating=None, tags=[], notes=None)
        assert row['rating'] is None

    def test_get_rating_returns_none_for_missing(self, db_path):
        repo = PreferenceRepo(db_path=db_path)
        assert repo.get_rating('/video/MISSING') is None

    def test_list_ratings_returns_all(self, db_path):
        repo = PreferenceRepo(db_path=db_path)
        for i in range(5):
            repo.upsert_rating(
                href=f'/video/X-{i:03d}', rating=i + 1, tags=[], notes=None
            )
        items, total = repo.list_ratings(limit=10, offset=0)
        assert total == 5
        assert len(items) == 5

    def test_list_ratings_pagination(self, db_path):
        repo = PreferenceRepo(db_path=db_path)
        for i in range(5):
            repo.upsert_rating(
                href=f'/video/Y-{i:03d}', rating=i + 1, tags=[], notes=None
            )
        items, total = repo.list_ratings(limit=3, offset=0)
        assert total == 5
        assert len(items) == 3

        page2, _ = repo.list_ratings(limit=3, offset=3)
        assert len(page2) == 2


# ---------------------------------------------------------------------------
# ContentPreferences tests
# ---------------------------------------------------------------------------

class TestContentPreferences:

    def test_upsert_creates_row(self, db_path):
        repo = PreferenceRepo(db_path=db_path)
        row = repo.upsert_preference(
            content_type='actor', content_id='/actors/EvkJ',
            content_name='Test Actor', hearted=True,
        )
        assert row['hearted'] == 1
        assert row['content_name'] == 'Test Actor'
        assert row['weight'] == pytest.approx(1.0)

    def test_upsert_overwrites_hearted_value(self, db_path):
        repo = PreferenceRepo(db_path=db_path)
        repo.upsert_preference(
            content_type='actor', content_id='/actors/X',
            content_name='X', hearted=True,
        )
        repo.upsert_preference(
            content_type='actor', content_id='/actors/X',
            content_name='X', hearted=False,
        )
        row = repo.get_preference('actor', '/actors/X')
        assert row['hearted'] == 0

    def test_is_actor_blocked_true_when_hearted_false(self, db_path):
        repo = PreferenceRepo(db_path=db_path)
        repo.upsert_preference(
            content_type='actor', content_id='/actors/BLOCKED',
            content_name='Blocked', hearted=False,
        )
        assert repo.is_actor_blocked('/actors/BLOCKED') is True

    def test_is_actor_blocked_false_when_hearted_true(self, db_path):
        repo = PreferenceRepo(db_path=db_path)
        repo.upsert_preference(
            content_type='actor', content_id='/actors/LIKED',
            content_name='Liked', hearted=True,
        )
        assert repo.is_actor_blocked('/actors/LIKED') is False

    def test_is_actor_blocked_false_when_no_record(self, db_path):
        repo = PreferenceRepo(db_path=db_path)
        assert repo.is_actor_blocked('/actors/UNKNOWN') is False

    def test_list_preferences_returns_all(self, db_path):
        repo = PreferenceRepo(db_path=db_path)
        repo.upsert_preference('actor', '/actors/A', 'A', hearted=True)
        repo.upsert_preference('category', '/tags?c=1', 'Cat1', hearted=False)
        items = repo.list_preferences()
        assert len(items) == 2

    def test_list_preferences_filter_by_content_type(self, db_path):
        repo = PreferenceRepo(db_path=db_path)
        repo.upsert_preference('actor', '/actors/A', 'A', hearted=True)
        repo.upsert_preference('maker', '/makers/M', 'M', hearted=True)
        items = repo.list_preferences(content_type='actor')
        assert len(items) == 1
        assert items[0]['content_type'] == 'actor'

    def test_list_preferences_hearted_only(self, db_path):
        repo = PreferenceRepo(db_path=db_path)
        repo.upsert_preference('actor', '/actors/A', 'A', hearted=True)
        repo.upsert_preference('actor', '/actors/B', 'B', hearted=False)
        items = repo.list_preferences(hearted_only=True)
        assert len(items) == 1
        assert items[0]['content_id'] == '/actors/A'
```

- [x] **Step 2: Run the tests**

```bash
pytest tests/unit/test_preference_repo.py -v
```

Expected: 14 tests, all PASS.

- [x] **Step 3: Commit**

```bash
git add tests/unit/test_preference_repo.py
git commit -m "test(storage): add PreferenceRepo unit tests (ADR-022)"
```

---

## Task 3 — B2 preference gate unit tests

**Files:**
- Create: `tests/unit/test_preference_gate.py`

- [x] **Step 1: Create the test file**

```python
"""Unit tests for the B2 preference gate in the qBittorrent uploader (ADR-022)."""

from unittest.mock import patch


class TestPreferenceGate:

    @patch('javdb.infra.config.cfg', return_value=False)
    def test_gate_disabled_always_returns_false(self, _):
        from javdb.integrations.qb.uploader import _preference_gate_blocks
        assert _preference_gate_blocks({'actor_link': '/actors/ANYONE'}) is False

    @patch('javdb.infra.config.cfg', return_value=False)
    def test_gate_disabled_with_empty_actor_link(self, _):
        from javdb.integrations.qb.uploader import _preference_gate_blocks
        assert _preference_gate_blocks({}) is False

    @patch('javdb.integrations.qb.uploader._preference_gate_blocks.__wrapped__', create=True)
    @patch('javdb.storage.repos.preference_repo.PreferenceRepo.is_actor_blocked',
           return_value=True)
    @patch('javdb.infra.config.cfg', return_value=True)
    def test_gate_blocks_when_actor_disliked(self, _cfg, _blocked, _wrap):
        from javdb.integrations.qb.uploader import _preference_gate_blocks
        assert _preference_gate_blocks({'actor_link': '/actors/BLOCKED'}) is True

    @patch('javdb.storage.repos.preference_repo.PreferenceRepo.is_actor_blocked',
           return_value=False)
    @patch('javdb.infra.config.cfg', return_value=True)
    def test_gate_allows_when_actor_not_blocked(self, _cfg, _blocked):
        from javdb.integrations.qb.uploader import _preference_gate_blocks
        assert _preference_gate_blocks({'actor_link': '/actors/LIKED'}) is False

    @patch('javdb.infra.config.cfg', return_value=True)
    def test_gate_allows_when_no_actor_link(self, _cfg):
        from javdb.integrations.qb.uploader import _preference_gate_blocks
        assert _preference_gate_blocks({}) is False

    @patch('javdb.infra.config.cfg', return_value=True)
    def test_gate_allows_when_actor_link_is_empty_string(self, _cfg):
        from javdb.integrations.qb.uploader import _preference_gate_blocks
        assert _preference_gate_blocks({'actor_link': ''}) is False

    @patch('javdb.storage.repos.preference_repo.PreferenceRepo.is_actor_blocked',
           side_effect=Exception("DB unavailable"))
    @patch('javdb.infra.config.cfg', return_value=True)
    def test_gate_fails_open_on_exception(self, _cfg, _blocked):
        from javdb.integrations.qb.uploader import _preference_gate_blocks
        assert _preference_gate_blocks({'actor_link': '/actors/ANY'}) is False
```

- [x] **Step 2: Run the tests**

```bash
pytest tests/unit/test_preference_gate.py -v
```

Expected: 7 tests, all PASS. (The code block above defines 7 methods — see the divergence note at the top. Code review later added more gate tests for the actor-link resolution path, so the live file has additional cases.)

- [x] **Step 3: Commit**

```bash
git add tests/unit/test_preference_gate.py
git commit -m "test(qb): add B2 preference gate unit tests (ADR-022)"
```

---

## Task 4 — Full test suite regression check

- [x] **Step 1: Run all unit tests**

```bash
pytest tests/unit/ -v
```

Expected: all pre-existing tests still PASS; the three new test files add passing tests with no failures.

- [x] **Step 2: Run smoke tests**

```bash
pytest tests/smoke/ -v
```

Expected: no regressions.

---

## Definition of Done

| # | Gate | Check |
|---|------|-------|
| 1 | `test_metadata_repo.py` passes | `pytest tests/unit/test_metadata_repo.py -v` → 8 PASS, 0 FAIL |
| 2 | `test_preference_repo.py` passes | `pytest tests/unit/test_preference_repo.py -v` → 14 PASS, 0 FAIL |
| 3 | `test_preference_gate.py` passes | `pytest tests/unit/test_preference_gate.py -v` → 7 PASS, 0 FAIL (7 methods, not 6 — see divergence note) |
| 4 | No regressions in existing unit tests | `pytest tests/unit/ -v` → 29 new tests added; no pre-existing test left failing (the `_HISTORY_DDL` parity guard was fixed under IMP-ADR022-01) |
| 5 | Smoke tests pass | `pytest tests/smoke/ -v` → all PASS |
