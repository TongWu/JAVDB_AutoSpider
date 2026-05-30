# Session Lifecycle Authority — Phase 2: `CommitPipeline`

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the data-movement substeps of `_commit_session_bulk` into a named, individually-testable `CommitPipeline`, and delegate its status flip to `SessionLifecycle` (Phase 1). The classification core becomes unit-testable with in-memory overlays — no real database.

**Architecture:** A new module `javdb/storage/db/_db_commit_pipeline.py` holding four substeps — `prefetch_pending` → `classify_and_resolve` (pure, with an injected live-lookup) → `batch_upsert` → `mark_applied`. `_commit_session_bulk` is rewritten as a thin orchestration over them, preserving its exact return tuple so the 4-pass rescan loop in `db_commit_session_history` is untouched.

**Tech Stack:** Python 3.11+, pytest. Single repo. No schema change, no D1 semantics change.

**Related:** [ADR-019](ADR-019-session-lifecycle-authority.md), [IMP-ADR019-01](IMP-ADR019-01-session-lifecycle.md) (**must land + be production-verified first** — provides `SessionLifecycle.transition`)

**Status:** Superseded / won't-do (2026-05-30). Phase 1 (IMP-ADR019-01) is complete and production-verified, so the *only* status-flip work this IMP scoped (Task 3's `:1636` / `:1535` flips → `SessionLifecycle.transition`) was **already delivered by Phase 1** — see `_db_history_write.py` (`transition(session_id, "finalizing", ...)` and `transition(session_id, "committed", ...)` now in place). The sole remaining deliverable was the `_commit_session_bulk` four-substep extraction whose own *Honest framing* section below judged it not worth touching the hottest write path purely for an isolation-test surface (no LOC reduction; essential, irreducible complexity). Decision (Ted, 2026-05-30): accept that framing and **do not pursue the extraction**. The high-value, low-risk win (Phase 1 lifecycle authority) is already banked. The substep-extraction steps (Tasks 1–5 below) are preserved as historical context only — do **not** execute them.

---

## Honest framing (read before starting)

This phase does **not** reduce lines of code. The commit's complexity — the 4-pass rescan, dual-backend integer-ID pre-generation, D1 99/100-param chunking, and conflict-deletion shadowing — is **essential** and irreducible. Total LOC will likely *increase* (interface + dataclass boilerplate). The deliverable is **isolation-testability**: `classify_and_resolve` exercised with in-memory overlay dicts and a stubbed live-lookup, asserting the produced SQL plan, with no DB. If the team does not value that test surface enough to justify touching the hottest write path in the system, **do not start this phase** — Phase 1 already captured the high-value, low-risk win.

---

## Scope

- **In:** extract `prefetch_pending` / `classify_and_resolve` / `batch_upsert` / `mark_applied` from `_commit_session_bulk` (`_db_history_write.py:1047-1380`); rewrite `_commit_session_bulk` to orchestrate them; delegate the status flip at `:1636` to `SessionLifecycle.transition`.
- **Out:** the non-bulk `_commit_one_movie` fallback (`:1580-1620`, legacy, default-off); the `committed`-residual cleanup branch (`:1506-1526`); `_d1_retry_pending_cleanup` (`:1401-1441`) — all stay in the orchestrator. The crash-ordering invariant (`:1622-1633`) is preserved, not moved.
- **Behavior:** strictly preserving. Bulk output must remain byte-identical to today (and to the per-href path).

---

## File Map

| Action | Path | Responsibility |
| ------ | ---- | -------------- |
| Create | `javdb/storage/db/_db_commit_pipeline.py` | `prefetch_pending`, `classify_and_resolve`, `batch_upsert`, `mark_applied`, `WritePlan` dataclass |
| Modify | `javdb/storage/db/_db_history_write.py` | Rewrite `_commit_session_bulk` (`:1047`) to orchestrate the substeps; status flip (`:1636`) → `SessionLifecycle.transition` |
| Create | `tests/unit/test_commit_pipeline.py` | `classify_and_resolve` with in-memory overlays + stub lookups (no DB); `mark_applied` chunking |

> The new module lives in `db/` (not `sessions/`) because it shares private helpers with `_db_history_write.py`: `_bulk_run`, `_chunked`, `_generate_integer_id`, `_compute_indicators`, `_merge_movie_overlay_rows` / `_merge_torrent_overlay_rows`, `_href_lookup_variants`. Import them rather than duplicating.

---

## Task 1: Map the seam precisely (no edits)

- [ ] Re-read `_commit_session_bulk` (`_db_history_write.py:1047-1380`) and label the phases against the agent's map:
  - **Phase A** prefetch overlays — `:1078-1101`
  - **Phase A.2 + B + C2** classify hrefs + read live rows + classify torrents — `:1115-1305`
  - **Phase C1 + D + E** batched movie/torrent writes + indicator recompute — `:1225-1351`
  - **Phase F** mark pending rows applied — `:1353-1378`
- [ ] Confirm the return tuple shape `(counts, consumed_movie_seqs, consumed_torrent_seqs)` and how `db_commit_session_history`'s 4-pass loop (`:1550-1572`) consumes it.
- [ ] Identify the cross-phase carriers that MUST survive extraction intact: the `projected` torrent-state map threaded D→E (`:1250-1305`), the pre-generated integer IDs (`:1178`, `:1276`), and the conflict-deletion rules (`:1313-1335`).

---

## Task 2: Extract the substeps into `_db_commit_pipeline.py`

- [ ] `prefetch_pending(conn, session_id, *, exclude_movie_seqs, exclude_torrent_seqs) -> Overlay` — Phase A. Returns the movie + torrent overlays (use the existing `_merge_*_overlay_rows` helpers).
- [ ] `classify_and_resolve(overlay, *, when, session_id, base_url, live_lookup) -> WritePlan` — Phases A.2+B+C2. **The live-row reads (C2) are injected via `live_lookup`** (a callable `hrefs -> {href: row}`) so the classification core is pure and testable with stubs. Returns a `WritePlan` dataclass: `new_movie_inserts`, `movie_updates`, `torrent_writes`, `indicator_inputs`, `consumed_movie_seqs`, `consumed_torrent_seqs`, `counts`. **No execution** inside.
- [ ] `batch_upsert(conn, plan) -> Counts` — Phases C1+D+E. Executes the batched writes (`_bulk_run` + `_chunked` at 99/100) and the indicator recompute (`_compute_indicators`).
- [ ] `mark_applied(conn, consumed_movie_seqs, consumed_torrent_seqs) -> int` — Phase F. Chunked over >99 seqs.
- [ ] Keep the `WritePlan` fields as plain lists of `(sql, params)` and primitive dicts so it is trivially assertable in tests.

---

## Task 3: Rewrite `_commit_session_bulk` as orchestration

- [ ] Replace the body of `_commit_session_bulk` (`:1047-1380`) with:

```python
overlay = prefetch_pending(conn, session_id, exclude_movie_seqs=..., exclude_torrent_seqs=...)
plan = classify_and_resolve(overlay, when=..., session_id=session_id, base_url=...,
                            live_lookup=lambda hrefs: _live_rows_by_href(conn, hrefs))
counts = batch_upsert(conn, plan)
mark_applied(conn, plan.consumed_movie_seqs, plan.consumed_torrent_seqs)
return counts, plan.consumed_movie_seqs, plan.consumed_torrent_seqs
```

- [ ] Keep the return tuple identical so `db_commit_session_history`'s 4-pass loop is untouched.
- [ ] In `db_commit_session_history`, replace the status flip at `:1636` with `SessionLifecycle.transition(session_id, "committed")` (from IMP-ADR019-01). Preserve the crash-ordering invariant (`:1622-1633`): flip happens **before** the pending delete.
- [ ] Leave the non-bulk fallback (`:1580-1620`), the committed-residual cleanup (`:1506-1526`), and `_d1_retry_pending_cleanup` (`:1401-1441`) where they are.

---

## Task 4: Tests

- [ ] Create `tests/unit/test_commit_pipeline.py` (the testability deliverable):
  - `classify_and_resolve` with hand-built in-memory overlays + a stub `live_lookup` returning fixed rows. Assert the `WritePlan`: insert-vs-update classification, conflict-deletes, indicator inputs, consumed seqs. **No real DB.**
  - `mark_applied` chunking correctness over >99 seqs (assert it issues chunked statements, not one giant IN).
- [ ] **Gold guardrails — must stay green (these are why this phase is safe):**
  - `tests/unit/test_commit_session_bulk.py::test_bulk_and_perhref_snapshots_match` (`:223`) — bulk output == per-href output.
  - `tests/unit/test_commit_session_bulk.py::test_bulk_path_issues_far_fewer_statements` (`:291`) — statement-count budget.
- [ ] Full regression: `pytest tests/unit/test_history_manager.py tests/unit/test_rollback_full_fidelity.py tests/unit/test_batch_c_movie_history_id.py tests/unit/test_pending_torrent_overlay_merge.py tests/unit/test_d1_dual.py -q`.

---

## Task 5: Verification gates

- [ ] `pytest tests/unit/test_commit_pipeline.py tests/unit/test_commit_session_bulk.py -v` — green (esp. the bulk==per-href parity and statement-budget tests).
- [ ] Full storage suite green.
- [ ] Diff review confirms: no change to the 4-pass loop, no change to crash-ordering, the `projected` torrent-state map and pre-generated IDs threaded intact through `WritePlan`.
- [ ] Update this IMP's `Status` to `Completed`, check off `IMP-ADR019-02`, and — if both IMPs are done — archive the ADR-019 folder per the docs convention.

---

## Risks

- **Behavior preservation is paramount** — this is the hottest write path. The exclude-seq dedup, 99/100-param chunking (`:1120,1233,1355`), pre-generated integer IDs (`:1178,1276`), and conflict-deletion rules (`:1313-1335`) must move verbatim.
- **The D→E `projected` torrent-state map** (`:1250-1305`) is the trickiest seam — if `WritePlan` drops or reshapes it, indicator recompute drifts. Pin it explicitly in `test_commit_pipeline.py`.
- **Accidental extra round-trips** — naively moving the C2 live-lookup can turn one batched read into N. Keep the batched `_bulk_run` shape; the injected `live_lookup` must itself batch.
