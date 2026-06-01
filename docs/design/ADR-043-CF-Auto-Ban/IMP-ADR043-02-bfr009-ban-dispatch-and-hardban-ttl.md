# IMP-ADR043-02: Fix BFR-009 Ban Dispatch + Hard-Ban DO Sharing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-043](ADR-043-cf-persistent-failure-auto-ban.md) (D8/D9/D10), fixes [BFR-009](../BFR-009-Rust-Pool-Cross-Runner-Ban-Dispatch/BFR-009-rust-pool-ban-dispatch.md). Phase 1 is [IMP-ADR043-01](IMP-ADR043-01-cf-auto-ban-worker.md).

**Goal:** Make every production proxy ban (CF-threshold, JavDB hard ban, and Rust-internal auto-drain) publish to the coordinator so peers skip it, with the DO ban TTL chosen by *cause* — JavDB hard bans last 8 days, CF bans 6 h, others 3 days.

**Architecture:** Approach 1 (per BFR-009): add a Rust→Python ban-dispatch callback on `RustProxyBanManager.add_ban` (mirroring the existing `health_provider` PyObject-callback pattern), so *every* newly-recorded ban — including the Rust-internal drain at `pool.rs:480` that no Python entry point can see — fires the Python dispatcher. The dispatcher forwards `(proxy_name, reason)` to `mark_proxy_banned`; the Worker maps the reason → TTL (new `HARD_BAN_TTL_MS`).

**Tech Stack:** Rust (PyO3 / maturin), Python (pytest), TypeScript (Cloudflare DO / vitest).

**Repos / working directory:**
- **Tasks 1–4** (Rust + Python) run in the **CICD repo** (this repo). Rebuild the Rust wheel after Rust edits: `cd javdb/rust_core && maturin develop --release && cd ../../..`.
- **Task 5** (Worker) runs in the **coordinator repo** [`TongWu/JAVDB_AutoSpider_Proxycoordinator`](https://github.com/TongWu/JAVDB_AutoSpider_Proxycoordinator).
- **Task 6** updates docs (CICD repo) + BFR-009 status.

**Build/test commands:**
- Rust: `cd javdb/rust_core && cargo build && maturin develop --release`
- Python: `pytest tests/unit/test_proxy_ban_manager.py tests/unit/test_proxy_coordinator_client.py -v`
- Worker: `npm test` (in coordinator repo)

---

## File Structure

| File (repo) | Responsibility | Change |
| --- | --- | --- |
| `javdb/rust_core/src/proxy/ban_manager.rs` (CICD) | Ban record + dispatch | Add `ban_dispatch` callback field, `set_ban_dispatch_callback`, `reason` on `add_ban`, fire on new ban |
| `javdb/rust_core/src/proxy/pool.rs` (CICD) | Pool ban sites | Thread `reason` through `ban_proxy` + the internal drain (`mark_failure_and_switch`) |
| `javdb/proxy/ban_manager.py` (CICD) | Python dispatch glue | `_dispatch_remote_ban(name, reason)`, `set_remote_ban_hook` arity, register Rust callback |
| `javdb/proxy/coordinator/proxy_coordinator_client.py` (CICD) | Coordinator client | Let `mark_proxy_banned` omit `ttl_ms` so the Worker decides by cause |
| `javdb/spider/runtime/context.py` + `state.py` (CICD) | Runtime wiring | Register the Rust ban-dispatch callback alongside the hook |
| `javdb/infra/request.py` + `javdb/spider/fetch/fetch_engine.py` (CICD) | Ban catch sites | Pass `ProxyBannedError.reason` into `ban_proxy(...)` |
| `src/types.ts` + `src/proxy_coordinator.ts` + `wrangler.toml` (coordinator) | Worker TTL mapping | Add `HARD_BAN_TTL_MS`, map ban `reason` → TTL |
| `docs/handbook/{en,zh}/self-hoster/proxy-coordinator.md` (CICD) | Docs | Document `HARD_BAN_TTL_MS` |
| `docs/design/BFR-009-.../BFR-009-*.md` + `.zh.md` (CICD) | BFR lifecycle | Status → Fixed |

**Cause vocabulary (single source of truth, used end-to-end):**

| Cause string (in `ProxyBannedError.reason` / dispatch) | Match rule | DO TTL |
| --- | --- | --- |
| `ban page detected` / `ban page detected via CF bypass` | contains `"ban page"` | `HARD_BAN_TTL_MS` (8 d) |
| `CF bypass failed N consecutive times` | contains `"CF bypass failed"` | `CF_BAN_TTL_MS` (6 h, from IMP-01) |
| `rust_auto_drain` (internal failure-count drain) | exact | `BAN_TTL_MS` (3 d) |
| anything else / absent | — | `BAN_TTL_MS` (3 d) |

---

## Task 1: Rust — ban-dispatch callback on `ProxyBanManager`

**Files:**
- Modify: `javdb/rust_core/src/proxy/ban_manager.rs`

- [ ] **Step 1: Add the callback field + `reason` to the ban record**

In `struct BanManagerInner` (lines 19-21), add the callback store (mirrors `pool.rs` `health_provider`):

```rust
struct BanManagerInner {
    banned_proxies: Mutex<HashMap<String, ProxyBanRecord>>,
    // BFR-009 / ADR-043 D8 — Python callback fired once per NEWLY recorded ban.
    ban_dispatch: Mutex<Option<PyObject>>,
}
```

In `ProxyBanRecord` (lines 12-17) add `pub reason: Option<String>,`. Update `ProxyBanManager::new()` to initialise `ban_dispatch: Mutex::new(None)` in the `BanManagerInner { ... }` literal.

- [ ] **Step 2: Add the setter (mirror `set_health_provider`)**

In the `#[pymethods] impl ProxyBanManager` block, after `remove_ban` (line ~85), add:

```rust
/// BFR-009 / ADR-043 D8 — register a Python callback invoked once per newly
/// recorded ban with ``(proxy_name: str, reason: Optional[str])``. Pass
/// ``None`` to clear. The callback MUST be fast and non-reentrant (it is
/// fire-and-forget enqueue on the Python side).
#[pyo3(signature = (callback=None))]
pub fn set_ban_dispatch_callback(&self, callback: Option<PyObject>) {
    let mut cb = self.inner.ban_dispatch.lock();
    *cb = callback;
}
```

- [ ] **Step 3: Fire the callback on a NEW ban inside `add_ban`**

Rewrite `add_ban` (lines 49-68) to take an optional `reason`, detect the new-ban case, drop the map lock BEFORE calling Python (avoid holding the mutex across the GIL), then invoke the callback:

```rust
#[pyo3(signature = (proxy_name, proxy_url=None, reason=None))]
pub fn add_ban(&self, proxy_name: &str, proxy_url: Option<String>, reason: Option<String>) {
    let newly_banned = {
        let mut banned = self.inner.banned_proxies.lock();
        if banned.contains_key(proxy_name) {
            debug!("Proxy '{}' is already banned this session, not updating", proxy_name);
            false
        } else {
            banned.insert(
                proxy_name.to_string(),
                ProxyBanRecord {
                    proxy_name: proxy_name.to_string(),
                    ban_time: Local::now(),
                    proxy_url,
                    reason: reason.clone(),
                },
            );
            debug!("Proxy '{}' banned [session-permanent]", proxy_name);
            true
        }
    }; // banned_proxies lock dropped here

    if newly_banned {
        let cb_guard = self.inner.ban_dispatch.lock();
        if let Some(cb) = cb_guard.as_ref() {
            Python::with_gil(|py| {
                if let Err(e) = cb.call1(py, (proxy_name, reason)) {
                    debug!("ban_dispatch callback for '{}' raised: {}", proxy_name, e);
                }
            });
        }
    }
}
```

Ensure `use pyo3::prelude::*;` / `Python`, `PyObject` are in scope (they are, since the file is a `#[pymodule]` member — confirm the import line at the top of the file and add `use pyo3::types::PyAnyMethods;` if `call1` needs it for your PyO3 version).

- [ ] **Step 4: Build**

Run: `cd javdb/rust_core && cargo build 2>&1 | tail -20`
Expected: compiles. Fix any borrow/lifetime errors surfaced.

- [ ] **Step 5: Install the wheel + commit**

Run: `cd javdb/rust_core && maturin develop --release && cd ../../..`

```bash
git add javdb/rust_core/src/proxy/ban_manager.rs
git commit -m "feat(rust): add ban-dispatch callback to RustProxyBanManager (BFR-009/ADR-043)"
```

---

## Task 2: Rust — thread `reason` through the pool ban sites

**Files:**
- Modify: `javdb/rust_core/src/proxy/pool.rs`

- [ ] **Step 1: Internal drain site (`mark_failure_and_switch`, ~line 480-491)**

Update the `add_ban` call to pass a cause:

```rust
self.ban_manager.add_ban(&current_name, proxy_url, Some("rust_auto_drain".to_string()));
```

- [ ] **Step 2: Explicit `ban_proxy` (line 659-697) — add a `reason` param and thread it**

Change the signature (line 659-660):

```rust
#[pyo3(signature = (proxy_name=None, reason=None))]
pub fn ban_proxy(&self, proxy_name: Option<String>, reason: Option<String>) -> bool {
```

At the `add_ban` call (line ~692) pass the reason:

```rust
self.ban_manager.add_ban(&target_name, proxy_url, reason.clone());
```

- [ ] **Step 3: Build + install + commit**

Run: `cd javdb/rust_core && cargo build 2>&1 | tail -20 && maturin develop --release && cd ../../..`
Expected: compiles.

```bash
git add javdb/rust_core/src/proxy/pool.rs
git commit -m "feat(rust): thread ban reason through pool ban sites (BFR-009/ADR-043)"
```

---

## Task 3: Python — dispatcher arity, client TTL-omission, runtime wiring

**Files:**
- Modify: `javdb/proxy/ban_manager.py`
- Modify: `javdb/proxy/coordinator/proxy_coordinator_client.py`
- Modify: `javdb/spider/runtime/context.py` (line ~876), `javdb/spider/runtime/state.py` (line ~722)
- Test: `tests/unit/test_proxy_ban_manager.py`

- [ ] **Step 1: Write the failing BFR-009 regression test** (the core proof)

Add to `tests/unit/test_proxy_ban_manager.py`:

```python
def test_rust_ban_dispatch_callback_fires_once_per_new_ban():
    """BFR-009: a ban recorded through the PRODUCTION Rust manager fires the
    registered dispatch callback exactly once per newly-banned proxy, with the
    reason threaded through."""
    from javdb.rust_core import RustProxyBanManager

    calls = []
    mgr = RustProxyBanManager()
    mgr.set_ban_dispatch_callback(lambda name, reason: calls.append((name, reason)))

    mgr.add_ban("proxy-A", None, "ban page detected")
    mgr.add_ban("proxy-A", None, "ban page detected")  # dedup → no 2nd call
    mgr.add_ban("proxy-B", None, None)

    assert calls == [("proxy-A", "ban page detected"), ("proxy-B", None)]


def test_set_ban_dispatch_callback_none_clears():
    from javdb.rust_core import RustProxyBanManager
    mgr = RustProxyBanManager()
    seen = []
    mgr.set_ban_dispatch_callback(lambda n, r: seen.append(n))
    mgr.set_ban_dispatch_callback(None)
    mgr.add_ban("proxy-C", None, None)
    assert seen == []
```

Run: `pytest tests/unit/test_proxy_ban_manager.py -k ban_dispatch -v`
Expected: FAIL until the wheel from Tasks 1–2 is installed (then this part PASSES — it validates the Rust side; keep it as the regression anchor).

- [ ] **Step 2: Update `_dispatch_remote_ban` + hook arity in `ban_manager.py`**

Change the hook type and dispatcher to carry `reason`:

```python
_remote_ban_hook: Optional[Callable[[str, Optional[str]], None]] = None


def set_remote_ban_hook(hook: Optional[Callable[[str, Optional[str]], None]]) -> None:
    """Register the cross-runner ban dispatcher (``(proxy_name, reason)``)."""
    global _remote_ban_hook
    _remote_ban_hook = hook


def _dispatch_remote_ban(proxy_name: str, reason: Optional[str] = None) -> None:
    hook = _remote_ban_hook
    if hook is None or not proxy_name:
        return
    try:
        hook(proxy_name, reason)
    except Exception:  # noqa: BLE001 — must NEVER escape the ban path
        logger.warning(
            "Remote ban hook for '%s' failed; ban remains local-only",
            proxy_name, exc_info=True,
        )
```

- [ ] **Step 3: Add a module helper that registers the Rust callback**

In `ban_manager.py`, add a function that bridges the Rust callback → the Python dispatcher (call this from runtime setup):

```python
def install_rust_ban_dispatch() -> None:
    """Wire the Rust ban manager's dispatch callback to ``_dispatch_remote_ban``.

    Idempotent; safe to call after the coordinator hook is registered. No-op
    when the Rust core is unavailable.
    """
    if not RUST_BAN_MANAGER_AVAILABLE:
        return
    get_ban_manager().set_ban_dispatch_callback(_dispatch_remote_ban)
```

- [ ] **Step 4: Let `mark_proxy_banned` omit `ttl_ms` so the Worker decides by cause**

In `proxy_coordinator_client.py`, change `mark_proxy_banned` to accept `ttl_ms: Optional[int] = None`:

```python
def mark_proxy_banned(
    self,
    proxy_id: str,
    reason: Optional[str] = None,
    *,
    ttl_ms: Optional[int] = None,
) -> None:
    """Persist a cross-runner ban on *proxy_id*.

    When ``ttl_ms`` is None the Worker chooses the TTL from ``reason``
    (ADR-043 D9: hard ban → 8 d, CF → 6 h, else 3 d). Explicit ``ttl_ms``
    still wins for manual/ops bans. Auto-expires server-side.
    """
    self.report_async(proxy_id, "ban", ttl_ms=ttl_ms, reason=reason)
```

> VERIFY: in `report_async` / `report`, confirm `ttl_ms=None` results in the body **omitting** `ttl_ms` (so the Worker's `Number.isFinite(body.ttl_ms)` check fails and it falls back to the reason map). If `report` currently always serialises `ttl_ms`, guard it: only add `ttl_ms` to the body when it is not None (mirror the existing `if reason is not None: body["reason"] = ...` pattern).

Note the signature now takes `reason` positionally so the Rust callback `(name, reason)` maps directly.

- [ ] **Step 5: Register the callback in runtime setup**

In `javdb/spider/runtime/state.py` (~line 722), the closure becomes 2-arg and we also install the Rust callback:

```python
    set_remote_ban_hook(lambda name, reason: client.mark_proxy_banned(name, reason))
    set_remote_unban_hook(lambda name: client.mark_proxy_unbanned(name))
    install_rust_ban_dispatch()  # BFR-009 — make Rust-recorded bans fire the hook
```

In `javdb/spider/runtime/context.py` (~line 876): `client.mark_proxy_banned` already accepts `(name, reason)` positionally, so:

```python
        legacy_state.set_remote_ban_hook(client.mark_proxy_banned)
        legacy_state.set_remote_unban_hook(client.mark_proxy_unbanned)
        legacy_state.install_rust_ban_dispatch()
```

Ensure `install_rust_ban_dispatch` is importable from `legacy_state` (it re-exports `ban_manager` symbols; add to the import/re-export list in `state.py` if needed, matching how `set_remote_ban_hook` is exposed at `state.py:34`).

- [ ] **Step 6: Update the existing `_dispatch_remote_ban` guard test**

The Explore notes `test_proxy_ban_manager.py:192-230` tests `_dispatch_remote_ban` input guarding. Update those calls to the new 2-arg form (`_dispatch_remote_ban("name", "reason")`) and add a case asserting a registered hook receives the reason.

- [ ] **Step 7: Run + commit**

Run: `pytest tests/unit/test_proxy_ban_manager.py tests/unit/test_proxy_coordinator_client.py -v`
Expected: PASS (including the new BFR-009 regression test).

```bash
git add javdb/proxy/ban_manager.py javdb/proxy/coordinator/proxy_coordinator_client.py \
        javdb/spider/runtime/state.py javdb/spider/runtime/context.py \
        tests/unit/test_proxy_ban_manager.py
git commit -m "feat(proxy): wire Rust ban dispatch to coordinator with reason (BFR-009/ADR-043)"
```

---

## Task 4: Python — thread `ProxyBannedError.reason` into `ban_proxy`

**Files:**
- Modify: `javdb/infra/request.py` (catch site ~line 1212-1216)
- Modify: `javdb/spider/fetch/fetch_engine.py` (~line 1045)
- Test: `tests/unit/test_proxy_pool.py`

- [ ] **Step 1: Pass the reason at the request.py catch site**

```python
        except ProxyBannedError as e:
            logger.debug(f"[{module_name}] Proxy '{e.proxy_name}' banned: {e.reason}")
            if self.proxy_pool:
                self.proxy_pool.ban_proxy(e.proxy_name, e.reason)
            raise
```

- [ ] **Step 2: Pass the reason at the fetch_engine.py site**

At `javdb/spider/fetch/fetch_engine.py:1045`, the `get_ban_manager().add_ban(...)` call gains the reason as the 3rd positional arg (it now accepts `reason`). If the surrounding code has the `ProxyBannedError` in scope, pass `e.reason`; otherwise pass a literal matching the cause (e.g. the `except ProxyBannedError as e:` there → `add_ban(e.proxy_name, None, e.reason)`). Confirm the local variable name for the exception at that line and use its `.reason`.

- [ ] **Step 3: Write/extend a test** in `tests/unit/test_proxy_pool.py`

```python
def test_ban_proxy_threads_reason_to_dispatch():
    from javdb.proxy.pool import create_proxy_pool_from_config
    pool = create_proxy_pool_from_config(
        [{"name": "p1", "http": "http://127.0.0.1:1", "https": "http://127.0.0.1:1"}],
        max_failures=3,
    )
    seen = []
    # The pool shares the global Rust ban manager singleton.
    from javdb.proxy.ban_manager import get_ban_manager
    get_ban_manager().set_ban_dispatch_callback(lambda name, reason: seen.append((name, reason)))
    pool.ban_proxy("p1", "ban page detected")
    assert seen == [("p1", "ban page detected")]
    get_ban_manager().set_ban_dispatch_callback(None)  # cleanup
```

> NOTE: the ban manager is a session singleton; this test sets/clears the callback to avoid leaking into other tests. If pytest isolation is a concern, mark with the existing proxy-test fixture used elsewhere in the file.

- [ ] **Step 4: Run + commit**

Run: `pytest tests/unit/test_proxy_pool.py -v && pytest tests/unit/test_engine.py -v`
Expected: PASS.

```bash
git add javdb/infra/request.py javdb/spider/fetch/fetch_engine.py tests/unit/test_proxy_pool.py
git commit -m "feat(spider): thread ban reason into ban_proxy at catch sites (ADR-043)"
```

---

## Task 5: Worker — `HARD_BAN_TTL_MS` + map ban reason → TTL

**Files (coordinator repo):**
- Modify: `src/types.ts`, `src/proxy_coordinator.ts`, `wrangler.toml`
- Test: `test/cf_auto_ban.test.ts` (or a new `test/hard_ban_ttl.test.ts`)

- [ ] **Step 1: Constant + Env field + loader**

In `src/types.ts` near `DEFAULT_BAN_TTL_MS`:

```ts
// ADR-043 D9 — JavDB hard-ban TTL (operator-observed ~7-day window + 1 d margin).
export const DEFAULT_HARD_BAN_TTL_MS = 8 * 24 * 60 * 60 * 1000; // 691_200_000 (8 d)
```

Add `HARD_BAN_TTL_MS?: string;` to `interface Env`.

In `src/proxy_coordinator.ts` near `loadBanTtlMs`, add:

```ts
function loadHardBanTtlMs(env: Env): number {
  const v = (env as { HARD_BAN_TTL_MS?: string }).HARD_BAN_TTL_MS;
  if (v === undefined || v === "") return DEFAULT_HARD_BAN_TTL_MS;
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : DEFAULT_HARD_BAN_TTL_MS;
}

/** ADR-043 D9 — pick the ban TTL by cause when the caller didn't pin ttl_ms. */
function ttlForBanReason(reason: string | undefined, env: Env): number {
  const r = (reason ?? "").toLowerCase();
  if (r.includes("ban page")) return loadHardBanTtlMs(env);       // JavDB hard ban → 8 d
  if (r.includes("cf bypass failed")) return loadCfBanTtlMs(env); // CF → 6 h (IMP-01)
  return loadBanTtlMs(env);                                       // else → 3 d
}
```

- [ ] **Step 2: Write the failing test** (`test/cf_auto_ban.test.ts`)

```ts
describe("ADR-043 hard-ban TTL by reason", () => {
  it("ban with reason 'ban page detected' and no ttl_ms → ~8 day ban", async () => {
    const p = "p-hardban";
    await call("do/report", { proxy_id: p, kind: "ban", reason: "ban page detected" });
    const s = await dump(p);
    const ms = (s as any).bannedUntil - (s as any).now;
    expect(ms).toBeGreaterThan(7.5 * 24 * 3600 * 1000);
    expect(ms).toBeLessThanOrEqual(8 * 24 * 3600 * 1000 + 1000);
    expect((s as any).bannedReason).toBe("javdb_hardban");
  });
  it("explicit ttl_ms still wins (manual ban)", async () => {
    const p = "p-manualban";
    await call("do/report", { proxy_id: p, kind: "ban", ttl_ms: 1000, reason: "ban page detected" });
    const s = await dump(p);
    const ms = (s as any).bannedUntil - (s as any).now;
    expect(ms).toBeLessThanOrEqual(1000 + 50);
  });
});
```

Run: `npm test -- cf_auto_ban` → the hard-ban test FAILS (currently falls back to 3 d).

- [ ] **Step 3: Use the mapping in the `kind === "ban"` branch**

In `handleReport`, replace the ban-branch TTL computation (lines 349-358) so the fallback uses the reason map, and tag `bannedReason`:

```ts
    if (rawKind === "ban") {
      kind = "ban";
      const ttl = Number.isFinite(body.ttl_ms as number) && (body.ttl_ms as number) > 0
        ? Number(body.ttl_ms)
        : ttlForBanReason(body.reason as string | undefined, this.env);
      const newBannedUntil = now + ttl;
      state.bannedUntil =
        state.bannedUntil !== null && state.bannedUntil > newBannedUntil
          ? state.bannedUntil
          : newBannedUntil;
      // ADR-043 D5 — record cause for /do/state + dashboards.
      const r = (body.reason ?? "").toString().toLowerCase();
      state.bannedReason = r.includes("ban page")
        ? "javdb_hardban"
        : r.includes("cf bypass failed")
          ? "cf_auto"
          : "manual";
      state.banEvents.push(now);
      await this.maybeEmitBanSpikeAlert(proxyId, state, now, body.reason);
    } else if ...
```

(Depends on `bannedReason` from IMP-01 Task 2 — if IMP-01 hasn't merged, add the field here per IMP-01 Task 2 Steps 1-2.)

- [ ] **Step 4: Run — expect PASS**

Run: `npm test -- cf_auto_ban` → PASS. `npm run typecheck` → clean. `npm test` → no regressions.

- [ ] **Step 5: wrangler.toml + commit**

Add to `[vars]`:

```toml
HARD_BAN_TTL_MS = "691200000"   # 8 days — JavDB explicit IP ban window + margin
```

```bash
git add src/types.ts src/proxy_coordinator.ts wrangler.toml test/cf_auto_ban.test.ts
git commit -m "feat(proxy-coordinator): map ban reason to TTL; 8d hard-ban (ADR-043 D9)"
```

---

## Task 6: Docs + close BFR-009

**Files (CICD repo):**
- Modify: `docs/handbook/{en,zh}/self-hoster/proxy-coordinator.md`
- Modify: `docs/design/BFR-009-Rust-Pool-Cross-Runner-Ban-Dispatch/BFR-009-rust-pool-ban-dispatch.md` + `.zh.md`
- Modify: `docs/design/ADR-043-CF-Auto-Ban/ADR-043-cf-persistent-failure-auto-ban.md` + `.zh.md`

- [ ] **Step 1: Handbook — add `HARD_BAN_TTL_MS`** to the env table created in IMP-01 Task 5 (en + zh):

```markdown
| `HARD_BAN_TTL_MS` | `691200000` (8 d) | How long a JavDB explicit IP ban is shared cross-runner. Matches JavDB's ~7-day ban window + 1 day margin. |
```

(zh: `| HARD_BAN_TTL_MS | 691200000（8 天） | JavDB 显式 IP 封禁跨 runner 共享的时长。对齐 JavDB ~7 天封禁窗口 + 1 天余量。 |`)

- [ ] **Step 2: BFR-009 → Fixed** (both `.md` and `.zh.md`):
  - Change `**Status**: Open` → `**Status**: Fixed`.
  - Check the remaining Follow-Up boxes (production-entry-point dispatch test → done in Task 3 Step 1; surface re-eval → note done/deferred).
  - Add a Status line: `- 2026-06-01: Fixed via IMP-ADR043-02 (Approach 1).`

- [ ] **Step 3: ADR-043 Status Log** — append `- 2026-06-01: Phase 2 (IMP-ADR043-02) implemented; BFR-009 closed.` to both `.md` and `.zh.md`. (Optionally flip ADR Status to `Accepted`/`Completed` once both IMPs merge.)

- [ ] **Step 4: Commit**

```bash
git add docs/handbook/en/self-hoster/proxy-coordinator.md docs/handbook/zh/self-hoster/proxy-coordinator.md \
        docs/design/BFR-009-Rust-Pool-Cross-Runner-Ban-Dispatch/ docs/design/ADR-043-CF-Auto-Ban/
git commit -m "docs(adr-043): document HARD_BAN_TTL_MS; mark BFR-009 fixed"
```

---

## Self-Review

- **Spec coverage (ADR-043 D8–D10):** D8 Rust→Python callback covering Rust-internal drain → Tasks 1, 2 (`add_ban` fires for both `pool.rs:480` drain and `ban_proxy`). D9 cause→TTL with 8 d hard ban → Tasks 3–5 (reason threaded Rust→Python→Worker; Worker maps reason→TTL). D10 keep local ban → unchanged by design (no task; the Rust ban still records locally first, then dispatches). ✓
- **Cross-repo dependency:** Task 5 (Worker reason→TTL) must deploy for hard bans to get 8 d; until then a dispatched hard ban falls back to the Worker's current `loadBanTtlMs` (3 d) — degraded but not broken. Tasks 1–4 are safe to ship first.
- **Placeholder scan:** "VERIFY" / "confirm" notes (report ttl_ms omission, fetch_engine exception var name, PyO3 `call1` import) are confirm-at-impl instructions, not code placeholders — real code is shown for each. No "TODO"/"TBD".
- **Type consistency:** `set_ban_dispatch_callback`, `add_ban(name, url, reason)`, `ban_proxy(name, reason)`, `_dispatch_remote_ban(name, reason)`, `mark_proxy_banned(proxy_id, reason, *, ttl_ms=None)`, `ttlForBanReason`, `bannedReason` values (`javdb_hardban`/`cf_auto`/`manual`) are consistent across Rust, Python, and Worker tasks and match the cause-vocabulary table.
- **Interaction with IMP-01:** Worker `bannedReason` field + `loadCfBanTtlMs` come from IMP-01; Task 5 reuses them (guarded note if IMP-01 not yet merged).
