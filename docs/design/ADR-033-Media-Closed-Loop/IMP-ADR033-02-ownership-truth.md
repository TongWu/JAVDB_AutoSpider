# IMP-ADR033-02: Ownership Truth (Media Closed-Loop Phase 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-033](ADR-033-media-closed-loop.md) (umbrella) — this is **Phase 2** of three. Sibling plan: [IMP-ADR033-03](IMP-ADR033-03-consumption-signal.md) (Phase 3, Consumption Signal). Phase 1 is [IMP-ADR033-01](IMP-ADR033-01-acquisition-outcome.md) (implemented and live).

**Status:** Implemented and locally verified (2026-06-06). All 11 tasks landed via subagent-driven development with two-stage (spec + quality) review per task: `OwnershipLedger` D1 migration + local mirror, ownership models/repo/persistence, the four read-only collectors (gdrive/qb/pikpak + nas stub), `AcquisitionOutcomeRepo` landing methods, the `run_ownership` sole-writer pass (per-source diff-sweep + `in_library` derivation), dedup re-pointed to the Ledger (public API byte-identical, transitional `RcloneInventory` fallback), the `--pass` CLI selector, `ReconcileLibrary.yml --pass all`, config knobs, and bilingual docs. Verification gate passed (Phase-2 unit + smoke suite green; collector read-only grep clean; dedup public API + Phase-1 `run()` byte-unchanged). Remote D1 apply (`wrangler d1 execute`) and the local SQLite mirror refresh (`sync_d1_to_sqlite --force-overwrite-all`) remain deployment-environment gates (this worktree lacks Cloudflare credentials).

**Goal:** Replace the GDrive-only `RcloneInventory` view with a multi-source `OwnershipLedger` (`video_code, source, category`) covering `gdrive` / `qb` / `pikpak` / `nas`, written by a new `run_ownership(...)` pass of the reconcile service; re-point the dedup checker to read the Ledger's `gdrive` rows (public API byte-identical, transitional fallback to `RcloneInventory`); and derive `AcquisitionOutcome.state='in_library'` once a `video_code` lands in a persistent (`gdrive`/`nas`) Ledger source — closing the `queued → … → completed → in_library` funnel that Phase 1 deliberately stopped short of.

**Architecture:** The Phase-1 module `javdb/ops/reconcile/` grows a **sibling entrypoint** `run_ownership(options, *, repo=None, ...)` next to the existing `run()` (which stays untouched). It shares the read-only collector seam (`collectors.py`), the typed-model module (`models.py`), and the call-time `get_db(OPERATIONS_DB_PATH)` persistence wiring (`persistence.py`). `run_ownership` is the **sole writer** of `OwnershipLedger`; four read-only collectors (`GdriveOwnershipCollector` projecting `RcloneInventory`, `QbOwnershipCollector` via the `AcquisitionOutcome` bridge, `PikpakOwnershipCollector` best-effort from `PikpakHistory`, `NasOwnershipCollector` an explicit stub) yield full per-source snapshots; the service UPSERTs observed rows and diff-sweeps prior rows of that source absent from the snapshot to `present=0` (audit-preserving, never deleted). The pass ends with an `in_library` derivation step against `AcquisitionOutcome`. The CLI gains the `--pass {acquisition,ownership,consumption,all}` selector (introduced here for `acquisition`/`ownership`/`all`; Phase 3 adds the `consumption` arm); `ReconcileLibrary.yml` switches to `--pass all`.

**Tech Stack:** Python 3, `sqlite3`/D1 via `javdb.storage.db.get_db`, `dataclasses`, `pytest`, Cloudflare D1 + `wrangler`, GitHub Actions.

**Storage placement:** `OwnershipLedger` lives in the **operations** logical DB (`javdb-operations`), alongside `AcquisitionOutcome` (Phase 1), `RcloneInventory`, `DedupRecords`, and `PikpakHistory` — operational ledger data, not history/dedup, not reports/sessions.

---

## File Structure

| Path | Create/Modify | Responsibility |
| --- | --- | --- |
| `javdb/migrations/d1/2026_06_06_add_ownership_ledger.sql` | Create | `OwnershipLedger` DDL + indexes (D1-first) |
| `javdb/storage/db/_db_migrations.py` (operations DDL block, after `AcquisitionOutcome` at line 617) | Modify | Mirror `OwnershipLedger` DDL into the local SQLite bootstrap |
| `javdb/ops/reconcile/models.py` | Modify | Append `OwnershipLedgerRecord`, `OWNERSHIP_SOURCES`, `PERSISTENT_OWNERSHIP_SOURCES`, `OwnershipOptions`, `OwnershipResult` |
| `javdb/storage/repos/ownership_ledger_repo.py` | Create | `OwnershipLedgerRepo` (upsert / list_by_source / mark_absent / list_present_video_codes) |
| `javdb/ops/reconcile/persistence.py` | Modify | Append `open_ledger_repo()` (call-time `get_db(OPERATIONS_DB_PATH)`) |
| `javdb/ops/reconcile/collectors.py` | Modify | Append `GdriveOwnershipCollector`, `QbOwnershipCollector`, `PikpakOwnershipCollector`, `NasOwnershipCollector` (+ `OwnershipObservation`) |
| `javdb/ops/reconcile/service.py` | Modify | Append `run_ownership()` (sole writer; per-source diff-sweep) + `_derive_in_library()` |
| `javdb/storage/repos/acquisition_outcome_repo.py` | Modify | Append `list_pending_landing(states)` + `mark_in_library(qb_hash, landed_at)` |
| `javdb/ops/reconcile/__init__.py` | Modify | Re-export the new public Options/Result/`run_ownership` |
| `javdb/spider/services/dedup.py:142-177` | Modify | `load_rclone_inventory` reads Ledger `gdrive` rows + transitional `RcloneInventory` fallback; add `should_skip_from_ownership` |
| `apps/cli/ops/reconcile.py` | Modify | Introduce `--pass {acquisition,ownership,all}` selector; wire `run_ownership` |
| `.github/workflows/ReconcileLibrary.yml` | Modify | Run `--pass all`; add gdrive/pikpak collector config notes |
| `config.py.example` | Modify | Document `OWNERSHIP_SOURCES` / `RCLONE_NAS_REMOTE` knobs |
| `CONTEXT.md` | Modify | Promote *Ownership ledger* to live; add *Present sweep* term |
| `docs/handbook/en/developer/cli-reference.md` (+ zh) | Modify | Document `--pass` selector |
| `docs/handbook/en/self-hoster/github-actions-setup.md` (+ zh) | Modify | Document `--pass all` and ownership sources |
| `tests/unit/test_ownership_ledger_models.py` | Create | Model/round-trip tests |
| `tests/unit/test_ownership_ledger_repo.py` | Create | Repo upsert/sweep/present-list tests (in-memory sqlite) |
| `tests/unit/test_ownership_collectors.py` | Create | Collector projection/collapse/stub tests |
| `tests/unit/test_run_ownership_service.py` | Create | `OwnershipOptions→OwnershipResult` service tests (fakes) + diff-sweep |
| `tests/unit/test_in_library_derivation.py` | Create | `in_library` promotion + repo method tests |
| `tests/unit/test_dedup_reads_ledger.py` | Create | dedup reads Ledger gdrive rows; fallback; API unchanged |
| `tests/smoke/test_reconcile_cli.py` | Modify | `--pass` selector smoke (help shows it; `--pass ownership --dry-run` exit) |

**Naming contract (used across tasks — keep verbatim):**
record class `OwnershipLedgerRecord` (fields: `video_code, source, category, path, size, present, observed_at`);
constants `OWNERSHIP_SOURCES = ("qb","nas","gdrive","pikpak")` and `PERSISTENT_OWNERSHIP_SOURCES = frozenset({"gdrive","nas"})`;
repo class `OwnershipLedgerRepo` with methods `upsert(record)`, `list_by_source(source)`, `mark_absent(source, present_keys)` (sweeps prior rows of `source` whose `(video_code, category)` is absent from `present_keys` to `present=0`), `list_present_video_codes(sources)` (distinct `video_code` with `present=1` in any of `sources`), `get(video_code, source, category)`;
collector classes `GdriveOwnershipCollector`, `QbOwnershipCollector`, `PikpakOwnershipCollector`, `NasOwnershipCollector`, each `.source` + `.collect(...) -> list[OwnershipObservation]`;
service functions `run_ownership(options, *, repo=None, outcome_repo=None, rclone_inventory=None, qb_outcomes=None, pikpak_rows=None)`;
new `AcquisitionOutcomeRepo` methods `list_pending_landing(states=("queued","downloading","completed"))` and `mark_in_library(qb_hash, landed_at)`;
options/result `OwnershipOptions` / `OwnershipResult` (NOT a reuse of `ReconcileOptions`/`ReconcileResult` — the source set, sweep semantics, and counters differ; documented in Task 2);
dedup additions: `load_rclone_inventory` re-pointed (public signature `load_rclone_inventory(csv_path) -> Dict[str, List[RcloneEntry]]` unchanged) + new `should_skip_from_ownership(video_code) -> bool`.

> **Heterogeneous category (ADR-033 D-P2-1):** `OwnershipLedger.category` is **source-native and NOT NULL** (`DEFAULT ''`). `gdrive` stores a glyph composite `"<SensorCategory>|<SubtitleCategory>"` (e.g. `无码破解|中字`); `qb` stores the `AcquisitionOutcome` **English** category (`subtitle` / `no_subtitle` / `hacked_subtitle` / `hacked_no_subtitle`); `pikpak` / `nas` store `''` when unknown. There is **no lossy cross-source unification** (D11 YAGNI). The PK `(video_code, source, category)` is safe precisely because `category` is `NOT NULL`.

> **Phase boundary:** This IMP is **backend data layer only**. No web read-API, no TypeScript, no Vue. Web surface for ownership is tracked by [ADR-034](../_archive/ADR-034-Media-Closed-Loop-Web-Surface/ADR-034-media-closed-loop-web-surface.md) Phase 2 and is out of scope here.

---

## Task 1: D1 migration + local mirror — `OwnershipLedger` table

**Files:**
- Create: `javdb/migrations/d1/2026_06_06_add_ownership_ledger.sql`
- Modify: `javdb/storage/db/_db_migrations.py` (operations DDL block, right after the `AcquisitionOutcome` block ending at line 617)

- [ ] **Step 1: Write the migration SQL**

```sql
-- 2026-06-06: Add OwnershipLedger table (ADR-033 Phase 2).
--
-- Apply with:
--   wrangler d1 execute javdb-operations --remote \
--     --file=javdb/migrations/d1/2026_06_06_add_ownership_ledger.sql
--
-- OwnershipLedger is the multi-source "what do I own" superset of
-- RcloneInventory (ADR-033 D6). It is enrichment: written off the
-- Pending->Commit path by run_ownership, idempotent UPSERT by the
-- (video_code, source, category) PK. category is source-native and NOT NULL
-- (ADR-033 D-P2-1): gdrive = '<sensor>|<subtitle>' glyph composite,
-- qb = AcquisitionOutcome English category, pikpak/nas = '' when unknown.
-- present=0 rows are swept (not deleted) when a source no longer reports them.

CREATE TABLE IF NOT EXISTS OwnershipLedger (
  video_code  TEXT NOT NULL,
  source      TEXT NOT NULL CHECK (source IN ('qb','nas','gdrive','pikpak')),
  category    TEXT NOT NULL DEFAULT '',
  path        TEXT,
  size        INTEGER,
  present     INTEGER NOT NULL DEFAULT 1,
  observed_at TEXT,
  PRIMARY KEY (video_code, source, category)
);

CREATE INDEX IF NOT EXISTS idx_ownership_ledger_video_code ON OwnershipLedger(video_code);
CREATE INDEX IF NOT EXISTS idx_ownership_ledger_source ON OwnershipLedger(source);
CREATE INDEX IF NOT EXISTS idx_ownership_ledger_source_present ON OwnershipLedger(source, present);
```

- [ ] **Step 2: Mirror the DDL into the local SQLite bootstrap** — open `javdb/storage/db/_db_migrations.py`, find the `AcquisitionOutcome` block inside `_OPERATIONS_DDL` (lines 600-616, terminated by the `idx_acq_outcome_last_seen` index at line 616). Append the identical `OwnershipLedger` DDL (table + the three indexes, `IF NOT EXISTS` form) immediately after it, before the closing `"""` at line 617. Keep the DDL byte-for-byte aligned with the D1 migration so `sync_d1_to_sqlite --force-overwrite-all` produces no drift.

- [ ] **Step 3: Apply to D1 (operations)** — *deployment-environment gate; requires Cloudflare creds*

Run:
```bash
wrangler d1 execute javdb-operations --remote \
  --file=javdb/migrations/d1/2026_06_06_add_ownership_ledger.sql
```
Expected: `wrangler` reports the statements executed without error.

- [ ] **Step 4: Re-align the local SQLite mirror from D1 (D1-canonical rule)** — *deployment-environment gate*

Run:
```bash
python3 -m apps.cli.db.sync_d1_to_sqlite --apply --force-overwrite-all
```
Expected: log shows `OwnershipLedger` rebuilt from D1's verbatim DDL; exit code 0.

- [ ] **Step 5: Verify the table exists locally** (works against the bootstrap DDL even without D1)

Run:
```bash
python3 -c "from javdb.storage.db import init_db, OPERATIONS_DB_PATH, get_db; init_db(force=True); \
import sqlite3; \
print(get_db.__name__); \
"
python3 -c "import sqlite3,glob; p=glob.glob('reports/operations.db')[0]; print(sqlite3.connect(p).execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name='OwnershipLedger'\").fetchone())"
```
Expected: `('OwnershipLedger',)`

- [ ] **Step 6: Commit**

```bash
git add javdb/migrations/d1/2026_06_06_add_ownership_ledger.sql javdb/storage/db/_db_migrations.py
git commit -m "feat(db): add OwnershipLedger table (ADR-033 Phase 2)"
```

---

## Task 2: Typed models — `OwnershipLedgerRecord`, `OwnershipOptions`, `OwnershipResult`

**Files:**
- Modify: `javdb/ops/reconcile/models.py` (append only — Phase 1 models stay untouched)
- Test: `tests/unit/test_ownership_ledger_models.py`

> **Decision (Options/Result reuse):** `run_ownership` uses **dedicated** `OwnershipOptions` / `OwnershipResult`, *not* `ReconcileOptions`/`ReconcileResult`. Rationale: the ownership pass speaks a different source vocabulary (`gdrive/qb/pikpak/nas` vs `qb` only), carries no `stalled_after_days`/`infer_absent` (no stalling concept), and needs ownership-specific counters (`swept_absent`, `marked_in_library`). Sharing one struct would overload fields and break the Phase-1 `run()` contract its tests pin. Phase 3's `run_consumption` will likewise add its own `ConsumptionOptions`/`ConsumptionResult`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_ownership_ledger_models.py
from javdb.ops.reconcile.models import (
    OWNERSHIP_SOURCES,
    PERSISTENT_OWNERSHIP_SOURCES,
    OwnershipLedgerRecord,
    OwnershipOptions,
    OwnershipResult,
)


def test_ownership_sources_vocabulary():
    assert OWNERSHIP_SOURCES == ("qb", "nas", "gdrive", "pikpak")
    assert PERSISTENT_OWNERSHIP_SOURCES == frozenset({"gdrive", "nas"})


def test_record_defaults():
    rec = OwnershipLedgerRecord(video_code="ABC-1", source="gdrive", category="无码破解|中字")
    assert rec.present == 1
    assert rec.path is None
    assert rec.size is None


def test_category_defaults_to_empty_not_none():
    rec = OwnershipLedgerRecord(video_code="ABC-1", source="pikpak")
    assert rec.category == ""  # NOT NULL contract (D-P2-1)


def test_options_default_to_all_sources():
    opts = OwnershipOptions()
    assert set(opts.sources) == set(OWNERSHIP_SOURCES)
    assert opts.dry_run is False


def test_result_starts_empty():
    res = OwnershipResult()
    assert res.observed == 0
    assert res.upserted == 0
    assert res.swept_absent == 0
    assert res.marked_in_library == 0
    assert res.errors == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_ownership_ledger_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'OwnershipLedgerRecord'`

- [ ] **Step 3: Append the models** to `javdb/ops/reconcile/models.py` (after the Phase-1 `ReconcileResult` dataclass at line 77)

```python
# --- ADR-033 Phase 2: Ownership truth ---------------------------------------

OWNERSHIP_SOURCES: tuple[str, ...] = ("qb", "nas", "gdrive", "pikpak")
# Sources that mean "the file is durably owned" (used by in_library + dedup skip).
PERSISTENT_OWNERSHIP_SOURCES: frozenset[str] = frozenset({"gdrive", "nas"})


@dataclass
class OwnershipLedgerRecord:
    video_code: str
    source: str
    category: str = ""            # source-native, NOT NULL (D-P2-1)
    path: Optional[str] = None
    size: Optional[int] = None
    present: int = 1
    observed_at: Optional[str] = None


@dataclass(frozen=True)
class OwnershipObservation:
    """Normalized, read-only ownership signal from one source (one snapshot row)."""

    source: str
    video_code: str
    category: str = ""
    path: Optional[str] = None
    size: Optional[int] = None


@dataclass
class OwnershipOptions:
    sources: Sequence[str] = OWNERSHIP_SOURCES
    derive_in_library: bool = True
    dry_run: bool = False


@dataclass
class OwnershipResult:
    observed: int = 0
    upserted: int = 0
    swept_absent: int = 0
    marked_in_library: int = 0
    errors: list[str] = field(default_factory=list)
```

> `OwnershipObservation` lives in `models.py` (next to Phase-1 `Observation`); collectors import it from there. It is **not** the same type as Phase-1 `Observation` — ownership carries `category/path/size`, not `state`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_ownership_ledger_models.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/ops/reconcile/models.py tests/unit/test_ownership_ledger_models.py
git commit -m "feat(reconcile): add ownership-ledger models (ADR-033 Phase 2)"
```

---

## Task 3: `OwnershipLedgerRepo`

**Files:**
- Create: `javdb/storage/repos/ownership_ledger_repo.py`
- Test: `tests/unit/test_ownership_ledger_repo.py`

Mirror the `AcquisitionOutcomeRepo` shape (`_COLUMNS` tuple, `_row_to_record`, idempotent `INSERT … ON CONFLICT DO UPDATE`, `sqlite3.Row` factory) at `javdb/storage/repos/acquisition_outcome_repo.py:15-99`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_ownership_ledger_repo.py
import sqlite3

import pytest

from javdb.ops.reconcile.models import OwnershipLedgerRecord
from javdb.storage.repos.ownership_ledger_repo import OwnershipLedgerRepo

_DDL = """
CREATE TABLE OwnershipLedger (
  video_code TEXT NOT NULL, source TEXT NOT NULL, category TEXT NOT NULL DEFAULT '',
  path TEXT, size INTEGER, present INTEGER NOT NULL DEFAULT 1, observed_at TEXT,
  PRIMARY KEY (video_code, source, category)
);
"""


@pytest.fixture
def repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return OwnershipLedgerRepo(c)


def test_upsert_then_get(repo):
    repo.upsert(OwnershipLedgerRecord("ABC-1", "gdrive", "无码破解|中字", path="/g/x", size=10, observed_at="t1"))
    got = repo.get("ABC-1", "gdrive", "无码破解|中字")
    assert got.path == "/g/x"
    assert got.present == 1


def test_upsert_is_idempotent_on_pk(repo):
    repo.upsert(OwnershipLedgerRecord("ABC-1", "gdrive", "无码破解|中字", size=10, observed_at="t1"))
    repo.upsert(OwnershipLedgerRecord("ABC-1", "gdrive", "无码破解|中字", size=20, observed_at="t2"))
    assert repo.get("ABC-1", "gdrive", "无码破解|中字").size == 20
    assert repo._conn.execute("SELECT COUNT(*) FROM OwnershipLedger").fetchone()[0] == 1


def test_distinct_category_is_a_distinct_row(repo):
    repo.upsert(OwnershipLedgerRecord("ABC-1", "gdrive", "无码破解|中字", observed_at="t1"))
    repo.upsert(OwnershipLedgerRecord("ABC-1", "gdrive", "有码|无字", observed_at="t1"))
    rows = repo.list_by_source("gdrive")
    assert {r.category for r in rows} == {"无码破解|中字", "有码|无字"}


def test_mark_absent_sweeps_only_unseen_rows_of_that_source(repo):
    repo.upsert(OwnershipLedgerRecord("A", "gdrive", "c1", observed_at="t1"))
    repo.upsert(OwnershipLedgerRecord("B", "gdrive", "c1", observed_at="t1"))
    repo.upsert(OwnershipLedgerRecord("C", "nas", "", observed_at="t1"))   # different source untouched
    swept = repo.mark_absent("gdrive", present_keys={("A", "c1")})
    assert swept == 1                                   # only B swept
    assert repo.get("A", "gdrive", "c1").present == 1
    assert repo.get("B", "gdrive", "c1").present == 0   # swept, not deleted
    assert repo.get("C", "nas", "").present == 1        # other source intact


def test_list_present_video_codes_filters_by_present_and_source(repo):
    repo.upsert(OwnershipLedgerRecord("OWNED", "gdrive", "c1", present=1, observed_at="t1"))
    repo.upsert(OwnershipLedgerRecord("GONE", "gdrive", "c1", present=0, observed_at="t1"))
    repo.upsert(OwnershipLedgerRecord("QBONLY", "qb", "subtitle", present=1, observed_at="t1"))
    codes = repo.list_present_video_codes(("gdrive", "nas"))
    assert codes == {"OWNED"}     # GONE swept; QBONLY is not a persistent source
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_ownership_ledger_repo.py -v`
Expected: FAIL — `ModuleNotFoundError: javdb.storage.repos.ownership_ledger_repo`

- [ ] **Step 3: Write the repo**

```python
# javdb/storage/repos/ownership_ledger_repo.py
"""Repository for ADR-033 OwnershipLedger rows (operations DB)."""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable, Optional

from javdb.ops.reconcile.models import OwnershipLedgerRecord

_COLUMNS = ("video_code", "source", "category", "path", "size", "present", "observed_at")
_PK = ("video_code", "source", "category")


def _row_to_record(row: Any) -> OwnershipLedgerRecord:
    return OwnershipLedgerRecord(**{column: row[column] for column in _COLUMNS})


class OwnershipLedgerRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def upsert(self, record: OwnershipLedgerRecord) -> None:
        values = [getattr(record, column) for column in _COLUMNS]
        placeholders = ", ".join(["?"] * len(_COLUMNS))
        columns = ", ".join(_COLUMNS)
        updates = ", ".join(f"{c}=excluded.{c}" for c in _COLUMNS if c not in _PK)
        self._conn.execute(
            f"""
            INSERT INTO OwnershipLedger ({columns})
            VALUES ({placeholders})
            ON CONFLICT(video_code, source, category) DO UPDATE SET {updates}
            """,
            values,
        )

    def get(self, video_code: str, source: str, category: str) -> Optional[OwnershipLedgerRecord]:
        row = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM OwnershipLedger "
            "WHERE video_code = ? AND source = ? AND category = ?",
            [video_code, source, category],
        ).fetchone()
        return None if row is None else _row_to_record(row)

    def list_by_source(self, source: str) -> list[OwnershipLedgerRecord]:
        rows = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM OwnershipLedger WHERE source = ?",
            [source],
        ).fetchall()
        return [_row_to_record(r) for r in rows]

    def mark_absent(self, source: str, present_keys: Iterable[tuple[str, str]]) -> int:
        """Sweep prior rows of *source* whose (video_code, category) is absent from
        *present_keys* to present=0 (audit-preserving; never deletes). Returns the
        number of rows swept."""
        present = {(vc, cat) for vc, cat in present_keys}
        swept = 0
        rows = self._conn.execute(
            "SELECT video_code, category FROM OwnershipLedger "
            "WHERE source = ? AND present = 1",
            [source],
        ).fetchall()
        for row in rows:
            key = (row["video_code"], row["category"])
            if key in present:
                continue
            self._conn.execute(
                "UPDATE OwnershipLedger SET present = 0 "
                "WHERE video_code = ? AND source = ? AND category = ?",
                [row["video_code"], source, row["category"]],
            )
            swept += 1
        return swept

    def list_present_video_codes(self, sources: Iterable[str]) -> set[str]:
        """Distinct video_codes with present=1 in any of *sources*."""
        sources = tuple(sources)
        if not sources:
            return set()
        placeholders = ", ".join(["?"] * len(sources))
        rows = self._conn.execute(
            f"SELECT DISTINCT video_code FROM OwnershipLedger "
            f"WHERE present = 1 AND source IN ({placeholders})",
            list(sources),
        ).fetchall()
        return {r["video_code"] for r in rows}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_ownership_ledger_repo.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/storage/repos/ownership_ledger_repo.py tests/unit/test_ownership_ledger_repo.py
git commit -m "feat(db): add OwnershipLedgerRepo with diff-sweep + present-list (ADR-033 Phase 2)"
```

---

## Task 4: Persistence wiring — `open_ledger_repo`

**Files:**
- Modify: `javdb/ops/reconcile/persistence.py` (append; keep Phase-1 `open_outcome_repo` byte-identical)

> No new unit test — exercised end-to-end by Task 6's service tests (which inject a repo) and Task 9's CLI smoke (real `get_db`). Mirror the BFR-016 call-time path resolution (`_db.OPERATIONS_DB_PATH`) so pytest's path monkeypatch is honoured.

- [ ] **Step 1: Append the ledger persistence helper**

Add to `javdb/ops/reconcile/persistence.py` (after `open_outcome_repo` at line 21):

```python
from javdb.storage.repos.ownership_ledger_repo import OwnershipLedgerRepo


@contextlib.contextmanager
def open_ledger_repo():
    """Yield an OwnershipLedgerRepo over the operations DB connection.

    Routing honours STORAGE_BACKEND via get_db (D1 / sqlite / dual). The DB
    path is resolved at call time (``_db.OPERATIONS_DB_PATH``) rather than bound
    at import, so pytest's path monkeypatch is honoured (BFR-016).
    """
    with get_db(_db.OPERATIONS_DB_PATH) as conn:
        yield OwnershipLedgerRepo(conn)
```

- [ ] **Step 2: Verify it imports**

Run: `python3 -c "from javdb.ops.reconcile.persistence import open_ledger_repo, open_outcome_repo; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add javdb/ops/reconcile/persistence.py
git commit -m "feat(reconcile): wire OwnershipLedger persistence to operations DB"
```

---

## Task 5: Ownership collectors (read-only seam)

**Files:**
- Modify: `javdb/ops/reconcile/collectors.py` (append; Phase-1 `QbCollector` stays untouched)
- Test: `tests/unit/test_ownership_collectors.py`

Each collector is **read-only** (ADR-033 D4): it transforms an already-loaded source read into `OwnershipObservation`s and **never writes**. The service is the only writer. Collectors take their source data as a constructor/`collect` argument so unit tests never touch live qB/rclone/pikpak.

- **`GdriveOwnershipCollector`** projects the `RcloneInventory` dict (`load_rclone_inventory()`'s `Dict[code -> List[RcloneEntry]]` shape, per `dedup.py:95-103,142-177`). For each `(video_code, sensor_category, subtitle_category)` it builds the glyph composite category `f"{sensor}|{subtitle}"`. Because `RcloneInventory`'s PK is the surrogate `Id` (DDL at `_db_migrations.py:523-533`), multiple rows can collapse onto one Ledger PK — the collector keeps the row with **MAX `folder_size`** for path/size (D-P2-3).
- **`QbOwnershipCollector`** bridges via `AcquisitionOutcome` (the ONLY qB-hash→video_code bridge, ADR-033 grounding): it takes the outcome rows and emits an ownership obs for every row whose `video_code` is non-empty, `source="qb"`, `category=outcome.category` (English namespace), `path=None`, `size=None`.
- **`PikpakOwnershipCollector`** is best-effort presence from `PikpakHistory` rows with `TransferStatus='success'` (DDL at `_db_migrations.py:554-566`). `pikpakapi` has no file-listing API, so it records OUTCOMES, not live presence; category `''`. Pikpak presence is **monotonic** (history is append-only → never swept; see D-P2-5 / Task 6).
- **`NasOwnershipCollector`** is an **explicit stub** (D-P2-4): returns `[]` and `log()`s `"nas ownership not collected (no RCLONE_NAS_REMOTE configured)"`. The `nas` value stays in the CHECK constraint for forward-compat; this satisfies the no-silent-caps rule.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_ownership_collectors.py
from javdb.ops.reconcile.collectors import (
    GdriveOwnershipCollector,
    NasOwnershipCollector,
    PikpakOwnershipCollector,
    QbOwnershipCollector,
)
from javdb.spider.services.dedup import RcloneEntry


def _gdrive_inventory():
    return {
        "ABC-1": [
            RcloneEntry("ABC-1", "无码破解", "中字", "/g/small", 10, 1, "t"),
            RcloneEntry("ABC-1", "无码破解", "中字", "/g/big", 99, 1, "t"),  # same key, bigger
            RcloneEntry("ABC-1", "有码", "无字", "/g/other", 5, 1, "t"),     # different key
        ]
    }


def test_gdrive_composite_category_and_max_size_collapse():
    obs = GdriveOwnershipCollector().collect(_gdrive_inventory())
    by_cat = {o.category: o for o in obs}
    assert set(by_cat) == {"无码破解|中字", "有码|无字"}
    assert by_cat["无码破解|中字"].path == "/g/big"   # max folder_size wins (D-P2-3)
    assert by_cat["无码破解|中字"].size == 99
    assert all(o.source == "gdrive" and o.video_code == "ABC-1" for o in obs)


def test_qb_owner_bridges_video_code_from_outcomes():
    outcomes = [
        {"video_code": "ABC-1", "category": "subtitle"},
        {"video_code": None, "category": "no_subtitle"},   # no code → skipped
    ]
    obs = QbOwnershipCollector().collect(outcomes)
    assert len(obs) == 1
    assert obs[0].source == "qb"
    assert obs[0].category == "subtitle"      # English namespace, unchanged


def test_pikpak_only_success_rows():
    rows = [
        {"TransferStatus": "success", "TorrentName": "ABC-1", "video_code": "ABC-1"},
        {"TransferStatus": "failed", "TorrentName": "DEF-2", "video_code": "DEF-2"},
    ]
    obs = PikpakOwnershipCollector().collect(rows)
    assert [o.video_code for o in obs] == ["ABC-1"]
    assert obs[0].source == "pikpak"
    assert obs[0].category == ""


def test_nas_is_an_explicit_empty_stub(caplog):
    import logging
    with caplog.at_level(logging.INFO):
        obs = NasOwnershipCollector().collect()
    assert obs == []
    assert any("nas ownership not collected" in r.message for r in caplog.records)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_ownership_collectors.py -v`
Expected: FAIL — `ImportError: cannot import name 'GdriveOwnershipCollector'`

- [ ] **Step 3: Append the collectors** to `javdb/ops/reconcile/collectors.py`

```python
import logging

from javdb.ops.reconcile.models import OwnershipObservation

logger = logging.getLogger(__name__)


class GdriveOwnershipCollector:
    """Project RcloneInventory into gdrive OwnershipObservations.

    Input is the load_rclone_inventory() dict (Dict[code -> List[RcloneEntry]]).
    Collapse rule (D-P2-3): multiple rows mapping to the same
    (video_code, '<sensor>|<subtitle>') keep the row with the MAX folder_size.
    """

    source = "gdrive"

    def collect(self, inventory) -> list[OwnershipObservation]:
        best: dict[tuple[str, str], OwnershipObservation] = {}
        for code, entries in (inventory or {}).items():
            for e in entries:
                category = f"{e.sensor_category}|{e.subtitle_category}"
                key = (e.video_code or code, category)
                size = int(e.folder_size or 0)
                current = best.get(key)
                if current is None or size > (current.size or 0):
                    best[key] = OwnershipObservation(
                        source=self.source,
                        video_code=e.video_code or code,
                        category=category,
                        path=e.folder_path or None,
                        size=size,
                    )
        return list(best.values())


class QbOwnershipCollector:
    """Bridge AcquisitionOutcome rows into qb OwnershipObservations.

    The only qB-hash -> video_code bridge is AcquisitionOutcome itself, so this
    consumes outcome rows (dicts or records) rather than re-reading qB."""

    source = "qb"

    def collect(self, outcomes) -> list[OwnershipObservation]:
        out: list[OwnershipObservation] = []
        for o in outcomes or []:
            video_code = o.get("video_code") if isinstance(o, dict) else getattr(o, "video_code", None)
            if not video_code:
                continue
            category = (o.get("category") if isinstance(o, dict) else getattr(o, "category", None)) or ""
            out.append(OwnershipObservation(
                source=self.source, video_code=video_code, category=category,
            ))
        return out


class PikpakOwnershipCollector:
    """Best-effort presence from PikpakHistory TransferStatus='success'.

    pikpakapi has no file-listing API, so this records transfer OUTCOMES, not
    live presence; category is '' and presence is monotonic (Task 6 never
    sweeps pikpak)."""

    source = "pikpak"

    def collect(self, rows) -> list[OwnershipObservation]:
        out: list[OwnershipObservation] = []
        for r in rows or []:
            status = r.get("TransferStatus", r.get("transfer_status"))
            if status != "success":
                continue
            video_code = r.get("video_code") or r.get("VideoCode") or r.get("TorrentName")
            if not video_code:
                continue
            out.append(OwnershipObservation(source=self.source, video_code=video_code, category=""))
        return out


class NasOwnershipCollector:
    """Explicit stub (D-P2-4): NAS ownership is not collected yet.

    Returns [] and logs once so the gap is visible (no silent caps). The 'nas'
    source value stays valid in the CHECK constraint for forward-compat."""

    source = "nas"

    def collect(self) -> list[OwnershipObservation]:
        logger.info("nas ownership not collected (no RCLONE_NAS_REMOTE configured)")
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_ownership_collectors.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/ops/reconcile/collectors.py tests/unit/test_ownership_collectors.py
git commit -m "feat(reconcile): add read-only ownership collectors (gdrive/qb/pikpak/nas)"
```

---

## Task 6: `AcquisitionOutcomeRepo` landing methods

**Files:**
- Modify: `javdb/storage/repos/acquisition_outcome_repo.py` (append two methods minimally, mirroring existing methods at lines 47-99)
- Test: `tests/unit/test_in_library_derivation.py` (repo half; service half added in Task 7)

The `in_library` derivation (Task 7) needs to (a) list candidate outcomes in non-terminal-ish landing states and (b) flip one to `in_library` with `landed_at`. Add exactly two methods.

- [ ] **Step 1: Write the failing test (repo half)**

```python
# tests/unit/test_in_library_derivation.py
import sqlite3

import pytest

from javdb.ops.reconcile.models import AcquisitionOutcomeRecord
from javdb.storage.repos.acquisition_outcome_repo import AcquisitionOutcomeRepo

_OUTCOME_DDL = """
CREATE TABLE AcquisitionOutcome (
  qb_hash TEXT PRIMARY KEY, href TEXT NOT NULL DEFAULT '', video_code TEXT,
  category TEXT, state TEXT NOT NULL DEFAULT 'queued', queued_at TEXT,
  completed_at TEXT, landed_at TEXT, last_seen_at TEXT, session_id TEXT
);
"""


@pytest.fixture
def outcome_repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_OUTCOME_DDL)
    return AcquisitionOutcomeRepo(c)


def test_list_pending_landing_excludes_failed_and_already_landed(outcome_repo):
    outcome_repo.upsert(AcquisitionOutcomeRecord(qb_hash="q", video_code="A-1", state="queued"))
    outcome_repo.upsert(AcquisitionOutcomeRecord(qb_hash="d", video_code="A-2", state="downloading"))
    outcome_repo.upsert(AcquisitionOutcomeRecord(qb_hash="c", video_code="A-3", state="completed"))
    outcome_repo.upsert(AcquisitionOutcomeRecord(qb_hash="f", video_code="A-4", state="failed"))
    outcome_repo.upsert(AcquisitionOutcomeRecord(qb_hash="l", video_code="A-5", state="in_library"))
    codes = {r.video_code for r in outcome_repo.list_pending_landing()}
    assert codes == {"A-1", "A-2", "A-3"}   # failed + in_library excluded


def test_mark_in_library_sets_state_and_landed_at(outcome_repo):
    outcome_repo.upsert(AcquisitionOutcomeRecord(qb_hash="c", video_code="A-3", state="completed"))
    outcome_repo.mark_in_library("c", landed_at="t-land")
    got = outcome_repo.get("c")
    assert got.state == "in_library"
    assert got.landed_at == "t-land"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_in_library_derivation.py -v`
Expected: FAIL — `AttributeError: 'AcquisitionOutcomeRepo' object has no attribute 'list_pending_landing'`

- [ ] **Step 3: Append the two methods** to `AcquisitionOutcomeRepo` (after `list_active` at line 99)

```python
    def list_pending_landing(
        self, states: tuple[str, ...] = ("queued", "downloading", "completed"),
    ) -> list[AcquisitionOutcomeRecord]:
        """Rows whose video_code may still be promoted to in_library.

        Excludes 'failed' (left untouched, D-P2-8) and 'in_library' (already
        landed). Uses the indexed video_code column downstream."""
        placeholders = ", ".join(["?"] * len(states))
        rows = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM AcquisitionOutcome "
            f"WHERE state IN ({placeholders})",
            list(states),
        ).fetchall()
        return [_row_to_record(row) for row in rows]

    def mark_in_library(self, qb_hash: str, landed_at: str) -> None:
        self._conn.execute(
            "UPDATE AcquisitionOutcome SET state = 'in_library', landed_at = ? "
            "WHERE qb_hash = ?",
            [landed_at, qb_hash],
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_in_library_derivation.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/storage/repos/acquisition_outcome_repo.py tests/unit/test_in_library_derivation.py
git commit -m "feat(db): add AcquisitionOutcomeRepo landing methods (ADR-033 D-P2-8)"
```

---

## Task 7: `run_ownership` service (sole writer + diff-sweep + in_library)

**Files:**
- Modify: `javdb/ops/reconcile/service.py` (append `run_ownership` + `_derive_in_library`; Phase-1 `run()`/`record_queued`/`apply_cleanup_completed` stay untouched)
- Test: `tests/unit/test_run_ownership_service.py` (+ extend `test_in_library_derivation.py` service half)

`run_ownership` is the **only writer** of `OwnershipLedger`. It accepts an injected `repo` (ledger), an injected `outcome_repo` (for the in_library step), and injected source data (`rclone_inventory`, `qb_outcomes`, `pikpak_rows`) so unit tests never touch live sources. When omitted it loads them from the real persistence (`open_ledger_repo`, `open_outcome_repo`, `OperationsRepo().load_rclone_inventory()`, the pikpak/qb reads).

**Per-source diff-sweep (D-P2-5):** for each requested source, collect the full snapshot, UPSERT every observed row (`present=1`, `observed_at=now`), then `mark_absent(source, present_keys)` to flip prior rows of THAT source not in the snapshot to `present=0`. **Pikpak is exempt from the sweep** (monotonic append-only history). **NAS sweep is a no-op** (empty snapshot → would sweep everything; guard it so a stub source never wipes a previously-populated nas — Task 5 returns `[]`, so skip the sweep when the collector is a known-stub/empty source).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_run_ownership_service.py
import sqlite3

import pytest

from javdb.ops.reconcile import service
from javdb.ops.reconcile.models import OwnershipLedgerRecord, OwnershipOptions
from javdb.storage.repos.ownership_ledger_repo import OwnershipLedgerRepo
from javdb.storage.repos.acquisition_outcome_repo import AcquisitionOutcomeRepo
from javdb.ops.reconcile.models import AcquisitionOutcomeRecord
from javdb.spider.services.dedup import RcloneEntry

_LEDGER_DDL = """
CREATE TABLE OwnershipLedger (
  video_code TEXT NOT NULL, source TEXT NOT NULL, category TEXT NOT NULL DEFAULT '',
  path TEXT, size INTEGER, present INTEGER NOT NULL DEFAULT 1, observed_at TEXT,
  PRIMARY KEY (video_code, source, category)
);
"""
_OUTCOME_DDL = """
CREATE TABLE AcquisitionOutcome (
  qb_hash TEXT PRIMARY KEY, href TEXT NOT NULL DEFAULT '', video_code TEXT,
  category TEXT, state TEXT NOT NULL DEFAULT 'queued', queued_at TEXT,
  completed_at TEXT, landed_at TEXT, last_seen_at TEXT, session_id TEXT
);
"""


@pytest.fixture
def ledger():
    c = sqlite3.connect(":memory:")
    c.executescript(_LEDGER_DDL)
    return OwnershipLedgerRepo(c)


@pytest.fixture
def outcomes():
    c = sqlite3.connect(":memory:")
    c.executescript(_OUTCOME_DDL)
    return AcquisitionOutcomeRepo(c)


def test_run_ownership_upserts_gdrive_snapshot(ledger, outcomes):
    inv = {"A-1": [RcloneEntry("A-1", "无码破解", "中字", "/g/x", 10, 1, "t")]}
    res = service.run_ownership(
        OwnershipOptions(sources=("gdrive",)),
        repo=ledger, outcome_repo=outcomes,
        rclone_inventory=inv, qb_outcomes=[], pikpak_rows=[],
    )
    assert res.upserted == 1
    assert ledger.get("A-1", "gdrive", "无码破解|中字").present == 1


def test_run_ownership_sweeps_absent_gdrive_rows(ledger, outcomes):
    ledger.upsert(OwnershipLedgerRecord("OLD", "gdrive", "有码|无字", present=1, observed_at="t0"))
    inv = {"NEW": [RcloneEntry("NEW", "无码", "中字", "/g/n", 5, 1, "t")]}
    res = service.run_ownership(
        OwnershipOptions(sources=("gdrive",)),
        repo=ledger, outcome_repo=outcomes,
        rclone_inventory=inv, qb_outcomes=[], pikpak_rows=[],
    )
    assert res.swept_absent == 1
    assert ledger.get("OLD", "gdrive", "有码|无字").present == 0   # swept, not deleted
    assert ledger.get("NEW", "gdrive", "无码|中字").present == 1


def test_run_ownership_does_not_sweep_pikpak(ledger, outcomes):
    ledger.upsert(OwnershipLedgerRecord("OLDPK", "pikpak", "", present=1, observed_at="t0"))
    res = service.run_ownership(
        OwnershipOptions(sources=("pikpak",)),
        repo=ledger, outcome_repo=outcomes,
        rclone_inventory={}, qb_outcomes=[], pikpak_rows=[],  # empty pikpak snapshot
    )
    assert res.swept_absent == 0
    assert ledger.get("OLDPK", "pikpak", "").present == 1   # monotonic: not swept


def test_run_ownership_derives_in_library(ledger, outcomes):
    outcomes.upsert(AcquisitionOutcomeRecord(qb_hash="c", video_code="A-1", state="completed"))
    inv = {"A-1": [RcloneEntry("A-1", "无码", "中字", "/g/x", 10, 1, "t")]}
    res = service.run_ownership(
        OwnershipOptions(sources=("gdrive",)),
        repo=ledger, outcome_repo=outcomes,
        rclone_inventory=inv, qb_outcomes=[], pikpak_rows=[],
    )
    assert res.marked_in_library == 1
    assert outcomes.get("c").state == "in_library"
    assert outcomes.get("c").landed_at is not None


def test_run_ownership_leaves_failed_untouched(ledger, outcomes):
    outcomes.upsert(AcquisitionOutcomeRecord(qb_hash="f", video_code="A-1", state="failed"))
    inv = {"A-1": [RcloneEntry("A-1", "无码", "中字", "/g/x", 10, 1, "t")]}
    service.run_ownership(
        OwnershipOptions(sources=("gdrive",)),
        repo=ledger, outcome_repo=outcomes,
        rclone_inventory=inv, qb_outcomes=[], pikpak_rows=[],
    )
    assert outcomes.get("f").state == "failed"   # D-P2-8: failed left untouched


def test_run_ownership_dry_run_writes_nothing(ledger, outcomes):
    inv = {"A-1": [RcloneEntry("A-1", "无码", "中字", "/g/x", 10, 1, "t")]}
    service.run_ownership(
        OwnershipOptions(sources=("gdrive",), dry_run=True),
        repo=ledger, outcome_repo=outcomes,
        rclone_inventory=inv, qb_outcomes=[], pikpak_rows=[],
    )
    assert ledger.get("A-1", "gdrive", "无码|中字") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_run_ownership_service.py -v`
Expected: FAIL — `AttributeError: module 'javdb.ops.reconcile.service' has no attribute 'run_ownership'`

- [ ] **Step 3: Append `run_ownership` + `_derive_in_library`** to `javdb/ops/reconcile/service.py`

```python
# --- ADR-033 Phase 2: Ownership truth ---------------------------------------

from javdb.ops.reconcile.collectors import (  # noqa: E402
    GdriveOwnershipCollector,
    NasOwnershipCollector,
    PikpakOwnershipCollector,
    QbOwnershipCollector,
)
from javdb.ops.reconcile.models import (  # noqa: E402
    OWNERSHIP_SOURCES,
    PERSISTENT_OWNERSHIP_SOURCES,
    OwnershipLedgerRecord,
    OwnershipOptions,
    OwnershipResult,
)
from javdb.ops.reconcile.persistence import open_ledger_repo  # noqa: E402

# Sources whose snapshots drive a present=0 sweep of absent rows. pikpak is
# monotonic (append-only history); nas is a stub that returns []. Both are
# excluded so an empty/partial snapshot never wipes durable rows (D-P2-5).
_SWEPT_OWNERSHIP_SOURCES = frozenset({"gdrive", "qb"})


@contextlib.contextmanager
def _ledger_ctx(repo):
    if repo is not None:
        yield repo
    else:
        with open_ledger_repo() as opened:
            yield opened


@contextlib.contextmanager
def _outcome_ctx(repo):
    if repo is not None:
        yield repo
    else:
        with open_outcome_repo() as opened:
            yield opened


def _load_gdrive_inventory():
    from javdb.storage.repos.operations_repo import OperationsRepo
    from javdb.spider.services.dedup import _normalise_code, RcloneEntry

    raw = OperationsRepo().load_rclone_inventory()
    inventory: dict = {}
    for code, entries in raw.items():
        ncode = _normalise_code(code)
        inventory.setdefault(ncode, []).extend(
            RcloneEntry(
                video_code=_normalise_code(e.get("VideoCode", e.get("video_code", ncode))),
                sensor_category=e.get("SensorCategory", e.get("sensor_category", "")),
                subtitle_category=e.get("SubtitleCategory", e.get("subtitle_category", "")),
                folder_path=e.get("FolderPath", e.get("folder_path", "")),
                folder_size=int(e.get("FolderSize", e.get("folder_size", 0)) or 0),
                file_count=int(e.get("FileCount", e.get("file_count", 0)) or 0),
                scan_datetime=e.get("DateTimeScanned", e.get("scan_datetime", "")),
            )
            for e in entries
        )
    return inventory


def _collect_source(source, *, rclone_inventory, qb_outcomes, pikpak_rows):
    if source == "gdrive":
        return GdriveOwnershipCollector().collect(rclone_inventory)
    if source == "qb":
        return QbOwnershipCollector().collect(qb_outcomes)
    if source == "pikpak":
        return PikpakOwnershipCollector().collect(pikpak_rows)
    if source == "nas":
        return NasOwnershipCollector().collect()
    return []


def run_ownership(
    options: OwnershipOptions,
    *,
    repo=None,
    outcome_repo=None,
    rclone_inventory=None,
    qb_outcomes=None,
    pikpak_rows=None,
) -> OwnershipResult:
    """Reconcile OwnershipLedger against all sources. Sole writer of the Ledger."""
    result = OwnershipResult()
    sources = [s for s in options.sources if s in OWNERSHIP_SOURCES]
    if not sources:
        result.errors.append("no valid ownership sources requested")
        return result

    now = utc_now_iso()
    # Lazily load real source data only when a source is requested and no
    # injection was provided (mirrors Phase-1 run()'s lazy qB client build).
    if rclone_inventory is None and "gdrive" in sources:
        rclone_inventory = _load_gdrive_inventory()
    if qb_outcomes is None and "qb" in sources:
        with _outcome_ctx(outcome_repo) as o:
            qb_outcomes = [vars(r) for r in o.list_pending_landing()]
    if pikpak_rows is None and "pikpak" in sources:
        from javdb.storage.repos.operations_repo import OperationsRepo
        pikpak_rows = OperationsRepo().load_pikpak_history()  # see note below

    with _ledger_ctx(repo) as r:
        for source in sources:
            try:
                observations = _collect_source(
                    source,
                    rclone_inventory=rclone_inventory or {},
                    qb_outcomes=qb_outcomes or [],
                    pikpak_rows=pikpak_rows or [],
                )
            except Exception as exc:
                logger.warning("run_ownership: collect failed for %s", source, exc_info=True)
                result.errors.append(str(exc))
                continue
            result.observed += len(observations)
            present_keys = set()
            for obs in observations:
                present_keys.add((obs.video_code, obs.category))
                if options.dry_run:
                    continue
                try:
                    r.upsert(OwnershipLedgerRecord(
                        video_code=obs.video_code, source=obs.source, category=obs.category,
                        path=obs.path, size=obs.size, present=1, observed_at=now,
                    ))
                    result.upserted += 1
                except Exception as exc:
                    logger.warning("run_ownership: upsert failed", exc_info=True)
                    result.errors.append(str(exc))
            if not options.dry_run and source in _SWEPT_OWNERSHIP_SOURCES:
                try:
                    result.swept_absent += r.mark_absent(source, present_keys)
                except Exception as exc:
                    logger.warning("run_ownership: sweep failed for %s", source, exc_info=True)
                    result.errors.append(str(exc))

        # Final step: derive in_library from the now-current persistent sources.
        if options.derive_in_library and not options.dry_run:
            result.marked_in_library += _derive_in_library(r, outcome_repo, now)

    return result


def _derive_in_library(ledger_repo, outcome_repo, now: str) -> int:
    """Promote AcquisitionOutcome rows to in_library when their video_code has a
    present gdrive/nas Ledger entry (D-P2-8). 'failed' rows are left untouched
    (list_pending_landing excludes them)."""
    owned = ledger_repo.list_present_video_codes(PERSISTENT_OWNERSHIP_SOURCES)
    if not owned:
        return 0
    promoted = 0
    with _outcome_ctx(outcome_repo) as o:
        for rec in o.list_pending_landing():
            if rec.video_code and rec.video_code in owned:
                o.mark_in_library(rec.qb_hash, landed_at=now)
                promoted += 1
    return promoted
```

> **Note on `load_pikpak_history`:** `OperationsRepo` currently has `db_append_pikpak_history` (a write at `_db_operations.py:243`) but **no read helper** for `PikpakHistory`. Add a minimal `OperationsRepo.load_pikpak_history()` + `db_load_pikpak_history(db_path=...)` (mirroring `db_load_rclone_inventory`/`load_dedup_records` at `_db_operations.py` / `operations_repo.py:304-350`) that `SELECT *` from `PikpakHistory`. Pin it with a tiny test in `test_ownership_collectors.py` or a new `test_operations_repo_pikpak_read.py`. Keep it read-only.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_run_ownership_service.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Extend the in_library test with the service half**

Add to `tests/unit/test_in_library_derivation.py` a test that builds a ledger with a present `gdrive` row for `A-1` and an outcome `completed` row for `A-1`, calls `service.run_ownership(... derive_in_library=True ...)`, and asserts the outcome flips to `in_library`. (The `test_run_ownership_derives_in_library` case in Task 7's suite already covers this; the addition here keeps the in_library tests co-located.)

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/unit/test_in_library_derivation.py tests/unit/test_run_ownership_service.py -v`
Expected: PASS.

- [ ] **Step 7: Re-export from package `__init__`**

Append to `javdb/ops/reconcile/__init__.py` (extend the existing imports + `__all__`):

```python
from .models import (
    OWNERSHIP_SOURCES,
    PERSISTENT_OWNERSHIP_SOURCES,
    OwnershipLedgerRecord,
    OwnershipObservation,
    OwnershipOptions,
    OwnershipResult,
)
from .service import run_ownership
```
and add `"OwnershipLedgerRecord"`, `"OwnershipObservation"`, `"OwnershipOptions"`, `"OwnershipResult"`, `"OWNERSHIP_SOURCES"`, `"PERSISTENT_OWNERSHIP_SOURCES"`, `"run_ownership"` to `__all__`.

- [ ] **Step 8: Commit**

```bash
git add javdb/ops/reconcile/service.py javdb/ops/reconcile/__init__.py \
        javdb/storage/repos/operations_repo.py javdb/storage/db/_db_operations.py \
        tests/unit/test_run_ownership_service.py tests/unit/test_in_library_derivation.py
git commit -m "feat(reconcile): add run_ownership pass with diff-sweep + in_library (ADR-033 Phase 2)"
```

---

## Task 8: Dedup reads the Ledger (public API byte-identical)

**Files:**
- Modify: `javdb/spider/services/dedup.py:142-177` (re-point `load_rclone_inventory`; add `should_skip_from_ownership`)
- Test: `tests/unit/test_dedup_reads_ledger.py`

Per ADR-033 D-P2-6 / D-P2-9 the dedup **public API, the Rust accelerator seam, and the 3 consumer call sites stay BYTE-IDENTICAL** (`should_skip_from_rclone`/`check_dedup_upgrade`/`check_redownload_dedup_upgrade` at `dedup.py:219-435`; consumers at `javdb/spider/app/run_service.py:396`, `javdb/spider/detail/runner.py:1091-1100`, `javdb/pipeline/planner.py:100-118`). Only the **internals** of `load_rclone_inventory(csv_path)` change: it now prefers the Ledger's `source='gdrive'` rows, synthesizing `RcloneEntry` by splitting the glyph composite category back into `sensor_category` / `subtitle_category`. **Transitional fallback (D-P2-9):** if the Ledger has zero `gdrive` rows AND `RcloneInventory` is non-empty, fall back to the current `OperationsRepo().load_rclone_inventory()` path and `log()` that the fallback was taken. (Remove the fallback in a follow-up once the Ledger is proven populated in production.)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_dedup_reads_ledger.py
import inspect

from javdb.spider.services import dedup


def test_public_api_signatures_unchanged():
    # load_rclone_inventory still takes a single csv_path positional.
    sig = inspect.signature(dedup.load_rclone_inventory)
    assert list(sig.parameters) == ["csv_path"]
    # the three consumer-facing entrypoints keep their names + arity
    assert callable(dedup.should_skip_from_rclone)
    assert callable(dedup.check_dedup_upgrade)
    assert callable(dedup.check_redownload_dedup_upgrade)
    # new persistent-source presence helper exists
    assert callable(dedup.should_skip_from_ownership)


def test_ledger_rows_synthesize_rclone_entries(monkeypatch):
    # Stub the Ledger read to return one gdrive row with a glyph composite.
    class _FakeLedger:
        def list_by_source(self, source):
            from javdb.ops.reconcile.models import OwnershipLedgerRecord
            assert source == "gdrive"
            return [OwnershipLedgerRecord("ABC-1", "gdrive", "无码破解|中字", path="/g/x", size=10, present=1)]

        def list_present_video_codes(self, sources):
            return {"ABC-1"}

    monkeypatch.setattr(dedup, "_open_ledger_for_dedup", lambda: _FakeLedger())
    monkeypatch.setattr(dedup, "_ledger_has_gdrive_rows", lambda repo: True)

    inv = dedup.load_rclone_inventory("ignored.csv")
    entries = inv["ABC-1"]
    assert entries[0].sensor_category == "无码破解"
    assert entries[0].subtitle_category == "中字"
    assert entries[0].folder_path == "/g/x"


def test_should_skip_from_ownership_uses_persistent_sources(monkeypatch):
    class _FakeLedger:
        def list_present_video_codes(self, sources):
            assert set(sources) == {"gdrive", "nas"}
            return {"OWNED-1"}

    monkeypatch.setattr(dedup, "_open_ledger_for_dedup", lambda: _FakeLedger())
    assert dedup.should_skip_from_ownership("owned-1") is True   # normalised match
    assert dedup.should_skip_from_ownership("MISSING-9") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_dedup_reads_ledger.py -v`
Expected: FAIL — `AttributeError: module 'javdb.spider.services.dedup' has no attribute 'should_skip_from_ownership'`

- [ ] **Step 3: Re-point `load_rclone_inventory` + add the helpers** in `javdb/spider/services/dedup.py`. Keep `_csv_load_rclone_inventory` as the CSV branch. Replace the SQLite branch (lines 148-175) so it:
  1. opens a ledger repo (`_open_ledger_for_dedup()` → `from javdb.ops.reconcile.persistence import open_ledger_repo`; wrap in a thin helper that returns an entered repo so it's monkeypatchable in tests),
  2. reads `list_by_source("gdrive")`,
  3. if no gdrive rows AND `RcloneInventory` non-empty → `log()` + fall back to the current `OperationsRepo().load_rclone_inventory()` path (the existing code, extracted into `_legacy_load_rclone_inventory()`),
  4. otherwise synthesize `RcloneEntry` from each Ledger row by splitting `category` on the first `|`:

```python
def _split_glyph_category(category: str) -> tuple[str, str]:
    """Split a gdrive Ledger composite '<sensor>|<subtitle>' (D-P2-1) back into
    (sensor_category, subtitle_category). Empty/malformed -> ('', '')."""
    sensor, sep, subtitle = (category or "").partition("|")
    return (sensor, subtitle) if sep else ("", "")


def _ledger_to_inventory(rows) -> Dict[str, List[RcloneEntry]]:
    inventory: Dict[str, List[RcloneEntry]] = {}
    for rec in rows:
        if rec.present != 1:
            continue
        code = _normalise_code(rec.video_code)
        sensor, subtitle = _split_glyph_category(rec.category)
        inventory.setdefault(code, []).append(RcloneEntry(
            video_code=code, sensor_category=sensor, subtitle_category=subtitle,
            folder_path=rec.path or "", folder_size=int(rec.size or 0),
            file_count=0, scan_datetime=rec.observed_at or "",
        ))
    return inventory


def should_skip_from_ownership(video_code: str) -> bool:
    """Skip a video_code already owned in a *persistent* source (gdrive/nas).

    Unlike should_skip_from_rclone (gdrive only), this consults the multi-source
    Ledger but deliberately ignores qb/pikpak (in-transit / mirror) so a
    downloading qB torrent never suppresses upgrade detection (D-P2-7)."""
    from javdb.ops.reconcile.models import PERSISTENT_OWNERSHIP_SOURCES
    repo = _open_ledger_for_dedup()
    owned = repo.list_present_video_codes(PERSISTENT_OWNERSHIP_SOURCES)
    return _normalise_code(video_code) in owned
```

  Wire the new SQLite branch of `load_rclone_inventory` to call `_ledger_to_inventory(...)` (preferred) or `_legacy_load_rclone_inventory(csv_path)` (fallback), logging which path ran. The returned dict shape, key normalisation, and the empty-vs-populated log lines stay observably the same to all 3 consumers.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_dedup_reads_ledger.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Regression-check the dedup consumers**

Run: `pytest tests/unit -k "dedup or rclone_inventory or planner or detail_runner or run_service" -q`
Expected: all PASS (the 3 call sites are unchanged; only the inventory source moved).

- [ ] **Step 6: Commit**

```bash
git add javdb/spider/services/dedup.py tests/unit/test_dedup_reads_ledger.py
git commit -m "feat(dedup): read OwnershipLedger gdrive rows with RcloneInventory fallback (ADR-033 D-P2-6/9)"
```

---

## Task 9: CLI `--pass` selector + `run_ownership` wiring

**Files:**
- Modify: `apps/cli/ops/reconcile.py` (introduce `--pass {acquisition,ownership,all}`; default `all`)
- Test: `tests/smoke/test_reconcile_cli.py` (extend)

> **Selector ownership split (shared-module contract):** Phase 2 **introduces** `--pass` with choices `acquisition`, `ownership`, `all`. Phase 3 ([IMP-ADR033-03](IMP-ADR033-03-consumption-signal.md)) **adds** the `consumption` arm to the same choices. Default is **`all`** (sequential) so the cron picks up ownership without a workflow flag change beyond Task 10; `--pass acquisition` reproduces the exact Phase-1 behavior for rollback.

- [ ] **Step 1: Write the failing smoke test (extend `tests/smoke/test_reconcile_cli.py`)**

```python
def test_reconcile_cli_help_shows_pass_selector():
    import subprocess, sys
    r = subprocess.run(
        [sys.executable, "-m", "apps.cli.ops.reconcile", "--help"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "--pass" in r.stdout
    assert "ownership" in r.stdout


def test_reconcile_cli_pass_ownership_dry_run_runs():
    import subprocess, sys
    r = subprocess.run(
        [sys.executable, "-m", "apps.cli.ops.reconcile",
         "--pass", "ownership", "--dry-run", "--json", "--log-level", "WARNING"],
        capture_output=True, text=True,
    )
    # dry-run writes nothing; either clean exit or a captured error, never a traceback.
    assert r.returncode in (0, 2)
    assert "Traceback" not in r.stderr
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/smoke/test_reconcile_cli.py -v`
Expected: FAIL — `--help` output lacks `--pass`.

- [ ] **Step 3: Add the selector + dispatch** to `apps/cli/ops/reconcile.py`. Add `--pass` to the parser (`dest="pass_name"` because `pass` is a keyword), choices `("acquisition", "ownership", "all")`, default `"all"`. In `main`, branch:
  - `acquisition` or `all` → build `ReconcileOptions(...)` (existing code) and call `run(options)` → an `acquisition_result`.
  - `ownership` or `all` → build `OwnershipOptions()` and call `run_ownership(options)` → an `ownership_result`.
  - Aggregate exit: `return 2 if (acquisition_errors or ownership_errors) else 0`.

  Keep the existing `--json`/summary rendering; for `all`, emit both result blocks (e.g. a JSON object `{"acquisition": {...}, "ownership": {...}}` under `--json`, or two `log_summary_block` calls). Add the import `from javdb.ops.reconcile.service import run, run_ownership` and `from javdb.ops.reconcile.models import OwnershipOptions, ReconcileOptions`.

```python
parser.add_argument(
    "--pass", dest="pass_name", default="all",
    choices=("acquisition", "ownership", "all"),
    help="Which reconcile pass to run. Default: all (acquisition then ownership).",
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/smoke/test_reconcile_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Manual exit-code sanity-check**

Run: `python3 -m apps.cli.ops.reconcile --pass all --dry-run --json --log-level WARNING; echo "exit=$?"`
Expected: a JSON line; `exit=0` (no errors) or `exit=2` (source unreachable → captured). A traceback is **not** acceptable.

- [ ] **Step 6: Commit**

```bash
git add apps/cli/ops/reconcile.py tests/smoke/test_reconcile_cli.py
git commit -m "feat(reconcile): introduce --pass selector and wire run_ownership (ADR-033 Phase 2)"
```

---

## Task 10: Workflow `--pass all`, config, and docs

**Files:**
- Modify: `.github/workflows/ReconcileLibrary.yml`
- Modify: `config.py.example`
- Modify: `CONTEXT.md`, `docs/handbook/en/developer/cli-reference.md` (+ zh), `docs/handbook/en/self-hoster/github-actions-setup.md` (+ zh)

- [ ] **Step 1: Switch the workflow to `--pass all`** — in `.github/workflows/ReconcileLibrary.yml`, in the "Run acquisition-outcome reconciliation" step (lines 86-101), add `--pass all` to the `CMD=(...)` array (after `python3 -m apps.cli.ops.reconcile`). Update the step name + the leading comment (lines 1-6) to say it runs all closed-loop passes, not just acquisition. Add the gdrive/pikpak collector config notes to the "Generate config.py" env block (lines 67-84): the ownership pass reads `RcloneInventory` and `PikpakHistory` (already in the operations D1) so no new secret is strictly required for `gdrive`/`pikpak`/`qb`; document that `RCLONE_NAS_REMOTE` is unset (nas stays a stub).

```yaml
          CMD=(python3 -m apps.cli.ops.reconcile
               --pass all
               --stalled-after-days "$INPUT_STALLED_AFTER_DAYS"
               --json)
```

- [ ] **Step 2: Validate the workflow YAML**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ReconcileLibrary.yml')); print('yaml ok')"`
Expected: `yaml ok`

- [ ] **Step 3: Document the config knobs** — add to `config.py.example` near the ops settings (next to `RECONCILE_STALLED_DAYS` from Phase 1)

```python
# ADR-033 Phase 2 (ownership truth): which sources the `--pass ownership` reconcile
# pass collects. gdrive (RcloneInventory projection), qb (AcquisitionOutcome bridge),
# and pikpak (PikpakHistory success rows) work out of the box. 'nas' is a forward-compat
# stub until RCLONE_NAS_REMOTE is configured; leaving it in the list is harmless (it
# collects nothing and is never swept).
OWNERSHIP_SOURCES = ['gdrive', 'qb', 'pikpak', 'nas']

# Optional rclone remote for NAS ownership collection. Unset/empty keeps the NAS
# collector a no-op stub (it logs and yields nothing).
RCLONE_NAS_REMOTE = ''
```

- [ ] **Step 4: Update CONTEXT.md** — promote *Ownership ledger* from Phase-2-gated to live (it was marked Phase 2 by IMP-01 Task 10 Step 4). Add a *Present sweep* entry: "the per-source diff-sweep that flips prior Ledger rows of a source absent from the latest snapshot to `present=0` (audit-preserving; never deletes)." Add *in_library* as the acquisition-outcome terminal reached when a `video_code` lands in a persistent (`gdrive`/`nas`) Ledger source.

- [ ] **Step 5: Update CLI reference** — in `docs/handbook/en/developer/cli-reference.md`, document the new `--pass {acquisition,ownership,all}` flag on `python -m apps.cli.ops.reconcile` (default `all`, semantics of each pass), and note `run_ownership` collects `gdrive/qb/pikpak/nas`. Mirror into `docs/handbook/zh/developer/cli-reference.md` (translate prose; keep flags/commands verbatim).

- [ ] **Step 6: Update GitHub Actions setup** — in `docs/handbook/en/self-hoster/github-actions-setup.md`, update the `ReconcileLibrary.yml` section to say it now runs `--pass all` (acquisition + ownership), describe the four ownership sources, and that `nas` requires `RCLONE_NAS_REMOTE` (otherwise a no-op). Mirror into the zh file.

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/ReconcileLibrary.yml config.py.example CONTEXT.md docs/handbook
git commit -m "feat(ci): run --pass all in ReconcileLibrary + docs for ADR-033 Phase 2"
```

---

## Task 11: Full-suite verification gate

- [ ] **Step 1: Run the new tests together**

Run:
```bash
pytest tests/unit/test_ownership_ledger_models.py \
       tests/unit/test_ownership_ledger_repo.py \
       tests/unit/test_ownership_collectors.py \
       tests/unit/test_run_ownership_service.py \
       tests/unit/test_in_library_derivation.py \
       tests/unit/test_dedup_reads_ledger.py \
       tests/smoke/test_reconcile_cli.py -v
```
Expected: all PASS.

- [ ] **Step 2: Run the broader suite for regressions in touched areas**

Run: `pytest tests/unit -k "reconcile or ownership or acquisition or dedup or rclone or planner or run_service" -q`
Expected: all PASS (no import-time breakage from the modified `dedup.py` / `service.py` / `models.py` / repos).

- [ ] **Step 3: Confirm the collector read-only invariant**

The ownership collectors must perform **no** DB writes (ADR-033 D4); the only `upsert`/`mark_absent`/`mark_in_library`/`execute(` write call sites live in `service.py` and the repos.

Run: `grep -nE "upsert|mark_absent|mark_in_library|execute\(|INSERT|UPDATE|DELETE" javdb/ops/reconcile/collectors.py`
Expected: no output (collectors are read-only).

- [ ] **Step 4: Confirm the dedup public API is unchanged**

The three consumer entrypoints and `load_rclone_inventory`'s signature must be byte-identical to Phase-1 (D-P2-6).

Run:
```bash
python3 -c "import inspect; from javdb.spider.services import dedup; \
print('load_rclone_inventory', list(inspect.signature(dedup.load_rclone_inventory).parameters)); \
print('should_skip_from_rclone', list(inspect.signature(dedup.should_skip_from_rclone).parameters)); \
print('check_dedup_upgrade', list(inspect.signature(dedup.check_dedup_upgrade).parameters)); \
print('check_redownload_dedup_upgrade', list(inspect.signature(dedup.check_redownload_dedup_upgrade).parameters))"
```
Expected:
```
load_rclone_inventory ['csv_path']
should_skip_from_rclone ['video_code', 'inventory', 'enable_dedup']
check_dedup_upgrade ['video_code', 'new_torrent_types', 'rclone_entries']
check_redownload_dedup_upgrade ['video_code', 'redownload_categories', 'new_size_links', 'rclone_entries']
```
(Cross-check against `dedup.py:219`, `dedup.py:263`, `dedup.py:378` before asserting — if the live signatures differ from these, update the assertion to the current truth, not the other way around.)

- [ ] **Step 5: Confirm `run()` (Phase 1) is untouched**

Run: `git log -1 --name-only -- javdb/ops/reconcile/service.py >/dev/null && git diff --stat HEAD~9 -- javdb/ops/reconcile/service.py`
Expected: the diff shows only **appended** lines (the Phase-1 `run`/`record_queued`/`apply_cleanup_completed` bodies unchanged). If `run()` was modified, revert that hunk — Phase 2 adds a sibling entrypoint, it does not rewrite `run()`.

- [ ] **Step 6: Final commit (if any doc/test tidy-ups remain)**

```bash
git add -A && git commit -m "test(reconcile): full-suite verification for ADR-033 Phase 2"
```

---

## Plan Self-Review

**Spec coverage (ADR-033 Phase 2 roadmap row + D-P2-* decisions):**
- `OwnershipLedger` table (D1 + local mirror, `category` NOT NULL + CHECK + indexes) → Task 1. ✓
- `OwnershipLedgerRecord` model + source constants + `OwnershipOptions`/`OwnershipResult` → Task 2. ✓
- `OwnershipLedgerRepo` (upsert / list_by_source / mark_absent / list_present_video_codes / get) mirroring `AcquisitionOutcomeRepo` → Task 3. ✓
- `open_ledger_repo` persistence (call-time path resolution, BFR-016) → Task 4. ✓
- **D-P2-1** heterogeneous source-native `category` (gdrive glyph composite, qb English, pikpak/nas `''`) → Tasks 1 (DDL `DEFAULT ''`), 2 (`category=""` default + test), 5 (`GdriveOwnershipCollector` composite, `QbOwnershipCollector` English). ✓
- **D-P2-2** gdrive collector reads `RcloneInventory` live each pass; service UPSERTs → Task 5 (`GdriveOwnershipCollector`), Task 7 (`_load_gdrive_inventory` + run_ownership upsert). ✓
- **D-P2-3** max-FolderSize collapse onto one Ledger PK → Task 5 (`test_gdrive_composite_category_and_max_size_collapse`). ✓
- **D-P2-4** sources gdrive(full)/qb(bridge)/pikpak(best-effort)/nas(explicit stub, logs, stays in CHECK) → Task 5 (all four collectors + nas stub test). ✓
- **D-P2-5** per-source diff-sweep (UPSERT present=1 then sweep absent → present=0, never delete); pikpak monotonic (never swept); nas/empty guarded → Task 3 (`mark_absent`), Task 7 (`_SWEPT_OWNERSHIP_SOURCES`, `test_run_ownership_sweeps_absent_gdrive_rows`, `test_run_ownership_does_not_sweep_pikpak`). ✓
- **D-P2-6** dedup `load_rclone_inventory` re-pointed to Ledger gdrive rows + synthesized `RcloneEntry`; public API/Rust seam/3 call sites byte-identical → Task 8, gate Task 11 Step 4. ✓
- **D-P2-7** `should_skip_from_ownership` consults only persistent sources (gdrive/nas), ignores qb/pikpak → Task 8 (`test_should_skip_from_ownership_uses_persistent_sources`). ✓
- **D-P2-8** in_library derivation as the FINAL run_ownership step (persistent-source present + outcome in {queued,downloading,completed}); failed untouched; new `list_pending_landing`/`mark_in_library` → Task 6 (repo methods), Task 7 (`_derive_in_library`, `test_run_ownership_leaves_failed_untouched`). ✓
- **D-P2-9** transitional `RcloneInventory` fallback inside `load_rclone_inventory` (Ledger empty + RcloneInventory non-empty → fall back + log; remove later) → Task 8. ✓
- CLI `--pass {acquisition,ownership,all}` introduced (default `all`) → Task 9. ✓
- `ReconcileLibrary.yml` runs `--pass all` → Task 10 Step 1. ✓
- Config knobs (`OWNERSHIP_SOURCES`, `RCLONE_NAS_REMOTE`) → Task 10 Step 3. ✓
- Doc updates (CONTEXT.md *Ownership ledger* live + *Present sweep*; cli-reference `--pass`; github-actions-setup `--pass all` + sources) → Task 10. ✓
- Full-suite gate incl. collector read-only grep + dedup-API-unchanged grep + run() untouched check → Task 11. ✓

**Shared-module evolution consistency (does not contradict IMP-03):**
- `models.py` **appends** `OwnershipLedgerRecord`/`OwnershipObservation`/`OwnershipOptions`/`OwnershipResult`/source constants; Phase-1 models untouched; Phase-3 will append `MediaItem`/`ConsumptionSignalRecord`/`UnresolvedMediaItemRecord`. ✓
- `__init__.py` **appends** ownership re-exports beside Phase-1's; Phase-3 appends consumption re-exports. ✓
- `service.py` **appends** `run_ownership`/`_derive_in_library`; `run()` left untouched (Task 11 Step 5); Phase-3 appends `run_consumption`. ✓
- `persistence.py` **appends** `open_ledger_repo` (same call-time `get_db(OPERATIONS_DB_PATH)` pattern as `open_outcome_repo`); Phase-3 appends `open_consumption_repo`/`open_unresolved_repo`. ✓
- `collectors.py` **appends** the four ownership collectors (read-only seam); Phase-3's media adapters live in `integrations/`. ✓
- CLI `--pass`: Phase 2 **introduces** `acquisition`/`ownership`/`all`; Phase 3 **adds** `consumption` (stated in Task 9). ✓
- `ReconcileLibrary.yml`: Phase 2 sets `--pass all` (covers consumption forward); Phase 3 only adds `MEDIA_SERVERS` config notes. ✓

**Type consistency:** `OwnershipLedgerRecord`, `OwnershipObservation`, `OwnershipLedgerRepo` (`upsert`/`list_by_source`/`mark_absent`/`list_present_video_codes`/`get`), `OwnershipOptions`/`OwnershipResult`, `run_ownership`, and the new `AcquisitionOutcomeRepo.list_pending_landing`/`mark_in_library` are used identically across Tasks 2-11. ✓

**Known couplings (documented, intentional):**
- **Two distinct fallback paths — do not conflate them.** (1) The **transitional**
  `RcloneInventory` fallback inside `load_rclone_inventory` (dedup.py, D-P2-9, Task 8):
  read the Ledger gdrive rows, but if the Ledger has zero gdrive rows AND
  `RcloneInventory` is non-empty, fall back to the legacy SQL read + log. This branch
  is **temporary** and tracked for removal once the Ledger is proven populated in
  production. (2) The **injection** fallback inside `run_ownership` (service.py, Task 7):
  collector source data (`rclone_inventory`/`qb_outcomes`/`pikpak_rows`) is lazy-loaded
  only when not injected. This branch is **structural and permanent** — it exists so
  tests inject fakes and the sole-writer service stays the only DB writer. Only fallback
  (1) is scheduled for removal.
- `OperationsRepo.load_pikpak_history()` is a new read helper added in Task 7 because only a write helper (`db_append_pikpak_history`) existed; keep it read-only.
- gdrive ownership self-heals on every `run_ownership` pass (D-P2-2) and is **not** coupled into `WeeklyDedup`.

**Web surface note:** Web surface (read endpoints, dual-backend parity, Vue ownership view) is tracked by [ADR-034](../_archive/ADR-034-Media-Closed-Loop-Web-Surface/ADR-034-media-closed-loop-web-surface.md) Phase 2 and is out of scope here. No dual-backend parity gate applies to this IMP.

**Open verification dependencies (deployment-environment gates, mirror IMP-01):** Task 1 Steps 3-4 require live `wrangler` D1 access and `apps.cli.db.sync_d1_to_sqlite`; run them where other migrations are applied (Cloudflare creds present). All other tasks verify with in-memory sqlite and the local bootstrap DDL.
