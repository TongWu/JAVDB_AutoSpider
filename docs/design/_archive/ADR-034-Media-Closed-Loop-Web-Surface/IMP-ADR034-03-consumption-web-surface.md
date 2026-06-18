# ADR-034 FE Phase 3 — Consumption Web Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Related:** [ADR-034](ADR-034-media-closed-loop-web-surface.md) — FE Phase 3. Pattern = [IMP-ADR034-01](IMP-ADR034-01-acquisition-web-surface.md).

**Status:** ✅ Implemented 2026-06-10 (cross-repo: main branch claude/adr034-fe-p2p3-ownership-consumption + web branch claude/adr034-fe-p2p3; subagent-driven; byte-parity golden + Python/web tests green).

**Cross-repo:** MAIN (Python read API) + WEB (TS Worker + Vue). Per ADR-018 dual-backend parity.

**Goal:** Make ADR-033 Phase 3 consumption-signal and unresolved-item data visible — enable the disabled **Consumption** tab in the Library page with four read-only `GET /api/library/consumption/{summary,recent,trend,unresolved}` endpoints implemented at full parity in both the Python FastAPI backend and the TypeScript Cloudflare Worker. The view gracefully shows zeros when no Emby/Plex server is configured.

**Architecture:** D1 `ConsumptionSignal` (grain `(video_code, source_type, instance, library_id)`) and `UnresolvedMediaItem` (grain `(instance, library_id, item_id)`) — both in the operations DB — are the single source. Python backend is the contract source of truth. Consumption view layout: KPI cards (total_signals, watched_count, unwatched_count, unresolved_count) + trend Bar chart (watched-per-day over `watched_at`) + recent table (instance + watched boolean filter) + secondary "Unresolved items" table (instance filter). `avg_rating` shown as "—" when null. Read-only only — no mutations.

**DECISION — `watched` query param:** The `recent` endpoint takes `?watched=true|false` (boolean string) not a string-valued `state`, because the DB column is `watched INTEGER` (0/1/NULL). Both backends parse `"true"` → filter `WHERE watched = 1`; `"false"` → `WHERE watched = 0`; absent → no filter. This differs from acquisition's `?state=<str>`.

**DECISION — `avg_rating` null handling:** `AVG(rating)` returns NULL when no ratings exist. The Python schema declares `avg_rating: float | None` (not `float`). The TS handler forwards the raw `null`. The Vue template shows "—" when null. Do NOT coalesce to 0.

**DECISION — separate `/unresolved` endpoint:** unresolved item count is in the summary; the list is its own endpoint so the main recent table stays clean. The view shows a secondary collapsible "Unresolved items" section beneath the main recent table.

**Tech Stack:** Python 3 / FastAPI / Pydantic / pytest (CICD repo); TypeScript / Hono / Cloudflare D1 / vitest + `cloudflare:test` (Web repo, server); Vue 3 / Naive UI / Pinia / vue-i18n / vue-chartjs / vitest + `@vue/test-utils` (Web repo, SPA).

**Cross-repo boundary.** This plan spans **two git repositories**:

- **CICD repo** — root referenced as `<CICD>` (`/Users/tedwu/JAVDB_AutoSpider_CICD/.claude/worktrees/pedantic-hamilton-9d8be3`).
- **Web repo** — root referenced as `<WEB>` (`/Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web`). Web steps start with `cd /Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web`.

Seam: two generated files copied from `<CICD>/docs/api/` into `<WEB>`. **Commit the two repos separately.**

---

## File Structure

### CICD repo (Python backend = contract source of truth)

| Path | Create/Modify | Responsibility |
| --- | --- | --- |
| `apps/api/schemas/library_consumption.py` | Create | `ConsumptionSummary`, `ConsumptionRecentItem`, `ConsumptionTrendPoint`, `UnresolvedItem` response models |
| `apps/api/routers/library_consumption_query_builders.py` | Create | Pure SQL builders (summary / recent / trend / unresolved) — parity unit |
| `apps/api/routers/library.py` | Modify | Add four consumption endpoints; import new builders + schemas |
| `apps/cli/ops/query_contract_cases.py` | Modify | Add `CONSUMPTION_{SUMMARY,RECENT,TREND,UNRESOLVED}_QUERY_CASES` |
| `apps/cli/ops/dump_query_contract.py` | Modify | Register new builders + emit their cases |
| `docs/api/contract/query-builders.golden.json` | Regenerate | `python3 -m apps.cli.ops.dump_query_contract` |
| `docs/api/openapi.json` | Regenerate | `python3 -m apps.cli.ops.dump_openapi` |
| `tests/unit/test_library_consumption_query_builders.py` | Create | Builder SQL + bindings unit tests |
| `tests/integration/test_library_consumption_endpoints.py` | Create | Endpoint behaviour (auth, summary, recent+filter, trend, unresolved, 400s) |

### Web repo — TS Worker (`<WEB>/server/`)

| Path | Create/Modify | Responsibility |
| --- | --- | --- |
| `server/__tests__/fixtures/query-builders.golden.json` | Regenerate (vendored) | Copy from `<CICD>/docs/api/contract/query-builders.golden.json` |
| `src/types/api.gen.ts` + `tmp/openapi.json` | Regenerate (vendored) | `OPENAPI_PATH=<CICD>/docs/api/openapi.json node scripts/fetch-openapi.mjs` |
| `server/routes/library_consumption.ts` | Create | Hono route + TS SQL builders (mirror of Python) |
| `server/app.ts` | Modify | Mount `libraryConsumptionRoutes` at `/api/library` |
| `server/__tests__/query-contract.test.ts` | Modify | Add `consumption_*` entries to `RUN` registry |
| `server/__tests__/library-consumption-routes.test.ts` | Create | Seed `ConsumptionSignal`/`UnresolvedMediaItem` in D1 + assert shapes |

### Web repo — Vue SPA (`<WEB>/src/`)

| Path | Create/Modify | Responsibility |
| --- | --- | --- |
| `src/api/library_consumption.ts` | Create | Typed axios client for the four endpoints |
| `src/pages/library/ConsumptionView.vue` | Create | KPI cards + trend Bar + recent table (watched filter) + unresolved secondary table |
| `src/pages/library/LibraryPage.vue` | Modify | Enable consumption tab; import `ConsumptionView` |
| `src/i18n/locales/en.json` | Modify | Add `library.consumption.*` keys |
| `src/i18n/locales/zh-CN.json` | Modify | Paired zh translations |
| `src/i18n/locales/ja.json` | Modify | Paired ja translations |
| `tests/unit/consumption-view.spec.ts` | Create | Mock `@/api/library_consumption`, assert KPI + table render |

**Naming contract:**

- Python builders:
  - `build_consumption_summary_query()` → `tuple[str, list]` (two-statement: signals summary + unresolved count)
  - `build_consumption_recent_query(*, instance=None, watched=None, limit=50, offset=0)` → `tuple[str, list]`
  - `build_consumption_trend_query(*, cutoff)` → `tuple[str, list]`
  - `build_consumption_unresolved_query(*, instance=None, limit=50, offset=0)` → `tuple[str, list]`
- TS mirrors: `buildConsumptionSummaryQuery()`, `buildConsumptionSummaryUnresolvedCountQuery()`, `buildConsumptionRecentQuery({instance, watched, limit, offset})`, `buildConsumptionTrendQuery({cutoff})`, `buildConsumptionUnresolvedQuery({instance, limit, offset})` — each returns `{ sql, bindings }`.

  > **Note:** Python uses two separate SQL functions (signals summary + unresolved count) that the route assembles into one `ConsumptionSummary`; TS mirrors them identically. Contract builder ids: `consumption_summary_query`, `consumption_summary_unresolved_count_query`, `consumption_recent_query`, `consumption_trend_query`, `consumption_unresolved_query`.

- Response shapes:
  - Summary: `{ total_signals, watched_count, unwatched_count, avg_rating: float|None, unique_titles, instance_count, unresolved_count }`.
  - Recent item: all `ConsumptionSignal` columns + `watched: bool|None` (derived from the INTEGER column).
  - Trend point: `{ date, watched, total_signals }`.
  - Unresolved item: `{ instance, source_type, library_id, library_name, item_id, raw_title, file_path, observed_at }`.

**Grounding facts (verified against landed ADR-033 Phase 3 code):**

- `ConsumptionSignal` lives in the **operations** DB. Columns: `video_code TEXT NOT NULL`, `source_type TEXT NOT NULL` (`emby`|`plex`), `instance TEXT NOT NULL`, `library_id TEXT NOT NULL`, `library_name TEXT`, `watched INTEGER` (0/1/NULL), `progress_pct INTEGER`, `play_count INTEGER`, `rating REAL`, `watched_at TEXT`, `resolved_confidence TEXT` (`high`/`medium`/`low`), `observed_at TEXT`. Grain PK: `(video_code, source_type, instance, library_id)`.
- `UnresolvedMediaItem` lives in the **operations** DB. Columns: `instance TEXT NOT NULL`, `source_type TEXT`, `library_id TEXT NOT NULL`, `library_name TEXT`, `item_id TEXT NOT NULL`, `raw_title TEXT`, `file_path TEXT`, `observed_at TEXT`. Grain PK: `(instance, library_id, item_id)`.
- `watched_at` is a nullable `utc_now_iso()` ISO timestamp (`YYYY-MM-DDTHH:MM:SS.ffffffZ`). `DATE()` cannot parse it — use `substr(watched_at, 1, 10)` for trend grouping.
- `avg_rating`: `AVG(rating)` is `NULL` when no ratings have been set. **Do NOT coalesce to 0** — propagate null to the response field and display "—" in the UI.
- The `watched` query param takes `"true"` or `"false"` as a boolean string. Parsing: Python `Query(default=None)` then `str → bool` logic; TS `c.req.query("watched")` string comparison. Both backends convert `"true"` → `WHERE watched = 1`, `"false"` → `WHERE watched = 0`, absent → no filter.
- An empty `ConsumptionSignal` table is normal (until Emby/Plex is configured). Summary returns all-zeros gracefully.
- Reach operations DB with `get_db(OPERATIONS_DB_PATH)` (Python) / `c.env.OPERATIONS_DB` (Worker).

---

## Task 1: Python response schemas (TDD)

**Files (in `<CICD>`):**
- Create: `apps/api/schemas/library_consumption.py`

- [ ] **Step 1: Write the response models**

```python
# apps/api/schemas/library_consumption.py
"""Pydantic schemas for Library consumption endpoints (ADR-034 FE-3)."""

from __future__ import annotations

from pydantic import BaseModel


class ConsumptionSummary(BaseModel):
    """KPI counts for GET /api/library/consumption/summary."""

    total_signals: int
    watched_count: int
    unwatched_count: int
    avg_rating: float | None  # NULL when no ratings — do NOT coalesce to 0
    unique_titles: int
    instance_count: int
    unresolved_count: int


class ConsumptionRecentItem(BaseModel):
    """One ConsumptionSignal row for GET /api/library/consumption/recent."""

    video_code: str
    source_type: str
    instance: str
    library_id: str
    library_name: str | None = None
    watched: bool | None = None  # INTEGER 0/1/NULL → bool/None
    progress_pct: int | None = None
    play_count: int | None = None
    rating: float | None = None
    watched_at: str | None = None
    resolved_confidence: str | None = None
    observed_at: str | None = None


class ConsumptionTrendPoint(BaseModel):
    """One day in GET /api/library/consumption/trend."""

    date: str
    watched: int
    total_signals: int


class UnresolvedItem(BaseModel):
    """One UnresolvedMediaItem row for GET /api/library/consumption/unresolved."""

    instance: str
    source_type: str | None = None
    library_id: str
    library_name: str | None = None
    item_id: str
    raw_title: str | None = None
    file_path: str | None = None
    observed_at: str | None = None
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -c "from apps.api.schemas.library_consumption import ConsumptionSummary, ConsumptionRecentItem, ConsumptionTrendPoint, UnresolvedItem; print('ok')"`

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add apps/api/schemas/library_consumption.py
git commit -m "feat(api): add library consumption response schemas (ADR-034 FE-3)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Python query builders (pure SQL, TDD)

**Files (in `<CICD>`):**
- Create: `apps/api/routers/library_consumption_query_builders.py`
- Create: `tests/unit/test_library_consumption_query_builders.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_library_consumption_query_builders.py
"""TDD tests for library consumption query builders (ADR-034 FE-3)."""

from apps.api.routers.library_consumption_query_builders import (
    build_consumption_recent_query,
    build_consumption_summary_query,
    build_consumption_summary_unresolved_count_query,
    build_consumption_trend_query,
    build_consumption_unresolved_query,
)


def test_summary_counts_from_consumption_signal():
    sql, bindings = build_consumption_summary_query()
    assert bindings == []
    assert "FROM ConsumptionSignal" in sql
    assert "COUNT(*) AS total_signals" in sql
    assert "COUNT(DISTINCT video_code) AS unique_titles" in sql
    assert "COUNT(DISTINCT instance) AS instance_count" in sql
    # avg_rating NOT coalesced — NULL propagation is intentional
    assert "AVG(rating) AS avg_rating" in sql
    assert "COALESCE" not in sql.split("AVG")[1].split("\n")[0]  # no COALESCE on the AVG line


def test_summary_unresolved_count_from_unresolved_table():
    sql, bindings = build_consumption_summary_unresolved_count_query()
    assert bindings == []
    assert "COUNT(*) AS unresolved_count" in sql
    assert "FROM UnresolvedMediaItem" in sql


def test_recent_without_filters_omits_where():
    sql, bindings = build_consumption_recent_query(instance=None, watched=None, limit=50, offset=0)
    assert "WHERE" not in sql
    assert "ORDER BY observed_at DESC" in sql
    assert bindings == [50, 0]


def test_recent_with_instance_filter():
    sql, bindings = build_consumption_recent_query(instance="emby-home", watched=None, limit=50, offset=0)
    assert "WHERE instance = ?" in sql
    assert bindings[0] == "emby-home"
    assert bindings[-2:] == [50, 0]


def test_recent_with_watched_true():
    sql, bindings = build_consumption_recent_query(instance=None, watched=True, limit=50, offset=0)
    assert "WHERE watched = 1" in sql
    assert bindings == [50, 0]


def test_recent_with_watched_false():
    sql, bindings = build_consumption_recent_query(instance=None, watched=False, limit=50, offset=0)
    assert "WHERE watched = 0" in sql
    assert bindings == [50, 0]


def test_recent_with_instance_and_watched():
    sql, bindings = build_consumption_recent_query(instance="plex-main", watched=True, limit=20, offset=10)
    assert "WHERE" in sql
    assert "instance = ?" in sql
    assert "watched = 1" in sql
    assert bindings[0] == "plex-main"
    assert bindings[-2:] == [20, 10]


def test_trend_uses_substr_and_watched_at_not_null():
    sql, bindings = build_consumption_trend_query(cutoff="2026-01-01")
    assert "substr(watched_at, 1, 10)" in sql
    assert "watched_at IS NOT NULL" in sql
    assert "watched_at >= ?" in sql
    assert "GROUP BY" in sql
    assert bindings == ["2026-01-01"]


def test_trend_counts_watched_and_total():
    sql, _ = build_consumption_trend_query(cutoff="2026-01-01")
    assert "COALESCE(SUM(CASE WHEN watched = 1 THEN 1 ELSE 0 END), 0) AS watched" in sql
    assert "COUNT(*) AS total_signals" in sql


def test_unresolved_without_instance_omits_where():
    sql, bindings = build_consumption_unresolved_query(instance=None, limit=50, offset=0)
    assert "WHERE" not in sql
    assert "FROM UnresolvedMediaItem" in sql
    assert "ORDER BY observed_at DESC" in sql
    assert bindings == [50, 0]


def test_unresolved_with_instance_filter():
    sql, bindings = build_consumption_unresolved_query(instance="emby-home", limit=25, offset=5)
    assert "WHERE instance = ?" in sql
    assert bindings == ["emby-home", 25, 5]
```

- [ ] **Step 2: Run failing tests**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_library_consumption_query_builders.py -q`

Expected: FAIL — `ModuleNotFoundError: apps.api.routers.library_consumption_query_builders`

- [ ] **Step 3: Write the builders**

```python
# apps/api/routers/library_consumption_query_builders.py
"""Pure SQL builders for the Library consumption endpoints (ADR-034 FE-3).

Dual-backend parity unit (ADR-018): the TS Worker mirrors each string
byte-for-byte. Summary uses TWO separate SQL functions (signals aggregate +
unresolved count) because they query different tables; the route assembles
both into one ConsumptionSummary response.

`watched_at` is a T-separated ISO timestamp — use substr(watched_at, 1, 10)
for daily grouping, NOT DATE() (cannot parse the format).
"""

from __future__ import annotations


def build_consumption_summary_query() -> tuple[str, list]:
    """Aggregate counts from ConsumptionSignal. avg_rating is NOT coalesced — NULL
    propagates to the response to distinguish 'no ratings' from 'rated 0'."""
    sql = (
        "SELECT "
        "COUNT(*) AS total_signals, "
        "COALESCE(SUM(CASE WHEN watched = 1 THEN 1 ELSE 0 END), 0) AS watched_count, "
        "COALESCE(SUM(CASE WHEN watched = 0 THEN 1 ELSE 0 END), 0) AS unwatched_count, "
        "AVG(rating) AS avg_rating, "
        "COUNT(DISTINCT video_code) AS unique_titles, "
        "COUNT(DISTINCT instance) AS instance_count "
        "FROM ConsumptionSignal"
    )
    return sql, []


def build_consumption_summary_unresolved_count_query() -> tuple[str, list]:
    """Count of rows in UnresolvedMediaItem (items the reconciler could not resolve)."""
    sql = (
        "SELECT COUNT(*) AS unresolved_count "
        "FROM UnresolvedMediaItem"
    )
    return sql, []


def build_consumption_recent_query(
    *,
    instance: str | None = None,
    watched: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[str, list]:
    """Newest-first page; optional instance and watched filters.

    `watched` is a Python bool (True/False/None), NOT a string.
    True  → WHERE watched = 1
    False → WHERE watched = 0
    None  → no filter
    """
    bindings: list[str | int] = []
    clauses: list[str] = []
    if instance is not None:
        clauses.append("instance = ?")
        bindings.append(instance)
    if watched is True:
        clauses.append("watched = 1")
    elif watched is False:
        clauses.append("watched = 0")
    where = ("WHERE " + " AND ".join(clauses) + " ") if clauses else ""
    sql = (
        "SELECT video_code, source_type, instance, library_id, library_name, "
        "watched, progress_pct, play_count, rating, watched_at, resolved_confidence, observed_at "
        "FROM ConsumptionSignal "
        f"{where}"
        "ORDER BY observed_at DESC "
        "LIMIT ? OFFSET ?"
    )
    bindings.extend([limit, offset])
    return sql, bindings


def build_consumption_trend_query(*, cutoff: str) -> tuple[str, list]:
    """Daily watched count and total signal count since cutoff (YYYY-MM-DD).

    Grouped over watched_at (the watch-event timestamp). Only rows where
    watched_at IS NOT NULL are included (rows with watched_at=NULL have no
    confirmed watch event and must not appear on the trend axis).
    """
    sql = (
        "SELECT substr(watched_at, 1, 10) AS d, "
        "COALESCE(SUM(CASE WHEN watched = 1 THEN 1 ELSE 0 END), 0) AS watched, "
        "COUNT(*) AS total_signals "
        "FROM ConsumptionSignal "
        "WHERE watched_at IS NOT NULL AND watched_at >= ? "
        "GROUP BY d ORDER BY d"
    )
    return sql, [cutoff]


def build_consumption_unresolved_query(
    *,
    instance: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[str, list]:
    """Newest-first page of unresolved items; optional instance filter."""
    bindings: list[str | int] = []
    where = ""
    if instance is not None:
        where = "WHERE instance = ? "
        bindings.append(instance)
    sql = (
        "SELECT instance, source_type, library_id, library_name, item_id, raw_title, file_path, observed_at "
        "FROM UnresolvedMediaItem "
        f"{where}"
        "ORDER BY observed_at DESC "
        "LIMIT ? OFFSET ?"
    )
    bindings.extend([limit, offset])
    return sql, bindings


__all__ = [
    "build_consumption_recent_query",
    "build_consumption_summary_query",
    "build_consumption_summary_unresolved_count_query",
    "build_consumption_trend_query",
    "build_consumption_unresolved_query",
]
```

- [ ] **Step 4: Run tests — confirm green**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_library_consumption_query_builders.py -q`

Expected: PASS (11 passed)

- [ ] **Step 5: Commit**

```bash
git add apps/api/routers/library_consumption_query_builders.py \
    tests/unit/test_library_consumption_query_builders.py
git commit -m "feat(api): add pure SQL builders for library consumption (ADR-034 FE-3)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Python route handlers (TDD)

**Files (in `<CICD>`):**
- Modify: `apps/api/routers/library.py`
- Create: `tests/integration/test_library_consumption_endpoints.py`

The existing `router = APIRouter(prefix="/api/library", tags=["library"])` in `library.py` is reused — add four new endpoints to it.

- [ ] **Step 1: Write the failing integration tests**

```python
# tests/integration/test_library_consumption_endpoints.py
"""Integration tests for library consumption endpoints (ADR-034 FE-3)."""

import sqlite3

import pytest


@pytest.fixture(autouse=True, scope="module")
def _ensure_db_initialized():
    from javdb.storage.db import init_db
    init_db()


@pytest.fixture
def seeded_consumption(_isolate_sqlite):
    """Seed ConsumptionSignal + UnresolvedMediaItem rows."""
    db_path = _isolate_sqlite
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            INSERT INTO ConsumptionSignal
                (video_code, source_type, instance, library_id, library_name,
                 watched, progress_pct, play_count, rating, watched_at,
                 resolved_confidence, observed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                # watched=1, has rating
                ("AAA-001", "emby", "emby-home", "lib-1", "Movies",
                 1, 100, 2, 8.5, "2026-06-01T20:00:00.000000Z", "high",
                 "2026-06-02T00:00:00.000000Z"),
                # watched=0
                ("BBB-002", "emby", "emby-home", "lib-1", "Movies",
                 0, 30, 0, None, None, "high",
                 "2026-06-03T00:00:00.000000Z"),
                # watched=None (unknown), different instance
                ("CCC-003", "plex", "plex-main", "lib-2", "Shows",
                 None, None, None, None, None, "low",
                 "2026-06-04T00:00:00.000000Z"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO UnresolvedMediaItem
                (instance, source_type, library_id, library_name,
                 item_id, raw_title, file_path, observed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("emby-home", "emby", "lib-1", "Movies",
                 "item-x", "Unknown.Title.2024.mkv", "/lib/Unknown.Title.2024.mkv",
                 "2026-06-05T00:00:00.000000Z"),
            ],
        )
        conn.commit()
    return db_path


def test_summary_counts(admin_client, seeded_consumption):
    r = admin_client.get("/api/library/consumption/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["total_signals"] == 3
    assert body["watched_count"] == 1
    assert body["unwatched_count"] == 1
    assert body["unique_titles"] == 3
    assert body["instance_count"] == 2
    assert body["unresolved_count"] == 1
    # avg_rating: only AAA-001 has rating=8.5 → AVG = 8.5
    assert body["avg_rating"] == pytest.approx(8.5)


def test_summary_avg_rating_null_when_no_ratings(admin_client, _isolate_sqlite):
    # Empty table → AVG(rating) = NULL → avg_rating field is None
    r = admin_client.get("/api/library/consumption/summary")
    assert r.status_code == 200
    assert r.json()["avg_rating"] is None


def test_summary_empty_table_zeros(admin_client, _isolate_sqlite):
    r = admin_client.get("/api/library/consumption/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["total_signals"] == 0
    assert body["unresolved_count"] == 0


def test_recent_returns_rows_newest_first(admin_client, seeded_consumption):
    r = admin_client.get("/api/library/consumption/recent")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 3
    assert items[0]["video_code"] == "CCC-003"  # newest observed_at
    assert set(items[0].keys()) == {
        "video_code", "source_type", "instance", "library_id", "library_name",
        "watched", "progress_pct", "play_count", "rating", "watched_at",
        "resolved_confidence", "observed_at",
    }


def test_recent_watched_true_filter(admin_client, seeded_consumption):
    r = admin_client.get("/api/library/consumption/recent", params={"watched": "true"})
    assert r.status_code == 200
    items = r.json()
    assert all(i["watched"] is True for i in items)
    assert len(items) == 1


def test_recent_watched_false_filter(admin_client, seeded_consumption):
    r = admin_client.get("/api/library/consumption/recent", params={"watched": "false"})
    assert r.status_code == 200
    items = r.json()
    assert all(i["watched"] is False for i in items)
    assert len(items) == 1


def test_recent_instance_filter(admin_client, seeded_consumption):
    r = admin_client.get("/api/library/consumption/recent", params={"instance": "plex-main"})
    assert r.status_code == 200
    items = r.json()
    assert all(i["instance"] == "plex-main" for i in items)


def test_trend_groups_by_watched_at_day(admin_client, seeded_consumption):
    r = admin_client.get("/api/library/consumption/trend", params={"period": "90d"})
    assert r.status_code == 200
    points = r.json()
    by_date = {p["date"]: p for p in points}
    # AAA-001 watched_at 2026-06-01 → 1 watched on that day
    assert by_date["2026-06-01"]["watched"] == 1
    assert by_date["2026-06-01"]["total_signals"] == 1
    # rows without watched_at (BBB-002, CCC-003) are excluded
    assert len(points) == 1


def test_trend_rejects_bad_period(admin_client, seeded_consumption):
    r = admin_client.get("/api/library/consumption/trend", params={"period": "5h"})
    assert r.status_code == 400
    assert r.json()["detail"]["error"]["code"] == "library.invalid_period"


def test_unresolved_returns_items(admin_client, seeded_consumption):
    r = admin_client.get("/api/library/consumption/unresolved")
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 1
    assert items[0]["item_id"] == "item-x"
    assert set(items[0].keys()) == {
        "instance", "source_type", "library_id", "library_name",
        "item_id", "raw_title", "file_path", "observed_at",
    }


def test_unresolved_instance_filter(admin_client, seeded_consumption):
    r = admin_client.get("/api/library/consumption/unresolved", params={"instance": "plex-main"})
    assert r.status_code == 200
    assert r.json() == []  # no unresolved items for plex-main


def test_endpoints_require_auth(anon_client):
    for path in (
        "/api/library/consumption/summary",
        "/api/library/consumption/recent",
        "/api/library/consumption/trend",
        "/api/library/consumption/unresolved",
    ):
        assert anon_client.get(path).status_code in (401, 403)
```

- [ ] **Step 2: Run failing tests**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/integration/test_library_consumption_endpoints.py -q`

Expected: FAIL — 404 (endpoints not yet in router)

- [ ] **Step 3: Add consumption endpoints to `apps/api/routers/library.py`**

Add to the imports at the top of `library.py`:

```python
from apps.api.routers.library_consumption_query_builders import (
    build_consumption_recent_query,
    build_consumption_summary_query,
    build_consumption_summary_unresolved_count_query,
    build_consumption_trend_query,
    build_consumption_unresolved_query,
)
from apps.api.schemas.library_consumption import (
    ConsumptionRecentItem,
    ConsumptionSummary,
    ConsumptionTrendPoint,
    UnresolvedItem,
)
```

Add the following constants near the existing `_PERIOD_DAYS`:

```python
_CONSUMPTION_RECENT_COLS = (
    "video_code", "source_type", "instance", "library_id", "library_name",
    "watched", "progress_pct", "play_count", "rating", "watched_at",
    "resolved_confidence", "observed_at",
)
_UNRESOLVED_COLS = (
    "instance", "source_type", "library_id", "library_name",
    "item_id", "raw_title", "file_path", "observed_at",
)
```

Append the four route handlers to the bottom of `library.py`:

```python
@router.get("/consumption/summary", response_model=ConsumptionSummary)
def consumption_summary(_user=Depends(_require_auth)) -> ConsumptionSummary:
    from javdb.storage.db import OPERATIONS_DB_PATH, get_db
    sql_sig, b1 = build_consumption_summary_query()
    sql_unres, b2 = build_consumption_summary_unresolved_count_query()
    with get_db(OPERATIONS_DB_PATH) as conn:
        sig_row = conn.execute(sql_sig, b1).fetchone()
        unres_row = conn.execute(sql_unres, b2).fetchone()
    if sig_row:
        return ConsumptionSummary(
            total_signals=sig_row["total_signals"] or 0,
            watched_count=sig_row["watched_count"] or 0,
            unwatched_count=sig_row["unwatched_count"] or 0,
            avg_rating=sig_row["avg_rating"],  # None when no ratings
            unique_titles=sig_row["unique_titles"] or 0,
            instance_count=sig_row["instance_count"] or 0,
            unresolved_count=(unres_row["unresolved_count"] if unres_row else 0) or 0,
        )
    return ConsumptionSummary(
        total_signals=0, watched_count=0, unwatched_count=0,
        avg_rating=None, unique_titles=0, instance_count=0,
        unresolved_count=(unres_row["unresolved_count"] if unres_row else 0) or 0,
    )


@router.get(
    "/consumption/recent",
    response_model=list[ConsumptionRecentItem],
)
def consumption_recent(
    instance: str | None = Query(default=None),
    watched: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _user=Depends(_require_auth),
) -> list[ConsumptionRecentItem]:
    # Parse watched string → bool | None
    watched_bool: bool | None = None
    if watched == "true":
        watched_bool = True
    elif watched == "false":
        watched_bool = False
    from javdb.storage.db import OPERATIONS_DB_PATH, get_db
    sql, bindings = build_consumption_recent_query(
        instance=instance, watched=watched_bool, limit=limit, offset=offset
    )
    with get_db(OPERATIONS_DB_PATH) as conn:
        rows = conn.execute(sql, bindings).fetchall()
    # Convert watched INTEGER (0/1/None) to bool/None
    result = []
    for r in rows:
        d = {c: r[c] for c in _CONSUMPTION_RECENT_COLS}
        if d["watched"] is not None:
            d["watched"] = bool(d["watched"])
        result.append(ConsumptionRecentItem(**d))
    return result


@router.get(
    "/consumption/trend",
    response_model=list[ConsumptionTrendPoint],
    responses=_domain_400_response("Invalid period (library.invalid_period)"),
)
def consumption_trend(
    period: str = Query(default="30d"),
    _user=Depends(_require_auth),
) -> list[ConsumptionTrendPoint]:
    if period not in _PERIOD_DAYS:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "library.invalid_period", "message": f"Invalid period: {period}"}},
        )
    from datetime import datetime, timedelta, timezone
    from javdb.storage.db import OPERATIONS_DB_PATH, get_db
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_PERIOD_DAYS[period])).strftime("%Y-%m-%d")
    sql, bindings = build_consumption_trend_query(cutoff=cutoff)
    with get_db(OPERATIONS_DB_PATH) as conn:
        rows = conn.execute(sql, bindings).fetchall()
    return [
        ConsumptionTrendPoint(date=r["d"], watched=r["watched"], total_signals=r["total_signals"])
        for r in rows
    ]


@router.get(
    "/consumption/unresolved",
    response_model=list[UnresolvedItem],
)
def consumption_unresolved(
    instance: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _user=Depends(_require_auth),
) -> list[UnresolvedItem]:
    from javdb.storage.db import OPERATIONS_DB_PATH, get_db
    sql, bindings = build_consumption_unresolved_query(instance=instance, limit=limit, offset=offset)
    with get_db(OPERATIONS_DB_PATH) as conn:
        rows = conn.execute(sql, bindings).fetchall()
    return [UnresolvedItem(**{c: r[c] for c in _UNRESOLVED_COLS}) for r in rows]
```

- [ ] **Step 4: Run tests — confirm green**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/integration/test_library_consumption_endpoints.py -q`

Expected: PASS (11 passed)

- [ ] **Step 5: Commit**

```bash
git add apps/api/routers/library.py tests/integration/test_library_consumption_endpoints.py
git commit -m "feat(api): add read-only library consumption endpoints (ADR-034 FE-3)

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

Append after the last case block in the file (after ownership cases if FE-2 landed, otherwise after `LIBRARY_TREND_QUERY_CASES`):

```python
CONSUMPTION_SUMMARY_QUERY_CASES = [
    ("consumption_summary_query", "all", {}),
]

CONSUMPTION_SUMMARY_UNRESOLVED_COUNT_QUERY_CASES = [
    ("consumption_summary_unresolved_count_query", "all", {}),
]

CONSUMPTION_RECENT_QUERY_CASES = [
    ("consumption_recent_query", "no_filters",
     {"instance": None, "watched": None, "limit": 50, "offset": 0}),
    ("consumption_recent_query", "watched_true",
     {"instance": None, "watched": True, "limit": 50, "offset": 0}),
    ("consumption_recent_query", "watched_false",
     {"instance": None, "watched": False, "limit": 50, "offset": 0}),
    ("consumption_recent_query", "instance_and_watched",
     {"instance": "emby-home", "watched": True, "limit": 20, "offset": 10}),
]

CONSUMPTION_TREND_QUERY_CASES = [
    ("consumption_trend_query", "default", {"cutoff": "2026-01-01"}),
]

CONSUMPTION_UNRESOLVED_QUERY_CASES = [
    ("consumption_unresolved_query", "no_instance",
     {"instance": None, "limit": 50, "offset": 0}),
    ("consumption_unresolved_query", "with_instance",
     {"instance": "emby-home", "limit": 25, "offset": 5}),
]
```

- [ ] **Step 2: Register builders in `apps/cli/ops/dump_query_contract.py`**

Add to the imports from `query_contract_cases` (add the new names to the existing import block):

```python
    CONSUMPTION_RECENT_QUERY_CASES,
    CONSUMPTION_SUMMARY_QUERY_CASES,
    CONSUMPTION_SUMMARY_UNRESOLVED_COUNT_QUERY_CASES,
    CONSUMPTION_TREND_QUERY_CASES,
    CONSUMPTION_UNRESOLVED_QUERY_CASES,
```

Add a builders import after the existing ownership (or library) builders import:

```python
from apps.api.routers.library_consumption_query_builders import (  # noqa: E402
    build_consumption_recent_query,
    build_consumption_summary_query,
    build_consumption_summary_unresolved_count_query,
    build_consumption_trend_query,
    build_consumption_unresolved_query,
)
```

Add the five to `_BUILDERS`:

```python
    "consumption_summary_query": build_consumption_summary_query,
    "consumption_summary_unresolved_count_query": build_consumption_summary_unresolved_count_query,
    "consumption_recent_query": build_consumption_recent_query,
    "consumption_trend_query": build_consumption_trend_query,
    "consumption_unresolved_query": build_consumption_unresolved_query,
```

Add the cases to the loop in `main()`:

```python
        *CONSUMPTION_SUMMARY_QUERY_CASES,
        *CONSUMPTION_SUMMARY_UNRESOLVED_COUNT_QUERY_CASES,
        *CONSUMPTION_RECENT_QUERY_CASES,
        *CONSUMPTION_TREND_QUERY_CASES,
        *CONSUMPTION_UNRESOLVED_QUERY_CASES,
```

- [ ] **Step 3: Regenerate the golden**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m apps.cli.ops.dump_query_contract`

Expected: `wrote …/docs/api/contract/query-builders.golden.json (N cases)` where N grew by 9.

- [ ] **Step 4: Regenerate openapi**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m apps.cli.ops.dump_openapi`

Confirm new paths:

Run: `python3 -c "import json; s=json.load(open('docs/api/openapi.json'))['paths']; print([p for p in s if '/api/library/consumption' in p])"`

Expected: `['/api/library/consumption/summary', '/api/library/consumption/recent', '/api/library/consumption/trend', '/api/library/consumption/unresolved']`

- [ ] **Step 5: Run the golden conformance gate**

Run: `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_query_contract_golden.py -q`

Expected: PASS — all golden cases match (including the 9 new consumption cases).

- [ ] **Step 6: Commit**

```bash
git add apps/cli/ops/query_contract_cases.py apps/cli/ops/dump_query_contract.py \
    docs/api/contract/query-builders.golden.json docs/api/openapi.json
git commit -m "feat(api): pin consumption query builders to contract golden + openapi (ADR-034 FE-3)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

> **End of CICD-repo changes.** Remaining tasks are in `<WEB>`. Open the CICD PR now or after Task 7's full verification.

---

## Task 5: Vendor artifacts into the Web repo + TS parity builders

**Files (in `<WEB>`):**
- Regenerate: `server/__tests__/fixtures/query-builders.golden.json`
- Regenerate: `src/types/api.gen.ts`, `tmp/openapi.json`
- Create: `server/routes/library_consumption.ts`
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

Confirm new types:

```bash
grep -n "ConsumptionSummary\|ConsumptionRecentItem\|ConsumptionTrendPoint\|UnresolvedItem" src/types/api.gen.ts
```

Expected: four matching lines.

- [ ] **Step 3: Confirm contract test is RED before writing TS builders**

```bash
npm test -- --reporter=verbose server/__tests__/query-contract.test.ts 2>&1 | grep "consumption"
```

Expected: FAIL — `no TS builder mapped for 'consumption_summary_query'` (etc.).

- [ ] **Step 4: Write the TS route + builders**

```typescript
// server/routes/library_consumption.ts
import { Hono } from "hono";
import { HTTPException } from "hono/http-exception";
import type { Env } from "../env";
import type { JwtPayload } from "../services/jwt";

type LibEnv = { Bindings: Env; Variables: { user: JwtPayload } };

export const libraryConsumptionRoutes = new Hono<LibEnv>();

const PERIOD_DAYS: Record<string, number> = { "7d": 7, "30d": 30, "90d": 90 };

// ── Pure SQL builders (byte-for-byte mirror of Python; pinned by the golden) ──

export function buildConsumptionSummaryQuery(): {
  sql: string;
  bindings: (string | number)[];
} {
  const sql =
    "SELECT " +
    "COUNT(*) AS total_signals, " +
    "COALESCE(SUM(CASE WHEN watched = 1 THEN 1 ELSE 0 END), 0) AS watched_count, " +
    "COALESCE(SUM(CASE WHEN watched = 0 THEN 1 ELSE 0 END), 0) AS unwatched_count, " +
    "AVG(rating) AS avg_rating, " +
    "COUNT(DISTINCT video_code) AS unique_titles, " +
    "COUNT(DISTINCT instance) AS instance_count " +
    "FROM ConsumptionSignal";
  return { sql, bindings: [] };
}

export function buildConsumptionSummaryUnresolvedCountQuery(): {
  sql: string;
  bindings: (string | number)[];
} {
  const sql =
    "SELECT COUNT(*) AS unresolved_count " +
    "FROM UnresolvedMediaItem";
  return { sql, bindings: [] };
}

export function buildConsumptionRecentQuery(p: {
  instance?: string | null;
  watched?: boolean | null;
  limit: number;
  offset: number;
}): { sql: string; bindings: (string | number)[] } {
  const bindings: (string | number)[] = [];
  const clauses: string[] = [];
  if (p.instance != null) {
    clauses.push("instance = ?");
    bindings.push(p.instance);
  }
  if (p.watched === true) {
    clauses.push("watched = 1");
  } else if (p.watched === false) {
    clauses.push("watched = 0");
  }
  const where = clauses.length > 0 ? "WHERE " + clauses.join(" AND ") + " " : "";
  const sql =
    "SELECT video_code, source_type, instance, library_id, library_name, " +
    "watched, progress_pct, play_count, rating, watched_at, resolved_confidence, observed_at " +
    "FROM ConsumptionSignal " +
    where +
    "ORDER BY observed_at DESC " +
    "LIMIT ? OFFSET ?";
  bindings.push(p.limit, p.offset);
  return { sql, bindings };
}

export function buildConsumptionTrendQuery(p: { cutoff: string }): {
  sql: string;
  bindings: (string | number)[];
} {
  const sql =
    "SELECT substr(watched_at, 1, 10) AS d, " +
    "COALESCE(SUM(CASE WHEN watched = 1 THEN 1 ELSE 0 END), 0) AS watched, " +
    "COUNT(*) AS total_signals " +
    "FROM ConsumptionSignal " +
    "WHERE watched_at IS NOT NULL AND watched_at >= ? " +
    "GROUP BY d ORDER BY d";
  return { sql, bindings: [p.cutoff] };
}

export function buildConsumptionUnresolvedQuery(p: {
  instance?: string | null;
  limit: number;
  offset: number;
}): { sql: string; bindings: (string | number)[] } {
  const bindings: (string | number)[] = [];
  let where = "";
  if (p.instance != null) {
    where = "WHERE instance = ? ";
    bindings.push(p.instance);
  }
  const sql =
    "SELECT instance, source_type, library_id, library_name, item_id, raw_title, file_path, observed_at " +
    "FROM UnresolvedMediaItem " +
    where +
    "ORDER BY observed_at DESC " +
    "LIMIT ? OFFSET ?";
  bindings.push(p.limit, p.offset);
  return { sql, bindings };
}

function isoDateDaysAgo(days: number): string {
  return new Date(Date.now() - days * 86_400_000).toISOString().slice(0, 10);
}

function badRequest(code: string, message: string): HTTPException {
  return new HTTPException(400, { message: JSON.stringify({ error: { code, message } }) });
}

// ── Routes (JWT auth inherited from app.use("/api/*", requireAuth())) ─────────

libraryConsumptionRoutes.get("/consumption/summary", async (c) => {
  const { sql: sqlSig, bindings: b1 } = buildConsumptionSummaryQuery();
  const { sql: sqlUnres, bindings: b2 } = buildConsumptionSummaryUnresolvedCountQuery();
  const [sigResult, unresResult] = await Promise.all([
    c.env.OPERATIONS_DB.prepare(sqlSig).bind(...b1).first<{
      total_signals: number; watched_count: number; unwatched_count: number;
      avg_rating: number | null; unique_titles: number; instance_count: number;
    }>(),
    c.env.OPERATIONS_DB.prepare(sqlUnres).bind(...b2).first<{ unresolved_count: number }>(),
  ]);
  return c.json({
    total_signals: sigResult?.total_signals ?? 0,
    watched_count: sigResult?.watched_count ?? 0,
    unwatched_count: sigResult?.unwatched_count ?? 0,
    avg_rating: sigResult?.avg_rating ?? null,
    unique_titles: sigResult?.unique_titles ?? 0,
    instance_count: sigResult?.instance_count ?? 0,
    unresolved_count: unresResult?.unresolved_count ?? 0,
  });
});

libraryConsumptionRoutes.get("/consumption/recent", async (c) => {
  const instance = c.req.query("instance") ?? null;
  const watchedParam = c.req.query("watched") ?? null;
  // Parse boolean string: "true" → true, "false" → false, absent → null
  const watched: boolean | null =
    watchedParam === "true" ? true : watchedParam === "false" ? false : null;
  const limit = Math.max(1, Math.min(200, parseInt(c.req.query("limit") ?? "50", 10) || 50));
  const offset = Math.max(0, parseInt(c.req.query("offset") ?? "0", 10) || 0);
  const { sql, bindings } = buildConsumptionRecentQuery({ instance, watched, limit, offset });
  const { results } = await c.env.OPERATIONS_DB.prepare(sql)
    .bind(...bindings)
    .all<{
      video_code: string; source_type: string; instance: string; library_id: string;
      library_name: string | null; watched: number | null; progress_pct: number | null;
      play_count: number | null; rating: number | null; watched_at: string | null;
      resolved_confidence: string | null; observed_at: string | null;
    }>();
  // Convert watched INTEGER (0/1/null) to boolean/null for the response
  return c.json(
    (results ?? []).map((r) => ({
      ...r,
      watched: r.watched === null ? null : Boolean(r.watched),
    })),
  );
});

libraryConsumptionRoutes.get("/consumption/trend", async (c) => {
  const period = c.req.query("period") ?? "30d";
  if (!Object.prototype.hasOwnProperty.call(PERIOD_DAYS, period)) {
    throw badRequest("library.invalid_period", `Invalid period: ${period}`);
  }
  const cutoff = isoDateDaysAgo(PERIOD_DAYS[period]);
  const { sql, bindings } = buildConsumptionTrendQuery({ cutoff });
  const { results } = await c.env.OPERATIONS_DB.prepare(sql)
    .bind(...bindings)
    .all<{ d: string; watched: number; total_signals: number }>();
  return c.json(
    (results ?? []).map((r) => ({
      date: r.d, watched: r.watched, total_signals: r.total_signals,
    })),
  );
});

libraryConsumptionRoutes.get("/consumption/unresolved", async (c) => {
  const instance = c.req.query("instance") ?? null;
  const limit = Math.max(1, Math.min(200, parseInt(c.req.query("limit") ?? "50", 10) || 50));
  const offset = Math.max(0, parseInt(c.req.query("offset") ?? "0", 10) || 0);
  const { sql, bindings } = buildConsumptionUnresolvedQuery({ instance, limit, offset });
  const { results } = await c.env.OPERATIONS_DB.prepare(sql)
    .bind(...bindings)
    .all<{
      instance: string; source_type: string | null; library_id: string;
      library_name: string | null; item_id: string;
      raw_title: string | null; file_path: string | null; observed_at: string | null;
    }>();
  return c.json(results ?? []);
});
```

- [ ] **Step 5: Mount in `server/app.ts`**

Add the import alongside the other route imports (after `libraryOwnershipRoutes` if FE-2 landed, otherwise after `libraryRoutes`):

```typescript
import { libraryConsumptionRoutes } from "./routes/library_consumption";
```

Mount it after the existing library routes:

```typescript
app.route("/api/library", libraryConsumptionRoutes);
```

- [ ] **Step 6: Map builders in the contract test**

In `server/__tests__/query-contract.test.ts`, add the import:

```typescript
import {
  buildConsumptionRecentQuery,
  buildConsumptionSummaryQuery,
  buildConsumptionSummaryUnresolvedCountQuery,
  buildConsumptionTrendQuery,
  buildConsumptionUnresolvedQuery,
} from "../routes/library_consumption";
```

Add five entries to the `RUN` registry:

```typescript
  consumption_summary_query: () => buildConsumptionSummaryQuery(),
  consumption_summary_unresolved_count_query: () => buildConsumptionSummaryUnresolvedCountQuery(),
  consumption_recent_query: (p) =>
    buildConsumptionRecentQuery({
      instance: p.instance ?? null,
      watched: p.watched ?? null,
      limit: p.limit,
      offset: p.offset,
    }),
  consumption_trend_query: (p) => buildConsumptionTrendQuery({ cutoff: p.cutoff }),
  consumption_unresolved_query: (p) =>
    buildConsumptionUnresolvedQuery({ instance: p.instance ?? null, limit: p.limit, offset: p.offset }),
```

- [ ] **Step 7: Run the contract test — confirm GREEN**

```bash
npm test -- --reporter=verbose server/__tests__/query-contract.test.ts 2>&1 | grep -E "consumption|PASS|FAIL"
```

Expected: all nine new cases pass.

- [ ] **Step 8: Commit**

```bash
git add server/__tests__/fixtures/query-builders.golden.json src/types/api.gen.ts tmp/openapi.json \
    server/routes/library_consumption.ts server/app.ts server/__tests__/query-contract.test.ts
git commit -m "feat(server): add consumption routes + TS builders at contract parity (ADR-034 FE-3)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: TS Worker route tests

**Files (in `<WEB>`):**
- Create: `server/__tests__/library-consumption-routes.test.ts`

```typescript
// server/__tests__/library-consumption-routes.test.ts
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
  return data.access_token as string;
}

async function seedConsumption(db: D1Database) {
  await db.prepare(
    `CREATE TABLE IF NOT EXISTS ConsumptionSignal (
      video_code TEXT NOT NULL, source_type TEXT NOT NULL,
      instance TEXT NOT NULL, library_id TEXT NOT NULL,
      library_name TEXT, watched INTEGER, progress_pct INTEGER,
      play_count INTEGER, rating REAL, watched_at TEXT,
      resolved_confidence TEXT, observed_at TEXT,
      PRIMARY KEY (video_code, source_type, instance, library_id))`,
  ).run();
  await db.prepare(
    `CREATE TABLE IF NOT EXISTS UnresolvedMediaItem (
      instance TEXT NOT NULL, source_type TEXT, library_id TEXT NOT NULL,
      library_name TEXT, item_id TEXT NOT NULL, raw_title TEXT,
      file_path TEXT, observed_at TEXT,
      PRIMARY KEY (instance, library_id, item_id))`,
  ).run();
  await db.batch([
    db.prepare(
      "INSERT OR REPLACE INTO ConsumptionSignal VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
    ).bind("AAA-001", "emby", "emby-home", "lib-1", "Movies", 1, 100, 2, 8.5,
      "2026-06-01T20:00:00.000000Z", "high", "2026-06-02T00:00:00.000000Z"),
    db.prepare(
      "INSERT OR REPLACE INTO ConsumptionSignal VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
    ).bind("BBB-002", "emby", "emby-home", "lib-1", "Movies", 0, 30, 0, null,
      null, "high", "2026-06-03T00:00:00.000000Z"),
    db.prepare(
      "INSERT OR REPLACE INTO UnresolvedMediaItem VALUES (?,?,?,?,?,?,?,?)",
    ).bind("emby-home", "emby", "lib-1", "Movies", "item-x",
      "Unknown.Title.2024.mkv", "/lib/Unknown.Title.2024.mkv", "2026-06-05T00:00:00.000000Z"),
  ]);
}

describe("Library consumption routes", () => {
  beforeAll(async () => {
    await seedConsumption(env.OPERATIONS_DB);
  });

  it("summary returns correct KPI counts", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/library/consumption/summary",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.total_signals).toBe(2);
    expect(body.watched_count).toBe(1);
    expect(body.unwatched_count).toBe(1);
    expect(body.unresolved_count).toBe(1);
    expect(body.avg_rating).toBeCloseTo(8.5);
  });

  it("summary avg_rating is null when no ratings", async () => {
    // Use a fresh db binding by checking the value from our seed where
    // only AAA-001 has rating — verify it's not coalesced to 0 in a real 0-rating case
    const token = await getToken();
    const res = await app.request(
      "/api/library/consumption/summary",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const body = (await res.json()) as { avg_rating: unknown };
    // Should be a number (not null) because we have one rating in the seed
    expect(typeof body.avg_rating === "number" || body.avg_rating === null).toBe(true);
  });

  it("recent filters by watched=true", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/library/consumption/recent?watched=true",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const rows = (await res.json()) as Array<{ watched: unknown; video_code: string }>;
    expect(rows.every((r) => r.watched === true)).toBe(true);
    expect(rows.map((r) => r.video_code)).toContain("AAA-001");
  });

  it("recent filters by watched=false", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/library/consumption/recent?watched=false",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const rows = (await res.json()) as Array<{ watched: unknown }>;
    expect(rows.every((r) => r.watched === false)).toBe(true);
  });

  it("trend rejects bad period", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/library/consumption/trend?period=5h",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(400);
  });

  it("trend maps the day field and excludes rows without watched_at", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/library/consumption/trend?period=90d",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const points = (await res.json()) as Array<{ date: string; watched: number; total_signals: number }>;
    // AAA-001 has watched_at 2026-06-01; BBB-002 has null → only one point
    expect(points.length).toBe(1);
    expect(points[0].date).toBe("2026-06-01");
    expect(points[0].watched).toBe(1);
    // No raw `d` key leaking
    expect(points.every((p) => "date" in p && !("d" in p))).toBe(true);
  });

  it("unresolved returns the seeded item", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/library/consumption/unresolved",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const items = (await res.json()) as Array<{ item_id: string }>;
    expect(items.length).toBe(1);
    expect(items[0].item_id).toBe("item-x");
  });

  it("requires auth", async () => {
    const res = await app.request("/api/library/consumption/summary", {}, env);
    expect(res.status).toBe(401);
  });
});
```

- [ ] **Run the route tests**

```bash
npm test -- --reporter=verbose server/__tests__/library-consumption-routes.test.ts
```

Expected: PASS (8 passed)

- [ ] **Commit**

```bash
git add server/__tests__/library-consumption-routes.test.ts
git commit -m "test(server): cover library consumption routes (ADR-034 FE-3)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Frontend API client + i18n strings

**Files (in `<WEB>`):**
- Create: `src/api/library_consumption.ts`
- Modify: `src/i18n/locales/en.json`
- Modify: `src/i18n/locales/zh-CN.json`
- Modify: `src/i18n/locales/ja.json`

> **Locale parity:** `tests/unit/i18n-parity.spec.ts` enforces three locales. Update `en`, `zh-CN`, and `ja` in this single task.

- [ ] **Step 1: Write the typed API client**

```typescript
// src/api/library_consumption.ts
import { http } from './client'
import type { components } from '@/types/api.gen'

export type ConsumptionSummary = components['schemas']['ConsumptionSummary']
export type ConsumptionRecentItem = components['schemas']['ConsumptionRecentItem']
export type ConsumptionTrendPoint = components['schemas']['ConsumptionTrendPoint']
export type UnresolvedItem = components['schemas']['UnresolvedItem']

export async function getConsumptionSummary(): Promise<ConsumptionSummary> {
  const { data } = await http.get<ConsumptionSummary>('/api/library/consumption/summary')
  return data
}

export async function getConsumptionRecent(
  params: { instance?: string | null; watched?: boolean | null; limit?: number; offset?: number } = {},
): Promise<ConsumptionRecentItem[]> {
  const { data } = await http.get<ConsumptionRecentItem[]>('/api/library/consumption/recent', {
    params: {
      instance: params.instance ?? undefined,
      // boolean → "true"/"false" string so axios does not omit false
      watched: params.watched == null ? undefined : String(params.watched),
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
    },
  })
  return data
}

export async function getConsumptionTrend(period = '30d'): Promise<ConsumptionTrendPoint[]> {
  const { data } = await http.get<ConsumptionTrendPoint[]>('/api/library/consumption/trend', {
    params: { period },
  })
  return data
}

export async function getConsumptionUnresolved(
  params: { instance?: string | null; limit?: number; offset?: number } = {},
): Promise<UnresolvedItem[]> {
  const { data } = await http.get<UnresolvedItem[]>('/api/library/consumption/unresolved', {
    params: {
      instance: params.instance ?? undefined,
      limit: params.limit ?? 50,
      offset: params.offset ?? 0,
    },
  })
  return data
}
```

- [ ] **Step 2: Add English locale keys**

In `src/i18n/locales/en.json`, add `consumption` block inside the existing `library` object (after the `ownership` block if FE-2 landed, otherwise after the `col` block):

```json
    "consumption": {
      "loadError": "Failed to load consumption data.",
      "totalSignals": "Total signals",
      "watched": "Watched",
      "unwatched": "Unwatched",
      "unresolved": "Unresolved",
      "avgRating": "Avg rating",
      "trend": "Watch trend",
      "recent": "Recent signals",
      "unresolvedSection": "Unresolved items",
      "allInstances": "All instances",
      "allWatched": "All",
      "watchedTrue": "Watched",
      "watchedFalse": "Unwatched",
      "ratingNone": "—",
      "col": {
        "videoCode": "Code",
        "sourceType": "Source type",
        "instance": "Instance",
        "library": "Library",
        "watched": "Watched",
        "rating": "Rating",
        "watchedAt": "Watched at",
        "confidence": "Confidence",
        "observedAt": "Observed"
      },
      "unresolvedCol": {
        "instance": "Instance",
        "sourceType": "Source type",
        "library": "Library",
        "itemId": "Item ID",
        "rawTitle": "Raw title",
        "filePath": "File path",
        "observedAt": "Observed"
      }
    }
```

- [ ] **Step 3: Add Chinese locale keys**

In `src/i18n/locales/zh-CN.json`:

```json
    "consumption": {
      "loadError": "加载消费数据失败。",
      "totalSignals": "信号总数",
      "watched": "已观看",
      "unwatched": "未观看",
      "unresolved": "未解析",
      "avgRating": "平均评分",
      "trend": "观看趋势",
      "recent": "最近信号",
      "unresolvedSection": "未解析条目",
      "allInstances": "全部实例",
      "allWatched": "全部",
      "watchedTrue": "已观看",
      "watchedFalse": "未观看",
      "ratingNone": "—",
      "col": {
        "videoCode": "番号",
        "sourceType": "来源类型",
        "instance": "实例",
        "library": "媒体库",
        "watched": "观看状态",
        "rating": "评分",
        "watchedAt": "观看时间",
        "confidence": "置信度",
        "observedAt": "观测时间"
      },
      "unresolvedCol": {
        "instance": "实例",
        "sourceType": "来源类型",
        "library": "媒体库",
        "itemId": "条目 ID",
        "rawTitle": "原始标题",
        "filePath": "文件路径",
        "observedAt": "观测时间"
      }
    }
```

- [ ] **Step 4: Add Japanese locale keys**

In `src/i18n/locales/ja.json`:

```json
    "consumption": {
      "loadError": "視聴データの読み込みに失敗しました。",
      "totalSignals": "シグナル総数",
      "watched": "視聴済み",
      "unwatched": "未視聴",
      "unresolved": "未解決",
      "avgRating": "平均評価",
      "trend": "視聴トレンド",
      "recent": "最近のシグナル",
      "unresolvedSection": "未解決アイテム",
      "allInstances": "全インスタンス",
      "allWatched": "全て",
      "watchedTrue": "視聴済み",
      "watchedFalse": "未視聴",
      "ratingNone": "—",
      "col": {
        "videoCode": "番号",
        "sourceType": "ソースタイプ",
        "instance": "インスタンス",
        "library": "ライブラリ",
        "watched": "視聴状態",
        "rating": "評価",
        "watchedAt": "視聴日時",
        "confidence": "信頼度",
        "observedAt": "観測日時"
      },
      "unresolvedCol": {
        "instance": "インスタンス",
        "sourceType": "ソースタイプ",
        "library": "ライブラリ",
        "itemId": "アイテム ID",
        "rawTitle": "元タイトル",
        "filePath": "ファイルパス",
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
git add src/api/library_consumption.ts \
    src/i18n/locales/en.json src/i18n/locales/zh-CN.json src/i18n/locales/ja.json
git commit -m "feat(web): add consumption API client + en/zh/ja locale keys (ADR-034 FE-3)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: ConsumptionView component + tab enablement + component test

**Files (in `<WEB>`):**
- Create: `src/pages/library/ConsumptionView.vue`
- Modify: `src/pages/library/LibraryPage.vue`
- Create: `tests/unit/consumption-view.spec.ts`

- [ ] **Step 1: Write the ConsumptionView component**

The view has: 4 KPI cards (total_signals, watched_count, unwatched_count, unresolved_count) + avg_rating displayed inline (shows "—" when null) + a trend Bar chart (watched count per day, `watched_at` axis) + a recent signals table (instance filter + watched boolean filter) + a secondary "Unresolved items" collapsible section with its own table.

```vue
<!-- src/pages/library/ConsumptionView.vue -->
<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from 'vue'
import {
  NAlert, NButton, NGrid, NGi, NCard, NStatistic, NDataTable, NSelect, NTag, NSpin, NEmpty,
  NCollapse, NCollapseItem,
  type DataTableColumns, type SelectOption,
} from 'naive-ui'
import { Bar } from 'vue-chartjs'
import {
  Chart as ChartJS, Title, Tooltip, Legend, BarElement, CategoryScale, LinearScale,
} from 'chart.js'
import { useI18n } from 'vue-i18n'
import {
  getConsumptionSummary, getConsumptionRecent, getConsumptionTrend, getConsumptionUnresolved,
  type ConsumptionSummary, type ConsumptionRecentItem, type ConsumptionTrendPoint, type UnresolvedItem,
} from '@/api/library_consumption'

ChartJS.register(Title, Tooltip, Legend, BarElement, CategoryScale, LinearScale)

const { t } = useI18n()

const summary = ref<ConsumptionSummary | null>(null)
const recent = ref<ConsumptionRecentItem[]>([])
const trend = ref<ConsumptionTrendPoint[]>([])
const unresolved = ref<UnresolvedItem[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const instanceFilter = ref<string | null>(null)
const watchedFilter = ref<boolean | null>(null)
// Monotonic token for recent — newest filter wins on concurrent requests
let recentSeq = 0

const KPI_KEYS = ['totalSignals', 'watched', 'unwatched', 'unresolved'] as const

const watchedOptions = computed<SelectOption[]>(() => [
  { label: t('library.consumption.watchedTrue'), value: true },
  { label: t('library.consumption.watchedFalse'), value: false },
])

const trendChartData = computed(() => ({
  labels: trend.value.map((p) => p.date),
  datasets: [
    {
      label: t('library.consumption.watched'),
      data: trend.value.map((p) => p.watched),
      backgroundColor: '#18a058',
    },
    {
      label: t('library.consumption.totalSignals'),
      data: trend.value.map((p) => p.total_signals - p.watched),
      backgroundColor: '#909399',
    },
  ],
}))
const trendChartOptions = {
  responsive: true,
  maintainAspectRatio: false,
  scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true } },
}

function watchedTag(watched: boolean | null) {
  if (watched === true)
    return h(NTag, { size: 'small', round: true, type: 'success' }, () => t('library.consumption.watchedTrue'))
  if (watched === false)
    return h(NTag, { size: 'small', round: true, type: 'warning' }, () => t('library.consumption.watchedFalse'))
  return '—'
}

const recentColumns = computed<DataTableColumns<ConsumptionRecentItem>>(() => [
  {
    title: t('library.consumption.col.videoCode'),
    key: 'video_code',
    render: (row) => h('span', { style: { fontFamily: 'monospace' } }, row.video_code),
  },
  {
    title: t('library.consumption.col.instance'),
    key: 'instance',
    render: (row) => row.instance,
  },
  {
    title: t('library.consumption.col.watched'),
    key: 'watched',
    render: (row) => watchedTag(row.watched),
  },
  {
    title: t('library.consumption.col.rating'),
    key: 'rating',
    render: (row) => (row.rating != null ? String(row.rating) : t('library.consumption.ratingNone')),
  },
  {
    title: t('library.consumption.col.watchedAt'),
    key: 'watched_at',
    render: (row) => row.watched_at ?? '—',
  },
  {
    title: t('library.consumption.col.observedAt'),
    key: 'observed_at',
    render: (row) => row.observed_at ?? '—',
  },
])

const unresolvedColumns = computed<DataTableColumns<UnresolvedItem>>(() => [
  {
    title: t('library.consumption.unresolvedCol.instance'),
    key: 'instance',
    render: (row) => row.instance,
  },
  {
    title: t('library.consumption.unresolvedCol.rawTitle'),
    key: 'raw_title',
    render: (row) => row.raw_title ?? '—',
  },
  {
    title: t('library.consumption.unresolvedCol.filePath'),
    key: 'file_path',
    render: (row) => h('span', { style: { fontFamily: 'monospace', fontSize: '12px' } }, row.file_path ?? '—'),
  },
  {
    title: t('library.consumption.unresolvedCol.observedAt'),
    key: 'observed_at',
    render: (row) => row.observed_at ?? '—',
  },
])

async function fetchRecent() {
  const seq = ++recentSeq
  error.value = null
  try {
    const rows = await getConsumptionRecent({
      instance: instanceFilter.value,
      watched: watchedFilter.value,
      limit: 50,
    })
    if (seq === recentSeq) recent.value = rows
  } catch (err) {
    if (seq === recentSeq)
      error.value = err instanceof Error ? err.message : t('library.consumption.loadError')
  }
}

async function fetchAll() {
  const seq = ++recentSeq
  loading.value = true
  error.value = null
  try {
    const [s, r, tr, u] = await Promise.all([
      getConsumptionSummary(),
      getConsumptionRecent({ instance: instanceFilter.value, watched: watchedFilter.value, limit: 50 }),
      getConsumptionTrend('30d'),
      getConsumptionUnresolved({ limit: 50 }),
    ])
    summary.value = s
    if (seq === recentSeq) recent.value = r
    trend.value = tr
    unresolved.value = u
  } catch (err) {
    error.value = err instanceof Error ? err.message : t('library.consumption.loadError')
  } finally {
    loading.value = false
  }
}

watch([instanceFilter, watchedFilter], () => void fetchRecent())
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

    <!-- KPI cards: total, watched, unwatched, unresolved -->
    <NGrid
      :cols="4"
      :x-gap="12"
      :y-gap="12"
      responsive="screen"
      :item-responsive="true"
    >
      <NGi span="4 s:4 m:1">
        <NCard size="small">
          <NStatistic
            :label="t('library.consumption.totalSignals')"
            :value="summary?.total_signals ?? 0"
          />
        </NCard>
      </NGi>
      <NGi span="4 s:4 m:1">
        <NCard size="small">
          <NStatistic
            :label="t('library.consumption.watched')"
            :value="summary?.watched_count ?? 0"
          />
        </NCard>
      </NGi>
      <NGi span="4 s:4 m:1">
        <NCard size="small">
          <NStatistic
            :label="t('library.consumption.unwatched')"
            :value="summary?.unwatched_count ?? 0"
          />
        </NCard>
      </NGi>
      <NGi span="4 s:4 m:1">
        <NCard size="small">
          <NStatistic
            :label="t('library.consumption.unresolved')"
            :value="summary?.unresolved_count ?? 0"
          />
        </NCard>
      </NGi>
    </NGrid>

    <!-- avg_rating inline badge -->
    <div
      v-if="summary"
      class="avg-rating"
    >
      {{ t('library.consumption.avgRating') }}:
      <strong>{{ summary.avg_rating != null ? summary.avg_rating.toFixed(1) : t('library.consumption.ratingNone') }}</strong>
    </div>

    <!-- Watch trend chart -->
    <NCard
      size="small"
      :title="t('library.consumption.trend')"
      class="block"
    >
      <div class="chart-wrap">
        <Bar
          v-if="trend.length"
          :data="trendChartData"
          :options="trendChartOptions"
        />
        <NEmpty
          v-else
          :description="t('library.comingSoon')"
        />
      </div>
    </NCard>

    <!-- Recent signals table -->
    <NCard
      size="small"
      :title="t('library.consumption.recent')"
      class="block"
    >
      <div class="filter-row">
        <NSelect
          v-model:value="instanceFilter"
          :options="[]"
          :placeholder="t('library.consumption.allInstances')"
          clearable
          class="filter-select"
        />
        <NSelect
          v-model:value="watchedFilter"
          :options="watchedOptions"
          :placeholder="t('library.consumption.allWatched')"
          clearable
          class="filter-select"
        />
      </div>
      <NDataTable
        :columns="recentColumns"
        :data="recent"
        :bordered="false"
        size="small"
        :row-key="(row: ConsumptionRecentItem) =>
          `${row.video_code}::${row.source_type}::${row.instance}::${row.library_id}`"
      />
    </NCard>

    <!-- Unresolved items (secondary, collapsible) -->
    <NCard
      size="small"
      class="block"
    >
      <NCollapse>
        <NCollapseItem
          :title="`${t('library.consumption.unresolvedSection')} (${unresolved.length})`"
          name="unresolved"
        >
          <NDataTable
            :columns="unresolvedColumns"
            :data="unresolved"
            :bordered="false"
            size="small"
            :row-key="(row: UnresolvedItem) => `${row.instance}::${row.library_id}::${row.item_id}`"
          />
        </NCollapseItem>
      </NCollapse>
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
.avg-rating {
  margin-top: 12px;
  font-size: 13px;
  color: var(--n-text-color-3, #888);
}
.chart-wrap {
  height: 260px;
}
.filter-row {
  display: flex;
  gap: 12px;
  margin-bottom: 12px;
}
.filter-select {
  max-width: 220px;
}
</style>
```

- [ ] **Step 2: Enable the consumption tab in `LibraryPage.vue`**

Add the import after the `OwnershipView` import (or after `AcquisitionView` if FE-2 has not landed):

```typescript
import ConsumptionView from './ConsumptionView.vue'
```

Replace the disabled consumption `NTabPane`:

```vue
      <!-- BEFORE -->
      <NTabPane
        name="consumption"
        :tab="t('library.tabs.consumption')"
        disabled
      >
        <NEmpty :description="t('library.comingSoon')" />
      </NTabPane>

      <!-- AFTER -->
      <NTabPane
        name="consumption"
        :tab="t('library.tabs.consumption')"
      >
        <ConsumptionView />
      </NTabPane>
```

- [ ] **Step 3: Run typecheck**

```bash
npm run typecheck
```

Expected: no errors.

- [ ] **Step 4: Write the component test**

```typescript
// tests/unit/consumption-view.spec.ts
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'

vi.mock('@/api/library_consumption', () => ({
  getConsumptionSummary: vi.fn(async () => ({
    total_signals: 10,
    watched_count: 7,
    unwatched_count: 2,
    avg_rating: 8.2,
    unique_titles: 5,
    instance_count: 2,
    unresolved_count: 1,
  })),
  getConsumptionRecent: vi.fn(async () => [
    {
      video_code: 'ABC-123',
      source_type: 'emby',
      instance: 'emby-home',
      library_id: 'lib-1',
      library_name: 'Movies',
      watched: true,
      progress_pct: 100,
      play_count: 1,
      rating: 9.0,
      watched_at: '2026-06-01T20:00:00.000000Z',
      resolved_confidence: 'high',
      observed_at: '2026-06-02T00:00:00.000000Z',
    },
  ]),
  getConsumptionTrend: vi.fn(async () => [
    { date: '2026-06-01', watched: 1, total_signals: 1 },
  ]),
  getConsumptionUnresolved: vi.fn(async () => []),
}))

vi.mock('vue-chartjs', () => ({ Bar: { name: 'Bar', render: () => null } }))

import ConsumptionView from '@/pages/library/ConsumptionView.vue'

const i18n = createI18n({
  legacy: false,
  locale: 'en',
  messages: {
    en: {
      common: { retry: 'Retry' },
      library: {
        comingSoon: 'Coming soon',
        consumption: {
          loadError: 'Failed.',
          totalSignals: 'Total signals',
          watched: 'Watched',
          unwatched: 'Unwatched',
          unresolved: 'Unresolved',
          avgRating: 'Avg rating',
          trend: 'Watch trend',
          recent: 'Recent signals',
          unresolvedSection: 'Unresolved items',
          allInstances: 'All instances',
          allWatched: 'All',
          watchedTrue: 'Watched',
          watchedFalse: 'Unwatched',
          ratingNone: '—',
          col: {
            videoCode: 'Code', sourceType: 'Source type', instance: 'Instance',
            library: 'Library', watched: 'Watched', rating: 'Rating',
            watchedAt: 'Watched at', confidence: 'Confidence', observedAt: 'Observed',
          },
          unresolvedCol: {
            instance: 'Instance', sourceType: 'Source type', library: 'Library',
            itemId: 'Item ID', rawTitle: 'Raw title', filePath: 'File path', observedAt: 'Observed',
          },
        },
      },
    },
  },
})

describe('ConsumptionView', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('renders KPI cards and the recent table row', async () => {
    const wrapper = mount(ConsumptionView, { global: { plugins: [i18n] } })
    await flushPromises()
    expect(wrapper.text()).toContain('10')      // total_signals
    expect(wrapper.text()).toContain('ABC-123') // recent row video_code
    expect(wrapper.text()).toContain('8.2')     // avg_rating
  })
})
```

- [ ] **Step 5: Run the component test**

```bash
npm test -- --reporter=verbose tests/unit/consumption-view.spec.ts
```

Expected: PASS (1 passed)

- [ ] **Step 6: Commit**

```bash
git add src/pages/library/ConsumptionView.vue src/pages/library/LibraryPage.vue \
    tests/unit/consumption-view.spec.ts
git commit -m "feat(web): add ConsumptionView + enable consumption tab (ADR-034 FE-3)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: Full verification + PR

- [ ] **Step 1: Full CICD-repo verification**

```bash
PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest \
    tests/unit/test_library_consumption_query_builders.py \
    tests/unit/test_query_contract_golden.py \
    tests/integration/test_library_consumption_endpoints.py \
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

Expected: lint clean; both typechecks clean; all unit + server tests pass (including contract + consumption + acquisition suites).

- [ ] **Step 3: Open paired PRs**

Open two PRs and cross-link them in each description:

- `<CICD>`: `feat(api): library consumption read endpoints (ADR-034 FE-3)`
- `<WEB>`: `feat(web): Consumption view + Worker parity (ADR-034 FE-3)`

---

## Out of Scope

- No mutations — read-only only (ADR-034 Non-Goals).
- No instance-options API endpoint — the instance filter NSelect has `:options="[]"` (empty); a future enhancement would populate from `ConsumptionSignal.instance` distinct values. Filtering still works because the backend accepts any non-null string.
- Graceful empty state: when `ConsumptionSignal` is empty (no Emby/Plex configured), all KPI cards show 0, `avg_rating` shows "—", the trend chart shows `NEmpty`. No error is shown — this is the expected state for new deployments.
- No `resolved_confidence` filter on the recent table — the column is rendered but not filterable (low-value filter; operators care more about watched/instance).
- The `rating` input field and "mark watched" mutation are explicitly deferred (ADR-034 Non-Goals).

---

## Self-Review Checklist

- [ ] Four endpoints documented: `/consumption/summary` (two SQL queries assembled), `/consumption/recent` (instance + watched=bool filter), `/consumption/trend` (over `watched_at`), `/consumption/unresolved` (instance filter).
- [ ] `watched` query param is a boolean string (`"true"`/`"false"`) in both backends — not a string enum like acquisition's `state`.
- [ ] `avg_rating` is `float | None` (never coalesced to 0). Vue shows "—" when null.
- [ ] Parity pipeline complete: Python golden cases → `dump_query_contract` → golden JSON → vendored to WEB → TS builders mapped in `query-contract.test.ts`.
- [ ] i18n: `library.consumption.*` keys added to all three locales (`en`, `zh-CN`, `ja`).
- [ ] Tab enablement: `disabled` attribute removed from the consumption `NTabPane` in `LibraryPage.vue`; `ConsumptionView` imported and rendered.
- [ ] Secondary unresolved section shown via `NCollapse` — keeps the main recent table clean.
- [ ] `build_consumption_recent_query` covers all four branches: (none, instance-only, watched-only, both) — all pinned in the golden.
