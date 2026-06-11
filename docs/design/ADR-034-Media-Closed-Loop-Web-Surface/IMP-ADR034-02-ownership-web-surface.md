# ADR-034 FE Phase 2 — Ownership Web Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Related:** [ADR-034](ADR-034-media-closed-loop-web-surface.md) — FE Phase 2. Pattern = [IMP-ADR034-01](IMP-ADR034-01-acquisition-web-surface.md).

**Status:** ✅ Implemented 2026-06-10 (cross-repo: main branch claude/adr034-fe-p2p3-ownership-consumption + web branch claude/adr034-fe-p2p3; subagent-driven; byte-parity golden + Python/web tests green).

**Cross-repo:** MAIN (Python read API) + WEB (TS Worker + Vue). Per ADR-018 dual-backend parity.

**Goal:** Make ADR-033 Phase 2 ownership-ledger data visible — enable the disabled **Ownership** tab in the Library page with two read-only `GET /api/library/ownership/{summary,recent}` endpoints implemented at full parity in both the Python FastAPI backend and the TypeScript Cloudflare Worker, conforming to the same query-contract golden pipeline already established by FE-1.

**Architecture:** D1 `OwnershipLedger` (operations DB, `OWNERSHIP_SOURCES = ("qb", "nas", "gdrive", "pikpak")`, grain `(video_code, source, category)`) is the single source. Python backend is the contract source of truth; pure SQL builders are pinned to `docs/api/contract/query-builders.golden.json`; the Worker vendors the golden and mirrors SQL byte-for-byte (enforced by `query-contract.test.ts`). No trend endpoint (DECISION: `observed_at` is a freshness stamp, not a first-seen date; a time-series from it would mislead and require a migration). Ownership view replaces the acquisition funnel+trend pattern with: total-owned KPI + per-source KPI cards + a static per-source breakdown bar (horizontal Bar chart from `summary.by_source`) + a recent table with a `present/swept` indicator and source filter. Read-only only — no mutations.

**Tech Stack:** Python 3 / FastAPI / Pydantic / pytest (CICD repo); TypeScript / Hono / Cloudflare D1 / vitest + `cloudflare:test` (Web repo, server); Vue 3 / Naive UI / Pinia / vue-i18n / vue-chartjs / vitest + `@vue/test-utils` (Web repo, SPA).

**Cross-repo boundary.** This plan spans **two git repositories**:

- **CICD repo** — root referenced as `<CICD>` (the worktree root, `/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/pedantic-hamilton-9d8be3`).
- **Web repo** — root referenced as `<WEB>` (`/Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web`). Web steps start with `cd /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web`.

Seam between repos: two generated files copied from `<CICD>/docs/api/` into `<WEB>` by vendor scripts. **Commit the two repos separately.**

---

## File Structure

### CICD repo (Python backend = contract source of truth)

| Path | Create/Modify | Responsibility |
| --- | --- | --- |
| `apps/api/schemas/library_ownership.py` | Create | `OwnershipSummary`, `OwnershipSourceBreakdown`, `OwnershipRecentItem` response models |
| `apps/api/routers/library_ownership_query_builders.py` | Create | Pure SQL builders (summary-per-source / summary-distinct / recent) — parity unit |
| `apps/api/routers/library.py` | Modify | Add ownership summary + recent endpoints; import new builders + schemas |
| `apps/cli/ops/query_contract_cases.py` | Modify | Add `OWNERSHIP_{SUMMARY,RECENT}_QUERY_CASES` |
| `apps/cli/ops/dump_query_contract.py` | Modify | Register new builders + emit their cases |
| `docs/api/contract/query-builders.golden.json` | Regenerate | `python3 -m apps.cli.ops.dump_query_contract` |
| `docs/api/openapi.json` | Regenerate | `python3 -m apps.cli.ops.dump_openapi` |
| `tests/unit/test_library_ownership_query_builders.py` | Create | Builder SQL + bindings unit tests |
| `tests/integration/test_library_ownership_endpoints.py` | Create | Endpoint behaviour (auth, summary, recent+filter, 400) |

### Web repo — TS Worker (`<WEB>/server/`)

| Path | Create/Modify | Responsibility |
| --- | --- | --- |
| `server/__tests__/fixtures/query-builders.golden.json` | Regenerate (vendored) | Copy from `<CICD>/docs/api/contract/query-builders.golden.json` |
| `src/types/api.gen.ts` + `tmp/openapi.json` | Regenerate (vendored) | `OPENAPI_PATH=<CICD>/docs/api/openapi.json node scripts/fetch-openapi.mjs` |
| `server/routes/library_ownership.ts` | Create | Hono route + TS SQL builders (mirror of Python) |
| `server/app.ts` | Modify | Mount `libraryOwnershipRoutes` at `/api/library` (same prefix as `libraryRoutes`) |
| `server/__tests__/query-contract.test.ts` | Modify | Add `ownership_*` entries to `RUN` registry |
| `server/__tests__/library-ownership-routes.test.ts` | Create | Seed `OwnershipLedger` in D1 + assert summary/recent shapes |

### Web repo — Vue SPA (`<WEB>/src/`)

| Path | Create/Modify | Responsibility |
| --- | --- | --- |
| `src/api/library_ownership.ts` | Create | Typed axios client for the two endpoints |
| `src/pages/library/OwnershipView.vue` | Create | Total-owned KPI + per-source KPI cards + source breakdown Bar + recent NDataTable |
| `src/pages/library/LibraryPage.vue` | Modify | Enable ownership tab (`disabled` → remove attribute); import `OwnershipView` |
| `src/i18n/locales/en.json` | Modify | Add `library.ownership.*` keys |
| `src/i18n/locales/zh-CN.json` | Modify | Paired zh translations |
| `src/i18n/locales/ja.json` | Modify | Paired ja translations (i18n-parity test enforces three locales) |
| `tests/unit/ownership-view.spec.ts` | Create | Mock `@/api/library_ownership`, assert KPI + table render |

**Naming contract:**

- Python builders: `build_ownership_summary_by_source_query()` → `tuple[str, list]`; `build_ownership_summary_distinct_query()` → `tuple[str, list]`; `build_ownership_recent_query(*, source=None, limit=50, offset=0)` → `tuple[str, list]`.
- TS mirrors: `buildOwnershipSummaryBySourceQuery()`, `buildOwnershipSummaryDistinctQuery()`, `buildOwnershipRecentQuery({source, limit, offset})` — each returns `{ sql, bindings }`.
- Contract builder ids: `ownership_summary_by_source_query`, `ownership_summary_distinct_query`, `ownership_recent_query`.
- Response shapes:
  - Summary: `{ total_owned_titles: int, by_source: list[{ source, unique_titles, present_rows, total_bytes }] }` (Python `OwnershipSummary` / `OwnershipSourceBreakdown`).
  - Recent item: `{ video_code, source, category, path, size, present, observed_at }` (Python `OwnershipRecentItem`).

**Grounding facts (verified against landed ADR-033 Phase 2 code):**

- `OwnershipLedger` lives in the **operations** DB; reach it with `get_db(OPERATIONS_DB_PATH)` (Python) / `c.env.OPERATIONS_DB` (Worker). Grain: `(video_code, source, category)` — unique constraint on all three. Columns: `video_code TEXT`, `source TEXT`, `category TEXT NOT NULL DEFAULT ''`, `path TEXT`, `size INTEGER`, `present INTEGER NOT NULL DEFAULT 1`, `observed_at TEXT`. `present=1` means file present on most recent scan; `present=0` means swept absent.
- Valid sources from `OWNERSHIP_SOURCES` (in `javdb/ops/reconcile/models.py`): `("qb", "nas", "gdrive", "pikpak")`.
- `observed_at` uses `utc_now_iso()` → `YYYY-MM-DDTHH:MM:SS.ffffffZ`. Not a first-seen date — refreshed on each reconcile pass (same freshness-stamp semantics as `last_seen_at` in AcquisitionOutcome). **No trend endpoint for this reason.**
- Summary requires two separate SQL statements:
  1. Per-source `GROUP BY source` to get `unique_titles`, `present_rows`, `total_bytes` per source.
  2. Cross-source `COUNT(DISTINCT video_code) WHERE present=1` for the global `total_owned_titles`.
  The Python route assembles both into one `OwnershipSummary` response object.
- `OWNERSHIP_SOURCES` is imported from `javdb.ops.reconcile.models` (same as `ACQUISITION_STATES` in FE-1).

---

## Task 1: Python response schemas (TDD)

**Files (in `<CICD>`):**
- Create: `apps/api/schemas/library_ownership.py`

- [ ] **Step 1: Write the response models**

```python
# apps/api/schemas/library_ownership.py
"""Pydantic schemas for Library ownership endpoints (ADR-034 FE-2)."""

from __future__ import annotations

from pydantic import BaseModel


class OwnershipSourceBreakdown(BaseModel):
    """Per-source counts for GET /api/library/ownership/summary."""

    source: str
    unique_titles: int
    present_rows: int
    total_bytes: int


class OwnershipSummary(BaseModel):
    """KPI + per-source breakdown for GET /api/library/ownership/summary."""

    total_owned_titles: int
    by_source: list[OwnershipSourceBreakdown]


class OwnershipRecentItem(BaseModel):
    """One OwnershipLedger row for GET /api/library/ownership/recent."""

    video_code: str
    source: str
    category: str
    path: str | None = None
    size: int | None = None
    present: int
    observed_at: str | None = None
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -c "from apps.api.schemas.library_ownership import OwnershipSummary, OwnershipRecentItem; print('ok')"`

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add apps/api/schemas/library_ownership.py
git commit -m "feat(api): add library ownership response schemas (ADR-034 FE-2)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Python query builders (pure SQL, TDD)

**Files (in `<CICD>`):**
- Create: `apps/api/routers/library_ownership_query_builders.py`
- Create: `tests/unit/test_library_ownership_query_builders.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_library_ownership_query_builders.py
"""TDD tests for library ownership query builders (ADR-034 FE-2)."""

from apps.api.routers.library_ownership_query_builders import (
    build_ownership_recent_query,
    build_ownership_summary_by_source_query,
    build_ownership_summary_distinct_query,
)


def test_summary_by_source_groups_by_source():
    sql, bindings = build_ownership_summary_by_source_query()
    assert bindings == []
    assert "FROM OwnershipLedger" in sql
    assert "GROUP BY source" in sql
    assert "COUNT(DISTINCT video_code)" in sql
    assert "COALESCE(SUM(size), 0)" in sql
    assert "present = 1" in sql


def test_summary_distinct_counts_unique_present_titles():
    sql, bindings = build_ownership_summary_distinct_query()
    assert bindings == []
    assert "COUNT(DISTINCT video_code)" in sql
    assert "FROM OwnershipLedger" in sql
    assert "WHERE present = 1" in sql


def test_recent_without_source_omits_where():
    sql, bindings = build_ownership_recent_query(source=None, limit=50, offset=0)
    assert "WHERE" not in sql
    assert "ORDER BY observed_at DESC" in sql
    assert bindings == [50, 0]


def test_recent_with_source_binds_source_first():
    sql, bindings = build_ownership_recent_query(source="qb", limit=20, offset=10)
    assert "WHERE source = ?" in sql
    assert bindings == ["qb", 20, 10]


def test_recent_selects_all_required_columns():
    sql, _ = build_ownership_recent_query(source=None, limit=50, offset=0)
    for col in ("video_code", "source", "category", "path", "size", "present", "observed_at"):
        assert col in sql
```

- [ ] **Step 2: Run failing tests**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_library_ownership_query_builders.py -q`

Expected: FAIL — `ModuleNotFoundError: apps.api.routers.library_ownership_query_builders`

- [ ] **Step 3: Write the builders**

```python
# apps/api/routers/library_ownership_query_builders.py
"""Pure SQL builders for the Library ownership endpoints (ADR-034 FE-2).

Dual-backend parity unit (ADR-018): the TS Worker mirrors each string
byte-for-byte and the query-contract golden pins them. Summary uses TWO
separate queries (per-source breakdown + cross-source distinct) because a
single GROUP BY cannot simultaneously compute per-source and cross-source
distinct counts without a subquery. The route assembles both into one
OwnershipSummary response.
"""

from __future__ import annotations


def build_ownership_summary_by_source_query() -> tuple[str, list]:
    """Per-source breakdown: unique present titles, present rows, total bytes."""
    sql = (
        "SELECT source, "
        "COUNT(DISTINCT video_code) AS unique_titles, "
        "COALESCE(SUM(CASE WHEN present = 1 THEN 1 ELSE 0 END), 0) AS present_rows, "
        "COALESCE(SUM(size), 0) AS total_bytes "
        "FROM OwnershipLedger "
        "WHERE present = 1 "
        "GROUP BY source "
        "ORDER BY source"
    )
    return sql, []


def build_ownership_summary_distinct_query() -> tuple[str, list]:
    """Cross-source total: distinct video codes with at least one present row."""
    sql = (
        "SELECT COUNT(DISTINCT video_code) AS total_owned_titles "
        "FROM OwnershipLedger "
        "WHERE present = 1"
    )
    return sql, []


def build_ownership_recent_query(
    *, source: str | None = None, limit: int = 50, offset: int = 0
) -> tuple[str, list]:
    """Newest-first page of ownership rows; optional source filter."""
    bindings: list[str | int] = []
    where = ""
    if source is not None:
        where = "WHERE source = ? "
        bindings.append(source)
    sql = (
        "SELECT video_code, source, category, path, size, present, observed_at "
        "FROM OwnershipLedger "
        f"{where}"
        "ORDER BY observed_at DESC "
        "LIMIT ? OFFSET ?"
    )
    bindings.extend([limit, offset])
    return sql, bindings


__all__ = [
    "build_ownership_recent_query",
    "build_ownership_summary_by_source_query",
    "build_ownership_summary_distinct_query",
]
```

- [ ] **Step 4: Run tests — confirm green**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_library_ownership_query_builders.py -q`

Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add apps/api/routers/library_ownership_query_builders.py tests/unit/test_library_ownership_query_builders.py
git commit -m "feat(api): add pure SQL builders for library ownership (ADR-034 FE-2)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Python route handlers (TDD)

**Files (in `<CICD>`):**
- Modify: `apps/api/routers/library.py`
- Create: `tests/integration/test_library_ownership_endpoints.py`

The existing `library.py` has `router = APIRouter(prefix="/api/library", tags=["library"])` (line 27). Add the two ownership endpoints to the **same router** — no new router object needed.

- [ ] **Step 1: Write the failing integration tests**

```python
# tests/integration/test_library_ownership_endpoints.py
"""Integration tests for library ownership endpoints (ADR-034 FE-2)."""

import sqlite3

import pytest


@pytest.fixture(autouse=True, scope="module")
def _ensure_db_initialized():
    from javdb.storage.db import init_db
    init_db()


@pytest.fixture
def seeded_ownership(_isolate_sqlite):
    """Seed OwnershipLedger rows; return the patched db path."""
    db_path = _isolate_sqlite
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO OwnershipLedger
                (video_code, source, category, path, size, present, observed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("AAA-001", "qb",     "subtitle",    "/dl/AAA-001.mkv", 2_147_483_648, 1, "2026-06-01T00:00:00.000000Z"),
                ("BBB-002", "qb",     "no_subtitle", "/dl/BBB-002.mkv", 1_073_741_824, 1, "2026-06-02T00:00:00.000000Z"),
                ("CCC-003", "gdrive", "subtitle",    "/gd/CCC-003.mkv", 4_294_967_296, 1, "2026-06-03T00:00:00.000000Z"),
                # present=0 → swept, must NOT count toward total_owned_titles
                ("DDD-004", "nas",    "subtitle",    "/nas/DDD-004.mkv", 2_000_000_000, 0, "2026-05-30T00:00:00.000000Z"),
            ],
        )
        conn.commit()
    return db_path


def test_summary_total_and_by_source(admin_client, seeded_ownership):
    r = admin_client.get("/api/library/ownership/summary")
    assert r.status_code == 200
    body = r.json()
    # DDD-004 present=0 excluded → 3 distinct titles owned
    assert body["total_owned_titles"] == 3
    by_source = {s["source"]: s for s in body["by_source"]}
    assert by_source["qb"]["unique_titles"] == 2
    assert by_source["gdrive"]["unique_titles"] == 1
    # nas row is present=0 → not in by_source
    assert "nas" not in by_source


def test_summary_empty_table_is_zero(admin_client, _isolate_sqlite):
    r = admin_client.get("/api/library/ownership/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["total_owned_titles"] == 0
    assert body["by_source"] == []


def test_recent_returns_rows_newest_first(admin_client, seeded_ownership):
    r = admin_client.get("/api/library/ownership/recent")
    assert r.status_code == 200
    items = r.json()
    # All 4 rows (including swept); ordered by observed_at DESC
    assert items[0]["video_code"] == "CCC-003"
    assert set(items[0].keys()) == {
        "video_code", "source", "category", "path", "size", "present", "observed_at",
    }


def test_recent_source_filter(admin_client, seeded_ownership):
    r = admin_client.get("/api/library/ownership/recent", params={"source": "qb"})
    assert r.status_code == 200
    items = r.json()
    assert all(i["source"] == "qb" for i in items)
    assert len(items) == 2


def test_recent_rejects_unknown_source(admin_client, seeded_ownership):
    r = admin_client.get("/api/library/ownership/recent", params={"source": "unknown_src"})
    assert r.status_code == 400
    body = r.json()
    assert body["detail"]["error"]["code"] == "library.invalid_source"


def test_endpoints_require_auth(anon_client):
    for path in (
        "/api/library/ownership/summary",
        "/api/library/ownership/recent",
    ):
        assert anon_client.get(path).status_code in (401, 403)
```

- [ ] **Step 2: Run failing tests**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/integration/test_library_ownership_endpoints.py -q`

Expected: FAIL — 404 (endpoints not yet in router)

- [ ] **Step 3: Add ownership endpoints to `apps/api/routers/library.py`**

Add to the existing imports at the top of `library.py`:

```python
from apps.api.routers.library_ownership_query_builders import (
    build_ownership_recent_query,
    build_ownership_summary_by_source_query,
    build_ownership_summary_distinct_query,
)
from apps.api.schemas.library_ownership import (
    OwnershipRecentItem,
    OwnershipSourceBreakdown,
    OwnershipSummary,
)
from javdb.ops.reconcile.models import OWNERSHIP_SOURCES
```

Add after the existing `RECENT_COLS` / `_SUMMARY_KEYS` constants:

```python
_OWNERSHIP_SOURCES = OWNERSHIP_SOURCES  # ("qb", "nas", "gdrive", "pikpak")
_OWNERSHIP_RECENT_COLS = (
    "video_code", "source", "category", "path", "size", "present", "observed_at",
)
```

Add the two new route handlers (append to the bottom of `library.py`):

```python
@router.get("/ownership/summary", response_model=OwnershipSummary)
def ownership_summary(_user=Depends(_require_auth)) -> OwnershipSummary:
    from javdb.storage.db import OPERATIONS_DB_PATH, get_db
    sql_per_source, b1 = build_ownership_summary_by_source_query()
    sql_distinct, b2 = build_ownership_summary_distinct_query()
    with get_db(OPERATIONS_DB_PATH) as conn:
        per_source_rows = conn.execute(sql_per_source, b1).fetchall()
        distinct_row = conn.execute(sql_distinct, b2).fetchone()
    total = (distinct_row["total_owned_titles"] if distinct_row else 0) or 0
    by_source = [
        OwnershipSourceBreakdown(
            source=r["source"],
            unique_titles=r["unique_titles"],
            present_rows=r["present_rows"],
            total_bytes=r["total_bytes"],
        )
        for r in per_source_rows
    ]
    return OwnershipSummary(total_owned_titles=total, by_source=by_source)


@router.get(
    "/ownership/recent",
    response_model=list[OwnershipRecentItem],
    responses=_domain_400_response("Invalid source filter (library.invalid_source)"),
)
def ownership_recent(
    source: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _user=Depends(_require_auth),
) -> list[OwnershipRecentItem]:
    if source is not None and source not in _OWNERSHIP_SOURCES:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "library.invalid_source", "message": f"Invalid source: {source}"}},
        )
    from javdb.storage.db import OPERATIONS_DB_PATH, get_db
    sql, bindings = build_ownership_recent_query(source=source, limit=limit, offset=offset)
    with get_db(OPERATIONS_DB_PATH) as conn:
        rows = conn.execute(sql, bindings).fetchall()
    return [OwnershipRecentItem(**{c: r[c] for c in _OWNERSHIP_RECENT_COLS}) for r in rows]
```

- [ ] **Step 4: Run tests — confirm green**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/integration/test_library_ownership_endpoints.py -q`

Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add apps/api/routers/library.py tests/integration/test_library_ownership_endpoints.py
git commit -m "feat(api): add read-only library ownership endpoints (ADR-034 FE-2)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Pin builders in the query contract + regenerate artifacts

**Files (in `<CICD>`):**
- Modify: `apps/cli/ops/query_contract_cases.py`
- Modify: `apps/cli/ops/dump_query_contract.py`
- Regenerate: `docs/api/contract/query-builders.golden.json`
- Regenerate: `docs/api/openapi.json`

- [ ] **Step 1: Add contract cases to `apps/cli/ops/query_contract_cases.py`**

Append after `LIBRARY_TREND_QUERY_CASES` (the last block in the file):

```python
OWNERSHIP_SUMMARY_BY_SOURCE_QUERY_CASES = [
    ("ownership_summary_by_source_query", "all", {}),
]

OWNERSHIP_SUMMARY_DISTINCT_QUERY_CASES = [
    ("ownership_summary_distinct_query", "all", {}),
]

OWNERSHIP_RECENT_QUERY_CASES = [
    ("ownership_recent_query", "no_source", {"source": None, "limit": 50, "offset": 0}),
    ("ownership_recent_query", "with_source", {"source": "qb", "limit": 20, "offset": 10}),
]
```

- [ ] **Step 2: Register builders in `apps/cli/ops/dump_query_contract.py`**

Add to the imports from `query_contract_cases`:

```python
from apps.cli.ops.query_contract_cases import (  # noqa: E402
    LIBRARY_RECENT_QUERY_CASES,
    LIBRARY_SUMMARY_QUERY_CASES,
    LIBRARY_TREND_QUERY_CASES,
    MOVIE_COUNT_CASES,
    MOVIE_FILTER_CASES,
    OWNERSHIP_RECENT_QUERY_CASES,
    OWNERSHIP_SUMMARY_BY_SOURCE_QUERY_CASES,
    OWNERSHIP_SUMMARY_DISTINCT_QUERY_CASES,
    SESSION_QUERY_CASES,
    STATS_TREND_QUERY_CASES,
    TORRENT_COUNT_CASES,
    TORRENT_FILTER_CASES,
    normalize_sql,
)
```

Add a builders import (after the existing `library_query_builders` import):

```python
from apps.api.routers.library_ownership_query_builders import (  # noqa: E402
    build_ownership_recent_query,
    build_ownership_summary_by_source_query,
    build_ownership_summary_distinct_query,
)
```

Add the three to `_BUILDERS`:

```python
    "ownership_summary_by_source_query": build_ownership_summary_by_source_query,
    "ownership_summary_distinct_query": build_ownership_summary_distinct_query,
    "ownership_recent_query": build_ownership_recent_query,
```

Add the cases to the loop in `main()`:

```python
        *OWNERSHIP_SUMMARY_BY_SOURCE_QUERY_CASES,
        *OWNERSHIP_SUMMARY_DISTINCT_QUERY_CASES,
        *OWNERSHIP_RECENT_QUERY_CASES,
```

- [ ] **Step 3: Regenerate the golden**

Run (from `<CICD>`): `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m apps.cli.ops.dump_query_contract`

Expected: `wrote …/docs/api/contract/query-builders.golden.json (N cases)` where N grew by 4.

- [ ] **Step 4: Regenerate openapi**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m apps.cli.ops.dump_openapi`

Confirm new paths:

Run: `python3 -c "import json; s=json.load(open('docs/api/openapi.json'))['paths']; print([p for p in s if '/api/library/ownership' in p])"`

Expected: `['/api/library/ownership/summary', '/api/library/ownership/recent']`

- [ ] **Step 5: Run the golden conformance gate**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_query_contract_golden.py -q`

Expected: PASS — all golden cases match builders (including the 4 new ownership cases).

- [ ] **Step 6: Commit**

```bash
git add apps/cli/ops/query_contract_cases.py apps/cli/ops/dump_query_contract.py \
    docs/api/contract/query-builders.golden.json docs/api/openapi.json
git commit -m "feat(api): pin ownership query builders to contract golden + openapi (ADR-034 FE-2)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

> **End of CICD-repo changes.** Remaining tasks are in `<WEB>`. Open the CICD PR now (or after Task 7's full verification). Reference the Web PR in the description.

---

## Task 5: Vendor artifacts into the Web repo + TS parity builders

**Files (in `<WEB>`):**
- Regenerate: `server/__tests__/fixtures/query-builders.golden.json`
- Regenerate: `src/types/api.gen.ts`, `tmp/openapi.json`
- Create: `server/routes/library_ownership.ts`
- Modify: `server/app.ts`
- Modify: `server/__tests__/query-contract.test.ts`

All commands run from `cd /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web`.

- [ ] **Step 1: Vendor the query-contract golden**

```bash
cd /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web
QUERY_GOLDEN_PATH="/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/pedantic-hamilton-9d8be3/docs/api/contract/query-builders.golden.json" node scripts/fetch-query-golden.mjs
```

Expected: `[fetch-query-golden] wrote …/server/__tests__/fixtures/query-builders.golden.json`

- [ ] **Step 2: Vendor openapi + regenerate TS types**

```bash
OPENAPI_PATH="/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/pedantic-hamilton-9d8be3/docs/api/openapi.json" node scripts/fetch-openapi.mjs
```

Confirm the new schemas appear in `src/types/api.gen.ts`:

```bash
grep -n "OwnershipSummary\|OwnershipRecentItem\|OwnershipSourceBreakdown" src/types/api.gen.ts
```

Expected: three matching lines.

- [ ] **Step 3: Confirm contract test is RED before writing TS builders**

```bash
npm test -- --reporter=verbose server/__tests__/query-contract.test.ts 2>&1 | grep "ownership"
```

Expected: FAIL — `no TS builder mapped for 'ownership_summary_by_source_query'` (etc.).

- [ ] **Step 4: Write the TS route + builders**

```typescript
// server/routes/library_ownership.ts
import { Hono } from "hono";
import { HTTPException } from "hono/http-exception";
import type { Env } from "../env";
import type { JwtPayload } from "../services/jwt";

type LibEnv = { Bindings: Env; Variables: { user: JwtPayload } };

export const libraryOwnershipRoutes = new Hono<LibEnv>();

const OWNERSHIP_SOURCES = ["qb", "nas", "gdrive", "pikpak"];

// ── Pure SQL builders (byte-for-byte mirror of Python; pinned by the golden) ──

export function buildOwnershipSummaryBySourceQuery(): {
  sql: string;
  bindings: (string | number)[];
} {
  const sql =
    "SELECT source, " +
    "COUNT(DISTINCT video_code) AS unique_titles, " +
    "COALESCE(SUM(CASE WHEN present = 1 THEN 1 ELSE 0 END), 0) AS present_rows, " +
    "COALESCE(SUM(size), 0) AS total_bytes " +
    "FROM OwnershipLedger " +
    "WHERE present = 1 " +
    "GROUP BY source " +
    "ORDER BY source";
  return { sql, bindings: [] };
}

export function buildOwnershipSummaryDistinctQuery(): {
  sql: string;
  bindings: (string | number)[];
} {
  const sql =
    "SELECT COUNT(DISTINCT video_code) AS total_owned_titles " +
    "FROM OwnershipLedger " +
    "WHERE present = 1";
  return { sql, bindings: [] };
}

export function buildOwnershipRecentQuery(p: {
  source?: string | null;
  limit: number;
  offset: number;
}): { sql: string; bindings: (string | number)[] } {
  const bindings: (string | number)[] = [];
  let where = "";
  if (p.source != null) {
    where = "WHERE source = ? ";
    bindings.push(p.source);
  }
  const sql =
    "SELECT video_code, source, category, path, size, present, observed_at " +
    "FROM OwnershipLedger " +
    where +
    "ORDER BY observed_at DESC " +
    "LIMIT ? OFFSET ?";
  bindings.push(p.limit, p.offset);
  return { sql, bindings };
}

function badRequest(code: string, message: string): HTTPException {
  return new HTTPException(400, { message: JSON.stringify({ error: { code, message } }) });
}

// ── Routes (JWT auth inherited from app.use("/api/*", requireAuth())) ─────────

libraryOwnershipRoutes.get("/ownership/summary", async (c) => {
  const { sql: sqlPerSource, bindings: b1 } = buildOwnershipSummaryBySourceQuery();
  const { sql: sqlDistinct, bindings: b2 } = buildOwnershipSummaryDistinctQuery();
  const [perSourceResult, distinctResult] = await Promise.all([
    c.env.OPERATIONS_DB.prepare(sqlPerSource)
      .bind(...b1)
      .all<{ source: string; unique_titles: number; present_rows: number; total_bytes: number }>(),
    c.env.OPERATIONS_DB.prepare(sqlDistinct)
      .bind(...b2)
      .first<{ total_owned_titles: number }>(),
  ]);
  return c.json({
    total_owned_titles: distinctResult?.total_owned_titles ?? 0,
    by_source: perSourceResult.results ?? [],
  });
});

libraryOwnershipRoutes.get("/ownership/recent", async (c) => {
  const source = c.req.query("source") ?? null;
  if (source !== null && !OWNERSHIP_SOURCES.includes(source)) {
    throw badRequest("library.invalid_source", `Invalid source: ${source}`);
  }
  const limit = Math.max(1, Math.min(200, parseInt(c.req.query("limit") ?? "50", 10) || 50));
  const offset = Math.max(0, parseInt(c.req.query("offset") ?? "0", 10) || 0);
  const { sql, bindings } = buildOwnershipRecentQuery({ source, limit, offset });
  const { results } = await c.env.OPERATIONS_DB.prepare(sql)
    .bind(...bindings)
    .all<{
      video_code: string; source: string; category: string;
      path: string | null; size: number | null; present: number; observed_at: string | null;
    }>();
  return c.json(results ?? []);
});
```

- [ ] **Step 5: Mount in `server/app.ts`**

Add the import alongside the other route imports (after `libraryRoutes`):

```typescript
import { libraryOwnershipRoutes } from "./routes/library_ownership";
```

Mount it after `app.route("/api/library", libraryRoutes)` (line 81), inheriting `requireAuth()`:

```typescript
app.route("/api/library", libraryOwnershipRoutes);
```

- [ ] **Step 6: Map builders in the contract test**

In `server/__tests__/query-contract.test.ts`, add the import after the existing library import:

```typescript
import {
  buildOwnershipRecentQuery,
  buildOwnershipSummaryBySourceQuery,
  buildOwnershipSummaryDistinctQuery,
} from "../routes/library_ownership";
```

Add three entries to the `RUN` registry after the existing `library_trend_query` entry:

```typescript
  ownership_summary_by_source_query: () => buildOwnershipSummaryBySourceQuery(),
  ownership_summary_distinct_query: () => buildOwnershipSummaryDistinctQuery(),
  ownership_recent_query: (p) =>
    buildOwnershipRecentQuery({ source: p.source ?? null, limit: p.limit, offset: p.offset }),
```

- [ ] **Step 7: Run the contract test — confirm GREEN**

```bash
npm test -- --reporter=verbose server/__tests__/query-contract.test.ts 2>&1 | grep -E "ownership|PASS|FAIL"
```

Expected: all four new cases pass (`ownership_summary_by_source_query:all`, `ownership_summary_distinct_query:all`, `ownership_recent_query:no_source`, `ownership_recent_query:with_source`).

- [ ] **Step 8: Commit (vendored artifacts + route + contract mapping)**

```bash
git add server/__tests__/fixtures/query-builders.golden.json src/types/api.gen.ts tmp/openapi.json \
    server/routes/library_ownership.ts server/app.ts server/__tests__/query-contract.test.ts
git commit -m "feat(server): add ownership routes + TS builders at contract parity (ADR-034 FE-2)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: TS Worker route tests

**Files (in `<WEB>`):**
- Create: `server/__tests__/library-ownership-routes.test.ts`

```typescript
// server/__tests__/library-ownership-routes.test.ts
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
  expect(res.status).toBe(200);
  const data = (await res.json()) as { access_token?: string };
  expect(typeof data.access_token).toBe("string");
  return data.access_token as string;
}

async function seedOwnership(db: D1Database) {
  await db
    .prepare(
      `CREATE TABLE IF NOT EXISTS OwnershipLedger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_code TEXT NOT NULL, source TEXT NOT NULL, category TEXT NOT NULL DEFAULT '',
        path TEXT, size INTEGER, present INTEGER NOT NULL DEFAULT 1,
        observed_at TEXT,
        UNIQUE(video_code, source, category))`,
    )
    .run();
  await db.batch([
    db.prepare(
      "INSERT OR REPLACE INTO OwnershipLedger (video_code, source, category, path, size, present, observed_at) VALUES (?,?,?,?,?,?,?)",
    ).bind("AAA-001", "qb", "subtitle", "/dl/AAA-001.mkv", 2147483648, 1, "2026-06-01T00:00:00.000000Z"),
    db.prepare(
      "INSERT OR REPLACE INTO OwnershipLedger (video_code, source, category, path, size, present, observed_at) VALUES (?,?,?,?,?,?,?)",
    ).bind("CCC-003", "gdrive", "subtitle", "/gd/CCC-003.mkv", 4294967296, 1, "2026-06-03T00:00:00.000000Z"),
    db.prepare(
      "INSERT OR REPLACE INTO OwnershipLedger (video_code, source, category, path, size, present, observed_at) VALUES (?,?,?,?,?,?,?)",
    ).bind("DDD-004", "nas", "subtitle", "/nas/DDD-004.mkv", 2000000000, 0, "2026-05-30T00:00:00.000000Z"),
  ]);
}

describe("Library ownership routes", () => {
  beforeAll(async () => {
    await seedOwnership(env.OPERATIONS_DB);
  });

  it("summary returns total_owned_titles and by_source breakdown", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/library/ownership/summary",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const body = (await res.json()) as {
      total_owned_titles: number;
      by_source: Array<{ source: string; unique_titles: number; present_rows: number; total_bytes: number }>;
    };
    // DDD-004 present=0 excluded → 2 distinct titles
    expect(body.total_owned_titles).toBe(2);
    const bySource = Object.fromEntries(body.by_source.map((s) => [s.source, s]));
    expect(bySource["qb"].unique_titles).toBe(1);
    expect(bySource["gdrive"].unique_titles).toBe(1);
    // nas row is present=0 → not in by_source
    expect(bySource["nas"]).toBeUndefined();
  });

  it("recent returns rows newest-first with all expected keys", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/library/ownership/recent",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const rows = (await res.json()) as Array<Record<string, unknown>>;
    expect(rows.length).toBeGreaterThanOrEqual(1);
    const keys = Object.keys(rows[0]);
    for (const k of ["video_code", "source", "category", "path", "size", "present", "observed_at"]) {
      expect(keys).toContain(k);
    }
  });

  it("recent filters by source", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/library/ownership/recent?source=qb",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const rows = (await res.json()) as Array<{ source: string }>;
    expect(rows.every((r) => r.source === "qb")).toBe(true);
  });

  it("recent rejects an unknown source", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/library/ownership/recent?source=unknown_src",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(400);
  });

  it("requires auth", async () => {
    const res = await app.request("/api/library/ownership/summary", {}, env);
    expect(res.status).toBe(401);
  });
});
```

- [ ] **Run the route tests**

```bash
npm test -- --reporter=verbose server/__tests__/library-ownership-routes.test.ts
```

Expected: PASS (5 passed)

- [ ] **Commit**

```bash
git add server/__tests__/library-ownership-routes.test.ts
git commit -m "test(server): cover library ownership routes (ADR-034 FE-2)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Frontend API client + i18n strings

**Files (in `<WEB>`):**
- Create: `src/api/library_ownership.ts`
- Modify: `src/i18n/locales/en.json`
- Modify: `src/i18n/locales/zh-CN.json`
- Modify: `src/i18n/locales/ja.json`

> **Locale parity:** `tests/unit/i18n-parity.spec.ts` enforces that all keys in `en.json` also exist in `zh-CN.json` and `ja.json`. Update all three locales in this task or the parity test will fail.

- [ ] **Step 1: Write the typed API client**

```typescript
// src/api/library_ownership.ts
import { http } from './client'
import type { components } from '@/types/api.gen'

export type OwnershipSummary = components['schemas']['OwnershipSummary']
export type OwnershipSourceBreakdown = components['schemas']['OwnershipSourceBreakdown']
export type OwnershipRecentItem = components['schemas']['OwnershipRecentItem']

export async function getOwnershipSummary(): Promise<OwnershipSummary> {
  const { data } = await http.get<OwnershipSummary>('/api/library/ownership/summary')
  return data
}

export async function getOwnershipRecent(
  params: { source?: string | null; limit?: number; offset?: number } = {},
): Promise<OwnershipRecentItem[]> {
  const { data } = await http.get<OwnershipRecentItem[]>('/api/library/ownership/recent', {
    params: {
      source: params.source ?? undefined,
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
    },
  })
  return data
}
```

- [ ] **Step 2: Add English locale keys**

In `src/i18n/locales/en.json`, add `ownership` block inside the existing `library` object (after the `col` block):

```json
    "ownership": {
      "loadError": "Failed to load ownership data.",
      "totalOwned": "Owned titles",
      "breakdown": "By source",
      "recent": "Recent ownership",
      "allSources": "All sources",
      "present": "Present",
      "swept": "Swept",
      "source": {
        "qb": "qBittorrent",
        "nas": "NAS",
        "gdrive": "Google Drive",
        "pikpak": "PikPak"
      },
      "col": {
        "videoCode": "Code",
        "source": "Source",
        "category": "Category",
        "path": "Path",
        "size": "Size",
        "present": "Present",
        "observedAt": "Observed"
      }
    }
```

- [ ] **Step 3: Add Chinese locale keys**

In `src/i18n/locales/zh-CN.json`, add the same structure inside the `library` object:

```json
    "ownership": {
      "loadError": "加载拥有数据失败。",
      "totalOwned": "已拥有番号",
      "breakdown": "按来源",
      "recent": "最近拥有",
      "allSources": "全部来源",
      "present": "存在",
      "swept": "已清除",
      "source": {
        "qb": "qBittorrent",
        "nas": "NAS",
        "gdrive": "Google Drive",
        "pikpak": "PikPak"
      },
      "col": {
        "videoCode": "番号",
        "source": "来源",
        "category": "分类",
        "path": "路径",
        "size": "大小",
        "present": "存在",
        "observedAt": "观测时间"
      }
    }
```

- [ ] **Step 4: Add Japanese locale keys**

In `src/i18n/locales/ja.json`, add the same structure inside the `library` object:

```json
    "ownership": {
      "loadError": "所有データの読み込みに失敗しました。",
      "totalOwned": "所有済みタイトル",
      "breakdown": "ソース別",
      "recent": "最近の所有",
      "allSources": "全ソース",
      "present": "存在",
      "swept": "削除済み",
      "source": {
        "qb": "qBittorrent",
        "nas": "NAS",
        "gdrive": "Google Drive",
        "pikpak": "PikPak"
      },
      "col": {
        "videoCode": "番号",
        "source": "ソース",
        "category": "カテゴリ",
        "path": "パス",
        "size": "サイズ",
        "present": "存在",
        "observedAt": "観測日時"
      }
    }
```

- [ ] **Step 5: Run typecheck + i18n parity**

```bash
npm run typecheck
npm test -- --reporter=verbose tests/unit/i18n-parity.spec.ts
```

Expected: typecheck clean; parity test passes.

- [ ] **Step 6: Commit**

```bash
git add src/api/library_ownership.ts \
    src/i18n/locales/en.json src/i18n/locales/zh-CN.json src/i18n/locales/ja.json
git commit -m "feat(web): add ownership API client + en/zh/ja locale keys (ADR-034 FE-2)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: OwnershipView component + tab enablement + component test

**Files (in `<WEB>`):**
- Create: `src/pages/library/OwnershipView.vue`
- Modify: `src/pages/library/LibraryPage.vue`
- Create: `tests/unit/ownership-view.spec.ts`

- [ ] **Step 1: Write the OwnershipView component**

The view has: one total-owned KPI card + per-source KPI cards (from `by_source`) + a horizontal Bar chart showing unique_titles per source (static from summary, no time axis) + a recent table with a source filter and a present/swept NTag indicator.

```vue
<!-- src/pages/library/OwnershipView.vue -->
<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from 'vue'
import {
  NAlert, NButton, NGrid, NGi, NCard, NStatistic, NDataTable, NSelect, NTag, NSpin, NEmpty,
  type DataTableColumns, type SelectOption,
} from 'naive-ui'
import { Bar } from 'vue-chartjs'
import {
  Chart as ChartJS, Title, Tooltip, Legend, BarElement, CategoryScale, LinearScale,
} from 'chart.js'
import { useI18n } from 'vue-i18n'
import {
  getOwnershipSummary, getOwnershipRecent,
  type OwnershipSummary, type OwnershipRecentItem,
} from '@/api/library_ownership'

ChartJS.register(Title, Tooltip, Legend, BarElement, CategoryScale, LinearScale)

const { t } = useI18n()

const summary = ref<OwnershipSummary | null>(null)
const recent = ref<OwnershipRecentItem[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const sourceFilter = ref<string | null>(null)
let recentSeq = 0

const SOURCE_KEYS = ['qb', 'nas', 'gdrive', 'pikpak'] as const

const sourceOptions = computed<SelectOption[]>(() =>
  SOURCE_KEYS.map((s) => ({
    label: t(`library.ownership.source.${s}`, s),
    value: s,
  })),
)

// Static per-source bar: unique_titles per source (from summary, not time-series)
const breakdownChartData = computed(() => ({
  labels: (summary.value?.by_source ?? []).map((s) =>
    t(`library.ownership.source.${s.source}`, s.source),
  ),
  datasets: [
    {
      label: t('library.ownership.totalOwned'),
      data: (summary.value?.by_source ?? []).map((s) => s.unique_titles),
      backgroundColor: '#2080f0',
    },
  ],
}))
const breakdownChartOptions = {
  indexAxis: 'y' as const,
  responsive: true,
  maintainAspectRatio: false,
  scales: { x: { beginAtZero: true } },
}

const columns = computed<DataTableColumns<OwnershipRecentItem>>(() => [
  {
    title: t('library.ownership.col.videoCode'),
    key: 'video_code',
    render: (row) => h('span', { style: { fontFamily: 'monospace' } }, row.video_code),
  },
  {
    title: t('library.ownership.col.source'),
    key: 'source',
    render: (row) => t(`library.ownership.source.${row.source}`, row.source),
  },
  { title: t('library.ownership.col.category'), key: 'category', render: (row) => row.category || '—' },
  { title: t('library.ownership.col.path'), key: 'path', render: (row) => row.path ?? '—' },
  {
    title: t('library.ownership.col.present'),
    key: 'present',
    render: (row) =>
      h(
        NTag,
        { size: 'small', round: true, type: row.present === 1 ? 'success' : 'warning' },
        () => (row.present === 1 ? t('library.ownership.present') : t('library.ownership.swept')),
      ),
  },
  { title: t('library.ownership.col.observedAt'), key: 'observed_at', render: (row) => row.observed_at ?? '—' },
])

async function fetchRecent() {
  const seq = ++recentSeq
  error.value = null
  try {
    const rows = await getOwnershipRecent({ source: sourceFilter.value, limit: 50 })
    if (seq === recentSeq) recent.value = rows
  } catch (err) {
    if (seq === recentSeq) error.value = err instanceof Error ? err.message : t('library.ownership.loadError')
  }
}

async function fetchAll() {
  const seq = ++recentSeq
  loading.value = true
  error.value = null
  try {
    const [s, r] = await Promise.all([
      getOwnershipSummary(),
      getOwnershipRecent({ source: sourceFilter.value, limit: 50 }),
    ])
    summary.value = s
    if (seq === recentSeq) recent.value = r
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('library.ownership.loadError')
  } finally {
    loading.value = false
  }
}

watch(sourceFilter, () => void fetchRecent())
onMounted(() => void fetchAll())
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
        @click="fetchAll"
      >
        {{ t('common.retry') }}
      </NButton>
    </NAlert>

    <!-- KPI: total + per-source cards -->
    <NGrid
      :cols="5"
      :x-gap="12"
      :y-gap="12"
      responsive="screen"
      :item-responsive="true"
    >
      <NGi span="5 s:5 m:1">
        <NCard size="small">
          <NStatistic
            :label="t('library.ownership.totalOwned')"
            :value="summary?.total_owned_titles ?? 0"
          />
        </NCard>
      </NGi>
      <NGi
        v-for="s in SOURCE_KEYS"
        :key="s"
        span="5 s:5 m:1"
      >
        <NCard size="small">
          <NStatistic
            :label="t(`library.ownership.source.${s}`, s)"
            :value="summary?.by_source.find((b) => b.source === s)?.unique_titles ?? 0"
          />
        </NCard>
      </NGi>
    </NGrid>

    <!-- Per-source breakdown bar -->
    <NCard
      size="small"
      :title="t('library.ownership.breakdown')"
      class="block"
    >
      <div class="chart-wrap">
        <Bar
          v-if="summary && summary.by_source.length"
          :data="breakdownChartData"
          :options="breakdownChartOptions"
        />
        <NEmpty
          v-else
          :description="t('library.comingSoon')"
        />
      </div>
    </NCard>

    <!-- Recent table -->
    <NCard
      size="small"
      :title="t('library.ownership.recent')"
      class="block"
    >
      <NSelect
        v-model:value="sourceFilter"
        :options="sourceOptions"
        :placeholder="t('library.ownership.allSources')"
        clearable
        class="source-filter"
      />
      <NDataTable
        :columns="columns"
        :data="recent"
        :bordered="false"
        size="small"
        :row-key="(row: OwnershipRecentItem) => `${row.video_code}::${row.source}::${row.category}`"
      />
    </NCard>
  </NSpin>
</template>

<style scoped>
.load-error {
  margin-bottom: 16px;
}
.block {
  margin-top: 16px;
}
.chart-wrap {
  height: 200px;
}
.source-filter {
  max-width: 220px;
  margin-bottom: 12px;
}
</style>
```

- [ ] **Step 2: Enable the ownership tab in `LibraryPage.vue`**

In `src/pages/library/LibraryPage.vue`:

Add the import after the `AcquisitionView` import:

```typescript
import OwnershipView from './OwnershipView.vue'
```

Replace the disabled ownership `NTabPane`:

```vue
      <!-- BEFORE -->
      <NTabPane
        name="ownership"
        :tab="t('library.tabs.ownership')"
        disabled
      >
        <NEmpty :description="t('library.comingSoon')" />
      </NTabPane>

      <!-- AFTER -->
      <NTabPane
        name="ownership"
        :tab="t('library.tabs.ownership')"
      >
        <OwnershipView />
      </NTabPane>
```

- [ ] **Step 3: Run typecheck**

```bash
npm run typecheck
```

Expected: no errors.

- [ ] **Step 4: Write the component test**

```typescript
// tests/unit/ownership-view.spec.ts
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'

vi.mock('@/api/library_ownership', () => ({
  getOwnershipSummary: vi.fn(async () => ({
    total_owned_titles: 42,
    by_source: [
      { source: 'qb', unique_titles: 30, present_rows: 30, total_bytes: 107374182400 },
      { source: 'gdrive', unique_titles: 12, present_rows: 12, total_bytes: 53687091200 },
    ],
  })),
  getOwnershipRecent: vi.fn(async () => [
    {
      video_code: 'ABC-123',
      source: 'qb',
      category: 'subtitle',
      path: '/dl/ABC-123.mkv',
      size: 2147483648,
      present: 1,
      observed_at: '2026-06-03T00:00:00.000000Z',
    },
  ]),
}))

vi.mock('vue-chartjs', () => ({ Bar: { name: 'Bar', render: () => null } }))

import OwnershipView from '@/pages/library/OwnershipView.vue'

const i18n = createI18n({
  legacy: false,
  locale: 'en',
  messages: {
    en: {
      common: { retry: 'Retry' },
      library: {
        comingSoon: 'Coming soon',
        ownership: {
          loadError: 'Failed to load ownership data.',
          totalOwned: 'Owned titles',
          breakdown: 'By source',
          recent: 'Recent ownership',
          allSources: 'All sources',
          present: 'Present',
          swept: 'Swept',
          source: { qb: 'qBittorrent', nas: 'NAS', gdrive: 'Google Drive', pikpak: 'PikPak' },
          col: {
            videoCode: 'Code', source: 'Source', category: 'Category',
            path: 'Path', size: 'Size', present: 'Present', observedAt: 'Observed',
          },
        },
      },
    },
  },
})

describe('OwnershipView', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('renders total KPI and recent table row', async () => {
    const wrapper = mount(OwnershipView, { global: { plugins: [i18n] } })
    await flushPromises()
    expect(wrapper.text()).toContain('42')       // total_owned_titles
    expect(wrapper.text()).toContain('ABC-123')  // recent row
  })
})
```

- [ ] **Step 5: Run the component test**

```bash
npm test -- --reporter=verbose tests/unit/ownership-view.spec.ts
```

Expected: PASS (1 passed)

- [ ] **Step 6: Commit**

```bash
git add src/pages/library/OwnershipView.vue src/pages/library/LibraryPage.vue \
    tests/unit/ownership-view.spec.ts
git commit -m "feat(web): add OwnershipView + enable ownership tab (ADR-034 FE-2)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Full verification + PR

- [ ] **Step 1: Full CICD-repo verification**

```bash
PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest \
    tests/unit/test_library_ownership_query_builders.py \
    tests/unit/test_query_contract_golden.py \
    tests/integration/test_library_ownership_endpoints.py \
    tests/integration/test_library_endpoints.py \
    tests/integration/test_openapi_response_shapes.py \
    -q
git diff --check
```

Expected: all pass; no whitespace errors.

- [ ] **Step 2: Full Web-repo verification**

```bash
cd /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web
npm run lint
npm run typecheck
npm run typecheck:server
npm test
```

Expected: lint clean; both typechecks clean; all unit + server tests pass (including contract + ownership + acquisition suites).

- [ ] **Step 3: Open paired PRs**

Open two PRs and cross-link them in each description:

- `<CICD>`: `feat(api): library ownership read endpoints (ADR-034 FE-2)`
- `<WEB>`: `feat(web): Ownership view + Worker parity (ADR-034 FE-2)`

---

## Out of Scope

- No mutations — read-only only (ADR-034 Non-Goals). Ownership reconcile writes happen in the Python pipeline, not this UI.
- No trend endpoint — `observed_at` is a freshness stamp (refreshed on every reconcile pass), not a first-seen date. A time-series from it would mislead and would require a separate `first_seen_at` migration (deferred).
- No `size` aggregation KPI card on the UI (bytes are in the per-source breakdown table data but not surfaced as a standalone card — avoids unit confusion across sources; operators can read the breakdown table).
- `in_library` derivation (setting `AcquisitionOutcome.state='in_library'` when a persistent source owns the title) is pipeline-side (ADR-033 Phase 2 reconcile); this IMP adds only the read surface.
- The Consumption tab remains disabled until IMP-ADR034-03 lands.

---

## Self-Review Checklist

- [ ] Two endpoints documented: `/ownership/summary` (two SQL queries assembled into one response) and `/ownership/recent` (with source filter + 400 on unknown source).
- [ ] Parity pipeline complete: Python golden cases → `dump_query_contract` → golden JSON → vendored to WEB → TS builders mapped in `query-contract.test.ts`.
- [ ] i18n: `library.ownership.*` keys added to all three locales (`en`, `zh-CN`, `ja`).
- [ ] Tab enablement: `disabled` attribute removed from the ownership `NTabPane` in `LibraryPage.vue`; `OwnershipView` imported and rendered.
- [ ] No new capability flag needed — `closed_loop` (already present from FE-1) gates the entire Library nav entry.
- [ ] `OwnershipSummary.by_source` uses `[]` (empty array) when no present rows exist; `total_owned_titles` uses `0`. No `null` in the summary response.
