# ADR-023: Proxy Recommendation Policy — Stability-Weighted Bandit for ProxyCoordinator

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Proposed                                                              |
| **Date**    | 2026-05-27                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-004](../_archive/ADR-004-Proxy-Discovery/ADR-004-proxy-discovery-via-runner-pool-upload.md), [ADR-013](../_archive/ADR-013-Runner-Runtime-State/ADR-013-runner-runtime-state-consolidation.md) |

## Context

`ProxyCoordinator` already exposes a useful health surface: `/report` records `success`, `failure`, `cf`, `ban`, `unban`, and `cf_bypass`; `computeHealthSnapshot()` derives a basic score from success ratio and latency; and `/recommend_proxy` returns sorted candidates with backward-compatible health fields. The Python spider client already reports completion latency back to the coordinator.

That is enough to rank proxies heuristically, but it is not enough to model the real operational problem. In practice, the proxy pool must balance several competing concerns:

- avoid repeatedly selecting proxies that trigger bans or Cloudflare bypass failures;
- avoid permanently starving a proxy that may recover after a cooldown or external JavDB unban;
- avoid confusing proxy-local health with a global JavDB outage or mass ban event;
- keep `/lease` and the request hot path simple and deterministic;
- preserve backward compatibility with existing Python clients and Worker DO state.

The goal is not to build an online LLM or a heavyweight ML service. The goal is a small policy layer that learns from operational feedback and improves proxy selection without changing the current contract shape.

## Decision

Replace the pure heuristic ranking in `ProxyCoordinator` with a stability-weighted bandit policy that runs only inside `/recommend_proxy` and is backed by existing coordinator state.

### Design Decisions

D1. **Stability first, throughput second** — The policy optimizes for fewer bans, fewer CF bypass failures, and fewer session-level instability events before it optimizes for raw latency or short-term throughput.

D2. **Reward is weighted, not binary** — Outcomes are scored on a stability spectrum: `success + low latency` is positive; `failure` is slightly negative; `cf` and `cf_bypass` are moderately negative; `ban` is strongly negative. If a failure correlates with login refresh or session instability, that signal is weighted more heavily.

D3. **Hard exclusions are limited to true operator intent** — The policy must not permanently eliminate a proxy from action. `ban` and `cf_bypass` enter cooldown / low-frequency probing, but recovery remains possible. Only explicit operator state or protocol-level hard disable should prevent all selection.

D4. **Global and local health are separated** — The policy compares each proxy against a shared global baseline window. If the whole pool is degrading at once, the model should become more conservative instead of blaming every proxy equally.

D5. **Cooldown and exploration are mandatory** — Each non-hard-disabled proxy keeps a small exploration floor. Recently bad proxies may re-enter selection after cooldown, with a small recovery probe rate instead of permanent exile.

D6. **Cold start uses the existing heuristic as a prior** — Brand-new or low-sample proxies start neutral. The current heuristic remains the fallback and the prior until confidence grows.

D7. **Shadow mode first** — The first rollout phase computes model scores alongside heuristic scores but does not change ranking. Only after the shadow data looks sane should the policy be allowed to influence ordering.

D8. **Explainability stays structured** — `/recommend_proxy` may return optional `model_score`, `heuristic_score`, `confidence`, `reason_code`, `cooldown_until`, and `model_version` fields. Existing Python clients may ignore them.

D9. **No heavyweight ML runtime in the hot path** — The first version is a lightweight TS policy / bandit implementation. No online LLM, no Workers AI, no Vectorize, and no separate ML service are introduced for selection.

### Implementation Shape

The policy lives inside `JAVDB_AutoSpider_Proxycoordinator` and reuses the existing proxy DO state:

- per-proxy state continues to hold counts, latency EMA, ban / bypass markers, and cooldown metadata;
- `/report` remains the feedback entry point;
- `/recommend_proxy` becomes the only scoring site;
- Python clients keep reporting events and consuming recommendations without protocol changes beyond optional fields.

The policy computes a final ranking score from three pieces:

- a heuristic prior derived from current health snapshot logic;
- a learned stability score derived from weighted outcome history;
- a confidence term derived from sample count, recency, and global baseline quality.

When the policy has low confidence, the heuristic dominates. When the policy has enough data, the learned component can gradually take over. If state is missing or malformed, the code falls back to the current heuristic ranking.

## Consequences

### Positive

- Proxy selection can learn from bans, CF bypass failures, and latency without changing the request hot path.
- Bad proxies are less likely to be permanently starved.
- Global JavDB outages will not poison every proxy equally.
- The recommendation API stays backward compatible for old clients.
- The rollout can be validated safely in shadow mode before changing behavior.

### Negative

- The policy is more complex than a single static score.
- A cooldown / exploration system requires careful tuning to avoid oscillation.
- The system still needs operational judgment to distinguish proxy-local failure from external site behavior.

### Risks

- **Over-penalizing transient outages** — If global baseline detection is too aggressive, the policy may become too conservative. Mitigation: use shared-window comparison and keep heuristic fallback.
- **Under-exploring recovered proxies** — If exploration floor is too low, recovery becomes slow. Mitigation: keep a non-zero probe floor and cooldown expiry.
- **Protocol drift** — New optional fields must remain optional so older clients do not break. Mitigation: preserve current response shape and add fields only.

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 | [IMP-ADR023-01](IMP-ADR023-01-shadowscore-confidence-fields.md) | Shadow scoring, reward aggregation, confidence, and optional explainability fields | No ranking change yet |
| Phase 2 | [IMP-ADR023-02](IMP-ADR023-02-policy-rollout-flag.md) | Policy-driven ordering behind a feature flag with heuristic fallback | Full automation of tuning and offline training |
| Phase 3 | [IMP-ADR023-03](IMP-ADR023-03-observability-rollout-hardening.md) | Observability and rollout hardening for global-vs-local health signals | Any heavyweight ML runtime or LLM-based selection |

## References

- `JAVDB_AutoSpider_Proxycoordinator/src/proxy_coordinator.ts` — current health snapshot and candidate ranking.
- `JAVDB_AutoSpider_Proxycoordinator/src/index.ts` — `/recommend_proxy` response shape and sorting.
- `javdb/proxy/coordinator/proxy_coordinator_client.py` — Python client contract and health parsing.
- `docs/handbook/en/self-hoster/proxy-coordinator.md` — current operator-facing proxy coordinator behavior.

## Status Log

- 2026-05-27: Proposed as ADR-023.
