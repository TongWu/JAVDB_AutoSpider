# apps/mcp/tools/diagnose.py
"""Read-only diagnosis tool — reuses the ADR-026 flow (ADR-038 D3)."""

from __future__ import annotations


def tool_diagnose_run(run_id: str | None = None, run_attempt: int | None = None,
                      session_id: str | None = None, workflow_name: str | None = None,
                      workflow_result: str | None = None) -> dict:
    """Collect read-only evidence and run the ADR-026 detector+model diagnosis.

    Read-only: builds the incident diagnosis but does NOT persist it."""
    from javdb.ops.diagnosis.collectors import collect_incident_bundle
    from javdb.ops.diagnosis.service import run_diagnosis
    from apps.mcp.tools._incident import incident_detail
    try:
        bundle = collect_incident_bundle(
            run_id=run_id, run_attempt=run_attempt, session_id=session_id,
            workflow_name=workflow_name, workflow_result=workflow_result,
            trigger_source="mcp",
        )
        record = run_diagnosis(bundle)
        return incident_detail(record)
    except Exception as exc:  # noqa: BLE001 — operator-facing tool must degrade, never crash the agent
        return {"error": "diagnosis failed", "detail": str(exc)}
