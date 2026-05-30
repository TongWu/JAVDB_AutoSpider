# IMP-ADR038-01: Read-Only MCP Surface (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Implementation may also load `anthropic-skills:mcp-builder` for FastMCP idioms.

**Related:** [ADR-038](ADR-038-agentic-operator-mcp-surface.md) (umbrella) — this is **Phase 1** of three.

**Goal:** A `apps/mcp/` Python FastMCP server (stdio) exposing read-only operator tools — a thin third adapter over the existing `apps/api/services/` + repos — so an agent can observe and diagnose the system conversationally.

**Architecture:** Each tool's logic lives in a plain, unit-testable function in `apps/mcp/tools/` that calls an existing service/repo and returns a JSON-able dict; `apps/mcp/server.py` registers them on a `FastMCP` instance and runs over stdio. No mutations. Tools backed by tables from sibling ADRs (events/outcomes/drift — ADR-036/033/035) degrade gracefully when those tables are absent, so this lands before they do and lights up automatically once they exist.

**Tech Stack:** Python 3, `mcp` (FastMCP), `pytest`. Reuses `build_capabilities`, `SessionsRepo`, `OpsIncidentRepo`, and the ADR-026 diagnosis service.

**ADR-015 discipline:** tools CALL services/repos; they never reimplement a query. Where a needed read lacks a function, add it to the service layer (shared with the API), not the MCP adapter.

---

## File Structure

| Path | Create/Modify | Responsibility |
| --- | --- | --- |
| `requirements.txt` | Modify | Add the `mcp` dependency |
| `apps/mcp/__init__.py` | Create | Package marker |
| `apps/mcp/tools/__init__.py` | Create | Tool re-exports |
| `apps/mcp/tools/observe.py` | Create | `get_capabilities`, `get_session`, `list_incidents`, `get_incident`, `query_events` (graceful) |
| `apps/mcp/tools/diagnose.py` | Create | `diagnose_run` (reuse ADR-026) |
| `apps/mcp/server.py` | Create | `FastMCP` instance + tool registration + stdio `main()` |
| `tests/unit/test_mcp_tools.py` | Create | Unit tests on the plain tool functions |
| `tests/smoke/test_mcp_server.py` | Create | Server imports + registers tools |
| `CONTEXT.md`, `docs/handbook/en/developer/` | Modify | Domain terms + MCP usage doc |

**Naming contract (verbatim):** plain functions `tool_get_capabilities() -> dict`,
`tool_get_session(session_id) -> dict`, `tool_list_incidents(status=None, limit=50) -> list[dict]`,
`tool_get_incident(incident_id) -> dict | None`, `tool_query_events(session_id=None, limit=100) -> dict`,
`tool_diagnose_run(run_id=None, run_attempt=None, session_id=None, workflow_name=None, workflow_result=None) -> dict`.
`server.py` registers each as an `@mcp.tool()`.

> **Phase-2-gated:** no mutating tools (`trigger_run`/`rollback_session`/`commit_session`);
> no remote transport; no TS Worker MCP. `list_runs` / `search_history` /
> `get_acquisition_outcomes` / `get_drift` follow the same thin-adapter pattern and are
> added incrementally (Task 6 wires `list_runs` + `search_history`; the ADR-033/035
> tools land with their ADRs via the graceful pattern shown for `query_events`).

---

## Task 1: Add the `mcp` dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add the dependency**

Append to `requirements.txt`:

```
# ADR-038: Agentic operator MCP surface (FastMCP server)
mcp>=1.2.0
```

- [ ] **Step 2: Install + verify import**

Run: `pip install "mcp>=1.2.0" && python3 -c "from mcp.server.fastmcp import FastMCP; print('fastmcp ok')"`
Expected: `fastmcp ok`

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "build(deps): add mcp (FastMCP) for ADR-038 MCP surface"
```

---

## Task 2: `observe` tools — capabilities + sessions

**Files:**
- Create: `apps/mcp/__init__.py`, `apps/mcp/tools/__init__.py`, `apps/mcp/tools/observe.py`
- Test: `tests/unit/test_mcp_tools.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_mcp_tools.py
from apps.mcp.tools.observe import tool_get_capabilities, tool_get_session


def test_get_capabilities_returns_dict():
    caps = tool_get_capabilities()
    assert isinstance(caps, dict)
    # build_capabilities always includes a deployment/version-ish field
    assert caps  # non-empty


def test_get_session_unknown_returns_not_found():
    out = tool_get_session("nonexistent-session-id")
    assert out["found"] is False
```

> The autouse `_isolate_sqlite` fixture gives these tools a real, empty schema to
> read from, so `get_session` finds nothing rather than erroring.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_mcp_tools.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the package + observe tools**

```python
# apps/mcp/__init__.py
"""Agentic operator MCP surface (ADR-038 Phase 1) — read-only."""
```

```python
# apps/mcp/tools/__init__.py
"""MCP tool functions (plain, unit-testable; registered in apps/mcp/server.py)."""
```

```python
# apps/mcp/tools/observe.py
"""Read-only observe tools — thin adapters over existing services/repos (ADR-038 D1)."""

from __future__ import annotations

from typing import Any


def tool_get_capabilities() -> dict:
    """Deployment capability + version surface (same as GET /api/capabilities)."""
    from apps.api.routers.capabilities import build_capabilities
    caps = build_capabilities()
    # CapabilitiesResponse is a pydantic model; return a plain dict.
    return caps.model_dump() if hasattr(caps, "model_dump") else dict(caps)


def tool_get_session(session_id: str) -> dict:
    """Session lifecycle detail (same data as GET /api/sessions/{id})."""
    import javdb.storage.db as _db
    from javdb.storage.repos.sessions_repo import SessionsRepo
    with _db.get_db(_db.REPORTS_DB_PATH) as conn:
        repo = SessionsRepo(conn)
        row = repo.get(session_id)
        if row is None:
            return {"found": False, "session_id": session_id}
        movies, torrents = repo.get_writes(session_id)
    return {"found": True, "session_id": session_id, "session": _as_dict(row),
            "movie_writes": len(movies), "torrent_writes": len(torrents)}


def _as_dict(row: Any) -> dict:
    try:
        return {k: row[k] for k in row.keys()}
    except Exception:
        return dict(row) if isinstance(row, dict) else {"value": str(row)}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_mcp_tools.py -v`
Expected: PASS (2 passed)

> If `SessionsRepo.get` requires a different call shape, confirm with
> `grep -nE "def get|def get_writes|class SessionsRepo" javdb/storage/repos/sessions_repo.py`
> and adjust — the test pins the contract.

- [ ] **Step 5: Commit**

```bash
git add apps/mcp/__init__.py apps/mcp/tools/__init__.py apps/mcp/tools/observe.py tests/unit/test_mcp_tools.py
git commit -m "feat(mcp): add capabilities + session observe tools (ADR-038)"
```

---

## Task 3: `observe` tools — incidents + events (graceful)

**Files:**
- Modify: `apps/mcp/tools/observe.py`
- Test: extend `tests/unit/test_mcp_tools.py`

Incidents reuse the ADR-026 `OpsIncidentRepo` (`list(status=, run_id=, session_id=, limit=)`,
`get(incident_id)`). Events read `PipelineEvent` (ADR-036) and **degrade gracefully**
when that table is absent.

- [ ] **Step 1: Add the failing tests**

```python
# add to tests/unit/test_mcp_tools.py
from apps.mcp.tools.observe import tool_list_incidents, tool_query_events


def test_list_incidents_returns_list():
    assert isinstance(tool_list_incidents(limit=5), list)


def test_query_events_graceful_when_table_absent():
    out = tool_query_events(limit=10)
    assert "available" in out  # degrades to {"available": False, ...} pre-ADR-036
```

- [ ] **Step 2: Run to verify FAIL**

Run: `pytest tests/unit/test_mcp_tools.py -k "incidents or events" -v`
Expected: FAIL — functions undefined

- [ ] **Step 3: Add the tools** to `apps/mcp/tools/observe.py`

```python
def tool_list_incidents(status: str | None = None, limit: int = 50) -> list[dict]:
    """Operational incidents (ADR-026 OpsIncidents), most recent first."""
    import javdb.storage.db as _db
    from javdb.storage.repos.ops_incident_repo import OpsIncidentRepo
    with _db.get_db(_db.REPORTS_DB_PATH) as conn:
        records = OpsIncidentRepo(conn).list(status=status, limit=limit)
    return [_incident_summary(r) for r in records]


def tool_get_incident(incident_id: str) -> dict | None:
    import javdb.storage.db as _db
    from javdb.storage.repos.ops_incident_repo import OpsIncidentRepo
    with _db.get_db(_db.REPORTS_DB_PATH) as conn:
        rec = OpsIncidentRepo(conn).get(incident_id)
    return None if rec is None else _incident_summary(rec)


def _incident_summary(rec: Any) -> dict:
    return {
        "incident_id": getattr(rec, "incident_id", None),
        "incident_type": getattr(rec, "incident_type", None),
        "status": getattr(rec, "status", None),
        "confidence": getattr(rec, "confidence", None),
        "session_id": getattr(rec, "session_id", None),
        "created_at": getattr(rec, "created_at", None),
    }


def tool_query_events(session_id: str | None = None, limit: int = 100) -> dict:
    """Pipeline event timeline (ADR-036 PipelineEvent). Degrades when absent."""
    import javdb.storage.db as _db
    try:
        with _db.get_db(_db.REPORTS_DB_PATH) as conn:
            if session_id:
                rows = conn.execute(
                    "SELECT seq, event_type, entity_type, entity_id, created_at "
                    "FROM PipelineEvent WHERE session_id = ? ORDER BY seq LIMIT ?",
                    [session_id, limit],
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT seq, event_type, entity_type, entity_id, created_at "
                    "FROM PipelineEvent ORDER BY seq DESC LIMIT ?", [limit],
                ).fetchall()
        events = [{"seq": r[0], "event_type": r[1], "entity_type": r[2],
                   "entity_id": r[3], "created_at": r[4]} for r in rows]
        return {"available": True, "events": events}
    except Exception:
        return {"available": False, "reason": "PipelineEvent table not present (ADR-036 not built)"}
```

- [ ] **Step 4: Run to verify PASS**

Run: `pytest tests/unit/test_mcp_tools.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add apps/mcp/tools/observe.py tests/unit/test_mcp_tools.py
git commit -m "feat(mcp): add incident + (graceful) event observe tools (ADR-038)"
```

---

## Task 4: `diagnose_run` tool (reuse ADR-026)

**Files:**
- Create: `apps/mcp/tools/diagnose.py`
- Test: extend `tests/unit/test_mcp_tools.py`

Reuse the ADR-026 read-only flow the CLI uses: `collect_incident_bundle(...)` →
`diagnose_incident(...)`.

- [ ] **Step 1: Confirm the ADR-026 entry signatures**

Run: `grep -nE "def collect_incident_bundle|def diagnose_incident" javdb/ops/diagnosis/collectors.py javdb/ops/diagnosis/service.py`
Record the parameter names; adjust the call below to match.

- [ ] **Step 2: Add the failing test**

```python
# add to tests/unit/test_mcp_tools.py
from apps.mcp.tools.diagnose import tool_diagnose_run


def test_diagnose_run_returns_structured_result():
    out = tool_diagnose_run(workflow_name="DailyIngestion", workflow_result="failure")
    assert isinstance(out, dict)
    assert "incident_type" in out or "error" in out
```

- [ ] **Step 3: Run to verify FAIL**

Run: `pytest tests/unit/test_mcp_tools.py -k diagnose -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: Write the diagnose tool**

```python
# apps/mcp/tools/diagnose.py
"""Read-only diagnosis tool — reuses the ADR-026 flow (ADR-038 D3)."""

from __future__ import annotations


def tool_diagnose_run(run_id: str | None = None, run_attempt: int | None = None,
                      session_id: str | None = None, workflow_name: str | None = None,
                      workflow_result: str | None = None) -> dict:
    """Collect read-only evidence and run the ADR-026 detector+model diagnosis."""
    from javdb.ops.diagnosis.collectors import collect_incident_bundle
    from javdb.ops.diagnosis.service import diagnose_incident
    try:
        bundle = collect_incident_bundle(
            run_id=run_id, run_attempt=run_attempt, session_id=session_id,
            workflow_name=workflow_name, workflow_result=workflow_result,
            trigger_source="mcp",
        )
        record = diagnose_incident(bundle)
        return {
            "incident_id": getattr(record, "incident_id", None),
            "incident_type": getattr(record, "incident_type", None),
            "confidence": getattr(record, "confidence", None),
            "persistence_status": getattr(record, "persistence_status", None),
        }
    except TypeError:
        # Signature drift — Step 1's grep is the guard; surface it rather than crash.
        return {"error": "collect_incident_bundle/diagnose_incident signature mismatch; "
                         "confirm via grep (Task 4 Step 1)"}
```

- [ ] **Step 5: Run to verify PASS**

Run: `pytest tests/unit/test_mcp_tools.py -k diagnose -v`
Expected: PASS (1)

- [ ] **Step 6: Commit**

```bash
git add apps/mcp/tools/diagnose.py tests/unit/test_mcp_tools.py
git commit -m "feat(mcp): add diagnose_run tool reusing ADR-026 (ADR-038)"
```

---

## Task 5: FastMCP server + stdio

**Files:**
- Create: `apps/mcp/server.py`
- Test: `tests/smoke/test_mcp_server.py`

- [ ] **Step 1: Write the failing smoke test**

```python
# tests/smoke/test_mcp_server.py
import asyncio


def test_server_registers_read_only_tools():
    from apps.mcp.server import mcp
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert {"get_capabilities", "get_session", "list_incidents",
            "diagnose_run", "query_events"}.issubset(names)
    # Phase 1 is read-only: no mutating tool names.
    assert not ({"trigger_run", "rollback_session", "commit_session"} & names)
```

- [ ] **Step 2: Run to verify FAIL**

Run: `pytest tests/smoke/test_mcp_server.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the server**

```python
# apps/mcp/server.py
"""FastMCP server exposing the read-only operator surface (ADR-038 Phase 1).

Run:  python -m apps.mcp.server   (stdio transport)
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from apps.mcp.tools.observe import (
    tool_get_capabilities, tool_get_session,
    tool_list_incidents, tool_get_incident, tool_query_events,
)
from apps.mcp.tools.diagnose import tool_diagnose_run

mcp = FastMCP("javdb-autospider")


@mcp.tool()
def get_capabilities() -> dict:
    """Deployment capability and version of the JAVDB AutoSpider backend."""
    return tool_get_capabilities()


@mcp.tool()
def get_session(session_id: str) -> dict:
    """Lifecycle detail and write counts for a pipeline session."""
    return tool_get_session(session_id)


@mcp.tool()
def list_incidents(status: str | None = None, limit: int = 50) -> list[dict]:
    """Recent operational incidents (ADR-026), optionally filtered by status."""
    return tool_list_incidents(status=status, limit=limit)


@mcp.tool()
def get_incident(incident_id: str) -> dict | None:
    """Full detail for one operational incident."""
    return tool_get_incident(incident_id)


@mcp.tool()
def query_events(session_id: str | None = None, limit: int = 100) -> dict:
    """Pipeline event timeline (ADR-036); reports availability if not yet built."""
    return tool_query_events(session_id=session_id, limit=limit)


@mcp.tool()
def diagnose_run(run_id: str | None = None, run_attempt: int | None = None,
                 session_id: str | None = None, workflow_name: str | None = None,
                 workflow_result: str | None = None) -> dict:
    """Read-only AI diagnosis of a run/incident (ADR-026)."""
    return tool_diagnose_run(run_id=run_id, run_attempt=run_attempt, session_id=session_id,
                             workflow_name=workflow_name, workflow_result=workflow_result)


def main() -> None:
    mcp.run()  # stdio transport by default


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify PASS**

Run: `pytest tests/smoke/test_mcp_server.py -v`
Expected: PASS (1). If `mcp.list_tools()` is not async in the installed `mcp`
version, adjust the test to the version's API (the assertion on tool names is the goal).

- [ ] **Step 5: Commit**

```bash
git add apps/mcp/server.py tests/smoke/test_mcp_server.py
git commit -m "feat(mcp): add FastMCP read-only server over stdio (ADR-038 Phase 1)"
```

---

## Task 6: Wire `list_runs` + `search_history`, docs, full gate

**Files:**
- Modify: `apps/mcp/tools/observe.py`, `apps/mcp/server.py`
- Modify: `CONTEXT.md`, `docs/handbook/en/developer/` (MCP usage)

- [ ] **Step 1: Confirm the backing service functions**

Run:
```bash
grep -nE "def list|def .*tasks|def .*jobs" apps/api/services/task_service.py | head
grep -nE "def search|def .*history|class " apps/api/services/explore_service.py | head
```
Record the exact function + signature for "list recent runs/tasks" and "search history".

- [ ] **Step 2: Add `tool_list_runs` + `tool_search_history`** to `observe.py`, each a thin call into the confirmed service function, returning a JSON-able dict/list (mirror the shape of Task 2's tools; degrade gracefully if a call raises). Register `list_runs` and `search_history` in `server.py` with `@mcp.tool()` wrappers and docstrings.

- [ ] **Step 3: Extend the smoke test** to assert `list_runs` and `search_history` are registered; run `pytest tests/smoke/test_mcp_server.py -v` → PASS.

- [ ] **Step 4: Update CONTEXT.md** — add ADR-038 terms verbatim: *MCP adapter*, *Read-only tool*, *Gated action*.

- [ ] **Step 5: Document MCP usage** — add a developer doc page on running the server
  (`python -m apps.mcp.server`) and connecting it to a local agent (stdio), listing the
  Phase-1 read-only tools; note Phase-2 gated actions are not yet available.

- [ ] **Step 6: Full gate**

Run: `pytest tests/unit/test_mcp_tools.py tests/smoke/test_mcp_server.py -v`
Expected: all PASS.

- [ ] **Step 7: Seam-discipline check** — confirm no SQL/business logic was duplicated
  that already exists in a service:

Run: `grep -rnE "INSERT|UPDATE|DELETE" apps/mcp/`
Expected: no output (read-only adapter; the one raw SELECT in `query_events` is a
documented read for the ADR-036 table that has no service function yet).

- [ ] **Step 8: Commit**

```bash
git add apps/mcp/ CONTEXT.md docs/handbook tests
git commit -m "feat(mcp): wire list_runs/search_history + docs for ADR-038 Phase 1"
```

---

## Plan Self-Review

**Spec coverage (ADR-038 Phase 1 row + D-decisions):**
- Third adapter over the service layer, no reimplementation (D1) → all tools call
  services/repos; Task 6 Step 7 guards it. ✓
- Python FastMCP, stdio (D2) → Tasks 1, 5. ✓
- Read-only tool taxonomy (D3) → Tasks 2-6 (capabilities, session, incidents, events,
  diagnose, runs, history); ADR-033/035 tools follow the `query_events` graceful pattern. ✓
- Gated actions deferred (D4) → smoke test asserts no mutating tool names. ✓
- Safety: read-only, no secrets tools (D5) → no mutation; no config/secret tool added. ✓
- No TS Worker MCP (D6) → Python only. ✓
- Docs (CONTEXT.md + developer MCP page) → Task 6. ✓

**Type consistency:** `tool_get_capabilities`, `tool_get_session`, `tool_list_incidents`,
`tool_get_incident`, `tool_query_events`, `tool_diagnose_run` are defined as plain
functions and wrapped 1:1 by `@mcp.tool()` registrations of matching names across Tasks 2-6.

**Service-signature confirmations (grep steps, not blind calls):** Task 2 Step 4
(`SessionsRepo`), Task 4 Step 1 (ADR-026 entry), Task 6 Step 1 (`task_service` /
`explore_service`). Each tool is a thin wrapper whose exact call is confirmed before wiring.

**Forward-compat:** `query_events` (ADR-036) degrades to `{"available": False}` until the
table exists; the same pattern carries `get_acquisition_outcomes` (ADR-033) and `get_drift`
(ADR-035) when those land — the MCP surface gains tools as the data does.

**Install dependency:** Task 1 requires `pip install mcp>=1.2.0`; the FastMCP API
(`mcp.server.fastmcp.FastMCP`, `mcp.run()`, `list_tools()`) should be confirmed against
the installed version (Task 1 Step 2 / Task 5 Step 4 note the adjust-points).
