# ADR-055: Dual-Backend Contract Single-Source (SQL-fragment + constant codegen)

**Status:** Proposed — executes ADR-018's deferred D7 ("eliminate") and widens scope to mutations + shared constants
**Date:** 2026-06-15
**Author:** Ted
**Related Implementation Plans:** [IMP-ADR055-01](IMP-ADR055-01-registry-generator-watchintent.md) (Phase 1 — registry + generator + CI + WatchIntent migration)
**D1 Write Class:** n/a (this ADR ships a codegen/contract mechanism; each centralized fragment keeps its own existing write class — e.g. WatchIntent stays `authoritative`)
**Related:** [ADR-018](../ADR-018-Dual-Backend-Query-Contract/ADR-018-dual-backend-query-contract.md) (query contract — guard for dynamic SELECT builders; this executes its deferred D7), [ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md) (dual-backend split), [ADR-054](../ADR-054-User-Intent-Discovery-Layer/ADR-054-user-intent-discovery-layer.md) (WS1 verification surfaced gap B6), [ADR-029](../_archive/ADR-029-Web-Security-Hardening/ADR-029-web-security-hardening.md) (auth — out of scope), [ADR-042](../ADR-042-D1-Atomic-Commit-Boundaries/ADR-042-d1-atomic-commit-boundaries.md) (D1 write classes)

## Context

[ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md) split one Vue frontend across **two independently-deployed backends in different languages and repos**:

- **Python** — `apps/api/` + `javdb/storage/` (FastAPI, Docker / local).
- **TypeScript** — `JAVDB_AutoSpider_Web/server/` (Hono on Cloudflare Workers).

Overlapping query/mutation logic must produce equivalent results regardless of which backend answers. [ADR-018](../ADR-018-Dual-Backend-Query-Contract/ADR-018-dual-backend-query-contract.md) mechanized part of this — a Python-sourced **Contract Golden** for the **dynamic SELECT builders**, vendored to TS and pinned by CI freshness + conformance. But ADR-018 D3 scoped that guard to dynamic SELECT builders **only**; static statements, mutations, and shared data constants were left to a prose rule plus per-repo tests. ADR-018 D7 — "**eliminate**" (a single source of truth, not just a drift guard) — was **deferred** "until recurring drift justifies it."

[ADR-054](../ADR-054-User-Intent-Discovery-Layer/ADR-054-user-intent-discovery-layer.md) WS1 verification surfaced that recurrence as **gap B6**. The `WatchIntent` UPSERT SQL exists as **four hand-maintained copies**:

| # | Location | Role |
| --- | --- | --- |
| 1 | `javdb/storage/repos/watchlist_repo.py` `WATCH_INTENT_UPSERT_SQL` | Python production |
| 2 | `tests/unit/test_watch_intent_upsert_parity.py` `CANONICAL` | Python test baseline |
| 3 | `JAVDB_AutoSpider_Web/server/services/watchlist-service.ts` `WATCH_INTENT_UPSERT_SQL` | TS production |
| 4 | `JAVDB_AutoSpider_Web/server/__tests__/watch-intent-upsert-parity.test.ts` `CANONICAL` | TS test baseline |

Each repo's parity test only asserts **its own production == its own CANONICAL** (intra-repo). **Nothing asserts `#2 == #4`.** A change to #1+#2 (Python green) that forgets the TS side leaves #4 unchanged, so the TS test stays green too — **cross-repo drift is silent**. The "guard" is two independent intra-repo self-checks plus a `// MUST be character-identical` comment. The same shape is already queued to recur: the [ADR-040](../ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.md) WS4a content-filter allow-list (`VALID_RULE_MODES` / `VALUE_REQUIRED`), the `system_state` upsert, the `ReportSessions` column list. This is the recurrence that justifies executing D7.

## Decision

Execute ADR-018's deferred D7 as a dedicated mechanism, **widened to all static cross-backend fragments**. Establish a **Python-sourced contract registry** that **generates** the TypeScript mirror — SQL strings, **typed bind helpers**, and shared constants — so **neither repo hand-writes them**. Distribute the generated artifact via the existing `openapi.json` / `api.gen.ts` cross-repo path. Dynamic SELECT builders remain under ADR-018's guard (explicit non-goal).

### Design Decisions

**D1. Single source = a Python contract registry (`javdb/storage/contract/`).** Every shared fragment/constant is declared **once** as structured data: SQL text (SQLite, `?` placeholders) + ordered typed params; or a constant's name + value + kind. This is the only hand-edited location. Follows ADR-018 D1's grain (source of truth = Python).

**D2. Eliminate, not guard — the generated artifact is *production* code TS imports, not a test fixture.** This is the structural break from ADR-018, whose golden lives in `server/__tests__/fixtures/` while TS keeps its own hand-written builder (the golden only *detects* drift). Here, TS's SQL and bind logic **is** the generated module under `server/contract/`, and there is no second hand-written copy. Drift cannot exist because there is exactly one author.

**D3. Scope = all *static* shareable fragments.** Mutation SQL (`INSERT` / `UPSERT` / `UPDATE` / `DELETE`), static `SELECT` strings, and hand-mirrored data constants (allow-lists / enums). **Dynamic SELECT builders are out** — they are conditional logic, not strings; eliminating them needs a query DSL (rejected, see Alternatives). They stay under ADR-018's guard.

**D4. Same SQLite dialect makes elimination cheap.** D1 *is* SQLite; Python `sqlite3` and the D1 `prepare()` API both use `?` positional binding. The shared SQL string is already byte-identical and already executes verbatim on both sides (proven by the current B6 code). "Generation" is therefore **emission, not dialect translation**.

**D5. Typed bind helpers eliminate bind-order drift.** The registry declares params in order with types. The generator emits, per fragment, a TS `bindXxx(stmt, { camelCaseParams }): D1PreparedStatement`; the Python side uses a generic `order_params(fragment, **kwargs)` that orders by the registry. **Neither repo hand-writes the `.bind()` / tuple order**, closing the "tokens unchanged but bind-order changed" semantic-drift risk that paired string tests cannot catch. `snake_case` (Python/SQL) → `camelCase` (TS object keys) mapping is mechanical and deterministic.

**D6. No Python codegen — the registry is consumed directly.** The registry *is* Python, so production Python imports the SQL const and the generic binder from it. Only the TS mirror is generated. This avoids the awkwardness of generating Python from Python and keeps the Python developer experience unchanged.

**D7. Distribution + CI reuse the `openapi.json` / `api.gen.ts` path (ADR-018 D4/D5/D6).** A generator `apps/cli/ops/dump_sql_contract.py` (next to `dump_openapi` / `dump_query_contract`) emits `docs/api/contract/sql-contract.gen.ts`, committed in the Python repo. Python CI runs a **freshness** test (`regen == committed`). The web repo adds `scripts/fetch-sql-contract.mjs` (`gen:sql-contract`, mirroring `fetch-openapi.mjs` / `fetch-query-golden.mjs`) that vendors the artifact to `server/contract/sql-contract.gen.ts`; TS CI runs **freshness** (re-fetch Python-`main`, `git diff --quiet`) + **conformance** (execute each fragment, assert column→value semantics). The cross-repo race (a Python-`main` change reds TS CI until re-vendored) is **accepted**, exactly as for `openapi.json`.

**D8. Per-fragment behavioral smoke is mandatory.** Each fragment carries one execute-and-read-back test in each repo (distinct value per column → assert the mapping; plus `COALESCE`/delete semantics where relevant). With typed helpers this is a safety net rather than the primary guard, but it pins runtime behavior and doubles as the TS conformance test.

**D9. Relationship to ADR-018 — extend, don't supersede.** ADR-018's guard for dynamic SELECT builders stands unchanged. ADR-055 executes its deferred D7 for the *static* surface and widens scope to mutations + constants. A back-reference is added to ADR-018's Status Log.

## Consequences

### Positive

- **Kills the cross-repo drift class for all static fragments** — one author, mechanically mirrored, CI-locked at the same trust level as `api.gen.ts`.
- **B6 resolved structurally** — four copies collapse to one registry entry; the queued WS4a allow-list and other instances get the mechanism for free.
- **Typed bind helpers remove the bind-order semantic-drift risk entirely** (the one thing paired string tests could not catch).
- **Reuses proven machinery** — the openapi / query-contract generate→vendor→CI shape; minimal new concepts.

### Negative

- **A third generated-artifact pipeline + CI** on both repos (mitigated: identical shape to the two existing ones).
- **Registry authoring is more verbose** than a bare SQL const — params must be declared with types (the cost of typed helpers).
- **Cross-repo race remains** (Python `main` change reds TS CI until re-vendor) — accepted, consistent with openapi.
- **Dynamic builders remain dual-maintained** under ADR-018 (not eliminated this round).

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 | [IMP-ADR055-01](IMP-ADR055-01-registry-generator-watchintent.md) | `javdb/storage/contract/` registry + `dump_sql_contract.py` + `sql-contract.gen.ts` + Python freshness CI + web `fetch-sql-contract.mjs` vendor + TS freshness/conformance CI + **migrate the WatchIntent upsert end-to-end** (4 copies → 1 registry entry; delete the hand-CANONICAL parity tests) | other instances |
| Phase 2 | IMP-ADR055-02 (written against real shapes when targets land) | migrate remaining static mirror points: WS4a allow-list (`VALID_RULE_MODES`/`VALUE_REQUIRED`), `system_state` upsert, `ReportSessions` column list, mirrored static SELECTs | — |
| Phase 3 | (convention, no IMP) | ADR mandate + PR checklist: every new dual-backend static SQL/constant enters the registry | — |

### Explicit non-goals (YAGNI)

- **Dynamic SELECT builders** — ADR-018 owns them; not eliminated here.
- **No ORM / query DSL** — emission of static strings only.
- **No auth logic** — owned by ADR-029 (TS-only sole live surface).
- **No runtime artifact loading in TS** — the generated-then-vendored module *is* the elimination notion for Workers (compile-time import, not a runtime fetch).

## Alternatives Considered

- **Better guard only** (a cross-repo CANONICAL diff test) — rejected: the chosen goal is elimination; a guard still leaves two hand-written copies.
- **Runtime-shared JSON loaded by both** — rejected: awkward in Workers, loses compile-time typing, creates a Python author/consumer circularity.
- **Neutral spec → bidirectional codegen** (Python + TS both generated) — rejected: introduces a third authoring format and violates ADR-018's "source of truth = Python"; heaviest.
- **Eliminate dynamic builders via a query DSL** — rejected this round: large, risky, and would replace ADR-018's working guard.

## Domain Language (additions for CONTEXT.md)

- **Contract registry** — the Python single source (`javdb/storage/contract/`) declaring every static cross-backend SQL fragment and shared constant once.
- **SQL fragment** — a named static SQL statement (mutation or static select) with ordered typed params, shared across both backends.
- **Generated contract module** — `sql-contract.gen.ts`, the TS mirror emitted from the registry; production code, vendored like `api.gen.ts`.

## References

- [ADR-018 — Dual-Backend Query Contract](../ADR-018-Dual-Backend-Query-Contract/ADR-018-dual-backend-query-contract.md)
- [ADR-017 — Cloudflare-First Deployment](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md)
- [ADR-054 — User-Intent & Discovery Layer](../ADR-054-User-Intent-Discovery-Layer/ADR-054-user-intent-discovery-layer.md) (WS1 verification, gap B6)
- [ADR-042 — D1 Atomic Commit Boundaries](../ADR-042-D1-Atomic-Commit-Boundaries/ADR-042-d1-atomic-commit-boundaries.md) (D1 write classes)

## Status Log

- 2026-06-15: Proposed. Brainstormed from ADR-054 WS1 gap B6. Decisions fixed: eliminate (not guard); scope = all static fragments (mutation SQL + static select + constants), dynamic builders stay under ADR-018; mechanism = Python registry → codegen TS via the openapi/`api.gen.ts` path; typed bind helpers both sides (no hand-written bind order). Phase 1 → [IMP-ADR055-01](IMP-ADR055-01-registry-generator-watchintent.md).
