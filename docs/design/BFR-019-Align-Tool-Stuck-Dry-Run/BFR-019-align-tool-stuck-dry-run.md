# BFR-019: Inventory-alignment migration tool stuck dry-run-only after ADR-005 PR-4

**Status**: Fixed
**Date**: 2026-06-15
**Severity**: Medium
**Affected**: `javdb/migrations/tools/align_inventory_with_moviehistory.py`, `javdb/migrations/migrate_to_current.py`, `.github/workflows/WeeklyDedup.yml` (caller)
**Related**: [ADR-005](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md) (amendment 9), [IMP-ADR005-01](../_archive/ADR-005-Db-Py-Retirement/IMP-ADR005-01-drop-audit-mode.md), [IMP-ADR005-02](../_archive/ADR-005-Db-Py-Retirement/IMP-ADR005-02-delete-db-facade.md)

---

## Symptom

The WeeklyDedup workflow's downstream `align-inventory-history` job (which calls
`Migration.yml` with `align_inventory_history: true`, `dry_run: false`) failed
**every week** at the migration step:

```
Executing: python3 -m apps.cli.db.migration --align-inventory-history --align-shuffle --align-enqueue-qb
…
✗ javdb.migrat  db_upsert_history was removed by ADR-005 PR-4. This tool cannot
                write history in non-dry-run mode until it is rewritten to use
                the staging+commit path.
Error: Process completed with exit code 1.
```

The weekly dedup itself succeeded; only the chained alignment job went red. The
failure fired immediately on entry to `run_alignment`, before any JavDB fetch —
so the logs showed a clean schema check (`history.db already at v9 … No schema
migration needed`) followed straight by the abort.

## Root Cause

This was **not** a crash — it was an intentional fail-fast guard masking an
**unfinished migration step**.

ADR-005 D2 retired Audit Mode and PR-4 deleted the direct-upsert write path
`db_upsert_history` / `db_upsert_history_batch`. The inventory-alignment
migration tool (`align_inventory_with_moviehistory.py`) used those functions to
write `MovieHistory` / `TorrentHistory` for inventory-only movie codes. PR-4's
verification step explicitly flagged the leftover reference as **"known PR-5
scope"** ([IMP-ADR005-01](../_archive/ADR-005-Db-Py-Retirement/IMP-ADR005-01-drop-audit-mode.md)).

But PR-5 ([IMP-ADR005-02](../_archive/ADR-005-Db-Py-Retirement/IMP-ADR005-02-delete-db-facade.md))
treated the tool as a pure **import rewrite** only — it never performed the
write-path rewrite the flag called for. To avoid an `ImportError` at test
collection (the symbols were gone) and to avoid a mid-run `NotImplementedError`,
the symbols were replaced with an `_audit_retired_stub`, and `run_alignment`
gained a guard that aborts with exit 1 whenever `--dry-run` is not set:

```python
db_upsert_history = _audit_retired_stub        # raises NotImplementedError if called

def run_alignment(args):
    if not args.dry_run and db_upsert_history is _audit_retired_stub:
        logger.error("db_upsert_history was removed by ADR-005 PR-4 …")
        raise SystemExit(1)
```

The guard was the *correct* defensive choice (fail loudly instead of corrupting
history), but the state it guarded — "this tool cannot write" — was an
**unintended leftover**, not a designed end-state. The deeper flaw is a **process
gap**: a deferred migration item ("known PR-5 scope") was dropped with no
tracking artifact (no ADR follow-up, no BFR, no issue). Nothing connected the
deferral to a caller, so it stayed invisible until the only non-dry-run caller —
the weekly `align_inventory_history` job — surfaced it as a recurring red build,
roughly three weeks later.

## Fix

Rewrote the alignment tool onto the same session-scoped **staging + commit**
path the spider uses (the path ADR-005 D3 prescribed as the replacement):

- `run_alignment` opens its own pending `ReportSessions` row via
  `SessionLifecycleRepo().create_report_session(report_type='alignment',
  write_mode='pending', …)` (unless an explicit `--session-id` is adopted), and
  primes the active run identity from `GITHUB_RUN_ID` / `GITHUB_RUN_ATTEMPT`.
- `_BatchedHistoryWriter` now stages each aligned movie + its non-empty torrent
  buckets through `HistoryRepo.stage_movie` / `stage_torrent` (new helper
  `_stage_aligned_movie`) instead of calling the deleted `db_upsert_history`.
  All four torrent categories are staged when present, preserving the tool's
  pre-ADR-005 behaviour (not the spider's best-of-pair collapse).
- At end of run the session is committed via `HistoryRepo().commit_session`
  (drains pending → live, walks `in_progress → finalizing → committed`).
- The session is opened **only once there is work to do** (after the
  `if not missing_codes: return 0` early-return), so an empty alignment never
  creates an orphan session.
- The whole staging/commit body runs inside `_run_alignment_core`, wrapped by a
  guard in `run_alignment`: **any** failure — a mid-batch staging error (e.g. a
  D1 write under `STRICT_DUAL_WRITE`), an unexpected exception, or a
  `KeyboardInterrupt` on either the parallel or sequential path — rolls the
  session back via `SessionLifecycleRepo().rollback_session` and re-raises,
  instead of leaving an `in_progress` row with staged pending writes. This
  matters because `Migration.yml` has no on-failure cleanup step. (Severity was
  bounded even before this guard: `StaleSessionCleanup` rolls orphaned
  `in_progress` sessions back after 48h — only `finalizing` sessions are
  resumed-to-committed — so a leaked session was discarded, never applied. The
  guard makes the tool self-heal immediately rather than relying on the cron.)
- The `_audit_retired_stub`, the guard, and the "required until rewrite" wording
  on `--dry-run` are removed.
- `migrate_to_current.py`'s `align_ns` now carries an explicit `session_id=None`
  (the migration CLI exposes no `--session-id`, so the tool self-opens one).

Tests (`tests/integration/test_align_inventory_with_moviehistory.py`): added
coverage for `_stage_aligned_movie` (stages all non-empty categories, skips
empties), `_finalize_alignment_session` (commit-on-success, rollback-on-failure),
and an end-to-end non-dry-run `run_alignment` that asserts session open → stage →
commit.

ADR-005 updated with amendment 9 recording the deferred-step closure.

## Side Effects

- The alignment job now actually writes again. Its `MovieHistory` /
  `TorrentHistory` rows land atomically at commit (pending-mode semantics)
  rather than via immediate audit-mode upserts — consistent with every other
  ingestion path and subject to scoped rollback.
- Each alignment run now creates one `ReportSessions` row (`ReportType='alignment'`).
  Stale/interrupted runs are rolled back in-process; the daily
  `StaleSessionCleanup` cron remains the backstop.
- No change to the qB-upgrade / rclone-purge planning or CSV outputs.

## Follow-Up

- [x] Rewrite the tool to staging+commit and remove the guard.
- [x] Add regression tests for the staging+commit and rollback paths.
- [x] Record the deferred-step closure in ADR-005 (amendment 9, both languages).
- [x] Adversarial multi-agent review of the rewrite — surfaced a session-leak
      gap (no guard around the body); fixed by the `_run_alignment_core` wrapper
      and covered by `test_run_alignment_rolls_back_session_on_core_error` /
      `test_run_alignment_empty_missing_codes_opens_no_session`.
- [x] PR review round (Codex) — four follow-ups addressed:
      (1) **actor metadata clobber** — an empty-actor parse staged
      `SupportingActors='[]'` (truthy, so it survives the stage's `'' → None`
      coercion that protects the name/gender/link fields), which the commit
      then wrote over good data; `_blank_actor_field_to_none` now nulls blank /
      `'[]'` actor fields so the commit preserves existing rows (matching the
      retired path's `_has_meaningful_actor_data` guard).
      (2) **commit-exception leak** — `_finalize_alignment_session` now
      re-raises a failing commit so the outer guard's `rollback_session` runs
      its status-aware cleanup (resume `finalizing`, roll back `in_progress`)
      instead of swallowing the error and leaving the row for the 48h sweep.
      (3) **adopted `--session-id` validation** — `_verify_adoptable_session`
      rejects a missing / non-pending / non-`in_progress` id rather than
      staging into it and exiting 0 having written nothing.
      (4) **qB-enqueue failure** kept after the commit (decoupling alignment
      from qB availability is intentional — a transient qB outage must not force
      a full JavDB re-scrape), but the error now points to the persisted upgrade
      CSV for manual re-enqueue.
- [ ] Confirm the next scheduled WeeklyDedup → `align_inventory_history` run is
      green (next weekly cron after merge).
