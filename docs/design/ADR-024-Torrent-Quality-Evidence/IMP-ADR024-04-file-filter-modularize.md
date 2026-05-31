# IMP-ADR024-04: ADR-024 Phase 1 — Modularize qB Read Helpers

**Status:** Proposed — design-reviewed 2026-05-31 (see Design Review note).

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the reusable, read-only qBittorrent helpers (recent-torrents filter, file-list fetch, metadata-readiness wait) out of `javdb/integrations/qb/file_filter/service.py` into a shared module `javdb/integrations/qb/readonly.py`, so the new evidence collector (IMP-05) reuses the exact same "wheels" the file filter already uses — **with zero behavior change** to the file filter.

**Architecture:** `readonly.py` holds **pure-ish HTTP helpers** parameterized by explicit `base_url` / `proxies` / `verify` / `timeout` (no module globals). `file_filter/service.py` keeps its existing public function names and module-global/proxy-helper wiring, but its bodies **delegate** to `readonly.py`. To preserve the existing tests that monkeypatch `service.get_torrent_files`, the shared `wait_for_metadata_readiness` accepts an injectable `fetch_files` callable, and `service.wait_for_metadata_readiness` passes its own (patchable) `get_torrent_files` wrapper.

**Tech Stack:** Python 3.11, `requests`, pytest, `unittest.mock`.

**Source spec:** [ADR-024](ADR-024-torrent-quality-evidence.md) — operationalizes the grilled decision "modularize QBFileFilter so the evidence collector reuses its wheels."

**Related:** [IMP-ADR024-05](IMP-ADR024-05-evidence-collection.md) (consumer of `readonly.py`).

**Depends on:** Nothing.

**Blocks:** IMP-ADR024-05.

---

## Design Review note (2026-05-31)

A `brainstorming` review compared the extracted helpers against the live
`file_filter/service.py` (`get_recent_torrents` filter loop,
`_recent_metadata_candidates`, `wait_for_metadata_readiness`): the logic is
**faithful** to the originals. One real breakage was found and fixed in this plan:

- **The metadata-wait `time.sleep` moves into `readonly.py`.** The test
  `tests/unit/test_qb_file_filter.py::...test_waits_until_majority_of_recent_torrents_are_ready`
  exercises the real wait loop, patches `service.time.sleep`, and asserts
  `mock_sleep.assert_called_once_with(10)`. If the sleep simply moved to
  `readonly`, that patch would no longer fire (and the test would sleep for real).
  Fix: `readonly.wait_for_metadata_readiness` takes an injectable
  `sleep` callable (default `time.sleep`); `service` passes its own module-level
  `time.sleep`, so the existing patch still catches it and the test stays green
  **unchanged** — mirroring the `fetch_files` injection already in this plan.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `javdb/integrations/qb/readonly.py` | Shared read-only qB helpers (explicit-param, no globals). |
| Create | `tests/unit/test_qb_readonly.py` | Unit tests for the shared helpers. |
| Modify | `javdb/integrations/qb/file_filter/service.py` | Delegate `get_torrent_files` / `get_recent_torrents` (time/category filter) / `wait_for_metadata_readiness` to `readonly.py`. |

## Scope Boundaries

- **No behavior change** to the file filter. The existing
  `tests/unit/test_qb_file_filter.py` must stay green unchanged.
- Do not move `set_file_priority`, `filter_small_files`, `delete_local_file`,
  proxy-helper setup, login, or `print_summary` — only the read helpers move.
- Do not change function signatures of `service.*` public functions.
- No new config keys, no workflow edits (those land in IMP-05).

---

## Task 0 — Capture the baseline

- [ ] **Step 1: Read the existing tests so the contract is known**

Run:

```bash
sed -n '1,200p' tests/unit/test_qb_file_filter.py
```

Note exactly which `service.*` names the tests patch (expected: `get_torrent_files`,
`get_recent_torrents`, `wait_for_metadata_readiness`, `set_file_priority`). The
refactor must keep those names importable and patchable on the `service` module.

- [ ] **Step 2: Run the baseline green**

Run:

```bash
pytest tests/unit/test_qb_file_filter.py -v
```

Expected: PASS. Record the passing count — it must be identical after the refactor.

---

## Task 1 — Create the shared read-only module

**Files:**
- Create: `javdb/integrations/qb/readonly.py`
- Test: `tests/unit/test_qb_readonly.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_qb_readonly.py`:

```python
"""Tests for the shared read-only qB helpers (ADR-024)."""

from __future__ import annotations

from javdb.integrations.qb import readonly


def test_filter_recent_torrents_by_time_and_category():
    now = 1_000_000
    torrents = [
        {"hash": "A", "added_on": now - 10, "category": "Daily Ingestion"},
        {"hash": "B", "added_on": now - 10_000_000, "category": "Daily Ingestion"},  # too old
        {"hash": "C", "added_on": now - 10, "category": "Other"},  # wrong category
    ]
    out = readonly.filter_recent_torrents(
        torrents, days=2, categories=["Daily Ingestion"], now=now
    )
    assert [t["hash"] for t in out] == ["A"]


def test_filter_recent_torrents_no_category_keeps_all_recent():
    now = 1_000_000
    torrents = [
        {"hash": "A", "added_on": now - 10, "category": "X"},
        {"hash": "B", "added_on": now - 10, "category": "Y"},
    ]
    out = readonly.filter_recent_torrents(torrents, days=2, categories=None, now=now)
    assert {t["hash"] for t in out} == {"A", "B"}


def test_get_torrent_files_returns_list_on_200():
    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return [{"name": "v.mp4", "size": 1}]

    class _Session:
        def get(self, *a, **k):
            return _Resp()

    files = readonly.get_torrent_files(
        _Session(), "http://qb", "HASH", proxies=None, verify=True, timeout=5
    )
    assert files == [{"name": "v.mp4", "size": 1}]


def test_get_torrent_files_returns_none_on_error_status():
    class _Resp:
        status_code = 500

    class _Session:
        def get(self, *a, **k):
            return _Resp()

    files = readonly.get_torrent_files(
        _Session(), "http://qb", "HASH", proxies=None, verify=True, timeout=5
    )
    assert files is None


def test_wait_for_metadata_readiness_uses_injected_fetcher():
    now = 1_000_000
    torrents = [{"hash": "A", "added_on": now - 10}]
    calls = {"n": 0}

    def fetch(_hash):
        calls["n"] += 1
        return [{"name": "v.mp4", "size": 10}]  # ready immediately

    summary = readonly.wait_for_metadata_readiness(
        torrents,
        fetch_files=fetch,
        max_wait_seconds=5,
        poll_interval_seconds=1,
        recent_window_seconds=900,
        now=now,
    )
    assert summary["ready"] == 1
    assert summary["pending"] == 0
    assert calls["n"] == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
pytest tests/unit/test_qb_readonly.py -v
```

Expected: FAIL with `ModuleNotFoundError: javdb.integrations.qb.readonly`.

- [ ] **Step 3: Implement `readonly.py`**

Create `javdb/integrations/qb/readonly.py`. This is the verbatim logic lifted from
`file_filter/service.py` (`get_torrent_files`, `_recent_metadata_candidates`,
`wait_for_metadata_readiness`, and the time/category filter inside
`get_recent_torrents`) with module globals replaced by explicit parameters and an
injectable `fetch_files`:

```python
"""Shared read-only qBittorrent helpers (ADR-024).

Extracted from ``file_filter/service.py`` so multiple callers (file filter,
torrent quality evidence collector) reuse the same metadata-wait / file-list /
recent-torrents logic. These helpers take an explicit ``base_url`` and request
parameters instead of reading module globals, so they are reusable and unit
testable. They never mutate qBittorrent state.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_METADATA_WAIT_SECONDS = 90
DEFAULT_METADATA_POLL_INTERVAL_SECONDS = 10
DEFAULT_RECENT_METADATA_WINDOW_SECONDS = 15 * 60


def get_torrent_files(
    session: Any,
    base_url: str,
    torrent_hash: str,
    *,
    proxies: Optional[dict] = None,
    verify: bool = True,
    timeout: int = 30,
) -> Optional[list]:
    """Return the file list for a torrent, or ``None`` on API failure.

    An empty list means metadata is not ready yet (distinct from ``None``).
    """
    files_url = f"{base_url}/api/v2/torrents/files"
    try:
        response = session.get(
            files_url,
            params={"hash": torrent_hash},
            timeout=timeout,
            proxies=proxies,
            verify=verify,
        )
        if response.status_code == 200:
            return response.json()
        logger.warning(
            "Failed to get files for torrent %s: %s", torrent_hash, response.status_code
        )
        return None
    except requests.RequestException as exc:
        logger.error("Error getting files for torrent %s: %s", torrent_hash, exc)
        return None


def filter_recent_torrents(
    torrents: list[dict],
    *,
    days: int = 2,
    categories: Optional[list[str]] = None,
    now: Optional[float] = None,
) -> list[dict]:
    """Filter a torrent list by added-on time window and optional categories.

    Pure: ``now`` is injectable for tests. Mirrors the time/category logic
    formerly inside ``service.get_recent_torrents``.
    """
    base = datetime.fromtimestamp(now) if now is not None else datetime.now()
    cutoff_date = base - timedelta(days=days - 1)
    cutoff_timestamp = int(
        cutoff_date.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    )
    out: list[dict] = []
    for torrent in torrents:
        if torrent.get("added_on", 0) < cutoff_timestamp:
            continue
        if categories and torrent.get("category", "") not in categories:
            continue
        out.append(torrent)
    return out


def recent_metadata_candidates(
    torrents: list[dict],
    *,
    now: Optional[float] = None,
    window_seconds: int = DEFAULT_RECENT_METADATA_WINDOW_SECONDS,
) -> list[dict]:
    """Return recently added torrents worth waiting on for metadata."""
    if now is None:
        now = time.time()
    cutoff = now - max(0, window_seconds)
    candidates: list[dict] = []
    for torrent in torrents:
        if not torrent.get("hash"):
            continue
        try:
            added_on = int(float(torrent.get("added_on") or 0))
        except (TypeError, ValueError):
            continue
        if added_on >= cutoff:
            candidates.append(torrent)
    return candidates


def wait_for_metadata_readiness(
    torrents: list[dict],
    *,
    fetch_files: Callable[[str], Optional[list]],
    max_wait_seconds: int = DEFAULT_METADATA_WAIT_SECONDS,
    poll_interval_seconds: int = DEFAULT_METADATA_POLL_INTERVAL_SECONDS,
    recent_window_seconds: int = DEFAULT_RECENT_METADATA_WINDOW_SECONDS,
    now: Optional[float] = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Poll ``fetch_files(hash)`` until most recent torrents expose metadata.

    ``fetch_files`` is injected so callers control HTTP/proxy concerns and tests
    can stub it. ``sleep`` is injected so the file-filter delegate can pass its
    own module-level ``time.sleep`` (keeping the existing ``service.time.sleep``
    patch effective). Returns the same summary dict shape as the original
    file-filter implementation.
    """
    candidates = recent_metadata_candidates(
        torrents, now=now, window_seconds=recent_window_seconds
    )
    if not candidates or max_wait_seconds <= 0:
        return {
            "checked": len(candidates),
            "ready": 0,
            "pending": 0,
            "api_failures": 0,
            "waited_seconds": 0,
        }

    ready_needed = (len(candidates) // 2) + 1
    deadline = time.monotonic() + max_wait_seconds
    waited_seconds = 0.0

    while True:
        ready = 0
        pending = 0
        api_failures = 0
        for torrent in candidates:
            files = fetch_files(torrent.get("hash", ""))
            if files is None:
                api_failures += 1
            elif len(files) == 0:
                pending += 1
            else:
                ready += 1

        logger.info(
            "Metadata readiness: ready=%d pending=%d api_failures=%d target=%d/%d",
            ready, pending, api_failures, ready_needed, len(candidates),
        )
        if pending == 0 or ready >= ready_needed:
            return {
                "checked": len(candidates),
                "ready": ready,
                "pending": pending,
                "api_failures": api_failures,
                "waited_seconds": int(waited_seconds),
            }

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {
                "checked": len(candidates),
                "ready": ready,
                "pending": pending,
                "api_failures": api_failures,
                "waited_seconds": int(waited_seconds),
            }

        sleep_for = min(max(1, poll_interval_seconds), remaining)
        sleep(sleep_for)
        waited_seconds += sleep_for
```

- [ ] **Step 4: Run the test to verify it passes**

Run:

```bash
pytest tests/unit/test_qb_readonly.py -v
```

Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add javdb/integrations/qb/readonly.py tests/unit/test_qb_readonly.py
git commit -m "feat(qb): extract shared read-only helpers (ADR-024)"
```

---

## Task 2 — Delegate from `file_filter/service.py`

**Files:**
- Modify: `javdb/integrations/qb/file_filter/service.py`

The goal: keep the public function names and signatures identical so
`tests/unit/test_qb_file_filter.py` (which patches `service.get_torrent_files`
etc.) stays green, while the HTTP/filter bodies delegate to `readonly.py`.

- [ ] **Step 1: Import the shared module**

In `javdb/integrations/qb/file_filter/service.py`, after the existing import block
(near line 27, after the `from javdb.integrations.qb.file_filter.result import ...`),
add:

```python
from javdb.integrations.qb import readonly as _qb_readonly
```

- [ ] **Step 2: Delegate `get_torrent_files`**

Replace the body of `get_torrent_files` (currently lines ~333-366) with a
delegation that preserves the signature:

```python
def get_torrent_files(session, torrent_hash, use_proxy=False):
    """Get list of files in a torrent (delegates to shared readonly helper).

    Returns a list on success (possibly empty if metadata not ready) or None
    on API failure.
    """
    return _qb_readonly.get_torrent_files(
        session,
        QB_BASE_URL,
        torrent_hash,
        proxies=get_proxies_dict('qbittorrent', use_proxy),
        verify=QB_VERIFY_TLS,
        timeout=REQUEST_TIMEOUT,
    )
```

- [ ] **Step 3: Delegate the time/category filter inside `get_recent_torrents`**

In `get_recent_torrents` (lines ~256-330), keep the HTTP GET exactly as-is, but
replace the in-line cutoff/category filtering loop (lines ~297-317) with a call to
the shared pure filter. After `torrents = response.json()`, replace the
filtering block with:

```python
            recent_torrents = _qb_readonly.filter_recent_torrents(
                torrents,
                days=days,
                categories=category_filter,
            )
```

Leave the surrounding logging and `return recent_torrents` intact.

- [ ] **Step 4: Delegate `wait_for_metadata_readiness` with an injectable fetcher**

Replace the body of `wait_for_metadata_readiness` (lines ~501-571) — keeping the
exact same signature and defaults — with a delegation that injects the **module's
own** `get_torrent_files` so existing patches of `service.get_torrent_files`
continue to flow:

```python
def wait_for_metadata_readiness(
    session,
    torrents,
    *,
    use_proxy=False,
    max_wait_seconds=QB_FILE_FILTER_METADATA_WAIT_SECONDS,
    poll_interval_seconds=QB_FILE_FILTER_METADATA_POLL_INTERVAL_SECONDS,
    recent_window_seconds=QB_FILE_FILTER_RECENT_METADATA_WINDOW_SECONDS,
):
    """Poll qBittorrent until most newly added torrents expose file metadata.

    Delegates to the shared readonly helper, injecting this module's
    ``get_torrent_files`` (which honours proxy/verify/base-url and stays
    patchable in tests).
    """
    return _qb_readonly.wait_for_metadata_readiness(
        torrents,
        fetch_files=lambda h: get_torrent_files(session, h, use_proxy),
        max_wait_seconds=max_wait_seconds,
        poll_interval_seconds=poll_interval_seconds,
        recent_window_seconds=recent_window_seconds,
        sleep=time.sleep,
    )
```

You may now delete the now-unused `_recent_metadata_candidates` helper (lines
~481-498) **only if** no test imports it. Check first:

```bash
rg -n "_recent_metadata_candidates" tests/
```

If there are no test references, remove it; otherwise leave it and have it
delegate to `_qb_readonly.recent_metadata_candidates`.

- [ ] **Step 5: Run the file-filter tests — must match the Task 0 baseline**

Run:

```bash
pytest tests/unit/test_qb_file_filter.py tests/unit/test_qb_readonly.py -v
```

Expected: PASS, with the file-filter passing count identical to Task 0 Step 2.
If any file-filter test fails, the delegation broke patchability — re-check that
`service.get_torrent_files` is still the name the tests patch and that
`wait_for_metadata_readiness` calls it via the closure above.

- [ ] **Step 6: Run the operations endpoint tests (they patch the package API)**

The package-level `run_file_filter` is consumed by the REST API and patched in
`tests/unit/test_operations_endpoints.py`. Confirm it still imports/works:

```bash
pytest tests/unit/test_operations_endpoints.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add javdb/integrations/qb/file_filter/service.py
git commit -m "refactor(qb): delegate file-filter read helpers to shared module (ADR-024)"
```

---

## Definition of Done

| # | Gate | Check |
|---|------|-------|
| 1 | Shared module works | `pytest tests/unit/test_qb_readonly.py -v` → PASS |
| 2 | File filter unchanged | `pytest tests/unit/test_qb_file_filter.py -v` → PASS, same count as Task 0 |
| 3 | REST API unaffected | `pytest tests/unit/test_operations_endpoints.py -v` → PASS |
| 4 | No duplicated logic | `get_torrent_files` / metadata-wait HTTP logic exists once, in `readonly.py` |
