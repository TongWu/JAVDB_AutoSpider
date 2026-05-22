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

---

## Task 2: Delete CLI Surface And Bake Wrapper

**Files:**
- Modify: `javdb/integrations/rclone/manager/__init__.py`
- Delete: `javdb/integrations/rclone/manager/_legacy.py`

- [ ] **Step 1: Replace `__init__.py` with final exports.**

Use:

```python
"""Rclone manager service package."""

from javdb.integrations.rclone.manager.options import RcloneManagerOptions
from javdb.integrations.rclone.manager.result import RcloneManagerResult
from javdb.integrations.rclone.manager.service import run_manager

__all__ = ["RcloneManagerOptions", "RcloneManagerResult", "run_manager"]
```

- [ ] **Step 2: Delete `_legacy.py`.**

Run:

```bash
git rm javdb/integrations/rclone/manager/_legacy.py
```

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
        tests/architecture/test_integrations_interface_boundary.py \
        tests/unit/test_rclone_manager.py \
        apps/cli/rclone/README.md \
        javdb/integrations/rclone/README.md
git add -u javdb/integrations/rclone/manager/_legacy.py
git commit -m "refactor(integrations): remove rclone manager bake wrapper"
```
