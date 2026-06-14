# ADR-052: Consolidate Drift-Cell Helpers & Unify the Drift-Log Writer into `drift_io`

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Completed (2026-06-14) — implemented by [IMP-ADR052-01](IMP-ADR052-01-drift-io-consolidation.md) |
| **Date**    | 2026-06-13                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [BFR-016](../../BFR-016-Import-Time-DB-Path-Binding/BFR-016-import-time-db-path-binding.md) (import-time path binding — this ADR realizes its lesson for the drift log), [ADR-050](../ADR-050-Pending-Verify-Builder/ADR-050-pending-verify-record-builder.md) (shares the `append_jsonl_record` callers — coordinate the import path), [ADR-009](../ADR-009-D1-Drift-Classifier/ADR-009-d1-drift-classifier-and-diagnose.md) (owns drift-diagnose **semantics** — untouched), [ADR-047](../../ADR-047-Dual-Backend-Drift-Reconciliation/ADR-047-dual-backend-drift-reconciliation.md) (owns reconcile **logic** — does not touch these helpers), [ADR-042](../../ADR-042-D1-Atomic-Commit-Boundaries/ADR-042-d1-atomic-commit-boundaries.md) (the drift log is a **diagnostic write**) |

> Originated from the 2026-06-13 architecture review (Candidate 5 — "consolidate drift-cell helpers and the duplicate drift-log writer"): [architecture-review-2026-06-13.html](../../architecture/architecture-review-2026-06-13.html).

## Context

Drift detection and the drift log are spread across four files that each carry a private copy of the same low-level machinery. The grounding confirmed the duplication is real and (mostly) value-identical:

**Three stdlib helpers, copied:**

- `_values_equal` — `drift_diagnose.py:91` and `reconcile_d1_drift.py:249` are **functionally identical** (same int/int → `==`, `(int,float)` → `float()` compare, else `str==str`; only `reconcile`'s docstring is richer, explaining the `>2**53` precision rationale).
- `_row_to_dict` — `drift_diagnose.py:138` and `reconcile_d1_drift.py:203` are **byte-identical**.
- JSONL reader — **three copies, two behaviours**: `drift_diagnose._read_jsonl:110` and `pending_health._read_jsonl:67` skip malformed lines silently; `reconcile._read_drift_log:157` logs a `WARNING` on malformed lines.

**Two drift-log writers that are NOT identical** — the real locality defect:

| | `dual_connection._append_drift_record` (L206) | `lifecycle_helpers.append_jsonl_record` (L311) |
| --- | --- | --- |
| Path binding | module-level `_DRIFT_LOG_PATH` (**import-time** from `$REPORTS_DIR`) | **call-time** (`reports_dir=` → `$REPORTS_DIR` → `"reports"`) |
| Thread safety | holds `_DRIFT_LOG_LOCK` | unlocked |
| pytest guard | refuses when `_DRIFT_LOG_PATH` resolves to the tracked path | refuses when neither `reports_dir` nor `$REPORTS_DIR` is set |
| Error log | `logger.error` | `logger.warning` |

The two writers carry **manually-synchronized pytest guards** whose comments cross-reference each other (*"The sibling writer carries the same guard, so both drift-log writers are protected symmetrically"*) — a fix-once-fixed-everywhere defect split across two files. Separately, `_DRIFT_LOG_PATH` is referenced in **six user-facing log messages** (L451, L498, L750, L930, L1213, L1219), not only at the write — so it is doing double duty as write-target *and* message string, and its **import-time binding** is exactly the hazard [BFR-016](../../BFR-016-Import-Time-DB-Path-Binding/BFR-016-import-time-db-path-binding.md) documents. Tests isolate the drift log through **two different seams**: 3 files monkeypatch `_DRIFT_LOG_PATH`; the lifecycle tests set `$REPORTS_DIR` / pass `reports_dir=`.

Deletion test: deleting the helper copies concentrates ~30 lines in one place (small win — these rarely change). Deleting one of the two writers concentrates the lock, the guard, and the path resolution in one module and collapses the two test-isolation seams to one — that is where the candidate earns its keep.

## Decision

Extract one stdlib-only `drift_io` module, unify the two writers into it on **call-time** path binding, and retire the import-time `_DRIFT_LOG_PATH` constant.

### Design Decisions

**D1. New `javdb/storage/drift_io.py`, stdlib-only (zero `javdb` deps).** Holds `_values_equal`, `_row_to_dict`, `read_jsonl`, `append_jsonl_record`, and a `drift_log_path` resolver. Stdlib-only is a deliberate constraint: `javdb/migrations/tools/reconcile_d1_drift.py` already imports it, and a zero-`javdb`-dependency module can be imported from anywhere (storage, migrations/tools) without a cycle.

**D2. Consolidate the three stdlib helpers.** Move `_values_equal` (keeping `reconcile`'s richer precision docstring), `_row_to_dict` (byte-identical), and one `read_jsonl(path)`. `drift_diagnose`, `reconcile_d1_drift`, and `pending_health` delete their copies and import from `drift_io`.

**D3. `read_jsonl` warns on malformed lines.** Reconcile's behaviour wins — it is strictly more informative, and a malformed drift line is rare and worth surfacing. The two formerly-silent callers gain a harmless `WARNING`; no caller loses information.

**D4. One `append_jsonl_record(record, *, reports_dir=None, filename="d1_drift.jsonl")`, call-time bound, carrying the lock and a single guard.** This is the unified writer: it resolves `reports_dir` at call time (param → `$REPORTS_DIR` → `"reports"`), holds the `_DRIFT_LOG_LOCK` (the drift path is multi-threaded; adding the lock to the lifecycle path is harmless and safer), and carries one pytest guard. The name stays `append_jsonl_record` (the records are not all "drift" — pending-verify metrics share the file), reflecting its general role.

**D5. Retire the import-time `_DRIFT_LOG_PATH` constant (BFR-016).** Delete `dual_connection._DRIFT_LOG_PATH`, `_DRIFT_LOG_LOCK`, and `_append_drift_record`. Its 3 callers (L446, L485, L1207) call `drift_io.append_jsonl_record(record)`; the 6 log-message references call a new `drift_io.drift_log_path(reports_dir=None) -> str` resolver (same resolution, call-time). This converts the drift log from an import-time-bound path — the exact failure class BFR-016 documents — to a call-time-resolved one.

**D6. One test-isolation seam: `$REPORTS_DIR`.** The 3 tests that monkeypatch `_DRIFT_LOG_PATH` (`test_d1_dual.py`, `test_batch_c_movie_history_id.py`, `test_system_state_repo.py`) switch to setting `$REPORTS_DIR` (env), matching how the lifecycle/`drift_diagnose` tests already isolate. After this, there is one seam, not two.

**D7. No re-export shim — re-point the writer's callers.** `append_jsonl_record` moves from `lifecycle_helpers` to `drift_io`; its 6 callers (`drift_diagnose`, `sessions/commit`, `rollback/core`, `apps/cli/db/commit_session`) re-point the import. **Coordinate with [ADR-050](../ADR-050-Pending-Verify-Builder/ADR-050-pending-verify-record-builder.md)**: ADR-050's pending-verify emitters call `append_jsonl_record`; whichever lands second re-points to `drift_io`. If both land together, the builder calls `drift_io.append_jsonl_record`.

## Consequences

### Positive

- **locality** — one `append_jsonl_record` (lock + guard + path resolution) replaces two manually-synchronized writers; a `_values_equal` precision fix reaches `reconcile` and `drift_diagnose` at once.
- **interface shrinks** — four files stop carrying private copies; the drift log has one writer and one isolation seam.
- **a BFR-016-class hazard is removed** — the drift log path becomes call-time-resolved, not import-time-bound.
- **tests hit one seam** — `$REPORTS_DIR` isolates every drift-log writer; no more `_DRIFT_LOG_PATH`-vs-`REPORTS_DIR` split.
- **deletion test holds** — the deleted copies do not reappear; their behaviour concentrates in `drift_io`.

### Negative

- **`drift_io` is a low-depth utility module.** Accepted: it is a deliberate stdlib-only sink for shared machinery, importable without cycles; its leverage is locality across four callers, not behaviour-hiding.
- **Moderate churn** — 4 writer-caller imports + 3 helper-caller imports + 3 test-isolation rewrites + 6 log-message-reference swaps, in one PR. Mitigated: pure relocation; existing drift/dual/rollback suites are the regression gate.

### Risks

- **A subtly-different helper copy is consolidated, changing behaviour.** Mitigated: the grounding verified `_values_equal`/`_row_to_dict` are value-identical and the only JSONL difference is silent-vs-warn (D3 picks warn, losing no information).
- **A drift write that relied on import-time `$REPORTS_DIR` now resolves differently.** Mitigated: call-time resolution reads the same `$REPORTS_DIR`; the only observable change is that a mid-process `$REPORTS_DIR` change now takes effect (the BFR-016-correct behaviour). Tests set `$REPORTS_DIR` before the write.
- **ADR-050 import-path collision.** Mitigated by D7 — coordinate the `append_jsonl_record` move.

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 (only) | [IMP-ADR052-01](IMP-ADR052-01-drift-io-consolidation.md) | `drift_io.py` (helpers + `read_jsonl` + unified `append_jsonl_record` + `drift_log_path`); `_DRIFT_LOG_PATH`/`_DRIFT_LOG_LOCK`/`_append_drift_record` retired; all helper/writer callers + 6 log refs re-pointed; 3 tests moved to `$REPORTS_DIR`; CONTEXT.md terms | — |

### Explicit non-goals (YAGNI)

- **Not** changing any drift classification, diagnosis, or reconciliation logic (ADR-009 / ADR-047 own those).
- **Not** changing what is written to the drift log or its format (diagnostic write, ADR-042, unchanged).
- **Not** consolidating the pending-verify *builder* — that is [ADR-050](../ADR-050-Pending-Verify-Builder/ADR-050-pending-verify-record-builder.md) (this ADR owns the *writer* underneath it).

## Domain Language (additions for CONTEXT.md)

- **Drift IO (`javdb/storage/drift_io.py`)** — the stdlib-only module owning the shared drift-log machinery: cell-comparison helpers (`_values_equal`, `_row_to_dict`), the JSONL reader (`read_jsonl`, warns on malformed), the unified call-time-bound thread-safe writer (`append_jsonl_record`), and the path resolver (`drift_log_path`). Zero `javdb` dependencies, so importable from `storage` and `migrations/tools` alike.
- **Drift-log writer** — the single `drift_io.append_jsonl_record(record, *, reports_dir=None, filename="d1_drift.jsonl")` that appends one JSON line under a process lock with one pytest guard; replaces the former `dual_connection._append_drift_record` (import-time path) and `lifecycle_helpers.append_jsonl_record` (call-time path). Isolated in tests via `$REPORTS_DIR`.

## Alternatives Considered

- **Helpers only; leave the two writers.** Rejected in grilling: leaves the two manually-synchronized guards — the part that earns the candidate's keep.
- **Keep `_DRIFT_LOG_PATH` as the resolved default; keep import-time binding.** Rejected (D5): preserves the BFR-016 hazard and the two test seams.
- **Keep `append_jsonl_record` as a re-export shim in `lifecycle_helpers`.** Rejected (D7): two names for one writer; the call-site set is closed and small.

## References

- [BFR-016 — Import-Time DB Path Binding](../../BFR-016-Import-Time-DB-Path-Binding/BFR-016-import-time-db-path-binding.md)
- [ADR-050 — Pending Verify Record Builder](../ADR-050-Pending-Verify-Builder/ADR-050-pending-verify-record-builder.md)
- [ADR-009 — D1 Drift Classifier & Diagnose](../ADR-009-D1-Drift-Classifier/ADR-009-d1-drift-classifier-and-diagnose.md)
- [ADR-047 — Dual-Backend Drift Reconciliation](../../ADR-047-Dual-Backend-Drift-Reconciliation/ADR-047-dual-backend-drift-reconciliation.md)
- 2026-06-13 architecture review: [architecture-review-2026-06-13.html](../../architecture/architecture-review-2026-06-13.html)

## Status Log

- 2026-06-14: Completed in [IMP-ADR052-01](IMP-ADR052-01-drift-io-consolidation.md). The planned single phase shipped `drift_io.py`, consolidated JSONL/helper/writer paths, retired import-time drift-log binding, moved tests to the `$REPORTS_DIR` seam, and updated CONTEXT.md terminology. No follow-up IMP remains for this ADR.
- 2026-06-13: Proposed (from the 2026-06-13 architecture review, Candidate 5). Grounding verified: `_values_equal`/`_row_to_dict` value-identical; JSONL reader 2-silent/1-warn; the two writers diverge on path-binding (import-time `_DRIFT_LOG_PATH` vs call-time), locking, and log level, with manually-synchronized guards. Decided (grilling): consolidate helpers + unify the writer on **call-time** binding + retire `_DRIFT_LOG_PATH` (BFR-016-aligned); `read_jsonl` warns; one `$REPORTS_DIR` test seam; no shim. ADR-047 (implemented locally) and ADR-009 (archived) own drift logic/semantics, not these helpers — no collision. IMP-ADR052-01 pending.
