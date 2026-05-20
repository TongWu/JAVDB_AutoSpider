# ADR-010: Unified Python D1 Access Port

**Status**: Accepted — implementation pending (all four phases unexecuted as of 2026-05-19)
**Date**: 2026-05-19
**Deciders**: D1 access-port brainstorming and grill session
**Prerequisites**: [ADR-006](ADR-006-pending-mode-default-rollout.md) keeps pending mode as the default write path; [ADR-009](ADR-009-d1-drift-classifier-and-diagnose.md) documents the recent D1 transient-failure and drift response.
**Related Implementation Plans**: [IMP-012](../impl/IMP-012-d1-access-port-phase1-core.md) (Phase 1 — port core), [IMP-013](../impl/IMP-013-d1-access-port-phase2-recovery-outbox.md) (Phase 2 — recovery outbox), [IMP-014](../impl/IMP-014-d1-access-port-phase3-safe-batching.md) (Phase 3 — safe batching), [IMP-015](../impl/IMP-015-d1-access-port-phase4-startup-replay.md) (Phase 4 — startup replay)

## Outstanding Work

- Phase 1 — `D1AccessPort` core class + `D1Connection`/`DualConnection` delegation. No `D1AccessPort` symbol exists in `javdb/storage/` yet.
- Phase 2 — recovery outbox + replay queue (per D5).
- Phase 3 — safe micro-batching + `flush()` boundaries (per D4).
- Phase 4 — startup replay of any persisted outbox entries.

The four phases are independently gated; nothing in this ADR has shipped as of the date above.

---

## Context

The current D1 path is split across several layers:

- `javdb/storage/d1_client.py` owns the Cloudflare D1 HTTP request shape, retry classification, backoff, `executemany`, `batch_execute`, and `requests.Session` reuse.
- `javdb/storage/dual_connection.py` mirrors writes to SQLite and D1, routes reads to D1, tracks drift, and enforces guarded primary-key rules.
- `javdb/storage/db/db_connection.py` selects `sqlite`, `d1`, or `dual` through `STORAGE_BACKEND`.
- The business write path still lives in `db.py`, `db_history_write.py`, and the Repo wrappers. Those layers know about sessions, pending history, stats, rollback, and operations.

This has worked, but it leaves two recurring problems:

1. D1 still receives many short-interval HTTP requests from hot paths. The worst offender used to be pending session commit; a tested `COMMIT_SESSION_BULK` path already exists and collapses per-href D1 traffic to batched requests, but it is still opt-in.
2. Recoverable D1 failures are handled mostly as per-request retry plus drift detection. When retry is exhausted, dual mode can continue with SQLite and record drift, but there is no unified port-level recovery queue that can replay proven-idempotent D1 writes.

The desired boundary is a Python-internal D1 access port, not a new external service. The port is also **not** a "D1 version of `db.py`". `db.py` and the Repo layer remain responsible for business semantics and SQL construction. The D1 access port owns transport, reliability, batching, recovery, schema metadata caching, and observability for Cloudflare D1.

---

## Decision

Introduce a process-local `D1AccessPort` under `javdb/storage/`. It becomes the only Python exit point to Cloudflare D1. `D1Connection` keeps the sqlite3-compatible facade used by callers, but delegates D1 HTTP execution and recovery behavior to the port.

### D1. Port Boundary

The layering becomes:

```text
Business storage code: db.py / repos / db_history_write / db_reports / db_stats
        |
get_db() / D1Connection / DualConnection
        |
D1AccessPort
        |
Cloudflare D1 HTTP API
```

The port may not take ownership of business invariants such as session lifecycle, pending-mode merge rules, rollback semantics, or stats payload interpretation. Those stay above `D1Connection`.

### D2. Minimal API Contract

The ADR fixes the contract, not the exact implementation:

```python
class D1AccessPort:
    def execute(self, sql, params=(), *, policy=None): ...
    def executemany(self, sql, seq_of_params, *, policy=None): ...
    def batch_execute(self, statements, *, policy=None): ...
    def flush(self, *, ordering_key=None): ...
    def drain_recovery(self, *, ordering_key=None, max_batches=None): ...
    def close(self): ...
```

`D1Connection` returns the same cursor-compatible surface as today (`lastrowid`, `rowcount`, `fetchone`, `fetchall`) so callers do not need to learn the port directly.

`RecoveryPolicy` metadata is used only for safe batching/replay decisions. At minimum it includes:

| Field | Purpose |
|---|---|
| `logical_db` | `history`, `reports`, or `operations` |
| `operation_type` | Human-readable category such as `pending_stage`, `stats_upsert`, `commit_apply_mark` |
| `idempotency_key` | Stable key used to collapse/reason about retries |
| `ordering_key` | FIFO key, defaulting to `<logical_db>:<session_id or global>` |
| `recovery_allowed` | Whether retry-exhausted writes may enter outbox |
| `max_attempts` | Replay cap before dead-lettering |

### D3. Synchronous Semantics First

Plain `execute()` remains synchronous. Ordinary SQL is not silently delayed. Only operations that are explicitly safe may use micro-batching or recovery outbox behavior.

This preserves current assumptions around:

- write-then-read visibility;
- `rowcount`;
- `lastrowid`;
- `DualConnection` drift accounting;
- guarded primary-key checks;
- `STRICT_DUAL_WRITE`.

### D4. Safe Batching

The port supports micro-batching, but the first rollout does not delay arbitrary SQL. Safe batches flush on:

- `flush()`;
- `commit()` / finalization boundaries;
- `close()`;
- batch size reaching `D1_BATCH_LIMIT` (current default: 50);
- elapsed time reaching `D1_FLUSH_INTERVAL_MS` (default candidate: 250 ms).

Phase 1 enables the existing `COMMIT_SESSION_BULK` path by default, because it is already tested and directly reduces pending commit D1 round-trips. Pending staging batch APIs are left for a later phase.

### D5. Recovery Outbox

On recoverable D1 write failure:

1. The port first performs synchronous retry/backoff using the existing transient/permanent classifier behavior.
2. If retry is exhausted, only a proven-safe operation may enter `reports/D1/d1_recovery_outbox.jsonl`.
3. The outbox stores complete SQL and params. This repository is private, and the complete payload is intentionally committed for cross-run and cross-runner recovery.
4. Successful replay moves records out of the active outbox into `reports/D1/d1_recovery_outbox.processed.jsonl`, mirroring the existing `d1_drift.jsonl` / `d1_drift.processed.jsonl` operational pattern.

Outbox events are append-only and use these states:

| State | Meaning |
|---|---|
| `queued` | Retry was exhausted and the safe operation was accepted for recovery |
| `attempting` | Replay has started for this event |
| `replayed` | Replay completed successfully |
| `dead_lettered` | Replay hit a permanent error or exceeded retry policy |
| `abandoned` | Operator intentionally stopped recovery for the event |

Replay is FIFO per `ordering_key`. Different ordering keys may drain independently.

### D6. Backend Semantics

The backend mode controls whether outbox queueing can count as success:

| Mode | Semantics |
|---|---|
| `STORAGE_BACKEND=d1` | Strong consistency. A D1 write must actually land in D1 before the caller sees success. The outbox can record diagnostics, but cannot turn failure into success. |
| `STORAGE_BACKEND=dual` | Safe operations may be treated as recoverable-success after the outbox is durably written, because SQLite has the local write. The relevant ordering key must be drained before session finalization/commit. |
| `STRICT_DUAL_WRITE=1` | Strict mode wins. Any D1 write failure still fails the transaction even if an outbox entry is queued. |

If replay reaches `dead_lettered`, the related ordering key is blocked. A session may not move to `finalizing` / `committed` while its ordering key has queued, attempting, or dead-lettered recovery work.

### D7. Safe Operation Admission

The port uses a hybrid admission model:

- Conservative built-in allowlist for obviously idempotent or keyed SQL, such as explicit `Id`/`Seq` inserts, `ON CONFLICT ... DO UPDATE`, and updates/deletes scoped by stable `Id`, `Seq`, or `SessionId`.
- Explicit `RecoveryPolicy` from hot business paths where SQL pattern matching is not enough.

DDL, schema migrations, unkeyed deletes, bare AUTOINCREMENT inserts, and order-sensitive SQL without a policy do not enter the outbox.

### D8. Read Behavior

The first version does not cache business SELECT results. Reads gain shared retry/metrics behavior, and the port may cache stable schema metadata such as `PRAGMA table_info` and selected `sqlite_master` lookups.

No TTL cache is introduced for `MovieHistory`, `ReportSessions`, stats, pending rows, or operations data.

### D9. Observability

The port emits structured logs plus a per-run summary at:

```text
reports/D1/d1_port_summary.json
```

The summary should include at least:

- D1 HTTP POST count;
- SQL statement count;
- batch count and average batch size;
- retry count and retry-success count;
- transient vs permanent failure count;
- outbox queued/replayed/dead-lettered counts;
- recovery drain duration;
- schema-cache hit/miss counts.

Do not flood `d1_drift.jsonl` with normal metrics. Drift remains an anomaly and verification log.

### D10. Gradual Enablement

The rollout is staged by code defaults and environment overrides. The ADR requires the gradual model; exact default flips happen in later PRs. Each phase has its own implementation plan (IMP), and those IMPs are the canonical execution plans for this ADR.

| Phase | Implementation plan | Default behavior | Opt-in / candidate behavior |
|---|---|---|---|
| Phase 1 | [IMP-012](../impl/IMP-012-d1-access-port-phase1-core.md) | `D1Connection` uses `D1AccessPort`; retry/metrics/schema-cache live; `COMMIT_SESSION_BULK` defaults on; `d1_port_summary.json` emitted; recovery inspect/replay CLI is available for tests/runbooks | Outbox and general micro-batching exist but are disabled |
| Phase 2 | [IMP-013](../impl/IMP-013-d1-access-port-phase2-recovery-outbox.md) | Outbox code still gated | `D1_RECOVERY_OUTBOX_ENABLED=1` allows safe operations to queue and replay |
| Phase 3 | [IMP-014](../impl/IMP-014-d1-access-port-phase3-safe-batching.md) | Ordinary SQL still synchronous | `D1_BATCHING_ENABLED=1` and `D1_FLUSH_INTERVAL_MS=250` allow safe-path micro-batching |
| Phase 4 | [IMP-015](../impl/IMP-015-d1-access-port-phase4-startup-replay.md) | Startup replay off | `D1_STARTUP_REPLAY_ENABLED=1` drains non-dead-lettered work on process startup |

Promotion gates should use `d1_port_summary.json`, pending verification records, and drift/dead-letter absence. A phase should not become default while it creates new pending residuals, new dead letters, or unexplained drift.

### D11. Workflow and Private-Payload Handling

Because `d1_recovery_outbox.jsonl` stores full SQL params, it is treated as private runtime state even though it is committed to this private repository.

When Phase 2 is enabled, workflows that commit D1 operational state must stage:

```text
reports/D1/d1_recovery_outbox.jsonl
reports/D1/d1_recovery_outbox.processed.jsonl
reports/D1/d1_port_summary.json
```

Any public publishing workflow must explicitly exclude the recovery outbox files or fail closed when they are present. This protects against accidentally copying full SQL params into a public mirror.

---

## Alternatives Considered

### Alternative A: Thin Port Only

Only move HTTP POST/retry into a port and leave batching/recovery untouched.

Rejected because it does not materially address the two motivating problems: short-interval D1 request bursts and network-failure write recovery.

### Alternative B: External HTTP Service or Worker Proxy

Add a separate service that all D1 traffic goes through.

Rejected for now. It adds another network dependency and deployment surface before the Python storage path is cleanly centralized.

### Alternative C: Aggressive Unified Queue for All Writes

Queue every D1 write and let the port flush/replay globally.

Rejected because it silently changes SQL timing and failure semantics. The current code relies on synchronous writes, `lastrowid`, `rowcount`, read-after-write behavior, and strict dual-write checks.

### Alternative D: SQLite Outbox

Store recovery work in a local SQLite table.

Rejected as the initial design. In `STORAGE_BACKEND=d1`, local `.db` files are not the source of truth and workflows already skip committing them. JSONL under `reports/D1` matches the existing drift/recovery audit pattern better.

### Alternative E: In-Memory Recovery Only

Retry in process memory without durable outbox.

Rejected because it cannot survive process exit, GitHub runner interruption, or long network instability.

---

## Consequences

### Positive

- Gives D1 a single Python access boundary.
- Reduces D1 request volume immediately by defaulting the existing bulk commit path on.
- Adds a durable recovery path for safe dual-mode D1 write failures.
- Preserves `D1Connection` and `DualConnection` caller contracts.
- Keeps business storage semantics out of the transport layer.
- Provides metrics for future rollout gates.

### Negative

- Adds a new storage-layer abstraction that must be kept small and disciplined.
- Full SQL params in the outbox increase private-repo data sensitivity.
- Recovery admission requires explicit metadata on some hot paths.
- The staged rollout adds several configuration switches that operators must understand.

### Risks and Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| A supposedly safe operation replays incorrectly | High | Conservative allowlist, explicit `RecoveryPolicy`, unit tests per operation type, dead-letter on uncertainty |
| Full outbox payload leaks through public publish | High | Public publish workflow must exclude outbox or fail closed |
| `STRICT_DUAL_WRITE` semantics become ambiguous | Medium | Strict mode always wins and forbids outbox soft-success |
| One poison event blocks unrelated recovery | Medium | FIFO is per ordering key, not global |
| Metrics become noisy | Low | Use `d1_port_summary.json`; keep `d1_drift.jsonl` anomaly-focused |

---

## Testing Impact

At minimum, implementation must update or add tests for:

- `D1Connection` delegating HTTP execution through `D1AccessPort`;
- retry/transient/permanent classification parity with current `d1_client.py`;
- schema metadata cache not caching business SELECT results;
- `COMMIT_SESSION_BULK` default-on behavior and env override;
- outbox state transitions: `queued`, `attempting`, `replayed`, `dead_lettered`, `abandoned`;
- ordering-key FIFO replay;
- `STORAGE_BACKEND=d1` strong consistency;
- `STORAGE_BACKEND=dual` safe-operation soft-success;
- `STRICT_DUAL_WRITE=1` overriding outbox soft-success;
- session finalization blocked until relevant recovery keys drain;
- processed-file migration preserving replay audit history.

`tests/unit/test_d1_dual.py` and `tests/unit/test_commit_session_bulk.py` are the main existing anchors.

---

## Documentation and Workflow Impact

When implementation changes behavior, update:

- root `README.md` and `README_CN.md` D1 configuration tables;
- `docs/handbook/en/self-hoster/configuration.md` and `docs/handbook/zh/self-hoster/configuration.md`;
- `docs/handbook/en/ops/d1-rollback.md` and `docs/handbook/zh/ops/d1-rollback.md`;
- companion wiki pages in `JAVDB_AutoSpider.wiki`;
- `.github/workflows/` staging rules for D1 recovery outbox, processed outbox, and port summary files.

No workflow default should enable outbox soft-success until tests, staging rules, and public-publish exclusions are in place.

---

## Related Decisions

- [ADR-005](ADR-005-db-py-retirement-and-repo-pattern.md) - Repo migration and eventual `db.py` retirement.
- [ADR-006](ADR-006-pending-mode-default-rollout.md) - Pending mode default and bake-gate model.
- [ADR-009](ADR-009-d1-drift-classifier-and-diagnose.md) - D1 transient classifier and drift diagnosis.
- [`docs/handbook/en/ops/d1-rollback.md`](../../handbook/en/ops/d1-rollback.md) - Current pending rollback and drift response runbook.
