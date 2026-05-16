# recommend

Worker `/recommend_proxy` integration: HTTP client + TTL-cached, background-refreshing health provider compatible with `ProxyPool.set_health_provider`.

## Files

| File | Purpose |
|---|---|
| `client.py` | Thin GET wrapper around the Worker's `/recommend_proxy` aggregator route. |
| `policy.py` | TTL-cached health provider with daemon `Timer` refresh loop and stale-score fallback semantics. |

## Subdirectories

(none)

## Depends on

- Upstream callers: `javdb.proxy.pool` (when a health provider is configured).
- Downstream: `javdb.proxy.coordinator.do_client_base`, `javdb.infra.config`, `javdb.infra.logging`.
