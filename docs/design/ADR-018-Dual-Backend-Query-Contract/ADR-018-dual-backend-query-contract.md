# ADR-018: Dual-Backend Query Contract — Golden-Fixture Drift Guard

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Accepted — Phase 1 (Python golden) implemented 2026-05-29; Phase 2 (TS consume) pending |
| **Date**    | 2026-05-29                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-029](../ADR-029-Web-Security-Hardening/ADR-029-web-security-hardening.md) (auth hardening — owns token revocation), [ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md) (dual-backend split), [ADR-010](../_archive/ADR-010-D1-Access-Port/ADR-010-d1-access-port.md) (D1 access port) |

> Originated from the 2026-05-29 architecture review (Candidate B): [architecture-review-2026-05-29.html](../architecture/architecture-review-2026-05-29.html).

## Context

The frontend is served by **two backends with overlapping logic** that must stay in sync (the *Backend Overlap*, per CLAUDE.md):

- **Python backend** — `apps/api/` + `javdb/storage/repos/` (FastAPI, Docker / local self-host).
- **TypeScript backend** — `JAVDB_AutoSpider_Web/server/routes/` (Hono on Cloudflare Workers, cloud).

These are **separate git repos**, deployed independently, but run the *same Vue frontend* — so a query must produce equivalent results regardless of which backend answers it. Today the only thing keeping the overlapping query logic aligned is a **prose rule** in CLAUDE.md ("modify one backend → update the other in the same PR"). There is no mechanical guard, and drift is silent.

### What is already covered (and out of scope here)

- **API response shapes** — already a single source of truth: `docs/api/openapi.json` is generated from the Python app and consumed by the TS repo via `scripts/fetch-openapi.mjs` → `openapi-typescript`, with contract tests (`server/__tests__/contract-compliance.test.ts`, `tests/contract/openapi-shapes.spec.ts`) pinning the TS responses. **Not re-litigated here.**
- **Token revocation / auth hardening** — owned by [ADR-029](../ADR-029-Web-Security-Hardening/ADR-029-web-security-hardening.md) (KV-backed, TS-only, mutations-only). The deployment topology is **TS Worker is the sole live auth surface** (Cloudflare-first); a given deployment authenticates against one backend, so cross-backend revocation consistency is not required. **Out of scope here.**

### The remaining gap

The **dynamic query builders** are duplicated verbatim across the two repos with no guard. The clearest example — the movie-history filter builder:

- Python: `javdb/storage/repos/history_repo.py:240` — `_build_movie_filters()`
- TypeScript: `JAVDB_AutoSpider_Web/server/routes/history.ts:69` — `buildMovieQuery()`

```sql
# both backends, character-identical:
(m.VideoCode LIKE ? OR m.ActorName LIKE ? OR m.SupportingActors LIKE ?)
... m.PerfectMatchIndicator = ? ...
```

The same shape recurs in the dynamic filter/cursor logic of `history`, `sessions`, and `stats`. A change to the WHERE clause, an added filter, or a tweak to cursor encoding in one repo silently diverges from the other until a user notices wrong results.

## Decision

Introduce a **Contract Golden**: Python-generated, language-neutral golden fixtures that both backends' tests assert against. This is a **drift guard** (detection-locality), not yet a single source of truth — per the agreed "guard first, eliminate later" sequencing.

### Design Decisions

**D1. Source of truth = Python; generator lives in `apps/cli/ops/`.** A new CLI tool (next to the existing `dump_openapi`) emits the golden fixtures. Python is already the source of truth for `openapi.json`; the Contract Golden follows the same grain.

**D2. Granularity = normalized SQL string + bindings, not result rows.** Each fixture maps a canonical set of filter params → `{ normalized_sql, bindings[] }`. Language-neutral, requires no database, and directly catches builder drift. (Whitespace is collapsed to a canonical form so formatting differences don't cause false failures.) Result-equivalence against a seeded D1 is explicitly *not* chosen now — heavier, needs a shared seed, and the builders are where drift actually lives.

**D3. Scope = dynamic builders only.** Pin the dynamic filter + cursor builders in `history` / `sessions` / `stats`. Static single-statement queries are left to the existing response-shape contract test — they rarely drift and pinning all ~46 `prepare()` sites would be low-leverage maintenance. (Phasing: `history` + `sessions` land in Phase 1; `stats` needs a small router→builder extraction first — its Python aggregations are inline in `apps/api/routers/stats.py`, not a repo builder — and lands in Phase 2.)

**D4. Distribution reuses the `openapi.json` cross-repo path.** Goldens are committed in the Python repo under `docs/api/contract/`. The TS repo fetches them exactly as it fetches the OpenAPI schema (`fetch-openapi.mjs`: local `OPENAPI_PATH`-style override in dev, GitHub raw URL in CI). No new distribution mechanism.

**D5. The cross-repo guard is the vendored golden + CI checks.** The CLAUDE.md "same PR" rule cannot be literal across two repos. Instead: a Python PR that changes a builder regenerates the golden (a **visible diff** in review); the TS repo vendors that golden and its CI fails if the vendored copy is stale or its builder diverges (see D6). The artifact mechanizes the prose rule.

**D6. Drift detection mirrors the `openapi.json` / `api.gen.ts` pattern; re-vendor is dispatch-automated.** An earlier draft proposed pinning the golden to a version/SHA to avoid a main-branch race. **Rejected** — it diverges from the house pattern, which deliberately accepts the race in exchange for *synchronous* drift detection. Concretely:

- **Vendored + two CI checks.** The golden is committed into the TS repo exactly as `src/types/api.gen.ts` is. TS CI runs (1) a *freshness* step that re-fetches the Python-`main` golden and `git diff --quiet`s it against the vendored copy — catching Python-side drift (stale vendor) — and (2) the vitest *conformance* test that runs `buildMovieQuery` etc. over the vendored golden cases — catching TS-side drift. This is the exact shape of the existing openapi gen-diff step (`ci.yml`) + `contract-compliance` test.
- **Accept the race.** A Python-`main` builder change turns TS CI red (on all PRs) until the golden is re-vendored — the same friction already accepted for openapi. Synchronous detection is the goal, not race-avoidance.
- **Re-vendor is automated via `repository_dispatch`.** When the golden changes on Python `main`, the Python repo's CI dispatches an event to the TS repo, which auto-opens a *re-vendor + reconcile* PR (runs the vendoring script, commits the refreshed golden). A human still updates the TS builder to match and merges. (Needs a cross-repo token; detail in IMP-ADR018-02.)
- **Golden `version` = content hash.** The golden's version is a hash of its cases (not a hand-bumped string) — any content change is self-evident and can ride the dispatch payload, with no forgotten-bump footgun.

**D7. "Eliminate" is deferred.** Collapsing the two builders into a single shared *filter spec* (a declarative field→column+operator+order table that both builders derive from) is the eventual single-source-of-truth endpoint. Deferred until the guard shows the duplication keeps drifting.

## Consequences

### Positive

- **Detection-locality** — divergence in the overlapping query builders fails CI instead of surfacing as wrong results in production.
- **Mechanizes the manual rule** — the CLAUDE.md "sync both backends" prose becomes an enforced artifact.
- **No new infrastructure** — reuses the existing Python-generates / TS-consumes pipeline; no new Cloudflare resource, no new service.
- **Cheap test surface** — SQL-string + bindings goldens run without a database in both pytest and vitest.

### Negative

- **Golden regeneration on intentional change** — any deliberate query-builder change requires regenerating + committing the golden (a visible, reviewable diff, but an extra step).
- **Cross-repo CI coupling + versioning** — TS CI gains a dependency on a Python-repo artifact; the version/pinning detail (D6) adds modest complexity.
- **SQL-string brittleness** — string equality is sensitive to formatting; mitigated by canonical normalization, but a normalization bug could cause false positives.

### Risks

- **Normalization drift** — if the two backends format SQL differently in ways the normalizer doesn't fold, the guard produces false failures. Mitigate by sharing a tiny normalization spec in the golden contract.
- **Scope creep toward result-equivalence** — resist expanding to seeded-D1 result checks unless string-level guarding proves insufficient.

## Implementation Roadmap

| Phase | IMP (planned) | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 | [IMP-ADR018-01](IMP-ADR018-01-python-golden-generator.md) | Golden generator in `apps/cli/ops/`, golden committed to `docs/api/contract/`, pytest pins history movie+torrent filters (`_build_movie_filters`/`_build_torrent_filters`) + sessions query (extracted `_build_session_query`) | `stats` (needs router→builder extraction) |
| Phase 2 | [IMP-ADR018-02](IMP-ADR018-02-ts-consume-and-dispatch.md) | Vendored golden in TS repo, CI freshness-diff vs Python `main` + vitest conformance for `buildMovieQuery` etc., **`stats` aggregation builders** (after extraction), `repository_dispatch` re-vendor automation (D6) | — |
| Phase 3 | IMP-ADR018-03 (eliminate, optional) | Shared filter spec; both builders derived from it (D7) | Until guard shows recurring drift |

## Out of Scope

- **Auth / token revocation** — owned by [ADR-029](../ADR-029-Web-Security-Hardening/ADR-029-web-security-hardening.md).
- **API response shapes** — already guarded by `openapi.json` + contract tests.
- **Cross-backend token consistency** — not required (TS Worker is the sole live auth surface).
- **Static single-statement queries** — left to the response-shape contract test.

## Status Log

- 2026-05-29: Proposed (from architecture review Candidate B grilling).
- 2026-05-29: D6 revised after grilling — mirror the `openapi.json` / `api.gen.ts` pattern (vendored golden + CI freshness-diff, accept the main-branch race); re-vendor automated via `repository_dispatch`; golden `version` = content hash. Earlier "pin to version/SHA" idea rejected as inconsistent with the house pattern.
- 2026-05-29: Phase 1 implemented ([IMP-ADR018-01](IMP-ADR018-01-python-golden-generator.md)). `_build_session_query` extracted from `SessionsRepo.list` (behavior-preserving); the golden generator (`apps/cli/ops/dump_query_contract.py`), shared cases + `normalize_sql` (`query_contract_cases.py`), the committed golden (`docs/api/contract/query-builders.golden.json`, content-hash `version`), and the pytest pin (`tests/unit/test_query_contract_golden.py`) all landed. Covers history movie+torrent filters + sessions query; every builder branch is pinned. **`stats` confirmed deferred to Phase 2** (its aggregations are inline in `apps/api/routers/stats.py`, needing a router→builder extraction first). Phase 1 roadmap row = history + sessions, as planned.
