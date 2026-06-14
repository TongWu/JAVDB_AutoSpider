# ADR-051: Route `ProxyCoordinatorClient.lease/report` Through the DO-Client Seam & Type the Async Report Queue

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Completed (2026-06-14) — implemented by [IMP-ADR051-01](IMP-ADR051-01-do-client-transport.md) |
| **Date**    | 2026-06-13                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-023](../../ADR-023-Proxy-Recommendation-Policy/ADR-023-proxy-recommendation-policy.md) (owns `/recommend_proxy` **scoring**; D19 keeps `/lease` simple/deterministic — this ADR preserves that), [ADR-013](../ADR-013-Runner-Runtime-State/ADR-013-runner-runtime-state-consolidation.md) (runtime state that calls `report_async`), [ADR-041](../ADR-041-Rust-Fallback-Policy/ADR-041-rust-fallback-policy.md) (`ProxyPool` is Rust-Required, but the DO-client HTTP layer is pure Python and unaffected) |

> Originated from the 2026-06-13 architecture review (Candidate 4 — "route `ProxyCoordinatorClient` through `_do_request`"): [architecture-review-2026-06-13.html](../../architecture/architecture-review-2026-06-13.html).

## Context

`BaseDOClient._do_request(method, path, body)` (`javdb/proxy/coordinator/do_client_base.py`) is the deep seam owning all Cloudflare Durable Object HTTP transport: `Timeout`/`ConnectionError` → `CoordinatorUnavailable`, non-2xx → `CoordinatorUnavailable`, invalid JSON → `CoordinatorUnavailable`, **and** an `isinstance(parsed, dict)` guard. Every sibling client routes through it — `MovieClaimClient` (9 methods), `RunnerRegistryClient` (4), `LoginStateClient` (6), `WorkDistributorClient` (5).

The two outliers are `ProxyCoordinatorClient.lease()` and `report()` (`proxy_coordinator_client.py`, 745 lines) — **the two highest-frequency DO-client call sites** — which **inline** the same transport block, bypassing the seam at its hottest callers and missing the `isinstance(parsed, dict)` guard `_do_request` provides. The seam has near-zero leverage exactly where it matters most.

Separately, `_async_report_loop` carries **dead** variable-length tuple-unpacking guards (`len(item) > 2 / > 3 / > 4`) whose backwards-compat targets — 2- and 4-tuple push sites — no longer exist anywhere in production or tests. The async report queue carries an untyped, variable-length tuple plus a bare-tuple shutdown sentinel.

Deletion test: deleting `lease`/`report` is not the move — they have real post-processing. But their *transport block* is a duplicate of `_do_request`; deleting that duplicate concentrates the HTTP error contract in one place. The dead tuple-compat is a pure pass-through to nowhere — it deletes cleanly.

## Decision

Route the two outliers through the seam, and replace the untyped async queue with a typed event.

### Design Decisions

**D1. Route `lease()` and `report()` through `self._do_request('POST', path, body)`.** After the call, apply only the method-specific post-processing on the returned dict — `lease`: health-cache write, `ProxyHealthSnapshot` construction, `banned_until`/`cf_bypass_until` parsing, `LeaseResult`; `report`: `penalty_factor`/`recent_event_count` extraction, `ReportResult`. Both inherit `_do_request`'s uniform error handling **including the currently-missing `isinstance(parsed, dict)` guard** — a correctness gain, not just dedup.

**D2. Parse inline after `_do_request` — no `_parse_lease_response`/`_parse_report_response` helpers.** `MovieClaimClient` and `RunnerRegistryClient` parse inline after `_do_request` with no private parse helpers; matching that pattern keeps the subsystem consistent, and the existing `patch(c._session.post)` tests already exercise the parse path end-to-end.

**D3. Replace the variable-length queue tuple with a frozen `AsyncReportEvent` dataclass.** Module-level in `proxy_coordinator_client.py` (fields: `proxy_id`, `kind`, `ttl_ms`, `reason`, `latency_ms`). `report_async()`'s public signature is unchanged; only the internal queue item type changes, so all four call sites (`sleep.py`, `state.py`, `context.py`, `fetch_engine.py`) are untouched. **Not** promoted to `BaseDOClient` — only `ProxyCoordinatorClient` has async dispatch; generalizing it now would add unused generality (one adapter ≠ a seam).

**D4. The shutdown sentinel becomes a typed `ASYNC_QUEUE_SENTINEL` constant; collapse the dead unpacking.** A module-level `ASYNC_QUEUE_SENTINEL = AsyncReportEvent(...)` makes the queue homogeneous (no `Union`); `_async_report_loop` checks `item is ASYNC_QUEUE_SENTINEL` then accesses named fields. Delete the `len(item) > 2/3/4` compat branches — their push sites are gone.

**D5. No public-API change.** `report_async()`, `lease()`, `report()`, `LeaseResult`, `ReportResult` signatures are unchanged; external call sites and the queue's producer interface are untouched. This honours [ADR-023](../../ADR-023-Proxy-Recommendation-Policy/ADR-023-proxy-recommendation-policy.md) D19 ("keep `/lease` and the request hot path simple and deterministic") — the external contract is byte-identical; only the internal transport routing and queue type change.

## Consequences

### Positive

- **leverage** — `BaseDOClient`'s depth finally reaches its two hottest callers; the HTTP error contract lives in one place for all DO clients.
- **locality** — `lease`/`report` drop ~40 duplicate transport lines each and own only response-field extraction; the async dispatch contract is one typed dataclass, not an implicit tuple shape.
- **a latent gap closes** — `lease`/`report` gain the `isinstance(parsed, dict)` guard they currently lack.
- **dead code deleted** — the `len(item) > 2/3/4` tuple-compat branches vanish (deletion test: nothing reappears; the push sites are gone).
- **tests hit one seam** — mock `_do_request` instead of `_session.post`; `AsyncReportEvent` construction is the single contract point.

### Negative

- **A new module-level dataclass + sentinel constant.** Trivial; it replaces an implicit, undocumented tuple shape.

### Risks

- **Behaviour drift in the routed transport.** Mitigated: `_do_request` already encodes the exact same `CoordinatorUnavailable` mapping the inline blocks did, *plus* the dict guard; existing `patch(c._session.post)` tests still intercept at the same point and stay green, and new tests cover the dict-guard path.
- **A missed async-queue push site still enqueues a tuple.** Mitigated: IMP Task enumerates every `report_async` and queue-`put` site; the type annotation `Queue[AsyncReportEvent]` makes a stray tuple a type error.

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 (only) | [IMP-ADR051-01](IMP-ADR051-01-do-client-transport.md) | `lease`/`report` routed through `_do_request`; `AsyncReportEvent` + typed sentinel; dead tuple-compat deleted; dict-guard tests added | — |

### Explicit non-goals (YAGNI)

- **Not** promoting async dispatch into `BaseDOClient` (D3) — one adapter, no second use.
- **Not** touching `/recommend_proxy` scoring (ADR-023) — only `/lease` and `/report` transport.
- **Not** changing any public signature or external call site (D5).

## Domain Language (additions for CONTEXT.md)

- **DO-client seam (`BaseDOClient._do_request`)** — the single method owning all Cloudflare Durable Object HTTP transport for the coordinator clients (timeout/connection/non-2xx/invalid-JSON → `CoordinatorUnavailable`, plus an `isinstance(dict)` guard). All DO clients route through it; ADR-051 brings `ProxyCoordinatorClient.lease`/`report` — the last two outliers — onto it.
- **AsyncReportEvent** — a frozen dataclass (`proxy_id`, `kind`, `ttl_ms`, `reason`, `latency_ms`) replacing the variable-length tuple enqueued by `report_async()`; the shutdown sentinel is a typed `ASYNC_QUEUE_SENTINEL` of this type.

## Alternatives Considered

- **Promote `AsyncReportEvent`/async dispatch to `BaseDOClient`.** Rejected (D3): only `ProxyCoordinatorClient` dispatches asynchronously; one adapter does not justify a seam.
- **Extract `_parse_lease_response`/`_parse_report_response` helpers.** Rejected (D2): the sibling clients parse inline after `_do_request`; consistency over marginal testability.
- **Keep the bare-tuple sentinel, `isinstance`-check before unpacking.** Rejected (D4): retains a mixed-type queue, defeating the dataclass replacement.

## References

- [ADR-023 — Proxy Recommendation Policy](../../ADR-023-Proxy-Recommendation-Policy/ADR-023-proxy-recommendation-policy.md)
- [ADR-013 — Runner Runtime State](../ADR-013-Runner-Runtime-State/ADR-013-runner-runtime-state-consolidation.md)
- [ADR-041 — Rust Core Fallback Policy](../ADR-041-Rust-Fallback-Policy/ADR-041-rust-fallback-policy.md)
- 2026-06-13 architecture review: [architecture-review-2026-06-13.html](../../architecture/architecture-review-2026-06-13.html)

## Status Log

- 2026-06-14: Completed in [IMP-ADR051-01](IMP-ADR051-01-do-client-transport.md). The planned single phase shipped `_do_request` routing for `lease`/`report`, the typed `AsyncReportEvent` queue item and sentinel, deletion of dead tuple-compat code, and focused dict-guard/async-queue regression tests. No follow-up IMP remains for this ADR.
- 2026-06-13: Proposed (from the 2026-06-13 architecture review, Candidate 4). Decided: route `lease`/`report` through `_do_request` (gaining the `isinstance(dict)` guard); inline post-processing (sibling-consistent); `AsyncReportEvent` frozen dataclass + typed `ASYNC_QUEUE_SENTINEL` module-level in `proxy_coordinator_client.py` (not promoted to base); delete the dead `len(item) > 2/3/4` tuple-compat; no public-API change. Verified: 745 lines exactly; all four sibling clients already route through `_do_request`; the tuple-compat push sites are gone. IMP-ADR051-01 pending.
