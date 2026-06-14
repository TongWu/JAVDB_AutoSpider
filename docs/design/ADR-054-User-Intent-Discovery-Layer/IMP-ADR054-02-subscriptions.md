# Actor Subscriptions + New-Works Monitoring (ADR-054 WS2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the operator follow actors. A scheduled GitHub-Actions cron (`SubscriptionMonitor.yml`) reads the followed actors from D1, scrapes each one through the **existing AdHoc spider path** (which bypasses the ADR-040 rating threshold *by construction*), diffs the freshly-ingested works against `MovieHistory` + a per-subscription cursor, and persists genuinely-new releases into a `NewWorks` feed. The feed is surfaced as a Library tab where each row is one-click → WS1 `want`.

**Architecture:** Two net-new authoritative tables in `HISTORY_DB` — `ActorSubscription` (followed actor + last-seen cursor, keyed by the normalized `/actors/<id>` href) and `NewWorks` (a discovered, dismissible release keyed by `video_code`). Dual-backend read+write endpoints (`/api/subscriptions`, `/api/new-works`) mirrored byte-for-byte between the Python FastAPI backend (`apps/api`) and the TypeScript Hono Worker (`server/`), per ADR-017/034. A single `subscriptions` capability flag (probe `SELECT 1 FROM ActorSubscription LIMIT 1` against `HISTORY_DB`) gates both backends and the UI. The scrape lands as a new GitHub-Actions cron workflow + a `subscription_monitor` pipeline module + CLI; **no new rating-bypass code is added** — the AdHoc selection path (`javdb/pipeline/index_selection.py:75-89`) already ignores `PHASE2_MIN_RATE`/`PHASE2_MIN_COMMENTS`. ADR-040 Phase-3 ("Subscriptions") is superseded by a bilingual amendment, with a regression test pinning the bypass. The frontend reuses WS1's `StatusControl.vue` for one-click want in the feed.

**Tech Stack:** D1 (SQLite dialect), Python 3 / FastAPI / pydantic / pytest, GitHub Actions (cron), TypeScript / Hono / `@cloudflare/workers-types` / Vitest (`cloudflare:test`), Vue 3 `<script setup>` / Naive UI / vue-i18n / Vitest.

**Cross-repo note.** Two git repos are involved:
- **[MAIN]** = `/Users/tedwu/JAVDB_AutoSpider_CICD` — D1 migration, Python router/repo/schema, the cron workflow + `subscription_monitor` pipeline + CLI, capabilities, `openapi.json`, the ADR-040 amendment, Python tests. This IMP and ADR-054 live here. Run `git` with `git -C /Users/tedwu/JAVDB_AutoSpider_CICD ...`.
- **[WEB]** = the `javdb-autospider-web` working directory (current cwd) — TS Worker route/service, capabilities probe, Vue components, i18n, Worker tests, `src/types/api.gen.ts`. Run `git` from cwd.

Work the phases in order: **A (D1 + Python)** → **B (Cron / scrape)** → **C (TS Worker)** → **D (parity guard)** → **E (frontend)** → **F (ADR-040 amendment)**. Each repo should be on its own feature branch.

---

## Design decisions locked by ADR-054 D4/D7 + WS2 open-decisions (do not re-litigate)

- **One scrape trigger: a GitHub-Actions cron workflow** (`SubscriptionMonitor.yml`), reusing the proven `DailyIngestion.yml`/`StaleSessionCleanup.yml` cron pattern — **not** a Cloudflare Worker cron (the web Worker has no `[triggers]` today). The cron reads `ActorSubscription` from D1 (`STORAGE_BACKEND=d1`) and scrapes every active actor in **one job** rather than fanning out N workflow dispatches. (D-WS2-1 rec A.)
- **No new rating-threshold-bypass code.** `select_index_entries(..., is_adhoc_mode=True)` is tag-based only and never applies `PHASE2_MIN_RATE`/`PHASE2_MIN_COMMENTS` (`javdb/pipeline/index_selection.py:75-89` vs the daily phase-2 gate at `:115-120`). A subscription scrape reuses the AdHoc path (`--url https://javdb.com/actors/<id>` → `is_adhoc_mode=True`), so the rating threshold is bypassed *by construction*. ADR-040 Phase-3 is superseded **by amendment only** + a regression test pinning the bypass. (D-WS2-2 rec A.)
- **Actor-only.** `ActorSubscription` keyed by `actor_href` (the normalized `/actors/<id>` path, matching `ActorMetadata.actor_href` and `MovieHistory.ActorLink`). Tag/series deferred to a later slice. (D-WS2-3 rec A.)
- **Two tables in `HISTORY_DB`** (alongside `WatchIntent`/`MovieRatings`): `ActorSubscription` + `NewWorks`. `MovieHistory` is the **seen-set input** to the diff, never the feed store. Single-operator (no `user_id`). Outside the Pending→Commit flow. (D-WS2-4 rec A.)
- **One capability flag `subscriptions`** (probe `SELECT 1 FROM ActorSubscription LIMIT 1` against `HISTORY_DB`). Both tables ship in one migration, so independent gating buys nothing. (D-WS2-5 rec A.)
- **"Mark want" reuses WS1 unchanged.** The feed embeds WS1's `StatusControl.vue` (`videoCode`+`href` props) → `PUT /api/watchlist/{videoCode}` with `{href, status:'want'}`. No new endpoint, no new mutation surface, inherits WS1's admin-only mutation gate. (D-WS2-6 rec A.)
- **Admin-gated mutations** on both backends — follow/unfollow + dismiss require `requireRole('admin')` (TS) / `Depends(require_role('admin'))` (Python), exactly like WS1's `PUT`/`DELETE /api/watchlist`.
- The `ActorSubscription` UPSERT SQL is byte-mirrored across backends and pinned by a dedicated parity test (the Query Contract Golden does not cover upserts), exactly like WS1's `watch-intent-upsert-parity`.

---

## File Structure

**[MAIN] create:**
- `javdb/migrations/d1/2026_06_14_add_actor_subscription_new_works.sql` — both D1 DDLs (Write-Class: authoritative).
- `javdb/storage/repos/subscription_repo.py` — `ActorSubscriptionRepo` + `NewWorksRepo` + `ACTOR_SUBSCRIPTION_UPSERT_SQL`.
- `apps/api/schemas/subscriptions.py` — pydantic request/response models.
- `apps/api/routers/subscriptions.py` — FastAPI router (`/api/subscriptions` + `/api/new-works`).
- `javdb/pipeline/subscription_monitor.py` — scrape-followed-actors → diff vs `MovieHistory` + cursor → write `NewWorks` (reuses the AdHoc spider path).
- `apps/cli/ops/subscription_monitor.py` — CLI entry the cron invokes.
- `.github/workflows/SubscriptionMonitor.yml` — cron workflow (pattern per `DailyIngestion.yml:88`).
- `tests/unit/test_subscription_repo.py` — repo unit tests.
- `tests/unit/test_subscription_monitor.py` — diff/persist + cursor-advance + idempotency tests.
- `tests/unit/test_adhoc_bypasses_rating_gate.py` — pins the `is_adhoc_mode` rating-gate bypass.
- `tests/unit/test_actor_subscription_upsert_parity.py` — SQL parity golden (Python side).

**[MAIN] modify:**
- `javdb/storage/db/_db_migrations.py` — append both DDLs into `_HISTORY_DDL`.
- `apps/api/services/runtime.py` — register `subscriptions_router`.
- `apps/api/routers/capabilities.py` — add `_subscriptions_enabled()` probe + `subscriptions` feature.
- `apps/api/schemas/capabilities_payloads.py` — add `subscriptions: bool` to `Features`.
- `docs/api/openapi.json` — regenerated (mechanical).
- `docs/design/ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.md` + `.zh.md` — supersede Phase-3 (Phase F).

**[WEB] create:**
- `server/services/subscription-service.ts` — D1 query functions + `ACTOR_SUBSCRIPTION_UPSERT_SQL`.
- `server/routes/subscriptions.ts` — Hono route (`/api/subscriptions` + `/api/new-works`).
- `server/__tests__/subscription-routes.test.ts` — Worker route tests (self-seeds the tables).
- `server/__tests__/actor-subscription-upsert-parity.test.ts` — SQL parity golden (TS side).
- `src/api/subscriptions.ts` — hand-typed api client.
- `src/api/new-works.ts` — hand-typed api client.
- `src/pages/library/SubscriptionsView.vue` — manage followed actors.
- `src/pages/library/NewWorksView.vue` — the new-works feed (embeds `StatusControl.vue`).

**[WEB] modify:**
- `server/app.ts` — mount `subscriptionsRoutes`.
- `server/routes/capabilities.ts` — add `subscriptionsEnabled()` probe + `subscriptions` feature.
- `src/pages/library/LibraryPage.vue` — add the two gated tabs (`subscriptions`, `new-works`).
- `src/i18n/locales/en.json` + `src/i18n/locales/zh-CN.json` — new strings (en/zh parity).
- `src/types/api.gen.ts` — regenerated (mechanical) so `Features.subscriptions` is typed.

---

## Phase A — D1 + Python backend [MAIN]

### Task 1: ActorSubscription + NewWorks D1 migration + SQLite mirror

**Files:**
- Create: `javdb/migrations/d1/2026_06_14_add_actor_subscription_new_works.sql`
- Modify: `javdb/storage/db/_db_migrations.py` (inside the `_HISTORY_DDL` triple-quoted literal, after the `WatchIntent` block, before its closing `"""`)

- [ ] **Step 1: Write the migration file**

Create `javdb/migrations/d1/2026_06_14_add_actor_subscription_new_works.sql` (2-space indent matches the existing `d1/*.sql` convention; the `-- Write-Class:` header is mandatory on new `CREATE TABLE` migrations per `javdb/migrations/README.md` / ADR-042 D6, enforced by `.github/workflows/validate-d1-write-class.yml`):

```sql
-- 2026-06-14: Add ActorSubscription + NewWorks tables (ADR-054 WS2 Phase A).
-- Write-Class: authoritative
--
-- Apply with:
--   wrangler d1 execute javdb-history --remote \
--     --file=javdb/migrations/d1/2026_06_14_add_actor_subscription_new_works.sql
-- Then re-align the SQLite mirror:
--   python3 -m apps.cli.db.sync_d1_to_sqlite --apply --force-overwrite-all
--
-- ActorSubscription: a followed actor + last-seen cursor (user-intent, ADR-054
-- WS2). Identity = the normalized /actors/<id> href (matches
-- ActorMetadata.actor_href and MovieHistory.ActorLink). actor_name is
-- display-only and best-effort (Japanese-name matching is unreliable) — never a
-- join key. Single-operator (no user_id). Co-located with WatchIntent /
-- MovieRatings in javdb-history. Outside the Pending->Commit flow.
--
-- NewWorks: a discovered, not-yet-acted-on release for a subscribed actor. A
-- feed-state table (discovered_at + dismissed), NOT an ingestion record —
-- MovieHistory remains the authoritative ingestion log. The SubscriptionMonitor
-- cron writes NewWorks from the scrape diff and never reads it back as truth.

CREATE TABLE IF NOT EXISTS ActorSubscription (
  actor_href      TEXT PRIMARY KEY,
  actor_name      TEXT,
  active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
  last_seen_href  TEXT,
  last_checked_at TEXT,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_actor_subscription_active ON ActorSubscription(active);

CREATE TABLE IF NOT EXISTS NewWorks (
  video_code    TEXT PRIMARY KEY,
  href          TEXT NOT NULL,
  actor_href    TEXT NOT NULL,
  title         TEXT,
  release_date  TEXT,
  discovered_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  dismissed     INTEGER NOT NULL DEFAULT 0 CHECK (dismissed IN (0,1))
);

CREATE INDEX IF NOT EXISTS idx_new_works_actor     ON NewWorks(actor_href);
CREATE INDEX IF NOT EXISTS idx_new_works_dismissed ON NewWorks(dismissed);
```

- [ ] **Step 2: Verify the Write-Class CI check passes for the new file**

Run: `python3 scripts/ci/validate_d1_write_class.py javdb/migrations/d1/2026_06_14_add_actor_subscription_new_works.sql`
Expected: exits 0 / prints OK (a valid `Write-Class: authoritative` header is detected). If the script takes no args, run it with no args and confirm it does not report the new file as missing a header.

- [ ] **Step 3: Mirror both DDLs into `_HISTORY_DDL`**

In `javdb/storage/db/_db_migrations.py`, find the `WatchIntent` block inside the `_HISTORY_DDL` triple-quoted string literal (it ends with `CREATE INDEX IF NOT EXISTS idx_watch_intent_status ON WatchIntent(status);`, just before the literal's closing `"""`). Insert the following **immediately after** that index line, still inside the same `"""` literal (4-space indent matches the surrounding Python-embedded DDL):

```sql
CREATE TABLE IF NOT EXISTS ActorSubscription (
    actor_href      TEXT PRIMARY KEY,
    actor_name      TEXT,
    active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    last_seen_href  TEXT,
    last_checked_at TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_actor_subscription_active ON ActorSubscription(active);
CREATE TABLE IF NOT EXISTS NewWorks (
    video_code    TEXT PRIMARY KEY,
    href          TEXT NOT NULL,
    actor_href    TEXT NOT NULL,
    title         TEXT,
    release_date  TEXT,
    discovered_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    dismissed     INTEGER NOT NULL DEFAULT 0 CHECK (dismissed IN (0,1))
);
CREATE INDEX IF NOT EXISTS idx_new_works_actor     ON NewWorks(actor_href);
CREATE INDEX IF NOT EXISTS idx_new_works_dismissed ON NewWorks(dismissed);
```

- [ ] **Step 4: Verify `_HISTORY_DDL` still parses and creates both tables**

Run:
```bash
python3 -c "import sqlite3; from javdb.storage.db import _db_migrations as m; c=sqlite3.connect(':memory:'); c.executescript(m._HISTORY_DDL); print(sorted(r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name IN ('ActorSubscription','NewWorks')\")))"
```
Expected: prints `['ActorSubscription', 'NewWorks']`

- [ ] **Step 5: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add javdb/migrations/d1/2026_06_14_add_actor_subscription_new_works.sql javdb/storage/db/_db_migrations.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "feat(db): add ActorSubscription + NewWorks tables (ADR-054 WS2)"
```

---

### Task 2: subscription_repo.py (TDD)

**Files:**
- Create: `javdb/storage/repos/subscription_repo.py`
- Test: `tests/unit/test_subscription_repo.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_subscription_repo.py` (mirrors `tests/unit/test_watchlist_repo.py`: seed the schema from the real D1 migration so CHECK constraints match production):

```python
"""Unit tests for ActorSubscriptionRepo + NewWorksRepo (ADR-054 WS2)."""

import pathlib
import sqlite3

import pytest

from javdb.storage.repos.subscription_repo import (
    ActorSubscriptionRepo,
    NewWorksRepo,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DDL = (
    _REPO_ROOT
    / "javdb/migrations/d1/2026_06_14_add_actor_subscription_new_works.sql"
).read_text(encoding="utf-8")


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test_subs.db")
    conn = sqlite3.connect(path)
    conn.executescript(_DDL)
    conn.commit()
    conn.close()
    return path


# ── ActorSubscriptionRepo ────────────────────────────────────────────


def test_upsert_creates_subscription(db_path):
    repo = ActorSubscriptionRepo(db_path=db_path)
    row = repo.upsert(actor_href="/actors/EvkJ", actor_name="Some Name")
    assert row["actor_href"] == "/actors/EvkJ"
    assert row["actor_name"] == "Some Name"
    assert row["active"] == 1
    assert row["updated_at"]


def test_upsert_updates_in_place(db_path):
    repo = ActorSubscriptionRepo(db_path=db_path)
    repo.upsert(actor_href="/actors/EvkJ", actor_name="Old")
    repo.upsert(actor_href="/actors/EvkJ", actor_name="New", active=0)
    items, total = repo.list()
    assert total == 1  # upsert, not a second row
    assert items[0]["actor_name"] == "New"
    assert items[0]["active"] == 0


def test_list_active_only_filters(db_path):
    repo = ActorSubscriptionRepo(db_path=db_path)
    repo.upsert(actor_href="/actors/A", actor_name="A")
    repo.upsert(actor_href="/actors/B", actor_name="B", active=0)
    items, total = repo.list(active_only=True)
    assert total == 1
    assert items[0]["actor_href"] == "/actors/A"


def test_list_active_hrefs(db_path):
    repo = ActorSubscriptionRepo(db_path=db_path)
    repo.upsert(actor_href="/actors/A", actor_name="A")
    repo.upsert(actor_href="/actors/B", actor_name="B", active=0)
    assert repo.list_active_hrefs() == ["/actors/A"]


def test_advance_cursor_sets_last_seen_and_checked(db_path):
    repo = ActorSubscriptionRepo(db_path=db_path)
    repo.upsert(actor_href="/actors/A", actor_name="A")
    repo.advance_cursor("/actors/A", last_seen_href="/v/newest")
    row = repo.get("/actors/A")
    assert row["last_seen_href"] == "/v/newest"
    assert row["last_checked_at"]


def test_delete_removes_subscription(db_path):
    repo = ActorSubscriptionRepo(db_path=db_path)
    repo.upsert(actor_href="/actors/A", actor_name="A")
    assert repo.delete("/actors/A") is True
    assert repo.get("/actors/A") is None
    assert repo.delete("/actors/A") is False  # already gone


# ── NewWorksRepo ─────────────────────────────────────────────────────


def test_add_new_work_is_idempotent(db_path):
    repo = NewWorksRepo(db_path=db_path)
    assert repo.add(
        video_code="ABC-001", href="/v/abc001", actor_href="/actors/A"
    ) is True
    # Re-adding the same video_code is a no-op (INSERT OR IGNORE).
    assert repo.add(
        video_code="ABC-001", href="/v/abc001", actor_href="/actors/A"
    ) is False
    items, total = repo.list()
    assert total == 1


def test_list_excludes_dismissed_by_default(db_path):
    repo = NewWorksRepo(db_path=db_path)
    repo.add(video_code="A-1", href="/v/a1", actor_href="/actors/A")
    repo.add(video_code="B-2", href="/v/b2", actor_href="/actors/A")
    assert repo.dismiss("B-2") is True
    items, total = repo.list()  # default: not dismissed
    assert total == 1
    assert items[0]["video_code"] == "A-1"
    # include_dismissed surfaces both
    _, total_all = repo.list(include_dismissed=True)
    assert total_all == 2


def test_list_filters_by_actor(db_path):
    repo = NewWorksRepo(db_path=db_path)
    repo.add(video_code="A-1", href="/v/a1", actor_href="/actors/A")
    repo.add(video_code="B-1", href="/v/b1", actor_href="/actors/B")
    items, total = repo.list(actor_href="/actors/B")
    assert total == 1
    assert items[0]["video_code"] == "B-1"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/unit/test_subscription_repo.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'javdb.storage.repos.subscription_repo'`

- [ ] **Step 3: Implement the repo**

Create `javdb/storage/repos/subscription_repo.py` (mirrors `watchlist_repo.py`: lazy `HISTORY_DB_PATH` via `_db` for BFR-016, `get_db` context manager, dict rows; `ACTOR_SUBSCRIPTION_UPSERT_SQL` is byte-mirrored with the TS service):

```python
"""Repositories for the ActorSubscription + NewWorks tables (ADR-054 WS2)."""

from __future__ import annotations

from typing import List, Optional, Tuple

from javdb.storage import db as _db
from javdb.storage.db import get_db

# Byte-mirrored with server/services/subscription-service.ts
# ACTOR_SUBSCRIPTION_UPSERT_SQL (ADR-017 dual-backend parity). Pinned by
# tests/unit/test_actor_subscription_upsert_parity.py.
#
# created_at is preserved on conflict (only set on first insert); active /
# actor_name / updated_at are refreshed. The cursor columns
# (last_seen_href / last_checked_at) are advanced separately by the monitor
# via advance_cursor() and must NOT be clobbered by a follow/unfollow upsert,
# so they are left untouched in the DO UPDATE branch.
ACTOR_SUBSCRIPTION_UPSERT_SQL = """
    INSERT INTO ActorSubscription
        (actor_href, actor_name, active, created_at, updated_at)
    VALUES (?, ?, ?,
        strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    ON CONFLICT(actor_href) DO UPDATE SET
        actor_name = excluded.actor_name,
        active     = excluded.active,
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
"""


class ActorSubscriptionRepo:
    """Typed wrapper over the ActorSubscription table in history.db."""

    def __init__(self, *, db_path: Optional[str] = None) -> None:
        # Resolve HISTORY_DB_PATH at construction via ``_db`` (not bound at
        # import) so pytest's path monkeypatch is honoured (BFR-016).
        self._db_path = db_path or _db.HISTORY_DB_PATH

    def upsert(
        self, *, actor_href: str, actor_name: Optional[str] = None, active: int = 1
    ) -> dict:
        """Follow (or update) an actor subscription. Returns the row as a dict."""
        with get_db(self._db_path) as conn:
            conn.execute(
                ACTOR_SUBSCRIPTION_UPSERT_SQL, (actor_href, actor_name, active)
            )
            row = conn.execute(
                "SELECT * FROM ActorSubscription WHERE actor_href = ?", (actor_href,)
            ).fetchone()
        return dict(row)

    def get(self, actor_href: str) -> Optional[dict]:
        with get_db(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM ActorSubscription WHERE actor_href = ?", (actor_href,)
            ).fetchone()
        return dict(row) if row is not None else None

    def list(
        self, *, active_only: bool = False, limit: int = 200, offset: int = 0
    ) -> Tuple[List[dict], int]:
        """Return (items, total_count) for paginated listing."""
        where = "WHERE active = 1" if active_only else ""
        with get_db(self._db_path) as conn:
            # Alias + key access: D1/Dual cursors return dict-shaped rows.
            total = conn.execute(
                f"SELECT COUNT(*) AS cnt FROM ActorSubscription {where}",  # noqa: S608
            ).fetchone()["cnt"]
            rows = conn.execute(
                f"SELECT * FROM ActorSubscription {where} "  # noqa: S608
                "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [dict(r) for r in rows], total

    def list_active_hrefs(self) -> List[str]:
        """Ordered list of actor_hrefs the cron should scrape this run."""
        with get_db(self._db_path) as conn:
            rows = conn.execute(
                "SELECT actor_href FROM ActorSubscription WHERE active = 1 "
                "ORDER BY actor_href"
            ).fetchall()
        return [r["actor_href"] for r in rows]

    def advance_cursor(self, actor_href: str, *, last_seen_href: Optional[str]) -> None:
        """Record the newest href seen + stamp last_checked_at after a scrape."""
        with get_db(self._db_path) as conn:
            conn.execute(
                "UPDATE ActorSubscription SET "
                "last_seen_href = ?, "
                "last_checked_at = strftime('%Y-%m-%dT%H:%M:%fZ','now'), "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE actor_href = ?",
                (last_seen_href, actor_href),
            )

    def delete(self, actor_href: str) -> bool:
        """Unfollow an actor. Returns True if a row was removed."""
        with get_db(self._db_path) as conn:
            cur = conn.execute(
                "DELETE FROM ActorSubscription WHERE actor_href = ?", (actor_href,)
            )
            return cur.rowcount > 0


class NewWorksRepo:
    """Typed wrapper over the NewWorks feed table in history.db."""

    def __init__(self, *, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or _db.HISTORY_DB_PATH

    def add(
        self,
        *,
        video_code: str,
        href: str,
        actor_href: str,
        title: Optional[str] = None,
        release_date: Optional[str] = None,
    ) -> bool:
        """Insert a discovered work. Idempotent: returns True iff a row was added."""
        with get_db(self._db_path) as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO NewWorks "
                "(video_code, href, actor_href, title, release_date) "
                "VALUES (?, ?, ?, ?, ?)",
                (video_code, href, actor_href, title, release_date),
            )
            return cur.rowcount > 0

    def list(
        self,
        *,
        actor_href: Optional[str] = None,
        include_dismissed: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[dict], int]:
        """Return (items, total_count). Excludes dismissed rows by default."""
        clauses: list = []
        params: list = []
        if not include_dismissed:
            clauses.append("dismissed = 0")
        if actor_href:
            clauses.append("actor_href = ?")
            params.append(actor_href)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with get_db(self._db_path) as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS cnt FROM NewWorks {where}",  # noqa: S608
                params,
            ).fetchone()["cnt"]
            rows = conn.execute(
                f"SELECT * FROM NewWorks {where} "  # noqa: S608
                "ORDER BY discovered_at DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return [dict(r) for r in rows], total

    def dismiss(self, video_code: str) -> bool:
        """Mark a feed row dismissed. Returns True if a row was updated."""
        with get_db(self._db_path) as conn:
            cur = conn.execute(
                "UPDATE NewWorks SET dismissed = 1 WHERE video_code = ?", (video_code,)
            )
            return cur.rowcount > 0
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest tests/unit/test_subscription_repo.py -q`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add javdb/storage/repos/subscription_repo.py tests/unit/test_subscription_repo.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "feat(storage): add ActorSubscriptionRepo + NewWorksRepo (ADR-054 WS2)"
```

---

### Task 3: Python schemas + router + registration

**Files:**
- Create: `apps/api/schemas/subscriptions.py`, `apps/api/routers/subscriptions.py`
- Modify: `apps/api/services/runtime.py`
- Test: `tests/unit/test_subscriptions_router.py`

- [ ] **Step 1: Write the schemas**

Create `apps/api/schemas/subscriptions.py` (mirrors `apps/api/schemas/watchlist.py`):

```python
"""Pydantic schemas for subscriptions + new-works endpoints (ADR-054 WS2)."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class ActorSubscriptionUpsert(BaseModel):
    actor_name: Optional[str] = None
    active: bool = True


class ActorSubscriptionResponse(BaseModel):
    actor_href: str
    actor_name: Optional[str] = None
    active: bool
    last_seen_href: Optional[str] = None
    last_checked_at: Optional[str] = None
    created_at: str
    updated_at: str


class ActorSubscriptionListResponse(BaseModel):
    items: List[ActorSubscriptionResponse]
    total: int


class NewWorkResponse(BaseModel):
    video_code: str
    href: str
    actor_href: str
    title: Optional[str] = None
    release_date: Optional[str] = None
    discovered_at: str
    dismissed: bool


class NewWorkListResponse(BaseModel):
    items: List[NewWorkResponse]
    total: int
```

- [ ] **Step 2: Write the router**

Create `apps/api/routers/subscriptions.py` (mirrors `apps/api/routers/watchlist.py`: per-route `_require_auth` for reads, `Depends(require_role("admin"))` for mutations, `{"error": {"code", "message"}}` envelope, list route declared with path `""` so the full path is exactly `/api/subscriptions` / `/api/new-works`). The `actor_href` and `video_code` are taken from the path; declare the list routes before the `/{...}` routes:

```python
"""Actor-subscription + new-works API routes (ADR-054 WS2)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from apps.api.infra.auth import _require_auth, require_role
from apps.api.schemas.subscriptions import (
    ActorSubscriptionListResponse,
    ActorSubscriptionResponse,
    ActorSubscriptionUpsert,
    NewWorkListResponse,
    NewWorkResponse,
)
from javdb.storage.repos.subscription_repo import (
    ActorSubscriptionRepo,
    NewWorksRepo,
)

# Two routers in one module — registered separately so the prefixes resolve to
# /api/subscriptions and /api/new-works exactly (no trailing-slash redirect).
subscriptions_router = APIRouter(prefix="/api/subscriptions", tags=["subscriptions"])
new_works_router = APIRouter(prefix="/api/new-works", tags=["subscriptions"])

_SUB_NOT_FOUND = {
    "error": {"code": "subscriptions.not_found", "message": "Record not found"}
}
_NW_NOT_FOUND = {
    "error": {"code": "new_works.not_found", "message": "Record not found"}
}


def _row_to_sub(row: dict) -> ActorSubscriptionResponse:
    return ActorSubscriptionResponse(
        actor_href=row["actor_href"],
        actor_name=row.get("actor_name"),
        active=bool(row["active"]),
        last_seen_href=row.get("last_seen_href"),
        last_checked_at=row.get("last_checked_at"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_new_work(row: dict) -> NewWorkResponse:
    return NewWorkResponse(
        video_code=row["video_code"],
        href=row["href"],
        actor_href=row["actor_href"],
        title=row.get("title"),
        release_date=row.get("release_date"),
        discovered_at=row["discovered_at"],
        dismissed=bool(row["dismissed"]),
    )


# ── /api/subscriptions ───────────────────────────────────────────────


@subscriptions_router.get("", response_model=ActorSubscriptionListResponse)
def list_subscriptions(
    active_only: bool = Query(default=False),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _user=Depends(_require_auth),
):
    items, total = ActorSubscriptionRepo().list(
        active_only=active_only, limit=limit, offset=offset
    )
    return ActorSubscriptionListResponse(
        items=[_row_to_sub(r) for r in items], total=total
    )


@subscriptions_router.put("/{actor_href:path}", response_model=ActorSubscriptionResponse)
def upsert_subscription(
    actor_href: str,
    body: ActorSubscriptionUpsert,
    # Mutations are admin-only (single-operator shared data).
    _admin=Depends(require_role("admin")),
):
    row = ActorSubscriptionRepo().upsert(
        actor_href=_norm_actor_href(actor_href),
        actor_name=body.actor_name,
        active=1 if body.active else 0,
    )
    return _row_to_sub(row)


@subscriptions_router.get("/{actor_href:path}", response_model=ActorSubscriptionResponse)
def get_subscription(actor_href: str, _user=Depends(_require_auth)):
    row = ActorSubscriptionRepo().get(_norm_actor_href(actor_href))
    if row is None:
        raise HTTPException(status_code=404, detail=_SUB_NOT_FOUND)
    return _row_to_sub(row)


@subscriptions_router.delete("/{actor_href:path}")
def delete_subscription(actor_href: str, _admin=Depends(require_role("admin"))):
    deleted = ActorSubscriptionRepo().delete(_norm_actor_href(actor_href))
    return {"deleted": deleted}


# ── /api/new-works ───────────────────────────────────────────────────


@new_works_router.get("", response_model=NewWorkListResponse)
def list_new_works(
    actor_href: Optional[str] = Query(default=None),
    include_dismissed: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _user=Depends(_require_auth),
):
    items, total = NewWorksRepo().list(
        actor_href=actor_href,
        include_dismissed=include_dismissed,
        limit=limit,
        offset=offset,
    )
    return NewWorkListResponse(items=[_row_to_new_work(r) for r in items], total=total)


@new_works_router.post("/{video_code}/dismiss")
def dismiss_new_work(video_code: str, _admin=Depends(require_role("admin"))):
    dismissed = NewWorksRepo().dismiss(video_code)
    if not dismissed:
        raise HTTPException(status_code=404, detail=_NW_NOT_FOUND)
    return {"dismissed": True}


def _norm_actor_href(raw: str) -> str:
    """Normalize a path-captured actor identifier to the /actors/<id> form.

    The client sends the already-normalized href (e.g. ``/actors/EvkJ``) but the
    leading slash is consumed by the route prefix, so FastAPI hands us
    ``actors/EvkJ``. Re-prepend the slash so storage matches
    MovieHistory.ActorLink / ActorMetadata.actor_href exactly.
    """
    raw = raw.strip("/")
    return "/" + raw if raw else raw
```

> Note: the `{actor_href:path}` converter is used because the identifier contains a `/` (`actors/EvkJ`). `_norm_actor_href` re-adds the leading slash so the stored key matches `MovieHistory.ActorLink`. The client (`src/api/subscriptions.ts`, Task 14) builds the path with `encodeURIComponent` on the `<id>` segment only, so this stays unambiguous.

- [ ] **Step 3: Register both routers**

In `apps/api/services/runtime.py`: add the import alongside the other `*_router` imports (search for `watchlist_router`):

```python
from apps.api.routers.subscriptions import (
    new_works_router,
    subscriptions_router,
)
```

Then add `subscriptions_router,` and `new_works_router,` into the `for router in (...)` tuple (after `watchlist_router,`).

- [ ] **Step 4: Write a router smoke test**

Create `tests/unit/test_subscriptions_router.py` (mirrors `tests/unit/test_watchlist_router.py`: seed from the real migration, override `_require_auth` *and* `require_role("admin")` so the smoke test needs no real JWT/role):

```python
"""Smoke tests for the subscriptions + new-works routers (ADR-054 WS2)."""

import pathlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

from javdb.storage import db as _db
from apps.api.infra.auth import _require_auth, require_role


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DDL = (
    _REPO_ROOT
    / "javdb/migrations/d1/2026_06_14_add_actor_subscription_new_works.sql"
).read_text(encoding="utf-8")


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = str(tmp_path / "history.db")
    conn = sqlite3.connect(path)
    conn.executescript(_DDL)
    # Seed one NewWorks row so the feed + dismiss paths have data.
    conn.execute(
        "INSERT INTO NewWorks (video_code, href, actor_href) VALUES (?, ?, ?)",
        ("NW-001", "/v/nw001", "/actors/EvkJ"),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(_db, "HISTORY_DB_PATH", path)
    from apps.api.services.runtime import app

    # Standard FastAPI test seam: override auth + admin-role deps so the smoke
    # test does not need a real JWT (auth/role enforcement is covered by the
    # auth router tests).
    app.dependency_overrides[_require_auth] = lambda: {"username": "test"}
    app.dependency_overrides[require_role("admin")] = lambda: {"username": "admin"}
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_follow_list_get_delete(client):
    put = client.put(
        "/api/subscriptions/actors/EvkJ", json={"actor_name": "Some Name"}
    )
    assert put.status_code == 200, put.text
    assert put.json()["actor_href"] == "/actors/EvkJ"
    assert put.json()["active"] is True

    listed = client.get("/api/subscriptions", params={"active_only": True})
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1

    got = client.get("/api/subscriptions/actors/EvkJ")
    assert got.status_code == 200
    assert got.json()["actor_href"] == "/actors/EvkJ"

    deleted = client.delete("/api/subscriptions/actors/EvkJ")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert client.get("/api/subscriptions/actors/EvkJ").status_code == 404


def test_new_works_feed_and_dismiss(client):
    feed = client.get("/api/new-works")
    assert feed.status_code == 200, feed.text
    assert feed.json()["total"] == 1
    assert feed.json()["items"][0]["video_code"] == "NW-001"

    dismissed = client.post("/api/new-works/NW-001/dismiss")
    assert dismissed.status_code == 200
    assert dismissed.json()["dismissed"] is True

    # Default feed now excludes the dismissed row.
    assert client.get("/api/new-works").json()["total"] == 0
    assert (
        client.get("/api/new-works", params={"include_dismissed": True}).json()["total"]
        == 1
    )

    # Dismissing a non-existent row is a 404.
    assert client.post("/api/new-works/NOPE-999/dismiss").status_code == 404
```

- [ ] **Step 5: Run the test**

Run: `python3 -m pytest tests/unit/test_subscriptions_router.py -q`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add apps/api/schemas/subscriptions.py apps/api/routers/subscriptions.py apps/api/services/runtime.py tests/unit/test_subscriptions_router.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "feat(api): add /api/subscriptions + /api/new-works routers (ADR-054 WS2)"
```

---

### Task 4: `subscriptions` capability flag (Python)

**Files:**
- Modify: `apps/api/routers/capabilities.py`, `apps/api/schemas/capabilities_payloads.py`

- [ ] **Step 1: Add the field to the `Features` schema**

In `apps/api/schemas/capabilities_payloads.py`, add `subscriptions: bool` to `class Features(BaseModel)` immediately after `watch_intent: bool`:

```python
    watch_intent: bool
    subscriptions: bool
    site_drift_sentinel: bool
```

- [ ] **Step 2: Add the probe + wire it**

In `apps/api/routers/capabilities.py`, add this probe after `_watch_intent_enabled()` (note: probes `HISTORY_DB`, where `ActorSubscription` lives — matching WS1, not the closed-loop probes which use `OPERATIONS_DB`):

```python
def _subscriptions_enabled() -> bool:
    """True when the ADR-054 ActorSubscription table is queryable (capability honesty)."""
    try:
        from javdb.storage.db import HISTORY_DB_PATH, get_db
        with get_db(HISTORY_DB_PATH) as conn:
            conn.execute("SELECT 1 FROM ActorSubscription LIMIT 1").fetchone()
        return True
    except Exception:
        return False
```

Then in `build_capabilities()`, add `subscriptions=_subscriptions_enabled(),` to the `Features(...)` call, immediately after `watch_intent=_watch_intent_enabled(),`.

- [ ] **Step 3: Verify capabilities builds and exposes the flag**

Run: `python3 -c "from apps.api.routers.capabilities import build_capabilities; print(build_capabilities().features.subscriptions)"`
Expected: prints `False` (no table on this path) — proves the field exists and the probe degrades gracefully.

- [ ] **Step 4: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add apps/api/routers/capabilities.py apps/api/schemas/capabilities_payloads.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "feat(api): expose subscriptions capability flag (ADR-054 WS2)"
```

---

### Task 5: Regenerate the OpenAPI contract

**Files:**
- Modify: `docs/api/openapi.json`

- [ ] **Step 1: Dump the OpenAPI schema**

Run: `cd /Users/tedwu/JAVDB_AutoSpider_CICD && python3 -m apps.cli.ops.dump_openapi`
Expected: `wrote /Users/tedwu/JAVDB_AutoSpider_CICD/docs/api/openapi.json (<N> bytes)`

- [ ] **Step 2: Verify the new surface is in the contract**

Run:
```bash
python3 -c "import json; d=json.load(open('docs/api/openapi.json')); p=d['paths']; print('/api/subscriptions' in p); print('/api/new-works' in p); print('subscriptions' in d['components']['schemas']['Features']['properties'])"
```
Expected: prints `True`, `True`, `True`

- [ ] **Step 3: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add docs/api/openapi.json
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "chore(api): re-vendor openapi.json for subscriptions (ADR-054 WS2)"
```

---

## Phase B — Cron / scrape [MAIN]

The scrape reuses the proven full spider pipeline. The monitor reads the followed actors from D1, scrapes each via the AdHoc spider path (`python3 -m apps.cli.spider --url https://javdb.com/actors/<id>`), then diffs the freshly-ingested rows in `MovieHistory` (the seen-set, `ActorLink == actor_href`) against the per-subscription cursor and writes new rows into `NewWorks`. The scrape function is injectable so the diff/persist logic is unit-testable without a live site.

### Task 6: subscription_monitor pipeline (TDD)

**Files:**
- Create: `javdb/pipeline/subscription_monitor.py`
- Test: `tests/unit/test_subscription_monitor.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_subscription_monitor.py`. It seeds `ActorSubscription` + a `MovieHistory`-shaped seen-set, injects a fake "scrape" that returns parsed index entries, and asserts only genuinely-new works (absent from the seen-set, past the cursor) land in `NewWorks`, the cursor advances, and a re-run is idempotent:

```python
"""Unit tests for the subscription_monitor diff/persist logic (ADR-054 WS2)."""

import pathlib
import sqlite3

import pytest

from javdb.pipeline.subscription_monitor import (
    ScrapedWork,
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


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "history.db")
    conn = sqlite3.connect(path)
    conn.executescript(_DDL)
    conn.commit()
    conn.close()
    return path


def test_process_actor_persists_only_unseen_works(db_path):
    subs = ActorSubscriptionRepo(db_path=db_path)
    works = NewWorksRepo(db_path=db_path)
    subs.upsert(actor_href="/actors/A", actor_name="A")

    scraped = [
        ScrapedWork(video_code="OLD-1", href="/v/old1", title="Old", release_date="2026-01-01"),
        ScrapedWork(video_code="NEW-1", href="/v/new1", title="New", release_date="2026-06-10"),
    ]
    # OLD-1 is already in the seen-set; only NEW-1 is genuinely new.
    seen = {"OLD-1"}
    added = process_actor(
        actor_href="/actors/A",
        scraped=scraped,
        seen_video_codes=seen,
        subs_repo=subs,
        new_works_repo=works,
    )
    assert added == 1
    items, total = works.list()
    assert total == 1
    assert items[0]["video_code"] == "NEW-1"
    assert items[0]["actor_href"] == "/actors/A"

    # Cursor advanced to the newest scraped href.
    assert subs.get("/actors/A")["last_seen_href"] == "/v/new1"
    assert subs.get("/actors/A")["last_checked_at"]


def test_process_actor_is_idempotent(db_path):
    subs = ActorSubscriptionRepo(db_path=db_path)
    works = NewWorksRepo(db_path=db_path)
    subs.upsert(actor_href="/actors/A", actor_name="A")

    scraped = [ScrapedWork(video_code="NEW-1", href="/v/new1", title="New", release_date=None)]
    first = process_actor(
        actor_href="/actors/A", scraped=scraped, seen_video_codes=set(),
        subs_repo=subs, new_works_repo=works,
    )
    second = process_actor(
        actor_href="/actors/A", scraped=scraped, seen_video_codes=set(),
        subs_repo=subs, new_works_repo=works,
    )
    assert first == 1
    assert second == 0  # NewWorks INSERT OR IGNORE makes the re-run a no-op
    _, total = works.list()
    assert total == 1


def test_process_actor_empty_scrape_advances_nothing(db_path):
    subs = ActorSubscriptionRepo(db_path=db_path)
    works = NewWorksRepo(db_path=db_path)
    subs.upsert(actor_href="/actors/A", actor_name="A")

    added = process_actor(
        actor_href="/actors/A", scraped=[], seen_video_codes=set(),
        subs_repo=subs, new_works_repo=works,
    )
    assert added == 0
    # last_checked_at is still stamped (we did check), cursor unchanged (None).
    row = subs.get("/actors/A")
    assert row["last_checked_at"]
    assert row["last_seen_href"] is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/unit/test_subscription_monitor.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'javdb.pipeline.subscription_monitor'`

- [ ] **Step 3: Implement the monitor**

Create `javdb/pipeline/subscription_monitor.py`. The pure `process_actor` (diff + persist + cursor) is separated from `scrape_actor` (the AdHoc spider invocation) and `run_subscription_monitor` (the orchestrator) so the diff logic is unit-testable without a live site:

```python
"""Subscription monitor: scrape followed actors, diff vs history, write feed.

ADR-054 WS2. Reuses the AdHoc spider path (``is_adhoc_mode=True``), which is
tag-based only and ignores PHASE2_MIN_RATE / PHASE2_MIN_COMMENTS — so the
ADR-040 rating threshold is bypassed *by construction*, with no new
bypass code. See javdb/pipeline/index_selection.py:75-89 and
tests/unit/test_adhoc_bypasses_rating_gate.py for the pin.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass
from typing import Iterable, List, Optional, Set

from javdb.parsing.common import normalize_javdb_href_path
from javdb.storage.db import HISTORY_DB_PATH, get_db
from javdb.storage.repos.subscription_repo import (
    ActorSubscriptionRepo,
    NewWorksRepo,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScrapedWork:
    """One release parsed from a followed actor's index page."""

    video_code: str
    href: str
    title: Optional[str] = None
    release_date: Optional[str] = None


def _actor_url(actor_href: str) -> str:
    """Build the full JavDB actor URL from the normalized /actors/<id> href."""
    rel = actor_href.lstrip("/")
    return f"https://javdb.com/{rel}"


def scrape_actor(actor_href: str, *, use_proxy: bool = False) -> None:
    """Run the full AdHoc spider for a followed actor (ingests into history).

    Reuses ``apps.cli.spider --url`` verbatim: the spider scrapes the actor
    index (is_adhoc_mode=True, rating gate bypassed by construction), parses
    detail pages, and persists ingested works into MovieHistory through the
    pending pipeline. We read the diff back out of MovieHistory afterwards, so
    this is a side-effecting call that returns nothing.
    """
    cmd = [sys.executable, "-m", "apps.cli.spider", "--url", _actor_url(actor_href)]
    if use_proxy:
        cmd.append("--use-proxy")
    logger.info("Scraping followed actor %s via AdHoc spider path", actor_href)
    subprocess.run(cmd, check=True)


def load_seen_video_codes(actor_href: str, *, db_path: Optional[str] = None) -> Set[str]:
    """Video codes already ingested for this actor (MovieHistory seen-set).

    MovieHistory.ActorLink is the normalized /actors/<id> href (matches our
    ActorSubscription identity). Used to decide which scraped works are
    genuinely new vs. already-ingested.
    """
    path = db_path or HISTORY_DB_PATH
    with get_db(path) as conn:
        rows = conn.execute(
            "SELECT VideoCode FROM MovieHistory WHERE ActorLink = ?",
            (actor_href,),
        ).fetchall()
    return {r["VideoCode"] for r in rows if r["VideoCode"]}


def process_actor(
    *,
    actor_href: str,
    scraped: Iterable[ScrapedWork],
    seen_video_codes: Set[str],
    subs_repo: ActorSubscriptionRepo,
    new_works_repo: NewWorksRepo,
) -> int:
    """Diff a fresh scrape against the seen-set, persist new feed rows.

    Returns the count of rows newly added to NewWorks. Always stamps the
    subscription's last_checked_at; advances last_seen_href to the newest
    scraped href (the first entry — index pages are newest-first), or leaves it
    unchanged when the scrape was empty.
    """
    scraped = list(scraped)
    added = 0
    for work in scraped:
        if work.video_code in seen_video_codes:
            continue
        if new_works_repo.add(
            video_code=work.video_code,
            href=work.href,
            actor_href=actor_href,
            title=work.title,
            release_date=work.release_date,
        ):
            added += 1

    newest_href = scraped[0].href if scraped else None
    subs_repo.advance_cursor(actor_href, last_seen_href=newest_href)
    logger.info(
        "Actor %s: %d new work(s) added to feed (scraped %d)",
        actor_href, added, len(scraped),
    )
    return added


def run_subscription_monitor(
    *, use_proxy: bool = False, db_path: Optional[str] = None
) -> int:
    """Orchestrate: scrape every active subscription, write the new-works feed.

    Returns the total number of new feed rows added across all actors.
    """
    subs_repo = ActorSubscriptionRepo(db_path=db_path)
    new_works_repo = NewWorksRepo(db_path=db_path)
    actor_hrefs: List[str] = subs_repo.list_active_hrefs()
    if not actor_hrefs:
        logger.info("No active subscriptions — nothing to scrape.")
        return 0

    total_added = 0
    for actor_href in actor_hrefs:
        href = normalize_javdb_href_path(actor_href) or actor_href
        try:
            scrape_actor(href, use_proxy=use_proxy)
        except subprocess.CalledProcessError as exc:
            logger.warning("Scrape failed for %s (exit %s); skipping", href, exc.returncode)
            continue
        seen = load_seen_video_codes(href, db_path=db_path)
        scraped = _scraped_works_from_history(href, seen_before=set(), db_path=db_path)
        total_added += process_actor(
            actor_href=href,
            scraped=scraped,
            # The diff is against the cursor + the NewWorks table itself
            # (INSERT OR IGNORE), so passing the full seen-set here is correct:
            # works already ingested before this run are not "new".
            seen_video_codes=seen - {w.video_code for w in scraped},
            subs_repo=subs_repo,
            new_works_repo=new_works_repo,
        )
    logger.info("Subscription monitor complete: %d new feed row(s).", total_added)
    return total_added


def _scraped_works_from_history(
    actor_href: str, *, seen_before: Set[str], db_path: Optional[str] = None
) -> List[ScrapedWork]:
    """Read this actor's ingested works back out of MovieHistory as ScrapedWork.

    The AdHoc spider does not emit parsed entries in its result sidecar, so the
    monitor reconstructs the scrape outcome from MovieHistory (the authoritative
    ingestion record). Newest-first by DateTimeCreated so process_actor's cursor
    advance picks the latest href.
    """
    path = db_path or HISTORY_DB_PATH
    with get_db(path) as conn:
        rows = conn.execute(
            "SELECT VideoCode, Href FROM MovieHistory WHERE ActorLink = ? "
            "ORDER BY DateTimeCreated DESC",
            (actor_href,),
        ).fetchall()
    return [
        ScrapedWork(video_code=r["VideoCode"], href=r["Href"])
        for r in rows
        if r["VideoCode"] and r["Href"]
    ]
```

> **Diff strategy note.** The AdHoc spider's result sidecar (`javdb/spider/app/result.py`) carries only run *stats*, not parsed entries. So the monitor reconstructs the scrape outcome from `MovieHistory` (the authoritative ingestion record, `ActorLink == actor_href`). `NewWorks.add` is `INSERT OR IGNORE`, so the diff is naturally idempotent: works the operator has already seen in the feed are never re-added, and the per-subscription `last_seen_href` cursor records the newest href for display/debugging. The unit tests exercise the pure `process_actor` directly with injected `ScrapedWork`s, so they do not need a live site or a `MovieHistory` snapshot.

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest tests/unit/test_subscription_monitor.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add javdb/pipeline/subscription_monitor.py tests/unit/test_subscription_monitor.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "feat(pipeline): add subscription_monitor diff/persist (ADR-054 WS2)"
```

---

### Task 7: Pin the AdHoc rating-gate bypass (regression guard for ADR-040 supersession)

**Files:**
- Test: `tests/unit/test_adhoc_bypasses_rating_gate.py`

This is the test that makes the ADR-040-Phase-3 supersession safe: it asserts that `select_index_entries(..., is_adhoc_mode=True)` keeps a low-rate, low-comment magnet entry that the daily phase-2 path drops. If a future refactor moves the rating gate into the adhoc branch, this test fails and the supersession guarantee is restored before it silently regresses.

- [ ] **Step 1: Write the test**

Create `tests/unit/test_adhoc_bypasses_rating_gate.py`. It builds a minimal `page_result`-shaped fake whose single entry carries a magnet tag but a rating/comment count well below `PHASE2_MIN_RATE`/`PHASE2_MIN_COMMENTS`, then asserts the adhoc path keeps it while the daily phase-2 path drops it:

```python
"""Pin: the AdHoc selection path bypasses the ADR-040 rating threshold.

ADR-054 WS2 supersedes ADR-040 Phase-3 ("Subscriptions: whitelist bypassing the
rating threshold") by *reusing* the AdHoc spider path, which is tag-based only
and never applies PHASE2_MIN_RATE / PHASE2_MIN_COMMENTS. This test is the
regression guard for that claim (javdb/pipeline/index_selection.py:75-89).
"""

from types import SimpleNamespace

from javdb.pipeline import index_selection
from javdb.pipeline.index_selection import (
    PHASE2_MIN_COMMENTS,
    PHASE2_MIN_RATE,
    select_index_entries,
)


def _fake_entry(*, video_code, href, tags, rate, comment_count):
    return SimpleNamespace(
        video_code=video_code,
        href=href,
        tags=tags,
        rate=rate,
        comment_count=comment_count,
        # to_legacy_dict() is what _entry_to_legacy_dict calls; return a minimal
        # dict so selection can append it.
        to_legacy_dict=lambda: {"video_code": video_code, "href": href},
    )


def _page_result(entries):
    return SimpleNamespace(has_movie_list=True, movies=entries)


def test_adhoc_phase2_keeps_low_rate_magnet_entry():
    """A non-subtitle magnet entry below the rating gate survives in adhoc mode."""
    assert PHASE2_MIN_RATE >= 4.0
    assert PHASE2_MIN_COMMENTS >= 100
    # Magnet tag present, NOT a subtitle entry → phase-2 candidate. Rate/comments
    # deliberately below the daily gate.
    entry = _fake_entry(
        video_code="LOW-001",
        href="/v/low001",
        tags=["含磁鏈"],  # _MAGNET_TAGS member, no subtitle tag
        rate="1.0",
        comment_count="3",
    )
    page = _page_result([entry])

    kept_adhoc = select_index_entries(page, page_num=1, phase=2, is_adhoc_mode=True)
    assert [e["video_code"] for e in kept_adhoc] == ["LOW-001"], (
        "AdHoc phase-2 selection must keep low-rate magnet entries "
        "(rating gate bypassed by construction — ADR-054 WS2 supersedes "
        "ADR-040 Phase-3)"
    )


def test_daily_phase2_drops_the_same_low_rate_entry():
    """The daily path drops it — proving the adhoc bypass is a real difference."""
    entry = _fake_entry(
        video_code="LOW-001",
        href="/v/low001",
        # Add a release-date tag so the daily path reaches the rating gate
        # rather than being dropped earlier by the new-releases filter.
        tags=["含磁鏈", "今日新種"],
        rate="1.0",
        comment_count="3",
    )
    page = _page_result([entry])

    kept_daily = select_index_entries(page, page_num=1, phase=2, is_adhoc_mode=False)
    assert kept_daily == [], (
        "Daily phase-2 selection must drop entries below "
        "PHASE2_MIN_RATE / PHASE2_MIN_COMMENTS"
    )
```

- [ ] **Step 2: Run the test**

Run: `python3 -m pytest tests/unit/test_adhoc_bypasses_rating_gate.py -q`
Expected: PASS (2 passed). If `test_daily_phase2_drops_the_same_low_rate_entry` does not drop the entry, re-confirm the entry's tags reach the rating gate (`_has_release_date` true, not a subtitle entry) — the daily branch only applies the gate after the release-date filter (`index_selection.py:103-124`).

- [ ] **Step 3: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add tests/unit/test_adhoc_bypasses_rating_gate.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "test(pipeline): pin AdHoc rating-gate bypass (ADR-054 WS2 supersedes ADR-040 P3)"
```

---

### Task 8: subscription_monitor CLI + SubscriptionMonitor.yml cron

**Files:**
- Create: `apps/cli/ops/subscription_monitor.py`, `.github/workflows/SubscriptionMonitor.yml`

- [ ] **Step 1: Write the CLI**

Create `apps/cli/ops/subscription_monitor.py` (mirrors the lean argparse style of the `apps/cli/ops/*.py` tools; the cron invokes `python3 -m apps.cli.ops.subscription_monitor`):

```python
#!/usr/bin/env python3
"""CLI entry: scrape followed actors and write the new-works feed (ADR-054 WS2).

Usage::

    python3 -m apps.cli.ops.subscription_monitor              # scrape all active
    python3 -m apps.cli.ops.subscription_monitor --use-proxy  # via proxy pool
    python3 -m apps.cli.ops.subscription_monitor --dry-run    # list actors only

Reads ActorSubscription from the active storage backend (STORAGE_BACKEND=d1 in
CI), scrapes each active actor through the AdHoc spider path, diffs against
MovieHistory + cursor, and writes new rows into NewWorks. See
javdb/pipeline/subscription_monitor.py.
"""

from __future__ import annotations

import argparse
import logging
import sys

from javdb.infra.logging import setup_logging
from javdb.pipeline.subscription_monitor import run_subscription_monitor
from javdb.storage.repos.subscription_repo import ActorSubscriptionRepo

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scrape followed actors and write the new-works feed.")
    parser.add_argument("--use-proxy", action="store_true", help="Route scrapes through the proxy pool.")
    parser.add_argument("--dry-run", action="store_true", help="List active subscriptions without scraping.")
    args = parser.parse_args(argv)

    setup_logging()

    active = ActorSubscriptionRepo().list_active_hrefs()
    if args.dry_run:
        logger.info("Active subscriptions (%d): %s", len(active), ", ".join(active) or "(none)")
        return 0

    added = run_subscription_monitor(use_proxy=args.use_proxy)
    logger.info("Subscription monitor added %d new feed row(s).", added)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

> If `javdb.infra.logging` does not export `setup_logging`, drop that import and the `setup_logging()` call — the function only configures handlers and the CLI works without it (logging falls back to the root config). Confirm with `grep -n "def setup_logging" javdb/infra/logging.py` before committing.

- [ ] **Step 2: Smoke the CLI against an empty subscription set**

Run: `STORAGE_BACKEND=sqlite python3 -m apps.cli.ops.subscription_monitor --dry-run`
Expected: logs `Active subscriptions (0): (none)` and exits 0 (no live scrape; proves the CLI imports and the repo reads cleanly).

- [ ] **Step 3: Write the cron workflow**

Create `.github/workflows/SubscriptionMonitor.yml`. It mirrors the `setup` job of `AdHocIngestion.yml` (config generation from secrets/vars, encrypted-config artifact) and adds a single `monitor` job that runs the CLI with `STORAGE_BACKEND=d1`. The cron schedule mirrors `DailyIngestion.yml:88` (daily, offset so it does not collide with the daily ingestion):

```yaml
name: JavDB Subscription Monitor

# ADR-054 WS2: scrape every followed actor (ActorSubscription, D1) through the
# AdHoc spider path and write genuinely-new works into the NewWorks feed.
# The AdHoc selection path is tag-based only — it bypasses the ADR-040 rating
# threshold by construction (no new bypass code; see
# tests/unit/test_adhoc_bypasses_rating_gate.py). Supersedes ADR-040 Phase-3.

permissions:
  contents: read

on:
  workflow_dispatch:
    inputs:
      proxy_spider:
        description: 'Enable proxy for spider requests'
        required: false
        default: true
        type: boolean
      dry_run:
        description: 'List active subscriptions without scraping'
        required: false
        default: false
        type: boolean
  schedule:
    # Daily at 14:00 UTC (2h after DailyIngestion's 12:00 UTC) so the two
    # do not contend for the proxy pool / login mutex.
    - cron: '00 14 * * *'

# One monitor run at a time: a long scrape must not overlap a re-run or a
# manual dispatch (both write the same NewWorks rows + advance the same
# cursors). cancel-in-progress: false so an in-flight scrape finishes.
concurrency:
  group: subscription-monitor-${{ github.ref }}
  cancel-in-progress: false

jobs:
  # ===========================================================================
  # Job 1: Setup — checkout, deps, render config.py, upload encrypted artifact
  # ===========================================================================
  setup:
    runs-on: ubuntu-latest
    environment: Production
    permissions:
      contents: read
    outputs:
      branch: ${{ steps.branch_check.outputs.branch }}
    env:
      TZ: Asia/Singapore
    steps:
      - name: Checkout repository
        uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6
        with:
          ssh-key: ${{ secrets.DEPLOY_KEY }}

      - name: Get current branch
        id: branch_check
        run: |
          CURRENT_BRANCH="${GITHUB_REF#refs/heads/}"
          echo "branch=$CURRENT_BRANCH" >> $GITHUB_OUTPUT

      - name: Set up Python, venv, dependencies, Rust extension
        uses: ./.github/actions/setup-python-env
        with:
          python-version: '3.11'

      # Reuse the AdHocIngestion config-generation contract verbatim (same
      # secret/var surface — spider needs JavDB login, proxy pool, D1 creds).
      # Trimmed to the inputs the monitor's scrape actually touches.
      - name: Generate config.py from GitHub Variables and Secrets
        env:
          VAR_JAVDB_PASSWORD: ${{ secrets.JAVDB_PASSWORD }}
          VAR_JAVDB_SESSION_COOKIE: ${{ secrets.JAVDB_SESSION_COOKIE }}
          VAR_JAVDB_USERNAME: ${{ secrets.JAVDB_USERNAME }}
          VAR_PROXY_POOL_JSON: ${{ secrets.PROXY_POOL_JSON }}
          VAR_PROXY_MODE: ${{ vars.PROXY_MODE }}
          VAR_PROXY_POOL_MAX_FAILURES: ${{ vars.PROXY_POOL_MAX_FAILURES || 3 }}
          VAR_PROXY_MODULES_JSON: ${{ vars.PROXY_MODULES_JSON }}
          VAR_PROXY_SPIDER_ENABLED: ${{ github.event_name == 'workflow_dispatch' && (inputs.proxy_spider && 'true' || 'false') || 'true' }}
          VAR_LOGIN_PROXY_NAME: ${{ vars.LOGIN_PROXY_NAME || '' }}
          VAR_CF_BYPASS_SERVICE_PORT: ${{ vars.CF_BYPASS_SERVICE_PORT }}
          VAR_CF_BYPASS_ENABLED: ${{ vars.CF_BYPASS_ENABLED || 'True' }}
          VAR_BASE_URL: ${{ vars.BASE_URL || 'https://javdb.com' }}
          VAR_PAGE_START: ${{ vars.PAGE_START }}
          VAR_PAGE_END: ${{ vars.PAGE_END }}
          VAR_LOG_LEVEL: ${{ vars.LOG_LEVEL || 'INFO' }}
          VAR_SPIDER_LOG_FILE: ${{ vars.SPIDER_LOG_FILE || 'logs/spider.log' }}
          VAR_REPORTS_DIR: ${{ vars.REPORTS_DIR || 'reports' }}
          VAR_AD_HOC_DIR: ${{ vars.AD_HOC_DIR || 'reports/AdHoc' }}
          # qB credentials so ingested works can be queued (same as AdHoc default).
          VAR_QB_URL: ${{ secrets.QB_URL_ADHOC || secrets.QB_URL }}
          VAR_QB_USERNAME: ${{ secrets.QB_USERNAME_ADHOC || secrets.QB_USERNAME }}
          VAR_QB_PASSWORD: ${{ secrets.QB_PASSWORD_ADHOC || secrets.QB_PASSWORD }}
          VAR_TORRENT_CATEGORY_ADHOC: ${{ vars.TORRENT_CATEGORY_ADHOC }}
          VAR_TORRENT_SAVE_PATH: ${{ vars.TORRENT_SAVE_PATH }}
          # D1 / Cloudflare — the monitor reads ActorSubscription from D1.
          VAR_STORAGE_BACKEND: 'd1'
          VAR_CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
          VAR_CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
          VAR_D1_HISTORY_DB_ID: ${{ secrets.D1_HISTORY_DB_ID }}
          VAR_D1_REPORTS_DB_ID: ${{ secrets.D1_REPORTS_DB_ID }}
          VAR_D1_OPERATIONS_DB_ID: ${{ secrets.D1_OPERATIONS_DB_ID }}
          VAR_PROXY_COORDINATOR_URL: ${{ vars.PROXY_COORDINATOR_URL }}
          VAR_PROXY_COORDINATOR_TOKEN: ${{ secrets.PROXY_COORDINATOR_TOKEN }}
        run: python3 -m apps.cli.ops.config_generator --github-actions

      - name: Encrypt config for artifact
        env:
          ARTIFACT_KEY: ${{ secrets.ARTIFACT_KEY }}
        run: |
          if [ -z "$ARTIFACT_KEY" ]; then
            echo "❌ ERROR: ARTIFACT_KEY secret is not configured."
            exit 1
          fi
          openssl enc -aes-256-cbc -salt -pbkdf2 -iter 100000 \
            -in config.py -out config.py.enc \
            -pass pass:"$ARTIFACT_KEY"

      - name: Upload encrypted config artifact
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7
        with:
          name: config-encrypted
          path: config.py.enc
          retention-days: 1

  # ===========================================================================
  # Job 2: Monitor — scrape each active subscription, write the new-works feed
  # ===========================================================================
  monitor:
    runs-on: ${{ vars.SUBSCRIPTION_MONITOR_RUNNER || 'ubuntu-latest' }}
    environment: Production
    permissions:
      contents: read
    needs: [setup]
    env:
      TZ: Asia/Singapore
      STORAGE_BACKEND: 'd1'
      JAVDB_HISTORY_WRITE_MODE: pending
    steps:
      - name: Checkout repository
        uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6
        with:
          ssh-key: ${{ secrets.DEPLOY_KEY }}
          lfs: true

      - name: Set up Python, venv, dependencies, Rust extension
        uses: ./.github/actions/setup-python-env
        with:
          python-version: '3.11'

      - name: Restore encrypted config
        uses: ./.github/actions/restore-encrypted-config
        with:
          artifact-name: config-encrypted
          artifact-key: ${{ secrets.ARTIFACT_KEY }}

      - name: Run subscription monitor
        env:
          INPUT_PROXY_SPIDER: ${{ github.event_name == 'workflow_dispatch' && inputs.proxy_spider || 'true' }}
          INPUT_DRY_RUN: ${{ github.event_name == 'workflow_dispatch' && inputs.dry_run || 'false' }}
        run: |
          set -eo pipefail
          mkdir -p logs
          CMD=(python3 -m apps.cli.ops.subscription_monitor)
          if [ "$INPUT_PROXY_SPIDER" = "true" ]; then
            CMD+=(--use-proxy)
          fi
          if [ "$INPUT_DRY_RUN" = "true" ]; then
            CMD+=(--dry-run)
          fi
          echo "Executing: ${CMD[*]}"
          "${CMD[@]}" 2>&1 | tee logs/subscription_monitor.log

      - name: Upload monitor log
        if: always()
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7
        with:
          name: subscription-monitor-log
          path: logs/subscription_monitor.log
          retention-days: 7
          if-no-files-found: ignore
```

- [ ] **Step 4: Lint the workflow YAML**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/SubscriptionMonitor.yml')); print('SubscriptionMonitor.yml: valid YAML')"`
Expected: `SubscriptionMonitor.yml: valid YAML`

- [ ] **Step 5: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add apps/cli/ops/subscription_monitor.py .github/workflows/SubscriptionMonitor.yml
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "feat(ci): add SubscriptionMonitor cron + CLI (ADR-054 WS2)"
```

---

## Phase C — TypeScript Worker [WEB]

### Task 9: subscription-service.ts

**Files:**
- Create: `server/services/subscription-service.ts`

- [ ] **Step 1: Write the service**

Create `server/services/subscription-service.ts` (mirrors `server/services/watchlist-service.ts`: `D1Database` is an ambient global — do not import it; functions take `db` not `env`; the UPSERT SQL is byte-identical to the Python `ACTOR_SUBSCRIPTION_UPSERT_SQL`):

```typescript
// Actor-subscription + new-works D1 queries (ADR-054 WS2).
// Keep this module free of Hono / c.env references; callers pass the binding.

export interface ActorSubscriptionRow {
  actor_href: string;
  actor_name: string | null;
  active: number;
  last_seen_href: string | null;
  last_checked_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface NewWorkRow {
  video_code: string;
  href: string;
  actor_href: string;
  title: string | null;
  release_date: string | null;
  discovered_at: string;
  dismissed: number;
}

// Byte-mirrored with javdb/storage/repos/subscription_repo.py
// ACTOR_SUBSCRIPTION_UPSERT_SQL (ADR-017 dual-backend parity). Pinned by
// actor-subscription-upsert-parity.test.ts. Cursor columns (last_seen_href /
// last_checked_at) are advanced by the monitor, not by follow/unfollow, so the
// DO UPDATE branch leaves them untouched.
export const ACTOR_SUBSCRIPTION_UPSERT_SQL = `
    INSERT INTO ActorSubscription
        (actor_href, actor_name, active, created_at, updated_at)
    VALUES (?, ?, ?,
        strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    ON CONFLICT(actor_href) DO UPDATE SET
        actor_name = excluded.actor_name,
        active     = excluded.active,
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')`;

export async function upsertSubscription(
  db: D1Database,
  actorHref: string,
  actorName: string | null,
  active: number,
): Promise<ActorSubscriptionRow> {
  await db
    .prepare(ACTOR_SUBSCRIPTION_UPSERT_SQL)
    .bind(actorHref, actorName, active)
    .run();
  return (await getSubscription(db, actorHref))!;
}

export async function getSubscription(
  db: D1Database,
  actorHref: string,
): Promise<ActorSubscriptionRow | null> {
  return db
    .prepare("SELECT * FROM ActorSubscription WHERE actor_href = ?")
    .bind(actorHref)
    .first<ActorSubscriptionRow>();
}

export async function listSubscriptions(
  db: D1Database,
  activeOnly: boolean,
  limit: number,
  offset: number,
): Promise<{ items: ActorSubscriptionRow[]; total: number }> {
  const where = activeOnly ? "WHERE active = 1" : "";

  const total =
    (await db
      .prepare(`SELECT COUNT(*) AS n FROM ActorSubscription ${where}`)
      .first<{ n: number }>())?.n ?? 0;

  const rows = await db
    .prepare(
      `SELECT * FROM ActorSubscription ${where} ORDER BY updated_at DESC LIMIT ? OFFSET ?`,
    )
    .bind(limit, offset)
    .all<ActorSubscriptionRow>();

  return { items: rows.results, total };
}

export async function deleteSubscription(
  db: D1Database,
  actorHref: string,
): Promise<boolean> {
  const res = await db
    .prepare("DELETE FROM ActorSubscription WHERE actor_href = ?")
    .bind(actorHref)
    .run();
  return (res.meta?.changes ?? 0) > 0;
}

export async function listNewWorks(
  db: D1Database,
  actorHref: string | null,
  includeDismissed: boolean,
  limit: number,
  offset: number,
): Promise<{ items: NewWorkRow[]; total: number }> {
  const clauses: string[] = [];
  const bindings: (string | number)[] = [];
  if (!includeDismissed) clauses.push("dismissed = 0");
  if (actorHref) {
    clauses.push("actor_href = ?");
    bindings.push(actorHref);
  }
  const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";

  const total =
    (await db
      .prepare(`SELECT COUNT(*) AS n FROM NewWorks ${where}`)
      .bind(...bindings)
      .first<{ n: number }>())?.n ?? 0;

  const rows = await db
    .prepare(
      `SELECT * FROM NewWorks ${where} ORDER BY discovered_at DESC LIMIT ? OFFSET ?`,
    )
    .bind(...bindings, limit, offset)
    .all<NewWorkRow>();

  return { items: rows.results, total };
}

export async function dismissNewWork(
  db: D1Database,
  videoCode: string,
): Promise<boolean> {
  const res = await db
    .prepare("UPDATE NewWorks SET dismissed = 1 WHERE video_code = ?")
    .bind(videoCode)
    .run();
  return (res.meta?.changes ?? 0) > 0;
}
```

- [ ] **Step 2: Verify it type-checks**

Run: `npx tsc -p server/tsconfig.json --noEmit`
Expected: no errors referencing `subscription-service.ts`.

(Commit together with Task 10.)

---

### Task 10: subscriptions.ts route + mount (TDD)

**Files:**
- Create: `server/routes/subscriptions.ts`, `server/__tests__/subscription-routes.test.ts`
- Modify: `server/app.ts`

- [ ] **Step 1: Write the failing route test**

Create `server/__tests__/subscription-routes.test.ts` (self-seeds the tables like `watchlist-routes.test.ts`; mutations require the CSRF `mutationHeaders` and the admin role — the test login user is `admin`):

```typescript
import { describe, it, expect, beforeAll } from "vitest";
import { env } from "cloudflare:test";
import { app } from "../app";

async function login() {
  const res = await app.request(
    "/api/auth/login",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "admin", password: "testpassword123" }),
    },
    env,
  );
  const data = (await res.json()) as Record<string, unknown>;
  return {
    accessToken: data.access_token as string,
    csrfToken: data.csrf_token as string,
  };
}

function authHeaders(accessToken: string) {
  return { Authorization: `Bearer ${accessToken}` };
}

function mutationHeaders(accessToken: string, csrfToken: string) {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${accessToken}`,
    "X-CSRF-Token": csrfToken,
    Cookie: `csrf_token=${csrfToken}`,
  };
}

async function seedTables() {
  await env.HISTORY_DB.prepare(
    `CREATE TABLE IF NOT EXISTS ActorSubscription (
      actor_href TEXT PRIMARY KEY, actor_name TEXT,
      active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
      last_seen_href TEXT, last_checked_at TEXT,
      created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
      updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    )`,
  ).run();
  await env.HISTORY_DB.prepare(
    `CREATE TABLE IF NOT EXISTS NewWorks (
      video_code TEXT PRIMARY KEY, href TEXT NOT NULL, actor_href TEXT NOT NULL,
      title TEXT, release_date TEXT,
      discovered_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
      dismissed INTEGER NOT NULL DEFAULT 0 CHECK (dismissed IN (0,1))
    )`,
  ).run();
  await env.HISTORY_DB.prepare(
    "INSERT OR IGNORE INTO NewWorks (video_code, href, actor_href) VALUES (?, ?, ?)",
  )
    .bind("NW-001", "/v/nw001", "/actors/EvkJ")
    .run();
}

describe("Subscription routes", () => {
  beforeAll(async () => {
    await seedTables();
  });

  it("follow → list → get → delete a subscription", async () => {
    const { accessToken, csrfToken } = await login();

    const put = await app.request(
      "/api/subscriptions/actors/EvkJ",
      {
        method: "PUT",
        headers: mutationHeaders(accessToken, csrfToken),
        body: JSON.stringify({ actor_name: "Some Name", active: true }),
      },
      env,
    );
    expect(put.status).toBe(200);
    const putBody = (await put.json()) as Record<string, unknown>;
    expect(putBody.actor_href).toBe("/actors/EvkJ");
    expect(putBody.active).toBe(true);

    const list = await app.request(
      "/api/subscriptions?active_only=true",
      { headers: authHeaders(accessToken) },
      env,
    );
    expect(list.status).toBe(200);
    const listBody = (await list.json()) as { items: unknown[]; total: number };
    expect(listBody.total).toBe(1);

    const got = await app.request(
      "/api/subscriptions/actors/EvkJ",
      { headers: authHeaders(accessToken) },
      env,
    );
    expect(got.status).toBe(200);
    expect((await got.json() as Record<string, unknown>).actor_href).toBe("/actors/EvkJ");

    const del = await app.request(
      "/api/subscriptions/actors/EvkJ",
      { method: "DELETE", headers: mutationHeaders(accessToken, csrfToken) },
      env,
    );
    expect(del.status).toBe(200);
    expect((await del.json() as Record<string, unknown>).deleted).toBe(true);
  });

  it("new-works feed lists, dismisses, and excludes dismissed", async () => {
    const { accessToken, csrfToken } = await login();

    const feed = await app.request(
      "/api/new-works",
      { headers: authHeaders(accessToken) },
      env,
    );
    expect(feed.status).toBe(200);
    expect((await feed.json() as { total: number }).total).toBe(1);

    const dismiss = await app.request(
      "/api/new-works/NW-001/dismiss",
      { method: "POST", headers: mutationHeaders(accessToken, csrfToken) },
      env,
    );
    expect(dismiss.status).toBe(200);
    expect((await dismiss.json() as Record<string, unknown>).dismissed).toBe(true);

    const after = await app.request(
      "/api/new-works",
      { headers: authHeaders(accessToken) },
      env,
    );
    expect((await after.json() as { total: number }).total).toBe(0);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run server/__tests__/subscription-routes.test.ts --config vitest.server.config.ts`
Expected: FAIL (routes 404 / `subscriptionsRoutes` not mounted).

- [ ] **Step 3: Write the route**

Create `server/routes/subscriptions.ts` (mirrors `server/routes/watchlist.ts`; mutations gated by `requireRole("admin")`; one Hono app mounts both `/api/subscriptions` and `/api/new-works` via two sub-paths — see the mount in Step 4). To keep both prefixes clean and matching the Python routers, this file exports ONE Hono app that handles both URL spaces by using full sub-paths under a shared `/api` mount:

```typescript
import { Hono } from "hono";
import type { Env } from "../env";
import type { JwtPayload } from "../services/jwt";
import { requireRole } from "../middleware/auth";
import {
  upsertSubscription,
  getSubscription,
  listSubscriptions,
  deleteSubscription,
  listNewWorks,
  dismissNewWork,
} from "../services/subscription-service";

type SubEnv = { Bindings: Env; Variables: { user: JwtPayload } };

export const subscriptionsRoutes = new Hono<SubEnv>();

const errJson = (code: string, message: string) => ({ error: { code, message } });

// Re-prepend the leading slash consumed by the route param so the stored key
// matches MovieHistory.ActorLink / ActorMetadata.actor_href (/actors/<id>).
function normActorHref(raw: string): string {
  const trimmed = raw.replace(/^\/+|\/+$/g, "");
  return trimmed ? `/${trimmed}` : trimmed;
}

function clampLimit(raw: string | undefined, def: number, max: number): number {
  const n = Number(raw ?? def);
  return Number.isNaN(n) ? def : Math.max(1, Math.min(max, n));
}

// ── /subscriptions ───────────────────────────────────────────────────

// GET /subscriptions — list followed actors.
subscriptionsRoutes.get("/subscriptions", async (c) => {
  const activeOnly = c.req.query("active_only") === "true";
  const limit = clampLimit(c.req.query("limit"), 200, 500);
  const offset = Math.max(0, Number(c.req.query("offset") ?? 0) || 0);
  const { items, total } = await listSubscriptions(c.env.HISTORY_DB, activeOnly, limit, offset);
  return c.json({
    items: items.map((r) => ({ ...r, active: Boolean(r.active) })),
    total,
  });
});

// PUT /subscriptions/actors/:id — follow/update. Admin-only.
subscriptionsRoutes.put("/subscriptions/actors/:id", requireRole("admin"), async (c) => {
  const actorHref = normActorHref(`actors/${c.req.param("id")}`);
  let body: { actor_name?: string | null; active?: boolean };
  try {
    body = await c.req.json();
  } catch {
    return c.json(errJson("subscriptions.invalid_body", "Request body must be valid JSON"), 422);
  }
  const active = body.active === false ? 0 : 1;
  const row = await upsertSubscription(c.env.HISTORY_DB, actorHref, body.actor_name ?? null, active);
  return c.json({ ...row, active: Boolean(row.active) });
});

// GET /subscriptions/actors/:id — one subscription.
subscriptionsRoutes.get("/subscriptions/actors/:id", async (c) => {
  const row = await getSubscription(c.env.HISTORY_DB, normActorHref(`actors/${c.req.param("id")}`));
  if (row === null) {
    return c.json(errJson("subscriptions.not_found", "Record not found"), 404);
  }
  return c.json({ ...row, active: Boolean(row.active) });
});

// DELETE /subscriptions/actors/:id — unfollow. Admin-only.
subscriptionsRoutes.delete("/subscriptions/actors/:id", requireRole("admin"), async (c) => {
  const deleted = await deleteSubscription(c.env.HISTORY_DB, normActorHref(`actors/${c.req.param("id")}`));
  return c.json({ deleted });
});

// ── /new-works ────────────────────────────────────────────────────────

// GET /new-works — feed, optionally filtered by actor / dismissed.
subscriptionsRoutes.get("/new-works", async (c) => {
  const actorHref = c.req.query("actor_href") ?? null;
  const includeDismissed = c.req.query("include_dismissed") === "true";
  const limit = clampLimit(c.req.query("limit"), 50, 200);
  const offset = Math.max(0, Number(c.req.query("offset") ?? 0) || 0);
  const { items, total } = await listNewWorks(c.env.HISTORY_DB, actorHref, includeDismissed, limit, offset);
  return c.json({
    items: items.map((r) => ({ ...r, dismissed: Boolean(r.dismissed) })),
    total,
  });
});

// POST /new-works/:videoCode/dismiss — dismiss a feed row. Admin-only.
subscriptionsRoutes.post("/new-works/:videoCode/dismiss", requireRole("admin"), async (c) => {
  const dismissed = await dismissNewWork(c.env.HISTORY_DB, c.req.param("videoCode"));
  if (!dismissed) {
    return c.json(errJson("new_works.not_found", "Record not found"), 404);
  }
  return c.json({ dismissed: true });
});
```

> The route uses `/subscriptions/actors/:id` (a single `:id` param) rather than a wildcard, because actor hrefs are always `/actors/<id>`. The Python side accepts the full `{actor_href:path}` for generality; both backends store the identical normalized `/actors/<id>` key, so the parity guard (Task 12) only needs to pin the *upsert SQL*, not the route shape.

- [ ] **Step 4: Mount the route**

In `server/app.ts`: add the import in the route-imports block (near `import { watchlistRoutes } from "./routes/watchlist";`):

```typescript
import { subscriptionsRoutes } from "./routes/subscriptions";
```

Then add the mount immediately after the `app.route("/api/watchlist", watchlistRoutes);` line (after the `app.use("/api/*", requireAuth());` gate, before `app.route("/api", stubRoutes);`). Mount at `/api` so the route's own `/subscriptions` and `/new-works` sub-paths resolve to `/api/subscriptions` and `/api/new-works`:

```typescript
app.route("/api", subscriptionsRoutes);
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `npx vitest run server/__tests__/subscription-routes.test.ts --config vitest.server.config.ts`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add server/services/subscription-service.ts server/routes/subscriptions.ts server/__tests__/subscription-routes.test.ts server/app.ts
git commit -m "feat(server): add /api/subscriptions + /api/new-works worker routes (ADR-054 WS2)"
```

---

### Task 11: `subscriptions` capability probe (TS)

**Files:**
- Modify: `server/routes/capabilities.ts`

- [ ] **Step 1: Add the probe**

In `server/routes/capabilities.ts`, after `watchIntentEnabled()`, add (probes `HISTORY_DB`, matching WS1):

```typescript
/** True when the ADR-054 ActorSubscription table is queryable in HISTORY_DB (capability honesty). */
async function subscriptionsEnabled(env: Env): Promise<boolean> {
  try {
    await env.HISTORY_DB.prepare("SELECT 1 FROM ActorSubscription LIMIT 1").first();
    return true;
  } catch {
    return false;
  }
}
```

- [ ] **Step 2: Wire it into the handler**

In the `capabilitiesRoutes.get("/", ...)` handler, after `const watch_intent = await watchIntentEnabled(env);` add:

```typescript
  const subscriptions = await subscriptionsEnabled(env);
```

Then add `subscriptions,` into the `features:` object, immediately after `watch_intent,`.

- [ ] **Step 3: Verify type-check + existing capabilities test still passes**

Run: `npx tsc -p server/tsconfig.json --noEmit`
Then: `npx vitest run server/__tests__ --config vitest.server.config.ts -t capabilit`
Expected: type-check clean; capabilities test(s) pass (the `subscriptions` key is now present in the response).

- [ ] **Step 4: Commit**

```bash
git add server/routes/capabilities.ts
git commit -m "feat(server): expose subscriptions capability flag (ADR-054 WS2)"
```

---

## Phase D — Cross-backend upsert parity guard

The Query Contract Golden pins only read query-builders; the `ActorSubscription` UPSERT is unguarded. Pin both sides to one canonical (whitespace-normalized) SQL string so neither drifts silently — exactly like WS1's `watch-intent-upsert-parity`.

### Task 12: parity tests in both repos

**Files:**
- Create: `tests/unit/test_actor_subscription_upsert_parity.py` [MAIN]
- Create: `server/__tests__/actor-subscription-upsert-parity.test.ts` [WEB]

- [ ] **Step 1: Python parity test**

Create `tests/unit/test_actor_subscription_upsert_parity.py`:

```python
"""Pin the ActorSubscription UPSERT SQL so it cannot drift from the TS Worker (ADR-054)."""

import re

from javdb.storage.repos.subscription_repo import ACTOR_SUBSCRIPTION_UPSERT_SQL

# The single canonical UPSERT shape both backends must emit (whitespace-collapsed).
CANONICAL = (
    "INSERT INTO ActorSubscription "
    "(actor_href, actor_name, active, created_at, updated_at) "
    "VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), "
    "strftime('%Y-%m-%dT%H:%M:%fZ','now')) "
    "ON CONFLICT(actor_href) DO UPDATE SET "
    "actor_name = excluded.actor_name, active = excluded.active, "
    "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')"
)


def _norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def test_python_upsert_matches_canonical():
    assert _norm(ACTOR_SUBSCRIPTION_UPSERT_SQL) == CANONICAL
```

Run: `python3 -m pytest tests/unit/test_actor_subscription_upsert_parity.py -q`
Expected: PASS. (If it fails, the `CANONICAL` constant here is the source of truth — fix whichever SQL drifted, not the test, and keep the TS test below identical.)

- [ ] **Step 2: TS parity test (identical CANONICAL string)**

Create `server/__tests__/actor-subscription-upsert-parity.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { ACTOR_SUBSCRIPTION_UPSERT_SQL } from "../services/subscription-service";

// MUST be character-identical to
// tests/unit/test_actor_subscription_upsert_parity.py CANONICAL.
const CANONICAL =
  "INSERT INTO ActorSubscription " +
  "(actor_href, actor_name, active, created_at, updated_at) " +
  "VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), " +
  "strftime('%Y-%m-%dT%H:%M:%fZ','now')) " +
  "ON CONFLICT(actor_href) DO UPDATE SET " +
  "actor_name = excluded.actor_name, active = excluded.active, " +
  "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')";

const norm = (s: string) => s.replace(/\s+/g, " ").trim();

describe("ActorSubscription upsert SQL parity", () => {
  it("TS upsert matches the canonical cross-backend shape", () => {
    expect(norm(ACTOR_SUBSCRIPTION_UPSERT_SQL)).toBe(CANONICAL);
  });
});
```

Run: `npx vitest run server/__tests__/actor-subscription-upsert-parity.test.ts --config vitest.server.config.ts`
Expected: PASS.

- [ ] **Step 3: Commit (both repos)**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add tests/unit/test_actor_subscription_upsert_parity.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "test(storage): pin ActorSubscription upsert SQL parity (ADR-054 WS2)"
git add server/__tests__/actor-subscription-upsert-parity.test.ts
git commit -m "test(server): pin ActorSubscription upsert SQL parity (ADR-054 WS2)"
```

---

## Phase E — Frontend [WEB]

### Task 13: Regenerate api types

**Files:**
- Modify: `src/types/api.gen.ts`

- [ ] **Step 1: Regenerate from the local openapi.json produced in Task 5**

Run: `OPENAPI_PATH=/Users/tedwu/JAVDB_AutoSpider_CICD/docs/api/openapi.json node scripts/fetch-openapi.mjs`
Expected: regenerates `src/types/api.gen.ts`.

- [ ] **Step 2: Verify `Features.subscriptions` is now typed**

Run: `grep -n "subscriptions" src/types/api.gen.ts`
Expected: matches the new `subscriptions: boolean;` line under the `Features` schema.

- [ ] **Step 3: Commit**

```bash
git add src/types/api.gen.ts
git commit -m "chore(web): re-vendor api types for subscriptions (ADR-054 WS2)"
```

> Note: the api **clients** in Task 14 are hand-typed (mirroring `src/api/watchlist.ts`), so they do not depend on this step. This regeneration is only needed so `cap.data?.features?.subscriptions` type-checks.

---

### Task 14: api/subscriptions.ts + api/new-works.ts clients

**Files:**
- Create: `src/api/subscriptions.ts`, `src/api/new-works.ts`

- [ ] **Step 1: Write the subscriptions client**

Create `src/api/subscriptions.ts` (hand-typed, mirroring `src/api/watchlist.ts`: shared `http` wrapper, `encodeURIComponent` path segment, 404→null). The actor identifier is the bare `<id>` (e.g. `EvkJ`); the client builds `/api/subscriptions/actors/<id>` to match both backends' routes:

```typescript
import axios from 'axios'
import { http } from './client'

// Hand-typed until src/types/api.gen.ts is regenerated to include the ADR-054
// WS2 subscription endpoints. Shapes mirror server/routes/subscriptions.ts.

export interface ActorSubscription {
  actor_href: string
  actor_name: string | null
  active: boolean
  last_seen_href: string | null
  last_checked_at: string | null
  created_at: string
  updated_at: string
}

export interface ActorSubscriptionListResponse {
  items: ActorSubscription[]
  total: number
}

// The actor_href is /actors/<id>; the route param is the bare <id>.
function actorIdFromHref(actorHref: string): string {
  return actorHref.replace(/^\/?actors\//, '').replace(/^\/+|\/+$/g, '')
}

export async function listSubscriptions(
  params: { activeOnly?: boolean; limit?: number; offset?: number } = {},
): Promise<ActorSubscriptionListResponse> {
  const { data } = await http.get<ActorSubscriptionListResponse>('/api/subscriptions', {
    params: {
      active_only: params.activeOnly ?? undefined,
      limit: params.limit ?? 200,
      offset: params.offset ?? 0,
    },
  })
  return data
}

export async function getSubscription(
  actorHref: string,
  opts: { skipErrorToast?: boolean } = {},
): Promise<ActorSubscription | null> {
  try {
    const { data } = await http.get<ActorSubscription>(
      `/api/subscriptions/actors/${encodeURIComponent(actorIdFromHref(actorHref))}`,
      { skipErrorToast: opts.skipErrorToast },
    )
    return data
  } catch (err) {
    if (axios.isAxiosError(err) && err.response?.status === 404) return null
    throw err
  }
}

export async function upsertSubscription(
  actorHref: string,
  payload: { actor_name?: string | null; active?: boolean },
): Promise<ActorSubscription> {
  const { data } = await http.put<ActorSubscription>(
    `/api/subscriptions/actors/${encodeURIComponent(actorIdFromHref(actorHref))}`,
    payload,
  )
  return data
}

export async function deleteSubscription(actorHref: string): Promise<void> {
  await http.delete(
    `/api/subscriptions/actors/${encodeURIComponent(actorIdFromHref(actorHref))}`,
  )
}
```

- [ ] **Step 2: Write the new-works client**

Create `src/api/new-works.ts` (hand-typed):

```typescript
import { http } from './client'

// Hand-typed until src/types/api.gen.ts is regenerated to include the ADR-054
// WS2 new-works endpoints. Shapes mirror server/routes/subscriptions.ts.

export interface NewWork {
  video_code: string
  href: string
  actor_href: string
  title: string | null
  release_date: string | null
  discovered_at: string
  dismissed: boolean
}

export interface NewWorkListResponse {
  items: NewWork[]
  total: number
}

export async function listNewWorks(
  params: {
    actorHref?: string | null
    includeDismissed?: boolean
    limit?: number
    offset?: number
  } = {},
): Promise<NewWorkListResponse> {
  const { data } = await http.get<NewWorkListResponse>('/api/new-works', {
    params: {
      actor_href: params.actorHref ?? undefined,
      include_dismissed: params.includeDismissed ?? undefined,
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
    },
  })
  return data
}

export async function dismissNewWork(videoCode: string): Promise<void> {
  await http.post(`/api/new-works/${encodeURIComponent(videoCode)}/dismiss`)
}
```

- [ ] **Step 3: Type-check**

Run: `npx vue-tsc --noEmit -p tsconfig.app.json`
Expected: no errors referencing `subscriptions.ts` / `new-works.ts`. (Commit with Task 15.)

---

### Task 15: SubscriptionsView.vue + NewWorksView.vue

**Files:**
- Create: `src/pages/library/SubscriptionsView.vue`, `src/pages/library/NewWorksView.vue`

- [ ] **Step 1: Write SubscriptionsView**

Create `src/pages/library/SubscriptionsView.vue` (mirrors `ConsumptionView.vue`/`WatchlistView.vue`: `NSpin` + error `NAlert` + KPI `NGrid` + add-actor `NInput` row + `NDataTable` with an active-toggle and unfollow action):

```vue
<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import {
  NAlert, NButton, NCard, NDataTable, NGi, NGrid, NInput, NSpace, NSpin,
  NStatistic, NSwitch, useMessage, type DataTableColumns,
} from 'naive-ui'
import { useI18n } from 'vue-i18n'
import {
  listSubscriptions, upsertSubscription, deleteSubscription,
  type ActorSubscription,
} from '@/api/subscriptions'

const { t } = useI18n()
const message = useMessage()

const items = ref<ActorSubscription[]>([])
const total = ref(0)
const loading = ref(false)
const error = ref<string | null>(null)
const newActorHref = ref('')

async function fetchList(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const res = await listSubscriptions({ limit: 200 })
    items.value = res.items
    total.value = res.total
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('library.subscriptions.loadError')
  } finally {
    loading.value = false
  }
}

async function addActor(): Promise<void> {
  const href = newActorHref.value.trim()
  if (!href) return
  try {
    await upsertSubscription(href, { active: true })
    newActorHref.value = ''
    await fetchList()
  } catch {
    message.error(t('library.subscriptions.saveError'))
  }
}

async function toggleActive(row: ActorSubscription, active: boolean): Promise<void> {
  try {
    await upsertSubscription(row.actor_href, { actor_name: row.actor_name, active })
    await fetchList()
  } catch {
    message.error(t('library.subscriptions.saveError'))
  }
}

async function unfollow(row: ActorSubscription): Promise<void> {
  try {
    await deleteSubscription(row.actor_href)
    await fetchList()
  } catch {
    message.error(t('library.subscriptions.saveError'))
  }
}

const columns = computed<DataTableColumns<ActorSubscription>>(() => [
  {
    title: t('library.subscriptions.col.actor'),
    key: 'actor_href',
    render: (row) => row.actor_name || row.actor_href,
  },
  {
    title: t('library.subscriptions.col.active'),
    key: 'active',
    width: 100,
    render: (row) =>
      h(NSwitch, {
        value: row.active,
        'onUpdate:value': (v: boolean) => void toggleActive(row, v),
      }),
  },
  {
    title: t('library.subscriptions.col.lastChecked'),
    key: 'last_checked_at',
    render: (row) =>
      row.last_checked_at ? row.last_checked_at.slice(0, 19).replace('T', ' ') : '—',
  },
  {
    title: t('library.subscriptions.col.actions'),
    key: 'actions',
    width: 120,
    render: (row) =>
      h(
        NButton,
        { size: 'small', tertiary: true, type: 'error', onClick: () => void unfollow(row) },
        { default: () => t('library.subscriptions.unfollow') },
      ),
  },
])

onMounted(() => void fetchList())
</script>

<template>
  <NSpin :show="loading">
    <NAlert
      v-if="error"
      type="error"
      class="load-error"
    >
      {{ error }}
      <NButton
        size="small"
        style="margin-left: 12px"
        @click="fetchList"
      >
        {{ t('common.retry') }}
      </NButton>
    </NAlert>

    <NGrid
      :cols="2"
      :x-gap="12"
      :y-gap="12"
      responsive="screen"
      :item-responsive="true"
    >
      <NGi span="2 s:2 m:1">
        <NCard size="small">
          <NStatistic
            :label="t('library.subscriptions.total')"
            :value="total"
          />
        </NCard>
      </NGi>
    </NGrid>

    <NCard
      size="small"
      :title="t('library.subscriptions.followed')"
      class="block"
    >
      <NSpace class="add-row">
        <NInput
          v-model:value="newActorHref"
          :placeholder="t('library.subscriptions.addPlaceholder')"
          class="add-input"
          @keyup.enter="addActor"
        />
        <NButton
          type="primary"
          @click="addActor"
        >
          {{ t('library.subscriptions.add') }}
        </NButton>
      </NSpace>
      <NDataTable
        :columns="columns"
        :data="items"
        :bordered="false"
        size="small"
        :row-key="(row: ActorSubscription) => row.actor_href"
      />
    </NCard>
  </NSpin>
</template>

<style scoped>
.load-error {
  margin-bottom: 12px;
}
.block {
  margin-top: 12px;
}
.add-row {
  margin-bottom: 8px;
}
.add-input {
  width: 320px;
}
</style>
```

- [ ] **Step 2: Write NewWorksView**

Create `src/pages/library/NewWorksView.vue` (mirrors `WatchlistView.vue`; embeds WS1's `StatusControl.vue` for one-click want; a dismiss action removes the row from the feed):

```vue
<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import {
  NAlert, NButton, NCard, NDataTable, NGi, NGrid, NSpin, NStatistic,
  useMessage, type DataTableColumns,
} from 'naive-ui'
import { useI18n } from 'vue-i18n'
import StatusControl from '@/components/StatusControl.vue'
import { listNewWorks, dismissNewWork, type NewWork } from '@/api/new-works'

const { t } = useI18n()
const message = useMessage()

const items = ref<NewWork[]>([])
const total = ref(0)
const loading = ref(false)
const error = ref<string | null>(null)

async function fetchList(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const res = await listNewWorks({ limit: 200 })
    items.value = res.items
    total.value = res.total
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('library.newWorks.loadError')
  } finally {
    loading.value = false
  }
}

async function dismiss(videoCode: string): Promise<void> {
  try {
    await dismissNewWork(videoCode)
    await fetchList()
  } catch {
    message.error(t('library.newWorks.dismissError'))
  }
}

const columns = computed<DataTableColumns<NewWork>>(() => [
  {
    title: t('library.newWorks.col.videoCode'),
    key: 'video_code',
    render: (row) =>
      h(
        'span',
        { style: 'font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px;' },
        row.video_code,
      ),
  },
  {
    title: t('library.newWorks.col.title'),
    key: 'title',
    render: (row) => row.title || '—',
  },
  {
    title: t('library.newWorks.col.releaseDate'),
    key: 'release_date',
    width: 120,
    render: (row) => row.release_date || '—',
  },
  {
    // One-click "want" reuses WS1's StatusControl (videoCode + href props);
    // setting "want" upserts the WatchIntent row via the byte-mirrored upsert.
    title: t('library.newWorks.col.status'),
    key: 'status',
    width: 140,
    render: (row) =>
      h(StatusControl, {
        videoCode: row.video_code,
        href: row.href,
        initialStatus: null,
      }),
  },
  {
    title: t('library.newWorks.col.actions'),
    key: 'actions',
    width: 110,
    render: (row) =>
      h(
        NButton,
        { size: 'small', tertiary: true, onClick: () => void dismiss(row.video_code) },
        { default: () => t('library.newWorks.dismiss') },
      ),
  },
])

onMounted(() => void fetchList())
</script>

<template>
  <NSpin :show="loading">
    <NAlert
      v-if="error"
      type="error"
      class="load-error"
    >
      {{ error }}
      <NButton
        size="small"
        style="margin-left: 12px"
        @click="fetchList"
      >
        {{ t('common.retry') }}
      </NButton>
    </NAlert>

    <NGrid
      :cols="2"
      :x-gap="12"
      :y-gap="12"
      responsive="screen"
      :item-responsive="true"
    >
      <NGi span="2 s:2 m:1">
        <NCard size="small">
          <NStatistic
            :label="t('library.newWorks.total')"
            :value="total"
          />
        </NCard>
      </NGi>
    </NGrid>

    <NCard
      size="small"
      :title="t('library.newWorks.recent')"
      class="block"
    >
      <NDataTable
        :columns="columns"
        :data="items"
        :bordered="false"
        size="small"
        :row-key="(row: NewWork) => row.video_code"
      />
    </NCard>
  </NSpin>
</template>

<style scoped>
.load-error {
  margin-bottom: 12px;
}
.block {
  margin-top: 12px;
}
</style>
```

(Commit with Task 17.)

---

### Task 16: LibraryPage two gated tabs

**Files:**
- Modify: `src/pages/library/LibraryPage.vue`

- [ ] **Step 1: Import the views**

In `src/pages/library/LibraryPage.vue`, after `import WatchlistView from './WatchlistView.vue'`:

```typescript
import SubscriptionsView from './SubscriptionsView.vue'
import NewWorksView from './NewWorksView.vue'
```

- [ ] **Step 2: Add the gate computed**

After `const showWatchlist = computed(() => !!features.value?.watch_intent)`:

```typescript
const showSubscriptions = computed(() => !!features.value?.subscriptions)
```

- [ ] **Step 3: Push the tabs into `visibleTabs`**

In the `visibleTabs` computed, after `if (showWatchlist.value) tabs.push('watchlist')`:

```typescript
  if (showSubscriptions.value) {
    tabs.push('subscriptions')
    tabs.push('new-works')
  }
```

- [ ] **Step 4: Add the tab panes**

After the watchlist `<NTabPane>`:

```vue
      <NTabPane
        v-if="showSubscriptions"
        name="subscriptions"
        :tab="t('library.tabs.subscriptions')"
      >
        <SubscriptionsView />
      </NTabPane>
      <NTabPane
        v-if="showSubscriptions"
        name="new-works"
        :tab="t('library.tabs.newWorks')"
      >
        <NewWorksView />
      </NTabPane>
```

- [ ] **Step 5: Type-check**

Run: `npx vue-tsc --noEmit -p tsconfig.app.json`
Expected: no errors. (Commit with Task 17, together with the i18n keys the templates reference.)

---

### Task 17: i18n strings (en + zh parity)

**Files:**
- Modify: `src/i18n/locales/en.json`, `src/i18n/locales/zh-CN.json`

- [ ] **Step 1: en.json**

In `src/i18n/locales/en.json`:

1. Add `subscriptions` + `newWorks` keys to the `library.tabs` object (after `watchlist`):

```json
    "tabs": {
      "acquisition": "Acquisition",
      "ownership": "Ownership",
      "consumption": "Consumption",
      "watchlist": "Watchlist",
      "subscriptions": "Subscriptions",
      "newWorks": "New Works"
    },
```

2. Add a `subscriptions` block as a sibling of `library.watchlist` (inside `library`):

```json
    "subscriptions": {
      "loadError": "Failed to load subscriptions.",
      "saveError": "Failed to update subscription.",
      "total": "Followed",
      "followed": "Followed Actors",
      "add": "Follow",
      "unfollow": "Unfollow",
      "addPlaceholder": "Actor href, e.g. /actors/EvkJ",
      "col": {
        "actor": "Actor",
        "active": "Active",
        "lastChecked": "Last Checked",
        "actions": "Actions"
      }
    },
```

3. Add a `newWorks` block as a sibling of `library.subscriptions`:

```json
    "newWorks": {
      "loadError": "Failed to load new works.",
      "dismissError": "Failed to dismiss.",
      "total": "New Works",
      "recent": "New Works Feed",
      "dismiss": "Dismiss",
      "col": {
        "videoCode": "Code",
        "title": "Title",
        "releaseDate": "Release",
        "status": "Status",
        "actions": "Actions"
      }
    },
```

- [ ] **Step 2: zh-CN.json (same keys, translated values)**

In `src/i18n/locales/zh-CN.json`:

1. `library.tabs`:

```json
    "tabs": {
      "acquisition": "获取",
      "ownership": "拥有",
      "consumption": "消费",
      "watchlist": "想看清单",
      "subscriptions": "订阅",
      "newWorks": "新作"
    },
```

2. `library.subscriptions` block:

```json
    "subscriptions": {
      "loadError": "加载订阅失败。",
      "saveError": "更新订阅失败。",
      "total": "已关注",
      "followed": "已关注演员",
      "add": "关注",
      "unfollow": "取消关注",
      "addPlaceholder": "演员 href，例如 /actors/EvkJ",
      "col": {
        "actor": "演员",
        "active": "启用",
        "lastChecked": "上次检查",
        "actions": "操作"
      }
    },
```

3. `library.newWorks` block:

```json
    "newWorks": {
      "loadError": "加载新作失败。",
      "dismissError": "忽略失败。",
      "total": "新作",
      "recent": "新作动态",
      "dismiss": "忽略",
      "col": {
        "videoCode": "番号",
        "title": "标题",
        "releaseDate": "上映日期",
        "status": "状态",
        "actions": "操作"
      }
    },
```

- [ ] **Step 3: Verify both locales parse and have the same keys**

Run:
```bash
node -e "const en=require('./src/i18n/locales/en.json').library; const zh=require('./src/i18n/locales/zh-CN.json').library; const k=o=>Object.keys(o).sort().join(','); for (const b of ['subscriptions','newWorks']) { if (k(en[b])!==k(zh[b]) || k(en[b].col)!==k(zh[b].col)) throw new Error('key drift in '+b); } if (k(en.tabs)!==k(zh.tabs)) throw new Error('tabs key drift'); console.log('i18n parity ok')"
```
Expected: `i18n parity ok`

- [ ] **Step 4: Full frontend type-check + unit tests**

Run: `npx vue-tsc --noEmit -p tsconfig.app.json && npx vitest run --config vitest.config.ts`
Expected: type-check clean; frontend unit tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/api/subscriptions.ts src/api/new-works.ts src/pages/library/SubscriptionsView.vue src/pages/library/NewWorksView.vue src/pages/library/LibraryPage.vue src/i18n/locales/en.json src/i18n/locales/zh-CN.json
git commit -m "feat(web): add Library Subscriptions + New-Works tabs + i18n (ADR-054 WS2)"
```

---

## Phase F — ADR-040 amendment (coordination debt, bilingual)

WS2 owns superseding ADR-040 Phase-3 ("Subscriptions: whitelist bypassing the rating threshold"). The bypass is realized via the AdHoc selection path (no new index-gate-bypass code). ADR-040 is bilingual, so this touches both `.md` and `.zh.md` in the same commit.

### Task 18: Mark ADR-040 Phase-3 superseded (en + zh)

**Files:**
- Modify: `docs/design/ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.md`
- Modify: `docs/design/ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.zh.md`
- Modify: `docs/design/ADR-054-User-Intent-Discovery-Layer/ADR-054-user-intent-discovery-layer.md` + `.zh.md` (Status Log)

- [ ] **Step 1: Amend the en roadmap row**

In `docs/design/ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.md`, edit the Phase-3 row of the Implementation Roadmap table (currently line ~128):

Replace:
```
| Phase 3 — Subscriptions | IMP-ADR040-03 (stub) | whitelist bypassing the rating threshold (needs an index-gate-bypass design) |
```
With:
```
| Phase 3 — Subscriptions | **Superseded by [ADR-054](../ADR-054-User-Intent-Discovery-Layer/ADR-054-user-intent-discovery-layer.md) WS2** | Actor subscriptions ship in ADR-054 WS2 ([IMP-ADR054-02](../ADR-054-User-Intent-Discovery-Layer/IMP-ADR054-02-subscriptions.md)). The "whitelist bypassing the rating threshold" is realized by reusing the **AdHoc selection path** (`is_adhoc_mode=True`), which is tag-based only and never applies `PHASE2_MIN_RATE`/`PHASE2_MIN_COMMENTS` — no new index-gate-bypass code. Pinned by `tests/unit/test_adhoc_bypasses_rating_gate.py`. |
```

- [ ] **Step 2: Amend the en "Subscription" domain-language note**

In the same file, find the Domain Language entry (line ~150-151):
```
- **Subscription** — (Phase 3) a followed entity whose new releases bypass the
  rating threshold.
```
Append a superseded note:
```
- **Subscription** — (Phase 3, **superseded by ADR-054 WS2**) a followed entity
  whose new releases bypass the rating threshold. Realized in ADR-054 WS2 as
  `ActorSubscription` + the AdHoc-path scrape (the rating bypass is a property of
  `is_adhoc_mode` selection, not a new index-gate hook).
```

- [ ] **Step 3: Add an en Status Log entry**

In the ADR-040 `## Status Log`, append:
```
- 2026-06-14: Phase 3 ("Subscriptions") superseded by ADR-054 WS2
  ([IMP-ADR054-02](../ADR-054-User-Intent-Discovery-Layer/IMP-ADR054-02-subscriptions.md)).
  Actor subscriptions ship there; the rating-threshold bypass is realized by
  reusing the AdHoc selection path (no new index-gate-bypass). The
  `ContentFilterRule` engine keeps its exclude/include/age scope unchanged.
```

- [ ] **Step 4: Mirror Steps 1-3 in the zh file**

In `docs/design/ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.zh.md`:

Replace the Phase-3 roadmap row (line ~73):
```
| Phase 3 — 订阅 | IMP-ADR040-03 (stub) | 越过评分门槛的白名单（需设计索引闸旁路方案） |
```
With:
```
| Phase 3 — 订阅 | **由 [ADR-054](../ADR-054-User-Intent-Discovery-Layer/ADR-054-user-intent-discovery-layer.zh.md) WS2 取代** | 演员订阅在 ADR-054 WS2 实现（[IMP-ADR054-02](../ADR-054-User-Intent-Discovery-Layer/IMP-ADR054-02-subscriptions.md)）。"越过评分门槛的白名单"通过复用 **AdHoc 选择路径**（`is_adhoc_mode=True`）实现——该路径仅基于 tag，从不应用 `PHASE2_MIN_RATE`/`PHASE2_MIN_COMMENTS`，无需新的索引闸旁路代码。由 `tests/unit/test_adhoc_bypasses_rating_gate.py` 锁定。 |
```

Append a superseded note to the zh "Subscription（订阅）" domain-language entry (line ~93):
```
- **Subscription（订阅）**——（Phase 3，**由 ADR-054 WS2 取代**）一个被关注的实体，
  其新作越过评分门槛。在 ADR-054 WS2 中以 `ActorSubscription` + AdHoc 路径爬取实现
  （评分旁路是 `is_adhoc_mode` 选择的固有属性，并非新的索引闸钩子）。
```

Append a zh Status Log entry:
```
- 2026-06-14：Phase 3（"订阅"）由 ADR-054 WS2
  （[IMP-ADR054-02](../ADR-054-User-Intent-Discovery-Layer/IMP-ADR054-02-subscriptions.md)）取代。
  演员订阅在该处实现；评分门槛旁路通过复用 AdHoc 选择路径实现（无新的索引闸旁路）。
  `ContentFilterRule` 引擎的 exclude/include/age 范围保持不变。
```

- [ ] **Step 5: Add an ADR-054 Status Log entry (en + zh)**

In `docs/design/ADR-054-User-Intent-Discovery-Layer/ADR-054-user-intent-discovery-layer.md` (and `.zh.md`), append to the Status Log:
```
- 2026-06-14: WS2 (Actor Subscriptions + New-Works Monitoring) implemented via
  IMP-ADR054-02. Adds ActorSubscription + NewWorks (HISTORY_DB), dual-backend
  /api/subscriptions + /api/new-works, the `subscriptions` capability flag, the
  SubscriptionMonitor cron (AdHoc-path scrape, no new rating-bypass code), and
  two gated Library tabs reusing WS1's StatusControl. Supersedes ADR-040 Phase-3.
```
(zh: translate the same entry; preserve table/identifier names verbatim.)

- [ ] **Step 6: Verify links + commit**

Run (sanity-check the new cross-links resolve):
```bash
cd /Users/tedwu/JAVDB_AutoSpider_CICD
test -f docs/design/ADR-054-User-Intent-Discovery-Layer/IMP-ADR054-02-subscriptions.md && echo "IMP present"
grep -l "Superseded by" docs/design/ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.md
grep -l "取代" docs/design/ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.zh.md
```
Expected: `IMP present` + both grep matches print their filenames.

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add \
  docs/design/ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.md \
  docs/design/ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.zh.md \
  docs/design/ADR-054-User-Intent-Discovery-Layer/ADR-054-user-intent-discovery-layer.md \
  docs/design/ADR-054-User-Intent-Discovery-Layer/ADR-054-user-intent-discovery-layer.zh.md
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "docs(adr): supersede ADR-040 Phase-3 with ADR-054 WS2 (bilingual)"
```

---

## Final verification gate

- [ ] **[MAIN] backend tests**

Run:
```bash
cd /Users/tedwu/JAVDB_AutoSpider_CICD && python3 -m pytest \
  tests/unit/test_subscription_repo.py \
  tests/unit/test_subscriptions_router.py \
  tests/unit/test_subscription_monitor.py \
  tests/unit/test_adhoc_bypasses_rating_gate.py \
  tests/unit/test_actor_subscription_upsert_parity.py -q
```
Expected: all pass.

- [ ] **[MAIN] CLI + workflow smoke**

Run:
```bash
cd /Users/tedwu/JAVDB_AutoSpider_CICD
STORAGE_BACKEND=sqlite python3 -m apps.cli.ops.subscription_monitor --dry-run
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/SubscriptionMonitor.yml')); print('yaml ok')"
```
Expected: CLI lists 0 active subscriptions + exits 0; `yaml ok`.

- [ ] **[WEB] worker + frontend tests + type-check**

Run (from cwd):
```bash
npx vitest run \
  server/__tests__/subscription-routes.test.ts \
  server/__tests__/actor-subscription-upsert-parity.test.ts \
  --config vitest.server.config.ts
npx vue-tsc --noEmit -p tsconfig.app.json
```
Expected: all pass; no type errors.

- [ ] **Manual smoke (optional, requires the tables applied to a dev D1/SQLite + both servers running):** open Library → the **Subscriptions** tab appears (when `subscriptions` is true); follow an actor (`/actors/EvkJ`) → it lists; run `SubscriptionMonitor` (or seed a `NewWorks` row) → the **New Works** tab shows the release; set its `StatusControl` to "Want" → it appears in the WS1 **Watchlist** tab; click **Dismiss** → it leaves the feed.

- [ ] **Apply the migration to remote D1 (deploy step, run once when shipping):**

```bash
cd /Users/tedwu/JAVDB_AutoSpider_CICD
wrangler d1 execute javdb-history --remote \
  --file=javdb/migrations/d1/2026_06_14_add_actor_subscription_new_works.sql
python3 -m apps.cli.db.sync_d1_to_sqlite --apply --force-overwrite-all
```

- [ ] **Enable the cron (deploy step):** confirm the `SubscriptionMonitor.yml` schedule is active on `main` and that the required secrets/vars are present (`JAVDB_*`, `PROXY_POOL_JSON`, `D1_*`, `ARTIFACT_KEY`, `DEPLOY_KEY`). Optionally set `vars.SUBSCRIPTION_MONITOR_RUNNER`.

---

## Coverage check (self-review)

- ActorSubscription + NewWorks (HISTORY_DB, `actor_href` identity, cursor, `dismissed`) → Tasks 1, 2. `_HISTORY_DDL` mirror → Task 1.
- Dual-backend `/api/subscriptions` (follow/list/get/unfollow) + `/api/new-works` (feed/dismiss), byte-mirrored UPSERT → Tasks 2, 3 (Python) + 9, 10 (TS); parity-pinned in Task 12.
- Scheduled scrape reusing the AdHoc path + new-works diff (vs `MovieHistory` + cursor, idempotent) → Tasks 6, 8. **No new rating-bypass code**; the `is_adhoc_mode` bypass pinned → Task 7. ADR-040 Phase-3 superseded by amendment only → Task 18.
- `subscriptions` capability flag, both backends, probe `HISTORY_DB` → Task 4 (Python) + Task 11 (TS), surfaced to the SPA in Task 13.
- Two gated Library tabs (`subscriptions`, `new-works`) reusing WS1's `StatusControl` for one-click want → Tasks 15, 16. en/zh parity → Task 17.
- Admin-gated mutations on both backends (follow/unfollow/dismiss) → Tasks 3 (Python `require_role("admin")`) + 10 (TS `requireRole("admin")`).
- ADR-040 Phase-3 superseded (bilingual) + ADR-054 Status Log → Task 18.
- Out of scope held: tag/series subscriptions, notifications, new bypass code, Worker-cron, a new "mark want" endpoint, `user_id` — none introduced.
