# proxy

Proxy management: in-process proxy pool with passive health tracking, session-scoped ban manager, CLI/runtime policy helpers, and Worker DO clients.

## Files

| File | Purpose |
|---|---|
| `pool.py` | Proxy pool manager with automatic failover and passive health checking (Rust-accelerated). |
| `ban_manager.py` | Session-scoped in-memory proxy ban tracking (resets on process restart; Rust-accelerated). |
| `policy.py` | CLI proxy mode parsing, module-level gating, and runtime selection policy (`normalize_proxy_id`, usability predicate). |

## Subdirectories

- `recommend/` — TTL-cached health provider backed by the Worker `/recommend_proxy` endpoint (W6.B).
- `coordinator/` — HTTP clients for the Cloudflare Worker + Durable Object coordinators (proxy, login state, movie claim, runner registry, work distributor).

## Depends on

- Upstream callers: `javdb.spider.fetch.*`, `javdb.spider.runtime.*`, `javdb.infra.request`, `javdb.infra.health_check`, `javdb.pipeline.service`, `apps.cli.spider`.
- Downstream: `javdb.infra.config`, `javdb.infra.logging`, `javdb.infra.masking`, `javdb.rust_core`.
