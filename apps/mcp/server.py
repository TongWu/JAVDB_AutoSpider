# apps/mcp/server.py
"""FastMCP server exposing the read-only operator surface (ADR-038 Phase 1).

Run:  python -m apps.mcp.server   (stdio transport)
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from apps.mcp.tools.observe import (
    tool_get_capabilities, tool_get_session,
    tool_list_incidents, tool_get_incident, tool_query_events,
    tool_list_runs, tool_search_history,
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


@mcp.tool()
def list_runs(limit: int = 50) -> dict:
    """Recent pipeline task runs and the next scheduled run."""
    return tool_list_runs(limit=limit)


@mcp.tool()
def search_history(q: str | None = None, limit: int = 50) -> dict:
    """Search local movie history ('do I have X?'). Read-only."""
    return tool_search_history(q=q, limit=limit)


def main() -> None:
    mcp.run()  # stdio transport by default


if __name__ == "__main__":
    main()
