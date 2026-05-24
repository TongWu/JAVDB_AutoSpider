# IMP-ADR014-02: ADR-014 Phase 2 - Canonical Lifecycle Helpers

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ADR-014 Phase 2 by moving the shared session helper implementation to `javdb.storage.sessions.lifecycle_helpers`, keeping legacy wrappers temporarily, and migrating production callers to the canonical path.

**Architecture:** `javdb.storage.sessions.lifecycle_helpers` becomes the single implementation module for shared session lifecycle scaffolding. Rollback and CLI helper paths become compatibility wrappers for this phase only.

**Tech Stack:** Python 3.11, pytest, existing DB rollback/commit storage libraries and CLIs.

**Source spec:** [ADR-014](ADR-014-storage-cli-layering.md), D4-D8.

**Non-negotiable:** This phase is behavior-preserving. Preserve helper signatures, exception handling, logging, JSONL writes, `GITHUB_OUTPUT`, MovieClaim fanout, run identity attachment, and commit/rollback exit codes.

---

## Files

| Path | Responsibility |
|---|---|
| `javdb/storage/sessions/lifecycle_helpers.py` | New canonical implementation module. |
| `javdb/storage/rollback/session_helpers.py` | Temporary re-export wrapper to the canonical module. |
| `apps/cli/db/_session_helpers.py` | Temporary CLI re-export wrapper to the canonical module. |
| `apps/cli/db/commit_session.py` | Production caller import migration. |
| `javdb/storage/rollback/core.py` | Production caller import migration. |
| `javdb/storage/sessions/commit.py` | Production caller import migration. |
| `apps/cli/db/README.md` | Document canonical helper ownership. |
| `javdb/storage/rollback/README.md` | Remove rollback-only helper ownership language, when present. |
| `javdb/storage/sessions/README.md` | Document session lifecycle helper ownership. |
| `tests/unit/test_session_helpers.py` | Behavior coverage through the canonical path. |
| `tests/unit/test_session_lifecycle_helper_imports.py` | New import identity coverage for temporary wrappers. |
| `tests/unit/test_rollback_core_library.py` | Rollback library coverage. |
| `tests/unit/test_rollback_cli.py` | Rollback CLI coverage. |
| `tests/unit/test_rollback_commit_cli.py` | Rollback/commit CLI coverage. |

---

## Task 1: Move Implementation To The Canonical Module

**Files:**
- Create: `javdb/storage/sessions/lifecycle_helpers.py`
- Modify: `javdb/storage/rollback/session_helpers.py`
- Modify: `apps/cli/db/_session_helpers.py`

- [ ] **Step 1: Move the implementation.**

Use `git mv`:

```bash
git mv javdb/storage/rollback/session_helpers.py javdb/storage/sessions/lifecycle_helpers.py
```

Update the new module docstring to describe the canonical ownership:

```python
"""Shared session lifecycle helpers for rollback, commit, and API session flows.

This module is the canonical implementation for timestamp normalization,
session lookups, pre-state reads, MovieClaim fanout, JSONL emission, workflow
output adapters, and run identity attachment.
"""
```

- [ ] **Step 2: Recreate the rollback compatibility wrapper.**

Create `javdb/storage/rollback/session_helpers.py`:

```python
"""Compatibility wrapper for javdb.storage.sessions.lifecycle_helpers."""

from javdb.storage.sessions.lifecycle_helpers import *  # noqa: F401,F403
```

- [ ] **Step 3: Update the CLI compatibility wrapper.**

Change `apps/cli/db/_session_helpers.py` to re-export the canonical module:

```python
"""Compatibility wrapper for javdb.storage.sessions.lifecycle_helpers."""

from javdb.storage.sessions.lifecycle_helpers import *  # noqa: F401,F403
```

Keep both wrappers in place until Phase 3.

---

## Task 2: Migrate Production Callers

**Files:**
- Modify: `apps/cli/db/commit_session.py`
- Modify: `javdb/storage/rollback/core.py`
- Modify: `javdb/storage/sessions/commit.py`

- [ ] **Step 1: Change imports to the canonical path.**

Replace imports from `javdb.storage.rollback.session_helpers` with:

```python
from javdb.storage.sessions.lifecycle_helpers import (
    ...
)
```

Apply this to:

- `apps/cli/db/commit_session.py`
- `javdb/storage/rollback/core.py`
- `javdb/storage/sessions/commit.py`

- [ ] **Step 2: Confirm production callers no longer use the legacy paths.**

```bash
rg -n "apps\.cli\.db\._session_helpers|javdb\.storage\.rollback\.session_helpers" apps javdb
```

Expected: only compatibility wrapper definitions, docstrings, or comments that
describe the migration.

---

## Task 3: Add Import Identity Coverage

**Files:**
- Create: `tests/unit/test_session_lifecycle_helper_imports.py`
- Modify: `tests/unit/test_session_helpers.py`

- [ ] **Step 1: Make helper behavior tests import the canonical path.**

Change:

```python
from javdb.storage.rollback import session_helpers as helpers
```

to:

```python
from javdb.storage.sessions import lifecycle_helpers as helpers
```

- [ ] **Step 2: Add temporary wrapper identity tests.**

Create `tests/unit/test_session_lifecycle_helper_imports.py`:

```python
from __future__ import annotations

from apps.cli.db import _session_helpers as cli_helpers
from javdb.storage.rollback import session_helpers as rollback_helpers
from javdb.storage.sessions import lifecycle_helpers as canonical


def test_legacy_wrappers_reexport_canonical_helpers():
    assert cli_helpers.normalize_run_started_at is canonical.normalize_run_started_at
    assert rollback_helpers.normalize_run_started_at is canonical.normalize_run_started_at
    assert cli_helpers.fanout_movie_claim is canonical.fanout_movie_claim
    assert rollback_helpers.fanout_movie_claim is canonical.fanout_movie_claim
```

These tests are temporary and are deleted in Phase 3 with the wrappers.

---

## Task 4: Update Documentation

**Files:**
- Modify: `apps/cli/db/README.md`
- Modify: `javdb/storage/rollback/README.md`
- Modify: `javdb/storage/sessions/README.md`

- [ ] **Step 1: Update CLI DB README.**

Describe `_session_helpers.py` as a temporary wrapper, not the helper owner.
Name `javdb.storage.sessions.lifecycle_helpers` as the canonical helper module.

- [ ] **Step 2: Update rollback storage README.**

Rollback owns rollback orchestration. It does not own generic session lifecycle
helper implementation after this phase.

- [ ] **Step 3: Update sessions README.**

Document that `lifecycle_helpers.py` owns shared session lifecycle scaffolding:

- timestamp normalization;
- session lookup;
- pre-state reads;
- MovieClaim fanout;
- JSONL emission;
- workflow output adapter;
- run identity attachment.

---

## Task 5: Verify Phase 2

- [ ] **Step 1: Run focused tests.**

```bash
pytest tests/unit/test_session_lifecycle_helper_imports.py -v
pytest tests/unit/test_session_helpers.py -v
pytest tests/unit/test_rollback_core_library.py -v
pytest tests/unit/test_rollback_cli.py -v
pytest tests/unit/test_rollback_commit_cli.py -v
pytest tests/architecture/test_storage_cli_layering.py -v
```

Expected: PASS.

- [ ] **Step 2: Run import search.**

```bash
rg -n "apps\.cli\.db\._session_helpers|javdb\.storage\.rollback\.session_helpers" apps javdb tests
```

Expected: only wrapper modules, wrapper identity tests, and migration docs.

- [ ] **Step 3: Review workflow and docs impact.**

This phase changes internal imports and README ownership text. CLI flags,
workflow commands, stdout output, `GITHUB_OUTPUT`, and JSONL behavior stay
unchanged. Confirm `.github/workflows/`, root README, and the wiki do not need
usage changes.

- [ ] **Step 4: Commit.**

```bash
git add javdb/storage/sessions/lifecycle_helpers.py \
        javdb/storage/rollback/session_helpers.py \
        apps/cli/db/_session_helpers.py \
        apps/cli/db/commit_session.py \
        javdb/storage/rollback/core.py \
        javdb/storage/sessions/commit.py \
        apps/cli/db/README.md \
        javdb/storage/rollback/README.md \
        javdb/storage/sessions/README.md \
        tests/unit/test_session_helpers.py \
        tests/unit/test_session_lifecycle_helper_imports.py
git commit -m "refactor(storage): canonicalize session lifecycle helpers"
```
