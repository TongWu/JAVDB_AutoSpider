"""Gated mutating operator actions with dry-run-by-default semantics.

Actions are thin adapters over existing services. Unless ``confirm=True``,
they return a preview with no mutation or audit side effect. Confirmed actions
execute the service and emit one best-effort audit event after completion.
"""

from __future__ import annotations

import json


def _emit_audit(event_type: str, session_id: str, payload: dict) -> None:
    """Emit an audit event without blocking an already-completed action."""
    try:
        from javdb.pipeline.events.store import emit

        emit(
            event_type,
            session_id=session_id,
            entity_type="session",
            entity_id=session_id,
            payload=json.dumps(payload, ensure_ascii=False, default=str),
        )
    except Exception:  # noqa: BLE001 - audit is intentionally best-effort
        pass


def tool_rollback_session(
    session_id: str,
    *,
    scope: str = "all",
    force: bool = False,
    confirm: bool = False,
) -> dict:
    """Preview or execute rollback through the existing storage service."""
    if not confirm:
        try:
            from javdb.storage.repos.session_lifecycle_repo import (
                SessionLifecycleRepo,
            )

            preview = SessionLifecycleRepo().rollback_session(
                session_id,
                dry_run=True,
                scope=scope,
                force=force,
            )
            return {
                "action": "rollback_session",
                "session_id": session_id,
                "confirmed": False,
                "dry_run": True,
                "would_affect": preview,
                "note": "Re-call with confirm=true to execute.",
            }
        except Exception as exc:  # noqa: BLE001 - operator tools must degrade
            return {
                "error": "rollback_session failed",
                "detail": str(exc),
            }

    try:
        from javdb.storage.repos.session_lifecycle_repo import SessionLifecycleRepo

        affected = SessionLifecycleRepo().rollback_session(
            session_id,
            dry_run=False,
            scope=scope,
            force=force,
        )
    except Exception as exc:  # noqa: BLE001 - action may be partially applied
        _emit_audit(
            "SessionRollbackFailed",
            session_id,
            {
                "scope": scope,
                "force": force,
                "error": str(exc),
            },
        )
        return {
            "error": "rollback_session failed",
            "detail": str(exc),
        }

    _emit_audit(
        "SessionRolledBack",
        session_id,
        {
            "scope": scope,
            "force": force,
            "affected": affected,
        },
    )
    return {
        "action": "rollback_session",
        "session_id": session_id,
        "confirmed": True,
        "dry_run": False,
        "affected": affected,
    }


def tool_commit_session(
    session_id: str,
    *,
    force: bool = False,
    drop_pending: bool = False,
    confirm: bool = False,
) -> dict:
    """Preview or execute commit through the existing storage service."""
    if not confirm:
        try:
            from apps.mcp.tools.observe import tool_get_session
            from javdb.storage.repos.history_repo import HistoryRepo

            session = tool_get_session(session_id)
            try:
                pending = HistoryRepo().pending_session_stats(session_id)
            except Exception:  # noqa: BLE001 - preview is best-effort
                pending = {}
            return {
                "action": "commit_session",
                "session_id": session_id,
                "confirmed": False,
                "dry_run": True,
                "force": force,
                "drop_pending": drop_pending,
                "session_found": bool(session.get("found")),
                (
                    "would_drop_pending"
                    if drop_pending
                    else "would_finalize_pending"
                ): pending,
                "note": "Re-call with confirm=true to execute.",
            }
        except Exception as exc:  # noqa: BLE001 - operator tools must degrade
            return {
                "error": "commit_session failed",
                "detail": str(exc),
            }

    try:
        from javdb.storage.sessions.commit import CommitRequest, commit_session

        result = commit_session(
            CommitRequest(
                session_id=session_id,
                force=force,
                drop_pending=drop_pending,
            )
        )
    except Exception as exc:  # noqa: BLE001 - action may be partially applied
        _emit_audit(
            "SessionCommitFailed",
            session_id,
            {
                "force": force,
                "drop_pending": drop_pending,
                "error": str(exc),
            },
        )
        return {
            "error": "commit_session failed",
            "detail": str(exc),
        }

    result_payload = {
        "new_state": result.new_state,
        "pending_dropped": result.pending_dropped,
    }
    _emit_audit(
        "SessionCommitted",
        session_id,
        {
            **result_payload,
            "force": force,
            "drop_pending": drop_pending,
        },
    )
    return {
        "action": "commit_session",
        "session_id": session_id,
        "confirmed": True,
        "dry_run": False,
        "result": {
            "session_id": result.session_id,
            **result_payload,
        },
    }
