# IMP-ADR034-01: Acquisition Web Surface (Media Closed-Loop FE Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-034](ADR-034-media-closed-loop-web-surface.md) (web surface) — this is **FE Phase 1** of three. Backend dependency [ADR-033 Phase 1](../ADR-033-Media-Closed-Loop/IMP-ADR033-01-acquisition-outcome.md) (`AcquisitionOutcome` table + `record_queued`/cleanup completion push) is **implemented and locally verified**, so the endpoint shapes below are grounded in the real table, not a paper contract.

**Status:** Not started (planned 2026-06-06).

**Goal:** Make the ADR-033 acquisition-outcome data visible — add a top-level **Library** page (Acquisition tab) to the Vue console, served by three read-only `GET /api/library/acquisition/{summary,recent,trend}` endpoints implemented at full parity in **both** the Python FastAPI backend and the TypeScript Cloudflare Worker, gated by a new `closed_loop` capability flag.

**Architecture:** D1 `AcquisitionOutcome` (operations DB) is the single source. The Python backend is the **contract source of truth**: its Pydantic `response_model`s generate `docs/api/openapi.json`, and its pure SQL builders are pinned into `docs/api/contract/query-builders.golden.json` (ADR-018). The Worker vendors both artifacts and mirrors the SQL byte-for-byte (enforced by the query-contract conformance test). The frontend reads the three endpoints through the existing axios client and gates the nav entry on `capabilities.features.closed_loop`. Read-only only — no mutations (ADR-034 Non-Goals).

**Tech Stack:** Python 3 / FastAPI / Pydantic / pytest (CICD repo); TypeScript / Hono / Cloudflare D1 / vitest + `cloudflare:test` (Web repo, server); Vue 3 / Naive UI / Pinia / vue-i18n / vue-chartjs / vitest + `@vue/test-utils` (Web repo, SPA).

**Cross-repo boundary (ADR-034 D7).** This plan spans **two git repositories**:

- **CICD repo** (this monorepo) — Python backend, the openapi + query-golden artifacts. Root referenced below as `<CICD>` = the repo you are in (e.g. the worktree root).
- **Web repo** — `/Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web` (a **separate** git repo, **not** a sibling `../` of the CICD package). Root referenced below as `<WEB>`. The TS Worker (`server/`) and the Vue SPA (`src/`) both live here.

The seam between them is two generated files copied from `<CICD>/docs/api/` into `<WEB>` by the vendoring scripts (`scripts/fetch-openapi.mjs`, `scripts/fetch-query-golden.mjs`). **Commit the two repos separately**; reference the paired PR in each description.

---

## File Structure

### CICD repo (Python backend = contract source of truth)

| Path | Create/Modify | Responsibility |
| --- | --- | --- |
| `apps/api/schemas/library.py` | Create | `AcquisitionSummary`, `AcquisitionRecentItem`, `AcquisitionTrendPoint` response models |
| `apps/api/routers/library_query_builders.py` | Create | Pure SQL builders (summary / recent / trend) — the parity unit pinned by the golden |
| `apps/api/routers/library.py` | Create | `APIRouter(prefix="/api/library")` with the three GET endpoints |
| `apps/api/services/runtime.py` | Modify | Import + register `library_router` |
| `apps/api/schemas/capabilities_payloads.py` | Modify | Add `closed_loop: bool` to `Features` |
| `apps/api/routers/capabilities.py` | Modify | Add `_closed_loop_enabled()` probe → `closed_loop` flag |
| `apps/cli/ops/query_contract_cases.py` | Modify | Add `LIBRARY_{SUMMARY,RECENT,TREND}_QUERY_CASES` |
| `apps/cli/ops/dump_query_contract.py` | Modify | Register the three builders + emit their cases |
| `docs/api/contract/query-builders.golden.json` | Regenerate | `python3 -m apps.cli.ops.dump_query_contract` |
| `docs/api/openapi.json` | Regenerate | `python3 -m apps.cli.ops.dump_openapi` |
| `tests/unit/test_library_query_builders.py` | Create | Builder SQL + bindings unit tests |
| `tests/integration/test_library_endpoints.py` | Create | Endpoint behaviour (auth, summary, recent+filter, trend) |
| `tests/integration/test_capabilities_closed_loop.py` | Create | `closed_loop` flag present + boolean |

### Web repo — TS Worker (`<WEB>/server/`)

| Path | Create/Modify | Responsibility |
| --- | --- | --- |
| `server/__tests__/fixtures/query-builders.golden.json` | Regenerate (vendored) | `QUERY_GOLDEN_PATH=… node scripts/fetch-query-golden.mjs` |
| `src/types/api.gen.ts` + `tmp/openapi.json` | Regenerate (vendored) | `OPENAPI_PATH=… node scripts/fetch-openapi.mjs` |
| `server/routes/library.ts` | Create | Hono route + the three TS builders (mirror of Python) |
| `server/app.ts` | Modify | `app.route("/api/library", libraryRoutes)` |
| `server/routes/capabilities.ts` | Modify | `closedLoopEnabled()` probe → `closed_loop` flag |
| `server/__tests__/query-contract.test.ts` | Modify | Add `library_*` entries to the `RUN` registry |
| `server/__tests__/library-routes.test.ts` | Create | Seed D1 + assert summary/recent/trend shapes |

### Web repo — Vue SPA (`<WEB>/src/`)

| Path | Create/Modify | Responsibility |
| --- | --- | --- |
| `src/api/library.ts` | Create | Typed axios client for the three endpoints |
| `src/router/routes.ts` | Modify | Add `/library` route |
| `src/components/layout/Sidebar.vue` | Modify | Nav entry gated by `features.closed_loop` + `routeMap` |
| `src/pages/library/LibraryPage.vue` | Create | Page shell: 3 `NTabPane` (Acquisition + 2 disabled) |
| `src/pages/library/AcquisitionView.vue` | Create | Funnel + 5 KPI cards + recent `NDataTable` + state filter + trend chart |
| `src/i18n/locales/en.json` | Modify | `nav.library` + `library.*` strings |
| `src/i18n/locales/zh-CN.json` | Modify | Paired zh strings (translation drift is a defect) |
| `tests/unit/acquisition-view.spec.ts` | Create | Mock `@/api/library`, assert KPI + table render |

**Naming contract (used verbatim across tasks):**
Python builders `build_acquisition_summary_query()`, `build_acquisition_recent_query(*, state=None, limit=50, offset=0)`, `build_acquisition_trend_query(*, cutoff)` — each returns `tuple[str, list]`. TS mirrors `buildAcquisitionSummaryQuery()`, `buildAcquisitionRecentQuery({state, limit, offset})`, `buildAcquisitionTrendQuery({cutoff})` — each returns `{ sql, bindings }`. Contract builder ids: `library_summary_query`, `library_recent_query`, `library_trend_query`. Response shapes (ADR-034 D3, verbatim): summary `{queued, downloading, completed, stalled, failed, total}`; recent item `{qb_hash, video_code, href, category, state, queued_at, completed_at, last_seen_at}`; trend point `{date, completed, stalled, failed}`.

**Grounding facts (verified against the landed ADR-033 Phase 1 code — do not re-derive):**
- `AcquisitionOutcome` lives in the **operations** DB; reach it with `get_db(OPERATIONS_DB_PATH)` (Python) / `c.env.OPERATIONS_DB` (Worker, binding `OPERATIONS_DB` → `javdb-operations`, already in `wrangler.toml`).
- States: `queued | downloading | completed | in_library | stalled | failed`. `in_library` is **Phase-2-gated** and never written in Phase 1, but is a valid enum value — validation must accept it.
- `record_queued` writes **both** `queued_at` and `last_seen_at` at queue time; the cleanup push sets `completed_at` = `last_seen_at`; the reconcile pass refreshes `last_seen_at` on every transition. Hence `queued_at` is the stable insertion order (used by `recent`) and `last_seen_at` is the transition date (used by `trend`).
- Timestamps are `utc_now_iso()` → `YYYY-MM-DDTHH:MM:SS.ffffffZ` (ISO, `T`-separated, `Z`-suffixed). **`DATE()` cannot parse this**, so `trend` extracts the day with `substr(last_seen_at, 1, 10)`. A `YYYY-MM-DD` cutoff compares correctly against the full timestamp lexicographically.

---

## Task 1: Python response schemas

**Files:**
- Create: `apps/api/schemas/library.py`

- [ ] **Step 1: Write the response models**

```python
# apps/api/schemas/library.py
from __future__ import annotations

from pydantic import BaseModel


class AcquisitionSummary(BaseModel):
    """Funnel/KPI counts for GET /api/library/acquisition/summary."""

    queued: int
    downloading: int
    completed: int
    stalled: int
    failed: int
    total: int


class AcquisitionRecentItem(BaseModel):
    """One AcquisitionOutcome row for GET /api/library/acquisition/recent."""

    qb_hash: str
    video_code: str | None = None
    href: str
    category: str | None = None
    state: str
    queued_at: str | None = None
    completed_at: str | None = None
    last_seen_at: str | None = None


class AcquisitionTrendPoint(BaseModel):
    """One day in GET /api/library/acquisition/trend (ADR-027 trend shape)."""

    date: str
    completed: int
    stalled: int
    failed: int
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `python3 -c "from apps.api.schemas.library import AcquisitionSummary, AcquisitionRecentItem, AcquisitionTrendPoint; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add apps/api/schemas/library.py
git commit -m "feat(api): add library acquisition response schemas (ADR-034 FE-1)"
```

---

## Task 2: Python query builders (pure SQL, TDD)

**Files:**
- Create: `apps/api/routers/library_query_builders.py`
- Test: `tests/unit/test_library_query_builders.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_library_query_builders.py
from apps.api.routers.library_query_builders import (
    build_acquisition_recent_query,
    build_acquisition_summary_query,
    build_acquisition_trend_query,
)


def test_summary_query_is_param_free_count():
    sql, bindings = build_acquisition_summary_query()
    assert bindings == []
    assert "FROM AcquisitionOutcome" in sql
    assert "COUNT(*) AS total" in sql
    # all five named states are counted
    for state in ("queued", "downloading", "completed", "stalled", "failed"):
        assert f"state='{state}'" in sql


def test_recent_query_without_state_omits_where():
    sql, bindings = build_acquisition_recent_query(state=None, limit=50, offset=0)
    assert "WHERE" not in sql
    assert "ORDER BY queued_at DESC" in sql
    assert bindings == [50, 0]


def test_recent_query_with_state_binds_state_first():
    sql, bindings = build_acquisition_recent_query(state="completed", limit=20, offset=40)
    assert "WHERE state = ?" in sql
    assert bindings == ["completed", 20, 40]


def test_trend_query_uses_substr_day_and_terminal_states():
    sql, bindings = build_acquisition_trend_query(cutoff="2026-01-01")
    assert "substr(last_seen_at, 1, 10)" in sql
    assert "state IN ('completed','stalled','failed')" in sql
    assert "last_seen_at >= ?" in sql
    assert bindings == ["2026-01-01"]
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `pytest tests/unit/test_library_query_builders.py -v`
Expected: FAIL — `ModuleNotFoundError: apps.api.routers.library_query_builders`

- [ ] **Step 3: Write the builders**

```python
# apps/api/routers/library_query_builders.py
"""Pure SQL builders for the Library acquisition endpoints (ADR-034 FE-1).

These are the dual-backend parity unit (ADR-018): the TS Worker mirrors each
string byte-for-byte and the query-contract golden pins them. Keep the SQL on
single-line string fragments so whitespace normalization stays trivial.
"""

from __future__ import annotations


def build_acquisition_summary_query() -> tuple[str, list]:
    """Funnel/KPI counts across all rows. COALESCE so an empty table yields 0."""
    sql = (
        "SELECT "
        "COALESCE(SUM(CASE WHEN state='queued' THEN 1 ELSE 0 END), 0) AS queued, "
        "COALESCE(SUM(CASE WHEN state='downloading' THEN 1 ELSE 0 END), 0) AS downloading, "
        "COALESCE(SUM(CASE WHEN state='completed' THEN 1 ELSE 0 END), 0) AS completed, "
        "COALESCE(SUM(CASE WHEN state='stalled' THEN 1 ELSE 0 END), 0) AS stalled, "
        "COALESCE(SUM(CASE WHEN state='failed' THEN 1 ELSE 0 END), 0) AS failed, "
        "COUNT(*) AS total "
        "FROM AcquisitionOutcome"
    )
    return sql, []


def build_acquisition_recent_query(
    *, state: str | None = None, limit: int = 50, offset: int = 0
) -> tuple[str, list]:
    """Newest-first page; optional state filter. queued_at is the stable order."""
    bindings: list = []
    where = ""
    if state is not None:
        where = "WHERE state = ? "
        bindings.append(state)
    sql = (
        "SELECT qb_hash, video_code, href, category, state, queued_at, completed_at, last_seen_at "
        "FROM AcquisitionOutcome "
        f"{where}"
        "ORDER BY queued_at DESC "
        "LIMIT ? OFFSET ?"
    )
    bindings.extend([limit, offset])
    return sql, bindings


def build_acquisition_trend_query(*, cutoff: str) -> tuple[str, list]:
    """Daily completed/stalled/failed counts since cutoff (YYYY-MM-DD).

    substr() not DATE(): last_seen_at is a T-separated ISO timestamp DATE()
    cannot parse. last_seen_at is the transition date (refreshed every pass).
    """
    sql = (
        "SELECT substr(last_seen_at, 1, 10) AS d, "
        "COALESCE(SUM(CASE WHEN state='completed' THEN 1 ELSE 0 END), 0) AS completed, "
        "COALESCE(SUM(CASE WHEN state='stalled' THEN 1 ELSE 0 END), 0) AS stalled, "
        "COALESCE(SUM(CASE WHEN state='failed' THEN 1 ELSE 0 END), 0) AS failed "
        "FROM AcquisitionOutcome "
        "WHERE state IN ('completed','stalled','failed') AND last_seen_at >= ? "
        "GROUP BY d ORDER BY d"
    )
    return sql, [cutoff]
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `pytest tests/unit/test_library_query_builders.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add apps/api/routers/library_query_builders.py tests/unit/test_library_query_builders.py
git commit -m "feat(api): add pure SQL builders for library acquisition (ADR-034 FE-1)"
```

---

## Task 3: Python router + registration (TDD)

**Files:**
- Create: `apps/api/routers/library.py`
- Modify: `apps/api/services/runtime.py` (import at line ~55; register in the loop at line ~189)
- Test: `tests/integration/test_library_endpoints.py`

- [ ] **Step 1: Write the failing integration test**

The integration `conftest.py` provides `admin_client` / `anon_client`; the root `conftest.py` provides `_isolate_sqlite` (fresh empty SQLite with all tables, patched `OPERATIONS_DB_PATH`). Seed `AcquisitionOutcome` directly.

```python
# tests/integration/test_library_endpoints.py
import sqlite3

import pytest


@pytest.fixture(autouse=True, scope="module")
def _ensure_db_initialized():
    from javdb.storage.db import init_db
    init_db()


@pytest.fixture
def seeded_outcomes(_isolate_sqlite):
    """Seed AcquisitionOutcome rows; return the patched operations db_path."""
    db_path = _isolate_sqlite
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO AcquisitionOutcome
                (qb_hash, href, video_code, category, state, queued_at, completed_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("h1", "/v/a", "AAA-001", "subtitle", "queued",
                 "2026-06-01T00:00:00.000000Z", None, "2026-06-01T00:00:00.000000Z"),
                ("h2", "/v/b", "BBB-002", "no_subtitle", "downloading",
                 "2026-06-02T00:00:00.000000Z", None, "2026-06-02T00:00:00.000000Z"),
                ("h3", "/v/c", "CCC-003", "subtitle", "completed",
                 "2026-06-03T00:00:00.000000Z", "2026-06-04T00:00:00.000000Z", "2026-06-04T00:00:00.000000Z"),
                # queued earliest (05-30) so queued_at DESC order is deterministic;
                # stalled later (last_seen 06-05) drives the trend assertion.
                ("h4", "/v/d", "DDD-004", "subtitle", "stalled",
                 "2026-05-30T00:00:00.000000Z", None, "2026-06-05T00:00:00.000000Z"),
            ],
        )
        conn.commit()
    return db_path


def test_summary_counts_by_state(admin_client, seeded_outcomes):
    r = admin_client.get("/api/library/acquisition/summary")
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "queued": 1, "downloading": 1, "completed": 1,
        "stalled": 1, "failed": 0, "total": 4,
    }


def test_summary_empty_table_is_all_zero(admin_client, _isolate_sqlite):
    r = admin_client.get("/api/library/acquisition/summary")
    assert r.status_code == 200
    assert r.json() == {
        "queued": 0, "downloading": 0, "completed": 0,
        "stalled": 0, "failed": 0, "total": 0,
    }


def test_recent_returns_rows_newest_first(admin_client, seeded_outcomes):
    r = admin_client.get("/api/library/acquisition/recent")
    assert r.status_code == 200
    items = r.json()
    assert [i["qb_hash"] for i in items] == ["h3", "h2", "h1", "h4"]
    assert set(items[0].keys()) == {
        "qb_hash", "video_code", "href", "category", "state",
        "queued_at", "completed_at", "last_seen_at",
    }


def test_recent_state_filter(admin_client, seeded_outcomes):
    r = admin_client.get("/api/library/acquisition/recent", params={"state": "completed"})
    assert r.status_code == 200
    items = r.json()
    assert [i["qb_hash"] for i in items] == ["h3"]


def test_recent_rejects_unknown_state(admin_client, seeded_outcomes):
    r = admin_client.get("/api/library/acquisition/recent", params={"state": "bogus"})
    assert r.status_code == 400


def test_recent_accepts_in_library_state(admin_client, seeded_outcomes):
    # in_library is Phase-2-gated but a valid enum value — must not 400.
    r = admin_client.get("/api/library/acquisition/recent", params={"state": "in_library"})
    assert r.status_code == 200
    assert r.json() == []


def test_trend_groups_terminal_states_by_day(admin_client, seeded_outcomes):
    r = admin_client.get("/api/library/acquisition/trend", params={"period": "90d"})
    assert r.status_code == 200
    points = r.json()
    by_date = {p["date"]: p for p in points}
    # completed h3 last_seen 06-04; stalled h4 last_seen 06-05
    assert by_date["2026-06-04"]["completed"] == 1
    assert by_date["2026-06-05"]["stalled"] == 1
    # queued/downloading rows are excluded from the trend
    assert "2026-06-02" not in by_date


def test_trend_rejects_bad_period(admin_client, seeded_outcomes):
    r = admin_client.get("/api/library/acquisition/trend", params={"period": "5h"})
    assert r.status_code == 400


def test_endpoints_require_auth(anon_client):
    for path in (
        "/api/library/acquisition/summary",
        "/api/library/acquisition/recent",
        "/api/library/acquisition/trend",
    ):
        assert anon_client.get(path).status_code in (401, 403)
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `pytest tests/integration/test_library_endpoints.py -v`
Expected: FAIL — 404 on every route (router not registered yet)

- [ ] **Step 3: Write the router**

```python
# apps/api/routers/library.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from apps.api.infra.auth import _require_auth
from apps.api.routers.library_query_builders import (
    build_acquisition_recent_query,
    build_acquisition_summary_query,
    build_acquisition_trend_query,
)
from apps.api.schemas.library import (
    AcquisitionRecentItem,
    AcquisitionSummary,
    AcquisitionTrendPoint,
)
from javdb.ops.reconcile.models import ACQUISITION_STATES
from javdb.storage.db import OPERATIONS_DB_PATH, get_db

router = APIRouter(prefix="/api/library", tags=["library"])

_PERIOD_DAYS = {"7d": 7, "30d": 30, "90d": 90}
_SUMMARY_KEYS = ("queued", "downloading", "completed", "stalled", "failed", "total")
_RECENT_COLS = (
    "qb_hash", "video_code", "href", "category",
    "state", "queued_at", "completed_at", "last_seen_at",
)


@router.get("/acquisition/summary", response_model=AcquisitionSummary)
def acquisition_summary(_user=Depends(_require_auth)) -> AcquisitionSummary:
    sql, bindings = build_acquisition_summary_query()
    with get_db(OPERATIONS_DB_PATH) as conn:
        row = conn.execute(sql, bindings).fetchone()
    values = dict(zip(_SUMMARY_KEYS, row)) if row else {k: 0 for k in _SUMMARY_KEYS}
    return AcquisitionSummary(**values)


@router.get("/acquisition/recent", response_model=list[AcquisitionRecentItem])
def acquisition_recent(
    state: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _user=Depends(_require_auth),
) -> list[AcquisitionRecentItem]:
    if state is not None and state not in ACQUISITION_STATES:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "library.invalid_state", "message": f"Invalid state: {state}"}},
        )
    sql, bindings = build_acquisition_recent_query(state=state, limit=limit, offset=offset)
    with get_db(OPERATIONS_DB_PATH) as conn:
        rows = conn.execute(sql, bindings).fetchall()
    return [AcquisitionRecentItem(**dict(zip(_RECENT_COLS, r))) for r in rows]


@router.get("/acquisition/trend", response_model=list[AcquisitionTrendPoint])
def acquisition_trend(
    period: str = Query(default="30d"),
    _user=Depends(_require_auth),
) -> list[AcquisitionTrendPoint]:
    if period not in _PERIOD_DAYS:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "library.invalid_period", "message": f"Invalid period: {period}"}},
        )
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_PERIOD_DAYS[period])).strftime("%Y-%m-%d")
    sql, bindings = build_acquisition_trend_query(cutoff=cutoff)
    with get_db(OPERATIONS_DB_PATH) as conn:
        rows = conn.execute(sql, bindings).fetchall()
    return [
        AcquisitionTrendPoint(date=r[0], completed=r[1], stalled=r[2], failed=r[3])
        for r in rows
    ]
```

- [ ] **Step 4: Register the router in `apps/api/services/runtime.py`**

Add the import next to the other router imports (after line 55, `preferences_router`):

```python
from apps.api.routers.library import router as library_router
```

Add `library_router` to the `for router in (...)` tuple (the block starting at line 172), e.g. immediately after `history_router,`:

```python
    history_router,
    library_router,
    operations_router,
```

- [ ] **Step 5: Run the test to confirm it passes**

Run: `pytest tests/integration/test_library_endpoints.py -v`
Expected: PASS (9 passed)

- [ ] **Step 6: Commit**

```bash
git add apps/api/routers/library.py apps/api/services/runtime.py tests/integration/test_library_endpoints.py
git commit -m "feat(api): add read-only library acquisition endpoints (ADR-034 FE-1)"
```

---

## Task 4: Python `closed_loop` capability flag (TDD)

**Files:**
- Modify: `apps/api/schemas/capabilities_payloads.py:13-19` (the `Features` model)
- Modify: `apps/api/routers/capabilities.py`
- Test: `tests/integration/test_capabilities_closed_loop.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_capabilities_closed_loop.py
def test_capabilities_exposes_closed_loop_bool(admin_client, _isolate_sqlite):
    r = admin_client.get("/api/capabilities")
    assert r.status_code == 200
    features = r.json()["features"]
    assert "closed_loop" in features
    assert isinstance(features["closed_loop"], bool)
    # _isolate_sqlite creates the AcquisitionOutcome table via init_db → true
    assert features["closed_loop"] is True
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `pytest tests/integration/test_capabilities_closed_loop.py -v`
Expected: FAIL — `KeyError: 'closed_loop'` (field absent)

- [ ] **Step 3: Add the field to the `Features` model**

In `apps/api/schemas/capabilities_payloads.py`, add `closed_loop: bool` to `Features`:

```python
class Features(BaseModel):
    pikpak: bool
    rclone: bool
    smtp: bool
    proxy_pool: bool
    javdb_login: bool
    proxy_preview: bool
    closed_loop: bool
```

- [ ] **Step 4: Compute the flag in `apps/api/routers/capabilities.py`**

Add the probe helper above `build_capabilities` (after `_bool_env`, line 39):

```python
def _closed_loop_enabled() -> bool:
    """True when the ADR-033 AcquisitionOutcome table is queryable (capability honesty)."""
    try:
        from javdb.storage.db import OPERATIONS_DB_PATH, get_db
        with get_db(OPERATIONS_DB_PATH) as conn:
            conn.execute("SELECT 1 FROM AcquisitionOutcome LIMIT 1").fetchone()
        return True
    except Exception:
        return False
```

Add the field to the `Features(...)` call inside `build_capabilities` (line 68):

```python
        features=Features(
            pikpak=_bool_env("FEATURE_PIKPAK"),
            rclone=_bool_env("FEATURE_RCLONE"),
            smtp=bool(os.getenv("SMTP_HOST") or os.getenv("SMTP_SERVER")),
            proxy_pool=_bool_env("PROXY_MODE_POOL", default=True),
            javdb_login=bool(os.getenv("JAVDB_USERNAME")),
            proxy_preview=True,
            closed_loop=_closed_loop_enabled(),
        ),
```

- [ ] **Step 5: Run the test to confirm it passes**

Run: `pytest tests/integration/test_capabilities_closed_loop.py -v`
Expected: PASS (1 passed)

- [ ] **Step 6: Commit**

```bash
git add apps/api/schemas/capabilities_payloads.py apps/api/routers/capabilities.py tests/integration/test_capabilities_closed_loop.py
git commit -m "feat(api): expose closed_loop capability flag (ADR-034 FE-1)"
```

---

## Task 5: Pin builders in the query contract + regenerate artifacts

**Files:**
- Modify: `apps/cli/ops/query_contract_cases.py`
- Modify: `apps/cli/ops/dump_query_contract.py`
- Regenerate: `docs/api/contract/query-builders.golden.json`, `docs/api/openapi.json`

- [ ] **Step 1: Add contract cases**

Append to `apps/cli/ops/query_contract_cases.py` (after `STATS_TREND_QUERY_CASES`):

```python
LIBRARY_SUMMARY_QUERY_CASES = [
    ("library_summary_query", "all", {}),
]
LIBRARY_RECENT_QUERY_CASES = [
    ("library_recent_query", "no_state", {"state": None, "limit": 50, "offset": 0}),
    ("library_recent_query", "with_state", {"state": "completed", "limit": 20, "offset": 40}),
]
LIBRARY_TREND_QUERY_CASES = [
    ("library_trend_query", "default", {"cutoff": "2026-01-01"}),
]
```

- [ ] **Step 2: Register the builders in `apps/cli/ops/dump_query_contract.py`**

Add to the imports from `query_contract_cases` (line 13):

```python
from apps.cli.ops.query_contract_cases import (  # noqa: E402
    LIBRARY_RECENT_QUERY_CASES,
    LIBRARY_SUMMARY_QUERY_CASES,
    LIBRARY_TREND_QUERY_CASES,
    MOVIE_COUNT_CASES,
    MOVIE_FILTER_CASES,
    SESSION_QUERY_CASES,
    STATS_TREND_QUERY_CASES,
    TORRENT_COUNT_CASES,
    TORRENT_FILTER_CASES,
    normalize_sql,
)
```

Add a builders import (after the `stats_query_builders` import, line 22):

```python
from apps.api.routers.library_query_builders import (  # noqa: E402
    build_acquisition_recent_query,
    build_acquisition_summary_query,
    build_acquisition_trend_query,
)
```

Add the three to `_BUILDERS` (line 50):

```python
_BUILDERS = {
    "movie_filters": _build_movie_filters,
    "movie_count": build_movie_count,
    "torrent_filters": _build_torrent_filters,
    "torrent_count": build_torrent_count,
    "session_query": _build_session_query,
    "stats_trend_query": _build_stats_trend_query_for_contract,
    "library_summary_query": build_acquisition_summary_query,
    "library_recent_query": build_acquisition_recent_query,
    "library_trend_query": build_acquisition_trend_query,
}
```

Add the cases to the loop in `main()` (line 71):

```python
    for builder_id, name, kwargs in (
        *MOVIE_FILTER_CASES,
        *MOVIE_COUNT_CASES,
        *TORRENT_FILTER_CASES,
        *TORRENT_COUNT_CASES,
        *SESSION_QUERY_CASES,
        *STATS_TREND_QUERY_CASES,
        *LIBRARY_SUMMARY_QUERY_CASES,
        *LIBRARY_RECENT_QUERY_CASES,
        *LIBRARY_TREND_QUERY_CASES,
    ):
```

- [ ] **Step 2b: Run it to confirm the golden test now fails (coverage gap is real)**

Run: `pytest tests/unit/test_query_contract_golden.py -v`
Expected: FAIL — `test_golden_covers_all_builders` reports `golden missing builders: {'library_summary_query', 'library_recent_query', 'library_trend_query'}` (the golden has not been regenerated yet). This proves the registration is wired before we regenerate.

- [ ] **Step 3: Regenerate the golden**

Run: `python3 -m apps.cli.ops.dump_query_contract`
Expected: `wrote …/docs/api/contract/query-builders.golden.json (N cases)` where N grew by 4.

- [ ] **Step 4: Regenerate openapi**

Run: `python3 -m apps.cli.ops.dump_openapi`
Expected: writes `docs/api/openapi.json`. Confirm it now contains the new paths:

Run: `python3 -c "import json; s=json.load(open('docs/api/openapi.json'))['paths']; print([p for p in s if '/api/library/' in p])"`
Expected: `['/api/library/acquisition/summary', '/api/library/acquisition/recent', '/api/library/acquisition/trend']`

- [ ] **Step 4b: Guard the new endpoints in the openapi-shape test**

The library endpoints are FE-consumed, so add them to the curated `must_be_typed` list in `tests/integration/test_openapi_response_shapes.py` (after the `/api/explore/search-by-video-code` tuple, line 28). `summary` is a `$ref` (object); `recent`/`trend` are `type: array` — both satisfy the test's `is_typed` check.

```python
        ("/api/library/acquisition/summary", "get"),
        ("/api/library/acquisition/recent", "get"),
        ("/api/library/acquisition/trend", "get"),
```

- [ ] **Step 5: Run the full contract + openapi-shape gate**

Run: `pytest tests/unit/test_query_contract_golden.py tests/integration/test_openapi_response_shapes.py -v`
Expected: PASS (every golden case matches; every library endpoint has a typed `response_model`).

- [ ] **Step 6: Commit**

```bash
git add apps/cli/ops/query_contract_cases.py apps/cli/ops/dump_query_contract.py docs/api/contract/query-builders.golden.json docs/api/openapi.json tests/integration/test_openapi_response_shapes.py
git commit -m "feat(api): pin library query builders to contract golden + openapi (ADR-034 FE-1)"
```

> **End of CICD-repo changes.** Open the CICD PR now (or after Task 12's full-suite run). The remaining tasks are in `<WEB>`.

---

## Task 6: Vendor the regenerated artifacts into the Web repo

**Files (in `<WEB>`):**
- Regenerate (vendored): `server/__tests__/fixtures/query-builders.golden.json`
- Regenerate (vendored): `src/types/api.gen.ts`, `tmp/openapi.json`

> `<CICD>` below is the absolute path to the CICD repo you committed Tasks 1–5 in (e.g. the worktree root). All commands run from `<WEB>`.

- [ ] **Step 1: Vendor the query-contract golden**

Run:
```bash
cd /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web
QUERY_GOLDEN_PATH="<CICD>/docs/api/contract/query-builders.golden.json" node scripts/fetch-query-golden.mjs
```
Expected: `[fetch-query-golden] wrote …/server/__tests__/fixtures/query-builders.golden.json`

- [ ] **Step 2: Vendor openapi + regenerate TS types**

Run:
```bash
OPENAPI_PATH="<CICD>/docs/api/openapi.json" node scripts/fetch-openapi.mjs
```
Expected: writes `tmp/openapi.json` then generates `src/types/api.gen.ts`. Confirm `closed_loop` flowed into the generated types:

Run: `grep -n "closed_loop" src/types/api.gen.ts`
Expected: a `closed_loop: boolean` line under the capabilities `features` schema.

- [ ] **Step 3: Confirm the contract test is now RED (unmapped builders)**

Run: `npx vitest run server/__tests__/query-contract.test.ts`
Expected: FAIL — new golden cases `library_summary_query:*`, `library_recent_query:*`, `library_trend_query:*` fail on `no TS builder mapped for '…'`. This is the intended red state; Task 7 maps them.

- [ ] **Step 4: Commit (vendored artifacts only)**

```bash
git add server/__tests__/fixtures/query-builders.golden.json src/types/api.gen.ts tmp/openapi.json
git commit -m "chore(server): vendor library query-golden + openapi types (ADR-034 FE-1)"
```

---

## Task 7: TS Worker route + builders + contract mapping (TDD)

**Files (in `<WEB>`):**
- Create: `server/routes/library.ts`
- Modify: `server/app.ts:80` (mount, after the stats route)
- Modify: `server/__tests__/query-contract.test.ts` (add `RUN` entries)
- Test: `server/__tests__/library-routes.test.ts`

- [ ] **Step 1: Write the route + builders**

```typescript
// server/routes/library.ts
import { Hono } from "hono";
import { HTTPException } from "hono/http-exception";
import type { Env } from "../env";
import type { JwtPayload } from "../services/jwt";

type LibEnv = { Bindings: Env; Variables: { user: JwtPayload } };

export const libraryRoutes = new Hono<LibEnv>();

// in_library is Phase-2-gated but a valid enum value — accept it.
const ACQUISITION_STATES = [
  "queued", "downloading", "completed", "in_library", "stalled", "failed",
];
const PERIOD_DAYS: Record<string, number> = { "7d": 7, "30d": 30, "90d": 90 };

// ── Pure SQL builders (byte-for-byte mirror of Python; pinned by the golden) ──

export function buildAcquisitionSummaryQuery(): {
  sql: string;
  bindings: (string | number)[];
} {
  const sql =
    "SELECT " +
    "COALESCE(SUM(CASE WHEN state='queued' THEN 1 ELSE 0 END), 0) AS queued, " +
    "COALESCE(SUM(CASE WHEN state='downloading' THEN 1 ELSE 0 END), 0) AS downloading, " +
    "COALESCE(SUM(CASE WHEN state='completed' THEN 1 ELSE 0 END), 0) AS completed, " +
    "COALESCE(SUM(CASE WHEN state='stalled' THEN 1 ELSE 0 END), 0) AS stalled, " +
    "COALESCE(SUM(CASE WHEN state='failed' THEN 1 ELSE 0 END), 0) AS failed, " +
    "COUNT(*) AS total " +
    "FROM AcquisitionOutcome";
  return { sql, bindings: [] };
}

export function buildAcquisitionRecentQuery(p: {
  state?: string | null;
  limit: number;
  offset: number;
}): { sql: string; bindings: (string | number)[] } {
  const bindings: (string | number)[] = [];
  let where = "";
  if (p.state != null) {
    where = "WHERE state = ? ";
    bindings.push(p.state);
  }
  const sql =
    "SELECT qb_hash, video_code, href, category, state, queued_at, completed_at, last_seen_at " +
    "FROM AcquisitionOutcome " +
    where +
    "ORDER BY queued_at DESC " +
    "LIMIT ? OFFSET ?";
  bindings.push(p.limit, p.offset);
  return { sql, bindings };
}

export function buildAcquisitionTrendQuery(p: { cutoff: string }): {
  sql: string;
  bindings: (string | number)[];
} {
  const sql =
    "SELECT substr(last_seen_at, 1, 10) AS d, " +
    "COALESCE(SUM(CASE WHEN state='completed' THEN 1 ELSE 0 END), 0) AS completed, " +
    "COALESCE(SUM(CASE WHEN state='stalled' THEN 1 ELSE 0 END), 0) AS stalled, " +
    "COALESCE(SUM(CASE WHEN state='failed' THEN 1 ELSE 0 END), 0) AS failed " +
    "FROM AcquisitionOutcome " +
    "WHERE state IN ('completed','stalled','failed') AND last_seen_at >= ? " +
    "GROUP BY d ORDER BY d";
  return { sql, bindings: [p.cutoff] };
}

function isoDateDaysAgo(days: number): string {
  return new Date(Date.now() - days * 86_400_000).toISOString().slice(0, 10);
}

function badRequest(code: string, message: string): HTTPException {
  return new HTTPException(400, { message: JSON.stringify({ error: { code, message } }) });
}

// ── Routes (JWT auth inherited from app.use("/api/*", requireAuth())) ─────────

libraryRoutes.get("/acquisition/summary", async (c) => {
  const { sql, bindings } = buildAcquisitionSummaryQuery();
  const row = await c.env.OPERATIONS_DB.prepare(sql)
    .bind(...bindings)
    .first<{
      queued: number; downloading: number; completed: number;
      stalled: number; failed: number; total: number;
    }>();
  return c.json(
    row ?? { queued: 0, downloading: 0, completed: 0, stalled: 0, failed: 0, total: 0 },
  );
});

libraryRoutes.get("/acquisition/recent", async (c) => {
  const state = c.req.query("state") ?? null;
  if (state !== null && !ACQUISITION_STATES.includes(state)) {
    throw badRequest("library.invalid_state", `Invalid state: ${state}`);
  }
  const limit = Math.max(1, Math.min(200, parseInt(c.req.query("limit") ?? "50", 10) || 50));
  const offset = Math.max(0, parseInt(c.req.query("offset") ?? "0", 10) || 0);
  const { sql, bindings } = buildAcquisitionRecentQuery({ state, limit, offset });
  const { results } = await c.env.OPERATIONS_DB.prepare(sql).bind(...bindings).all();
  return c.json(results ?? []);
});

libraryRoutes.get("/acquisition/trend", async (c) => {
  const period = c.req.query("period") ?? "30d";
  if (!(period in PERIOD_DAYS)) {
    throw badRequest("library.invalid_period", `Invalid period: ${period}`);
  }
  const cutoff = isoDateDaysAgo(PERIOD_DAYS[period]);
  const { sql, bindings } = buildAcquisitionTrendQuery({ cutoff });
  const { results } = await c.env.OPERATIONS_DB.prepare(sql)
    .bind(...bindings)
    .all<{ d: string; completed: number; stalled: number; failed: number }>();
  return c.json(
    (results ?? []).map((r) => ({
      date: r.d, completed: r.completed, stalled: r.stalled, failed: r.failed,
    })),
  );
});
```

- [ ] **Step 2: Mount the route in `server/app.ts`**

Add the import alongside the other route imports (after line 19, `statsRoutes`):

```typescript
import { libraryRoutes } from "./routes/library";
```

Mount it after the stats route (line 79), so it inherits `requireAuth()`:

```typescript
app.route("/api/stats", statsRoutes);
app.route("/api/library", libraryRoutes);
```

- [ ] **Step 3: Map the builders in the contract test**

In `server/__tests__/query-contract.test.ts`, add the import (after the `buildStatsTrendQuery` import, line 10):

```typescript
import {
  buildAcquisitionRecentQuery,
  buildAcquisitionSummaryQuery,
  buildAcquisitionTrendQuery,
} from "../routes/library";
```

Add three entries to the `RUN` registry (after the `stats_trend_query` entry, line 56):

```typescript
  library_summary_query: () => buildAcquisitionSummaryQuery(),
  library_recent_query: (p) =>
    buildAcquisitionRecentQuery({ state: p.state ?? null, limit: p.limit, offset: p.offset }),
  library_trend_query: (p) => buildAcquisitionTrendQuery({ cutoff: p.cutoff }),
```

- [ ] **Step 4: Run the contract test — now GREEN**

Run: `npx vitest run server/__tests__/query-contract.test.ts`
Expected: PASS — `library_summary_query:all`, `library_recent_query:no_state`, `library_recent_query:with_state`, `library_trend_query:default` all match the vendored golden.

- [ ] **Step 5: Write the route behaviour test**

```typescript
// server/__tests__/library-routes.test.ts
import { describe, it, expect, beforeAll } from "vitest";
import { env } from "cloudflare:test";
import { app } from "../app";

async function getToken(): Promise<string> {
  const res = await app.request(
    "/api/auth/login",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "admin", password: "testpassword123" }),
    },
    env,
  );
  const data = (await res.json()) as { access_token: string };
  return data.access_token;
}

async function seedOutcomes(db: D1Database) {
  await db
    .prepare(
      `CREATE TABLE IF NOT EXISTS AcquisitionOutcome (
        qb_hash TEXT PRIMARY KEY NOT NULL, href TEXT NOT NULL DEFAULT '',
        video_code TEXT, category TEXT, state TEXT NOT NULL DEFAULT 'queued',
        queued_at TEXT, completed_at TEXT, landed_at TEXT, last_seen_at TEXT, session_id TEXT)`,
    )
    .run();
  await db.batch([
    db.prepare(
      "INSERT INTO AcquisitionOutcome (qb_hash, href, video_code, category, state, queued_at, completed_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?)",
    ).bind("h1", "/v/a", "AAA-001", "subtitle", "queued", "2026-06-01T00:00:00.000000Z", null, "2026-06-01T00:00:00.000000Z"),
    db.prepare(
      "INSERT INTO AcquisitionOutcome (qb_hash, href, video_code, category, state, queued_at, completed_at, last_seen_at) VALUES (?,?,?,?,?,?,?,?)",
    ).bind("h3", "/v/c", "CCC-003", "subtitle", "completed", "2026-06-03T00:00:00.000000Z", "2026-06-04T00:00:00.000000Z", "2026-06-04T00:00:00.000000Z"),
  ]);
}

describe("Library acquisition routes", () => {
  beforeAll(async () => {
    await seedOutcomes(env.OPERATIONS_DB);
  });

  it("summary counts by state", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/library/acquisition/summary",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const body = (await res.json()) as Record<string, number>;
    expect(body.queued).toBe(1);
    expect(body.completed).toBe(1);
    expect(body.total).toBe(2);
  });

  it("recent filters by state", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/library/acquisition/recent?state=completed",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const rows = (await res.json()) as Array<{ qb_hash: string }>;
    expect(rows.map((r) => r.qb_hash)).toEqual(["h3"]);
  });

  it("recent rejects an unknown state", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/library/acquisition/recent?state=bogus",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(400);
  });

  it("trend rejects a bad period", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/library/acquisition/trend?period=5h",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(400);
  });

  it("requires auth", async () => {
    const res = await app.request("/api/library/acquisition/summary", {}, env);
    expect(res.status).toBe(401);
  });
});
```

- [ ] **Step 6: Run the route test**

Run: `npx vitest run server/__tests__/library-routes.test.ts`
Expected: PASS (5 passed)

- [ ] **Step 7: Commit**

```bash
git add server/routes/library.ts server/app.ts server/__tests__/query-contract.test.ts server/__tests__/library-routes.test.ts
git commit -m "feat(server): add library acquisition routes at contract parity (ADR-034 FE-1)"
```

---

## Task 8: TS `closed_loop` capability flag (TDD)

**Files (in `<WEB>`):**
- Modify: `server/routes/capabilities.ts`
- Test: `server/__tests__/capabilities-closed-loop.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// server/__tests__/capabilities-closed-loop.test.ts
import { describe, it, expect, beforeAll } from "vitest";
import { env } from "cloudflare:test";
import { app } from "../app";

async function getToken(): Promise<string> {
  const res = await app.request(
    "/api/auth/login",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "admin", password: "testpassword123" }),
    },
    env,
  );
  return ((await res.json()) as { access_token: string }).access_token;
}

describe("capabilities closed_loop", () => {
  beforeAll(async () => {
    await env.OPERATIONS_DB.prepare(
      "CREATE TABLE IF NOT EXISTS AcquisitionOutcome (qb_hash TEXT PRIMARY KEY NOT NULL, state TEXT)",
    ).run();
  });

  it("exposes a boolean closed_loop flag (true when table exists)", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/capabilities",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const features = ((await res.json()) as { features: Record<string, unknown> }).features;
    expect(typeof features.closed_loop).toBe("boolean");
    expect(features.closed_loop).toBe(true);
  });
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `npx vitest run server/__tests__/capabilities-closed-loop.test.ts`
Expected: FAIL — `closed_loop` is `undefined` (not a boolean).

- [ ] **Step 3: Add the probe + flag in `server/routes/capabilities.ts`**

Make the handler async, add the probe helper, and add the flag to `features`:

```typescript
async function closedLoopEnabled(env: Env): Promise<boolean> {
  try {
    await env.OPERATIONS_DB.prepare("SELECT 1 FROM AcquisitionOutcome LIMIT 1").first();
    return true;
  } catch {
    return false;
  }
}

capabilitiesRoutes.get("/", async (c) => {
  const env = c.env;
  const closed_loop = await closedLoopEnabled(env);

  return c.json({
    version: "2.0.0",
    ingestion_mode: env.INGESTION_MODE ?? "local",
    gh_actions: {
      tier: env.GH_ACTIONS_TIER ?? "none",
      repo: env.GH_ACTIONS_REPO ?? null,
      token_configured: !!env.GH_ACTIONS_TOKEN,
    },
    storage_backend: "d1",
    features: {
      pikpak: envBool(env.FEATURE_PIKPAK),
      rclone: envBool(env.FEATURE_RCLONE),
      smtp: !!(env.SMTP_HOST || env.SMTP_SERVER),
      proxy_pool: true,
      javdb_login: !!env.JAVDB_USERNAME,
      proxy_preview: true,
      closed_loop,
    },
    deployment: "cloudflare",
    build: {
      frontend_version: env.FRONTEND_VERSION ?? null,
      backend_version: env.BACKEND_VERSION ?? "2.0.0",
      git_sha: "cloudflare",
    },
  });
});
```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `npx vitest run server/__tests__/capabilities-closed-loop.test.ts`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add server/routes/capabilities.ts server/__tests__/capabilities-closed-loop.test.ts
git commit -m "feat(server): expose closed_loop capability flag at parity (ADR-034 FE-1)"
```

---

## Task 9: Frontend i18n strings + API client

**Files (in `<WEB>`):**
- Create: `src/api/library.ts`
- Modify: `src/i18n/locales/en.json`, `src/i18n/locales/zh-CN.json`

- [ ] **Step 1: Add the typed API client**

```typescript
// src/api/library.ts
import { http } from './client'

export interface AcquisitionSummary {
  queued: number
  downloading: number
  completed: number
  stalled: number
  failed: number
  total: number
}

export interface AcquisitionRecentItem {
  qb_hash: string
  video_code: string | null
  href: string
  category: string | null
  state: string
  queued_at: string | null
  completed_at: string | null
  last_seen_at: string | null
}

export interface AcquisitionTrendPoint {
  date: string
  completed: number
  stalled: number
  failed: number
}

export async function getAcquisitionSummary(): Promise<AcquisitionSummary> {
  const { data } = await http.get<AcquisitionSummary>('/api/library/acquisition/summary')
  return data
}

export async function getAcquisitionRecent(
  params: { state?: string | null; limit?: number; offset?: number } = {},
): Promise<AcquisitionRecentItem[]> {
  const { data } = await http.get<AcquisitionRecentItem[]>('/api/library/acquisition/recent', {
    params: {
      state: params.state ?? undefined,
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
    },
  })
  return data
}

export async function getAcquisitionTrend(period = '30d'): Promise<AcquisitionTrendPoint[]> {
  const { data } = await http.get<AcquisitionTrendPoint[]>('/api/library/acquisition/trend', {
    params: { period },
  })
  return data
}
```

- [ ] **Step 2: Add English strings**

In `src/i18n/locales/en.json`, add `library` under `nav` and a top-level `library` block:

```json
  "nav": {
    "library": "Library"
  },
  "library": {
    "subtitle": "What was acquired, owned, and watched.",
    "tabs": {
      "acquisition": "Acquisition",
      "ownership": "Ownership",
      "consumption": "Consumption"
    },
    "comingSoon": "Coming soon",
    "funnel": "Acquisition funnel",
    "trend": "Acquisition trend",
    "recent": "Recent acquisitions",
    "allStates": "All states",
    "kpi": {
      "queued": "Queued",
      "downloading": "Downloading",
      "completed": "Completed",
      "stalled": "Stalled",
      "failed": "Failed"
    },
    "state": {
      "queued": "Queued",
      "downloading": "Downloading",
      "completed": "Completed",
      "in_library": "In library",
      "stalled": "Stalled",
      "failed": "Failed"
    },
    "col": {
      "videoCode": "Code",
      "category": "Category",
      "state": "State",
      "queuedAt": "Queued",
      "completedAt": "Completed",
      "lastSeenAt": "Last seen"
    }
  }
```

> Merge into the existing `nav` object — do not create a second `nav` key. Keep keys alphabetical only if the file already is; otherwise append.

- [ ] **Step 3: Add the paired Chinese strings**

In `src/i18n/locales/zh-CN.json`, add the same structure with translations:

```json
  "nav": {
    "library": "资源库"
  },
  "library": {
    "subtitle": "已获取、已拥有、已观看的内容。",
    "tabs": {
      "acquisition": "获取",
      "ownership": "拥有",
      "consumption": "消费"
    },
    "comingSoon": "敬请期待",
    "funnel": "获取漏斗",
    "trend": "获取趋势",
    "recent": "最近获取",
    "allStates": "全部状态",
    "kpi": {
      "queued": "排队中",
      "downloading": "下载中",
      "completed": "已完成",
      "stalled": "已停滞",
      "failed": "失败"
    },
    "state": {
      "queued": "排队中",
      "downloading": "下载中",
      "completed": "已完成",
      "in_library": "已入库",
      "stalled": "已停滞",
      "failed": "失败"
    },
    "col": {
      "videoCode": "番号",
      "category": "分类",
      "state": "状态",
      "queuedAt": "入队时间",
      "completedAt": "完成时间",
      "lastSeenAt": "最近更新"
    }
  }
```

- [ ] **Step 4: Typecheck + commit**

Run: `npm run typecheck`
Expected: no errors.

```bash
git add src/api/library.ts src/i18n/locales/en.json src/i18n/locales/zh-CN.json
git commit -m "feat(web): add library api client + en/zh strings (ADR-034 FE-1)"
```

---

## Task 10: Router entry + capability-gated nav

**Files (in `<WEB>`):**
- Modify: `src/router/routes.ts`
- Modify: `src/components/layout/Sidebar.vue`

- [ ] **Step 1: Add the route**

In `src/router/routes.ts`, add after the `/stats` entry (line 30):

```typescript
  {
    path: '/library',
    name: 'library',
    component: () => import('@/pages/library/LibraryPage.vue'),
    meta: { requiresAuth: true },
  },
```

- [ ] **Step 2: Add the gated nav item in `src/components/layout/Sidebar.vue`**

Inside the `options` computed, after the `data` group push (line 42, before the Operations group), add:

```typescript
  // Library: gated by the closed_loop capability (ADR-034 D4)
  if (!features || features.closed_loop) {
    items.push({ label: t('nav.library'), key: 'library', icon: () => '📚' })
  }
```

Add the route mapping to `routeMap` (after `torrents: '/data/torrents',`):

```typescript
  library: '/library',
```

- [ ] **Step 3: Typecheck**

Run: `npm run typecheck`
Expected: no errors (`features.closed_loop` exists in `api.gen.ts` after Task 6).

- [ ] **Step 4: Commit**

```bash
git add src/router/routes.ts src/components/layout/Sidebar.vue
git commit -m "feat(web): register library route + closed_loop-gated nav (ADR-034 FE-1)"
```

---

## Task 11: Library page shell + Acquisition view

**Files (in `<WEB>`):**
- Create: `src/pages/library/LibraryPage.vue`
- Create: `src/pages/library/AcquisitionView.vue`

- [ ] **Step 1: Write the page shell (3 tabs, 2 disabled placeholders)**

```vue
<!-- src/pages/library/LibraryPage.vue -->
<script setup lang="ts">
import { ref } from 'vue'
import { NTabs, NTabPane, NEmpty } from 'naive-ui'
import { useI18n } from 'vue-i18n'
import AcquisitionView from './AcquisitionView.vue'

const { t } = useI18n()
const activeTab = ref('acquisition')
</script>

<template>
  <div class="library-page">
    <h2>{{ t('nav.library') }}</h2>
    <p class="subtitle">{{ t('library.subtitle') }}</p>
    <NTabs v-model:value="activeTab" type="line" animated>
      <NTabPane name="acquisition" :tab="t('library.tabs.acquisition')">
        <AcquisitionView />
      </NTabPane>
      <NTabPane name="ownership" :tab="t('library.tabs.ownership')" disabled>
        <NEmpty :description="t('library.comingSoon')" />
      </NTabPane>
      <NTabPane name="consumption" :tab="t('library.tabs.consumption')" disabled>
        <NEmpty :description="t('library.comingSoon')" />
      </NTabPane>
    </NTabs>
  </div>
</template>

<style scoped>
.library-page {
  padding: 16px;
}
.subtitle {
  color: var(--text-color-3, #888);
  margin-top: -4px;
  margin-bottom: 12px;
}
</style>
```

- [ ] **Step 2: Write the Acquisition view (funnel + KPI cards + recent table + filter + trend)**

```vue
<!-- src/pages/library/AcquisitionView.vue -->
<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from 'vue'
import {
  NGrid, NGi, NCard, NStatistic, NDataTable, NSelect, NTag, NSpin, NEmpty,
  type DataTableColumns,
} from 'naive-ui'
import { Bar } from 'vue-chartjs'
import {
  Chart as ChartJS, Title, Tooltip, Legend, BarElement, CategoryScale, LinearScale,
} from 'chart.js'
import { useI18n } from 'vue-i18n'
import {
  getAcquisitionSummary, getAcquisitionRecent, getAcquisitionTrend,
  type AcquisitionSummary, type AcquisitionRecentItem, type AcquisitionTrendPoint,
} from '@/api/library'

ChartJS.register(Title, Tooltip, Legend, BarElement, CategoryScale, LinearScale)

const { t } = useI18n()

const summary = ref<AcquisitionSummary | null>(null)
const recent = ref<AcquisitionRecentItem[]>([])
const trend = ref<AcquisitionTrendPoint[]>([])
const loading = ref(false)
const stateFilter = ref<string | null>(null)

const KPI_KEYS = ['queued', 'downloading', 'completed', 'stalled', 'failed'] as const
const FUNNEL_KEYS = ['queued', 'downloading', 'completed'] as const

const stateOptions = computed(() => [
  { label: t('library.allStates'), value: null },
  ...['queued', 'downloading', 'completed', 'in_library', 'stalled', 'failed'].map((s) => ({
    label: t(`library.state.${s}`),
    value: s,
  })),
])

const funnelMax = computed(() =>
  Math.max(1, ...FUNNEL_KEYS.map((k) => summary.value?.[k] ?? 0)),
)

function stateTagType(state: string): 'default' | 'info' | 'success' | 'warning' | 'error' {
  return (
    {
      queued: 'default', downloading: 'info', completed: 'success',
      in_library: 'success', stalled: 'warning', failed: 'error',
    } as const
  )[state] ?? 'default'
}

const columns = computed<DataTableColumns<AcquisitionRecentItem>>(() => [
  {
    title: t('library.col.videoCode'),
    key: 'video_code',
    render: (row) =>
      h('span', { style: 'font-family: monospace' }, row.video_code ?? row.qb_hash.slice(0, 8)),
  },
  { title: t('library.col.category'), key: 'category', render: (row) => row.category ?? '—' },
  {
    title: t('library.col.state'),
    key: 'state',
    render: (row) =>
      h(NTag, { size: 'small', round: true, type: stateTagType(row.state) }, () =>
        t(`library.state.${row.state}`),
      ),
  },
  { title: t('library.col.queuedAt'), key: 'queued_at', render: (row) => row.queued_at ?? '—' },
  { title: t('library.col.completedAt'), key: 'completed_at', render: (row) => row.completed_at ?? '—' },
])

const trendChartData = computed(() => ({
  labels: trend.value.map((p) => p.date),
  datasets: [
    { label: t('library.state.completed'), data: trend.value.map((p) => p.completed), backgroundColor: '#18a058' },
    { label: t('library.state.stalled'), data: trend.value.map((p) => p.stalled), backgroundColor: '#f0a020' },
    { label: t('library.state.failed'), data: trend.value.map((p) => p.failed), backgroundColor: '#d03050' },
  ],
}))
const trendChartOptions = {
  responsive: true,
  maintainAspectRatio: false,
  scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true } },
}

async function fetchRecent() {
  recent.value = await getAcquisitionRecent({ state: stateFilter.value, limit: 50 })
}

async function fetchAll() {
  loading.value = true
  try {
    const [s, r, tr] = await Promise.all([
      getAcquisitionSummary(),
      getAcquisitionRecent({ state: stateFilter.value, limit: 50 }),
      getAcquisitionTrend('30d'),
    ])
    summary.value = s
    recent.value = r
    trend.value = tr
  } finally {
    loading.value = false
  }
}

watch(stateFilter, () => void fetchRecent())
onMounted(() => void fetchAll())
</script>

<template>
  <NSpin :show="loading">
    <!-- KPI cards -->
    <NGrid :cols="5" :x-gap="12" :y-gap="12" responsive="screen">
      <NGi v-for="k in KPI_KEYS" :key="k" span="5 s:5 m:1">
        <NCard size="small">
          <NStatistic :label="t(`library.kpi.${k}`)" :value="summary?.[k] ?? 0" />
        </NCard>
      </NGi>
    </NGrid>

    <!-- Funnel -->
    <NCard size="small" :title="t('library.funnel')" class="block">
      <div class="funnel">
        <div
          v-for="k in FUNNEL_KEYS"
          :key="k"
          class="funnel-stage"
          :style="{ flexGrow: (summary?.[k] ?? 0) / funnelMax + 0.15 }"
        >
          <div class="funnel-bar" :class="`s-${k}`">{{ summary?.[k] ?? 0 }}</div>
          <div class="funnel-label">{{ t(`library.state.${k}`) }}</div>
        </div>
      </div>
    </NCard>

    <!-- Trend -->
    <NCard size="small" :title="t('library.trend')" class="block">
      <div class="chart-wrap">
        <Bar v-if="trend.length" :data="trendChartData" :options="trendChartOptions" />
        <NEmpty v-else :description="t('library.comingSoon')" />
      </div>
    </NCard>

    <!-- Recent table -->
    <NCard size="small" :title="t('library.recent')" class="block">
      <NSelect
        v-model:value="stateFilter"
        :options="stateOptions"
        clearable
        class="state-filter"
      />
      <NDataTable :columns="columns" :data="recent" :bordered="false" size="small" />
    </NCard>
  </NSpin>
</template>

<style scoped>
.block {
  margin-top: 16px;
}
.funnel {
  display: flex;
  align-items: flex-end;
  gap: 8px;
}
.funnel-stage {
  flex-basis: 0;
  text-align: center;
}
.funnel-bar {
  color: #fff;
  padding: 16px 8px;
  border-radius: 6px;
  font-weight: 700;
}
.s-queued { background: #909399; }
.s-downloading { background: #2080f0; }
.s-completed { background: #18a058; }
.funnel-label {
  margin-top: 6px;
  font-size: 12px;
}
.chart-wrap {
  height: 260px;
}
.state-filter {
  max-width: 220px;
  margin-bottom: 12px;
}
</style>
```

- [ ] **Step 3: Typecheck**

Run: `npm run typecheck`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add src/pages/library/LibraryPage.vue src/pages/library/AcquisitionView.vue
git commit -m "feat(web): add Library page shell + Acquisition view (ADR-034 FE-1)"
```

---

## Task 12: Frontend component test + full verification

**Files (in `<WEB>`):**
- Create: `tests/unit/acquisition-view.spec.ts`

- [ ] **Step 1: Write the component test**

```typescript
// tests/unit/acquisition-view.spec.ts
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'

vi.mock('@/api/library', () => ({
  getAcquisitionSummary: vi.fn(async () => ({
    queued: 5, downloading: 3, completed: 100, stalled: 1, failed: 2, total: 111,
  })),
  getAcquisitionRecent: vi.fn(async () => [
    {
      qb_hash: 'abc12345', video_code: 'ABC-123', href: '/v/abc', category: 'subtitle',
      state: 'downloading', queued_at: '2026-06-01T00:00:00.000000Z',
      completed_at: null, last_seen_at: '2026-06-01T00:00:00.000000Z',
    },
  ]),
  getAcquisitionTrend: vi.fn(async () => [
    { date: '2026-06-04', completed: 1, stalled: 0, failed: 0 },
  ]),
}))

// Stub the chart so jsdom never touches a real <canvas> context.
vi.mock('vue-chartjs', () => ({ Bar: { name: 'Bar', render: () => null } }))

import AcquisitionView from '@/pages/library/AcquisitionView.vue'

const i18n = createI18n({
  legacy: false,
  locale: 'en',
  messages: {
    en: {
      library: {
        allStates: 'All states', funnel: 'Funnel', trend: 'Trend', recent: 'Recent',
        comingSoon: 'Coming soon',
        kpi: { queued: 'Queued', downloading: 'Downloading', completed: 'Completed', stalled: 'Stalled', failed: 'Failed' },
        state: { queued: 'Queued', downloading: 'Downloading', completed: 'Completed', in_library: 'In library', stalled: 'Stalled', failed: 'Failed' },
        col: { videoCode: 'Code', category: 'Category', state: 'State', queuedAt: 'Queued', completedAt: 'Completed', lastSeenAt: 'Last seen' },
      },
    },
  },
})

describe('AcquisitionView', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('renders KPI values and the recent table row', async () => {
    const wrapper = mount(AcquisitionView, { global: { plugins: [i18n] } })
    await flushPromises()
    expect(wrapper.text()).toContain('Completed')
    expect(wrapper.text()).toContain('ABC-123')
  })
})
```

- [ ] **Step 2: Run the component test**

Run: `npm run test:unit -- acquisition-view`
Expected: PASS (1 passed)

- [ ] **Step 3: Full Web-repo verification**

Run, expecting each to pass:
```bash
npm run lint
npm run typecheck
npm run typecheck:server
npm run test:unit
npx vitest run server/__tests__/
```
Expected: lint clean; both typechecks clean; all unit tests pass; all server tests pass (including the contract + library + capabilities suites).

- [ ] **Step 4: Full CICD-repo verification**

Back in `<CICD>`, run:
```bash
pytest tests/unit/test_library_query_builders.py tests/unit/test_query_contract_golden.py tests/integration/test_library_endpoints.py tests/integration/test_capabilities_closed_loop.py tests/integration/test_openapi_response_shapes.py -v
git diff --check
```
Expected: all pass; no whitespace errors.

- [ ] **Step 5: Commit + open the paired PRs**

```bash
git add tests/unit/acquisition-view.spec.ts
git commit -m "test(web): cover Acquisition view render (ADR-034 FE-1)"
```

Open two PRs and cross-link them in each description:
- `<CICD>`: `feat(api): library acquisition read endpoints + closed_loop (ADR-034 FE-1)`
- `<WEB>`: `feat(web): Library page + Worker parity for acquisition (ADR-034 FE-1)`

---

## Definition of Done

- [ ] Three `GET /api/library/acquisition/{summary,recent,trend}` endpoints serve identical shapes from **both** backends; the query-contract golden conformance test is green on both sides.
- [ ] `GET /api/capabilities` returns `features.closed_loop` (bool) in both backends; the Library nav entry is hidden when it is false.
- [ ] The Library page renders the Acquisition tab (funnel + 5 KPI cards + recent table with a state filter + trend chart); Ownership/Consumption are disabled placeholders.
- [ ] All new strings exist in both `en.json` and `zh-CN.json`.
- [ ] `docs/api/openapi.json` and `docs/api/contract/query-builders.golden.json` regenerated and committed; the Web repo vendored both.
- [ ] No mutations were added (ADR-034 Non-Goals respected).

## Out of scope (deferred — do not implement here)

- Ownership / Consumption views (IMP-ADR034-02 / -03; they need `OwnershipLedger` / `ConsumptionSignal`).
- Any mutation (`Re-queue` / `Dismiss` / `Open in qB`) — needs the LAN-connected Python backend (ADR-034 Non-Goals).
- Realtime / websockets — the page fetches on load and on filter change only.
