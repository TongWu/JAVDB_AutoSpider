# IMP-ADR035-01: Piggyback Telemetry + Commit Gate (Site-Contract Sentinel Phase 1) Implementation Plan

**Status:** Completed (2026-05-31) — Phase 1 implemented, verified, and closed out; see As-Built Notes below.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-035](ADR-035-site-contract-drift-sentinel.md) (umbrella) — this is **Phase 1** of three.

**Goal:** Detect silent field-level parser drift on the daily run by recording per-field fill-rate from the live index parse, then **gate the session commit** — critical-field collapse refuses to commit (protecting the DB), soft-field collapse raises an advisory — reusing the pending→commit lifecycle and the ADR-026 incident surface.

**Architecture:** The spider's index parse boundary feeds a per-run `FieldHealthAccumulator`; at the end of the index flow the fills are **persisted** to a new D1 `ParseRunFieldFill` table keyed by `session_id` (spider and commit are separate processes, so in-process state cannot bridge them). The separate commit step reads those fills, evaluates them against a declarative `PARSE_CONTRACT` (critical = absolute `min_fill`; soft = relative to a baseline computed from recent committed runs), and gates `db_commit_session_history`. Drift becomes an `OpsIncidents` row (`incident_type='site_drift'`).

**Tech Stack:** Python 3, `sqlite3`/D1 via `javdb.storage.db.get_db`, `dataclasses`, `statistics.median`, `pytest`, Cloudflare D1 + `wrangler`.

**Storage placement:** `ParseRunFieldFill` lives in the **reports** logical DB (`javdb-reports`), alongside `OpsIncidents` and `SpiderStats`.

**Scope correction vs. ADR-035 D8:** Phase 1 uses **one** table (`ParseRunFieldFill`) and computes the soft-field baseline on the fly (median over recent committed rows), instead of a separate `ParseFieldHealth` EMA table. This satisfies D5 (slow baseline via median-over-window; clean-only via the `committed` filter) with less surface. A dedicated EMA baseline table is a possible Phase 2 optimisation. Phase 1 wires the **index** parse boundary only; detail-page fills are a documented fast-follow.

---

## File Structure

| Path | Create/Modify | Responsibility |
| --- | --- | --- |
| `javdb/migrations/d1/2026_05_29_add_parse_run_field_fill.sql` | Create | `ParseRunFieldFill` DDL + indexes |
| `javdb/spider/parse_contract.py` | Create | Declarative `PARSE_CONTRACT` + `fields_for()` |
| `javdb/ops/sentinel/__init__.py` | Create | Package marker + re-exports |
| `javdb/ops/sentinel/models.py` | Create | `FieldFill`, `DriftFinding`, `SentinelVerdict`, `SentinelOptions`, `utc_now_iso` |
| `javdb/ops/sentinel/field_health.py` | Create | `FieldHealthAccumulator` + process-global run accumulator |
| `javdb/storage/repos/parse_run_field_fill_repo.py` | Create | `ParseRunFieldFillRepo` (upsert / get / baseline / mark_committed) |
| `javdb/ops/sentinel/detectors.py` | Create | `evaluate()` pure drift evaluation |
| `javdb/ops/sentinel/persistence.py` | Create | `get_db` wiring + `build_drift_incident()` (reuse OpsIncidentRepo) |
| `javdb/ops/sentinel/service.py` | Create | `persist_run()`, `evaluate_session()`, `mark_committed()` (sole writer of fills/incidents) |
| `apps/cli/ops/sentinel.py` | Create | CLI: evaluate a session's persisted fills → report + exit code |
| `javdb/spider/fetch/index.py` | Modify | Observe per-page fills; persist at end of index flow |
| `apps/cli/db/commit_session.py` | Modify | Gate before `db_commit_session_history` (critical → fail path) |
| `config.py.example` | Modify | Document `SENTINEL_MIN_SAMPLE`, `SENTINEL_BASELINE_WINDOW` |
| `CONTEXT.md`, `docs/handbook/en/developer/cli-reference.md` (+ zh) | Modify | Domain terms + new CLI |
| `tests/unit/test_parse_contract.py` … (see tasks) | Create | Unit + fixture-based success-criteria tests |

**Naming contract (verbatim across tasks):**
`PARSE_CONTRACT` (dict) + `fields_for(page_type) -> dict`; `FieldFill(page_type, field, fill_rate, sample_count)`; `DriftFinding(page_type, field, severity, fill_rate, threshold, baseline)`; `SentinelVerdict(critical: bool, findings: list[DriftFinding], evaluated: int)`; `FieldHealthAccumulator` with `observe(page_type, records)` / `fill_rates() -> list[FieldFill]`; module fns `start_run()`, `current()`, `persist_run(*, repo=None)`; `ParseRunFieldFillRepo` with `upsert_fills(session_id, fills)`, `get_fills(session_id)`, `baseline(page_type, field, *, window)`, `mark_committed(session_id)`; service `persist_run(...)`, `evaluate_session(session_id, *, run_id=None, run_attempt=None, fill_repo=None, incident_repo=None) -> SentinelVerdict`, `mark_committed(session_id, *, repo=None)`.

> **Phase-2-gated:** the independent canary (`probes.py`, `SiteContractSentinel.yml`) is **not** in this plan. Detail-page fills and a dedicated EMA baseline table are documented fast-follows, not built here.

---

## Task 1: D1 migration — `ParseRunFieldFill`

**Files:**
- Create: `javdb/migrations/d1/2026_05_29_add_parse_run_field_fill.sql`

- [ ] **Step 1: Write the migration SQL**

```sql
-- 2026-05-29: Add ParseRunFieldFill table (ADR-035 Phase 1).
--
-- Apply with:
--   wrangler d1 execute javdb-reports --remote \
--     --file=javdb/migrations/d1/2026_05_29_add_parse_run_field_fill.sql
--
-- Per (session_id, page_type, field): the fill-rate observed during that run's
-- live parse. The commit gate reads these; the soft-field baseline is the median
-- over recent rows where committed=1. Enrichment, off the Pending->Commit path.

CREATE TABLE IF NOT EXISTS ParseRunFieldFill (
  session_id    TEXT NOT NULL,
  page_type     TEXT NOT NULL,
  field         TEXT NOT NULL,
  fill_rate     REAL NOT NULL,
  sample_count  INTEGER NOT NULL,
  committed     INTEGER NOT NULL DEFAULT 0,
  observed_at   TEXT,
  PRIMARY KEY (session_id, page_type, field)
);

CREATE INDEX IF NOT EXISTS idx_prff_field_committed
  ON ParseRunFieldFill(page_type, field, committed, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_prff_session ON ParseRunFieldFill(session_id);
```

- [ ] **Step 2: Apply to D1, then re-align SQLite (D1-canonical rule)**

Run:
```bash
wrangler d1 execute javdb-reports --remote \
  --file=javdb/migrations/d1/2026_05_29_add_parse_run_field_fill.sql
python3 -m apps.cli.db.sync_d1_to_sqlite --apply --force-overwrite-all
```
Expected: statements execute; `ParseRunFieldFill` rebuilt locally from D1 DDL; exit 0.

- [ ] **Step 3: Verify the table exists locally**

Run:
```bash
python3 -c "import sqlite3,glob; p=glob.glob('reports/reports.db')[0]; print(sqlite3.connect(p).execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name='ParseRunFieldFill'\").fetchone())"
```
Expected: `('ParseRunFieldFill',)`

- [ ] **Step 4: Commit**

```bash
git add javdb/migrations/d1/2026_05_29_add_parse_run_field_fill.sql
git commit -m "feat(db): add ParseRunFieldFill table (ADR-035 Phase 1)"
```

---

## Task 2: Declarative parse contract

**Files:**
- Create: `javdb/spider/parse_contract.py`
- Test: `tests/unit/test_parse_contract.py`

Field names match the real parser models (`javdb/parsing/models.py`): `MovieIndexEntry`
has `href, video_code, title, rate, comment_count, release_date`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_parse_contract.py
from javdb.spider.parse_contract import PARSE_CONTRACT, fields_for


def test_index_critical_fields_present():
    idx = fields_for("index")
    assert idx["href"]["severity"] == "critical"
    assert idx["video_code"]["severity"] == "critical"
    assert idx["rate"]["severity"] == "soft"


def test_critical_has_min_fill_soft_has_baseline_rel():
    for spec in fields_for("index").values():
        if spec["severity"] == "critical":
            assert "min_fill" in spec
        else:
            assert "baseline_rel" in spec


def test_fields_for_unknown_page_type_is_empty():
    assert fields_for("nope") == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_parse_contract.py -v`
Expected: FAIL — `ModuleNotFoundError: javdb.spider.parse_contract`

- [ ] **Step 3: Write the contract**

```python
# javdb/spider/parse_contract.py
"""Declarative parse contract for the site-contract drift sentinel (ADR-035).

Field names mirror javdb/parsing/models.py. 'critical' fields gate the commit
(absolute min_fill); 'soft' fields warn (fill below baseline_rel x baseline)."""

from __future__ import annotations

PARSE_CONTRACT: dict[str, dict[str, dict]] = {
    "index": {
        "href":         {"severity": "critical", "min_fill": 0.99},
        "video_code":   {"severity": "critical", "min_fill": 0.99},
        "title":        {"severity": "critical", "min_fill": 0.95},
        "rate":         {"severity": "soft",     "baseline_rel": 0.5},
        "comment_count":{"severity": "soft",     "baseline_rel": 0.5},
        "release_date": {"severity": "soft",     "baseline_rel": 0.5},
    },
    # detail-page fields are contract-ready; wiring the detail boundary is a
    # documented Phase-1 fast-follow (see plan header).
    "detail": {
        "video_code":   {"severity": "critical", "min_fill": 0.99},
        "title":        {"severity": "critical", "min_fill": 0.95},
        "magnets":      {"severity": "critical", "min_fill": 0.90},
        "actors":       {"severity": "soft",     "baseline_rel": 0.5},
        "rate":         {"severity": "soft",     "baseline_rel": 0.5},
        "release_date": {"severity": "soft",     "baseline_rel": 0.5},
    },
}


def fields_for(page_type: str) -> dict[str, dict]:
    return PARSE_CONTRACT.get(page_type, {})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_parse_contract.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/spider/parse_contract.py tests/unit/test_parse_contract.py
git commit -m "feat(spider): add declarative parse contract (ADR-035)"
```

---

## Task 3: Sentinel models

**Files:**
- Create: `javdb/ops/sentinel/__init__.py`
- Create: `javdb/ops/sentinel/models.py`
- Test: `tests/unit/test_sentinel_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_sentinel_models.py
from javdb.ops.sentinel.models import (
    FieldFill, DriftFinding, SentinelVerdict, SentinelOptions, utc_now_iso,
)


def test_utc_now_iso_trailing_z():
    assert utc_now_iso().endswith("Z")


def test_field_fill_fields():
    f = FieldFill(page_type="index", field="href", fill_rate=0.98, sample_count=120)
    assert f.fill_rate == 0.98


def test_verdict_defaults():
    v = SentinelVerdict()
    assert v.critical is False
    assert v.findings == []
    assert v.evaluated == 0


def test_options_defaults():
    o = SentinelOptions()
    assert o.min_sample == 30
    assert o.baseline_window == 14
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_sentinel_models.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create package + models**

```python
# javdb/ops/sentinel/__init__.py
"""Site-contract drift sentinel (ADR-035 Phase 1)."""
```

```python
# javdb/ops/sentinel/models.py
"""Typed contracts for the site-contract drift sentinel (ADR-035 Phase 1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional

Severity = Literal["critical", "soft"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class FieldFill:
    page_type: str
    field: str
    fill_rate: float
    sample_count: int


@dataclass(frozen=True)
class DriftFinding:
    page_type: str
    field: str
    severity: Severity
    fill_rate: float
    threshold: float
    baseline: Optional[float] = None


@dataclass
class SentinelVerdict:
    critical: bool = False
    findings: list[DriftFinding] = field(default_factory=list)
    evaluated: int = 0


@dataclass
class SentinelOptions:
    min_sample: int = 30
    baseline_window: int = 14
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_sentinel_models.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/ops/sentinel/__init__.py javdb/ops/sentinel/models.py tests/unit/test_sentinel_models.py
git commit -m "feat(sentinel): add models (ADR-035 Phase 1)"
```

---

## Task 4: `FieldHealthAccumulator` (piggyback aggregation)

**Files:**
- Create: `javdb/ops/sentinel/field_health.py`
- Test: `tests/unit/test_field_health_accumulator.py`

`observe(page_type, records)` counts, per contract field, how many records have a
non-empty value (truthy str after strip; non-empty list). It reads field values via
`getattr` (dataclasses) falling back to `dict.get`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_field_health_accumulator.py
from dataclasses import dataclass, field as dfield

from javdb.ops.sentinel.field_health import FieldHealthAccumulator


@dataclass
class _Row:
    href: str = ""
    video_code: str = ""
    title: str = ""
    rate: str = ""
    comment_count: str = ""
    release_date: str = ""


def test_fill_rates_count_non_empty():
    acc = FieldHealthAccumulator()
    acc.observe("index", [
        _Row(href="/v/1", video_code="A-1", title="t", rate="4.1"),
        _Row(href="/v/2", video_code="A-2", title="t2", rate=""),  # rate empty
    ])
    fills = {f.field: f for f in acc.fill_rates()}
    assert fills["href"].fill_rate == 1.0
    assert fills["rate"].fill_rate == 0.5
    assert fills["href"].sample_count == 2


def test_observe_accumulates_across_calls():
    acc = FieldHealthAccumulator()
    acc.observe("index", [_Row(href="/v/1", video_code="A-1", title="t")])
    acc.observe("index", [_Row(href="", video_code="A-2", title="t")])
    fills = {f.field: f for f in acc.fill_rates()}
    assert fills["href"].fill_rate == 0.5
    assert fills["href"].sample_count == 2


def test_empty_observation_yields_no_fills():
    acc = FieldHealthAccumulator()
    assert acc.fill_rates() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_field_health_accumulator.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the accumulator**

```python
# javdb/ops/sentinel/field_health.py
"""Per-run field-fill aggregation for the drift sentinel (read-only piggyback).

Observes parsed records at the parse boundary and counts non-empty values per
contract field. Never writes the DB; persistence is the service's job."""

from __future__ import annotations

import logging

from javdb.ops.sentinel.models import FieldFill
from javdb.spider.parse_contract import fields_for

logger = logging.getLogger(__name__)


def _value(record, name):
    if hasattr(record, name):
        return getattr(record, name)
    if isinstance(record, dict):
        return record.get(name)
    return None


def _is_filled(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True  # numbers/bools count as present


class FieldHealthAccumulator:
    def __init__(self) -> None:
        # (page_type, field) -> [filled_count, total_count]
        self._counts: dict[tuple[str, str], list[int]] = {}

    def observe(self, page_type: str, records) -> None:
        spec = fields_for(page_type)
        if not spec:
            return
        for record in records:
            for name in spec:
                slot = self._counts.setdefault((page_type, name), [0, 0])
                slot[1] += 1
                if _is_filled(_value(record, name)):
                    slot[0] += 1

    def fill_rates(self) -> list[FieldFill]:
        out: list[FieldFill] = []
        for (page_type, name), (filled, total) in self._counts.items():
            if total == 0:
                continue
            out.append(FieldFill(page_type, name, filled / total, total))
        return out


# --- process-global current-run accumulator (single spider process) ----------
_CURRENT: FieldHealthAccumulator | None = None


def start_run() -> FieldHealthAccumulator:
    global _CURRENT
    _CURRENT = FieldHealthAccumulator()
    return _CURRENT


def current() -> FieldHealthAccumulator | None:
    return _CURRENT


def persist_run(*, repo=None) -> int:
    """Persist the current run's fills via the service. No-op if no run started.
    Best-effort: logs and swallows on failure (must not break the spider)."""
    acc = _CURRENT
    if acc is None:
        return 0
    fills = acc.fill_rates()
    if not fills:
        return 0
    try:
        from javdb.ops.sentinel.service import persist_run as _svc_persist
        return _svc_persist(fills, repo=repo)
    except Exception:
        logger.warning("field_health.persist_run failed", exc_info=True)
        return 0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_field_health_accumulator.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/ops/sentinel/field_health.py tests/unit/test_field_health_accumulator.py
git commit -m "feat(sentinel): add FieldHealthAccumulator piggyback aggregation"
```

---

## Task 5: `ParseRunFieldFillRepo`

**Files:**
- Create: `javdb/storage/repos/parse_run_field_fill_repo.py`
- Test: `tests/unit/test_parse_run_field_fill_repo.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_parse_run_field_fill_repo.py
import sqlite3

import pytest

from javdb.ops.sentinel.models import FieldFill
from javdb.storage.repos.parse_run_field_fill_repo import ParseRunFieldFillRepo

_DDL = """
CREATE TABLE ParseRunFieldFill (
  session_id TEXT NOT NULL, page_type TEXT NOT NULL, field TEXT NOT NULL,
  fill_rate REAL NOT NULL, sample_count INTEGER NOT NULL,
  committed INTEGER NOT NULL DEFAULT 0, observed_at TEXT,
  PRIMARY KEY (session_id, page_type, field)
);
"""


@pytest.fixture
def repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return ParseRunFieldFillRepo(c)


def test_upsert_and_get(repo):
    repo.upsert_fills("S1", [FieldFill("index", "href", 0.97, 100)])
    got = {f.field: f for f in repo.get_fills("S1")}
    assert got["href"].fill_rate == 0.97


def test_upsert_idempotent(repo):
    repo.upsert_fills("S1", [FieldFill("index", "href", 0.9, 100)])
    repo.upsert_fills("S1", [FieldFill("index", "href", 0.5, 100)])
    got = {f.field: f for f in repo.get_fills("S1")}
    assert got["href"].fill_rate == 0.5


def test_baseline_uses_committed_only_median(repo):
    repo.upsert_fills("S1", [FieldFill("index", "rate", 0.90, 100)]); repo.mark_committed("S1")
    repo.upsert_fills("S2", [FieldFill("index", "rate", 0.80, 100)]); repo.mark_committed("S2")
    repo.upsert_fills("S3", [FieldFill("index", "rate", 0.10, 100)])  # NOT committed
    assert repo.baseline("index", "rate", window=14) == 0.85  # median(0.90, 0.80)


def test_baseline_none_when_no_committed_rows(repo):
    repo.upsert_fills("S1", [FieldFill("index", "rate", 0.9, 100)])  # uncommitted
    assert repo.baseline("index", "rate", window=14) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_parse_run_field_fill_repo.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the repo**

```python
# javdb/storage/repos/parse_run_field_fill_repo.py
"""Repository for ADR-035 ParseRunFieldFill rows (reports DB)."""

from __future__ import annotations

import logging
import sqlite3
import statistics
from typing import Optional

from javdb.ops.sentinel.models import FieldFill, utc_now_iso

logger = logging.getLogger(__name__)


class ParseRunFieldFillRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        try:
            self._conn.row_factory = sqlite3.Row
        except Exception:
            logger.debug("row_factory set failed", exc_info=True)

    def upsert_fills(self, session_id: str, fills: list[FieldFill]) -> None:
        now = utc_now_iso()
        self._conn.executemany(
            """
            INSERT INTO ParseRunFieldFill
              (session_id, page_type, field, fill_rate, sample_count, committed, observed_at)
            VALUES (?, ?, ?, ?, ?, 0, ?)
            ON CONFLICT(session_id, page_type, field) DO UPDATE SET
              fill_rate=excluded.fill_rate,
              sample_count=excluded.sample_count,
              observed_at=excluded.observed_at
            """,
            [(session_id, f.page_type, f.field, f.fill_rate, f.sample_count, now) for f in fills],
        )

    def get_fills(self, session_id: str) -> list[FieldFill]:
        rows = self._conn.execute(
            "SELECT page_type, field, fill_rate, sample_count "
            "FROM ParseRunFieldFill WHERE session_id = ?",
            [session_id],
        ).fetchall()
        return [FieldFill(r["page_type"], r["field"], r["fill_rate"], r["sample_count"]) for r in rows]

    def baseline(self, page_type: str, field: str, *, window: int) -> Optional[float]:
        rows = self._conn.execute(
            """
            SELECT fill_rate FROM ParseRunFieldFill
            WHERE page_type = ? AND field = ? AND committed = 1
            ORDER BY observed_at DESC LIMIT ?
            """,
            [page_type, field, window],
        ).fetchall()
        values = [r["fill_rate"] for r in rows]
        return statistics.median(values) if values else None

    def mark_committed(self, session_id: str) -> None:
        self._conn.execute(
            "UPDATE ParseRunFieldFill SET committed = 1 WHERE session_id = ?",
            [session_id],
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_parse_run_field_fill_repo.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/storage/repos/parse_run_field_fill_repo.py tests/unit/test_parse_run_field_fill_repo.py
git commit -m "feat(db): add ParseRunFieldFillRepo with committed-only baseline"
```

---

## Task 6: `detectors.evaluate` (pure drift evaluation)

**Files:**
- Create: `javdb/ops/sentinel/detectors.py`
- Test: `tests/unit/test_sentinel_detectors.py`

`evaluate(fills, *, min_sample, baseline_fn)` is pure. `baseline_fn(page_type, field)`
returns the soft-field baseline (or None). It applies the contract, honours the
sample-size guard, and returns a `SentinelVerdict`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_sentinel_detectors.py
from javdb.ops.sentinel.detectors import evaluate
from javdb.ops.sentinel.models import FieldFill


def _no_baseline(_pt, _f):
    return None


def test_critical_below_min_fill_sets_critical():
    fills = [FieldFill("index", "href", 0.10, 100)]  # min_fill 0.99
    v = evaluate(fills, min_sample=30, baseline_fn=_no_baseline)
    assert v.critical is True
    assert v.findings[0].field == "href"
    assert v.findings[0].severity == "critical"


def test_critical_ok_when_above_min_fill():
    v = evaluate([FieldFill("index", "href", 1.0, 100)], min_sample=30, baseline_fn=_no_baseline)
    assert v.critical is False
    assert v.findings == []


def test_soft_below_baseline_rel_is_soft_not_critical():
    # rate baseline 0.9, baseline_rel 0.5 -> threshold 0.45; observed 0.10 -> soft
    v = evaluate([FieldFill("index", "rate", 0.10, 100)], min_sample=30,
                 baseline_fn=lambda pt, f: 0.9 if f == "rate" else None)
    assert v.critical is False
    assert len(v.findings) == 1
    assert v.findings[0].severity == "soft"


def test_sample_guard_skips_small_runs():
    v = evaluate([FieldFill("index", "href", 0.0, 5)], min_sample=30, baseline_fn=_no_baseline)
    assert v.critical is False
    assert v.findings == []
    assert v.evaluated == 0


def test_soft_skipped_when_no_baseline_yet():
    v = evaluate([FieldFill("index", "rate", 0.0, 100)], min_sample=30, baseline_fn=_no_baseline)
    assert v.findings == []  # cannot judge soft drift without a baseline
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_sentinel_detectors.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the detector**

```python
# javdb/ops/sentinel/detectors.py
"""Pure drift evaluation for the site-contract sentinel (ADR-035)."""

from __future__ import annotations

from typing import Callable, Optional

from javdb.ops.sentinel.models import DriftFinding, FieldFill, SentinelVerdict
from javdb.spider.parse_contract import fields_for

BaselineFn = Callable[[str, str], Optional[float]]


def evaluate(fills: list[FieldFill], *, min_sample: int, baseline_fn: BaselineFn) -> SentinelVerdict:
    verdict = SentinelVerdict()
    for fill in fills:
        spec = fields_for(fill.page_type).get(fill.field)
        if spec is None:
            continue
        if fill.sample_count < min_sample:
            continue  # sample-size guard
        verdict.evaluated += 1
        if spec["severity"] == "critical":
            threshold = spec["min_fill"]
            if fill.fill_rate < threshold:
                verdict.critical = True
                verdict.findings.append(DriftFinding(
                    fill.page_type, fill.field, "critical", fill.fill_rate, threshold,
                ))
        else:  # soft
            baseline = baseline_fn(fill.page_type, fill.field)
            if baseline is None:
                continue  # cannot judge relative drift without a baseline
            threshold = spec["baseline_rel"] * baseline
            if fill.fill_rate < threshold:
                verdict.findings.append(DriftFinding(
                    fill.page_type, fill.field, "soft", fill.fill_rate, threshold, baseline,
                ))
    return verdict
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_sentinel_detectors.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/ops/sentinel/detectors.py tests/unit/test_sentinel_detectors.py
git commit -m "feat(sentinel): add pure drift evaluation"
```

---

## Task 7: Persistence + service (sole writer; incident emission)

**Files:**
- Create: `javdb/ops/sentinel/persistence.py`
- Create: `javdb/ops/sentinel/service.py`
- Test: `tests/unit/test_sentinel_service.py`

`persistence.py` wires `get_db(REPORTS_DB_PATH)` and builds a `site_drift` `OpsIncidentRecord`
(reusing the ADR-026 `OpsIncidentRepo`). `service.py` is the only writer.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_sentinel_service.py
import sqlite3

import pytest

from javdb.ops.sentinel import service
from javdb.ops.sentinel.models import FieldFill, SentinelOptions
from javdb.storage.repos.parse_run_field_fill_repo import ParseRunFieldFillRepo

_DDL = """
CREATE TABLE ParseRunFieldFill (
  session_id TEXT NOT NULL, page_type TEXT NOT NULL, field TEXT NOT NULL,
  fill_rate REAL NOT NULL, sample_count INTEGER NOT NULL,
  committed INTEGER NOT NULL DEFAULT 0, observed_at TEXT,
  PRIMARY KEY (session_id, page_type, field)
);
"""


class _FakeIncidentRepo:
    def __init__(self):
        self.records = []

    def upsert(self, record):
        self.records.append(record)


@pytest.fixture
def fill_repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return ParseRunFieldFillRepo(c)


def test_persist_run_writes_fills(fill_repo):
    n = service.persist_run([FieldFill("index", "href", 0.9, 100)], session_id="S1", repo=fill_repo)
    assert n == 1
    assert fill_repo.get_fills("S1")[0].field == "href"


def test_evaluate_session_critical_emits_incident_and_flags_critical(fill_repo):
    fill_repo.upsert_fills("S1", [FieldFill("index", "href", 0.10, 100)])  # critical
    inc = _FakeIncidentRepo()
    verdict = service.evaluate_session("S1", run_id="R", run_attempt=1,
                                       fill_repo=fill_repo, incident_repo=inc,
                                       options=SentinelOptions(min_sample=30))
    assert verdict.critical is True
    assert len(inc.records) == 1
    assert inc.records[0].incident_type == "site_drift"


def test_evaluate_session_clean_emits_nothing(fill_repo):
    fill_repo.upsert_fills("S1", [FieldFill("index", "href", 1.0, 100)])
    inc = _FakeIncidentRepo()
    verdict = service.evaluate_session("S1", fill_repo=fill_repo, incident_repo=inc)
    assert verdict.critical is False
    assert inc.records == []


def test_mark_committed_flips_flag(fill_repo):
    fill_repo.upsert_fills("S1", [FieldFill("index", "rate", 0.9, 100)])
    service.mark_committed("S1", repo=fill_repo)
    assert fill_repo.baseline("index", "rate", window=14) == 0.9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_sentinel_service.py -v`
Expected: FAIL — `ModuleNotFoundError` / missing functions

- [ ] **Step 3: Write persistence**

```python
# javdb/ops/sentinel/persistence.py
"""D1-canonical persistence wiring for the drift sentinel (ADR-035)."""

from __future__ import annotations

import contextlib
import json

from javdb.ops.diagnosis.models import OpsIncidentRecord, build_incident_id
from javdb.ops.sentinel.models import SentinelVerdict, utc_now_iso
from javdb.storage.db import REPORTS_DB_PATH, get_db
from javdb.storage.repos.ops_incident_repo import OpsIncidentRepo
from javdb.storage.repos.parse_run_field_fill_repo import ParseRunFieldFillRepo


@contextlib.contextmanager
def open_fill_repo():
    with get_db(REPORTS_DB_PATH) as conn:
        yield ParseRunFieldFillRepo(conn)


@contextlib.contextmanager
def open_incident_repo():
    with get_db(REPORTS_DB_PATH) as conn:
        yield OpsIncidentRepo(conn)


def build_drift_incident(
    verdict: SentinelVerdict, *, session_id: str | None,
    run_id: str | None, run_attempt: int | None,
) -> OpsIncidentRecord:
    now = utc_now_iso()
    findings = [
        {"page_type": f.page_type, "field": f.field, "severity": f.severity,
         "fill_rate": f.fill_rate, "threshold": f.threshold, "baseline": f.baseline}
        for f in verdict.findings
    ]
    confidence = "high" if verdict.critical else "medium"
    actions = (["Inspect the parser/selectors; the commit was gated."]
               if verdict.critical else ["Inspect the soft-field selector; run committed."])
    return OpsIncidentRecord(
        incident_id=build_incident_id(
            trigger_source="sentinel", run_id=run_id, run_attempt=run_attempt,
            session_id=session_id, incident_type="site_drift",
        ),
        trigger_source="sentinel",
        run_id=run_id,
        run_attempt=run_attempt,
        session_id=session_id,
        incident_type="site_drift",
        status="open",
        persistence_status="d1_written",
        model_version="n/a",
        detector_version="sentinel-v1",
        bundle_schema_version="n/a",
        confidence=confidence,
        confirmed_findings_json=json.dumps(findings, ensure_ascii=False),
        likely_causes_json="[]",
        unknowns_json="[]",
        recommended_next_actions_json=json.dumps(actions, ensure_ascii=False),
        unsafe_actions_json="[]",
        evidence_refs_json="[]",
        created_at=now,
        updated_at=now,
        resolved_at=None,
    )
```

- [ ] **Step 4: Write the service**

```python
# javdb/ops/sentinel/service.py
"""Sentinel service — sole writer of fills + drift incidents (ADR-035)."""

from __future__ import annotations

import contextlib
import logging

from javdb.infra.config import cfg
from javdb.ops.sentinel.detectors import evaluate
from javdb.ops.sentinel.models import FieldFill, SentinelOptions, SentinelVerdict, utc_now_iso
from javdb.ops.sentinel.persistence import (
    build_drift_incident, open_fill_repo, open_incident_repo,
)

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def _fill_ctx(repo):
    if repo is not None:
        yield repo
    else:
        with open_fill_repo() as opened:
            yield opened


@contextlib.contextmanager
def _incident_ctx(repo):
    if repo is not None:
        yield repo
    else:
        with open_incident_repo() as opened:
            yield opened


def _active_session_id() -> str | None:
    try:
        from javdb.storage.db import get_active_session_id
        return get_active_session_id()
    except Exception:
        return None


def persist_run(fills: list[FieldFill], *, session_id: str | None = None, repo=None) -> int:
    sid = session_id or _active_session_id()
    if not sid or not fills:
        return 0
    with _fill_ctx(repo) as r:
        r.upsert_fills(sid, fills)
    return len(fills)


def evaluate_session(
    session_id: str, *, run_id: str | None = None, run_attempt: int | None = None,
    options: SentinelOptions | None = None, fill_repo=None, incident_repo=None,
) -> SentinelVerdict:
    opts = options or SentinelOptions(
        min_sample=int(cfg("SENTINEL_MIN_SAMPLE", 30)),
        baseline_window=int(cfg("SENTINEL_BASELINE_WINDOW", 14)),
    )
    with _fill_ctx(fill_repo) as r:
        fills = r.get_fills(session_id)
        verdict = evaluate(
            fills, min_sample=opts.min_sample,
            baseline_fn=lambda pt, f: r.baseline(pt, f, window=opts.baseline_window),
        )
    if verdict.findings:
        record = build_drift_incident(
            verdict, session_id=session_id, run_id=run_id, run_attempt=run_attempt)
        try:
            with _incident_ctx(incident_repo) as ir:
                ir.upsert(record)
        except Exception:
            logger.warning("evaluate_session: incident persist failed", exc_info=True)
    return verdict


def mark_committed(session_id: str, *, repo=None) -> None:
    with _fill_ctx(repo) as r:
        r.mark_committed(session_id)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/test_sentinel_service.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Re-export + commit**

Append to `javdb/ops/sentinel/__init__.py`:

```python
from javdb.ops.sentinel.service import persist_run, evaluate_session, mark_committed  # noqa: E402,F401
```

```bash
git add javdb/ops/sentinel/persistence.py javdb/ops/sentinel/service.py javdb/ops/sentinel/__init__.py tests/unit/test_sentinel_service.py
git commit -m "feat(sentinel): add persistence + service with site_drift incident emission"
```

---

## Task 8: Piggyback hook in the index parse flow

**Files:**
- Modify: `javdb/spider/fetch/index.py`
- Test: `tests/unit/test_index_sentinel_observe.py`

`index.py:175` has `page_result = parse_index_page(index_html, page_num)` inside the
page loop; `page_result.movies` is the `list[MovieIndexEntry]`. Observe each page, and
persist once when the index flow finishes.

- [ ] **Step 1: Write the failing test** (pins the observe→persist contract the hook relies on)

```python
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
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/unit/test_index_sentinel_observe.py -v`
Expected: PASS (pins `field_health` + `service.persist_run` from Tasks 4/7)

- [ ] **Step 3: Add the import** at the top of `javdb/spider/fetch/index.py`

```python
from javdb.ops.sentinel import field_health as _sentinel_field_health
```

- [ ] **Step 4: Start the accumulator at the entry of the index function**

Locate the function that contains the page loop (it begins the multi-page scan; the loop body with `page_result = parse_index_page(...)` is at ~line 175). Immediately before that loop starts, insert:

```python
        _sentinel_field_health.start_run()  # ADR-035: begin per-run field-health
```

Run this grep to find the loop's enclosing function and the line just before the loop:
`grep -n "for page_num" javdb/spider/fetch/index.py`

- [ ] **Step 5: Observe each parsed page** — right after the existing parse call (line ~175):

Find:

```python
        page_result = parse_index_page(index_html, page_num)
```

Insert immediately after:

```python
        _acc = _sentinel_field_health.current()
        if _acc is not None:
            _acc.observe("index", page_result.movies)  # ADR-035 piggyback
```

- [ ] **Step 6: Persist at the end of the index flow** — at the function's return point (after the page loop completes, before `return`), insert:

```python
        _sentinel_field_health.persist_run()  # ADR-035: persist run field-health (best-effort)
```

Use `grep -n "return" javdb/spider/fetch/index.py` to find the function's return inside the index-scan function and place the call immediately before it.

- [ ] **Step 7: Import-and-smoke check**

Run: `python3 -c "import javdb.spider.fetch.index; print('import ok')" && pytest tests/unit/test_index_sentinel_observe.py -v`
Expected: `import ok` + PASS

- [ ] **Step 8: Commit**

```bash
git add javdb/spider/fetch/index.py tests/unit/test_index_sentinel_observe.py
git commit -m "feat(spider): piggyback field-health observation at index parse boundary (ADR-035)"
```

---

## Task 9: Commit gate in `commit_session`

**Files:**
- Modify: `apps/cli/db/commit_session.py` (the `db_commit_session_history(sid)` call site, ~line 389)
- Test: `tests/unit/test_commit_gate_site_drift.py`

Before the session's pending rows are drained, evaluate the run's persisted fills. On
**critical** drift, skip the commit and route to the existing failure path with
`FailureReason='site_drift'`; otherwise commit and `mark_committed`.

- [ ] **Step 1: Write the failing test** (pins the gate decision helper)

```python
# tests/unit/test_commit_gate_site_drift.py
import sqlite3

from javdb.ops.sentinel import service
from javdb.ops.sentinel.models import FieldFill
from javdb.storage.repos.parse_run_field_fill_repo import ParseRunFieldFillRepo

_DDL = """
CREATE TABLE ParseRunFieldFill (
  session_id TEXT NOT NULL, page_type TEXT NOT NULL, field TEXT NOT NULL,
  fill_rate REAL NOT NULL, sample_count INTEGER NOT NULL,
  committed INTEGER NOT NULL DEFAULT 0, observed_at TEXT,
  PRIMARY KEY (session_id, page_type, field)
);
"""


class _Inc:
    def __init__(self): self.records = []
    def upsert(self, r): self.records.append(r)


def _repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return ParseRunFieldFillRepo(c)


def test_gate_blocks_on_critical_drift():
    repo = _repo()
    repo.upsert_fills("S1", [FieldFill("index", "href", 0.05, 100)])  # critical
    v = service.evaluate_session("S1", fill_repo=repo, incident_repo=_Inc())
    assert v.critical is True  # caller must NOT commit


def test_gate_allows_clean_run():
    repo = _repo()
    repo.upsert_fills("S1", [FieldFill("index", "href", 1.0, 100)])
    v = service.evaluate_session("S1", fill_repo=repo, incident_repo=_Inc())
    assert v.critical is False  # caller commits, then mark_committed
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/unit/test_commit_gate_site_drift.py -v`
Expected: PASS (pins `evaluate_session` from Task 7)

- [ ] **Step 3: Add the import** near the top of `apps/cli/db/commit_session.py`

```python
from javdb.ops.sentinel.service import evaluate_session as _sentinel_evaluate, mark_committed as _sentinel_mark_committed
```

- [ ] **Step 4: Insert the gate before the drain** — find the commit call (~line 389):

```python
                drain = db_commit_session_history(sid)
```

Insert immediately **before** it:

```python
                # ADR-035 site-contract gate: refuse to commit on critical parser drift.
                _verdict = _sentinel_evaluate(sid)
                if _verdict.critical:
                    logger.error(
                        "Site-contract drift gate: critical drift for session %s "
                        "(%d finding(s)); refusing commit.", sid, len(_verdict.findings),
                    )
                    raise SystemExit(4)  # FailureReason='site_drift' — do not drain pending rows
```

> Adapt the failure mechanism to this file's existing pattern: if `commit_session.py`
> already routes failures through a `FailureReason`/rollback helper rather than
> `SystemExit`, call that path with reason `'site_drift'` instead of raising. The
> invariant is: **critical drift ⇒ `db_commit_session_history` is NOT called**.

- [ ] **Step 5: Mark committed on success** — immediately **after** a successful drain:

Find (just after the `drain = db_commit_session_history(sid)` success handling):

```python
                drain = db_commit_session_history(sid)
```

Add after the existing success log for that drain:

```python
                _sentinel_mark_committed(sid)  # ADR-035: this run's fills now baseline-eligible
```

- [ ] **Step 6: Import-and-smoke check**

Run: `python3 -c "import apps.cli.db.commit_session; print('import ok')" && pytest tests/unit/test_commit_gate_site_drift.py -v`
Expected: `import ok` + PASS

- [ ] **Step 7: Commit**

```bash
git add apps/cli/db/commit_session.py tests/unit/test_commit_gate_site_drift.py
git commit -m "feat(db): gate session commit on critical site-contract drift (ADR-035 D3)"
```

---

## Task 10: CLI adapter + config

**Files:**
- Create: `apps/cli/ops/sentinel.py`
- Modify: `config.py.example`
- Test: `tests/smoke/test_sentinel_cli.py`

- [ ] **Step 1: Write the failing smoke test**

```python
# tests/smoke/test_sentinel_cli.py
import subprocess
import sys


def test_sentinel_cli_help():
    r = subprocess.run(
        [sys.executable, "-m", "apps.cli.ops.sentinel", "--help"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "drift" in r.stdout.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/smoke/test_sentinel_cli.py -v`
Expected: FAIL — `No module named apps.cli.ops.sentinel`

- [ ] **Step 3: Write the CLI**

```python
# apps/cli/ops/sentinel.py
"""Evaluate a session's persisted field-health for site-contract drift (ADR-035).

Read-only by default; exit code 4 signals critical drift so a workflow can act."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from javdb.infra.logging import setup_logging
from javdb.ops.sentinel.service import evaluate_session

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="apps.cli.ops.sentinel",
        description="Evaluate a run's parse field-health for site-contract drift.",
    )
    p.add_argument("--session-id", required=True)
    p.add_argument("--run-id", default=None)
    p.add_argument("--attempt", type=int, default=None, dest="run_attempt")
    p.add_argument("--json", action="store_true", dest="json_output")
    p.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    setup_logging(level=args.log_level)
    verdict = evaluate_session(args.session_id, run_id=args.run_id, run_attempt=args.run_attempt)
    if args.json_output:
        print(json.dumps({
            "critical": verdict.critical,
            "evaluated": verdict.evaluated,
            "findings": [f.__dict__ for f in verdict.findings],
        }, ensure_ascii=False))
    else:
        logger.info("Sentinel: critical=%s evaluated=%d findings=%d",
                    verdict.critical, verdict.evaluated, len(verdict.findings))
    return 4 if verdict.critical else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Document config knobs** — add to `config.py.example` near other ops settings

```python
# ADR-035 site-contract drift sentinel.
SENTINEL_MIN_SAMPLE = 30      # skip drift evaluation when a run parsed fewer items
SENTINEL_BASELINE_WINDOW = 14 # soft-field baseline = median over this many committed runs
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/smoke/test_sentinel_cli.py -v`
Expected: PASS (1 passed)

- [ ] **Step 6: Commit**

```bash
git add apps/cli/ops/sentinel.py config.py.example tests/smoke/test_sentinel_cli.py
git commit -m "feat(sentinel): add apps.cli.ops.sentinel CLI + config knobs"
```

---

## Task 11: Fixture success-criteria tests + docs + full gate

**Files:**
- Create: `tests/unit/test_sentinel_drift_fixtures.py`
- Modify: `CONTEXT.md`, `docs/handbook/en/developer/cli-reference.md` (+ zh)

The ADR's success criteria: a stripped critical field → critical drift; a stripped soft
field → soft finding. Drive these through the real contract + detector with synthesised
`FieldFill`s (the fixtures under `tests/fixtures/parser/` exercise the parser itself in
existing tests; here we pin the **drift** behaviour end-to-end through the public seam).

- [ ] **Step 1: Write the success-criteria tests**

```python
# tests/unit/test_sentinel_drift_fixtures.py
from javdb.ops.sentinel.detectors import evaluate
from javdb.ops.sentinel.models import FieldFill


def _baseline(_pt, f):
    return {"rate": 0.95, "comment_count": 0.9, "release_date": 0.92}.get(f)


def test_href_selector_break_is_critical_gate():
    # href silently stopped parsing across the run
    fills = [
        FieldFill("index", "href", 0.0, 120),
        FieldFill("index", "video_code", 1.0, 120),
        FieldFill("index", "title", 1.0, 120),
    ]
    v = evaluate(fills, min_sample=30, baseline_fn=_baseline)
    assert v.critical is True
    assert any(f.field == "href" and f.severity == "critical" for f in v.findings)


def test_rate_selector_break_is_soft_warn_only():
    # div.score broke: rate collapses, criticals intact
    fills = [
        FieldFill("index", "href", 1.0, 120),
        FieldFill("index", "video_code", 1.0, 120),
        FieldFill("index", "title", 1.0, 120),
        FieldFill("index", "rate", 0.0, 120),
    ]
    v = evaluate(fills, min_sample=30, baseline_fn=_baseline)
    assert v.critical is False
    assert any(f.field == "rate" and f.severity == "soft" for f in v.findings)
```

- [ ] **Step 2: Run to verify PASS**

Run: `pytest tests/unit/test_sentinel_drift_fixtures.py -v`
Expected: PASS (2 passed)

- [ ] **Step 3: Update CONTEXT.md** — add the ADR-035 domain terms verbatim from the ADR's "Domain Language" section: *Parse contract*, *Field fill-rate*, *Site drift*, *Sentinel*, *Canary probe* (mark *Canary probe* as Phase-2).

- [ ] **Step 4: Update CLI reference** — add `python -m apps.cli.ops.sentinel` (flags `--session-id`, `--run-id`, `--attempt`, `--json`, `--log-level`; exit code 4 = critical drift) to `docs/handbook/en/developer/cli-reference.md`; mirror into `docs/handbook/zh/developer/cli-reference.md`.

- [ ] **Step 5: Full verification gate**

Run:
```bash
pytest tests/unit/test_parse_contract.py tests/unit/test_sentinel_models.py \
       tests/unit/test_field_health_accumulator.py tests/unit/test_parse_run_field_fill_repo.py \
       tests/unit/test_sentinel_detectors.py tests/unit/test_sentinel_service.py \
       tests/unit/test_index_sentinel_observe.py tests/unit/test_commit_gate_site_drift.py \
       tests/unit/test_sentinel_drift_fixtures.py tests/smoke/test_sentinel_cli.py -v
```
Expected: all PASS.

- [ ] **Step 6: Seam invariant + regression check**

Run:
```bash
grep -rnE "INSERT|UPDATE|execute\(" javdb/ops/sentinel/detectors.py javdb/ops/sentinel/field_health.py
pytest tests/unit -k "index or commit_session or sentinel" -q
```
Expected: first grep prints nothing (detector + accumulator are read-only/pure); tests PASS.

- [ ] **Step 7: Commit**

```bash
git add tests/unit/test_sentinel_drift_fixtures.py CONTEXT.md docs/handbook
git commit -m "test(sentinel): fixture success-criteria + docs for ADR-035 Phase 1"
```

---

## Plan Self-Review

**Spec coverage (ADR-035 Phase 1 row + D-decisions):**
- Declarative `PARSE_CONTRACT` with severity tiers + layered detection (D2) → Tasks 2, 6. ✓
- Per-run piggyback fill-rate telemetry (D1, piggyback half) → Tasks 4, 8. ✓
- Tiered action: critical gates the commit, soft warns (D3) → Tasks 6, 7, 9. ✓
- Boundary with `html_validators` (D4) → the sentinel evaluates fill-rate of already-parsed records; login/maintenance/empty never reach the accumulator (they fail earlier). Noted; no code claims their territory. ✓
- Baseline-erosion mitigation (D5) → committed-only median baseline on `ParseRunFieldFill` (Task 5 `baseline()`, Task 9 `mark_committed`). Slow baseline via median-over-window; clean-only via `committed=1`. ✓
- Reuse ADR-026 `OpsIncidents`, `incident_type='site_drift'` (D6) → Task 7. ✓
- Module shape `javdb/ops/sentinel/` + CLI + parse-boundary hook + commit gate (D7) → Tasks 3-10. ✓
- One baseline table simplification vs D8 → documented in header. ✓
- Deferred: independent canary, detail-boundary, EMA table → not built; documented. ✓
- Configurable threshold/window → `SENTINEL_MIN_SAMPLE`, `SENTINEL_BASELINE_WINDOW` (Task 10). ✓
- Success criteria (strip critical → gate; strip soft → warn) → Task 11. ✓
- Docs (CONTEXT.md, cli-reference) → Task 11. ✓

**Type consistency:** `FieldFill`, `DriftFinding`, `SentinelVerdict`, `SentinelOptions`,
`FieldHealthAccumulator` (`observe`/`fill_rates`), `ParseRunFieldFillRepo`
(`upsert_fills`/`get_fills`/`baseline`/`mark_committed`), and service
`persist_run`/`evaluate_session`/`mark_committed` are used identically across Tasks 3-11. ✓

**Integration points needing in-file location (grep provided, not blind line numbers):**
Task 8 (index.py: loop start / parse call / function return) and Task 9 (commit_session.py:
the `db_commit_session_history` call + this file's failure mechanism). Both give the exact
target + a grep; the executor confirms the precise lines because surrounding code may have
shifted.

**Known coupling (resolved):** the commit gate now lives in both
`apps/cli/db/commit_session.py` and `javdb/storage/sessions/commit.py`. The CLI
path and the API/library path both call `evaluate_session` before commit work
that would promote parse data, so critical drift is blocked consistently.
`drop_pending` is exempt because it discards staged rows rather than promoting
them, and the API router surfaces critical drift as HTTP 409 `site_drift`.

**Open verification dependency:** none for Phase 1. The production D1 schema was
checked directly and `ParseRunFieldFill` is present in `javdb-reports`.

---

## As-Built Notes (implemented 2026-05-31)

All 11 tasks were implemented and reviewed. The following diverged from the plan
text and were corrected during execution (recorded here per the design-feedback
loop so the plan reflects what actually shipped):

1. **Task 1 — `_REPORTS_DDL` parity is mandatory, not optional.** A new reports-DB
   migration table MUST also be added to `_REPORTS_DDL` in
   `javdb/storage/db/_db_migrations.py`, or `tests/unit/test_rollback_full_fidelity.py`
   (schema-parity contract: every D1-migration column must exist in the local DDL)
   fails. The remote `wrangler d1 execute --remote` apply was verified on the
   production `javdb-reports` database, and local materialisation was verified via
   `init_db`.

2. **Task 7 — `get_db(REPORTS_DB_PATH)`, not `get_db("reports")`.** The plan mirrored
   ADR-026's `persistence.py`, which calls `get_db("reports")`. That is **broken**:
   `get_db` takes a filesystem path (keyed in `_DB_PATH_TO_LOGICAL_NAME` by path), so
   `get_db("reports")` raises `DatabaseError` under sqlite and `ValueError` under D1.
   ADR-026 only survives it via a broad `try/except` → JSONL fallback; the ADR-035
   commit gate reads fills with no such net, so it would have crashed the commit.
   The sentinel uses `get_db(REPORTS_DB_PATH)` (works under sqlite + D1). *(ADR-026's
   `persist_incident` retains the latent bug — flagged as a separate follow-up.)*

3. **Task 9 — no `SystemExit`; fail-open.** `commit_session.py` is a per-session loop
   collecting `failed_commits`; critical drift routes through that existing failure
   path (`failed_commits.append` + `SessionFailed` event + `_emit_pending_verify('finalizing')`
   + `continue`) so other sessions still commit — `raise SystemExit(4)` (plan text)
   would abort the whole batch. The gate is **fail-open**: a sentinel evaluation error
   logs a warning and lets the commit proceed (a sentinel bug must never halt the
   pipeline); only a successful `verdict.critical is True` blocks.

4. **Task 10 — `setup_logging(log_level=...)`**, not `setup_logging(level=...)` (the
   real signature is `setup_logging(log_file=None, log_level=None, *, log_style=None)`).

5. **Task 8 — the index loop is `while True:` in `_fetch_all_index_pages_sequential`,
   not `for page_num`** (the plan's grep would not match). `page_result.movies` is
   correct (`IndexPageResult.movies: list[MovieIndexEntry]`).

6. **Task 5 — baseline median rounding.** `statistics.median([0.80, 0.90])` returns
   `0.8500000000000001` (IEEE-754), failing the plan's verbatim `== 0.85` assertion.
   `baseline()` rounds to 6 dp (`round(median, 6)`); harmless for a [0,1] ratio.

7. **Task 12 (added) — parallel index path wired.** The plan scoped only the
   sequential path, but the daily production run uses the **parallel** path
   (`use_parallel = use_proxy AND PROXY_MODE=='pool' AND len(PROXY_POOL)>1`), which
   bypassed the hook — leaving the sentinel dormant on daily runs. The same
   `start_run`/`observe`/`persist_run` piggyback was added to
   `javdb/spider/fetch/index_parallel.py::fetch_all_index_pages_parallel`, placed in
   the **single-threaded** parse loop that runs *after* `backend.shutdown()` (so the
   non-thread-safe `FieldHealthAccumulator`/`_CURRENT` global is never touched from
   worker threads).

**Phase 1 close-out updates:**

- **Production D1 migration is applied.** A read-only remote D1 schema check
  confirmed `ParseRunFieldFill`, `idx_prff_field_committed`, and
  `idx_prff_session` exist in `javdb-reports`.
- **API commit path is gated.** `javdb/storage/sessions/commit.py::commit_session`
  (via `POST /api/sessions/{id}/commit`) now calls `evaluate_session` before
  pending writes are dropped/promoted or the session status is flipped, fails
  open on sentinel errors, and marks fills committed after a successful commit.

**Deferred beyond Phase 1:**

- **Detail-page boundary** (contract-ready, not wired) and a **dedicated EMA baseline
  table** remain as documented Phase-2 candidates.
