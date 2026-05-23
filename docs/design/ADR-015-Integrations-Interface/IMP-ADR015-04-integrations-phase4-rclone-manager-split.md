# IMP-ADR015-04: ADR-015 Phase 4 - Rclone Manager Split

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ADR-015 Phase 4 by splitting rclone manager into a typed command package and real CLI adapter, while routing its scan persistence/session lifecycle DB writes through storage Repos and keeping a short bake wrapper for legacy imports and monkeypatches.

**Architecture:** `javdb.integrations.rclone.manager` becomes a package. `apps.cli.rclone.manager` owns CLI parsing and exit-code mapping. Rclone scan persistence uses `OperationsRepo` for inventory staging/swap and a narrow `SessionLifecycleRepo` for `ReportSessions` lifecycle writes. The package keeps a bake wrapper that re-exports selected legacy helpers until IMP-ADR015-05 removes it.

**Tech Stack:** Python 3.11, dataclasses, argparse, pytest, existing rclone helper/manager logic.

**Source spec:** [ADR-015](ADR-015-integrations-interface-boundary.md), D1-D6 and D8-D9.

**Non-negotiable:** Preserve rclone scan/report/execute/execute-soft-delete/validate behavior, mode validation, staging/swap semantics, CSV export, dry-run behavior, log streaming, and exit codes. Do not deep-split `javdb.integrations.rclone.helper` in this phase. After this phase, `run_rclone_manager` and the CLI scan path must not import or call the raw `init_db`, `db_create_report_session`, `db_mark_session_committed`, `db_mark_session_failed`, `db_open_rclone_staging`, `db_append_rclone_staging`, `db_swap_rclone_inventory`, `db_merge_rclone_inventory_from_stage`, `db_drop_rclone_staging`, or `get_active_session_id` functions directly from `javdb.storage.db`.

---

## Files

| Path | Responsibility |
|---|---|
| `javdb/integrations/rclone/manager.py` | Move into package as bake legacy implementation. |
| `javdb/integrations/rclone/manager/__init__.py` | Temporary bake wrapper exports plus new contract exports. |
| `javdb/integrations/rclone/manager/options.py` | `RcloneManagerOptions` dataclass. |
| `javdb/integrations/rclone/manager/result.py` | `RcloneManagerResult` dataclass. |
| `javdb/integrations/rclone/manager/service.py` | Manager orchestration service. |
| `javdb/storage/repos/session_lifecycle_repo.py` | Write-Repo wrapper for `ReportSessions` lifecycle helpers needed by rclone scan persistence. |
| `javdb/storage/repos/README.md` | Document the new session lifecycle write Repo. |
| `apps/cli/rclone/manager.py` | Real CLI parser and adapter. |
| `tests/unit/test_session_lifecycle_repo.py` | New Repo delegation tests for ReportSessions lifecycle writes. |
| `tests/unit/test_rclone_manager_options.py` | New options/result/parser tests. |
| `tests/unit/test_rclone_manager.py` | Update or add service contract coverage while preserving wrapper tests. |
| `apps/cli/rclone/README.md` | Mark manager alias as replaced by real CLI adapter. |
| `javdb/integrations/rclone/README.md` | Document manager package and bake wrapper. |

## Issue #79 Validation

Issue [#79](https://github.com/TongWu/JAVDB_AutoSpider_CICD/issues/79)
identified that `run_rclone_manager` still calls raw `db_*` functions from the
integration layer. The recommendation is valid and feasible:

- `OperationsRepo` already wraps the rclone staging/swap operations.
- `ReportSessions` lifecycle writes need a narrow write Repo because the
  existing `SessionsRepo` is a conn-owned read/API surface.
- ADR-015 Phase 4 is the right home because it is already splitting rclone
  manager and touching the same scan orchestration code.

This is not a new ADR. It is an explicit Phase 4 acceptance criterion.

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

## Task 3: Add Session Lifecycle Write Repo

**Files:**
- Create: `javdb/storage/repos/session_lifecycle_repo.py`
- Modify: `javdb/storage/repos/README.md`
- Create: `tests/unit/test_session_lifecycle_repo.py`

- [ ] **Step 1: Write Repo delegation tests.**

Create `tests/unit/test_session_lifecycle_repo.py`:

```python
from unittest.mock import patch

from javdb.storage.repos.session_lifecycle_repo import SessionLifecycleRepo


def test_create_report_session_delegates_with_db_path():
    repo = SessionLifecycleRepo(db_path="/tmp/reports.db")

    with patch("javdb.storage.db.db_create_report_session", return_value="sess-1") as mock_fn:
        result = repo.create_report_session(
            report_type="rclone_inventory",
            report_date="20260523",
            csv_filename="rclone_inventory.csv",
        )

    assert result == "sess-1"
    mock_fn.assert_called_once_with(
        report_type="rclone_inventory",
        report_date="20260523",
        csv_filename="rclone_inventory.csv",
        db_path="/tmp/reports.db",
    )


def test_mark_session_committed_delegates():
    repo = SessionLifecycleRepo()

    with patch("javdb.storage.db.db_mark_session_committed", return_value=1) as mock_fn:
        assert repo.mark_session_committed("sess-1") == 1

    mock_fn.assert_called_once_with("sess-1", db_path=None)


def test_mark_session_failed_delegates_with_reason():
    repo = SessionLifecycleRepo(db_path="/tmp/reports.db")

    with patch("javdb.storage.db.db_mark_session_failed", return_value=1) as mock_fn:
        assert repo.mark_session_failed("sess-1", reason="scan_failed") == 1

    mock_fn.assert_called_once_with(
        "sess-1",
        db_path="/tmp/reports.db",
        reason="scan_failed",
    )


def test_active_session_id_delegates():
    repo = SessionLifecycleRepo()

    with patch("javdb.storage.db.get_active_session_id", return_value="sess-1") as mock_fn:
        assert repo.get_active_session_id() == "sess-1"

    mock_fn.assert_called_once_with()


def test_init_storage_delegates():
    repo = SessionLifecycleRepo(db_path="/tmp/reports.db")

    with patch("javdb.storage.db.init_db") as mock_fn:
        repo.init_storage()

    mock_fn.assert_called_once_with(db_path="/tmp/reports.db")
```

- [ ] **Step 2: Run tests to verify they fail before implementation.**

```bash
pytest tests/unit/test_session_lifecycle_repo.py -v
```

Expected: FAIL with `ModuleNotFoundError` for
`javdb.storage.repos.session_lifecycle_repo`.

- [ ] **Step 3: Implement the Repo.**

Create `javdb/storage/repos/session_lifecycle_repo.py`:

```python
"""Write-Repo wrapper for ReportSessions lifecycle mutations."""

from __future__ import annotations

from typing import Optional


class SessionLifecycleRepo:
    """Thin typed wrapper over ReportSessions lifecycle write helpers.

    This intentionally stays separate from SessionsRepo: SessionsRepo is a
    conn-owned read/API surface, while this Repo mirrors the write-family
    pattern used by HistoryRepo, OperationsRepo, and StatsRepo.
    """

    def __init__(self, *, db_path: Optional[str] = None) -> None:
        self._db_path = db_path

    def init_storage(self) -> None:
        from javdb.storage.db import init_db

        init_db(db_path=self._db_path)

    def get_active_session_id(self) -> Optional[str]:
        from javdb.storage.db import get_active_session_id

        return get_active_session_id()

    def create_report_session(
        self,
        *,
        report_type: str,
        report_date: str,
        csv_filename: str,
    ) -> str:
        from javdb.storage.db import db_create_report_session

        return db_create_report_session(
            report_type=report_type,
            report_date=report_date,
            csv_filename=csv_filename,
            db_path=self._db_path,
        )

    def mark_session_committed(self, session_id: str) -> int:
        from javdb.storage.db import db_mark_session_committed

        return db_mark_session_committed(session_id, db_path=self._db_path)

    def mark_session_failed(
        self,
        session_id: str,
        *,
        reason: Optional[str] = None,
    ) -> int:
        from javdb.storage.db import db_mark_session_failed

        return db_mark_session_failed(
            session_id,
            db_path=self._db_path,
            reason=reason,
        )
```

- [ ] **Step 4: Document the Repo shape.**

Add a row to `javdb/storage/repos/README.md`:

```markdown
| `session_lifecycle_repo.py` | `SessionLifecycleRepo` write wrapper for ReportSessions lifecycle helpers used outside API read contexts. |
```

- [ ] **Step 5: Run Repo tests.**

```bash
pytest tests/unit/test_session_lifecycle_repo.py -v
```

Expected: PASS.

---

## Task 4: Replace CLI Alias With Real Adapter

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

## Task 5: Add Rclone Service

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

## Task 6: Route Rclone Scan Persistence Through Repos

**Files:**
- Modify: `javdb/integrations/rclone/manager/_legacy.py`
- Modify: `tests/unit/test_rclone_manager.py`

- [ ] **Step 1: Add a raw-DB boundary regression test.**

Add this test to `tests/unit/test_rclone_manager.py`:

```python
def test_rclone_scan_persistence_does_not_import_raw_db_helpers():
    import ast
    from pathlib import Path

    source = Path("javdb/integrations/rclone/manager/_legacy.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    forbidden = {
        "db_create_report_session",
        "db_mark_session_committed",
        "db_mark_session_failed",
        "init_db",
        "db_open_rclone_staging",
        "db_append_rclone_staging",
        "db_swap_rclone_inventory",
        "db_merge_rclone_inventory_from_stage",
        "db_drop_rclone_staging",
        "get_active_session_id",
    }

    imported = set()
    used_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "javdb.storage.db":
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Name):
            used_names.add(node.id)

    assert forbidden.isdisjoint(imported)
    assert forbidden.isdisjoint(used_names)
```

- [ ] **Step 2: Run the regression test to verify it fails.**

```bash
pytest tests/unit/test_rclone_manager.py::test_rclone_scan_persistence_does_not_import_raw_db_helpers -v
```

Expected: FAIL while `_legacy.py` still imports or uses the forbidden names.

- [ ] **Step 3: Replace the `run_rclone_manager` scan DB block.**

In `javdb/integrations/rclone/manager/_legacy.py`, add imports near the other
module imports:

```python
from javdb.storage.repos.operations_repo import OperationsRepo
from javdb.storage.repos.session_lifecycle_repo import SessionLifecycleRepo
```

Then replace the raw DB import/use block in `run_rclone_manager` with:

```python
        session_repo = SessionLifecycleRepo()
        operations_repo = OperationsRepo()

        session_repo.init_storage()
        staging_sid = session_repo.get_active_session_id()
        created_local_session = staging_sid is None
        if created_local_session:
            staging_sid = session_repo.create_report_session(
                report_type="rclone_inventory",
                report_date=datetime.now().strftime("%Y%m%d"),
                csv_filename=RCLONE_INVENTORY_CSV,
            )
        try:
            operations_repo.open_rclone_staging(staging_sid)
            total_rows, error_count = scan_inventory(
                remote_name,
                root_folder,
                row_callback=lambda rows: operations_repo.append_rclone_staging(
                    rows,
                    session_id=staging_sid,
                ),
            )
            if error_count == 0:
                operations_repo.swap_rclone_inventory(staging_sid)
                if created_local_session:
                    session_repo.mark_session_committed(staging_sid)
            else:
                operations_repo.drop_rclone_staging(staging_sid)
                if created_local_session:
                    session_repo.mark_session_failed(
                        staging_sid,
                        reason="rclone_scan_partial",
                    )
        except Exception:
            operations_repo.drop_rclone_staging(staging_sid)
            if created_local_session:
                session_repo.mark_session_failed(
                    staging_sid,
                    reason="rclone_scan_error",
                )
            raise
```

- [ ] **Step 4: Replace the CLI scan DB block.**

In `run_manager_from_options`, replace the raw DB import/use block under
`if _use_sqlite():` with the same repo instances. Keep the existing control
flow, logging, retry loop, CSV temp-file cleanup, year-filter merge behavior,
and post-swap commit retry behavior. The key substitutions are:

```python
session_repo = SessionLifecycleRepo()
operations_repo = OperationsRepo()
session_repo.init_storage()
_staging_session_id = session_repo.get_active_session_id()
```

```python
_staging_session_id = session_repo.create_report_session(
    report_type="rclone_inventory",
    report_date=datetime.now().strftime("%Y%m%d"),
    csv_filename=os.path.basename(output_path),
)
```

```python
operations_repo.open_rclone_staging(_staging_session_id)
operations_repo.append_rclone_staging(rows, session_id=_staging_session_id)
operations_repo.merge_rclone_inventory_from_stage(_staging_session_id, year_filter)
operations_repo.swap_rclone_inventory(_staging_session_id)
operations_repo.drop_rclone_staging(_staging_session_id)
session_repo.mark_session_failed(_staging_session_id, reason="rclone_scan_error")
session_repo.mark_session_committed(_staging_session_id)
```

- [ ] **Step 5: Update monkeypatch helpers in rclone tests.**

Replace `_patch_rclone_db_mocks` with fake Repo factories:

```python
def _patch_rclone_repo_mocks(monkeypatch, rm, order, overrides=None):
    overrides = overrides or {}

    class FakeSessionLifecycleRepo:
        def init_storage(self):
            order.append("init_db")

        def get_active_session_id(self):
            return overrides.get("get_active_session_id", lambda: None)()

        def create_report_session(self, **_kwargs):
            return overrides.get(
                "create_report_session",
                lambda **_kw: order.append("create_session") or 123,
            )(**_kwargs)

        def mark_session_committed(self, sid):
            return overrides.get(
                "mark_session_committed",
                lambda _sid: order.append(("mark_committed", _sid)) or 1,
            )(sid)

        def mark_session_failed(self, sid, *, reason=None):
            return overrides.get(
                "mark_session_failed",
                lambda _sid, **_kw: order.append(("mark_failed", _sid)) or 1,
            )(sid, reason=reason)

    class FakeOperationsRepo:
        def open_rclone_staging(self, sid):
            return overrides.get(
                "open_rclone_staging",
                lambda _sid: order.append(("open_staging", _sid)),
            )(sid)

        def append_rclone_staging(self, rows, session_id):
            return overrides.get(
                "append_rclone_staging",
                lambda _rows, session_id: order.append(("append_staging", session_id)),
            )(rows, session_id=session_id)

        def swap_rclone_inventory(self, session_id):
            return overrides.get(
                "swap_rclone_inventory",
                lambda sid: order.append(("swap_inventory", sid)) or 1,
            )(session_id)

        def merge_rclone_inventory_from_stage(self, session_id, years):
            return overrides.get(
                "merge_rclone_inventory_from_stage",
                lambda sid, yrs: order.append(("merge_inventory", sid, yrs)) or 1,
            )(session_id, years)

        def drop_rclone_staging(self, session_id):
            return overrides.get(
                "drop_rclone_staging",
                lambda sid: order.append(("drop_staging", sid)),
            )(session_id)

    monkeypatch.setattr(rm, "SessionLifecycleRepo", FakeSessionLifecycleRepo)
    monkeypatch.setattr(rm, "OperationsRepo", FakeOperationsRepo)
```

- [ ] **Step 6: Run rclone manager tests.**

```bash
pytest tests/unit/test_rclone_manager.py -v
```

Expected: PASS.

---

## Task 7: Update Tests And Docs

**Files:**
- Modify: `tests/unit/test_rclone_manager.py`
- Modify: `apps/cli/rclone/README.md`
- Modify: `javdb/integrations/rclone/README.md`

- [ ] **Step 1: Add service contract tests.**

Add this test:

```python
from javdb.integrations.rclone.manager.options import RcloneManagerOptions
from javdb.integrations.rclone.manager.result import RcloneManagerResult
from javdb.integrations.rclone.manager.service import run_manager


def test_run_manager_wraps_legacy_exit_code(monkeypatch):
    from javdb.integrations.rclone.manager import _legacy

    monkeypatch.setattr(_legacy, "run_manager_from_options", lambda _options: 7)

    result = run_manager(RcloneManagerOptions(report=True))

    assert result == RcloneManagerResult(exit_code=7)
```

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

## Task 8: Verify Phase 4

- [ ] **Step 1: Run focused tests.**

```bash
pytest tests/unit/test_rclone_manager_options.py -v
pytest tests/unit/test_session_lifecycle_repo.py -v
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

- [ ] **Step 3: Verify rclone scan persistence no longer calls raw DB helpers.**

```bash
rg -n "from javdb\\.storage\\.db import|\\binit_db\\b|db_create_report_session|db_mark_session_committed|db_mark_session_failed|db_open_rclone_staging|db_append_rclone_staging|db_swap_rclone_inventory|db_merge_rclone_inventory_from_stage|db_drop_rclone_staging" javdb/integrations/rclone/manager
```

Expected: no results for production code in `javdb/integrations/rclone/manager`.
`session_repo.get_active_session_id()` is allowed; direct raw
`get_active_session_id` imports/calls from `javdb.storage.db` are not.

- [ ] **Step 4: Review workflows and docs.**

```bash
rg -n "apps\\.cli\\.rclone|rclone_manager|rclone\\.manager" .github/workflows README.md JAVDB_AutoSpider.wiki 2>/dev/null
```

Expected: workflow command invocations remain unchanged.

- [ ] **Step 5: Commit.**

```bash
git add javdb/integrations/rclone/manager \
        javdb/storage/repos/session_lifecycle_repo.py \
        javdb/storage/repos/README.md \
        apps/cli/rclone/manager.py \
        tests/unit/test_rclone_manager_options.py \
        tests/unit/test_session_lifecycle_repo.py \
        tests/unit/test_rclone_manager.py \
        apps/cli/rclone/README.md \
        javdb/integrations/rclone/README.md
git add -u javdb/integrations/rclone/manager.py
git commit -m "refactor(integrations): split rclone manager service"
```
