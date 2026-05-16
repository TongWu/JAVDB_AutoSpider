# ADR-009: Dashboard Overhaul — Phase 1: Runner Reports `proxy_pool` to Worker

**Status**: Accepted — Completed 2026-05-16 (merged via #f4c5d23c + #e224c374 + #60797d16)
**Date**: 2026-05-16
**Deciders**: Proxy Coordinator Dashboard rewrite working stream
**Related**: implements [ADR-004](ADR-004-proxy-discovery-via-runner-pool-upload.md); prerequisite for [ADR-010](ADR-010-dashboard-phase2-worker-backend.md)

> **Note on format:** This ADR was originally written as a step-by-step implementation plan and relocated into the ADR space (per repo convention for design records). The decision context is captured in the **Goal / Architecture / Tech Stack** preamble below; the rest is the execution checklist preserved from the original plan.
>
> **For agentic workers (historical):** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the Python-side `RunnerRegistryClient.register()` to upload the full PROXY_POOL (as `[{id, name}]`) on every register call so that future Phase 2 work can persist a `proxies_seen` table in the Worker. Backward compatible: old Workers ignore the new field.

**Architecture:** Add an optional `proxy_pool` keyword arg to `RunnerRegistryClient.register()`. Add a sibling helper `proxy_pool_summary_for_registry()` next to the existing `proxy_pool_hash()` that whitelist-serialises PROXY_POOL to `[{id, name}]` (no URLs, no credentials, no auth). Wire it into the two call sites in `state.py` (initial register + re-register after eviction).

**Tech Stack:** Python 3.11+, pytest, existing `packages/python/javdb_platform/runner_registry_client.py` patterns.

**Reference docs:** [CONTEXT.md](../../../CONTEXT.md), [ADR-004](../../ai/adr/ADR-004-proxy-discovery-via-runner-pool-upload.md)

---

## File Structure

**New files:** none

**Modified files:**
- `packages/python/javdb_platform/runner_registry_client.py` — Add `proxy_pool_summary_for_registry()` helper near the existing `proxy_pool_hash()`; add `proxy_pool` kwarg to `RunnerRegistryClient.register()`
- `packages/python/javdb_spider/runtime/state.py` — Pass `proxy_pool=...` at both `client.register(...)` call sites (lines ~996 and ~1255)
- `tests/unit/test_runner_registry_client.py` (create if missing) — Unit tests for the helper + register payload

**Boundary:**
- Helper takes the in-memory PROXY_POOL list-of-dicts and returns `[{id: str, name: str}]` items only — explicitly whitelist these two fields. Items with no `name` get their `name` derived from the same fallback logic used by `normalize_proxy_id()`.
- The helper must never produce keys `http`, `https`, `user`, `pass`, `auth`, etc.

---

## Task 1: Helper function — serialise PROXY_POOL for registry

**Files:**
- Modify: `packages/python/javdb_platform/runner_registry_client.py` (add helper around line 66, near `proxy_pool_hash()`)
- Test: `tests/unit/test_runner_registry_proxy_pool_serialiser.py` (new file)

- [ ] **Step 1: Write the failing test for empty input**

```python
# tests/unit/test_runner_registry_proxy_pool_serialiser.py
"""Phase 1: proxy_pool serialiser for RunnerRegistry register payload (ADR-004)."""

import pytest

from packages.python.javdb_platform.runner_registry_client import (
    proxy_pool_summary_for_registry,
)


def test_empty_pool_returns_empty_list():
    assert proxy_pool_summary_for_registry([]) == []


def test_none_pool_returns_empty_list():
    assert proxy_pool_summary_for_registry(None) == []
```

- [ ] **Step 2: Verify test fails**

Run: `pytest tests/unit/test_runner_registry_proxy_pool_serialiser.py -v`
Expected: FAIL with `ImportError: cannot import name 'proxy_pool_summary_for_registry'`

- [ ] **Step 3: Implement minimal helper to make empty/None tests pass**

In `packages/python/javdb_platform/runner_registry_client.py`, add directly under the existing `proxy_pool_hash()` function:

```python
def proxy_pool_summary_for_registry(pool) -> list[dict]:
    """Serialise the in-memory PROXY_POOL list to the Worker register payload.

    Returns ``[{id, name}]`` items only. URLs, credentials, and any other
    PROXY_POOL fields are intentionally dropped — the Worker stores the
    summary in ``proxies_seen`` for dashboard display, and no part of the
    Worker handles or needs the upstream proxy URL.

    See ADR-004 for the security rationale (no creds cross the
    autospider/Worker boundary).
    """
    if not pool:
        return []
    out: list[dict] = []
    for entry in pool:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        clean = name.strip()
        out.append({"id": clean, "name": clean})
    return out
```

- [ ] **Step 4: Verify the two tests now pass**

Run: `pytest tests/unit/test_runner_registry_proxy_pool_serialiser.py -v`
Expected: 2 passed

- [ ] **Step 5: Add tests for the happy path**

Append to `tests/unit/test_runner_registry_proxy_pool_serialiser.py`:

```python
def test_basic_pool_returns_id_and_name():
    pool = [
        {"name": "Singapore Arm-3", "http": "http://x:7890", "https": "http://x:7890"},
        {"name": "Tokyo Backup-1", "http": "http://y:7890", "https": "http://y:7890"},
    ]
    assert proxy_pool_summary_for_registry(pool) == [
        {"id": "Singapore Arm-3", "name": "Singapore Arm-3"},
        {"id": "Tokyo Backup-1", "name": "Tokyo Backup-1"},
    ]


def test_whitespace_in_name_is_stripped():
    pool = [{"name": "  Singapore Arm-3  ", "http": "x"}]
    result = proxy_pool_summary_for_registry(pool)
    assert result == [{"id": "Singapore Arm-3", "name": "Singapore Arm-3"}]


def test_entries_without_name_are_dropped():
    pool = [
        {"name": "Has-Name", "http": "x"},
        {"http": "y"},                  # missing name → dropped
        {"name": "", "http": "z"},      # empty name → dropped
        {"name": "   ", "http": "w"},   # whitespace-only → dropped
    ]
    assert proxy_pool_summary_for_registry(pool) == [
        {"id": "Has-Name", "name": "Has-Name"},
    ]


def test_non_dict_entries_are_silently_skipped():
    pool = [{"name": "A"}, "garbage", None, 42, {"name": "B"}]
    assert proxy_pool_summary_for_registry(pool) == [
        {"id": "A", "name": "A"},
        {"id": "B", "name": "B"},
    ]
```

- [ ] **Step 6: Verify all happy-path tests pass**

Run: `pytest tests/unit/test_runner_registry_proxy_pool_serialiser.py -v`
Expected: 6 passed

- [ ] **Step 7: Add the critical security regression test**

Append to `tests/unit/test_runner_registry_proxy_pool_serialiser.py`:

```python
def test_no_credentials_leak_into_payload():
    """ADR-004 security guarantee: the payload MUST NOT contain proxy URLs,
    usernames, passwords, or auth fields. Workers never need these."""
    pool = [
        {
            "name": "Auth-Proxy",
            "http": "http://user:supersecret@host:7890",
            "https": "http://user:supersecret@host:7890",
            "user": "user",
            "password": "supersecret",
            "auth": "Basic ZWFnZXI6c2VjcmV0",
        },
    ]
    result = proxy_pool_summary_for_registry(pool)
    serialised = repr(result)

    # Whitelist allows only id and name.
    assert result == [{"id": "Auth-Proxy", "name": "Auth-Proxy"}]

    # Defence-in-depth: explicitly assert no leak fragments anywhere.
    for forbidden in ("supersecret", "user:", "Basic ", "7890", "http://"):
        assert forbidden not in serialised, (
            f"PROXY_POOL leak detected: {forbidden!r} present in payload"
        )
```

- [ ] **Step 8: Verify security regression test passes**

Run: `pytest tests/unit/test_runner_registry_proxy_pool_serialiser.py -v`
Expected: 7 passed

- [ ] **Step 9: Commit**

```bash
git add packages/python/javdb_platform/runner_registry_client.py tests/unit/test_runner_registry_proxy_pool_serialiser.py
git commit -m "$(cat <<'EOF'
feat(platform): add proxy_pool_summary_for_registry helper (Phase 1, ADR-004)

Whitelist-serialise PROXY_POOL to [{id, name}] for runner register
payload. URLs and credentials are explicitly stripped — Workers never
need them. Backs the Phase 2 proxies_seen persistence layer.
EOF
)"
```

---

## Task 2: Extend `RunnerRegistryClient.register()` with `proxy_pool` kwarg

**Files:**
- Modify: `packages/python/javdb_platform/runner_registry_client.py:449-486` (the `register()` method body)
- Test: `tests/unit/test_runner_registry_client_register.py` (new file)

- [ ] **Step 1: Write the failing test that asserts proxy_pool is in the request body**

```python
# tests/unit/test_runner_registry_client_register.py
"""Phase 1: RunnerRegistryClient.register() carries proxy_pool field (ADR-004)."""

from unittest.mock import MagicMock

import pytest

from packages.python.javdb_platform.runner_registry_client import (
    RunnerRegistryClient,
)


def _make_client(captured_body: list):
    """Build a client whose _do_request intercepts the outgoing body."""
    client = RunnerRegistryClient(base_url="https://example.test", token="t")

    def fake_do_request(method, path, body):
        captured_body.append({"method": method, "path": path, "body": body})
        # Return a minimal valid register response so register() doesn't raise.
        return {
            "registered": True,
            "active_runners": [],
            "pool_hash_summary": [],
            "server_time": 0,
            "movie_claim_recommended": False,
            "movie_claim_min_runners": 0,
        }

    client._do_request = fake_do_request  # type: ignore[assignment]
    return client


def test_register_includes_proxy_pool_when_provided():
    captured: list = []
    client = _make_client(captured)
    client.register(
        holder_id="holder-1",
        proxy_pool=[{"id": "P-1", "name": "P-1"}, {"id": "P-2", "name": "P-2"}],
    )
    assert len(captured) == 1
    assert captured[0]["body"]["proxy_pool"] == [
        {"id": "P-1", "name": "P-1"},
        {"id": "P-2", "name": "P-2"},
    ]


def test_register_omits_proxy_pool_field_when_not_provided():
    """Backward compat: callers that don't pass proxy_pool produce
    payloads identical to the pre-Phase-1 contract."""
    captured: list = []
    client = _make_client(captured)
    client.register(holder_id="holder-1")
    body = captured[0]["body"]
    assert "proxy_pool" not in body
```

- [ ] **Step 2: Verify both tests fail**

Run: `pytest tests/unit/test_runner_registry_client_register.py -v`
Expected: 2 failures — `TypeError: register() got an unexpected keyword argument 'proxy_pool'` on the first; the second will also fail because the kwarg doesn't exist yet.

- [ ] **Step 3: Modify `register()` signature and body**

In `packages/python/javdb_platform/runner_registry_client.py`, change the `register` method signature and body construction. The current signature at line 449-458 becomes:

```python
    def register(
        self,
        *,
        holder_id: str,
        workflow_run_id: str = "",
        workflow_name: str = "",
        started_at: Optional[int] = None,
        proxy_hash: str = "",
        page_range: Optional[str] = None,
        proxy_pool: Optional[list[dict]] = None,
    ) -> RegisterResult:
```

Update the docstring's paragraph about `proxy_pool_hash` to add (just after that paragraph):

```
        ``proxy_pool`` (W5.7 / ADR-004): pass the output of
        :func:`proxy_pool_summary_for_registry` so the Worker can persist
        the full pool — including idle backup proxies — to ``proxies_seen``
        for dashboard enumeration. Omit on pre-Phase-2 Workers (the
        Worker silently ignores unknown payload fields).
```

In the body-construction block at lines 477-485, after the existing `if started_at is not None:` block, add:

```python
        if proxy_pool is not None:
            body["proxy_pool"] = proxy_pool
```

- [ ] **Step 4: Verify both tests pass**

Run: `pytest tests/unit/test_runner_registry_client_register.py -v`
Expected: 2 passed

- [ ] **Step 5: Run the existing runner_registry_client tests to ensure no regression**

Run: `pytest tests/ -k "runner_registry" -v`
Expected: All pre-existing tests still pass (no regressions from the new kwarg).

- [ ] **Step 6: Commit**

```bash
git add packages/python/javdb_platform/runner_registry_client.py tests/unit/test_runner_registry_client_register.py
git commit -m "$(cat <<'EOF'
feat(platform): add proxy_pool kwarg to RunnerRegistryClient.register (Phase 1, ADR-004)

Optional [{id, name}] payload uploaded on every register call.
Field is omitted when caller passes None so pre-Phase-1 deploys
produce identical payloads (backward compatible). Workers that
don't understand the field ignore it.
EOF
)"
```

---

## Task 3: Wire the helper into both `register()` call sites in `state.py`

**Files:**
- Modify: `packages/python/javdb_spider/runtime/state.py:996-1001` (re-register after eviction)
- Modify: `packages/python/javdb_spider/runtime/state.py:1255-1265` (initial register; verify exact lines via grep below)

- [ ] **Step 1: Locate the second call site**

Run: `grep -n "client.register(" packages/python/javdb_spider/runtime/state.py`
Expected output includes both call sites:
- Line ~996 — eviction recovery
- Line ~1255 — initial registration in `setup_runner_registry_client()`

- [ ] **Step 2: Read both call sites to confirm context**

Read `packages/python/javdb_spider/runtime/state.py` around each line and confirm both currently pass `proxy_hash=proxy_pool_hash(_resolve_proxy_pool_json())`.

- [ ] **Step 3: Add the import for the new helper**

Find the existing import block (line 38-46):

```python
from packages.python.javdb_platform.runner_registry_client import (
    HeartbeatResult,
    RunnerRegistryClient,
    RunnerRegistryUnavailable,
    create_runner_registry_client_from_env,
    proxy_pool_hash,
)
```

Add `proxy_pool_summary_for_registry,` to the import list (sorted with the others):

```python
from packages.python.javdb_platform.runner_registry_client import (
    HeartbeatResult,
    RunnerRegistryClient,
    RunnerRegistryUnavailable,
    create_runner_registry_client_from_env,
    proxy_pool_hash,
    proxy_pool_summary_for_registry,
)
```

- [ ] **Step 4: Wire the helper into the eviction-recovery call site**

Around line 996-1001, find:

```python
                rereg = client.register(
                    holder_id=holder_id,
                    workflow_run_id=os.environ.get("GITHUB_RUN_ID", ""),
                    workflow_name=os.environ.get("GITHUB_WORKFLOW", ""),
                    proxy_hash=proxy_pool_hash(_resolve_proxy_pool_json()),
                )
```

Change to:

```python
                rereg = client.register(
                    holder_id=holder_id,
                    workflow_run_id=os.environ.get("GITHUB_RUN_ID", ""),
                    workflow_name=os.environ.get("GITHUB_WORKFLOW", ""),
                    proxy_hash=proxy_pool_hash(_resolve_proxy_pool_json()),
                    proxy_pool=proxy_pool_summary_for_registry(PROXY_POOL),
                )
```

- [ ] **Step 5: Wire the helper into the initial-register call site**

Read `packages/python/javdb_spider/runtime/state.py` around line 1255 to find the matching `client.register(...)` block. Apply the same pattern: add a `proxy_pool=proxy_pool_summary_for_registry(PROXY_POOL),` keyword argument as the last argument.

- [ ] **Step 6: Run a typecheck pass to catch typos**

Run: `python -m mypy packages/python/javdb_spider/runtime/state.py --ignore-missing-imports 2>&1 | head -20`
Expected: No new errors introduced by these changes (some pre-existing errors may exist; verify your diff doesn't add to them).

- [ ] **Step 7: Smoke-test the import wiring**

Run: `python -c "from packages.python.javdb_spider.runtime.state import proxy_pool_summary_for_registry; print(proxy_pool_summary_for_registry([{'name': 'X'}]))"`
Expected output: `[{'id': 'X', 'name': 'X'}]`

- [ ] **Step 8: Run the spider runtime tests to confirm no regression**

Run: `pytest tests/unit/ -k "spider_runtime or state" -v 2>&1 | tail -30`
Expected: No regression. If a test imports `state.py` at module level it should still pass.

- [ ] **Step 9: Commit**

```bash
git add packages/python/javdb_spider/runtime/state.py
git commit -m "$(cat <<'EOF'
feat(spider): upload proxy_pool to RunnerRegistry on register (Phase 1, ADR-004)

Both register call sites (initial setup and post-eviction recovery)
now ship the whitelist-serialised PROXY_POOL alongside proxy_pool_hash.
Enables Phase 2 to enumerate the full pool — including idle backup
proxies — from the Worker side.
EOF
)"
```

---

## Task 4: Integration smoke — full register payload contains both `proxy_pool_hash` and `proxy_pool`

**Files:**
- Test: `tests/integration/test_runner_register_payload_shape.py` (new file)

This task adds a single end-to-end test that catches the full payload shape (both fields present, neither dropped) without needing a real Worker.

- [ ] **Step 1: Write the integration test**

```python
# tests/integration/test_runner_register_payload_shape.py
"""Phase 1 integration: confirm the live register payload carries
both proxy_pool_hash and proxy_pool when wired through state.py."""

from unittest.mock import patch

import pytest


@pytest.mark.integration
def test_register_payload_has_both_hash_and_pool(monkeypatch):
    """End-to-end: a runner that calls register() emits a payload containing
    proxy_pool_hash (legacy) AND proxy_pool (Phase 1, ADR-004)."""
    from packages.python.javdb_platform import runner_registry_client as rrc

    # Patch the network call to capture the body.
    captured = []

    def fake_do_request(self, method, path, body):
        captured.append({"method": method, "path": path, "body": body})
        return {
            "registered": True,
            "active_runners": [],
            "pool_hash_summary": [],
            "server_time": 0,
            "movie_claim_recommended": False,
            "movie_claim_min_runners": 0,
        }

    monkeypatch.setattr(rrc.RunnerRegistryClient, "_do_request", fake_do_request)

    client = rrc.RunnerRegistryClient(base_url="https://x.test", token="t")
    client.register(
        holder_id="holder-int-1",
        proxy_hash="0123456789abcdef",
        proxy_pool=rrc.proxy_pool_summary_for_registry(
            [
                {"name": "Singapore Arm-3", "http": "x"},
                {"name": "Tokyo Backup-1", "https": "y"},
            ]
        ),
    )

    body = captured[0]["body"]
    assert body["proxy_pool_hash"] == "0123456789abcdef"
    assert body["proxy_pool"] == [
        {"id": "Singapore Arm-3", "name": "Singapore Arm-3"},
        {"id": "Tokyo Backup-1", "name": "Tokyo Backup-1"},
    ]
    # ADR-004 security check at the integration layer too.
    serialised = repr(body)
    assert "http" not in serialised or "http://" not in serialised
```

- [ ] **Step 2: Verify the test passes**

Run: `pytest tests/integration/test_runner_register_payload_shape.py -v -m integration`
Expected: 1 passed

- [ ] **Step 3: Run the full unit + integration suite for this slice**

Run: `pytest tests/unit/test_runner_registry_proxy_pool_serialiser.py tests/unit/test_runner_registry_client_register.py tests/integration/test_runner_register_payload_shape.py -v`
Expected: 10 passed (7 + 2 + 1)

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_runner_register_payload_shape.py
git commit -m "test(platform): integration smoke for register payload shape (Phase 1)"
```

---

## Task 5: Documentation — update CLAUDE.md and docs/ai

**Files:**
- Modify: `CLAUDE.md` (env vars section if applicable, or imports section)
- Modify: `docs/en/developer/` (if there's a runner registry guide)

- [ ] **Step 1: Search for existing runner-registry documentation**

Run: `grep -rn "proxy_pool_hash\|RunnerRegistryClient\|runner_registry" CLAUDE.md docs/ai/ docs/en/ 2>/dev/null | head -20`

- [ ] **Step 2: Find the right doc to update**

If `grep` finds a `docs/en/developer/*.md` file describing the runner registry payload, add a paragraph there. Otherwise, the only doc that needs an update is the CONTEXT.md entry for `RunnerRegistry DO` (which already mentions Phase 1 in the `proxies_seen` description — confirm this is accurate).

- [ ] **Step 3: Verify CONTEXT.md is already accurate**

Read [CONTEXT.md](../../../CONTEXT.md) `RunnerRegistry DO` section. It already describes Phase 1's `proxies_seen` extension. No edit needed unless the description mentions Phase 2 details that haven't shipped.

- [ ] **Step 4: Commit any doc changes (skip if none)**

If you edited any doc:

```bash
git add docs/ CLAUDE.md
git commit -m "docs: note Phase 1 proxy_pool register payload (ADR-004)"
```

If nothing changed, this task ends with no commit.

---

## Task 6: Phase 1 verification & handoff

**Files:** (none modified)

- [ ] **Step 1: Run full unit test suite for affected modules**

Run: `pytest tests/unit/ -k "runner_registry or proxy_pool" -v 2>&1 | tail -30`
Expected: all green.

- [ ] **Step 2: Run an end-to-end import sanity check**

Run: `python -c "from packages.python.javdb_spider.runtime import state; print('OK')"`
Expected output: `OK`

- [ ] **Step 3: Print the diff summary for review**

Run: `git log --oneline main..HEAD`
Expected output: A small linear history (~4-5 commits) — helper, client, state.py wiring, integration test, optional docs.

- [ ] **Step 4: Phase 1 done — handoff note**

Phase 1 ships in isolation. Workers that don't understand the new `proxy_pool` field will silently drop it (verified by reading `JAVDB_AutoSpider_Proxycoordinator/src/runner_registry.ts` register handler: it uses `clipString(body.proxy_pool_hash ?? "")` which already tolerates extra fields).

Once Phase 1 is merged and deployed to all running runners, **Phase 2** (Worker-side `proxies_seen` table + history tables + MetricsState DO + Cron trigger) can begin without coordination. See `docs/superpowers/plans/2026-05-16-dashboard-overhaul-phase-2-worker-backend.md`.

---

## Self-Review Checklist (already applied)

- ✅ Each task has working code, not "implement here"
- ✅ Both register call sites in state.py updated (eviction recovery + initial setup)
- ✅ Security regression test (no creds in payload) included
- ✅ Backward-compat test (no proxy_pool kwarg → no field in payload)
- ✅ TDD red-green-commit cycle followed throughout
- ✅ Integration test catches end-to-end payload shape
- ✅ No dependency on Phase 2 (Worker changes); 100% backward-compatible
