# IMP-ADR015-03: ADR-015 Phase 3 - PikPak Bridge Package

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ADR-015 Phase 3 by migrating the PikPak bridge into a typed command package and replacing the `apps.cli.pikpak.bridge` alias with a real CLI adapter.

**Architecture:** `javdb.integrations.pikpak.bridge` becomes a package with `options.py`, `result.py`, and `service.py`. The service owns transfer orchestration; `apps.cli.pikpak.bridge` owns argument parsing, proxy override resolution, and exit-code mapping.

**Tech Stack:** Python 3.11, dataclasses, argparse, pytest, existing PikPak/qB helpers, `javdb.workflow.stats_sink`, `javdb.workflow.git_side_effects`.

**Source spec:** [ADR-015](ADR-015-integrations-interface-boundary.md), D1-D8.

**Non-negotiable:** Preserve `--days`, `--dry-run`, `--individual`, proxy flags, `--from-pipeline`, `--session-id`, `--root-folder`, batch/individual transfer behavior, primary/adhoc qB scanning, PikPak history writes, stats persistence, git side effects, proxy behavior, streaming logs, and current exit behavior.

---

## Files

| Path | Responsibility |
|---|---|
| `javdb/integrations/pikpak/bridge.py` | Move into package during migration; no final CLI surface remains. |
| `javdb/integrations/pikpak/bridge/__init__.py` | Public service package exports. |
| `javdb/integrations/pikpak/bridge/options.py` | `PikPakBridgeOptions` dataclass. |
| `javdb/integrations/pikpak/bridge/result.py` | `PikPakBridgeResult` dataclass. |
| `javdb/integrations/pikpak/bridge/service.py` | Transfer orchestration service. |
| `apps/cli/pikpak/bridge.py` | Real CLI parser and adapter. |
| `tests/architecture/test_integrations_interface_boundary.py` | Remove PikPak allowlist entries. |
| `tests/unit/test_pikpak_bridge_options.py` | New options/result/parser tests. |
| `tests/unit/test_pikpak_bridge.py` | Update imports and monkeypatch targets. |
| `apps/cli/pikpak/README.md` | Remove alias wording. |
| `javdb/integrations/pikpak/README.md` | Document service package. |

---

## Task 1: Move Bridge Module Into A Package

**Files:**
- Move: `javdb/integrations/pikpak/bridge.py`
- Create: `javdb/integrations/pikpak/bridge/__init__.py`

- [ ] **Step 1: Move the existing bridge implementation.**

Run:

```bash
git mv javdb/integrations/pikpak/bridge.py javdb/integrations/pikpak/bridge_legacy_tmp.py
mkdir -p javdb/integrations/pikpak/bridge
git mv javdb/integrations/pikpak/bridge_legacy_tmp.py javdb/integrations/pikpak/bridge/_legacy.py
```

- [ ] **Step 2: Add package exports.**

Create `javdb/integrations/pikpak/bridge/__init__.py`:

> **Implementation note (deviation):** `pikpak_bridge(days, dry_run, ...)` is the
> existing programmatic entry point consumed by the REST layer
> (`apps/api/routers/operations.py` imports `pikpak_bridge` and
> `tests/unit/test_operations_endpoints.py` patches
> `javdb.integrations.pikpak.bridge.pikpak_bridge`). It MUST remain importable at
> the package level with its current signature and the session set/clear wrapper.
> `run_bridge(options)` is the new CLI service and is a thin wrapper that calls
> `pikpak_bridge(...)` — it does NOT replace it. The package therefore re-exports
> both. Domain helpers that existing tests import from the package
> (`_build_pikpak_target_path`, `PIKPAK_ROOT_FOLDER_DEFAULT`,
> `process_pikpak_batch`, …) either stay re-exported here or the tests are updated
> to import them from `.service` (see Task 5).

```python
"""PikPak bridge service package."""

from javdb.integrations.pikpak.bridge.options import PikPakBridgeOptions
from javdb.integrations.pikpak.bridge.result import PikPakBridgeResult
from javdb.integrations.pikpak.bridge.service import pikpak_bridge, run_bridge

__all__ = ["PikPakBridgeOptions", "PikPakBridgeResult", "pikpak_bridge", "run_bridge"]
```

---

## Task 2: Add PikPak Typed Contract

**Files:**
- Create: `javdb/integrations/pikpak/bridge/options.py`
- Create: `javdb/integrations/pikpak/bridge/result.py`
- Create: `tests/unit/test_pikpak_bridge_options.py`

- [ ] **Step 1: Write contract tests.**

Create `tests/unit/test_pikpak_bridge_options.py`:

```python
from __future__ import annotations

from apps.cli.pikpak.bridge import options_from_args, parse_args
from javdb.integrations.pikpak.bridge.options import PikPakBridgeOptions
from javdb.integrations.pikpak.bridge.result import PikPakBridgeResult


def test_pikpak_options_defaults():
    options = PikPakBridgeOptions()

    assert options.days == 3
    assert options.dry_run is False
    assert options.batch_mode is True
    assert options.proxy_override is None
    assert options.from_pipeline is False
    assert options.session_id is None
    assert options.root_folder is None


def test_pikpak_cli_individual_turns_off_batch_mode():
    options = options_from_args(parse_args(["--individual", "--days", "5"]))

    assert options.days == 5
    assert options.batch_mode is False


def test_pikpak_result_default_exit_code_matches_current_cli_behavior():
    result = PikPakBridgeResult(total_torrents=4, filtered_old=4, failed_count=4)

    assert result.exit_code == 0
```

- [ ] **Step 2: Implement options/result.**

Create `javdb/integrations/pikpak/bridge/options.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PikPakBridgeOptions:
    days: int = 3
    dry_run: bool = False
    batch_mode: bool = True
    proxy_override: bool | None = None
    from_pipeline: bool = False
    session_id: str | None = None
    root_folder: str | None = None
```

Create `javdb/integrations/pikpak/bridge/result.py`:

```python
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PikPakBridgeResult:
    total_torrents: int = 0
    filtered_old: int = 0
    successful_count: int = 0
    failed_count: int = 0
    uploaded_count: int = 0
    delete_failed_count: int = 0
    dry_run: bool = False
    errors: Sequence[str] = field(default_factory=tuple)

    @property
    def exit_code(self) -> int:
        return 0
```

---

## Task 3: Replace CLI Alias With Real Adapter

**Files:**
- Modify: `apps/cli/pikpak/bridge.py`

- [ ] **Step 1: Replace `apps/cli/pikpak/bridge.py`.**

Use:

```python
from __future__ import annotations

import argparse

from javdb.integrations.pikpak.bridge.options import PikPakBridgeOptions
from javdb.integrations.pikpak.bridge.service import run_bridge
from javdb.proxy.policy import add_proxy_arguments, resolve_proxy_override


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PikPak Bridge - Transfer torrents from qBittorrent to PikPak")
    parser.add_argument("--days", type=int, default=3, help="Filter torrents older than N days")
    parser.add_argument("--dry-run", action="store_true", help="Test mode: no delete or PikPak add")
    parser.add_argument("--individual", action="store_true", help="Process torrents individually instead of batch mode (default: batch mode)")
    add_proxy_arguments(
        parser,
        use_help="Force-enable proxy for PikPak and qBittorrent requests in this command",
        no_help="Force-disable proxy for PikPak and qBittorrent requests in this command",
    )
    parser.add_argument("--from-pipeline", action="store_true", help="Running from pipeline.py - use GIT_USERNAME for commits")
    parser.add_argument("--session-id", type=str, default=None, help="Report session ID for saving pikpak stats to SQLite")
    parser.add_argument(
        "--root-folder",
        default=None,
        help="PikPak root folder for uploads. Each torrent is placed under {root}/{qB category}. Defaults to PIKPAK_ROOT_FOLDER from config (/Javdb_AutoSpider).",
    )
    return parser.parse_args(argv)


def options_from_args(args: argparse.Namespace) -> PikPakBridgeOptions:
    return PikPakBridgeOptions(
        days=args.days,
        dry_run=args.dry_run,
        batch_mode=not args.individual,
        proxy_override=resolve_proxy_override(args.use_proxy, args.no_proxy),
        from_pipeline=args.from_pipeline,
        session_id=args.session_id,
        root_folder=args.root_folder,
    )


def main(argv: list[str] | None = None) -> int:
    # Preserve the legacy CLI lifecycle: ensure the DB connection is closed at
    # process exit (the former bridge.main() registered this).
    import atexit

    from javdb.storage.db import close_db

    atexit.register(close_db)
    return run_bridge(options_from_args(parse_args(argv))).exit_code


if __name__ == "__main__":
    raise SystemExit(main())
```

---

## Task 4: Extract Bridge Service

**Files:**
- Create: `javdb/integrations/pikpak/bridge/service.py`
- Modify: `javdb/integrations/pikpak/bridge/_legacy.py`

- [ ] **Step 1: Create `run_bridge`.**

Create `javdb/integrations/pikpak/bridge/service.py` to hold the full former
bridge body — `pikpak_bridge`, `_pikpak_bridge_impl`, and all domain helpers —
**unchanged in signature/behavior**, plus a NEW thin wrapper
`run_bridge(options: PikPakBridgeOptions) -> PikPakBridgeResult`.

> **Deviation from the original wording:** do NOT collapse `pikpak_bridge` /
> `_pikpak_bridge_impl` into `run_bridge`. `pikpak_bridge` is the REST
> programmatic API and must keep its exact signature + the session set/clear
> wrapper (covered by `tests/unit/test_pikpak_bridge.py::test_clears_active_session_id_after_impl_returns`
> and `tests/unit/test_operations_endpoints.py`). `run_bridge` simply calls
> `pikpak_bridge(...)` and returns a `PikPakBridgeResult` (whose `exit_code` is
> always `0`, matching the current always-exit-0 CLI behavior — `main()` never
> called `sys.exit`).

`run_bridge` maps options → the existing `pikpak_bridge` call:

| `pikpak_bridge` parameter | New source |
|---|---|
| `days` | `options.days` |
| `dry_run` | `options.dry_run` |
| `batch_mode` | `options.batch_mode` |
| `use_proxy` | `options.proxy_override` |
| `from_pipeline` | `options.from_pipeline` |
| `session_id` | `options.session_id` |
| `root_folder` | `options.root_folder` |

Route the side effects currently embedded in `_pikpak_bridge_impl` through the
Phase 1 workflow adapters, **keeping the existing `not dry_run` guards at the
call sites** (the adapters do NOT know about dry-run):

- Replace the inline `db_save_pikpak_stats(session_id, {...})` block with
  `javdb.workflow.stats_sink.save_pikpak_stats(session_id, PikPakStats(...))`,
  preserving the `if session_id and not dry_run:` guard. Field mapping:
  `threshold_days=days`, `total_torrents=len(torrents)`,
  `filtered_old=len(old_torrents)`, `successful_count`, `failed_count`,
  `uploaded_count=successful_count + delete_failed_count`,
  `delete_failed_count`. Preserve the success log line naming the backend.
- Replace the inline `git_commit_and_push(...)` call with
  `javdb.workflow.git_side_effects.commit_workflow_outputs(GitCommitRequest(...))`,
  preserving the exact branching: `if not dry_run and has_git_credentials(...)`
  → log "Committing PikPak bridge results...", flush, commit with
  `files_to_add=['logs/', REPORTS_DIR]` and the same `commit_message`;
  `elif not dry_run:` → log the "Skipping git commit - no credentials" message.

- [ ] **Step 2: Remove bridge CLI surface.**

Delete from bridge package internals:

- `argparse` imports;
- command `main()` functions;
- `if __name__ == "__main__"` blocks.

The final bridge package may keep private helper functions such as path
normalization, history writing, and qB/PikPak transfer helpers.

---

## Task 5: Update Tests, Docs, And Guards

**Files:**
- Modify: `tests/architecture/test_integrations_interface_boundary.py`
- Modify: `tests/unit/test_pikpak_bridge.py`
- Modify: `apps/cli/pikpak/README.md`
- Modify: `javdb/integrations/pikpak/README.md`

- [ ] **Step 1: Update test import and monkeypatch targets.**

Use `apps.cli.pikpak.bridge` only for parser and CLI exit tests.

Use these package paths for domain behavior:

```text
javdb.integrations.pikpak.bridge.service
javdb.integrations.pikpak.bridge
```

- [ ] **Step 2: Remove PikPak allowlist entries.**

Delete:

```python
"javdb/integrations/pikpak/bridge.py"
"apps/cli/pikpak/bridge.py"
```

from the architecture allowlists.

- [ ] **Step 3: Update READMEs.**

`apps/cli/pikpak/README.md` must no longer describe `bridge.py` as an alias.

`javdb/integrations/pikpak/README.md` must describe `bridge/` as a service
package.

---

## Task 6: Verify Phase 3

- [ ] **Step 1: Run focused tests.**

```bash
pytest tests/architecture/test_integrations_interface_boundary.py -v
pytest tests/unit/test_pikpak_bridge_options.py -v
pytest tests/unit/test_pikpak_bridge.py -v
```

Expected: PASS.

- [ ] **Step 2: Run CLI-surface searches.**

```bash
rg -n "argparse|parse_arguments|def main|sys\\.exit|__main__" javdb/integrations/pikpak/bridge
rg -n "sys\\.modules\\[__name__\\]" apps/cli/pikpak
```

Expected: no results.

- [ ] **Step 3: Review workflows and docs.**

```bash
rg -n "apps\\.cli\\.pikpak|pikpak_bridge|pikpak\\.bridge" .github/workflows README.md JAVDB_AutoSpider.wiki 2>/dev/null
```

Expected: workflow command invocations remain unchanged.

- [ ] **Step 4: Commit.**

```bash
git add javdb/integrations/pikpak/bridge \
        apps/cli/pikpak/bridge.py \
        tests/architecture/test_integrations_interface_boundary.py \
        tests/unit/test_pikpak_bridge_options.py \
        tests/unit/test_pikpak_bridge.py \
        apps/cli/pikpak/README.md \
        javdb/integrations/pikpak/README.md
git add -u javdb/integrations/pikpak/bridge.py
git commit -m "refactor(integrations): split PikPak bridge service"
```
