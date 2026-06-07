# ADR-034: Media Closed-Loop Web Surface

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Proposed — web counterpart to ADR-033; phased to mirror it            |
| **Date**    | 2026-05-29                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md), [ADR-008](../_archive/ADR-008-Frontend-Rewrite/ADR-008-frontend-rewrite-architecture.md), [ADR-027](../_archive/ADR-027-Stats-Dashboard-Charts/ADR-027-stats-dashboard-chart-expansion.md), [ADR-030](../_archive/ADR-030-Web-Feature-Parity/ADR-030-web-feature-parity.md), [ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md) |

> Originated from a 2026-05-29 brainstorming session (with the visual companion)
> on the web surface for the [ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md)
> media closed-loop.

## Context

[ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md) builds a
three-layer media closed-loop and persists it to new D1 tables
(`AcquisitionOutcome`, then `OwnershipLedger`, `ConsumptionSignal`). That data is
currently **invisible** — there is no web surface for it. The operator runs the
Vue console (`javdb-autospider-web`, ADR-008) but cannot see whether last night's
selected torrents actually landed, what is owned across sources, or what has been
watched.

The web platform is **two backends behind one Vue frontend** (ADR-017): a
TypeScript Worker (Hono, D1 queries on Cloudflare) and a Python FastAPI backend
(full local execution). ADR-018/030 require their overlapping query surface to
stay in parity. Any new read surface for the closed-loop must therefore be
designed as a dual-backend contract, not a single-backend feature.

This ADR defines the **web surface** for the closed-loop: navigation placement,
the Phase-1 Acquisition view, the dual-backend read endpoints, and capability
gating. It is phased to mirror ADR-033 — only `AcquisitionOutcome` exists in
Phase 1, so only the Acquisition view ships first.

## Decision

Add a new top-level **Library** page to the Vue console with three sub-tabs
(Acquisition / Ownership / Consumption), backed by **dual-backend, read-only**
endpoints over the ADR-033 D1 tables, gated by a `closed_loop` capability flag.
Ship the Acquisition view first; Ownership and Consumption follow ADR-033's
Phases 2 and 3.

### Design Decisions

**D1. A new top-level "Library" page with three sub-tabs.** The closed-loop is a
distinct domain ("what I acquired / own / watched"), not "more stats", so it gets
its own page rather than folding into the Stats dashboard. Sub-tabs:
`Acquisition` (Phase 1), `Ownership` (Phase 2, disabled placeholder), `Consumption`
(Phase 3, disabled placeholder). Chosen over folding into Stats (rejected: buries
a content domain inside analytics) and a purpose-split across Tasks+Library
(rejected: two homes for one story).

**D2. The Acquisition view is read-only: funnel + KPI cards + recent table.** A
horizontal funnel (`queued → downloading → completed`), five KPI cards
(queued / downloading / completed / stalled / failed), and a recent-acquisitions
`NDataTable` with state chips and a state filter. **No mutations in Phase 1** —
`Re-queue`/`Dismiss` actions are explicitly deferred (they would re-add to qB,
which only the LAN-connected Python backend can do; see Non-Goals).

**D3. Dual-backend full parity for the read endpoints.** Three read-only
endpoints are implemented in **both** the TS Worker (`server/routes/library.ts`)
and the Python backend (`apps/api/routers/library.py`), executing identical SQL
over the D1 `AcquisitionOutcome` table, behind the existing JWT auth middleware:

| Endpoint | Response | Drives |
| --- | --- | --- |
| `GET /api/library/acquisition/summary` | `{queued, downloading, completed, stalled, failed, total}` | KPI cards + funnel |
| `GET /api/library/acquisition/recent?state=&limit=&offset=` | `[{qb_hash, video_code, href, category, state, queued_at, completed_at, last_seen_at}]` | recent table |
| `GET /api/library/acquisition/trend?period=30d` | `[{date, completed, stalled, failed}]` | optional trend chart (ADR-027 trend shape) |

The shapes are captured in `openapi.json` (generated from the Python app, consumed
by the TS frontend for types), so both backends and the frontend share one
contract. This honors the ADR-018/030 parity rule in both directions.

**D4. Capability gating via a `closed_loop` flag — capability honesty.**
`GET /api/capabilities` gains a `closed_loop` boolean (true when the
`AcquisitionOutcome` table exists / reconcile is configured). The frontend hides
the Library nav entry when the flag is false, so a deployment without the
closed-loop tables never shows a broken page. This extends the ADR-008 D5
capabilities-driven discovery pattern.

**D5. The frontend is phased to mirror ADR-033.** FE Phase 1 ships the Library
shell + Acquisition view + the three read endpoints. FE Phase 2 adds the
Ownership view (when `OwnershipLedger` lands). FE Phase 3 adds the Consumption
view at `(instance, library)` granularity (when `ConsumptionSignal` lands). The
disabled placeholder tabs make the roadmap legible to the operator.

**D6. i18n parity (en + zh) for all new strings.** Every new label, KPI title,
state chip, and table header is added to both `en` and `zh` locale files in the
same change — translation drift is a defect (mirrors the repo's bilingual rule).

**D7. The cross-repo boundary is explicit.** The Vue components and the TS route
land in the standalone `javdb-autospider-web` repo; the Python router lands in
this monorepo (`apps/api/routers/library.py`); `openapi.json` is the seam. The
IMP enumerates which file lands in which repo so the work is not mistaken for a
single-repo change.

## Consequences

### Positive

- The closed-loop data becomes **visible** — the operator can finally see whether
  selected torrents landed, and where they stalled.
- **Parity by construction** — one contract (`openapi.json`), two backend
  adapters; both deployment topologies (Cloudflare console, local Docker) serve
  the page.
- **Capability-honest** — the page only appears where it can be served.
- **Additive & legible** — Ownership/Consumption slot into the same page as
  ADR-033 Phases 2/3 land; the disabled tabs advertise what's coming.

### Negative

- **Two implementations of the same SQL** (TS + Python) — the exact parity cost
  the 2026-05-29 architecture review flagged as Candidate B; accepted here as the
  ADR-018/030 status quo until a shared query seam exists.
- **A new capability flag to wire** through capabilities + frontend gating.
- **Read-only first** — operators who want to re-queue a stalled torrent from the
  UI must wait for a later, Python-only actions increment.

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| FE Phase 1 — Acquisition | IMP-ADR034-01 (stub) | Library page shell (3 tabs, 2 disabled); Acquisition view (funnel + KPI + recent table, read-only); `GET /api/library/acquisition/{summary,recent,trend}` in **both** backends; `closed_loop` capability flag + nav gating; en/zh strings | Ownership/Consumption views; any mutations |
| FE Phase 2 — Ownership | IMP-ADR034-02 (stub) | Ownership view over `OwnershipLedger` | — |
| FE Phase 3 — Consumption | IMP-ADR034-03 (stub) | Consumption view at `(instance, library)` granularity over `ConsumptionSignal` | — |

FE Phase 1 depends only on ADR-033 Phase 1 (`AcquisitionOutcome`). Phases 2/3
depend on ADR-033 Phases 2/3 and are detailed once those land.

**Planning cadence.** No FE IMP is written yet. **IMP-ADR034-01 is intentionally
deferred** until ADR-033 Phase 1 (`AcquisitionOutcome` + the read endpoints' data)
lands; its detailed plan — and IMP-ADR034-02/03 — will be produced in a `grill-me`
+ `brainstorming` round after the corresponding backend phase ships, so the FE
plan reflects the real endpoint shapes rather than a paper contract.

### Explicit non-goals (YAGNI)

- **No mutations in Phase 1** — no `Re-queue` / `Dismiss` / `Open in qB`. These
  need the LAN-connected Python backend and are a later, clearly-scoped actions
  increment, not part of the read surface.
- **No realtime** — the page fetches on load / manual refresh; no websockets.
- **No Emby/Plex per-library drill-down** until Phase 3 (the data does not exist
  before `ConsumptionSignal`).
- **No new chart library** — reuse `vue-chartjs` (Chart.js), already in the app
  per ADR-027.

## Alternatives Considered

- **Fold the closed-loop into the Stats dashboard** (placement B) — rejected
  (D1): buries a content domain inside analytics tabs.
- **Split by purpose: funnel in Tasks, ownership/consumption in Library**
  (placement C) — rejected (D1): two homes for one coherent story.
- **An actionable ops console for Phase 1** (layout B) — rejected (D2): mutations
  need the Python backend and add risk atop a brand-new reconcile loop.
- **TS-only or Python-only endpoints** — rejected (D3): breaks ADR-018/030
  parity; the page would 404 on the other deployment topology.

## References

- [ADR-033 — Media Closed-Loop](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md)
- [ADR-008 — Frontend Rewrite](../_archive/ADR-008-Frontend-Rewrite/ADR-008-frontend-rewrite-architecture.md)
- [ADR-027 — Stats Dashboard Chart Expansion](../_archive/ADR-027-Stats-Dashboard-Charts/ADR-027-stats-dashboard-chart-expansion.md)
- [ADR-030 — Web Feature Parity](../_archive/ADR-030-Web-Feature-Parity/ADR-030-web-feature-parity.md)
- [ADR-017 — Cloudflare-First Deployment](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md)

## Status Log

- 2026-05-29: Proposed (web counterpart to ADR-033; FE Phase 1 scoped, IMP pending).
- 2026-05-29: IMP-ADR034-01 deferred until ADR-033 Phase 1 lands; FE IMPs to be
  detailed via a post-backend `grill-me` + `brainstorming` round.
