# BFR-009: Cross-runner ban dispatch does not fire on the Rust pool/ban-manager path

**Status**: Open
**Date**: 2026-05-30
**Severity**: Medium
**Affected**: `javdb/proxy/ban_manager.py` (`_dispatch_remote_ban`, `set_remote_ban_hook`), `javdb/proxy/pool.py` (Python `ProxyPool.ban_proxy` / drain), `javdb/spider/runtime/state.py:722`, `javdb/spider/runtime/context.py:880` (hook registration), `javdb/rust_core/src/proxy/{pool,ban_manager}.rs`
**Related**: [ADR-041](../ADR-041-Rust-Fallback-Policy/ADR-041-rust-fallback-policy.md) (surfaced this during Task 4 — removed the last Python callers of the dispatcher), [ADR-023](../ADR-023-Proxy-Recommendation-Policy/ADR-023-proxy-recommendation-policy.md) (proxy coordination), CONTEXT.md → "Signal" (`ban_proxy`)

---

## Symptom

Cross-runner ban propagation does not happen in production. When one runner bans a proxy, peer runners are **not** told via the coordinator (Worker Durable Object); they keep selecting the banned proxy until they independently hit failures.

The wiring exists but never fires on the production path:

- The remote-ban hook **is registered** at runtime — `set_remote_ban_hook(client.mark_proxy_banned)` (`javdb/spider/runtime/state.py:722`, `javdb/spider/runtime/context.py:880`).
- The hook is **only invoked** by `_dispatch_remote_ban(...)`, whose **only call sites are in Python code**: the Python `ProxyPool` (`javdb/proxy/pool.py:411`, `:485`) and the Python `ProxyBanManager.add_ban` (`javdb/proxy/ban_manager.py:162`).
- **Production uses the Rust pool + Rust ban manager** (`create_proxy_pool_from_config` / `get_ban_manager()` return the Rust implementations when the wheel is installed, which it always is in Docker/CI). The Rust `ban_proxy` / `add_ban` are inside the PyO3 extension and **cannot reach** the Python `_dispatch_remote_ban` hook.

Net effect: every production ban is recorded **locally only**; the `client.mark_proxy_banned(name)` → Worker DO broadcast is never triggered.

## Root Cause

The cross-runner ban dispatch (P1-A) was implemented as a **Python module-level hook** fired from inside the Python pool/ban-manager. When the Rust pool/ban-manager became the production default, the dispatch call sites were **not** ported into the Rust extension, and no Rust→Python ban callback was added. The Rust extension has no mechanism (no `set_ban_callback`-style setter) to notify Python when a ban is recorded.

This is a latent **pre-existing** gap — it predates [ADR-041](../ADR-041-Rust-Fallback-Policy/ADR-041-rust-fallback-policy.md). ADR-041 only made it **visible**: removing the Python `ProxyPool` / `ProxyBanManager` (now Rust-Required) deletes the last three callers of `_dispatch_remote_ban`, so the dispatcher and the registered hook become unambiguously dead rather than dead-only-in-production.

The `remove_ban` docstring in `ban_manager.py` already half-acknowledges the asymmetry: it notes the Rust manager "can't reach the Python `_dispatch_remote_unban` hook from inside the extension" and defers unban dispatch to `ProxyPool.unban_proxy` — but the Rust pool's `ban_proxy` has the same limitation and no equivalent dispatch.

## Impact

- **Multi-runner runs lose cross-runner ban coordination.** A proxy that one runner has determined is bad (CF-challenged, blocked) is not broadcast; peers waste requests on it until each independently bans it. This is an efficiency/coordination degradation, not data corruption.
- **Single-runner runs are unaffected** — local bans work correctly via the Rust ban manager.
- The coordinator's `mark_proxy_banned` endpoint and the `ban_proxy` Signal (CONTEXT.md) are effectively under-fed from the Python side.

## Fix

Not yet implemented — tracked here, deferred out of ADR-041 (which is a fallback-policy change, not a coordination change). Candidate approaches (decide in the follow-up):

1. **Rust→Python ban callback.** Add a `set_ban_dispatch(callback)` to the Rust pool/ban manager, invoked on each *newly recorded* ban (mirroring the Python `newly_banned` dedup), wired from the same runtime setup that registers `set_remote_ban_hook`.
2. **Python-side dispatch at the call site.** Wrap the production ban entry points (`get_ban_manager().add_ban(...)`, `pool.ban_proxy(...)`) in a thin Python helper that records via Rust *and* fires `_dispatch_remote_ban` — keeping the dispatcher Python-side and not requiring a Rust change.
3. **Observer/delta poll.** Have the coordinator-integration layer diff the Rust ban manager's banned set against the last-dispatched set and push deltas.

Approach 2 is the smallest change and keeps `_dispatch_remote_ban` / `set_remote_ban_hook` meaningful; approach 1 is the cleanest long-term but touches the Rust crate.

## Follow-Up

- [ ] Decide the dispatch approach (1/2/3) and implement.
- [ ] Add a test that a ban recorded through the **production** entry point (`get_ban_manager().add_ban` / `create_proxy_pool_from_config(...).ban_proxy`) fires the registered remote hook exactly once per newly-banned proxy.
- [ ] Re-evaluate the `_dispatch_remote_ban` / `set_remote_ban_hook` / `set_remote_unban_hook` surface once the dispatch path is real (ADR-041 keeps these symbols but their only callers were the now-removed Python pool/manager).
