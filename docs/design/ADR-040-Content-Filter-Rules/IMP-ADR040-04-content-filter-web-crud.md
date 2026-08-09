# Content-Filter Web CRUD + SPA Overlay (ADR-040 / ADR-054 WS4a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the operator a web surface to manage the existing `ContentFilterRule` rows (the rules the ingestion engine already applies) without dropping to the CLI, and a read-side Browse/Movies overlay that dims movies a rule would exclude. Mutations are admin-gated; the whole surface is gated by a new `content_filter` capability flag.

**Architecture:** A dual-backend `/api/content-filter` CRUD API mirrored byte-for-byte between the Python FastAPI backend (`apps/api`) and the TypeScript Hono Worker (`server/`), per the ADR-017/034 dual-backend rule and the WS1 watchlist precedent (IMP-ADR054-01). **No new D1 table** — `ContentFilterRule` already exists in **`REPORTS_DB`** (`javdb-reports`), shipped by ADR-040 Phase 1. The Python router **delegates to the already-CRUD-complete `ContentFilterRepo`** (`load_rules`/`add_rule`/`list_rules`/`remove_rule`/`set_enabled` — **no new repo methods**); the TS Worker service re-implements the same SQL against **`env.REPORTS_DB`**. The legal `(dimension, mode)` allow-list stays canonical in the CLI (`apps/cli/ops/content_filter.py`), is imported by the Python router, hand-mirrored in the TS route, and pinned by a cross-backend golden test modelled on WS1's `watch-intent-upsert-parity`. A `content_filter` capability flag probes `SELECT 1 FROM ContentFilterRule LIMIT 1` against **REPORTS_DB** in both backends. The frontend adds a `SettingsFilterRulesPage.vue` CRUD table (gated by `content_filter`) and a **read-side** overlay on the Movies list that re-reads the same rules via the same `GET` and dims already-matched movies client-side (NOT a parallel filter, per ADR-054 D6).

**Tech Stack:** D1 (SQLite dialect), Python 3 / FastAPI / pydantic / pytest, TypeScript / Hono / `@cloudflare/workers-types` / Vitest (`cloudflare:test`), Vue 3 `<script setup>` / Naive UI / vue-i18n / Vitest.

**Cross-repo note.** Two git repos are involved:
- **[MAIN]** = `/Users/tedwu/JAVDB_AutoSpider_CICD` — Python router/schema/repo-delegation, capabilities, `openapi.json`, Python tests, ADR-040 Status Log. This IMP and ADR-040 live here. Run `git` with `git -C /Users/tedwu/JAVDB_AutoSpider_CICD ...`.
- **[WEB]** = the `javdb-autospider-web` working directory (current cwd) — TS Worker route/service, capabilities probe, Vue components, i18n, Worker tests, `src/types/api.gen.ts`. Run `git` from cwd.

Work the phases in order: **A (Python)** → **B (TS Worker)** → **C (allow-list parity guard)** → **D (frontend)** → **E (ADR-040 Status Log + roadmap)**. Each repo should be on its own feature branch.

> **⚠ The single most load-bearing fact in this IMP — read twice.**
> `ContentFilterRule` lives in **`REPORTS_DB` / `env.REPORTS_DB` (`javdb-reports`)**, NOT `HISTORY_DB` like WS1's `WatchIntent`. The WS1 probe and SQL targeted `HISTORY_DB`; copy-pasting the WS1 binding here would silently point the `content_filter` flag and every CRUD query at the wrong D1 database. **Every file you touch — Python probe, TS probe, TS service SQL, repo delegation — must use REPORTS_DB.** Per-DB binding map: closed-loop/ownership/consumption probes use `OPERATIONS_DB`; `watch_intent` uses `HISTORY_DB`; **`content_filter` uses `REPORTS_DB`.**

---

## Execution reconciliation notes (2026-06-15, applied during WS4a)

This IMP was authored against the **pre-IMP-03** allow-list (7 `(dimension, mode)` pairs).
Because [IMP-ADR040-03](IMP-ADR040-03-content-filter-regex-date.md) shipped first and grew the
canonical CLI tuples to **13 `VALID_RULE_MODES` / 12 `VALUE_REQUIRED`** pairs (adding the regex
and release_date modes), the following were reconciled during execution. None re-litigate a
locked decision; they keep the dual-backend surface coherent with the engine that already ships.

1. **Allow-list golden = 13/12, not 7/6.** The Python router imports the CLI tuples (now 13/12),
   so the parity golden (`test_content_filter_modes_parity.py`) and the TS `Set`s were set to the
   same 13/12 — updating the golden to track the source of truth (the CLI), per WS4-D3, never the
   reverse.
2. **Web-boundary value validation = `release_date` only (strict ISO), NOT regex.** JS `new RegExp`
   and Python `re` dialects diverge (inline flags like `(?i)…` are valid in Python but throw in JS),
   so a shared regex compile-check is impossible cross-backend and would wrongly reject common
   patterns on the Worker. Both backends validate `release_date` (strict `YYYY-MM-DD`); regex is
   left to the engine's fail-open (ADR-040 "fail-open everywhere"). The CLI keeps its Python-only
   regex check.
3. **Settings page dropdowns extended** to all 13 rule types (added `release_date` + `regex_*` +
   `before`/`after`) so the UI can author every rule the backend now accepts. The backend returns
   422 for an illegal `(dimension, mode)` pair, surfaced as a save-error toast.
4. **Overlay test lives at `tests/unit/content-filter-overlay.spec.ts`** (not the IMP's
   `src/pages/data/__tests__/*.test.ts`): the repo's default `vitest.config.ts` only includes
   `tests/unit/**/*.spec.ts`, so a co-located `src` test would never run. The matcher source stays
   co-located at `src/pages/data/content-filter-overlay.ts`.
5. **i18n is three-way (en/zh-CN/ja).** `tests/unit/i18n-parity.spec.ts` enforces parity across all
   three locales (matching the ADR-054 campaign i18n scope); `ja.json` was added alongside en/zh.
6. **api types + openapi were consumed from the MAIN worktree path**
   (`.claude/worktrees/ws4a-content-filter/docs/api/openapi.json`), not the main checkout.
7. **Router test uses a CSRF cookie/header fixture** (the app's CSRF middleware runs before DI),
   mirroring `test_watchlist_router.py` — the IMP's bare auth override alone returns 403 on mutations.

---

## Design decisions locked by ADR-054 D6/D7 + WS4-D2/D3/D6 (do not re-litigate)

- **No new D1 table, no migration.** `ContentFilterRule(id, dimension, mode, value, enabled, created_at)` already exists in `REPORTS_DB` (ADR-040 Phase 1, `javdb/migrations/d1/2026_05_29_add_content_filter_rule.sql`; mirrored in `_REPORTS_DDL`). This IMP is pure dual-backend plumbing + UI on top of an existing table.
- **Python router delegates to `ContentFilterRepo` — no new repo methods.** `load_rules`/`add_rule`/`list_rules`/`remove_rule`/`set_enabled` already exist (`javdb/storage/repos/content_filter_repo.py`). The router opens a `REPORTS_DB` connection via `get_db(_db.REPORTS_DB_PATH)` exactly as the CLI does (`apps/cli/ops/content_filter.py:117`).
- **TS Worker re-implements the same SQL against `env.REPORTS_DB`.** This is the ADR-018/030 "two implementations of the same SQL" tax. The CRUD SQL is small (INSERT/SELECT/DELETE/UPDATE) and is not byte-mirrored as a single constant the way the WS1 upsert was — instead the **allow-list** is the cross-backend invariant pinned by the parity golden.
- **Allow-list parity (WS4-D3).** `VALID_RULE_MODES` / `VALUE_REQUIRED` in `apps/cli/ops/content_filter.py:16-32` are canonical. The Python router imports them. The TS route hand-mirrors them. A cross-backend parity test (normalized-set golden, modelled on WS1 `watch-intent-upsert-parity`) pins both so neither drifts. **CLI tuples are the source of truth — fix whichever side drifted, never the test.**
- **Capability flag `content_filter` (WS4-D2)** probes `SELECT 1 FROM ContentFilterRule LIMIT 1` against **REPORTS_DB** in both backends. Table-existence is the right honesty bar: the CRUD page and the overlay both read/write that one table, so "table queryable" exactly predicts "these surfaces work".
- **Admin-gated mutations.** `POST` / `PUT` / `DELETE` require `role == admin` (TS `requireRole("admin")`, Python `Depends(require_role("admin"))`). `GET` requires auth only (so a readonly operator can still see and benefit from the overlay). Mirrors the `config` router precedent (`server/routes/config.ts:34`, `apps/api/infra/auth.py:330`).
- **SPA overlay = read-side reuse (ADR-054 D6).** The Movies list re-reads the same rules via the same `GET /api/content-filter` and dims rows a rule would exclude — it does **not** re-implement the filter engine or add a parallel query. It is a presentation hint only; it never mutates ingestion behavior.
- **Out of scope:** the regex/release-date engine modes (those are IMP-ADR040-03, [MAIN]-only, shippable independently); MCP surface; bulk import/export.

---

## File Structure

**[MAIN] create:**
- `apps/api/schemas/content_filter.py` — pydantic request/response models.
- `apps/api/routers/content_filter.py` — FastAPI router (`/api/content-filter`) delegating to `ContentFilterRepo`.
- `tests/unit/test_content_filter_router.py` — router smoke tests (REPORTS_DB monkeypatched).
- `tests/unit/test_content_filter_modes_parity.py` — allow-list parity golden (Python side).

**[MAIN] modify:**
- `apps/api/services/runtime.py` — register `content_filter_router`.
- `apps/api/routers/capabilities.py` — add `_content_filter_enabled()` probe (**REPORTS_DB**) + `content_filter` feature.
- `apps/api/schemas/capabilities_payloads.py` — add `content_filter: bool` to `Features`.
- `docs/api/openapi.json` — regenerated (mechanical).

**[WEB] create:**
- `server/services/content-filter-service.ts` — D1 query functions against `env.REPORTS_DB`.
- `server/routes/content-filter.ts` — Hono route (`/api/content-filter`), hand-mirrored allow-list.
- `server/__tests__/content-filter-routes.test.ts` — Worker route tests (self-seeds the table in `REPORTS_DB`).
- `server/__tests__/content-filter-modes-parity.test.ts` — allow-list parity golden (TS side).
- `src/api/content-filter.ts` — hand-typed api client.
- `src/pages/settings/SettingsFilterRulesPage.vue` — CRUD table (gated by `content_filter`, admin-gated mutations).
- `src/pages/data/content-filter-overlay.ts` — pure rule-matcher used by the read-side overlay (unit-testable).

**[WEB] modify:**
- `server/app.ts` — mount `contentFilterRoutes`.
- `server/routes/capabilities.ts` — add `contentFilterEnabled()` probe (**REPORTS_DB**) + `content_filter` feature.
- `src/router/routes.ts` — add the `filter-rules` child route under `/settings`.
- `src/components/settings/SettingsLayout.vue` — add the gated "Filter Rules" tab.
- `src/pages/data/MoviesPage.vue` — add the read-side dimming overlay (re-read rules + `rowProps` class).
- `src/i18n/locales/en.json` + `src/i18n/locales/zh-CN.json` — new strings (en/zh parity).
- `src/types/api.gen.ts` — regenerated (mechanical) so `Features.content_filter` is typed.

**[MAIN] modify (Phase E):**
- `docs/design/ADR-040-Content-Filter-Rules/ADR-040-*.md` + paired `.zh.md` — Status Log + roadmap (Phase-4 web CRUD done).

---

## Phase A — Python backend [MAIN]

### Task 1: Python schemas

**Files:**
- Create: `apps/api/schemas/content_filter.py`

- [x] **Step 1: Write the schemas**

Create `apps/api/schemas/content_filter.py` (the response mirrors the `Rule` dataclass shape that `ContentFilterRepo` returns — `id/dimension/mode/value/enabled`; the create payload leaves `(dimension, mode)` validation to the router so the error envelope can reference the canonical allow-list rather than a generic 422 enum):

```python
"""Pydantic schemas for content-filter CRUD endpoints (ADR-040 Phase 4 / WS4a)."""

from __future__ import annotations

from typing import List

from pydantic import BaseModel


class ContentFilterRuleCreate(BaseModel):
    dimension: str
    mode: str
    value: str = ""


class ContentFilterRuleEnabledUpdate(BaseModel):
    enabled: bool


class ContentFilterRuleResponse(BaseModel):
    id: int
    dimension: str
    mode: str
    value: str
    enabled: bool


class ContentFilterRuleListResponse(BaseModel):
    items: List[ContentFilterRuleResponse]
    total: int
```

(Commit together with Task 2.)

---

### Task 2: Python router (delegates to ContentFilterRepo) + registration

**Files:**
- Create: `apps/api/routers/content_filter.py`
- Modify: `apps/api/services/runtime.py`

- [x] **Step 1: Write the router**

Create `apps/api/routers/content_filter.py`. It imports the **canonical allow-list tuples** (`VALID_RULE_MODES`, `VALUE_REQUIRED`) from the CLI, opens a **`REPORTS_DB`** connection exactly as the CLI does (`get_db(_db.REPORTS_DB_PATH)`), and delegates every operation to the existing `ContentFilterRepo`. `GET` requires auth; mutations require admin (`Depends(require_role("admin"))`). The list route uses path `""` so the full path is exactly `/api/content-filter`:

```python
"""Content-filter CRUD API routes (ADR-040 Phase 4 / WS4a).

Delegates to the already-CRUD-complete ContentFilterRepo. ContentFilterRule
lives in REPORTS_DB (javdb-reports) — NOT HISTORY_DB. The legal (dimension,
mode) allow-list is imported from apps.cli.ops.content_filter (the single
source of truth), hand-mirrored in server/routes/content-filter.ts, and pinned
by tests/unit/test_content_filter_modes_parity.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from apps.api.infra.auth import _require_auth, require_role
from apps.api.schemas.content_filter import (
    ContentFilterRuleCreate,
    ContentFilterRuleEnabledUpdate,
    ContentFilterRuleListResponse,
    ContentFilterRuleResponse,
)
# Canonical allow-list (single source of truth, ADR-040 / WS4-D3).
from apps.cli.ops.content_filter import VALID_RULE_MODES, VALUE_REQUIRED
from javdb.spider.services.content_filter import Rule
from javdb.storage import db as _db
from javdb.storage.db import get_db
from javdb.storage.repos.content_filter_repo import ContentFilterRepo

router = APIRouter(prefix="/api/content-filter", tags=["content-filter"])

_NOT_FOUND = {
    "error": {"code": "content_filter.not_found", "message": "Rule not found"}
}


def _invalid_mode(dimension: str, mode: str) -> dict:
    return {
        "error": {
            "code": "content_filter.invalid_mode",
            "message": f"{dimension} rules do not support mode {mode!r}",
        }
    }


_VALUE_REQUIRED_ERR = {
    "error": {
        "code": "content_filter.value_required",
        "message": "this dimension/mode requires a non-empty value",
    }
}


def _rule_to_response(rule: Rule) -> ContentFilterRuleResponse:
    return ContentFilterRuleResponse(
        id=rule.id,
        dimension=rule.dimension,
        mode=rule.mode,
        value=rule.value,
        enabled=rule.enabled,
    )


@router.get("", response_model=ContentFilterRuleListResponse)
def list_rules(_user=Depends(_require_auth)):
    with get_db(_db.REPORTS_DB_PATH) as conn:
        rules = ContentFilterRepo(conn).list_rules()
    items = [_rule_to_response(r) for r in rules]
    return ContentFilterRuleListResponse(items=items, total=len(items))


@router.post("", response_model=ContentFilterRuleResponse, status_code=201)
def add_rule(body: ContentFilterRuleCreate, _admin=Depends(require_role("admin"))):
    rule_key = (body.dimension, body.mode)
    value = (body.value or "").strip()
    if rule_key not in VALID_RULE_MODES:
        raise HTTPException(status_code=422, detail=_invalid_mode(body.dimension, body.mode))
    if rule_key in VALUE_REQUIRED and not value:
        raise HTTPException(status_code=422, detail=_VALUE_REQUIRED_ERR)
    with get_db(_db.REPORTS_DB_PATH) as conn:
        repo = ContentFilterRepo(conn)
        rule_id = repo.add_rule(body.dimension, body.mode, value)
        created = next((r for r in repo.list_rules() if r.id == rule_id), None)
    if created is None:  # pragma: no cover - the row was just inserted
        raise HTTPException(status_code=500, detail=_NOT_FOUND)
    return _rule_to_response(created)


@router.put("/{rule_id}", response_model=ContentFilterRuleResponse)
def set_enabled(
    rule_id: int,
    body: ContentFilterRuleEnabledUpdate,
    _admin=Depends(require_role("admin")),
):
    with get_db(_db.REPORTS_DB_PATH) as conn:
        repo = ContentFilterRepo(conn)
        if not any(r.id == rule_id for r in repo.list_rules()):
            raise HTTPException(status_code=404, detail=_NOT_FOUND)
        repo.set_enabled(rule_id, body.enabled)
        updated = next(r for r in repo.list_rules() if r.id == rule_id)
    return _rule_to_response(updated)


@router.delete("/{rule_id}")
def remove_rule(rule_id: int, _admin=Depends(require_role("admin"))):
    with get_db(_db.REPORTS_DB_PATH) as conn:
        repo = ContentFilterRepo(conn)
        existed = any(r.id == rule_id for r in repo.list_rules())
        if existed:
            repo.remove_rule(rule_id)
    return {"deleted": existed}
```

> **Why `list_rules()` re-reads after a mutation:** `ContentFilterRepo` exposes no `get_rule(id)` and `add_rule` returns only the new id, so the router re-reads the (small, operator-authored) rule set to build the response. This keeps the repo untouched (no new methods) per the locked decision. `list_rules()` returns every rule (enabled and disabled), unlike `load_rules()` which returns only enabled rows.

- [x] **Step 2: Register the router**

In `apps/api/services/runtime.py`: add the import alongside the other `*_router` imports (next to `from apps.api.routers.watchlist import router as watchlist_router` on line 57):

```python
from apps.api.routers.content_filter import router as content_filter_router
```

Then add `content_filter_router,` into the `for router in (...)` tuple (after `watchlist_router,`).

- [x] **Step 3: Write the router smoke test**

Create `tests/unit/test_content_filter_router.py` (seeds the real `ContentFilterRule` DDL from the production migration into a temp file, monkeypatches `REPORTS_DB_PATH`, and overrides BOTH auth seams — `_require_auth` for GET and `require_role` is satisfied by giving the override user `role == admin`):

```python
"""Smoke tests for the content-filter router (ADR-040 Phase 4 / WS4a)."""

import pathlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

from javdb.storage import db as _db
from apps.api.infra.auth import _require_auth


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_CONTENT_FILTER_DDL = (
    _REPO_ROOT / "javdb/migrations/d1/2026_05_29_add_content_filter_rule.sql"
).read_text(encoding="utf-8")


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = str(tmp_path / "reports.db")  # NOTE: REPORTS_DB, not HISTORY_DB.
    conn = sqlite3.connect(path)
    conn.executescript(_CONTENT_FILTER_DDL)
    conn.commit()
    conn.close()
    monkeypatch.setattr(_db, "REPORTS_DB_PATH", path)
    from apps.api.services.runtime import app

    # require_role("admin") depends on _require_auth, so overriding the auth
    # seam with an admin user satisfies both the GET and the admin-gated routes.
    app.dependency_overrides[_require_auth] = lambda: {"username": "test", "role": "admin"}
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(_require_auth, None)


def test_add_list_toggle_delete(client):
    created = client.post(
        "/api/content-filter",
        json={"dimension": "tag", "mode": "exclude", "value": "censored"},
    )
    assert created.status_code == 201, created.text
    rule_id = created.json()["id"]
    assert created.json()["enabled"] is True

    listed = client.get("/api/content-filter")
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["dimension"] == "tag"

    disabled = client.put(f"/api/content-filter/{rule_id}", json={"enabled": False})
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False

    deleted = client.delete(f"/api/content-filter/{rule_id}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert client.get("/api/content-filter").json()["total"] == 0


def test_rejects_invalid_dimension_mode_pair(client):
    bad = client.post(
        "/api/content-filter",
        json={"dimension": "actor", "mode": "require_lead", "value": "x"},
    )
    assert bad.status_code == 422  # (actor, require_lead) not in VALID_RULE_MODES


def test_value_required_pair_rejects_empty(client):
    bad = client.post(
        "/api/content-filter",
        json={"dimension": "tag", "mode": "exclude", "value": "  "},
    )
    assert bad.status_code == 422  # (tag, exclude) requires a value


def test_toggle_missing_rule_404(client):
    assert client.put("/api/content-filter/999", json={"enabled": True}).status_code == 404
```

- [x] **Step 4: Run the tests**

Run: `python3 -m pytest tests/unit/test_content_filter_router.py -q`
Expected: PASS (4 passed). If `_require_auth` import path or `require_role` differs, re-check `apps/api/infra/auth.py:323-339`.

- [x] **Step 5: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add apps/api/schemas/content_filter.py apps/api/routers/content_filter.py apps/api/services/runtime.py tests/unit/test_content_filter_router.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "feat(api): add /api/content-filter CRUD router (ADR-040 WS4a)"
```

---

### Task 3: `content_filter` capability flag (Python)

**Files:**
- Modify: `apps/api/routers/capabilities.py`, `apps/api/schemas/capabilities_payloads.py`

- [x] **Step 1: Add the field to the `Features` schema**

In `apps/api/schemas/capabilities_payloads.py`, add `content_filter: bool` to `class Features(BaseModel)` immediately after `watch_intent: bool` (line 23):

```python
    watch_intent: bool
    content_filter: bool
    site_drift_sentinel: bool
```

- [x] **Step 2: Add the probe + wire it**

In `apps/api/routers/capabilities.py`, add this probe after `_watch_intent_enabled()` (line 83). **Critical: probes `REPORTS_DB`, where `ContentFilterRule` lives — NOT `HISTORY_DB` (where `WatchIntent` lives) and NOT `OPERATIONS_DB`):**

```python
def _content_filter_enabled() -> bool:
    """True when the ADR-040 ContentFilterRule table is queryable in REPORTS_DB
    (capability honesty). NOTE: REPORTS_DB, not HISTORY_DB — distinct from
    watch_intent above."""
    try:
        from javdb.storage.db import REPORTS_DB_PATH, get_db
        with get_db(REPORTS_DB_PATH) as conn:
            conn.execute("SELECT 1 FROM ContentFilterRule LIMIT 1").fetchone()
        return True
    except Exception:
        return False
```

Then in `build_capabilities()`, add `content_filter=_content_filter_enabled(),` to the `Features(...)` call, immediately after `watch_intent=_watch_intent_enabled(),` (line 122).

- [x] **Step 3: Verify capabilities builds and exposes the flag**

Run: `python3 -c "from apps.api.routers.capabilities import build_capabilities; print(build_capabilities().features.content_filter)"`
Expected: prints `True` or `False` depending on whether `reports.db` exists on this path — either proves the field exists and the probe degrades gracefully. (On a fresh checkout with no `reports/reports.db`, it prints `False`.)

- [x] **Step 4: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add apps/api/routers/capabilities.py apps/api/schemas/capabilities_payloads.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "feat(api): expose content_filter capability flag (ADR-040 WS4a)"
```

---

### Task 4: Regenerate the OpenAPI contract

**Files:**
- Modify: `docs/api/openapi.json`

- [x] **Step 1: Dump the OpenAPI schema**

Run: `cd /Users/tedwu/JAVDB_AutoSpider_CICD && python3 -m apps.cli.ops.dump_openapi`
Expected: `wrote /Users/tedwu/JAVDB_AutoSpider_CICD/docs/api/openapi.json (<N> bytes)`

- [x] **Step 2: Verify the new surface is in the contract**

Run: `python3 -c "import json; d=json.load(open('docs/api/openapi.json')); print('/api/content-filter' in d['paths']); print('content_filter' in d['components']['schemas']['Features']['properties'])"`
Expected: prints `True` then `True`

- [x] **Step 3: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add docs/api/openapi.json
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "chore(api): re-vendor openapi.json for content-filter (ADR-040 WS4a)"
```

---

## Phase B — TypeScript Worker [WEB]

### Task 5: content-filter-service.ts

**Files:**
- Create: `server/services/content-filter-service.ts`

- [x] **Step 1: Write the service**

Create `server/services/content-filter-service.ts` (mirrors `preference-service.ts`: `D1Database` is an ambient global — do not import it; functions take `db` not `env`). **Every call site passes `env.REPORTS_DB` — this module's SQL must run against the reports DB, mirroring the Python `ContentFilterRepo` which opens `REPORTS_DB_PATH`:**

```typescript
// Content-filter CRUD D1 queries (ADR-040 Phase 4 / WS4a).
// Keep this module free of Hono / c.env references; callers pass the binding.
// CRITICAL: callers MUST pass env.REPORTS_DB (javdb-reports) — ContentFilterRule
// lives in REPORTS_DB, NOT HISTORY_DB. This mirrors the Python ContentFilterRepo,
// which opens REPORTS_DB_PATH (apps/cli/ops/content_filter.py:117).

export interface ContentFilterRuleRow {
  id: number;
  dimension: string;
  mode: string;
  value: string | null;
  enabled: number;
}

export async function listRules(db: D1Database): Promise<ContentFilterRuleRow[]> {
  const rows = await db
    .prepare(
      "SELECT id, dimension, mode, value, enabled FROM ContentFilterRule ORDER BY id ASC",
    )
    .all<ContentFilterRuleRow>();
  return rows.results;
}

export async function getRule(
  db: D1Database,
  id: number,
): Promise<ContentFilterRuleRow | null> {
  return db
    .prepare("SELECT id, dimension, mode, value, enabled FROM ContentFilterRule WHERE id = ?")
    .bind(id)
    .first<ContentFilterRuleRow>();
}

export async function addRule(
  db: D1Database,
  dimension: string,
  mode: string,
  value: string,
): Promise<ContentFilterRuleRow> {
  const res = await db
    .prepare("INSERT INTO ContentFilterRule (dimension, mode, value, enabled) VALUES (?, ?, ?, 1)")
    .bind(dimension, mode, value)
    .run();
  const id = Number(res.meta?.last_row_id ?? 0);
  return (await getRule(db, id))!;
}

export async function setEnabled(
  db: D1Database,
  id: number,
  enabled: boolean,
): Promise<void> {
  await db
    .prepare("UPDATE ContentFilterRule SET enabled = ? WHERE id = ?")
    .bind(enabled ? 1 : 0, id)
    .run();
}

export async function removeRule(db: D1Database, id: number): Promise<boolean> {
  const res = await db.prepare("DELETE FROM ContentFilterRule WHERE id = ?").bind(id).run();
  return (res.meta?.changes ?? 0) > 0;
}
```

- [x] **Step 2: Verify it type-checks**

Run: `npx tsc -p server/tsconfig.json --noEmit`
Expected: no errors referencing `content-filter-service.ts`. (Commit together with Task 6.)

---

### Task 6: content-filter.ts route + mount (TDD)

**Files:**
- Create: `server/routes/content-filter.ts`, `server/__tests__/content-filter-routes.test.ts`
- Modify: `server/app.ts`

- [x] **Step 1: Write the failing route test**

Create `server/__tests__/content-filter-routes.test.ts` (self-seeds the table into **`env.REPORTS_DB`** like `preferences-routes.test.ts` self-seeds; mutations require both the CSRF `mutationHeaders` and an admin token — the seeded `admin` login user has `role == admin`):

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

// Seeds into REPORTS_DB — NOT HISTORY_DB. ContentFilterRule lives in javdb-reports.
async function seedContentFilterRule() {
  await env.REPORTS_DB.prepare(
    `CREATE TABLE IF NOT EXISTS ContentFilterRule (
      id         INTEGER PRIMARY KEY AUTOINCREMENT,
      dimension  TEXT NOT NULL,
      mode       TEXT NOT NULL,
      value      TEXT,
      enabled    INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    )`,
  ).run();
}

describe("Content-filter routes", () => {
  beforeAll(async () => {
    await seedContentFilterRule();
  });

  it("POST adds, GET lists, PUT toggles, DELETE removes", async () => {
    const { accessToken, csrfToken } = await login();

    const post = await app.request(
      "/api/content-filter",
      {
        method: "POST",
        headers: mutationHeaders(accessToken, csrfToken),
        body: JSON.stringify({ dimension: "tag", mode: "exclude", value: "censored" }),
      },
      env,
    );
    expect(post.status).toBe(201);
    const created = (await post.json()) as Record<string, unknown>;
    const ruleId = created.id as number;
    expect(created.enabled).toBe(true);

    const list = await app.request(
      "/api/content-filter",
      { headers: authHeaders(accessToken) },
      env,
    );
    expect(list.status).toBe(200);
    const listBody = (await list.json()) as { items: unknown[]; total: number };
    expect(listBody.total).toBe(1);

    const put = await app.request(
      `/api/content-filter/${ruleId}`,
      {
        method: "PUT",
        headers: mutationHeaders(accessToken, csrfToken),
        body: JSON.stringify({ enabled: false }),
      },
      env,
    );
    expect(put.status).toBe(200);
    expect((await put.json() as Record<string, unknown>).enabled).toBe(false);

    const del = await app.request(
      `/api/content-filter/${ruleId}`,
      { method: "DELETE", headers: mutationHeaders(accessToken, csrfToken) },
      env,
    );
    expect(del.status).toBe(200);
    expect((await del.json() as Record<string, unknown>).deleted).toBe(true);
  });

  it("rejects an invalid dimension/mode pair with 422", async () => {
    const { accessToken, csrfToken } = await login();
    const res = await app.request(
      "/api/content-filter",
      {
        method: "POST",
        headers: mutationHeaders(accessToken, csrfToken),
        body: JSON.stringify({ dimension: "actor", mode: "require_lead", value: "x" }),
      },
      env,
    );
    expect(res.status).toBe(422);
  });
});
```

> If the test runner's seeded login user is not `admin` (check `server/services/users.ts` / the test seed), give the mutation routes a non-admin path test that asserts 403 instead, and adjust which fixture user has admin. The WS1 watchlist test logged in as `admin` and exercised mutations successfully, so the same seed applies here.

- [x] **Step 2: Run to verify it fails**

Run: `npx vitest run server/__tests__/content-filter-routes.test.ts --config vitest.server.config.ts`
Expected: FAIL (route 404s / `contentFilterRoutes` not mounted).

- [x] **Step 3: Write the route**

Create `server/routes/content-filter.ts`. The allow-list is **hand-mirrored from `apps/cli/ops/content_filter.py:16-32`** and pinned by the parity test in Task 7. `GET` is auth-only; mutations use `requireRole("admin")`. All DB calls pass `c.env.REPORTS_DB`:

```typescript
import { Hono } from "hono";
import type { Env } from "../env";
import type { JwtPayload } from "../services/jwt";
import { requireRole } from "../middleware/auth";
import {
  listRules,
  getRule,
  addRule,
  setEnabled,
  removeRule,
} from "../services/content-filter-service";

type CfEnv = { Bindings: Env; Variables: { user: JwtPayload } };

export const contentFilterRoutes = new Hono<CfEnv>();

// Hand-mirrored from apps/cli/ops/content_filter.py:16-32 (the canonical source
// of truth). Pinned by server/__tests__/content-filter-modes-parity.test.ts —
// if these drift from the Python tuples, that test fails. Encode each pair as
// "dimension:mode" so the parity golden can compare normalized sets.
export const VALID_RULE_MODES = new Set([
  "actor:exclude",
  "tag:exclude",
  "tag:include",
  "gender:require_lead",
  "gender:exclude_all_male",
  "age:min_age",
  "age:max_age",
]);

export const VALUE_REQUIRED = new Set([
  "actor:exclude",
  "tag:exclude",
  "tag:include",
  "gender:require_lead",
  "age:min_age",
  "age:max_age",
]);

const errJson = (code: string, message: string) => ({ error: { code, message } });
const rowToRule = (r: { id: number; dimension: string; mode: string; value: string | null; enabled: number }) => ({
  id: r.id,
  dimension: r.dimension,
  mode: r.mode,
  value: r.value ?? "",
  enabled: r.enabled === 1,
});

// GET / — list all rules (auth only; the read-side overlay needs this).
contentFilterRoutes.get("/", async (c) => {
  const rows = await listRules(c.env.REPORTS_DB);
  const items = rows.map(rowToRule);
  return c.json({ items, total: items.length });
});

// POST / — add a rule (admin only).
contentFilterRoutes.post("/", requireRole("admin"), async (c) => {
  let body: { dimension?: string; mode?: string; value?: string };
  try {
    body = await c.req.json();
  } catch {
    return c.json(errJson("content_filter.invalid_body", "Request body must be valid JSON"), 422);
  }
  const dimension = body.dimension ?? "";
  const mode = body.mode ?? "";
  const value = (body.value ?? "").trim();
  const key = `${dimension}:${mode}`;
  if (!VALID_RULE_MODES.has(key)) {
    return c.json(errJson("content_filter.invalid_mode", `${dimension} rules do not support mode '${mode}'`), 422);
  }
  if (VALUE_REQUIRED.has(key) && value.length === 0) {
    return c.json(errJson("content_filter.value_required", "this dimension/mode requires a non-empty value"), 422);
  }
  const row = await addRule(c.env.REPORTS_DB, dimension, mode, value);
  return c.json(rowToRule(row), 201);
});

// PUT /:ruleId — toggle enabled (admin only).
contentFilterRoutes.put("/:ruleId", requireRole("admin"), async (c) => {
  const ruleId = Number(c.req.param("ruleId"));
  let body: { enabled?: boolean };
  try {
    body = await c.req.json();
  } catch {
    return c.json(errJson("content_filter.invalid_body", "Request body must be valid JSON"), 422);
  }
  if (typeof body.enabled !== "boolean") {
    return c.json(errJson("content_filter.invalid_enabled", "enabled must be a boolean"), 422);
  }
  const existing = await getRule(c.env.REPORTS_DB, ruleId);
  if (existing === null) {
    return c.json(errJson("content_filter.not_found", "Rule not found"), 404);
  }
  await setEnabled(c.env.REPORTS_DB, ruleId, body.enabled);
  return c.json(rowToRule((await getRule(c.env.REPORTS_DB, ruleId))!));
});

// DELETE /:ruleId — remove a rule (admin only).
contentFilterRoutes.delete("/:ruleId", requireRole("admin"), async (c) => {
  const deleted = await removeRule(c.env.REPORTS_DB, Number(c.req.param("ruleId")));
  return c.json({ deleted });
});
```

- [x] **Step 4: Mount the route**

In `server/app.ts`: add the import in the route-imports block (near `import { preferencesRoutes } from "./routes/preferences";` on line 23):

```typescript
import { contentFilterRoutes } from "./routes/content-filter";
```

Then add the mount immediately after the `app.route("/api/preferences", preferencesRoutes);` line (line 86, after the `app.use("/api/*", requireAuth());` gate on line 70, before `app.route("/api", stubRoutes);`):

```typescript
app.route("/api/content-filter", contentFilterRoutes);
```

- [x] **Step 5: Run the test to verify it passes**

Run: `npx vitest run server/__tests__/content-filter-routes.test.ts --config vitest.server.config.ts`
Expected: PASS (2 passed)

- [x] **Step 6: Commit**

```bash
git add server/services/content-filter-service.ts server/routes/content-filter.ts server/__tests__/content-filter-routes.test.ts server/app.ts
git commit -m "feat(server): add /api/content-filter worker route (ADR-040 WS4a)"
```

---

### Task 7: `content_filter` capability probe (TS)

**Files:**
- Modify: `server/routes/capabilities.ts`

- [x] **Step 1: Add the probe**

In `server/routes/capabilities.ts`, after `libraryConsumptionEnabled()` (line 41), add. **Critical: probes `env.REPORTS_DB` — NOT `OPERATIONS_DB` (the three closed-loop probes) and NOT `HISTORY_DB` (where `watch_intent` will probe in the WS1 merge):**

```typescript
/** True when the ADR-040 ContentFilterRule table is queryable in REPORTS_DB (capability honesty).
 *  NOTE: REPORTS_DB (javdb-reports), NOT OPERATIONS_DB or HISTORY_DB. */
async function contentFilterEnabled(env: Env): Promise<boolean> {
  try {
    await env.REPORTS_DB.prepare("SELECT 1 FROM ContentFilterRule LIMIT 1").first();
    return true;
  } catch {
    return false;
  }
}
```

- [x] **Step 2: Wire it into the handler**

In the `capabilitiesRoutes.get("/", ...)` handler, after `const library_consumption = await libraryConsumptionEnabled(env);` (line 47) add:

```typescript
  const content_filter = await contentFilterEnabled(env);
```

Then add `content_filter,` into the `features:` object, immediately after `library_consumption,` (line 67). If the WS1 `watch_intent` key is also present in this file at merge time, place `content_filter` after it for ordering parity with the Python `Features` schema.

- [x] **Step 3: Verify type-check + capabilities test still passes**

Run: `npx tsc -p server/tsconfig.json --noEmit`
Then: `npx vitest run server/__tests__ --config vitest.server.config.ts -t capabilit`
Expected: type-check clean; capabilities test(s) pass (the `content_filter` key is now present in the response).

- [x] **Step 4: Commit**

```bash
git add server/routes/capabilities.ts
git commit -m "feat(server): expose content_filter capability flag (ADR-040 WS4a)"
```

---

## Phase C — Cross-backend allow-list parity guard

The CRUD SQL differs in shape between backends (the repo uses Python DB-API, the Worker uses D1 `prepare/bind`), so unlike WS1 there is no single byte-mirrored SQL string. The cross-backend invariant here is the **`(dimension, mode)` allow-list**: the CLI tuples are canonical, the Python router imports them, the TS route hand-mirrors them. Pin both to one normalized golden so neither drifts silently — the exact WS1 parity-test pattern (`watch-intent-upsert-parity`).

### Task 8: parity tests in both repos

**Files:**
- Create: `tests/unit/test_content_filter_modes_parity.py` [MAIN]
- Create: `server/__tests__/content-filter-modes-parity.test.ts` [WEB]

- [x] **Step 1: Python parity test**

Create `tests/unit/test_content_filter_modes_parity.py`. It asserts the imported-by-the-router CLI tuples equal a frozen canonical set, AND that the router imports the same tuples object (so the router can never diverge from the CLI):

```python
"""Pin the content-filter (dimension, mode) allow-list so it cannot drift from
the TS Worker (ADR-040 WS4a / WS4-D3)."""

from apps.cli.ops.content_filter import VALID_RULE_MODES, VALUE_REQUIRED
from apps.api.routers import content_filter as cf_router

# The single canonical allow-list both backends must agree on, encoded as
# "dimension:mode" strings (the same wire shape the TS Set uses). This is the
# source of truth; if it fails, fix whichever side drifted — never the test.
CANONICAL_VALID = {
    "actor:exclude",
    "tag:exclude",
    "tag:include",
    "gender:require_lead",
    "gender:exclude_all_male",
    "age:min_age",
    "age:max_age",
}
CANONICAL_VALUE_REQUIRED = {
    "actor:exclude",
    "tag:exclude",
    "tag:include",
    "gender:require_lead",
    "age:min_age",
    "age:max_age",
}


def _encode(pairs) -> set:
    return {f"{dim}:{mode}" for (dim, mode) in pairs}


def test_cli_allow_list_matches_canonical():
    assert _encode(VALID_RULE_MODES) == CANONICAL_VALID
    assert _encode(VALUE_REQUIRED) == CANONICAL_VALUE_REQUIRED


def test_router_imports_the_canonical_cli_tuples():
    # The router must use the CLI tuples verbatim (not a copy) so it cannot drift.
    assert cf_router.VALID_RULE_MODES is VALID_RULE_MODES
    assert cf_router.VALUE_REQUIRED is VALUE_REQUIRED
```

Run: `python3 -m pytest tests/unit/test_content_filter_modes_parity.py -q`
Expected: PASS. (If `test_router_imports_the_canonical_cli_tuples` fails, the router redefined the tuples instead of importing them — fix the router import in Task 2, not the test.)

- [x] **Step 2: TS parity test (identical CANONICAL set)**

Create `server/__tests__/content-filter-modes-parity.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { VALID_RULE_MODES, VALUE_REQUIRED } from "../routes/content-filter";

// MUST be set-identical to tests/unit/test_content_filter_modes_parity.py CANONICAL_*.
const CANONICAL_VALID = [
  "actor:exclude",
  "tag:exclude",
  "tag:include",
  "gender:require_lead",
  "gender:exclude_all_male",
  "age:min_age",
  "age:max_age",
];
const CANONICAL_VALUE_REQUIRED = [
  "actor:exclude",
  "tag:exclude",
  "tag:include",
  "gender:require_lead",
  "age:min_age",
  "age:max_age",
];

const sorted = (s: Set<string>) => [...s].sort();

describe("Content-filter allow-list parity", () => {
  it("TS VALID_RULE_MODES matches the canonical cross-backend set", () => {
    expect(sorted(VALID_RULE_MODES)).toEqual([...CANONICAL_VALID].sort());
  });
  it("TS VALUE_REQUIRED matches the canonical cross-backend set", () => {
    expect(sorted(VALUE_REQUIRED)).toEqual([...CANONICAL_VALUE_REQUIRED].sort());
  });
});
```

Run: `npx vitest run server/__tests__/content-filter-modes-parity.test.ts --config vitest.server.config.ts`
Expected: PASS.

- [x] **Step 3: Commit (both repos)**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add tests/unit/test_content_filter_modes_parity.py
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "test(api): pin content-filter allow-list parity (ADR-040 WS4a)"
git add server/__tests__/content-filter-modes-parity.test.ts
git commit -m "test(server): pin content-filter allow-list parity (ADR-040 WS4a)"
```

---

## Phase D — Frontend [WEB]

### Task 9: Regenerate api types

**Files:**
- Modify: `src/types/api.gen.ts`

- [x] **Step 1: Regenerate from the local openapi.json produced in Task 4**

Run: `OPENAPI_PATH=/Users/tedwu/JAVDB_AutoSpider_CICD/docs/api/openapi.json node scripts/fetch-openapi.mjs`
Expected: regenerates `src/types/api.gen.ts`.

- [x] **Step 2: Verify `Features.content_filter` is now typed**

Run: `grep -n "content_filter" src/types/api.gen.ts`
Expected: matches the new `content_filter: boolean;` line under the `Features` schema.

- [x] **Step 3: Commit**

```bash
git add src/types/api.gen.ts
git commit -m "chore(web): re-vendor api types for content_filter (ADR-040 WS4a)"
```

> Note: the api **client** in Task 10 is hand-typed (mirroring `src/api/watchlist.ts`), so it does not depend on this step. This regeneration is only needed so `cap.data?.features?.content_filter` type-checks.

---

### Task 10: api/content-filter.ts client

**Files:**
- Create: `src/api/content-filter.ts`

- [x] **Step 1: Write the client**

Create `src/api/content-filter.ts` (hand-typed, mirroring `src/api/watchlist.ts`: shared `http` wrapper):

```typescript
import { http } from './client'

// Hand-typed until src/types/api.gen.ts is regenerated to include the ADR-040
// WS4a content-filter endpoints. Shapes mirror server/routes/content-filter.ts.

export interface ContentFilterRule {
  id: number
  dimension: string
  mode: string
  value: string
  enabled: boolean
}

export interface ContentFilterRuleListResponse {
  items: ContentFilterRule[]
  total: number
}

export async function listContentFilterRules(): Promise<ContentFilterRuleListResponse> {
  const { data } = await http.get<ContentFilterRuleListResponse>('/api/content-filter')
  return data
}

export async function addContentFilterRule(payload: {
  dimension: string
  mode: string
  value?: string
}): Promise<ContentFilterRule> {
  const { data } = await http.post<ContentFilterRule>('/api/content-filter', payload)
  return data
}

export async function setContentFilterRuleEnabled(
  id: number,
  enabled: boolean,
): Promise<ContentFilterRule> {
  const { data } = await http.put<ContentFilterRule>(`/api/content-filter/${id}`, { enabled })
  return data
}

export async function deleteContentFilterRule(id: number): Promise<void> {
  await http.delete(`/api/content-filter/${id}`)
}
```

- [x] **Step 2: Type-check**

Run: `npx vue-tsc --noEmit -p tsconfig.app.json`
Expected: no errors referencing `content-filter.ts`. (Commit with Task 13.)

---

### Task 11: content-filter-overlay.ts (pure matcher, TDD)

**Files:**
- Create: `src/pages/data/content-filter-overlay.ts`
- Test: `src/pages/data/__tests__/content-filter-overlay.test.ts`

> The read-side overlay must decide, client-side, whether a `MovieSearchItem` would be excluded by an enabled rule. To keep `MoviesPage.vue` thin and to unit-test the matching logic, extract a pure function. It deliberately covers only the dimensions the Movies grid carries data for (`actor`/`gender` via `actor_name`/`actor_gender`/`supporting_actors`); `tag`/`age`/regex/date dimensions are NOT matchable from the search row, so those rules never dim a row (fail-open — the overlay is a hint, the authoritative filter still runs at ingestion).

- [x] **Step 1: Write the failing test**

Create `src/pages/data/__tests__/content-filter-overlay.test.ts`:

```typescript
import { describe, it, expect } from 'vitest'
import { isDimmedByRules } from '../content-filter-overlay'
import type { ContentFilterRule } from '@/api/content-filter'

const rule = (over: Partial<ContentFilterRule>): ContentFilterRule => ({
  id: 1, dimension: 'actor', mode: 'exclude', value: '', enabled: true, ...over,
})

const row = (over: Partial<{ actor_name: string | null; actor_gender: string | null; supporting_actors: string | null }>) => ({
  actor_name: null, actor_gender: null, supporting_actors: null, ...over,
})

describe('isDimmedByRules', () => {
  it('dims when an enabled actor-exclude rule matches the lead actor', () => {
    expect(isDimmedByRules(row({ actor_name: 'Jane Doe' }), [rule({ dimension: 'actor', mode: 'exclude', value: 'Jane Doe' })])).toBe(true)
  })

  it('matches case-insensitively (mirrors the engine casefold)', () => {
    expect(isDimmedByRules(row({ actor_name: 'Jane Doe' }), [rule({ value: 'jane doe' })])).toBe(true)
  })

  it('ignores disabled rules', () => {
    expect(isDimmedByRules(row({ actor_name: 'Jane Doe' }), [rule({ value: 'Jane Doe', enabled: false })])).toBe(false)
  })

  it('does not dim for dimensions the row carries no data for (tag/age — fail-open)', () => {
    expect(isDimmedByRules(row({ actor_name: 'Jane Doe' }), [rule({ dimension: 'tag', mode: 'exclude', value: 'x' })])).toBe(false)
  })

  it('dims on gender exclude_all_male when no female actor is present', () => {
    expect(isDimmedByRules(row({ actor_gender: 'male' }), [rule({ dimension: 'gender', mode: 'exclude_all_male', value: '' })])).toBe(true)
  })

  it('returns false when there are no rules', () => {
    expect(isDimmedByRules(row({ actor_name: 'Jane Doe' }), [])).toBe(false)
  })
})
```

- [x] **Step 2: Run to verify it fails**

Run: `npx vitest run src/pages/data/__tests__/content-filter-overlay.test.ts`
Expected: FAIL (`isDimmedByRules` not found).

- [x] **Step 3: Implement the matcher**

Create `src/pages/data/content-filter-overlay.ts` (mirrors the engine's `_matches_link` casefold-equality semantics; deliberately scoped to the row's available fields):

```typescript
import type { ContentFilterRule } from '@/api/content-filter'

export interface OverlayMovieRow {
  actor_name?: string | null
  actor_gender?: string | null
  supporting_actors?: string | null
}

const cf = (s: string | null | undefined): string => (s ?? '').toLowerCase().trim()

/**
 * Read-side hint: would an enabled rule exclude this row? This is a presentation
 * dimmer only — the authoritative filter runs at ingestion (javdb/spider/services/
 * content_filter.py). It covers only the dimensions the Movies search row carries
 * data for; rules over tag/age/regex/date never dim a row (fail-open).
 */
export function isDimmedByRules(row: OverlayMovieRow, rules: ContentFilterRule[]): boolean {
  for (const r of rules) {
    if (!r.enabled) continue
    if (r.dimension === 'actor' && r.mode === 'exclude') {
      const target = cf(r.value)
      if (target && (cf(row.actor_name) === target || cf(row.supporting_actors) === target)) return true
    } else if (r.dimension === 'gender' && r.mode === 'exclude_all_male') {
      // No female actor known on this row → would be dropped by the engine.
      if (cf(row.actor_gender) === 'male') return true
    }
    // Other dimensions/modes are not matchable from the search row → no dim.
  }
  return false
}
```

- [x] **Step 4: Run to verify it passes**

Run: `npx vitest run src/pages/data/__tests__/content-filter-overlay.test.ts`
Expected: PASS (6 passed). (Commit with Task 13.)

---

### Task 12: SettingsFilterRulesPage.vue + router + settings tab

**Files:**
- Create: `src/pages/settings/SettingsFilterRulesPage.vue`
- Modify: `src/router/routes.ts`, `src/components/settings/SettingsLayout.vue`

- [x] **Step 1: Write the CRUD page**

Create `src/pages/settings/SettingsFilterRulesPage.vue` (mirrors the settings-page idiom: `NSpin` + error `NAlert` + `NDataTable`; gated by `content_filter`; mutation controls disabled for non-admins via the auth store). The "add rule" form uses `NSelect` for dimension/mode + `NInput` for value:

```vue
<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import {
  NAlert, NButton, NCard, NDataTable, NInput, NSelect, NSpace, NSpin, NSwitch,
  useMessage, type DataTableColumns,
} from 'naive-ui'
import { useI18n } from 'vue-i18n'
import { useCapabilitiesStore } from '@/stores/capabilities'
import { useAuthStore } from '@/stores/auth'
import {
  listContentFilterRules, addContentFilterRule, setContentFilterRuleEnabled,
  deleteContentFilterRule, type ContentFilterRule,
} from '@/api/content-filter'

const { t } = useI18n()
const message = useMessage()
const cap = useCapabilitiesStore()
const auth = useAuthStore()

const enabledFeature = computed(() => cap.data?.features?.content_filter === true)
const isAdmin = computed(() => auth.role === 'admin')

const rules = ref<ContentFilterRule[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

// Add-form state. The allow-list mirrors the canonical CLI tuples.
const draftDimension = ref<string>('tag')
const draftMode = ref<string>('exclude')
const draftValue = ref<string>('')
const saving = ref(false)

const dimensionOptions = [
  { label: 'actor', value: 'actor' },
  { label: 'tag', value: 'tag' },
  { label: 'gender', value: 'gender' },
  { label: 'age', value: 'age' },
]
const modeOptions = [
  { label: 'exclude', value: 'exclude' },
  { label: 'include', value: 'include' },
  { label: 'require_lead', value: 'require_lead' },
  { label: 'exclude_all_male', value: 'exclude_all_male' },
  { label: 'min_age', value: 'min_age' },
  { label: 'max_age', value: 'max_age' },
]

async function fetchRules(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const res = await listContentFilterRules()
    rules.value = res.items
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('settings.filterRules.loadError')
  } finally {
    loading.value = false
  }
}

async function onAdd(): Promise<void> {
  saving.value = true
  try {
    await addContentFilterRule({
      dimension: draftDimension.value,
      mode: draftMode.value,
      value: draftValue.value.trim(),
    })
    draftValue.value = ''
    await fetchRules()
    message.success(t('settings.filterRules.added'))
  } catch {
    message.error(t('settings.filterRules.saveError'))
  } finally {
    saving.value = false
  }
}

async function onToggle(row: ContentFilterRule, enabled: boolean): Promise<void> {
  try {
    await setContentFilterRuleEnabled(row.id, enabled)
    await fetchRules()
  } catch {
    message.error(t('settings.filterRules.saveError'))
  }
}

async function onDelete(row: ContentFilterRule): Promise<void> {
  try {
    await deleteContentFilterRule(row.id)
    await fetchRules()
  } catch {
    message.error(t('settings.filterRules.saveError'))
  }
}

const columns = computed<DataTableColumns<ContentFilterRule>>(() => [
  { title: t('settings.filterRules.col.id'), key: 'id', width: 64 },
  { title: t('settings.filterRules.col.dimension'), key: 'dimension', width: 100 },
  { title: t('settings.filterRules.col.mode'), key: 'mode', width: 140 },
  { title: t('settings.filterRules.col.value'), key: 'value' },
  {
    title: t('settings.filterRules.col.enabled'),
    key: 'enabled',
    width: 90,
    render: (row) =>
      h(NSwitch, {
        value: row.enabled,
        disabled: !isAdmin.value,
        'onUpdate:value': (v: boolean) => void onToggle(row, v),
      }),
  },
  {
    title: t('settings.filterRules.col.actions'),
    key: 'actions',
    width: 100,
    render: (row) =>
      h(
        NButton,
        { size: 'small', type: 'error', tertiary: true, disabled: !isAdmin.value, onClick: () => void onDelete(row) },
        { default: () => t('common.delete') },
      ),
  },
])

// Load when the capability resolves — `cap.data` is filled asynchronously, so a
// one-shot onMounted check can miss it and leave the page blank until a manual
// refresh. Mirrors the WS1 MoviesPage watcher fix. (`watch` is imported from
// `vue` alongside `computed`/`ref`/`h`/`onMounted`.)
watch(
  enabledFeature,
  (enabled) => {
    if (enabled) void fetchRules()
  },
  { immediate: true },
)
</script>

<template>
  <div class="filter-rules-page">
    <NAlert
      v-if="!enabledFeature"
      type="info"
      :show-icon="true"
    >
      {{ t('settings.filterRules.disabled') }}
    </NAlert>

    <template v-else>
      <NCard
        :title="t('settings.filterRules.addTitle')"
        size="small"
      >
        <NSpace v-if="isAdmin" align="center">
          <NSelect v-model:value="draftDimension" :options="dimensionOptions" style="width: 120px" />
          <NSelect v-model:value="draftMode" :options="modeOptions" style="width: 160px" />
          <NInput v-model:value="draftValue" :placeholder="t('settings.filterRules.valuePlaceholder')" style="width: 240px" />
          <NButton type="primary" :loading="saving" @click="onAdd">
            {{ t('settings.filterRules.add') }}
          </NButton>
        </NSpace>
        <NAlert v-else type="warning" :show-icon="true">
          {{ t('settings.filterRules.adminOnly') }}
        </NAlert>
      </NCard>

      <NAlert
        v-if="error"
        type="error"
        :show-icon="true"
        closable
        @close="error = null"
      >
        {{ error }}
      </NAlert>

      <NSpin :show="loading">
        <NDataTable
          :columns="columns"
          :data="rules"
          :row-key="(row: ContentFilterRule) => row.id"
          size="small"
        />
      </NSpin>
    </template>
  </div>
</template>

<style scoped>
.filter-rules-page { display: flex; flex-direction: column; gap: 12px; }
</style>
```

> Confirm the auth store exposes `role` (it is referenced by `src/router/routes.ts` via `Role` from `@/stores/auth`). If the accessor differs (e.g. `auth.user?.role`), adjust `isAdmin`. Mutation controls are disabled (not hidden) for non-admins so a readonly operator still sees the rule set the overlay uses.

- [x] **Step 2: Add the child route**

In `src/router/routes.ts`, inside the `/settings` route's `children` array, add after the `appearance` child (line 104):

```typescript
      {
        path: 'filter-rules',
        name: 'settings-filter-rules',
        component: () => import('@/pages/settings/SettingsFilterRulesPage.vue'),
      },
```

- [x] **Step 3: Add the gated settings tab**

In `src/components/settings/SettingsLayout.vue`:

1. Import the capabilities store in `<script setup>`:

```typescript
import { useCapabilitiesStore } from '@/stores/capabilities'
const cap = useCapabilitiesStore()
```

2. Make the `TABS` array conditional on the capability (replace the `const TABS = [...] as const` with a `computed`):

```typescript
const TABS = computed(() => [
  { key: 'config', path: '/settings/config' },
  { key: 'auth', path: '/settings/auth' },
  { key: 'capabilities', path: '/settings/capabilities' },
  { key: 'appearance', path: '/settings/appearance' },
  ...(cap.data?.features?.content_filter
    ? [{ key: 'filter-rules', path: '/settings/filter-rules' }]
    : []),
])
```

Update `active`/`onTab` to read `TABS.value` instead of `TABS`, and add an `NTabPane` for the new tab in the template, gated with `v-if`:

```vue
        <NTabPane
          v-if="cap.data?.features?.content_filter"
          name="filter-rules"
          :tab="t('nav.filterRules')"
        />
```

> `import { computed } from 'vue'` is already present in this file. The `TABS[number]['key']` type alias becomes `(typeof TABS.value)[number]['key']` — or just type `active` as `computed<string>`.

- [x] **Step 4: Type-check**

Run: `npx vue-tsc --noEmit -p tsconfig.app.json`
Expected: no errors. (Commit with Task 13.)

---

### Task 13: Read-side Browse/Movies overlay

**Files:**
- Modify: `src/pages/data/MoviesPage.vue`

- [x] **Step 1: Add imports + state + rule load**

In `src/pages/data/MoviesPage.vue` `<script setup>`:

1. `ref`, `watch`, and `onMounted` are already imported from `vue` (line 2) — confirm they are present (the real `MoviesPage.vue` imports `computed, h, onMounted, onUnmounted, ref, watch`). Add the overlay imports near the other `@/` imports:

```typescript
import { listContentFilterRules, type ContentFilterRule } from '@/api/content-filter'
import { isDimmedByRules } from './content-filter-overlay'
import { useCapabilitiesStore } from '@/stores/capabilities'
```

2. Add the capability store + a reactive rule list + a loader (mirrors the existing `ratings`/`actorHearted` reactive-Map idiom; only loads when the capability is on):

```typescript
const cap = useCapabilitiesStore()
const filterRules = ref<ContentFilterRule[]>([])

async function loadFilterRules(): Promise<void> {
  if (!cap.data?.features?.content_filter) return
  try {
    const { items } = await listContentFilterRules()
    filterRules.value = items.filter((r) => r.enabled)
  } catch {
    // non-fatal: the overlay simply dims nothing
  }
}

// Load when the capability resolves (it can arrive after mount); clear when off.
// Mirrors the WS1 MoviesPage watcher fix — a one-shot onMounted call can miss a
// late-resolving `cap.data` and leave the overlay inert until a manual refresh.
watch(
  () => cap.data?.features?.content_filter,
  (enabled) => {
    if (enabled) void loadFilterRules()
    else filterRules.value = []
  },
  { immediate: true },
)
```

- [x] **Step 2: Dim matched rows via `rowProps`**

Extend the existing `rowProps` function (line 117) to add a dimmed style when an enabled rule matches the row. Keep the existing batch-focus style and append:

```typescript
function rowProps(row: MovieSearchItem, index: number) {
  const dimmed =
    cap.data?.features?.content_filter && isDimmedByRules(row, filterRules.value)
  const base =
    batchMode.value && index === focusedIndex.value
      ? 'background: rgba(24,160,88,0.12);'
      : ''
  return {
    style: dimmed ? `${base} opacity: 0.45; text-decoration: line-through;` : base,
    title: dimmed ? t('movies.filteredHint') : undefined,
  }
}
```

> The first param was `_row` (unused); rename to `row` since it is now read. Verify the `<NDataTable>` binds `:row-props="rowProps"` (it does — the function already exists for batch focus). The overlay is purely visual; it never removes rows or changes the query.

- [x] **Step 3: Type-check**

Run: `npx vue-tsc --noEmit -p tsconfig.app.json`
Expected: no errors. (`MovieSearchItem` carries `actor_name`, `actor_gender`, `supporting_actors` — the fields `isDimmedByRules` reads.)

- [x] **Step 4: Commit**

```bash
git add src/api/content-filter.ts src/pages/data/content-filter-overlay.ts src/pages/data/__tests__/content-filter-overlay.test.ts src/pages/settings/SettingsFilterRulesPage.vue src/router/routes.ts src/components/settings/SettingsLayout.vue src/pages/data/MoviesPage.vue
git commit -m "feat(web): content-filter CRUD page + read-side Movies overlay (ADR-040 WS4a)"
```

---

### Task 14: i18n strings (en/zh parity)

**Files:**
- Modify: `src/i18n/locales/en.json`, `src/i18n/locales/zh-CN.json`

- [x] **Step 1: Add the English strings**

In `src/i18n/locales/en.json`:

1. Add to the `nav` object: `"filterRules": "Filter Rules"`.
2. Add to the `movies` object: `"filteredHint": "Hidden by a content filter rule"`.
3. Add a `settings.filterRules` block:

```json
    "filterRules": {
      "disabled": "Content filtering is not available on this deployment (the ContentFilterRule table is not present in the reports database).",
      "addTitle": "Add a filter rule",
      "add": "Add",
      "added": "Filter rule added.",
      "adminOnly": "Only administrators can add or edit filter rules.",
      "valuePlaceholder": "Value (actor name, tag, gender, or age)",
      "loadError": "Failed to load filter rules.",
      "saveError": "Failed to save the filter rule.",
      "col": {
        "id": "ID",
        "dimension": "Dimension",
        "mode": "Mode",
        "value": "Value",
        "enabled": "Enabled",
        "actions": "Actions"
      }
    }
```

4. Ensure `common.delete` exists (it is used by the page). If absent, add `"delete": "Delete"` to `common`.

- [x] **Step 2: Add the matching Chinese strings**

In `src/i18n/locales/zh-CN.json`, add the **same key paths** with translated values:

1. `nav.filterRules`: `"过滤规则"`.
2. `movies.filteredHint`: `"已被内容过滤规则隐藏"`.
3. `settings.filterRules`:

```json
    "filterRules": {
      "disabled": "当前部署不支持内容过滤（reports 数据库中不存在 ContentFilterRule 表）。",
      "addTitle": "添加过滤规则",
      "add": "添加",
      "added": "过滤规则已添加。",
      "adminOnly": "只有管理员可以添加或编辑过滤规则。",
      "valuePlaceholder": "值（演员名、标签、性别或年龄）",
      "loadError": "加载过滤规则失败。",
      "saveError": "保存过滤规则失败。",
      "col": {
        "id": "ID",
        "dimension": "维度",
        "mode": "模式",
        "value": "值",
        "enabled": "启用",
        "actions": "操作"
      }
    }
```

4. Ensure `common.delete` exists in zh (`"删除"`); add if absent.

> Code-block values like `actor`/`tag`/`gender`/`age` in the `NSelect` option labels are NOT translated (they are the literal allow-list tokens the backend expects) — they are hard-coded in the component, not i18n keys, so no translation drift. Only the surrounding UI chrome is translated.

- [x] **Step 3: Verify en/zh key parity**

Run:
```bash
node -e "
const e=require('./src/i18n/locales/en.json'), z=require('./src/i18n/locales/zh-CN.json');
const flat=(o,p='')=>Object.entries(o).flatMap(([k,v])=>typeof v==='object'&&v?flat(v,p+k+'.'):[p+k]);
const ek=new Set(flat(e)), zk=new Set(flat(z));
const missZ=[...ek].filter(k=>!zk.has(k)).filter(k=>k.includes('filterRules')||k==='nav.filterRules'||k==='movies.filteredHint');
const missE=[...zk].filter(k=>!ek.has(k)).filter(k=>k.includes('filterRules')||k==='nav.filterRules'||k==='movies.filteredHint');
console.log('missing in zh:', missZ); console.log('missing in en:', missE);
"
```
Expected: both arrays empty.

- [x] **Step 4: Final frontend type-check + targeted tests**

Run:
```bash
npx vue-tsc --noEmit -p tsconfig.app.json
npx vitest run src/pages/data/__tests__/content-filter-overlay.test.ts
```
Expected: type-check clean; overlay tests pass.

- [x] **Step 5: Commit**

```bash
git add src/i18n/locales/en.json src/i18n/locales/zh-CN.json
git commit -m "i18n(web): content-filter rules strings en/zh (ADR-040 WS4a)"
```

---

## Phase E — ADR-040 Status Log + roadmap [MAIN]

### Task 15: Update ADR-040 (bilingual)

**Files:**
- Modify: `docs/design/ADR-040-Content-Filter-Rules/ADR-040-*.md` and the paired `ADR-040-*.zh.md`

- [x] **Step 1: Locate the ADR files**

Run: `ls docs/design/ADR-040-Content-Filter-Rules/`
Identify the `ADR-040-<topic>.md` and `ADR-040-<topic>.zh.md` pair (the decision record), and confirm the roadmap/Status-Log section names.

- [x] **Step 2: Append the Status Log entry (both languages)**

Add a Status Log row/entry recording that **Phase 4 (web CRUD) shipped** — dual-backend `/api/content-filter` over the existing `ContentFilterRule` table in REPORTS_DB, a `content_filter` capability flag, a Settings CRUD page, and a read-side Movies overlay; no schema migration (reused the Phase-1 table). Reference this IMP (`IMP-ADR040-04-content-filter-web-crud.md`) by filename only (same-folder link convention). Mark the roadmap's "Phase 4 — Web/MCP" item's web-CRUD portion as **done** (MCP remains future). Update both the `.md` and the `.zh.md` in the same commit (translation drift is a defect).

> Per the WS4a stale-comment finding, also confirm the engine-side IMP (IMP-ADR040-03) backfilled the stale `age`/`min_age`/`max_age` (and regex/date) comments in `javdb/migrations/d1/2026_05_29_add_content_filter_rule.sql:7-10` and `javdb/storage/db/_db_migrations.py:236-238`. If IMP-ADR040-03 has not landed yet, do NOT touch those comments here — they belong to the engine IMP; just note the dependency in the Status Log.

- [x] **Step 3: Commit**

```bash
git -C /Users/tedwu/JAVDB_AutoSpider_CICD add docs/design/ADR-040-Content-Filter-Rules/
git -C /Users/tedwu/JAVDB_AutoSpider_CICD commit -m "docs(adr-040): record Phase-4 web CRUD shipped (WS4a)"
```

---

## Final Verification Gate

Run all of the following and confirm green before declaring done.

- [x] **[MAIN] Python tests**

```bash
cd /Users/tedwu/JAVDB_AutoSpider_CICD
python3 -m pytest tests/unit/test_content_filter_router.py tests/unit/test_content_filter_modes_parity.py -q
```
Expected: all pass.

- [x] **[MAIN] capability + contract spot-check**

```bash
python3 -c "from apps.api.routers.capabilities import build_capabilities as b; print('content_filter' in b().features.model_dump())"
python3 -c "import json; d=json.load(open('docs/api/openapi.json')); print('/api/content-filter' in d['paths'])"
```
Expected: `True`, `True`.

- [x] **[WEB] Worker + frontend tests + type-check**

```bash
# from the [WEB] cwd
npx vitest run server/__tests__/content-filter-routes.test.ts server/__tests__/content-filter-modes-parity.test.ts --config vitest.server.config.ts
npx vitest run src/pages/data/__tests__/content-filter-overlay.test.ts
npx tsc -p server/tsconfig.json --noEmit
npx vue-tsc --noEmit -p tsconfig.app.json
```
Expected: all pass; both type-checks clean.

- [x] **[WEB] i18n parity** — re-run the Task 14 Step 3 parity script; both arrays empty.

- [ ] **Manual smoke** (optional, requires both backends + a seeded `ContentFilterRule` table in REPORTS_DB): _Left unchecked: deferred (needs a live both-backends deployment + seeded REPORTS_DB, out of scope for the isolated worktrees). The automated router/route/overlay/i18n suites pin the same behavior deterministically._
  1. Log in as `admin` → Settings shows a **Filter Rules** tab (absent if `content_filter` is false).
  2. Add a rule `dimension=actor mode=exclude value="<an actor on your Movies list>"`.
  3. Open Data → Movies → rows by that actor render dimmed/struck-through (the read-side overlay), while the data is unchanged.
  4. Log in as a readonly user → the tab still appears but Add/Delete/Toggle controls are disabled; the overlay still dims.

- [x] **REPORTS_DB audit (the load-bearing check):** grep every file this IMP touched for the DB binding and confirm NONE accidentally use `HISTORY_DB`/`HISTORY_DB_PATH`/`OPERATIONS_DB`:

```bash
# [MAIN]
grep -n "HISTORY_DB\|OPERATIONS_DB" /Users/tedwu/JAVDB_AutoSpider_CICD/apps/api/routers/content_filter.py \
  /Users/tedwu/JAVDB_AutoSpider_CICD/apps/api/routers/capabilities.py || echo "OK: no wrong binding in new content_filter code"
# [WEB] — from cwd
grep -n "HISTORY_DB\|OPERATIONS_DB" server/services/content-filter-service.ts server/routes/content-filter.ts || echo "OK: TS uses REPORTS_DB only"
```
Expected: the only `capabilities` hits are the *other* features' probes (watch_intent/closed_loop/etc.); the content-filter probe and all new content-filter code must show **REPORTS_DB** only.

---

## Coverage Check (what this IMP pins down)

| Behavior | Pinned by |
| --- | --- |
| CRUD round-trip (add→list→toggle→delete) over `ContentFilterRule` in REPORTS_DB (Python) | `tests/unit/test_content_filter_router.py::test_add_list_toggle_delete` |
| Invalid `(dimension, mode)` pair → 422 (Python) | `test_content_filter_router.py::test_rejects_invalid_dimension_mode_pair` |
| Value-required pair with empty value → 422 (Python) | `test_content_filter_router.py::test_value_required_pair_rejects_empty` |
| Toggle/delete on missing rule → 404 (Python) | `test_content_filter_router.py::test_toggle_missing_rule_404` |
| CRUD round-trip over REPORTS_DB (TS Worker) | `server/__tests__/content-filter-routes.test.ts` |
| Invalid pair → 422 (TS Worker) | `content-filter-routes.test.ts` ("rejects an invalid dimension/mode pair") |
| Allow-list never drifts CLI↔Python↔TS | `test_content_filter_modes_parity.py` + `content-filter-modes-parity.test.ts` (one shared canonical set) |
| Router uses the CLI tuples verbatim (cannot fork) | `test_content_filter_modes_parity.py::test_router_imports_the_canonical_cli_tuples` |
| Read-side overlay matching (case-insensitive, disabled-rule skip, fail-open on unmatchable dimensions) | `src/pages/data/__tests__/content-filter-overlay.test.ts` |
| `content_filter` flag present in both backends' capability payloads | Python: capability spot-check; TS: `-t capabilit` run |
| en/zh i18n key parity for the new strings | Task 14 Step 3 parity script |

**Decisions logged for the reviewer:**
- **No new unit test for the capability probe shape** beyond the spot-check: the probe is a verbatim clone of the four existing probes (`_watch_intent_enabled` etc.), differing only in table name and binding; the existing capabilities tests + the REPORTS_DB audit grep cover it. The load-bearing risk (wrong binding) is caught by the audit grep, not a unit test.
- **No byte-mirrored SQL parity test** (unlike WS1): the CRUD SQL is shaped differently per backend (DB-API vs D1 `prepare/bind`), so there is no single string to pin; the cross-backend invariant is the allow-list, which is pinned instead.
- **The overlay matcher is deliberately partial** (actor/gender only): the Movies search row carries no tag/age/release-date data, and the overlay is a non-authoritative hint, so fail-open is correct — pinned by the "fail-open" overlay test.
