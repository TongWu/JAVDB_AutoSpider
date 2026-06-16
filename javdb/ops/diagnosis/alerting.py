"""Deterministic alert-trigger evaluation for ADR-026 Phase 4.

This module decides WHETHER an incident should alert. It does not deliver
notifications. Delivery is handled by ADR-039 NotifyPlugin dispatch, invoked
from the diagnosis service after a `fired` decision.
"""

from __future__ import annotations

import hashlib
from typing import Protocol

from javdb.ops.diagnosis.models import AlertDecision, OpsAlertPolicy, confidence_rank

_VALID_CONFIDENCE = {"low", "medium", "high"}


class IncidentLike(Protocol):
    incident_id: str
    incident_type: str
    confidence: str


def build_alert_id(incident_id: str) -> str:
    digest = hashlib.sha256(incident_id.encode("utf-8")).hexdigest()[:24]
    return f"opsalert_{digest}"


def evaluate_alert(
    record: IncidentLike,
    policies: list[OpsAlertPolicy],
    *,
    already_fired: bool,
) -> AlertDecision:
    """Return a pure alert decision for an incident record."""
    incident_id = record.incident_id
    incident_type = record.incident_type
    confidence = record.confidence
    alert_id = build_alert_id(incident_id)

    policy = next(
        (
            candidate
            for candidate in policies
            if candidate.incident_type == incident_type and candidate.enabled
        ),
        None,
    )

    if policy is None:
        return AlertDecision(
            alert_id=alert_id,
            incident_id=incident_id,
            policy_id=None,
            status="skipped",
            reason="No matching enabled policy for this incident type.",
        )

    if confidence not in _VALID_CONFIDENCE:
        return AlertDecision(
            alert_id=alert_id,
            incident_id=incident_id,
            policy_id=policy.policy_id,
            status="skipped",
            reason=f"Unknown incident confidence {confidence!r}; alert evaluation skipped.",
        )

    if policy.min_confidence not in _VALID_CONFIDENCE:
        return AlertDecision(
            alert_id=alert_id,
            incident_id=incident_id,
            policy_id=policy.policy_id,
            status="skipped",
            reason=(
                f"Unknown policy confidence threshold {policy.min_confidence!r}; "
                "alert evaluation skipped."
            ),
        )

    if confidence_rank(confidence) < confidence_rank(policy.min_confidence):
        return AlertDecision(
            alert_id=alert_id,
            incident_id=incident_id,
            policy_id=policy.policy_id,
            status="skipped",
            reason=(
                f"Incident confidence {confidence!r} is below policy "
                f"threshold {policy.min_confidence!r}."
            ),
        )

    if already_fired:
        return AlertDecision(
            alert_id=alert_id,
            incident_id=incident_id,
            policy_id=policy.policy_id,
            status="suppressed",
            reason="An alert has already fired for this incident.",
        )

    return AlertDecision(
        alert_id=alert_id,
        incident_id=incident_id,
        policy_id=policy.policy_id,
        status="fired",
        reason="Matching enabled policy and confidence threshold met.",
    )
