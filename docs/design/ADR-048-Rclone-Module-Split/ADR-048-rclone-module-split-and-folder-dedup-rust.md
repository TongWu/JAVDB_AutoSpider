# ADR-048: Split the rclone Helper & Make the Folder-Dedup Cascade Rust-Required

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Partially implemented — Phase 1 shipped (2026-06-14); **Phase 2 & 3 not started** (the folder-dedup cascade still lives in Python — `dedup.py`'s `SIZE_THRESHOLD_RATIO` is a live production consumer). Execution tracked in [IMP-ADR048-01](IMP-ADR048-01-module-split-and-rust-folder-dedup.md) (3 phases) |
| **Date**    | 2026-06-13                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-041](../_archive/ADR-041-Rust-Fallback-Policy/ADR-041-rust-fallback-policy.md) (the fallback-tier policy this ADR instantiates and extends), [ADR-015](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md) (split the rclone **manager** but explicitly deferred the **helper** deep-split — ADR-048 is that deferred continuation), [ADR-035](../_archive/ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md) (Rust is the canonical parse path), [ADR-039](../ADR-039-Pluggable-Integration-Platform/ADR-039-pluggable-integration-platform.md) (plugin platform — owns the **downloader/notify** categories, **not** rclone cleanup) |

> Originated from the 2026-06-13 architecture review (Candidate 1 — "split `rclone/helper.py`"): [architecture-review-2026-06-13.html](../architecture/architecture-review-2026-06-13.html). The review found `javdb/integrations/rclone/helper.py` is a 1,438-line flat module whose 55-function interface forces `manager/service.py` to name-import 27 symbols spanning three structurally independent concerns, with one Rust migration debt buried inside the scan engine and a permanent-deletion cascade bundled into the same shallow surface.

## Context

`javdb/integrations/rclone/helper.py` (1,438 lines, 55 `def`s) is the rclone cleanup subsystem's only deep dependency. Its sole production caller, `javdb/integrations/rclone/manager/service.py`, name-imports **27 symbols** from it; the only other importer is `javdb/migrations/tools/strip_rclone_root_folder.py` (3 path symbols). The module is a flat **grab-bag** — one import surface in front of structurally independent concerns:

- **Path manipulation** (8 pure string functions: `strip_drive_name`, `strip_root_folder`, `to_full_remote_path`, …) — no I/O, no rclone.
- **Scan engine** (health-check prerequisites + folder-name parsing + `FolderCache` + 11 scan functions) — drives `rclone lsd`/`lsjson` subprocesses, logically one entry point `scan_folder_structure() → Dict[year, Dict[actor, List[FolderInfo]]]`.
- **Folder-dedup cascade** (`analyze_duplicates_for_code` + `_process_wuma_dedup` + `_apply_sensor_priority` + `_process_subtitle_dedup` + `execute_deletions`) — decides, for a `video_code` with multiple physical folders on Google Drive, which to keep and which to **`rclone purge` permanently** (helper.py:1227).

Three measured smells:

1. **The interface is as wide as the implementation** (shallow): testing any one concern loads the whole 1,438-line module; `service.py`'s 27-symbol import couples it to all three.
2. **A Rust migration debt is buried inside the scan engine.** `helper.py:754` routes the Python parser unconditionally because the Rust `parse_lsjson_for_year` (`rust_core/src/rclone_ops.rs:55`) still expects the **legacy 2-level** `<actor>/<code [sensor-subtitle]>` layout, while the on-remote layout migrated to **3-level** `<actor>/<code>/<sensor-subtitle>`. The debt is invisible because no module owns that seam exclusively. Rust `parse_lsd_output` and `group_by_movie_code` (same file) are likewise **already present but unused** by the Python scan path.
3. **A permanent-deletion driver shares a surface with pure utilities.** The dedup cascade — four ranking rules (uncensored sensor priority, 中字 > 无字, the 1.30× size exception) — drives irreversible `rclone purge` but enforces **no explicit invariant** that it never purges *every* folder for a code. Its safety-critical decision is buried among path helpers and CSV formatters.

There is also a **terminology hazard**: the repo has three things called "dedup" — the `DedupRecords` table + `spider/services/dedup.py` (skip-time: *don't re-download a code already present*), and this folder cascade (cleanup-time: *purge inferior physical copies already on Drive*). They are different operations sharing a word; the split is an opportunity to disambiguate them in CONTEXT.md.

**Lineage.** [ADR-015](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md) Phase 4–5 split the rclone *manager* into a typed command package + CLI adapter + storage Repos, but twice recorded a Non-negotiable: *"Do not deep-split `javdb.integrations.rclone.helper` in this phase."* The deep-split was **deferred, not rejected** — `helper.py` is the residue ADR-015 deliberately left whole. ADR-048 is that continuation, so it extends ADR-015 rather than re-litigating it.

## Decision

Split `helper.py` into **four deep modules**, adopt the already-shipped Rust scan primitives, and port the permanent-deletion cascade to Rust as a **Rust-Required** module — extending [ADR-041](../_archive/ADR-041-Rust-Fallback-Policy/ADR-041-rust-fallback-policy.md)'s tier policy with a second classification axis.

### Design Decisions

**D1. Four-module split (`javdb/integrations/rclone/`).** Each module is deep — a single primary entry point in front of its implementation:

| Module | Owns | Deep entry point |
| --- | --- | --- |
| `types.py` | `FolderInfo`, `DedupResult`, `DeletionRecord`, `SensorCategory`, `SubtitleCategory` + module constants (`SIZE_THRESHOLD_RATIO`, `INCREMENTAL_DAYS`, …) | (data only — breaks the `scan ↔ dedup` import cycle) |
| `path_utils.py` | 8 pure remote-path transforms + `get_configured_drive_name`/`get_configured_root_folder` | `to_full_remote_path`, `strip_root_folder` |
| `scan.py` | health-check prerequisites (`check_*`, `setup_rclone_config_from_base64`, `run_health_checks`) + folder parsing (`parse_leaf_name`, `parse_folder_name` + its `_py_parse_folder_name` Best-Effort fallback) + `FolderCache` + the 11 scan functions | `scan_folder_structure() → Dict[year, Dict[actor, List[FolderInfo]]]` |
| `dedup.py` | the ranking cascade + deletion execution (`rclone_purge`, `rclone_move`, `delete_folder`, `execute_deletions`) + rclone-only reporting (`format_size`, `generate_csv_report`, `print_summary`) | `analyze_all_duplicates()`, `execute_deletions()` |

`helper.py` is deleted; `service.py` and `strip_rclone_root_folder.py` re-point their imports to the new modules. The migrations tool imports `path_utils` only — confirming `path_utils` is a real seam (two adapters), not a hypothetical one.

**D2. `types.py` stays rclone-local — it is *not* `spider/contracts.py`.** `FolderInfo`/`DedupResult`/`DeletionRecord` are the cleanup-time dedup domain; `spider/contracts.py`'s `DedupRecord` (a skip-time NamedTuple) is a different concept that merely shares the stem "dedup". Co-locating them would re-introduce the very confusion this ADR sets out to name. `SensorCategory.get_priority` continues to read `UNCENSORED_SENSOR_PRIORITY` from `spider/contracts.py` (the single source of the priority table, already mirrored into `rust_core/src/dedup_ops.rs`).

**D3. Scan engine adopts the already-shipped Rust primitives (Phase 2).** `scan.py` routes through `rust_core` `parse_lsd_output` (replaces the manual `lsd` line-parsing in `get_year_folders`/`get_actor_folders`), `group_by_movie_code` (replaces Python `group_folders_by_movie_code`), and a **fixed** `parse_lsjson_for_year` updated for the 3-level layout — discharging the `helper.py:754` debt. These stay in the **Best-Effort** tier per ADR-041: folder parsing is inspectable, so `parse_leaf_name`/`_py_parse_folder_name` remain as a shape-contracted Python fallback. The behaviour target is value-parity with today's Python `get_all_movie_folders_for_year` (3-level), pinned by a fixture test before the Rust path is switched on.

**D4. The folder-dedup cascade becomes a Rust-Required module (Phase 3).** Port `analyze_duplicates_for_code` and its helpers (`_process_wuma_dedup`, `_apply_sensor_priority`, `_process_subtitle_dedup`) into `rust_core/src/dedup_ops.rs`. Following ADR-041's Rust-Required tier (as for `ProxyPool`/`ProxyBanManager`): the Python cascade is **removed**; constructing/invoking the dedup path without `javdb.rust_core` raises a clear error at the chokepoint (the `analyze_all_duplicates` entry in `dedup.py`), never silently degrades. The `rclone purge` *execution* (`execute_deletions`) stays Python — only the keep/delete *decision* moves to Rust.

**D5. The Rust cascade enforces, at the type/assertion level, invariants the Python version left implicit.** This is the leverage of the port, not just speed:

- **Partition** — every input folder lands in exactly one of `keep`/`delete` (`keep ∪ delete = input`, `keep ∩ delete = ∅`).
- **Non-empty keep** — a non-empty input *never* yields an empty keep set; the cascade can never purge every copy of a `video_code`. (The current Python code upholds this only incidentally; making it a checked invariant is the safety win.)
- **Single sensor winner** — within a subtitle group, at most one sensor-priority winner is kept.
- **Size-exception monotonicity** — a 无字 folder is kept over a 中字 folder only when `size > 1.30 × kept_中字_size`.

A violated invariant is a Rust-side error (fail-closed), not a silent mis-deletion.

**D6. This *extends* ADR-041's classification rubric — it does not amend or supersede it.** ADR-041 classifies a module Rust-Required when its behaviour is **stateful and not inspectable** (the proxy pool). The folder-dedup cascade is **pure and inspectable** (a dry-run prints keep/delete), so by ADR-041's literal criterion it would be Best-Effort. ADR-048 adds a **second trigger: irreversibility / blast radius.** A pure, inspectable function still qualifies as Rust-Required when a divergent fallback would cause **irreversible data loss** — here, a Best-Effort Python copy computing a different keep/delete set would permanently `rclone purge` the wrong folder, a data-loss trap rather than a debugging trap. ADR-041's policy text stands unchanged; this ADR owns the new axis and adds a back-reference line to ADR-041's Status Log.

**D7. Only the *decision* is Rust-Required; parsing and path utils remain Best-Effort.** `scan.py`'s `parse_leaf_name`/`parse_folder_name` and all of `path_utils.py` stay pure-Python-capable (inspectable output, no irreversible consequence), consistent with ADR-041 D1. The Rust-Required boundary is drawn precisely at the purge-driving keep/delete computation.

## Consequences

### Positive

- **locality** — the keep/delete decision lives in one place (Rust); the scan-layout seam lives in `scan.py` alone, so the `:754` migration debt surfaces and is dischargeable.
- **leverage** — `scan.py` exposes one entry (`scan_folder_structure`) over 11 functions; `dedup.py` exposes two over the cascade; `service.py`'s import narrows from one 27-symbol wide pull to four concern-scoped imports.
- **interface shrinks; implementation absorbs the helpers** — each module is deep where `helper.py` was shallow; tests hit one seam per concern instead of a 55-function module.
- **a latent data-loss path gains a checked invariant** — the "never purge every copy" guarantee becomes explicit and fail-closed (D5).
- **terminology disambiguated** — cleanup-time vs skip-time dedup get distinct CONTEXT.md entries.

### Negative

- **Three sequential PRs**, each gated; Phase 3 deletes a working Python implementation and replaces it with Rust, raising review weight on the highest-risk phase. Mitigated: behaviour tests migrate to the Rust cascade and run green *before* the Python deletion (same order as ADR-041 D5a).
- **Local dev without the Rust wheel loses the ability to run the dedup cascade.** Mitigated: cleanup runs only in CI (`DailyIngestion`, `AdHocIngestion`, `RcloneManager` workflows — all ship the wheel; ADR-041 D6); scan/path/scaffold still work; building the wheel is one `maturin develop` step.
- **`types.py` is a shallow module by construction** — it exists to break the import cycle, not to hide behaviour. Accepted: it is data, not a seam, and carries no entry point.

### Risks

- **A Rust port that diverges from the Python cascade purges the wrong folder.** Mitigated by D5 (checked invariants), a value-parity fixture pinned before the switch (generated multi-folder code sets compared Python-vs-Rust while both exist), and `--dry-run` defaults in the manager.
- **An importer outside the enumerated set breaks on the `helper.py` deletion.** Mitigated: Phase-1 Task 0 enumerates every importer of every moved symbol before the move; the gate stops if the set is larger than {`service.py`, `strip_rclone_root_folder.py`, the rclone test files}.

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 — Module split | [IMP-ADR048-01](IMP-ADR048-01-module-split-and-rust-folder-dedup.md) §Phase 1 | Pure relocation into `types`/`path_utils`/`scan`/`dedup`; `helper.py` deleted; imports re-pointed; behaviour unchanged; CONTEXT.md disambiguation terms | Any Rust change |
| Phase 2 — Rust scan adoption | [IMP-ADR048-01](IMP-ADR048-01-module-split-and-rust-folder-dedup.md) §Phase 2 | `scan.py` routes through Rust `parse_lsd_output`/`group_by_movie_code`; `parse_lsjson_for_year` fixed for 3-level layout (`:754` debt cleared); value-parity fixture | The dedup cascade |
| Phase 3 — Rust-Required folder dedup | [IMP-ADR048-01](IMP-ADR048-01-module-split-and-rust-folder-dedup.md) §Phase 3 | Cascade ported to `rust_core/src/dedup_ops.rs` with D5 invariants; Python cascade removed; chokepoint guard; behaviour tests repointed to Rust; CONTEXT.md Rust-Required extension | — |

### Explicit non-goals (YAGNI)

- **Not** moving rclone cleanup under the ADR-039 plugin platform — that platform owns the **downloader/notify** categories; rclone cleanup is not a pluggable backend.
- **Not** porting `execute_deletions`/`rclone_purge` to Rust — only the keep/delete *decision* is Rust-Required; the subprocess execution stays Python.
- **Not** touching `spider/services/dedup.py` (skip-time dedup) — that is review Candidate 2, a separate initiative.

## Domain Language (additions for CONTEXT.md)

- **Cleanup-time dedup (清理时去重)** — deciding, for a `video_code` with multiple physical folders already on a remote (Google Drive), which to keep and which to `rclone purge`, by the ranking cascade. Distinct from **skip-time dedup** (`DedupRecords` + `spider/services/dedup.py`: *don't re-download/re-upload a code already present*). Both are called "dedup"; one is "don't fetch", the other is "delete what exists".
- **Rclone scan engine (`rclone/scan.py`)** — health prerequisites + folder parsing + `FolderCache` + scan, behind one deep entry `scan_folder_structure() → Dict[year, Dict[actor, List[FolderInfo]]]`. Adopts Rust `rclone_ops` (`parse_lsd_output`/`group_by_movie_code`/`parse_lsjson_for_year`).
- **Folder dedup cascade (`rclone/dedup.py`)** — the four-rule keep/purge decision (uncensored sensor priority, 中字 > 无字, 1.30× size exception). A **Rust-Required Module** (ADR-041 tier, extended by ADR-048 D6): no Python fallback, because a divergent copy drives irreversible deletion.
- **Drive folder layout** — legacy 2-level `<actor>/<code [sensor-subtitle]>` (`_py_parse_folder_name`) migrated to 3-level `<actor>/<code>/<sensor-subtitle>` (`parse_leaf_name`); Rust `parse_lsjson_for_year` realigned to 3-level in Phase 2.
- **Rust-Required Module (extension)** — ADR-041 triggered on *statefulness + non-inspectability*; ADR-048 adds *irreversibility / blast radius* as a second trigger: a pure, inspectable function is Rust-Required when a divergent fallback causes irreversible data loss.

## Alternatives Considered

- **Relocate only, leave the Rust debt (3-module split, no Rust).** Rejected by the grilling: satisfies the split but leaves the `:754` debt and the implicit-invariant deletion path untouched — misses the "safer language" value.
- **Keep the dedup cascade as a Best-Effort fallback (Python mirror retained, shape-contracted).** Rejected: a Best-Effort copy that diverges silently purges the wrong folder; ADR-041's "best-effort proxy selection is a trap" reasoning applies *a fortiori* to permanent deletion.
- **Value-parity dual (keep Python cascade + parity test).** Rejected: re-introduces the two-language value lockstep ADR-041 explicitly retired; a parity test is a migration-time guard (used in Phase 3 before deletion), not a steady-state policy.
- **3 coarse modules (path/scan/dedup, fold types in) or 6 fine modules (path/health/parsing/scan/dedup/report).** Rejected in grilling for **4** (`types` extracted to break the cycle; health/parsing fold into `scan`; deletion/report fold into `dedup`) — deepest modules without over-splitting `service.py`'s import points.

## References

- [ADR-041 — Rust Core Fallback Policy](../_archive/ADR-041-Rust-Fallback-Policy/ADR-041-rust-fallback-policy.md)
- [ADR-035 — Site-Contract Drift Sentinel](../_archive/ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md)
- [ADR-015 — Integrations Interface](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md) (Phase 4–5 deferred the helper deep-split this ADR completes)
- 2026-06-13 architecture review: [architecture-review-2026-06-13.html](../architecture/architecture-review-2026-06-13.html)

## Status Log

- 2026-06-13: Proposed (from the 2026-06-13 architecture review, Candidate 1 grilling). Decided: 4-module split (`types`/`path_utils`/`scan`/`dedup`); Phase 2 adopts existing Rust scan primitives + fixes `parse_lsjson_for_year` for 3-level layout; Phase 3 ports the folder-dedup cascade to a **Rust-Required** module with explicit invariants (D5). Recorded the rubric extension (D6: irreversibility as a second Rust-Required trigger) and the cleanup-vs-skip dedup disambiguation. IMP-ADR048-01 (3 phases) pending review.
- 2026-06-14: **Phase 1 shipped** (pure module split — `helper.py` → `types`/`path_utils`/`scan`/`dedup`; behaviour unchanged). **Phases 2 & 3 not started.** Consequence to note: the folder-dedup keep/delete cascade (`analyze_duplicates_for_code` + helpers, with the 1.30× size exception) is **still Python**, and `types.py`'s `SIZE_THRESHOLD_RATIO` remains a live production consumer via `dedup.py` → `service.py` → the `RcloneManager` workflow. `dedup_ops.rs` does **not** yet own this decision (it only holds the skip-time `should_skip_from_rclone`/`check_dedup_upgrade`). Do not delete the Python cascade or `SIZE_THRESHOLD_RATIO` until Phase 3 (Steps P3.2/P3.5) lands.
