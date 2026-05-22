# IMP-ADR015-02: ADR-015 Phase 2 - qB Command Packages

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ADR-015 Phase 2 by migrating qB uploader and file filter from integration modules that also act as CLIs into command packages with typed service contracts and real `apps.cli.qb.*` adapters.

**Architecture:** `javdb.integrations.qb.client` remains the qB Web API primitive layer. `javdb.integrations.qb.uploader` and `javdb.integrations.qb.file_filter` become packages with `options.py`, `result.py`, and `service.py`; CLI parsing and exit mapping move to `apps.cli.qb.uploader` and `apps.cli.qb.file_filter`.

**Tech Stack:** Python 3.11, dataclasses, argparse, pytest, existing qB client/config helpers, `javdb.workflow` adapters from IMP-ADR015-01.

**Source spec:** [ADR-015](ADR-015-integrations-interface-boundary.md), D1-D7.

**Non-negotiable:** Preserve qB uploader and file-filter flags, defaults, proxy behavior, CSV resolution behavior, duplicate handling, completed-torrent cleanup behavior, stats persistence, git side effects, streaming logs, and exit codes.

---

## Files

| Path | Responsibility |
|---|---|
| `javdb/integrations/qb/uploader.py` | Move into package during migration; no final CLI surface remains. |
| `javdb/integrations/qb/uploader/__init__.py` | Public service package exports. |
| `javdb/integrations/qb/uploader/options.py` | `QbUploaderOptions` dataclass. |
| `javdb/integrations/qb/uploader/result.py` | `QbUploaderResult` dataclass and exit decision helpers. |
| `javdb/integrations/qb/uploader/service.py` | Upload orchestration service. |
| `javdb/integrations/qb/file_filter.py` | Move into package during migration; no final CLI surface remains. |
| `javdb/integrations/qb/file_filter/__init__.py` | Public service package exports. |
| `javdb/integrations/qb/file_filter/options.py` | `QbFileFilterOptions` dataclass. |
| `javdb/integrations/qb/file_filter/result.py` | `QbFileFilterResult` dataclass and exit decision helpers. |
| `javdb/integrations/qb/file_filter/service.py` | File-filter orchestration service. |
| `apps/cli/qb/uploader.py` | Real CLI parser and adapter for qB uploader. |
| `apps/cli/qb/file_filter.py` | Real CLI parser and adapter for qB file filter. |
| `tests/architecture/test_integrations_interface_boundary.py` | Remove qB from allowlists. |
| `tests/unit/test_qb_uploader_options.py` | New qB uploader contract tests. |
| `tests/unit/test_qb_file_filter_options.py` | New qB file-filter contract tests. |
| `tests/unit/test_qb_uploader.py` | Update imports and monkeypatch targets. |
| `tests/unit/test_qb_file_filter.py` | Update imports and monkeypatch targets. |
| `apps/cli/qb/README.md` | Remove alias wording for qB commands. |
| `javdb/integrations/qb/README.md` | Document command packages and service boundary. |

---

## Task 1: Move qB Modules Into Packages

**Files:**
- Move: `javdb/integrations/qb/uploader.py`
- Move: `javdb/integrations/qb/file_filter.py`
- Create: package files under `javdb/integrations/qb/uploader/`
- Create: package files under `javdb/integrations/qb/file_filter/`

- [ ] **Step 1: Move current implementation files to temporary package internals.**

Run:

```bash
git mv javdb/integrations/qb/uploader.py javdb/integrations/qb/uploader_legacy_tmp.py
mkdir -p javdb/integrations/qb/uploader
git mv javdb/integrations/qb/uploader_legacy_tmp.py javdb/integrations/qb/uploader/_legacy.py
git mv javdb/integrations/qb/file_filter.py javdb/integrations/qb/file_filter_legacy_tmp.py
mkdir -p javdb/integrations/qb/file_filter
git mv javdb/integrations/qb/file_filter_legacy_tmp.py javdb/integrations/qb/file_filter/_legacy.py
```

- [ ] **Step 2: Add package `__init__.py` files.**

Create `javdb/integrations/qb/uploader/__init__.py`:

```python
"""qB uploader service package."""

from javdb.integrations.qb.uploader.options import QbUploaderOptions
from javdb.integrations.qb.uploader.result import QbUploaderResult
from javdb.integrations.qb.uploader.service import run_uploader

__all__ = ["QbUploaderOptions", "QbUploaderResult", "run_uploader"]
```

Create `javdb/integrations/qb/file_filter/__init__.py`:

```python
"""qB file-filter service package."""

from javdb.integrations.qb.file_filter.options import QbFileFilterOptions
from javdb.integrations.qb.file_filter.result import QbFileFilterResult
from javdb.integrations.qb.file_filter.service import run_file_filter

__all__ = ["QbFileFilterOptions", "QbFileFilterResult", "run_file_filter"]
```

---

## Task 2: Add qB Typed Contracts

**Files:**
- Create: `javdb/integrations/qb/uploader/options.py`
- Create: `javdb/integrations/qb/uploader/result.py`
- Create: `javdb/integrations/qb/file_filter/options.py`
- Create: `javdb/integrations/qb/file_filter/result.py`
- Create: `tests/unit/test_qb_uploader_options.py`
- Create: `tests/unit/test_qb_file_filter_options.py`

- [ ] **Step 1: Add uploader contract tests.**

Create `tests/unit/test_qb_uploader_options.py`:

```python
from __future__ import annotations

from javdb.integrations.qb.uploader.options import QbUploaderOptions
from javdb.integrations.qb.uploader.result import QbUploaderResult


def test_qb_uploader_options_defaults():
    options = QbUploaderOptions()

    assert options.mode == "daily"
    assert options.input_file is None
    assert options.proxy_override is None
    assert options.from_pipeline is False
    assert options.category is None
    assert options.session_id is None


def test_qb_uploader_result_exit_code_for_all_failed_attempts():
    result = QbUploaderResult(
        total_torrents=3,
        duplicate_count=0,
        attempted=3,
        successfully_added=0,
        failed_count=3,
    )

    assert result.exit_code == 1


def test_qb_uploader_result_exit_code_for_no_work():
    result = QbUploaderResult(total_torrents=0, duplicate_count=0, attempted=0)

    assert result.exit_code == 0
```

- [ ] **Step 2: Add file-filter contract tests.**

Create `tests/unit/test_qb_file_filter_options.py`:

```python
from __future__ import annotations

from javdb.integrations.qb.file_filter.options import QbFileFilterOptions
from javdb.integrations.qb.file_filter.result import QbFileFilterResult


def test_qb_file_filter_options_defaults():
    options = QbFileFilterOptions(min_size_mb=100.0)

    assert options.days == 2
    assert options.proxy_override is None
    assert options.dry_run is False
    assert options.category is None
    assert options.categories is None
    assert options.delete_local_files is False


def test_qb_file_filter_result_exit_code_for_all_errors():
    result = QbFileFilterResult(torrents_processed=0, errors=2)

    assert result.exit_code == 1


def test_qb_file_filter_result_exit_code_for_pending_metadata():
    result = QbFileFilterResult(torrents_processed=0, pending_metadata=5, errors=0)

    assert result.exit_code == 0
```

- [ ] **Step 3: Implement option/result dataclasses.**

Create `javdb/integrations/qb/uploader/options.py`:

```python
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class QbUploaderOptions:
    mode: Literal["adhoc", "daily"] = "daily"
    input_file: str | None = None
    proxy_override: bool | None = None
    from_pipeline: bool = False
    category: str | None = None
    session_id: str | None = None
```

Create `javdb/integrations/qb/uploader/result.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QbUploaderResult:
    total_torrents: int = 0
    duplicate_count: int = 0
    attempted: int = 0
    successfully_added: int = 0
    failed_count: int = 0
    hacked_subtitle_count: int = 0
    hacked_no_subtitle_count: int = 0
    subtitle_count: int = 0
    no_subtitle_count: int = 0
    csv_path: str | None = None
    csv_ok: bool = True
    error_reason: str | None = None

    @property
    def exit_code(self) -> int:
        if self.error_reason:
            return 1
        if not self.csv_ok:
            return 1
        if self.attempted > 0 and self.successfully_added == 0:
            return 1
        return 0
```

Create `javdb/integrations/qb/file_filter/options.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QbFileFilterOptions:
    min_size_mb: float
    days: int = 2
    proxy_override: bool | None = None
    dry_run: bool = False
    category: str | None = None
    categories: Sequence[str] | None = None
    delete_local_files: bool = False
```

Create `javdb/integrations/qb/file_filter/result.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class QbFileFilterResult:
    torrents_processed: int = 0
    torrents_with_filtered_files: int = 0
    files_filtered: int = 0
    files_kept: int = 0
    size_saved: int = 0
    local_files_deleted: int = 0
    local_size_deleted: int = 0
    pending_metadata: int = 0
    errors: int = 0
    details: list[dict[str, object]] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        if self.errors > 0 and self.torrents_processed == 0:
            return 1
        return 0
```

- [ ] **Step 4: Run contract tests.**

```bash
pytest tests/unit/test_qb_uploader_options.py tests/unit/test_qb_file_filter_options.py -v
```

Expected: PASS.

---

## Task 3: Move CLI Parsing Into `apps.cli.qb`

**Files:**
- Modify: `apps/cli/qb/uploader.py`
- Modify: `apps/cli/qb/file_filter.py`

- [ ] **Step 1: Replace uploader alias with real CLI adapter.**

Replace `apps/cli/qb/uploader.py` with:

```python
from __future__ import annotations

import argparse

from javdb.integrations.qb.uploader.options import QbUploaderOptions
from javdb.integrations.qb.uploader.service import run_uploader
from javdb.proxy.policy import add_proxy_arguments, resolve_proxy_override


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="qBittorrent Uploader")
    parser.add_argument("--mode", choices=["adhoc", "daily"], default="daily", help="Upload mode: adhoc (Ad Hoc folder) or daily (Daily Report folder)")
    parser.add_argument("--input-file", type=str, help="Specify input CSV file name (overrides default date-based name)")
    add_proxy_arguments(
        parser,
        use_help="Force-enable proxy for qBittorrent API requests",
        no_help="Force-disable proxy for qBittorrent API requests",
    )
    parser.add_argument("--from-pipeline", action="store_true", help="Running from pipeline.py - use GIT_USERNAME for commits")
    parser.add_argument("--category", type=str, help="Override qBittorrent category (defaults to TORRENT_CATEGORY_ADHOC for adhoc mode, TORRENT_CATEGORY for daily mode)")
    parser.add_argument("--session-id", type=str, default=None, help="Report session ID for saving uploader stats to SQLite")
    return parser.parse_args(argv)


def options_from_args(args: argparse.Namespace) -> QbUploaderOptions:
    return QbUploaderOptions(
        mode=args.mode,
        input_file=args.input_file,
        proxy_override=resolve_proxy_override(args.use_proxy, args.no_proxy),
        from_pipeline=args.from_pipeline,
        category=args.category,
        session_id=args.session_id,
    )


def main(argv: list[str] | None = None) -> int:
    return run_uploader(options_from_args(parse_args(argv))).exit_code


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Replace file-filter alias with real CLI adapter.**

Replace `apps/cli/qb/file_filter.py` with:

```python
from __future__ import annotations

import argparse
import json

from javdb.integrations.qb.config import QB_FILE_FILTER_MIN_SIZE_MB
from javdb.integrations.qb.file_filter.options import QbFileFilterOptions
from javdb.integrations.qb.file_filter.service import run_file_filter
from javdb.proxy.policy import add_proxy_arguments, resolve_proxy_override


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Filter out small files from recently added torrents in qBittorrent"
    )
    parser.add_argument("--min-size", type=float, default=QB_FILE_FILTER_MIN_SIZE_MB, help=f"Minimum file size in MB (files smaller than this will be skipped). Default: {QB_FILE_FILTER_MIN_SIZE_MB}MB")
    parser.add_argument("--days", type=int, default=2, help="Number of days to look back for recently added torrents (default: 2 for today and yesterday)")
    add_proxy_arguments(
        parser,
        use_help="Force-enable proxy for qBittorrent API requests",
        no_help="Force-disable proxy for qBittorrent API requests",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would be filtered without actually making changes")
    parser.add_argument("--category", type=str, default=None, help="Filter only torrents in this category (default: all categories). Deprecated: use --categories instead.")
    parser.add_argument("--categories", type=str, default=None, help="JSON array of categories to filter (e.g., '[\"Ad Hoc\", \"Daily Ingestion\"]'). If specified, overrides --category.")
    parser.add_argument("--delete-local-files", action="store_true", help="Delete local files that have already been downloaded but are below the size threshold")
    return parser.parse_args(argv)


def _parse_categories(raw_categories: str | None) -> list[str] | None:
    if not raw_categories:
        return None
    categories = json.loads(raw_categories)
    if not isinstance(categories, list):
        raise argparse.ArgumentTypeError("--categories must be a JSON array")
    return [str(category) for category in categories if category]


def options_from_args(args: argparse.Namespace) -> QbFileFilterOptions:
    return QbFileFilterOptions(
        min_size_mb=args.min_size,
        days=args.days,
        proxy_override=resolve_proxy_override(args.use_proxy, args.no_proxy),
        dry_run=args.dry_run,
        category=args.category,
        categories=_parse_categories(args.categories),
        delete_local_files=args.delete_local_files,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        options = options_from_args(parse_args(argv))
    except (json.JSONDecodeError, argparse.ArgumentTypeError) as exc:
        raise SystemExit(str(exc)) from exc
    return run_file_filter(options).exit_code


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Add CLI parser tests.**

Add parser tests to `tests/unit/test_qb_uploader_options.py` and `tests/unit/test_qb_file_filter_options.py` that import from `apps.cli.qb.uploader` and `apps.cli.qb.file_filter`, then assert the options match the old defaults and flags.

Run:

```bash
pytest tests/unit/test_qb_uploader_options.py tests/unit/test_qb_file_filter_options.py -v
```

Expected: PASS.

---

## Task 4: Extract qB Services

**Files:**
- Create: `javdb/integrations/qb/uploader/service.py`
- Create: `javdb/integrations/qb/file_filter/service.py`
- Modify: `javdb/integrations/qb/uploader/_legacy.py`
- Modify: `javdb/integrations/qb/file_filter/_legacy.py`

- [ ] **Step 1: Create service functions that preserve old flow.**

Create `javdb/integrations/qb/uploader/service.py` by moving the body of `_legacy.main()` into `run_uploader(options: QbUploaderOptions) -> QbUploaderResult`.

Required conversions:

| Old `args` access | New source |
|---|---|
| `args.mode` | `options.mode` |
| `args.input_file` | `options.input_file` |
| `resolve_proxy_override(args.use_proxy, args.no_proxy)` | `options.proxy_override` |
| `args.from_pipeline` | `options.from_pipeline` |
| `args.category` | `options.category` |
| `args.session_id` | `options.session_id` |

Use `javdb.workflow.artifact_inputs.resolve_qb_uploader_csv_path` and
`javdb.workflow.artifact_inputs.read_torrent_csv` for CSV resolution and reading.

Use `javdb.workflow.stats_sink.save_uploader_stats` for uploader stats.

Use `javdb.workflow.git_side_effects.commit_workflow_outputs` for git
commit/push.

- [ ] **Step 2: Create file-filter service.**

Create `javdb/integrations/qb/file_filter/service.py` by moving the body of
`_legacy.main()` into `run_file_filter(options: QbFileFilterOptions) -> QbFileFilterResult`.

Required conversions:

| Old `args` access | New source |
|---|---|
| `args.min_size` | `options.min_size_mb` |
| `args.days` | `options.days` |
| `resolve_proxy_override(args.use_proxy, args.no_proxy)` | `options.proxy_override` |
| `args.dry_run` | `options.dry_run` |
| `args.category` | `options.category` |
| parsed `args.categories` | `options.categories` |
| `args.delete_local_files` | `options.delete_local_files` |

Return `QbFileFilterResult` using the existing `filter_small_files` stats
dictionary.

- [ ] **Step 3: Remove CLI surface from qB internals.**

Delete from qB package internals:

- `argparse` imports;
- `parse_arguments()` functions;
- `main()` functions;
- `if __name__ == "__main__"` blocks;
- `sys.exit()` calls.

Replace former `sys.exit(1)` branches with `QbUploaderResult(error_reason=<specific message>)`
or `QbFileFilterResult(errors=1)` as appropriate, preserving the CLI return
code through `result.exit_code`.

---

## Task 5: Update Tests, Docs, And Guards

**Files:**
- Modify: `tests/architecture/test_integrations_interface_boundary.py`
- Modify: `tests/unit/test_qb_uploader.py`
- Modify: `tests/unit/test_qb_file_filter.py`
- Modify: `apps/cli/qb/README.md`
- Modify: `javdb/integrations/qb/README.md`

- [ ] **Step 1: Update qB test imports and monkeypatch targets.**

Replace `apps.cli.qb.uploader` monkeypatch targets that patch domain behavior with:

```text
javdb.integrations.qb.uploader.service
```

Replace `apps.cli.qb.file_filter` monkeypatch targets that patch domain behavior with:

```text
javdb.integrations.qb.file_filter.service
```

Keep parser/exit-code tests on `apps.cli.qb.*`.

- [ ] **Step 2: Remove qB allowlist entries.**

Delete from `INTEGRATION_CLI_SURFACE_ALLOWLIST`:

```python
"javdb/integrations/qb/uploader.py"
"javdb/integrations/qb/file_filter.py"
```

Delete from `APPS_CLI_INTEGRATION_ALIAS_ALLOWLIST`:

```python
"apps/cli/qb/uploader.py"
"apps/cli/qb/file_filter.py"
```

If the guard now reports package paths, ensure no qB package file contains CLI
surface.

- [ ] **Step 3: Update qB READMEs.**

`apps/cli/qb/README.md` must no longer say the qB CLI aliases integration
modules. It should say `apps.cli.qb.uploader` and `apps.cli.qb.file_filter` are
real CLI adapters.

`javdb/integrations/qb/README.md` must describe:

- `client.py` as qB Web API primitives;
- `config.py` as qB connection config;
- `uploader/` as uploader service package;
- `file_filter/` as file-filter service package.

---

## Task 6: Verify Phase 2

- [ ] **Step 1: Run focused tests.**

```bash
pytest tests/architecture/test_integrations_interface_boundary.py -v
pytest tests/unit/test_qb_uploader_options.py -v
pytest tests/unit/test_qb_file_filter_options.py -v
pytest tests/unit/test_qb_client.py -v
pytest tests/unit/test_qb_uploader.py -v
pytest tests/unit/test_qb_file_filter.py -v
```

Expected: PASS.

- [ ] **Step 2: Run import and CLI-surface searches.**

```bash
rg -n "argparse|parse_arguments|def main|sys\\.exit|__main__" javdb/integrations/qb/uploader javdb/integrations/qb/file_filter
rg -n "sys\\.modules\\[__name__\\]" apps/cli/qb
```

Expected: no results.

- [ ] **Step 3: Review workflows and docs.**

Review:

```bash
rg -n "apps\\.cli\\.qb|qb_uploader|qb_file_filter" .github/workflows README.md JAVDB_AutoSpider.wiki 2>/dev/null
```

Expected: command invocations remain unchanged.

- [ ] **Step 4: Commit.**

```bash
git add javdb/integrations/qb/uploader \
        javdb/integrations/qb/file_filter \
        apps/cli/qb/uploader.py \
        apps/cli/qb/file_filter.py \
        tests/architecture/test_integrations_interface_boundary.py \
        tests/unit/test_qb_uploader_options.py \
        tests/unit/test_qb_file_filter_options.py \
        tests/unit/test_qb_uploader.py \
        tests/unit/test_qb_file_filter.py \
        apps/cli/qb/README.md \
        javdb/integrations/qb/README.md
git add -u javdb/integrations/qb/uploader.py javdb/integrations/qb/file_filter.py
git commit -m "refactor(integrations): split qB command services"
```
