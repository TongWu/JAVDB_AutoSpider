# ADR-002: Storage Topology for Observability Data

**Status**: Completed 2026-05-17 — all five history tables shipped (`metrics_snapshots` in MetricsState DO, `signals_event_log` + `runners_event_log` + `proxies_seen` in RunnerRegistry DO, `login_event_log` in GlobalLoginState DO, `config_audit_log` in ConfigState DO).
**Date**: 2026-05-16
**Deciders**: Proxy Coordinator Dashboard rework
**Related Implementation Plans**: [IMP-ADR003-01](../ADR-003-Metrics-Pipeline/IMP-ADR003-01-dashboard-phase2-worker-backend.md) (worker backend infrastructure — schema landed), [IMP-ADR002-01](IMP-ADR002-01-dashboard-phase4-history-drilldowns.md) (downstream drill-down UI; consumer, planned)

---

## Context

To let the operations dashboard support "see what happened in the past" (history drill-down), we need to persist five classes of replay data:

| Data type | Nature | Purpose |
|---|---|---|
| Metrics Snapshots | Periodic time-series samples | latency / health score / queue depth charts |
| Signals Event Log | Event stream | signal creation / expiry / revocation audit |
| Runners Event Log | Event stream | runner register / unregister / crashed history |
| Login Event Log | Event stream | attempt / publish / invalidate / lease flow |
| Config Audit Log | Change audit | before/after for PATCH /config |

**None of this is persisted today** — the `RunnerRegistry` DO retains only active runners / active signals and drops anything that expires; the `ConfigState` DO records no change history; the `GlobalLoginState` DO has only a bounded ring buffer.

### Problem

We need to answer: "**Which DO** do these history tables live in?"

---

## Decision

**Each class of replay data extends the SQLite schema of its corresponding business DO**, rather than being centralised into a new `HistoryState` DO.

Specific placement:

| Data | Hosting DO | Table |
|---|---|---|
| Metrics Snapshots | **MetricsState DO** (new, see ADR-003) | `metrics_snapshots` |
| Signals Event Log | `RunnerRegistry` DO (existing, extend schema) | `signals_event_log` |
| Runners Event Log | `RunnerRegistry` DO (existing, extend schema) | `runners_event_log` |
| Login Event Log | `GlobalLoginState` DO (existing, extend schema) | `login_event_log` |
| Config Audit Log | `ConfigState` DO (existing, extend schema) | `config_audit_log` |

Each DO runs its own retention sweep (differentiated retention windows: metrics 30d / signals 90d / runners 90d / login 30d / config 365d).

---

## Alternatives Considered

### Alternative A: Centralised HistoryState DO

Create a singleton `HistoryState` DO that holds all five history classes, with other DOs writing across DO boundaries on state changes.

**Pros**:
- Single source of truth, single cleanup task
- Cross-history-type queries are easier (e.g. "what happened simultaneously at 23:00 last night")
- Adding a new history type only touches one place

**Cons (why rejected)**:
- **Atomicity broken**: the state change (e.g. PATCH /config) and the audit write live on two different DOs, not in the same transaction. If the audit write fails, the config has already changed but no one recorded it — the audit purpose is defeated.
- **Cross-DO writes add IO, latency, and failure-handling burden**: every state change must fan out a fetch to the HistoryState DO, which either blocks the main path (slows the response) or is fire-and-forget (risks data loss).
- **The centralised DO becomes a hotspot**: all operations events converge on one DO, creating a single write bottleneck.
- **Limited value in cross-history queries**: dashboard drill-down is organised by panel (signals tab / config tab each stand alone) and does not actually need joined queries.

---

## Implementation

### Phase 2 (rollout phase, see grill-me Q7)

Add one history table to each of the three existing DOs:

#### `RunnerRegistry` DO
```sql
CREATE TABLE signals_event_log (
  ts INTEGER NOT NULL,            -- ms wall-clock
  event_kind TEXT NOT NULL,       -- 'create' | 'auto_expire' | 'explicit_revoke'
  signal_id TEXT NOT NULL,
  signal_kind TEXT NOT NULL,      -- throttle_global | ban_proxy | pause_all | resume
  payload_json TEXT,              -- factor / proxy_id / reason ...
  PRIMARY KEY (ts, signal_id)
);
CREATE INDEX idx_signals_event_log_kind ON signals_event_log(signal_kind, ts);

CREATE TABLE runners_event_log (
  ts INTEGER NOT NULL,
  event_kind TEXT NOT NULL,       -- 'register' | 'unregister' | 'crashed'
  holder_id TEXT NOT NULL,
  workflow_run_id TEXT,
  workflow_name TEXT,
  proxy_pool_hash TEXT,
  final_status TEXT,              -- on unregister/crashed only
  PRIMARY KEY (ts, holder_id, event_kind)
);
CREATE INDEX idx_runners_event_log_holder ON runners_event_log(holder_id, ts);
```

#### `ConfigState` DO
```sql
CREATE TABLE config_audit_log (
  ts INTEGER NOT NULL,
  key TEXT NOT NULL,
  old_value TEXT,                 -- JSON
  new_value TEXT,                 -- JSON
  actor TEXT,                     -- principal id (bearer token name / dashboard cookie)
  actor_kind TEXT NOT NULL,       -- 'operator' | 'system'
  reason TEXT,
  PRIMARY KEY (ts, key)
);
```

#### `GlobalLoginState` DO
```sql
CREATE TABLE login_event_log (
  ts INTEGER NOT NULL,
  event_kind TEXT NOT NULL,       -- 'attempt' | 'publish' | 'invalidate' | 'lease_acquire' | 'lease_release'
  holder_id TEXT,                 -- nullable: invalidate may come from anyone
  outcome TEXT,                   -- 'success' | 'failure' (attempt only)
  cookie_version INTEGER,         -- publish/invalidate only
  detail TEXT,                    -- free-form reason
  PRIMARY KEY (ts, event_kind, COALESCE(holder_id, ''))
);
```

### Retention sweep

Each DO's GC alarm (already in place) gets one extra `DELETE WHERE ts < now() - retention_ms`:
- Signals / Runners event log: 90 days
- Login event log: 30 days
- Config audit log: 365 days

Sweep frequency: piggybacks on every GC alarm; an additional hard cap of 100k rows per table (defensive).

### Phase 4 (drill-down UI)

Expose GET endpoints panel by panel (cookie-authed only):
- `GET /signals/history?range=...`
- `GET /runners/history?range=...&holder_id=...`
- `GET /login/history?range=...&holder_id=...`
- `GET /config/history?range=...&key=...`

The dashboard drawer fetches the corresponding endpoint when opened.

---

## Consequences

### Positive

1. **Atomic writes**: state change + history write happen in the same DO transaction, never drift
2. **Zero added write latency**: the history write is on the main path, no fan-out needed
3. **DO write load is distributed**: each DO carries its own history traffic
4. **DO schema evolution is localised**: adding a field in one DO does not affect others

### Negative

1. **Five independent retention implementations** (one sweep per DO) — mitigated by extracting a shared `pruneLogTable(db, table, retentionMs, maxRows)` helper
2. **Cross-history-type joined queries are awkward** — the dashboard does not actually need them
3. **Adding a new history type requires editing the corresponding DO** — but this happens infrequently, which is acceptable

### Risks

1. **SQLite storage on some DO grows unpredictably** — retention sweep + hard cap as backstop
2. **Schema migration**: existing DOs already hold data, so we need `CREATE TABLE IF NOT EXISTS` without disrupting existing columns
   - **Mitigation**: rely on SQLite `ALTER TABLE ADD COLUMN`'s backward compatibility; do not touch existing columns

---

## Related Decisions

- **ADR-003**: Concrete design of the Metrics Pipeline (rationale for a standalone MetricsState DO)
- **ADR-004**: Runner reporting of PROXY_POOL (partial data source for `runners_event_log`)

---

## References

- [CONTEXT.md](../../../../CONTEXT.md) — observability data chapter, terminology definitions
- Existing DO implementations: `JAVDB_AutoSpider_Proxycoordinator/src/runner_registry.ts`, `config_state.ts`, `global_login_state.ts`
