from __future__ import annotations

from javdb.ops.diagnosis.alerting import build_alert_id, evaluate_alert
from javdb.ops.diagnosis.models import OpsAlertPolicy


def _policy(
    incident_type: str = "failed_ingestion",
    *,
    min_confidence: str = "medium",
    enabled: bool = True,
    channels: list[str] | None = None,
) -> OpsAlertPolicy:
    return OpsAlertPolicy.create(
        incident_type=incident_type,
        min_confidence=min_confidence,
        enabled=enabled,
        channels=channels or ["email"],
        updated_by="admin",
    )


class _Record:
    def __init__(self, incident_id: str, incident_type: str, confidence: str) -> None:
        self.incident_id = incident_id
        self.incident_type = incident_type
        self.confidence = confidence


def test_alert_id_is_deterministic_from_incident_id():
    first = build_alert_id("opsinc_abc")
    second = build_alert_id("opsinc_abc")

    assert first == second
    assert first.startswith("opsalert_")


def test_fires_when_policy_matches_and_confidence_meets_threshold():
    record = _Record("opsinc_abc", "failed_ingestion", "high")

    decision = evaluate_alert(record, [_policy(min_confidence="medium")], already_fired=False)

    assert decision.status == "fired"
    assert decision.policy_id is not None
    assert decision.alert_id == build_alert_id("opsinc_abc")


def test_suppressed_when_already_fired():
    record = _Record("opsinc_abc", "failed_ingestion", "high")

    decision = evaluate_alert(record, [_policy()], already_fired=True)

    assert decision.status == "suppressed"
    assert "already" in decision.reason.lower()


def test_skipped_when_confidence_below_threshold():
    record = _Record("opsinc_abc", "failed_ingestion", "low")

    decision = evaluate_alert(record, [_policy(min_confidence="high")], already_fired=False)

    assert decision.status == "skipped"
    assert "confidence" in decision.reason.lower()


def test_skipped_when_no_matching_enabled_policy():
    record = _Record("opsinc_abc", "stale_session", "high")

    decision = evaluate_alert(record, [_policy(incident_type="failed_ingestion")], already_fired=False)

    assert decision.status == "skipped"


def test_skipped_when_matching_policy_is_disabled():
    record = _Record("opsinc_abc", "failed_ingestion", "high")

    decision = evaluate_alert(record, [_policy(enabled=False)], already_fired=False)

    assert decision.status == "skipped"


def test_confidence_ordering_is_deterministic():
    record = _Record("opsinc_abc", "failed_ingestion", "medium")

    fires = evaluate_alert(record, [_policy(min_confidence="medium")], already_fired=False)
    skips = evaluate_alert(record, [_policy(min_confidence="high")], already_fired=False)

    assert fires.status == "fired"
    assert skips.status == "skipped"


def test_skipped_when_record_confidence_is_unknown():
    record = _Record("opsinc_abc", "failed_ingestion", "bogus")

    decision = evaluate_alert(record, [_policy(min_confidence="low")], already_fired=False)

    assert decision.status == "skipped"
    assert "unknown" in decision.reason.lower() or "invalid" in decision.reason.lower()
    assert "confidence" in decision.reason.lower()


def test_skipped_when_policy_threshold_is_unknown():
    record = _Record("opsinc_abc", "failed_ingestion", "high")

    decision = evaluate_alert(record, [_policy(min_confidence="bogus")], already_fired=False)

    assert decision.status == "skipped"
    reason = decision.reason.lower()
    assert "unknown" in reason or "invalid" in reason
    assert "threshold" in reason or "confidence" in reason


def test_alert_decision_to_event_preserves_fields_and_adds_fired_at():
    record = _Record("opsinc_abc", "failed_ingestion", "high")
    decision = evaluate_alert(record, [_policy()], already_fired=False)

    event = decision.to_event()

    assert event.alert_id == decision.alert_id
    assert event.incident_id == decision.incident_id
    assert event.policy_id == decision.policy_id
    assert event.status == decision.status
    assert event.reason == decision.reason
    assert event.fired_at.endswith("Z")


def test_uses_first_enabled_matching_policy():
    record = _Record("opsinc_abc", "failed_ingestion", "medium")
    first = _policy(min_confidence="high")
    second = _policy(min_confidence="low")
    disabled = _policy(min_confidence="low", enabled=False)

    decision = evaluate_alert(record, [disabled, first, second], already_fired=False)

    assert decision.status == "skipped"
    assert decision.policy_id == first.policy_id
    assert "threshold" in decision.reason.lower()
