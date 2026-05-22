# IMP-ADR015-04: ADR-015 Phase 4 - Rclone Manager Split

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ADR-015 Phase 4 by splitting rclone manager into a typed command package and real CLI adapter while keeping a short bake wrapper for legacy imports and monkeypatches.

**Architecture:** `javdb.integrations.rclone.manager` becomes a package. `apps.cli.rclone.manager` owns CLI parsing and exit-code mapping. The package keeps a bake wrapper that re-exports selected legacy helpers until IMP-ADR015-05 removes it.

**Tech Stack:** Python 3.11, dataclasses, argparse, pytest, existing rclone helper/manager logic.

**Source spec:** [ADR-015](ADR-015-integrations-interface-boundary.md), D1-D6 and D8-D9.

**Non-negotiable:** Preserve rclone scan/report/execute/execute-soft-delete/validate behavior, mode validation, staging/swap semantics, CSV export, dry-run behavior, log streaming, and exit codes. Do not deep-split `javdb.integrations.rclone.helper` in this phase.

---

## Files

| Path | Responsibility |
|---|---|
| `javdb/integrations/rclone/manager.py` | Move into package as bake legacy implementation. |
| `javdb/integrations/rclone/manager/__init__.py` | Temporary bake wrapper exports plus new contract exports. |
| `javdb/integrations/rclone/manager/options.py` | `RcloneManagerOptions` dataclass. |
| `javdb/integrations/rclone/manager/result.py` | `RcloneManagerResult` dataclass. |
| `javdb/integrations/rclone/manager/service.py` | Manager orchestration service. |
| `apps/cli/rclone/manager.py` | Real CLI parser and adapter. |
| `tests/unit/test_rclone_manager_options.py` | New options/result/parser tests. |
| `tests/unit/test_rclone_manager.py` | Update or add service contract coverage while preserving wrapper tests. |
| `apps/cli/rclone/README.md` | Mark manager alias as replaced by real CLI adapter. |
| `javdb/integrations/rclone/README.md` | Document manager package and bake wrapper. |

---

## Task 1: Move Manager Module Into A Package

**Files:**
- Move: `javdb/integrations/rclone/manager.py`
- Create: `javdb/integrations/rclone/manager/__init__.py`

- [ ] **Step 1: Move current manager implementation.**

Run:

```bash
git mv javdb/integrations/rclone/manager.py javdb/integrations/rclone/manager_legacy_tmp.py
mkdir -p javdb/integrations/rclone/manager
git mv javdb/integrations/rclone/manager_legacy_tmp.py javdb/integrations/rclone/manager/_legacy.py
```

- [ ] **Step 2: Add bake wrapper exports.**

Create `javdb/integrations/rclone/manager/__init__.py`:

```python
"""Rclone manager service package.

The selected legacy exports remain during ADR-015 Phase 4 and are removed by
IMP-ADR015-05 after the bake window.
"""

from javdb.integrations.rclone.manager.options import RcloneManagerOptions
from javdb.integrations.rclone.manager.result import RcloneManagerResult
from javdb.integrations.rclone.manager.service import run_manager
from javdb.integrations.rclone.manager._legacy import (
    export_db_to_csv,
    export_dedup_history,
    list_remote_truth_paths,
    load_inventory_as_folder_structure,
    migrate_strip_drive_names,
    resolve_latest_dedup_file,
    resolve_rclone_root,
    run_execute_from_csv,
    run_execute_inventory_purge_from_csv,
    run_execute_soft_delete_from_csv,
    run_report_from_inventory,
    run_validate_inventory,
    scan_inventory,
    validate_dedup_records_against_inventory,
)

__all__ = [
    "RcloneManagerOptions",
    "RcloneManagerResult",
    "run_manager",
    "export_db_to_csv",
    "export_dedup_history",
    "list_remote_truth_paths",
    "load_inventory_as_folder_structure",
    "migrate_strip_drive_names",
    "resolve_latest_dedup_file",
    "resolve_rclone_root",
    "run_execute_from_csv",
    "run_execute_inventory_purge_from_csv",
    "run_execute_soft_delete_from_csv",
    "run_report_from_inventory",
    "run_validate_inventory",
    "scan_inventory",
    "validate_dedup_records_against_inventory",
]
```

---

## Task 2: Add Rclone Manager Contract

**Files:**
- Create: `javdb/integrations/rclone/manager/options.py`
- Create: `javdb/integrations/rclone/manager/result.py`
- Create: `tests/unit/test_rclone_manager_options.py`

- [ ] **Step 1: Write contract tests.**

Create `tests/unit/test_rclone_manager_options.py`:

```python
from __future__ import annotations

from apps.cli.rclone.manager import options_from_args, parse_args
from javdb.integrations.rclone.manager.options import RcloneManagerOptions
from javdb.integrations.rclone.manager.result import RcloneManagerResult


def test_rclone_options_defaults_for_report():
    options = options_from_args(parse_args(["--report"]))

    assert options.report is True
    assert options.scan is False
    assert options.execute is False
    assert options.execute_soft_delete is False
    assert options.validate is False
    assert options.workers == 4
    assert options.log_level == "INFO"


def test_rclone_parse_rejects_scan_execute_without_report():
    try:
        parse_args(["--scan", "--execute"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected parser error")


def test_rclone_result_exit_code():
    assert RcloneManagerResult(exit_code=0).exit_code == 0
    assert RcloneManagerResult(exit_code=1, error_reason="failed").exit_code == 1


def test_rclone_options_years_tuple():
    options = RcloneManagerOptions(scan=True, years=("2025", "2026"))

    assert options.years == ("2025", "2026")
```

- [ ] **Step 2: Implement options/result.**

Create `javdb/integrations/rclone/manager/options.py`:

```python
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class RcloneManagerOptions:
    scan: bool = False
    report: bool = False
    execute: bool = False
    execute_soft_delete: bool = False
    validate: bool = False
    root_path: str | None = None
    years: Sequence[str] | None = None
    workers: int = 4
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    output: str | None = None
    incremental: bool = False
    dry_run: bool = False
    dedup_csv: str | None = None
    soft_delete_csv: str | None = None
    soft_delete_backup_prefix: str = ""
    validate_prune: bool = True
```

Create `javdb/integrations/rclone/manager/result.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RcloneManagerResult:
    exit_code: int
    mode: str = ""
    error_reason: str | None = None
```

---

## Task 3: Replace CLI Alias With Real Adapter

**Files:**
- Modify: `apps/cli/rclone/manager.py`

- [ ] **Step 1: Move parser into `apps.cli.rclone.manager`.**

Replace the alias wrapper with the current parser from `_legacy.parse_arguments`,
renamed to `parse_args(argv: list[str] | None = None) -> argparse.Namespace`.

Keep these validations unchanged:

```python
if not (args.scan or args.report or args.execute or args.execute_soft_delete or args.validate):
    parser.error("At least one mode flag is required")
if args.scan and args.execute and not args.report:
    parser.error("--scan --execute requires --report (use --scan --report --execute)")
if args.validate and (args.scan or args.report or args.execute or args.execute_soft_delete):
    parser.error("--validate must be used on its own (no other mode flag)")
```

- [ ] **Step 2: Add options mapping and main.**

Add:

```python
from javdb.integrations.rclone.manager.options import RcloneManagerOptions
from javdb.integrations.rclone.manager.service import run_manager


def _parse_years(raw_years: str | None) -> list[str] | None:
    if not raw_years:
        return None
    years = [year.strip() for year in raw_years.split(",") if year.strip()]
    return years or None


def options_from_args(args: argparse.Namespace) -> RcloneManagerOptions:
    return RcloneManagerOptions(
        scan=args.scan,
        report=args.report,
        execute=args.execute,
        execute_soft_delete=args.execute_soft_delete,
        validate=args.validate,
        root_path=args.root_path,
        years=_parse_years(args.years),
        workers=args.workers,
        log_level=args.log_level,
        output=args.output,
        incremental=args.incremental,
        dry_run=args.dry_run,
        dedup_csv=args.dedup_csv,
        soft_delete_csv=args.soft_delete_csv,
        soft_delete_backup_prefix=args.soft_delete_backup_prefix,
        validate_prune=args.validate_prune,
    )


def main(argv: list[str] | None = None) -> int:
    return run_manager(options_from_args(parse_args(argv))).exit_code


if __name__ == "__main__":
    raise SystemExit(main())
```

---

## Task 4: Add Rclone Service

**Files:**
- Create: `javdb/integrations/rclone/manager/service.py`
- Modify: `javdb/integrations/rclone/manager/_legacy.py`

- [ ] **Step 1: Create `run_manager`.**

Create `javdb/integrations/rclone/manager/service.py`:

```python
from __future__ import annotations

from javdb.integrations.rclone.manager.options import RcloneManagerOptions
from javdb.integrations.rclone.manager.result import RcloneManagerResult


def run_manager(options: RcloneManagerOptions) -> RcloneManagerResult:
    from javdb.integrations.rclone.manager import _legacy

    exit_code = _legacy.run_manager_from_options(options)
    return RcloneManagerResult(exit_code=exit_code)
```

- [ ] **Step 2: Extract legacy main body into `run_manager_from_options`.**

In `javdb/integrations/rclone/manager/_legacy.py`, extract the current body of
`main()` after argument parsing into
`run_manager_from_options(options: RcloneManagerOptions) -> int`.

Replace every `args.<field>` access with `options.<field>`.

Keep `parse_arguments()` and `main()` in `_legacy.py` during Phase 4 so the bake
wrapper can support legacy tests. These are removed in IMP-ADR015-05.

---

## Task 5: Update Tests And Docs

**Files:**
- Modify: `tests/unit/test_rclone_manager.py`
- Modify: `apps/cli/rclone/README.md`
- Modify: `javdb/integrations/rclone/README.md`

- [ ] **Step 1: Add service contract tests.**

Add tests that monkeypatch `_legacy.run_manager_from_options` and assert
`run_manager(options)` returns `RcloneManagerResult(exit_code=<value>)`.

- [ ] **Step 2: Keep wrapper compatibility tests.**

Keep existing imports such as:

```python
from apps.cli.rclone.manager import run_report_from_inventory
```

working through the Phase 4 bake wrapper. These imports are migrated in
IMP-ADR015-05.

- [ ] **Step 3: Update READMEs.**

Document that rclone manager is in a Phase 4 bake state: real CLI adapter exists
under `apps.cli.rclone.manager`, while selected legacy package exports remain
until IMP-ADR015-05.

---

## Task 6: Verify Phase 4

- [ ] **Step 1: Run focused tests.**

```bash
pytest tests/unit/test_rclone_manager_options.py -v
pytest tests/unit/test_rclone_manager.py -v
pytest tests/unit/test_rclone_helper.py -v
pytest tests/architecture/test_integrations_interface_boundary.py -v
```

Expected: PASS.

- [ ] **Step 2: Verify alias wrapper is gone from apps CLI.**

```bash
rg -n "sys\\.modules\\[__name__\\]|import_module\\(\"javdb\\.integrations\\.rclone\\.manager" apps/cli/rclone/manager.py
```

Expected: no results.

- [ ] **Step 3: Review workflows and docs.**

```bash
rg -n "apps\\.cli\\.rclone|rclone_manager|rclone\\.manager" .github/workflows README.md JAVDB_AutoSpider.wiki 2>/dev/null
```

Expected: workflow command invocations remain unchanged.

- [ ] **Step 4: Commit.**

```bash
git add javdb/integrations/rclone/manager \
        apps/cli/rclone/manager.py \
        tests/unit/test_rclone_manager_options.py \
        tests/unit/test_rclone_manager.py \
        apps/cli/rclone/README.md \
        javdb/integrations/rclone/README.md
git add -u javdb/integrations/rclone/manager.py
git commit -m "refactor(integrations): split rclone manager service"
```
