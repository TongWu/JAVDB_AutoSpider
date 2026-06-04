"""Deterministic feature extraction for ADR-026 incident analytics."""

from __future__ import annotations

import json
import re
from typing import Iterable

from javdb.ops.diagnosis.models import IncidentBundle, OpsIncidentFeatures, OpsIncidentRecord, utc_now_iso

FEATURE_VERSION = "ops-incident-features-v1"
_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{2,}")
_STOPWORDS = {
    "and",
    "are",
    "before",
    "cannot",
    "the",
    "this",
    "with",
    "without",
}


def _json_load_list(raw: str) -> list:
    value = json.loads(raw or "[]")
    return value if isinstance(value, list) else []


def _tokens(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        for match in _TOKEN_RE.findall(value.lower()):
            if match in _STOPWORDS or match in seen:
                continue
            seen.add(match)
            ordered.append(match)
    return ordered[:80]


def _evidence_kinds(raw: str) -> list[str]:
    kinds: list[str] = []
    seen: set[str] = set()
    for item in _json_load_list(raw):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        if kind and kind not in seen:
            seen.add(kind)
            kinds.append(kind)
    return kinds


def build_incident_features(
    record: OpsIncidentRecord,
    *,
    bundle: IncidentBundle | None = None,
) -> OpsIncidentFeatures:
    findings = [str(item) for item in _json_load_list(record.confirmed_findings_json)]
    causes = [str(item) for item in _json_load_list(record.likely_causes_json)]
    unknowns = [str(item) for item in _json_load_list(record.unknowns_json)]
    actions = [str(item) for item in _json_load_list(record.recommended_next_actions_json)]
    unsafe_actions = [str(item) for item in _json_load_list(record.unsafe_actions_json)]
    evidence_kinds = _evidence_kinds(record.evidence_refs_json)
    now = utc_now_iso()
    categorical = {
        "incident_type": record.incident_type,
        "status": record.status,
        "confidence": record.confidence,
        "trigger_source": record.trigger_source,
        "persistence_status": record.persistence_status,
        "model_version": record.model_version,
        "detector_version": record.detector_version,
    }
    return OpsIncidentFeatures(
        incident_id=record.incident_id,
        incident_type=record.incident_type,
        status=record.status,
        confidence=record.confidence,
        workflow_name=bundle.workflow_name if bundle is not None else None,
        run_id=record.run_id,
        run_attempt=record.run_attempt,
        session_id=record.session_id,
        feature_version=FEATURE_VERSION,
        categorical_features_json=json.dumps(categorical, separators=(",", ":"), ensure_ascii=False),
        text_tokens_json=json.dumps(_tokens([*findings, *causes, *unknowns, *actions]), separators=(",", ":")),
        unsafe_action_tokens_json=json.dumps(_tokens(unsafe_actions), separators=(",", ":")),
        evidence_kinds_json=json.dumps(evidence_kinds, separators=(",", ":")),
        created_at=now,
        updated_at=now,
    )
