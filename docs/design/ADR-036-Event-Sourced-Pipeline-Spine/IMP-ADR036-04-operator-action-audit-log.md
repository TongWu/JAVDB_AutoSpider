# IMP-ADR036-04: ADR-036 Phase 4 - Operator / Web-Console Action Audit Log

**Status:** Proposed

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-036](ADR-036-event-sourced-pipeline-spine.md) (umbrella) — this is **Phase 4**. It is independent of and does NOT disturb the reserved Phase-3 strangler slot (`IMP-ADR036-03`, deferred).

**Goal:** Ship ADR-036 Phase 4 by adding an operator-action audit log: a record of *who did what mutation, when, and from where (web/cli/worker)* across the Python API, the Cloudflare Worker, and operator CLI commands — without touching the pipeline `PipelineEvent` spine.

**Architecture:** The existing `PipelineEvent` spine records **pipeline entity-lifecycle** events (movie/torrent/session: `Discovered`/`Selected`/`Queued`/`Completed`, `RunStarted`/`SessionCommitted`/`SessionFailed`) and has **no actor/operator dimension**; its `session_id` is `NOT NULL`. This phase adds a **sibling, append-only `OperatorAuditEvent` table** that captures human/operator mutations. A new sibling table (not new columns on `PipelineEvent`) is the key decision: `PipelineEvent.session_id` is `NOT NULL` and pipeline-scoped, while operator actions frequently have no session. Keeping them separate keeps the pipeline spine pure (consistent with the ADR's additive, entity-lifecycle framing) while reusing the same append-only / global-monotonic-`seq` pattern. A best-effort `record_operator_action(...)` emit helper is called at existing mutation points; an admin-only read API plus a Web "Audit Log" view expose the log. Audit writes are best-effort — a failure to record an audit row must NEVER raise into or fail the underlying mutation.

**Tech Stack:** Python 3, `sqlite3`/D1 via `javdb.storage.db.get_db`, `dataclasses`, `pytest`, FastAPI/Pydantic, Cloudflare D1 + `wrangler`, TypeScript, Hono, Vue 3, Naive UI, Vitest, Markdown docs.

**Storage placement:** `OperatorAuditEvent` lives in the **reports** logical DB (`javdb-reports`), the same DB family as `PipelineEvent` (confirmed in [IMP-ADR036-01](IMP-ADR036-01-event-spine.md) "Storage placement"). The D1 migration is mirrored into `_REPORTS_DDL` in `javdb/storage/db/_db_migrations.py`.

**Source spec:** [ADR-036](ADR-036-event-sourced-pipeline-spine.md), D1/D3/D4 (additive, entity-lifecycle, best-effort emit) extended to the operator dimension.

**Non-negotiable:** Phase 4 records **human/operator mutations only**. It must NOT alter the existing `PipelineEvent` table, its event taxonomy, or its emit points. Audit writes are best-effort and must never fail the mutation they observe. There are **no automatic actions** — this phase is read + record only.

## Table of Contents

- [File Map](#file-map)
- [Scope Boundaries](#scope-boundaries)
- [Task 1: D1 OperatorAuditEvent Table](#task-1-d1-operatorauditevent-table)
- [Task 2: Audit Event Model + Repository](#task-2-audit-event-model--repository)
- [Task 3: Best-Effort record_operator_action Emit Helper](#task-3-best-effort-record_operator_action-emit-helper)
- [Task 4: Python API Emit + Read Surface](#task-4-python-api-emit--read-surface)
- [Task 5: Cloudflare Worker Parity](#task-5-cloudflare-worker-parity)
- [Task 6: Web Audit Log View](#task-6-web-audit-log-view)
- [Task 7: Documentation](#task-7-documentation)
- [Task 8: Verification And Closeout](#task-8-verification-and-closeout)

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `javdb/migrations/d1/2026_06_13_add_operator_audit_event.sql` | D1-first sibling append-only operator-action audit table. |
| Modify | `javdb/storage/db/_db_migrations.py` | Add `OperatorAuditEvent` to `_REPORTS_DDL` for the local SQLite mirror. |
| Create | `javdb/events/operator_audit.py` | `OperatorAuditEventRecord` model + best-effort `record_operator_action(...)` emit helper. |
| Create | `javdb/storage/repos/operator_audit_repo.py` | `OperatorAuditRepo`: `append(event)`, `list(filters, limit, cursor)`. |
| Modify | `apps/api/schemas/diagnostics.py` | Add operator-audit list/item schemas. |
| Modify | `apps/api/routers/diagnostics.py` | Add admin-only `GET /api/audit/operator-events` read endpoint. |
| Modify | `apps/api/routers/sessions.py` | Emit operator-audit rows on commit/rollback (source `web`). |
| Modify | `apps/api/routers/config.py` | Emit on config save (source `web`). |
| Modify | `apps/api/routers/operations.py` | Emit on content-filter rule change (ADR-040) (source `web`). |
| Modify | `apps/api/routers/gh_actions.py` | Emit on job/workflow dispatch (source `web`). |
| Modify | `apps/cli/db/rollback.py` | Emit on CLI rollback (source `cli`, actor from OS/env user). |
| Modify | `apps/cli/ops/stale_session_cleanup.py` | Emit on CLI stale-session cleanup (source `cli`). |
| Create | `tests/unit/test_operator_audit_model.py` | Model serialization tests. |
| Create | `tests/unit/test_operator_audit_repo.py` | Repository append/list/filter/cursor tests. |
| Create | `tests/unit/test_operator_audit_emit.py` | Best-effort emit tests (repo failure must NOT propagate). |
| Modify | `tests/unit/test_ops_diagnostics_api.py` | API tests: read endpoint is admin-only; emit is best-effort. |
| Modify | `../JAVDB_AutoSpider_Web/server/routes/diagnostics.ts` | Worker read endpoint + audit emit helper. |
| Modify | `../JAVDB_AutoSpider_Web/server/routes/sessions.ts` | Worker emit on commit/rollback dispatch (source `worker`). |
| Modify | `../JAVDB_AutoSpider_Web/server/routes/config.ts` | Worker emit on config save. |
| Modify | `../JAVDB_AutoSpider_Web/server/routes/operations.ts` | Worker emit on content-filter rule change. |
| Modify | `../JAVDB_AutoSpider_Web/server/routes/gh-actions.ts` | Worker emit on workflow dispatch. |
| Modify | `../JAVDB_AutoSpider_Web/server/__tests__/diagnostics-routes.test.ts` | Worker audit read-route tests. |
| Modify | `../JAVDB_AutoSpider_Web/src/api/diagnostics.ts` | Frontend operator-audit API types + functions. |
| Create | `../JAVDB_AutoSpider_Web/src/pages/diagnostics/AuditLogPage.vue` | Web "Audit Log" view (filterable list of operator actions). |
| Modify | `../JAVDB_AutoSpider_Web/src/router/routes.ts` | Add `/diag/audit-log` route. |
| Modify | `../JAVDB_AutoSpider_Web/src/components/layout/Sidebar.vue` | Add `auditLog` diagnostics sidebar entry + path. |
| Modify | `../JAVDB_AutoSpider_Web/src/locales/en.json` | Add `nav.auditLog` label (+ paired zh locale). |
| Create | `../JAVDB_AutoSpider_Web/tests/unit/operator-audit-api.spec.ts` | Frontend API client tests. |
| Create | `docs/handbook/en/ops/audit-log.md` | What is / isn't recorded (operator mutations, NOT pipeline entity events). |
| Create | `docs/handbook/zh/ops/audit-log.md` | Chinese mirror. |

## Scope Boundaries

- The audit log records **operator/human mutations only** — never pipeline entity-lifecycle events. `PipelineEvent` is the spine for the latter and is **untouched** by this phase.
- This is a **sibling table**, not new columns on `PipelineEvent`. Do not add an actor dimension to `PipelineEvent`; do not change its event types or emit points.
- Audit writes are **best-effort**: a repo/D1 failure must be swallowed and must never raise into, roll back, or fail the underlying mutation.
- This phase is **read + record only**. No endpoint or UI element executes, replays, or undoes any recorded action.
- `session_id` on an audit row is **nullable** — operator actions often have no session (the whole reason for a sibling table rather than columns on `PipelineEvent`).
- The read API is **admin-only**. The Web view is gated behind the same admin role.

---

## Task 1: D1 OperatorAuditEvent Table

**Files:**
- Create: `javdb/migrations/d1/2026_06_13_add_operator_audit_event.sql`
- Modify: `javdb/storage/db/_db_migrations.py` (add the table + indexes to `_REPORTS_DDL`)

> **Schema-mirror invariant.** `init_db()` builds local `reports.db` from
> `_REPORTS_DDL` (fresh installs AND the test suite's `_isolate_sqlite` fixture);
> it does NOT run `d1/*.sql`. The same table + indexes must be added to
> `_REPORTS_DDL` next to `PipelineEvent` / `OpsIncidents`, or a fresh install hits
> `no such table` and best-effort emits silently drop every row. Keep `--`
> comments OUT of the `CREATE TABLE` body (the repo's column parser splits on
> commas and would read a comment line as a column — same constraint as
> [IMP-ADR036-01](IMP-ADR036-01-event-spine.md) Task 1). No `SCHEMA_VERSION` bump
> is needed (additive table, same as `OpsIncidents` / `PipelineEvent`).

- [ ] **Step 1: Write the migration SQL**

Create `javdb/migrations/d1/2026_06_13_add_operator_audit_event.sql`:

```sql
-- 2026-06-13: Add operator-action audit log (ADR-036 Phase 4).
--
-- Apply with:
--   wrangler d1 execute javdb-reports --remote \
--     --file=javdb/migrations/d1/2026_06_13_add_operator_audit_event.sql
--
-- Sibling append-only log for HUMAN/OPERATOR mutations only. It does NOT touch
-- the PipelineEvent spine (pipeline entity-lifecycle events) and is fully
-- additive. session_id is nullable because operator actions often have none.

CREATE TABLE IF NOT EXISTS OperatorAuditEvent (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  actor TEXT NOT NULL,
  actor_type TEXT NOT NULL CHECK(actor_type IN ('user','api','github','system')),
  action TEXT NOT NULL,
  target_type TEXT,
  target_id TEXT,
  source TEXT NOT NULL CHECK(source IN ('web','cli','worker')),
  session_id TEXT,
  payload_json TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_operator_audit_actor ON OperatorAuditEvent(actor);
CREATE INDEX IF NOT EXISTS idx_operator_audit_action ON OperatorAuditEvent(action);
CREATE INDEX IF NOT EXISTS idx_operator_audit_created_at ON OperatorAuditEvent(created_at);
CREATE INDEX IF NOT EXISTS idx_operator_audit_target ON OperatorAuditEvent(target_type, target_id);
```

- [ ] **Step 2: Add the local mirror DDL**

Add the same `CREATE TABLE OperatorAuditEvent` + the four indexes to the
`_REPORTS_DDL` block in `javdb/storage/db/_db_migrations.py`, next to the
`PipelineEvent` / `EventConsumerCursor` definitions. Keep `--` comments out of
the `CREATE TABLE` body.

- [ ] **Step 3: Verify schema syntax locally**

Run:

```bash
python3 -m compileall javdb/storage/db/_db_migrations.py
```

Expected: compile succeeds.

- [ ] **Step 4: Verify the mirror table parses**

Run:

```bash
python3 -c "import sqlite3; from javdb.storage.db._db_migrations import _REPORTS_DDL; c=sqlite3.connect(':memory:'); c.executescript(_REPORTS_DDL); print(sorted(r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table' AND name='OperatorAuditEvent'\")))"
```

Expected: `['OperatorAuditEvent']`.

- [ ] **Step 5: Defer remote apply**

Record this command for rollout, but do not run it while writing the plan:

```bash
wrangler d1 execute javdb-reports --remote \
  --file=javdb/migrations/d1/2026_06_13_add_operator_audit_event.sql
python3 -m apps.cli.db.sync_d1_to_sqlite --apply --force-overwrite-all
```

Expected during rollout: D1 creates `OperatorAuditEvent`; local SQLite re-aligns.

---

## Task 2: Audit Event Model + Repository

**Files:**
- Create: `javdb/events/operator_audit.py` (model only in this task; the emit helper lands in Task 3)
- Create: `javdb/storage/repos/operator_audit_repo.py`
- Test: `tests/unit/test_operator_audit_model.py`, `tests/unit/test_operator_audit_repo.py`

- [ ] **Step 1: Write the failing model + repo tests**

Create `tests/unit/test_operator_audit_model.py`:

```python
from __future__ import annotations

from javdb.events.operator_audit import OperatorAuditEventRecord


def test_record_minimal_defaults():
    r = OperatorAuditEventRecord(
        actor="alice",
        actor_type="user",
        action="session.commit",
        source="web",
    )
    assert r.target_type is None
    assert r.target_id is None
    assert r.session_id is None
    assert r.payload_json is None
    assert r.seq is None


def test_record_full():
    r = OperatorAuditEventRecord(
        actor="alice",
        actor_type="user",
        action="session.rollback",
        target_type="session",
        target_id="20260613T120000.000000Z-0001-0001",
        source="web",
        session_id="20260613T120000.000000Z-0001-0001",
        payload_json='{"reason":"bad run"}',
    )
    assert r.actor_type == "user"
    assert r.source == "web"
    assert r.target_type == "session"
```

Create `tests/unit/test_operator_audit_repo.py`:

```python
from __future__ import annotations

import sqlite3

from javdb.events.operator_audit import OperatorAuditEventRecord
from javdb.storage.repos.operator_audit_repo import OperatorAuditRepo

_DDL = """
CREATE TABLE OperatorAuditEvent (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  actor TEXT NOT NULL,
  actor_type TEXT NOT NULL,
  action TEXT NOT NULL,
  target_type TEXT,
  target_id TEXT,
  source TEXT NOT NULL,
  session_id TEXT,
  payload_json TEXT,
  created_at TEXT NOT NULL
);
"""


def _conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_DDL)
    return c


def _rec(actor="alice", action="session.commit", source="web", target_type="session", target_id="S1"):
    return OperatorAuditEventRecord(
        actor=actor, actor_type="user", action=action, source=source,
        target_type=target_type, target_id=target_id, created_at="t",
    )


def test_append_returns_monotonic_seq():
    repo = OperatorAuditRepo(_conn())
    s1 = repo.append(_rec())
    s2 = repo.append(_rec(action="config.save"))
    assert s2 > s1


def test_list_returns_newest_first():
    repo = OperatorAuditRepo(_conn())
    repo.append(_rec(action="session.commit"))
    repo.append(_rec(action="config.save"))
    items = repo.list(limit=10)
    assert [i.action for i in items] == ["config.save", "session.commit"]


def test_list_filters_by_actor_action_target_source():
    repo = OperatorAuditRepo(_conn())
    repo.append(_rec(actor="alice", action="session.commit", source="web"))
    repo.append(_rec(actor="bob", action="config.save", source="cli", target_type="config"))

    assert [i.actor for i in repo.list(actor="alice")] == ["alice"]
    assert [i.action for i in repo.list(action="config.save")] == ["config.save"]
    assert [i.source for i in repo.list(source="cli")] == ["cli"]
    assert [i.target_type for i in repo.list(target_type="config")] == ["config"]


def test_list_cursor_pagination():
    repo = OperatorAuditRepo(_conn())
    for _ in range(3):
        repo.append(_rec())
    page1 = repo.list(limit=2)
    assert len(page1) == 2
    cursor = page1[-1].seq
    page2 = repo.list(limit=2, cursor=cursor)
    assert len(page2) == 1
    assert page2[0].seq < cursor
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_operator_audit_model.py tests/unit/test_operator_audit_repo.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write the model**

Create `javdb/events/operator_audit.py`:

```python
"""Operator/web-console action audit log (ADR-036 Phase 4).

Sibling to the PipelineEvent spine: records HUMAN/OPERATOR mutations (who did
what, when, from where). It does NOT carry pipeline entity-lifecycle events.
The emit helper (record_operator_action) is added in Task 3."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

ACTOR_TYPES: tuple[str, ...] = ("user", "api", "github", "system")
SOURCES: tuple[str, ...] = ("web", "cli", "worker")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class OperatorAuditEventRecord:
    actor: str
    actor_type: str            # user | api | github | system
    action: str                # e.g. session.commit, session.rollback, config.save
    source: str                # web | cli | worker
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    session_id: Optional[str] = None      # nullable: operator actions often have none
    payload_json: Optional[str] = None    # JSON string
    seq: Optional[int] = None             # assigned by the DB on append
    created_at: Optional[str] = None
```

- [ ] **Step 4: Write the repository**

Create `javdb/storage/repos/operator_audit_repo.py`:

```python
"""Repository for the ADR-036 Phase 4 operator-action audit log (reports DB)."""

from __future__ import annotations

import logging
import sqlite3

from javdb.events.operator_audit import OperatorAuditEventRecord, utc_now_iso

logger = logging.getLogger(__name__)

_COLS = ("actor", "actor_type", "action", "target_type", "target_id",
         "source", "session_id", "payload_json", "created_at")


class OperatorAuditRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        try:
            self._conn.row_factory = sqlite3.Row
        except Exception:
            logger.debug("row_factory set failed", exc_info=True)

    def append(self, event: OperatorAuditEventRecord) -> int:
        created = event.created_at or utc_now_iso()
        cur = self._conn.execute(
            f"INSERT INTO OperatorAuditEvent ({', '.join(_COLS)}) "
            f"VALUES ({', '.join(['?'] * len(_COLS))})",
            [event.actor, event.actor_type, event.action, event.target_type,
             event.target_id, event.source, event.session_id, event.payload_json, created],
        )
        return int(cur.lastrowid)

    def list(
        self,
        *,
        actor: str | None = None,
        action: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        source: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 50,
        cursor: int | None = None,
    ) -> list[OperatorAuditEventRecord]:
        clauses: list[str] = []
        params: list[object] = []
        for col, val in (("actor", actor), ("action", action),
                         ("target_type", target_type), ("target_id", target_id),
                         ("source", source)):
            if val is not None:
                clauses.append(f"{col} = ?")
                params.append(val)
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(since)
        if until is not None:
            clauses.append("created_at <= ?")
            params.append(until)
        if cursor is not None:
            clauses.append("seq < ?")   # keyset pagination, newest-first
            params.append(cursor)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._conn.execute(
            f"SELECT seq, {', '.join(_COLS)} FROM OperatorAuditEvent"
            f"{where} ORDER BY seq DESC LIMIT ?",
            [*params, limit],
        ).fetchall()
        return [
            OperatorAuditEventRecord(
                actor=r["actor"], actor_type=r["actor_type"], action=r["action"],
                source=r["source"], target_type=r["target_type"], target_id=r["target_id"],
                session_id=r["session_id"], payload_json=r["payload_json"],
                seq=r["seq"], created_at=r["created_at"],
            ) for r in rows
        ]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_operator_audit_model.py tests/unit/test_operator_audit_repo.py -v`
Expected: PASS.

---

## Task 3: Best-Effort record_operator_action Emit Helper

**Files:**
- Modify: `javdb/events/operator_audit.py` (add `record_operator_action`)
- Test: `tests/unit/test_operator_audit_emit.py`

> **Best-effort invariant (mirrors ADR-036 D4 / the spine's `emit`).**
> `record_operator_action` NEVER raises — a failure to write the audit row must
> not propagate into or fail the underlying mutation. It swallows its own
> exceptions and returns `None` on failure (or `seq` on success). It resolves the
> reports DB path at CALL TIME via `_db.REPORTS_DB_PATH` (module attribute), not a
> top-level import, so the test suite's path monkeypatch applies — same pattern as
> `javdb/pipeline/events/store.py`.

- [ ] **Step 1: Write the failing emit tests**

Create `tests/unit/test_operator_audit_emit.py`:

```python
from __future__ import annotations

import sqlite3

from javdb.events import operator_audit
from javdb.storage.repos.operator_audit_repo import OperatorAuditRepo

_DDL = """
CREATE TABLE OperatorAuditEvent (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  actor TEXT NOT NULL, actor_type TEXT NOT NULL, action TEXT NOT NULL,
  target_type TEXT, target_id TEXT, source TEXT NOT NULL,
  session_id TEXT, payload_json TEXT, created_at TEXT NOT NULL
);
"""


def _repo():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(_DDL)
    return OperatorAuditRepo(c)


def test_record_appends_and_returns_seq():
    repo = _repo()
    seq = operator_audit.record_operator_action(
        actor="alice", actor_type="user", action="session.commit",
        target_type="session", target_id="S1", source="web", repo=repo,
    )
    assert seq == 1
    assert repo.list()[0].action == "session.commit"


def test_record_serializes_payload_dict_to_json():
    repo = _repo()
    operator_audit.record_operator_action(
        actor="alice", actor_type="user", action="config.save",
        source="web", payload={"keys": ["SMTP_USER"]}, repo=repo,
    )
    assert '"keys"' in (repo.list()[0].payload_json or "")


def test_record_is_best_effort_repo_failure_does_not_propagate():
    class BoomRepo:
        def append(self, _event):
            raise RuntimeError("D1 down")

    # MUST NOT raise — best-effort. Returns None on failure.
    result = operator_audit.record_operator_action(
        actor="alice", actor_type="user", action="session.rollback",
        source="web", repo=BoomRepo(),
    )
    assert result is None


def test_record_missing_actor_is_skipped_not_raised():
    repo = _repo()
    assert operator_audit.record_operator_action(
        actor="", actor_type="user", action="config.save", source="web", repo=repo,
    ) is None
    assert repo.list() == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_operator_audit_emit.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'record_operator_action'`.

- [ ] **Step 3: Add the emit helper**

Append to `javdb/events/operator_audit.py`:

```python
import contextlib
import json
import logging

from javdb.storage import db as _db
from javdb.storage.db import get_db

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def _repo_ctx(repo):
    if repo is not None:
        yield repo
    else:
        # Resolve the path via the module attribute at call time (not a top-level
        # import) so the test suite's path monkeypatch applies. Mirrors
        # javdb/pipeline/events/store.py.
        from javdb.storage.repos.operator_audit_repo import OperatorAuditRepo
        with get_db(_db.REPORTS_DB_PATH) as conn:
            yield OperatorAuditRepo(conn)


def record_operator_action(
    *,
    actor: str,
    actor_type: str,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    source: str,
    session_id: str | None = None,
    payload: dict | None = None,
    repo=None,
) -> int | None:
    """Best-effort: NEVER raises. Records one operator-mutation audit row.

    A failure to write the audit row must not fail the underlying mutation."""
    if not actor:
        logger.debug("operator-audit emit skipped: missing actor (action=%s)", action)
        return None
    record = OperatorAuditEventRecord(
        actor=actor, actor_type=actor_type, action=action,
        target_type=target_type, target_id=target_id, source=source,
        session_id=session_id,
        payload_json=json.dumps(payload, ensure_ascii=False) if payload is not None else None,
        created_at=utc_now_iso(),
    )
    try:
        with _repo_ctx(repo) as r:
            return r.append(record)
    except Exception:
        logger.warning("operator-audit emit failed (action=%s actor=%s)", action, actor, exc_info=True)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_operator_audit_emit.py -v`
Expected: PASS.

---

## Task 4: Python API Emit + Read Surface

**Files:**
- Modify: `apps/api/schemas/diagnostics.py`
- Modify: `apps/api/routers/diagnostics.py`
- Modify: `apps/api/routers/sessions.py`, `apps/api/routers/config.py`, `apps/api/routers/operations.py`, `apps/api/routers/gh_actions.py`
- Modify: `tests/unit/test_ops_diagnostics_api.py`

> **Auth contract (verbatim from the codebase):** the read endpoint is admin-only
> via `from apps.api.infra.auth import _require_auth, require_role` →
> `Depends(require_role("admin"))`. The actor for each emit is the auth principal
> (`str(current.get("sub") or "unknown")`); `source="web"` for all Python API
> emits. Every emit is wrapped by `record_operator_action`, which is already
> best-effort — do NOT add a second try/except at the call site.

- [ ] **Step 1: Add API tests**

Add to `tests/unit/test_ops_diagnostics_api.py`:

```python
def test_operator_audit_events_admin_only(monkeypatch, user_client, admin_client):
    from apps.api.routers import diagnostics
    from javdb.events.operator_audit import OperatorAuditEventRecord

    monkeypatch.setattr(
        diagnostics, "_list_operator_audit",
        lambda **_kwargs: [OperatorAuditEventRecord(
            actor="admin", actor_type="user", action="session.commit",
            source="web", target_type="session", target_id="S1",
            seq=1, created_at="2026-06-13T00:00:00Z",
        )],
    )

    # admin: 200 with items
    ok = admin_client.get("/api/audit/operator-events")
    assert ok.status_code == 200
    assert ok.json()["items"][0]["action"] == "session.commit"

    # non-admin: forbidden
    forbidden = user_client.get("/api/audit/operator-events")
    assert forbidden.status_code in (401, 403)


def test_session_commit_emits_operator_audit_best_effort(monkeypatch, admin_client):
    calls = []
    import javdb.events.operator_audit as audit
    monkeypatch.setattr(audit, "record_operator_action",
                        lambda **kw: calls.append(kw))
    # ... drive the existing session-commit endpoint as the other commit tests do ...
    # Assert an audit row was emitted with action="session.commit", source="web".
    # (And assert that if record_operator_action raised, the commit still returns 200,
    #  proving best-effort — patch it to raise and re-drive.)
```

- [ ] **Step 2: Add schemas**

Add to `apps/api/schemas/diagnostics.py`:

```python
class OperatorAuditEventSchema(BaseModel):
    seq: int
    actor: str
    actor_type: str
    action: str
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    source: str
    session_id: Optional[str] = None
    payload_json: Optional[str] = None
    created_at: str


class OperatorAuditEventListResponse(BaseModel):
    items: list[OperatorAuditEventSchema]
    next_cursor: Optional[int] = None
```

Add the schema names to `__all__`.

- [ ] **Step 3: Add the read endpoint + helper**

Add to `apps/api/routers/diagnostics.py`:

```python
from javdb.storage.db import REPORTS_DB_PATH, get_db
from javdb.storage.repos.operator_audit_repo import OperatorAuditRepo


def _list_operator_audit(**filters):
    with get_db(REPORTS_DB_PATH) as conn:
        return OperatorAuditRepo(conn).list(**filters)


@router.get("/audit/operator-events", response_model=OperatorAuditEventListResponse)
def list_operator_audit_events(
    actor: Optional[str] = None,
    action: Optional[str] = None,
    target_type: Optional[str] = None,
    source: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 50,
    cursor: Optional[int] = None,
    _current: Dict[str, Any] = Depends(require_role("admin")),
) -> OperatorAuditEventListResponse:
    items = _list_operator_audit(
        actor=actor, action=action, target_type=target_type, source=source,
        since=since, until=until, limit=limit, cursor=cursor,
    )
    next_cursor = items[-1].seq if len(items) == limit else None
    return OperatorAuditEventListResponse(
        items=[OperatorAuditEventSchema(**vars(i)) for i in items],
        next_cursor=next_cursor,
    )
```

> The `/audit/operator-events` path is mounted on the diagnostics router; confirm
> its router prefix yields `/api/audit/operator-events` (adjust the prefix or use
> an explicit path so the public route matches the spec and the Worker parity).

- [ ] **Step 4: Instrument the mutation endpoints (source=`web`)**

At each existing Python API mutation endpoint, after the mutation succeeds, add
one `record_operator_action(...)` call. The actor is `str(current.get("sub") or "unknown")`,
`actor_type="user"`, `source="web"`:

- `apps/api/routers/sessions.py` — session **commit** (`action="session.commit"`,
  `target_type="session"`, `target_id=session_id`) and session **rollback**
  (`action="session.rollback"`).
- `apps/api/routers/config.py` — **config save** (`action="config.save"`,
  `target_type="config"`, optional `payload={"keys": [...]}` of changed keys).
- `apps/api/routers/operations.py` — **content-filter rule change** (ADR-040)
  (`action="content_filter.rule_change"`, `target_type="content_filter_rule"`).
- `apps/api/routers/gh_actions.py` — **job/workflow dispatch**
  (`action="workflow.dispatch"`, `target_type="workflow"`, `target_id=<workflow>`).

Example (sessions commit):

```python
from javdb.events.operator_audit import record_operator_action

# ... after the commit succeeds, before returning the response ...
record_operator_action(
    actor=str(_user.get("sub") or "unknown"), actor_type="user",
    action="session.commit", target_type="session", target_id=session_id,
    source="web", session_id=session_id,
)
```

> `record_operator_action` is best-effort internally — no extra try/except at the
> call site. Do not change the endpoint's control flow or response.

- [ ] **Step 5: Run API tests**

Run: `pytest tests/unit/test_ops_diagnostics_api.py -v`
Expected: PASS (admin-only read; emit best-effort; commit still 200 if emit raises).

---

## Task 5: Cloudflare Worker Parity

**Files:**
- Modify: `../JAVDB_AutoSpider_Web/server/routes/diagnostics.ts`
- Modify: `../JAVDB_AutoSpider_Web/server/routes/sessions.ts`, `config.ts`, `operations.ts`, `gh-actions.ts`
- Modify: `../JAVDB_AutoSpider_Web/server/__tests__/diagnostics-routes.test.ts`

> **Parity contract (ADR-017 dual-backend rule).** The Worker is the thin D1
> query + GH-Actions-dispatch backend. It exposes the same read endpoint shape and
> emits the same audit actions at its mutation routes. Worker emits use the JWT
> principal as `actor` and `source` is `worker` for actions the Worker itself
> performs (e.g. dispatching a GH Actions workflow that the Python backend would
> run as a subprocess), or `web` where the Worker is purely the console-facing
> write path. Pick per route and keep it consistent with the Python side's action
> names. Audit inserts are best-effort: wrap each in a try/catch that logs and
> swallows so an audit failure never fails the mutation.

- [ ] **Step 1: Add Worker read-route tests**

Add to `server/__tests__/diagnostics-routes.test.ts` in the Web repo:

```ts
async function seedOperatorAuditTable(db: D1Database) {
  await db.prepare(`
    CREATE TABLE IF NOT EXISTS OperatorAuditEvent (
      seq INTEGER PRIMARY KEY AUTOINCREMENT,
      actor TEXT NOT NULL, actor_type TEXT NOT NULL, action TEXT NOT NULL,
      target_type TEXT, target_id TEXT, source TEXT NOT NULL,
      session_id TEXT, payload_json TEXT, created_at TEXT NOT NULL
    )
  `).run();
  await db.prepare("DELETE FROM OperatorAuditEvent").run();
  await db.prepare(`
    INSERT INTO OperatorAuditEvent
      (actor, actor_type, action, target_type, target_id, source, session_id, payload_json, created_at)
    VALUES ('admin','user','session.commit','session','S1','web',NULL,NULL,'2026-06-13T00:00:00Z')
  `).run();
}

it("GET /api/audit/operator-events is admin-only and returns rows", async () => {
  await seedOperatorAuditTable(env.REPORTS_DB);
  const token = await getAdminToken();

  const res = await app.request("/api/audit/operator-events", {
    headers: { Authorization: `Bearer ${token}` },
  }, env);

  expect(res.status).toBe(200);
  const data = await res.json() as any;
  expect(data.items[0].action).toBe("session.commit");

  const userToken = await getUserToken();
  const forbidden = await app.request("/api/audit/operator-events", {
    headers: { Authorization: `Bearer ${userToken}` },
  }, env);
  expect([401, 403]).toContain(forbidden.status);
});
```

- [ ] **Step 2: Add the Worker read route + audit helper**

Add to `server/routes/diagnostics.ts`:

```ts
function mapOperatorAuditEvent(row: any) {
  return {
    seq: row.seq,
    actor: row.actor,
    actor_type: row.actor_type,
    action: row.action,
    target_type: row.target_type ?? null,
    target_id: row.target_id ?? null,
    source: row.source,
    session_id: row.session_id ?? null,
    payload_json: row.payload_json ?? null,
    created_at: row.created_at,
  };
}

// Best-effort audit insert — never throws into the caller.
export async function recordOperatorAction(
  db: D1Database,
  e: { actor: string; actor_type: string; action: string; target_type?: string | null;
       target_id?: string | null; source: string; session_id?: string | null; payload?: unknown },
): Promise<void> {
  if (!e.actor) return;
  try {
    await db.prepare(`
      INSERT INTO OperatorAuditEvent
        (actor, actor_type, action, target_type, target_id, source, session_id, payload_json, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    `).bind(
      e.actor, e.actor_type, e.action, e.target_type ?? null, e.target_id ?? null,
      e.source, e.session_id ?? null, e.payload != null ? JSON.stringify(e.payload) : null,
    ).run();
  } catch (err) {
    console.warn("operator-audit emit failed", e.action, err);
  }
}

diagnosticsRoutes.get("/audit/operator-events", requireRole("admin"), async (c) => {
  const q = c.req.query();
  const clauses: string[] = [];
  const params: unknown[] = [];
  for (const col of ["actor", "action", "target_type", "source"] as const) {
    if (q[col]) { clauses.push(`${col} = ?`); params.push(q[col]); }
  }
  if (q.since) { clauses.push("created_at >= ?"); params.push(q.since); }
  if (q.until) { clauses.push("created_at <= ?"); params.push(q.until); }
  if (q.cursor) { clauses.push("seq < ?"); params.push(Number(q.cursor)); }
  const limit = Math.min(Number(q.limit ?? 50), 200);
  const where = clauses.length ? ` WHERE ${clauses.join(" AND ")}` : "";
  const rows = await c.env.REPORTS_DB
    .prepare(`SELECT * FROM OperatorAuditEvent${where} ORDER BY seq DESC LIMIT ?`)
    .bind(...params, limit)
    .all();
  const items = rows.results.map(mapOperatorAuditEvent);
  const next_cursor = items.length === limit ? items[items.length - 1].seq : null;
  return c.json({ items, next_cursor });
});
```

> Confirm the diagnostics router mount yields `/api/audit/operator-events` to
> match the Python path; adjust the mount/path if the prefix differs.

- [ ] **Step 3: Emit at the Worker mutation routes**

At each existing Worker mutation route, after the mutation/dispatch succeeds, call
`recordOperatorAction(c.env.REPORTS_DB, {...})` with `actor` from the JWT principal
(`c.get("user").sub`) and the same `action` names as the Python side:

- `server/routes/sessions.ts` — commit (`session.commit`) / rollback dispatch (`session.rollback`).
- `server/routes/config.ts` — `config.save`.
- `server/routes/operations.ts` — `content_filter.rule_change`.
- `server/routes/gh-actions.ts` — `workflow.dispatch` (`source: "worker"`).

- [ ] **Step 4: Run Worker tests**

Run from the Web repo:

```bash
npm run test:server -- server/__tests__/diagnostics-routes.test.ts
```

Expected: PASS.

---

## Task 6: Web Audit Log View

**Files:**
- Modify: `../JAVDB_AutoSpider_Web/src/api/diagnostics.ts`
- Create: `../JAVDB_AutoSpider_Web/src/pages/diagnostics/AuditLogPage.vue`
- Modify: `../JAVDB_AutoSpider_Web/src/router/routes.ts`
- Modify: `../JAVDB_AutoSpider_Web/src/components/layout/Sidebar.vue`
- Modify: `../JAVDB_AutoSpider_Web/src/locales/en.json` (+ paired zh locale)
- Create: `../JAVDB_AutoSpider_Web/tests/unit/operator-audit-api.spec.ts`

- [ ] **Step 1: Add frontend API tests**

Create `tests/unit/operator-audit-api.spec.ts` in the Web repo:

```ts
import { describe, expect, it, vi } from 'vitest'
import { http } from '@/api/client'
import { listOperatorAuditEvents } from '@/api/diagnostics'

describe('operator audit API', () => {
  it('lists operator audit events with filters', async () => {
    const spy = vi.spyOn(http, 'get').mockResolvedValueOnce({ data: { items: [], next_cursor: null } })

    await listOperatorAuditEvents({ actor: 'alice', source: 'web', limit: 50 })

    expect(spy).toHaveBeenCalledWith('/api/audit/operator-events', {
      params: { actor: 'alice', source: 'web', limit: 50 },
    })
  })
})
```

- [ ] **Step 2: Add frontend API contracts**

Modify `src/api/diagnostics.ts` in the Web repo:

```ts
export interface OperatorAuditEvent {
  seq: number
  actor: string
  actor_type: string
  action: string
  target_type?: string | null
  target_id?: string | null
  source: string
  session_id?: string | null
  payload_json?: string | null
  created_at: string
}

export interface OperatorAuditEventListResponse {
  items: OperatorAuditEvent[]
  next_cursor?: number | null
}

export interface OperatorAuditFilters {
  actor?: string
  action?: string
  target_type?: string
  source?: string
  since?: string
  until?: string
  limit?: number
  cursor?: number
}

export async function listOperatorAuditEvents(
  params: OperatorAuditFilters = {},
): Promise<OperatorAuditEventListResponse> {
  const { data } = await http.get<OperatorAuditEventListResponse>(
    '/api/audit/operator-events', { params },
  )
  return data
}
```

- [ ] **Step 3: Create the Audit Log view**

Create `src/pages/diagnostics/AuditLogPage.vue`:

- Load operator-audit events on mount and when filters change.
- Filter controls: actor, action, target_type, source, since/until date range.
- Render a table: created_at, actor (+ actor_type), action, target (target_type/target_id),
  source, session_id; expand row to show `payload_json`.
- Cursor pagination via `next_cursor` ("Load more").
- This is a **read-only** view — no buttons that execute, replay, or undo any
  recorded action.

- [ ] **Step 4: Wire route + sidebar + locale**

- `src/router/routes.ts` — add `{ path: '/diag/audit-log', component: AuditLogPage }`
  (admin-guarded, mirroring the `/diag/ops-incidents` route).
- `src/components/layout/Sidebar.vue` — add `{ label: t('nav.auditLog'), key: 'auditLog' }`
  to the diagnostics children (next to `opsIncidents`), and add
  `auditLog: '/diag/audit-log'` to the path map.
- `src/locales/en.json` — add `"auditLog": "Audit Log"` under `nav`; mirror into
  the paired zh locale (e.g. `"auditLog": "审计日志"`).

- [ ] **Step 5: Run Web unit tests**

Run from the Web repo:

```bash
npm run test:unit -- tests/unit/operator-audit-api.spec.ts
```

Expected: PASS.

---

## Task 7: Documentation

**Files:**
- Create: `docs/handbook/en/ops/audit-log.md`
- Create: `docs/handbook/zh/ops/audit-log.md`

- [ ] **Step 1: Write the English ops doc**

Create `docs/handbook/en/ops/audit-log.md`:

````markdown
# Operator Action Audit Log

The audit log records **operator/human mutations** — who did what, when, and from
where — across the web console, the Cloudflare Worker, and operator CLI commands.
It is a sibling of the ADR-036 pipeline event spine, not part of it.

## What IS recorded

Operator mutations, including:

- Session commit / rollback
- Config save
- Content-filter rule change (ADR-040)
- Job / workflow dispatch

Each row carries: `actor`, `actor_type` (`user` / `api` / `github` / `system`),
`action`, `target_type` / `target_id`, `source` (`web` / `cli` / `worker`),
optional `session_id`, optional `payload_json`, and `created_at`.

## What is NOT recorded

- **Pipeline entity-lifecycle events** (movie/torrent/session `Discovered` /
  `Selected` / `Queued` / `Completed`, `RunStarted` / `SessionCommitted` /
  `SessionFailed`) — those live in `PipelineEvent` (ADR-036 Phase 1/2), not here.
- Per-field mutations — the audit log records the operator action, not every
  field touched.

## Read access

`GET /api/audit/operator-events` (admin-only; filters: `actor`, `action`,
`target_type`, `source`, `since`, `until`; cursor-paginated). The Web "Audit Log"
view under Diagnostics renders the same data.

## Guarantees

Audit writes are **best-effort**: a failure to record an audit row never fails the
underlying mutation. The log is **read + record only** — nothing here executes,
replays, or undoes a recorded action.
````

- [ ] **Step 2: Mirror in Chinese**

Create `docs/handbook/zh/ops/audit-log.md` with the same structure, translating
prose to Chinese and keeping code/paths/identifiers verbatim (`actor`,
`actor_type`, `source`, `PipelineEvent`, `GET /api/audit/operator-events`, etc.).

- [ ] **Step 3: Documentation whitespace check**

Run:

```bash
git diff --check -- docs/handbook/en/ops/audit-log.md docs/handbook/zh/ops/audit-log.md
```

Expected: no output.

---

## Task 8: Verification And Closeout

- [ ] **Step 1: Run Python tests**

Run:

```bash
pytest \
  tests/unit/test_operator_audit_model.py \
  tests/unit/test_operator_audit_repo.py \
  tests/unit/test_operator_audit_emit.py \
  tests/unit/test_ops_diagnostics_api.py \
  -v
```

Expected: all pass.

- [ ] **Step 2: Run Web tests**

Run from `../JAVDB_AutoSpider_Web`:

```bash
npm run test:server -- server/__tests__/diagnostics-routes.test.ts
npm run test:unit -- tests/unit/operator-audit-api.spec.ts
```

Expected: all pass.

- [ ] **Step 3: Run static checks**

Run from the main repo:

```bash
python3 -m compileall \
  javdb/events/operator_audit.py \
  javdb/storage/repos/operator_audit_repo.py \
  javdb/storage/db/_db_migrations.py \
  apps/api/routers/diagnostics.py \
  apps/api/schemas/diagnostics.py
git diff --check
```

Run from the Web repo:

```bash
npm run typecheck
npm run lint
```

Expected: no failures.

- [ ] **Step 4: PipelineEvent-untouched invariant**

Confirm this phase did not alter the spine:

```bash
git diff --stat -- javdb/pipeline/events javdb/migrations/d1/2026_05_29_add_pipeline_event.sql
```

Expected: no output (the `PipelineEvent` spine and its emit points are untouched).

- [ ] **Step 5: Manual safety smoke**

Open the Web UI at:

```text
http://localhost:5173/diag/audit-log
```

Expected:

- Audit Log view lists operator actions with filters and cursor pagination.
- Only an admin can reach it; a non-admin is forbidden.
- No UI element executes, replays, or undoes any recorded action.

- [ ] **Step 6: Commit**

Commit only the Phase 4 source, tests, and docs. Do not commit `reports/` data files.

```bash
git add \
  javdb/migrations/d1/2026_06_13_add_operator_audit_event.sql \
  javdb/storage/db/_db_migrations.py \
  javdb/events/operator_audit.py \
  javdb/storage/repos/operator_audit_repo.py \
  apps/api/schemas/diagnostics.py \
  apps/api/routers/diagnostics.py \
  apps/api/routers/sessions.py \
  apps/api/routers/config.py \
  apps/api/routers/operations.py \
  apps/api/routers/gh_actions.py \
  apps/cli/db/rollback.py \
  apps/cli/ops/stale_session_cleanup.py \
  tests/unit/test_operator_audit_model.py \
  tests/unit/test_operator_audit_repo.py \
  tests/unit/test_operator_audit_emit.py \
  tests/unit/test_ops_diagnostics_api.py \
  docs/handbook/en/ops/audit-log.md \
  docs/handbook/zh/ops/audit-log.md
git commit -m "feat(ops): add operator/web-console action audit log (ADR-036 Phase 4)"
```

Commit the Web repo changes separately:

```bash
cd ../JAVDB_AutoSpider_Web
git add \
  server/routes/diagnostics.ts \
  server/routes/sessions.ts \
  server/routes/config.ts \
  server/routes/operations.ts \
  server/routes/gh-actions.ts \
  server/__tests__/diagnostics-routes.test.ts \
  src/api/diagnostics.ts \
  src/pages/diagnostics/AuditLogPage.vue \
  src/router/routes.ts \
  src/components/layout/Sidebar.vue \
  src/locales/en.json \
  tests/unit/operator-audit-api.spec.ts
git commit -m "feat(diagnostics): add operator audit log view (ADR-036 Phase 4)"
```
