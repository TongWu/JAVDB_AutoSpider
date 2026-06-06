"""Read-only observe tools — thin adapters over existing services/repos (ADR-038 D1)."""

from __future__ import annotations

import sqlite3
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
    """Best-effort row -> dict for heterogeneous row shapes."""
    import dataclasses
    if dataclasses.is_dataclass(row) and not isinstance(row, type):
        return dataclasses.asdict(row)
    try:
        return {k: row[k] for k in row.keys()}
    except Exception:
        return dict(row) if isinstance(row, dict) else {"value": str(row)}


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
    from apps.mcp.tools._incident import incident_detail
    with _db.get_db(_db.REPORTS_DB_PATH) as conn:
        rec = OpsIncidentRepo(conn).get(incident_id)
    return None if rec is None else incident_detail(rec)


def _incident_summary(rec: Any) -> dict:
    """Compact incident projection for list views."""
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
        events = [{"seq": r["seq"], "event_type": r["event_type"],
                   "entity_type": r["entity_type"], "entity_id": r["entity_id"],
                   "created_at": r["created_at"]} for r in rows]
        return {"available": True, "events": events}
    except Exception as exc:  # noqa: BLE001 — operator-facing tool must degrade, never crash the agent
        if isinstance(exc, sqlite3.OperationalError) and "no such table" in str(exc).lower():
            return {"available": False, "reason": "PipelineEvent table not present (ADR-036 not built)"}
        return {"available": False, "reason": f"PipelineEvent query unavailable: {exc}"}


def tool_list_runs(limit: int = 50) -> dict:
    """Recent pipeline task runs + next schedule (same data as GET /api/tasks)."""
    try:
        from apps.api.services import task_service
        return task_service.list_tasks_payload(limit=limit, username="mcp")
    except Exception as exc:  # noqa: BLE001 — operator-facing tool must degrade, never crash the agent
        return {"error": "list_runs failed", "detail": str(exc)}


def tool_search_history(q: str | None = None, limit: int = 50) -> dict:
    """Search local MovieHistory ('do I have X?'); read-only keyset search."""
    try:
        from javdb.storage.repos.history_repo import HistoryRepo
        items, next_cursor, total = HistoryRepo().search_movies(q=q, limit=limit)
        return {"items": items, "next_cursor": next_cursor, "total": total}
    except Exception as exc:  # noqa: BLE001 — operator-facing tool must degrade, never crash the agent
        return {"error": "search_history failed", "detail": str(exc)}
