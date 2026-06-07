# ADR-036: Event-Sourced Pipeline Spine

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Proposed — umbrella; Phase 1 implemented and verified; execution delegated to per-phase IMPs |
| **Date**    | 2026-05-29                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-012](../_archive/ADR-012-Pipeline-Run-Boundary/ADR-012-pipeline-run-structured-boundary.md), [ADR-019](../ADR-019-Session-Lifecycle-Authority/ADR-019-session-lifecycle-authority.md), [ADR-005](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md), [ADR-010](../_archive/ADR-010-D1-Access-Port/ADR-010-d1-access-port.md), [ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md), [ADR-035](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md) |

> Originated from a 2026-05-29 brainstorming session on net-new directions
> (Direction 3 — a replayable pipeline core).

## Context

The pipeline is an **orchestrated, command-style** procedure: `javdb/pipeline/`
runs spider → uploader → pikpak as subprocess/in-process steps with structured
result sidecars ([ADR-012](../_archive/ADR-012-Pipeline-Run-Boundary/ADR-012-pipeline-run-structured-boundary.md)).
Adding a new cross-cutting capability means **pipeline surgery** — the two most
recent designs prove it:

- [ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md) (media
  closed-loop) had to **instrument the uploader** (queue-time write) and **push
  from the cleanup step** (completed) to learn a torrent's fate.
- [ADR-035](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md)
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

**D6. Strangler path for the existing hooks.** Phase 2 re-points
[ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md)'s
`AcquisitionOutcome` and [ADR-035](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md)'s
sentinel to **consume events** instead of hooking the pipeline — retroactively
de-invasifying them. Making `pending→commit`/history a projection of the log is
Phase 3+, deferred and high-care.

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
| Phase 2 — Adopt consumers | IMP-ADR036-02 (stub) | Re-point ADR-033 `AcquisitionOutcome` and ADR-035 sentinel to consume events | — |
| Phase 3 — Strangler (optional) | IMP-ADR036-03 (stub) | Make `pending→commit`/history a projection of the log | — |

Phase 1 stands alone and touches nothing authoritative. Phase 2 depends on
ADR-033/035 having landed. Phase 3 is an optional, high-care authority migration.

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
- [ADR-033 — Media Closed-Loop](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md)
- [ADR-035 — Site-Contract Drift Sentinel](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md)

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
