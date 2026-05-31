# IMP-ADR024-08: ADR-024 Phase 2 — Assist Mode (Outline)

**Status:** Outline — requires refinement before execution

> **For agentic workers:** This is an **outline IMP**, not an executable bite-sized plan. Phase 2 depends on Phase 1 evidence existing in production and on decisions that are not yet settled (remote probe endpoint, Top-K, Web surface in the standalone `javdb-autospider-web` repo). Before executing, run `superpowers:writing-plans` to expand each task below into TDD-level steps, and `superpowers:brainstorming`/`superpowers:grill-me` for the open questions. Do not write code straight from this file.

**Goal (Phase 2):** Move from shadow-only observation to **assist** mode — surface per-category quality recommendations and review actions to operators (API/Web), still **without** automatic enforcement.

**Source spec:** [ADR-024](ADR-024-torrent-quality-evidence.md), roadmap Phase 2; D8 (Top-K), D5/D6/D7 (remote probe), D11 (`TORRENT_QUALITY_POLICY_MODE=assist`).

**Depends on:** Phase 1 (IMP-ADR024-01..07) shipped and accumulating evidence in D1.

---

## Prerequisites that must be resolved first

These were explicitly **deferred** from Phase 1 and are likely prerequisites for a
meaningful assist mode. Each needs its own design pass before this IMP can be
expanded:

1. **Movie context join.** Phase 1 evaluations may carry an empty `movie_href`
   (the production qB torrent does not expose the JavDB href/video_code). Assist
   recommendations are per-movie-per-category, so a reliable `info_hash → movie`
   linkage must exist first (e.g. recorded at upload time, or joined from CSV /
   `ReportTorrents`). **Open question — design before coding.**
2. **Top-K runner-up collection (D8).** Assist's value is "the ranker would have
   chosen a different candidate." That requires evidence for runner-up magnets,
   which Phase 1 does not collect (CSV keeps one magnet per category). Needs the
   runner-up plumbing (sidecar JSON or new CSV columns) + bounded Top-K probe.
3. **Remote `quality_probe` endpoint (D4-D7).** Probing runner-ups means adding
   magnets to a dedicated remote qB with metadata-only capability detection
   (`stopCondition=MetadataReceived`), a dedicated `JavDB Quality Shadow`
   category, and short-lived removal. None of this exists yet. **High-risk —
   needs its own IMP/spec.**

---

## Outline task list (expand with writing-plans before executing)

### Task A — `policy_mode=assist` activation (no auto-change)

- Make the collector/evaluator honour `TORRENT_QUALITY_POLICY_MODE=assist`,
  computing `would_replace_current_choice` and `shadow_rank` per category once
  Top-K evidence exists. Still writes only — no uploader change.
- Verify: evaluation rows show `policy_mode=assist` and a populated
  `would_replace_current_choice`; production download decision byte-identical.

### Task B — Per-category recommendation surface (API)

- Extend `/api/quality` with a per-movie-per-category recommendation view:
  "current choice vs. recommended candidate vs. why" (reason codes diff).
- Add a `decision=needs_review` queue endpoint for operator triage.
- Verify: API returns recommendations only when assist mode is on; read-only.

### Task C — Web review actions (standalone repo)

- In `javdb-autospider-web` (separate repo — coordinate per ADR-018), add a
  review UI: list `needs_review` evaluations, show evidence + reason codes, let an
  operator accept/reject a recommendation (writes a review decision, not a
  download). Mirror the D1 query/auth contract from the Python backend.
- Verify: TS + Python query/response shapes stay in sync (ADR-018 rule).

### Task D — Top-K + remote probe (likely its own IMP)

- If not already delivered as a standalone IMP, plumb runner-up magnets, build the
  remote `quality_probe` collector with capability detection and fail-closed
  behaviour, and feed its evidence (`target_role=quality_probe`) into the same
  tables. **Recommend a dedicated IMP-ADR024-1x for this — it is the largest and
  riskiest piece.**

---

## Scope Boundaries (Phase 2)

- Still **no automatic enforcement** — assist only recommends and queues for review.
- No video-content inspection (still D10-deferred).
- Any production-path change (uploader, category) is **out of scope** until Phase 3.

## Definition of Done (high-level)

- Operators can see, per movie+category, what the ranker would have chosen and why.
- A `needs_review` triage flow exists end to end (API + Web).
- Production download behaviour is provably unchanged (assist writes only).
