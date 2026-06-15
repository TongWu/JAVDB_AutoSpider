# ADR-054: User-Intent & Discovery Layer

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Proposed — umbrella; routes work to phases/child-ADRs; ships no code itself |
| **Date**    | 2026-06-13                                                            |
| **Authors** | Ted                                                                   |
| **D1 Write Class** | n/a (umbrella ships no code; WS1/WS2 introduce **authoritative** writes, classified in their own IMPs) |
| **Related** | [ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md), [ADR-034](../ADR-034-Media-Closed-Loop-Web-Surface/ADR-034-media-closed-loop-web-surface.md), [ADR-040](../ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.md), [ADR-039](../ADR-039-Pluggable-Integration-Platform/ADR-039-pluggable-integration-platform.md), [ADR-024](../ADR-024-Torrent-Quality-Evidence/ADR-024-torrent-quality-evidence.md), [ADR-022](../_archive/ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md), [ADR-025](../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md), [ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md) |

> Originated from a 2026-06-13 brainstorming session triaging the
> [Adsryen/JavdBviewed](https://github.com/Adsryen/JavdBviewed) browser extension
> (a mature JAVDB augmentation suite, ~39 feature modules) for features worth
> porting into our Vue + Cloudflare web app.

## Context

The [JavdBviewed](https://github.com/Adsryen/JavdBviewed) browser extension is a
mature JAVDB companion: a Manifest-V3 content-script suite that augments
javdb.com pages plus a dashboard, storing a `VideoRecord` / `ActorRecord` /
`ListRecord` model in IndexedDB with `viewed | browsed | want | untracked`
status, actor subscriptions with scheduled new-works monitoring, multi-source
magnet aggregation, content filtering, AI translation, and more. It demonstrates
the user-facing JAVDB features our system lacks.

Our media closed-loop ([ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md)
backend, [ADR-034](../ADR-034-Media-Closed-Loop-Web-Surface/ADR-034-media-closed-loop-web-surface.md)
web surface) records **what the system did** — `AcquisitionOutcome` (what was
queued/downloaded), `OwnershipLedger` (what is owned across qB/NAS/GDrive/PikPak),
`ConsumptionSignal` (what a media server reports as watched). There is **no
representation of what the operator _wants_** — no wishlist, no "I've seen this",
no followed actors. That demand-side gap is exactly what the extension's core
features fill, and it is the natural complement to the closed-loop's supply side.

Two constraints shape this initiative:

1. **Delivery vehicle (decided).** We port **data and logic into the standalone
   SPA only** — no browser extension and no javdb.com DOM injection. The
   extension's most "native" features (hover trailers, cover swap, detail-page
   button injection, keyboard shortcuts, screenshot-blur, comment/paywall unlock,
   anchor nav, password autofill) only make sense overlaying javdb.com itself and
   are **out of scope** (recorded in Non-Goals; backlog as a future companion
   extension or in-app proxied-browse enhancement).

2. **Do not duplicate existing design.** A triage of our own `docs/design/` found
   that several candidate features already have homes:
   [ADR-040](../ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.md)
   owns content filtering **and** a (differently-defined) deferred "Subscription";
   [ADR-039](../ADR-039-Pluggable-Integration-Platform/ADR-039-pluggable-integration-platform.md)
   is the plugin platform that translation/availability backends should register
   under; [ADR-024](../ADR-024-Torrent-Quality-Evidence/ADR-024-torrent-quality-evidence.md)
   owns per-magnet quality/subtitle scoring;
   [ADR-022](../_archive/ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md)
   built the user-authored `MovieRatings`/`ContentPreferences` D1 pattern. The
   existing web roadmap ([ADR-028](../ADR-028-Web-Platform-Completeness-Roadmap/ADR-028-web-platform-completeness-roadmap.md)
   — a closed 2026-05-29 snapshot audit whose child ADRs are archived) and ADR-034
   (fully shipped, a 1:1 read mirror of ADR-033) are **not** living containers to
   add phases to. A **new umbrella** is therefore the correct vessel, but it must
   **delegate and extend** rather than re-build.

## Decision

Establish **ADR-054 as the umbrella** for the user-intent & discovery initiative.
It **owns** the genuinely net-new pieces — a user-intent watchlist, a unified
subscription + new-works feed, and cross-source magnet aggregation — and
**delegates** the rest to their existing homes (content filtering to ADR-040;
AI translation and availability checks to ADR-039 plugin categories), **reusing**
ADR-024's quality scoring and the ADR-034 web-surface pattern. Each workstream is
designed and shipped via its own ADR/spec → IMP; this roadmap ships no code.

### Design Decisions

**D1. Scope: port data/logic into the SPA; no extension, no page injection.**
The value we capture is server-backed: D1 tables + dual-backend endpoints +
Vue views, not chrome.storage + content scripts. The extension's DOM-injection
features (see Non-Goals) are explicitly excluded this round. This keeps the
initiative inside our existing architecture (ADR-008 SPA, ADR-017 dual backend)
rather than opening a second delivery channel.

**D2. ADR-054 is a new umbrella, not an extension of ADR-028/034.** ADR-028 is a
closed snapshot audit that ships no code and governs a fixed 2026-05-29 backlog;
ADR-034 is fully implemented and a strict 1:1 mirror of ADR-033's read-only
tables. Neither has an open phase slot for a net-new content-intelligence domain.
ADR-054 follows the umbrella pattern of ADR-033/ADR-039 (an "Accepted — umbrella"
record that routes work into phases/child-ADRs).

**D3. WS1 — User-Intent Watchlist.** A net-new **authoritative** D1 table
(`WatchIntent`, in `HISTORY_DB` alongside ADR-022's `MovieRatings` /
`ContentPreferences`, reusing their D1-first user-authored pattern — not folded
into the rating/heart model, which ADR-022 deliberately excluded). It is the
**manual-intent complement** to ADR-033's media-derived `ConsumptionSignal` (cite
ADR-033 D11: "Scope is the signal, not the model"); WS1 never derives status from
`ConsumptionSignal` nor writes back into it — the two stay distinct,
reconciliation deferred. The web surface follows the ADR-034 Library pattern
(capability-gated, dual-backend, en/zh).

_Resolved 2026-06-13 (brainstorming + cross-repo deep-read):_ the status enum is
**`want | viewed`** — `browsed` is **dropped** (in the source extension it existed
only as a content-script page-visit default, an event our injection-free SPA
cannot emit; re-introducing it as an auto "surfaced/opened" tier is deferred).
`untracked` is the **absent-row** state (an explicit un-track is a `DELETE`); no
`user_id` (single-operator); no edit-lock (nothing but the operator writes this
table). PK is `video_code` (aligning with the closed-loop tables) with `href`
carried as a bridge column so the inline UI can write/join by `href`. The
capability flag is **`watch_intent`**. Two surfaces: an inline `StatusControl`
setter on the existing movie list (the SETTER) and a Library **Watchlist** tab
(the aggregate VIEW of `want`/`viewed` items). Shipped **read+write in one IMP**,
with a dedicated cross-backend upsert-parity test (the Query Golden does not cover
upserts). Full execution detail in
[IMP-ADR054-01](IMP-ADR054-01-watchlist.md).

**D4. WS2 — Subscriptions, unified (supersedes ADR-040 Phase-3).** One
**Subscription** concept: follow an entity (actor first; tag/series later) such
that its new works (a) surface in a **New-Works Feed** and (b) **bypass the
ADR-040 rating threshold at ingestion**. This **absorbs and supersedes ADR-040's
deferred Phase-3 "Subscription"** (which defined only the rating-threshold-bypass
whitelist), so the codebase has exactly one "subscription" meaning. New works are
discovered by **scheduled scraping** (Cron / GitHub Actions) reusing the existing
AdHoc scrape path, and each can be one-click marked **want** (feeding WS1). A
Subscription store (followed entities + last-seen cursor) is a net-new
authoritative table. ADR-040 will be cross-referenced/updated to point its Phase-3
row here when WS2 is designed.

**D5. WS3 — Multi-Source Magnet Aggregation.** Fetch and **dedup magnets across
multiple sources** (Sukebei, BTdig, BTSOW, JAVBUS) — net-new — registering each
source as a backend under a **new ADR-039 plugin category** (working name
`magnet-source` / `indexer`) rather than a bespoke aggregator. Per-magnet
**quality/subtitle scoring reuses ADR-024's evidence layer** (subtitle-file
evidence, resolution/category consistency, reason codes) instead of re-parsing.
Cloudflare-challenge handling is done **server-side** (the extension's hidden-tab
fetch trick is unnecessary when we control the fetch path). Surfaces in the
existing Browse / detail flow.

**D6. WS4 — Overlays, delegated (not owned by ADR-054).**
- **WS4a Content filtering** (keyword / regex / release-date) → **extend ADR-040**:
  add `regex` and `release-date` dimensions/modes to the existing
  `ContentFilterRule` engine plus its planned Phase-4 web rule CRUD; a display-side
  overlay in the SPA is a thin **read-side reuse** of the same rules. **Do not
  build a parallel filter system.** Owned by ADR-040.
- **WS4b AI title translation** → a **new ADR-039 plugin category** (working name
  `enrichment` / `translation`), following ADR-026's structured-LLM-call pattern
  (provider behind a contract, rate-limited). Net-new feature, existing seam.
- **WS4c Online availability check** → a **new ADR-039 plugin category** (working
  name `availability`) probing streaming sources with TTL caching. Net-new
  feature, existing seam.

**D7. Dual-backend, cross-repo, capability-honest by construction.** Every new
read/write surface obeys the ADR-017/018/030 parity rule: implemented in **both**
the TS Worker (`server/`, web repo) and the Python backend
(`apps/api/`, this monorepo), with `openapi.json` as the shared contract; gated by
a capability flag (ADR-034 D4 pattern, flags `watch_intent` (WS1) /
`subscriptions` (WS2)) so a deployment without the tables never shows a broken page;
with en/zh i18n parity for all new strings. The cross-repo split is explicit —
Vue + TS Worker land in `javdb-autospider-web`; D1 migrations, the Python router,
and the Cron/GH-Actions scrape (WS2) land in this monorepo — and each WS's IMP
enumerates which file lands where.

**D8. Phased and sequenced; this roadmap ships no code.** Recommended order:
**WS1 → WS2 → WS3 → WS4**. WS1 is the foundation (the "want" status is where WS2's
new works land); WS2 depends on WS1; WS3 is independent and may run in parallel;
WS4 is delegated and incremental. Each workstream is taken through its own
`brainstorming` (spec/ADR) → `writing-plans` (IMP) round, grounded in the real
table/endpoint shapes at the time — not pre-committed here. Like ADR-028, this
umbrella is a sequencing record, not an implementation.

**D9. Reuse-don't-duplicate ledger.** The triage mapping that governs the whole
initiative:

| Ported feature | Relationship | Home / vessel |
| --- | --- | --- |
| Watchlist (`want/viewed/browsed`) | **Net-new** status store; reuses ADR-022 D1 pattern; complements ADR-033 `ConsumptionSignal` | **ADR-054 WS1** |
| Actor subscriptions + new-works | **Net-new**; **unifies & supersedes** ADR-040 Phase-3 "Subscription" | **ADR-054 WS2** |
| Multi-source magnet aggregation | Cross-source fetch+dedup **net-new**; quality/subtitle **extends ADR-024**; sources are **ADR-039** plugins | **ADR-054 WS3** (+ ADR-039, ADR-024) |
| Content filtering (keyword/regex/date) | **Extend** existing `ContentFilterRule` (+ regex/date dimensions) | **ADR-040** (cross-ref) |
| AI title translation | **Net-new** feature; new **ADR-039** plugin category | **ADR-039** (cross-ref) |
| Online availability check | **Net-new** feature; new **ADR-039** plugin category | **ADR-039** (cross-ref) |

## Consequences

### Positive

- **Closes the loop on the demand side** — the operator can finally express
  want / seen and follow actors, complementing ADR-033/034's supply-side record.
- **No duplicated design** — each feature lands in exactly one home; the ledger
  (D9) prevents two filters, two "subscription" meanings, or a re-parsed quality
  scorer.
- **One "subscription" in the domain language** — D4 resolves the ADR-040 naming
  collision before it ships, not after.
- **Grows existing platforms** — WS3/WS4b/WS4c make ADR-039 a real ecosystem;
  WS4a deepens ADR-040; WS1 extends the ADR-022 user-data pattern.
- **Architecture-honest** — dual-backend + capability gating + i18n by
  construction (D7); no second delivery channel (D1).

### Negative

- **Cross-repo, dual-backend cost per workstream** — each WS pays the ADR-018/030
  "two implementations of the same SQL" tax and spans both repos (D7); the
  accepted status quo until a shared query seam exists.
- **Coordination debt** — WS2 must amend ADR-040 (supersede Phase-3) and WS3/WS4b/4c
  must add ADR-039 categories; these cross-ADR edits are tracked, not free.
- **Backlogs the most-loved extension features** — the DOM-injection UX (previews,
  on-page badges) is deferred (D1); some users will miss "it on the javdb page".
- **Scope breadth** — six workstreams across four ADRs; only delivered if each is
  taken one at a time, not all at once.

## Implementation Roadmap

| Phase | Owner | Child ADR / IMP | Ships | Deferred |
| --- | --- | --- | --- | --- |
| WS1 — Watchlist | ADR-054 | [IMP-ADR054-01](IMP-ADR054-01-watchlist.md) | `WatchIntent` D1 table (`want/viewed`, `video_code`+`href`); inline `StatusControl` setter + Library Watchlist tab; dual-backend `/api/watchlist` read+write + upsert-parity test; `watch_intent` capability flag; en/zh | `browsed` auto-signal; `ConsumptionSignal` reconciliation; bulk ops |
| WS2 — Subscriptions + New-Works | ADR-054 | [IMP-ADR054-02](IMP-ADR054-02-subscriptions.md) | `ActorSubscription` + `NewWorks` (HISTORY_DB, actor-only); `SubscriptionMonitor.yml` GH-cron scrapes followed actors via the AdHoc path (rating threshold bypassed **by construction** — no new code); New-Works feed reuses WS1 `StatusControl` (one-click → want); `subscriptions` flag; supersedes ADR-040 P3 by amendment | tag/series; notifications; Worker-cron |
| WS3 — Magnet Aggregation | ADR-054 (+ ADR-039, ADR-024) | [IMP-ADR054-03](IMP-ADR054-03-magnet-aggregation.md) | ADR-039 `indexer` category (JAVBUS + Sukebei); server-side fetch + infohash dedup; **live** ADR-024 scoring (file signals → `probe_unavailable`); `POST /api/explore/aggregate-magnets` (Worker 501); `magnet_aggregation` flag (config-presence); **ephemeral v1** | cache table; BTdig/BTSOW; negative-cache/backoff |
| WS4a — Content filtering | **ADR-040** | [IMP-ADR040-03](../ADR-040-Content-Filter-Rules/IMP-ADR040-03-content-filter-regex-date.md) (engine) + [IMP-ADR040-04](../ADR-040-Content-Filter-Rules/IMP-ADR040-04-content-filter-web-crud.md) (web CRUD) | `regex_exclude/include` + `release_date before/after` on the existing `ContentFilterRule` triple (**no migration**); dual-backend `/api/content-filter` CRUD + Settings page + read-side Browse overlay (**REPORTS_DB**); `content_filter` flag | — |
| WS4b — AI translation | **ADR-039** | backlog (deferred 2026-06-14) | `translation` plugin category; first real OpenAI-compatible LLM client; lazy per-title, memoized | the whole sub-item (not in this round) |
| WS4c — Availability check | **ADR-039** | backlog (deferred 2026-06-14) | `availability` plugin category; TTL cache table; per-source isolation | the whole sub-item (server-side streaming probe carries ban/legal risk) |

Each phase is detailed in a post-decision `brainstorming` + `writing-plans` round
against the real shapes at the time (cadence per ADR-034).

### Explicit non-goals (YAGNI)

- **No browser extension and no javdb.com DOM injection** — the extension's
  on-page features (hover trailers/previews, cover swap, detail-page button
  injection, keyboard shortcuts, screenshot-blur privacy mode, comment/paywall
  unlock, anchor/super-ranking nav, generic password autofill) are out of scope.
  Backlog: a future companion extension or in-app proxied-browse enhancement.
- **No WebDAV / cloud-sync port** — our D1 backend **is** the sync layer (ADR-017);
  the extension's WebDAV diff/merge is replaced, not ported (its
  `manuallyEditedFields` edit-lock idea is noted for WS1's write path, not adopted
  wholesale).
- **No 115 cloud-drive integration** this round — qB/PikPak already cover
  downloading; 115 is backlog (would slot as an ADR-039 downloader/destination
  plugin if ever wanted).
- **No second "subscription" concept** — D4 unifies it.
- **No new chart/UI library** — reuse Naive UI + vue-chartjs already in the SPA.
- **This umbrella ships no code** — it sequences; each WS ships via its own IMP.

## Domain Language (additions for CONTEXT.md)

- **Watch Intent** — the operator's manually-set status on a video
  (`want | viewed`; the absence of a row means untracked). Distinct from
  **Consumption Signal** (ADR-033, media-server-derived): intent is what the
  operator declares; signal is what a media server observed. (`browsed` from the
  source extension is deferred — it had no injection-free trigger; see D3.)
- **Watchlist** — the set of videos whose Watch Intent is `want`.
- **Subscription** — a followed entity (actor; later tag/series) whose new works
  (a) surface in the **New-Works Feed** and (b) bypass the ADR-040 ingestion rating
  threshold. **Unifies and supersedes** the deferred ADR-040 Phase-3 "Subscription"
  (which meant only the rating-threshold-bypass whitelist).
- **New-Works Feed** — the surfaced stream of newly-discovered releases from
  Subscriptions, produced by scheduled scraping.
- **Magnet source (plugin)** — an ADR-039 plugin-category backend that fetches
  magnets from one external indexer (e.g. Sukebei, BTdig, BTSOW, JAVBUS); the
  aggregator dedups across active sources.

## Alternatives Considered

- **Build a companion browser extension in-repo** — rejected this round (D1):
  opens a second delivery channel and pulls back the DOM-injection scope we chose
  to defer; recorded as backlog.
- **Enhance the in-app proxied javdb browse to inject overlays** — rejected this
  round (D1): constrained by what the proxy can rewrite; backlog.
- **Add these features as phases of ADR-028 or ADR-034** — rejected (D2): ADR-028
  is a closed snapshot audit, ADR-034 is a shipped 1:1 mirror; neither is a living
  container.
- **Keep two "subscription" meanings** (ADR-040 whitelist + a new "follow")
  — rejected (D4): a domain-language defect; unified instead.
- **Re-implement filtering and quality scoring from scratch** — rejected (D6):
  extend ADR-040 and reuse ADR-024; building parallel systems is the duplication
  this umbrella exists to prevent.
- **A single mega-spec for all six features** — rejected (D8): too large; phased
  into one-at-a-time ADR/spec → IMP rounds.

## References

- [ADR-033 — Media Closed-Loop](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md)
- [ADR-034 — Media Closed-Loop Web Surface](../ADR-034-Media-Closed-Loop-Web-Surface/ADR-034-media-closed-loop-web-surface.md)
- [ADR-040 — Content Filter Rules](../ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.md)
- [ADR-039 — Pluggable Integration Platform](../ADR-039-Pluggable-Integration-Platform/ADR-039-pluggable-integration-platform.md)
- [ADR-024 — Torrent Quality Evidence Foundation](../ADR-024-Torrent-Quality-Evidence/ADR-024-torrent-quality-evidence.md)
- [ADR-022 — User Preference Foundation](../_archive/ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md)
- [ADR-025 — User Preference Model](../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md)
- [ADR-026 — AI Operations Diagnosis](../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md)
- [ADR-028 — Web Platform Completeness Roadmap](../ADR-028-Web-Platform-Completeness-Roadmap/ADR-028-web-platform-completeness-roadmap.md)
- [ADR-017 — Cloudflare-First Deployment](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md)
- [Adsryen/JavdBviewed](https://github.com/Adsryen/JavdBviewed) — the browser extension triaged for this initiative

## Status Log

- 2026-06-13: Proposed (umbrella; six workstreams across four ADRs scoped; WS1→WS4
  sequencing recorded; no IMPs yet). Open coordination items: WS2 must amend
  ADR-040 to supersede its Phase-3 "Subscription"; WS3/WS4b/WS4c must add ADR-039
  plugin categories.
- 2026-06-13: WS1 design resolved (brainstorming + cross-repo deep-read). Enum
  fixed to `want | viewed` (`browsed` dropped/deferred — no injection-free
  trigger); `WatchIntent` lands in `HISTORY_DB` keyed by `video_code` (+`href`
  bridge), `untracked` = absent row, no `user_id`, no edit-lock; capability flag
  `watch_intent`; inline `StatusControl` setter + Library Watchlist tab;
  read+write in one IMP with a cross-backend upsert-parity test. Detailed in
  [IMP-ADR054-01](IMP-ADR054-01-watchlist.md).
- 2026-06-14: WS2/WS3/WS4a designs resolved (brainstorming + cross-repo
  deep-read) and IMPs written — **no implementation this round**. WS2 →
  [IMP-ADR054-02](IMP-ADR054-02-subscriptions.md) (actor-only; GH-cron
  `SubscriptionMonitor.yml` reusing the AdHoc path; `ActorSubscription`+`NewWorks`
  in `HISTORY_DB`; `subscriptions` flag). WS3 →
  [IMP-ADR054-03](IMP-ADR054-03-magnet-aggregation.md) (JAVBUS+Sukebei `indexer`
  plugins; ephemeral; `POST /api/explore/aggregate-magnets` with Worker 501;
  `magnet_aggregation` config-presence flag). WS4a →
  [IMP-ADR040-03](../ADR-040-Content-Filter-Rules/IMP-ADR040-03-content-filter-regex-date.md)
  +
  [IMP-ADR040-04](../ADR-040-Content-Filter-Rules/IMP-ADR040-04-content-filter-web-crud.md)
  (extend `ContentFilterRule` in `REPORTS_DB`, no migration). **WS4b (AI
  translation) + WS4c (availability) deferred to backlog** this round. Key
  finding: WS2's rating-threshold bypass is already free on the AdHoc path (no
  new code; ADR-040 P3 superseded by amendment at WS2 implementation).
- 2026-06-15: WS2 backend/server slice implemented via
  [IMP-ADR054-02](IMP-ADR054-02-subscriptions.md): `ActorSubscription` +
  `NewWorks` D1 tables in `HISTORY_DB`; Python `/api/subscriptions` and
  `/api/new-works`; TS Worker server mirror; `subscriptions` capability flag in
  both backends; `SubscriptionMonitor.yml` GitHub cron + CLI/pipeline; OpenAPI
  contract updated; cross-backend UPSERT parity tests added. ADR-040 Phase-3
  "Subscriptions" is superseded by amendment. Vue/UI work remains scheduled for
  the frontend Sprint 3 slice.
