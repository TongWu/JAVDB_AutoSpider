# IMP-ADR041-01: Demote Best-Effort Mirrors & Split Out Rust-Required Modules — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-041](ADR-041-rust-fallback-policy.md) — this is **Phase 1** (the only phase).

**Goal:** Apply the two-tier Rust fallback policy. (a) **Best-Effort tier** (parsers, magnet, url_helper, masking): drop value-parity tests, replace with one shape/smoke test, make every fallback log a consistent `WARNING`. (b) **Rust-Required tier** (`ProxyPool`, `ProxyBanManager`): guard the two construction factories so they raise a clear error without `javdb.rust_core`, remove the stateful Python re-implementations, keep the harmless shared symbols (`ProxyInfo`, `is_proxy_usable`, `mask_proxy_url`).

**Architecture / approach:** This is a deletion-and-guard refactor, not a new module. Production is unaffected (Docker/CI always ship the Rust wheel, ADR-041 D6); every change targets the no-Rust path or the test layer. The Rust `ProxyPool` already exposes the full interface callers use — `set_health_provider` (pool.rs:387, the ADR-023 Selection Signal hook), `get_next_proxy`, `ban_proxy`, `ban_manager`, `get_ban_summary` — so removing the Python pool loses no production capability. The guard lives at the two factory chokepoints every caller routes through (`create_proxy_pool_from_config`, `get_ban_manager`), so callers are untouched.

**Tech Stack:** Python 3, `pytest`, PyO3 (`javdb.rust_core`), `logging`.

**Verification posture:** "No-Rust path" is simulated in tests by (a) importing the Python fallback modules directly (`javdb.parsing.fallback.*`, etc. — the same trick the current parity tests use), and (b) monkeypatching `RUST_PROXY_AVAILABLE`/the import to assert the factory raises. Do **not** uninstall the wheel.

---

## File Structure

| Path | Create/Modify/Delete | Responsibility |
| --- | --- | --- |
| `tests/parity/test_parser_parity.py` | **Delete** | Value-parity (Rust vs Python) parser suite (368 lines) — retired by ADR-041 D2 |
| `tests/unit/test_magnet_parity.py` | **Delete** | Value-parity magnet suite (105 lines) — retired by ADR-041 D2 |
| `tests/parity/__init__.py` | **Delete (if empty after)** | Package marker; remove only if `tests/parity/` is now empty |
| `tests/unit/test_fallback_shape.py` | **Create** | Shape/smoke test: Best-Effort fallbacks import + return the right shape on a fixture (not byte-equal to Rust) — ADR-041 D2 |
| `javdb/parsing/magnet_categorize.py` | Modify | `logger.debug` → `logger.warning` on fallback (ADR-041 D3) |
| `javdb/parsing/__init__.py` | Modify | Normalize fallback `WARNING` message to the ADR-041 D3 wording |
| `javdb/spider/url_helper.py` | Modify | Ensure fallback logs the D3 `WARNING` (add if missing) |
| `javdb/infra/masking.py` | Modify | Ensure fallback logs the D3 `WARNING` (add if missing) |
| `javdb/proxy/pool.py` | Modify | Guard `create_proxy_pool_from_config`; remove Python `ProxyPool` class body; keep `ProxyInfo` + `mask_proxy_url`; collapse `RUST_PROXY_AVAILABLE` branches |
| `javdb/proxy/ban_manager.py` | Modify | Guard `get_ban_manager`; remove Python `ProxyBanManager` class body; keep `_dispatch_remote_ban`/`_dispatch_remote_unban` |
| `tests/unit/test_proxy_pool.py` | Modify | **Repoint** `ProxyPool()` constructions to `create_proxy_pool_from_config(...)` (Rust pool); keep `ProxyInfo` tests as-is; adapt/drop direct-API cases the Rust surface lacks (D5a) |
| `tests/unit/test_proxy_ban_manager.py` | Modify | **Repoint** `ProxyBanManager()` constructions to `get_ban_manager()` (Rust ban manager); adapt/drop direct-API cases (D5a) |
| `apps/cli/ops/profile_hot_paths.py` | Modify | Drop the two Python-pool benchmarks (`bench_get_next_proxy_rr`, `bench_get_next_proxy_weighted`) + `_build_pool`; keep `bench_is_proxy_usable` (D5a) |
| `javdb/legacy/_spider_legacy.py`, `javdb/spider/runtime/state.py` | Modify | Drop `ProxyPool` from the import; rewrite `global_proxy_pool: Optional[ProxyPool]` annotation to `Optional[Any]` |
| `javdb/integrations/pikpak/bridge/service.py`, `javdb/integrations/qb/uploader/service.py` | Modify | Drop the unused `ProxyPool` symbol from `from javdb.proxy.pool import ...` (keep `create_proxy_pool_from_config`) |
| `javdb/pipeline/service.py` | Verify only | `RUST_PROXY_AVAILABLE` status flag (line 71-72) stays valid (always True in practice) |
| `CONTEXT.md` | Modify | Add **Best-Effort Fallback** + **Rust-Required Module** terms (ADR-041 Domain Language) |
| `docs/design/_archive/ADR-020-Parser-Interface-Consolidation/ADR-020-parser-interface-consolidation.md` + `.zh.md` | Modify | Status Log back-reference to ADR-041 (both languages, same commit) |
| `docs/handbook/en/developer/*` + `docs/handbook/zh/*` | Modify (if present) | Note: pure-Python is dev-only best-effort; proxy requires Rust |

---

## Task 0: Baseline & importer enumeration (do before any edit)

- [ ] **Step 0.1 — Green baseline.** Run the targeted suites and record they pass *before* changes:
  ```bash
  pytest tests/parity/test_parser_parity.py tests/unit/test_magnet_parity.py tests/unit/test_proxy_pool.py -q
  ```
- [ ] **Step 0.2 — Enumerate every importer of the symbols being removed/guarded.** These must all still resolve after the change:
  ```bash
  grep -rn "create_proxy_pool_from_config\|from javdb.proxy.pool import\|from javdb.proxy.ban_manager import\|get_ban_manager\|ProxyPool\b\|ProxyBanManager\b\|ProxyInfo\b\|is_proxy_usable" javdb apps tests --include="*.py"
  ```
  Confirm the set matches ADR-041's expectation: factory `create_proxy_pool_from_config` callers = `legacy/_spider_legacy.py`, `integrations/pikpak/bridge/service.py`, `integrations/qb/uploader/service.py`, `integrations/qb/file_filter/service.py`, `spider/spider_gateway.py`; direct `ProxyPool` class importers = `legacy/_spider_legacy.py` + tests; `get_ban_manager` callers = `infra/health_check.py`, `integrations/notify/email/log_analysis.py`; kept-symbol importers = `policy.py`, `apps/cli/ops/profile_hot_paths.py`, tests.

  **Verification gate:** if any importer outside this set is found, STOP and reconcile against ADR-041 before continuing.

---

## Task 1: Best-Effort tier — loud, consistent fallback (ADR-041 D3)

**Files:** `javdb/parsing/magnet_categorize.py`, `javdb/parsing/__init__.py`, `javdb/spider/url_helper.py`, `javdb/infra/masking.py`

- [ ] **Step 1.1 — magnet: `debug` → `warning`.** In `magnet_categorize.py` change the fallback branch from `logger.debug("⚠️  Rust magnet extractor not available, using Python fallback")` to a `logger.warning` using the D3 wording.
- [ ] **Step 1.2 — Normalize the message** across all four modules to: `"Rust core unavailable — pure-Python <area> fallback is best-effort and may diverge from production"` (`<area>` ∈ `parsers`, `magnet`, `url_helper`, `masking`).
- [ ] **Step 1.3 — Add a `WARNING`** to `url_helper.py` and `masking.py` fallback branches if they currently log nothing.

  **Verification gate:** force each fallback (import the Python module directly / monkeypatch the `RUST_*_AVAILABLE` flag) and assert exactly one `WARNING` is emitted per area (use `caplog`).

---

## Task 2: Best-Effort tier — replace value parity with a shape contract (ADR-041 D2)

**Files:** delete `tests/parity/test_parser_parity.py`, `tests/unit/test_magnet_parity.py`; create `tests/unit/test_fallback_shape.py`

- [ ] **Step 2.1 — Author the shape/smoke test** first (red→green): import the Python fallbacks directly and assert *shape*, not Rust-equality, against a fixture in `tests/fixtures/parser/`:
  - `javdb.parsing.fallback.index_parser.parse_index_page(html)` → result exposes the index entry shape (href, video_code, title keys/attrs present).
  - `javdb.parsing.fallback.detail_parser.parse_detail_page(html)` → exposes `get_magnets_as_legacy()` (the uniform accessor, ADR-020 D2).
  - `javdb.parsing.magnet_categorize.categorize(magnets)` (Python branch) → dict with keys `subtitle`, `hacked_subtitle`, `hacked_no_subtitle`, `no_subtitle`.
  - Assert the fallback emits the D3 `WARNING` (fold Task 1's gate in here).
- [ ] **Step 2.2 — Delete** `tests/parity/test_parser_parity.py` and `tests/unit/test_magnet_parity.py`. If `tests/parity/` is now empty except `__init__.py`, delete the directory.
- [ ] **Step 2.3 — Grep for stragglers** referencing the deleted tests / the parity concept:
  ```bash
  grep -rn "test_magnet_parity\|test_parser_parity\|tests/parity" . --include="*.py" --include="*.yml" --include="*.toml" --include="*.cfg" --include="*.ini"
  ```
  Update any CI test-selection map (`scripts/ci/`) or markers that name these paths.

  **Verification gate:** `pytest tests/unit/test_fallback_shape.py -q` passes; the deleted files are gone; the straggler grep is empty.

---

## Task 3: Rust-Required tier — guard the construction chokepoints (ADR-041 D4)

**Files:** `javdb/proxy/pool.py`, `javdb/proxy/ban_manager.py`

- [ ] **Step 3.1 — Guard `create_proxy_pool_from_config`.** At the top of the factory, if `not RUST_PROXY_AVAILABLE`, raise:
  ```python
  raise RuntimeError(
      "proxy pool requires the Rust core (javdb.rust_core); install the wheel "
      "(`cd javdb/rust_core && maturin develop --release`) or run with --no-proxy"
  )
  ```
- [ ] **Step 3.2 — Guard `get_ban_manager`** (`ban_manager.py`) with the same pattern (message names the ban manager).
- [ ] **Step 3.3 — Keep import-time safe.** Do **not** raise at module top level. `import javdb.proxy.pool` must still succeed for `ProxyInfo`, `mask_proxy_url`, and `is_proxy_usable`.

  **Verification gate:** a test that monkeypatches `RUST_PROXY_AVAILABLE=False` asserts (a) `import javdb.proxy.pool` succeeds, (b) `mask_proxy_url("http://1.2.3.4:8080")` returns a masked string, (c) `create_proxy_pool_from_config(...)` raises `RuntimeError` with the actionable message.

---

## Task 4: Rust-Required tier — migrate behaviour tests, then remove the Python re-implementations (ADR-041 D4/D5/D5a)

> **Order matters (D5a):** migrate the behaviour tests to the factory/Rust pool **first** and confirm they pass against Rust, **then** delete the Python classes. This keeps a green behaviour spec across the change and proves the Rust pool satisfies the same contract.

**Files:** `tests/unit/test_proxy_pool.py`, `tests/unit/test_proxy_ban_manager.py`, `javdb/proxy/pool.py`, `javdb/proxy/ban_manager.py`, `apps/cli/ops/profile_hot_paths.py`, import-line sites.

- [ ] **Step 4.1 — Repoint `tests/unit/test_proxy_pool.py` to the Rust pool.** Replace direct `ProxyPool()` constructions with `create_proxy_pool_from_config([...])` (or a small helper building a Rust pool via `add_proxies_from_list`). Keep the `TestProxyInfo` cases unchanged (`ProxyInfo` stays). For each behaviour group (round-robin, cooldown, health-weighting, banned-skip, `ban_proxy`, session-scoped bans) assert the same contract against the Rust pool. Where a test depends on a Python-only entrypoint the Rust pool lacks (e.g. `add_proxy()` singular), adapt to the Rust API or drop it with a one-line `# dropped: Rust pool has no singular add_proxy (ADR-041 D5a)` note. **Run green against Rust before deleting anything.**
- [ ] **Step 4.2 — Repoint `tests/unit/test_proxy_ban_manager.py`** the same way: construct via `get_ban_manager()` (returns the Rust ban manager) instead of `ProxyBanManager()`; assert add/clear/is-banned/singleton behaviour against Rust. Adapt/drop direct-API-only cases.

  **Gate 4.A:** `pytest tests/unit/test_proxy_pool.py tests/unit/test_proxy_ban_manager.py -q` green **with the Python classes still present** (proves the tests now bind to the Rust pool, not the Python one).

- [ ] **Step 4.3 — Remove the Python `ProxyPool` class body** (`pool.py:152` onward — stateful selection/cooldown/ban). **Keep**: `ProxyInfo` dataclass, `mask_proxy_url`, module constants, `create_proxy_pool_from_config` (Rust-only + Task 3 guard). Collapse the `RUST_PROXY_AVAILABLE` branches (`pool.py:53,65,676`) into the Rust path.
- [ ] **Step 4.4 — Remove the Python `ProxyBanManager` class body** (`ban_manager.py:120`). **Keep**: `_dispatch_remote_ban`, `_dispatch_remote_unban`, `get_ban_manager` (Rust-only + guard).
- [ ] **Step 4.5 — Fix the `ProxyPool`-symbol importers** (Task 0.2 set): drop `ProxyPool` from the `from javdb.proxy.pool import ...` lines in `legacy/_spider_legacy.py`, `spider/runtime/state.py`, `integrations/pikpak/bridge/service.py`, `integrations/qb/uploader/service.py`; rewrite the `global_proxy_pool: Optional[ProxyPool]` annotations (`legacy:335`, `state.py:124`) to `Optional[Any]` (import `Any` if needed).
- [ ] **Step 4.6 — `profile_hot_paths.py`:** delete `_build_pool`, `bench_get_next_proxy_rr`, `bench_get_next_proxy_weighted` and any registry entry referencing them; keep `bench_is_proxy_usable`.
- [ ] **Step 4.7 — Remove orphans YOUR change created** (unused imports, `RUST_IMPORT_ERROR` plumbing if no longer read, dead helpers only the Python pool used). Do not remove pre-existing unrelated code.

  **Verification gate:**
  ```bash
  grep -rn "class ProxyPool\b\|class ProxyBanManager\b" javdb/proxy   # expect: none
  grep -rn "import ProxyPool\b\|ProxyPool\b" javdb apps --include="*.py" | grep -v "RustProxyPool\|RuntimeProxyPool\|create_proxy_pool"  # expect: none (or comments only)
  python -c "import javdb.proxy.pool, javdb.proxy.ban_manager, apps.cli.ops.profile_hot_paths; print('import ok')"
  ruff check javdb/proxy apps/cli/ops/profile_hot_paths.py tests/unit/test_proxy_pool.py tests/unit/test_proxy_ban_manager.py
  pytest tests/unit/test_proxy_pool.py tests/unit/test_proxy_ban_manager.py -q
  ```

---

## Task 5: Docs & domain language (ADR-041 D7 + CONTEXT.md additions)

**Files:** `CONTEXT.md`, ADR-020 `.md` + `.zh.md`, handbook (if applicable)

- [ ] **Step 5.1 — CONTEXT.md:** add **Best-Effort Fallback** and **Rust-Required Module** to the "架构模式 / Architectural Patterns" section and the 术语对照表, verbatim from ADR-041's Domain Language section.
- [ ] **Step 5.2 — ADR-020 back-reference (both languages, same commit):** append a Status Log line to `ADR-020-parser-interface-consolidation.md` **and** `.zh.md`: *"2026-05-30: Fallback-policy dimension amended by [ADR-041](../ADR-041-Rust-Fallback-Policy/ADR-041-rust-fallback-policy.md) — value-parity (D6) is a migration-time guard; steady-state fallback is shape-contracted (Best-Effort tier), proxy pool/ban become Rust-Required."*
- [ ] **Step 5.3 — Handbook (only if a relevant page exists):** in the developer setup/CLI docs, note pure-Python is dev-only **best-effort** for parsers/magnet/url/masking, and the proxy pool **requires** the Rust wheel. Update `docs/handbook/en/` and the paired `docs/handbook/zh/` in the same commit.

  **Verification gate:** `grep -n "Best-Effort Fallback\|Rust-Required Module" CONTEXT.md` non-empty; both ADR-020 files carry the back-reference; no English/Chinese pairing drift.

---

## Task 6: Final verification gates

- [ ] **Step 6.1 — Full unit suite:** `pytest tests/unit tests/smoke -q` green.
- [ ] **Step 6.2 — Importer integrity:** re-run the Task 0.2 grep; every importer still resolves (`python -c "import ..."` for each module touched).
- [ ] **Step 6.3 — `--no-proxy` local-dev path works without constructing a pool:** smoke-run `python3 -m apps.cli.spider --no-proxy --dry-run --start-page 1 --end-page 1` (or the nearest offline smoke) and confirm no `RuntimeError` from the proxy guard.
- [ ] **Step 6.4 — Guard fires on the no-Rust proxy path:** the monkeypatched test from Task 3 is green.
- [ ] **Step 6.5 — Net deletion sanity:** `git diff --stat` shows the proxy Python bodies + 473 parity lines removed, offset by the small shape test + guards.
- [ ] **Step 6.6 — Lint:** `ruff check javdb tests` clean on touched files.

---

## Rollback

Pure refactor; revert the PR. No data, schema, or D1 changes. No migration. The Rust core and production behaviour are untouched, so rollback risk is confined to the test layer and the no-Rust dev path.

## Out of Scope

- The phantom HTTP requester (`javdb/infra/request.py` + dead Rust `requester/handler.rs`) — review Card 2, separate ADR/PR.
- Any change to the Rust core itself (it already exposes the needed pool interface).
- Deleting the Best-Effort parser/magnet/url/masking mirrors (kept by ADR-041 D1).
