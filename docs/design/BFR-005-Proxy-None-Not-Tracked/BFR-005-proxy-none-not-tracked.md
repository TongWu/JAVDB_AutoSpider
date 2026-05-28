# BFR-005: Health-weighted proxy selection does not penalize workers that consistently return None

**Status**: Fixed
**Date**: 2026-05-28
**Severity**: High
**Affected**: `javdb/spider/fetch/fetch_engine.py`, `javdb/proxy/ban_manager.py`
**Related**: [Issue #109](https://github.com/TongWu/JAVDB_AutoSpider_CICD/issues/109)

---

## Symptom

DailyIngestion run 26512511043: three workers (Seoul-ARM2, Chuncheon-ARM1, Chuncheon-ARM2) returned `None` on their first task in every phase (index, phase 1, phase 2). Each `None` caused a re-queue costing ~10-20s per phase, ~30-60s total. Despite consistent failures, the final summary showed `available=11/11 · cooldown=0 · banned=0` — these workers were never penalized.

## Root Cause

In `fetch_engine.py:1020-1039`, when `_process_fn` returns `None`:
1. The proxy is added to the task's `failed_proxies` set (task-level tracking)
2. The task is re-queued via `requeue_front()`
3. **No call to `BanManager.add_ban()` or any health-weight penalty**

`BanManager.add_ban()` is only invoked in the `ProxyBannedError` exception handler (`fetch_engine.py:1043-1050`). The `None` return path never interacts with the ban manager or health-weight system.

The task-level `failed_proxies` prevents the *same task* from being re-assigned to the same proxy, but new tasks from the queue can still be dispatched to the failing proxy, repeating the cycle.

## Fix

Add a per-worker consecutive-None counter in FetchEngine's worker loop. After N consecutive `None` returns (default: 2), soft-ban the proxy for the remainder of the session via `BanManager.add_ban()` and route the worker through FetchEngine's existing ban handler so `_banned_proxies`, active-worker counts, sleep rebalance, and all-proxies-banned handling stay consistent.

## Side Effects

Workers that intermittently return `None` (e.g. transient network issue) will be banned sooner. The threshold of 2 consecutive Nones mitigates false positives — a single None still allows retry.

## Follow-Up

- [x] Add per-worker None counter in FetchEngine worker loop
- [x] Trigger soft-ban after N consecutive Nones
- [x] Route soft-ban through FetchEngine's existing ban/rebalance path
- [x] Add unit test for consecutive-None ban behavior
