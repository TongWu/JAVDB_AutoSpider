from __future__ import annotations

import json

from javdb.integrations.notify.plugin import NotifyResult
from javdb.ops.diagnosis.models import (
    DiagnosisResult,
    EvidenceRef,
    IncidentBundle,
    OpsAlertEvent,
    OpsAlertPolicy,
)
from javdb.ops.diagnosis.service import diagnose_incident


class CapturingRepo:
    def __init__(self):
        self.records = []
        self.features = []

    def upsert(self, record):
        self.records.append(record)

    def upsert_features(self, features):
        self.features.append(features)


def test_service_runs_detector_and_persists_record():
    repo = CapturingRepo()
    bundle = IncidentBundle(
        trigger_source="manual_cli",
        workflow_result="failure",
        session_id=None,
    )

    record = diagnose_incident(bundle, repo=repo)

    assert record.incident_type == "failed_ingestion"
    assert record.persistence_status == "d1_written"
    assert len(repo.records) == 1
    assert repo.records[0].incident_id == record.incident_id


def test_service_uses_ai_synthesizer_when_available():
    repo = CapturingRepo()

    def synthesize(bundle, detector_result):
        return DiagnosisResult(
            incident_type="failed_ingestion",
            confidence="high",
            confirmed_findings=detector_result.confirmed_findings + ["AI summary produced"],
            likely_causes=["Known failure pattern"],
            unknowns=[],
            recommended_next_actions=["Open diagnosis page"],
            unsafe_actions=["Do not force rollback"],
            evidence_refs=[EvidenceRef(kind="incident", ref="synthetic")],
            model_version="fake-ai-v1",
            detector_version=detector_result.detector_version,
        )

    record = diagnose_incident(
        IncidentBundle(trigger_source="manual_cli", workflow_result="failure", session_id="sid"),
        repo=repo,
        synthesizer=synthesize,
    )

    assert record.model_version == "fake-ai-v1"
    assert json.loads(record.confirmed_findings_json)[-1] == "AI summary produced"


def test_service_persists_feature_row_when_incident_is_written():
    repo = CapturingRepo()
    bundle = IncidentBundle(
        trigger_source="workflow_failure",
        workflow_name="DailyIngestion",
        workflow_result="failure",
        session_id="20260527T120000.000000Z-0001-0001",
    )

    record = diagnose_incident(bundle, repo=repo)

    assert record.persistence_status == "d1_written"
    assert repo.features[0].incident_id == record.incident_id
    assert repo.features[0].workflow_name == "DailyIngestion"


def test_service_does_not_persist_feature_row_on_jsonl_fallback(tmp_path):
    class FailingUpsertRepo:
        def __init__(self):
            self.features = []

        def upsert(self, record):
            raise RuntimeError("simulated D1 failure")

        def upsert_features(self, features):
            self.features.append(features)

    repo = FailingUpsertRepo()
    bundle = IncidentBundle(
        trigger_source="workflow_failure",
        workflow_name="DailyIngestion",
        workflow_result="failure",
        session_id=None,
    )

    record = diagnose_incident(bundle, repo=repo, jsonl_path=tmp_path / "fallback.jsonl")

    assert record.persistence_status == "d1_failed_jsonl_written"
    assert repo.features == []


def test_service_can_generate_remediation_proposals():
    class ProposalRepo:
        def __init__(self):
            self.proposals = []

        def upsert(self, proposal):
            self.proposals.append(proposal)

    incident_repo = CapturingRepo()
    proposal_repo = ProposalRepo()
    bundle = IncidentBundle(
        trigger_source="manual_cli",
        workflow_result="failure",
        session_id="20260527T120000.000000Z-0001-0001",
    )

    record = diagnose_incident(
        bundle,
        repo=incident_repo,
        remediation_repo=proposal_repo,
        generate_remediation=True,
    )

    assert record.incident_type == "failed_ingestion"
    assert proposal_repo.proposals
    assert {proposal.action_type for proposal in proposal_repo.proposals}


def test_service_swallows_remediation_persistence_failure():
    """Remediation proposals are non-critical: a failure to persist them must not
    break the main diagnosis flow (graceful degradation)."""

    class ExplodingProposalRepo:
        def upsert(self, proposal):
            raise RuntimeError("boom")

    incident_repo = CapturingRepo()
    bundle = IncidentBundle(
        trigger_source="manual_cli",
        workflow_result="failure",
        session_id="20260527T120000.000000Z-0001-0001",
    )

    # Must not raise despite the proposal repo blowing up mid-persist.
    record = diagnose_incident(
        bundle,
        repo=incident_repo,
        remediation_repo=ExplodingProposalRepo(),
        generate_remediation=True,
    )

    assert record.incident_type == "failed_ingestion"
    assert record.persistence_status == "d1_written"


class CapturingAlertRepo:
    def __init__(self, *, existing_events=None):
        self.events = []
        self.existing_events = existing_events or []

    def list_policies(self):
        return [
            OpsAlertPolicy.create(
                incident_type="failed_ingestion",
                min_confidence="low",
                enabled=True,
                channels=["email"],
            )
        ]

    def list_events_for_incident(self, incident_id):
        return [event for event in self.existing_events if event.incident_id == incident_id]

    def upsert_event(self, event):
        self.events.append(event)

    def claim_fired_event(self, event):
        if any(
            item.status == "fired" and item.incident_id == event.incident_id
            for item in self.existing_events + self.events
        ):
            return False
        self.events.append(event)
        return True

    def mark_no_delivery(self, event):
        self.events = [
            event if item.alert_id == event.alert_id and item.incident_id == event.incident_id else item
            for item in self.events
        ]


def test_service_fires_alert_and_reuses_adr039_dispatch(monkeypatch):
    from javdb.integrations.notify.plugin import NotifyMessage
    from javdb.ops.diagnosis import service as service_module

    sent = []

    def fake_dispatch(message, exclude=None):
        sent.append((message, exclude))
        return [NotifyResult(plugin="email", ok=True, detail="sent")]

    monkeypatch.setattr(service_module, "dispatch_notify_message", fake_dispatch)
    monkeypatch.setattr(service_module.notify_dispatch, "active_names", lambda: ["email"])

    incident_repo = CapturingRepo()
    alert_repo = CapturingAlertRepo()
    bundle = IncidentBundle(
        trigger_source="workflow_failure",
        workflow_result="failure",
        session_id="20260613T120000.000000Z-0001-0001",
        run_id="12345",
        run_attempt=2,
    )

    record = diagnose_incident(
        bundle,
        repo=incident_repo,
        alert_repo=alert_repo,
        generate_alerts=True,
    )

    assert record.incident_type == "failed_ingestion"
    assert len(sent) == 1
    message, exclude = sent[0]
    assert isinstance(message, NotifyMessage)
    assert exclude is None
    assert "[ops-alert] failed_ingestion" in message.subject
    assert record.incident_id in message.body
    assert alert_repo.events
    assert alert_repo.events[0].status == "fired"


def test_service_filters_adr039_dispatch_to_policy_channels(monkeypatch):
    from javdb.ops.diagnosis import service as service_module

    captured = []

    def fake_dispatch(message, exclude=None):
        captured.append((message, exclude))
        return [NotifyResult(plugin="telegram", ok=True, detail="sent")]

    class TelegramAlertRepo(CapturingAlertRepo):
        def list_policies(self):
            return [
                OpsAlertPolicy.create(
                    incident_type="failed_ingestion",
                    min_confidence="low",
                    enabled=True,
                    channels=["telegram"],
                )
            ]

    monkeypatch.setattr(service_module, "dispatch_notify_message", fake_dispatch)
    monkeypatch.setattr(service_module.notify_dispatch, "active_names", lambda: ["email", "telegram"])

    diagnose_incident(
        IncidentBundle(
            trigger_source="workflow_failure",
            workflow_result="failure",
            session_id="20260613T120000.000000Z-0001-0001",
        ),
        repo=CapturingRepo(),
        alert_repo=TelegramAlertRepo(),
        generate_alerts=True,
    )

    assert len(captured) == 1
    assert captured[0][1] == {"email"}


def test_service_uses_default_adr039_dispatch_when_policy_channels_empty(monkeypatch):
    from javdb.ops.diagnosis import service as service_module

    captured = []

    def fake_dispatch(message, exclude=None):
        captured.append((message, exclude))
        return [NotifyResult(plugin="email", ok=True, detail="sent")]

    class EmptyChannelsAlertRepo(CapturingAlertRepo):
        def list_policies(self):
            return [
                OpsAlertPolicy.create(
                    incident_type="failed_ingestion",
                    min_confidence="low",
                    enabled=True,
                    channels=[],
                )
            ]

    monkeypatch.setattr(service_module, "dispatch_notify_message", fake_dispatch)
    monkeypatch.setattr(service_module.notify_dispatch, "active_names", lambda: ["email", "telegram"])

    diagnose_incident(
        IncidentBundle(trigger_source="workflow_failure", workflow_result="failure"),
        repo=CapturingRepo(),
        alert_repo=EmptyChannelsAlertRepo(),
        generate_alerts=True,
    )

    assert len(captured) == 1
    assert captured[0][1] is None


def test_service_skips_alert_when_policy_channels_have_no_active_backend(monkeypatch):
    from javdb.ops.diagnosis import service as service_module

    class SmsAlertRepo(CapturingAlertRepo):
        def list_policies(self):
            return [
                OpsAlertPolicy.create(
                    incident_type="failed_ingestion",
                    min_confidence="low",
                    enabled=True,
                    channels=["sms"],
                )
            ]

    sent = []
    alert_repo = SmsAlertRepo()
    monkeypatch.setattr(service_module, "dispatch_notify_message", sent.append)
    monkeypatch.setattr(service_module.notify_dispatch, "active_names", lambda: ["email"])

    diagnose_incident(
        IncidentBundle(trigger_source="workflow_failure", workflow_result="failure"),
        repo=CapturingRepo(),
        alert_repo=alert_repo,
        generate_alerts=True,
    )

    assert sent == []
    assert alert_repo.events
    assert alert_repo.events[0].status == "skipped"
    assert "no_delivery" in (alert_repo.events[0].reason or "")


def test_service_marks_no_delivery_when_dispatch_returns_no_results(monkeypatch):
    from javdb.ops.diagnosis import service as service_module

    def fake_dispatch(_message, exclude=None):
        return []

    alert_repo = CapturingAlertRepo()
    monkeypatch.setattr(service_module, "dispatch_notify_message", fake_dispatch)
    monkeypatch.setattr(service_module.notify_dispatch, "active_names", lambda: ["email"])

    diagnose_incident(
        IncidentBundle(trigger_source="workflow_failure", workflow_result="failure"),
        repo=CapturingRepo(),
        alert_repo=alert_repo,
        generate_alerts=True,
    )

    assert alert_repo.events
    assert alert_repo.events[0].status == "skipped"
    assert "no_delivery" in (alert_repo.events[0].reason or "")


def test_service_does_not_dispatch_when_alert_claim_is_lost(monkeypatch):
    from javdb.ops.diagnosis import service as service_module

    class LostClaimAlertRepo(CapturingAlertRepo):
        def claim_fired_event(self, event):
            return False

    sent = []
    monkeypatch.setattr(service_module, "dispatch_notify_message", sent.append)
    monkeypatch.setattr(service_module.notify_dispatch, "active_names", lambda: ["email"])

    diagnose_incident(
        IncidentBundle(trigger_source="workflow_failure", workflow_result="failure"),
        repo=CapturingRepo(),
        alert_repo=LostClaimAlertRepo(),
        generate_alerts=True,
    )

    assert sent == []


def test_service_logs_adr039_dispatch_result_failures(monkeypatch, caplog):
    from javdb.ops.diagnosis import service as service_module

    def fake_dispatch(_message, exclude=None):
        return [NotifyResult(plugin="email", ok=False, detail="not configured")]

    monkeypatch.setattr(service_module, "dispatch_notify_message", fake_dispatch)

    alert_repo = CapturingAlertRepo()

    with caplog.at_level("WARNING", logger=service_module.logger.name):
        diagnose_incident(
            IncidentBundle(trigger_source="workflow_failure", workflow_result="failure"),
            repo=CapturingRepo(),
            alert_repo=alert_repo,
            generate_alerts=True,
        )

    assert alert_repo.events
    assert alert_repo.events[0].status == "fired"
    assert "Alert dispatch returned failures" in caplog.text
    assert "email=not configured" in caplog.text


def test_service_alert_delivery_failure_is_best_effort(monkeypatch):
    from javdb.ops.diagnosis import service as service_module

    def boom(_message):
        raise RuntimeError("smtp down")

    monkeypatch.setattr(service_module, "dispatch_notify_message", boom)

    alert_repo = CapturingAlertRepo()
    bundle = IncidentBundle(
        trigger_source="workflow_failure",
        workflow_result="failure",
        session_id="20260613T120000.000000Z-0001-0001",
    )

    record = diagnose_incident(
        bundle,
        repo=CapturingRepo(),
        alert_repo=alert_repo,
        generate_alerts=True,
    )

    assert record.persistence_status == "d1_written"
    assert alert_repo.events
    assert alert_repo.events[0].status == "fired"


def test_service_does_not_generate_alerts_by_default(monkeypatch):
    from javdb.ops.diagnosis import service as service_module

    sent = []
    monkeypatch.setattr(service_module, "dispatch_notify_message", sent.append)

    record = diagnose_incident(
        IncidentBundle(trigger_source="workflow_failure", workflow_result="failure"),
        repo=CapturingRepo(),
    )

    assert record.persistence_status == "d1_written"
    assert sent == []


def test_service_does_not_alert_on_jsonl_fallback(tmp_path, monkeypatch):
    from javdb.ops.diagnosis import service as service_module

    class FailingUpsertRepo:
        def upsert(self, record):
            raise RuntimeError("simulated D1 failure")

    sent = []
    monkeypatch.setattr(service_module, "dispatch_notify_message", sent.append)
    alert_repo = CapturingAlertRepo()

    record = diagnose_incident(
        IncidentBundle(trigger_source="workflow_failure", workflow_result="failure"),
        repo=FailingUpsertRepo(),
        jsonl_path=tmp_path / "fallback.jsonl",
        alert_repo=alert_repo,
        generate_alerts=True,
    )

    assert record.persistence_status == "d1_failed_jsonl_written"
    assert sent == []
    assert alert_repo.events == []


def test_service_alert_default_repo_failure_is_best_effort(monkeypatch):
    from javdb.ops.diagnosis import service as service_module

    def db_down(_path):
        raise RuntimeError("db down")

    sent = []
    monkeypatch.setattr(service_module, "get_db", db_down)
    monkeypatch.setattr(service_module, "dispatch_notify_message", sent.append)

    record = diagnose_incident(
        IncidentBundle(trigger_source="workflow_failure", workflow_result="failure"),
        repo=CapturingRepo(),
        generate_alerts=True,
    )

    assert record.persistence_status == "d1_written"
    assert sent == []


def test_service_suppresses_already_fired_alert(monkeypatch):
    from javdb.ops.diagnosis import service as service_module

    class IncidentScopedExistingAlertRepo(CapturingAlertRepo):
        def list_events_for_incident(self, incident_id):
            return [
                OpsAlertEvent(
                    alert_id="existing-alert",
                    incident_id=incident_id,
                    policy_id="opsalertpolicy_failed_ingestion",
                    status="fired",
                    reason="already fired",
                    fired_at="2026-06-13T12:00:00Z",
                )
            ]

    alert_repo = IncidentScopedExistingAlertRepo()
    sent = []
    monkeypatch.setattr(service_module, "dispatch_notify_message", sent.append)

    record = diagnose_incident(
        IncidentBundle(trigger_source="workflow_failure", workflow_result="failure"),
        repo=CapturingRepo(),
        alert_repo=alert_repo,
        generate_alerts=True,
    )

    assert record.persistence_status == "d1_written"
    assert sent == []
    assert alert_repo.events
    assert alert_repo.events[0].status == "suppressed"


def test_service_does_not_reimplement_delivery():
    import inspect

    from javdb.ops.diagnosis import service as service_module

    source = inspect.getsource(service_module)
    assert "dispatch_notify_message" in source
    assert "smtplib" not in source
    assert "notify.email" not in source
