# IMP-ADR033-01: Acquisition Outcome (Media Closed-Loop Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-033](ADR-033-media-closed-loop.md) (umbrella) — this is **Phase 1** of three.

**Goal:** Record the real fate of every selected torrent after qBittorrent (`queued → downloading → completed`, plus `stalled`/`failed`) in a new D1-canonical `AcquisitionOutcome` table, written by an async `Options→Result` reconcile service.

**Architecture:** A new `javdb/ops/reconcile/` module mirrors the ADR-026 diagnosis module shape: typed `models.py`, read-only `collectors.py` (`SourceCollector` seam, `QbCollector` only in Phase 1), a `service.py` whose `run(ReconcileOptions) -> ReconcileResult` is the **single writer**, and `persistence.py` wiring `get_db(OPERATIONS_DB_PATH)`. The uploader writes a `state=queued` row at add-time (reusing `extract_hash_from_magnet`); the existing cleanup step pushes `state=completed` for the hashes it removes (ADR-033 D3); a new `ReconcileLibrary.yml` cron drives `downloading`/`stalled`/`failed` derivation.

**Tech Stack:** Python 3, `sqlite3`/D1 via `javdb.storage.db.get_db`, `dataclasses`, `pytest`, Cloudflare D1 + `wrangler`, GitHub Actions.

**Storage placement:** `AcquisitionOutcome` lives in the **operations** logical DB (`javdb-operations`), alongside the future `OwnershipLedger` (Phase 2) — operational ledger data, not history/dedup, not reports/sessions.

---

## File Structure

| Path | Create/Modify | Responsibility |
| --- | --- | --- |
| `javdb/migrations/d1/2026_05_29_add_acquisition_outcome.sql` | Create | `AcquisitionOutcome` DDL + indexes (D1-first) |
| `javdb/ops/reconcile/__init__.py` | Create | Package marker + public re-exports |
| `javdb/ops/reconcile/models.py` | Create | `AcquisitionState`, `AcquisitionOutcomeRecord`, `Observation`, `ReconcileOptions`, `ReconcileResult`, `utc_now_iso` |
| `javdb/storage/repos/acquisition_outcome_repo.py` | Create | `AcquisitionOutcomeRepo` (upsert / mark_state / get / list_active) |
| `javdb/ops/reconcile/persistence.py` | Create | `get_db(OPERATIONS_DB_PATH)` wiring → repo context |
| `javdb/ops/reconcile/collectors.py` | Create | `SourceCollector` Protocol + read-only `QbCollector` |
| `javdb/ops/reconcile/service.py` | Create | `run()` (sole writer) + `record_queued()` + `apply_cleanup_completed()` |
| `apps/cli/ops/reconcile.py` | Create | CLI adapter: argparse + exit codes + `--json` |
| `javdb/integrations/qb/uploader/service.py:643-660` | Modify | Write `state=queued` row on successful add |
| `javdb/integrations/pikpak/bridge/service.py:515,544` | Modify | Push `state=completed` from cleanup stats |
| `.github/workflows/ReconcileLibrary.yml` | Create | Cron + manual reconcile, self-hosted runner option |
| `config.py.example` | Modify | Document `RECONCILE_STALLED_DAYS` |
| `tests/unit/test_acquisition_outcome_models.py` | Create | Model/round-trip tests |
| `tests/unit/test_acquisition_outcome_repo.py` | Create | Repo upsert/mark_state tests (in-memory sqlite) |
| `tests/unit/test_reconcile_qb_collector.py` | Create | Collector transform tests |
| `tests/unit/test_reconcile_service.py` | Create | `Options→Result` service tests (fakes) |
| `tests/unit/test_uploader_acquisition_queued.py` | Create | Uploader writes queued row |
| `tests/unit/test_cleanup_acquisition_completed.py` | Create | Cleanup pushes completed |
| `tests/smoke/test_reconcile_cli.py` | Create | CLI smoke (`--help`, dry-run exit 0) |

**Naming contract (used across tasks — keep verbatim):**
`AcquisitionState = Literal["queued","downloading","completed","in_library","stalled","failed"]`;
record class `AcquisitionOutcomeRecord`; repo class `AcquisitionOutcomeRepo` with methods `upsert(record)`, `mark_state(qb_hash, state, *, completed_at=None, last_seen_at=None)`, `get(qb_hash)`, `list_active()`; service functions `run(options, *, repo=None, qb_client=None)`, `record_queued(torrent, session_id, *, repo=None)`, `apply_cleanup_completed(stats, *, repo=None)`; collector `QbCollector().collect(torrents) -> list[Observation]`.

> **Phase-2-gated:** `in_library` is set only when `OwnershipLedger` exists (ADR-033 D6, Phase 2). In Phase 1 the state machine **stops at `completed`**; `in_library` is a valid enum value but is never written here.

---

## Task 1: D1 migration — `AcquisitionOutcome` table

**Files:**
- Create: `javdb/migrations/d1/2026_05_29_add_acquisition_outcome.sql`

- [ ] **Step 1: Write the migration SQL**

```sql
-- 2026-05-29: Add AcquisitionOutcome table (ADR-033 Phase 1).
--
-- Apply with:
--   wrangler d1 execute javdb-operations --remote \
--     --file=javdb/migrations/d1/2026_05_29_add_acquisition_outcome.sql
--
-- AcquisitionOutcome records the real fate of each selected torrent after qB.
-- It is enrichment: written off the Pending->Commit path, idempotent UPSERT
-- by qb_hash. session_id is provenance only (the run that queued the torrent).

CREATE TABLE IF NOT EXISTS AcquisitionOutcome (
  qb_hash       TEXT PRIMARY KEY,
  href          TEXT NOT NULL DEFAULT '',
  video_code    TEXT,
  category      TEXT,
  state         TEXT NOT NULL DEFAULT 'queued'
    CHECK (state IN ('queued','downloading','completed','in_library','stalled','failed')),
  queued_at     TEXT,
  completed_at  TEXT,
  landed_at     TEXT,
  last_seen_at  TEXT,
  session_id    TEXT
);

CREATE INDEX IF NOT EXISTS idx_acq_outcome_state ON AcquisitionOutcome(state);
CREATE INDEX IF NOT EXISTS idx_acq_outcome_video_code ON AcquisitionOutcome(video_code);
CREATE INDEX IF NOT EXISTS idx_acq_outcome_session ON AcquisitionOutcome(session_id);
CREATE INDEX IF NOT EXISTS idx_acq_outcome_last_seen ON AcquisitionOutcome(last_seen_at);
```

- [ ] **Step 2: Apply to D1 (operations)**

Run:
```bash
wrangler d1 execute javdb-operations --remote \
  --file=javdb/migrations/d1/2026_05_29_add_acquisition_outcome.sql
```
Expected: `wrangler` reports the statements executed without error.

- [ ] **Step 3: Re-align the local SQLite mirror from D1 (D1-canonical rule)**

Run:
```bash
python3 -m apps.cli.db.sync_d1_to_sqlite --apply --force-overwrite-all
```
Expected: log shows `AcquisitionOutcome` rebuilt from D1's verbatim DDL; exit code 0.

- [ ] **Step 4: Verify the table exists locally**

Run:
```bash
python3 -c "import sqlite3,glob; p=glob.glob('reports/operations.db')[0]; print(sqlite3.connect(p).execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name='AcquisitionOutcome'\").fetchone())"
```
Expected: `('AcquisitionOutcome',)`

- [ ] **Step 5: Commit**

```bash
git add javdb/migrations/d1/2026_05_29_add_acquisition_outcome.sql
git commit -m "feat(db): add AcquisitionOutcome table (ADR-033 Phase 1)"
```

---

## Task 2: Typed models

**Files:**
- Create: `javdb/ops/reconcile/__init__.py`
- Create: `javdb/ops/reconcile/models.py`
- Test: `tests/unit/test_acquisition_outcome_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_acquisition_outcome_models.py
from javdb.ops.reconcile.models import (
    AcquisitionOutcomeRecord,
    Observation,
    ReconcileOptions,
    ReconcileResult,
    utc_now_iso,
)


def test_utc_now_iso_has_trailing_z():
    assert utc_now_iso().endswith("Z")


def test_record_defaults_to_queued():
    rec = AcquisitionOutcomeRecord(qb_hash="abc", href="/v/1")
    assert rec.state == "queued"
    assert rec.video_code is None


def test_observation_is_frozen():
    obs = Observation(source="qb", qb_hash="abc", state="downloading", observed_at="t")
    assert obs.source == "qb"


def test_reconcile_result_starts_empty():
    res = ReconcileResult()
    assert res.observed == 0
    assert res.errors == []


def test_reconcile_options_defaults():
    opts = ReconcileOptions()
    assert opts.sources == ("qb",)
    assert opts.stalled_after_days == 7
    assert opts.dry_run is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_acquisition_outcome_models.py -v`
Expected: FAIL — `ModuleNotFoundError: javdb.ops.reconcile.models`

- [ ] **Step 3: Create the package marker**

```python
# javdb/ops/reconcile/__init__.py
"""Media closed-loop reconciliation (ADR-033 Phase 1)."""
```

- [ ] **Step 4: Write the models**

```python
# javdb/ops/reconcile/models.py
"""Typed contracts for ADR-033 Phase 1 acquisition-outcome reconciliation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional, Sequence

AcquisitionState = Literal[
    "queued", "downloading", "completed", "in_library", "stalled", "failed"
]
ACQUISITION_STATES: tuple[str, ...] = (
    "queued", "downloading", "completed", "in_library", "stalled", "failed",
)
# Terminal in Phase 1 (in_library is Phase-2-gated; see ADR-033 D6).
TERMINAL_STATES: frozenset[str] = frozenset({"completed", "in_library", "failed"})


def utc_now_iso() -> str:
    """ISO 8601 UTC timestamp with a trailing Z."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class AcquisitionOutcomeRecord:
    qb_hash: str
    href: str = ""
    video_code: Optional[str] = None
    category: Optional[str] = None
    state: AcquisitionState = "queued"
    queued_at: Optional[str] = None
    completed_at: Optional[str] = None
    landed_at: Optional[str] = None
    last_seen_at: Optional[str] = None
    session_id: Optional[str] = None


@dataclass(frozen=True)
class Observation:
    """Normalized, read-only signal from one source about one torrent."""
    source: str                 # 'qb' in Phase 1
    qb_hash: str
    state: AcquisitionState     # 'downloading' | 'completed'
    observed_at: str


@dataclass
class ReconcileOptions:
    sources: Sequence[str] = ("qb",)
    categories: Sequence[str] = ("JavDB", "Ad Hoc")
    stalled_after_days: int = 7
    dry_run: bool = False


@dataclass
class ReconcileResult:
    observed: int = 0
    outcomes_updated: int = 0
    marked_downloading: int = 0
    marked_completed: int = 0
    marked_stalled: int = 0
    marked_failed: int = 0
    errors: list[str] = field(default_factory=list)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/test_acquisition_outcome_models.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add javdb/ops/reconcile/__init__.py javdb/ops/reconcile/models.py tests/unit/test_acquisition_outcome_models.py
git commit -m "feat(reconcile): add acquisition-outcome models (ADR-033 Phase 1)"
```

---

## Task 3: `AcquisitionOutcomeRepo`

**Files:**
- Create: `javdb/storage/repos/acquisition_outcome_repo.py`
- Test: `tests/unit/test_acquisition_outcome_repo.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_acquisition_outcome_repo.py
import sqlite3

import pytest

from javdb.ops.reconcile.models import AcquisitionOutcomeRecord
from javdb.storage.repos.acquisition_outcome_repo import AcquisitionOutcomeRepo

_DDL = """
CREATE TABLE AcquisitionOutcome (
  qb_hash TEXT PRIMARY KEY, href TEXT NOT NULL DEFAULT '', video_code TEXT,
  category TEXT, state TEXT NOT NULL DEFAULT 'queued', queued_at TEXT,
  completed_at TEXT, landed_at TEXT, last_seen_at TEXT, session_id TEXT
);
"""


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return c


def test_upsert_then_get(conn):
    repo = AcquisitionOutcomeRepo(conn)
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="h1", href="/v/1", state="queued"))
    got = repo.get("h1")
    assert got.qb_hash == "h1"
    assert got.state == "queued"


def test_upsert_is_idempotent_on_hash(conn):
    repo = AcquisitionOutcomeRepo(conn)
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="h1", href="/v/1", state="queued"))
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="h1", href="/v/1", state="downloading"))
    assert repo.get("h1").state == "downloading"
    assert conn.execute("SELECT COUNT(*) FROM AcquisitionOutcome").fetchone()[0] == 1


def test_mark_state_updates_existing(conn):
    repo = AcquisitionOutcomeRepo(conn)
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="h1", href="/v/1", state="queued"))
    repo.mark_state("h1", "completed", completed_at="t2", last_seen_at="t2")
    got = repo.get("h1")
    assert got.state == "completed"
    assert got.completed_at == "t2"


def test_mark_state_inserts_minimal_when_absent(conn):
    repo = AcquisitionOutcomeRepo(conn)
    repo.mark_state("orphan", "completed", completed_at="t2", last_seen_at="t2")
    got = repo.get("orphan")
    assert got.state == "completed"
    assert got.href == ""


def test_list_active_excludes_terminal(conn):
    repo = AcquisitionOutcomeRepo(conn)
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="a", state="queued"))
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="b", state="downloading"))
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="c", state="completed"))
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="d", state="failed"))
    active = {r.qb_hash for r in repo.list_active()}
    assert active == {"a", "b"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_acquisition_outcome_repo.py -v`
Expected: FAIL — `ModuleNotFoundError: javdb.storage.repos.acquisition_outcome_repo`

- [ ] **Step 3: Write the repo**

```python
# javdb/storage/repos/acquisition_outcome_repo.py
"""Repository for ADR-033 AcquisitionOutcome rows (operations DB)."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Optional

from javdb.ops.reconcile.models import AcquisitionOutcomeRecord, utc_now_iso

logger = logging.getLogger(__name__)

_COLUMNS = (
    "qb_hash", "href", "video_code", "category", "state",
    "queued_at", "completed_at", "landed_at", "last_seen_at", "session_id",
)
_ACTIVE_STATES = ("queued", "downloading")


def _row_to_record(row: Any) -> AcquisitionOutcomeRecord:
    return AcquisitionOutcomeRecord(**{c: row[c] for c in _COLUMNS})


class AcquisitionOutcomeRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        try:
            self._conn.row_factory = sqlite3.Row
        except Exception:
            logger.debug("row_factory set failed", exc_info=True)

    def upsert(self, record: AcquisitionOutcomeRecord) -> None:
        values = [getattr(record, c) for c in _COLUMNS]
        placeholders = ", ".join(["?"] * len(_COLUMNS))
        columns = ", ".join(_COLUMNS)
        updates = ", ".join(f"{c}=excluded.{c}" for c in _COLUMNS if c != "qb_hash")
        self._conn.execute(
            f"INSERT INTO AcquisitionOutcome ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT(qb_hash) DO UPDATE SET {updates}",
            values,
        )

    def mark_state(
        self,
        qb_hash: str,
        state: str,
        *,
        completed_at: Optional[str] = None,
        last_seen_at: Optional[str] = None,
    ) -> None:
        """Partial state transition. Inserts a minimal row if qb_hash is unknown
        (e.g. a torrent added outside our pipeline that the cleanup observed)."""
        last_seen = last_seen_at or utc_now_iso()
        self._conn.execute(
            """
            INSERT INTO AcquisitionOutcome (qb_hash, href, state, completed_at, last_seen_at)
            VALUES (?, '', ?, ?, ?)
            ON CONFLICT(qb_hash) DO UPDATE SET
              state=excluded.state,
              completed_at=COALESCE(excluded.completed_at, AcquisitionOutcome.completed_at),
              last_seen_at=excluded.last_seen_at
            """,
            [qb_hash, state, completed_at, last_seen],
        )

    def get(self, qb_hash: str) -> Optional[AcquisitionOutcomeRecord]:
        row = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM AcquisitionOutcome WHERE qb_hash = ?",
            [qb_hash],
        ).fetchone()
        return None if row is None else _row_to_record(row)

    def list_active(self) -> list[AcquisitionOutcomeRecord]:
        placeholders = ", ".join(["?"] * len(_ACTIVE_STATES))
        rows = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM AcquisitionOutcome "
            f"WHERE state IN ({placeholders})",
            list(_ACTIVE_STATES),
        ).fetchall()
        return [_row_to_record(r) for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_acquisition_outcome_repo.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/storage/repos/acquisition_outcome_repo.py tests/unit/test_acquisition_outcome_repo.py
git commit -m "feat(db): add AcquisitionOutcomeRepo with idempotent upsert/mark_state"
```

---

## Task 4: Persistence wiring (`get_db(OPERATIONS_DB_PATH)`)

**Files:**
- Create: `javdb/ops/reconcile/persistence.py`

> No new unit test here — exercised end-to-end by Task 6's service tests (which inject a repo) and Task 10's CLI smoke (which uses real `get_db`). Keeping this file tiny and dependency-light is the point.

- [ ] **Step 1: Write the persistence helper**

```python
# javdb/ops/reconcile/persistence.py
"""D1-canonical persistence wiring for acquisition-outcome reconciliation."""

from __future__ import annotations

import contextlib
import logging

from javdb.storage.db import OPERATIONS_DB_PATH, get_db
from javdb.storage.repos.acquisition_outcome_repo import AcquisitionOutcomeRepo

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def open_outcome_repo():
    """Yield an AcquisitionOutcomeRepo over the operations DB connection.

    Routing honours STORAGE_BACKEND via get_db (D1 / sqlite / dual)."""
    with get_db(OPERATIONS_DB_PATH) as conn:
        yield AcquisitionOutcomeRepo(conn)
```

- [ ] **Step 2: Verify it imports**

Run: `python3 -c "from javdb.ops.reconcile.persistence import open_outcome_repo; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add javdb/ops/reconcile/persistence.py
git commit -m "feat(reconcile): wire AcquisitionOutcome persistence to operations DB"
```

---

## Task 5: `QbCollector` (read-only)

**Files:**
- Create: `javdb/ops/reconcile/collectors.py`
- Test: `tests/unit/test_reconcile_qb_collector.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_reconcile_qb_collector.py
from javdb.ops.reconcile.collectors import QbCollector


def test_collect_maps_progress_and_state():
    torrents = [
        {"hash": "a", "progress": 0.4, "state": "downloading"},
        {"hash": "b", "progress": 1.0, "state": "uploading"},
        {"hash": "c", "progress": 0.0, "state": "stalledDL"},
    ]
    obs = {o.qb_hash: o for o in QbCollector().collect(torrents)}
    assert obs["a"].state == "downloading"
    assert obs["b"].state == "completed"   # progress == 1.0
    assert obs["c"].state == "downloading"
    assert obs["a"].source == "qb"
    assert obs["a"].observed_at  # non-empty timestamp


def test_collect_skips_hashless_rows():
    obs = QbCollector().collect([{"progress": 1.0, "state": "uploading"}])
    assert obs == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_reconcile_qb_collector.py -v`
Expected: FAIL — `ModuleNotFoundError: javdb.ops.reconcile.collectors`

- [ ] **Step 3: Write the collector**

```python
# javdb/ops/reconcile/collectors.py
"""Read-only source collectors for reconciliation (ADR-033 D4).

A collector NEVER writes the DB. It transforms a source read into normalized
Observations; the service is the only writer."""

from __future__ import annotations

from typing import Iterable, Protocol

from javdb.ops.reconcile.models import Observation, utc_now_iso

# qB torrent states that mean "finished downloading" even if progress<1 rounding.
_QB_COMPLETED_STATES = frozenset({
    "uploading", "stalledUP", "pausedUP", "queuedUP", "forcedUP", "checkingUP",
})


class SourceCollector(Protocol):
    source: str

    def collect(self, torrents: Iterable[dict]) -> list[Observation]: ...


class QbCollector:
    source = "qb"

    def collect(self, torrents: Iterable[dict]) -> list[Observation]:
        now = utc_now_iso()
        out: list[Observation] = []
        for t in torrents:
            qb_hash = t.get("hash")
            if not qb_hash:
                continue
            progress = t.get("progress") or 0.0
            qb_state = t.get("state") or ""
            completed = progress >= 1.0 or qb_state in _QB_COMPLETED_STATES
            out.append(Observation(
                source=self.source,
                qb_hash=qb_hash,
                state="completed" if completed else "downloading",
                observed_at=now,
            ))
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_reconcile_qb_collector.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/ops/reconcile/collectors.py tests/unit/test_reconcile_qb_collector.py
git commit -m "feat(reconcile): add read-only QbCollector"
```

---

## Task 6: Reconcile service (`run` + `record_queued` + `apply_cleanup_completed`)

**Files:**
- Create: `javdb/ops/reconcile/service.py`
- Test: `tests/unit/test_reconcile_service.py`

The service is the **only writer**. It accepts an optional `repo` (for tests) and an
optional `qb_client` (a read-only client exposing
`get_torrents_multiple_categories(categories, torrent_filter=...)`); when omitted it
builds the real ones.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_reconcile_service.py
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from javdb.ops.reconcile.models import AcquisitionOutcomeRecord, ReconcileOptions
from javdb.ops.reconcile import service
from javdb.storage.repos.acquisition_outcome_repo import AcquisitionOutcomeRepo

_DDL = """
CREATE TABLE AcquisitionOutcome (
  qb_hash TEXT PRIMARY KEY, href TEXT NOT NULL DEFAULT '', video_code TEXT,
  category TEXT, state TEXT NOT NULL DEFAULT 'queued', queued_at TEXT,
  completed_at TEXT, landed_at TEXT, last_seen_at TEXT, session_id TEXT
);
"""


def _old_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z")


@pytest.fixture
def repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return AcquisitionOutcomeRepo(c)


class _FakeQb:
    def __init__(self, torrents):
        self._t = torrents

    def get_torrents_multiple_categories(self, categories, torrent_filter="downloading"):
        return self._t


def test_record_queued_writes_queued_row(repo):
    torrent = {"magnet": "magnet:?xt=urn:btih:" + "a" * 40, "href": "/v/1",
               "video_code": "ABC-123", "type": "subtitle"}
    service.record_queued(torrent, session_id="S1", repo=repo)
    got = repo.get("a" * 40)
    assert got.state == "queued"
    assert got.video_code == "ABC-123"
    assert got.category == "subtitle"
    assert got.session_id == "S1"


def test_record_queued_ignores_unparseable_magnet(repo):
    service.record_queued({"magnet": "not-a-magnet"}, session_id="S1", repo=repo)
    assert repo.list_active() == []


def test_apply_cleanup_completed_marks_hashes(repo):
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="h1", href="/v/1", state="queued"))
    res = service.apply_cleanup_completed({"hashes": ["h1", "h2"]}, repo=repo)
    assert repo.get("h1").state == "completed"
    assert repo.get("h2").state == "completed"   # minimal-insert for orphan
    assert res.marked_completed == 2


def test_run_marks_downloading_from_observation(repo):
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="d1", href="/v/1", state="queued",
                                         last_seen_at=_old_iso(0)))
    qb = _FakeQb([{"hash": "d1", "progress": 0.5, "state": "downloading"}])
    res = service.run(ReconcileOptions(), repo=repo, qb_client=qb)
    assert repo.get("d1").state == "downloading"
    assert res.marked_downloading == 1


def test_run_marks_stalled_when_absent_and_old(repo):
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="s1", href="/v/1", state="queued",
                                         last_seen_at=_old_iso(10)))
    qb = _FakeQb([])  # no longer in qB, and not completed
    res = service.run(ReconcileOptions(stalled_after_days=7), repo=repo, qb_client=qb)
    assert repo.get("s1").state == "stalled"
    assert res.marked_stalled == 1


def test_run_marks_failed_when_long_overdue(repo):
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="f1", href="/v/1", state="downloading",
                                         last_seen_at=_old_iso(20)))
    qb = _FakeQb([])
    res = service.run(ReconcileOptions(stalled_after_days=7), repo=repo, qb_client=qb)
    assert repo.get("f1").state == "failed"   # > 2x threshold
    assert res.marked_failed == 1


def test_run_dry_run_writes_nothing(repo):
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="d1", href="/v/1", state="queued",
                                         last_seen_at=_old_iso(0)))
    qb = _FakeQb([{"hash": "d1", "progress": 0.5, "state": "downloading"}])
    service.run(ReconcileOptions(dry_run=True), repo=repo, qb_client=qb)
    assert repo.get("d1").state == "queued"  # unchanged
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_reconcile_service.py -v`
Expected: FAIL — `AttributeError`/`ImportError` (service functions absent)

- [ ] **Step 3: Write the service**

```python
# javdb/ops/reconcile/service.py
"""Reconcile service — the sole writer of AcquisitionOutcome (ADR-033 D4/D10)."""

from __future__ import annotations

import contextlib
import logging
from datetime import datetime, timezone

from javdb.integrations.qb.client import extract_hash_from_magnet
from javdb.ops.reconcile.collectors import QbCollector
from javdb.ops.reconcile.models import (
    AcquisitionOutcomeRecord,
    ReconcileOptions,
    ReconcileResult,
    utc_now_iso,
)
from javdb.ops.reconcile.persistence import open_outcome_repo

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def _repo_ctx(repo):
    """Use an injected repo as-is, else open the operations DB repo."""
    if repo is not None:
        yield repo
    else:
        with open_outcome_repo() as opened:
            yield opened


def _age_days(iso_ts: str | None) -> float:
    if not iso_ts:
        return 0.0
    try:
        parsed = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return (datetime.now(timezone.utc) - parsed).total_seconds() / 86400.0


def record_queued(torrent: dict, session_id: str | None, *, repo=None) -> None:
    """Write a state=queued row for a torrent just added to qB. Best-effort
    enrichment: failures are logged, never raised (must not break the upload)."""
    qb_hash = extract_hash_from_magnet(torrent.get("magnet", ""))
    if not qb_hash:
        logger.debug("record_queued: unparseable magnet, skipping")
        return
    now = utc_now_iso()
    record = AcquisitionOutcomeRecord(
        qb_hash=qb_hash,
        href=torrent.get("href") or "",
        video_code=torrent.get("video_code") or None,
        category=torrent.get("type") or None,
        state="queued",
        queued_at=now,
        last_seen_at=now,
        session_id=session_id,
    )
    try:
        with _repo_ctx(repo) as r:
            r.upsert(record)
    except Exception:
        logger.warning("record_queued: failed to persist queued outcome", exc_info=True)


def apply_cleanup_completed(stats: dict, *, repo=None) -> ReconcileResult:
    """Push state=completed for the hashes the cleanup step removed (ADR-033 D3)."""
    result = ReconcileResult()
    hashes = [h for h in (stats or {}).get("hashes", []) if h]
    if not hashes:
        return result
    now = utc_now_iso()
    try:
        with _repo_ctx(repo) as r:
            for qb_hash in hashes:
                r.mark_state(qb_hash, "completed", completed_at=now, last_seen_at=now)
                result.marked_completed += 1
    except Exception as exc:
        logger.warning("apply_cleanup_completed: persist failed", exc_info=True)
        result.errors.append(str(exc))
    return result


def run(options: ReconcileOptions, *, repo=None, qb_client=None) -> ReconcileResult:
    """Reconcile active outcomes against live sources. Sole writer."""
    result = ReconcileResult()
    now = utc_now_iso()
    with _repo_ctx(repo) as r:
        active = {rec.qb_hash: rec for rec in r.list_active()}

        observations = {}
        if "qb" in options.sources:
            client = qb_client or _build_qb_client(options)
            torrents = client.get_torrents_multiple_categories(
                list(options.categories), torrent_filter="all"
            )
            for obs in QbCollector().collect(torrents):
                observations[obs.qb_hash] = obs
        result.observed = len(observations)

        for qb_hash, rec in active.items():
            obs = observations.get(qb_hash)
            new_state = None
            if obs is not None:
                if obs.state == "completed" and rec.state != "completed":
                    new_state, extra = "completed", {"completed_at": now}
                    result.marked_completed += 1
                elif obs.state == "downloading" and rec.state != "downloading":
                    new_state, extra = "downloading", {}
                    result.marked_downloading += 1
                else:
                    new_state, extra = rec.state, {}  # refresh last_seen only
                rec.last_seen_at = now
            else:
                # Absent from qB and not completed → stall/fail by age.
                age = _age_days(rec.last_seen_at or rec.queued_at)
                if age >= 2 * options.stalled_after_days:
                    new_state, extra = "failed", {}
                    result.marked_failed += 1
                elif age >= options.stalled_after_days:
                    new_state, extra = "stalled", {}
                    result.marked_stalled += 1
                else:
                    continue  # still within grace window; leave untouched

            if new_state is None or options.dry_run:
                continue
            rec.state = new_state
            for k, v in extra.items():
                setattr(rec, k, v)
            try:
                r.upsert(rec)
                result.outcomes_updated += 1
            except Exception as exc:
                logger.warning("run: upsert failed for %s", qb_hash, exc_info=True)
                result.errors.append(str(exc))
    return result


def _build_qb_client(options: ReconcileOptions):
    """Build a real read-only qB client from config. Imported lazily so unit
    tests never need live qB config."""
    from javdb.integrations.qb.client import QBittorrentClient
    from javdb.integrations.qb.config import qb_base_url_candidates
    from javdb.infra.config import cfg
    return QBittorrentClient(
        qb_base_url_candidates(),
        cfg("QB_USERNAME", ""),
        cfg("QB_PASSWORD", ""),
        False,
    )
```

> **Note on `marked_completed` double-count:** `apply_cleanup_completed` and `run`
> each own distinct capture paths (cleanup-push vs. live-observation); both
> increment `marked_completed` on their own `ReconcileResult`, never the same one.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_reconcile_service.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Re-export from package `__init__`**

Append to `javdb/ops/reconcile/__init__.py`:

```python
from javdb.ops.reconcile.models import ReconcileOptions, ReconcileResult  # noqa: E402,F401
from javdb.ops.reconcile.service import run, record_queued, apply_cleanup_completed  # noqa: E402,F401
```

- [ ] **Step 6: Commit**

```bash
git add javdb/ops/reconcile/service.py javdb/ops/reconcile/__init__.py tests/unit/test_reconcile_service.py
git commit -m "feat(reconcile): add Options->Result reconcile service (ADR-033 Phase 1)"
```

---

## Task 7: CLI adapter `apps/cli/ops/reconcile.py`

**Files:**
- Create: `apps/cli/ops/reconcile.py`
- Test: `tests/smoke/test_reconcile_cli.py`

- [ ] **Step 1: Write the failing smoke test**

```python
# tests/smoke/test_reconcile_cli.py
import subprocess
import sys


def test_reconcile_cli_help():
    r = subprocess.run(
        [sys.executable, "-m", "apps.cli.ops.reconcile", "--help"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "reconcile" in r.stdout.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/smoke/test_reconcile_cli.py -v`
Expected: FAIL — `No module named apps.cli.ops.reconcile`

- [ ] **Step 3: Write the CLI adapter** (mirrors `apps/cli/ops/diagnose_run.py`)

```python
# apps/cli/ops/reconcile.py
"""Reconcile acquisition outcomes against live sources (ADR-033 Phase 1).

CLI adapter only: parses args, owns exit codes. All domain logic lives in
javdb.ops.reconcile.service (Options -> Result)."""

from __future__ import annotations

import argparse
import json
import logging
import sys

from javdb.infra.config import cfg
from javdb.infra.logging import setup_logging
from javdb.ops.reconcile.models import ReconcileOptions
from javdb.ops.reconcile.service import run

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apps.cli.ops.reconcile",
        description="Reconcile acquisition outcomes (ADR-033 media closed-loop, Phase 1).",
    )
    parser.add_argument("--source", action="append", dest="sources", default=None,
                        help="Source to reconcile (repeatable). Default: qb")
    parser.add_argument("--category", action="append", dest="categories", default=None,
                        help="qB category to scan (repeatable). Default: JavDB, Ad Hoc")
    parser.add_argument("--stalled-after-days", type=int,
                        default=int(cfg("RECONCILE_STALLED_DAYS", 7)),
                        help="Active outcomes unseen for this many days become stalled.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute transitions but write nothing.")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--log-level", default="INFO",
                        choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    setup_logging(level=args.log_level)
    options = ReconcileOptions(
        sources=tuple(args.sources or ("qb",)),
        categories=tuple(args.categories or ("JavDB", "Ad Hoc")),
        stalled_after_days=args.stalled_after_days,
        dry_run=args.dry_run,
    )
    result = run(options)
    if args.json_output:
        print(json.dumps(result.__dict__, ensure_ascii=False))
    else:
        logger.info(
            "Reconcile done: observed=%d updated=%d downloading=%d completed=%d "
            "stalled=%d failed=%d errors=%d",
            result.observed, result.outcomes_updated, result.marked_downloading,
            result.marked_completed, result.marked_stalled, result.marked_failed,
            len(result.errors),
        )
    return 2 if result.errors else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/smoke/test_reconcile_cli.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Sanity-check dry-run exit code** (uses real `get_db`; on a machine with no qB it should still exit cleanly because `--dry-run` writes nothing and qB errors are caught into `result.errors`)

Run: `python3 -m apps.cli.ops.reconcile --dry-run --json --log-level WARNING; echo "exit=$?"`
Expected: a JSON line printed; `exit=0` (no errors) or `exit=2` (qB unreachable → error captured, not a crash). Either is acceptable; a traceback is **not**.

- [ ] **Step 6: Commit**

```bash
git add apps/cli/ops/reconcile.py tests/smoke/test_reconcile_cli.py
git commit -m "feat(reconcile): add apps.cli.ops.reconcile CLI adapter"
```

---

## Task 8: Uploader instrumentation — write `queued` on success

**Files:**
- Modify: `javdb/integrations/qb/uploader/service.py` (success branch, ~lines 643-660)
- Test: `tests/unit/test_uploader_acquisition_queued.py`

The success branch already computes `new_hash = extract_hash_from_magnet(...)`. Add the
queued write right after it, reusing the same `torrent` dict and `options.session_id`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_uploader_acquisition_queued.py
import sqlite3

from javdb.ops.reconcile import service
from javdb.storage.repos.acquisition_outcome_repo import AcquisitionOutcomeRepo

_DDL = """
CREATE TABLE AcquisitionOutcome (
  qb_hash TEXT PRIMARY KEY, href TEXT NOT NULL DEFAULT '', video_code TEXT,
  category TEXT, state TEXT NOT NULL DEFAULT 'queued', queued_at TEXT,
  completed_at TEXT, landed_at TEXT, last_seen_at TEXT, session_id TEXT
);
"""


def test_uploader_helper_writes_queued_row():
    """Pin the contract the uploader relies on: a successful add → one queued row."""
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    repo = AcquisitionOutcomeRepo(c)
    torrent = {"magnet": "magnet:?xt=urn:btih:" + "b" * 40, "title": "ABC-1 [sub]",
               "type": "subtitle", "href": "/v/ABC-1", "video_code": "ABC-1"}

    service.record_queued(torrent, session_id="S9", repo=repo)

    got = repo.get("b" * 40)
    assert got is not None
    assert got.state == "queued"
    assert got.queued_at is not None
    assert got.session_id == "S9"
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `pytest tests/unit/test_uploader_acquisition_queued.py -v`
Expected: PASS already (this pins `service.record_queued`, implemented in Task 6). The *modify* below wires it into the uploader; the test guards the contract the uploader depends on.

- [ ] **Step 3: Add the import** near the other qb-client imports (the file already imports `extract_hash_from_magnet` at line ~303)

```python
from javdb.ops.reconcile.service import record_queued as _record_acquisition_queued
```

- [ ] **Step 4: Wire the queued write into the success branch**

Find (around lines 654-658):

```python
                # Add newly added torrent hash to existing set to avoid re-adding in same session
                new_hash = extract_hash_from_magnet(torrent['magnet'])
                if new_hash:
                    existing_hashes.add(new_hash)
```

Replace with:

```python
                # Add newly added torrent hash to existing set to avoid re-adding in same session
                new_hash = extract_hash_from_magnet(torrent['magnet'])
                if new_hash:
                    existing_hashes.add(new_hash)
                # ADR-033 Phase 1: record the acquisition as queued (best-effort
                # enrichment; record_queued swallows its own errors).
                _record_acquisition_queued(torrent, options.session_id)
```

- [ ] **Step 5: Run the uploader unit tests to confirm no regression**

Run: `pytest tests/unit/test_uploader_acquisition_queued.py tests/unit/ -k uploader -v`
Expected: PASS (no import errors, existing uploader tests still green)

- [ ] **Step 6: Commit**

```bash
git add javdb/integrations/qb/uploader/service.py tests/unit/test_uploader_acquisition_queued.py
git commit -m "feat(qb): record acquisition outcome as queued on successful add (ADR-033)"
```

---

## Task 9: Cleanup instrumentation — push `completed`

**Files:**
- Modify: `javdb/integrations/pikpak/bridge/service.py` (the two `remove_completed_torrents_keep_files` call sites: ~515 primary, ~544 adhoc)
- Test: `tests/unit/test_cleanup_acquisition_completed.py`

`remove_completed_torrents_keep_files` already returns `{"scanned","deleted","hashes"}`.
Capture its return value and feed it to `apply_cleanup_completed` (ADR-033 D3).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cleanup_acquisition_completed.py
import sqlite3

from javdb.ops.reconcile import service
from javdb.ops.reconcile.models import AcquisitionOutcomeRecord
from javdb.storage.repos.acquisition_outcome_repo import AcquisitionOutcomeRepo

_DDL = """
CREATE TABLE AcquisitionOutcome (
  qb_hash TEXT PRIMARY KEY, href TEXT NOT NULL DEFAULT '', video_code TEXT,
  category TEXT, state TEXT NOT NULL DEFAULT 'queued', queued_at TEXT,
  completed_at TEXT, landed_at TEXT, last_seen_at TEXT, session_id TEXT
);
"""


def test_cleanup_stats_promote_queued_to_completed():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    repo = AcquisitionOutcomeRepo(c)
    repo.upsert(AcquisitionOutcomeRecord(qb_hash="h1", href="/v/1", state="downloading"))

    stats = {"scanned": 2, "deleted": 2, "hashes": ["h1", "h2"]}
    res = service.apply_cleanup_completed(stats, repo=repo)

    assert repo.get("h1").state == "completed"
    assert repo.get("h1").completed_at is not None
    assert repo.get("h2").state == "completed"
    assert res.marked_completed == 2
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/unit/test_cleanup_acquisition_completed.py -v`
Expected: PASS (pins `apply_cleanup_completed`, implemented in Task 6)

- [ ] **Step 3: Add the import** near the top of `javdb/integrations/pikpak/bridge/service.py` (next to the existing `_shared_remove_completed` import, ~line 85)

```python
from javdb.ops.reconcile.service import apply_cleanup_completed as _push_acquisition_completed
```

- [ ] **Step 4: Capture + push at the primary call site (~line 515)**

Find:

```python
    remove_completed_torrents_keep_files(qb, CATEGORIES, dry_run=dry_run, qb_label="Primary QB")
```

Replace with:

```python
    _primary_cleanup = remove_completed_torrents_keep_files(
        qb, CATEGORIES, dry_run=dry_run, qb_label="Primary QB"
    )
    if not dry_run:
        _push_acquisition_completed(_primary_cleanup)  # ADR-033 D3: completed push
```

- [ ] **Step 5: Capture + push at the adhoc call site (~line 544)**

Find:

```python
            remove_completed_torrents_keep_files(
                qb_adhoc, adhoc_categories, dry_run=dry_run, qb_label="Adhoc QB"
            )
```

Replace with:

```python
            _adhoc_cleanup = remove_completed_torrents_keep_files(
                qb_adhoc, adhoc_categories, dry_run=dry_run, qb_label="Adhoc QB"
            )
            if not dry_run:
                _push_acquisition_completed(_adhoc_cleanup)  # ADR-033 D3: completed push
```

- [ ] **Step 6: Run tests + import check**

Run: `pytest tests/unit/test_cleanup_acquisition_completed.py -v && python3 -c "import javdb.integrations.pikpak.bridge.service; print('import ok')"`
Expected: PASS + `import ok`

- [ ] **Step 7: Commit**

```bash
git add javdb/integrations/pikpak/bridge/service.py tests/unit/test_cleanup_acquisition_completed.py
git commit -m "feat(pikpak): push acquisition completed from cleanup stats (ADR-033 D3)"
```

---

## Task 10: Cron workflow, config, and docs

**Files:**
- Create: `.github/workflows/ReconcileLibrary.yml`
- Modify: `config.py.example`
- Modify: `CONTEXT.md`, `docs/handbook/en/developer/cli-reference.md` (+ `docs/handbook/zh/developer/cli-reference.md`), `docs/handbook/en/self-hoster/github-actions-setup.md` (+ zh)

- [ ] **Step 1: Write the cron workflow** (modeled on `StaleSessionCleanup.yml`)

```yaml
# .github/workflows/ReconcileLibrary.yml
name: Reconcile Library

# ADR-033 Phase 1: the asynchronous heartbeat of the media closed-loop.
# Reconciles AcquisitionOutcome rows against live qB state — promotes
# queued→downloading→completed and derives stalled/failed for torrents that
# left qB without completing. Needs a self-hosted runner for LAN qB access.

permissions:
  contents: read

on:
  schedule:
    # Hourly; qB completion/landing is minutes-to-hours after a daily run.
    - cron: '0 * * * *'
  workflow_dispatch:
    inputs:
      runner:
        description: 'Runner (self-hosted reaches LAN qB).'
        required: false
        default: 'self-hosted'
        type: choice
        options:
          - self-hosted
          - ubuntu-latest
      stalled_after_days:
        description: 'Active outcomes unseen this many days → stalled.'
        required: false
        default: '7'
        type: string
      dry_run:
        description: 'Compute transitions but write nothing.'
        required: false
        default: false
        type: boolean

concurrency:
  group: reconcile-library
  cancel-in-progress: false

jobs:
  reconcile:
    runs-on: ${{ inputs.runner || 'self-hosted' }}
    steps:
      - uses: actions/checkout@v4
      - uses: ./.github/actions/setup-python-env
      - uses: ./.github/actions/restore-encrypted-config
      - name: Run reconcile
        env:
          STORAGE_BACKEND: d1
        run: |
          ARGS=(--stalled-after-days "${{ inputs.stalled_after_days || '7' }}")
          if [ "${{ inputs.dry_run }}" = "true" ]; then ARGS+=(--dry-run); fi
          python3 -m apps.cli.ops.reconcile "${ARGS[@]}" --json
```

> Cross-check the two composite-action names (`setup-python-env`,
> `restore-encrypted-config`) against `.github/actions/` and an existing workflow
> (e.g. `StaleSessionCleanup.yml`) before committing; copy their exact `with:`
> inputs if those actions require any.

- [ ] **Step 2: Validate the workflow YAML**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ReconcileLibrary.yml')); print('yaml ok')"`
Expected: `yaml ok`

- [ ] **Step 3: Document the config knob** — add to `config.py.example` near other ops settings

```python
# ADR-033 media closed-loop: active acquisition outcomes (queued/downloading)
# that have not been seen in qB for this many days are marked 'stalled'
# (and 'failed' after 2x this window). Tune to your slowest expected download.
RECONCILE_STALLED_DAYS = 7
```

- [ ] **Step 4: Update CONTEXT.md** — add the ADR-033 domain terms (verbatim from the ADR's "Domain Language" section): *Acquisition outcome*, *Ownership ledger*, *Consumption signal*, *Reconciliation pass*, *Collector*. (Only *Acquisition outcome*, *Reconciliation pass*, and *Collector* are live in Phase 1; mark *Ownership ledger* / *Consumption signal* as Phase 2/3.)

- [ ] **Step 5: Update CLI reference** — add `python -m apps.cli.ops.reconcile` to `docs/handbook/en/developer/cli-reference.md` with its flags (`--source`, `--category`, `--stalled-after-days`, `--dry-run`, `--json`, `--log-level`); mirror into `docs/handbook/zh/developer/cli-reference.md`.

- [ ] **Step 6: Update GitHub Actions setup** — document `ReconcileLibrary.yml` (cron cadence, self-hosted runner requirement, dispatch inputs) in `docs/handbook/en/self-hoster/github-actions-setup.md`; mirror into the zh file.

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/ReconcileLibrary.yml config.py.example CONTEXT.md docs/handbook
git commit -m "feat(ci): add ReconcileLibrary cron + docs for ADR-033 Phase 1"
```

---

## Task 11: Full-suite verification gate

- [ ] **Step 1: Run the new tests together**

Run:
```bash
pytest tests/unit/test_acquisition_outcome_models.py \
       tests/unit/test_acquisition_outcome_repo.py \
       tests/unit/test_reconcile_qb_collector.py \
       tests/unit/test_reconcile_service.py \
       tests/unit/test_uploader_acquisition_queued.py \
       tests/unit/test_cleanup_acquisition_completed.py \
       tests/smoke/test_reconcile_cli.py -v
```
Expected: all PASS.

- [ ] **Step 2: Run the broader suite for regressions in touched areas**

Run: `pytest tests/unit -k "uploader or pikpak or reconcile or acquisition" -q`
Expected: all PASS (no import-time breakage from the two modified production files).

- [ ] **Step 3: Confirm the seam invariant**

Manually confirm (grep): `collectors.py` performs **no** DB writes, and the only
`upsert`/`mark_state` call sites are inside `service.py` / the repo.

Run: `grep -rnE "upsert|mark_state|execute\(" javdb/ops/reconcile/collectors.py`
Expected: no output (collector is read-only).

- [ ] **Step 4: Final commit (if any doc/test tidy-ups remain)**

```bash
git add -A && git commit -m "test(reconcile): full-suite verification for ADR-033 Phase 1"
```

---

## Plan Self-Review

**Spec coverage (ADR-033 Phase 1 row + D-decisions):**
- `AcquisitionOutcome` table (D1) → Task 1. ✓
- Reconcile `Options→Result` service + collector seam, service is sole writer (D4) → Tasks 4-6, gate Task 11 Step 3. ✓
- `QbCollector` (Phase 1's only collector) → Task 5. ✓
- Queue-time `qb_hash` write reusing `extract_hash_from_magnet` (D2) → Task 8. ✓
- `completed` push at the cleanup step (D3) → Task 9. ✓
- CLI adapter → Task 7. ✓
- `ReconcileLibrary.yml` cron, self-hosted (D5 default path) → Task 10. ✓
- Idempotent UPSERT, off the Pending→Commit path, D1-canonical (D10) → Tasks 1, 3, 6. ✓
- `in_library` Phase-2-gated → documented in File Structure + models `TERMINAL_STATES`. ✓
- Deferred items (Ledger/Consumption/other collectors/Docker trigger/preference model) → not planned. ✓
- Stalled/failed threshold concrete (7 days) + configurable (`RECONCILE_STALLED_DAYS`, `--stalled-after-days`) → Tasks 6, 7, 10. ✓
- Doc updates (CONTEXT.md, cli-reference, github-actions-setup) → Task 10. ✓

**Type consistency:** `AcquisitionOutcomeRecord`, `AcquisitionOutcomeRepo`
(`upsert`/`mark_state`/`get`/`list_active`), `Observation`, `ReconcileOptions`,
`ReconcileResult`, and service `run`/`record_queued`/`apply_cleanup_completed`
are used identically across Tasks 2-11. ✓

**Known couplings (documented, intentional):** completed-capture is coupled to the
pikpak-bridge cleanup call sites (ADR-033 D3 / Negative consequence). If a second
cleanup path is added later (e.g. `QBFileFilter` standalone), it must also call
`apply_cleanup_completed` — note this when touching cleanup.

**Open verification dependency:** Task 1 Steps 2-3 require live `wrangler` D1
access and the `apps.cli.db.sync_d1_to_sqlite` tool; run them in an environment
with D1 credentials (the same place other migrations are applied).
