"""Service orchestration for ADR-026 read-only diagnosis."""

from __future__ import annotations

import logging
from pathlib import Path
import json
from typing import Protocol

from javdb.integrations.notify import dispatch as notify_dispatch
from javdb.integrations.notify.plugin import NotifyMessage
from javdb.ops.diagnosis.ai import Synthesizer, synthesize_with_configured_ai
from javdb.ops.diagnosis.alerting import evaluate_alert
from javdb.ops.diagnosis.detectors import detect_incident
from javdb.ops.diagnosis.features import build_incident_features
from javdb.ops.diagnosis.models import AlertDecision, IncidentBundle, OpsAlertEvent, OpsIncidentRecord
from javdb.ops.diagnosis.persistence import persist_incident
from javdb.ops.diagnosis.remediation import propose_remediation
from javdb.storage.db import REPORTS_DB_PATH, get_db
from javdb.storage.repos.ops_alert_repo import OpsAlertRepo
from javdb.storage.repos.ops_incident_repo import OpsIncidentRepo
from javdb.storage.repos.ops_remediation_repo import OpsRemediationRepo

logger = logging.getLogger(__name__)

dispatch_notify_message = notify_dispatch.send

_CONFIDENCE_TO_LEVEL = {"low": "info", "medium": "warning", "high": "error"}


class _AlertRepoLike(Protocol):
    def list_policies(self): ...

    def list_events_for_incident(self, incident_id: str): ...

    def upsert_event(self, event: OpsAlertEvent) -> None: ...

    def claim_fired_event(self, event: OpsAlertEvent) -> bool: ...

    def mark_no_delivery(self, event: OpsAlertEvent) -> None: ...


def _policy_channels(channels_json: str | None) -> set[str]:
    if not channels_json:
        return set()
    try:
        value = json.loads(channels_json)
    except (TypeError, json.JSONDecodeError):
        return set()
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str) and item}


def _dispatch_exclude_for_channels(channels: set[str]) -> set[str] | None:
    if not channels:
        return None
    excluded = {name for name in notify_dispatch.active_names() if name not in channels}
    return excluded or None


def _no_delivery_event(decision: AlertDecision, reason: str) -> OpsAlertEvent:
    event = decision.to_event()
    return OpsAlertEvent(
        alert_id=event.alert_id,
        incident_id=event.incident_id,
        policy_id=event.policy_id,
        status="skipped",
        reason=reason,
        fired_at=event.fired_at,
    )


def _has_delivery_channel(channels: set[str], active_names: set[str]) -> bool:
    if not channels:
        return bool(active_names)
    return bool(channels & active_names)


def _persist_incident_features(bundle: IncidentBundle, record: OpsIncidentRecord, repo: object | None) -> None:
    features = build_incident_features(record, bundle=bundle)
    if repo is not None and hasattr(repo, "upsert_features"):
        repo.upsert_features(features)
        return
    with get_db(REPORTS_DB_PATH) as conn:
        OpsIncidentRepo(conn).upsert_features(features)


def _persist_proposals(record: OpsIncidentRecord, remediation_repo: object | None) -> None:
    # Remediation proposals are audit-only, non-critical enrichment. A failure to
    # generate or persist them must never break the main diagnosis flow, so we log
    # and continue (graceful degradation), mirroring persist_incident's posture.
    try:
        proposals = propose_remediation(record)
        if remediation_repo is not None:
            for proposal in proposals:
                remediation_repo.upsert(proposal)
            return
        with get_db(REPORTS_DB_PATH) as conn:
            repo = OpsRemediationRepo(conn)
            for proposal in proposals:
                repo.upsert(proposal)
    except Exception:
        logger.exception(
            "Failed to persist remediation proposals (non-critical); "
            "continuing diagnosis: incident_id=%s",
            record.incident_id,
        )


def _build_alert_message(record: OpsIncidentRecord) -> NotifyMessage:
    level = _CONFIDENCE_TO_LEVEL.get(record.confidence, "warning")
    subject = f"[ops-alert] {record.incident_type} ({record.confidence})"
    body = (
        f"Incident ID: {record.incident_id}\n"
        f"Incident type: {record.incident_type}\n"
        f"Confidence: {record.confidence}\n"
        f"Session: {record.session_id or 'n/a'}\n"
        f"Run: {record.run_id or 'n/a'} attempt {record.run_attempt or 'n/a'}"
    )
    return NotifyMessage(subject=subject, body=body, level=level)


def _evaluate_and_persist_alert(record: OpsIncidentRecord, repo: _AlertRepoLike) -> None:
    try:
        policies = repo.list_policies()
        events = repo.list_events_for_incident(record.incident_id)
        already_fired = any(event.status == "fired" for event in events)
        decision = evaluate_alert(record, policies, already_fired=already_fired)
        if decision.status == "fired":
            policy = next(
                (item for item in policies if item.policy_id == decision.policy_id),
                None,
            )
            channels = _policy_channels(policy.channels_json if policy else None)
            active_names = set(notify_dispatch.active_names())
            if not _has_delivery_channel(channels, active_names):
                repo.upsert_event(
                    _no_delivery_event(
                        decision,
                        "no_delivery: policy channels do not match any active notify backend.",
                    )
                )
                return
            claimed_event = decision.to_event()
            if not repo.claim_fired_event(claimed_event):
                return
            try:
                results = dispatch_notify_message(
                    _build_alert_message(record),
                    exclude=_dispatch_exclude_for_channels(channels),
                )
                if not results:
                    no_delivery = _no_delivery_event(
                        decision,
                        "no_delivery: dispatch returned no notify delivery results.",
                    )
                    repo.mark_no_delivery(no_delivery)
                    return
                failures = [result for result in results if not result.ok]
                if failures:
                    logger.warning(
                        "Alert dispatch returned failures for incident_id=%s: %s",
                        record.incident_id,
                        ", ".join(f"{result.plugin}={result.detail}" for result in failures),
                    )
            except Exception:
                logger.warning(
                    "Alert delivery failed for incident_id=%s; continuing diagnosis",
                    record.incident_id,
                    exc_info=True,
                )
            return
        repo.upsert_event(decision.to_event())
    except Exception:
        logger.exception(
            "Failed to evaluate or persist alert event (non-critical); "
            "continuing diagnosis: incident_id=%s",
            record.incident_id,
        )


def _maybe_alert(record: OpsIncidentRecord, alert_repo: object | None) -> None:
    if alert_repo is not None:
        _evaluate_and_persist_alert(record, alert_repo)
        return
    try:
        with get_db(REPORTS_DB_PATH) as conn:
            _evaluate_and_persist_alert(record, OpsAlertRepo(conn))
    except Exception:
        logger.exception(
            "Failed to acquire alert repository (non-critical); "
            "continuing diagnosis: incident_id=%s",
            record.incident_id,
        )


def run_diagnosis(
    bundle: IncidentBundle,
    *,
    synthesizer: Synthesizer | None = None,
) -> OpsIncidentRecord:
    """Read-only diagnosis: run the detector + synthesis and build the incident
    record WITHOUT persisting it. Shared by the persisting ``diagnose_incident``
    and read-only callers (e.g. the ADR-038 MCP surface)."""
    detector_result = detect_incident(bundle)
    result = (synthesizer or synthesize_with_configured_ai)(bundle, detector_result)
    return OpsIncidentRecord.from_bundle_and_result(bundle, result)


def diagnose_incident(
    bundle: IncidentBundle,
    *,
    synthesizer: Synthesizer | None = None,
    repo: object | None = None,
    jsonl_path: str | Path | None = None,
    remediation_repo: object | None = None,
    generate_remediation: bool = False,
    alert_repo: object | None = None,
    generate_alerts: bool = False,
) -> OpsIncidentRecord:
    record = run_diagnosis(bundle, synthesizer=synthesizer)
    persisted = persist_incident(record, repo=repo, jsonl_path=jsonl_path)
    if persisted.persistence_status == "d1_written":
        _persist_incident_features(bundle, persisted, repo)
    if generate_remediation and persisted.persistence_status == "d1_written":
        _persist_proposals(persisted, remediation_repo)
    if generate_alerts and persisted.persistence_status == "d1_written":
        _maybe_alert(persisted, alert_repo)
    return persisted
