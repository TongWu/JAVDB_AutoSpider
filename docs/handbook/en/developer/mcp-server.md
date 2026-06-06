# MCP Server

ADR-038 Phase 1 introduces a read-only [FastMCP](https://github.com/jlowin/fastmcp) server at `apps/mcp/`, the third adapter over the service layer — alongside the CLI (`apps/cli/`) and the REST API (`apps/api/`). Phase 1 exposes eight read-only operator tools; mutating "gated actions" (`trigger_run`, `rollback_session`, `commit_session`) are deferred to Phase 2.

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

## Phase-1 Read-Only Tools

| Tool | What it answers |
|------|----------------|
| `get_capabilities` | Deployment capability and backend version |
| `get_session` | Lifecycle detail + write counts for a pipeline session |
| `list_incidents` | Recent operational incidents (ADR-026), optional status filter |
| `get_incident` | Full detail for one operational incident |
| `query_events` | Pipeline event timeline (ADR-036); reports availability if not built |
| `diagnose_run` | Read-only AI diagnosis of a run/incident (ADR-026) |
| `list_runs` | Recent task runs and the next scheduled run |
| `search_history` | Search local movie history ("do I have X?") |

## Return-Shape Conventions

Tools that probe a capability that may be absent return `{"available": false, "reason": ...}` — for example, `query_events` before ADR-036 is built will return this shape.

Tools that attempted an operation that failed return `{"error": ..., "detail": ...}` — for example, if `list_runs` cannot reach the task service.

This is a deliberate distinction: `available: false` means the capability is not present in this deployment; `error` means the capability exists but the call failed at runtime.

## Phase 2 (Not Yet Available)

Mutating "gated actions" — `trigger_run`, `rollback_session`, `commit_session` — will land in Phase 2. Each will be guarded by a dry-run preview, an explicit `confirm` parameter, reused auth, and an audit event. None of these tools exist in Phase 1.

## Related Design Record

[ADR-038 — Agentic Operator MCP Surface](../../../design/ADR-038-Agentic-Operator-MCP/ADR-038-agentic-operator-mcp-surface.md)
