# 2026-05-08 — Decouple SessionId from per-backend AUTOINCREMENT

## What changed

1. `ReportSessions` gained three NULLABLE columns: `RunId TEXT`,
   `RunAttempt INTEGER`, `FailureReason TEXT`.
2. `MovieHistoryAudit` and `TorrentHistoryAudit` each gained NULLABLE
   `RunId TEXT` and `RunAttempt INTEGER` columns.
3. New compound indexes `idx_report_sessions_run`, `idx_mh_audit_run`,
   `idx_th_audit_run`.
4. **Behavioural change** (no schema impact): the application layer now
   generates `ReportSessions.Id` itself via `_generate_session_id()`
   (microsecond-timestamp snowflake) and supplies it explicitly in
   `INSERT`s instead of relying on each backend's AUTOINCREMENT counter.
   This eliminates id drift between SQLite and Cloudflare D1 under
   `STORAGE_BACKEND=dual`.

## Why

A `STORAGE_BACKEND=dual` deployment (SQLite mirror + D1 source-of-truth)
relied on `cur.lastrowid` to learn the id of the row it had just inserted.
`DualCursor` returns SQLite's lastrowid, so a workflow run that managed to
insert into both backends successfully would still see the SQLite id —
which differs from the D1 id whenever the two AUTOINCREMENT counters
have ever drifted (any past INSERT that succeeded on one side and not the
other puts them permanently out of sync).

On 2026-05-08 a daily run minted SQLite `Id=332` while D1 minted
`Id=346`; the spider tagged every downstream write with `SessionId=332`,
which on D1 collided with a stale `SessionId=332` left over from a
2026-05-07 workflow. The subsequent rollback CLI saw 145 audit rows
spanning 35 hours and refused to roll most of them back as drift.

## How to apply

```bash
wrangler d1 execute javdb-reports --remote \
  --file=migration/d1/2026_05_08_add_run_identity_columns_reports.sql
wrangler d1 execute javdb-history --remote \
  --file=migration/d1/2026_05_08_add_run_identity_columns_history.sql
```

Both files use `ALTER TABLE ... ADD COLUMN` only, so they are forward and
backward compatible: legacy rows have `RunId IS NULL` and continue to be
addressed via `SessionId`; new rows have both attributes available so the
rollback CLI can pick the most specific lookup.

## How to roll back

`ALTER TABLE ... DROP COLUMN` on D1 is supported on recent SQLite
versions, but rolling back is rarely needed — the new columns are
NULLABLE and old code paths keep working when they are unset.

If you really need to undo:

```sql
ALTER TABLE ReportSessions      DROP COLUMN FailureReason;
ALTER TABLE ReportSessions      DROP COLUMN RunAttempt;
ALTER TABLE ReportSessions      DROP COLUMN RunId;
ALTER TABLE MovieHistoryAudit   DROP COLUMN RunAttempt;
ALTER TABLE MovieHistoryAudit   DROP COLUMN RunId;
ALTER TABLE TorrentHistoryAudit DROP COLUMN RunAttempt;
ALTER TABLE TorrentHistoryAudit DROP COLUMN RunId;
DROP INDEX IF EXISTS idx_report_sessions_run;
DROP INDEX IF EXISTS idx_mh_audit_run;
DROP INDEX IF EXISTS idx_th_audit_run;
```

You will also need to revert the application-layer change in
`packages/python/javdb_platform/db.py` `db_create_report_session` so it
reverts to relying on `cur.lastrowid` again — but that brings the dual
mode back into the original drift hazard, so do this only if you are
also moving away from `STORAGE_BACKEND=dual`.
