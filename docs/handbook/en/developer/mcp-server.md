# MCP Server

ADR-038 Phase 1 introduces a read-only [FastMCP](https://github.com/jlowin/fastmcp) server at `apps/mcp/`, the third adapter over the service layer — alongside the CLI (`apps/cli/`) and the REST API (`apps/api/`). The current MCP surface ships 10 tools: eight read-only operators plus two gated actions (`rollback_session`, `commit_session`); `trigger_run` remains deferred.

## Running the Server

```bash
python -m apps.mcp.server
```

The server uses stdio transport (the MCP default). The `mcp` package must be installed:

```bash
pip install -r requirements.txt
```

## Connecting a Local Agent (stdio)

Add an entry to your MCP client configuration (e.g. Claude Desktop `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "javdb-autospider": {
      "command": "python3",
      "args": ["-m", "apps.mcp.server"],
      "cwd": "/path/to/JAVDB_AutoSpider_CICD"
    }
  }
}
```

The server name `javdb-autospider` matches the `FastMCP("javdb-autospider")` declaration in `apps/mcp/server.py`.

## Current Tool Surface

| Tool | Kind | What it answers |
|------|------|----------------|
| `get_capabilities` | read-only | Deployment capability and backend version |
| `get_session` | read-only | Lifecycle detail + write counts for a pipeline session |
| `list_incidents` | read-only | Recent operational incidents (ADR-026), optional status filter |
| `get_incident` | read-only | Full detail for one operational incident |
| `query_events` | read-only | Pipeline event timeline (ADR-036); reports availability if not built |
| `diagnose_run` | read-only | Read-only AI diagnosis of a run/incident (ADR-026) |
| `list_runs` | read-only | Recent task runs and the next scheduled run |
| `search_history` | read-only | Search local movie history ("do I have X?") |
| `rollback_session` | gated | Dry-run-by-default rollback; requires `confirm=true` to execute |
| `commit_session` | gated | Dry-run-by-default commit; requires `confirm=true` to execute |

The two gated tools are dry-run by default. With `confirm=false`, they return a
preview and do not mutate anything. With `confirm=true`, they execute the action
and attempt to emit a best-effort audit event.

## Return-Shape Conventions

Tools that probe a capability that may be absent return `{"available": false, "reason": ...}` — for example, `query_events` before ADR-036 is built will return this shape.

Tools that attempted an operation that failed return `{"error": ..., "detail": ...}` — for example, if `list_runs` cannot reach the task service.

This is a deliberate distinction: `available: false` means the capability is not present in this deployment; `error` means the capability exists but the call failed at runtime.

## Phase 2 Scope

`rollback_session` and `commit_session` are the shipped Phase 2 gated actions. Both are guarded by a dry-run preview, an explicit `confirm` parameter, reused auth, and best-effort audit emission on confirmed calls. `trigger_run` remains deferred because there is no existing Python service to thin-adapt, it would require new GitHub `workflow_dispatch` code, and operators already have the GitHub UI / TS Worker routes. The tool is not part of the current surface.

## Related Design Record

[ADR-038 — Agentic Operator MCP Surface](../../../design/ADR-038-Agentic-Operator-MCP/ADR-038-agentic-operator-mcp-surface.md)
