# D1 Workflow Rollback (X3 hybrid strategy)

This document is the operator's reference for rolling back partial Cloudflare D1 (and SQLite) writes after a pipeline run fails midway. It covers:

- The X3 hybrid strategy and what each table looks like after the migration.
- The automatic `cleanup-on-failure` job in `DailyIngestion.yml` / `AdHocIngestion.yml`.
- The manual `RollbackD1.yml` workflow.
- A "Re-run failed jobs" safety matrix telling you when GitHub's native retry button is safe to press without first running a rollback.
- Direct CLI usage and the audit-table forensics workflow.

> Every workflow run that performs D1 writes is now logically tied to a single `ReportSessions.Id` — the **session_id**. Rollback always operates per-session.

## TL;DR

- **Failed run?** Don't touch anything. The `cleanup-on-failure` job runs automatically on `DailyIngestion` / `AdHocIngestion` and undoes uncommitted D1 writes for that run.
- **Need to manually clean up?** Run the `Rollback D1 Session` workflow with `dry_run=true` to preview, then re-run with `dry_run=false`.
- **Lost the session_id?** Pass `run_started_at` (the failed run's start ISO timestamp) and the rollback CLI will discover every `Status='in_progress'` session created in that window.
- **Successful runs are protected.** Any session marked `Status='committed'` is refused for rollback unless `force=true` is set.

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

Replaying these in reverse `Id` order (highest first) cleanly unwinds every change made by a single session, while leaving rows that were last modified by other sessions alone (logged as `drift_skipped`).

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
 committed()        ├─ Status='failed'
       │            ├─ DELETE … WHERE SessionId=?
       ▼            ├─ replay *_Audit in reverse
  Status='committed' └─ DROP staging table
```

- `Status='in_progress'` rows are the **only** ones cleanup-on-failure / RollbackD1 will touch.
- `Status='committed'` rows are immutable (`db_rollback_session` raises `ValueError` unless `force=True`).
- `Status='failed'` is a debug breadcrumb — it's set by `db_rollback_session` *before* the deletes so a partially-failed rollback leaves the row in a recognisable state for follow-up.

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
          --session-id "${{ needs.run-pipeline.outputs.session_id }}"   # if known
```

What it does:

1. Looks up every `ReportSessions` row with `Status='in_progress'` and `DateTimeCreated >= run_started_at`. If `--session-id` is supplied, that's used directly.
2. For each session, runs the X3 rollback orchestration (reports → operations → history).
3. Marks each session `Status='failed'` for traceability.
4. Uploads `logs/rollback.log` (artifact: `rollback-log`, retention: 14 days).

It's a no-op if the spider failed before `db_create_report_session` returned an id.

> **Safety guarantee:** the cleanup job has a separate `cleanup-on-failure` artifact and never touches `Status='committed'` sessions, so a parallel run that just succeeded is never disturbed.

The companion **Mark sessions as committed** step runs at the very end of `run-pipeline`'s success path (`if: ${{ success() }}`), flipping `Status` from `in_progress` to `committed`. This must execute *after* every D1-writing step (`spider`, `qb_uploader`, `pikpak_bridge`) but *before* the optional `qb_file_filter` and `dedup` continue-on-error steps so transient post-failures of those steps don't delay protection of the canonical writes.

---

## Manual rollback workflow (`RollbackD1.yml`)

For incident response, ad-hoc cleanup, or rolling back a specific session you know about, dispatch the **Rollback D1 Session** workflow from the Actions tab.

**Inputs:**

| Input | Default | Notes |
|---|---|---|
| `session_id` | (blank) | Highest priority. Pass `ReportSessions.Id` to target one specific run. |
| `run_id`, `attempt` | (blank) | For audit/log only. |
| `run_started_at` | (blank) | ISO timestamp lower bound; used when `session_id` is blank to discover all in-progress sessions in that window. |
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
python3 -m apps.cli.rollback --session-id 123 --dry-run

# Apply the rollback:
python3 -m apps.cli.rollback --session-id 123

# Roll back every in-progress session created today:
python3 -m apps.cli.rollback --run-started-at 2026-05-04T00:00:00Z

# Partial scope (only history audit replay):
python3 -m apps.cli.rollback --session-id 123 --scope history

# Force rollback of a committed session (DANGEROUS):
python3 -m apps.cli.rollback --session-id 123 --force
```

**Exit codes:**

- `0` — success / dry-run completed cleanly
- `2` — refused: session has `Status='committed'` and `--force` was not passed
- `3` — could not connect to D1 / SQLite
- `4` — partial failure (one or more sessions left `Status='failed'` with `drift_skipped > 0`)

The CLI prints a JSON summary at the end with per-table counts; pipe it to `jq` for inspection.

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
3. Optionally re-run `apps.cli.rollback --scope history --session-id <id>` later if you decide the concurrent run was also wrong.

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
