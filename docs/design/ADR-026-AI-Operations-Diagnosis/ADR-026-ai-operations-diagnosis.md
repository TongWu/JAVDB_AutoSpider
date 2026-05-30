# ADR-026: AI Operations Diagnosis Assistant

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Accepted - Phase 1 delivered; Phases 2-3 pending                     |
| **Date**    | 2026-05-27                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-009](../_archive/ADR-009-D1-Drift-Classifier/ADR-009-d1-drift-classifier-and-diagnose.md), [ADR-010](../_archive/ADR-010-D1-Access-Port/ADR-010-d1-access-port.md), [ADR-015](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md) |

## Related

- [IMP-ADR026-01](IMP-ADR026-01-ai-ops-diagnosis-readonly.md)
- [IMP-ADR026-02](IMP-ADR026-02-ai-ops-diagnosis-history-analytics.md)
- [IMP-ADR026-03](IMP-ADR026-03-ai-ops-diagnosis-gated-remediation.md)
- [ADR-009](../_archive/ADR-009-D1-Drift-Classifier/ADR-009-d1-drift-classifier-and-diagnose.md)
- [ADR-010](../_archive/ADR-010-D1-Access-Port/ADR-010-d1-access-port.md)
- [ADR-015](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md)

## Context

The repository already has several operational signals and repair tools:

- ADR-009 provides read-only drift diagnosis and guarded apply for D1/SQLite
  pending-write drift.
- ADR-010 provides a D1 access port, recovery outbox, and startup replay.
- ADR-015 keeps notify/email and CLI boundaries explicit.
- The current API already exposes a small diagnostics surface for JavDB session
  state.
- The email path already emits short operational advisories.

Those pieces are useful, but they are still mostly single-purpose tools. When a
run fails, the operator still has to gather evidence from multiple places:
workflow result, session lifecycle, D1 drift, rollback safety, email summary,
and relevant runbook pages. The same incident can also need different handling
depending on whether the problem is a failed ingestion run, a stale session, a
pending orphan, a dead-lettered recovery event, or an unsafe rollback
candidate.

This ADR defines an AI-assisted diagnostics layer that stays on the safe side
of the boundary: first collect evidence deterministically, then let the model
summarize and rank likely causes, unknowns, and next actions. The first version
must not auto-fix anything.

## Decision

Create a D1-canonical, explainable, read-only AI operations diagnosis assistant.
Phase 1 collects incident evidence into a structured bundle, runs a two-stage
detector + model diagnosis flow, persists the incident record, and surfaces the
result through CLI, API, and short email summaries.

ADR-026 is advisory only in Phase 1. It does not auto rollback, auto rerun a
workflow, auto modify D1, auto delete qBittorrent tasks, or auto patch any
runtime state.

### Design Decisions

D1. **Read-only by default** - The assistant may explain and recommend, but it
must not directly mutate D1, re-run workflows, roll back sessions, or delete
qBittorrent tasks in Phase 1.

D2. **Evidence first, model second** - Raw logs are not fed directly into the
model. A deterministic collector builds a compact incident bundle first; the
model only sees curated facts and references.

D3. **D1 is the source of truth** - Incident records, diagnosis metadata, and
status updates are stored in D1 first. SQLite may mirror the tables for local
debugging, but it is not authoritative.

D4. **Structured output is required** - Every diagnosis must expose
`confirmed_findings`, `likely_causes`, `unknowns`,
`recommended_next_actions`, `unsafe_actions`, and `evidence_refs` rather than
an opaque answer blob.

D5. **Detectors own facts, the model owns synthesis** - Deterministic rules and
lightweight classifiers collect candidate facts. The model ranks and explains
them, but it may not invent evidence that is not present in the bundle.

D6. **Fail closed when evidence is incomplete** - If the bundle is missing
critical data, the assistant must mark the gap as `unknown` instead of
guessing. The safe fallback is human review.

D7. **Persist incidents, not raw logs** - Store structured summaries and
evidence pointers, not full raw log dumps. The bundle should keep only the
minimum needed to reproduce the diagnosis.

D8. **Short email, rich UI/API** - Email should contain only a short summary and
a link or pointer to the diagnosis record. Detailed reasoning belongs in the
CLI and API.

D9. **Phase 1 is bounded** - The first rollout covers DailyIngestion,
AdHocIngestion, TestIngestion, session lifecycle anomalies, D1/SQLite drift,
pending orphans, recovery outbox/dead-letter states, rollback safety, and
qBittorrent side-effect checks. It does not cover deep qB file-filter
diagnostics, PikPak/Rclone deep diagnosis, parser auto-fix, front-end
performance, or proxy bandit tuning.

D10. **Safety over automation** - Later phases may add approval flows or gated
remediation suggestions, but automatic remediation is explicitly out of scope
for Phase 1.

### Incident Bundle

The collector builds a compact `incident_bundle` from deterministic inputs:

- triggering source: manual CLI, workflow failure, or targeted operator action;
- `run_id`, `run_attempt`, `session_id`, and timestamp context;
- workflow status and key job results;
- D1 drift / pending orphan indicators;
- rollback safety signals and session lifecycle state;
- email summary fragments;
- relevant log snippets and known verdicts;
- runbook references and related ADR pointers;
- optional qBittorrent side-effect metadata when the incident touches uploads.

The bundle should be small enough to persist and inspect, but rich enough for a
useful diagnosis. It is a curated representation, not a full log archive.

### Detector Layer

Before any model call, the assistant runs deterministic detectors. These
detectors may:

- classify whether the incident is likely a failed ingestion run, a stale
  session, D1/SQLite drift, a pending orphan, or a recovery outbox issue;
- extract known symptoms from logs or workflow outputs;
- flag whether rollback looks safe, unsafe, or unknown;
- tag whether qBittorrent side effects already happened.

Detectors are intentionally narrow. If a detector cannot prove a fact, it
should emit a weaker signal or an unknown state instead of forcing a verdict.

### AI Diagnosis Layer

The model takes the bundle and detector output, then produces a structured
diagnosis. The output must separate:

- `confirmed_findings` - facts supported by the bundle;
- `likely_causes` - best explanation of why the incident happened;
- `unknowns` - missing facts that block stronger confidence;
- `recommended_next_actions` - operator actions or runbook steps;
- `unsafe_actions` - actions that should not be taken yet;
- `confidence` - a coarse confidence indicator for the diagnosis.

The model is not allowed to invent new facts, recommend destructive actions
without a safety warning, or override a detector-proven unknown state.

### Incident Store

Phase 1 stores incidents in a D1-canonical record set. A reasonable record shape
is:

- `incident_id`
- `trigger_source`
- `run_id`
- `run_attempt`
- `session_id`
- `incident_type`
- `status`
- `confirmed_findings_json`
- `likely_causes_json`
- `unknowns_json`
- `recommended_next_actions_json`
- `unsafe_actions_json`
- `evidence_refs_json`
- `created_at`
- `updated_at`
- `resolved_at`

The exact table names can evolve during implementation, but the core contract is
stable: incidents are persistent, queryable, and versioned, and they do not
depend on keeping the full raw log corpus in the database.

If D1 persistence fails, the assistant should still return the diagnosis and
record a degraded persistence status, for example `d1_failed_jsonl_written`.
A JSONL fallback is acceptable as a durability backstop, but D1 remains the
canonical store.

### Entry Points

The assistant should be reachable from three surfaces:

1. CLI - a future command such as `python3 -m apps.cli.ops.diagnose_run`.
2. API/Web - structured incident lookup and diagnosis rendering.
3. Email - a short advisory that points at the diagnosis record instead of
   repeating the full explanation.

CLI remains the primary operator entry point. API/Web is the read interface.
Email is notification only.

### Safety Boundary

The assistant must never perform these actions in Phase 1:

- rollback a session automatically;
- rerun DailyIngestion, AdHocIngestion, or TestIngestion automatically;
- modify D1 based on diagnosis output;
- delete qBittorrent tasks automatically;
- mark a recovery event resolved without operator intent.

If the model is uncertain, the assistant should say so. If a detector says the
action is unsafe, the model may not override it.

## Consequences

### Positive

- Operators get a single diagnosis artifact instead of manually stitching
  together run logs, session state, drift data, and runbook pages.
- The design is compatible with the repo's D1-first direction.
- Structured incident records can later support search, similarity lookup, and
  safer remediation flows.
- The first rollout is low risk because it is advisory only.

### Negative

- New incident storage and diagnosis plumbing adds operational surface area.
- The assistant will still need careful prompt and detector tuning.
- A read-only design can suggest next steps, but it cannot remove all manual
  intervention in the first phase.

### Risks

- **Overconfidence in diagnosis** - Mitigation: require structured evidence,
  keep unknowns explicit, and fail closed when evidence is thin.
- **Too much raw data** - Mitigation: persist summaries and references only,
  not the full log archive.
- **Accidental auto-remediation creep** - Mitigation: keep Phase 1 purely
  advisory and gate any later remediation behind a separate ADR or phase.
- **Duplicate operational tools** - Mitigation: align with ADR-009 and ADR-010
  rather than inventing a second rollback or drift system.

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 | [IMP-ADR026-01](IMP-ADR026-01-ai-ops-diagnosis-readonly.md) (Completed 2026-05-27) | D1 incident schema, deterministic incident bundle collector, detector layer, AI synthesis, CLI/API read-only lookup, short email summary, JSONL fallback | No automatic remediation |
| Phase 2 | [IMP-ADR026-02](IMP-ADR026-02-ai-ops-diagnosis-history-analytics.md) | UI history browsing, deterministic similarity search, and richer incident analytics | Remediation approval flow |
| Phase 3 | [IMP-ADR026-03](IMP-ADR026-03-ai-ops-diagnosis-gated-remediation.md) | Gated remediation suggestions with human confirmation and explicit safety rails | Any fully automatic fix |

## References

- `apps/cli/db/drift_diagnose.py` - existing read-only diagnose CLI boundary.
- `javdb/storage/drift_diagnose.py` - existing canonical drift diagnosis service.
- `apps/api/routers/diagnostics.py` - current diagnostics API surface.
- `apps/api/schemas/diagnostics.py` - current diagnostics schema shape.
- `javdb/integrations/notify/email.py` - current email advisory path.
- `docs/handbook/en/ops/d1-rollback.md` - rollback and pending-mode operator SOP.
- `docs/handbook/en/ops/troubleshooting.md` - current troubleshooting reference.

## Status Log

- 2026-05-27: Proposed as ADR-026.
- 2026-05-27: Phase 1 delivered and verified; Phases 2-3 remain proposed.
