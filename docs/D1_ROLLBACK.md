# D1 Workflow Rollback (X3 hybrid strategy)

This document is the operator's reference for rolling back partial Cloudflare D1 (and SQLite) writes after a pipeline run fails midway. It covers:

- The X3 hybrid strategy and what each table looks like after the migration.
- The automatic `cleanup-on-failure` job in `DailyIngestion.yml` / `AdHocIngestion.yml`.
- The manual `RollbackD1.yml` workflow.
- A "Re-run failed jobs" safety matrix telling you when GitHub's native retry button is safe to press without first running a rollback.
- Direct CLI usage and the audit-table forensics workflow.

> Every workflow run that performs D1 writes is now logically tied to a single `ReportSessions.Id` — the **session_id** — *and* a `(RunId, RunAttempt)` pair derived from `GITHUB_RUN_ID` / `GITHUB_RUN_ATTEMPT`. Rollback can be addressed by either; the run identity is the primary lookup path because it remains valid even if a prior failed rollback deleted the owning `ReportSessions` row.

## TL;DR

- **Failed run?** Don't touch anything. The `cleanup-on-failure` job runs automatically on `DailyIngestion` / `AdHocIngestion` and undoes uncommitted D1 writes for that run.
- **Need to manually clean up?** Run the `Rollback D1 Session` workflow with `dry_run=true` to preview, then re-run with `dry_run=false`.
- **Lost the session_id?** Pass `run_id` + `attempt` (the failed run's GitHub identity) — the rollback CLI's primary lookup path will find every session that workflow run touched, even if the `ReportSessions` row was already deleted. `run_started_at` is still accepted as a fallback time-window scan, but only when `--include-orphaned` is set (the legacy unconditional sweep is now opt-in to avoid clobbering sibling sessions).
- **Cross-day reject:** the CLI refuses any candidate session whose `DateTimeCreated` predates `--run-started-at` by more than one hour. This prevents the 2026-05-08 incident class where a stale `--session-id` accidentally pointed at a session from a prior day. Pass `--force` to override.
- **Successful runs are protected.** Any session marked `Status='committed'` is refused for rollback unless `force=true` is set. Committed sessions also have their `MovieHistoryAudit` / `TorrentHistoryAudit` rows pruned automatically (no rollback needed → no audit needed).
- **Stale-session cron:** [`StaleSessionCleanup.yml`](../.github/workflows/StaleSessionCleanup.yml) runs daily at 02:00 UTC and unwinds any session stuck `in_progress` for more than 48h, tagging them `FailureReason='stale_timeout'`.

---

## Strategy summary (X3 hybrid)

The plan in `.cursor/plans/d1_workflow_rollback_plan_*.plan.md` (kept for reference) describes the design rationale. Each table is rolled back the way that's cheapest for it:

| Table family | Rollback technique | Schema additions |
|---|---|---|
| `ReportMovies`, `ReportTorrents`, `ReportSessions`, `SpiderStats`, `UploaderStats`, `PikpakStats` | Cascade-delete by `SessionId`; refuse to delete `ReportSessions` rows whose `Status='committed'` | `ReportSessions.Status TEXT DEFAULT 'in_progress'` |
| `MovieHistory`, `TorrentHistory` | Replay `*_Audit` tables in reverse to undo each `INSERT` / `UPDATE` / `DELETE`; skip rows whose current `SessionId` no longer matches (drift) | `SessionId INTEGER` on each table; new `MovieHistoryAudit` and `TorrentHistoryAudit` tables |
| `PikpakHistory`, `DedupRecords`, `InventoryAlignNoExactMatch` | Delete session-scoped rows. `DedupRecords` soft-delete/orphan updates first snapshot their pre-image into `DedupRecordsRollback_<session_id>`, so rollback restores pre-existing rows and deletes rows created by the failed session | `SessionId INTEGER` on each table; per-session `DedupRecordsRollback_<session_id>` backup table |
| `RcloneInventory` | Per-session staging table → atomic D1 batch swap. A failed scan drops staging; the live table never sees a half-written scan | `RcloneInventoryStaging_<session_id>` (created/dropped per run) |

### Why audit tables for history?

`MovieHistory` and `TorrentHistory` are upserted (a row may be touched many times across many runs). Plain `DELETE WHERE SessionId=...` is wrong — it would erase rows another run is correctly maintaining.

The audit tables capture, *before* every write:

- `Action` — `INSERT`, `UPDATE`, or `DELETE`
- `OldRowJson` — full JSON snapshot of the previous row state (for `UPDATE` / `DELETE`)
- `SessionId` — the run that performed the change
- `RunId` / `RunAttempt` (added 2026-05-08) — the GitHub Actions workflow run that owns the audit row, so rollback can address by run identity even if the `ReportSessions` row is missing.

Replaying these in reverse `Id` order (highest first) cleanly unwinds every change made by a single session, while leaving rows that were last modified by other sessions alone (logged as `drift_skipped`).

### SessionId generation (2026-05-08+)

`ReportSessions.Id` is **no longer** allocated by the per-backend AUTOINCREMENT counter. The application generates the id itself via `_generate_session_id()` in [`packages/python/javdb_platform/db.py`](../packages/python/javdb_platform/db.py):

```python
candidate = (time.time_ns() // 1_000_000) << 10  # 41-bit ms timestamp + 10-bit slot
```

The same id is INSERTed explicitly on both backends. Why:

- Under `STORAGE_BACKEND=dual`, SQLite and D1 each maintain their own AUTOINCREMENT counter; any past asymmetric INSERT (one side committed, the other failed) leaves them permanently out of sync.
- `DualCursor.lastrowid` returns whichever backend the cursor wraps. Trusting it as `SessionId` for downstream tables is what caused the 2026-05-08 incident: the SQLite-side allocated `Id=332`, but on D1 `Id=332` was a stale row from a 2026-05-07 workflow, and the spider tagged its history writes with `SessionId=332`. The rollback CLI later saw 145 audit rows spanning 35 hours and refused to roll most of them back as drift.
- See [`migration/d1/2026_05_08_sessionid_decouple.md`](../migration/d1/2026_05_08_sessionid_decouple.md) for the migration writeup.

A guard in [`packages/python/javdb_platform/dual_connection.py`](../packages/python/javdb_platform/dual_connection.py) (`DualCursor.for_write`) raises `DualWriteIdMismatchError` if any future code path attempts to INSERT into a guarded table (`APPLICATION_GENERATED_ID_TABLES`) without supplying an explicit Id and the two backends disagree on `lastrowid`.

### Rollback CLI lookup precedence

The CLI ([`apps/cli/rollback.py`](../apps/cli/rollback.py)) walks three sources in order, unioning the results:

1. **`--session-id`** (most specific). Targets that one session and **does not expand** into a window scan unless `--include-orphaned` is set.
2. **`--run-id` + `--attempt`** (primary path for run-aware lookups). Calls `db_find_sessions_by_run` which queries both `ReportSessions` and the audit tables (so a run whose `ReportSessions` row was already deleted by a previous failed rollback is still recoverable).
3. **`--run-started-at` window scan** (legacy fallback). Only consulted when `--include-orphaned` is set OR when no other source yielded any session id (the auto-cleanup job needs this so a run that died before printing its session id can still be cleaned by date window).

Cross-day sanity filter: every candidate session's `DateTimeCreated` is checked against `--run-started-at`. Sessions older than `run_started_at - 1h` are refused (`exit code 2`) unless `--force` is set.

### Audit retention on commit

Once `db_mark_session_committed` flips a session to `Status='committed'`, the rollback CLI refuses to roll it back (without `--force`). The `MovieHistoryAudit` / `TorrentHistoryAudit` rows for that session are no longer needed and would only bloat the tables, so the same call eagerly `DELETE`s them (no-op if the session is already committed). A partial UNIQUE index `uq_reportsessions_runidentity_csv` on `ReportSessions(RunId, RunAttempt, CsvFilename) WHERE Status='in_progress' AND RunId IS NOT NULL` enforces the real invariant at the DB layer: **no two in-progress sessions can share the same CSV in the same workflow run**. Any path that tries to double-INSERT (re-entry, dual-write `lastrowid` drift, manual SQL) fails with `sqlite3.IntegrityError`. Resolved (committed/failed) sessions are intentionally excluded so the same CSV can be re-ingested in a future attempt; legacy rows where `RunId IS NULL` are also excluded for backwards compatibility. The application-layer helper `db_find_in_progress_session_ids_for_run_csv` is now defence-in-depth — it surfaces a structured error message before the INSERT, and it covers the local-dev `RunId IS NULL` case the index intentionally skips. Sibling sessions in the same `(RunId, RunAttempt)` with **different CSV filenames** are fully legitimate (DailyIngestion runs the TodayTitle spider and an AdHoc URL spider in sequence); `cleanup-on-failure` rolls all siblings back together via `--run-id`.

---

## Session lifecycle

```text
db_create_report_session()       →  Status='in_progress'  (every D1 write tagged)
              │
              ▼
       (workflow runs)
              │
       ┌──────┴──────┐
       │             │
   success        failure
       │             │
       ▼             ▼
 db_mark_session_   db_rollback_session()
 committed()        ├─ Status='failed' (non-committed)
       │            ├─ DELETE … WHERE SessionId=?
       ▼            ├─ replay *_Audit in reverse
  Status='committed' └─ DROP staging table
```

- `Status='in_progress'` rows are the **only** ones cleanup-on-failure / RollbackD1 will touch.
- `Status='committed'` rows are immutable (`db_rollback_session` raises `ValueError` unless `force=True`).
- `Status='failed'` is a debug breadcrumb — for non-committed sessions, it's set by `db_rollback_session` *before* the deletes so a partially-failed rollback leaves the row in a recognisable state for follow-up.

---

## Automatic cleanup-on-failure

Each ingestion workflow now has a job:

```yaml
cleanup-on-failure:
  needs: [setup, run-pipeline]
  if: ${{ needs.run-pipeline.result == 'failure' || needs.run-pipeline.result == 'cancelled' }}
  steps:
    - name: Roll back uncommitted D1 writes
      run: |
        python3 -m apps.cli.rollback \
          --run-id "${{ github.run_id }}" \
          --attempt "${{ github.run_attempt }}" \
          --run-started-at "${{ needs.setup.outputs.pipeline_workflow_run_started_at }}" \
          --scope all \
          --apply \
          --session-id "${{ needs.run-pipeline.outputs.session_id }}"   # if known
```

What it does:

1. Looks up every `ReportSessions` row with `Status='in_progress'` and `DateTimeCreated >= run_started_at` when `--run-started-at` is supplied. If `--session-id` is supplied by itself, only that explicit session is targeted; if both are supplied, the explicit session is unioned with the window lookup.
2. For each session, runs the X3 rollback orchestration (reports → operations → history).
3. Marks each session `Status='failed'` for traceability.
4. Uploads `logs/rollback.log` (artifact: `rollback-log`, retention: 14 days).

It's a no-op if the spider failed before `db_create_report_session` returned an id.

> **Safety guarantee:** the cleanup job uploads a separate `rollback-log` artifact and never touches `Status='committed'` sessions, so a parallel run that just succeeded is never disturbed and operators can reliably locate rollback evidence.

The companion **Mark sessions as committed** step runs at the end of `run-pipeline`'s success path (`if: ${{ success() }}`), after `spider`, `qb_uploader`, `qb_file_filter`, `pikpak_bridge`, and `dedup` have had their turn. The optional `qb_file_filter` / `dedup` steps keep `continue-on-error: true`, so their transient failures do not prevent the session from being protected once the required D1-writing steps have succeeded.

---

## Manual rollback workflow (`RollbackD1.yml`)

For incident response, ad-hoc cleanup, or rolling back a specific session you know about, dispatch the **Rollback D1 Session** workflow from the Actions tab.

**Inputs:**

| Input | Default | Notes |
|---|---|---|
| `session_id` | (blank) | Pass `ReportSessions.Id` to include a specific run. |
| `run_id`, `attempt` | (blank) | For audit/log only. |
| `run_started_at` | (blank) | ISO timestamp lower bound; discovers all in-progress sessions in that window and unions them with `session_id` when both are supplied. When omitted, `session_id` alone stays targeted to that session. |
| `scope` | `all` | One of `all`, `reports`, `operations`, `history`. |
| `dry_run` | `true` | **Always preview first.** |
| `force` | `false` | Set only when you really need to roll back a `Status='committed'` session. Logs a `::warning::`. |
| `log_level` | `INFO` | `DEBUG` is useful when debugging audit replay. |
| `runner` | `self-hosted` | `ubuntu-latest` for CF-hosted SQLite-only runs. |

**Standard SOP:**

```text
1. Open Actions → Rollback D1 Session → Run workflow.
2. Fill in either:
   - session_id  (preferred — exact target), or
   - run_started_at  (e.g. 2026-05-04T19:30:00Z; everything in_progress
     after this point is rolled back).
3. Leave dry_run=true on the first run. Inspect the JSON summary in the
   "Run rollback" step + the rollback-log artifact.
4. If the diff matches expectations, dispatch again with dry_run=false.
5. Confirm via Actions logs that drift_total=0 (no concurrent-run drift).
   If drift_total>0, see "Drift handling" below.
```

The workflow's **concurrency group** is `rollback-d1`, so two operators can't accidentally run rollbacks in parallel.

---

## "Re-run failed jobs" safety matrix

GitHub's native **Re-run failed jobs** button is convenient, but only safe for steps that are idempotent or which run *after* a successful cleanup. Use this matrix before clicking it:

| Pipeline step | Safe to re-run directly? | Why |
|---|---|---|
| `setup` | ✅ Yes | Pure config bootstrap; no DB writes. |
| `run-pipeline` → Step 1 (spider) | ⚠️ Only after cleanup | Spider writes `MovieHistory` / `TorrentHistory` (audit-tracked) and `ReportSessions/Movies/Torrents`. Re-running without rollback creates a duplicate session and double-writes. |
| `run-pipeline` → Step 2 (qb_uploader) | ⚠️ Only after cleanup | Adds torrents to qBittorrent (external side-effect) and writes `UploaderStats` keyed by `SessionId`. Without cleanup you'll re-upload already-added torrents and duplicate stats rows. |
| `run-pipeline` → Step 2.5 (qb_file_filter) | ✅ Yes | `continue-on-error: true`, idempotent qB pause/delete operations. |
| `run-pipeline` → Step 3 (pikpak_bridge) | ⚠️ Only after cleanup | Calls PikPak API (external side-effect) and appends `PikpakHistory` / `PikpakStats`. Re-running without rollback re-uploads torrents that were already PikPak'd. |
| `run-pipeline` → Step 4 (rclone_dedup) | ✅ Mostly | `continue-on-error: true`; rclone purge is idempotent on already-deleted paths. Rollback now restores pre-existing `DedupRecords` rows that were soft-deleted by the failed session and deletes newly-created rows. |
| `Mark sessions as committed` | ✅ Yes | Idempotent UPDATE; second run is a no-op. |
| `cleanup-on-failure` | ✅ Yes | Re-running rollback on already-rolled-back data is idempotent (audit rows already consumed). |
| `email-notification` / `commit-results` | ✅ Yes | No DB writes. |

**Rule of thumb:** if Re-run failed jobs would re-execute Step 1, 2, or 3, run **Rollback D1 Session** first (or wait for the automatic `cleanup-on-failure` job), *then* re-run.

---

## Direct CLI usage

When operating from a developer machine or a terminal session on the runner:

```bash
# Dry-run preview (no DB writes):
python3 -m apps.cli.rollback --session-id 123

# Apply the rollback:
python3 -m apps.cli.rollback --session-id 123 --apply

# Roll back by GitHub run identity (preferred — survives a deleted ReportSessions row):
python3 -m apps.cli.rollback --run-id 12345 --attempt 1 --apply

# Legacy time-window scan (now opt-in via --include-orphaned):
python3 -m apps.cli.rollback --run-started-at 2026-05-04T00:00:00Z --include-orphaned --apply

# Partial scope (only history audit replay):
python3 -m apps.cli.rollback --session-id 123 --scope history --apply

# Force rollback of a committed session (DANGEROUS):
python3 -m apps.cli.rollback --session-id 123 --force --apply

# Override cross-day reject for a deliberately ancient session:
python3 -m apps.cli.rollback --session-id 123 --run-started-at 2026-05-04T00:00:00Z --force --apply
```

**Exit codes:**

- `0` — success / dry-run completed cleanly
- `2` — refused: session has `Status='committed'` and `--force` was not passed, OR a candidate session predates `--run-started-at` by more than 1 hour (cross-day reject — pass `--force` to override)
- `3` — could not connect to D1 / SQLite
- `4` — partial failure or rollback drift; inspect the JSON summary and logs

The CLI prints a JSON summary at the end with per-table counts; pipe it to `jq` for inspection. Drift / orphan_pruned counters are also appended to `reports/D1/d1_drift.jsonl` and (under GitHub Actions) `$GITHUB_OUTPUT` so downstream steps and email notifications can react.

### Incident-response tooling (one-shot scripts)

For the rare situation where the audit / history tables are corrupted (e.g. the 2026-05-08 SessionId-collision incident left ~1 k phantom audit rows on D1):

```bash
# 1. Pull every business table from D1 down into local sqlite (default dry-run):
python3 -m scripts.sync_d1_to_sqlite                # report what would change
python3 -m scripts.sync_d1_to_sqlite --apply        # actually overwrite reports/*.db

# 2. Detect & delete phantom audit rows on either / both sides:
python3 -m scripts.cleanup_stale_session_audits                  # dry-run on both sides
python3 -m scripts.cleanup_stale_session_audits --apply          # commit deletions
python3 -m scripts.cleanup_stale_session_audits --target d1 --apply
```

Both scripts default to dry-run and write a JSON report under `reports/`. Use `--target` to restrict to one side; `--session-ids 332,346` to restrict to specific ids; `--cross-day-hours 12` to tune the cross-day phantom threshold for `cleanup_stale_session_audits`.

Neither script is meant to be wired into a cron — they are manual incident-response tools. The recurring stale-cleanup job lives in [`StaleSessionCleanup.yml`](../.github/workflows/StaleSessionCleanup.yml) and uses [`apps.cli.cleanup_stale_in_progress`](../apps/cli/cleanup_stale_in_progress.py).

### Marking a session committed manually

If a session legitimately succeeded but the workflow died in a non-DB-writing post-step (e.g. email step), you can flip `Status` manually:

```bash
python3 -m apps.cli.commit_session --session-id 123
```

`commit_session` is idempotent and ignores already-committed rows.

---

## Audit table forensics

The `MovieHistoryAudit` and `TorrentHistoryAudit` tables are scratch storage — `db_rollback_session` deletes their rows after a successful replay. Until then, they're a useful diagnostic trail.

```sql
-- What did session 123 do to MovieHistory?
SELECT Id, TargetId, Action, DateTimeCreated, OldRowJson
FROM MovieHistoryAudit
WHERE SessionId = 123
ORDER BY Id;

-- All sessions still pending rollback:
SELECT Id, ReportType, ReportDate, DateTimeCreated, Status
FROM ReportSessions
WHERE Status = 'in_progress'
ORDER BY DateTimeCreated;

-- Per-session write volume (audit-row count):
SELECT SessionId, COUNT(*) AS movie_changes
FROM MovieHistoryAudit
GROUP BY SessionId
ORDER BY movie_changes DESC;
```

`OldRowJson` is the full pre-write row state as JSON (column → value). For `Action='INSERT'` it's `NULL` (nothing existed before). For `UPDATE` and `DELETE` it's the pre-image used during rollback restore.

---

## Drift handling

A "drift" event is logged whenever rollback can't safely undo a change because another run has since touched the same row. The most common causes:

- A concurrent ingestion run upserted the same `MovieHistory.Href` after the failed run did.
- A manual SQL fix changed the row's `SessionId` after the audit row was written.
- The audit row references a row that was already deleted by a different rollback.

When `drift_total > 0`:

1. Read the warning lines from the rollback log — they include the table name and the conflicting row's `Id`.
2. Decide whether the concurrent run's data is more recent (usually yes — leave the drift in place).
3. Optionally re-run `apps.cli.rollback --scope history --session-id <id> --apply` later if you decide the concurrent run was also wrong.

The CLI exits with code `4` to surface partial failures so an operator notices.

---

## Schema migration

If you upgraded from a pre-X3 build, run the bundled migrations to add the new columns and audit tables:

```bash
# Local SQLite — migration is automatic on db init (forward-compat ALTERs).
python3 -m apps.cli.migration --backup

# Cloudflare D1 — apply the SQL bundle:
wrangler d1 execute history    --file=migration/d1/2026_05_04_add_rollback_columns_history.sql
wrangler d1 execute reports    --file=migration/d1/2026_05_04_add_rollback_columns_reports.sql
wrangler d1 execute operations --file=migration/d1/2026_05_04_add_rollback_columns_operations.sql

# 2026-05-08 follow-up: add (RunId, RunAttempt, FailureReason) columns
# so rollback can address sessions by GitHub run identity. See
# migration/d1/2026_05_08_sessionid_decouple.md for the rationale.
wrangler d1 execute reports --file=migration/d1/2026_05_08_add_run_identity_columns_reports.sql
wrangler d1 execute history --file=migration/d1/2026_05_08_add_run_identity_columns_history.sql
```

After migration, `db.SCHEMA_VERSION == 11`. The `_ensure_rollback_columns` helper inside `init_db` adds the columns on subsequent boots if a partial migration occurred.

---

## Validation playbook (dev branch)

Before merging to `main`, exercise the cleanup paths once on `dev`:

1. **Push the rollback wiring to `dev`.** Confirm the new `cleanup-on-failure` job, `Mark sessions as committed` step, and `RollbackD1.yml` are present on the dev branch.
2. **Smoke the success path.** Dispatch `Daily Ingestion Pipeline` (or `AdHoc Ingestion Pipeline`) on `dev`. After it succeeds:
   - Open the run's logs and confirm the **Mark sessions as committed** step ran and reports `1 row updated`.
   - Query `ReportSessions` (D1 dashboard or `wrangler d1 execute reports --command "SELECT Id, ReportType, Status FROM ReportSessions ORDER BY Id DESC LIMIT 5"`) — the new session should be `Status='committed'`.
   - Confirm the `cleanup-on-failure` job did **not** run (it's gated on `failure()`).
3. **Smoke the failure path.** Force a controlled spider failure on `dev`:
   - Either temporarily inject `exit 1` after `Step 1 - Run Spider` in your dev branch's workflow file, *or* set `--max-movies-phase1 99999` with a short `--end-page 1` to provoke proxy exhaustion.
   - Re-dispatch the workflow.
   - When `run-pipeline` fails, watch the **Cleanup on failure** job spin up.
   - Open the **rollback-log** artifact: confirm at least one session was discovered, the JSON summary lists per-table counts, and `drift_total=0`.
   - Re-query `ReportSessions` — the failed run's row should now be `Status='failed'` with no companion rows in `ReportMovies` / `ReportTorrents` / `MovieHistory*Audit`.
4. **Smoke the manual workflow.** From the Actions tab dispatch `Rollback D1 Session`:
   - First with `dry_run=true` and `session_id=<the just-rolled-back id>` — confirm zero counts (already cleaned).
   - Then with `dry_run=true` and an obviously-bad `session_id` (e.g. `999999`) — confirm exit code `0` with empty session list.
   - Finally with `dry_run=true` + `force=true` against a `committed` session id — confirm the workflow proceeds (force is honoured) but counts show only the `ReportSessions` row stays intact (because `_rollback_reports` keeps committed rows by design).
5. **Revert the injected failure** before promoting to `main`.

If anything in the validation deviates from the above, do **not** merge — capture the rollback log and open an issue.

---

## File pointers

- CLI: [`apps/cli/rollback.py`](../apps/cli/rollback.py), [`apps/cli/commit_session.py`](../apps/cli/commit_session.py)
- Core helpers: [`packages/python/javdb_platform/db.py`](../packages/python/javdb_platform/db.py) (`db_rollback_session`, `db_mark_session_committed`, `db_find_in_progress_sessions`, `_audit_record_movie_change`, `_rollback_history`, etc.)
- Workflows: [`.github/workflows/DailyIngestion.yml`](../.github/workflows/DailyIngestion.yml), [`.github/workflows/AdHocIngestion.yml`](../.github/workflows/AdHocIngestion.yml), [`.github/workflows/RollbackD1.yml`](../.github/workflows/RollbackD1.yml)
- Migrations: [`migration/d1/2026_05_04_add_rollback_columns_*.sql`](../migration/d1/)
- Tests: [`tests/unit/test_rollback.py`](../tests/unit/test_rollback.py)
