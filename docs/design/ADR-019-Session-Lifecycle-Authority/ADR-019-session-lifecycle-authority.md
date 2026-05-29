# ADR-019: Session Lifecycle Authority

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Accepted — Phase 1 implemented 2026-05-29; Phase 2 (CommitPipeline) deferred |
| **Date**    | 2026-05-29                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-005](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md) (Repo pattern, pending-mode commit/rollback), [ADR-012](../_archive/ADR-012-Pipeline-Run-Boundary/ADR-012-pipeline-run-structured-boundary.md) (run boundary) |

> Originated from the 2026-05-29 architecture review (Candidate E): [architecture-review-2026-05-29.html](../architecture/architecture-review-2026-05-29.html).

## Context

A pipeline **Session** moves through a state machine — `in_progress → finalizing → committed`, or `→ failed`. Today that machine has **no single owner**: its legal transitions are encoded inline in SQL `WHERE` clauses spread across four primitive functions, and the rest of the codebase reads/writes `ReportSessions.Status` directly from 15+ sites.

### The four transition primitives have inconsistent guards

All live in `javdb/storage/db/_db_reports.py`:

| Primitive | file:line | SQL guard | Allows |
| --- | --- | --- | --- |
| `db_begin_finalize_session` | `:741` (SQL `:750`) | `WHERE Id=? AND Status='in_progress'` | `in_progress → finalizing` (strict) |
| `db_finish_commit_session` | `:757` (SQL `:766`) | `WHERE Id=? AND Status='finalizing'` | `finalizing → committed` (strict) |
| `db_mark_session_committed` | `:147` (SQL `:164`) | `WHERE Id=? AND Status IS NOT 'committed'` | **any non-committed → committed** (loose — incl. `failed → committed`) |
| `db_mark_session_failed` | `:173` (SQL `:193`) | `WHERE Id=?` (**no status guard**) | **any → failed**, incl. `committed → failed` |

The last two are a **latent data-corruption path**: `db_mark_session_failed` can flip a `committed` session to `failed`, and `db_mark_session_committed` can resurrect a `failed` one. `db_rollback_session` defends against this in Python (`_db_rollback.py:403-427` skip the flag for committed/finalizing), but the *primitives themselves* offer no protection — any current or future caller can corrupt a committed run's protection.

### No `can_transition`, no isolated test

There is no `can_transition(from, to)` anywhere — legality lives in SQL strings and prose docstrings. Consequently transition legality **cannot be unit-tested without a real database**; every commit/rollback test builds real SQLite.

### Commit orchestration is large and untestable in pieces

`_commit_session_bulk` (`_db_history_write.py:1047-1380`, ~333 lines) and `db_commit_session_history` (`:1454-1653`, ~199 lines) interleave the *state-machine* concern (status flips at `:1535`, `:1636`) with the *data-movement* concern (prefetch → classify → batch-upsert → mark-applied). The classification core already separates "decide" from "execute" (it builds statement lists before any `_bulk_run`), but that boundary is a local variable, not a return value — so substeps can't be exercised alone.

## Decision

Introduce **`SessionLifecycle`** as the single authority for legal session-status transitions, and (deferred Phase 2) extract a **`CommitPipeline`** of named, individually-testable substeps. **These are two distinct concerns, sequenced** — Phase 1 ships alone.

### Design Decisions

**D1. `SessionLifecycle` is the single transition authority.** A new deep module `javdb/storage/sessions/lifecycle.py` exposes a small interface: `get_state(session_id) -> SessionState`, a **pure** `can_transition(from, to) -> bool` (zero DB — the single source of truth for the legal graph), and `transition(session_id, to)` which validates then dispatches to the existing primitive. All status **writes** route through it.

**D2. The legal graph; illegal edges raise, idempotent edges return 0.**

```text
in_progress → finalizing        in_progress → committed (staging fast-path, required by rclone/commit)
in_progress → failed            finalizing  → committed        finalizing → failed
X → X (idempotent, return 0)    committed/failed already-in-target → return 0
committed → failed   ── ILLEGAL → raise IllegalTransition
failed    → committed ── ILLEGAL → raise IllegalTransition
```

`transition` raises **only** on truly-illegal edges (`committed→failed`, `failed→committed`); for idempotent/no-op edges it returns `0`, **preserving** today's `n==0` control flow at `sessions/commit.py:222-228` and `apps/cli/db/commit_session.py:434`. The illegal edges are ones no current caller intentionally exercises, so raising there is strictly a safety improvement.

**D3. Route writes through it; leave reads; keep policy above it.** The ~9 status-write call-sites (`_db_history_write.py:1535,1636`; `_db_rollback.py:430`; `sessions/commit.py:216`; `apps/cli/db/commit_session.py:430`; `rclone/manager.py:1262,1267,1273,1536,1609,1660,1687`) call `transition`. Reads keep using `db_get_session_status` / `SessionsRepo`; `get_state` is offered as a typed alternative, not a forced migration. The existing **Python policy guards** in `_db_rollback.py` (committed-refusal, finalizing-skip) stay *above* `transition` — they decide *whether* to flag failed, which is policy, not legality.

**D4. Phase 2 — extract `CommitPipeline` (deferred).** Split `_commit_session_bulk` into `prefetch_pending` / `classify_and_resolve` (pure, with an injected live-lookup) / `batch_upsert` / `mark_applied`, behind a small interface, with the status flip delegating to `SessionLifecycle.transition`. **Honest note:** this does *not* reduce LOC — essential complexity (4-pass rescan, dual-backend ID pre-generation, D1 100-param chunking, conflict-deletion shadowing) is irreducible. The win is **isolation-testability** (classify with in-memory overlays, no DB), not fewer lines.

**D5. Two concerns, sequenced; Phase 1 ships first.** `SessionLifecycle` and `CommitPipeline` touch different files, solve different problems (legality vs data-movement), and carry different risk. The only coupling is the one-line `transition_status` delegation, so the authority must exist before the pipeline uses it. Ship Phase 1, verify in production, then Phase 2.

**D6. Reconcile CONTEXT.md drift.** `CONTEXT.md` documents a `ReportsRepo(conn, session_id).mark_committed()` / `.mark_failed()` interface that does not exist (actual: free functions in `_db_reports.py` + a mutator-less `SessionsRepo`). This work is the moment to align CONTEXT.md with reality and add the `SessionLifecycle` vocabulary.

## Consequences

### Positive

- **One legality source** — a pure `can_transition` exhaustively unit-testable over the 4×4 status matrix with no DB.
- **Fixes a latent data-corruption bug** — `committed→failed` and `failed→committed` become impossible to express.
- **locality** — transition knowledge concentrates in one module instead of four SQL `WHERE` clauses.
- **Phase 2: testable commit substeps** — `classify_and_resolve` tested with in-memory overlays, no real DB.

### Negative

- **Phase 1 reroutes ~9 write sites** — behavior-preserving, but a real (small) diff across storage + rclone + CLI.
- **Phase 2 may increase LOC** — interface + dataclass boilerplate; the payoff is testability, not size.
- **Core write-path risk** — both phases touch commit/rollback; behavior preservation is paramount.

### Risks

- **Silent-noop → raise** could change control flow if a caller relied on the unguarded edge. Mitigated by D2 (raise only on truly-illegal edges; idempotent edges still return 0).
- **rclone staging sessions** legitimately do `in_progress→committed` / `→failed` — the graph must keep both legal.
- The session-id **process global** (`_db_session`) is a separate concern (Candidate C), not addressed here.

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 | [IMP-ADR019-01](IMP-ADR019-01-session-lifecycle.md) | `SessionLifecycle` module + reroute ~9 writes + pure transition tests + CONTEXT.md reconcile | — |
| Phase 2 | [IMP-ADR019-02](IMP-ADR019-02-commit-pipeline.md) | `CommitPipeline` substeps extracted from `_commit_session_bulk`, status flip delegated to `SessionLifecycle` | Until Phase 1 is verified in production |

## Out of Scope

- The session-id process global (`set/get_active_session_id`) — Candidate C.
- The non-bulk `_commit_one_movie` fallback path and `_d1_retry_pending_cleanup` — left in the orchestrator.
- Mass-migrating status *reads* to `get_state` — unnecessary churn.

## Status Log

- 2026-05-29: Proposed (from architecture review Candidate E grilling).
- 2026-05-29: Phase 1 implemented & verified ([IMP-ADR019-01](IMP-ADR019-01-session-lifecycle.md)) — `SessionLifecycle` authority + all status writes rerouted; `committed→failed` / `failed→committed` now raise. Phase 2 (CommitPipeline) deferred pending production verification.
