# ADR-004: Proxy Discovery — Runners Upload the Full PROXY_POOL

**Status**: Completed 2026-05-16 — runner `/register` payload now uploads `proxy_pool`; worker-side `proxies_seen` table + handlers (`/do/proxies_seen`, `/proxies_seen`) shipped in RunnerRegistry DO.
**Date**: 2026-05-16
**Deciders**: Proxy Coordinator Dashboard rework
**Related Implementation Plans**: [IMP-ADR004-01](IMP-ADR004-01-dashboard-phase1-proxy-pool-upload.md) (Phase 1 — runner-side upload, completed 2026-05-16), [IMP-ADR003-01](../ADR-003-Metrics-Pipeline/IMP-ADR003-01-dashboard-phase2-worker-backend.md) (worker backend persistence — schema shipped)

---

## Context

One requirement of the dashboard rework: the per-proxy state panel must by default show **every proxy** (including idle backups) and surface a human-readable `name` (e.g. "Singapore Arm-3").

The current architecture imposes a **key constraint**:

```
// JAVDB_AutoSpider_Proxycoordinator/src/index.ts L859-866 (original comment)
Proxy enumeration:
  The ProxyCoordinator DO is addressed per-id (`idFromName(proxy_id)`);
  there is no master "list of known proxies" registry. The operator
  passes the proxy IDs they care about via `?proxy_ids=a,b,c`
```

**The Worker side has no knowledge of which proxies exist.** Each runner reads its own `config.py` `PROXY_POOL` list at startup, but the Worker cannot see that data. The existing `proxy_pool_hash` field on `RunnerRegistry` is just the SHA1 prefix (16 chars) of the PROXY_POOL JSON — not an ID list.

To "show every proxy with its name by default", the "where does the full set come from?" question has to be answered first.

---

## Decision

**Extend the runner `/register` payload to upload the full PROXY_POOL (including idle backups). Persist it on the Worker side as a `proxies_seen` table inside the `RunnerRegistry` DO. The dashboard reads this table as the canonical full set.**

### Payload extension

`POST /register` request body gains a new field (backwards-compatible — older runners that don't send it still work):

```json
{
  "holder_id": "...",
  "workflow_run_id": "...",
  "proxy_pool_hash": "...",
  "proxy_pool": [                 // ← new
    { "id": "Singapore Arm-3", "name": "Singapore Arm-3" },
    { "id": "Tokyo Backup-1", "name": "Tokyo Backup-1" }
  ]
}
```

Note: on the Python side, `normalize_proxy_id()` already treats `name` as `proxy_id` (see `proxy_policy.py:150`), so 99% of the time `id` and `name` are identical. Reporting both fields preserves future flexibility for `id ≠ name`.

### Worker-side persistence

`RunnerRegistry` DO gains a new table:

```sql
CREATE TABLE proxies_seen (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  last_seen_ms INTEGER NOT NULL,
  first_seen_ms INTEGER NOT NULL
);
```

`/register` handling: for each entry in `proxy_pool`, `INSERT OR REPLACE` into `proxies_seen` and refresh `last_seen_ms`.

### Dashboard read path

`/ops/snapshot` is updated so that:
- If the query string omits `?proxy_ids=...`, automatically use the full set from `proxies_seen` (replacing the current "empty → prompt the user to add a query param" behaviour).
- `?proxy_ids=...` is still supported (backwards compatibility for external monitoring scripts).

### Stale handling (manual delete + 30-day fold)

- No automatic deletion by default (avoids accidentally removing long-idle backup proxies).
- Dashboard UI: entries with `last_seen_ms < now - 30d` are collapsed by default; the user can click to expand.
- Manual deletion: a new `DELETE /proxies_seen?id=...` endpoint (cookie-authed only).

---

## Alternatives Considered

### Alternative A — Only display proxies reported by active runners (no persisted full set)

Dashboard shows only proxies reported by currently heartbeating runners.

**Pros**: simplest implementation; no storage.
**Cons (why rejected)**:
- During quiet periods (no GH Actions running) the dashboard is completely empty — operators cannot inspect proxy configuration in advance.
- Backup proxies that have never been actively used are never visible.
- The experience is barely different from "show nothing".

### Alternative B — Static `KNOWN_PROXY_IDS` in Worker `[vars]`

The operator maintains a proxy ID list in `wrangler.toml`.

**Pros**: fully independent of the runners.
**Cons (why rejected)**:
- Requires `wrangler deploy` to change the proxy list — double maintenance against `config.py`'s `PROXY_POOL`.
- Drift-prone (two sources of truth).

### Alternative D — Auto-register inside the ProxyCoordinator DO

On every `/lease` or `/report`, write the proxy_id into a singleton "registry" DO.

**Pros**: fully automatic; runners need no protocol change.
**Cons (why rejected)**:
- Adds one extra DO write per lease (a high-frequency path).
- Still cannot discover "idle backup proxies" (anything never leased never appears).
- No source for the `name` field (the lease path only carries `proxy_id`).

---

## Implementation

### Phase 1 (rollout stage, see grill-me Q7)

**Ship the autospider side first** (backwards-compatible; the Worker not consuming it has no impact):

Modify `packages/python/javdb_platform/runner_registry_client.py`:
- Add a `proxy_pool: list[dict]` parameter to `register()`.
- The caller (spider/pipeline entrypoint) reads `config.PROXY_POOL` and normalises it into a `[{id, name}]` list.

Modify `packages/python/javdb_platform/proxy_policy.py` (if needed):
- Expose a `serialize_proxy_pool_for_registry(pool) -> list[dict]` helper.

Merge and deploy autospider. Old Worker versions receiving the new field simply ignore it (confirmed that `clipString` does not fail on extra fields).

### Phase 2 (rollout stage)

**Worker side starts consuming**:

Modify `JAVDB_AutoSpider_Proxycoordinator/src/runner_registry.ts`:
- Add `proxy_pool?: Array<{id: string; name: string}>` to the `RegisterRunnerRequest` type.
- The register handler parses `proxy_pool` and writes into the `proxies_seen` table.

Modify `JAVDB_AutoSpider_Proxycoordinator/src/index.ts`:
- `aggregateOpsSnapshot()` falls back to reading the full `proxies_seen` set when the `proxy_ids` query param is empty.
- `?proxy_ids=...` is still honoured as an explicit filter.

### Phase 3 (rollout stage)

Dashboard UI:
- The per-proxy panel always renders the full `proxies_seen` set.
- Chip filter group (grill-me Q6b).
- 30-day stale entries auto-collapsed.

---

## Consequences

### Positive

1. **Dashboard is ready-to-view by default**: no need for the operator to hand-pass `?proxy_ids=...`.
2. **The proxy list stays visible during quiet periods**: backup proxies remain enumerable.
3. **The `name` field is the legend source for the multi-line charts in Q5d/Q5e.**
4. **`last_seen_ms` is reusable for a "history" feature**: it can answer "when was this proxy last active?".

### Negative

1. **Runner ↔ Worker payload contract change**: two repositories have to coordinate releases.
   - **Mitigation**: Phase 1/2 split into two steps, backwards-compatible.
2. **`proxies_seen` has no automatic cleanup** (30 days only folds, never deletes).
   - **Mitigation**: a manual delete endpoint; in ops scenarios the proxy count is <50, so long-term accumulation is acceptable.
3. **Adds one DO write batch** (N `INSERT OR REPLACE` per register).
   - **Mitigation**: register is a low-frequency path (once per runner start) and N is typically ≤10.

### Risks

1. **Old runners that don't upload `proxy_pool`** → no new proxy is discovered during their registration window.
   - **Mitigation**: all runners will roll forward eventually; old runners do not run indefinitely.
2. **Does `proxy_pool` contain sensitive information** (e.g. proxy URLs with passwords)?
   - **Decision**: only upload `id` and `name`; **never upload URLs or credentials**.
   - **Mitigation**: the client-side serialiser explicitly whitelists fields.

---

## Related Decisions

- **ADR-002**: observability data storage topology (why `proxies_seen` lives in the `RunnerRegistry` DO instead of a new DO — same atomicity principle as ADR-002).
- **ADR-003**: Metrics Pipeline (`proxies_seen` indirectly bounds how many proxies the metrics snapshot can enumerate).

---

## References

- [CONTEXT.md](../../../../CONTEXT.md) — Runner / RunnerRegistry DO definitions.
- Existing register implementation: `JAVDB_AutoSpider_Proxycoordinator/src/runner_registry.ts:160-219`.
- Python proxy_id normalisation: `packages/python/javdb_platform/proxy_policy.py:150-190`.
- Existing PROXY_POOL config example: `config.py.example:114-141`.
