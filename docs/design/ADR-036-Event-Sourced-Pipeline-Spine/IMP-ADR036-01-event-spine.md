# IMP-ADR036-01: Event Spine + Demonstrator Consumer (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-036](ADR-036-event-sourced-pipeline-spine.md) (umbrella) — this is **Phase 1** of three.

**Status:** Implemented and verified (2026-05-30). All 9 tasks landed; the `2026_05_29_add_pipeline_event.sql` migration is applied to remote `javdb-reports` D1 and mirrored locally; GitHub full unit tests passed with no failures. Two plan-vs-code corrections were made during implementation and documented inline (Task 4: `get_db(REPORTS_DB_PATH)` not the literal `"reports"`; Task 7: emit `SessionCommitted` after the status transition succeeds, `SessionFailed` in both failure paths).

**Goal:** Stand up an additive, append-only `PipelineEvent` log in D1 with a cursor-based consumer framework and a demonstrator projection, proving emit → consume → replay end to end — without touching the authoritative `pending→commit` path.

**Architecture:** `javdb/pipeline/events/` provides `emit()` (append a `PipelineEvent`), `read_since(cursor)`, and a base `Consumer` that reads `seq > last_seq`, projects idempotently, and advances its cursor. Phase 1 wires only the three cheap, non-colliding **session-lifecycle** events (`RunStarted`, `SessionCommitted`, `SessionFailed`); per-entity events are a Phase-2 concern (wired alongside re-pointing ADR-033/035, with batching). The demonstrator `RunEventSummaryConsumer` projects per-session event-type counts; resetting its cursor replays the log.

**Tech Stack:** Python 3, `sqlite3`/D1 via `javdb.storage.db.get_db`, `dataclasses`, `pytest`, Cloudflare D1 + `wrangler`.

**Storage placement:** `PipelineEvent`, `EventConsumerCursor`, `RunEventSummary` live in the **reports** logical DB (`javdb-reports`), alongside `ReportSessions` / `SpiderStats`.

**Scope note (vs. ADR-036 D3/D4):** the event *taxonomy* (all 8 types) ships in `models.py`, but Phase 1 **wires only** `RunStarted` / `SessionCommitted` / `SessionFailed`. Per-entity events (`Movie*`, `Torrent*`) are deliberately deferred to Phase 2 to (a) avoid hot-loop per-movie D1 writes without batching and (b) avoid double-hooking the uploader/cleanup points that ADR-033 already claims — Phase 2 re-points those to *consume* events. `emit` is best-effort (must never break the pipeline); the commit-class events are emitted immediately on the commit outcome (consistent in practice; a true in-transaction outbox is a later hardening item).

---

## File Structure

| Path | Create/Modify | Responsibility |
| --- | --- | --- |
| `javdb/migrations/d1/2026_05_29_add_pipeline_event.sql` | Create | `PipelineEvent` + `EventConsumerCursor` + `RunEventSummary` DDL |
| `javdb/pipeline/events/__init__.py` | Create | Package marker + re-exports |
| `javdb/pipeline/events/models.py` | Create | `EVENT_TYPES`, `PipelineEventRecord`, `utc_now_iso` |
| `javdb/storage/repos/pipeline_event_repo.py` | Create | `PipelineEventRepo` (append/read_since/cursor) + `RunEventSummaryRepo` |
| `javdb/pipeline/events/store.py` | Create | `emit()` (best-effort) + `read_since()` + cursor helpers |
| `javdb/pipeline/events/consumer.py` | Create | base `Consumer` + `RunEventSummaryConsumer` |
| `apps/cli/ops/events.py` | Create | CLI: run the demonstrator consumer; `--replay` rebuilds |
| `javdb/spider/app/run_service.py:587` | Modify | Emit `RunStarted` after session activation |
| `apps/cli/db/commit_session.py` | Modify | Emit `SessionCommitted` (drain ok) / `SessionFailed` (except) |
| `CONTEXT.md`, `docs/handbook/en/developer/cli-reference.md` (+ zh) | Modify | Domain terms + new CLI |
| `tests/unit/test_pipeline_event_*.py` … | Create | Unit + replay tests |

**Naming contract (verbatim across tasks):**
`EVENT_TYPES` (tuple); `PipelineEventRecord(event_type, session_id, entity_type, entity_id=None, payload=None, run_id=None, run_attempt=None, seq=None, created_at=None)`; `PipelineEventRepo` with `append(record) -> int`, `read_since(last_seq, *, limit) -> list[PipelineEventRecord]`, `get_cursor(consumer) -> int`, `advance_cursor(consumer, last_seq)`; `RunEventSummaryRepo` with `bump(session_id, event_type, n=1)`, `reset()`, `get(session_id) -> dict`; store fns `emit(event_type, *, session_id, entity_type, entity_id=None, payload=None, run_id=None, run_attempt=None, repo=None) -> int | None`, `read_since(last_seq, *, limit=500, repo=None)`; `Consumer` with `name`, `handle(event)`, `run_once(*, event_repo, batch=500) -> int`; `RunEventSummaryConsumer(summary_repo)`.

> **Phase-2-gated:** per-entity emit + batching, and re-pointing ADR-033/035 to consume events, are NOT in this plan.

---

## Task 1: D1 migration — event spine tables

**Files:**
- Create: `javdb/migrations/d1/2026_05_29_add_pipeline_event.sql`
- Modify: `javdb/storage/db/_db_migrations.py` (add the same tables to `_REPORTS_DDL`)

> **Amended 2026-05-30 (review feedback).** The D1 migration file alone is NOT
> enough. `init_db()` builds local `reports.db` from `_REPORTS_DDL` (used by
> fresh installs AND the test suite's autouse `_isolate_sqlite` fixture); it does
> not run the `d1/*.sql` files. The same three tables must be added to
> `_REPORTS_DDL` (next to `OpsIncidents`), or a fresh local install hits
> `no such table` and the best-effort emits silently drop every event. A repo
> test (`test_rollback_full_fidelity.py::...test_every_d1_migration_column_exists_in_local_ddl`)
> enforces this — so keep `--` comments OUT of the migration's `CREATE TABLE`
> bodies (its column parser splits on commas and would read a comment as a
> column). No `SCHEMA_VERSION` bump is needed (additive tables after the v14
> bump, same as `OpsIncidents`).

- [ ] **Step 1: Write the migration SQL**

```sql
-- 2026-05-29: Add event-spine tables (ADR-036 Phase 1).
--
-- Apply with:
--   wrangler d1 execute javdb-reports --remote \
--     --file=javdb/migrations/d1/2026_05_29_add_pipeline_event.sql
--
-- Additive, append-only. Does NOT change the authoritative pending->commit path.

CREATE TABLE IF NOT EXISTS PipelineEvent (
  seq          INTEGER PRIMARY KEY AUTOINCREMENT,  -- global monotonic order
  session_id   TEXT NOT NULL,
  run_id       TEXT,
  run_attempt  INTEGER,
  event_type   TEXT NOT NULL,
  entity_type  TEXT NOT NULL,   -- session | movie | torrent
  entity_id    TEXT,
  payload      TEXT,            -- JSON
  created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pipeline_event_session ON PipelineEvent(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_pipeline_event_type ON PipelineEvent(event_type, seq);

CREATE TABLE IF NOT EXISTS EventConsumerCursor (
  consumer   TEXT PRIMARY KEY,
  last_seq   INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT
);

-- Demonstrator projection: per (session_id, event_type) counts.
CREATE TABLE IF NOT EXISTS RunEventSummary (
  session_id  TEXT NOT NULL,
  event_type  TEXT NOT NULL,
  count       INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (session_id, event_type)
);
```

- [ ] **Step 2: Apply to D1, then re-align SQLite**

Run:
```bash
wrangler d1 execute javdb-reports --remote \
  --file=javdb/migrations/d1/2026_05_29_add_pipeline_event.sql
python3 -m apps.cli.db.sync_d1_to_sqlite --apply --force-overwrite-all
```
Expected: statements execute; tables rebuilt locally; exit 0.

- [ ] **Step 3: Verify**

Run:
```bash
python3 -c "import sqlite3,glob; p=glob.glob('reports/reports.db')[0]; c=sqlite3.connect(p); print([r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name IN ('PipelineEvent','EventConsumerCursor','RunEventSummary')\").fetchall()])"
```
Expected: `['EventConsumerCursor', 'PipelineEvent', 'RunEventSummary']` (any order)

- [ ] **Step 4: Commit**

```bash
git add javdb/migrations/d1/2026_05_29_add_pipeline_event.sql
git commit -m "feat(db): add event-spine tables (ADR-036 Phase 1)"
```

---

## Task 2: Event models

**Files:**
- Create: `javdb/pipeline/events/__init__.py`
- Create: `javdb/pipeline/events/models.py`
- Test: `tests/unit/test_pipeline_event_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_pipeline_event_models.py
from javdb.pipeline.events.models import EVENT_TYPES, PipelineEventRecord, utc_now_iso


def test_event_taxonomy_complete():
    assert {"RunStarted", "SessionCommitted", "SessionFailed",
            "MovieDiscovered", "MovieSelected",
            "TorrentSelected", "TorrentQueued", "TorrentCompleted"} == set(EVENT_TYPES)


def test_record_minimal():
    r = PipelineEventRecord(event_type="RunStarted", session_id="S1", entity_type="session")
    assert r.entity_id is None
    assert r.seq is None


def test_utc_now_iso_trailing_z():
    assert utc_now_iso().endswith("Z")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_pipeline_event_models.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create package + models**

```python
# javdb/pipeline/events/__init__.py
"""Additive event spine (ADR-036 Phase 1)."""
```

```python
# javdb/pipeline/events/models.py
"""Event-spine contracts (ADR-036). Taxonomy is full; Phase 1 wires a subset."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

EVENT_TYPES: tuple[str, ...] = (
    "RunStarted", "SessionCommitted", "SessionFailed",   # session (wired in Phase 1)
    "MovieDiscovered", "MovieSelected",                   # movie  (Phase 2)
    "TorrentSelected", "TorrentQueued", "TorrentCompleted",  # torrent (Phase 2)
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class PipelineEventRecord:
    event_type: str
    session_id: str
    entity_type: str            # session | movie | torrent
    entity_id: Optional[str] = None
    payload: Optional[str] = None   # JSON string
    run_id: Optional[str] = None
    run_attempt: Optional[int] = None
    seq: Optional[int] = None       # assigned by the DB on append
    created_at: Optional[str] = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_pipeline_event_models.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/pipeline/events/__init__.py javdb/pipeline/events/models.py tests/unit/test_pipeline_event_models.py
git commit -m "feat(events): add pipeline event models (ADR-036 Phase 1)"
```

---

## Task 3: `PipelineEventRepo` + `RunEventSummaryRepo`

**Files:**
- Create: `javdb/storage/repos/pipeline_event_repo.py`
- Test: `tests/unit/test_pipeline_event_repo.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_pipeline_event_repo.py
import sqlite3

import pytest

from javdb.pipeline.events.models import PipelineEventRecord
from javdb.storage.repos.pipeline_event_repo import PipelineEventRepo, RunEventSummaryRepo

_DDL = """
CREATE TABLE PipelineEvent (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, run_id TEXT,
  run_attempt INTEGER, event_type TEXT NOT NULL, entity_type TEXT NOT NULL,
  entity_id TEXT, payload TEXT, created_at TEXT NOT NULL
);
CREATE TABLE EventConsumerCursor (consumer TEXT PRIMARY KEY, last_seq INTEGER NOT NULL DEFAULT 0, updated_at TEXT);
CREATE TABLE RunEventSummary (session_id TEXT NOT NULL, event_type TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (session_id, event_type));
"""


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return c


def _rec(t, sid="S1"):
    return PipelineEventRecord(event_type=t, session_id=sid, entity_type="session",
                               entity_id=sid, created_at="t")


def test_append_returns_monotonic_seq(conn):
    repo = PipelineEventRepo(conn)
    s1 = repo.append(_rec("RunStarted"))
    s2 = repo.append(_rec("SessionCommitted"))
    assert s2 > s1


def test_read_since_returns_ordered_tail(conn):
    repo = PipelineEventRepo(conn)
    repo.append(_rec("RunStarted"))
    cut = repo.append(_rec("SessionCommitted"))
    repo.append(_rec("SessionFailed"))
    rows = repo.read_since(cut, limit=10)
    assert [r.event_type for r in rows] == ["SessionFailed"]


def test_cursor_get_default_zero_and_advance(conn):
    repo = PipelineEventRepo(conn)
    assert repo.get_cursor("c1") == 0
    repo.advance_cursor("c1", 7)
    assert repo.get_cursor("c1") == 7


def test_summary_bump_reset_get(conn):
    s = RunEventSummaryRepo(conn)
    s.bump("S1", "RunStarted")
    s.bump("S1", "RunStarted")
    assert s.get("S1")["RunStarted"] == 2
    s.reset()
    assert s.get("S1") == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_pipeline_event_repo.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the repos**

```python
# javdb/storage/repos/pipeline_event_repo.py
"""Repositories for the ADR-036 event spine (reports DB)."""

from __future__ import annotations

import logging
import sqlite3

from javdb.pipeline.events.models import PipelineEventRecord, utc_now_iso

logger = logging.getLogger(__name__)

_EVENT_COLS = ("session_id", "run_id", "run_attempt", "event_type",
               "entity_type", "entity_id", "payload", "created_at")


class PipelineEventRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        try:
            self._conn.row_factory = sqlite3.Row
        except Exception:
            logger.debug("row_factory set failed", exc_info=True)

    def append(self, record: PipelineEventRecord) -> int:
        created = record.created_at or utc_now_iso()
        cur = self._conn.execute(
            f"INSERT INTO PipelineEvent ({', '.join(_EVENT_COLS)}) "
            f"VALUES ({', '.join(['?'] * len(_EVENT_COLS))})",
            [record.session_id, record.run_id, record.run_attempt, record.event_type,
             record.entity_type, record.entity_id, record.payload, created],
        )
        return int(cur.lastrowid)

    def read_since(self, last_seq: int, *, limit: int) -> list[PipelineEventRecord]:
        rows = self._conn.execute(
            "SELECT seq, session_id, run_id, run_attempt, event_type, entity_type, "
            "entity_id, payload, created_at FROM PipelineEvent "
            "WHERE seq > ? ORDER BY seq ASC LIMIT ?",
            [last_seq, limit],
        ).fetchall()
        return [
            PipelineEventRecord(
                event_type=r["event_type"], session_id=r["session_id"],
                entity_type=r["entity_type"], entity_id=r["entity_id"],
                payload=r["payload"], run_id=r["run_id"], run_attempt=r["run_attempt"],
                seq=r["seq"], created_at=r["created_at"],
            ) for r in rows
        ]

    def get_cursor(self, consumer: str) -> int:
        row = self._conn.execute(
            "SELECT last_seq FROM EventConsumerCursor WHERE consumer = ?", [consumer],
        ).fetchone()
        return 0 if row is None else int(row["last_seq"])

    def advance_cursor(self, consumer: str, last_seq: int) -> None:
        self._conn.execute(
            "INSERT INTO EventConsumerCursor (consumer, last_seq, updated_at) "
            "VALUES (?, ?, ?) ON CONFLICT(consumer) DO UPDATE SET "
            "last_seq=excluded.last_seq, updated_at=excluded.updated_at",
            [consumer, last_seq, utc_now_iso()],
        )


class RunEventSummaryRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        try:
            self._conn.row_factory = sqlite3.Row
        except Exception:
            logger.debug("row_factory set failed", exc_info=True)

    def bump(self, session_id: str, event_type: str, n: int = 1) -> None:
        self._conn.execute(
            "INSERT INTO RunEventSummary (session_id, event_type, count) VALUES (?, ?, ?) "
            "ON CONFLICT(session_id, event_type) DO UPDATE SET count = count + excluded.count",
            [session_id, event_type, n],
        )

    def reset(self) -> None:
        self._conn.execute("DELETE FROM RunEventSummary")

    def get(self, session_id: str) -> dict:
        rows = self._conn.execute(
            "SELECT event_type, count FROM RunEventSummary WHERE session_id = ?",
            [session_id],
        ).fetchall()
        return {r["event_type"]: r["count"] for r in rows}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_pipeline_event_repo.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/storage/repos/pipeline_event_repo.py tests/unit/test_pipeline_event_repo.py
git commit -m "feat(db): add PipelineEventRepo + RunEventSummaryRepo (ADR-036)"
```

---

## Task 4: `store.emit` + `read_since` (best-effort)

**Files:**
- Create: `javdb/pipeline/events/store.py`
- Test: `tests/unit/test_pipeline_event_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_pipeline_event_store.py
import sqlite3

from javdb.pipeline.events import store
from javdb.storage.repos.pipeline_event_repo import PipelineEventRepo

_DDL = """
CREATE TABLE PipelineEvent (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, run_id TEXT,
  run_attempt INTEGER, event_type TEXT NOT NULL, entity_type TEXT NOT NULL,
  entity_id TEXT, payload TEXT, created_at TEXT NOT NULL
);
CREATE TABLE EventConsumerCursor (consumer TEXT PRIMARY KEY, last_seq INTEGER NOT NULL DEFAULT 0, updated_at TEXT);
"""


def _repo():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return PipelineEventRepo(c)


def test_emit_appends_and_read_since_returns_it():
    repo = _repo()
    seq = store.emit("RunStarted", session_id="S1", entity_type="session", entity_id="S1", repo=repo)
    assert seq == 1
    rows = store.read_since(0, repo=repo)
    assert rows[0].event_type == "RunStarted"


def test_emit_is_best_effort_on_unknown_type():
    repo = _repo()
    # unknown type is still appended (validation is advisory, not a hard gate) but
    # a None/blank session must NOT raise — emit returns None on bad input.
    assert store.emit("RunStarted", session_id="", entity_type="session", repo=repo) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_pipeline_event_store.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the store**

```python
# javdb/pipeline/events/store.py
"""Best-effort emit + cursor-read for the event spine (ADR-036).

emit() NEVER raises — an event-log failure must not break the pipeline (D4)."""

from __future__ import annotations

import contextlib
import logging

from javdb.pipeline.events.models import PipelineEventRecord, utc_now_iso
from javdb.storage import db as _db
from javdb.storage.db import get_db
from javdb.storage.repos.pipeline_event_repo import PipelineEventRepo

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def _repo_ctx(repo):
    if repo is not None:
        yield repo
    else:
        # NOTE (amended 2026-05-30): two corrections vs the original plan.
        # (1) get_db takes a db *path*, not the logical name "reports" (which
        #     raises under every backend), so resolve REPORTS_DB_PATH.
        # (2) Reference it via the module attribute (_db.REPORTS_DB_PATH) at
        #     CALL TIME, not a top-level `from ... import REPORTS_DB_PATH`. A
        #     top-level import binds the value at import and bypasses the test
        #     suite's path monkeypatch, so emits would write to the real
        #     reports.db during tests. Mirrors javdb/storage/rollback/core.py.
        #     Both bugs were silent because emit() is best-effort.
        with get_db(_db.REPORTS_DB_PATH) as conn:
            yield PipelineEventRepo(conn)


def emit(event_type: str, *, session_id: str, entity_type: str,
         entity_id: str | None = None, payload: str | None = None,
         run_id: str | None = None, run_attempt: int | None = None,
         repo=None) -> int | None:
    if not session_id:
        logger.debug("event emit skipped: missing session_id (type=%s)", event_type)
        return None
    record = PipelineEventRecord(
        event_type=event_type, session_id=session_id, entity_type=entity_type,
        entity_id=entity_id, payload=payload, run_id=run_id, run_attempt=run_attempt,
        created_at=utc_now_iso(),
    )
    try:
        with _repo_ctx(repo) as r:
            return r.append(record)
    except Exception:
        logger.warning("event emit failed (type=%s session=%s)", event_type, session_id, exc_info=True)
        return None


def read_since(last_seq: int, *, limit: int = 500, repo=None) -> list[PipelineEventRecord]:
    with _repo_ctx(repo) as r:
        return r.read_since(last_seq, limit=limit)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_pipeline_event_store.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add javdb/pipeline/events/store.py tests/unit/test_pipeline_event_store.py
git commit -m "feat(events): add best-effort emit + cursor read (ADR-036)"
```

---

## Task 5: base `Consumer` + `RunEventSummaryConsumer` + replay

**Files:**
- Create: `javdb/pipeline/events/consumer.py`
- Test: `tests/unit/test_pipeline_event_consumer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_pipeline_event_consumer.py
import sqlite3

from javdb.pipeline.events import store
from javdb.pipeline.events.consumer import RunEventSummaryConsumer
from javdb.storage.repos.pipeline_event_repo import PipelineEventRepo, RunEventSummaryRepo

_DDL = """
CREATE TABLE PipelineEvent (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, run_id TEXT,
  run_attempt INTEGER, event_type TEXT NOT NULL, entity_type TEXT NOT NULL,
  entity_id TEXT, payload TEXT, created_at TEXT NOT NULL
);
CREATE TABLE EventConsumerCursor (consumer TEXT PRIMARY KEY, last_seq INTEGER NOT NULL DEFAULT 0, updated_at TEXT);
CREATE TABLE RunEventSummary (session_id TEXT NOT NULL, event_type TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (session_id, event_type));
"""


def _wire():
    c = sqlite3.connect(":memory:")
    c.executescript(_DDL)
    return PipelineEventRepo(c), RunEventSummaryRepo(c)


def test_consumer_projects_then_advances():
    ev, sm = _wire()
    store.emit("RunStarted", session_id="S1", entity_type="session", repo=ev)
    store.emit("SessionCommitted", session_id="S1", entity_type="session", repo=ev)
    n = RunEventSummaryConsumer(sm).run_once(event_repo=ev)
    assert n == 2
    assert sm.get("S1") == {"RunStarted": 1, "SessionCommitted": 1}
    # cursor advanced -> a second run sees nothing
    assert RunEventSummaryConsumer(sm).run_once(event_repo=ev) == 0


def test_consumer_is_idempotent_no_double_count():
    ev, sm = _wire()
    store.emit("RunStarted", session_id="S1", entity_type="session", repo=ev)
    c = RunEventSummaryConsumer(sm)
    c.run_once(event_repo=ev)
    c.run_once(event_repo=ev)  # nothing new
    assert sm.get("S1")["RunStarted"] == 1


def test_replay_rebuilds_projection():
    ev, sm = _wire()
    store.emit("RunStarted", session_id="S1", entity_type="session", repo=ev)
    store.emit("SessionFailed", session_id="S1", entity_type="session", repo=ev)
    c = RunEventSummaryConsumer(sm)
    c.run_once(event_repo=ev)
    # replay: reset cursor + projection, re-run -> identical result
    ev.advance_cursor(c.name, 0)
    sm.reset()
    c.run_once(event_repo=ev)
    assert sm.get("S1") == {"RunStarted": 1, "SessionFailed": 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_pipeline_event_consumer.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the consumer**

```python
# javdb/pipeline/events/consumer.py
"""Cursor-based event consumers (ADR-036). Replay = reset cursor, re-run."""

from __future__ import annotations

from javdb.pipeline.events.models import PipelineEventRecord
from javdb.storage.repos.pipeline_event_repo import RunEventSummaryRepo


class Consumer:
    name = "base"

    def handle(self, event: PipelineEventRecord) -> None:
        raise NotImplementedError

    def run_once(self, *, event_repo, batch: int = 500) -> int:
        last = event_repo.get_cursor(self.name)
        events = event_repo.read_since(last, limit=batch)
        for event in events:
            self.handle(event)
        if events:
            event_repo.advance_cursor(self.name, events[-1].seq)
        return len(events)


class RunEventSummaryConsumer(Consumer):
    name = "run_event_summary"

    def __init__(self, summary_repo: RunEventSummaryRepo) -> None:
        self._summary = summary_repo

    def handle(self, event: PipelineEventRecord) -> None:
        self._summary.bump(event.session_id, event.event_type)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_pipeline_event_consumer.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Re-export + commit**

Append to `javdb/pipeline/events/__init__.py`:

```python
from javdb.pipeline.events.store import emit, read_since  # noqa: E402,F401
from javdb.pipeline.events.consumer import Consumer, RunEventSummaryConsumer  # noqa: E402,F401
```

```bash
git add javdb/pipeline/events/consumer.py javdb/pipeline/events/__init__.py tests/unit/test_pipeline_event_consumer.py
git commit -m "feat(events): add cursor consumer + demonstrator + replay (ADR-036)"
```

---

## Task 6: Emit `RunStarted` at session activation

**Files:**
- Modify: `javdb/spider/app/run_service.py` (~line 587, after `_set_active_session_id(_session_id)`)
- Test: covered by Task 4's store test + the import-smoke below (the wiring is a single best-effort call).

- [ ] **Step 1: Add the import** near the top of `javdb/spider/app/run_service.py`

```python
from javdb.pipeline.events import emit as _emit_event
```

- [ ] **Step 2: Emit after session activation** — find (~line 587):

```python
                _set_active_session_id(_session_id)
```

Insert immediately after it:

```python
                _emit_event("RunStarted", session_id=str(_session_id),
                            entity_type="session", entity_id=str(_session_id))  # ADR-036
```

> `emit` is best-effort (swallows its own errors), so this cannot break run start.

- [ ] **Step 3: Import-smoke**

Run: `python3 -c "import javdb.spider.app.run_service; print('import ok')"`
Expected: `import ok`

- [ ] **Step 4: Commit**

```bash
git add javdb/spider/app/run_service.py
git commit -m "feat(spider): emit RunStarted on session activation (ADR-036)"
```

---

## Task 7: Emit `SessionCommitted` / `SessionFailed` at commit

**Files:**
- Modify: `apps/cli/db/commit_session.py` (the true-commit success branch + BOTH `except` blocks, ~lines 408-455)
- Test: `tests/unit/test_commit_session_events.py` (pins the corrected placement) + import-smoke below.

> **Amended 2026-05-30 (design feedback loop).** The original plan emitted
> `SessionCommitted` right after the drain-success `logger.info`. Reading the
> code showed that log sits **before** `transition(sid, "committed")` — the
> drain materialises pending→history rows, but the session is only truly
> committed once the subsequent `transition` succeeds. Emitting at the drain log
> would announce `SessionCommitted` for a session whose transition then fails
> (it lands in `failed_commits`), violating ADR-036 **D4** ("the log never
> disagrees with reality about what committed"). Corrected: emit
> `SessionCommitted` inside the post-transition success branch
> (`if n > 0 or drained_pending_session: committed.append(sid)`), and emit
> `SessionFailed` in **both** failure `except` blocks (drain failure and
> transition failure), not just one.

- [ ] **Step 1: Add the import** near the top of `apps/cli/db/commit_session.py`

```python
from javdb.pipeline.events import emit as _emit_event
```

- [ ] **Step 2: Emit `SessionCommitted` after the transition succeeds** — find the
true-commit success branch (after `n = transition(sid, "committed")`):

```python
        if n > 0 or drained_pending_session:
            committed.append(sid)
        else:
            # Already committed — that's fine, idempotent.
            skipped.append(sid)
```

Insert immediately after `committed.append(sid)` (inside the `if` branch only —
the `else` is the idempotent already-committed case and must NOT re-emit):

```python
            committed.append(sid)
            _emit_event("SessionCommitted", session_id=str(sid),
                        entity_type="session", entity_id=str(sid))  # ADR-036
```

- [ ] **Step 3: Emit `SessionFailed` in BOTH `except` blocks** — there are two
failure paths, each appending to `failed_commits`. Insert after each.

Drain failure (`except Exception as e:` around the `HistoryRepo().commit_session`):

```python
                failed_commits.append(sid)
                _emit_event("SessionFailed", session_id=str(sid),
                            entity_type="session", entity_id=str(sid))  # ADR-036
```

Transition failure (`except Exception as e:` around `transition(sid, "committed")`):

```python
            failed_commits.append(sid)
            _emit_event("SessionFailed", session_id=str(sid),
                        entity_type="session", entity_id=str(sid))  # ADR-036
```

> Both ride the commit outcome in the same process; emit is best-effort and cannot
> change commit behaviour. A true in-transaction outbox is an ADR-036 hardening item.

- [ ] **Step 4: Import-smoke + placement regression test**

Run:
```bash
python3 -c "import apps.cli.db.commit_session; print('import ok')"
pytest tests/unit/test_commit_session_events.py -q
```
Expected: `import ok` + 3 passed (SessionCommitted only on true commit;
SessionFailed on transition failure and on drain failure).

- [ ] **Step 5: Commit**

```bash
git add apps/cli/db/commit_session.py tests/unit/test_commit_session_events.py
git commit -m "feat(db): emit SessionCommitted/SessionFailed on commit outcome (ADR-036)"
```

---

## Task 8: CLI — run the demonstrator consumer (+ replay)

**Files:**
- Create: `apps/cli/ops/events.py`
- Test: `tests/smoke/test_events_cli.py`

- [ ] **Step 1: Write the failing smoke test**

```python
# tests/smoke/test_events_cli.py
import subprocess
import sys


def test_events_cli_help():
    r = subprocess.run(
        [sys.executable, "-m", "apps.cli.ops.events", "--help"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "replay" in r.stdout.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/smoke/test_events_cli.py -v`
Expected: FAIL — `No module named apps.cli.ops.events`

- [ ] **Step 3: Write the CLI**

```python
# apps/cli/ops/events.py
"""Run the event-spine demonstrator consumer (ADR-036 Phase 1).

Reads new PipelineEvent rows by cursor and projects per-session counts into
RunEventSummary. --replay resets the cursor + projection and rebuilds from seq 0."""

from __future__ import annotations

import argparse
import logging
import sys

from javdb.infra.logging import setup_logging
from javdb.pipeline.events.consumer import RunEventSummaryConsumer
from javdb.storage import db as _db
from javdb.storage.db import get_db
from javdb.storage.repos.pipeline_event_repo import PipelineEventRepo, RunEventSummaryRepo

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="apps.cli.ops.events",
        description="Project pipeline events into RunEventSummary (ADR-036).",
    )
    p.add_argument("--replay", action="store_true",
                   help="Reset the consumer cursor + projection, then rebuild from seq 0.")
    p.add_argument("--batch", type=int, default=500)
    p.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    setup_logging(level=args.log_level)
    with get_db(_db.REPORTS_DB_PATH) as conn:
        event_repo = PipelineEventRepo(conn)
        consumer = RunEventSummaryConsumer(RunEventSummaryRepo(conn))
        if args.replay:
            event_repo.advance_cursor(consumer.name, 0)
            RunEventSummaryRepo(conn).reset()
            logger.info("Replay: cursor + projection reset")
        total = 0
        while True:
            n = consumer.run_once(event_repo=event_repo, batch=args.batch)
            total += n
            if n < args.batch:
                break
    logger.info("Projected %d event(s)", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/smoke/test_events_cli.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add apps/cli/ops/events.py tests/smoke/test_events_cli.py
git commit -m "feat(events): add apps.cli.ops.events consumer/replay CLI (ADR-036)"
```

---

## Task 9: Docs + full verification gate

**Files:**
- Modify: `CONTEXT.md`, `docs/handbook/en/developer/cli-reference.md` (+ zh)

- [ ] **Step 1: Update CONTEXT.md** — add the ADR-036 terms verbatim from the ADR's "Domain Language": *Pipeline event*, *Event spine*, *Consumer cursor*, *Projection*, *Strangler migration*.

- [ ] **Step 2: Update CLI reference** — add `python -m apps.cli.ops.events` (flags `--replay`, `--batch`, `--log-level`) to `docs/handbook/en/developer/cli-reference.md`; mirror into `docs/handbook/zh/developer/cli-reference.md`.

- [ ] **Step 3: Full verification gate**

Run:
```bash
pytest tests/unit/test_pipeline_event_models.py tests/unit/test_pipeline_event_repo.py \
       tests/unit/test_pipeline_event_store.py tests/unit/test_pipeline_event_consumer.py \
       tests/smoke/test_events_cli.py -v
```
Expected: all PASS.

- [ ] **Step 4: Additive-invariant + regression check**

Run:
```bash
python3 -c "import javdb.spider.app.run_service, apps.cli.db.commit_session; print('emit wiring imports ok')"
pytest tests/unit -k "commit_session or run_service or pipeline_event" -q
```
Expected: `emit wiring imports ok` + tests PASS (no behaviour change to commit/run paths; emit is best-effort additive).

- [ ] **Step 5: Commit**

```bash
git add CONTEXT.md docs/handbook
git commit -m "docs: document ADR-036 event spine terms + events CLI"
```

---

## Plan Self-Review

**Spec coverage (ADR-036 Phase 1 row + D-decisions):**
- Additive, non-destructive — only new tables + best-effort emit; `pending→commit` untouched (D1) → Tasks 1, 6, 7. ✓
- D1 append-only table + cursor consumers (D2) → Tasks 1, 3, 5. ✓
- Full taxonomy in models; Phase 1 wires session events only (D3 + scope note) → Tasks 2, 6, 7. ✓
- Emit at natural points, best-effort, commit-class on commit outcome (D4) → Tasks 4, 6, 7. ✓
- Cursor-based idempotent consumers + replay (D5) → Tasks 5, 8 (`--replay`). ✓
- Demonstrator `RunEventSummary` projection → Tasks 3, 5, 8. ✓
- Module shape `javdb/pipeline/events/` + repo (D7) → Tasks 2-5. ✓
- Deferred: per-entity emit + batching, re-pointing ADR-033/035, history-as-projection → not built; documented. ✓
- Docs (CONTEXT.md, cli-reference) → Task 9. ✓

**Type consistency:** `PipelineEventRecord`, `PipelineEventRepo`
(`append`/`read_since`/`get_cursor`/`advance_cursor`), `RunEventSummaryRepo`
(`bump`/`reset`/`get`), store `emit`/`read_since`, `Consumer.run_once`,
`RunEventSummaryConsumer` are used identically across Tasks 2-9. ✓

**Integration points (grep-located edits):** Task 6 (`run_service.py:587`,
after `_set_active_session_id`) and Task 7 (`commit_session.py` drain-success log +
`except` block). Both add a single best-effort `emit` call; neither alters control flow.

**Known overlap (documented, intentional):** the torrent emit points
(`TorrentQueued`/`TorrentCompleted`) are NOT wired here to avoid double-hooking the
uploader/cleanup lines ADR-033 IMP-033-01 already edits. Phase 2 (IMP-ADR036-02)
wires per-entity emit *and* re-points ADR-033/035 to consume the spine (D6 strangler).

**Open verification dependency:** Task 1 Steps 2-3 require live `wrangler` D1 +
`apps.cli.db.sync_d1_to_sqlite`; run where other migrations are applied.
