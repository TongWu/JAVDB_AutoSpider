# ADR-003: Metrics Pipeline (Hybrid Write + Idle Suppression)

**Status**: Completed 2026-05-17 — `MetricsState` DO + 1-minute scheduled cron (`* * * * *`) + idle suppression (`is_transition_marker` / `is_heartbeat_anchor` columns) + 5-second bucketed JSON snapshot all shipped per the decision matrix.
**Date**: 2026-05-16
**Deciders**: Proxy Coordinator Dashboard redesign
**Related Implementation Plans**: [IMP-ADR003-01](IMP-ADR003-01-dashboard-phase2-worker-backend.md) (worker backend — pipeline shipped), [IMP-ADR003-02](IMP-ADR003-02-dashboard-phase3-ui.md) (main dashboard UI consumer — planned)

---

## Context

The dashboard needs to show time-series charts (latency / health score / active runners / queue depth / CF-bypass ratio / per-proxy multi-line trends, etc.). The Worker side currently keeps **only a "current snapshot" for every DO** — there is no historical time-series data.

To draw a time-series chart we must persist each point-in-time state as a series of sampling points. The design space has four independent axes:

1. **Data-source granularity**: in-browser memory accumulation vs. Worker DO persistence
2. **Write trigger**: cron scheduled pull vs. dashboard polling side effect vs. runner self-report vs. hybrid
3. **Sampling interval**: 5 seconds / 30 seconds / 1 minute / longer
4. **Storage schema**: single-row JSON snapshot vs. one row per metric

### Key Constraints

- **Production time distribution is severely uneven**: the GH Actions batch is active for roughly 6 hours per day; the remaining 18 hours are almost entirely idle (`active_runners=0`).
- **Cloudflare cron minimum interval is 1 minute** (30 seconds is not available).
- **DO IO is a billed resource**: CF DO SQLite is billed by "rows written / read", so we need to economise.
- **The dashboard is mostly valuable during active periods**: all-zero data points during idle periods carry no operational information.

---

## Decision

### Choice: Worker DO persistence + hybrid write + JSON snapshot + idle suppression

Concrete configuration:

| Axis | Selected |
|---|---|
| Data-source granularity | **New `MetricsState` DO persistence** |
| Write trigger | **Cron at 1 minute + dashboard 5-second polling side effect** |
| Sampling interval | **5-second bucket** (`floor(now_ms/5000)*5000`) |
| Storage schema | **Single-row JSON snapshot** (`metrics_snapshots(ts INTEGER PK, payload TEXT, source TEXT)`) |
| Deduplication strategy | **`INSERT OR REPLACE`** — within the same 5-second bucket, the last write overwrites the previous one |
| Idle suppression | **Write only when active; write a transition marker at the active↔idle boundary; write a heartbeat anchor on the hour** |
| Retention | **30-day rolling TTL + 100k-row hard cap** |

### Idle determination (all must hold simultaneously)
```
active_runners == 0
  AND queue_depth == 0 AND in_flight == 0
  AND active_signals == 0
  AND (no lease / report activity from any proxy in the past 5 minutes)
```

### Write decision matrix
| Previous tick | This tick | Behaviour |
|---|---|---|
| active | active | Write |
| active | idle | Write **transition marker** (closes the line segment) |
| idle | active | Write recovery point |
| idle | idle | **Skip** (the main IO-saving case) |
| any | any (current time is exactly :00) | Write heartbeat anchor |

---

## Alternatives Considered

### Alternative A: In-browser memory accumulation, no persistence

The dashboard JS keeps a ring buffer of each polled sample in memory; it only shows the window since the page was opened; refreshing resets it.

**Pros**: extremely lightweight implementation; zero Worker storage cost; zero IO.
**Cons (why rejected)**:
- The **history requirement (grill-me Q5) demands visibility across refreshes** — in-browser memory resetting breaks the audit value.
- Multiple operators opening the dashboard concurrently would see different "histories".
- History is lost the moment the tab closes; post-hoc analysis cannot replay it.

### Alternative B: Pure cron-driven write at 1 minute

Sample only inside the cron alarm. Fully decoupled from dashboard usage.

**Pros**: cleanest implementation; does not couple to the main request path.
**Cons (partially the reason for rejection)**:
- 1-minute resolution is too coarse for active periods — latency jitter is invisible.
- In practice 1-minute resolution is already sufficient, and **this is the main trade-off against the (IV) hybrid scheme** — see "Why" below.

### Alternative C: Runner self-report (push metrics on every heartbeat)

The runner pushes its view of metrics to `MetricsState` DO on every heartbeat (~15 seconds).

**Pros**: naturally high resolution; does not depend on the dashboard.
**Cons (why rejected)**:
- Multiple runners reporting concurrently requires deduplication logic (take max? average?) — complex.
- One extra DO write per heartbeat raises the main-path cost.
- The perspective is the runner's local view, not the Worker's global state (the health score is computed by the `ProxyCoordinator` DO and is not directly observable from a runner).

### Alternative D: Structured schema with one row per metric

`metrics(ts, metric_kind, dim_key, value)` multi-row storage.

**Pros**: precise per-metric queries that ride a SQL index efficiently.
**Cons (why rejected)**:
- **Severe write amplification**: 10 proxies × 5 metrics + 4 global ≈ 54 rows/tick; 54× more than the 1 row/tick of (P) single-row JSON.
- Schema evolution requires migrations (adding a new metric means `ALTER TABLE`).
- The dashboard's primary use is "read the full chart for a time range", not picking a specific metric.

---

## Why (IV) Hybrid Write Instead of Pure Cron

The initial decision was pure cron at 1 minute. After discussion we changed to cron + dashboard 5-second hybrid, for these reasons:

- **Cron guarantees the baseline**: even if nobody opens the dashboard, the 1-minute resolution history for past active periods is already written. Looking back later, there is no gap.
- **Dashboard densifies to 5 seconds**: when an operator is watching, they get 5-second resolution (useful for spotting latency jitter).
- **5-second bucket primary key + `INSERT OR REPLACE` provides natural deduplication**: when cron and dashboard happen to land in the same bucket, the later write overwrites the earlier one — no concurrency conflict.
- **Dashboard writes use `ctx.waitUntil()` asynchronously**: they do not block the `/ops/snapshot` main response path.
- **Idle suppression applies to both**: even with the dashboard open, nothing is written while the system is idle.

---

## Implementation

### Phase 2: infrastructure

1. Create the `MetricsState` DO class (`src/metrics_state.ts`); implement `recordSnapshot(payload, source)` + `queryRange(fromTs, toTs)` + a GC alarm that runs the retention sweep.
2. `wrangler.toml`:
   - Add the DO binding `METRICS_STATE_DO`.
   - Add a cron trigger `* * * * *` (every minute).
3. Worker `scheduled` handler: every minute, pull the result of `aggregateOpsSnapshot()` and call `MetricsState.recordSnapshot(..., 'cron')`.
4. At the end of `/ops/snapshot`, use `ctx.waitUntil(metricsState.recordSnapshot(..., 'dashboard'))` fire-and-forget.

### Phase 3: dashboard integration

Add the endpoint `GET /metrics/range?from=...&to=...`; the dashboard JS pulls it when a time range is selected and feeds the result into uPlot.

### Test coverage

- `test/metrics_state.test.ts`:
  - 5-second bucket deduplication
  - idle skip logic
  - transition marker writes
  - heartbeat anchor writes
  - retention sweep deletes expired rows
  - 100k-row hard cap triggers cleanup

---

## Consequences

### Positive

1. **Real history visible across refreshes**: closing the dashboard does not lose data.
2. **Near-zero IO during idle periods**: estimated to drop from 1440 writes/day to ~320 writes/day (about -78%).
3. **5-second resolution (active + dashboard open) + 1-minute resolution (active without dashboard) + heartbeat anchor** — the multi-tier sampling adapts naturally to the scenario.
4. **Unified schema is simple**: single-row JSON snapshot; adding metrics in the future requires no migration.

### Negative

1. **One additional DO and one additional cron trigger**: deployment complexity grows slightly.
2. **JSON parsing cost**: reading 24h of history on the dashboard means parsing ~1440 JSON objects (in the fully active case) — lightweight but non-zero.
3. **The dashboard main path picks up one extra DO write** (`waitUntil` is asynchronous and does not block the response, but it does consume CPU quota).

### Risks

1. **Cron trigger may fail to fire** (CF occasionally drops cron schedules) → history develops holes.
   - **Mitigation**: on-the-hour heartbeat anchor; the dashboard 5-second sampling provides redundant fallback.
2. **The 5-second bucket may occasionally misalign under cross-isolate clock drift.**
   - **Mitigation**: CF Worker clock sync precision is < 1 second; a 5-second bucket has ample tolerance.
3. **`MetricsState` DO storage growing unexpectedly** (dual failure of idle suppression and the 30-day TTL).
   - **Mitigation**: 100k-row hard cap; GC alarm scans continuously.

---

## Related Decisions

- **ADR-002**: observability data storage topology (why metrics gets its own DO instead of being merged into `RunnerRegistry`).
- **ADR-004**: runner reports `PROXY_POOL` (affects the `proxies` field inside the `metrics_snapshots` payload).

---

## References

- [CONTEXT.md](../../../../CONTEXT.md) — definitions of Snapshots / Idle Suppression / Transition Marker.
- Cloudflare Cron Triggers minimum interval: <https://developers.cloudflare.com/workers/configuration/cron-triggers/>
- Cloudflare DO SQLite pricing model: <https://developers.cloudflare.com/durable-objects/platform/pricing/>
