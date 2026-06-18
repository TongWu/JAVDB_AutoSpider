# ADR-047: Dual-Backend Drift Reconciliation & Targeted Guard Extension

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Completed — Phase 1 reconciliation + Phase 2 targeted guard landed 2026-06-04; cross-repo PRs pending |
| **Date**    | 2026-06-02                                                           |
| **Authors** | Ted                                                                  |
| **Related** | [ADR-018](../ADR-018-Dual-Backend-Query-Contract/ADR-018-dual-backend-query-contract.md) (Contract Golden — this extends its guard), [ADR-017](../ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md) (dual-backend split), [ADR-029](../ADR-029-Web-Security-Hardening/ADR-029-web-security-hardening.md) (auth — owns token revocation), [ADR-005](../ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md) (`audit` write-mode retired) |

> Originated from the 2026-05-29 architecture review (Candidate B): [architecture-review-2026-05-29.html](../../architecture/architecture-review-2026-05-29.html). A 2026-06-02 follow-up scan found that the surface ADR-018 deliberately left unguarded has, in fact, drifted — with user-visible bugs. This ADR reconciles that drift and extends the guard to exactly where drift occurred.

## Context

Two backends serve the same Vue frontend over the same D1 (the *Backend Overlap*, per CLAUDE.md / [ADR-017](../ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md)):

- **Python** — `apps/api/` + `javdb/storage/repos/` (FastAPI, Docker / self-host).
- **TypeScript Worker** — `JAVDB_AutoSpider_Web/server/` (Hono on Cloudflare Workers, cloud). Separate git repo.

[ADR-018](../ADR-018-Dual-Backend-Query-Contract/ADR-018-dual-backend-query-contract.md) introduced a **Contract Golden** that mechanically guards the **dynamic query builders** (history / sessions / stats-trend WHERE + cursor logic, 31 cases). That guard works — a 2026-06-02 scan confirmed the **guarded builders show zero drift**. ADR-018 D3 deliberately **left static single-statement queries, count statements, row-mappers, and response-shape *values* unguarded**, on the judgment that they "rarely drift" and pinning all ~46 `prepare()` sites would be low-leverage.

The 2026-06-02 scan disproved that judgment for a handful of high-traffic surfaces. The two backends currently return **different answers** to the same request:

| # | Divergence | Python | TypeScript | Severity |
| --- | --- | --- | --- | --- |
| 1 | session `write_mode` default (NULL `WriteMode`) — `sessions_repo.py:24,91` ↔ `server/routes/sessions.ts:32` | `"audit"` | `"pending"` | **Bug** — `audit` was retired by [ADR-005](../ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md) PR-4; Python's default is stale |
| 3 | history `total_estimate` count — `history_repo.py:505,584` ↔ `server/routes/history.ts:136` | capped `≤10000` | uncapped | **Bug** — different counts past 10k |
| 6 | stats `/summary` — `apps/api/routers/stats.py:170,183,188` ↔ `server/routes/stats.ts:38,67,111` | `total_torrents` ← `ReportTorrents`; `avg_duration`=null; `proxy_bans` log-derived | `total_torrents` ← `TorrentHistory`; `avg_duration` computed; `proxy_bans`=`0` | **Bug** — different numbers |
| 2 | `SessionList.total_estimate` dead field — `sessions_repo.py:39` | dataclass field, router omits | n/a | Cosmetic |
| 4 | revocation enforcement — `auth.py:222` ↔ `server/middleware/auth.ts:65` | every decode | mutations-only | Intentional (ADR-029) |
| 5 | `plain:` password escape hatch — `server/routes/auth.ts:16` | absent | dev-only | Intentional (TS dev) |
| 7 | `/capabilities` defaults — `capabilities.py:49` ↔ `server/routes/capabilities.ts:24` | `storage_backend="sqlite"`, real `git_sha` | `"d1"`, literal `"cloudflare"` | Deployment-intrinsic |

The JWT-revocation gap the 2026-05-29 review flagged is **closed** — both backends now implement revocation (`token-revocation.ts`); the only difference (#4) is the mutations-only enforcement scope, which is intentional per [ADR-029](../ADR-029-Web-Security-Hardening/ADR-029-web-security-hardening.md).

**Key insight.** ADR-018 D7 deferred "eliminate" (Phase 3 — collapse the two builders into one shared spec) "until the guard shows recurring drift." The evidence says the **guarded builders are not drifting**; the **unguarded static/response surface is**. So the right move is **not** to eliminate the builders (ADR-018 Phase 3), but to **fix the drift and widen the guard to exactly where drift happened** — leaving ADR-018's "low-leverage" judgment intact for the non-drifting majority.

## Decision

Reconcile the confirmed drift to one correct behavior per item, then extend the Contract Golden **narrowly** to the surfaces that actually drifted.

### Design Decisions

**D1. Reconcile the three real bugs to a single correct behavior (decisions baked in).**

- **D1a. `write_mode` NULL default → `"pending"` on both backends.** `audit` is retired ([ADR-005](../ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md) PR-4); pending is the only mode. **Python changes** (`sessions_repo.py` dataclass default + row-mapper).
- **D1b. history `total_estimate` → cap both at 10000.** Bounded `COUNT` cost on large D1 tables; UX shows "10000+". **TS changes** (add the `MIN(COUNT(*), 10000)` cap to the count statement).
- **D1c. stats `/summary` → `total_torrents` counts `TorrentHistory` on both.** "Total torrents" means the canonical history total, not per-session report rows. **Python changes** (`ReportTorrents` → `TorrentHistory`). `avg_duration_seconds` is computed on both (from `ReportSessions.CommittedAt`, as TS already does); **Python changes** (stop returning null).

**D2. `proxy_bans_last_7d` and `/capabilities` env fields are deployment-intrinsic — not contracted.** `proxy_bans` is log-derived on Python (which has the logs) and unavailable on the Worker (D1 has no such log) → each reports its best-effort value; it is **not** a cross-backend equality contract. Same for `storage_backend` / `deployment` / `git_sha`: each backend truthfully reports its *own* deployment. These are documented as intentionally backend-specific, not drift.

**D3. Leave the intentional auth differences as-is (documented).** Revocation enforcement scope (#4, mutations-only on TS) is owned by [ADR-029](../ADR-029-Web-Security-Hardening/ADR-029-web-security-hardening.md); the `plain:` dev hatch (#5) is a TS non-production affordance. Neither is reconciled here.

**D4. Clean up the cosmetic dead field (#2).** Remove the unpopulated `total_estimate` from the Python `SessionList` dataclass (or populate it); it is dangling and risks a latent shape divergence.

**D5. Extend the Contract Golden narrowly — only to the drifted points.** Two guard kinds:

- **D5a. Static-SQL cases.** Add the now-reconciled count statements (history `total_estimate` count, and any sibling count whose cap/source we pinned) to the ADR-018 golden as **fixed-SQL cases** (same `{normalized_sql, bindings}` mechanism, no DB). This catches a future re-divergence of those specific statements.
- **D5b. Pin the static `/summary` queries + the `write_mode` default with symmetric per-backend tests.** The reconciled surface that is *not* a dynamic builder is guarded the way ADR-018 D3 guards static queries — a unit test on **each** backend pinning its `/summary` query sources (`TorrentHistory`; the `CommittedAt` avg-duration formula; the `IsDeleted=1` dedup filter) and the `write_mode` NULL→`"pending"` default. *(Amended during IMP-ADR047-02 authoring: the originally-proposed standalone `docs/api/contract/response-values.golden.json` fixture is **not** built — the drifted surface is overwhelmingly SQL, so the count statements join the existing ADR-018 SQL golden under D5a, and the lone non-SQL value plus the handful of static queries are cheaper to pin with symmetric tests than to justify a new fixture type + cross-repo vendor pipeline.)*

**D6. Stay narrow — do NOT guard the broad static surface.** ADR-018 D3's judgment (the ~46 non-drifting `prepare()` sites are low-leverage to pin) **stands**. This ADR guards only the points with *demonstrated* drift. We are not adopting result-equivalence or full static-query pinning.

**D7. Cross-repo, lockstep execution.** Each fix touches both repos; the extended golden is the mechanical guard that the two stay aligned (Python regenerates → TS vendors + CI-checks, exactly as ADR-018 D5/D6 already wired via `repository_dispatch`).

**D8. Relationship to ADR-018 — extend, don't supersede.** ADR-018's guard-first/builders-only charter is unchanged; ADR-047 adds (a) a one-time drift reconciliation and (b) a *targeted, evidence-driven* widening of the same guard mechanism. A back-reference is added to ADR-018's Status Log.

## Consequences

### Positive

- **Users get consistent answers** from whichever backend serves them — the three real bugs (stale `audit`, capped-vs-uncapped counts, wrong `total_torrents` table) are removed.
- **The drifted surface can't silently re-diverge** — the narrow SQL golden extension + symmetric per-backend tests mechanize it, reusing ADR-018's proven distribution path where the guard is cross-repo.
- **Minimal, evidence-driven** — guards exactly what drifted; honours ADR-018's "low-leverage" judgment for everything else.
- **Builds on existing infrastructure** — no new cross-repo mechanism; the `repository_dispatch` re-vendor pipeline already exists.

### Negative

- **Cross-repo PRs** — each fix lands in both the Python repo and the TS repo, with the golden tying them together (the ADR-018 friction, now extended to a few more cases).
- **More per-backend tests** — static `/summary` queries and the `write_mode` mapper now have symmetric tests on both backends.

### Risks

- **Symmetric static-query tests could over-reach.** Mitigate: keep them to the handful of reconciled defaults and static queries with demonstrated drift; resist growing them into broad response snapshots (that is the OpenAPI contract's job).
- **A reconciliation decision proves wrong later** (e.g. `total_torrents` semantics). Mitigate: the affected SQL/count statements and summary semantics are now pinned by SQL golden cases plus symmetric backend tests, so changing them creates visible test/golden diffs across both backends.

## Implementation Roadmap

| Phase | Ships | Deferred |
| --- | --- | --- |
| **Phase 1 — Reconcile** | **Implemented locally (2026-06-04).** Fix the 3 real bugs in both repos (D1a Python `write_mode`→pending; D1b TS `total_estimate` cap; D1c Python stats `/summary` `total_torrents`→TorrentHistory + `avg_duration`); cosmetic dead-field cleanup (D4); document the intentional/deployment-intrinsic items (D2/D3) | The guard |
| **Phase 2 — Guard** | **Implemented locally (2026-06-04).** Extend the ADR-018 SQL golden with the reconciled count statements (D5a), re-vendor + CI-check in TS via the existing pipeline, and pin `/summary` + `write_mode` with symmetric per-backend unit tests (D5b) | Broad static-query guard (explicitly out — D6) |

### Explicit non-goals (YAGNI)

- **Not the broad static-query / response-snapshot guard** — ADR-018 D3's low-leverage judgment stands for the non-drifting majority (D6).
- **Not ADR-018 Phase 3 (eliminate)** — the guarded builders are not drifting, so collapsing them into a shared spec is not justified by current evidence.
- **Not auth reconciliation** — revocation scope and the `plain:` hatch stay (ADR-029 owns auth).
- **Not contracting deployment-intrinsic fields** — `proxy_bans`, `storage_backend`, `deployment`, `git_sha` truthfully differ per deployment (D2).

## Domain Language (additions for CONTEXT.md)

- **Symmetric static-query guard** — matched unit tests in both backends that pin a demonstrated-drift static query or mapper value without adding it to the cross-repo SQL golden. ADR-047 uses this for `/summary` (`TorrentHistory`, `CommittedAt`, `IsDeleted=1`) and `write_mode` NULL→`"pending"`.
- **Deployment-intrinsic field** — an API field that *correctly* differs between backends because it reports each deployment's own environment (`storage_backend`, `deployment`, `git_sha`, `proxy_bans_last_7d`); explicitly excluded from cross-backend equality contracts.

## Alternatives Considered

- **Fix the bugs without extending the guard** — rejected: the same surfaces would silently re-drift; the whole point of ADR-018 is mechanical detection.
- **Eliminate (ADR-018 Phase 3 — shared filter spec)** — rejected for now: the evidence shows the *builders* aren't drifting; eliminating them is effort spent where the problem isn't.
- **Broadly guard all static queries / full response snapshots** — rejected: ADR-018 D3 already weighed this as low-leverage; the 2026-06-02 evidence justifies guarding only the drifted points, not all ~46 sites.

## References

- [ADR-018 — Dual-Backend Query Contract](../ADR-018-Dual-Backend-Query-Contract/ADR-018-dual-backend-query-contract.md)
- [ADR-017 — Cloudflare-First Deployment](../ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md)
- [ADR-029 — Web Security Hardening](../ADR-029-Web-Security-Hardening/ADR-029-web-security-hardening.md)
- [ADR-005 — db.py Retirement & Repo Pattern](../ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md)
- 2026-05-29 architecture review (Candidate B): [architecture-review-2026-05-29.html](../../architecture/architecture-review-2026-05-29.html)

## Status Log

- 2026-06-02: Proposed. From the 2026-05-29 review Candidate B + a 2026-06-02 cross-repo scan that found drift in ADR-018's deliberately-unguarded static/response surface (7 divergences; 3 real user-facing bugs). Decisions baked in: `write_mode`→`pending` (D1a), `total_estimate` cap-both-at-10000 (D1b), stats `total_torrents`→`TorrentHistory` (D1c); narrow guard extension only (D6). Reframes Candidate B away from ADR-018 Phase 3 ("eliminate") toward fix-the-drift + widen-the-guard, since the guarded builders are clean and the unguarded surface is where drift occurred.
- 2026-06-02: **IMPs written** (IMP-ADR047-01 reconcile, IMP-ADR047-02 guard). **D5b amended** during authoring: the standalone `response-values.golden.json` fixture is dropped — the count statements join ADR-018's SQL golden (D5a); the static `/summary` queries + `write_mode` default are pinned by symmetric per-backend unit tests. Rationale: the drifted surface is mostly SQL; one non-SQL value does not justify a new artifact type + vendor pipeline.
- 2026-06-04: **Phase 1 and Phase 2 implemented locally** across the Python repo and the separate TypeScript Worker repo. Phase 1 reconciled `write_mode` NULL→`pending`, `total_estimate` capped count behavior, `/summary.total_torrents` from `TorrentHistory`, `avg_duration_seconds` from `CommittedAt`, and the dedup `IsDeleted=1` filter. Phase 2 added `movie_count` / `torrent_count` SQL golden cases, re-vendored the TS fixture, added TS conformance, and pinned `/summary` + `write_mode` with symmetric tests. Drift simulations confirmed the guard: changing the TS cap to `9999` makes 22 conformance cases fail; changing the Python cap to `9999` regenerates a visible golden diff.
