# coordinator

Cloudflare Worker + Durable Object HTTP clients: per-proxy pacing, global login-state, movie-claim mutex, runner registry, and work-distribution queue.

## Files

| File | Purpose |
|---|---|
| `do_client_base.py` | Shared HTTP boilerplate (auth, retry, JSON envelope) for all four DO client classes in this package. |
| `proxy_coordinator_client.py` | Client for the per-proxy `ProxyCoordinator` DO — globally-consistent per-proxy request pacing across runners. |
| `login_state_client.py` | Client for the singleton `GlobalLoginState` DO — shared JavDB session cookie state across runners. |
| `movie_claim_client.py` | Client for the per-day-sharded `MovieClaimState` DO (P1-B) — mutex preventing two runners from fetching the same `/v/<id>`. |
| `runner_registry_client.py` | Client for the `RunnerRegistry` singleton DO (P2-E) — runner heartbeat + work-stealing coordination. |
| `work_distributor_client.py` | Client for the `WorkDistributor` DO (W6.C / W5.2) — opt-in singleton queue with visibility leases. |

## Subdirectories

(none)

## Depends on

- Upstream callers: `javdb.proxy.pool`, `javdb.proxy.recommend.*`, `javdb.spider.fetch.*`, `javdb.spider.detail.runner`.
- Downstream: `javdb.infra.config`, `javdb.infra.logging`, `javdb.infra.masking`, `javdb.infra.request` (HTTP).
