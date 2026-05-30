# ADR-032: Mandatory Session Binding & Repo Interface Consolidation

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Completed — Phases 1 & 2 implemented 2026-05-29; Phase 3 deferred (D4) |
| **Date**    | 2026-05-29                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-005](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md) (this completes ADR-005 amendment-2's stated goal; see ADR-005 amendment-8) |

> Originated from the 2026-05-29 architecture review (Candidate C): [architecture-review-2026-05-29.html](../architecture/architecture-review-2026-05-29.html).

## Context

ADR-005 retired `db.py` and introduced the Repo classes. Its **amendment-2** (2026-05-17) superseded the original D5 "(conn, session_id) constructor" with a per-method `session_id` shape, and asserted:

> "D5's actual goal (eliminate the `db_session._active` thread-local global) is satisfied either way, because `session_id` still flows explicitly through every method that needs it."

**That assertion is incomplete in the current code.** The process-global session id (`_SESSION_ID_SENTINEL` → `get_active_session_id()`) still survives as an implicit fallback in several write functions:

- `javdb/storage/db/_db_operations.py` — ~10 functions default `session_id` to the sentinel and call `_resolve_session_id(session_id)` (`:104,115,257,326,368,427,474,490,521,580`).
- `javdb/storage/db/_db_history_write.py` — `db_batch_update_last_visited` (`:570-575`) and `db_batch_update_movie_actors` (`:652-657`) do the same.

So `session_id` does **not** flow explicitly through every write method — some silently fall back to the global. A caller that forgets to pass it writes an **untagged** row instead of failing. ADR-005's own Alternative-3 flagged exactly this: *"The Repo class's `session_id=None` default lets 'forgot to pass session' become an implicit bug again."*

Separately, the interface is **dual**: the same write operations are reachable both via the Repo classes **and** via module-level `db_*` functions re-exported from `javdb/storage/db/__init__.py` `__all__`. Two ways in; tests target the functions; callers must learn both.

### What this ADR is NOT

This is **not** a reversal of amendment-2. Per-method `session_id` (amendment-2's shape) is **kept**. Constructor binding (the original D5 wording) stays **rejected** — no production caller relies on instance-bound session state, and a single Repo instance legitimately services multiple sessions (rollback sweeps). This ADR **finishes** amendment-2's goal, it does not undo it.

### Scope correction (from grounding)

`HistoryRepo` is **not** a thin pass-through — it already owns deep SQL (`search_movies`, `search_torrents`, `export_*`, `load_history_joined`, the real `batch_update_movie_actors`). Only a handful of its methods are thin delegates (the exact set enumerated during implementation). `db_stage_history_write` / `db_commit_session_history` already take a **mandatory** `session_id` (no global fallback). So the genuine friction is narrower than "the Repo is shallow": it is (a) the surviving global fallback in operations + 2 batch functions, and (b) the dual public interface.

## Decision

Complete amendment-2's goal and consolidate to a single public storage interface, in two phases.

### Design Decisions

**D1. Make `session_id` mandatory — remove the global fallback (Phase 1).** Drop the `_SESSION_ID_SENTINEL` default from the operations functions and the two history-batch functions; require `session_id` explicitly. A caller that omits it now raises instead of silently writing an untagged row — the intended hardening (ADR-005 Alternative-3). Per-method binding is preserved. **Required is necessary but not sufficient:** for the two history-batch functions an explicit `session_id=None` under pending write mode (the only supported mode) is also rejected with `ValueError` — a `None` session would still bypass `PendingMovieHistoryWrites` staging and write an unrollbackable live row. So in pending mode the value must be *present and non-None*; a `None` session is only meaningful under a (currently non-existent) non-pending mode. (Review hardening — see Status Log.)

**D2. Make the Repo the single public interface (Phase 2).** Migrate the remaining direct `db_*` callers (~67 call expressions across ~28 non-test files) to Repo methods; stop re-exporting the migrated `db_*` write/operations functions from `__init__.__all__`; add the few missing thin Repo methods (e.g. `HistoryRepo.resume_finalizing_session`). Keep exporting stateless primitives (`get_db`, `*_DB_PATH`, `init_db`, `generate_session_id`, `generate_integer_id`).

**D3. Mandatory-raise is a behavior change, intentionally.** For callers that already pass `session_id` (the production write paths — `history_manager.py:177`, `rclone/manager.py:1255`, dedup via `OperationsRepo`) this is behavior-preserving. For any caller relying on the implicit global, the new failure mode (raise) is the desired hardening; each such site is audited and threaded explicitly (the one to verify: `pikpak/bridge.py` `db_append_pikpak_history`).

**D4. Phase 3 (delete the global readers) is deferred / gated.** Removing `set/get_active_session_id` entirely would require threading `session_id` through the orchestration readers (enumerated in Phase 1) including the **cross-process** detail-runner MovieClaim DO call (`detail/runner.py:155`) and subprocess workers. High risk, low marginal value once D1 makes the fallback unreachable from writes. Out of scope here; revisit separately.

**D5. Phase 4 (constructor binding) is rejected.** It would reverse amendment-2, add interface surface (constructor *plus* per-method override), and serve no caller that exists. Explicitly not done.

**D6. Some `db_*` stay module functions.** Stateless primitives (`generate_session_id`, `get_db`, `init_db`, `*_DB_PATH`) are not domain operations and are **not** forced into Repos. One-shot migration tools (`migrations/tools/*`) and the single-use `align_*` functions are **excluded** from the Phase-2 migration — pure churn with no maintainability payoff.

## Consequences

### Positive

- **Completes amendment-2** — `session_id` truly flows explicitly; the global is unreachable from writes.
- **Fixes a latent untagged-write bug** — "forgot to pass session" now raises instead of writing an unrollbackable row.
- **One public interface** — the Repo classes; the `db_*` facade stops being a second front door.
- **Test surface improves** — contract tests move onto the Repo; boundary tests forbid regression to raw `db_*`.

### Negative

- **Phase 2 is large churn** — ~67 call sites across ~28 files; a big, mechanical diff.
- **Behavior change** — newly-mandatory `session_id` turns a silent fallback into a raise (intended, but must catch every relying caller first).

### Risks

- **Missing a caller that relied on the global** → a `TypeError` at call time. That is the *desired* failure mode but must be found before prod (audit + tests). Prime suspect: `pikpak/bridge.py`.
- **The repo↔db↔repo import shim** (`_db_history_write.py` imports `history_repo`) must survive trimming `__init__` exports — it imports from submodules directly, so it is safe, but verify.

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 | [IMP-ADR032-01](IMP-ADR032-01-mandatory-session-id.md) | Remove `_SESSION_ID_SENTINEL` default from operations + 2 batch functions; thread `session_id` at every caller; "no silent global" tests | — |
| Phase 2 | [IMP-ADR032-02](IMP-ADR032-02-single-repo-interface.md) | Migrate ~67 `db_*` call sites to Repos; trim `__init__.__all__`; boundary tests | — |
| Phase 3 | (deferred) | Delete `set/get_active_session_id` readers | Gated — cross-process risk |

## Out of Scope

- Constructor-time session binding (D5, rejected).
- Deleting the session global readers (D4, deferred).
- Migration tools + `align_*` functions (D6, excluded from migration).

## Status Log

- 2026-05-29: Proposed (from architecture review Candidate C grilling). Recorded as a pointer in ADR-005 amendment-8.
- 2026-05-29: Phase 1 implemented & verified ([IMP-ADR032-01](IMP-ADR032-01-mandatory-session-id.md)) — `session_id` is now mandatory on the `_db_operations` write functions + the two `_db_history_write` batch functions; the process-global fallback is unreachable from them; callers threaded; a "no silent global" test was added. Phase 2 (single-Repo interface) and Phase 3 (delete the global) remain.
- 2026-05-29: Phase 1 review hardening (CodeRabbit, PR #122). Making `session_id` *required* stopped accidental omission, but an explicit `session_id=None` under pending mode still bypassed staging into an untagged live row in `db_batch_update_last_visited` / `db_batch_update_movie_actors` (D1 amended). Both now raise `ValueError` on pending+None before any DB access; a regression test was added to `test_mandatory_session_id.py`. Production callers (detail runner, legacy spider) always run inside an active session, so this is behavior-preserving for them; three session-less unit tests in `test_history_manager.py` were migrated to the session+commit flow.
- 2026-05-29: Phase 2 implemented ([IMP-ADR032-02](IMP-ADR032-02-single-repo-interface.md), split 2a/2b). **2a** (PR #127): added the missing thin Repo methods (`SessionLifecycleRepo`/`HistoryRepo`) and migrated every external `db_*` caller (integrations, apps/cli, spider, workflow, infra) to Repos. **2b**: repointed the implementation layer (Repos + `sessions/` + `rollback/`) off the `javdb.storage.db` facade to the `_db_*` submodules, and added an ast-based **boundary test** forbidding non-storage/non-migration production code from importing `db_*` from the facade. Scope correction: the literal hard-removal of the exports (the original Task-6 de-export proof) was measured at ~46 files / 33 tests and judged low marginal value vs the SQLite-retirement direction, so Phase 2b **enforces the boundary by test rather than by removal** — `__all__` is left intact. Phase 3 (delete the `set/get_active_session_id` global readers) remains deferred (D4).
