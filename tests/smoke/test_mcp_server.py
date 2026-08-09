# tests/smoke/test_mcp_server.py
import asyncio


def test_server_registers_phase_2_tools():
    from apps.mcp.server import mcp
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    expected = {
        "get_capabilities", "get_session", "list_incidents", "get_incident",
        "query_events", "diagnose_run", "list_runs", "search_history",
        "rollback_session", "commit_session",
    }
    assert names == expected  # exact Phase 2 surface: no more, no fewer
    assert "trigger_run" not in names
