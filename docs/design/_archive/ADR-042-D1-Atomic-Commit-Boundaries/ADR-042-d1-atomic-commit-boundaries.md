# ADR-042: D1 Atomic-Commit Boundaries for Authoritative Writes

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Completed — codifies the current boundary between D1 transport, session-level history, and additive enrichment |
| **Date**    | 2026-05-31                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-005](../ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md), [ADR-009](../ADR-009-D1-Drift-Classifier/ADR-009-d1-drift-classifier-and-diagnose.md), [ADR-010](../ADR-010-D1-Access-Port/ADR-010-d1-access-port.md), [ADR-019](../ADR-019-Session-Lifecycle-Authority/ADR-019-session-lifecycle-authority.md), [ADR-032](../ADR-032-Mandatory-Session-Binding/ADR-032-mandatory-session-binding.md), [ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md), [ADR-036](../../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md) |
| **Related Implementation Plans** | [IMP-ADR042-01](IMP-ADR042-01-d1-atomic-commit-boundaries.md) - Phase 1 docs follow-through; [IMP-ADR042-02](IMP-ADR042-02-d1-write-class-enforcement.md) - Phase 2 D6 enforcement |

> This ADR was written after a grilling session that separated "D1 itself" from "the authoritative write boundary". That distinction matters: the system does not need a distributed transaction manager, but it does need a session-level boundary that behaves like one for the authoritative history path.

## Context

The repository already splits write responsibility across several layers:

- `javdb/storage/d1_client.py` and `javdb/storage/d1_port.py` own HTTP transport, retry, batching, recovery hooks, and summary metrics.
- `javdb/storage/dual_connection.py` mirrors writes to SQLite and D1, then records drift when the two diverge.
- `javdb/storage/db/_db_history_write.py` implements the pending-stage / commit / resume flow for `MovieHistory` and `TorrentHistory`.
- `javdb/storage/sessions/lifecycle.py` centralises legal `ReportSessions.Status` transitions.
- `javdb/storage/db/_db_connection.py` chooses `sqlite`, `d1`, or `dual`.
- `docs/handbook/en/ops/d1-rollback.md` treats rollback and resume as operator recovery, not as proof of a distributed transaction.

That architecture answers the original question in layers, not with one yes/no:

1. D1 can provide atomicity for a single request or batch.
2. The authoritative history path needs session-level all-or-nothing behavior.
3. Additive enrichment and diagnostics must not be promoted into the authoritative boundary.

The practical problem is that these layers are easy to blur together. If "ACID" is applied too broadly, it suggests a distributed transaction that this system does not have. If it is applied too narrowly, it ignores the fact that users care about whether a session's authoritative history committed as one unit.

## Decision

Use **session-level atomic commit** for authoritative writes, while keeping D1 transport, enrichment, and diagnostics outside that boundary.

### Design Decisions

**D1. D1 transport is not the authority boundary.**

D1 only needs to be locally atomic at the smallest transport boundary it can actually guarantee: a single request or a single batch. `D1Connection` and `D1AccessPort` remain synchronous, explicit, and request-oriented. They are not a distributed transaction coordinator.

**D2. Authoritative history writes are one logical transaction at session scope.**

The writes that decide whether a pipeline session succeeded or failed must behave as one unit from the user's point of view. That includes:

- `ReportSessions.Status` transitions;
- staging into `PendingMovieHistoryWrites` / `PendingTorrentHistoryWrites`;
- draining pending rows into `MovieHistory` / `TorrentHistory`;
- rollback / resume behavior for failed or interrupted sessions.

This is the boundary that needs atomic-commit semantics.

**Scope of the guarantee.** "Atomic commit" here means *all-or-nothing at session scope* — atomicity plus consistency: the authoritative history either commits as one unit or stays recoverable to a clean state. It deliberately does **not** claim database-level isolation or durability across SQLite and D1. Concurrent sessions are kept apart by `SessionId` partitioning and the cross-process `MovieClaim` lease (not by a DB transaction); durability rests on D1's per-request / per-batch atomicity plus the recovery flow. This is why the ADR says "atomic commit" and not "ACID": only the A and C are promised at this boundary.

**D3. Additive enrichment is outside the authoritative boundary.**

Tables and flows that are append-only, idempotent, or replayable may be D1-canonical without being part of session success. Examples include enrichment tables and event-log style writes that can be retried, replayed, or re-derived later. They must not determine whether the authoritative session is committed.

**D4. Diagnostics and recovery records are operational, not user truth.**

Drift logs, port summaries, and recovery outbox entries are necessary for observability and recovery, but they do not upgrade a failed authoritative write into a success. They describe what happened; they do not redefine correctness.

Note that recovery state can still *gate* an authoritative commit — an undrained `history:SESSION_ID` recovery ordering key, or a dead-lettered entry, will block the session from reaching `committed`. That is the point: a recovery record may **block** a failure from being declared a success, but it can never **upgrade** a failure into one. Gating is part of fail-closed behavior, not an exception to D4.

**D5. Dual mode is a verifier, not a transaction manager.**

`DualConnection` is allowed to drift, log the drift, and fail loud. Its job is to prove or disprove parity during migration and recovery work, not to simulate a distributed ACID transaction between SQLite and D1.

**D6. Every new D1 write must declare its class.**

New D1-backed write paths must be classified during design review as one of:

| Class | Meaning | Rule |
| --- | --- | --- |
| authoritative | Must participate in session-level atomic commit | Fail closed on error; session correctness depends on it |
| additive | Safe to replay or rebuild | Prefer idempotent UPSERT / append-only behavior |
| diagnostic | Observes or explains state | Never determines user-facing correctness |

If a write cannot be classified, it is too ambiguous to land.

**Enforcement (Phase 2).** D6 is enforced at two points: (1) every `CREATE TABLE` migration under `javdb/migrations/d1/` must carry a `-- Write-Class: <class>` header, checked on every PR by `.github/workflows/validate-d1-write-class.yml` (fail-closed); and (2) the ADR template carries a `D1 Write Class` field so the class is prompted at design-review time. Column adds, indexes, version bumps, and drops are not new write surfaces and are exempt. See [IMP-ADR042-02](IMP-ADR042-02-d1-write-class-enforcement.md).

## Domain Language

- **Authoritative write** — a write that decides whether a session committed correctly.
- **Additive write** — a write that records extra state without changing the meaning of the authoritative session.
- **Diagnostic write** — a write that records evidence, drift, or recovery state.
- **Session-level atomic commit** — the guarantee that the authoritative session behaves like one all-or-nothing unit at session scope (atomicity + consistency), even though the system is built from multiple layers and recovery mechanisms underneath. It does not promise database-level isolation or durability across SQLite and D1.

## Consequences

### Positive

- **No fake distributed transaction promise** — the architecture states the truth plainly.
- **Clear review rule for future tables** — every new D1 write must be classified.
- **Matches the current code shape** — the stack already uses pending staging, session lifecycle authority, drift logging, and recovery.
- **Protects the authoritative history path** — users care most about whether a session's history committed correctly.
- **Leaves enrichment flexible** — additive tables can stay idempotent and replayable without being dragged into the critical path.

### Negative

- **More policy surface for reviewers** — authors must justify why a write is authoritative, additive, or diagnostic.
- **Not every D1 write gets the same failure semantics** — operators and developers must keep the classes straight.
- **Dual / recovery plumbing remains necessary** — because the system is not pretending to have a distributed transaction.

## Alternatives Considered

### Full distributed ACID across SQLite and D1

Rejected. The current system uses HTTP-backed D1 requests, local SQLite, dual-write parity checks, and recovery flows. That is not a distributed transaction stack, and pretending otherwise would create false confidence.

### Best-effort for everything

Rejected. The authoritative history path would become too weak, and a failed session could no longer be reasoned about cleanly.

### Treat every D1 table as authoritative

Rejected. The repository already has additive enrichment and diagnostic surfaces that should remain outside the session commit boundary.

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 ✅ | [IMP-ADR042-01](IMP-ADR042-01-d1-atomic-commit-boundaries.md) | Propagate the boundary into CONTEXT.md and the storage / handbook docs — **done 2026-06-01** | Any distributed-transaction fantasy across SQLite and D1 |
| Phase 2 ✅ | [IMP-ADR042-02](IMP-ADR042-02-d1-write-class-enforcement.md) | Enforce D6: a `Write-Class:` header is required on new `CREATE TABLE` migrations (CI fail-closed) + a `D1 Write Class` field in the ADR template — **done 2026-06-01** | Backfilling existing migrations; gating code-level writes to existing tables; CI-enforcing the (soft-by-design) ADR field |

## References

- [ADR-005 — Db Py Retirement and Repo Pattern](../ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md)
- [ADR-009 — D1 Drift Classifier](../ADR-009-D1-Drift-Classifier/ADR-009-d1-drift-classifier-and-diagnose.md)
- [ADR-010 — D1 Access Port](../ADR-010-D1-Access-Port/ADR-010-d1-access-port.md)
- [ADR-019 — Session Lifecycle Authority](../ADR-019-Session-Lifecycle-Authority/ADR-019-session-lifecycle-authority.md)
- [ADR-032 — Mandatory Session Binding](../ADR-032-Mandatory-Session-Binding/ADR-032-mandatory-session-binding.md)
- [ADR-033 — Media Closed Loop](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md)
- [ADR-036 — Event Sourced Pipeline Spine](../../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md)
- [D1 rollback handbook](../../../handbook/en/ops/d1-rollback.md)
- [javdb/storage/d1_client.py](../../../../javdb/storage/d1_client.py)
- [javdb/storage/dual_connection.py](../../../../javdb/storage/dual_connection.py)
- [javdb/storage/db/_db_history_write.py](../../../../javdb/storage/db/_db_history_write.py)

## Status Log

- 2026-05-31: Accepted — codified the session-level atomic-commit boundary for authoritative D1 writes.
- 2026-05-31: Renamed from "logical ACID" to "atomic commit" and scoped the guarantee to atomicity + consistency (isolation via `SessionId`/`MovieClaim`, durability via recovery) after a design review flagged the ACID framing as overclaiming I/D.
- 2026-06-01: IMP-ADR042-01 completed — write-boundary vocabulary propagated into `CONTEXT.md` (the `写入边界分类` section + glossary), the storage READMEs, and the developer / ops handbooks.
- 2026-06-01: IMP-ADR042-02 (Phase 2) completed — D6 is now enforced by a CI gate (a `Write-Class:` header is required on new `CREATE TABLE` migrations) plus a `D1 Write Class` field in the ADR template. This closes the D6-enforcement item deferred by IMP-ADR042-01.
