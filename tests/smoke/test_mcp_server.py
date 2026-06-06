# tests/smoke/test_mcp_server.py
import asyncio


def test_server_registers_read_only_tools():
    from apps.mcp.server import mcp
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    expected = {
        "get_capabilities", "get_session", "list_incidents", "get_incident",
        "query_events", "diagnose_run", "list_runs", "search_history",
    }
    assert names == expected  # exact Phase-1 read-only surface: no more, no fewer
    # Phase 1 is read-only: explicitly no mutating tools.
    assert not ({"trigger_run", "rollback_session", "commit_session"} & names)
