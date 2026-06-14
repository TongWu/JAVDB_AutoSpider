# ADR-050: Consolidate the Triplicated `pending_session_verify` Record Builder

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Completed (2026-06-14) — implemented by [IMP-ADR050-01](IMP-ADR050-01-pending-verify-builder.md) |
| **Date**    | 2026-06-13                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-042](../../ADR-042-D1-Atomic-Commit-Boundaries/ADR-042-d1-atomic-commit-boundaries.md) (`pending_session_verify` is a **diagnostic write** — never authoritative), [ADR-019](../ADR-019-Session-Lifecycle-Authority/ADR-019-session-lifecycle-authority.md) (the lifecycle whose commit/fail/rollback events emit the record), [ADR-036](../../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md) (the `PipelineEvent` spine is a separate codepath — not this JSONL sidecar), [ADR-026](../../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md) (`log_analysis` consumes the record for pending alerts) |

> Originated from the 2026-06-13 architecture review (Candidate 3 — "consolidate the `pending_session_verify` builder"): [architecture-review-2026-06-13.html](../../architecture/architecture-review-2026-06-13.html).

## Context

`pending_session_verify` is a **diagnostic** JSONL record (ADR-042 write class) appended to `reports/D1/d1_drift.jsonl` at the end of every pending-mode session lifecycle event. It is built **three times, independently**:

- `apps/cli/db/commit_session.py` `_emit_pending_verify` (L239–309) — the richest emitter (also writes `GITHUB_OUTPUT`, runs a shadow-audit).
- `javdb/storage/sessions/commit.py` `_emit_commit_metrics` (L135–192) — whose docstring **admits it is a "Simplified version"** of the CLI emitter. That admission *is* the schema-drift evidence.
- `javdb/storage/rollback/core.py` `_emit_pending_verify_for_session` (L182–251) — adds rollback-only fields (`rollback_mode`, `cleanup_path_mismatch_count`).

All three share a **17-field core schema** (`kind`, `ts`, `source`, `session_id`, `write_mode`, `final_status`, `pending_staged_count`, `pending_applied_count`, `pending_residual_count`, `commit_attempts`, `commit_duration_ms`, `hrefs_processed`, `torrents_upserted`, `torrents_deleted`, `movies_upserted`, `worker_stage_rollback_failed`, `shadow_audit_enabled`) plus source-specific extras. The consumer — `javdb/integrations/notify/email/log_analysis.py` `_evaluate_pending_alerts` (L1044–1055) and `_CRITICAL_ALERT_FIELDS` (L986–990) — couples to these field names as **string literals**, with no compile-time link to any producer. A producer renaming a field silently breaks the alert.

Deletion test: the three emitters are not pass-throughs — delete one and its lifecycle event loses its diagnostic record. But they re-implement one schema three times; the schema, not the emit, is the thing that should be deep.

## Decision

Extract one pure builder + a single exported field-name vocabulary; the three emitters and the one parser bind to it.

### Design Decisions

**D1. One pure builder in `javdb/storage/sessions/pending_verify.py`.** `build_pending_verify_record(session_id, *, source, write_mode, final_status, drain, stats, commit_attempts, commit_duration_ms, shadow_audit_result=None, rollback_extras=None) -> dict`. Pure — no I/O. It lives in the `storage/sessions/` layer alongside `lifecycle_helpers.py`/`commit.py`; the three emitters delegate to it, supplying only their source-specific inputs.

**D2. Export the field-name vocabulary from the same module; the parser imports it.** Module-level `F_*` constants (or a `FIELDS` namespace) define every key. `log_analysis._CRITICAL_ALERT_FIELDS` becomes a tuple of imported constants; `_evaluate_pending_alerts` uses them. A renamed field now changes one constant and both producer and consumer move together — the silent-break risk is gone. (`javdb/integrations` importing from `javdb/storage` is an intra-`javdb` import; the layering invariant only forbids `apps`→`javdb`.)

**D3. The builder is pure — callers supply pre-fetched `stats`.** Each call site already fetches stats at its own point (the CLI emitter calls `HistoryRepo().pending_session_stats()` before building; the rollback emitter calls `db_pending_session_stats()`; the lib emitter wraps it in try/except and passes `{}`). Keeping the builder free of `HistoryRepo` makes it trivially unit-testable without a DB fixture and keeps it deep (one signature, no retry/fallback logic inside).

**D4. Rollback-only fields arrive as an opaque `rollback_extras: Optional[Dict[str, Any]]`.** The builder already returns a dict; a second `RollbackExtras` dataclass would be complexity without benefit, and explicit `rollback_mode=`/`cleanup_path_mismatch_count=` kwargs would bloat the shared signature with rollback concerns. The extras dict is merged in, its keys guarded by the D2 constant set.

**D5. Shadow-audit stays in the CLI; its result is passed in.** `_shadow_audit_drift` queries `MovieHistory`/`TorrentHistory` via `get_db` — it is I/O-heavy and CLI-only. With the builder pure (D3), the CLI computes `shadow_audit_result` and passes it; the lib emitter passes `shadow_audit_result=None` (it hardcodes `shadow_audit_enabled=False` today, which continues to hold). No shadow-audit logic enters the `javdb/storage` layer.

**D6. `GITHUB_OUTPUT` write stays in `apps/cli/db/commit_session.py`.** It is a genuinely CLI-only post-emit side effect; the builder returns a dict, the CLI appends the JSONL line *and* writes `GITHUB_OUTPUT`. The other two emitters only append the JSONL line.

**D7. Write class unchanged — no migration annotation.** `pending_session_verify` stays a **diagnostic write** (ADR-042): it explains drift/recovery state and may block but never upgrades a commit. No new table, no schema change, so ADR-042 D6's `-- Write-Class:` migration-header rule does not apply.

## Consequences

### Positive

- **locality** — the record schema lives in one builder; a schema fix happens once, not in three diverging emitters.
- **leverage** — one signature replaces three private implementations; the parser and producers share one field vocabulary by construction (D2).
- **interface shrinks; tests hit one seam** — the builder is unit-testable for all three `source` paths without CLI/rollback/DB plumbing.
- **the documented drift closes** — the "Simplified version" lib emitter stops diverging because there is nothing to keep in sync.

### Negative

- **Seven call sites re-point** (4 in `commit_session.py`, 1 in `sessions/commit.py`, 3 in `rollback/core.py`). Mitigated: pure relocation; existing emit/parse tests are the regression gate.
- **The builder grows source-conditional branches** (commit vs rollback extras). Accepted: that conditionality already exists, scattered across three files; concentrating it is the point.

### Risks

- **A field renamed in the builder but not in `log_analysis`.** This is exactly the risk D2 removes — both bind to the same exported constants. Net safer than today.
- **A monkeypatch target moves.** `tests/unit/test_commit_session_events.py` patches `cs._emit_pending_verify` (L45); after the refactor it patches the builder (or `append_jsonl_record`). One enumerated test update.

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 (only) | [IMP-ADR050-01](IMP-ADR050-01-pending-verify-builder.md) | `pending_verify.py` builder + field-name constants; three emitters deleted and re-pointed; `log_analysis` binds to the constants; new builder unit test; CONTEXT.md terms | — |

### Explicit non-goals (YAGNI)

- **Not** changing what gets written, when, or where (`reports/D1/d1_drift.jsonl` unchanged).
- **Not** touching the `PipelineEvent` spine (ADR-036) — a separate `_emit_event` codepath.
- **Not** promoting the record to a typed dataclass return — it stays a dict (D4).

## Domain Language (additions for CONTEXT.md)

- **Pending Verify Record (`pending_session_verify`)** — a diagnostic JSONL record appended to `reports/D1/d1_drift.jsonl` at the end of every pending-mode session lifecycle event (commit success, commit failure, rollback). A **diagnostic write** (ADR-042) — explains recovery state, never authoritative.
- **Pending Verify Builder** — the single pure `build_pending_verify_record()` in `javdb/storage/sessions/pending_verify.py` that constructs the record dict; the three emitters (CLI commit, lib commit, rollback) delegate to it, and `log_analysis` binds to its exported field-name constants.

## Alternatives Considered

- **Builder fetches `stats` internally.** Rejected (D3): forces a DB fixture into every builder test and pulls retry/fallback into a pure function.
- **Typed `RollbackExtras` dataclass.** Rejected (D4): complexity without benefit; the record is a dict and the field names are already guarded.
- **Move shadow-audit into `storage/sessions`.** Rejected (D5): it is CLI-only I/O; importing it into `javdb/storage` widens that layer for no caller.

## References

- [ADR-042 — D1 Atomic Commit Boundaries](../../ADR-042-D1-Atomic-Commit-Boundaries/ADR-042-d1-atomic-commit-boundaries.md)
- [ADR-019 — Session Lifecycle Authority](../ADR-019-Session-Lifecycle-Authority/ADR-019-session-lifecycle-authority.md)
- [ADR-026 — AI Operations Diagnosis](../../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md)
- 2026-06-13 architecture review: [architecture-review-2026-06-13.html](../../architecture/architecture-review-2026-06-13.html)

## Status Log

- 2026-06-14: Completed in [IMP-ADR050-01](IMP-ADR050-01-pending-verify-builder.md). The planned single phase shipped the pure pending-verify builder, shared field-name constants, emitter/parser re-pointing, CLI-only alert-decision wrapper, focused tests, workflow updates, and handbook updates. No follow-up IMP remains for this ADR.
- 2026-06-13: Proposed (from the 2026-06-13 architecture review, Candidate 3). Decided: one pure `build_pending_verify_record()` in `javdb/storage/sessions/pending_verify.py`; exported field-name constants bound by both producers and the `log_analysis` consumer; pure builder (callers pass pre-fetched stats); `rollback_extras` as an opaque dict; shadow-audit + `GITHUB_OUTPUT` stay CLI-only. Verified: 17-field core schema (candidate said ~13; the lib emitter carries 19 fields); the "Simplified version" docstring drift is real; diagnostic write class (ADR-042) — no migration annotation. IMP-ADR050-01 pending.
