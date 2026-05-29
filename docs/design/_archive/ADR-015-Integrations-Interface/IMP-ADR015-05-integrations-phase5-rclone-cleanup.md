# IMP-ADR015-05: ADR-015 Phase 5 - Rclone Cleanup

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ADR-015 Phase 5 by deleting the rclone manager bake wrapper, updating remaining imports and tests, and removing rclone from the ADR-015 architecture allowlists.

**Architecture:** After this phase, `apps.cli.rclone.manager` is the only rclone manager CLI surface, and `javdb.integrations.rclone.manager` exposes typed service contracts plus non-CLI helper exports only where intentionally retained.

**Tech Stack:** Python 3.11, pytest, AST architecture guard.

**Source spec:** [ADR-015](ADR-015-integrations-interface-boundary.md), D6, D8, and D9.

**Non-negotiable:** Preserve rclone behavior and exit codes while removing compatibility exports. Do not deep-split `javdb.integrations.rclone.helper` in this phase.

---

## Files

| Path | Responsibility |
|---|---|
| `javdb/integrations/rclone/manager/__init__.py` | Remove bake wrapper exports. |
| `javdb/integrations/rclone/manager/_legacy.py` | Delete after remaining implementation has moved into service/private helpers. |
| `javdb/integrations/rclone/manager/service.py` | Own final manager orchestration. |
| `apps/cli/rclone/manager.py` | Remove the Phase 4 bake re-export block; keep the real adapter (`parse_args`/`options_from_args`/`main`). |
| `tests/architecture/test_integrations_interface_boundary.py` | Remove rclone allowlist entries. |
| `tests/unit/test_rclone_manager.py` | Update imports and monkeypatch targets. |
| `apps/cli/rclone/README.md` | Remove bake wrapper note. |
| `javdb/integrations/rclone/README.md` | Document final manager service boundary. |

---

## Task 1: Move Remaining Legacy Logic Into Service

**Files:**
- Modify: `javdb/integrations/rclone/manager/service.py`
- Modify: `javdb/integrations/rclone/manager/_legacy.py`

- [ ] **Step 1: Move `run_manager_from_options` into `service.py`.**

Move the implementation of `_legacy.run_manager_from_options(options)` into
`javdb/integrations/rclone/manager/service.py`.

Keep the public service signature
`run_manager(options: RcloneManagerOptions) -> RcloneManagerResult`.

The function must return `RcloneManagerResult(exit_code=<old integer>)`.

- [ ] **Step 2: Move required private helpers.**

Move manager-only private helpers from `_legacy.py` into `service.py` when they
are used only by `run_manager`.

Keep reusable non-CLI functions in focused modules or re-export them explicitly
from `__init__.py` only when tests or production callers still use them.

> **Implementation note (move EVERYTHING non-CLI into `service.py`).** Practically,
> `_legacy.py` is deleted in Task 2, so every non-CLI top-level symbol it defines
> must land in `service.py`: the module constants (`RCLONE_FOLDER_PATH`,
> `REPORTS_DIR`, `RCLONE_INVENTORY_CSV`, `DEDUP_*`, `SOFT_DELETE_*`,
> `INVENTORY_FIELDNAMES`, `ORPHAN_REASON_SUFFIX`, `*_ORPHAN_FIELDNAMES`,
> `_YEAR_RE`, `REPO_ROOT`), every helper (`parse_root_path`, `resolve_rclone_root`,
> `_folder_to_row`, `_process_year`, `scan_inventory`, `export_db_to_csv`,
> `load_inventory_as_folder_structure`, `run_report_from_inventory`,
> `_persist_dedup_records`, `_write_*_orphan_csv`,
> `validate_dedup_records_against_inventory`, `_list_remote_dirs_for_year`,
> `list_remote_truth_paths`, `run_validate_inventory`, `export_dedup_history`,
> `migrate_strip_drive_names`, `_assert_remote_drive_resolved`,
> `resolve_latest_dedup_file`, `run_execute_from_csv`,
> `run_execute_soft_delete_from_csv`, `run_execute_inventory_purge_from_csv`,
> `run_rclone_manager`, `run_manager_from_options`, `_describe_mode`), and the
> `REPO_ROOT`/`os.chdir(... )` bootstrap. `service.py` is one dir deeper than
> `manager.py` was but SAME depth as `_legacy.py`, so keep `REPO_ROOT =
> Path(__file__).resolve().parents[4]`.
>
> **CLI-surface MUST NOT come along (the guard checks `service.py` with no
> allowlist):**
> - Do NOT move `parse_arguments`, `main`, `_options_from_arguments`, the
>   `if __name__ == "__main__"` block, `import argparse`, or the `sys.exit(main())`
>   line. (Verified: `sys.exit` appears ONLY in the `__main__` block, so once it
>   and `main()` are dropped, `service.py` has no `sys.exit`.)
> - `_describe_mode` IS used by `run_manager_from_options` (production path), so it
>   moves — but its current signature is `_describe_mode(args: argparse.Namespace)`.
>   Change the annotation to `_describe_mode(options: "RcloneManagerOptions")`
>   (it is already called with `options` and only reads mode flags that exist on
>   both types). Leaving `argparse.Namespace` would import `argparse` into
>   `service.py` and trip the guard's `argparse_namespace_annotation` token.
> - After the move, `service.py` must contain no `import argparse`.

---

## Task 2: Delete CLI Surface And Bake Wrapper

**Files:**
- Modify: `javdb/integrations/rclone/manager/__init__.py`
- Delete: `javdb/integrations/rclone/manager/_legacy.py`

- [ ] **Step 1: Replace `__init__.py` with final exports.**

> **Implementation note (retained programmatic exports):** two non-CLI functions
> have live PRODUCTION callers that import them from the package path and must
> stay re-exported (the ADR-015 boundary explicitly allows "non-CLI helper
> exports where intentionally retained"):
>
> - `run_rclone_manager` — imported by `apps/api/routers/operations.py`
>   (`from javdb.integrations.rclone.manager import run_rclone_manager`) and
>   patched in `tests/unit/test_operations_rclone_cleanup.py` at
>   `javdb.integrations.rclone.manager.run_rclone_manager`.
> - `run_execute_inventory_purge_from_csv` — imported by
>   `javdb/migrations/tools/align_inventory_with_moviehistory.py`.
>
> Both now live in `service.py` (moved from `_legacy.py` in Task 1). Re-export
> them from `__init__.py`. Dropping them breaks the REST router, the migrations
> tool, and the operations-cleanup tests.

Use:

```python
"""Rclone manager service package."""

from javdb.integrations.rclone.manager.options import RcloneManagerOptions
from javdb.integrations.rclone.manager.result import RcloneManagerResult
from javdb.integrations.rclone.manager.service import (
    run_manager,
    run_execute_inventory_purge_from_csv,
    run_rclone_manager,
)

__all__ = [
    "RcloneManagerOptions",
    "RcloneManagerResult",
    "run_manager",
    "run_execute_inventory_purge_from_csv",
    "run_rclone_manager",
]
```

- [ ] **Step 2: Delete `_legacy.py`.**

Run:

```bash
git rm javdb/integrations/rclone/manager/_legacy.py
```

- [ ] **Step 3: Remove the Phase 4 bake re-export from the CLI adapter.**

Delete the temporary bake re-export block at the bottom of
`apps/cli/rclone/manager.py` (the
`from javdb.integrations.rclone.manager import (load_inventory_as_folder_structure, …)`
block added in Phase 4). The adapter keeps only `parse_args`, `_parse_years`,
`options_from_args`, `main`, and the `if __name__ == "__main__"` block, importing
just `RcloneManagerOptions` and `run_manager`. After this, no test should import
domain helpers from `apps.cli.rclone.manager` (Task 3 migrates them to
`.service`). Update the adapter module docstring to drop the bake note.

---

## Task 3: Update Tests And Allowlist

**Files:**
- Modify: `tests/architecture/test_integrations_interface_boundary.py`
- Modify: `tests/unit/test_rclone_manager.py`

- [ ] **Step 1: Update test imports.**

Parser and exit-code tests import:

```text
apps.cli.rclone.manager
```

Service tests import:

```text
javdb.integrations.rclone.manager.service
```

Helper tests continue to import:

```text
javdb.integrations.rclone.helper
```

- [ ] **Step 2: Remove rclone allowlist entries.**

Remove any rclone entries from:

```python
INTEGRATION_CLI_SURFACE_ALLOWLIST
APPS_CLI_INTEGRATION_ALIAS_ALLOWLIST
```

No `javdb/integrations/rclone/manager/*` file may contain:

```text
argparse
parse_arguments
def main
sys.exit
__main__
```

---

## Task 4: Update Documentation

**Files:**
- Modify: `apps/cli/rclone/README.md`
- Modify: `javdb/integrations/rclone/README.md`

- [ ] **Step 1: Remove bake wording.**

Remove any Phase 4 bake-wrapper note from both READMEs.

- [ ] **Step 2: Document final boundary.**

`apps/cli/rclone/README.md` describes `manager.py` as the real CLI adapter.

`javdb/integrations/rclone/README.md` describes `manager/` as a service package
and `helper.py` as a still-large internal helper awaiting a follow-up ADR.

---

## Task 5: Verify Phase 5

- [ ] **Step 1: Run focused tests.**

```bash
pytest tests/architecture/test_integrations_interface_boundary.py -v
pytest tests/unit/test_rclone_manager_options.py -v
pytest tests/unit/test_rclone_manager.py -v
pytest tests/unit/test_rclone_helper.py -v
```

Expected: PASS.

- [ ] **Step 2: Run CLI-surface searches.**

```bash
rg -n "argparse|parse_arguments|def main|sys\\.exit|__main__" javdb/integrations/rclone/manager
rg -n "_legacy|sys\\.modules\\[__name__\\]" javdb/integrations/rclone/manager apps/cli/rclone/manager.py tests/unit/test_rclone_manager.py
```

Expected: no results.

- [ ] **Step 3: Commit.**

```bash
git add javdb/integrations/rclone/manager \
        apps/cli/rclone/manager.py \
        tests/architecture/test_integrations_interface_boundary.py \
        tests/unit/test_rclone_manager.py \
        apps/cli/rclone/README.md \
        javdb/integrations/rclone/README.md \
        docs/design/ADR-015-Integrations-Interface/IMP-ADR015-05-integrations-phase5-rclone-cleanup.md
git add -u javdb/integrations/rclone/manager/_legacy.py
git commit -m "refactor(integrations): remove rclone manager bake wrapper"
```
