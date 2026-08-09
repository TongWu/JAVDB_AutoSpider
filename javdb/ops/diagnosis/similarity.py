"""Explainable incident similarity for ADR-026 Phase 2."""

from __future__ import annotations

import json

from javdb.ops.diagnosis.models import OpsIncidentFeatures, SimilarIncident


def _list(raw: str) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _categorical(raw: str) -> dict[str, str]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _overlap(left: list[str], right: list[str]) -> tuple[float, list[str]]:
    left_set = set(left)
    right_set = set(right)
    if not left_set or not right_set:
        return 0.0, []
    matched = sorted(left_set & right_set)
    union = left_set | right_set
    return len(matched) / len(union), matched


def score_similarity(target: OpsIncidentFeatures, candidate: OpsIncidentFeatures) -> SimilarIncident:
    score = 0.0
    reasons: list[str] = []

    if target.incident_type == candidate.incident_type:
        score += 0.35
        reasons.append("incident_type")
    if target.confidence == candidate.confidence:
        score += 0.05
        reasons.append("confidence")

    target_cat = _categorical(target.categorical_features_json)
    candidate_cat = _categorical(candidate.categorical_features_json)
    if target_cat.get("trigger_source") and target_cat.get("trigger_source") == candidate_cat.get("trigger_source"):
        score += 0.05
        reasons.append("trigger_source")

    text_score, text_matches = _overlap(_list(target.text_tokens_json), _list(candidate.text_tokens_json))
    if text_matches:
        score += 0.35 * text_score
        reasons.append("text_tokens:" + ",".join(text_matches[:5]))

    unsafe_score, unsafe_matches = _overlap(
        _list(target.unsafe_action_tokens_json),
        _list(candidate.unsafe_action_tokens_json),
    )
    if unsafe_matches:
        score += 0.15 * unsafe_score
        reasons.append("unsafe_actions:" + ",".join(unsafe_matches[:5]))

    evidence_score, evidence_matches = _overlap(_list(target.evidence_kinds_json), _list(candidate.evidence_kinds_json))
    if evidence_matches:
        score += 0.05 * evidence_score
        reasons.append("evidence_kinds:" + ",".join(evidence_matches[:5]))

    return SimilarIncident(
        incident_id=candidate.incident_id,
        score=round(min(score, 1.0), 4),
        matched_reasons=reasons,
    )


def rank_similar_incidents(
    target: OpsIncidentFeatures,
    candidates: list[OpsIncidentFeatures],
    *,
    limit: int = 5,
) -> list[SimilarIncident]:
    scored = [
        score_similarity(target, candidate)
        for candidate in candidates
        if candidate.incident_id != target.incident_id
    ]
    scored.sort(key=lambda item: (-item.score, item.incident_id))
    return [item for item in scored if item.score > 0][:limit]
