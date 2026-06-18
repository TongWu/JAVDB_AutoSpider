# ADR-036: Event-Sourced Pipeline Spine

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Proposed — umbrella; Phases 1 & 2 implemented & verified; Phase 2 (additive emit + shadow consumer) landed 2026-06-10; Phase 3 (strangler) optional/deferred; execution delegated to per-phase IMPs |
| **Date**    | 2026-05-29                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-012](../_archive/ADR-012-Pipeline-Run-Boundary/ADR-012-pipeline-run-structured-boundary.md), [ADR-019](../_archive/ADR-019-Session-Lifecycle-Authority/ADR-019-session-lifecycle-authority.md), [ADR-005](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md), [ADR-010](../_archive/ADR-010-D1-Access-Port/ADR-010-d1-access-port.md), [ADR-033](../_archive/ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md), [ADR-035](../_archive/ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md) |

> Originated from a 2026-05-29 brainstorming session on net-new directions
> (Direction 3 — a replayable pipeline core).

## Context

The pipeline is an **orchestrated, command-style** procedure: `javdb/pipeline/`
runs spider → uploader → pikpak as subprocess/in-process steps with structured
result sidecars ([ADR-012](../_archive/ADR-012-Pipeline-Run-Boundary/ADR-012-pipeline-run-structured-boundary.md)).
Adding a new cross-cutting capability means **pipeline surgery** — the two most
recent designs prove it:

- [ADR-033](../_archive/ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md) (media
  closed-loop) had to **instrument the uploader** (queue-time write) and **push
  from the cleanup step** (completed) to learn a torrent's fate.
- [ADR-035](../_archive/ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md)
  (drift sentinel) had to **hook the index parse boundary** and **gate the commit
  path**.

Each new feature reaches into the pipeline at a different point. There is no
shared stream a consumer can subscribe to.

Two facts shape the right ambition:

1. **The system is already half event-sourced.** `PendingMovieHistoryWrites` /
   `PendingTorrentHistoryWrites` are an **append-then-project** log (rows accrue
   with `ApplyState='pending'`, materialize to `MovieHistory` / `TorrentHistory`
   at commit); the `ReportSessions` lifecycle is a governed state machine
   ([ADR-019](../_archive/ADR-019-Session-Lifecycle-Authority/ADR-019-session-lifecycle-authority.md)).
2. **Appetite for a heavy log is low — by evidence.** The per-row change log
   (`MovieHistoryAudit` / `TorrentHistoryAudit`) was **deleted** on 2026-05-22
   ([ADR-005](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md)
   PR-4). A full event-sourcing rewrite would re-introduce exactly the kind of
   verbose log they just removed.

This ADR therefore takes the **additive** path: a single append-only event spine
the pipeline emits to, that new features consume instead of hooking — without
touching the authoritative `pending→commit` path.

## Decision

Introduce an **additive, append-only `PipelineEvent` log** in D1. The pipeline
emits **entity-lifecycle events** at its natural points; consumers read the log
by cursor and build idempotent projections. The existing `pending→commit` flow
remains the authority for history; the spine is purely additive and may later
absorb projections via a strangler migration.

### Design Decisions

**D1. Additive spine, non-destructive.** A single append-only log is added
*alongside* the current pipeline. `pending→commit` stays the source of truth for
`MovieHistory` / `TorrentHistory`. Nothing existing is rewritten; the spine can
become authoritative incrementally later (strangler), or not at all.

**D2. The store is a D1 append-only table; consumers poll by cursor.** No
Cloudflare Queue / Durable Object. The system is batch (cron pipelines, batch
consumers), so a queryable D1 table with monotonic ordering is the right
substrate — D1-canonical, replayable, no realtime-push infra.

```sql
CREATE TABLE PipelineEvent (
  seq          INTEGER PRIMARY KEY AUTOINCREMENT,  -- global monotonic order (D1 serializes writes)
  session_id   TEXT NOT NULL,
  run_id       TEXT,
  run_attempt  INTEGER,
  event_type   TEXT NOT NULL,
  entity_type  TEXT NOT NULL,   -- session | movie | torrent
  entity_id    TEXT,            -- href (movie) | qb_hash (torrent) | session_id (session)
  payload      TEXT,            -- JSON
  created_at   TEXT NOT NULL
);

CREATE TABLE EventConsumerCursor (
  consumer   TEXT PRIMARY KEY,
  last_seq   INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT
);
```

**D3. Entity-lifecycle granularity — never per-field.** Events fire at meaningful
lifecycle transitions of a movie/torrent/session, not on every field mutation
(that was the retired audit-log mistake). The taxonomy:

| Entity | Events |
| --- | --- |
| session | `RunStarted`, `SessionCommitted`, `SessionFailed` |
| movie | `MovieDiscovered`, `MovieSelected` |
| torrent | `TorrentSelected`, `TorrentQueued`, `TorrentCompleted` |

Volume is comparable to the pending tables (one row per entity per transition),
which the system already persists — not the extra per-row log it deleted.

**D4. Emit at the natural pipeline points; consistency tiered.** `events.emit()`
is called where features already hook (index selection, uploader add, cleanup,
commit). In-run events (`Discovered`/`Selected`/`Queued`) are **best-effort** (an
emit failure must not break the pipeline); commit-class events
(`SessionCommitted`/`SessionFailed` and the per-entity `Committed` view) ride the
**commit transaction** so the log never disagrees with reality about what
committed. A full transactional outbox for all events is a later hardening item.

**D5. Cursor-based idempotent consumers + free replay.** A consumer reads
`seq > last_seq`, projects idempotently, advances its cursor. **Replay** = reset a
consumer's cursor to 0 and re-run → its projection rebuilds from the log. This is
the headline value (replayable / auditable), and it is nearly free with the
cursor model.

**D6. Strangler path for the existing hooks — additive first, cutover gated.** Phase 2 makes the spine *carry* the per-entity lifecycle events and proves the consume path with a **shadow** projection, but does **not** rip out the existing direct-write hooks. Concretely: emit `MovieDiscovered` / `MovieSelected` / `TorrentSelected` / `TorrentQueued` / `TorrentCompleted` at the natural pipeline points (additive, best-effort per D4); add a consumer that rebuilds an `AcquisitionOutcome`-shaped projection from those events for **cross-validation** against [ADR-033](../_archive/ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md)'s authoritative direct-write path (the shadow projection is never read by production decisions). The [ADR-035](../_archive/ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md) sentinel is re-pointed onto the event stream **only where it maps cleanly** to per-entity events; if its per-field fill computation does not, it stays on its current piggyback hook. **The actual cutover of ADR-033's data-critical `AcquisitionOutcome` — which feeds the ADR-024/025 quality/preference data clock — is deferred** until the in-run events prove reliable in production (or the outcome-determining events are promoted to commit-class per D4). Rationale: `AcquisitionOutcome`'s current hook is a synchronous direct write; replacing it with a *best-effort* in-run emit + async projection would risk silently dropping acquisition rows under emit failure, regressing a freshly-landed critical path. Making `pending→commit` / history a projection of the log remains Phase 3+, deferred and high-care.

**D7. Module shape mirrors the repo's conventions.** `javdb/pipeline/events/`
holds `models.py` (event types), `store.py` (`emit` + read-since-cursor),
`consumer.py` (base consumer + cursor advance); `javdb/storage/repos/pipeline_event_repo.py`
is the D1 access. Emit call sites live at the existing pipeline points.

## Consequences

### Positive

- **New features become consumers, not surgery** — subscribe to the spine instead
  of hooking the pipeline at a new point each time.
- **Replayable & auditable** — reset a cursor to rebuild any projection; the log
  is the ordered truth of what happened.
- **Retroactively simplifies ADR-033/035** — their invasive hooks become event
  consumers in Phase 2.
- **Low risk** — additive; `pending→commit` authority is untouched.
- **D1-canonical** — one more append-only table, no new infra.

### Negative

- **A second stream to keep honest** — emit points must stay correct as the
  pipeline evolves; best-effort in-run emits can miss events under failure (the
  commit-class events are the consistent backbone).
- **Eventual-consistency for consumers** — cursor-poll projections lag the log by
  a poll interval (acceptable for a batch system).
- **Overlap with pending tables until strangler** — the spine and the pending log
  both describe history writes during the additive phase; resolved only if/when
  Phase 3 makes history a projection.

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 — Spine + demonstrator | [IMP-ADR036-01](IMP-ADR036-01-event-spine.md) | `PipelineEvent` + `EventConsumerCursor` tables; `events` module (`emit`, read-since-cursor, base consumer); emit at the pipeline points; one demonstrator consumer (`RunEventSummary` per-session counts) proving emit→consume→replay | Re-pointing ADR-033/035; history-as-projection |
| Phase 2 — Adopt consumers | [IMP-ADR036-02](IMP-ADR036-02-adopt-consumers.md) ✅ | Emit the 5 per-entity events (`MovieDiscovered`, `MovieSelected`, `TorrentSelected`, `TorrentQueued`, `TorrentCompleted`) at the natural pipeline points (best-effort/additive); `AcquisitionOutcomeShadow` projection table + `AcquisitionOutcomeShadowRepo` + `AcquisitionOutcomeShadowConsumer`; cross-validation `compare_shadow_to_authoritative()` + CLI; import-cycle fix; sentinel left on piggyback (not-clean mapping, as designed) | Cutover of ADR-033's direct-write `AcquisitionOutcome` (gated on shadow proving reliable in production); history-as-projection |
| Phase 3 — Strangler (optional) | IMP-ADR036-03 (deferred) | Make `pending→commit`/history a projection of the log | — |
| Phase 4 — Operator audit log | [IMP-ADR036-04](IMP-ADR036-04-operator-action-audit-log.md) | Sibling `OperatorAuditEvent` append-only table (actor/action/target/source); best-effort emit at web/worker/CLI mutation points; admin read API + Web Audit Log view | mutating PipelineEvent; pipeline entity-lifecycle events |

Phase 1 stands alone and touches nothing authoritative. Phase 2 depends on
ADR-033/035 having landed; the Phase 2 AcquisitionOutcome cutover is additionally
gated on in-run events proving reliable in production. Phase 3 is an optional,
high-care authority migration.

### Explicit non-goals (YAGNI)

- **The log is not authoritative** — `pending→commit` stays the source of truth
  (Phase 3+ only).
- **No realtime push** — D1 table + cursor poll; no Cloudflare Queue/DO.
- **No per-field events** — entity-lifecycle transitions only.
- **No rebuild of existing projections** from the log in Phase 1.

## Domain Language (additions for CONTEXT.md)

- **Pipeline event** — an immutable, append-only record of an entity-lifecycle
  transition (movie/torrent/session) in `PipelineEvent`, ordered by `seq`.
- **Event spine** — the single append-only log the pipeline emits to and
  consumers subscribe to.
- **Consumer cursor** — a per-consumer `last_seq` marking how far it has
  projected; resetting it replays.
- **Projection** — an idempotent read model a consumer builds from events.
- **Strangler migration** — incrementally moving authority from `pending→commit`
  to the event log (deferred).

## Alternatives Considered

- **Full event sourcing (every action an event, all state a projection, replay
  rebuilds everything)** — rejected: maximum blast radius on a working pipeline,
  and re-introduces the verbose per-row log deleted in ADR-005 PR-4.
- **Cloudflare Queue / Durable Object substrate** — rejected (D2): the pipeline is
  Python (GH Actions/local) and consumers are batch; a CF Queue adds cross-process
  HTTP coupling for realtime the system does not need.
- **Stage/run-level granularity only** — rejected (D3): consumers (closed-loop,
  preference, stats) need per-entity events to derive per-entity state.
- **Make the log authoritative now** — rejected (D1): high risk on a working
  system; the strangler path keeps it optional and incremental.

## References

- [ADR-012 — Pipeline Run Structured Boundary](../_archive/ADR-012-Pipeline-Run-Boundary/ADR-012-pipeline-run-structured-boundary.md)
- [ADR-019 — Session Lifecycle Authority](../_archive/ADR-019-Session-Lifecycle-Authority/ADR-019-session-lifecycle-authority.md)
- [ADR-005 — db.py Retirement & Repo Pattern](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md)
- [ADR-010 — D1 Access Port](../_archive/ADR-010-D1-Access-Port/ADR-010-d1-access-port.md)
- [ADR-033 — Media Closed-Loop](../_archive/ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md)
- [ADR-035 — Site-Contract Drift Sentinel](../_archive/ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md)

## Status Log

- 2026-05-29: Proposed (umbrella; three phases scoped, IMPs pending).
- 2026-05-30: Phase 1 implemented and verified ([IMP-ADR036-01](IMP-ADR036-01-event-spine.md)).
  `PipelineEvent` / `EventConsumerCursor` / `RunEventSummary` tables applied to
  remote `javdb-reports` D1; `PipelineEvent` has live rows from `RunStarted` /
  `SessionCommitted`; `javdb/pipeline/events/` (`emit`, `read_since`, cursor
  `Consumer`) + `PipelineEventRepo` / `RunEventSummaryRepo`; session events
  (`RunStarted` / `SessionCommitted` / `SessionFailed`) wired at the run and
  commit boundaries; demonstrator `apps.cli.ops.events` consumer/replay CLI;
  GitHub full unit tests passed with no failures. Umbrella stays **Proposed**
  pending Phase 2 (adopt consumers) and Phase 3 (optional strangler).
- 2026-06-10 (re-scope): Phase 2 re-scoped to conservative additive phasing via design-feedback-loop review (owner decision). WHY: D4 makes in-run events (`Discovered`/`Selected`/`Queued`) best-effort; replacing `AcquisitionOutcome`'s synchronous direct-write hook with a best-effort emit + async projection would risk silently dropping acquisition rows under emit failure, regressing a freshly-landed path that feeds the ADR-024/025 quality/preference data clock. WHAT Phase 2 now ships: emit the 5 per-entity events (`MovieDiscovered`, `MovieSelected`, `TorrentSelected`, `TorrentQueued`, `TorrentCompleted`) at the natural pipeline points (additive); a shadow `AcquisitionOutcome`-projection consumer for cross-validation against ADR-033's authoritative direct-write (never read by production); re-point the ADR-035 sentinel onto the event stream only where it maps cleanly. DEFERRED/GATED: the full cutover of ADR-033's data-critical `AcquisitionOutcome` is deferred until in-run events prove reliable in production (or the outcome-determining events are promoted to commit-class per D4).
- 2026-06-13: Added Phase 4 (IMP-ADR036-04, operator/web-console action audit log) to cover the gap where the spine records only pipeline entity-lifecycle events with no actor dimension. Operator mutations land in a sibling append-only `OperatorAuditEvent` table (best-effort), leaving PipelineEvent and the reserved Phase-3 strangler untouched.
- 2026-06-10 (implemented): Phase 2 implemented and verified (branch `claude/adr036-p2-event-consumers`; 9 tasks, subagent-driven with implementer + spec/code review per task; ~44 Phase-2 unit tests green). Emit points: `MovieDiscovered` in `javdb/spider/app/run_service.py` (after `RunStarted`, loops `all_index_results_phase1 + all_index_results_phase2`); `MovieSelected` in `javdb/spider/detail/runner.py` `process_detail_entries` (after `prepare_detail_entries`); `TorrentSelected` in `javdb/spider/detail/runner.py` `persist_parsed_detail_result` (inside `if plan.should_include_in_report:`, per magnet link); `TorrentQueued` in `javdb/integrations/qb/uploader/service.py` `run_uploader` (after `_record_queued_acquisition` succeeds); `TorrentCompleted` in `javdb/ops/reconcile/service.py` `apply_cleanup_completed` (after `mark_state`, `session_id` recovered from `AcquisitionOutcome` row). All emits are best-effort — the pipeline step still succeeds if the emit raises. Shadow projection: new reports-DB table `AcquisitionOutcomeShadow` (D1 migration `2026_06_10_add_acquisition_outcome_shadow.sql` + SQLite `_REPORTS_DDL` mirror), `AcquisitionOutcomeShadowRepo`, `AcquisitionOutcomeShadowConsumer` (consumes `TorrentQueued` + `TorrentCompleted`, skips events with no `entity_id`); wired into `apps/cli/ops/events.py` via `--consumer {run_event_summary,acquisition_outcome_shadow}`. Cross-validation: `compare_shadow_to_authoritative()` in `javdb/ops/reconcile/shadow_validate.py` + CLI `apps/cli/ops/shadow_validate.py` — read-only, compares shadow vs authoritative `AcquisitionOutcome` (operations DB), coarse-maps authoritative richer states to `queued`/`completed` so downstream states like `in_library` are not false-positives. Sentinel NOT re-pointed — confirmed not-clean (needs raw per-record `MovieEntry` field access at parse time; event payload cannot carry that granularity; stays on piggyback in `javdb/spider/fetch/index.py` / `index_parallel.py`, as per D6 amendment). `AcquisitionOutcome` cutover deferred/gated on shadow proving reliable in production. Also fixed a latent storage↔events import cycle (`pipeline_event_repo` no longer imports `javdb.pipeline.events` at module load; uses a local `_utc_now_iso` + lazy `PipelineEventRecord` import). Verification: all Phase-2 unit tests green; existing event-spine + reconcile + pipeline tests pass; full unit+integration suite shows no facade/pipeline regressions (remaining failures are pre-existing environmental ones — 401 auth in the credential-less worktree, missing local Rust wheel `ImportError`).
