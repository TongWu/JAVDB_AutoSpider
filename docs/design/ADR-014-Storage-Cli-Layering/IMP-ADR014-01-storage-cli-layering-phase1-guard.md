# IMP-ADR014-01: ADR-014 Phase 1 - Guard And Direct Storage Imports

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ADR-014 Phase 1 by making the storage-to-CLI dependency rule executable, switching the remaining commit-session helper import away from the CLI shim, and updating stale design docs.

**Architecture:** CLI modules may import storage modules. `javdb.storage.*` must not import `apps.cli.*`. Phase 1 keeps the current helper implementation at `javdb.storage.rollback.session_helpers`; the neutral canonical move waits for Phase 2.

**Tech Stack:** Python 3.11, ast, pathlib, pytest, existing DB rollback/commit CLIs.

**Source spec:** [ADR-014](ADR-014-storage-cli-layering.md), D1-D3 and D9.

**Non-negotiable:** This phase is behavior-preserving. Do not move helper implementation yet. Do not change rollback, commit, pending-mode, MovieClaim fanout, JSONL, `GITHUB_OUTPUT`, run identity, or exit-code semantics.

---

## Files

| Path | Responsibility |
|---|---|
| `tests/architecture/test_storage_cli_layering.py` | New import-direction guard for storage modules. |
| `apps/cli/db/commit_session.py` | Import shared helpers from storage instead of the CLI shim. |
| `docs/design/ADR-008-Frontend-Rewrite/ADR-008-frontend-rewrite-architecture.md` | Add updated note for the historical rollback layering item. |
| `docs/design/ADR-008-Frontend-Rewrite/ADR-008-frontend-rewrite-architecture.zh.md` | Chinese version of the same updated note. |
| `docs/design/ADR-008-Frontend-Rewrite/IMP-ADR008-02-frontend-phase1-completion.md` | Add updated note for Task 4. |
| `tests/unit/test_session_helpers.py` | Existing behavior coverage for shared helpers. |
| `tests/unit/test_rollback_core_library.py` | Existing rollback library coverage. |
| `tests/unit/test_rollback_cli.py` | Existing rollback CLI coverage. |
| `tests/unit/test_rollback_commit_cli.py` | Existing rollback/commit CLI coverage. |

---

## Task 1: Add Storage-To-CLI Architecture Guard

**Files:**
- Create: `tests/architecture/test_storage_cli_layering.py`

- [ ] **Step 1: Write the guard test.**

Create `tests/architecture/test_storage_cli_layering.py`:

```python
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
STORAGE_ROOT = ROOT / "javdb" / "storage"


def _storage_python_files() -> list[Path]:
    return sorted(
        path
        for path in STORAGE_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    )


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


def test_storage_modules_do_not_import_cli_modules():
    offenders: list[str] = []

    for path in _storage_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for line_no, target in _import_targets(tree):
            if target == "apps.cli" or target.startswith("apps.cli."):
                relpath = path.relative_to(ROOT)
                offenders.append(f"{relpath}:{line_no}: imports {target}")

    assert offenders == []
```

- [ ] **Step 2: Run the guard.**

```bash
pytest tests/architecture/test_storage_cli_layering.py -v
```

Expected: PASS on the current codebase. The test protects against reintroducing
the old direction.

---

## Task 2: Switch Commit Session Off The CLI Shim

**Files:**
- Modify: `apps/cli/db/commit_session.py`

- [ ] **Step 1: Replace the helper import.**

Change:

```python
from apps.cli.db._session_helpers import (
    ...
)
```

to:

```python
from javdb.storage.rollback.session_helpers import (
    ...
)
```

- [ ] **Step 2: Keep behavior unchanged.**

Do not rename helper functions, change arguments, change logging, alter
exception handling, or change exit codes in this phase.

- [ ] **Step 3: Confirm the CLI shim has no production caller requirement.**

```bash
rg -n "apps\.cli\.db\._session_helpers" apps javdb tests
```

Expected after this phase: no production import from `apps/cli/db/commit_session.py`.
Test references can remain until Phase 2 or Phase 3, based on their monkeypatch
target.

---

## Task 3: Update Historical Docs

**Files:**
- Modify: `docs/design/ADR-008-Frontend-Rewrite/ADR-008-frontend-rewrite-architecture.md`
- Modify: `docs/design/ADR-008-Frontend-Rewrite/ADR-008-frontend-rewrite-architecture.zh.md`
- Modify: `docs/design/ADR-008-Frontend-Rewrite/IMP-ADR008-02-frontend-phase1-completion.md`

- [ ] **Step 1: Add the ADR-008 updated note.**

Replace the historical rollback-layering bullet with a note that says:

- the original `javdb/storage/rollback/core.py -> apps.cli.db._session_helpers`
  import has been removed;
- `javdb.storage.rollback.session_helpers` is the current interim storage path;
- ADR-014 tracks convergence to `javdb.storage.sessions.lifecycle_helpers`;
- Phase 3 deletes both legacy wrappers.

- [ ] **Step 2: Add the IMP-ADR008-02 Task 4 updated note.**

Add a short status note before the Task 4 context:

```text
2026-05-20 update: this task has been partially completed. The original storage-to-CLI import has moved to javdb.storage.rollback.session_helpers, while apps.cli.db._session_helpers remains a shim. ADR-014 and IMP-ADR014-01 through IMP-ADR014-03 track the final canonical module and wrapper deletion.
```

Keep the historical instructions intact below the note so the document remains
auditable.

---

## Task 4: Verify Phase 1

- [ ] **Step 1: Run focused tests.**

```bash
pytest tests/architecture/test_storage_cli_layering.py -v
pytest tests/unit/test_session_helpers.py -v
pytest tests/unit/test_rollback_core_library.py -v
pytest tests/unit/test_rollback_cli.py -v
pytest tests/unit/test_rollback_commit_cli.py -v
```

Expected: PASS.

- [ ] **Step 2: Run a focused import search.**

```bash
rg -n "from apps\.cli|import apps\.cli" javdb/storage
```

Expected: no results.

- [ ] **Step 3: Review workflow and docs impact.**

This phase changes a CLI import but not CLI behavior, flags, outputs, or
workflow commands. Review `.github/workflows/` and the root README/wiki only to
confirm no usage text changes are required.

- [ ] **Step 4: Commit.**

```bash
git add tests/architecture/test_storage_cli_layering.py \
        apps/cli/db/commit_session.py \
        docs/design/ADR-008-Frontend-Rewrite/ADR-008-frontend-rewrite-architecture.md \
        docs/design/ADR-008-Frontend-Rewrite/ADR-008-frontend-rewrite-architecture.zh.md \
        docs/design/ADR-008-Frontend-Rewrite/IMP-ADR008-02-frontend-phase1-completion.md
git commit -m "refactor(storage): guard CLI layering"
```
