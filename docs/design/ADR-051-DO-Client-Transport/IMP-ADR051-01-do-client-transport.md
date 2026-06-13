# IMP-ADR051-01: Route `lease`/`report` Through `_do_request` & Type the Async Queue — Implementation Plan

> **Status: 🔲 Proposed (2026-06-13).** Single PR; no public-API change. Authored from [ADR-051](ADR-051-do-client-transport-consolidation.md).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Related:** [ADR-051](ADR-051-do-client-transport-consolidation.md) — Phase 1 (the only phase).

**Goal:** Bring `ProxyCoordinatorClient.lease()`/`report()` onto the `BaseDOClient._do_request` seam (gaining the `isinstance(dict)` guard), replace the variable-length async-report tuple with a frozen `AsyncReportEvent` + typed sentinel, and delete the dead `len(item)` tuple-compat — with no change to any public signature or external call site.

**Architecture / approach:** `_do_request` already encodes the exact `CoordinatorUnavailable` mapping the inline blocks duplicate, plus a dict guard. `lease`/`report` keep their method-specific post-processing inline after the call (sibling-consistent). The async queue becomes homogeneous (`Queue[AsyncReportEvent]`); `report_async()`'s signature is unchanged so the four producer call sites are untouched.

**Tech Stack:** Python 3, `pytest`, `dataclasses`, `queue.Queue`, `curl_cffi`/`requests` session (via `_do_request`).

**Verification posture:** Existing `test_proxy_coordinator_client.py` tests patch `c._session.post` and stay valid (routing through `_do_request` still calls `_session.post`); add tests for the newly-gained dict guard and the typed sentinel. No external call site changes.

---

## File Structure

| Path | Action | Responsibility |
| --- | --- | --- |
| `javdb/proxy/coordinator/proxy_coordinator_client.py` | Modify | Add `AsyncReportEvent` frozen dataclass + `ASYNC_QUEUE_SENTINEL` (module level, before the class). Route `lease()`/`report()` through `self._do_request('POST', '/lease' \| '/report', body)`, moving post-processing after the call. Replace `report_async()`'s tuple enqueue with `AsyncReportEvent`. Collapse `_async_report_loop` to `item is ASYNC_QUEUE_SENTINEL` + named-field access; delete the `len(item) > 2/3/4` branches. Annotate the queue `Queue[AsyncReportEvent]`. |
| `javdb/proxy/coordinator/do_client_base.py` | Verify only | `_do_request` error contract + dict guard are exactly what `lease`/`report` need (no change). |
| `tests/unit/test_proxy_coordinator_client.py` | Modify | Existing `patch(c._session.post)` tests stay; **add** (a) non-dict-JSON → `CoordinatorUnavailable` for `lease` and `report` (the newly-gained guard), (b) `AsyncReportEvent` sentinel identity exits `_async_report_loop`, (c) a non-sentinel event is dispatched by named field. |
| `CONTEXT.md` | Modify | Add **DO-client seam** + **AsyncReportEvent** (ADR-051 Domain Language). |

## Task 0: Baseline & push-site enumeration

- [ ] **Step 0.1 — Green baseline:** `pytest tests/unit/test_proxy_coordinator_client.py -q`.
- [ ] **Step 0.2 — Enumerate every async-queue producer and the sentinel:**
  ```bash
  grep -rn "report_async\|_ASYNC_QUEUE_SENTINEL\|\.put(" javdb/proxy/coordinator/proxy_coordinator_client.py
  grep -rn "report_async" javdb apps tests --include="*.py"
  ```
  **Verification gate:** confirm the 4 external `report_async` callers (`sleep.py`, `state.py`, `context.py`, `fetch_engine.py`) + the 3 internal `mark_proxy_*` wrappers; confirm there is exactly one `.put(sentinel)` site and no surviving 2-/4-tuple push site. If a variable-length push exists, STOP and reconcile against ADR-051.

## Task 1: Type the async queue (do before routing — isolates the change)

- [ ] **Step 1.1 — Add `AsyncReportEvent`** frozen dataclass + module-level `ASYNC_QUEUE_SENTINEL`.
- [ ] **Step 1.2 — `report_async()`** enqueues `AsyncReportEvent(...)` (signature unchanged); annotate the queue `Queue[AsyncReportEvent]`.
- [ ] **Step 1.3 — `_async_report_loop`:** `if item is ASYNC_QUEUE_SENTINEL: break`; access `item.proxy_id`/`item.kind`/… by name; **delete** the `len(item) > 2/3/4` branches.

  **Verification gate:** `pytest tests/unit/test_proxy_coordinator_client.py -k "async or report_async or loop" -q` green; a stray tuple would now be a type error.

## Task 2: Route `lease`/`report` through the seam

- [ ] **Step 2.1 — `lease()`:** `data = self._do_request('POST', '/lease', body)`; then the existing health-cache write, `ProxyHealthSnapshot`, `banned_until`/`cf_bypass_until` parsing, `LeaseResult` — on `data`. Remove the inline `try/except` transport block.
- [ ] **Step 2.2 — `report()`:** `data = self._do_request('POST', '/report', body)`; then `penalty_factor`/`recent_event_count` extraction, `ReportResult`. Remove the inline transport block.
- [ ] **Step 2.3 — Remove orphans** the routing creates (unused imports, now-dead local helpers).

  **Verification gate:** `pytest tests/unit/test_proxy_coordinator_client.py -q` green unchanged (the `patch(c._session.post)` tests still intercept); the inline `CoordinatorUnavailable` blocks are gone (`grep -n "CoordinatorUnavailable" proxy_coordinator_client.py` shows only post-processing usages, not transport).

## Task 3: New guard tests + docs

- [ ] **Step 3.1 — Dict-guard tests:** feed a bare list / non-dict JSON to `lease()` and `report()`; assert `CoordinatorUnavailable` (covers the guard `_do_request` adds that the old inline code lacked).
- [ ] **Step 3.2 — Sentinel/event tests:** the loop exits on `ASYNC_QUEUE_SENTINEL`; a real `AsyncReportEvent` is dispatched by field.
- [ ] **Step 3.3 — CONTEXT.md:** add the two terms.

  **Verification gate:** new tests green; `grep -n "DO-client seam\|AsyncReportEvent" CONTEXT.md` non-empty.

## Task 4: Final gates

- [ ] `pytest tests/unit/test_proxy_coordinator_client.py tests/unit -k "coordinator or proxy_client" -q` green.
- [ ] `grep -n "len(item) >" javdb/proxy/coordinator/proxy_coordinator_client.py` → empty (dead compat gone).
- [ ] No public-API diff: `report_async`/`lease`/`report`/`LeaseResult`/`ReportResult` signatures unchanged; the 4 external `report_async` call sites untouched.
- [ ] `ruff check javdb/proxy/coordinator/proxy_coordinator_client.py` clean.

## Rollback

Pure refactor; revert the PR. No data/schema/D1/Rust change; the Worker-side `/lease` and `/report` contracts are untouched.

## Out of Scope

- Promoting async dispatch to `BaseDOClient` (ADR-051 D3).
- `/recommend_proxy` scoring (ADR-023).
- Any public signature or external call-site change.
