# IMP-030: ADR-015 Phase 1 - Guard And Workflow Adapters

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ADR-015 Phase 1 by making the integrations/CLI boundary executable and introducing the first shared workflow-side-effect adapters.

**Architecture:** `apps.cli.*` remains the only command-line surface. Phase 1 adds architecture tests with allowlists for current violations, plus `javdb.workflow.artifact_inputs`, `javdb.workflow.stats_sink`, and `javdb.workflow.git_side_effects` as narrow workflow adapters that the domain migration phases consume.

**Tech Stack:** Python 3.11, dataclasses, pathlib, ast, pytest, existing git helper and storage stats APIs.

**Source spec:** [ADR-015](../adr/ADR-015-integrations-interface-boundary.md), D1-D6.

**Non-negotiable:** This phase must not change any workflow command, CLI flag, exit code, logging behavior, qB/PikPak/rclone/notify behavior, or production import path. The guards start with allowlists; they do not force domain migration in Phase 1.

---

## Files

| Path | Responsibility |
|---|---|
| `tests/architecture/test_integrations_interface_boundary.py` | New executable boundary rules with allowlists. |
| `javdb/workflow/__init__.py` | New workflow adapter namespace. |
| `javdb/workflow/artifact_inputs.py` | Shared artifact input resolution and CSV reading. |
| `javdb/workflow/stats_sink.py` | Shared stats persistence adapter for integration flows. |
| `javdb/workflow/git_side_effects.py` | Shared git commit/push side-effect adapter. |
| `tests/unit/test_workflow_artifact_inputs.py` | Unit tests for artifact input resolution and CSV parsing. |
| `tests/unit/test_workflow_stats_sink.py` | Unit tests for stats sink behavior. |
| `tests/unit/test_workflow_git_side_effects.py` | Unit tests for git side-effect behavior. |
| `apps/cli/README.md` | Document current alias wrappers as transitional under ADR-015. |
| `javdb/integrations/README.md` | Document integrations as service/client surface under ADR-015. |

---

## Task 1: Add Architecture Boundary Guard

**Files:**
- Create: `tests/architecture/test_integrations_interface_boundary.py`

- [ ] **Step 1: Write the guard test.**

Create `tests/architecture/test_integrations_interface_boundary.py`:

```python
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INTEGRATIONS_ROOT = ROOT / "javdb" / "integrations"
APPS_CLI_ROOT = ROOT / "apps" / "cli"

INTEGRATION_CLI_SURFACE_ALLOWLIST = {
    "javdb/integrations/qb/uploader.py": {
        "argparse_import",
        "parse_arguments",
        "main",
        "dunder_main",
    },
    "javdb/integrations/qb/file_filter.py": {
        "argparse_import",
        "parse_arguments",
        "main",
        "dunder_main",
    },
    "javdb/integrations/pikpak/bridge.py": {
        "argparse_import",
        "main",
        "dunder_main",
    },
    "javdb/integrations/rclone/manager.py": {
        "argparse_import",
        "parse_arguments",
        "main",
        "dunder_main",
        "argparse_namespace_annotation",
    },
    "javdb/integrations/notify/email.py": {
        "argparse_import",
        "parse_arguments",
        "main",
        "dunder_main",
    },
}

APPS_CLI_INTEGRATION_ALIAS_ALLOWLIST = {
    "apps/cli/qb/uploader.py",
    "apps/cli/qb/file_filter.py",
    "apps/cli/pikpak/bridge.py",
    "apps/cli/rclone/manager.py",
    "apps/cli/notify/email.py",
}


def _python_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _string_value(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _is_sys_exit_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "exit"
        and isinstance(func.value, ast.Name)
        and func.value.id == "sys"
    )


def _is_dunder_main_check(node: ast.AST) -> bool:
    if not isinstance(node, ast.If):
        return False
    test = node.test
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "__name__"
        and any(_string_value(comparator) == "__main__" for comparator in test.comparators)
    )


def _integration_cli_surface(tree: ast.AST) -> set[str]:
    surface: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "argparse" for alias in node.names):
                surface.add("argparse_import")
        elif isinstance(node, ast.ImportFrom):
            if node.module == "argparse":
                surface.add("argparse_import")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "parse_arguments":
                surface.add("parse_arguments")
            elif node.name == "main":
                surface.add("main")
        elif _is_sys_exit_call(node):
            surface.add("sys_exit")
        elif _is_dunder_main_check(node):
            surface.add("dunder_main")
        elif isinstance(node, ast.Attribute) and node.attr == "Namespace":
            value = node.value
            if isinstance(value, ast.Name) and value.id == "argparse":
                surface.add("argparse_namespace_annotation")
    return surface


def _imports_integration_module(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "import_module"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and node.args[0].value.startswith("javdb.integrations.")
            ):
                return True
    return False


def _assigns_to_sys_modules_dunder_name(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Subscript):
                continue
            if not isinstance(target.value, ast.Attribute):
                continue
            if target.value.attr != "modules":
                continue
            if not isinstance(target.value.value, ast.Name):
                continue
            if target.value.value.id != "sys":
                continue
            key = target.slice
            if isinstance(key, ast.Constant) and key.value == "__name__":
                return True
    return False


def test_integrations_do_not_add_untracked_cli_surface():
    offenders: list[str] = []

    for path in _python_files(INTEGRATIONS_ROOT):
        relpath = _rel(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        actual = _integration_cli_surface(tree)
        allowed = INTEGRATION_CLI_SURFACE_ALLOWLIST.get(relpath, set())
        unexpected = sorted(actual - allowed)
        if unexpected:
            offenders.append(f"{relpath}: {', '.join(unexpected)}")

    assert offenders == []


def test_apps_cli_does_not_add_untracked_integration_aliases():
    offenders: list[str] = []

    for path in _python_files(APPS_CLI_ROOT):
        relpath = _rel(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        is_integration_alias = (
            _imports_integration_module(tree)
            and _assigns_to_sys_modules_dunder_name(tree)
        )
        if is_integration_alias and relpath not in APPS_CLI_INTEGRATION_ALIAS_ALLOWLIST:
            offenders.append(relpath)

    assert offenders == []
```

- [ ] **Step 2: Run the architecture test.**

```bash
pytest tests/architecture/test_integrations_interface_boundary.py -v
```

Expected: PASS with the current allowlists.

---

## Task 2: Add Artifact Input Adapter

**Files:**
- Create: `javdb/workflow/__init__.py`
- Create: `javdb/workflow/artifact_inputs.py`
- Create: `tests/unit/test_workflow_artifact_inputs.py`

- [ ] **Step 1: Write artifact input tests.**

Create `tests/unit/test_workflow_artifact_inputs.py`:

```python
from __future__ import annotations

import csv
from pathlib import Path

from javdb.workflow.artifact_inputs import (
    CsvInputResolution,
    read_torrent_csv,
    resolve_qb_uploader_csv_path,
)


def test_resolve_qb_uploader_csv_path_uses_full_input_path(tmp_path):
    csv_path = tmp_path / "custom.csv"

    result = resolve_qb_uploader_csv_path(
        mode="daily",
        input_file=str(csv_path),
        daily_report_dir="reports/DailyReport",
        adhoc_dir="reports/AdHoc",
        dated_path_resolver=lambda root, name: f"{root}/dated/{name}",
        latest_daily_finder=lambda: "daily.csv",
        latest_adhoc_finder=lambda: "adhoc.csv",
    )

    assert result == CsvInputResolution(path=str(csv_path), source="explicit-path")


def test_resolve_qb_uploader_csv_path_builds_dated_daily_path():
    result = resolve_qb_uploader_csv_path(
        mode="daily",
        input_file="daily.csv",
        daily_report_dir="reports/DailyReport",
        adhoc_dir="reports/AdHoc",
        dated_path_resolver=lambda root, name: f"{root}/2026/05/{name}",
        latest_daily_finder=lambda: "unused-daily.csv",
        latest_adhoc_finder=lambda: "unused-adhoc.csv",
    )

    assert result == CsvInputResolution(
        path="reports/DailyReport/2026/05/daily.csv",
        source="explicit-name",
    )


def test_resolve_qb_uploader_csv_path_uses_latest_adhoc_when_no_input():
    result = resolve_qb_uploader_csv_path(
        mode="adhoc",
        input_file=None,
        daily_report_dir="reports/DailyReport",
        adhoc_dir="reports/AdHoc",
        dated_path_resolver=lambda root, name: f"{root}/{name}",
        latest_daily_finder=lambda: "daily.csv",
        latest_adhoc_finder=lambda: "adhoc.csv",
    )

    assert result == CsvInputResolution(path="adhoc.csv", source="latest")


def test_read_torrent_csv_returns_rows_and_success(tmp_path):
    csv_path = tmp_path / "torrents.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["title", "magnet", "type"])
        writer.writeheader()
        writer.writerow({"title": "Movie A", "magnet": "magnet:?xt=urn:btih:abc", "type": "subtitle"})

    rows, ok = read_torrent_csv(str(csv_path))

    assert ok is True
    assert rows == [{"title": "Movie A", "magnet": "magnet:?xt=urn:btih:abc", "type": "subtitle"}]


def test_read_torrent_csv_missing_file_returns_false(tmp_path):
    rows, ok = read_torrent_csv(str(tmp_path / "missing.csv"))

    assert rows == []
    assert ok is False
```

- [ ] **Step 2: Run tests and verify expected failure.**

```bash
pytest tests/unit/test_workflow_artifact_inputs.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'javdb.workflow'`.

- [ ] **Step 3: Implement the adapter.**

Create `javdb/workflow/__init__.py`:

```python
"""Workflow-level adapters shared by CLI-driven integration flows."""
```

Create `javdb/workflow/artifact_inputs.py`:

```python
from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from typing import Callable, Literal


@dataclass(frozen=True)
class CsvInputResolution:
    path: str
    source: Literal["explicit-path", "explicit-name", "latest"]


def resolve_qb_uploader_csv_path(
    *,
    mode: Literal["daily", "adhoc"],
    input_file: str | None,
    daily_report_dir: str,
    adhoc_dir: str,
    dated_path_resolver: Callable[[str, str], str],
    latest_daily_finder: Callable[[], str | None],
    latest_adhoc_finder: Callable[[], str | None],
) -> CsvInputResolution:
    if input_file:
        if os.path.sep in input_file or input_file.startswith("reports"):
            return CsvInputResolution(path=input_file, source="explicit-path")
        root = adhoc_dir if mode == "adhoc" else daily_report_dir
        return CsvInputResolution(
            path=dated_path_resolver(root, input_file),
            source="explicit-name",
        )

    finder = latest_adhoc_finder if mode == "adhoc" else latest_daily_finder
    return CsvInputResolution(path=finder() or "", source="latest")


def read_torrent_csv(filename: str) -> tuple[list[dict[str, str]], bool]:
    if not filename or not os.path.exists(filename):
        return [], False

    rows: list[dict[str, str]] = []
    try:
        with open(filename, newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                rows.append({str(key): str(value or "") for key, value in row.items()})
    except Exception:
        return rows, False

    return rows, True
```

- [ ] **Step 4: Run tests.**

```bash
pytest tests/unit/test_workflow_artifact_inputs.py -v
```

Expected: PASS.

---

## Task 3: Add Stats Sink Adapter

**Files:**
- Create: `javdb/workflow/stats_sink.py`
- Create: `tests/unit/test_workflow_stats_sink.py`

- [ ] **Step 1: Write stats sink tests.**

Create `tests/unit/test_workflow_stats_sink.py`:

```python
from __future__ import annotations

from javdb.workflow import stats_sink
from javdb.workflow.stats_sink import PikPakStats, StatsSinkResult, UploaderStats


def test_save_uploader_stats_skips_without_session_id():
    result = stats_sink.save_uploader_stats(None, UploaderStats(total_torrents=1))

    assert result == StatsSinkResult(saved=False, backend=None, error=None)


def test_save_uploader_stats_calls_storage(monkeypatch):
    calls: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(stats_sink, "_use_sqlite", lambda: True)
    monkeypatch.setattr(stats_sink, "_init_db", lambda: None)
    monkeypatch.setattr(stats_sink, "_current_backend", lambda: "sqlite")
    monkeypatch.setattr(
        stats_sink,
        "_db_save_uploader_stats",
        lambda session_id, payload: calls.append((session_id, payload)),
    )

    result = stats_sink.save_uploader_stats(
        "42",
        UploaderStats(total_torrents=3, successfully_added=2, failed_count=1),
    )

    assert result == StatsSinkResult(saved=True, backend="sqlite", error=None)
    assert calls == [("42", {"total_torrents": 3, "successfully_added": 2, "failed_count": 1})]


def test_save_pikpak_stats_calls_storage(monkeypatch):
    calls: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(stats_sink, "_use_sqlite", lambda: True)
    monkeypatch.setattr(stats_sink, "_init_db", lambda: None)
    monkeypatch.setattr(stats_sink, "_current_backend", lambda: "sqlite")
    monkeypatch.setattr(
        stats_sink,
        "_db_save_pikpak_stats",
        lambda session_id, payload: calls.append((session_id, payload)),
    )

    result = stats_sink.save_pikpak_stats(
        "99",
        PikPakStats(threshold_days=3, total_torrents=8, successful_count=5),
    )

    assert result == StatsSinkResult(saved=True, backend="sqlite", error=None)
    assert calls == [("99", {"threshold_days": 3, "total_torrents": 8, "successful_count": 5})]
```

- [ ] **Step 2: Run tests and verify expected failure.**

```bash
pytest tests/unit/test_workflow_stats_sink.py -v
```

Expected: FAIL with `ImportError` for missing names.

- [ ] **Step 3: Implement the adapter.**

Create `javdb/workflow/stats_sink.py`:

```python
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class StatsSinkResult:
    saved: bool
    backend: str | None
    error: str | None


@dataclass(frozen=True)
class UploaderStats:
    total_torrents: int = 0
    duplicate_count: int = 0
    attempted: int = 0
    successfully_added: int = 0
    failed_count: int = 0
    hacked_sub: int = 0
    hacked_nosub: int = 0
    subtitle_count: int = 0
    no_subtitle_count: int = 0
    success_rate: float = 0.0


@dataclass(frozen=True)
class PikPakStats:
    threshold_days: int = 0
    total_torrents: int = 0
    filtered_old: int = 0
    successful_count: int = 0
    failed_count: int = 0
    uploaded_count: int = 0
    delete_failed_count: int = 0


def _use_sqlite() -> bool:
    from javdb.infra.config import use_sqlite

    return use_sqlite()


def _init_db() -> None:
    from javdb.storage.db.db_migrations import init_db

    init_db()


def _current_backend() -> str:
    from javdb.storage.db.db_connection import current_backend

    return current_backend()


def _db_save_uploader_stats(session_id: str, payload: dict[str, Any]) -> None:
    from javdb.storage.db.db_stats import db_save_uploader_stats

    db_save_uploader_stats(session_id, payload)


def _db_save_pikpak_stats(session_id: str, payload: dict[str, Any]) -> None:
    from javdb.storage.db.db_stats import db_save_pikpak_stats

    db_save_pikpak_stats(session_id, payload)


def _compact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value not in (0, 0.0, None)}


def save_uploader_stats(session_id: str | None, stats: UploaderStats) -> StatsSinkResult:
    if not session_id or not _use_sqlite():
        return StatsSinkResult(saved=False, backend=None, error=None)
    try:
        _init_db()
        _db_save_uploader_stats(session_id, _compact_payload(asdict(stats)))
        return StatsSinkResult(saved=True, backend=_current_backend(), error=None)
    except Exception as exc:
        return StatsSinkResult(saved=False, backend=None, error=str(exc))


def save_pikpak_stats(session_id: str | None, stats: PikPakStats) -> StatsSinkResult:
    if not session_id or not _use_sqlite():
        return StatsSinkResult(saved=False, backend=None, error=None)
    try:
        _init_db()
        _db_save_pikpak_stats(session_id, _compact_payload(asdict(stats)))
        return StatsSinkResult(saved=True, backend=_current_backend(), error=None)
    except Exception as exc:
        return StatsSinkResult(saved=False, backend=None, error=str(exc))
```

- [ ] **Step 4: Run tests.**

```bash
pytest tests/unit/test_workflow_stats_sink.py -v
```

Expected: PASS.

---

## Task 4: Add Git Side-Effect Adapter

**Files:**
- Create: `javdb/workflow/git_side_effects.py`
- Create: `tests/unit/test_workflow_git_side_effects.py`

- [ ] **Step 1: Write git adapter tests.**

Create `tests/unit/test_workflow_git_side_effects.py`:

```python
from __future__ import annotations

from javdb.workflow import git_side_effects
from javdb.workflow.git_side_effects import GitCommitRequest, GitCommitResult


def test_commit_workflow_outputs_skips_without_credentials(monkeypatch):
    monkeypatch.setattr(git_side_effects, "_has_git_credentials", lambda username, password: False)

    result = git_side_effects.commit_workflow_outputs(
        GitCommitRequest(
            files_to_add=("logs/",),
            commit_message="Auto-commit: test",
            from_pipeline=True,
            git_username="",
            git_password="",
            git_repo_url="https://example.invalid/repo.git",
            git_branch="main",
        )
    )

    assert result == GitCommitResult(committed=False, skipped_reason="missing-credentials", error=None)


def test_commit_workflow_outputs_calls_git_helper(monkeypatch):
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(git_side_effects, "_has_git_credentials", lambda username, password: True)
    monkeypatch.setattr(git_side_effects, "_flush_log_handlers", lambda: calls.append({"flushed": True}))
    monkeypatch.setattr(
        git_side_effects,
        "_git_commit_and_push",
        lambda **kwargs: calls.append(kwargs),
    )

    result = git_side_effects.commit_workflow_outputs(
        GitCommitRequest(
            files_to_add=("logs/", "reports"),
            commit_message="Auto-commit: test",
            from_pipeline=True,
            git_username="user",
            git_password="token",
            git_repo_url="https://example.invalid/repo.git",
            git_branch="main",
        )
    )

    assert result == GitCommitResult(committed=True, skipped_reason=None, error=None)
    assert calls[0] == {"flushed": True}
    assert calls[1]["files_to_add"] == ["logs/", "reports"]
    assert calls[1]["commit_message"] == "Auto-commit: test"
```

- [ ] **Step 2: Run tests and verify expected failure.**

```bash
pytest tests/unit/test_workflow_git_side_effects.py -v
```

Expected: FAIL with `ImportError` for missing names.

- [ ] **Step 3: Implement the adapter.**

Create `javdb/workflow/git_side_effects.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class GitCommitRequest:
    files_to_add: Sequence[str]
    commit_message: str
    from_pipeline: bool
    git_username: str
    git_password: str
    git_repo_url: str
    git_branch: str


@dataclass(frozen=True)
class GitCommitResult:
    committed: bool
    skipped_reason: str | None
    error: str | None


def _has_git_credentials(git_username: str, git_password: str) -> bool:
    from javdb.infra.git_helper import has_git_credentials

    return has_git_credentials(git_username, git_password)


def _flush_log_handlers() -> None:
    from javdb.infra.git_helper import flush_log_handlers

    flush_log_handlers()


def _git_commit_and_push(**kwargs: object) -> None:
    from javdb.infra.git_helper import git_commit_and_push

    git_commit_and_push(**kwargs)


def commit_workflow_outputs(request: GitCommitRequest) -> GitCommitResult:
    if not _has_git_credentials(request.git_username, request.git_password):
        return GitCommitResult(
            committed=False,
            skipped_reason="missing-credentials",
            error=None,
        )

    try:
        _flush_log_handlers()
        _git_commit_and_push(
            files_to_add=list(request.files_to_add),
            commit_message=request.commit_message,
            from_pipeline=request.from_pipeline,
            git_username=request.git_username,
            git_password=request.git_password,
            git_repo_url=request.git_repo_url,
            git_branch=request.git_branch,
        )
    except Exception as exc:
        return GitCommitResult(committed=False, skipped_reason=None, error=str(exc))

    return GitCommitResult(committed=True, skipped_reason=None, error=None)
```

- [ ] **Step 4: Run tests.**

```bash
pytest tests/unit/test_workflow_git_side_effects.py -v
```

Expected: PASS.

---

## Task 5: Update Boundary Documentation

**Files:**
- Modify: `apps/cli/README.md`
- Modify: `javdb/integrations/README.md`

- [ ] **Step 1: Update apps CLI README.**

Replace the alias convention bullet with:

```markdown
- `apps.cli.*` is the only user-facing CLI surface. Some integration wrappers still alias `javdb.integrations.*` during the ADR-015 migration; those aliases are tracked by architecture allowlists and are removed by IMP-031 through IMP-036.
```

- [ ] **Step 2: Update integrations README.**

Add this note after the opening paragraph:

```markdown
> **ADR-015 migration note:** `javdb.integrations.*` is converging to service/client modules only. User-facing `argparse`, `main()`, `sys.exit()`, and `python -m` entrypoints belong under `apps.cli.*`.
```

---

## Task 6: Verify Phase 1

- [ ] **Step 1: Run focused tests.**

```bash
pytest tests/architecture/test_integrations_interface_boundary.py -v
pytest tests/unit/test_workflow_artifact_inputs.py -v
pytest tests/unit/test_workflow_stats_sink.py -v
pytest tests/unit/test_workflow_git_side_effects.py -v
```

Expected: PASS.

- [ ] **Step 2: Review workflows and docs.**

Review:

```bash
rg -n "apps\.cli\.(qb|pikpak|rclone|notify)|qb\.uploader|qb\.file_filter|pikpak\.bridge|rclone\.manager|notify\.email" .github/workflows README.md JAVDB_AutoSpider.wiki 2>/dev/null
```

Expected: command invocations remain unchanged. Record in the implementation notes whether README/wiki changes were required.

- [ ] **Step 3: Commit.**

```bash
git add tests/architecture/test_integrations_interface_boundary.py \
        javdb/workflow/__init__.py \
        javdb/workflow/artifact_inputs.py \
        javdb/workflow/stats_sink.py \
        javdb/workflow/git_side_effects.py \
        tests/unit/test_workflow_artifact_inputs.py \
        tests/unit/test_workflow_stats_sink.py \
        tests/unit/test_workflow_git_side_effects.py \
        apps/cli/README.md \
        javdb/integrations/README.md
git commit -m "refactor(integrations): add CLI boundary guards"
```
