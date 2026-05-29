# ADR-028: Web Platform & Capability Completeness Roadmap

| Field       | Value                                                                 |
| ----------- | --------------------------------------------------------------------- |
| **Status**  | Accepted — umbrella roadmap; execution delegated to child ADRs        |
| **Date**    | 2026-05-29                                                            |
| **Authors** | Ted                                                                   |
| **Related** | [ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md), [ADR-029](../_archive/ADR-029-Web-Security-Hardening/ADR-029-web-security-hardening.md), [ADR-030](../_archive/ADR-030-Web-Feature-Parity/ADR-030-web-feature-parity.md), [ADR-031](../_archive/ADR-031-Web-Operational-Polish/ADR-031-web-operational-polish.md), [ADR-026](../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md), [ADR-022](../ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md), [ADR-023](../ADR-023-Proxy-Recommendation-Policy/ADR-023-proxy-recommendation-policy.md), [ADR-024](../ADR-024-Torrent-Quality-Evidence/ADR-024-torrent-quality-evidence.md), [ADR-025](../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md) |

> **Renumber complete (2026-05-29).** The web cluster was renumbered by
> `IMP-ADR028-01`: ADR-029 (`ADR-029-Web-Security-Hardening/`),
> ADR-030 (`ADR-030-Web-Feature-Parity/`), ADR-031 (`ADR-031-Web-Operational-Polish/`).
> The [Renumbering Plan](#renumbering-plan) below retains the old→new mapping as the historical record.

## Context

The system runs **two API backends** behind one Vue frontend
([`JAVDB_AutoSpider_Web`](https://github.com/TongWu/JAVDB_AutoSpider_Web)), as
decided in [ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md):

| | **Cloudflare (TypeScript Worker, Hono)** | **Local / Docker (Python FastAPI)** |
| --- | --- | --- |
| Role | Thin layer: **D1 queries + GitHub Actions dispatch** | Full execution: **subprocess spider + live qB / PikPak / rclone / SMTP** |
| Capability boundary | No filesystem, no subprocess, no LAN egress to live services | Executes everything directly on the host |

A capability audit (2026-05-29) compared the two backends against the nine
GitHub Actions workflows and the design backlog. It found that completeness gaps
are **scattered across deployment modes and across several ADRs** with no single
document sequencing the work. Three Accepted web ADRs (018/019/020) sit largely
unimplemented; an AI-ops ADR (026) is mid-flight; and four content-intelligence
ADRs (022–025) are Proposed with interdependencies but no agreed ordering.

This ADR is an **umbrella roadmap**. It records the audit, defines a
prioritization rubric, groups the work into workstreams, and assigns each
workstream to a child ADR. It deliberately produces **no code** — execution lives
in the child ADRs and their IMPs.

## Audit Findings

### Finding 1 — Cloudflare GitHub Actions coverage

Cloudflare cannot execute pipeline work itself; it must dispatch to GitHub
Actions. Six workflows have first-class typed endpoints with `job_runs` tracking;
three are reachable **only** via the generic `POST /api/gh-actions/runs`
(requires `GH_ACTIONS_TIER=admin` and a known workflow filename) and are **not
written to `job_runs`**, so they never appear in the Tasks list or stats.

| Workflow | Typed endpoint | `job_runs` tracking | Status |
| --- | --- | --- | --- |
| `DailyIngestion.yml` | `POST /api/tasks/daily` | yes | Complete |
| `AdHocIngestion.yml` | `POST /api/tasks/adhoc` | yes | Complete |
| `QBFileFilter.yml` | `POST /api/ops/qb/filter-small` | yes | Complete |
| `RcloneManager.yml` | `POST /api/ops/rclone/run` | yes | Complete |
| `StaleSessionCleanup.yml` | `POST /api/ops/cleanup/stale-sessions` | yes | Complete |
| `RollbackD1.yml` | `POST /api/sessions/:id/rollback` | yes | Complete |
| `Migration.yml` | none (`/api/migrations/*` is a 501 stub) | no | **Gap** — generic dispatch only, untracked |
| `WeeklyDedup.yml` | none | no | **Gap** — generic dispatch only, untracked |
| `TestIngestion.yml` | none | no | **Gap** — generic dispatch only, untracked |

**Answer to "can Cloudflare carry all GitHub Actions activity?":** Mostly yes for
the six operator workflows. Migration / WeeklyDedup / TestIngestion are
dispatchable but not first-class — no typed endpoint, no UI affordance, no task
tracking.

### Finding 2 — Local / Docker capability and the `INGESTION_MODE` honesty gap

The Python backend has live handlers for everything Cloudflare stubs out: live qB
torrent list, PikPak queue/transfer, email test/history/resend, rclone, cleanup
(including claim-stages), local spider subprocess, headless login, parse tester,
deep health-check, and migrations. **Answer to "can local/Docker execute all
activity?": yes**, via subprocess + live connections.

**One correctness gap:** `INGESTION_MODE=github` / `dual` is advertised by
`GET /api/capabilities` (and the web README markets it as a topology), but
`apps/api/services/task_service.py` has **no GitHub-dispatch branch** —
`trigger_daily_task` / `trigger_adhoc_task` always run a local subprocess
regardless of `INGESTION_MODE`. The capabilities endpoint therefore advertises a
mode the execution layer does not honor.

### Finding 3 — Web ADR backlog status

All claims below were verified against the current code, not just the ADR status
field.

| ADR | Declared status | Code reality | Gap |
| --- | --- | --- | --- |
| ADR-018 Web Security Hardening | Accepted | `server/app.ts` wires only CORS + auth | No rate-limit / CSRF / security-headers middleware |
| ADR-019 Web Feature Parity | Accepted | `config-schema.ts` ≈37 fields; `auth.ts` has only login/refresh/logout | Missing 26 config keys, no `change-password`, no `SMTP_SERVER`/`PAGE_START` aliases, no `duration` trend |
| ADR-020 Web Operational Polish | Accepted | No corresponding implementation found | No workflow-schema endpoint, no dispatch input validation, qb test lacks `status` field |
| ADR-026 AI Operations Diagnosis | Phase 1 delivered (2026-05-27) | — | Phase 2 (history analytics), Phase 3 (gated remediation) pending |
| ADR-022/023/024/025 | Proposed | Not started | Preference foundation/model, proxy recommendation, torrent quality evidence |

## Decision

### Design Decisions

**D1. Govern the web platform as one initiative.** Treat the Cloudflare/local
parity gaps and the web ADR backlog as a single governed program, sequenced by
this umbrella rather than progressed ad hoc per ADR.

**D2. Prioritization rubric — capability honesty first.** Rank work by:
(1) **capability honesty** (the system must not advertise what it cannot do —
correctness/trust); (2) **security**, escalated to top priority when the console
is internet-exposed; (3) **feature parity** between the two backends;
(4) **operational polish & AI-ops**; (5) **decision intelligence** (largest
scope, longest horizon). Within a tier, prefer low-risk/low-effort items first.

**D3. Five workstreams, each owned by a child ADR.** See
[Workstream Roadmap](#workstream-roadmap). The umbrella owns sequencing and
dependencies; each child ADR owns its design and IMP.

**D4. Renumber the web cluster so the umbrella leads.** No free integer exists
below ADR-019, and a `+1` shift collides with the archived ADR-021. The cluster
therefore moves to a fresh contiguous block at the tail with the umbrella first:
ADR-028 (umbrella) → ADR-029/030/031 (children). Old numbers are retired and
never reused. See [Renumbering Plan](#renumbering-plan).

**D5. Merge "capability honesty" (WS-A) into Feature Parity (ADR-030), not a new
ADR.** The Cloudflare typed-endpoint gaps and the Python `INGESTION_MODE` gap are
parity concerns and belong in the renumbered Feature Parity ADR rather than
spawning a standalone ADR.

**D6. This ADR ships no code.** It is a routing/sequencing record. The only
execution it triggers directly is the bookkeeping renumber in `IMP-ADR028-01`.

### Workstream Roadmap

| WS | Priority | Scope | Owning ADR | Depends on |
| --- | --- | --- | --- | --- |
| **WS-A Capability Honesty** | **P0** | Cloudflare: add typed dispatch endpoints + `job_runs` tracking for `Migration` / `WeeklyDedup` / `TestIngestion`. Python: resolve `INGESTION_MODE=github`/`dual` — either implement GH dispatch in `task_service`, or stop advertising the mode in `/api/capabilities`. | ADR-030 (merged) | — |
| **WS-B Web Security Hardening** | **P1** (P0 if internet-exposed) | Rate-limiting, CSRF protection, security response headers in the Worker. | ADR-029 | — |
| **WS-C Feature Parity** | **P1** | 26 missing config keys, `POST /api/auth/change-password`, canonical key aliases (`SMTP_SERVER`/`PAGE_START`/`PAGE_END`), `duration` stats trend. | ADR-030 | — |
| **WS-D Operational Polish + AI Ops** | **P2** | Workflow-schema endpoint, dispatch input validation, qb test `status` field; ADR-026 Phase 2 (history analytics) and Phase 3 (gated remediation). | ADR-031 + ADR-026 | WS-B, WS-C |
| **WS-E Decision Intelligence** | **P3** | Preference data foundation → preference model; torrent quality evidence; proxy recommendation bandit. Converges on a future `download_utility_score`. | ADR-022 → ADR-025; ADR-024; ADR-023 | loose: WS-A data plumbing |

### Dependency Graph

```
ADR-028  (umbrella roadmap)
  │
  ├─ WS-A  Capability Honesty ......... ADR-030            [P0]
  ├─ WS-B  Security .................... ADR-029            [P1 / P0 if exposed]
  ├─ WS-C  Feature Parity .............. ADR-030            [P1]
  ├─ WS-D  Polish + AI Ops ............. ADR-031, ADR-026 Ph2/3   [P2]
  └─ WS-E  Decision Intelligence       [P3]
            ADR-022 (preference foundation) ─→ ADR-025 (preference model) ─┐
            ADR-024 (torrent quality evidence) ───────────────────────────┴─→ download_utility_score (future)
            ADR-023 (proxy recommendation bandit) ── parallel, independent
```

### Renumbering Plan

`IMP-ADR028-01` executes this mechanical renumber. Until then the child ADRs
remain at their old paths.

| Document | Old number | New number | Old folder | New folder |
| --- | --- | --- | --- | --- |
| Web Platform Completeness Roadmap (this doc) | — | **ADR-028** | — | `ADR-028-Web-Platform-Completeness-Roadmap/` |
| Web Security Hardening | ADR-018 | **ADR-029** | `ADR-018-Web-Security-Hardening/` | `ADR-029-Web-Security-Hardening/` |
| Web Feature Parity | ADR-019 | **ADR-030** | `ADR-019-Web-Feature-Parity/` | `ADR-030-Web-Feature-Parity/` |
| Web Operational Polish | ADR-020 | **ADR-031** | `ADR-020-Web-Operational-Polish/` | `ADR-031-Web-Operational-Polish/` |

**Per-folder rename touches:** the `ADR-0NN-*.md` + `.zh.md` files, the
`IMP-ADR0NN-PP-*.md` filenames, and all internal `ADR-0NN` / `IMP-ADR0NN`
self-references in both languages.

**Blast radius (verified):** confined to `docs/design/`. Cross-references to
018/019/020 exist only in the three cluster folders, in `ADR-022`, and in the
archived `ADR-021`. The web repo, `CONTEXT.md`, `CLAUDE.md`, and `README.md`
contain **no** references to these numbers. `.claude/worktrees/*` are separate
worktrees and are out of scope.

## Consequences

### Positive

- A single document answers "is the platform complete?" and sequences the
  remaining work, replacing scattered per-ADR judgement.
- Capability-honesty gaps (the system advertising what it cannot do) are
  surfaced as P0 instead of lingering as silent trust bugs.
- The umbrella sorts immediately before its children (ADR-028 → 029/030/031),
  making governance precedence visible in the directory listing.

### Negative

- Renumbering three Accepted ADRs mutates historical records and requires a
  careful, mechanical cross-reference sweep.
- The retired numbers (018/019/020) leave gaps in the sequence that future
  readers must understand as "moved, not missing."

### Risks

- A missed cross-reference during renumber yields a dangling link. Mitigated by
  scoping the blast radius up front and grepping after the move.
- Treating the umbrella as a place to *do* work (rather than route it) would
  re-create the sprawl it aims to fix. Mitigated by D6 (ships no code).

## Implementation Roadmap

| Phase | IMP | Ships | Deferred |
| --- | --- | --- | --- |
| Phase 1 | `IMP-ADR028-01` | Execute the web-cluster renumber (028 umbrella + 029/030/031 children); update cross-references in `ADR-022` and archived `ADR-021`; merge WS-A capability-honesty scope into the renumbered Feature Parity ADR (030). | All feature implementation — lives in the child ADRs' own IMPs (`IMP-ADR029-*`, `IMP-ADR030-*`, `IMP-ADR031-*`). |

## References

- [ADR-017 Cloudflare-First Deployment](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md) — the two-backend split this roadmap governs
- [ADR-007 Monorepo Restructure](../_archive/ADR-007-Monorepo-Restructure/ADR-007-monorepo-restructure-2026-05.md) — canonical layout
- Child ADRs: [ADR-029](../_archive/ADR-029-Web-Security-Hardening/ADR-029-web-security-hardening.md), [ADR-030](../_archive/ADR-030-Web-Feature-Parity/ADR-030-web-feature-parity.md), [ADR-031](../_archive/ADR-031-Web-Operational-Polish/ADR-031-web-operational-polish.md), [ADR-026](../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md), [ADR-022](../ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md), [ADR-023](../ADR-023-Proxy-Recommendation-Policy/ADR-023-proxy-recommendation-policy.md), [ADR-024](../ADR-024-Torrent-Quality-Evidence/ADR-024-torrent-quality-evidence.md), [ADR-025](../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md)

## Status Log

- 2026-05-29: Accepted. Umbrella roadmap created; web-cluster renumber and WS-A
  scope-merge delegated to `IMP-ADR028-01`.
- 2026-05-29: `IMP-ADR028-01` executed — web cluster renumbered (018/019/020 → 029/030/031); WS-A capability-honesty scope merged into ADR-030.
