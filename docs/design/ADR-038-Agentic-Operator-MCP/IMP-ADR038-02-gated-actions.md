# IMP-ADR038-02: ADR-038 Phase 2 — Gated Mutating MCP Tools

> **Closeout note:** This IMP is complete. The sections below now serve as a shipped-work record and verification log.

**Status:** Completed

**Related:** [ADR-038](ADR-038-agentic-operator-mcp-surface.md) (parent), [IMP-ADR038-01](IMP-ADR038-01-readonly-mcp.md) (Phase 1, read-only surface)

**Goal:** Add two **gated, dry-run-by-default** mutating tools — `rollback_session` and `commit_session` — to the ADR-038 FastMCP operator surface, each a thin adapter over an existing service, executing only on an explicit `confirm=true` and attempting a best-effort ADR-036 audit event on confirmed calls.

**Architecture:** Phase 1 shipped a read-only FastMCP server (`apps/mcp/server.py` + `apps/mcp/tools/{observe,diagnose}.py`). This phase adds one new module `apps/mcp/tools/act.py` with two thin `tool_*` functions and registers them in `server.py`. Each tool is **dry-run by default**: with `confirm=False` it returns a preview and mutates nothing; with `confirm=True` it calls the existing service (`db_rollback_session` / `commit_session(CommitRequest)`) and attempts a best-effort `PipelineEvent` audit record. No new auth (stdio = trusted operator, D5); no secrets exposed.

**Tech Stack:** Python 3.11, FastMCP (`mcp.server.fastmcp`), Cloudflare D1 / SQLite via the storage layer, ADR-036 event spine (`javdb/pipeline/events/store.py`), pytest.

**Source spec:** [ADR-038](ADR-038-agentic-operator-mcp-surface.md) D1 (thin adapter), D4 (gated actions: dry-run → `confirm=true` → reuse auth → audit event), D5 (read-only/masked/no-secrets, stdio trusted).

**Non-negotiable (safety):**
- The dry-run path (`confirm=False`) performs **ZERO mutation and ZERO side effects**.
- `confirm=True` is the ONLY path that mutates; confirmed calls attempt one best-effort audit event, with no delivery guarantee on failure paths.
- Reuse — never bypass — the existing rollback safety rails (`db_rollback_session` refuses `committed` sessions unless `force=True`) and the commit service guards.
- `config.py`/secrets are never returned by a tool.

## Run commands (this repo's broken-.venv workaround)

Tests/compile use the anaconda interpreter with an explicit `PYTHONPATH` (the repo `.venv` is broken):

```bash
PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_mcp_actions.py -v
```

## Scope Boundaries

- **In scope:** `rollback_session` + `commit_session` MCP tools (dry-run/confirm/audit), their unit tests, server registration, and the ADR doc amendment recording the `trigger_run` deferral.
- **Out of scope — `trigger_run` is DEFERRED** (ADR-038 D4 listed it, this phase does not ship it). The ADR now records the rationale: it has **no existing Python service** to thin-adapt (would require new GitHub `workflow_dispatch` code, violating D1's thin-adapter principle), is the **highest external-side-effect** action (kicks off CI), and has the **lowest marginal value** because operators already dispatch runs from the GitHub UI / the TS Worker `gh-actions` route. It is revisited only when a dispatch service exists.
- No remote/HTTP transport, no TS Worker MCP (those remain later phases per D6).
- No new auth, no rate limiting, no new DB tables.

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `apps/mcp/tools/act.py` | Thin `tool_rollback_session` / `tool_commit_session` + `_emit_audit` helper. |
| Modify | `apps/mcp/server.py` | Register the two tools via `@mcp.tool()`. |
| Create | `tests/unit/test_mcp_actions.py` | Dry-run-no-mutation, confirm-executes-and-audits, error-degrades. |
| Modify | `docs/design/ADR-038-Agentic-Operator-MCP/ADR-038-agentic-operator-mcp-surface.md` | Record `trigger_run` deferral in D4 + phase table. |
| Modify | `docs/design/ADR-038-Agentic-Operator-MCP/ADR-038-agentic-operator-mcp-surface.zh.md` | Chinese mirror of the same amendment. |
| Modify | `docs/design/ADR-038-Agentic-Operator-MCP/IMP-ADR038-02-gated-actions.md` | Set `Status: Completed` at closeout. |

## Reference signatures used during implementation

- `javdb/storage/db/_db_rollback.py`: `db_rollback_session(session_id: str, *, dry_run: bool = False, scope: str = 'all', force: bool = False, ...) -> Dict[str, Dict[str, int]]`. `dry_run=True` returns `{scope: {table: rows_affected}}` and mutates nothing. `force=False` refuses `ReportSessions.Status='committed'`.
- `javdb/storage/sessions/commit.py`: `@dataclass CommitRequest(session_id: str, force: bool = False, drop_pending: bool = False, emit_metrics: bool = False, fanout_claims: bool = False)`; `commit_session(req: CommitRequest) -> CommitResult` where `CommitResult(session_id, new_state, pending_dropped, ...)`. There is **no built-in dry_run** — the commit preview must be synthesized read-only (do NOT call `commit_session` in the dry-run path).
- `javdb/pipeline/events/store.py`: `emit(event_type: str, *, session_id: str, entity_type: str, entity_id: str | None = None, payload: str | None = None, run_id=None, run_attempt=None, repo=None) -> int | None`. Best-effort (never raises; returns None on failure).
- P1 tool pattern: `apps/mcp/tools/observe.py:tool_get_session` — thin, lazy-imports the service, returns a dict, used by tests that INSERT a real `ReportSessions` row into the isolated test DB and assert on the returned dict.

---

## Task 1: `rollback_session` gated tool

**Files:**
- Create: `apps/mcp/tools/act.py`
- Test: `tests/unit/test_mcp_actions.py`

- Completed: **Step 1: Read the real signatures first**

Read `javdb/storage/db/_db_rollback.py` (the `db_rollback_session` signature + docstring) and `apps/mcp/tools/observe.py` (the `tool_get_session` thin-adapter pattern) so the implementation matched reality. The shipped call path preserves the intended split: `dry_run=True` for preview, `dry_run=False` for execute.

- Completed: **Step 2: Write the failing tests**

Added failing-first coverage in `tests/unit/test_mcp_actions.py`:

```python
import json

import javdb.storage.db as _db
from apps.mcp.tools.act import tool_rollback_session, tool_commit_session


def _insert_session(session_id: str, status: str = "in_progress") -> None:
    with _db.get_db(_db.REPORTS_DB_PATH) as conn:
        conn.execute(
            "INSERT INTO ReportSessions (Id, ReportType, ReportDate, CsvFilename, "
            "DateTimeCreated, Status) VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, "daily", "2026-06-13", "t.csv", "2026-06-13T00:00:00Z", status),
        )


def _session_status(session_id: str) -> str | None:
    with _db.get_db(_db.REPORTS_DB_PATH) as conn:
        row = conn.execute(
            "SELECT Status FROM ReportSessions WHERE Id = ?", (session_id,)
        ).fetchone()
    return None if row is None else row[0]


def test_rollback_dry_run_returns_preview_and_does_not_mutate():
    _insert_session("rb-dry-001", status="in_progress")
    out = tool_rollback_session("rb-dry-001")  # confirm defaults to False
    assert out["confirmed"] is False
    assert out["dry_run"] is True
    assert "would_affect" in out
    # No mutation: the session is still in_progress (rollback marks it 'failed').
    assert _session_status("rb-dry-001") == "in_progress"


def test_rollback_confirm_executes_and_emits_audit(monkeypatch):
    _insert_session("rb-run-001", status="in_progress")
    events: list[dict] = []

    def _capture(event_type, *, session_id, entity_type, **kw):
        events.append({"type": event_type, "session_id": session_id, "entity_type": entity_type})
        return 1

    monkeypatch.setattr("javdb.pipeline.events.store.emit", _capture)

    out = tool_rollback_session("rb-run-001", confirm=True)
    assert out["confirmed"] is True
    assert out["dry_run"] is False
    assert "affected" in out
    # Mutation happened: rollback marks the session 'failed'.
    assert _session_status("rb-run-001") == "failed"
    # Exactly one audit event for the execution.
    assert len(events) == 1
    assert events[0]["type"] == "SessionRolledBack"
    assert events[0]["session_id"] == "rb-run-001"
    assert events[0]["entity_type"] == "session"


def test_rollback_error_degrades_without_raising(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("javdb.storage.db._db_rollback.db_rollback_session", _boom)
    out = tool_rollback_session("whatever", confirm=True)
    assert "error" in out
    assert "kaboom" in out["detail"]
```

- Completed: **Step 3: Run the tests, confirm they fail**

```bash
PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_mcp_actions.py -k rollback -v
```
Verified at the time: the pre-implementation run failed before `tool_rollback_session` existed.

- Completed: **Step 4: Create `apps/mcp/tools/act.py` with the rollback tool**

```python
# apps/mcp/tools/act.py
"""Gated mutating operator actions (ADR-038 Phase 2).

Each tool is a THIN adapter over an existing service (ADR-038 D1) and is
**dry-run by default**: with confirm=False it returns a preview and performs NO
mutation; with confirm=True it executes the underlying service and attempts one
best-effort ADR-036 audit event. Local stdio assumes a trusted operator (D5); no secrets or
config are ever returned. Tools degrade to {"error": ...} and never crash the agent.
"""

from __future__ import annotations

import json


def _emit_audit(event_type: str, session_id: str, payload: dict) -> None:
    """Best-effort audit event for an executed action (ADR-036 spine)."""
    try:
        from javdb.pipeline.events.store import emit
        emit(
            event_type,
            session_id=session_id,
            entity_type="session",
            entity_id=session_id,
            payload=json.dumps(payload, ensure_ascii=False, default=str),
        )
    except Exception:  # noqa: BLE001 — audit is best-effort, never blocks the action
        pass


def tool_rollback_session(
    session_id: str,
    *,
    scope: str = "all",
    force: bool = False,
    confirm: bool = False,
) -> dict:
    """Roll back a pipeline session's writes (ADR-038 D4 — GATED).

    Dry-run unless confirm=True. Refuses a committed session unless force=True
    (the existing rollback safety matrix). With confirm=False returns a preview
    of the rows that WOULD be removed; with confirm=True executes and attempts a
    best-effort 'SessionRolledBack' audit event.
    """
    try:
        from javdb.storage.db._db_rollback import db_rollback_session
        if not confirm:
            preview = db_rollback_session(session_id, dry_run=True, scope=scope, force=force)
            return {
                "action": "rollback_session",
                "session_id": session_id,
                "confirmed": False,
                "dry_run": True,
                "would_affect": preview,
                "note": "Re-call with confirm=true to execute.",
            }
        affected = db_rollback_session(session_id, dry_run=False, scope=scope, force=force)
        _emit_audit("SessionRolledBack", session_id, {"scope": scope, "force": force, "affected": affected})
        return {
            "action": "rollback_session",
            "session_id": session_id,
            "confirmed": True,
            "dry_run": False,
            "affected": affected,
        }
    except Exception as exc:  # noqa: BLE001 — operator-facing tool must degrade
        return {"error": "rollback_session failed", "detail": str(exc)}
```

- Completed: **Step 5: Run the rollback tests, confirm they pass**

```bash
PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_mcp_actions.py -k rollback -v
```
Verified: rollback tests passed after the implementation was added.

- Completed: **Step 6: ruff + commit**

```bash
PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD /opt/anaconda3/bin/python3 -m ruff check apps/mcp/tools/act.py tests/unit/test_mcp_actions.py
git add apps/mcp/tools/act.py tests/unit/test_mcp_actions.py
git commit -m "feat(mcp): add gated rollback_session tool (ADR-038 P2)"
```

---

## Task 2: `commit_session` gated tool

**Files:**
- Modify: `apps/mcp/tools/act.py`
- Test: `tests/unit/test_mcp_actions.py`

- Completed: **Step 1: Read the real signatures first**

Read `javdb/storage/sessions/commit.py` to confirm `CommitRequest` fields and `commit_session(req) -> CommitResult` (`new_state`, `pending_dropped`). Also confirmed how the commit preview obtains pending counts (`HistoryRepo().pending_session_stats(session_id)`); the shipped dry-run path does not call `commit_session`.

- Completed: **Step 2: Append the failing tests**

Extended `tests/unit/test_mcp_actions.py` with:

```python
def test_commit_dry_run_returns_preview_and_does_not_commit():
    _insert_session("cm-dry-001", status="in_progress")
    out = tool_commit_session("cm-dry-001")  # confirm defaults to False
    assert out["confirmed"] is False
    assert out["dry_run"] is True
    assert "would_finalize_pending" in out
    # No mutation: still in_progress (commit moves it to a committed state).
    assert _session_status("cm-dry-001") == "in_progress"


def test_commit_confirm_executes_and_emits_audit(monkeypatch):
    _insert_session("cm-run-001", status="in_progress")
    events: list[dict] = []

    def _capture(event_type, *, session_id, entity_type, **kw):
        events.append({"type": event_type, "session_id": session_id})
        return 1

    captured = {}

    def _fake_commit(req):
        captured["session_id"] = req.session_id
        class _R:
            session_id = req.session_id
            new_state = "committed"
            pending_dropped = 0
        return _R()

    monkeypatch.setattr("javdb.pipeline.events.store.emit", _capture)
    monkeypatch.setattr("javdb.storage.sessions.commit.commit_session", _fake_commit)

    out = tool_commit_session("cm-run-001", confirm=True)
    assert out["confirmed"] is True
    assert out["dry_run"] is False
    assert out["result"]["new_state"] == "committed"
    assert captured["session_id"] == "cm-run-001"
    assert len(events) == 1
    assert events[0]["type"] == "SessionCommitted"


def test_commit_error_degrades_without_raising(monkeypatch):
    def _boom(req):
        raise RuntimeError("commit-boom")

    monkeypatch.setattr("javdb.storage.sessions.commit.commit_session", _boom)
    out = tool_commit_session("whatever", confirm=True)
    assert "error" in out
    assert "commit-boom" in out["detail"]
```

> Note: `test_commit_confirm_executes_and_emits_audit` monkeypatches `commit_session` so the unit test does not depend on a fully-populated pending pipeline; it verifies the tool calls the service with the right `session_id` and attempts the audit emission path. A heavier integration test of the real commit path is out of scope here (covered by the commit service's own tests).

- Completed: **Step 3: Run, confirm the new tests fail**

```bash
PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_mcp_actions.py -k commit -v
```
Verified at the time: the pre-implementation run failed before `tool_commit_session` existed, then passed after implementation.

- Completed: **Step 4: Add `tool_commit_session` to `act.py`**

Added to `apps/mcp/tools/act.py`:

```python
def tool_commit_session(
    session_id: str,
    *,
    force: bool = False,
    drop_pending: bool = False,
    confirm: bool = False,
) -> dict:
    """Finalize (commit) a pipeline session (ADR-038 D4 — GATED).

    Dry-run unless confirm=True. With confirm=False returns a read-only preview
    of the pending writes that WOULD be finalized (it does NOT call the commit
    service). With confirm=True executes commit_session and attempts a
    best-effort 'SessionCommitted' audit event.
    """
    try:
        if not confirm:
            from apps.mcp.tools.observe import tool_get_session
            session = tool_get_session(session_id)
            pending: dict = {}
            try:
                from javdb.storage.repos.history_repo import HistoryRepo
                pending = HistoryRepo().pending_session_stats(session_id)
            except Exception:  # noqa: BLE001 — preview is best-effort
                pending = {}
            return {
                "action": "commit_session",
                "session_id": session_id,
                "confirmed": False,
                "dry_run": True,
                "session_found": bool(session.get("found", False)),
                "would_finalize_pending": pending,
                "note": "Re-call with confirm=true to execute.",
            }
        from javdb.storage.sessions.commit import CommitRequest, commit_session
        result = commit_session(
            CommitRequest(session_id=session_id, force=force, drop_pending=drop_pending)
        )
        payload = {
            "new_state": getattr(result, "new_state", None),
            "pending_dropped": getattr(result, "pending_dropped", 0),
        }
        _emit_audit("SessionCommitted", session_id, payload)
        return {
            "action": "commit_session",
            "session_id": session_id,
            "confirmed": True,
            "dry_run": False,
            "result": payload,
        }
    except Exception as exc:  # noqa: BLE001 — operator-facing tool must degrade
        return {"error": "commit_session failed", "detail": str(exc)}
```

- Completed: **Step 5: Run all action tests, confirm pass**

```bash
PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_mcp_actions.py -v
```
Verified: all action tests passed.

- Completed: **Step 6: ruff + commit**

```bash
PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD /opt/anaconda3/bin/python3 -m ruff check apps/mcp/tools/act.py tests/unit/test_mcp_actions.py
git add apps/mcp/tools/act.py tests/unit/test_mcp_actions.py
git commit -m "feat(mcp): add gated commit_session tool (ADR-038 P2)"
```

---

## Task 3: Register the tools in the MCP server

**Files:**
- Modify: `apps/mcp/server.py`

- Completed: **Step 1: Read the registration pattern**

Read `apps/mcp/server.py` and mirrored the existing `@mcp.tool()` registration pattern used by the read-only tools.

- Completed: **Step 2: Add the import + two registrations**

Added to the imports near the other `from apps.mcp.tools...` lines:

```python
from apps.mcp.tools.act import tool_rollback_session, tool_commit_session
```

Added two registrations alongside the existing `@mcp.tool()` blocks (after the read-only tools):

```python
@mcp.tool()
def rollback_session(session_id: str, scope: str = "all", force: bool = False,
                     confirm: bool = False) -> dict:
    """GATED: roll back a session's writes. Dry-run preview unless confirm=true;
    refuses committed sessions unless force=true. Confirmed calls attempt a
    best-effort audit event."""
    return tool_rollback_session(session_id, scope=scope, force=force, confirm=confirm)


@mcp.tool()
def commit_session(session_id: str, force: bool = False, drop_pending: bool = False,
                   confirm: bool = False) -> dict:
    """GATED: finalize (commit) a session. Dry-run preview unless confirm=true.
    Confirmed calls attempt a best-effort audit event."""
    return tool_commit_session(session_id, force=force, drop_pending=drop_pending, confirm=confirm)
```

- Completed: **Step 3: Verify the server imports + smoke test still pass**

```bash
PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m compileall apps/mcp/server.py apps/mcp/tools/act.py
PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/smoke/test_mcp_server.py -v
```
Verified: compile succeeded and the smoke test passed with the full 10-tool surface.

- Completed: **Step 4: ruff + commit**

```bash
PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD /opt/anaconda3/bin/python3 -m ruff check apps/mcp/server.py
git add apps/mcp/server.py tests/smoke/test_mcp_server.py
git commit -m "feat(mcp): register gated rollback/commit tools (ADR-038 P2)"
```

---

## Task 4: Amend ADR-038 — record the `trigger_run` deferral (bilingual)

**Files:**
- Modify: `docs/design/ADR-038-Agentic-Operator-MCP/ADR-038-agentic-operator-mcp-surface.md`
- Modify: `docs/design/ADR-038-Agentic-Operator-MCP/ADR-038-agentic-operator-mcp-surface.zh.md`

- Completed: **Step 1: Amend D4 in the English ADR**

Updated `ADR-038-agentic-operator-mcp-surface.md` so **D4** reflects what Phase 2 actually ships while preserving the dry-run/confirm/auth/audit wording:

```markdown
> **Phase 2 scope (amended):** Phase 2 ships `rollback_session` and `commit_session`
> only. `trigger_run` is **deferred** — unlike the other two it has no existing
> Python service to thin-adapt (it would require new GitHub `workflow_dispatch`
> code, contrary to D1), it is the highest external-side-effect action, and
> operators already dispatch runs from the GitHub UI / the TS Worker. It is
> revisited when a dispatch service exists. See IMP-ADR038-02.
```

Updated the **phase table** row for Phase 2 to reference `IMP-ADR038-02` and to list only `rollback_session` / `commit_session`, with `trigger_run` noted as deferred.

- Completed: **Step 2: Mirror the amendment in the Chinese ADR**

Applied the same change to `ADR-038-agentic-operator-mcp-surface.zh.md`, keeping `trigger_run` / `rollback_session` / `commit_session` / `workflow_dispatch` / `IMP-ADR038-02` verbatim.

- Completed: **Step 3: Whitespace check + commit**

```bash
git diff --check -- docs/design/ADR-038-Agentic-Operator-MCP/
git add docs/design/ADR-038-Agentic-Operator-MCP/ADR-038-agentic-operator-mcp-surface.md docs/design/ADR-038-Agentic-Operator-MCP/ADR-038-agentic-operator-mcp-surface.zh.md
git commit -m "docs(adr-038): record trigger_run deferral for Phase 2 (ADR-038 P2)"
```

---

## Task 5: Verification and closeout

**Files:**
- Modify: `docs/design/ADR-038-Agentic-Operator-MCP/IMP-ADR038-02-gated-actions.md`

- Completed: **Step 1: Full action + smoke test run**

```bash
PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest tests/unit/test_mcp_actions.py tests/unit/test_mcp_tools.py tests/smoke/test_mcp_server.py -v
```
Verified: all action and smoke tests passed.

- Completed: **Step 2: Static checks**

```bash
PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD /opt/anaconda3/bin/python3 -m ruff check apps/mcp/ tests/unit/test_mcp_actions.py
PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m compileall apps/mcp
git diff --check
```
Verified: static checks were clean.

- Completed: **Step 3: Manual safety review (read the diff, confirm each)**

- The dry-run branch of BOTH tools performs no mutation and no `emit` (audit only on execute).
- `confirm=True` is the only path that calls `db_rollback_session(dry_run=False)` / `commit_session(...)`.
- Confirmed success paths attempt one best-effort audit event (`SessionRolledBack` / `SessionCommitted`); failure paths are not guaranteed to persist audit output.
- No tool returns `config.py`/secret values.
- The rollback tool passes `force` through unchanged (it does not silently force; the committed-session guard stays in `db_rollback_session`).

- Completed: **Step 4: Set IMP status + commit**

Set this file's header to `**Status:** Completed`.

```bash
git add docs/design/ADR-038-Agentic-Operator-MCP/IMP-ADR038-02-gated-actions.md
git commit -m "docs(adr-038): mark IMP-ADR038-02 complete (ADR-038 P2)"
```

- Completed: **Step 5: Post-task review (per CLAUDE.md)**

- Tests: the behavior worth pinning (dry-run-no-mutation, confirm-executes-and-audits, error-degrades) is covered by `test_mcp_actions.py`. ✓
- Docs: ADR amended; `docs/handbook/en/developer/mcp-server.md` and `docs/handbook/zh/developer/mcp-server.md` now document the current 10-tool MCP surface. No CLI/config/env changes were needed. ✓
- Outcome: the closeout summary explicitly records why additional workflow/README/test changes were unnecessary. ✓

---

## Self-review checklist (author)

- **Spec coverage:** D4 gate (dry-run/confirm/auth/audit) → Tasks 1-2; D1 thin-adapter → both tools wrap existing services; D5 no-secrets/stdio → no auth added, no config returned; `trigger_run` deferral → Task 4. ✓
- **No placeholders:** every code/test step has complete code. ✓
- **Type/name consistency:** `tool_rollback_session` / `tool_commit_session` / `_emit_audit` used identically across tasks; event types `SessionRolledBack` / `SessionCommitted` consistent; `confirm` keyword consistent. ✓
- **Known verification points covered:** `db_rollback_session` kwargs (Task 1 Step 1), `CommitRequest`/`pending_session_stats` (Task 2 Step 1), and the smoke-test tool-count assertion (Task 3 Step 3) were all checked against current code during implementation.
