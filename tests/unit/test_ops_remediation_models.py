from __future__ import annotations

import json

from javdb.ops.diagnosis.models import (
    EvidenceRef,
    OpsRemediationProposal,
    build_proposal_id,
)


def test_proposal_id_is_stable_for_incident_and_action_type():
    first = build_proposal_id("opsinc_abc", "prepare_rollback_workflow")
    second = build_proposal_id("opsinc_abc", "prepare_rollback_workflow")

    assert first == second
    assert first.startswith("opsprop_")


def test_proposal_serializes_evidence_and_required_checks():
    proposal = OpsRemediationProposal.create(
        incident_id="opsinc_abc",
        action_type="prepare_rollback_workflow",
        safety_level="requires_review",
        title="Prepare rollback workflow",
        rationale="Session failed before commit and rollback safety is not blocked.",
        command_preview="gh workflow run RollbackD1.yml -f session_id=20260527T120000.000000Z-0001-0001",
        runbook_ref="docs/handbook/en/ops/d1-rollback.md",
        evidence_refs=[EvidenceRef(kind="incident", ref="opsinc_abc")],
        required_checks=["Confirm session status is failed."],
        blocked_reasons=[],
    )

    assert proposal.proposal_id.startswith("opsprop_")
    assert proposal.status == "proposed"
    assert json.loads(proposal.required_checks_json) == ["Confirm session status is failed."]
    assert json.loads(proposal.evidence_refs_json)[0]["kind"] == "incident"
