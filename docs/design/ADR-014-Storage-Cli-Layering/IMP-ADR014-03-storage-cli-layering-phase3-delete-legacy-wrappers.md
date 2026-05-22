# IMP-ADR014-03: ADR-014 Phase 3 - Delete Legacy Wrappers

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ADR-014 Phase 3 by deleting both legacy helper wrappers, updating all imports and monkeypatch targets to `javdb.storage.sessions.lifecycle_helpers`, and extending guards so the old paths do not return.

**Architecture:** `javdb.storage.sessions.lifecycle_helpers` is the only supported helper import path. `apps.cli.db._session_helpers` and `javdb.storage.rollback.session_helpers` are removed.

**Tech Stack:** Python 3.11, ast, importlib, pathlib, pytest, existing DB rollback/commit tests.

**Source spec:** [ADR-014](ADR-014-storage-cli-layering.md), D6-D8.

**Non-negotiable:** This phase is behavior-preserving. Wrapper deletion must not change helper behavior, CLI output, `GITHUB_OUTPUT`, JSONL, MovieClaim fanout, run identity, or commit/rollback exit codes.

---

## Files

| Path | Responsibility |
|---|---|
| `apps/cli/db/_session_helpers.py` | Delete. |
| `javdb/storage/rollback/session_helpers.py` | Delete. |
| `tests/architecture/test_storage_cli_layering.py` | Extend guard to reject deleted legacy imports. |
| `tests/unit/test_session_lifecycle_helper_imports.py` | Replace Phase 2 wrapper identity tests with deletion tests, or delete and fold checks into architecture tests. |
| `tests/unit/test_session_helpers.py` | Canonical helper behavior coverage. |
| `tests/unit/test_rollback_core_library.py` | Rollback library coverage. |
| `tests/unit/test_rollback_cli.py` | Rollback CLI coverage. |
| `tests/unit/test_rollback_commit_cli.py` | Rollback/commit CLI coverage. |
| `tests/unit/test_rollback_pending_mode.py` | Update monkeypatch targets when present. |
| `apps/cli/db/README.md` | Remove `_session_helpers.py` from active module list. |
| `javdb/storage/rollback/README.md` | Remove legacy wrapper references. |
| `javdb/storage/sessions/README.md` | Confirm canonical helper ownership. |

---

## Task 1: Delete The Wrapper Modules

**Files:**
- Delete: `apps/cli/db/_session_helpers.py`
- Delete: `javdb/storage/rollback/session_helpers.py`

- [ ] **Step 1: Remove wrapper files.**

```bash
git rm apps/cli/db/_session_helpers.py
git rm javdb/storage/rollback/session_helpers.py
```

- [ ] **Step 2: Confirm no production import remains.**

```bash
rg -n "apps\.cli\.db\._session_helpers|javdb\.storage\.rollback\.session_helpers" apps javdb
```

Expected: no production imports. Documentation references are handled in Task 4.

---

## Task 2: Update Tests And Monkeypatch Targets

**Files:**
- Modify: tests that mention `apps.cli.db._session_helpers`
- Modify: tests that mention `javdb.storage.rollback.session_helpers`
- Modify or delete: `tests/unit/test_session_lifecycle_helper_imports.py`

- [ ] **Step 1: Locate legacy test references.**

```bash
rg -n "apps\.cli\.db\._session_helpers|javdb\.storage\.rollback\.session_helpers" tests
```

- [ ] **Step 2: Move monkeypatch targets to the canonical path.**

Use:

```text
javdb.storage.sessions.lifecycle_helpers
```

for helper monkeypatches.

- [ ] **Step 3: Replace wrapper identity tests with deletion tests.**

Create or update `tests/unit/test_session_lifecycle_helper_imports.py`:

```python
from __future__ import annotations

import importlib.util

from javdb.storage.sessions import lifecycle_helpers


def test_canonical_lifecycle_helpers_remain_importable():
    assert lifecycle_helpers.normalize_run_started_at is not None


def test_legacy_session_helper_wrappers_are_deleted():
    assert importlib.util.find_spec("apps.cli.db._session_helpers") is None
    assert importlib.util.find_spec("javdb.storage.rollback.session_helpers") is None
```

---

## Task 3: Extend Architecture Guard

**Files:**
- Modify: `tests/architecture/test_storage_cli_layering.py`

- [ ] **Step 1: Keep the storage-to-CLI guard.**

Do not weaken the Phase 1 rule:

```text
javdb.storage.* must not import apps.cli.*
```

- [ ] **Step 2: Add a deleted-path import guard.**

Extend the test file with a repository-wide AST scan for Python import
statements that reference either deleted helper path:

```python
def _import_targets(tree: ast.AST) -> list[tuple[int, str]]:
    targets: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend((alias.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            targets.append((node.lineno, node.module))
            targets.extend(
                (node.lineno, f"{node.module}.{alias.name}")
                for alias in node.names
                if alias.name != "*"
            )
    return targets


DELETED_HELPER_MODULES = {
    "apps.cli.db._session_helpers",
    "javdb.storage.rollback.session_helpers",
}

SCAN_ROOTS = (
    ROOT / "apps",
    ROOT / "javdb",
    ROOT / "tests",
)


def test_deleted_session_helper_modules_are_not_imported():
    offenders: list[str] = []

    for scan_root in SCAN_ROOTS:
        for path in scan_root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for line_no, target in _import_targets(tree):
                if target in DELETED_HELPER_MODULES:
                    relpath = path.relative_to(ROOT)
                    offenders.append(f"{relpath}:{line_no}: imports {target}")

    assert offenders == []
```

---

## Task 4: Update Documentation

**Files:**
- Modify: `apps/cli/db/README.md`
- Modify: `javdb/storage/rollback/README.md`
- Modify: `javdb/storage/sessions/README.md`
- Modify: docs that mention deleted wrapper paths

- [ ] **Step 1: Remove `_session_helpers.py` from the active CLI DB README.**

The README should no longer list `_session_helpers.py` as an active module.

- [ ] **Step 2: Remove rollback helper wrapper language.**

Rollback docs should direct shared helper readers to
`javdb.storage.sessions.lifecycle_helpers`.

- [ ] **Step 3: Search docs for stale wrapper references.**

```bash
rg -n "apps\.cli\.db\._session_helpers|javdb\.storage\.rollback\.session_helpers" docs apps javdb
```

Expected: only historical ADR/IMP references that explicitly say the paths were
legacy and deleted by ADR-014 Phase 3.

---

## Task 5: Verify Phase 3

- [ ] **Step 1: Run focused tests.**

```bash
pytest tests/architecture/test_storage_cli_layering.py -v
pytest tests/unit/test_session_lifecycle_helper_imports.py -v
pytest tests/unit/test_session_helpers.py -v
pytest tests/unit/test_rollback_core_library.py -v
pytest tests/unit/test_rollback_cli.py -v
pytest tests/unit/test_rollback_commit_cli.py -v
pytest tests/unit/test_rollback_pending_mode.py -v
```

Expected: PASS.

- [ ] **Step 2: Run import searches.**

```bash
rg -n "from apps\.cli|import apps\.cli" javdb/storage
rg -n "apps\.cli\.db\._session_helpers|javdb\.storage\.rollback\.session_helpers" apps javdb tests
```

Expected: no results.

- [ ] **Step 3: Review workflow and docs impact.**

This phase deletes Python compatibility wrappers, but does not change CLI
commands, flags, stdout, workflow outputs, or JSONL. Confirm `.github/workflows/`,
root README, and the wiki do not require usage changes.

- [ ] **Step 4: Commit.**

```bash
git add -u apps/cli/db/_session_helpers.py \
          javdb/storage/rollback/session_helpers.py \
          tests/architecture/test_storage_cli_layering.py \
          tests/unit/test_session_lifecycle_helper_imports.py \
          tests/unit/test_session_helpers.py \
          tests/unit/test_rollback_pending_mode.py \
          apps/cli/db/README.md \
          javdb/storage/rollback/README.md \
          javdb/storage/sessions/README.md
git commit -m "refactor(storage): delete legacy session helper wrappers"
```
