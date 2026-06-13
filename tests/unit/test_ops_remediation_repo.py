from __future__ import annotations

import sqlite3

import pytest

from javdb.ops.diagnosis.models import OpsRemediationProposal
from javdb.storage.repos.ops_remediation_repo import OpsRemediationRepo


DDL = """
CREATE TABLE OpsRemediationProposals (
  proposal_id TEXT PRIMARY KEY,
  incident_id TEXT NOT NULL,
  action_type TEXT NOT NULL,
  status TEXT NOT NULL,
  safety_level TEXT NOT NULL,
  title TEXT NOT NULL,
  rationale TEXT NOT NULL,
  command_preview TEXT,
  runbook_ref TEXT,
  evidence_refs_json TEXT NOT NULL,
  required_checks_json TEXT NOT NULL,
  blocked_reasons_json TEXT NOT NULL,
  proposed_by TEXT NOT NULL,
  decided_by TEXT,
  decision_note TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  decided_at TEXT
)
"""


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(DDL)
    return conn


def _proposal():
    return OpsRemediationProposal.create(
        incident_id="opsinc_abc",
        action_type="open_runbook",
        safety_level="safe_to_prepare",
        title="Open runbook",
        rationale="Review runbook.",
    )


def test_repo_upserts_and_lists_proposals():
    repo = OpsRemediationRepo(_conn())
    proposal = _proposal()

    repo.upsert(proposal)
    items = repo.list_for_incident("opsinc_abc")

    assert len(items) == 1
    assert items[0].proposal_id == proposal.proposal_id


def test_repo_records_decision_without_execution():
    repo = OpsRemediationRepo(_conn())
    proposal = _proposal()
    repo.upsert(proposal)

    decided = repo.record_decision(
        proposal.proposal_id,
        status="approved",
        decided_by="admin",
        decision_note="Reviewed manually.",
    )

    assert decided is not None
    assert decided.status == "approved"
    assert decided.decided_by == "admin"
    assert decided.decision_note == "Reviewed manually."


def _blocked_proposal():
    return OpsRemediationProposal.create(
        incident_id="opsinc_abc",
        action_type="prepare_rollback_workflow",
        safety_level="blocked",
        title="Prepare rollback workflow",
        rationale="Rollback blocked.",
        blocked_reasons=["Session id is missing."],
    )


def test_repo_upsert_refreshes_undecided_but_preserves_decisions():
    """Regenerating a proposal refreshes descriptive fields while undecided, but
    never overwrites a recorded human decision (the proposal_id is stable)."""
    repo = OpsRemediationRepo(_conn())
    proposal = _proposal()
    repo.upsert(proposal)

    # Still undecided → a regeneration refreshes the descriptive fields.
    repo.upsert(
        OpsRemediationProposal.create(
            incident_id="opsinc_abc",
            action_type="open_runbook",
            safety_level="safe_to_prepare",
            title="Open runbook (updated)",
            rationale="Review runbook again.",
        )
    )
    assert repo.get(proposal.proposal_id).title == "Open runbook (updated)"

    # An admin decides it...
    repo.record_decision(
        proposal.proposal_id, status="approved", decided_by="admin", decision_note="ok"
    )

    # ...and a later regeneration must NOT wipe the decision or its audit trail.
    repo.upsert(_proposal())
    decided = repo.get(proposal.proposal_id)
    assert decided.status == "approved"
    assert decided.decided_by == "admin"
    assert decided.decision_note == "ok"


def test_repo_rejects_approving_blocked_proposal():
    """A proposal the safety policy blocked can be rejected but never approved."""
    repo = OpsRemediationRepo(_conn())
    blocked = _blocked_proposal()
    repo.upsert(blocked)

    with pytest.raises(ValueError, match="blocked"):
        repo.record_decision(
            blocked.proposal_id, status="approved", decided_by="admin", decision_note=None
        )
    assert repo.get(blocked.proposal_id).status == "proposed"  # unchanged

    rejected = repo.record_decision(
        blocked.proposal_id, status="rejected", decided_by="admin", decision_note=None
    )
    assert rejected is not None
    assert rejected.status == "rejected"
