# User-Intent Watchlist (ADR-054 WS1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the operator manually mark a movie `want` or `viewed`, persisted to a new D1 `WatchIntent` table, surfaced as an inline control on the Movies list and as a Library "Watchlist" tab.

**Architecture:** A net-new authoritative `WatchIntent` table in `HISTORY_DB`, keyed by `video_code` with `href` as a bridge column. Dual-backend read+write endpoints (`/api/watchlist`) mirrored byte-for-byte between the Python FastAPI backend (`apps/api`) and the TypeScript Hono Worker (`server/`), per the ADR-022 preferences precedent and the ADR-017/034 dual-backend rule. A `watch_intent` capability flag gates the UI. The frontend adds a `StatusControl.vue` setter (modelled on `HeartButton.vue`) and a `WatchlistView.vue` Library tab (modelled on `ConsumptionView.vue`).

**Tech Stack:** D1 (SQLite dialect), Python 3 / FastAPI / pydantic / pytest, TypeScript / Hono / `@cloudflare/workers-types` / Vitest (`cloudflare:test`), Vue 3 `<script setup>` / Naive UI / vue-i18n / Vitest.

**Cross-repo note.** Two git repos are involved:
- **[MAIN]** = `/Users/tedwu/JAVDB_AutoSpider_CICD` — D1 migration, Python router/repo/schema, capabilities, `openapi.json`, Python tests. This IMP and ADR-054 live here. Run `git` with `git -C /Users/tedwu/JAVDB_AutoSpider_CICD ...`.
- **[WEB]** = the `javdb-autospider-web` working directory (current cwd) — TS Worker route/service, capabilities probe, Vue components, i18n, Worker tests, `src/types/api.gen.ts`. Run `git` from cwd.

Work the phases in order: **A (D1 + Python)** → **B (TS Worker)** → **C (parity guard)** → **D (frontend)**. Each repo should be on its own feature branch.

---

## Design decisions locked by ADR-054 D3 (do not re-litigate)

- Status enum is **`want | viewed`**. No `browsed` (dropped — no injection-free trigger). `untracked` = **absent row**; an explicit un-track is a `DELETE`.
- Table `WatchIntent` in **`HISTORY_DB`** (alongside `MovieRatings`), PK `video_code`, with `href` stored as a `NOT NULL` bridge column. No `user_id` (single-operator). No edit-lock.
- Capability flag **`watch_intent`** (probe `SELECT 1 FROM WatchIntent LIMIT 1` against `HISTORY_DB`).
- Two surfaces: inline `StatusControl` setter on the Movies list (primary SETTER) + a Library **Watchlist** tab (aggregate VIEW). Both gated by `watch_intent`.
- Read **and** write ship in this one IMP. The upsert SQL is byte-mirrored across backends and pinned by a dedicated parity test (the Query Contract Golden does not cover upserts).
- Do **not** derive `status` from `ConsumptionSignal` or write back into it (ADR-033 D11 boundary). Reconciliation and bulk ops are out of scope.

---

## File Structure

**[MAIN] create:**
- `javdb/migrations/d1/2026_06_13_add_watch_intent.sql` — the `WatchIntent` D1 DDL (Write-Class: authoritative).
- `javdb/storage/repos/watchlist_repo.py` — `WatchIntentRepo` (upsert/get/list/delete) + `WATCH_INTENT_UPSERT_SQL`.
- `apps/api/schemas/watchlist.py` — pydantic request/response models.
- `apps/api/routers/watchlist.py` — FastAPI router (`/api/watchlist`).
- `tests/unit/test_watchlist_repo.py` — repo unit tests.
- `tests/unit/test_watch_intent_upsert_parity.py` — SQL parity golden (Python side).

**[MAIN] modify:**
- `javdb/storage/db/_db_migrations.py` — append the `WatchIntent` DDL into `_HISTORY_DDL`.
- `apps/api/services/runtime.py` — register `watchlist_router`.
- `apps/api/routers/capabilities.py` — add `_watch_intent_enabled()` probe + `watch_intent` feature.
- `apps/api/schemas/capabilities_payloads.py` — add `watch_intent: bool` to `Features`.
- `docs/api/openapi.json` — regenerated (mechanical).

**[WEB] create:**
- `server/services/watchlist-service.ts` — D1 query functions + `WATCH_INTENT_UPSERT_SQL`.
- `server/routes/watchlist.ts` — Hono route (`/api/watchlist`).
- `server/__tests__/watchlist-routes.test.ts` — Worker route tests (self-seeds the table).
- `server/__tests__/watch-intent-upsert-parity.test.ts` — SQL parity golden (TS side).
- `src/api/watchlist.ts` — hand-typed api client.
- `src/components/StatusControl.vue` — inline want/viewed setter.
- `src/pages/library/WatchlistView.vue` — Library "Watchlist" tab view.

**[WEB] modify:**
- `server/app.ts` — mount `watchlistRoutes`.
- `server/routes/capabilities.ts` — add `watchIntentEnabled()` probe + `watch_intent` feature.
- `src/pages/data/MoviesPage.vue` — add the gated inline status column.
- `src/pages/library/LibraryPage.vue` — add the gated Watchlist tab.
- `src/i18n/locales/en.json` + `src/i18n/locales/zh-CN.json` — new strings (en/zh parity).
- `src/types/api.gen.ts` — regenerated (mechanical) so `Features.watch_intent` is typed.

---

## Phase A — D1 + Python backend [MAIN]

### Task 1: WatchIntent D1 migration + SQLite mirror

**Files:**
- Create: `javdb/migrations/d1/2026_06_13_add_watch_intent.sql`
- Modify: `javdb/storage/db/_db_migrations.py` (inside the `_HISTORY_DDL` triple-quoted literal, before its closing `"""`)

- [x] **Step 1: Write the migration file**

Create `javdb/migrations/d1/2026_06_13_add_watch_intent.sql` (2-space indent matches the existing `d1/*.sql` convention; the `-- Write-Class:` header is mandatory on new `CREATE TABLE` migrations per `javdb/migrations/README.md` / ADR-042 D6, enforced by `.github/workflows/validate-d1-write-class.yml`):

```sql
-- 2026-06-13: Add WatchIntent table (ADR-054 WS1 Phase 1).
-- Write-Class: authoritative
--
-- Apply with:
--   wrangler d1 execute javdb-history --remote \
--     --file=javdb/migrations/d1/2026_06_13_add_watch_intent.sql
-- Then re-align the SQLite mirror:
--   python3 -m apps.cli.db.sync_d1_to_sqlite --apply --force-overwrite-all
--
-- WatchIntent: the operator's manual want/viewed status per movie (user-intent,
-- ADR-054 WS1). Single-operator (no user_id). Distinct from ConsumptionSignal
-- (machine-observed media-server evidence, in javdb-operations) and from
-- MovieMetadata.want_count/watched_count (scraped site-wide crowd counts).
-- Co-located with MovieRatings in javdb-history. Outside the Pending->Commit flow.

CREATE TABLE IF NOT EXISTS WatchIntent (
  video_code  TEXT PRIMARY KEY,
  href        TEXT NOT NULL,
  status      TEXT NOT NULL CHECK (status IN ('want','viewed')),
  notes       TEXT,
  status_at   TEXT,
  updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_watch_intent_status ON WatchIntent(status);
```

- [x] **Step 2: Verify the Write-Class CI check passes for the new file**

Run: `python3 scripts/ci/validate_d1_write_class.py --paths javdb/migrations/d1/2026_06_13_add_watch_intent.sql`
Expected: exits 0 / prints OK (a valid `Write-Class: authoritative` header is detected). If the script takes no args, run it with no args and confirm it does not report the new file as missing a header.

- [x] **Step 3: Mirror the DDL into `_HISTORY_DDL`**

In `javdb/storage/db/_db_migrations.py`, find the `ContentPreferences` block inside the `_HISTORY_DDL` triple-quoted string literal (it ends with the `idx_content_prefs_hearted` index, just before the literal's closing `"""`). Insert the following **immediately after** that index line, still inside the same `"""` literal (4-space indent matches the surrounding Python-embedded DDL):

```sql
CREATE TABLE IF NOT EXISTS WatchIntent (
    video_code  TEXT PRIMARY KEY,
    href        TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN ('want','viewed')),
    notes       TEXT,
    status_at   TEXT,
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_watch_intent_status ON WatchIntent(status);
```

- [x] **Step 4: Verify `_HISTORY_DDL` still parses and creates the table**

Run: `python3 -c "import sqlite3; from javdb.storage.db import _db_migrations as m; c=sqlite3.connect(':memory:'); c.executescript(m._HISTORY_DDL); print([r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name='WatchIntent'\")])"`
Expected: prints `['WatchIntent']`

- [x] **Step 5: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add javdb/migrations/d1/2026_06_13_add_watch_intent.sql javdb/storage/db/_db_migrations.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "feat(db): add WatchIntent table (ADR-054 WS1)"
```

---

### Task 2: WatchIntentRepo (TDD)

**Files:**
- Create: `javdb/storage/repos/watchlist_repo.py`
- Test: `tests/unit/test_watchlist_repo.py`

- [x] **Step 1: Write the failing test**

Create `tests/unit/test_watchlist_repo.py` (mirrors `tests/unit/test_preference_repo.py`: seed the schema from the real D1 migration so CHECK constraints match production):

```python
"""Unit tests for WatchIntentRepo (ADR-054 WS1)."""

import pathlib
import sqlite3

import pytest

from javdb.storage.repos.watchlist_repo import WatchIntentRepo

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_WATCH_INTENT_DDL = (
    _REPO_ROOT / "javdb/migrations/d1/2026_06_13_add_watch_intent.sql"
).read_text(encoding="utf-8")


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test_watch.db")
    conn = sqlite3.connect(path)
    conn.executescript(_WATCH_INTENT_DDL)
    conn.commit()
    conn.close()
    return path


def test_upsert_creates_row(db_path):
    repo = WatchIntentRepo(db_path=db_path)
    row = repo.upsert(video_code="ABC-001", href="/v/abc001", status="want")
    assert row["video_code"] == "ABC-001"
    assert row["href"] == "/v/abc001"
    assert row["status"] == "want"
    assert row["updated_at"]


def test_upsert_updates_status_in_place(db_path):
    repo = WatchIntentRepo(db_path=db_path)
    repo.upsert(video_code="ABC-001", href="/v/abc001", status="want")
    row = repo.upsert(video_code="ABC-001", href="/v/abc001", status="viewed")
    assert row["status"] == "viewed"
    items, total = repo.list()
    assert total == 1  # upsert, not a second row


def test_get_returns_none_when_absent(db_path):
    assert WatchIntentRepo(db_path=db_path).get("NOPE-999") is None


def test_list_filters_by_status(db_path):
    repo = WatchIntentRepo(db_path=db_path)
    repo.upsert(video_code="A-1", href="/v/a1", status="want")
    repo.upsert(video_code="B-2", href="/v/b2", status="viewed")
    items, total = repo.list(status="want")
    assert total == 1
    assert items[0]["video_code"] == "A-1"


def test_delete_removes_row(db_path):
    repo = WatchIntentRepo(db_path=db_path)
    repo.upsert(video_code="A-1", href="/v/a1", status="want")
    assert repo.delete("A-1") is True
    assert repo.get("A-1") is None
    assert repo.delete("A-1") is False  # already gone
```

- [x] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/unit/test_watchlist_repo.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'javdb.storage.repos.watchlist_repo'`

- [x] **Step 3: Implement the repo**

Create `javdb/storage/repos/watchlist_repo.py` (mirrors `preference_repo.py`: lazy `HISTORY_DB_PATH` via `_db` for BFR-016, `get_db` context manager, dict rows):

```python
"""Repository for the WatchIntent table (ADR-054 WS1)."""

from __future__ import annotations

from typing import List, Optional, Tuple

from javdb.storage import db as _db
from javdb.storage.db import get_db

# Byte-mirrored with server/services/watchlist-service.ts (ADR-017 dual-backend
# parity). Pinned by tests/unit/test_watch_intent_upsert_parity.py.
WATCH_INTENT_UPSERT_SQL = """
    INSERT INTO WatchIntent (video_code, href, status, notes, status_at, updated_at)
    VALUES (?, ?, ?, ?,
        strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    ON CONFLICT(video_code) DO UPDATE SET
        href       = excluded.href,
        status     = excluded.status,
        notes      = COALESCE(excluded.notes, notes),
        status_at  = strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
"""


class WatchIntentRepo:
    """Typed wrapper over the WatchIntent table in history.db."""

    def __init__(self, *, db_path: Optional[str] = None) -> None:
        # Resolve HISTORY_DB_PATH at construction via ``_db`` (not bound at
        # import) so pytest's path monkeypatch is honoured (BFR-016).
        self._db_path = db_path or _db.HISTORY_DB_PATH

    def upsert(
        self, *, video_code: str, href: str, status: str, notes: Optional[str] = None
    ) -> dict:
        """UPSERT a watch intent. Returns the updated row as a dict."""
        with get_db(self._db_path) as conn:
            conn.execute(WATCH_INTENT_UPSERT_SQL, (video_code, href, status, notes))
            row = conn.execute(
                "SELECT * FROM WatchIntent WHERE video_code = ?", (video_code,)
            ).fetchone()
        return dict(row)

    def get(self, video_code: str) -> Optional[dict]:
        with get_db(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM WatchIntent WHERE video_code = ?", (video_code,)
            ).fetchone()
        return dict(row) if row is not None else None

    def list(
        self, *, status: Optional[str] = None, limit: int = 50, offset: int = 0
    ) -> Tuple[List[dict], int]:
        """Return (items, total_count) for paginated listing."""
        where = "WHERE status = ?" if status else ""
        params: list = [status] if status else []
        with get_db(self._db_path) as conn:
            # Alias + key access: D1/Dual cursors return dict-shaped rows.
            total = conn.execute(
                f"SELECT COUNT(*) AS cnt FROM WatchIntent {where}",  # noqa: S608
                params,
            ).fetchone()["cnt"]
            rows = conn.execute(
                f"SELECT * FROM WatchIntent {where} "  # noqa: S608
                "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
        return [dict(r) for r in rows], total

    def delete(self, video_code: str) -> bool:
        """Delete a watch intent (un-track). Returns True if a row was removed."""
        with get_db(self._db_path) as conn:
            cur = conn.execute(
                "DELETE FROM WatchIntent WHERE video_code = ?", (video_code,)
            )
            return cur.rowcount > 0
```

- [x] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest tests/unit/test_watchlist_repo.py -q`
Expected: PASS (5 passed)

- [x] **Step 5: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add javdb/storage/repos/watchlist_repo.py tests/unit/test_watchlist_repo.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "feat(storage): add WatchIntentRepo (ADR-054 WS1)"
```

---

### Task 3: Python schemas + router + registration

**Files:**
- Create: `apps/api/schemas/watchlist.py`, `apps/api/routers/watchlist.py`
- Modify: `apps/api/services/runtime.py`

- [x] **Step 1: Write the schemas**

Create `apps/api/schemas/watchlist.py` (the `Literal` status gives an automatic 422 on bad input, mirroring how `MovieRatingUpsert` bounds `rating`):

```python
"""Pydantic schemas for watchlist (watch-intent) endpoints (ADR-054 WS1)."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel


class WatchIntentUpsert(BaseModel):
    href: str
    status: Literal["want", "viewed"]
    notes: Optional[str] = None


class WatchIntentResponse(BaseModel):
    video_code: str
    href: str
    status: str
    notes: Optional[str] = None
    status_at: Optional[str] = None
    updated_at: str


class WatchIntentListResponse(BaseModel):
    items: List[WatchIntentResponse]
    total: int
```

- [x] **Step 2: Write the router**

Create `apps/api/routers/watchlist.py` (mirrors `routers/preferences.py`: per-route `_require_auth`, `_row_to_*` mapper, `{"error": {"code", "message"}}` envelope). The list route uses path `""` so the full path is exactly `/api/watchlist` (no trailing slash, matching the client). Declare the list route before the `/{video_code}` routes:

```python
"""Watch-intent (watchlist) API routes (ADR-054 WS1)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from apps.api.infra.auth import _require_auth
from apps.api.schemas.watchlist import (
    WatchIntentListResponse,
    WatchIntentResponse,
    WatchIntentUpsert,
)
from javdb.storage.repos.watchlist_repo import WatchIntentRepo

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])

_NOT_FOUND = {"error": {"code": "watchlist.not_found", "message": "Record not found"}}
_INVALID_STATUS = {
    "error": {
        "code": "watchlist.invalid_status",
        "message": "status must be one of: want, viewed",
    }
}


def _row_to_intent(row: dict) -> WatchIntentResponse:
    return WatchIntentResponse(
        video_code=row["video_code"],
        href=row["href"],
        status=row["status"],
        notes=row.get("notes"),
        status_at=row.get("status_at"),
        updated_at=row["updated_at"],
    )


@router.get("", response_model=WatchIntentListResponse)
def list_watch_intents(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _user=Depends(_require_auth),
):
    if status is not None and status not in {"want", "viewed"}:
        raise HTTPException(status_code=422, detail=_INVALID_STATUS)
    items, total = WatchIntentRepo().list(status=status, limit=limit, offset=offset)
    return WatchIntentListResponse(
        items=[_row_to_intent(r) for r in items], total=total
    )


@router.put("/{video_code}", response_model=WatchIntentResponse)
def upsert_watch_intent(
    video_code: str,
    body: WatchIntentUpsert,
    _user=Depends(_require_auth),
):
    row = WatchIntentRepo().upsert(
        video_code=video_code, href=body.href, status=body.status, notes=body.notes
    )
    return _row_to_intent(row)


@router.get("/{video_code}", response_model=WatchIntentResponse)
def get_watch_intent(video_code: str, _user=Depends(_require_auth)):
    row = WatchIntentRepo().get(video_code)
    if row is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return _row_to_intent(row)


@router.delete("/{video_code}")
def delete_watch_intent(video_code: str, _user=Depends(_require_auth)):
    deleted = WatchIntentRepo().delete(video_code)
    return {"deleted": deleted}
```

- [x] **Step 3: Register the router**

In `apps/api/services/runtime.py`: add the import alongside the other `*_router` imports (search for `preferences_router`):

```python
from apps.api.routers.watchlist import router as watchlist_router
```

Then add `watchlist_router,` into the `for router in (...)` tuple (after `preferences_router,`).

- [x] **Step 4: Write a router smoke test**

Create `tests/unit/test_watchlist_router.py`:

```python
"""Smoke tests for the watchlist router (ADR-054 WS1)."""

import pathlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

from javdb.storage import db as _db
from apps.api.infra.auth import _require_auth


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_WATCH_INTENT_DDL = (
    _REPO_ROOT / "javdb/migrations/d1/2026_06_13_add_watch_intent.sql"
).read_text(encoding="utf-8")


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = str(tmp_path / "history.db")
    conn = sqlite3.connect(path)
    conn.executescript(_WATCH_INTENT_DDL)
    conn.commit()
    conn.close()
    monkeypatch.setattr(_db, "HISTORY_DB_PATH", path)
    from apps.api.services.runtime import app
    # Standard FastAPI test seam: override the auth dependency so the smoke test
    # does not need a real JWT (auth itself is covered by the auth router tests).
    app.dependency_overrides[_require_auth] = lambda: {"username": "test"}
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(_require_auth, None)


def test_put_then_get_then_delete(client):
    put = client.put(
        "/api/watchlist/ABC-001",
        json={"href": "/v/abc001", "status": "want"},
    )
    assert put.status_code == 200, put.text
    assert put.json()["status"] == "want"

    got = client.get("/api/watchlist/ABC-001")
    assert got.status_code == 200
    assert got.json()["video_code"] == "ABC-001"

    listed = client.get("/api/watchlist", params={"status": "want"})
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1

    bad = client.put(
        "/api/watchlist/ABC-001", json={"href": "/v/abc001", "status": "nope"}
    )
    assert bad.status_code == 422

    deleted = client.delete("/api/watchlist/ABC-001")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert client.get("/api/watchlist/ABC-001").status_code == 404
```

> The fixture uses `app.dependency_overrides[_require_auth]` — FastAPI's standard auth seam — so it needs no real token. This also now asserts the bare-path list route (`GET /api/watchlist`) returns `total == 1`, confirming the `@router.get("")` path resolves without a trailing-slash redirect.

- [x] **Step 5: Run the test**

Run: `python3 -m pytest tests/unit/test_watchlist_router.py -q`
Expected: PASS

- [x] **Step 6: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add apps/api/schemas/watchlist.py apps/api/routers/watchlist.py apps/api/services/runtime.py tests/unit/test_watchlist_router.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "feat(api): add /api/watchlist router (ADR-054 WS1)"
```

---

### Task 4: `watch_intent` capability flag (Python)

**Files:**
- Modify: `apps/api/routers/capabilities.py`, `apps/api/schemas/capabilities_payloads.py`

- [x] **Step 1: Add the field to the `Features` schema**

In `apps/api/schemas/capabilities_payloads.py`, add `watch_intent: bool` to `class Features(BaseModel)` immediately after `library_consumption: bool`:

```python
    library_consumption: bool
    watch_intent: bool
    site_drift_sentinel: bool
```

- [x] **Step 2: Add the probe + wire it**

In `apps/api/routers/capabilities.py`, add this probe after `_library_consumption_enabled()` (note: probes `HISTORY_DB`, where `WatchIntent` lives — not `OPERATIONS_DB`):

```python
def _watch_intent_enabled() -> bool:
    """True when the ADR-054 WatchIntent table is queryable (capability honesty)."""
    try:
        from javdb.storage.db import HISTORY_DB_PATH, get_db
        with get_db(HISTORY_DB_PATH) as conn:
            conn.execute("SELECT 1 FROM WatchIntent LIMIT 1").fetchone()
        return True
    except Exception:
        return False
```

Then in `build_capabilities()`, add `watch_intent=_watch_intent_enabled(),` to the `Features(...)` call, immediately after `library_consumption=_library_consumption_enabled(),`.

- [x] **Step 3: Verify capabilities builds and exposes the flag**

Run: `python3 -c "from apps.api.routers.capabilities import build_capabilities; print(build_capabilities().features.watch_intent)"`
Expected: prints `False` (no table on this path) — proves the field exists and the probe degrades gracefully.

- [x] **Step 4: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add apps/api/routers/capabilities.py apps/api/schemas/capabilities_payloads.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "feat(api): expose watch_intent capability flag (ADR-054 WS1)"
```

---

### Task 5: Regenerate the OpenAPI contract

**Files:**
- Modify: `docs/api/openapi.json`

- [x] **Step 1: Dump the OpenAPI schema**

Run: `cd /Users/tedwu/JAVDB_AutoSpider_CICD && python3 -m apps.cli.ops.dump_openapi`
Expected: `wrote /Users/tedwu/JAVDB_AutoSpider_CICD/docs/api/openapi.json (<N> bytes)`

- [x] **Step 2: Verify the new surface is in the contract**

Run: `python3 -c "import json; d=json.load(open('docs/api/openapi.json')); print('/api/watchlist' in d['paths']); print('watch_intent' in d['components']['schemas']['Features']['properties'])"`
Expected: prints `True` then `True`

- [x] **Step 3: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add docs/api/openapi.json
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "chore(api): re-vendor openapi.json for watchlist (ADR-054 WS1)"
```

---

## Phase B — TypeScript Worker [WEB]

### Task 6: watchlist-service.ts

**Files:**
- Create: `server/services/watchlist-service.ts`

- [x] **Step 1: Write the service**

Create `server/services/watchlist-service.ts` (mirrors `preference-service.ts`: `D1Database` is an ambient global — do not import it; functions take `db` not `env`; the UPSERT SQL is byte-identical to the Python `WATCH_INTENT_UPSERT_SQL`):

```typescript
// Watch-intent (watchlist) D1 queries (ADR-054 WS1).
// Keep this module free of Hono / c.env references; callers pass the binding.

export interface WatchIntentRow {
  video_code: string;
  href: string;
  status: string;
  notes: string | null;
  status_at: string | null;
  updated_at: string;
}

// Byte-mirrored with javdb/storage/repos/watchlist_repo.py WATCH_INTENT_UPSERT_SQL
// (ADR-017 dual-backend parity). Pinned by watch-intent-upsert-parity.test.ts.
export const WATCH_INTENT_UPSERT_SQL = `
    INSERT INTO WatchIntent (video_code, href, status, notes, status_at, updated_at)
    VALUES (?, ?, ?, ?,
        strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    ON CONFLICT(video_code) DO UPDATE SET
        href       = excluded.href,
        status     = excluded.status,
        notes      = COALESCE(excluded.notes, notes),
        status_at  = strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')`;

export async function upsertWatchIntent(
  db: D1Database,
  videoCode: string,
  href: string,
  status: string,
  notes: string | null,
): Promise<WatchIntentRow> {
  await db.prepare(WATCH_INTENT_UPSERT_SQL).bind(videoCode, href, status, notes).run();
  return (await getWatchIntent(db, videoCode))!;
}

export async function getWatchIntent(
  db: D1Database,
  videoCode: string,
): Promise<WatchIntentRow | null> {
  return db
    .prepare("SELECT * FROM WatchIntent WHERE video_code = ?")
    .bind(videoCode)
    .first<WatchIntentRow>();
}

export async function listWatchIntents(
  db: D1Database,
  status: string | null,
  limit: number,
  offset: number,
): Promise<{ items: WatchIntentRow[]; total: number }> {
  const where = status ? "WHERE status = ?" : "";
  const filterBindings: string[] = status ? [status] : [];

  const total =
    (await db
      .prepare(`SELECT COUNT(*) AS n FROM WatchIntent ${where}`)
      .bind(...filterBindings)
      .first<{ n: number }>())?.n ?? 0;

  const rows = await db
    .prepare(
      `SELECT * FROM WatchIntent ${where} ORDER BY updated_at DESC LIMIT ? OFFSET ?`,
    )
    .bind(...filterBindings, limit, offset)
    .all<WatchIntentRow>();

  return { items: rows.results, total };
}

export async function deleteWatchIntent(
  db: D1Database,
  videoCode: string,
): Promise<boolean> {
  const res = await db
    .prepare("DELETE FROM WatchIntent WHERE video_code = ?")
    .bind(videoCode)
    .run();
  return (res.meta?.changes ?? 0) > 0;
}
```

- [x] **Step 2: Verify it type-checks**

Run: `npx tsc -p server/tsconfig.json --noEmit`
Expected: no errors referencing `watchlist-service.ts`.

(Commit together with Task 7.)

---

### Task 7: watchlist.ts route + mount (TDD)

**Files:**
- Create: `server/routes/watchlist.ts`, `server/__tests__/watchlist-routes.test.ts`
- Modify: `server/app.ts`

- [x] **Step 1: Write the failing route test**

Create `server/__tests__/watchlist-routes.test.ts` (self-seeds the table like `preferences-routes.test.ts`; mutations require the CSRF `mutationHeaders`):

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

async function seedWatchIntent() {
  await env.HISTORY_DB.prepare(
    `CREATE TABLE IF NOT EXISTS WatchIntent (
      video_code TEXT PRIMARY KEY, href TEXT NOT NULL,
      status TEXT NOT NULL CHECK (status IN ('want','viewed')),
      notes TEXT, status_at TEXT,
      updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    )`,
  ).run();
}

describe("Watchlist routes", () => {
  beforeAll(async () => {
    await seedWatchIntent();
  });

  it("PUT upserts, GET reads, list filters, DELETE untracks", async () => {
    const { accessToken, csrfToken } = await login();

    const put = await app.request(
      "/api/watchlist/ABC-001",
      {
        method: "PUT",
        headers: mutationHeaders(accessToken, csrfToken),
        body: JSON.stringify({ href: "/v/abc001", status: "want" }),
      },
      env,
    );
    expect(put.status).toBe(200);
    expect((await put.json() as Record<string, unknown>).status).toBe("want");

    const got = await app.request(
      "/api/watchlist/ABC-001",
      { headers: authHeaders(accessToken) },
      env,
    );
    expect(got.status).toBe(200);
    expect((await got.json() as Record<string, unknown>).video_code).toBe("ABC-001");

    const list = await app.request(
      "/api/watchlist?status=want",
      { headers: authHeaders(accessToken) },
      env,
    );
    expect(list.status).toBe(200);
    const listBody = (await list.json()) as { items: unknown[]; total: number };
    expect(listBody.total).toBe(1);

    const del = await app.request(
      "/api/watchlist/ABC-001",
      { method: "DELETE", headers: mutationHeaders(accessToken, csrfToken) },
      env,
    );
    expect(del.status).toBe(200);
    expect((await del.json() as Record<string, unknown>).deleted).toBe(true);
  });

  it("rejects an invalid status with 422", async () => {
    const { accessToken, csrfToken } = await login();
    const res = await app.request(
      "/api/watchlist/X-1",
      {
        method: "PUT",
        headers: mutationHeaders(accessToken, csrfToken),
        body: JSON.stringify({ href: "/v/x1", status: "nope" }),
      },
      env,
    );
    expect(res.status).toBe(422);
  });
});
```

- [x] **Step 2: Run to verify it fails**

Run: `npx vitest run server/__tests__/watchlist-routes.test.ts --config vitest.server.config.ts`
Expected: FAIL (route 404s / `watchlistRoutes` not mounted).

- [x] **Step 3: Write the route**

Create `server/routes/watchlist.ts` (mirrors `routes/preferences.ts`; the list route `/` is declared before the `/:videoCode` routes):

```typescript
import { Hono } from "hono";
import type { Env } from "../env";
import type { JwtPayload } from "../services/jwt";
import {
  upsertWatchIntent,
  getWatchIntent,
  listWatchIntents,
  deleteWatchIntent,
} from "../services/watchlist-service";

type WatchEnv = { Bindings: Env; Variables: { user: JwtPayload } };

export const watchlistRoutes = new Hono<WatchEnv>();

const VALID_STATUS = new Set(["want", "viewed"]);
const errJson = (code: string, message: string) => ({ error: { code, message } });

// GET / — list, optionally filtered by status.
watchlistRoutes.get("/", async (c) => {
  const status = c.req.query("status") ?? null;
  if (status && !VALID_STATUS.has(status)) {
    return c.json(errJson("watchlist.invalid_status", "status must be one of: want, viewed"), 422);
  }
  const raw = Number(c.req.query("limit") ?? 50);
  const limit = Number.isNaN(raw) ? 50 : Math.max(1, Math.min(200, raw));
  const offset = Math.max(0, Number(c.req.query("offset") ?? 0) || 0);

  const { items, total } = await listWatchIntents(c.env.HISTORY_DB, status, limit, offset);
  return c.json({ items, total });
});

// PUT /:videoCode — upsert a watch intent.
watchlistRoutes.put("/:videoCode", async (c) => {
  const videoCode = c.req.param("videoCode");
  let body: { href?: string; status?: string; notes?: string | null };
  try {
    body = await c.req.json();
  } catch {
    return c.json(errJson("watchlist.invalid_body", "Request body must be valid JSON"), 422);
  }
  if (typeof body.href !== "string" || body.href.length === 0) {
    return c.json(errJson("watchlist.invalid_href", "href is required"), 422);
  }
  if (!body.status || !VALID_STATUS.has(body.status)) {
    return c.json(errJson("watchlist.invalid_status", "status must be one of: want, viewed"), 422);
  }
  const row = await upsertWatchIntent(c.env.HISTORY_DB, videoCode, body.href, body.status, body.notes ?? null);
  return c.json(row);
});

// GET /:videoCode — one watch intent.
watchlistRoutes.get("/:videoCode", async (c) => {
  const row = await getWatchIntent(c.env.HISTORY_DB, c.req.param("videoCode"));
  if (row === null) {
    return c.json(errJson("watchlist.not_found", "Record not found"), 404);
  }
  return c.json(row);
});

// DELETE /:videoCode — un-track.
watchlistRoutes.delete("/:videoCode", async (c) => {
  const deleted = await deleteWatchIntent(c.env.HISTORY_DB, c.req.param("videoCode"));
  return c.json({ deleted });
});
```

- [x] **Step 4: Mount the route**

In `server/app.ts`: add the import in the route-imports block (near `import { preferencesRoutes } from "./routes/preferences";`):

```typescript
import { watchlistRoutes } from "./routes/watchlist";
```

Then add the mount immediately after the `app.route("/api/preferences", preferencesRoutes);` line (after the `app.use("/api/*", requireAuth());` gate, before `app.route("/api", stubRoutes);`):

```typescript
app.route("/api/watchlist", watchlistRoutes);
```

- [x] **Step 5: Run the test to verify it passes**

Run: `npx vitest run server/__tests__/watchlist-routes.test.ts --config vitest.server.config.ts`
Expected: PASS (2 passed)

- [x] **Step 6: Commit**

```bash
git add server/services/watchlist-service.ts server/routes/watchlist.ts server/__tests__/watchlist-routes.test.ts server/app.ts
git commit -m "feat(server): add /api/watchlist worker route (ADR-054 WS1)"
```

---

### Task 8: `watch_intent` capability probe (TS)

**Files:**
- Modify: `server/routes/capabilities.ts`

- [x] **Step 1: Add the probe**

In `server/routes/capabilities.ts`, after `libraryConsumptionEnabled()`, add (note: probes `HISTORY_DB`, unlike the three closed-loop probes which use `OPERATIONS_DB`):

```typescript
/** True when the ADR-054 WatchIntent table is queryable in HISTORY_DB (capability honesty). */
async function watchIntentEnabled(env: Env): Promise<boolean> {
  try {
    await env.HISTORY_DB.prepare("SELECT 1 FROM WatchIntent LIMIT 1").first();
    return true;
  } catch {
    return false;
  }
}
```

- [x] **Step 2: Wire it into the handler**

In the `capabilitiesRoutes.get("/", ...)` handler, after `const library_consumption = await libraryConsumptionEnabled(env);` add:

```typescript
  const watch_intent = await watchIntentEnabled(env);
```

Then add `watch_intent,` into the `features:` object, immediately after `library_consumption,`.

- [x] **Step 3: Verify type-check + existing capabilities test still passes**

Run: `npx tsc -p server/tsconfig.json --noEmit`
Then: `npx vitest run server/__tests__ --config vitest.server.config.ts -t capabilit`
Expected: type-check clean; capabilities test(s) pass (the `watch_intent` key is now present in the response).

- [x] **Step 4: Commit**

```bash
git add server/routes/capabilities.ts
git commit -m "feat(server): expose watch_intent capability flag (ADR-054 WS1)"
```

---

## Phase C — Cross-backend upsert parity guard

The Query Contract Golden pins only read query-builders; the WatchIntent UPSERT is unguarded. Pin both sides to one canonical (whitespace-normalized) SQL string so neither drifts silently.

### Task 9: parity tests in both repos

**Files:**
- Create: `tests/unit/test_watch_intent_upsert_parity.py` [MAIN]
- Create: `server/__tests__/watch-intent-upsert-parity.test.ts` [WEB]

- [x] **Step 1: Python parity test**

Create `tests/unit/test_watch_intent_upsert_parity.py`:

```python
"""Pin the WatchIntent UPSERT SQL so it cannot drift from the TS Worker (ADR-054)."""

import re

from javdb.storage.repos.watchlist_repo import WATCH_INTENT_UPSERT_SQL

# The single canonical UPSERT shape both backends must emit (whitespace-collapsed).
CANONICAL = (
    "INSERT INTO WatchIntent (video_code, href, status, notes, status_at, updated_at) "
    "VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), "
    "strftime('%Y-%m-%dT%H:%M:%fZ','now')) "
    "ON CONFLICT(video_code) DO UPDATE SET "
    "href = excluded.href, status = excluded.status, "
    "notes = COALESCE(excluded.notes, notes), "
    "status_at = strftime('%Y-%m-%dT%H:%M:%fZ','now'), "
    "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')"
)


def _norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def test_python_upsert_matches_canonical():
    assert _norm(WATCH_INTENT_UPSERT_SQL) == CANONICAL
```

Run: `python3 -m pytest tests/unit/test_watch_intent_upsert_parity.py -q`
Expected: PASS. (If it fails, the `CANONICAL` constant here is the source of truth — fix whichever SQL drifted, not the test, and keep the TS test below identical.)

- [x] **Step 2: TS parity test (identical CANONICAL string)**

Create `server/__tests__/watch-intent-upsert-parity.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { WATCH_INTENT_UPSERT_SQL } from "../services/watchlist-service";

// MUST be character-identical to tests/unit/test_watch_intent_upsert_parity.py CANONICAL.
const CANONICAL =
  "INSERT INTO WatchIntent (video_code, href, status, notes, status_at, updated_at) " +
  "VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), " +
  "strftime('%Y-%m-%dT%H:%M:%fZ','now')) " +
  "ON CONFLICT(video_code) DO UPDATE SET " +
  "href = excluded.href, status = excluded.status, " +
  "notes = COALESCE(excluded.notes, notes), " +
  "status_at = strftime('%Y-%m-%dT%H:%M:%fZ','now'), " +
  "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')";

const norm = (s: string) => s.replace(/\s+/g, " ").trim();

describe("WatchIntent upsert SQL parity", () => {
  it("TS upsert matches the canonical cross-backend shape", () => {
    expect(norm(WATCH_INTENT_UPSERT_SQL)).toBe(CANONICAL);
  });
});
```

Run: `npx vitest run server/__tests__/watch-intent-upsert-parity.test.ts --config vitest.server.config.ts`
Expected: PASS.

- [x] **Step 3: Commit (both repos)**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add tests/unit/test_watch_intent_upsert_parity.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "test(storage): pin WatchIntent upsert SQL parity (ADR-054 WS1)"
git add server/__tests__/watch-intent-upsert-parity.test.ts
git commit -m "test(server): pin WatchIntent upsert SQL parity (ADR-054 WS1)"
```

---

## Phase D — Frontend [WEB]

### Task 10: Regenerate api types

**Files:**
- Modify: `src/types/api.gen.ts`

- [ ] **Step 1: Regenerate from the local openapi.json produced in Task 5**

Run: `OPENAPI_PATH=/Users/tedwu/JAVDB_AutoSpider_CICD/docs/api/openapi.json node scripts/fetch-openapi.mjs`
Expected: regenerates `src/types/api.gen.ts`.

- [ ] **Step 2: Verify `Features.watch_intent` is now typed**

Run: `grep -n "watch_intent" src/types/api.gen.ts`
Expected: matches the new `watch_intent: boolean;` line under the `Features` schema.

- [ ] **Step 3: Commit**

```bash
git add src/types/api.gen.ts
git commit -m "chore(web): re-vendor api types for watch_intent (ADR-054 WS1)"
```

> Note: the api **client** in Task 11 is hand-typed (mirroring `src/api/preferences.ts`), so it does not depend on this step. This regeneration is only needed so `cap.data?.features?.watch_intent` type-checks.

---

### Task 11: api/watchlist.ts client

**Files:**
- Create: `src/api/watchlist.ts`

- [ ] **Step 1: Write the client**

Create `src/api/watchlist.ts` (hand-typed, mirroring `src/api/preferences.ts`: shared `http` wrapper, `encodeURIComponent` path segment, `skipErrorToast` + 404→null):

```typescript
import axios from 'axios'
import { http } from './client'

// Hand-typed until src/types/api.gen.ts is regenerated to include the ADR-054
// WS1 watchlist endpoints. Shapes mirror server/routes/watchlist.ts.

export type WatchStatus = 'want' | 'viewed'

export interface WatchIntent {
  video_code: string
  href: string
  status: WatchStatus
  notes: string | null
  status_at: string | null
  updated_at: string
}

export interface WatchIntentListResponse {
  items: WatchIntent[]
  total: number
}

export async function listWatchIntents(
  params: { status?: WatchStatus | null; limit?: number; offset?: number } = {},
): Promise<WatchIntentListResponse> {
  const { data } = await http.get<WatchIntentListResponse>('/api/watchlist', {
    params: {
      status: params.status ?? undefined,
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
    },
  })
  return data
}

export async function getWatchIntent(
  videoCode: string,
  opts: { skipErrorToast?: boolean } = {},
): Promise<WatchIntent | null> {
  try {
    const { data } = await http.get<WatchIntent>(
      `/api/watchlist/${encodeURIComponent(videoCode)}`,
      { skipErrorToast: opts.skipErrorToast },
    )
    return data
  } catch (err) {
    if (axios.isAxiosError(err) && err.response?.status === 404) return null
    throw err
  }
}

export async function upsertWatchIntent(
  videoCode: string,
  payload: { href: string; status: WatchStatus; notes?: string | null },
): Promise<WatchIntent> {
  const { data } = await http.put<WatchIntent>(
    `/api/watchlist/${encodeURIComponent(videoCode)}`,
    payload,
  )
  return data
}

export async function deleteWatchIntent(videoCode: string): Promise<void> {
  await http.delete(`/api/watchlist/${encodeURIComponent(videoCode)}`)
}
```

- [ ] **Step 2: Type-check**

Run: `npx vue-tsc --noEmit -p tsconfig.app.json`
Expected: no errors referencing `watchlist.ts`. (Commit with Task 12.)

---

### Task 12: StatusControl.vue

**Files:**
- Create: `src/components/StatusControl.vue`

- [ ] **Step 1: Write the component**

Create `src/components/StatusControl.vue` (models `HeartButton.vue`: local `ref` seeded from a prop, `watch` to reconcile external updates, performs the API write itself then emits; an `NSelect` `clearable` → clearing deletes the row, mirroring the `NRate` `clearable` idiom):

```vue
<script setup lang="ts">
  import { computed, ref, watch } from 'vue'
  import { NSelect, useMessage } from 'naive-ui'
  import { useI18n } from 'vue-i18n'
  import { upsertWatchIntent, deleteWatchIntent, type WatchStatus } from '@/api/watchlist'

  const props = defineProps<{
    videoCode: string
    href: string
    initialStatus?: WatchStatus | null
  }>()

  const emit = defineEmits<{
    (e: 'change', status: WatchStatus | null): void
  }>()

  const { t } = useI18n()
  const message = useMessage()
  const status = ref<WatchStatus | null>(props.initialStatus ?? null)
  const loading = ref(false)

  watch(
    () => props.initialStatus,
    (v) => {
      status.value = v ?? null
    },
  )

  const options = computed(() => [
    { label: t('library.watchlist.status.want'), value: 'want' },
    { label: t('library.watchlist.status.viewed'), value: 'viewed' },
  ])

  async function onUpdate(value: WatchStatus | null): Promise<void> {
    loading.value = true
    try {
      if (value === null) {
        await deleteWatchIntent(props.videoCode)
      } else {
        await upsertWatchIntent(props.videoCode, { href: props.href, status: value })
      }
      status.value = value
      emit('change', value)
    } catch {
      message.error(t('library.watchlist.saveError'))
    } finally {
      loading.value = false
    }
  }
</script>

<template>
  <NSelect
    :value="status"
    :options="options"
    :loading="loading"
    size="small"
    clearable
    style="width: 116px"
    :placeholder="t('library.watchlist.untracked')"
    @update:value="onUpdate"
  />
</template>
```

(Commit with Task 13.)

---

### Task 13: MoviesPage inline status column

**Files:**
- Modify: `src/pages/data/MoviesPage.vue`

- [ ] **Step 1: Add imports + state + bulk load**

In `src/pages/data/MoviesPage.vue` `<script setup>`:

1. Ensure `onMounted` is imported from `vue` (add it to the existing `import { ... } from 'vue'` line if absent).
2. Add component + api imports near the other `@/` imports:

```typescript
import StatusControl from '@/components/StatusControl.vue'
import { listWatchIntents, type WatchStatus } from '@/api/watchlist'
import { useCapabilitiesStore } from '@/stores/capabilities'
```

3. Add the capability store + a `video_code`-keyed status cache + a loader (mirrors the `actorHearted`/`ratings` Map idiom — reassign a new Map so the column re-renders):

```typescript
const cap = useCapabilitiesStore()
const watchIntents = ref<Map<string, WatchStatus>>(new Map())

async function loadWatchIntents(): Promise<void> {
  if (!cap.data?.features?.watch_intent) return
  try {
    const { items } = await listWatchIntents({ limit: 200 })
    const next = new Map<string, WatchStatus>()
    for (const it of items) next.set(it.video_code, it.status)
    watchIntents.value = next
  } catch {
    // non-fatal: the column simply shows "untracked" for every row
  }
}

onMounted(() => void loadWatchIntents())
```

- [ ] **Step 2: Add the gated column**

Inside the `columns = computed<DataTableColumns<MovieSearchItem>>(() => [ ... ])` array, add this entry after the rating column (gate it on the capability so deployments without the table never render a write control). Because the column is conditional, insert it via a spread:

```typescript
    ...(cap.data?.features?.watch_intent
      ? [
          {
            title: t('movies.col.watchStatus'),
            key: 'watch_status',
            width: 132,
            render: (row: MovieSearchItem) =>
              h(StatusControl, {
                videoCode: row.video_code,
                href: row.href,
                initialStatus: watchIntents.value.get(row.video_code) ?? null,
                onChange: (val: WatchStatus | null) => {
                  const next = new Map(watchIntents.value)
                  if (val === null) next.delete(row.video_code)
                  else next.set(row.video_code, val)
                  watchIntents.value = next
                },
              }),
          },
        ]
      : []),
```

- [ ] **Step 3: Type-check**

Run: `npx vue-tsc --noEmit -p tsconfig.app.json`
Expected: no errors. (`MovieSearchItem` already carries both `video_code` and `href`.)

- [ ] **Step 4: Commit**

```bash
git add src/api/watchlist.ts src/components/StatusControl.vue src/pages/data/MoviesPage.vue
git commit -m "feat(web): inline watch-status control on Movies page (ADR-054 WS1)"
```

---

### Task 14: Watchlist Library tab (WatchlistView + LibraryPage)

**Files:**
- Create: `src/pages/library/WatchlistView.vue`
- Modify: `src/pages/library/LibraryPage.vue`

- [ ] **Step 1: Write WatchlistView**

Create `src/pages/library/WatchlistView.vue` (mirrors `ConsumptionView.vue`: `NSpin` + error `NAlert` + KPI `NGrid` + filter `NSelect` + `NDataTable`; reuses `StatusControl` so the operator can re-status/un-track from the aggregate view):

```vue
<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from 'vue'
import {
  NAlert, NButton, NGrid, NGi, NCard, NStatistic, NDataTable, NSelect, NSpin,
  type DataTableColumns,
} from 'naive-ui'
import { useI18n } from 'vue-i18n'
import StatusControl from '@/components/StatusControl.vue'
import { listWatchIntents, type WatchIntent, type WatchStatus } from '@/api/watchlist'

const { t } = useI18n()

const items = ref<WatchIntent[]>([])
const total = ref(0)
const loading = ref(false)
const error = ref<string | null>(null)
const statusFilter = ref<WatchStatus | null>(null)

const statusOptions = computed(() => [
  { label: t('library.watchlist.status.want'), value: 'want' },
  { label: t('library.watchlist.status.viewed'), value: 'viewed' },
])

async function fetchList(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const res = await listWatchIntents({ status: statusFilter.value, limit: 200 })
    items.value = res.items
    total.value = res.total
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('library.watchlist.loadError')
  } finally {
    loading.value = false
  }
}

const columns = computed<DataTableColumns<WatchIntent>>(() => [
  {
    title: t('library.watchlist.col.videoCode'),
    key: 'video_code',
    render: (row) =>
      h(
        'span',
        { style: 'font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px;' },
        row.video_code,
      ),
  },
  {
    title: t('library.watchlist.col.status'),
    key: 'status',
    width: 140,
    render: (row) =>
      h(StatusControl, {
        videoCode: row.video_code,
        href: row.href,
        initialStatus: row.status,
        onChange: () => void fetchList(),
      }),
  },
  {
    title: t('library.watchlist.col.updatedAt'),
    key: 'updated_at',
    render: (row) => (row.updated_at ? row.updated_at.slice(0, 19).replace('T', ' ') : '—'),
  },
])

watch(statusFilter, () => void fetchList())
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
            :label="t('library.watchlist.total')"
            :value="total"
          />
        </NCard>
      </NGi>
    </NGrid>

    <NCard
      size="small"
      :title="t('library.watchlist.recent')"
      class="block"
    >
      <div class="filter-row">
        <NSelect
          v-model:value="statusFilter"
          :options="statusOptions"
          :placeholder="t('library.watchlist.allStatuses')"
          clearable
          class="filter-select"
        />
      </div>
      <NDataTable
        :columns="columns"
        :data="items"
        :bordered="false"
        size="small"
        :row-key="(row: WatchIntent) => row.video_code"
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
.filter-row {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}
.filter-select {
  width: 200px;
}
</style>
```

- [ ] **Step 2: Add the gated tab to LibraryPage**

In `src/pages/library/LibraryPage.vue`:

1. Import the view (after `import ConsumptionView from './ConsumptionView.vue'`):

```typescript
import WatchlistView from './WatchlistView.vue'
```

2. Add the gate computed (after `const showConsumption = ...`):

```typescript
const showWatchlist = computed(() => !!features.value?.watch_intent)
```

3. Add `'watchlist'` to `visibleTabs` (after the consumption push):

```typescript
  if (showWatchlist.value) tabs.push('watchlist')
```

4. Add the tab pane (after the consumption `<NTabPane>`):

```vue
      <NTabPane
        v-if="showWatchlist"
        name="watchlist"
        :tab="t('library.tabs.watchlist')"
      >
        <WatchlistView />
      </NTabPane>
```

- [ ] **Step 3: Type-check + build**

Run: `npx vue-tsc --noEmit -p tsconfig.app.json`
Expected: no errors.

(Commit with Task 15, together with the i18n keys those templates reference.)

---

### Task 15: i18n strings (en + zh parity)

**Files:**
- Modify: `src/i18n/locales/en.json`, `src/i18n/locales/zh-CN.json`

- [ ] **Step 1: en.json**

In `src/i18n/locales/en.json`:

1. Add a `watchlist` key to the `library.tabs` object:

```json
    "tabs": {
      "acquisition": "Acquisition",
      "ownership": "Ownership",
      "consumption": "Consumption",
      "watchlist": "Watchlist"
    },
```

2. Add a `watchlist` block as a sibling of `library.consumption` (inside `library`):

```json
    "watchlist": {
      "loadError": "Failed to load watchlist.",
      "saveError": "Failed to update status.",
      "total": "Tracked",
      "recent": "Watchlist",
      "untracked": "Untracked",
      "allStatuses": "All",
      "status": {
        "want": "Want",
        "viewed": "Viewed"
      },
      "col": {
        "videoCode": "Code",
        "status": "Status",
        "updatedAt": "Updated"
      }
    },
```

3. Add a `watchStatus` key to the `movies.col` object (the inline column header):

```json
        "watchStatus": "Status",
```

- [ ] **Step 2: zh-CN.json (same keys, translated values)**

In `src/i18n/locales/zh-CN.json`:

1. `library.tabs`:

```json
    "tabs": {
      "acquisition": "获取",
      "ownership": "拥有",
      "consumption": "消费",
      "watchlist": "想看清单"
    },
```

2. `library.watchlist` block:

```json
    "watchlist": {
      "loadError": "加载想看清单失败。",
      "saveError": "更新状态失败。",
      "total": "已追踪",
      "recent": "想看清单",
      "untracked": "未追踪",
      "allStatuses": "全部",
      "status": {
        "want": "想看",
        "viewed": "已看"
      },
      "col": {
        "videoCode": "番号",
        "status": "状态",
        "updatedAt": "更新时间"
      }
    },
```

3. `movies.col.watchStatus`:

```json
        "watchStatus": "观看状态",
```

- [ ] **Step 3: Verify both locales parse and have the same keys**

Run: `node -e "const en=require('./src/i18n/locales/en.json').library.watchlist; const zh=require('./src/i18n/locales/zh-CN.json').library.watchlist; const k=o=>Object.keys(o).sort().join(','); if(k(en)!==k(zh)||k(en.status)!==k(zh.status)||k(en.col)!==k(zh.col)) throw new Error('key drift'); console.log('i18n parity ok')"`
Expected: `i18n parity ok`

- [ ] **Step 4: Full frontend type-check + unit tests**

Run: `npx vue-tsc --noEmit -p tsconfig.app.json && npx vitest run --config vitest.config.ts`
Expected: type-check clean; frontend unit tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/pages/library/WatchlistView.vue src/pages/library/LibraryPage.vue src/i18n/locales/en.json src/i18n/locales/zh-CN.json
git commit -m "feat(web): add Library Watchlist tab + i18n (ADR-054 WS1)"
```

---

## Final verification gate

- [ ] **[MAIN] backend tests + lint**

Run: `cd /Users/tedwu/JAVDB_AutoSpider_CICD && python3 -m pytest tests/unit/test_watchlist_repo.py tests/unit/test_watchlist_router.py tests/unit/test_watch_intent_upsert_parity.py -q`
Expected: all pass.

- [ ] **[WEB] worker + frontend tests + type-check**

Run (from cwd):
```bash
npx vitest run server/__tests__/watchlist-routes.test.ts server/__tests__/watch-intent-upsert-parity.test.ts --config vitest.server.config.ts
npx vue-tsc --noEmit -p tsconfig.app.json
```
Expected: all pass; no type errors.

- [ ] **Manual smoke (optional, requires the table applied to a dev D1/SQLite + both servers running):** open the Movies page → the Status column appears (when `watch_intent` is true), set a movie to "Want", reload → it persists; open Library → Watchlist tab lists it; clear the select → it disappears (un-tracked).

- [ ] **Apply the migration to remote D1 (deploy step, run once when shipping):**

```bash
cd /Users/tedwu/JAVDB_AutoSpider_CICD
wrangler d1 execute javdb-history --remote --file=javdb/migrations/d1/2026_06_13_add_watch_intent.sql
python3 -m apps.cli.db.sync_d1_to_sqlite --apply --force-overwrite-all
```

---

## Coverage check (self-review)

- WatchIntent table (HISTORY_DB, `want|viewed`, `video_code` PK + `href` bridge, no `user_id`, no edit-lock) → Task 1 + Task 2.
- Dual-backend read+write `/api/watchlist` (PUT/GET/list/DELETE), byte-mirrored upsert → Tasks 2,3 (Python) + 6,7 (TS), parity-pinned in Task 9.
- `watch_intent` capability flag, both backends, probe `HISTORY_DB` → Task 4 (Python) + Task 8 (TS), surfaced to the SPA in Task 10.
- Inline `StatusControl` setter on Movies, gated → Tasks 11,12,13. Library Watchlist tab, gated → Task 14. en/zh parity → Task 15.
- `untracked` = absent row; clearing the select issues a DELETE → StatusControl `onUpdate(null)` (Task 12) + DELETE route (Tasks 3,7).
- Out of scope held: no `browsed`, no `ConsumptionSignal` derivation/write-back, no bulk ops, no edit-lock — none introduced.
