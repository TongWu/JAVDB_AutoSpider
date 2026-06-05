# BFR-017: D1 schema drift — `ReportSessions.CommittedAt` never applied to remote D1

**Status**: Fixed
**Date**: 2026-06-05
**Severity**: Critical (every D1-backend pipeline run failed at commit time)
**Affected**: `javdb/storage/db/_db_reports.py`, `javdb/storage/db/_db_history_write.py`, `javdb/migrations/d1/2026_06_03_add_reports_committed_at.sql`, `javdb/infra/health_check.py`, `javdb/storage/db/_db_migrations.py`
**Related**: [PR #165](https://github.com/TongWu/JAVDB_AutoSpider_CICD/pull/165) (introduced the drift), DailyIngestion run [27015949886](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/27015949886)

---

## Symptom

The 2026-06-05 `DailyIngestion` run failed in two places, both with the same D1 error:

- **`run-pipeline` → "Mark sessions as committed"** (exit 1):
  ```
  ✗ __main__  db_commit_session_history failed for pending session
    20260605T125521.738770Z-c765-0000:
    D1 API returned HTTP 400: [{'code': 7500,
    'message': 'no such column: CommittedAt: SQLITE_ERROR'}]
  ```
- **`Cleanup Failed Pipeline` → "Roll back uncommitted D1 writes"** (exit 4): the
  same `no such column: CommittedAt` error while the rollback CLI tried to drive
  the session to `committed` via `--auto-resume-finalizing`.

The spider, uploader, file-filter, PikPak and rclone steps had all succeeded — the
failure was purely at the session-commit boundary. The session was left stranded in
`Status='finalizing'` with its staged pending rows neither promoted nor rolled back.

## Root Cause

[PR #165](https://github.com/TongWu/JAVDB_AutoSpider_CICD/pull/165) (merged to `main`
at 2026-06-05T00:46Z) shipped **two coupled changes**:

1. Code that writes the column — `db_commit_session_history` /
   `db_finish_commit_session` now run
   `UPDATE ReportSessions SET Status='committed', CommittedAt=strftime(...)`
   ([`_db_reports.py:165`](../../../javdb/storage/db/_db_reports.py),
   [`:770`](../../../javdb/storage/db/_db_reports.py)).
2. The matching D1 migration
   [`2026_06_03_add_reports_committed_at.sql`](../../../javdb/migrations/d1/2026_06_03_add_reports_committed_at.sql)
   (`ALTER TABLE ReportSessions ADD COLUMN CommittedAt TEXT`).

**The migration was never executed against remote D1.** Per the canonical-D1
discipline in `CLAUDE.md`, D1 schema changes are applied **out-of-band** with
`wrangler d1 execute … --file=…`; that manual step was missed, so the code went live
on `main` while D1 still lacked the column.

The drift was invisible to tests and local runs because **local SQLite self-heals**:
`_ensure_rollback_columns` ([`_db_migrations.py`](../../../javdb/storage/db/_db_migrations.py))
idempotently `ALTER`s the column into any pre-existing SQLite database at
`init_db()`. D1 has no equivalent auto-migration, so only the `STORAGE_BACKEND=d1`
production path was affected.

The deeper design flaw: **the code-side dependency on a column and the D1-side
application of the migration are decoupled, with nothing verifying they agree before
work begins.** The mismatch could therefore only surface at *commit* time — after a
full scrape — rather than at startup.

## Fix

**Immediate remediation (data):**
- Applied `ALTER TABLE ReportSessions ADD COLUMN CommittedAt TEXT` to remote D1
  `javdb-reports`.
- Resume-committed the stranded session `20260605T125521.738770Z-c765-0000`
  (`apps.cli.db.commit_session --session-id … --no-claim-commit`): 45 movies / 76
  torrents promoted to the live tables, 204 pending rows drained, `Status` →
  `committed`, `CommittedAt` set. D1 is consistent again.

**Guard against recurrence (code):**
- `javdb/storage/db/_db_migrations.py`: extracted the rollback/pending column list
  into a module constant `ROLLBACK_COLUMN_SPECS` (single source of truth, previously
  a local in `_ensure_rollback_columns`) and added `find_missing_rollback_columns(conn)`
  — a read-only audit that returns the expected columns absent on a live connection.
  Works against both sqlite3 and D1 connections.
- `javdb/infra/health_check.py`: added `check_d1_schema()` as a **critical** pre-flight
  check (the first one). On the `d1`/`dual` backends it audits all three logical D1
  databases and **fails the run before the spider starts** if any rollback/pending
  column is missing, with a message pointing at the un-applied migration. SQLite is
  skipped (it self-heals). The pre-existing non-critical checks (qB, proxy, SMTP) are
  unchanged.
- `tests/unit/test_d1_schema_drift_guard.py`: 8 tests pinning the audit (detects the
  exact `CommittedAt` shape, ignores absent tables, tolerates D1 dict rows, asserts
  `ROLLBACK_COLUMN_SPECS` matches the real init schema) and `check_d1_schema`
  (skips non-D1, reports drift, passes when present).

The health-check step in `DailyIngestion.yml` / `AdHocIngestion.yml` runs with
`set -e` and no `continue-on-error`, so a `check_d1_schema` failure aborts the job
at the pre-flight stage — no wasted scrape, no stranded session.

## Side Effects

- On the `d1`/`dual` backends, pre-flight now opens three short-lived D1 connections
  to read `PRAGMA table_info`, adding a few seconds and requiring D1 credentials at
  health-check time (already present — `Restore encrypted config` runs first).
- `check_d1_schema` is the **first check that can actually fail the pipeline**. The
  legacy `all_passed` summary path (which never failed) is retained verbatim for the
  existing informational checks; the new critical-failure path is additive.
- SQLite-backend behaviour is unchanged (the check is skipped).

## Follow-Up

- [x] Apply the `CommittedAt` migration to remote D1.
- [x] Resume-commit the stranded session `20260605T125521.738770Z-c765-0000`.
- [x] Add the pre-flight D1 schema-drift guard + unit tests.
- [ ] Consider broadening the audit beyond the rollback/pending columns to a full
      `migrations/d1/*.sql` ↔ live-D1 column diff (catches drift in any future
      migration, not just rollback-critical ones).
- [ ] Consider a CI reminder on PRs that add a `javdb/migrations/d1/*.sql` file,
      prompting "applied to remote D1?" before merge.
