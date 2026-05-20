# db

Session-lifecycle and database-maintenance CLIs — rollback, commit, audit cleanup, pending-mode health/alert, D1↔SQLite sync.

## Files

| File | Purpose |
|---|---|
| `rollback.py` | Roll back D1/SQLite writes for a failed run. Two modes: automated cleanup-on-failure (`--run-id`/`--attempt`) and manual recovery (`--session-id`). Defaults to dry-run; pass `--apply` to mutate. |
| `commit_session.py` | Mark `in_progress` ReportSessions rows as `committed` once Spider + Uploader + Bridge all succeed. Accepts `--session-id` and/or `--run-started-at` lookups. |
| `migration.py` | Canonical migration CLI — wraps `javdb.migrations.migrate_to_current.main`. |
| `audit_archive.py` | Phase-4 audit-table archival cron. Prunes `MovieHistoryAudit` / `TorrentHistoryAudit` rows whose owning session is committed > N days ago, orphaned, or stale failed/in_progress. |
| `cleanup_stale_in_progress.py` | Daily cron to roll back sessions stuck `in_progress` and drive `finalizing` sessions to `committed`. Walks rows older than `--max-age-hours` (default 48). |
| `cleanup_stale_session_audits.py` | Read-only forensics tool that flags phantom audit / history rows (orphan-session, cross-day, committed-with-audit). Writes a JSON report; mutation path retired in Phase 4. |
| `sweep_claim_stages.py` | Cron-friendly wrapper around the MovieClaim DO `sweep_orphan_stages` route. Reaps staged completions left behind by crashed runners. |
| `sync_d1_to_sqlite.py` | One-shot reconciliation tool that pulls every business table from D1 down into the local sqlite mirror. Default mode is dry-run; never deletes SQLite rows without `--prune-local-only`. |
| `pending_health.py` | Phase-3 email pre-step. Aggregates the last 24h of `pending_session_verify` records from `reports/D1/d1_drift.jsonl` into `reports/D1/pending_health_24h.json`. |
| `pending_alert.py` | ADR-006 pause-on-alert: injects `pipeline_paused_until: <ISO>` into `.publish-config.yml` when the email pipeline detects a critical pending-mode alert. Idempotent — extends the timer rather than shortening it. |
| `_session_helpers.py` | Internal scaffolding shared by `rollback` and `commit_session`: ISO-timestamp normalisation, session lookups, pre-state reads, MovieClaim DO fan-out, JSONL emission. Not a CLI entry point. |

## Invoked by

- **`AuditArchive.yml`** — `python3 -m apps.cli.db.audit_archive` (weekly cron).
- **`DailyIngestion.yml`** — `python3 -m apps.cli.db.pending_health` (pre-email step) and `python3 -m apps.cli.db.pending_alert` (post-email pause on critical alert).
- **`AdHocIngestion.yml`** — `python3 -m apps.cli.db.pending_health` and `python3 -m apps.cli.db.pending_alert` (same roles as DailyIngestion).
- **`StaleSessionCleanup.yml`** — `apps.cli.cleanup_stale_in_progress` and `apps.cli.sweep_movie_claim_stages` (currently still resolved via Phase-1 shims; the canonical modules are `apps.cli.db.cleanup_stale_in_progress` / `apps.cli.db.sweep_claim_stages`).
- **`RollbackD1.yml`** — `apps.cli.rollback` (manual recovery; canonical module is `apps.cli.db.rollback`).
- **`DailyIngestion.yml` / `AdHocIngestion.yml` cleanup-on-failure** — `apps.cli.rollback` and `apps.cli.commit_session`.

## Related

- [ADR-006 — Pending mode default rollout](../../../docs/design/adr/ADR-006-pending-mode-default-rollout.md)
- [ADR-007 — Monorepo restructure](../../../docs/design/adr/archive/ADR-007-monorepo-restructure-2026-05.md)
- [docs/handbook/en/ops/d1-rollback.md](../../../docs/handbook/en/ops/d1-rollback.md)
