# IMP-ADR024-09: ADR-024 Phase 3 — Enforce Mode (Outline)

**Status:** Outline — requires refinement before execution

> **For agentic workers:** This is an **outline IMP**, not an executable bite-sized plan. Phase 3 changes the production download decision and is the highest-risk phase. Before executing, run `superpowers:writing-plans` to expand each task into TDD-level steps and `superpowers:grill-me`/`superpowers:brainstorming` for the rollout-gate and threshold-tuning design. Do not write code straight from this file.

**Goal (Phase 3):** Behind rollout gates, let the quality ranker **enforce** a better per-category candidate than the total-size heuristic, with threshold tuning informed by the accumulated Phase 1/2 dataset, plus backfill/reporting jobs.

**Source spec:** [ADR-024](ADR-024-torrent-quality-evidence.md), roadmap Phase 3; D11 (`TORRENT_QUALITY_POLICY_MODE=enforce`). Note D10 still defers video-content/CV inspection and heavyweight ML runtimes.

**Depends on:** Phase 2 (assist) shipped, with enough reviewed data to trust thresholds; Top-K + remote probe delivered (the assist-phase prerequisite).

---

## Prerequisites that must be resolved first

1. **Trustworthy thresholds.** Enforce must not regress production. The accept/reject
   thresholds and reason-code gates need tuning against the labelled assist-era
   dataset (operator accept/reject decisions). **Design a tuning/validation
   methodology before coding** (offline replay over historical evidence).
2. **Rollback safety.** Changing which torrent is downloaded interacts with the
   session/rollback system, dedup, PikPak, and file-filter. Each downstream effect
   needs explicit consideration. **Cross-module — likely a BFR-class risk surface.**
3. **Rollout gating.** Needs a staged gate (per-category, per-percentage, or
   allowlist) so enforce can be enabled narrowly and rolled back instantly.

---

## Outline task list (expand with writing-plans before executing)

### Task A — Rollout gate + `policy_mode=enforce`

- Add a narrow, instantly-reversible gate (e.g. enforce only for specific
  categories or a sampled fraction). When enforce is active for a movie+category,
  the uploader selects the quality-ranked candidate instead of the heuristic one.
- Verify: with the gate off, production behaviour is byte-identical to today; with
  it on for a test category, the selected magnet matches the ranker's top choice.

### Task B — Threshold tuning + offline replay

- Build an offline tool that replays scoring over historical `TorrentQualityEvidence`
  / `TorrentQualityEvaluation` rows and reports what enforce *would* have changed,
  with precision/recall against operator review labels.
- Verify: tuning report is reproducible and version-stamped (`scoring_version`).

### Task C — Backfill + reporting jobs

- Backfill evidence/evaluation for historical torrents where file lists are still
  available; add a periodic quality report (counts by decision, category-mismatch
  rate, junk-ratio distribution).
- Verify: backfill is idempotent (UPSERT by PK); report numbers reconcile with D1.

### Task D — Production guardrails

- Add monitoring/alerts for enforce-mode regressions (e.g. spike in
  `needs_review`, drop in main-video-ratio of enforced choices) and an emergency
  off-switch.
- Verify: flipping the off-switch reverts to heuristic selection within one run.

---

## Scope Boundaries (Phase 3)

- Still **no** video-content/CV inspection or heavyweight ML runtimes (D10).
- Enforce must be gated and instantly reversible — no all-at-once rollout.
- Every downstream interaction (rollback, dedup, PikPak, file filter) must be
  verified before enabling enforce for a category.

## Definition of Done (high-level)

- Enforce can be enabled for a narrow scope behind a gate and reverted instantly.
- A reproducible threshold-tuning/replay report justifies the chosen thresholds.
- Backfill + periodic reporting jobs exist and reconcile with D1.
- Production guardrails (alerts + off-switch) are in place and tested.
