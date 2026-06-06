"""Shared OpsIncident serialization for the ADR-038 MCP tools.

Mirrors the API's apps/api/routers/diagnostics.py::_ops_record_to_schema field
set so the MCP surface and the REST API expose the same incident vocabulary.
"""

from __future__ import annotations

import json
from typing import Any


def json_list_field(raw: str | None) -> list:
    """Parse a JSON-array string column to a list; return [] on missing/invalid."""
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def incident_detail(rec: Any) -> dict:
    """Full OpsIncidentRecord -> JSON-able dict (parsed findings/causes/etc.)."""
    return {
        "incident_id": rec.incident_id,
        "trigger_source": rec.trigger_source,
        "run_id": rec.run_id,
        "run_attempt": rec.run_attempt,
        "session_id": rec.session_id,
        "incident_type": rec.incident_type,
        "status": rec.status,
        "persistence_status": rec.persistence_status,
        "model_version": rec.model_version,
        "detector_version": rec.detector_version,
        "confidence": rec.confidence,
        "confirmed_findings": json_list_field(rec.confirmed_findings_json),
        "likely_causes": json_list_field(rec.likely_causes_json),
        "unknowns": json_list_field(rec.unknowns_json),
        "recommended_next_actions": json_list_field(rec.recommended_next_actions_json),
        "unsafe_actions": json_list_field(rec.unsafe_actions_json),
        "evidence_refs": json_list_field(rec.evidence_refs_json),
        "created_at": rec.created_at,
        "updated_at": rec.updated_at,
        "resolved_at": rec.resolved_at,
    }
