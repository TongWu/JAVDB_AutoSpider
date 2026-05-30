# ADR-041: Rust Core Fallback Policy — Best-Effort Mirrors vs Rust-Required Modules

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Proposed — execution in [IMP-ADR041-01](IMP-ADR041-01-demote-and-split.md) |
| **Date**    | 2026-05-30                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-020](../_archive/ADR-020-Parser-Interface-Consolidation/ADR-020-parser-interface-consolidation.md) (parser interface; relied on the frozen mirror + parity guard this ADR amends), [ADR-035](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md) (Rust scraper is the canonical parse path), [ADR-023](../ADR-023-Proxy-Recommendation-Policy/ADR-023-proxy-recommendation-policy.md) (Selection Signal plugs into `ProxyPool.set_health_provider`), [ADR-011](../_archive/ADR-011-Parsing-Module/ADR-011-javdb-parsing-module.md) (established the parsing module + frozen Python mirror) |

> Originated from the 2026-05-30 architecture review (Candidate 1 — "what to rewrite in Rust/Go"): [architecture-review-2026-05-30.html](../architecture/architecture-review-2026-05-30.html). The review found the Rust migration half-finished: the seam in front of several Rust modules fronts a **full Python re-implementation kept in value-parity lockstep**, plus one **phantom** Rust adapter (the HTTP requester). This ADR sets the steady-state policy for that fallback layer; the phantom requester is tracked separately (review Card 2).

## Context

The Rust core (`javdb/rust_core/`, installed as `javdb.rust_core` via PyO3/maturin) is the high-performance implementation for HTML parsing, magnet categorization, proxy pool/ban management, URL helpers, and masking. Every one of these is fronted by a Python module that follows the **"prefer `javdb.rust_core`, fall back to a pure-Python mirror on `ImportError`"** pattern — the idiom [ADR-020](../_archive/ADR-020-Parser-Interface-Consolidation/ADR-020-parser-interface-consolidation.md) (D2) called "layer-legal and idiomatic".

The 2026-05-30 review measured the cost of that idiom in its current form:

- **~2,826 lines of Python re-implement Rust behind these seams**: parsers `javdb/parsing/fallback/` (1,070), proxy `javdb/proxy/pool.py` + `ban_manager.py` (942), and `javdb/spider/url_helper.py` + `javdb/parsing/magnet_categorize.py` + `javdb/infra/masking.py` (814).
- **The two implementations are required to be identical**, enforced by **value-parity tests** — `tests/parity/test_parser_parity.py` (368) and `tests/unit/test_magnet_parity.py` (105). [ADR-020](../_archive/ADR-020-Parser-Interface-Consolidation/ADR-020-parser-interface-consolidation.md) (D6) made `test_magnet_parity.py` the guard that the relocation "must stay green throughout".
- **Two adapters justify a seam only when behaviour varies across it.** Here behaviour may *not* vary — parity is the contract — so the seam fronts duplicated locality, not variation, and the cost is a permanent two-language lockstep.
- **Production never runs the fallback.** Both `docker/Dockerfile` and `docker/Dockerfile.api` build the wheel with `maturin build --release`; CI installs it via `setup-python-env` → `install-rust-wheel`. The Python mirror only runs when the wheel is absent — i.e. local development without the Rust toolchain. [ADR-035](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md) already treats the **Rust scraper as the canonical parse path** (its parse contract and fill-rate telemetry observe the Rust output).
- **The fallback engages silently for some modules.** Parsers and the proxy pool log a `WARNING` on fallback; `javdb/parsing/magnet_categorize.py` logs only `logger.debug` — so a magnet-categorization downgrade is invisible.
- **Not all fallbacks carry equal risk.** A parser/magnet/url/masking fallback that diverges produces *inspectable* output — a developer sees the wrong parse. But the **proxy pool and ban manager are stateful** (selection, cooldown, ban): a silently-divergent copy mis-behaves in ways that cannot be eyeballed, and is exactly the behaviour a developer might be debugging locally. A "best-effort proxy selection" is a debugging trap, not a convenience.

The decision space (from the review's grilling): the pure-Python fallback **is** still wanted as a no-Rust-toolchain local-dev path — but it should stop being a value-parity maintenance anchor, and it should not silently diverge where divergence is undetectable.

## Decision

Replace the uniform "Rust-first + value-parity Python mirror" policy with a **two-tier fallback policy, classified by inspectability**, and make every fallback loud.

### Design Decisions

**D1. Two fallback tiers.**

- **Best-Effort Fallback** — `javdb.parsing` (index/detail/tag parsers), `javdb/parsing/magnet_categorize.py`, `javdb/spider/url_helper.py`, `javdb/infra/masking.py` (and the colocated `mask_proxy_url` helper in `javdb/proxy/pool.py`). The pure-Python mirror is **kept** as a no-toolchain local-dev convenience. Its output is inspectable, so divergence is self-evident. It is **shape-contracted, not value-parity-guaranteed**.
- **Rust-Required Module** — `ProxyPool` and `ProxyBanManager`. These are stateful and not inspectable. The Python re-implementation is **removed**; constructing them without `javdb.rust_core` raises a clear error.

**D2. Best-Effort tier: replace value parity with a shape contract.** Delete the value-parity suites `tests/parity/test_parser_parity.py` and `tests/unit/test_magnet_parity.py`. Replace them with one thin **shape/smoke** test that asserts the Python fallback (a) imports and (b) returns the right *shape* on a small fixture — the same accessors/keys the Rust objects expose (e.g. `MovieDetail.get_magnets_as_legacy()`, the `subtitle/hacked_subtitle/hacked_no_subtitle/no_subtitle` dict keys), **not** byte-equality with Rust. This preserves the only contract callers actually depend on (shape, per [ADR-020](../_archive/ADR-020-Parser-Interface-Consolidation/ADR-020-parser-interface-consolidation.md) D2's `get_magnets_as_legacy()` uniformity) while dropping the two-language value lockstep.

**D3. Loud, consistent fallback.** Every Best-Effort fallback engagement logs exactly one `WARNING`: *"Rust core unavailable — pure-Python `<area>` fallback is best-effort and may diverge from production."* Promote `javdb/parsing/magnet_categorize.py`'s `logger.debug` to `logger.warning` so no fallback is silent. This directly discharges [ADR-020](../_archive/ADR-020-Parser-Interface-Consolidation/ADR-020-parser-interface-consolidation.md) D6's concern (silently dropping to the slow Python path): with parity gone, *loudness* — not a value-equality test — is what stops a silent Rust bypass going unnoticed.

**D4. Rust-Required guard at the construction chokepoint, not at import.** The guard lives in the two factories every caller already routes through — `create_proxy_pool_from_config(...)` and `get_ban_manager()` — which raise a clear error (`RuntimeError`, message: "proxy pool requires the Rust core (`javdb.rust_core`); install the wheel") when Rust is unavailable. It is **not** an import-time failure: `import javdb.proxy.pool` stays safe for the harmless shared symbols (`ProxyInfo`, `is_proxy_usable`, `mask_proxy_url`), and a `--no-proxy` run that never constructs a pool keeps working without Rust. Remove the stateful Python `ProxyPool` selection/ban bodies and the `ProxyBanManager` Python class; collapse the `RUST_PROXY_AVAILABLE` branches.

**D5. Keep harmless shared models/helpers (they are Best-Effort, not pool state).** `ProxyInfo` (a plain dataclass, imported by `javdb/proxy/policy.py`, `apps/cli/ops/profile_hot_paths.py`, tests), `is_proxy_usable` (`javdb/proxy/policy.py`), and `mask_proxy_url` (masking) are **not** stateful selection logic — they stay, and `mask_proxy_url` keeps its pure-Python body. "Rust-Required" applies to the pool/ban *behaviour*, not to every symbol colocated in the file.

**D5a. The proxy behaviour test surface migrates to the Rust pool — it is not deleted (amended during implementation).** The proxy pool/ban manager have **no Rust-side tests** (`pool.rs` / `ban_manager.rs` carry zero `#[test]`), so `tests/unit/test_proxy_pool.py` + `tests/unit/test_proxy_ban_manager.py` (~1,000 lines) are the **only** behaviour spec for selection / cooldown / health-weighting / ban-skip / session-scoped bans. Most of those tests construct the *Python* `ProxyPool()` / `ProxyBanManager()` directly, so deleting the Python classes would drop proxy behaviour coverage to near-zero. Therefore the behaviour tests are **repointed** to construct via the factories `create_proxy_pool_from_config(...)` / `get_ban_manager()` (which return the Rust pool in this environment) and assert the same behaviour contract against the Rust implementation — **preserving and upgrading** the test surface (it now tests the production implementation through its real interface; the interface is the test surface). Direct-API tests that rely on a Python-only shape (`add_proxy()` singular, direct `ProxyBanManager()` singleton semantics) are adapted to the Rust API (`add_proxies_from_list` / factory) or dropped where the Rust surface genuinely differs. `apps/cli/ops/profile_hot_paths.py`'s two Python-pool micro-benchmarks (`bench_get_next_proxy_rr`, `bench_get_next_proxy_weighted`) lose their subject and are dropped; the `ProxyInfo` / `is_proxy_usable` benchmark stays.

**D6. Production is unaffected; this changes only the no-Rust path.** Docker and CI always ship the wheel, so production never ran the fallback and never raises the D4 error. The change deletes a maintenance anchor (value parity across two languages) and refuses one undetectable-divergence path (the proxy pool) in local dev. This is consistent with [ADR-035](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md) (Rust is the canonical parse path) and [ADR-020](../_archive/ADR-020-Parser-Interface-Consolidation/ADR-020-parser-interface-consolidation.md) (Rust-first dispatch).

**D7. Relationship to ADR-020 / ADR-011 — amend, do not supersede.** ADR-020's parser-interface consolidation and ADR-011's parsing module stand unchanged. This ADR amends only the *fallback-policy* dimension they leaned on: ADR-020 D6 used value parity as a **migration guard** (now Implemented); ADR-041 sets the **steady-state** guard to *shape, not value* for the Best-Effort tier and removes the mirror entirely for the Rust-Required tier. A back-reference is added to ADR-020's Status Log.

## Consequences

### Positive

- **Deletes ~2,826 lines** of Python re-implementation (proxy pool/ban) plus 473 lines of value-parity tests, net of the small kept Best-Effort mirrors and the new shape/smoke test.
- **locality** — proxy selection/ban behaviour lives in exactly one place (Rust); there is no second copy to keep in lockstep.
- **No undetectable divergence** — the one fallback that could mis-behave invisibly (the proxy pool) is refused with a clear error instead of silently running.
- **No silent downgrade** — D3 makes every remaining fallback announce itself; a Rust bypass is now visible without a parity test.
- **Inverted deletion test holds** — deleting the proxy Python mirror does *not* make complexity reappear (Rust covers it in production); deleting the parity suite does not lose a contract callers depend on (shape is preserved by D2).

### Negative

- **Local dev without the Rust toolchain loses the ability to exercise the proxy pool** — a `--use-proxy` pool run now requires the wheel. Mitigated: `--no-proxy` local dev is unaffected (D4), and building the wheel is one `maturin develop` step.
- **The Best-Effort mirror may drift from Rust** — by design it is no longer value-guaranteed. Mitigated: it never runs in production (D6), it is loud (D3), and its shape is still tested (D2).
- **~1,000 lines of proxy behaviour tests must be repointed, not deleted** — `tests/unit/test_proxy_pool.py` + `tests/unit/test_proxy_ban_manager.py` are the only behaviour spec (no Rust-side tests exist), so they migrate to the factory/Rust pool (D5a). This is the largest single piece of work in the phase, with some adaptation/dropping where the Rust surface differs.
- **`apps/cli/ops/profile_hot_paths.py` loses two micro-benchmarks** — its Python-pool selection benchmarks are dropped (D5a).

### Risks

- **A caller constructs a pool on an unexpected no-Rust path** and now raises instead of silently degrading. Mitigated: the guard is at the two factory chokepoints with a clear, actionable message; production always has Rust.
- **An importer of the removed Python `ProxyPool` class breaks.** IMP Task 0 enumerated them: the factory `create_proxy_pool_from_config` is the public entrypoint (callers untouched); the *class symbol* is used only as a type annotation in `javdb/legacy/_spider_legacy.py` + `javdb/spider/runtime/state.py` (rewrite to `Optional[Any]`), as an unused import in the pikpak/qb services (drop from the import line), as a benchmark subject in `profile_hot_paths.py` (D5a), and in the two behaviour-test files (D5a). All are accounted for in IMP-ADR041-01.

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 — Demote & split | [IMP-ADR041-01](IMP-ADR041-01-demote-and-split.md) | Best-Effort tier (drop value parity → shape/smoke test; loud `WARNING` incl. magnet `debug→warning`); Rust-Required tier (guard `create_proxy_pool_from_config` + `get_ban_manager`; remove Python `ProxyPool`/`ProxyBanManager` bodies; keep `ProxyInfo`/`is_proxy_usable`/`mask_proxy_url`); CONTEXT.md terms; ADR-020 back-reference | The phantom HTTP requester (review Card 2 — separate ADR/PR) |

### Explicit non-goals (YAGNI)

- **Not deleting the Best-Effort mirrors** — the no-toolchain local-dev path is wanted (review grilling outcome).
- **Not touching the HTTP requester** — the phantom Rust requester is review Card 2; tracked separately.
- **Not making the Rust wheel a hard install-time dependency of the whole package** — only pool/ban construction requires it; everything else degrades best-effort.

## Domain Language (additions for CONTEXT.md)

- **Best-Effort Fallback** — a pure-Python mirror of a Rust module (parsers, magnet categorization, URL helpers, masking) kept for no-toolchain local development. Shape-contracted, **not** value-parity-guaranteed; logs a `WARNING` when engaged; never runs in production (Docker/CI always ship the Rust wheel).
- **Rust-Required Module** — a module whose behaviour is stateful and not inspectable (`ProxyPool`, `ProxyBanManager`), so it has **no** Python fallback. Constructing it without `javdb.rust_core` raises a clear error at the construction chokepoint (`create_proxy_pool_from_config`, `get_ban_manager`).

## Alternatives Considered

- **Delete the Python fallback entirely (make Rust a hard dependency everywhere)** — rejected: the no-Rust-toolchain local-dev path is wanted for the inspectable modules.
- **Keep the status quo (value parity for all six)** — rejected: a two-language value lockstep where nothing may vary is a maintenance anchor, and the proxy mirror can diverge undetectably.
- **Uniform best-effort for all six (keep proxy mirror, drop parity, warn)** — rejected: proxy/ban divergence is not inspectable; "best-effort proxy selection" is a trap, not a convenience.
- **Import-time hard fail for the proxy module** — rejected: breaks `import javdb.proxy.pool` for the harmless shared helpers and breaks `--no-proxy` local dev without Rust.

## References

- [ADR-020 — Parser Interface Consolidation](../_archive/ADR-020-Parser-Interface-Consolidation/ADR-020-parser-interface-consolidation.md)
- [ADR-035 — Site-Contract Drift Sentinel](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md)
- [ADR-023 — Proxy Recommendation Policy](../ADR-023-Proxy-Recommendation-Policy/ADR-023-proxy-recommendation-policy.md)
- [ADR-011 — JavDB Parsing Module](../_archive/ADR-011-Parsing-Module/ADR-011-javdb-parsing-module.md)
- 2026-05-30 architecture review: [architecture-review-2026-05-30.html](../architecture/architecture-review-2026-05-30.html)

## Status Log

- 2026-05-30: Proposed (from the 2026-05-30 architecture review, Candidate 1 grilling). Tier split (Best-Effort vs Rust-Required), parity→shape, loud fallback, construction-chokepoint guard. IMP-ADR041-01 pending.
- 2026-05-30: Amended during implementation (Task 0 importer enumeration). Discovered the proxy pool/ban have **no Rust-side tests**, so the ~1,000 lines of Python pool/ban tests are the only behaviour spec. Added **D5a**: the behaviour tests are **repointed to the factory/Rust pool**, not deleted (preserving + upgrading the test surface); `profile_hot_paths.py`'s Python-pool benchmarks are dropped; `ProxyPool`-as-type-annotation sites (`legacy`, `state.py`) rewrite to `Optional[Any]`. Negative/Risks updated accordingly.
