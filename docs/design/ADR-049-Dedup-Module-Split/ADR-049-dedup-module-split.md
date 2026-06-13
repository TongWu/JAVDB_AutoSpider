# ADR-049: Split `spider/services/dedup.py` into types / pure-query / store, and promote `normalise_code`

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Proposed — execution in [IMP-ADR049-01](IMP-ADR049-01-dedup-module-split.md) (single PR) |
| **Date**    | 2026-06-13                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-048](../ADR-048-Rclone-Module-Split/ADR-048-rclone-module-split-and-folder-dedup-rust.md) (**cleanup-time** dedup — sibling; this ADR owns **skip-time** dedup), [ADR-046](../_archive/ADR-046-Retire-Db-Facade/ADR-046-retire-db-facade.md) (Repos are the only public storage entry — `dedup_store` reads/writes via `OperationsRepo`), [ADR-011](../_archive/ADR-011-Parsing-Module/ADR-011-javdb-parsing-module.md) (the parsing module that becomes `normalise_code`'s home), [ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md) (ownership ledger reads consumed by `dedup_store`), [ADR-041](../_archive/ADR-041-Rust-Fallback-Policy/ADR-041-rust-fallback-policy.md) (the Rust dedup bridge `dedup_query` wraps stays Best-Effort) |

> Originated from the 2026-06-13 architecture review (Candidate 2 — "split `dedup.py`"): [architecture-review-2026-06-13.html](../architecture/architecture-review-2026-06-13.html).

## Context

`javdb/spider/services/dedup.py` (779 lines) is the **skip-time dedup** module — it decides whether to skip re-downloading/re-uploading a `video_code` already present in the rclone inventory, and persists `DedupRecord` rows. It interleaves three tiers of differing interface depth behind one import seam:

1. **Types** — `RcloneEntry`, `DedupRecord` (a pure NamedTuple), `DEDUP_FIELDNAMES` (L96–132). Zero dependencies.
2. **Pure-query** — the Rust dedup bridge wrappers (L53–77) + `should_skip_from_rclone` / `check_dedup_upgrade` / `check_redownload_dedup_upgrade` / `is_in_rclone_inventory` (L303–524). These take an already-loaded in-memory inventory dict and return a decision; no I/O.
3. **Store** — inventory loading from `OwnershipLedger`/`OperationsRepo`/CSV (L143–296) + `DedupRecord` persistence (L531–779), owning **two process-globals** `_db_initialised` (L80) and `_pending_paths_cache` (L531) that an autouse fixture in `tests/conftest.py` resets each test (L128–129).

Three measured smells:

- **Shallow seam over three concerns.** Any importer of `dedup.py` loads all three tiers. `pipeline/models.py` imports `DedupRecord` (a pure NamedTuple) at module level (L8) — and thereby drags the Rust bridge `try/except` and `OperationsRepo` into every transitive importer of `pipeline.models` (`planner`, `engine`, `detail/runner`, `apps/api/*`). The single type import pays the whole module's load cost.
- **A private normalizer copied verbatim across a package boundary.** `_normalise_code` (NFKC + strip + upper, L19–31) is duplicated into `ops/reconcile/code_resolver.py` (L44–46) with a docstring that admits *"identical to dedup._normalise_code"*. Separately, `ops/reconcile/service.py` lazily imports the **private** `_normalise_code` from `spider/services/dedup` (L285, L425) — an `ops/` → `spider/`-private cross-package pull.
- **CSV schema duplicated.** `javdb/migrations/tools/csv_to_sqlite.py` keeps its own parallel `_DEDUP_FIELDNAMES` list (L265–277).

Applying the deletion test: deleting `dedup.py` makes complexity reappear across 9 production importers — it earns its keep, but as three deep modules, not one shallow surface.

## Decision

Split `dedup.py` into three modules by tier, promote the normalizer to its true home, and delete the monolith.

### Design Decisions

**D1. Three modules in `javdb/spider/services/`.**

| Module | Owns | Depth |
| --- | --- | --- |
| `dedup_types.py` | `RcloneEntry`, `DedupRecord`, `DEDUP_FIELDNAMES` | shallow by construction — data only, zero deps; breaks the Rust-bridge/`OperationsRepo` transitive load |
| `dedup_query.py` | Rust dedup bridge wrappers + `RUST_DEDUP_AVAILABLE` + `should_skip_from_rclone`, `is_in_rclone_inventory`, `check_dedup_upgrade`, `check_redownload_dedup_upgrade` | deep — pure decisions over an in-memory inventory; no storage import |
| `dedup_store.py` | inventory loading (`load_rclone_inventory`, `_ledger_to_inventory`, `_open_ledger_for_dedup`, CSV fallback) + persistence (`append_dedup_record`, `mark_records_deleted`, `cleanup_deleted_records`, `load_dedup_csv`, `save_dedup_csv`, `export_dedup_db_to_csv`) + the two process-globals | deep — the only I/O tier; reads/writes via `OperationsRepo` (ADR-046) |

**D2. `dedup_types` stays in `spider/services/` — it is *not* `spider/contracts.py`.** Same reasoning as [ADR-048](../ADR-048-Rclone-Module-Split/ADR-048-rclone-module-split-and-folder-dedup-rust.md) D2: `DedupRecord` is skip-time-dedup domain data, not a cross-cutting contract like `UNCENSORED_SENSOR_PRIORITY`. Co-locating in `contracts.py` would conflate two layers; moving it to `pipeline/models.py` would create a cycle (`pipeline` already imports from `spider`, `spider` from `pipeline`). A zero-dependency module inside the services package isolates the types and breaks the transitive load.

**D3. Promote `_normalise_code` → public `normalise_code` in `parsing/common.py`.** The body is three lines of pure Unicode normalization with no spider/storage dependency; `parsing/common.py` already applies NFKC inline and is already imported by `code_resolver.py`, so promotion adds **no new import edge**. After: `dedup_query`/`dedup_store` import `normalise_code` from `parsing.common`; `code_resolver.py` deletes its verbatim copy; `ops/reconcile/service.py` imports `normalise_code` from `parsing.common` (not the spider private symbol); `migrations/tools/csv_to_sqlite.py` imports the canonical `DEDUP_FIELDNAMES` and deletes its local copy.

**D4. The split fixes the `ops/` → `spider/`-private layering pull.** Today `ops/reconcile/service.py` reaches into `spider/services/dedup` for the private `_normalise_code` and `RcloneEntry`. After the split, `ops/` imports `normalise_code` from `parsing/common` and `RcloneEntry` from `dedup_types` — no longer touching `dedup_store` or any spider-private symbol. The layering invariant (no reverse `apps`→`javdb`) is untouched; this removes an intra-`javdb` cross-package private coupling.

**D5. No re-export shim — delete `dedup.py` and re-point all call sites.** A shim would preserve the bundling (every importer still loads everything), defeating the import-isolation objective. The call-site list is closed and enumerated in the IMP (9 production files + `tests/conftest.py` 3-line fixture update + test files).

**D6. `should_skip_from_ownership` goes to `dedup_store` (it opens a live DB read), and is flagged as possible dead code.** It performs per-call `OwnershipLedgerRepo` I/O, so it belongs in `dedup_store`, not `dedup_query`. It has **zero production callers** (only `tests/unit/test_dedup_reads_ledger.py`); the IMP notes it for a separate dead-code decision — this ADR does **not** delete it (out of scope: behaviour-preserving relocation only).

## Consequences

### Positive

- **leverage** — pure-query callers (`detail/runner`, `planner`) stop loading `OperationsRepo`/the Rust bridge/the process-globals; a `DedupRecord` import costs a NamedTuple, not the whole module.
- **locality** — the two process-globals and all I/O concentrate in `dedup_store`, giving the autouse fixture a single target; `normalise_code` has one definition guarding all callers.
- **interface shrinks** — three deep modules replace one 779-line shallow seam; tests hit one tier per concern.
- **a layering pull and two duplications vanish** — the `ops`→spider-private import, the `code_resolver` copy, and the `csv_to_sqlite` field-list copy collapse to one canonical source each (deletion test: complexity does not reappear).

### Negative

- **Call-site churn in a single PR** — 9 production files + 13 test files re-point imports. Mitigated: the list is closed and enumerated; pure relocation, no behaviour change, so the existing suites are the regression gate.
- **Two new small modules** (`dedup_types` is shallow). Accepted: it exists to break the transitive load, not to hide behaviour.

### Risks

- **A missed importer breaks on `dedup.py` deletion.** Mitigated: IMP Task 0 enumerates every importer before the move; the gate stops if the set differs.
- **The autouse fixture is not re-pointed** → `AttributeError` on the removed globals. Mitigated: it is an explicit IMP step with its own gate.

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 (only) | [IMP-ADR049-01](IMP-ADR049-01-dedup-module-split.md) | Three-module split; `normalise_code` promotion; `dedup.py` deleted; all call sites + autouse fixture re-pointed; duplicate copies removed; import-isolation regression test; CONTEXT.md skip-time-dedup terms | A dead-code decision on `should_skip_from_ownership` |

### Explicit non-goals (YAGNI)

- **Not** changing any dedup decision logic or persistence behaviour — pure relocation.
- **Not** touching `rclone/helper.py` (cleanup-time dedup) — that is [ADR-048](../ADR-048-Rclone-Module-Split/ADR-048-rclone-module-split-and-folder-dedup-rust.md).
- **Not** deleting `should_skip_from_ownership` — flagged for a separate decision.

## Domain Language (additions for CONTEXT.md)

- **Skip-time dedup (跳过时去重)** — deciding during spider ingestion whether to skip re-downloading/re-uploading a `video_code` already in the rclone inventory; persists `DedupRecord`. Owned by `spider/services/dedup_query.py` (decision) + `dedup_store.py` (inventory + persistence). Distinct from **cleanup-time dedup** (ADR-048: `rclone/dedup.py`, purge inferior physical folders).
- **Dedup query module (`dedup_query.py`)** — the pure-computation tier: takes an already-loaded inventory dict, returns skip/upgrade decisions; wraps the Rust dedup bridge (Best-Effort, ADR-041); zero storage imports.
- **Dedup store module (`dedup_store.py`)** — the I/O tier: inventory loading (ownership ledger / `OperationsRepo` / CSV fallback) + `DedupRecord` persistence; owns the `_db_initialised` / `_pending_paths_cache` process-globals.
- **Code normalisation (`normalise_code`, 番号归一化)** — NFKC + strip + upper applied to a `video_code` before any lookup/comparison; the single public definition in `parsing/common.py` (was the private `_normalise_code` duplicated across `dedup` and `code_resolver`).

## Alternatives Considered

- **`DedupRecord`/`RcloneEntry` → `spider/contracts.py`.** Rejected (D2): conflates skip-time-dedup domain data with cross-cutting contracts; ADR-048 D2 set the precedent.
- **Keep `dedup.py` as a re-export shim.** Rejected (D5): preserves the bundled load, defeating the isolation objective.
- **`should_skip_from_ownership` → `dedup_query`.** Rejected (D6): it performs per-call DB I/O; placing it in the pure tier breaks the zero-storage invariant.

## References

- [ADR-048 — Rclone Module Split](../ADR-048-Rclone-Module-Split/ADR-048-rclone-module-split-and-folder-dedup-rust.md)
- [ADR-046 — Retire Db Facade](../_archive/ADR-046-Retire-Db-Facade/ADR-046-retire-db-facade.md)
- [ADR-011 — JavDB Parsing Module](../_archive/ADR-011-Parsing-Module/ADR-011-javdb-parsing-module.md)
- 2026-06-13 architecture review: [architecture-review-2026-06-13.html](../architecture/architecture-review-2026-06-13.html)

## Status Log

- 2026-06-13: Proposed (from the 2026-06-13 architecture review, Candidate 2). Decided: 3-module split (`dedup_types`/`dedup_query`/`dedup_store`), no shim; promote `_normalise_code`→`normalise_code` in `parsing/common.py`; `dedup_store` keeps the two process-globals and uses `OperationsRepo`. Verified: 779 lines (candidate said ~778); the `code_resolver` copy + `csv_to_sqlite` field-list copy + `ops`→spider-private import are real. `should_skip_from_ownership` flagged as possible dead code (zero prod callers). IMP-ADR049-01 pending.
