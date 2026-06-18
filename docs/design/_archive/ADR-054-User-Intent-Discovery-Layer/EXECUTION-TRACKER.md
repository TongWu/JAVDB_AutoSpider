# Execution Tracker — User-Intent & Discovery campaign

**Owner ADR:** [ADR-054](ADR-054-user-intent-discovery-layer.md) (umbrella)
**Scope:** ADR-054 WS1–WS4a + the delegated [ADR-040](../../ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.md) WS4a + the parallel [ADR-026 Phase 4](../ADR-026-AI-Operations-Diagnosis/IMP-ADR026-04-proactive-incident-alerting.md).
**Created:** 2026-06-14
**Status:** ✅ Campaign complete — Sprints 1–4 all verified done on `origin/main` of both repos; ADR-055 P1+P2 done and the post-Sprint-4 `ops_alert` registry regression closed (#234, verified). Only ADR-055 Phase-3 (the standing convention) remains, and the data-gated/deferred register below.

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
| **S2** | WS2 backend: tables + `SubscriptionMonitor.yml` cron + pipeline module + CLI + routers + TS mirror + ADR-040-P3 supersede | WS4a full slice: [IMP-ADR040-03](../../ADR-040-Content-Filter-Rules/IMP-ADR040-03-content-filter-regex-date.md) engine + [IMP-ADR040-04](../../ADR-040-Content-Filter-Rules/IMP-ADR040-04-content-filter-web-crud.md) CRUD+overlay | contract handoff: B → F for WS2 |
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
- [x] `ActorSubscription` + `NewWorks` D1 tables (`HISTORY_DB`)
- [x] `SubscriptionMonitor.yml` GH-cron reusing the AdHoc scrape path
- [x] `subscription_monitor` pipeline module + CLI
- [x] Python `/api/subscriptions` + `/api/new-works` + `subscriptions` flag + TS mirror
- [x] supersede ADR-040 Phase-3 "Subscription" (bilingual amendment) + bypass regression test

Agent F:
- [x] New-Works feed view (reuse WS1 `StatusControl.vue` for one-click want)
- [x] subscriptions management UI + en/zh

**WS2 verification (2026-06-15):** Sprint-2 backend + Sprint-3 UI adversarially verified on `origin/main` of both repos — all deliverables complete; tests green. Review fix: the Worker `listSubscriptions`/`listNewWorks` lacked Python's secondary sort key — added `actor_href` / `video_code ASC` + a tie-break regression test (WEB `6ffec5a`). Added the missing WS2 frontend unit specs (`subscriptions-view`/`new-works-view`). The hand-mirrored `ACTOR_SUBSCRIPTION_UPSERT_SQL` is an ADR-055 Phase-2 item (chipped).

### WS3 — Magnet aggregation · [IMP-ADR054-03](IMP-ADR054-03-magnet-aggregation.md)
Agent B:
- [x] ADR-039 `indexer` plugin category (`IndexerPlugin` Protocol + `IndexerResult`), JAVBUS + Sukebei plugins
- [x] source-agnostic fetch helper (reuse `RequestHandler` proxy pool, `use_cf_bypass=False`)
- [x] aggregator: info-hash dedup + `video_code` grouping + live ADR-024 `score_torrent`
- [x] `POST /api/explore/aggregate-magnets` (Python real; Worker 501 in cloudflare mode); `magnet_aggregation` flag = `bool(MAGNET_SOURCES)`

Agent F:
- [x] gated **Source** column in `ResolveMagnetTable.vue` (only when flag on) + i18n

**WS3 verification (2026-06-15):** verified on `origin/main` — indexer fan-out (JAVBUS/Sukebei, self-registering), fetch reuses the proxy pool but bypasses javdb guards, info-hash dedup + live ADR-024 scoring, Python-real / Worker-501 capability honesty, gated Source column. Ephemeral v1 as designed; all 8 deliverables complete (135 Python + 43 web tests green).

### WS4a — Content filtering · ADR-040 (delegated)
Agent F (full vertical — self-contained, low backend risk):
- [x] engine: `regex_exclude/include` + `release_date before/after` on existing `ContentFilterRule` (no migration) — [IMP-ADR040-03](../../ADR-040-Content-Filter-Rules/IMP-ADR040-03-content-filter-regex-date.md)
- [x] dual-backend `/api/content-filter` CRUD (delegates to existing `ContentFilterRepo`) — [IMP-ADR040-04](../../ADR-040-Content-Filter-Rules/IMP-ADR040-04-content-filter-web-crud.md)
- [x] `SettingsFilterRulesPage.vue` + read-side Browse overlay + `content_filter` flag + en/zh/ja
> If F's Python comfort is low, split: B takes IMP-040-03 engine + IMP-040-04 router/TS; F takes the Vue page + overlay.
> **Done 2026-06-15 (Agent F, full vertical).** MAIN branch `claude/ws4a-content-filter`
> (engine + CLI + dual-backend Python router + capability + openapi + parity, 14 commits);
> WEB branch `claude/nostalgic-hawking-fe6c0b` (TS Worker route/service/capability/parity +
> api client + overlay matcher + Settings CRUD page + Movies overlay + en/zh/ja i18n, 8 commits).
> Allow-list reconciled to 13/12 pairs (regex/release_date included); web-boundary validation
> covers release_date (strict ISO) but not regex (JS/Python dialect — engine fail-opens);
> i18n is three-way (en/zh/ja). Two correctness fixes landed (engine actor `regex_include`;
> `StrictBool`/non-string-value cross-backend parity). All tests green; no schema migration.

### ADR-026-P4 — Proactive incident alerting · [IMP-ADR026-04](../ADR-026-AI-Operations-Diagnosis/IMP-ADR026-04-proactive-incident-alerting.md)
Agent B:
- [x] deterministic alert-trigger policy after incident `d1_written`; build `NotifyMessage` → hand to existing ADR-039 notify dispatch
- [x] alert-event audit row (dedup keyed on incident) + operator-config API + TS mirror

Agent F:
- [ ] operator alert-config page (per-type enable + confidence threshold + channels, Naive UI) + en/zh

## 5. Deferred register (NOT on the critical path)

| Item | Reason | Unblock condition |
| --- | --- | --- |
| [ADR-024](../../ADR-024-Torrent-Quality-Evidence/ADR-024-torrent-quality-evidence.md) P2/P3 (IMP-08/09) | data-gated; outlines need refinement | pipeline collects Top-K runner-up quality evidence |
| [ADR-025](../../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md) preference model | data-starved | `MovieRatings` ≥ 200 explicit ratings (now 0) |
| [ADR-038](../../ADR-038-Agentic-Operator-MCP/ADR-038-agentic-operator-mcp-surface.md) `trigger_run` + Phase 3 | deferred by design — needs new `workflow_dispatch` service, highest side-effect, lowest value | a thin-adaptable dispatch service exists |
| ADR-036 P3 (strangler) / ADR-039 P3 (ecosystem) | optional/deferred in their ADRs | demand justifies it |
| Ops closeout: ADR-033 D1 apply / Emby-Plex verify · ADR-035 TS-mirror · ADR-047 cross-repo PRs · stale doc headers | deployment/housekeeping | do in agent idle gaps |

## 6. Change log

- 2026-06-14: Created. Sprint plan + two-agent split recorded; `trigger_run` confirmed deferred (moved out of active scope); ADR-026-P4 confirmed dual-backend & execution-ready.
- 2026-06-14: Recorded MANDATORY execution method — every IMP is driven via `superpowers:subagent-driven-development` (or `superpowers:executing-plans`), task-by-task with checkbox tracking.
- 2026-06-14: Sprint 1 (WS1, B+F) and Sprint 2 (B → WS2; F → WS4a) agent prompts drafted and ready to dispatch. Dispatch Sprint 2 after WS1 merges.
- 2026-06-15: WS1 verified done on `origin/main` (both repos) via a 12-agent adversarial workflow; 36 tests green; remote D1 migration confirmed applied. Added `tests/unit/test_watch_intent_capability_probe.py` to close the B3 probe-honesty gap. WS1 → Sprint 2 unblocked.
- 2026-06-15: Sprint 2 WS2 backend/server closeout completed for
  [IMP-ADR054-02](IMP-ADR054-02-subscriptions.md): MAIN backend + CLI +
  workflow + docs and WEB `server/` mirror are ready for PR. WS2 Vue frontend
  remains a Sprint 3 / Agent F item, and remote D1 apply + cron enablement
  remain deployment gates.
- 2026-06-15: WS3 Agent B backend slice completed: MAIN indexer plugins/fetch/aggregation/Python API/OpenAPI/capability, plus WEB `server/` Worker 501 mirror and hardcoded-false capability. Frontend `src/` work remains with Agent F.
- 2026-06-15: **Sprint 3 / Agent F — WS2 UI complete.** Library `Subscriptions` + `New-Works` tabs (reuse WS1 `StatusControl.vue` unchanged), hand-typed `src/api/{subscriptions,new-works}.ts`, gated on `subscriptions`, en/zh-CN/**ja**. `api.gen.ts` re-vendored post-#217 (typed gate), drift-clean, 155 web unit tests + build green. 2 commits on `claude/priceless-curie-b0a53f`.
- 2026-06-15: **Sprint 3 / Agent F — WS3 UI complete (unblocked same day).** WS3 backend landed mid-session (MAIN #218 + WEB `server/` mirror #36), so the UI was built on a fresh branch `claude/adr054-ws3-magnet-source` off WEB `main`: re-vendored `api.gen.ts` for `magnet_aggregation`; `apiAggregateMagnets` client (`skipErrorToast`); capability-gated `browse.aggregateMagnets` store action (+3 unit tests); `ResolveCard` orchestration (call/merge/error); gated **Source** column in `ResolveMagnetTable` rendering per-source provenance + ADR-024 `quality_score`; en/zh-CN/**ja**. Verified: drift-clean, typecheck, 167 web unit tests, server 501-mirror suite 9/9, build. 3 commits (chore→feat→test).
- 2026-06-15: **Sprints 2 & 3 adversarially verified** on `origin/main` of both repos (two 11–14-agent workflows). Both `done` — all deliverables independently confirmed, Sprint-2 143 tests green, Sprint-3 135 Python + 43 web green. One real WS2 dual-backend parity defect found & fixed: the Worker `listSubscriptions`/`listNewWorks` lacked Python's secondary sort key → added `actor_href`/`video_code ASC` + a tie-break regression test, and added the previously-missing WS2 frontend unit specs (WEB `846b322..6ffec5a`). New hand-mirror follow-ups (`ACTOR_SUBSCRIPTION_UPSERT_SQL`, content-filter allow-list **and** the `INSERT INTO ContentFilterRule` statement) spun off to the **ADR-055 Phase-2** task (`task_7ae01603`).
- 2026-06-15: **Sprint 4 / Agent B backend slice complete pending final suite closeout.** ADR-026-P4 now has D1 alert policy/event tables, deterministic post-`d1_written` alert evaluation, incident-keyed alert audit dedupe, ADR-039 notify-dispatch handoff (no delivery reimplementation), Python config/event API + `ops_alerting` capability/OpenAPI, and Web `server/` Hono mirror. ADR-055 contract registry now owns the new alert static SQL/probes and preserves existing generated Worker exports. Agent F still owns the Web `src/` alert config/status UI.
- 2026-06-18: **Sprint 4 adversarially verified** on `origin/main` (12-agent workflow). `done_with_gaps`: feature complete (120 tests green), but found a real ADR-055 regression — the Python `ops_alert_repo.py` hand-mirrored its cross-backend SQL (4 fragments had zero Python consumers; policy upsert drifted to 8 params + app-clock vs the registry's 6 params + server-clock). Two minor items fixed inline (IMP-026-04 stale checkboxes + `AlertPolicyPanel` telegram channel preset). Regression spun off to a chip.
- 2026-06-18: **Campaign closeout — ADR-055 `ops_alert` regression CLOSED** (MAIN #234, verified by a 6-agent workflow). `ops_alert_repo.py` now executes the 4 cross-backend statements via `order_params(fragments.OPS_ALERT_*)`; the 3 remaining inline `OpsAlertEvent` writes are confirmed Python-only (the alert trigger runs server-side, the Worker only reads). `regression_closed=true`, `byte_parity_restored=true` (Python fragment byte-identical to the Worker SQL: 6 params + server-clock), pinned by a `_CapturingConn` test asserting the repo runs the registry SQL. 74 Python + 35 web green; `VENDORED == MAIN`; WEB CI freshness gate (`ci.yml` + `revendor-sql-contract.yml` + MAIN `publish-sql-contract.yml`) confirmed present. **ADR-054 WS1–WS4a + ADR-055 P1/P2 + ADR-026-P4 all verified done.**
