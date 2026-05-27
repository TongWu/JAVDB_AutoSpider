# IMP-ADR026-03: ADR-026 Phase 3 - Gated Remediation Suggestions

**Status:** Proposed

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ADR-026 Phase 3 by adding explicit, human-approved remediation suggestions that remain safe, auditable, and separate from automatic execution.

**Architecture:** Extend the diagnosis domain with a proposal ledger that records recommended actions, safety gates, evidence, and operator decisions. The assistant may propose approved action classes such as opening a rollback workflow link, opening a rerun workflow link, or showing a drift apply command, but execution stays outside the model and requires a human confirmation step. API and UI surfaces expose proposal state and confirmation metadata; they do not perform destructive work in this phase.

**Tech Stack:** Python 3.11, Cloudflare D1, FastAPI/Pydantic, pytest, TypeScript, Hono, Vue 3, Naive UI, Vitest, Playwright, Markdown docs.

**Source spec:** [ADR-026](ADR-026-ai-operations-diagnosis.md), Phase 3 roadmap, D1-D10.

**Non-negotiable:** Phase 3 is not fully automatic remediation. It must not execute rollback, rerun workflows, modify D1 recovery state, delete qBittorrent tasks, or apply drift fixes directly. It may only produce auditable proposals and record human decisions about those proposals.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `javdb/migrations/d1/2026_05_27_add_ops_remediation_proposals.sql` | D1-first proposal ledger for gated remediation suggestions. |
| Modify | `javdb/storage/db/_db_migrations.py` | Include proposal ledger in local SQLite mirror initialization. |
| Modify | `javdb/ops/diagnosis/models.py` | Add proposal, gate, decision, and audit contracts. |
| Create | `javdb/ops/diagnosis/remediation.py` | Convert diagnosis records into safe proposal candidates. |
| Create | `javdb/storage/repos/ops_remediation_repo.py` | Repository for proposal rows and decision updates. |
| Modify | `javdb/ops/diagnosis/service.py` | Optionally generate proposals after incident persistence. |
| Modify | `apps/api/schemas/diagnostics.py` | Add proposal list/detail/decision schemas. |
| Modify | `apps/api/routers/diagnostics.py` | Add read-only proposal endpoints and non-executing decision recording. |
| Create | `tests/unit/test_ops_remediation_models.py` | Proposal model serialization and safety-shape tests. |
| Create | `tests/unit/test_ops_remediation_policy.py` | Proposal generation and safety gate tests. |
| Create | `tests/unit/test_ops_remediation_repo.py` | Proposal repository tests. |
| Modify | `tests/unit/test_ops_diagnosis_service.py` | Service orchestration tests for proposal generation. |
| Modify | `tests/unit/test_ops_diagnostics_api.py` | API tests for proposal read and decision endpoints. |
| Modify | `../JAVDB_AutoSpider_Web/server/routes/diagnostics.ts` | Worker read-only proposal endpoints and decision recording parity. |
| Modify | `../JAVDB_AutoSpider_Web/server/__tests__/diagnostics-routes.test.ts` | Worker proposal route tests. |
| Modify | `../JAVDB_AutoSpider_Web/src/api/diagnostics.ts` | Frontend proposal API types and functions. |
| Modify | `../JAVDB_AutoSpider_Web/src/pages/diagnostics/OpsIncidentsPage.vue` | Show proposal panel and record approve/reject decisions. |
| Create | `../JAVDB_AutoSpider_Web/tests/unit/ops-remediation-api.spec.ts` | Frontend API client tests. |
| Create | `../JAVDB_AutoSpider_Web/tests/e2e/ops-remediation.spec.ts` | Playwright smoke for proposal review UX. |
| Modify | `docs/handbook/en/ops/troubleshooting.md` | Document remediation proposal semantics. |
| Modify | `docs/handbook/zh/ops/troubleshooting.md` | Chinese mirror. |
| Modify | `docs/handbook/en/ops/d1-rollback.md` | Clarify that proposals do not replace rollback safety matrix. |
| Modify | `docs/handbook/zh/ops/d1-rollback.md` | Chinese mirror. |

## Scope Boundaries

- Proposals are audit records, not execution jobs.
- Approval means "operator accepted the recommendation", not "system executed the action".
- The proposal engine must be deterministic and must not rely on a model to decide whether an action is safe.
- Any future direct execution of rollback/rerun/drift apply requires a separate ADR or a new phase with a stricter execution design.
- The Web repo may show links and copyable commands, but must not call rollback, rerun, drift apply, qB delete, or recovery resolve endpoints from this feature.

---

## Task 1: D1 Proposal Ledger

**Files:**
- Create: `javdb/migrations/d1/2026_05_27_add_ops_remediation_proposals.sql`
- Modify: `javdb/storage/db/_db_migrations.py`

- [ ] **Step 1: Create proposal migration**

Create `javdb/migrations/d1/2026_05_27_add_ops_remediation_proposals.sql`:

```sql
-- 2026-05-27: Add ADR-026 gated remediation proposal ledger.
--
-- Apply with:
--   wrangler d1 execute javdb-reports --remote \
--     --file=javdb/migrations/d1/2026_05_27_add_ops_remediation_proposals.sql
--
-- This table records suggestions and human decisions. It does not execute
-- rollback, rerun, drift apply, qB cleanup, or recovery mutation.

CREATE TABLE IF NOT EXISTS OpsRemediationProposals (
  proposal_id TEXT PRIMARY KEY,
  incident_id TEXT NOT NULL,
  action_type TEXT NOT NULL
    CHECK (action_type IN (
      'open_runbook',
      'prepare_rollback_workflow',
      'prepare_rerun_workflow',
      'prepare_drift_apply_command',
      'inspect_qb_side_effects',
      'inspect_recovery_outbox'
    )),
  status TEXT NOT NULL DEFAULT 'proposed'
    CHECK (status IN ('proposed', 'approved', 'rejected', 'expired')),
  safety_level TEXT NOT NULL
    CHECK (safety_level IN ('safe_to_prepare', 'requires_review', 'blocked')),
  title TEXT NOT NULL,
  rationale TEXT NOT NULL,
  command_preview TEXT,
  runbook_ref TEXT,
  evidence_refs_json TEXT NOT NULL DEFAULT '[]',
  required_checks_json TEXT NOT NULL DEFAULT '[]',
  blocked_reasons_json TEXT NOT NULL DEFAULT '[]',
  proposed_by TEXT NOT NULL DEFAULT 'adr026-policy-v1',
  decided_by TEXT,
  decision_note TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  decided_at TEXT,
  FOREIGN KEY (incident_id) REFERENCES OpsIncidents(incident_id)
);

CREATE INDEX IF NOT EXISTS idx_ops_remediation_incident
  ON OpsRemediationProposals(incident_id);

CREATE INDEX IF NOT EXISTS idx_ops_remediation_status
  ON OpsRemediationProposals(status);

CREATE INDEX IF NOT EXISTS idx_ops_remediation_action_type
  ON OpsRemediationProposals(action_type);
```

- [ ] **Step 2: Add local mirror DDL**

Modify `javdb/storage/db/_db_migrations.py` by adding the same table and indexes to the reports DDL block.

- [ ] **Step 3: Verify schema syntax locally**

Run:

```bash
python3 -m compileall javdb/storage/db/_db_migrations.py
```

Expected: compile succeeds.

- [ ] **Step 4: Defer remote apply**

Record this command for rollout, but do not run it while writing the plan:

```bash
wrangler d1 execute javdb-reports --remote \
  --file=javdb/migrations/d1/2026_05_27_add_ops_remediation_proposals.sql
```

Expected during rollout: D1 creates `OpsRemediationProposals`.

---

## Task 2: Proposal Models

**Files:**
- Modify: `javdb/ops/diagnosis/models.py`
- Create: `tests/unit/test_ops_remediation_models.py`

- [ ] **Step 1: Write model tests**

Create `tests/unit/test_ops_remediation_models.py`:

```python
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
        command_preview="gh workflow run RollbackD1.yml -f session_id=sid",
        runbook_ref="docs/handbook/en/ops/d1-rollback.md",
        evidence_refs=[EvidenceRef(kind="incident", ref="opsinc_abc")],
        required_checks=["Confirm session status is failed."],
        blocked_reasons=[],
    )

    assert proposal.proposal_id.startswith("opsprop_")
    assert proposal.status == "proposed"
    assert json.loads(proposal.required_checks_json) == ["Confirm session status is failed."]
    assert json.loads(proposal.evidence_refs_json)[0]["kind"] == "incident"
```

- [ ] **Step 2: Add model types**

Add to `javdb/ops/diagnosis/models.py`:

```python
ActionType = Literal[
    "open_runbook",
    "prepare_rollback_workflow",
    "prepare_rerun_workflow",
    "prepare_drift_apply_command",
    "inspect_qb_side_effects",
    "inspect_recovery_outbox",
]
ProposalStatus = Literal["proposed", "approved", "rejected", "expired"]
SafetyLevel = Literal["safe_to_prepare", "requires_review", "blocked"]


def build_proposal_id(incident_id: str, action_type: str) -> str:
    digest = hashlib.sha256(f"{incident_id}|{action_type}".encode("utf-8")).hexdigest()[:24]
    return f"opsprop_{digest}"


@dataclass(frozen=True)
class OpsRemediationProposal:
    proposal_id: str
    incident_id: str
    action_type: ActionType
    status: ProposalStatus
    safety_level: SafetyLevel
    title: str
    rationale: str
    command_preview: str | None
    runbook_ref: str | None
    evidence_refs_json: str
    required_checks_json: str
    blocked_reasons_json: str
    proposed_by: str
    decided_by: str | None
    decision_note: str | None
    created_at: str
    updated_at: str
    decided_at: str | None = None

    @classmethod
    def create(
        cls,
        *,
        incident_id: str,
        action_type: ActionType,
        safety_level: SafetyLevel,
        title: str,
        rationale: str,
        command_preview: str | None = None,
        runbook_ref: str | None = None,
        evidence_refs: list[EvidenceRef] | None = None,
        required_checks: list[str] | None = None,
        blocked_reasons: list[str] | None = None,
        proposed_by: str = "adr026-policy-v1",
    ) -> "OpsRemediationProposal":
        now = utc_now_iso()
        return cls(
            proposal_id=build_proposal_id(incident_id, action_type),
            incident_id=incident_id,
            action_type=action_type,
            status="proposed",
            safety_level=safety_level,
            title=title,
            rationale=rationale,
            command_preview=command_preview,
            runbook_ref=runbook_ref,
            evidence_refs_json=_json_dumps([asdict(ref) for ref in evidence_refs or []]),
            required_checks_json=_json_dumps(required_checks or []),
            blocked_reasons_json=_json_dumps(blocked_reasons or []),
            proposed_by=proposed_by,
            decided_by=None,
            decision_note=None,
            created_at=now,
            updated_at=now,
        )
```

Export `OpsRemediationProposal` and `build_proposal_id` from `javdb/ops/diagnosis/__init__.py`.

- [ ] **Step 3: Run model tests**

Run:

```bash
pytest tests/unit/test_ops_remediation_models.py -v
```

Expected: pass.

---

## Task 3: Deterministic Remediation Policy

**Files:**
- Create: `javdb/ops/diagnosis/remediation.py`
- Create: `tests/unit/test_ops_remediation_policy.py`

- [ ] **Step 1: Write policy tests**

Create `tests/unit/test_ops_remediation_policy.py`:

```python
from __future__ import annotations

from javdb.ops.diagnosis.models import DiagnosisResult, IncidentBundle, OpsIncidentRecord
from javdb.ops.diagnosis.remediation import propose_remediation


def _record(incident_type: str, unsafe_actions: list[str], session_id: str | None = "sid") -> OpsIncidentRecord:
    result = DiagnosisResult(
        incident_type=incident_type,
        confidence="medium",
        confirmed_findings=["Workflow result is failure."],
        likely_causes=[],
        unknowns=[],
        recommended_next_actions=["Inspect logs."],
        unsafe_actions=unsafe_actions,
        evidence_refs=[],
        model_version="fallback-v1",
        detector_version="detectors-v1",
    )
    return OpsIncidentRecord.from_bundle_and_result(
        IncidentBundle(
            trigger_source="workflow_failure",
            run_id="100",
            run_attempt=1,
            session_id=session_id,
            workflow_name="DailyIngestion",
            workflow_result="failure",
        ),
        result,
    ).with_persistence_status("d1_written")


def test_failed_ingestion_with_session_gets_rerun_and_rollback_preparation_proposals():
    proposals = propose_remediation(_record("failed_ingestion", []))

    action_types = {proposal.action_type for proposal in proposals}
    assert "prepare_rerun_workflow" in action_types
    assert "prepare_rollback_workflow" in action_types
    assert all(proposal.status == "proposed" for proposal in proposals)


def test_unsafe_rollback_blocks_rollback_proposal():
    proposals = propose_remediation(_record("failed_ingestion", ["Do not run forced rollback without locating the owning session."], session_id=None))

    rollback = [proposal for proposal in proposals if proposal.action_type == "prepare_rollback_workflow"][0]
    assert rollback.safety_level == "blocked"
    assert "Session id is missing." in rollback.blocked_reasons_json


def test_d1_drift_gets_command_preview_not_apply_execution():
    proposals = propose_remediation(_record("d1_drift", []))

    drift = [proposal for proposal in proposals if proposal.action_type == "prepare_drift_apply_command"][0]
    assert drift.command_preview is not None
    assert "drift_diagnose" in drift.command_preview
    assert "--apply" not in drift.command_preview
```

- [ ] **Step 2: Implement remediation policy**

Create `javdb/ops/diagnosis/remediation.py`:

```python
"""Deterministic, non-executing remediation proposals for ADR-026 Phase 3."""

from __future__ import annotations

import json

from javdb.ops.diagnosis.models import EvidenceRef, OpsIncidentRecord, OpsRemediationProposal

POLICY_VERSION = "adr026-remediation-policy-v1"


def _list(raw: str) -> list[str]:
    value = json.loads(raw or "[]")
    return [str(item) for item in value] if isinstance(value, list) else []


def _has_unsafe_rollback(record: OpsIncidentRecord) -> bool:
    text = " ".join(_list(record.unsafe_actions_json)).lower()
    return "rollback" in text and ("do not" in text or "unsafe" in text or "cannot" in text)


def _incident_ref(record: OpsIncidentRecord) -> EvidenceRef:
    return EvidenceRef(kind="incident", ref=record.incident_id)


def propose_remediation(record: OpsIncidentRecord) -> list[OpsRemediationProposal]:
    proposals: list[OpsRemediationProposal] = [
        OpsRemediationProposal.create(
            incident_id=record.incident_id,
            action_type="open_runbook",
            safety_level="safe_to_prepare",
            title="Open operations troubleshooting runbook",
            rationale="Every incident should start with the read-only runbook context.",
            runbook_ref="docs/handbook/en/ops/troubleshooting.md",
            evidence_refs=[_incident_ref(record)],
            required_checks=["Review confirmed findings and unknowns before taking action."],
            blocked_reasons=[],
            proposed_by=POLICY_VERSION,
        )
    ]

    if record.incident_type == "failed_ingestion":
        proposals.append(
            OpsRemediationProposal.create(
                incident_id=record.incident_id,
                action_type="prepare_rerun_workflow",
                safety_level="requires_review",
                title="Prepare ingestion workflow rerun",
                rationale="The incident is a failed ingestion. A rerun may be appropriate after checking external side effects.",
                command_preview=f"gh workflow run DailyIngestion.yml -F run_id={record.run_id or ''}",
                runbook_ref="docs/handbook/en/ops/troubleshooting.md",
                evidence_refs=[_incident_ref(record)],
                required_checks=[
                    "Confirm the failure was not caused by an active D1 recovery outbox dead-letter.",
                    "Confirm qBittorrent side effects do not make a rerun unsafe.",
                ],
                blocked_reasons=[],
                proposed_by=POLICY_VERSION,
            )
        )

        rollback_blocked = _has_unsafe_rollback(record) or record.session_id is None
        proposals.append(
            OpsRemediationProposal.create(
                incident_id=record.incident_id,
                action_type="prepare_rollback_workflow",
                safety_level="blocked" if rollback_blocked else "requires_review",
                title="Prepare rollback workflow",
                rationale="Rollback may be appropriate only when the session is known and the rollback safety matrix permits it.",
                command_preview=(
                    f"gh workflow run RollbackD1.yml -f session_id={record.session_id}"
                    if record.session_id else None
                ),
                runbook_ref="docs/handbook/en/ops/d1-rollback.md",
                evidence_refs=[_incident_ref(record)],
                required_checks=[
                    "Confirm session status and write mode.",
                    "Confirm rollback does not conflict with committed history.",
                ],
                blocked_reasons=["Session id is missing."] if rollback_blocked else [],
                proposed_by=POLICY_VERSION,
            )
        )

    if record.incident_type == "d1_drift":
        proposals.append(
            OpsRemediationProposal.create(
                incident_id=record.incident_id,
                action_type="prepare_drift_apply_command",
                safety_level="requires_review",
                title="Prepare drift diagnose review command",
                rationale="D1 drift must be reviewed with ADR-009 tooling before any apply step is considered.",
                command_preview="python3 -m apps.cli.db.drift_diagnose --since 24 --json",
                runbook_ref="docs/handbook/en/ops/d1-rollback.md",
                evidence_refs=[_incident_ref(record)],
                required_checks=[
                    "Run the command and confirm the verdict is SAFE_TO_APPLY before considering apply.",
                    "Do not append --apply until a human has reviewed the exact affected rows.",
                ],
                blocked_reasons=[],
                proposed_by=POLICY_VERSION,
            )
        )

    if record.incident_type == "d1_recovery_outbox":
        proposals.append(
            OpsRemediationProposal.create(
                incident_id=record.incident_id,
                action_type="inspect_recovery_outbox",
                safety_level="requires_review",
                title="Inspect D1 recovery outbox",
                rationale="Recovery outbox incidents require ordering-key inspection before any state is marked resolved.",
                command_preview="python3 -m apps.cli.db.replay_d1_recovery --dry-run",
                runbook_ref="docs/handbook/en/ops/d1-rollback.md",
                evidence_refs=[_incident_ref(record)],
                required_checks=["Confirm whether dead-lettered work blocks the affected session ordering key."],
                blocked_reasons=[],
                proposed_by=POLICY_VERSION,
            )
        )

    return proposals
```

- [ ] **Step 3: Run policy tests**

Run:

```bash
pytest tests/unit/test_ops_remediation_policy.py -v
```

Expected: pass.

---

## Task 4: Proposal Repository

**Files:**
- Create: `javdb/storage/repos/ops_remediation_repo.py`
- Create: `tests/unit/test_ops_remediation_repo.py`

- [ ] **Step 1: Write repository tests**

Create `tests/unit/test_ops_remediation_repo.py`:

```python
from __future__ import annotations

import sqlite3

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
```

- [ ] **Step 2: Implement repository**

Create `javdb/storage/repos/ops_remediation_repo.py`:

```python
"""Repository for ADR-026 remediation proposal rows."""

from __future__ import annotations

import sqlite3

from javdb.ops.diagnosis.models import OpsRemediationProposal, utc_now_iso

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


def _row_to_proposal(row: sqlite3.Row) -> OpsRemediationProposal:
    return OpsRemediationProposal(**{column: row[column] for column in _COLUMNS})


class OpsRemediationRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def upsert(self, proposal: OpsRemediationProposal) -> None:
        values = [getattr(proposal, column) for column in _COLUMNS]
        columns = ", ".join(_COLUMNS)
        placeholders = ", ".join(["?"] * len(_COLUMNS))
        updates = ", ".join([f"{column}=excluded.{column}" for column in _COLUMNS if column != "proposal_id"])
        self._conn.execute(
            f"""
            INSERT INTO OpsRemediationProposals ({columns})
            VALUES ({placeholders})
            ON CONFLICT(proposal_id) DO UPDATE SET {updates}
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
```

- [ ] **Step 3: Run repository tests**

Run:

```bash
pytest tests/unit/test_ops_remediation_repo.py -v
```

Expected: pass.

---

## Task 5: Service Integration

**Files:**
- Modify: `javdb/ops/diagnosis/service.py`
- Modify: `tests/unit/test_ops_diagnosis_service.py`

- [ ] **Step 1: Add service test**

Add to `tests/unit/test_ops_diagnosis_service.py`:

```python
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
        session_id="sid",
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
```

- [ ] **Step 2: Extend service signature**

Modify `javdb/ops/diagnosis/service.py`:

```python
from javdb.ops.diagnosis.remediation import propose_remediation
from javdb.storage.db import REPORTS_DB_PATH, get_db
from javdb.storage.repos.ops_remediation_repo import OpsRemediationRepo


def _persist_proposals(record: OpsIncidentRecord, remediation_repo: object | None) -> None:
    proposals = propose_remediation(record)
    if remediation_repo is not None:
        for proposal in proposals:
            remediation_repo.upsert(proposal)
        return
    with get_db(REPORTS_DB_PATH) as conn:
        repo = OpsRemediationRepo(conn)
        for proposal in proposals:
            repo.upsert(proposal)
```

Extend `diagnose_incident(...)`:

```python
def diagnose_incident(
    bundle: IncidentBundle,
    *,
    synthesizer: Synthesizer | None = None,
    repo: object | None = None,
    jsonl_path: str | Path | None = None,
    remediation_repo: object | None = None,
    generate_remediation: bool = False,
) -> OpsIncidentRecord:
    detector_result = detect_incident(bundle)
    result = (synthesizer or synthesize_with_configured_ai)(bundle, detector_result)
    record = OpsIncidentRecord.from_bundle_and_result(bundle, result)
    persisted = persist_incident(record, repo=repo, jsonl_path=jsonl_path)
    if generate_remediation and persisted.persistence_status == "d1_written":
        _persist_proposals(persisted, remediation_repo)
    return persisted
```

- [ ] **Step 3: Keep workflow default unchanged**

Do not change Phase 1 workflow steps to pass `generate_remediation=True` in this task. Proposal generation should be enabled explicitly after the proposal ledger exists in D1.

- [ ] **Step 4: Run service tests**

Run:

```bash
pytest tests/unit/test_ops_diagnosis_service.py -v
```

Expected: pass.

---

## Task 6: Python API Proposal Surface

**Files:**
- Modify: `apps/api/schemas/diagnostics.py`
- Modify: `apps/api/routers/diagnostics.py`
- Modify: `tests/unit/test_ops_diagnostics_api.py`

- [ ] **Step 1: Add API tests**

Add to `tests/unit/test_ops_diagnostics_api.py`:

```python
def test_ops_remediation_proposals_returns_items(monkeypatch, admin_client: TestClient):
    from apps.api.routers import diagnostics
    from javdb.ops.diagnosis.models import OpsRemediationProposal

    proposal = OpsRemediationProposal.create(
        incident_id="opsinc_test",
        action_type="open_runbook",
        safety_level="safe_to_prepare",
        title="Open runbook",
        rationale="Review troubleshooting runbook.",
    )
    monkeypatch.setattr(diagnostics, "_list_remediation_proposals", lambda _incident_id: [proposal])

    response = admin_client.get("/api/diag/ops-incidents/opsinc_test/remediation-proposals")

    assert response.status_code == 200
    assert response.json()["items"][0]["action_type"] == "open_runbook"


def test_ops_remediation_decision_records_only_decision(monkeypatch, admin_client: TestClient):
    from apps.api.routers import diagnostics
    from javdb.ops.diagnosis.models import OpsRemediationProposal

    proposal = OpsRemediationProposal.create(
        incident_id="opsinc_test",
        action_type="open_runbook",
        safety_level="safe_to_prepare",
        title="Open runbook",
        rationale="Review troubleshooting runbook.",
    )
    decided = OpsRemediationProposal(
        **{**proposal.__dict__, "status": "approved", "decided_by": "admin", "decision_note": "Reviewed."}
    )
    monkeypatch.setattr(diagnostics, "_record_remediation_decision", lambda *_args, **_kwargs: decided)

    response = admin_client.post(
        f"/api/diag/remediation-proposals/{proposal.proposal_id}/decision",
        json={"status": "approved", "decision_note": "Reviewed."},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert response.json()["decision_note"] == "Reviewed."
```

- [ ] **Step 2: Add schemas**

Add to `apps/api/schemas/diagnostics.py`:

```python
class OpsRemediationProposalSchema(BaseModel):
    proposal_id: str
    incident_id: str
    action_type: str
    status: str
    safety_level: str
    title: str
    rationale: str
    command_preview: Optional[str] = None
    runbook_ref: Optional[str] = None
    evidence_refs: list[EvidenceRefSchema]
    required_checks: list[str]
    blocked_reasons: list[str]
    proposed_by: str
    decided_by: Optional[str] = None
    decision_note: Optional[str] = None
    created_at: str
    updated_at: str
    decided_at: Optional[str] = None


class OpsRemediationProposalListResponse(BaseModel):
    items: list[OpsRemediationProposalSchema]


class OpsRemediationDecisionRequest(BaseModel):
    status: Literal["approved", "rejected"]
    decision_note: Optional[str] = None
```

Add the schema names to `__all__`.

- [ ] **Step 3: Add router helpers**

Add to `apps/api/routers/diagnostics.py`:

```python
from javdb.storage.repos.ops_remediation_repo import OpsRemediationRepo


def _proposal_to_schema(proposal) -> OpsRemediationProposalSchema:
    return OpsRemediationProposalSchema(
        proposal_id=proposal.proposal_id,
        incident_id=proposal.incident_id,
        action_type=proposal.action_type,
        status=proposal.status,
        safety_level=proposal.safety_level,
        title=proposal.title,
        rationale=proposal.rationale,
        command_preview=proposal.command_preview,
        runbook_ref=proposal.runbook_ref,
        evidence_refs=[EvidenceRefSchema(**item) for item in json.loads(proposal.evidence_refs_json)],
        required_checks=json.loads(proposal.required_checks_json),
        blocked_reasons=json.loads(proposal.blocked_reasons_json),
        proposed_by=proposal.proposed_by,
        decided_by=proposal.decided_by,
        decision_note=proposal.decision_note,
        created_at=proposal.created_at,
        updated_at=proposal.updated_at,
        decided_at=proposal.decided_at,
    )


def _list_remediation_proposals(incident_id: str):
    with get_db(REPORTS_DB_PATH) as conn:
        return OpsRemediationRepo(conn).list_for_incident(incident_id)


def _record_remediation_decision(
    proposal_id: str,
    *,
    status: str,
    decided_by: str,
    decision_note: str | None,
):
    with get_db(REPORTS_DB_PATH) as conn:
        return OpsRemediationRepo(conn).record_decision(
            proposal_id,
            status=status,
            decided_by=decided_by,
            decision_note=decision_note,
        )
```

- [ ] **Step 4: Add endpoints**

Add to `apps/api/routers/diagnostics.py` before `/ops-incidents/{incident_id}`:

```python
@router.get(
    "/ops-incidents/{incident_id}/remediation-proposals",
    response_model=OpsRemediationProposalListResponse,
)
def list_ops_remediation_proposals(
    incident_id: str,
    _user: Dict[str, Any] = Depends(_require_auth),
) -> OpsRemediationProposalListResponse:
    return OpsRemediationProposalListResponse(
        items=[_proposal_to_schema(item) for item in _list_remediation_proposals(incident_id)]
    )


@router.post(
    "/remediation-proposals/{proposal_id}/decision",
    response_model=OpsRemediationProposalSchema,
)
def decide_ops_remediation_proposal(
    proposal_id: str,
    body: OpsRemediationDecisionRequest,
    current: Dict[str, Any] = Depends(require_role("admin")),
) -> OpsRemediationProposalSchema:
    proposal = _record_remediation_decision(
        proposal_id,
        status=body.status,
        decided_by=str(current.get("sub") or "unknown"),
        decision_note=body.decision_note,
    )
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return _proposal_to_schema(proposal)
```

This endpoint records the decision only. It must not call rollback, rerun, drift apply, qB, or recovery mutation code.

- [ ] **Step 5: Run API tests**

Run:

```bash
pytest tests/unit/test_ops_diagnostics_api.py -v
```

Expected: pass.

---

## Task 7: Cloudflare Worker API Parity

**Files:**
- Modify: `../JAVDB_AutoSpider_Web/server/routes/diagnostics.ts`
- Modify: `../JAVDB_AutoSpider_Web/server/__tests__/diagnostics-routes.test.ts`

- [ ] **Step 1: Add Worker tests**

Add to `server/__tests__/diagnostics-routes.test.ts` in the Web repo:

```ts
async function seedRemediationProposalTable(db: D1Database) {
  await db.prepare(`
    CREATE TABLE IF NOT EXISTS OpsRemediationProposals (
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
  `).run();
  await db.prepare("DELETE FROM OpsRemediationProposals").run();
  await db.prepare(`
    INSERT INTO OpsRemediationProposals (
      proposal_id, incident_id, action_type, status, safety_level, title, rationale,
      command_preview, runbook_ref, evidence_refs_json, required_checks_json,
      blocked_reasons_json, proposed_by, decided_by, decision_note, created_at, updated_at, decided_at
    )
    VALUES (
      'opsprop_test', 'opsinc_test', 'open_runbook', 'proposed', 'safe_to_prepare',
      'Open runbook', 'Review runbook.', NULL, 'docs/handbook/en/ops/troubleshooting.md',
      '[]', '["Review confirmed findings."]', '[]', 'adr026-policy-v1',
      NULL, NULL, '2026-05-27T00:00:00Z', '2026-05-27T00:00:00Z', NULL
    )
  `).run();
}

it("GET /api/diag/ops-incidents/:id/remediation-proposals returns proposals", async () => {
  await seedRemediationProposalTable(env.REPORTS_DB);
  const token = await getToken();

  const res = await app.request("/api/diag/ops-incidents/opsinc_test/remediation-proposals", {
    headers: { Authorization: `Bearer ${token}` },
  }, env);

  expect(res.status).toBe(200);
  const data = await res.json() as any;
  expect(data.items[0].proposal_id).toBe("opsprop_test");
});
```

- [ ] **Step 2: Add Worker mapping helper**

Modify `server/routes/diagnostics.ts`:

```ts
function mapRemediationProposal(row: any) {
  return {
    proposal_id: row.proposal_id,
    incident_id: row.incident_id,
    action_type: row.action_type,
    status: row.status,
    safety_level: row.safety_level,
    title: row.title,
    rationale: row.rationale,
    command_preview: row.command_preview ?? null,
    runbook_ref: row.runbook_ref ?? null,
    evidence_refs: parseJsonArray(row.evidence_refs_json),
    required_checks: parseJsonArray(row.required_checks_json),
    blocked_reasons: parseJsonArray(row.blocked_reasons_json),
    proposed_by: row.proposed_by,
    decided_by: row.decided_by ?? null,
    decision_note: row.decision_note ?? null,
    created_at: row.created_at,
    updated_at: row.updated_at,
    decided_at: row.decided_at ?? null,
  };
}
```

- [ ] **Step 3: Add Worker proposal routes**

Add to `server/routes/diagnostics.ts`:

```ts
diagnosticsRoutes.get("/ops-incidents/:incident_id/remediation-proposals", async (c) => {
  const incidentId = c.req.param("incident_id");
  const rows = await c.env.REPORTS_DB
    .prepare("SELECT * FROM OpsRemediationProposals WHERE incident_id = ? ORDER BY created_at ASC")
    .bind(incidentId)
    .all();
  return c.json({ items: rows.results.map(mapRemediationProposal) });
});

diagnosticsRoutes.post("/remediation-proposals/:proposal_id/decision", requireRole("admin"), async (c) => {
  const proposalId = c.req.param("proposal_id");
  const body = await c.req.json<{ status: string; decision_note?: string | null }>();
  if (body.status !== "approved" && body.status !== "rejected") {
    throw new HTTPException(422, { message: "status must be approved or rejected" });
  }
  const user = c.get("user");
  const now = new Date().toISOString();
  await c.env.REPORTS_DB
    .prepare(`
      UPDATE OpsRemediationProposals
      SET status = ?, decided_by = ?, decision_note = ?, decided_at = ?, updated_at = ?
      WHERE proposal_id = ?
    `)
    .bind(body.status, user.sub, body.decision_note ?? null, now, now, proposalId)
    .run();
  const row = await c.env.REPORTS_DB
    .prepare("SELECT * FROM OpsRemediationProposals WHERE proposal_id = ?")
    .bind(proposalId)
    .first();
  if (!row) throw new HTTPException(404, { message: "Proposal not found" });
  return c.json(mapRemediationProposal(row));
});
```

Do not call GitHub Actions, rollback, drift apply, qB, or recovery APIs from these routes.

- [ ] **Step 4: Run Worker tests**

Run from the Web repo:

```bash
npm run test:server -- server/__tests__/diagnostics-routes.test.ts
```

Expected: pass.

---

## Task 8: Web Proposal Review UX

**Files:**
- Modify: `../JAVDB_AutoSpider_Web/src/api/diagnostics.ts`
- Modify: `../JAVDB_AutoSpider_Web/src/pages/diagnostics/OpsIncidentsPage.vue`
- Create: `../JAVDB_AutoSpider_Web/tests/unit/ops-remediation-api.spec.ts`
- Create: `../JAVDB_AutoSpider_Web/tests/e2e/ops-remediation.spec.ts`

- [ ] **Step 1: Add frontend API tests**

Create `tests/unit/ops-remediation-api.spec.ts` in the Web repo:

```ts
import { describe, expect, it, vi } from 'vitest'
import { http } from '@/api/client'
import { decideRemediationProposal, listRemediationProposals } from '@/api/diagnostics'

describe('ops remediation proposal API', () => {
  it('lists proposals for an incident', async () => {
    const spy = vi.spyOn(http, 'get').mockResolvedValueOnce({ data: { items: [] } })

    await listRemediationProposals('opsinc_test')

    expect(spy).toHaveBeenCalledWith('/api/diag/ops-incidents/opsinc_test/remediation-proposals')
  })

  it('records proposal decision', async () => {
    const spy = vi.spyOn(http, 'post').mockResolvedValueOnce({ data: { proposal_id: 'opsprop_test', status: 'approved' } })

    const result = await decideRemediationProposal('opsprop_test', {
      status: 'approved',
      decision_note: 'Reviewed.',
    })

    expect(result.status).toBe('approved')
    expect(spy).toHaveBeenCalledWith('/api/diag/remediation-proposals/opsprop_test/decision', {
      status: 'approved',
      decision_note: 'Reviewed.',
    })
  })
})
```

- [ ] **Step 2: Add frontend API contracts**

Modify `src/api/diagnostics.ts` in the Web repo:

```ts
export interface OpsRemediationProposal {
  proposal_id: string
  incident_id: string
  action_type: string
  status: string
  safety_level: string
  title: string
  rationale: string
  command_preview?: string | null
  runbook_ref?: string | null
  evidence_refs: EvidenceRef[]
  required_checks: string[]
  blocked_reasons: string[]
  proposed_by: string
  decided_by?: string | null
  decision_note?: string | null
  created_at: string
  updated_at: string
  decided_at?: string | null
}

export interface OpsRemediationProposalListResponse {
  items: OpsRemediationProposal[]
}

export interface RemediationDecisionRequest {
  status: 'approved' | 'rejected'
  decision_note?: string | null
}

export async function listRemediationProposals(incidentId: string): Promise<OpsRemediationProposalListResponse> {
  const { data } = await http.get<OpsRemediationProposalListResponse>(
    `/api/diag/ops-incidents/${incidentId}/remediation-proposals`,
  )
  return data
}

export async function decideRemediationProposal(
  proposalId: string,
  body: RemediationDecisionRequest,
): Promise<OpsRemediationProposal> {
  const { data } = await http.post<OpsRemediationProposal>(
    `/api/diag/remediation-proposals/${proposalId}/decision`,
    body,
  )
  return data
}
```

- [ ] **Step 3: Extend incident detail UI**

Modify `src/pages/diagnostics/OpsIncidentsPage.vue`:

- Load remediation proposals when the selected incident changes.
- Show each proposal with status, safety level, title, rationale, required checks, blocked reasons, runbook ref, and command preview.
- Render command preview in a copyable read-only code block.
- Disable approval when `safety_level === 'blocked'`.
- Use an explicit confirmation modal for approve/reject decisions.
- After approve/reject, reload proposals.
- Do not execute the command preview or call any mutation endpoint other than decision recording.

- [ ] **Step 4: Add E2E smoke**

Create `tests/e2e/ops-remediation.spec.ts` in the Web repo:

```ts
import { test, expect } from '@playwright/test'
import { loginViaUi, markOnboarded, resetBackend } from './fixtures/auth'

test.describe('Ops remediation proposal review', () => {
  test.beforeEach(async ({ request, page }) => {
    await resetBackend(request)
    await markOnboarded(request, page)
    await page.route('**/api/diag/ops-incidents', async (route) => {
      await route.fulfill({
        json: {
          items: [{
            incident_id: 'opsinc_test',
            trigger_source: 'workflow_failure',
            run_id: '100',
            run_attempt: 1,
            session_id: 'sid',
            incident_type: 'failed_ingestion',
            status: 'open',
            persistence_status: 'd1_written',
            model_version: 'fallback-v1',
            detector_version: 'detectors-v1',
            confidence: 'low',
            confirmed_findings: ['Workflow result is failure.'],
            likely_causes: [],
            unknowns: [],
            recommended_next_actions: ['Inspect logs.'],
            unsafe_actions: [],
            evidence_refs: [],
            created_at: '2026-05-27T00:00:00Z',
            updated_at: '2026-05-27T00:00:00Z',
            resolved_at: null,
          }],
        },
      })
    })
    await page.route('**/api/diag/ops-incidents/analytics', async (route) => {
      await route.fulfill({ json: { total: 1, by_type: {}, by_status: {}, by_confidence: {}, open_high_confidence: 0 } })
    })
    await page.route('**/api/diag/ops-incidents/opsinc_test/remediation-proposals', async (route) => {
      await route.fulfill({
        json: {
          items: [{
            proposal_id: 'opsprop_test',
            incident_id: 'opsinc_test',
            action_type: 'prepare_rollback_workflow',
            status: 'proposed',
            safety_level: 'blocked',
            title: 'Prepare rollback workflow',
            rationale: 'Rollback requires a known safe session.',
            command_preview: null,
            runbook_ref: 'docs/handbook/en/ops/d1-rollback.md',
            evidence_refs: [],
            required_checks: ['Confirm session status.'],
            blocked_reasons: ['Session id is missing.'],
            proposed_by: 'adr026-policy-v1',
            decided_by: null,
            decision_note: null,
            created_at: '2026-05-27T00:00:00Z',
            updated_at: '2026-05-27T00:00:00Z',
            decided_at: null,
          }],
        },
      })
    })
  })

  test('shows blocked proposal without execution controls', async ({ page }) => {
    await loginViaUi(page)
    await page.goto('/diag/ops-incidents')
    await page.getByText('opsinc_test').click()
    await expect(page.getByText('Prepare rollback workflow')).toBeVisible()
    await expect(page.getByText('Session id is missing.')).toBeVisible()
    await expect(page.getByRole('button', { name: /execute|rollback|rerun|apply/i })).toHaveCount(0)
  })
})
```

- [ ] **Step 5: Run Web tests**

Run from the Web repo:

```bash
npm run test:unit -- tests/unit/ops-remediation-api.spec.ts
npm run test:e2e -- tests/e2e/ops-remediation.spec.ts
```

Expected: pass.

---

## Task 9: Documentation And Workflow Review

**Files:**
- Modify: `docs/handbook/en/ops/troubleshooting.md`
- Modify: `docs/handbook/zh/ops/troubleshooting.md`
- Modify: `docs/handbook/en/ops/d1-rollback.md`
- Modify: `docs/handbook/zh/ops/d1-rollback.md`

- [ ] **Step 1: Document proposal semantics in English**

Add to `docs/handbook/en/ops/troubleshooting.md`:

````markdown
### Gated Remediation Proposals

ADR-026 Phase 3 can attach remediation proposals to an incident. A proposal is an auditable recommendation, not an executed job. The system may show a runbook link or command preview, but an operator must still run the underlying rollback, rerun, drift apply, qB inspection, or recovery command manually.

Proposal states:

- `proposed` - generated by the deterministic policy.
- `approved` - an admin accepted the recommendation after review.
- `rejected` - an admin declined it.
- `expired` - no longer valid for the current incident state.

Safety levels:

- `safe_to_prepare` - safe to show as a next read-only step.
- `requires_review` - review the required checks before using the command preview.
- `blocked` - do not act until blocked reasons are resolved.
````

- [ ] **Step 2: Mirror proposal semantics in Chinese**

Add to `docs/handbook/zh/ops/troubleshooting.md`:

````markdown
### 门控式修复建议

ADR-026 Phase 3 可以给 incident 附加 remediation proposal。proposal 是可审计的建议，不是已经执行的任务。系统可以展示 runbook 链接或 command preview，但 operator 仍必须手动执行底层 rollback、rerun、drift apply、qB inspection 或 recovery 命令。

Proposal 状态：

- `proposed` - 由确定性 policy 生成。
- `approved` - admin 复核后接受该建议。
- `rejected` - admin 拒绝该建议。
- `expired` - 对当前 incident 状态已经不再有效。

Safety level：

- `safe_to_prepare` - 可以安全展示为下一步只读操作。
- `requires_review` - 使用 command preview 前必须复核 required checks。
- `blocked` - blocked reasons 解决前不能执行。
````

- [ ] **Step 3: Clarify rollback SOP**

Add to `docs/handbook/en/ops/d1-rollback.md`:

````markdown
ADR-026 remediation proposals may point to this rollback SOP, but they do not replace the safety matrix. An `approved` proposal means an operator accepted the recommendation; it does not mean rollback has run or that the rollback CLI can skip its own checks.
````

Mirror in `docs/handbook/zh/ops/d1-rollback.md`.

- [ ] **Step 4: Review GitHub Actions impact**

No GitHub Actions workflow should execute remediation proposals in Phase 3. Verify this with:

```bash
rg -n "remediation|OpsRemediation|RollbackD1|workflow run|drift_diagnose.*--apply" .github/workflows
```

Expected: no ADR-026 proposal workflow execution is present.

- [ ] **Step 5: Run documentation checks**

Run:

```bash
git diff --check -- \
  docs/design/ADR-026-AI-Operations-Diagnosis/IMP-ADR026-03-ai-ops-diagnosis-gated-remediation.md \
  docs/handbook/en/ops/troubleshooting.md \
  docs/handbook/zh/ops/troubleshooting.md \
  docs/handbook/en/ops/d1-rollback.md \
  docs/handbook/zh/ops/d1-rollback.md
```

Expected: no output.

---

## Task 10: Verification And Closeout

- [ ] **Step 1: Run Python tests**

Run:

```bash
pytest \
  tests/unit/test_ops_remediation_models.py \
  tests/unit/test_ops_remediation_policy.py \
  tests/unit/test_ops_remediation_repo.py \
  tests/unit/test_ops_diagnosis_service.py \
  tests/unit/test_ops_diagnostics_api.py \
  -v
```

Expected: all pass.

- [ ] **Step 2: Run Web tests**

Run from `../JAVDB_AutoSpider_Web`:

```bash
npm run test:server -- server/__tests__/diagnostics-routes.test.ts
npm run test:unit -- tests/unit/ops-remediation-api.spec.ts
npm run test:e2e -- tests/e2e/ops-remediation.spec.ts
```

Expected: all pass.

- [ ] **Step 3: Run static checks**

Run from the main repo:

```bash
python3 -m compileall javdb/ops/diagnosis javdb/storage/repos apps/api/routers/diagnostics.py apps/api/schemas/diagnostics.py
git diff --check
```

Run from the Web repo:

```bash
npm run typecheck
npm run lint
```

Expected: no failures.

- [ ] **Step 4: Manual safety smoke**

Open the Web UI at:

```text
http://localhost:5173/diag/ops-incidents
```

Expected:

- Incident detail shows remediation proposals when present.
- Blocked proposals cannot be approved.
- Approve/reject records a decision only.
- No UI element directly executes rollback, rerun, drift apply, qB cleanup, or recovery resolve.

- [ ] **Step 5: Commit**

Commit only the Phase 3 source, tests, and docs. Do not commit `reports/` data files.

```bash
git add \
  javdb/migrations/d1/2026_05_27_add_ops_remediation_proposals.sql \
  javdb/storage/db/_db_migrations.py \
  javdb/ops/diagnosis \
  javdb/storage/repos/ops_remediation_repo.py \
  apps/api/schemas/diagnostics.py \
  apps/api/routers/diagnostics.py \
  tests/unit/test_ops_remediation_models.py \
  tests/unit/test_ops_remediation_policy.py \
  tests/unit/test_ops_remediation_repo.py \
  tests/unit/test_ops_diagnosis_service.py \
  tests/unit/test_ops_diagnostics_api.py \
  docs/handbook/en/ops/troubleshooting.md \
  docs/handbook/zh/ops/troubleshooting.md \
  docs/handbook/en/ops/d1-rollback.md \
  docs/handbook/zh/ops/d1-rollback.md
git commit -m "feat(ops): add gated remediation proposals"
```

Commit the Web repo changes separately:

```bash
cd ../JAVDB_AutoSpider_Web
git add \
  server/routes/diagnostics.ts \
  server/__tests__/diagnostics-routes.test.ts \
  src/api/diagnostics.ts \
  src/pages/diagnostics/OpsIncidentsPage.vue \
  tests/unit/ops-remediation-api.spec.ts \
  tests/e2e/ops-remediation.spec.ts
git commit -m "feat(diagnostics): add remediation proposal review"
```
