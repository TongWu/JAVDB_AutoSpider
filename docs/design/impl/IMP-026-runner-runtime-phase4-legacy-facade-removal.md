# IMP-026: ADR-013 Phase 4 - Legacy Facade Freeze Or Removal

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish ADR-013 by freezing or deleting legacy direct-mutation `state.py` compatibility after production callers have migrated to explicit runtime/context access.

**Architecture:** `state.py` stops being a broad mutable global API. Remaining entries are either documented compatibility functions or deleted in favor of explicit `SpiderRuntime` access. Architecture tests prevent production code from adding new direct `state.*` dependencies.

**Tech Stack:** Python 3.11, pytest architecture tests, ripgrep, Markdown docs.

**Source spec:** [ADR-013](../adr/ADR-013-runner-runtime-state-consolidation.md), D4-D5, D12.

**Non-negotiable:** Do not remove a compatibility entry while production code still depends on it. Do not leave a direct-mutation wrapper without documenting whether it is frozen compatibility or scheduled for deletion.

---

## Files

| Path | Responsibility |
|---|---|
| `javdb/spider/runtime/state.py` | Freeze or remove legacy direct-mutation fields and keep documented public compatibility functions. |
| `javdb/spider/runtime/context.py` | Final runtime/context source of truth. |
| `tests/architecture/test_spider_runtime_state_boundaries.py` | New architecture tests preventing production direct mutation of `state.py`. |
| `tests/unit/test_spider_runtime_state_facade.py` | Update to assert the final compatibility contract. |
| `docs/design/architecture/spider-baseline.md` | Update runtime ownership rule. |
| `javdb/spider/README.md` | Document new runtime/context usage rule for Spider contributors. |

---

## Task 1: Add Architecture Guard For Production Direct Mutation

**Files:**
- Create: `tests/architecture/test_spider_runtime_state_boundaries.py`

- [ ] **Step 1: Write failing architecture test**

Create `tests/architecture/test_spider_runtime_state_boundaries.py`:

```python
from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]

PRODUCTION_GLOBS = [
    "javdb/spider/**/*.py",
]

ALLOWED_FILES = {
    "javdb/spider/runtime/state.py",
    "javdb/spider/runtime/context.py",
}

FORBIDDEN_DIRECT_STATE_FIELDS = {
    "parsed_links",
    "proxy_ban_html_files",
    "global_proxy_pool",
    "global_request_handler",
    "global_proxy_coordinator",
    "global_login_state_client",
    "global_movie_claim_client",
    "global_runner_registry_client",
    "global_recommend_proxy_policy",
    "global_work_distributor_client",
    "runtime_holder_id",
    "login_attempted",
    "refreshed_session_cookie",
    "logged_in_proxy_name",
    "current_login_state_version",
    "login_attempts_per_proxy",
    "login_failures_per_proxy",
    "login_total_attempts",
    "login_total_budget",
    "always_bypass_time",
    "proxies_requiring_cf_bypass",
}


def _production_files():
    for pattern in PRODUCTION_GLOBS:
        for path in ROOT.glob(pattern):
            rel = path.relative_to(ROOT).as_posix()
            if rel in ALLOWED_FILES:
                continue
            if "__pycache__" in rel:
                continue
            yield path


def test_production_code_does_not_directly_use_legacy_state_fields():
    pattern = re.compile(
        r"\\bstate\\.("
        + "|".join(re.escape(name) for name in sorted(FORBIDDEN_DIRECT_STATE_FIELDS))
        + r")\\b"
    )
    offenders: list[str] = []
    for path in _production_files():
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{line_no}: {line.strip()}")

    assert offenders == []
```

- [ ] **Step 2: Run test and capture remaining offenders**

Run:

```bash
pytest tests/architecture/test_spider_runtime_state_boundaries.py -v
```

Expected: FAIL listing any production files still using direct legacy fields.
Fix every listed production offender before removing wrappers.

---

## Task 2: Remove Or Freeze Legacy Direct-Mutation Fields

**Files:**
- Modify: `javdb/spider/runtime/state.py`
- Modify: `tests/unit/test_spider_runtime_state_facade.py`

- [ ] **Step 1: Define final allowed facade entries**

Keep these function-style compatibility entries in `state.py`:

- `bind_active_runtime`
- `clear_active_runtime`
- `get_active_runtime`
- `setup_proxy_pool`
- `initialize_request_handler`
- `get_page`
- `should_use_proxy_for_module`
- `extract_ip_from_proxy_url`
- `get_cf_bypass_service_url`
- `is_cf_bypass_failure`
- `set_active_runner_session`
- `setup_runner_registry_client`
- `setup_movie_claim_client`
- `setup_login_state_client`
- `setup_proxy_coordinator`
- `setup_work_distributor_client`
- `proxy_needs_cf_bypass`
- `mark_proxy_cf_bypass`
- `deduct_proxy_login_budget`
- `ensure_reports_dir`
- `ensure_report_dated_dir`
- `save_proxy_ban_html`

Delete or freeze broad public data-field compatibility after tests prove
production callers no longer use it.

- [ ] **Step 2: Update facade tests to the final contract**

Replace direct-field tests in `tests/unit/test_spider_runtime_state_facade.py`
with explicit final contract tests:

```python
from __future__ import annotations

import pytest

import javdb.spider.runtime.state as state
from javdb.spider.runtime.context import SpiderRuntime


def test_state_facade_keeps_runtime_binding_functions():
    runtime = SpiderRuntime()

    assert state.bind_active_runtime(runtime) is runtime
    assert state.get_active_runtime() is runtime
    state.clear_active_runtime(runtime)
    assert state.get_active_runtime() is None


def test_deleted_direct_field_compatibility_is_not_part_of_public_contract():
    assert "parsed_links" not in getattr(state, "__all__", [])
    assert "global_proxy_pool" not in getattr(state, "__all__", [])
```

If a field must remain for external compatibility, add it to a documented
`LEGACY_DATA_FIELDS` tuple in `state.py` and make the test assert it is listed
there. Do not leave undocumented data fields.

- [ ] **Step 3: Run facade tests**

Run:

```bash
pytest tests/unit/test_spider_runtime_state_facade.py -v
```

Expected: PASS.

---

## Task 3: Update Runtime Ownership Documentation

**Files:**
- Modify: `docs/design/architecture/spider-baseline.md`
- Modify: `javdb/spider/README.md`

- [ ] **Step 1: Update architecture baseline**

In `docs/design/architecture/spider-baseline.md`, replace the runtime state row
with:

```markdown
- Runtime ownership lives under `scripts/spider/runtime/`
  - `context.py`: `SpiderRuntime` aggregate and focused runtime state objects
  - `state.py`: documented compatibility facade for legacy entrypoints
  - `sleep.py`: sleep/throttle classes and compatibility names; production code uses runtime-owned sleep state
  - `report.py`: summary reporting with explicit runtime access
```

Add this usage rule:

```markdown
## Runtime Usage Rule

New Spider production code must receive `SpiderRuntime` or a focused state/service object explicitly. Do not add new direct `state.<field>` dependencies. `state.py` exists only for documented compatibility functions and transitional tests.
```

- [ ] **Step 2: Update Spider README**

Add to `javdb/spider/README.md`:

```markdown
## Runtime State Rule

`SpiderRunService` owns one `SpiderRuntime` per run. Runtime state is split into focused objects such as `DetailRunState`, `ProxyRunState`, `LoginRunState`, `RunnerRegistryState`, `MovieClaimRuntimeState`, and `SleepRuntimeState`.

New production code should accept runtime state/services explicitly instead of importing mutable fields from `javdb.spider.runtime.state`. The `state.py` module is a compatibility facade for legacy entrypoints.
```

- [ ] **Step 3: Run doc link sanity checks**

Run:

```bash
python - <<'PY'
from pathlib import Path
for path in [
    Path("docs/design/architecture/spider-baseline.md"),
    Path("javdb/spider/README.md"),
]:
    text = path.read_text(encoding="utf-8")
    assert "SpiderRuntime" in text
    assert "state.py" in text
PY
```

Expected: command exits 0.

---

## Task 4: Phase 4 Gate

- [ ] Run architecture guard:

```bash
pytest tests/architecture/test_spider_runtime_state_boundaries.py -v
```

- [ ] Run final runtime and smoke tests:

```bash
pytest tests/unit/test_spider_runtime_context.py tests/unit/test_spider_runtime_state_facade.py tests/unit/test_spider_runtime_registry_lifecycle.py tests/unit/test_spider_runtime_explicit_callers.py tests/smoke/test_spider.py tests/smoke/test_spider_detail_runner.py tests/smoke/test_spider_app_main.py -v
```

- [ ] Run related runtime behavior suites:

```bash
pytest tests/unit/test_engine.py tests/unit/test_login_coordinator_park.py tests/unit/test_sleep_with_coordinator.py tests/unit/test_setup_runner_registry_client.py tests/unit/test_runner_heartbeat_dynamic_interval.py tests/unit/test_setup_movie_claim_client.py tests/unit/test_movie_claim_auto_toggle.py tests/unit/test_detail_runner_movie_claim.py tests/unit/test_detail_runner_work_distributor.py -v
```

- [ ] Confirm no production direct field dependencies remain:

```bash
rg -n "state\\.(parsed_links|proxy_ban_html_files|global_proxy_pool|global_request_handler|global_movie_claim_client|global_work_distributor_client|runtime_holder_id|login_total_attempts|login_total_budget|always_bypass_time)" javdb/spider -g '*.py'
```

Expected: output is empty or limited to `javdb/spider/runtime/state.py`.

- [ ] Commit:

```bash
git add javdb/spider/runtime/state.py javdb/spider/runtime/context.py tests/architecture/test_spider_runtime_state_boundaries.py tests/unit/test_spider_runtime_state_facade.py docs/design/architecture/spider-baseline.md javdb/spider/README.md
git commit -m "refactor(runtime): freeze legacy state facade"
```
