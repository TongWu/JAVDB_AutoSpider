# IMP-ADR048-01: Split the rclone Helper & Port the Folder-Dedup Cascade to Rust — Implementation Plan

> **Status: ✅ All 3 phases implemented (Phase 1 2026-06-14; Phases 2–3 2026-06-19).** Authored from the [ADR-048](ADR-048-rclone-module-split-and-folder-dedup-rust.md) grilling. Three sequential phases, each shipping as its own PR with a final verification gate. Phase 2 routed the scan engine through Rust `rclone_ops`; Phase 3 made the folder-dedup cascade Rust-Required (3a ported + proved parity, 3b removed the Python cascade + added the chokepoint guard).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-048](ADR-048-rclone-module-split-and-folder-dedup-rust.md) — Phase 1 (module split) / Phase 2 (Rust scan adoption) / Phase 3 (Rust-Required folder dedup). Instantiates [ADR-041](../_archive/ADR-041-Rust-Fallback-Policy/ADR-041-rust-fallback-policy.md)'s fallback tiers.

**Goal:** Turn the 1,438-line flat `rclone/helper.py` into four deep modules, route the scan engine through the already-shipped Rust `rclone_ops` primitives (clearing the `helper.py:754` layout debt), and port the permanent-deletion ranking cascade into Rust as a Rust-Required module with explicit safety invariants (ADR-048 D5).

**Architecture / approach:** Phase 1 is a **pure relocation** — no behaviour change, only import re-pointing; the deletion test holds (every moved symbol still has its caller). Phase 2 swaps the Python scan internals for existing Rust functions behind an unchanged `scan_folder_structure` interface, pinned by a value-parity fixture against today's Python output. Phase 3 follows ADR-041 D5a ordering: migrate the cascade behaviour tests to the Rust implementation and run them green **before** deleting the Python cascade. Production is CI-only (`DailyIngestion`/`AdHocIngestion`/`RcloneManager` workflows ship the Rust wheel), so the Rust-Required guard never fires in production.

**Tech Stack:** Python 3, `pytest`, Rust + PyO3 (`javdb.rust_core`, maturin), `subprocess`/`rclone`, `logging`.

**Verification posture:** The "no-Rust path" is simulated by monkeypatching the `rust_core` import / availability flag (the trick ADR-041's tests use) — do **not** uninstall the wheel. Phase 1 carries a behaviour-equivalence gate (the existing `test_rclone_helper.py` + `test_rclone_manager.py` suites must stay green unchanged except for import lines). Phase 2/3 add value-parity fixtures comparing Rust output to the frozen Python output before the Python path is removed.

---

## Phase 1 — Module split (PR 1, pure relocation)

### File Structure (Phase 1)

| Path | Create/Modify/Delete | Responsibility |
| --- | --- | --- |
| `javdb/integrations/rclone/types.py` | **Create** | `SensorCategory`, `SubtitleCategory`, `FolderInfo`, `DeletionRecord`, `DedupResult`, `_VALID_SENSORS`/`_VALID_SUBTITLES`, module constants (`SIZE_THRESHOLD_RATIO`, `BATCH_SIZE`, `DRY_RUN_*`, `INCREMENTAL_DAYS`, `VIDEO_EXTENSIONS`). Reads `UNCENSORED_SENSOR_PRIORITY` from `spider/contracts.py` (ADR-048 D2). Data only — breaks the scan↔dedup cycle. |
| `javdb/integrations/rclone/path_utils.py` | **Create** | `has_remote_prefix`, `strip_drive_name`, `get_configured_drive_name`, `get_configured_root_folder`, `strip_root_folder`, `prepend_root_folder`, `to_full_remote_path`, `prepend_drive_name`. Pure, no rclone subprocess. |
| `javdb/integrations/rclone/scan.py` | **Create** | health prereq (`setup_rclone_config_from_base64`, `check_rclone_installed`, `check_remote_exists`, `check_remote_folder_access`, `run_health_checks`) + parsing (`_py_parse_folder_name`, `parse_leaf_name`, `parse_folder_name` Rust-dispatch) + `FolderCache` + 11 scan functions. Deep entry: `scan_folder_structure`. |
| `javdb/integrations/rclone/dedup.py` | **Create** | cascade (`group_folders_by_movie_code`, `analyze_duplicates_for_code`, `_process_wuma_dedup`, `_apply_sensor_priority`, `_process_subtitle_dedup`, `analyze_all_duplicates`) + deletion exec (`rclone_purge`, `rclone_move`, `delete_folder`, `execute_deletions`) + rclone-only reporting (`format_size`, `generate_csv_report`, `print_summary`). Deep entries: `analyze_all_duplicates`, `execute_deletions`. |
| `javdb/integrations/rclone/helper.py` | **Delete** | Replaced by the four modules above. |
| `javdb/integrations/rclone/manager/service.py` | Modify | Re-point the 27-symbol import (lines 41–69) to the four new modules. |
| `javdb/migrations/tools/strip_rclone_root_folder.py` | Modify | Re-point `strip_drive_name`, `strip_root_folder`, `get_configured_root_folder` import to `path_utils`. |
| `tests/unit/test_rclone_helper.py` | Modify | Re-point imports to the new modules (assertions unchanged). |
| `tests/unit/test_rclone_manager.py` | Modify | Re-point the helper imports (line 26) + the `@patch('javdb.integrations.rclone.helper.…')` targets (lines 1260–1387) to the module that now owns each symbol. |
| `javdb/infra/logging.py` | Modify | The logger-name→label map (line 101) keys `'javdb.integrations.rclone.helper'`; replace with the four new module loggers (or a shared `RcloneHelper` label) so log formatting is unaffected. |
| `CONTEXT.md` | Modify | Add the cleanup-time vs skip-time dedup disambiguation + the scan-engine / folder-dedup-cascade terms (ADR-048 Domain Language). Phase-1-safe (describes the just-built structure). |

### Task 0: Baseline & importer enumeration (before any edit)

- [x] **Step 0.1 — Green baseline.** Record passing before changes:
  ```bash
  pytest tests/unit/test_rclone_helper.py tests/unit/test_rclone_manager.py -q
  ```
- [x] **Step 0.2 — Enumerate every importer of every symbol in `helper.py`.** All must resolve after the split:
  ```bash
  grep -rn "rclone.helper\|from javdb.integrations.rclone import helper" javdb apps scripts tests --include="*.py"
  ```
  **Verification gate:** the importer set must be exactly {`manager/service.py`, `migrations/tools/strip_rclone_root_folder.py`, `tests/unit/test_rclone_helper.py`, `tests/unit/test_rclone_manager.py`, `javdb/infra/logging.py` (label map)}. If anything else imports `helper`, STOP and reconcile against ADR-048 before moving symbols.

### Task 1: Create the four modules (move, do not rewrite)

- [x] **Step 1.1 — `types.py`.** Move the data classes + constants verbatim. Import `UNCENSORED_SENSOR_PRIORITY` from `javdb.spider.contracts`. No logic change.
- [x] **Step 1.2 — `path_utils.py`.** Move the 8 path functions verbatim. Only stdlib + `javdb.infra` imports.
- [x] **Step 1.3 — `scan.py`.** Move the health/parsing/`FolderCache`/scan functions verbatim. `from javdb.integrations.rclone.types import FolderInfo, …`. Keep the `parse_folder_name` Rust-dispatch try/except exactly as-is (ADR-048 D7 — still Best-Effort).
- [x] **Step 1.4 — `dedup.py`.** Move the cascade + deletion + reporting verbatim. `from javdb.integrations.rclone.types import DedupResult, DeletionRecord, FolderInfo, SensorCategory, SubtitleCategory, SIZE_THRESHOLD_RATIO`. Confirm no import of `scan` (the cascade consumes the *output* of scan, not its functions).
- [x] **Step 1.5 — Delete `helper.py`.**

  **Verification gate:** `python -c "import javdb.integrations.rclone.types, javdb.integrations.rclone.path_utils, javdb.integrations.rclone.scan, javdb.integrations.rclone.dedup; print('ok')"` — no `ImportError`, no circular-import error.

### Task 2: Re-point callers & tests

- [x] **Step 2.1 — `service.py`.** Replace the single 27-symbol `from …helper import (…)` block with four concern-scoped imports from `types`/`path_utils`/`scan`/`dedup`.
- [x] **Step 2.2 — `strip_rclone_root_folder.py`.** Import the 3 path symbols from `path_utils`.
- [x] **Step 2.3 — `test_rclone_helper.py` / `test_rclone_manager.py`.** Re-point imports; update every `@patch('javdb.integrations.rclone.helper.X')` to the module that now owns `X` (e.g. `subprocess.run` patches target `scan` or `dedup` depending on the function under test; `get_configured_drive_name` → `path_utils`).
- [x] **Step 2.4 — `infra/logging.py` label map.** Replace the `helper` key so the four new loggers format identically.

  **Verification gate:** `pytest tests/unit/test_rclone_helper.py tests/unit/test_rclone_manager.py -q` green **with assertions unchanged** from the Task 0.1 baseline (only import/patch lines differ). Re-run the Task 0.2 grep → empty for `helper`.

### Task 3: Docs & domain language

- [x] **Step 3.1 — CONTEXT.md.** Add **Cleanup-time dedup** vs **Skip-time dedup**, **Rclone scan engine**, **Folder dedup cascade**, **Drive folder layout** to the appropriate section + 术语对照表, verbatim from ADR-048's Domain Language. (Defer the **Rust-Required extension** term to Phase 3.)

  **Verification gate:** `grep -n "Cleanup-time dedup\|Folder dedup cascade" CONTEXT.md` non-empty.

### Phase 1 final gate

- [x] Focused rclone gate: `pytest tests/unit/test_rclone_helper.py tests/unit/test_rclone_manager.py -q` green (`169 passed`). Local broad `pytest tests/unit -q -k rclone` collection is unsuitable while unrelated local `javdb.rust_core` proxy symbols are missing.
- [x] `ruff check javdb/integrations/rclone` clean.
- [x] `grep -rn "rclone.helper" javdb apps scripts tests --include="*.py"` → empty.
- [x] `git diff --stat` shows `helper.py` deleted, four modules created, existing behaviour assertions preserved, and boundary/stats regression tests added.

---

## Phase 2 — Rust scan adoption (PR 2)

> Discharges the `helper.py:754` debt. The Rust functions already exist in `rust_core/src/rclone_ops.rs`; `parse_lsjson_for_year` needs a 3-level-layout fix; `parse_lsd_output` and `group_by_movie_code` are present and correct.

### File Structure (Phase 2)

| Path | Action | Responsibility |
| --- | --- | --- |
| `javdb/rust_core/src/rclone_ops.rs` | Modify | Fix `parse_lsjson_for_year` for the 3-level `<actor>/<code>/<sensor-subtitle>` layout: dirs at `parts.len()==3`, files at `>=4`; parse the leaf via a `parse_leaf_name`-equivalent (`rpartition('-')`, validate against `VALID_SENSORS`/`VALID_SUBTITLES`) instead of the bracket `FOLDER_PATTERN`; key by `(actor, movie_code, leaf)` and emit `size`/`file_count`. Mirror Python `get_all_movie_folders_for_year` (scan.py). |
| `javdb/integrations/rclone/scan.py` | Modify | Route `get_year_folders`/`get_actor_folders` lsd-parsing through Rust `parse_lsd_output`; route `get_all_movie_folders_for_year` through the fixed Rust `parse_lsjson_for_year`; route `group_folders_by_movie_code` users (in `dedup.py`) — see Step 2.3 — through Rust `group_by_movie_code`. Keep `parse_leaf_name`/`_py_parse_folder_name` as the Best-Effort fallback (ADR-048 D7). Remove the unconditional-Python comment at the old `:754`. |
| `tests/unit/test_rclone_scan_parity.py` | **Create** | Value-parity fixture: a recorded `rclone lsjson -R` JSON (3-level) + `lsd` output → assert Rust output `==` the frozen Python output, key-for-key. Pins behaviour before the switch. |

### Tasks (Phase 2)

- [x] **Step P2.1 — Freeze the Python baseline.** Capture today's Python `get_all_movie_folders_for_year` / `get_year_folders` output on a representative recorded `lsjson`/`lsd` fixture; commit as the parity golden.
- [x] **Step P2.2 — Fix Rust `parse_lsjson_for_year`** for the 3-level layout; add Rust `#[test]`s for 3-level dirs/files + leaf validation.
- [x] **Step P2.3 — Switch `scan.py` to the Rust primitives** behind the unchanged `scan_folder_structure` interface; keep the Best-Effort Python fallback on `ImportError` (loud `WARNING`, ADR-041 D3).

  **Verification gate:** `maturin develop --release` then `pytest tests/unit/test_rclone_scan_parity.py tests/unit -k rclone -q` green; the Rust and Python paths produce identical structures on the fixture; `--no-Rust` (monkeypatched) still scans via the Python fallback with a `WARNING`.

  **Divergence (2026-06-19):** `group_folders_by_movie_code` (`dedup.py`) was NOT routed through Rust `group_by_movie_code` as P2.3 originally listed. That function groups assembled `FolderInfo` dataclass instances across the whole `Dict[year][actor]->List[FolderInfo]`; Rust `group_by_movie_code` operates on plain dicts. Routing through it would force a lossy `FolderInfo`→dict→group→`FolderInfo` round-trip — strictly more code, slower, for a trivial non-hot-path `defaultdict` grouping. Left as pure Python, untouched.

### Phase 2 final gate

- [x] Rust `cargo test` (rclone_ops) green; `parse_lsjson_for_year` 3-level tests pass.
- [x] Parity golden matches Rust output; the `:754` unconditional-Python comment is gone.
- [x] `scan_folder_structure` interface unchanged (callers untouched).

---

## Phase 3 — Rust-Required folder dedup (PR 3, highest risk)

> ADR-048 D4/D5. **Order (ADR-041 D5a): migrate cascade behaviour tests to Rust and run green BEFORE deleting the Python cascade.**

### File Structure (Phase 3)

| Path | Action | Responsibility |
| --- | --- | --- |
| `javdb/rust_core/src/dedup_ops.rs` | Modify | Add the folder-dedup cascade: `analyze_duplicates_for_code(video_code, folders) -> DedupResultDict` + the wuma/sensor-priority/subtitle helpers. Enforce ADR-048 D5 invariants (partition, non-empty-keep, single sensor winner, 1.30× size-exception monotonicity) — return a Rust error on violation. Reuse the existing `uncensored_sensor_priority` table already in this file. |
| `javdb/rust_core/src/lib.rs` | Modify | Register the new `#[pyfunction]`(s) (after line 167, alongside `should_skip_from_rclone`). |
| `javdb/integrations/rclone/dedup.py` | Modify | `analyze_all_duplicates` / `analyze_duplicates_for_code` route to Rust; **remove** the Python cascade bodies (`_process_wuma_dedup`/`_apply_sensor_priority`/`_process_subtitle_dedup`); add the Rust-Required chokepoint guard (raise a clear `RuntimeError` if `javdb.rust_core` is unavailable, naming the wheel install step). Keep `execute_deletions`/`rclone_purge` in Python (ADR-048 D4). |
| `tests/unit/test_rclone_dedup.py` (or the cascade cases in `test_rclone_manager.py`) | Modify | Repoint cascade-behaviour tests to the Rust implementation (via the `dedup.py` entry); add invariant tests (never-empty-keep on every multi-folder fixture; partition; size-exception boundary at exactly 1.30×). |
| `tests/unit/test_rclone_dedup_parity.py` | **Create** | Generated multi-folder `video_code` sets → assert Rust keep/delete `==` the frozen Python keep/delete, while both still exist (pre-deletion parity). |
| `CONTEXT.md` | Modify | Add the **Rust-Required Module (extension)** term (irreversibility/blast-radius axis, ADR-048 D6). |
| ADR-041 `.md` + `.zh.md` | Modify | Status Log back-reference to ADR-048 (both languages, same commit) — see Step P3.6. |

### Tasks (Phase 3)

- [x] **Step P3.1 — Freeze the Python cascade baseline** on generated multi-folder fixtures (Phase 3a parity guard, then frozen as the golden in `test_rclone_dedup_golden.py`).
- [x] **Step P3.2 — Port the cascade to Rust** (`dedup_ops.rs` `analyze_folder_dedup`), enforcing the D5 invariants; Rust `#[test]`s incl. never-purge-all, the 1.30×-boundary, equal-priority tie, unclassifiable fail-closed, and the three invariant-guard `Err` arms (`EmptyKeep`/`PartitionViolation`/`MultipleSensorWinners`).
- [x] **Step P3.3 — Repoint cascade behaviour tests to Rust** — the `TestAnalyzeDuplicates`/`TestSizeException` suites now exercise the Rust path via `analyze_duplicates_for_code`, green **with the Python cascade still present** (Phase 3a, Gate 3.A).
- [x] **Step P3.4 — Parity check** — Phase 3a asserted Rust == Python on the generated sets (incl. a 20k-input brute force). Retired in 3b per the ADR; the fixtures live on as a frozen golden (`test_rclone_dedup_golden.py`).
- [x] **Step P3.5 — Remove the Python cascade bodies** (`_analyze_duplicates_for_code_py`/`_process_wuma_dedup`/`_apply_sensor_priority`/`_process_subtitle_dedup`) + added the `_require_rust_dedup` chokepoint guard; kept `execute_deletions`/`rclone_purge`; removed the orphaned `SensorCategory`/`SubtitleCategory`/`SIZE_THRESHOLD_RATIO` imports. Added a defensive partition assertion in the Python wrapper.
- [x] **Step P3.6 — Docs:** CONTEXT.md Rust-Required extension term + the now-done Phase 2/3 entries; ADR-041 Status Log back-ref (both languages): *"2026-06-13: Rust-Required tier extended by [ADR-048](../../ADR-048-Rclone-Module-Split/ADR-048-rclone-module-split-and-folder-dedup-rust.md) — the rclone folder-dedup cascade joins ProxyPool/ProxyBanManager as a Rust-Required module, on a new irreversibility/blast-radius trigger (ADR-048 D6)."*; flipped ADR-048 + this IMP status to Completed.

  **Verification gate:**
  ```bash
  grep -rn "_process_wuma_dedup\|_apply_sensor_priority\|_process_subtitle_dedup" javdb/integrations/rclone   # expect: none (moved to Rust)
  python -c "import javdb.integrations.rclone.dedup; print('ok')"
  # no-Rust guard fires:
  pytest tests/unit -k "rclone_dedup" -q
  ```

### Phase 3 final gate

- [x] Rust `cargo test` (dedup_ops) green incl. invariant tests (24 passed).
- [x] Cascade behaviour tests green against Rust; golden (`test_rclone_dedup_golden.py`) matches.
- [x] No-Rust path raises the clear chokepoint `RuntimeError` (monkeypatched `TestRustRequiredChokepoint`).
- [x] `pytest tests/unit tests/smoke -q -k "rclone or dedup"` green (448 passed); `ruff check javdb/integrations/rclone javdb/rust_core` clean.
- [x] The `test_rclone_manager.py` dry-run cases (the fixture-backed stand-in for `apps.cli.rclone.manager`) produce the same keep/delete report as pre-port.

---

## Rollback

- **Phase 1** — pure refactor; revert the PR. No data/schema/D1/Rust change.
- **Phase 2** — revert the PR; the Python scan fallback already exists, so reverting restores the unconditional-Python path (`:754`).
- **Phase 3** — revert the PR restores the Python cascade. **Caveat:** Phase 3 is a permanent-deletion path; the parity golden (P3.4) + dry-run gate must pass before merge. No schema/D1 change.

## Out of Scope

- `spider/services/dedup.py` (skip-time dedup) — review Candidate 2, separate initiative.
- Porting `execute_deletions`/`rclone_purge` to Rust — only the keep/delete decision is Rust-Required (ADR-048 D4).
- Any change to the ADR-039 plugin platform — rclone cleanup is not a pluggable backend.
