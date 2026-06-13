"""Repository for ADR-026 remediation proposal rows."""

from __future__ import annotations

import logging
import sqlite3

from javdb.ops.diagnosis.models import OpsRemediationProposal, utc_now_iso

logger = logging.getLogger(__name__)

_COLUMNS = (
    "proposal_id",
    "incident_id",
    "action_type",
    "status",
    "safety_level",
    "title",
    "rationale",
    "command_preview",
    "runbook_ref",
    "evidence_refs_json",
    "required_checks_json",
    "blocked_reasons_json",
    "proposed_by",
    "decided_by",
    "decision_note",
    "created_at",
    "updated_at",
    "decided_at",
)


# Fields owning a human decision (plus the PK and original creation time) are
# never overwritten when a proposal is regenerated — see ``upsert``.
_DECISION_COLUMNS = ("status", "decided_by", "decision_note", "decided_at")
_PRESERVE_ON_CONFLICT = {"proposal_id", "created_at", *_DECISION_COLUMNS}


def _row_to_proposal(row: sqlite3.Row) -> OpsRemediationProposal:
    return OpsRemediationProposal(**{column: row[column] for column in _COLUMNS})


class OpsRemediationRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        try:
            self._conn.row_factory = sqlite3.Row
        except Exception:
            logger.debug("row_factory set failed", exc_info=True)

    def upsert(self, proposal: OpsRemediationProposal) -> None:
        values = [getattr(proposal, column) for column in _COLUMNS]
        columns = ", ".join(_COLUMNS)
        placeholders = ", ".join(["?"] * len(_COLUMNS))
        # ``build_proposal_id`` is stable, so regenerating proposals for the same
        # incident collides on an existing row that may already carry an admin's
        # approve/reject. Never overwrite the decision fields (or created_at), and
        # only refresh the generated descriptive fields while the row is still
        # undecided — otherwise the decision and its audit trail are silently lost.
        updates = ", ".join(
            f"{column}=excluded.{column}"
            for column in _COLUMNS
            if column not in _PRESERVE_ON_CONFLICT
        )
        self._conn.execute(
            f"""
            INSERT INTO OpsRemediationProposals ({columns})
            VALUES ({placeholders})
            ON CONFLICT(proposal_id) DO UPDATE SET {updates}
            WHERE OpsRemediationProposals.status = 'proposed'
            """,
            values,
        )

    def get(self, proposal_id: str) -> OpsRemediationProposal | None:
        row = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM OpsRemediationProposals WHERE proposal_id = ?",
            [proposal_id],
        ).fetchone()
        return None if row is None else _row_to_proposal(row)

    def list_for_incident(self, incident_id: str) -> list[OpsRemediationProposal]:
        rows = self._conn.execute(
            f"""
            SELECT {', '.join(_COLUMNS)}
            FROM OpsRemediationProposals
            WHERE incident_id = ?
            ORDER BY created_at ASC
            """,
            [incident_id],
        ).fetchall()
        return [_row_to_proposal(row) for row in rows]

    def record_decision(
        self,
        proposal_id: str,
        *,
        status: str,
        decided_by: str,
        decision_note: str | None,
    ) -> OpsRemediationProposal | None:
        # A proposal the safety policy has blocked must never become 'approved' —
        # that would record a contradictory "approved but blocked" decision and
        # could mislead an operator into acting. Rejecting a blocked proposal is
        # always allowed.
        if status == "approved":
            existing = self.get(proposal_id)
            if existing is not None and existing.safety_level == "blocked":
                raise ValueError(
                    f"Cannot approve a proposal blocked by the safety policy: {proposal_id}"
                )
        now = utc_now_iso()
        self._conn.execute(
            """
            UPDATE OpsRemediationProposals
            SET status = ?, decided_by = ?, decision_note = ?, decided_at = ?, updated_at = ?
            WHERE proposal_id = ?
            """,
            [status, decided_by, decision_note, now, now, proposal_id],
        )
        return self.get(proposal_id)
