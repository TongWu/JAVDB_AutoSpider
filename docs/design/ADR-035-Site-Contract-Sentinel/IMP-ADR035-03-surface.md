# IMP-ADR035-03: Drift Surface — Per-Field Health on the Web (Site-Contract Sentinel Phase 3) Implementation Plan

**Status:** Proposed (2026-06-03) — Phase 3 of three (optional polish). Phases 1–2 ship the detection + gate + canary; this phase makes the data **visible**.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-035](ADR-035-site-contract-drift-sentinel.md) (umbrella) — this is **Phase 3**. Follows the dual-backend web-surface pattern of [ADR-034](../ADR-034-Media-Closed-Loop-Web-Surface/ADR-034-media-closed-loop-web-surface.md) (D3/D4/D7: Python router + TS Worker mirror behind JWT, `openapi.json` as the seam, capability-gated nav).

**Goal:** Surface the drift data Phases 1–2 already write — make `site_drift` `OpsIncidents` filterable by type and expose the latest per-field parse health from `ParseRunFieldFill` — through two read-only API endpoints, behind the existing JWT auth and a new `site_drift_sentinel` capability flag, with `openapi.json` regenerated and the TypeScript Worker mirror tracked as a linked cross-repo follow-up.

**Architecture:** Two read-only additions to the **existing** `/api/diag` surface (`apps/api/routers/diagnostics.py`, where `ops-incidents` already lives): (1) an `incident_type` filter on `GET /api/diag/ops-incidents` (so the web — and ADR-026's AI, per D6 — can pull `site_drift` incidents); (2) a new `GET /api/diag/parse-field-health` returning the latest committed fill-rate per contract field, annotated with severity/baseline/threshold/status by a pure `javdb/ops/sentinel/health.py` view that reuses the Phase-1 `PARSE_CONTRACT`. A `site_drift_sentinel` boolean joins `capabilities.features`. No new DB table; no writes — pure read surface over Phase-1/2 data.

**Tech Stack:** Python 3, FastAPI (`APIRouter`, `Depends(_require_auth)`), Pydantic, `sqlite3`/D1 via `javdb.storage.db.get_db`, `pytest`; `apps.cli.ops.dump_openapi` for the contract; Cloudflare Worker (Hono/TS) for the mirror follow-up.

**Scope decisions (read before implementing):**

- **No new AI code.** "AI drift summary" (ADR-035 Phase 3 / D6) is delivered by **ADR-026's existing diagnosis surface**: once `site_drift` incidents are filterable (Task 1), ADR-026's synthesizer summarises them like any other incident when AI is enabled (it is a documented stub today — building real model calls is ADR-026's job, explicitly out of scope here). This IMP makes drift incidents *queryable and visible*; it does not add a model client.
- **No new domain vocabulary.** Phases 1–2 already added *Parse contract*, *Field fill-rate*, *Site drift*, *Sentinel*, *Canary probe* to `CONTEXT.md`. Phase 3 surfaces existing concepts; it adds no terms.
- **The TypeScript mirror is a cross-repo follow-up, not built here.** Per CLAUDE.md's dual-backend rule, the overlapping D1 queries / response shapes / capability flag must be mirrored in `javdb-autospider-web/server/` (a separate repo, not in this monorepo). Task 6 specifies exactly what the TS side must add and tracks it as a linked follow-up; the Python side + `openapi.json` is the seam.
- **Surgical placement.** Both endpoints extend the existing `diagnostics` router and `diagnostics` schema module — no new router file, no new registration in `runtime.py`. This matches where `ops-incidents` already lives and keeps the diff minimal.

---

## File Structure

| Path | Create/Modify | Responsibility |
| --- | --- | --- |
| `javdb/storage/repos/ops_incident_repo.py` | Modify | `list(..., incident_type=None)` — filter incidents by type |
| `javdb/storage/repos/parse_run_field_fill_repo.py` | Modify | `latest_committed_fills()` — newest committed fill per `(page_type, field)` |
| `javdb/ops/sentinel/health.py` | Create | Pure `compute_field_health(rows, *, baseline_fn, min_sample)` + `FieldHealth` view |
| `apps/api/schemas/diagnostics.py` | Modify | `ParseFieldHealthItem`, `ParseFieldHealthResponse` |
| `apps/api/routers/diagnostics.py` | Modify | `incident_type` query param; `GET /parse-field-health` + helpers |
| `apps/api/schemas/capabilities_payloads.py` | Modify | `Features.site_drift_sentinel: bool` |
| `apps/api/routers/capabilities.py` | Modify | populate `site_drift_sentinel` |
| `docs/api/openapi.json` | Modify (generated) | regenerated contract |
| `docs/handbook/en/developer/*` (+ zh) | Modify | document the two endpoints + capability flag |
| `tests/unit/test_ops_incident_repo_filter.py` | Create | `list(incident_type=...)` |
| `tests/unit/test_parse_field_fill_latest.py` | Create | `latest_committed_fills()` |
| `tests/unit/test_sentinel_health.py` | Create | `compute_field_health` status matrix |
| `tests/unit/test_parse_field_health_endpoint.py` | Create | `_compute_parse_field_health(repo=...)` + route registration |
| `tests/unit/test_capabilities_site_drift_flag.py` | Create | capability flag default + env override |

**Naming contract (verbatim across tasks):**
`OpsIncidentRepo.list(*, status=None, run_id=None, session_id=None, incident_type=None, limit=50)`; `ParseRunFieldFillRepo.latest_committed_fills() -> list[tuple[str, str, float, int, str | None]]` (rows: `(page_type, field, fill_rate, sample_count, observed_at)`); `FieldHealth(page_type, field, severity, fill_rate, sample_count, observed_at, baseline, threshold, status)`; `compute_field_health(rows, *, baseline_fn, min_sample) -> list[FieldHealth]`; `ParseFieldHealthItem` / `ParseFieldHealthResponse(items: list[ParseFieldHealthItem])`; router helpers `_compute_parse_field_health(*, repo=None)`, `_field_health_to_schema(h)`; `Features.site_drift_sentinel: bool`. Status vocabulary (exact strings): `"ok" | "critical_drift" | "soft_drift" | "no_baseline" | "insufficient_sample"`.

Reused verbatim (do not redefine): `fields_for(page_type)` (`javdb/spider/parse_contract.py`); `ParseRunFieldFillRepo.baseline(page_type, field, *, window)`; `get_db`, `REPORTS_DB_PATH` (`javdb.storage.db`); `_require_auth` (`apps/api/infra/auth.py`); `_bool_env` (`apps/api/routers/capabilities.py`); `cfg` (`javdb.infra.config`).

---

## Task 1: `OpsIncidentRepo.list(incident_type=...)` + endpoint filter

**Files:**
- Test: `tests/unit/test_ops_incident_repo_filter.py` (Create)
- ~~Modify: `javdb/storage/repos/ops_incident_repo.py`~~ — **already implemented**
- ~~Modify: `apps/api/routers/diagnostics.py`~~ — **already implemented**

This unlocks pulling `site_drift` incidents specifically (for the web panel and ADR-026's AI summariser, D6).

> **As-built (2026-06-06): the code half of this task already shipped.** Verified
> against the current tree: `OpsIncidentRepo.list()` already accepts
> `incident_type` (and an extra `confidence` filter from later work) and wires the
> `WHERE incident_type = ?` clause; `apps/api/routers/diagnostics.py`'s
> `_list_ops_incident_records` helper and the `GET /api/diag/ops-incidents` route
> already thread `incident_type` through. The Step 3 / Step 5 find-and-replace
> blocks **will not match** the current code — do **not** re-apply them. Execute
> **only Step 1 (write the test), Step 2 (run — it goes green on arrival because
> the feature pre-exists), and Step 7 (commit the test)**; skip Steps 3–6. The
> test stands as a regression pin for the already-shipped filter.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_ops_incident_repo_filter.py
import sqlite3

import pytest

from javdb.storage.repos.ops_incident_repo import OpsIncidentRepo

_DDL = """
CREATE TABLE OpsIncidents (
  incident_id TEXT PRIMARY KEY, trigger_source TEXT, run_id TEXT, run_attempt INTEGER,
  session_id TEXT, incident_type TEXT, status TEXT, persistence_status TEXT,
  model_version TEXT, detector_version TEXT, bundle_schema_version TEXT, confidence TEXT,
  confirmed_findings_json TEXT, likely_causes_json TEXT, unknowns_json TEXT,
  recommended_next_actions_json TEXT, unsafe_actions_json TEXT, evidence_refs_json TEXT,
  created_at TEXT, updated_at TEXT, resolved_at TEXT
);
"""


def _insert(conn, incident_id, incident_type, created_at):
    conn.execute(
        "INSERT INTO OpsIncidents (incident_id, trigger_source, run_id, run_attempt, "
        "session_id, incident_type, status, persistence_status, model_version, "
        "detector_version, bundle_schema_version, confidence, confirmed_findings_json, "
        "likely_causes_json, unknowns_json, recommended_next_actions_json, "
        "unsafe_actions_json, evidence_refs_json, created_at, updated_at, resolved_at) "
        "VALUES (?, 'sentinel', NULL, NULL, NULL, ?, 'open', 'd1_written', 'n/a', "
        "'sentinel-v1', 'n/a', 'high', '[]', '[]', '[]', '[]', '[]', '[]', ?, ?, NULL)",
        [incident_id, incident_type, created_at, created_at],
    )


@pytest.fixture
def repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    _insert(c, "i1", "site_drift", "2026-06-01T00:00:00Z")
    _insert(c, "i2", "failed_ingestion", "2026-06-02T00:00:00Z")
    _insert(c, "i3", "site_drift", "2026-06-03T00:00:00Z")
    return OpsIncidentRepo(c)


def test_filter_by_incident_type(repo):
    rows = repo.list(incident_type="site_drift")
    assert {r.incident_id for r in rows} == {"i1", "i3"}


def test_no_filter_returns_all(repo):
    assert len(repo.list()) == 3


def test_filter_combines_with_other_clauses(repo):
    rows = repo.list(incident_type="site_drift", status="open")
    assert {r.incident_id for r in rows} == {"i1", "i3"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_ops_incident_repo_filter.py -v`
Expected: FAIL — `list() got an unexpected keyword argument 'incident_type'`

- [ ] **Step 3: Add the `incident_type` clause to `OpsIncidentRepo.list`**

In `javdb/storage/repos/ops_incident_repo.py`, change the `list` signature and add the clause.

Find:
```python
    def list(
        self,
        *,
        status: str | None = None,
        run_id: str | None = None,
        session_id: str | None = None,
        limit: int = 50,
    ) -> list[OpsIncidentRecord]:
        clauses: list[str] = []
        params: list[object] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
```

Replace with:
```python
    def list(
        self,
        *,
        status: str | None = None,
        run_id: str | None = None,
        session_id: str | None = None,
        incident_type: str | None = None,
        limit: int = 50,
    ) -> list[OpsIncidentRecord]:
        clauses: list[str] = []
        params: list[object] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if incident_type:
            clauses.append("incident_type = ?")
            params.append(incident_type)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_ops_incident_repo_filter.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Thread `incident_type` through the diagnostics endpoint**

In `apps/api/routers/diagnostics.py`, update the helper and the endpoint.

Find:
```python
def _list_ops_incident_records(
    *,
    status: str | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
):
    with get_db(REPORTS_DB_PATH) as conn:
        return OpsIncidentRepo(conn).list(
            status=status,
            run_id=run_id,
            session_id=session_id,
            limit=limit,
        )
```

Replace with:
```python
def _list_ops_incident_records(
    *,
    status: str | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    incident_type: str | None = None,
    limit: int = 50,
):
    with get_db(REPORTS_DB_PATH) as conn:
        return OpsIncidentRepo(conn).list(
            status=status,
            run_id=run_id,
            session_id=session_id,
            incident_type=incident_type,
            limit=limit,
        )
```

Find:
```python
@router.get("/ops-incidents", response_model=OpsIncidentListResponse)
def list_ops_incidents(
    status: str | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
    _user: Dict[str, Any] = Depends(_require_auth),
) -> OpsIncidentListResponse:
    """Return persisted read-only operations diagnosis incidents."""
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be a positive integer")

    items = _list_ops_incident_records(
        status=status,
        run_id=run_id,
        session_id=session_id,
        limit=min(limit, 100),
    )
```

Replace with:
```python
@router.get("/ops-incidents", response_model=OpsIncidentListResponse)
def list_ops_incidents(
    status: str | None = None,
    run_id: str | None = None,
    session_id: str | None = None,
    incident_type: str | None = None,
    limit: int = 50,
    _user: Dict[str, Any] = Depends(_require_auth),
) -> OpsIncidentListResponse:
    """Return persisted read-only operations diagnosis incidents.

    `incident_type` filters by type (e.g. 'site_drift' for ADR-035 drift)."""
    if limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be a positive integer")

    items = _list_ops_incident_records(
        status=status,
        run_id=run_id,
        session_id=session_id,
        incident_type=incident_type,
        limit=min(limit, 100),
    )
```

- [ ] **Step 6: Import-and-smoke check**

Run: `python3 -c "import apps.api.routers.diagnostics; print('ok')"`
Expected: `ok`

- [ ] **Step 7: Commit**

```bash
git add javdb/storage/repos/ops_incident_repo.py apps/api/routers/diagnostics.py tests/unit/test_ops_incident_repo_filter.py
git commit -m "feat(db,api): filter ops-incidents by incident_type (ADR-035 Phase 3)"
```

---

## Task 2: `ParseRunFieldFillRepo.latest_committed_fills()`

**Files:**
- Modify: `javdb/storage/repos/parse_run_field_fill_repo.py`
- Test: `tests/unit/test_parse_field_fill_latest.py`

Returns the **newest committed** fill per `(page_type, field)` — the "current health" the web shows. Uncommitted (pending) rows and older runs are excluded.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_parse_field_fill_latest.py
import sqlite3

import pytest

from javdb.storage.repos.parse_run_field_fill_repo import ParseRunFieldFillRepo

_DDL = """
CREATE TABLE ParseRunFieldFill (
  session_id TEXT NOT NULL, page_type TEXT NOT NULL, field TEXT NOT NULL,
  fill_rate REAL NOT NULL, sample_count INTEGER NOT NULL,
  committed INTEGER NOT NULL DEFAULT 0, observed_at TEXT,
  PRIMARY KEY (session_id, page_type, field)
);
"""


def _row(conn, sid, field, rate, observed_at, committed):
    conn.execute(
        "INSERT INTO ParseRunFieldFill "
        "(session_id, page_type, field, fill_rate, sample_count, committed, observed_at) "
        "VALUES (?, 'index', ?, ?, 100, ?, ?)",
        [sid, field, rate, committed, observed_at],
    )


@pytest.fixture
def repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return c, ParseRunFieldFillRepo(c)


def test_latest_committed_wins(repo):
    conn, r = repo
    _row(conn, "S1", "href", 0.90, "2026-06-01T00:00:00Z", 1)
    _row(conn, "S2", "href", 0.95, "2026-06-03T00:00:00Z", 1)  # newer
    _row(conn, "S3", "href", 0.10, "2026-06-04T00:00:00Z", 0)  # newest but uncommitted
    rows = {row[1]: row for row in r.latest_committed_fills()}
    assert rows["href"][2] == 0.95          # fill_rate from S2 (newest committed)
    assert rows["href"][0] == "index"       # page_type
    assert rows["href"][4] == "2026-06-03T00:00:00Z"


def test_excludes_fields_with_no_committed_row(repo):
    conn, r = repo
    _row(conn, "S1", "rate", 0.80, "2026-06-01T00:00:00Z", 0)  # uncommitted only
    assert r.latest_committed_fills() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_parse_field_fill_latest.py -v`
Expected: FAIL — `'ParseRunFieldFillRepo' object has no attribute 'latest_committed_fills'`

- [ ] **Step 3: Add the method**

Append to `class ParseRunFieldFillRepo` in `javdb/storage/repos/parse_run_field_fill_repo.py` (after `mark_committed`):

```python
    def latest_committed_fills(self) -> list[tuple[str, str, float, int, str | None]]:
        """Newest committed fill per (page_type, field): the 'current health'.

        Rows: (page_type, field, fill_rate, sample_count, observed_at). Uncommitted
        rows and older runs are excluded; one row per field."""
        rows = self._conn.execute(
            """
            SELECT page_type, field, fill_rate, sample_count, observed_at
            FROM ParseRunFieldFill p
            WHERE committed = 1
              AND observed_at = (
                SELECT MAX(observed_at) FROM ParseRunFieldFill q
                WHERE q.page_type = p.page_type AND q.field = p.field AND q.committed = 1
              )
            ORDER BY page_type, field
            """
        ).fetchall()
        return [
            (r["page_type"], r["field"], r["fill_rate"], r["sample_count"], r["observed_at"])
            for r in rows
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_parse_field_fill_latest.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/storage/repos/parse_run_field_fill_repo.py tests/unit/test_parse_field_fill_latest.py
git commit -m "feat(db): add latest_committed_fills for the drift surface (ADR-035 Phase 3)"
```

---

## Task 3: `health.py` — pure `compute_field_health`

**Files:**
- Create: `javdb/ops/sentinel/health.py`
- Test: `tests/unit/test_sentinel_health.py`

A read-only view that annotates each latest fill with its contract `severity`, `baseline`, `threshold`, and a `status` — reusing `fields_for()` and mirroring the detector's threshold math (no DB, baseline injected).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_sentinel_health.py
from javdb.ops.sentinel.health import FieldHealth, compute_field_health


def _baseline(_pt, f):
    return {"rate": 0.90}.get(f)


def _rows():
    # (page_type, field, fill_rate, sample_count, observed_at)
    return [
        ("index", "href", 0.10, 100, "t"),   # critical, below 0.99 -> critical_drift
        ("index", "video_code", 1.0, 100, "t"),  # critical, ok
        ("index", "rate", 0.10, 100, "t"),   # soft, baseline 0.90 -> thr 0.45 -> soft_drift
        ("index", "comment_count", 0.80, 100, "t"),  # soft, no baseline -> no_baseline
        ("index", "href", 0.0, 5, "t"),      # below min_sample -> insufficient_sample
    ]


def test_status_matrix():
    out = compute_field_health(_rows(), baseline_fn=_baseline, min_sample=30)
    by = {(h.field, h.fill_rate): h for h in out}
    assert by[("href", 0.10)].status == "critical_drift"
    assert by[("video_code", 1.0)].status == "ok"
    assert by[("rate", 0.10)].status == "soft_drift"
    assert by[("rate", 0.10)].baseline == 0.90
    assert by[("comment_count", 0.80)].status == "no_baseline"
    assert by[("href", 0.0)].status == "insufficient_sample"


def test_unknown_field_is_skipped():
    out = compute_field_health(
        [("index", "not_a_contract_field", 0.0, 100, "t")],
        baseline_fn=_baseline, min_sample=30)
    assert out == []


def test_returns_fieldhealth_instances():
    out = compute_field_health(
        [("index", "href", 1.0, 100, "t")], baseline_fn=_baseline, min_sample=30)
    assert isinstance(out[0], FieldHealth)
    assert out[0].severity == "critical"
    assert out[0].threshold == 0.99
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_sentinel_health.py -v`
Expected: FAIL — `ModuleNotFoundError: javdb.ops.sentinel.health`

- [ ] **Step 3: Write the view**

```python
# javdb/ops/sentinel/health.py
"""Read-only per-field health view for the drift surface (ADR-035 Phase 3).

Annotates the latest committed fill per contract field with severity, baseline,
threshold and a status string. Pure: no DB access (baseline is injected); reuses
the Phase-1 PARSE_CONTRACT as the single source of truth."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from javdb.spider.parse_contract import fields_for

BaselineFn = Callable[[str, str], Optional[float]]


@dataclass(frozen=True)
class FieldHealth:
    page_type: str
    field: str
    severity: str  # 'critical' | 'soft'
    fill_rate: float
    sample_count: int
    observed_at: Optional[str]
    baseline: Optional[float]
    threshold: Optional[float]
    status: str  # ok | critical_drift | soft_drift | no_baseline | insufficient_sample


def _status(spec: dict, fill_rate: float, sample_count: int,
            baseline: Optional[float], min_sample: int) -> tuple[str, Optional[float]]:
    if sample_count < min_sample:
        return "insufficient_sample", None
    if spec["severity"] == "critical":
        threshold = spec["min_fill"]
        return ("critical_drift" if fill_rate < threshold else "ok"), threshold
    # soft
    if baseline is None:
        return "no_baseline", None
    threshold = spec["baseline_rel"] * baseline
    return ("soft_drift" if fill_rate < threshold else "ok"), threshold


def compute_field_health(
    rows: Iterable[tuple], *, baseline_fn: BaselineFn, min_sample: int,
) -> list[FieldHealth]:
    """Annotate latest-fill rows with contract status.

    rows: iterable of (page_type, field, fill_rate, sample_count, observed_at)."""
    out: list[FieldHealth] = []
    for page_type, field_name, fill_rate, sample_count, observed_at in rows:
        spec = fields_for(page_type).get(field_name)
        if spec is None:
            continue  # field not in the contract — nothing to judge
        baseline = baseline_fn(page_type, field_name) if spec["severity"] == "soft" else None
        status, threshold = _status(spec, fill_rate, sample_count, baseline, min_sample)
        out.append(FieldHealth(
            page_type=page_type, field=field_name, severity=spec["severity"],
            fill_rate=fill_rate, sample_count=sample_count, observed_at=observed_at,
            baseline=baseline, threshold=threshold, status=status))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_sentinel_health.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/ops/sentinel/health.py tests/unit/test_sentinel_health.py
git commit -m "feat(sentinel): add pure per-field health view (ADR-035 Phase 3)"
```

---

## Task 4: `GET /api/diag/parse-field-health` endpoint

**Files:**
- Modify: `apps/api/schemas/diagnostics.py`
- Modify: `apps/api/routers/diagnostics.py`
- Test: `tests/unit/test_parse_field_health_endpoint.py`

- [ ] **Step 1: Write the failing test** (drives the injectable helper + route registration)

```python
# tests/unit/test_parse_field_health_endpoint.py
import sqlite3

import pytest

from javdb.storage.repos.parse_run_field_fill_repo import ParseRunFieldFillRepo

_DDL = """
CREATE TABLE ParseRunFieldFill (
  session_id TEXT NOT NULL, page_type TEXT NOT NULL, field TEXT NOT NULL,
  fill_rate REAL NOT NULL, sample_count INTEGER NOT NULL,
  committed INTEGER NOT NULL DEFAULT 0, observed_at TEXT,
  PRIMARY KEY (session_id, page_type, field)
);
"""


def _row(conn, sid, field, rate, observed_at, committed=1, sample=100):
    conn.execute(
        "INSERT INTO ParseRunFieldFill "
        "(session_id, page_type, field, fill_rate, sample_count, committed, observed_at) "
        "VALUES (?, 'index', ?, ?, ?, ?, ?)",
        [sid, field, rate, sample, committed, observed_at],
    )


@pytest.fixture
def repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return c, ParseRunFieldFillRepo(c)


def test_compute_parse_field_health_with_injected_repo(repo):
    from apps.api.routers.diagnostics import _compute_parse_field_health
    conn, r = repo
    _row(conn, "S1", "href", 0.05, "2026-06-03T00:00:00Z")  # critical -> critical_drift
    items = _compute_parse_field_health(repo=r)
    href = {i.field: i for i in items}["href"]
    assert href.status == "critical_drift"
    assert href.severity == "critical"
    assert href.page_type == "index"


def test_route_is_registered():
    from apps.api.services.runtime import app
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/api/diag/parse-field-health" in paths
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_parse_field_health_endpoint.py -v`
Expected: FAIL — `cannot import name '_compute_parse_field_health'` / route absent.

- [ ] **Step 3: Add the schemas** — append to `apps/api/schemas/diagnostics.py` (before `__all__`):

```python
class ParseFieldHealthItem(BaseModel):
    """Latest committed parse health for one contract field (ADR-035 Phase 3)."""

    page_type: str
    field: str
    severity: str
    fill_rate: float
    sample_count: int
    observed_at: Optional[str] = None
    baseline: Optional[float] = None
    threshold: Optional[float] = None
    status: str


class ParseFieldHealthResponse(BaseModel):
    """List response for per-field parse health."""

    items: list[ParseFieldHealthItem]
```

Then add both names to `__all__` in that file:

```python
    "OpsIncidentListResponse",
    "OpsIncidentSchema",
    "ParseFieldHealthItem",
    "ParseFieldHealthResponse",
```

- [ ] **Step 4: Add the endpoint + helpers** in `apps/api/routers/diagnostics.py`.

Extend the schema import (add the two names to the existing `from apps.api.schemas.diagnostics import (...)` block):

```python
    OpsIncidentListResponse,
    OpsIncidentSchema,
    ParseFieldHealthItem,
    ParseFieldHealthResponse,
```

Add these top-level imports near the other `javdb.*` imports:

```python
from javdb.ops.sentinel.health import compute_field_health
from javdb.storage.repos.parse_run_field_fill_repo import ParseRunFieldFillRepo
```

Add the helpers (place them next to `_ops_record_to_schema`):

```python
def _field_health_to_schema(h) -> ParseFieldHealthItem:
    return ParseFieldHealthItem(
        page_type=h.page_type,
        field=h.field,
        severity=h.severity,
        fill_rate=h.fill_rate,
        sample_count=h.sample_count,
        observed_at=h.observed_at,
        baseline=h.baseline,
        threshold=h.threshold,
        status=h.status,
    )


def _field_health_items(repo, min_sample: int, window: int) -> list[ParseFieldHealthItem]:
    rows = repo.latest_committed_fills()
    health = compute_field_health(
        rows, min_sample=min_sample,
        baseline_fn=lambda pt, f: repo.baseline(pt, f, window=window),
    )
    return [_field_health_to_schema(h) for h in health]


def _compute_parse_field_health(*, repo=None) -> list[ParseFieldHealthItem]:
    min_sample = int(cfg("SENTINEL_MIN_SAMPLE", 30))
    window = int(cfg("SENTINEL_BASELINE_WINDOW", 14))
    if repo is not None:
        return _field_health_items(repo, min_sample, window)
    with get_db(REPORTS_DB_PATH) as conn:
        return _field_health_items(ParseRunFieldFillRepo(conn), min_sample, window)
```

Add the route (next to the `ops-incidents` routes):

```python
@router.get("/parse-field-health", response_model=ParseFieldHealthResponse)
def get_parse_field_health(
    _user: Dict[str, Any] = Depends(_require_auth),
) -> ParseFieldHealthResponse:
    """Latest committed per-field parse health (ADR-035 site-contract sentinel)."""
    return ParseFieldHealthResponse(items=_compute_parse_field_health())
```

Add `get_parse_field_health` to the router module's `__all__` list.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/test_parse_field_health_endpoint.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add apps/api/schemas/diagnostics.py apps/api/routers/diagnostics.py tests/unit/test_parse_field_health_endpoint.py
git commit -m "feat(api): add GET /api/diag/parse-field-health (ADR-035 Phase 3)"
```

---

## Task 5: Capability flag `site_drift_sentinel`

**Files:**
- Modify: `apps/api/schemas/capabilities_payloads.py`
- Modify: `apps/api/routers/capabilities.py`
- Test: `tests/unit/test_capabilities_site_drift_flag.py`

Per ADR-034 D4, the frontend hides the drift panel when the flag is false. The sentinel ships with the system (Phase 1 is live), so the flag defaults **true**; `FEATURE_SITE_DRIFT_SENTINEL=false` hides it.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_capabilities_site_drift_flag.py
from apps.api.routers.capabilities import build_capabilities


def test_flag_defaults_true(monkeypatch):
    monkeypatch.delenv("FEATURE_SITE_DRIFT_SENTINEL", raising=False)
    caps = build_capabilities()
    assert caps.features.site_drift_sentinel is True


def test_flag_env_override_false(monkeypatch):
    monkeypatch.setenv("FEATURE_SITE_DRIFT_SENTINEL", "false")
    caps = build_capabilities()
    assert caps.features.site_drift_sentinel is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_capabilities_site_drift_flag.py -v`
Expected: FAIL — `Features` has no `site_drift_sentinel` (Pydantic validation error or AttributeError).

- [ ] **Step 3: Add the field to the schema** — in `apps/api/schemas/capabilities_payloads.py`:

Find:
```python
class Features(BaseModel):
    pikpak: bool
    rclone: bool
    smtp: bool
    proxy_pool: bool
    javdb_login: bool
    proxy_preview: bool
```

Replace with:
```python
class Features(BaseModel):
    pikpak: bool
    rclone: bool
    smtp: bool
    proxy_pool: bool
    javdb_login: bool
    proxy_preview: bool
    site_drift_sentinel: bool
```

- [ ] **Step 4: Populate it** — in `apps/api/routers/capabilities.py`, inside the `Features(...)` constructor in `build_capabilities()`:

Find:
```python
        features=Features(
            pikpak=_bool_env("FEATURE_PIKPAK"),
            rclone=_bool_env("FEATURE_RCLONE"),
            smtp=bool(os.getenv("SMTP_HOST") or os.getenv("SMTP_SERVER")),
            proxy_pool=_bool_env("PROXY_MODE_POOL", default=True),
            javdb_login=bool(os.getenv("JAVDB_USERNAME")),
            proxy_preview=True,
        ),
```

Replace with:
```python
        features=Features(
            pikpak=_bool_env("FEATURE_PIKPAK"),
            rclone=_bool_env("FEATURE_RCLONE"),
            smtp=bool(os.getenv("SMTP_HOST") or os.getenv("SMTP_SERVER")),
            proxy_pool=_bool_env("PROXY_MODE_POOL", default=True),
            javdb_login=bool(os.getenv("JAVDB_USERNAME")),
            proxy_preview=True,
            # ADR-035: site-contract drift sentinel ships with the system; the
            # frontend hides the drift panel only when explicitly disabled.
            site_drift_sentinel=_bool_env("FEATURE_SITE_DRIFT_SENTINEL", default=True),
        ),
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/test_capabilities_site_drift_flag.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add apps/api/schemas/capabilities_payloads.py apps/api/routers/capabilities.py tests/unit/test_capabilities_site_drift_flag.py
git commit -m "feat(api): add site_drift_sentinel capability flag (ADR-035 Phase 3)"
```

---

## Task 6: Regenerate `openapi.json`, docs, and the TS-mirror follow-up

**Files:**
- Modify (generated): `docs/api/openapi.json`
- Modify: `docs/handbook/en/developer/*` (+ `docs/handbook/zh/developer/*`)

- [ ] **Step 1: Regenerate the OpenAPI contract**

Run:
```bash
python3 -m apps.cli.ops.dump_openapi
```
Expected: `wrote .../docs/api/openapi.json (... bytes)`.

- [ ] **Step 2: Confirm the new surface is in the contract**

Run:
```bash
python3 -c "import json; s=json.load(open('docs/api/openapi.json')); p=s['paths']; \
print('/api/diag/parse-field-health' in p); \
print(any('incident_type' == q.get('name') for q in p['/api/diag/ops-incidents']['get'].get('parameters', [])))"
```
Expected: `True` then `True`.

- [ ] **Step 3: Document the endpoints** — locate the developer API reference and add the two endpoints; mirror into the paired `zh` file (translate prose, keep paths/JSON verbatim).

Run: `grep -rl "ops-incidents\|/api/diag" docs/handbook/en/developer/ || ls docs/handbook/en/developer/`

Add, in the diagnostics section:

```text
GET /api/diag/ops-incidents?incident_type=site_drift   # filter drift incidents
GET /api/diag/parse-field-health                        # latest per-field parse health
  -> { items: [ { page_type, field, severity, fill_rate, sample_count,
                  observed_at, baseline, threshold, status } ] }
  status ∈ ok | critical_drift | soft_drift | no_baseline | insufficient_sample
Gated by capabilities.features.site_drift_sentinel.
```

Mirror the same block into the paired `docs/handbook/zh/developer/<same-file>.md`.

- [ ] **Step 4: Record the TypeScript-mirror follow-up** (dual-backend rule, CLAUDE.md / ADR-034 D3/D7).

The overlapping surface added here must be mirrored in the separate `javdb-autospider-web` repo (not in this monorepo, so it is a **linked follow-up**, not implemented in this PR). Append a tracked checklist to ADR-035's Status Log (Task 7 of this plan updates the ADR) **and** open a follow-up issue capturing exactly:

```text
TS Worker mirror (javdb-autospider-web/server/) — ADR-035 Phase 3 parity:
  1. GET /api/diag/ops-incidents: add `incident_type` query param -> WHERE incident_type = ?
     (same D1 SQL clause as javdb/storage/repos/ops_incident_repo.py::list).
  2. GET /api/diag/parse-field-health: new route. Port latest_committed_fills()
     (correlated MAX(observed_at) per (page_type, field) WHERE committed=1) +
     compute_field_health status logic (PARSE_CONTRACT mirror) -> identical JSON shape.
  3. capabilities.features.site_drift_sentinel: add the boolean (default true) to the
     TS capabilities builder; frontend hides the drift panel when false.
  Contract: docs/api/openapi.json (regenerated in Step 1) is the seam — match it exactly.
```

> Do **not** attempt to edit the TS repo from this worktree (it is not checked
> out here). If it is added to the workspace later, implement the three items
> above in the same review and tick them off in the ADR Status Log.

- [ ] **Step 5: Full verification gate**

Run:
```bash
pytest tests/unit/test_ops_incident_repo_filter.py tests/unit/test_parse_field_fill_latest.py \
       tests/unit/test_sentinel_health.py tests/unit/test_parse_field_health_endpoint.py \
       tests/unit/test_capabilities_site_drift_flag.py -v
```
Expected: all PASS.

- [ ] **Step 6: Read-surface invariant — Phase 3 never writes**

Run:
```bash
grep -rnE "INSERT|UPDATE|DELETE|upsert|mark_committed" javdb/ops/sentinel/health.py \
  apps/api/routers/diagnostics.py | grep -v "def list" || echo "read-only ok"
```
Expected: `read-only ok` (the health view and the two endpoints only read; the only `list`/`SELECT` paths are queries). If the grep prints a line, it must be a pre-existing write in `diagnostics.py` unrelated to ADR-035 (e.g. the `javdb-session/refresh` POST) — confirm it is not in the new helpers.

- [ ] **Step 7: Commit**

```bash
git add docs/api/openapi.json docs/handbook
git commit -m "docs(api): regenerate openapi + document drift surface (ADR-035 Phase 3)"
```

---

## Plan Self-Review

**Spec coverage (ADR-035 Phase 3 row + D-decisions):**
- Per-field health on web (ADR-034 pattern) → `GET /api/diag/parse-field-health` (Tasks 2–4), JWT-auth + capability-gated (Tasks 4–5), `openapi.json` seam (Task 6). ✓
- AI drift summary (D6) → `incident_type='site_drift'` filter (Task 1) makes drift incidents queryable by the web **and** ADR-026's AI summariser; no new AI client built (scope note). ✓
- Reuse ADR-026 incident surface, no new alerting (D6) → extends the existing `/api/diag/ops-incidents`; no new table, no new router. ✓
- Capability gating (ADR-034 D4) → `Features.site_drift_sentinel` (Task 5). ✓
- Dual-backend parity (ADR-034 D3/D7, CLAUDE.md) → TS mirror specified + tracked as a linked follow-up (Task 6 Step 4); repo not on disk, so not implemented here — explicitly. ✓
- Read-only surface over Phase-1/2 data → enforced by the Task 6 Step 6 grep. ✓

**Type consistency:** `OpsIncidentRepo.list(incident_type=...)`, `latest_committed_fills()` (5-tuple rows), `compute_field_health(rows, *, baseline_fn, min_sample)`, `FieldHealth`, `ParseFieldHealthItem`/`ParseFieldHealthResponse`, `_compute_parse_field_health(*, repo=None)`, and `Features.site_drift_sentinel` are used identically across Tasks 1–6. The `status` vocabulary (`ok`/`critical_drift`/`soft_drift`/`no_baseline`/`insufficient_sample`) is fixed in Task 3 and asserted verbatim in the Task 3 + Task 4 tests and documented in Task 6 Step 3. ✓

**Placeholder scan:** No invented data. The endpoint test injects an in-memory repo (`_compute_parse_field_health(repo=...)`) rather than standing up the auth/DB harness; route registration is checked against the real `app.routes`. The TS mirror is a precisely-specified follow-up, not a vague "update TS too". ✓

**Integration points needing in-file location (exact find/replace given):**
Task 1 (`OpsIncidentRepo.list` body + the `_list_ops_incident_records` helper + the `list_ops_incidents` route — all quoted verbatim), Task 4 (schema `__all__`, the diagnostics schema-import block, and helper placement — quoted), Task 5 (`Features` + the `Features(...)` constructor — quoted), Task 6 Step 3 (`grep -rl` to find the API doc). Each gives the exact anchor text plus a verifying command.

**Known coupling (resolved):** Phase 3 adds no DB columns and changes no Phase-1/2 write path — it only reads `ParseRunFieldFill` (Phase 1) and `OpsIncidents` (ADR-026). `OpsIncidentRepo.list` gains an **optional** `incident_type` (default `None` preserves every existing caller, incl. the unchanged `status`/`run_id`/`session_id` filters). `Features` gains a required field; `build_capabilities` populates it in the same task, and the OpenAPI regen (Task 6) keeps the contract in sync. The TS backend is updated via the tracked follow-up (Task 6 Step 4).

**Open verification dependency:** the TypeScript Worker mirror lands in `javdb-autospider-web` (not in this repo); it is specified and tracked, not implemented here. No production D1 schema change is introduced — both tables are already live in `javdb-reports`.
