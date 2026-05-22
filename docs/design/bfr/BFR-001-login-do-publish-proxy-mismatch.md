# BFR-001: Login state DO publish fails with 409 proxy_name_mismatch_with_lease

**Status:** Fixed
**Date:** 2026-05-22
**Severity:** Medium
**Affected:** `javdb/spider/fetch/login_coordinator.py`
**Related:** DO handler `JAVDB_AutoSpider_Proxycoordinator/src/global_login_state.ts:469-482`

---

## Symptom

When the spider falls back to a different proxy during multi-proxy login, the
cross-runner cookie broadcast fails with HTTP 409:

```
13:59:21  ⚠ Session  Failed to publish login state to DO (proxy=Singapore-ARM1): HTTP 409:
  {"error":"proxy_name_mismatch_with_lease","lease_target_proxy_name":"Miraculous Fortress"}
```

The cookie works locally on the runner that logged in, but other runners
cannot reuse it and must each login independently — wasting login budget and
increasing the risk of hitting JavDB's rate limits.

## Root Cause

`_find_and_login_next_worker_with_lease` acquires the DO lease with
`target_proxy_name = hint_proxy_name` (the caller's own proxy). However, both
call sites (`handle_login_required` lines 961 and 1048) always place the hint
proxy in the `exclude` set:

```python
next_wid, parked = self._find_and_login_next_worker_with_lease(
    exclude={worker.proxy_name},        # hint is excluded
    hint_proxy_name=worker.proxy_name,  # hint == excluded proxy
)
```

This guarantees the login succeeds through a **different** proxy. Inside the
iteration, `_login_and_verify` publishes with `proxy_name = actual_proxy`. The
DO's `handlePublish` validation correctly rejects this because
`actual_proxy ≠ lease.target_proxy_name`.

The design flaw is that the lease's `target_proxy_name` was treated as a
"diagnostic hint" but the DO enforces it as a contract. The multi-proxy path
never had a mechanism to update the lease after discovering which proxy
actually succeeded.

The single-proxy path (`_login_and_verify_with_lease`) is unaffected because
hint always equals actual.

## Fix

Scope: Python-side only (`login_coordinator.py`). The DO-side validation is
correct and stays unchanged.

### Change 1: `_login_and_verify` — add `defer_publish` parameter

```python
def _login_and_verify(self, worker, *, defer_publish: bool = False) -> tuple[bool, str | None]:
```

When `defer_publish=True`, skip both `_publish_login_state_to_do` calls (lines
629 and 637). Default `False` preserves backward compatibility for the
single-proxy `_login_and_verify_with_lease` path.

### Change 2: `_find_and_login_next_worker` — return cookie, pass `defer_publish`

Signature changes from `-> int | None` to `-> tuple[int | None, str | None]`.

```python
verified, new_cookie = self._login_and_verify(w, defer_publish=True)
# ...
return w.worker_id, new_cookie
```

### Change 3: `_find_and_login_next_worker_with_lease` — release → re-acquire → publish

After `_find_and_login_next_worker` returns successfully:

1. Release old lease (target = hint proxy)
2. Re-acquire new lease (target = actual proxy) via raw `client.acquire_lease`
   — not `_try_acquire_login_lease`, to avoid cooldown/park side effects
3. Publish cookie → release new lease

On any failure (another runner grabs the lease, network error, etc.), log a
warning and skip publish. **Fail-open**: the cookie already works locally.

New helper method `_reacquire_and_publish` encapsulates steps 2–3.

### Error handling

| Scenario | Behavior |
|----------|----------|
| Another runner grabs lease during race window | `acquired=False` → log warning, skip publish |
| Re-acquire network timeout | `LoginStateUnavailable` → log warning, skip publish |
| Publish itself fails | Existing `_publish_login_state_to_do` warning handles it |
| Re-acquire succeeds but lease expires before publish | DO returns 409 `lease_required` → same warning path |

### Tests

Add to `tests/unit/test_login_coordinator_park.py`:

- `TestDeferPublish` — `defer_publish=True` skips DO publish; default still fires
- `TestFindAndLoginNextWorkerReturnsCookie` — new return type `(worker_id, cookie)`; passes `defer_publish=True`
- `TestFindAndLoginNextWorkerWithLeaseReacquire` — happy path (release → re-acquire → publish → release); re-acquire denied; network error; no DO configured; login failed (no re-acquire)

## Side Effects

None. The single-proxy path (`_login_and_verify_with_lease`) and all external
callers (`handle_login_required`) are unchanged. The `_find_and_login_next_worker`
return type change is internal — the only consumer is
`_find_and_login_next_worker_with_lease`.

## Follow-Up

- [x] Implement the fix (3 changes + tests as described above)
- [ ] Verify on next AdHoc/Daily ingestion run that the 409 no longer appears and cookie is broadcast
