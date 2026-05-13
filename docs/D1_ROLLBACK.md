# D1 Workflow Rollback (Pending mode + legacy X3 audit fallback)

This document is the operator's reference for rolling back partial Cloudflare D1 (and SQLite) writes after a pipeline run fails midway. It covers:

- The Phase 3 **pending** write path (default since 2026-05) and the legacy X3 audit fallback (sunsetting per Appendix A).
- What each table looks like after the migration.
- The automatic `cleanup-on-failure` job in `DailyIngestion.yml` / `AdHocIngestion.yml`.
- The manual `RollbackD1.yml` workflow.
- A "Re-run failed jobs" safety matrix telling you when GitHub's native retry button is safe to press without first running a rollback.
- Direct CLI usage and the audit-table forensics workflow.
- Phase 3 alerting + auto-fallback (`pending_session_verify`, Health Snapshot, `pending_mode_disabled_until`).
- The 6-step pre-promotion validation playbook.
- **Appendix A (Phase 4, 2026-05-13)** — legacy audit deprecation timeline, sunset date, and the new `audit_archive` cron.

> Phase 4 deprecation (2026-05-13): the `WriteMode='audit'` write path is now **deprecated** for new sessions. `db_upsert_history` emits `DeprecationWarning` on every call; the legacy `MovieHistoryAudit` / `TorrentHistoryAudit` tables are **read-only forensics** for historical sessions only. New `INSERT`s are auto-staged through `db_stage_history_write` via `save_parsed_movie_to_history`; the audit fallback remains available as the emergency escape valve until **2026-08-13** (90-day sunset window) so any unfinished migration work has a deterministic exit deadline. See [Appendix A](#appendix-a-legacy-audit-fallback-sunset-2026-08) for the full timeline, the `JAVDB_AUDIT_WRITES_DISABLED` kill switch, and the new `audit_archive` cron.

> Ingestion Perfect Rollback (Phase 3, 2026-05) — `MovieHistory` / `TorrentHistory` are now only **ever** mutated at commit time. Spider / detail / qb_uploader / pikpak_bridge stage every write into `PendingMovieHistoryWrites` / `PendingTorrentHistoryWrites`; a successful run drains those rows into the live tables in one pass; a failure deletes the staged rows instead of replaying them. The legacy audit path is preserved as a fallback (`WriteMode='audit'`) but is no longer the default.

> Every workflow run that performs D1 writes is now logically tied to a single `ReportSessions.Id` — the **session_id** — *and* a `(RunId, RunAttempt)` pair derived from `GITHUB_RUN_ID` / `GITHUB_RUN_ATTEMPT`. Rollback can be addressed by either; the run identity is the primary lookup path because it remains valid even if a prior failed rollback deleted the owning `ReportSessions` row.

## TL;DR

- **Failed run?** Don't touch anything. The `cleanup-on-failure` job runs automatically on `DailyIngestion` / `AdHocIngestion` and undoes uncommitted D1 writes for that run.
- **Need to manually clean up?** Run the `Rollback D1 Session` workflow with `dry_run=true` to preview, then re-run with `dry_run=false`.
- **Lost the session_id?** Pass `run_id` + `attempt` (the failed run's GitHub identity) — the rollback CLI's primary lookup path will find every session that workflow run touched, even if the `ReportSessions` row was already deleted. `run_started_at` is still accepted as a fallback time-window scan, but only when `--include-orphaned` is set (the legacy unconditional sweep is now opt-in to avoid clobbering sibling sessions).
- **Cross-day reject:** the CLI refuses any candidate session whose `DateTimeCreated` predates `--run-started-at` by more than one hour. This prevents the 2026-05-08 incident class where a stale `--session-id` accidentally pointed at a session from a prior day. Pass `--force` to override.
- **Successful runs are protected.** Any session marked `Status='committed'` is refused for rollback unless `force=true` is set. Committed sessions also have their `MovieHistoryAudit` / `TorrentHistoryAudit` rows pruned automatically (no rollback needed → no audit needed).
- **Stale-session cron:** [`StaleSessionCleanup.yml`](../.github/workflows/StaleSessionCleanup.yml) runs daily at 02:00 UTC and unwinds any session stuck `in_progress` for more than 48h, tagging them `FailureReason='stale_timeout'`. The same job now also calls `apps.cli.sweep_movie_claim_stages` to clean up Phase-1 orphaned `staged_complete{}` entries on the MovieClaim Durable Object (cutoff 48h, server-floored to ≥ 1h).
- **MovieClaim cross-session rollback safety (Phase 1):** detail-page completions are now staged per-session on the MovieClaim DO before they enter the permanent `completed_committed[]` list. `apps.cli.commit_session` promotes the stage on success; `apps.cli.rollback` calls `rollback_staged_movies` (with up to 3 retries) before completing the DB rollback. A failed peer session no longer blocks an ad-hoc retry on the same href in another session — only `completed_committed[]` does. See [`docs/PROXY_COORDINATOR_DEPLOY.md` §15.2](PROXY_COORDINATOR_DEPLOY.md) for the protocol and `JAVDB_AutoSpider.wiki/Cross-Runner-State.md` §2.3 for the runtime semantics.

---

## Strategy summary (Pending default + X3 audit fallback)

The original X3 audit hybrid plan in `.cursor/plans/d1_workflow_rollback_plan_*.plan.md` is preserved for reference; Phase 3 (`.cursor/plans/ingestion_perfect_rollback_2152bae2.plan.md`) layered the Pending write path on top — that path is now the **default** for `MovieHistory` / `TorrentHistory`.  Each table is rolled back the way that's cheapest for it:

| Table family | Rollback technique | Schema additions |
|---|---|---|
| `ReportMovies`, `ReportTorrents`, `ReportSessions`, `SpiderStats`, `UploaderStats`, `PikpakStats` | Cascade-delete by `SessionId`; refuse to delete `ReportSessions` rows whose `Status='committed'` | `ReportSessions.Status TEXT DEFAULT 'in_progress'`; Phase 3 added `WriteMode` and the `finalizing` value to `Status` |
| `MovieHistory`, `TorrentHistory` (Pending mode — Phase 3 default) | All writes stage into `PendingMovie/TorrentHistoryWrites` first; commit recomputes derived fields once and UPSERTs live in one pass; rollback `DELETE`s the staged rows for `Status='in_progress'` and `db_resume_finalizing_session` for `Status='finalizing'`. No audit replay needed. | `PendingMovieHistoryWrites` and `PendingTorrentHistoryWrites` tables (each with explicit application-generated snowflake `Seq`, `ApplyState`, `SessionId` / `RunId` / `RunAttempt`) |
| `MovieHistory`, `TorrentHistory` (Audit fallback — **deprecated since Phase 4, sunset 2026-08-13**) | Replay `*_Audit` tables in reverse to undo each `INSERT` / `UPDATE` / `DELETE`; skip rows whose current `SessionId` no longer matches (drift). Engaged by the `pending_mode_disabled_until` auto-fallback or workflow `write_mode_override`. Audit tables are read-only forensics — see [Appendix A](#appendix-a-legacy-audit-fallback-sunset-2026-08). | `SessionId INTEGER` on each live table; `MovieHistoryAudit` and `TorrentHistoryAudit` tables (read-only since 2026-05-13, archived weekly by `scripts/audit_archive.py`) |
| `PikpakHistory`, `DedupRecords`, `InventoryAlignNoExactMatch` | Delete session-scoped rows. `DedupRecords` soft-delete/orphan updates first snapshot their pre-image into `DedupRecordsRollback_<session_id>`, so rollback restores pre-existing rows and deletes rows created by the failed session | `SessionId INTEGER` on each table; per-session `DedupRecordsRollback_<session_id>` backup table |
| `RcloneInventory` | Per-session staging table → atomic D1 batch swap. A failed scan drops staging; the live table never sees a half-written scan | `RcloneInventoryStaging_<session_id>` (created/dropped per run) |

### Why audit tables for history? *(legacy — kept for context, see [Appendix A](#appendix-a-legacy-audit-fallback-sunset-2026-08))*

`MovieHistory` and `TorrentHistory` are upserted (a row may be touched many times across many runs). Plain `DELETE WHERE SessionId=...` is wrong — it would erase rows another run is correctly maintaining.

The audit tables capture, *before* every write:

- `Action` — `INSERT`, `UPDATE`, or `DELETE`
- `OldRowJson` — full JSON snapshot of the previous row state (for `UPDATE` / `DELETE`)
- `SessionId` — the run that performed the change
- `RunId` / `RunAttempt` (added 2026-05-08) — the GitHub Actions workflow run that owns the audit row, so rollback can address by run identity even if the `ReportSessions` row is missing.

Replaying these in reverse `Id` order (highest first) cleanly unwinds every change made by a single session, while leaving rows that were last modified by other sessions alone (logged as `drift_skipped`).

Phase 4 obsoletes this approach for new sessions: the Pending write path recomputes derived fields at commit time inside a single transaction, removing the need for a per-mutation audit trail. The audit tables remain queryable for historical-session forensics until [Appendix A's sunset date](#appendix-a-legacy-audit-fallback-sunset-2026-08).

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

Once `db_mark_session_committed` flips a session to `Status='committed'`, the rollback CLI refuses to roll it back (without `--force`). The `MovieHistoryAudit` / `TorrentHistoryAudit` rows for that session are no longer needed and would only bloat the tables, so the same call eagerly `DELETE`s them (no-op if the session is already committed). ### Smoke-test cleanup strategy

`TestIngestion.yml` runs the spider on every push/PR and **must** exercise the full dual-write path (otherwise it can't catch D1 / SQLite drift, schema-migration regressions, `DualWriteIdMismatchError` triggers, etc. before they reach the production DailyIngestion / AdHocIngestion runs). To prevent mock rows from accumulating in production, every TestIngestion run is paired with a guaranteed cleanup:

* **Spider runs in dual mode** (same `STORAGE_BACKEND` / `STORAGE_MODE` as production). It does **not** auto-commit — the spider entrypoint never calls `db_mark_session_committed`, so all sessions it creates stay `Status='in_progress'`.
* **`always()`-runs cleanup step** at the end of the job calls `apps.cli.rollback --run-id $GITHUB_RUN_ID --attempt $GITHUB_RUN_ATTEMPT --scope all --apply`. The rollback CLI uses the `(RunId, RunAttempt)` union query to find every sibling session created by this run (TestIngestion runs both Daily and AdHoc spiders, each with a distinct CSV → distinct session, both rolled back together) and walks `MovieHistoryAudit` / `TorrentHistoryAudit` in reverse to undo INSERTs, UPDATEs and DELETEs.
* **Verification steps** run after the rollback (also `always()`) and **fail the workflow** if any row tagged to this `(RunId, RunAttempt)` remains in `ReportSessions` / `MovieHistoryAudit` / `TorrentHistoryAudit` — both in local SQLite and in the live D1 instance (queried via the Cloudflare REST API). A leftover row means the rollback machinery has a bug; surfacing it fast is the whole point of TestIngestion.

The `JAVDB_FORBID_DB_WRITES=1` kill switch in `config_helper.py` (`db_writes_forbidden()` → forces `storage_backend='sqlite'` / `storage_mode='csv'`, plus a `RuntimeError` guard inside `db_create_report_session`) remains available as opt-in infrastructure for any unit test or local script that genuinely needs zero-DB execution. **TestIngestion does not engage it** because doing so would skip the very D1 / dual-write code paths the smoke test is supposed to exercise.

### `(RunId, RunAttempt, CsvFilename)` invariant

A partial UNIQUE index `uq_reportsessions_runidentity_csv` on `ReportSessions(RunId, RunAttempt, CsvFilename) WHERE Status='in_progress' AND RunId IS NOT NULL` enforces the real invariant at the DB layer: **no two in-progress sessions can share the same CSV in the same workflow run**. Any path that tries to double-INSERT (re-entry, dual-write `lastrowid` drift, manual SQL) fails with `sqlite3.IntegrityError`. Resolved (committed/failed) sessions are intentionally excluded so the same CSV can be re-ingested in a future attempt; legacy rows where `RunId IS NULL` are also excluded for backwards compatibility. The application-layer helper `db_find_in_progress_session_ids_for_run_csv` is now defence-in-depth — it surfaces a structured error message before the INSERT, and it covers the local-dev `RunId IS NULL` case the index intentionally skips. Sibling sessions in the same `(RunId, RunAttempt)` with **different CSV filenames** are fully legitimate (DailyIngestion runs the TodayTitle spider and an AdHoc URL spider in sequence); `cleanup-on-failure` rolls all siblings back together via `--run-id`.

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

# 2. Detect phantom audit rows on either / both sides (read-only since Phase 4):
python3 -m scripts.cleanup_stale_session_audits             # dry-run, both sides
python3 -m scripts.cleanup_stale_session_audits --target d1 # dry-run, D1 only
# ``--apply`` is now a deprecated alias for ``--dry-run`` — the script
# logs a warning and behaves like a read-only inspection.  Operators
# wanting destructive cleanup use the archival cron (step 3 below).

# 3. Archive audit rows older than 30 days (committed / failed / orphan):
python3 -m scripts.audit_archive                    # dry-run, default 30-day window
python3 -m scripts.audit_archive --apply            # apply, both sides
python3 -m scripts.audit_archive --apply --target sqlite --older-than-days 60
```

All three scripts default to dry-run and write a JSON report under `reports/`. Use `--target` to restrict to one side; for `cleanup_stale_session_audits` use `--session-ids 332,346` to restrict to specific ids and `--cross-day-hours 12` to tune the phantom-detection threshold.

`sync_d1_to_sqlite` + `cleanup_stale_session_audits` are manual incident-response tools (don't wire into cron). `audit_archive` **is** a cron job and runs weekly via [`.github/workflows/AuditArchive.yml`](../.github/workflows/AuditArchive.yml). The recurring stale-session cleanup lives in [`StaleSessionCleanup.yml`](../.github/workflows/StaleSessionCleanup.yml) and uses [`apps.cli.cleanup_stale_in_progress`](../apps/cli/cleanup_stale_in_progress.py).

### Marking a session committed manually

If a session legitimately succeeded but the workflow died in a non-DB-writing post-step (e.g. email step), you can flip `Status` manually:

```bash
python3 -m apps.cli.commit_session --session-id 123
```

`commit_session` is idempotent and ignores already-committed rows.

---

## Audit table forensics *(read-only since Phase 4, 2026-05-13)*

> Phase 4 contract: the audit tables are **historical-session forensics only**. No new sessions append rows to them — the Pending write path is the default. Until the sunset date in [Appendix A](#appendix-a-legacy-audit-fallback-sunset-2026-08), rows for any committed/failed session linger long enough for operators to query them; the [`scripts/audit_archive.py`](../scripts/audit_archive.py) cron prunes anything > 30 days old every Monday.

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

## Pending mode (current default)

`ReportSessions.WriteMode` (added 2026-05-09) selects the dispatch path for cleanup-on-failure and the stale-session cron. The default has been **`pending`** since Phase 3 (DailyIngestion / AdHocIngestion / TestIngestion); the legacy audit path remains selectable via env var `JAVDB_HISTORY_WRITE_MODE=audit` or via the workflow_dispatch input `write_mode_override`. See [the architecture plan](../.cursor/plans/ingestion_perfect_rollback_2152bae2.plan.md) for the design rationale.

### Pending state machine

```
in_progress ─(db_begin_finalize)─▶ finalizing ─(db_finish_commit)─▶ committed
     │                                  │
     │                                  └─(idempotent resume)─▶ finalizing ─▶ committed
     │
     └─(rollback DELETE pending)─▶ failed
```

- `db_stage_history_write` writes to `PendingMovie/TorrentHistoryWrites` instead of `MovieHistory` / `TorrentHistory`.
- `db_load_history_snapshot` reads `committed live + this session's pending overlay` so the in-progress process always sees its own writes without polluting other concurrent sessions.
- `db_commit_session_history` walks every distinct `Href` in the session, locks per-`Href`, recomputes `PerfectMatchIndicator` / `HiResIndicator`, UPSERTs the live row, and finally `DELETE`s every applied pending row.
- `db_resume_finalizing_session` is the idempotent re-entry: a workflow that crashed mid-finalize is driven to `committed` rather than rolled back.

### Cleanup dispatch matrix (Phase 3)

| `WriteMode` | `Status` | Cleanup-on-failure action | Stale-session cron action |
|---|---|---|---|
| `audit` | `in_progress` | Replay `*Audit` tables in reverse (legacy X3) | Same (legacy) |
| `pending` | `in_progress` | `DELETE FROM PendingMovie/TorrentHistoryWrites WHERE SessionId=?`, no audit replay | Same |
| `pending` | `finalizing` | **`db_resume_finalizing_session`** drives session to `committed` (default `--auto-resume-finalizing`) | Same — never roll back |
| `audit` or `pending` | `committed` | Refused — re-runs / retries skip these | Skipped (only `in_progress`/`finalizing` candidate) |
| `audit` | `finalizing` | Unexpected (audit doesn't use finalizing); cron logs `audit_finalizing_unexpected` and refuses | Same |

### Pending mode metrics (`pending_session_verify`)

Both `apps.cli.commit_session` (every pending-mode commit) and `apps.cli.rollback` (every pending-mode rollback / resume) emit one `pending_session_verify` JSONL record to `reports/D1/d1_drift.jsonl` per session they handle. Fields:

- `session_id`, `run_id`, `run_attempt`, `write_mode`, `final_status`, `source` (`commit_session` or `rollback`).
- `pending_staged_count` (how many rows ever entered the pending tables for this session).
- `pending_applied_count` (how many converted to live).
- `pending_residual_count` (rows still `ApplyState='pending'` after the run — **must be 0**).
- `commit_attempts` (1 for first-pass; ≥ 2 if any resume_commit happened).
- `commit_duration_ms`, `hrefs_processed`, `movies_upserted`, `torrents_upserted`, `torrents_deleted`.
- `derived_recompute_drift` + `derived_drift_samples` (only populated when `JAVDB_PENDING_SHADOW_AUDIT=1` — Phase 2 toggle, kept gated in Phase 3 so the comparison can be ramped down once a clean week is on file).
- `worker_stage_rollback_failed`, `cleanup_path_mismatch_count`, `staged_claim_orphan_count`.

The same file also receives `stale_session_cleanup` and `rollback_summary` records; downstream consumers filter by `kind`.

### Email Pending Mode Verification + Health Snapshot

The email step ([`packages/python/javdb_integrations/email_notification.py`](../packages/python/javdb_integrations/email_notification.py)) now reads `reports/D1/d1_drift.jsonl`, restricts to `pending_session_verify` records owned by `$GITHUB_RUN_ID` / `$GITHUB_RUN_ATTEMPT`, and renders a **Pending Mode Verification** body block listing every pending session's counts. Any threshold violation flags the row inline (`[CRITICAL]` / `[ALERT]`) and prefixes the email subject:

- **Soft alert** (subject `[PENDING-ALERT] (...)`) — `commit_attempts > Phase3_max`, `worker_stage_rollback_failed > 0`, `staged_claim_orphan_count > 0`, `d1_request_count_audit_baseline_ratio > 1.8`, or `final_status='finalizing'`.
- **Critical alert** (subject `[PENDING-ROLLBACK-AUTO] (...)`) — `pending_residual_count > 0`, `derived_recompute_drift > 0`, or `cleanup_path_mismatch_count > 0`. Also engages the [auto-fallback](#auto-fallback-publish-configyml) below.

A **Health Snapshot** block follows the per-session table when [`scripts/aggregate_pending_health.py`](../scripts/aggregate_pending_health.py) has produced `reports/D1/pending_health_24h.json`. Both DailyIngestion and AdHocIngestion call this aggregator before `Run Email Notification` so the snapshot covers the trailing 24h of pending sessions, plus stale-cron resume successes / failures.

Phase 2 thresholds remain available via the env var `JAVDB_PENDING_ALERT_PHASE=2` — useful for the TestIngestion canary while it warms up.

### Auto-fallback (`.publish-config.yml`)

A **critical** pending alert in DailyIngestion / AdHocIngestion runs the `Auto-fallback on critical pending alert` step in the email job. It calls [`scripts/pending_mode_auto_fallback.py`](../scripts/pending_mode_auto_fallback.py) which writes (or extends):

```yaml
# Phase 3 auto-fallback marker — written by scripts/pending_mode_auto_fallback.py.
pending_mode_disabled_until: '2026-05-11T07:00:00+00:00'
pending_mode_disabled_reason: 'DailyIngestion run 12345: pending_residual_count=2 session=67890'
```

into `.publish-config.yml`, then commits + pushes the change so the **next** ingestion run's `Resolve effective WriteMode` step picks `audit` instead of the default `pending`. The window is 24h; an operator restores the pending path by either reverting the auto-commit or letting the timer expire.

### Operator recovery SOP

| Symptom | Look for | Fix |
|---|---|---|
| Email subject `[PENDING-ALERT]` only | `commit_attempts`, ratio, or finalizing flag in body | Inspect `reports/D1/d1_drift.jsonl`; usually transient (Worker lease timeout). No automatic action. |
| Email subject `[PENDING-ROLLBACK-AUTO]` | `pending_residual_count`, `derived_recompute_drift`, `cleanup_path_mismatch_count` | System auto-disabled pending for 24h. Investigate, fix, then either revert the auto-commit on `.publish-config.yml` or wait. |
| `final_status='finalizing'` two cron cycles in a row | StaleSessionCleanup unable to drive session to `committed` | `python3 -m apps.cli.commit_session --session-id <id> --shadow-audit --log-level DEBUG`; if 3 attempts still fail, `python3 -m apps.cli.rollback --session-id <id> --no-auto-resume-finalizing --apply` to mark `failed`. |
| `worker_stage_rollback_failed > 0` | Rollback CLI couldn't reach MovieClaim coordinator | Check coordinator health; orphan sweep cron will reconcile within 4h. |
| `pending_residual_count > 0` on a `committed` session | Half-applied commit, residual `ApplyState='pending'` rows | Live tables are already correct (the `committed` flip is the source of truth); the residual rows just need clearing. Safe options, in order of preference: (1) manual `DELETE FROM PendingMovieHistoryWrites WHERE SessionId=? AND ApplyState='pending'` plus the same on `PendingTorrentHistoryWrites` after asserting `SELECT Status FROM ReportSessions WHERE Id=?` returns `'committed'` — these tables never feed live reads, so the DELETE is non-destructive; (2) one-shot Python: `python3 -c "from packages.python.javdb_platform.db import db_commit_session_history; print(db_commit_session_history(<id>))"` — drains then deletes via the same applied-cleanup path used at commit time. (`apps.cli.commit_session` skips the drain when the session row is already `committed`, so the CLI route does **not** clear the residual.) |

---

## Validation playbook (dev branch — Phase 3, 6 steps)

Before promoting Phase 3 to `main`, exercise every dispatch path once on `dev`:

1. **Happy path** — Dispatch `Daily Ingestion Pipeline` on `dev` with default settings. Expected outcomes:
   - Spider runs, every history write goes through `db_stage_history_write` (verify by querying `PendingMovieHistoryWrites WHERE SessionId=<sid>`).
   - `Mark sessions as committed` step runs `db_commit_session_history`; the session ends `Status='committed'`, `pending_residual_count=0`.
   - Email subject has **no** `PENDING-ALERT` prefix; the **Pending Mode Verification** block lists every metric green.
2. **In-progress fail** — In a dev workflow file, inject `exit 1` after `Step 1 - Run Spider`. Re-dispatch. Expected outcomes:
   - `cleanup-on-failure` job triggers; the rollback CLI dispatches via `_rollback_pending_in_progress` (visible in the JSON summary as `mode='rollback_pending'`).
   - `pending_staged_count > 0` and `pending_residual_count = 0` in the verify line.
   - Email body shows `final_status='failed'`, no critical alert.
3. **Finalizing fail** — In a dev workflow file, inject `kill -9` against the `Mark sessions as committed` step's Python process mid-run (or temporarily monkey-patch `db_finish_commit_session` to raise). Re-dispatch. Expected outcomes:
   - `cleanup-on-failure` discovers the session in `Status='finalizing'`, dispatches `db_resume_finalizing_session`, drives it to `committed`.
   - Email subject prefixed `[PENDING-ALERT]` (`commit_attempts=2`); body confirms the resume succeeded.
4. **Forced soft alert** — Dispatch with env override `JAVDB_PENDING_BATCH_SIZE=1` (forces N D1 calls per row). Expected outcomes:
   - `d1_request_count_audit_baseline_ratio > 1.8` triggers `[PENDING-ALERT]`.
   - **No** auto-fallback (soft alerts only annotate the subject).
5. **Forced critical alert** — Temporarily monkey-patch `_commit_one_movie` on `dev` to write a wrong `PerfectMatchIndicator`, ensure `JAVDB_PENDING_SHADOW_AUDIT=1`. Re-dispatch. Expected outcomes:
   - Email subject prefixed `[PENDING-ROLLBACK-AUTO]`.
   - `.publish-config.yml` gains a `pending_mode_disabled_until` block via the email job's auto-fallback step.
   - Re-dispatch immediately: `Resolve effective WriteMode` reports `audit`; spider falls back to legacy X3.
6. **Manual recovery** — `git revert` the auto-fallback commit (or delete the `pending_mode_disabled_*` lines manually) and dispatch once more. Expected outcomes:
   - `Resolve effective WriteMode` reports `pending` again.
   - Verify line is clean; email has no alert prefix.

If any of these six steps deviates from the expected outcome, **do not** promote Phase 3 to `main`. Capture the failing run's verify line + rollback log and open an issue.

> *Legacy audit-mode validation* has moved to [Appendix A — Legacy audit fallback (sunset 2026-08)](#appendix-a-legacy-audit-fallback-sunset-2026-08) along with the rest of the `WriteMode='audit'` reference material.

---

## File pointers

- CLI: [`apps/cli/rollback.py`](../apps/cli/rollback.py), [`apps/cli/commit_session.py`](../apps/cli/commit_session.py), [`apps/cli/cleanup_stale_in_progress.py`](../apps/cli/cleanup_stale_in_progress.py)
- Core helpers: [`packages/python/javdb_platform/db.py`](../packages/python/javdb_platform/db.py) (`db_stage_history_write`, `db_commit_session_history`, `db_resume_finalizing_session`, `db_rollback_session`, `db_mark_session_committed`, `db_find_in_progress_sessions`, `db_find_stale_pending_sessions`, `db_pending_session_stats`, `_audit_record_movie_change`, `_rollback_history`, etc.)
- Phase 3 scripts: [`scripts/aggregate_pending_health.py`](../scripts/aggregate_pending_health.py), [`scripts/pending_mode_auto_fallback.py`](../scripts/pending_mode_auto_fallback.py)
- Phase 4 scripts: [`scripts/audit_archive.py`](../scripts/audit_archive.py), [`scripts/cleanup_stale_session_audits.py`](../scripts/cleanup_stale_session_audits.py) (read-only since 2026-05-13)
- Email integration: [`packages/python/javdb_integrations/email_notification.py`](../packages/python/javdb_integrations/email_notification.py) (`_format_pending_verify_section`, `_evaluate_pending_alerts`, `_format_health_snapshot_section`)
- Workflows: [`.github/workflows/DailyIngestion.yml`](../.github/workflows/DailyIngestion.yml), [`.github/workflows/AdHocIngestion.yml`](../.github/workflows/AdHocIngestion.yml), [`.github/workflows/RollbackD1.yml`](../.github/workflows/RollbackD1.yml), [`.github/workflows/StaleSessionCleanup.yml`](../.github/workflows/StaleSessionCleanup.yml), [`.github/workflows/AuditArchive.yml`](../.github/workflows/AuditArchive.yml)
- Migrations: [`migration/d1/2026_05_04_add_rollback_columns_*.sql`](../migration/d1/), [`migration/d1/2026_05_09_add_pending_history_tables.sql`](../migration/d1/)
- Plan reference: [`.cursor/plans/ingestion_perfect_rollback_2152bae2.plan.md`](../.cursor/plans/ingestion_perfect_rollback_2152bae2.plan.md)

---

## Appendix A — Legacy audit fallback (sunset 2026-08)

> **Status** — *deprecated since Phase 4, 2026-05-13.*
> **Sunset date** — *2026-08-13.* On that date the audit fallback is removed entirely: `JAVDB_HISTORY_WRITE_MODE=audit` will be rejected at session-create time, the `_rollback_history` audit replay path will be deleted, and the `MovieHistoryAudit` / `TorrentHistoryAudit` tables will be dropped from the schema. Any session still pinning `audit` past that date will refuse to start.

### A.1 Timeline

| Date | Event |
|---|---|
| 2026-05-04 | X3 audit hybrid lands on `main` as the default rollback strategy. |
| 2026-05-09 | Phase 0 / 1 / 2 — `PendingMovie/TorrentHistoryWrites` schema + pending-mode write path shipped behind `JAVDB_HISTORY_WRITE_MODE`. |
| 2026-05-11 | Phase 3 — Daily / AdHoc / TestIngestion default flips to `WriteMode='pending'`; audit fallback retained for emergency dispatch. |
| **2026-05-13** | **Phase 4 — Audit deprecation announced.** `db_upsert_history` emits `DeprecationWarning`; `JAVDB_AUDIT_WRITES_DISABLED` kill switch becomes available; `scripts/cleanup_stale_session_audits.py` flips to read-only; `scripts/audit_archive.py` cron starts running. |
| 2026-07-13 | Soft cutover — every workflow file removes `write_mode_override=audit` from its dispatch matrix; any operator-set override is logged but not yet refused. |
| **2026-08-13** | **Hard sunset.** `_resolve_write_mode('audit')` raises; rollback CLI's audit-replay branch removed; audit tables dropped from new SQLite + D1 schemas (existing rows are migrated out by the final archive run). |

### A.2 What "deprecated" means in practice

- `db_upsert_history()` emits `DeprecationWarning` on every call. The function still works (audit-fallback rollback depends on it for legacy sessions) — direct callers must migrate to `save_parsed_movie_to_history` (which auto-stages under `WriteMode='pending'` and only reaches `db_upsert_history` for explicit audit fallbacks).
- The `JAVDB_AUDIT_WRITES_DISABLED=1` env var (added 2026-05-13, [`packages/python/javdb_platform/db.py`](../packages/python/javdb_platform/db.py)) turns every audit-row INSERT into a no-op while still letting `MovieHistory` / `TorrentHistory` UPSERTs land. Default is `0` because the audit fallback still needs audit rows during the deprecation window; flip to `1` once every workflow has been verified to run pending-only.
- `MovieHistoryAudit` / `TorrentHistoryAudit` rows remain queryable for forensics — manual rollback via `apps.cli.rollback --scope history --session-id <id>` still works for any legacy session that has audit rows. The expectation is that no *new* sessions land in this branch.
- The destructive cleanup helper `scripts/cleanup_stale_session_audits.py` is now strictly read-only — passing `--apply` logs a deprecation warning and silently degrades to dry-run.

### A.3 Audit-archive cron ([`AuditArchive.yml`](../.github/workflows/AuditArchive.yml))

Runs every Monday at 04:00 UTC (12:00 Asia/Singapore). Default mode is dry-run; an operator promotes to `apply=true` through `workflow_dispatch` once a week's worth of dry-run reports look sane.

The cron prunes audit rows whose owning `ReportSessions` row is older than `--older-than-days` (default 30) and falls into one of three buckets:

1. `committed_expired` — owning session is `Status='committed'` (the inline prune in `db_mark_session_committed` failed to fire, usually due to a transient D1 error during the success-path step).
2. `failed_expired` / `in_progress_expired` / `finalizing_expired` — owning session is in a non-committed state but is past the archival window. The stale-session cron has already had several chances to drive it to a resolved state, so the audit rows can be reaped.
3. `orphan_session` — owning `ReportSessions.Id` no longer exists. These are the canonical "phantom" rows the 2026-05-08 incident produced; the archival window guarantees no legitimate cleanup workflow is still asking for them.

```bash
# Dry-run weekly artifact:
python3 -m scripts.audit_archive  # --target both --older-than-days 30

# Manual apply with a shorter window (incident response):
python3 -m scripts.audit_archive --apply --older-than-days 7 --target sqlite
```

### A.4 Legacy audit-mode validation playbook (kept for fallback)

The pre-Phase-3 5-step audit playbook still applies when an operator forces `WriteMode='audit'` for a run *before* the 2026-08-13 sunset:

1. Push the rollback wiring to `dev`. Confirm `cleanup-on-failure`, `Mark sessions as committed`, and `RollbackD1.yml` are present.
2. Smoke the success path. Dispatch DailyIngestion / AdHocIngestion with `write_mode_override=audit`. Confirm `Mark sessions as committed` flips `Status` to `committed` and the `MovieHistoryAudit` rows for that session are pruned.
3. Smoke the failure path. Inject `exit 1` after `Step 1 - Run Spider`. Watch `cleanup-on-failure` run, the rollback log report `mode='audit_replay'`, and the verify line emit `pending_staged_count=0`.
4. Smoke the manual workflow. Dispatch `Rollback D1 Session` with `dry_run=true` and the just-rolled-back session id. Confirm zero counts.
5. Revert the injected failure before promoting.

> After 2026-08-13 this playbook is not runnable — the `audit` value is rejected. Pending-mode is the only supported path; manual incidents that need byte-for-byte historical reconstruction have to fall back to `scripts/sync_d1_to_sqlite.py` against a pre-sunset backup.
- Tests: [`tests/unit/test_rollback.py`](../tests/unit/test_rollback.py), [`tests/unit/test_rollback_pending_mode.py`](../tests/unit/test_rollback_pending_mode.py)
