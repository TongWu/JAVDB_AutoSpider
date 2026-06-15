# Execution Tracker — User-Intent & Discovery campaign

**Owner ADR:** [ADR-054](ADR-054-user-intent-discovery-layer.md) (umbrella)
**Scope:** ADR-054 WS1–WS4a + the delegated [ADR-040](../ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.md) WS4a + the parallel [ADR-026 Phase 4](../ADR-026-AI-Operations-Diagnosis/IMP-ADR026-04-proactive-incident-alerting.md).
**Created:** 2026-06-14
**Status:** Active — Sprint 1 (WS1) verified done on `origin/main` of both repos (MAIN `7a0adf47`, WEB `28c98f5`); Sprint 2 ready to dispatch.

> This is a coordination/tracking artifact (English-only, like an IMP). It does
> not replace the ADRs/IMPs it points to — it sequences them across two agents.
> Each line item's authoritative detail lives in its linked IMP.

## 1. Two-agent model

| Agent | Strength | Owns | Repo / paths |
| --- | --- | --- | --- |
| **B** | backend | D1 schema/migrations, Python FastAPI router (`apps/api/`), **TS Hono Worker mirror** (`server/`), `openapi.json` contract, cross-backend parity tests, CLI (`apps/cli/`), GitHub-Actions cron, MCP | MAIN monorepo + web-repo `server/` |
| **F** | frontend | Vue SPA components/views/stores/routing, capability-gated rendering, i18n (en/zh/ja) | web-repo `src/` |

**Repos:**
- MAIN monorepo — `/Users/tedwu/JAVDB_AutoSpider_CICD` (Python, D1 migrations, CLI, `.github/workflows/`). Worktrees have **no `config.py`** (lives only in main repo); run D1 code with `PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD ...`.
- WEB repo — `/Users/tedwu/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web` (**separate git repo**). Vue SPA in `src/`, TS Hono Worker in `server/`.

**Execution method (MANDATORY):** every IMP in this campaign must be implemented
with the **`superpowers:subagent-driven-development`** skill (recommended) or
**`superpowers:executing-plans`** — task-by-task, ticking the IMP's own `- [ ]`
checkboxes as each task lands. This is not optional: each IMP header carries the
same `REQUIRED SUB-SKILL` directive. Do not free-hand the implementation; drive it
through the skill so every plan step gets its review checkpoint.

**Coordination protocol (contract-first):**
1. For each dual-backend workstream, **B lands the contract first** — `openapi.json` shape + endpoint signatures + the capability-flag name + the D1 table DDL — then F builds the Vue surface against it.
2. In the web repo, **B touches only `server/`, F touches only `src/`**. Coordinate commits/PRs to avoid conflicts (separate PRs preferred).
3. Every new read/write surface is dual-backend, capability-gated, i18n-parity (ADR-017/018/030/034 rules). The cross-backend **upsert/parity golden test** is B's deliverable.

**Worktree test runbook (MAIN, .venv broken):**
`PYTHONPATH=/Users/tedwu/JAVDB_AutoSpider_CICD:javdb/rust_core/python /opt/anaconda3/bin/python3 -m pytest --continue-on-collection-errors`
Baseline = 57 pre-existing env failures (401 auth-gated + un-built Rust wheel). Any non-401/non-Rust failure is a real regression. Never `git add -A` (reports/*.db get dirtied) — `git checkout -- reports/` to clean.

## 2. Dependency graph

```
WS1 (foundation) ──► WS2 (subscriptions)
   │                    │
   │ StatusControl +    │ reuses WS1 StatusControl
   │ want status        ▼
   │                 WS2 UI
   ├──► WS3 (magnet aggregation)   [independent — parallel]
   ├──► WS4a (ADR-040 content filter) [independent — parallel]
   └  ADR-026-P4 (incident alerting)  [independent — parallel]
```

- **WS1 must be first** (WS2 reuses its `StatusControl.vue` + `want` status).
- **WS2 depends on WS1.**
- **WS3 / WS4a / ADR-026-P4 are independent** — schedulable any time after WS1's dual-backend pattern is set.

## 3. Sprint plan

| Sprint | Agent B (backend) | Agent F (frontend) | Gate |
| --- | --- | --- | --- |
| **S1** | WS1 backend: `WatchIntent` table, `/api/watchlist` Python, TS mirror, `watch_intent` flag, upsert-parity test | WS1 frontend: `StatusControl.vue` + `WatchlistView.vue` + en/zh | WS1 merged → unblocks WS2 |
| **S2** | WS2 backend: tables + `SubscriptionMonitor.yml` cron + pipeline module + CLI + routers + TS mirror + ADR-040-P3 supersede | WS4a full slice: [IMP-ADR040-03](../ADR-040-Content-Filter-Rules/IMP-ADR040-03-content-filter-regex-date.md) engine + [IMP-ADR040-04](../ADR-040-Content-Filter-Rules/IMP-ADR040-04-content-filter-web-crud.md) CRUD+overlay | contract handoff: B → F for WS2 |
| **S3** | WS3 backend: ADR-039 `indexer` plugins + fetch + dedup + live ADR-024 scoring + `POST /api/explore/aggregate-magnets` (Worker 501) | WS2 UI (New-Works feed + subscriptions) + WS3 UI (Source column) | — |
| **S4** | ADR-026-P4 backend: alert-trigger policy + audit row + config API + TS mirror | ADR-026-P4 frontend: operator alert-config page (Naive UI) + i18n/parity tail | campaign complete |

## 4. Workstream checklists

### WS1 — Watchlist · [IMP-ADR054-01](IMP-ADR054-01-watchlist.md)
Agent B:
- [x] `WatchIntent` D1 migration in `HISTORY_DB` (PK `video_code`, `status IN ('want','viewed')`, `href` bridge col)
- [x] Python `/api/watchlist` read + write (upsert / delete-on-untrack) in `apps/api/`
- [x] `watch_intent` capability flag (both backends)
- [x] `openapi.json` update + publish contract to F
- [x] TS Hono Worker `/api/watchlist` mirror (`server/`)
- [x] cross-backend upsert-parity golden test

Agent F:
- [x] `StatusControl.vue` inline setter on the movie list (model on `HeartButton.vue`)
- [x] `WatchlistView.vue` Library tab (model on `ConsumptionView.vue`)
- [x] capability-gated rendering + en/zh i18n

**WS1 verification (2026-06-15):** 12-agent adversarial sweep over `origin/main` of both repos — all 9 deliverables independently confirmed; 36 tests green (14 Python + 22 web). Remote D1 `history.WatchIntent` confirmed applied (cols match, 0 rows) → capability is live, not just merged. Follow-ups closed: B3 probe-honesty now pinned by `tests/unit/test_watch_intent_capability_probe.py` (toggles the table, asserts the flag flips). Remaining (non-blocking): B6 cross-repo CANONICAL upsert-SQL has no single source of truth (paired tests + comments only).

### WS2 — Subscriptions + New-Works · [IMP-ADR054-02](IMP-ADR054-02-subscriptions.md)
Agent B:
- [ ] `ActorSubscription` + `NewWorks` D1 tables (`HISTORY_DB`)
- [ ] `SubscriptionMonitor.yml` GH-cron reusing the AdHoc scrape path
- [ ] `subscription_monitor` pipeline module + CLI
- [ ] Python `/api/subscriptions` + `/api/new-works` + `subscriptions` flag + TS mirror
- [ ] supersede ADR-040 Phase-3 "Subscription" (bilingual amendment) + bypass regression test

Agent F:
- [ ] New-Works feed view (reuse WS1 `StatusControl.vue` for one-click want)
- [ ] subscriptions management UI + en/zh

### WS3 — Magnet aggregation · [IMP-ADR054-03](IMP-ADR054-03-magnet-aggregation.md)
Agent B:
- [ ] ADR-039 `indexer` plugin category (`IndexerPlugin` Protocol + `IndexerResult`), JAVBUS + Sukebei plugins
- [ ] source-agnostic fetch helper (reuse `RequestHandler` proxy pool, `use_cf_bypass=False`)
- [ ] aggregator: info-hash dedup + `video_code` grouping + live ADR-024 `score_torrent`
- [ ] `POST /api/explore/aggregate-magnets` (Python real; Worker 501 in cloudflare mode); `magnet_aggregation` flag = `bool(MAGNET_SOURCES)`

Agent F:
- [ ] gated **Source** column in `ResolveMagnetTable.vue` (only when flag on) + i18n

### WS4a — Content filtering · ADR-040 (delegated)
Agent F (full vertical — self-contained, low backend risk):
- [ ] engine: `regex_exclude/include` + `release_date before/after` on existing `ContentFilterRule` (no migration) — [IMP-ADR040-03](../ADR-040-Content-Filter-Rules/IMP-ADR040-03-content-filter-regex-date.md)
- [ ] dual-backend `/api/content-filter` CRUD (delegates to existing `ContentFilterRepo`) — [IMP-ADR040-04](../ADR-040-Content-Filter-Rules/IMP-ADR040-04-content-filter-web-crud.md)
- [ ] `SettingsFilterRulesPage.vue` + read-side Browse overlay + `content_filter` flag + en/zh
> If F's Python comfort is low, split: B takes IMP-040-03 engine + IMP-040-04 router/TS; F takes the Vue page + overlay.

### ADR-026-P4 — Proactive incident alerting · [IMP-ADR026-04](../ADR-026-AI-Operations-Diagnosis/IMP-ADR026-04-proactive-incident-alerting.md)
Agent B:
- [ ] deterministic alert-trigger policy after incident `d1_written`; build `NotifyMessage` → hand to existing ADR-039 notify dispatch
- [ ] alert-event audit row (dedup keyed on incident) + operator-config API + TS mirror

Agent F:
- [ ] operator alert-config page (per-type enable + confidence threshold + channels, Naive UI) + en/zh

## 5. Deferred register (NOT on the critical path)

| Item | Reason | Unblock condition |
| --- | --- | --- |
| [ADR-024](../ADR-024-Torrent-Quality-Evidence/ADR-024-torrent-quality-evidence.md) P2/P3 (IMP-08/09) | data-gated; outlines need refinement | pipeline collects Top-K runner-up quality evidence |
| [ADR-025](../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md) preference model | data-starved | `MovieRatings` ≥ 200 explicit ratings (now 0) |
| [ADR-038](../ADR-038-Agentic-Operator-MCP/ADR-038-agentic-operator-mcp-surface.md) `trigger_run` + Phase 3 | deferred by design — needs new `workflow_dispatch` service, highest side-effect, lowest value | a thin-adaptable dispatch service exists |
| ADR-036 P3 (strangler) / ADR-039 P3 (ecosystem) | optional/deferred in their ADRs | demand justifies it |
| Ops closeout: ADR-033 D1 apply / Emby-Plex verify · ADR-035 TS-mirror · ADR-047 cross-repo PRs · stale doc headers | deployment/housekeeping | do in agent idle gaps |

## 6. Change log

- 2026-06-14: Created. Sprint plan + two-agent split recorded; `trigger_run` confirmed deferred (moved out of active scope); ADR-026-P4 confirmed dual-backend & execution-ready.
- 2026-06-14: Recorded MANDATORY execution method — every IMP is driven via `superpowers:subagent-driven-development` (or `superpowers:executing-plans`), task-by-task with checkbox tracking.
- 2026-06-14: Sprint 1 (WS1, B+F) and Sprint 2 (B → WS2; F → WS4a) agent prompts drafted and ready to dispatch. Dispatch Sprint 2 after WS1 merges.
- 2026-06-15: WS1 verified done on `origin/main` (both repos) via a 12-agent adversarial workflow; 36 tests green; remote D1 migration confirmed applied. Added `tests/unit/test_watch_intent_capability_probe.py` to close the B3 probe-honesty gap. WS1 → Sprint 2 unblocked.
