-- 2026-05-09: Add Pending* history write tables for Phase 0 of the
--   Ingestion Perfect Rollback project (see
--   /Users/tedwu/.cursor/plans/ingestion_perfect_rollback_2152bae2.plan.md).
--
-- Apply with:
--   wrangler d1 execute javdb-history --remote \
--     --file=migration/d1/2026_05_09_add_pending_history_tables.sql
--
-- These tables stage every MovieHistory / TorrentHistory mutation that an
-- ingestion run wants to apply.  Until ``apps.cli.commit_session`` runs
-- and promotes the rows into the live MovieHistory / TorrentHistory
-- tables (Phase 2 ``db_commit_session_history``), the pending rows act
-- as the session's overlay on top of the committed live state without
-- polluting any sibling session's reads.
--
-- The Phase 0 migration only creates the schema; the feature is gated
-- behind ``ReportSessions.WriteMode='pending'`` (see the companion
-- 2026_05_09_add_pending_history_tables_reports.sql migration) which
-- defaults to ``'audit'`` so the existing audit-replay rollback path
-- stays in effect until Phase 2 cuts the implementation over.

CREATE TABLE IF NOT EXISTS PendingMovieHistoryWrites (
    Seq INTEGER PRIMARY KEY AUTOINCREMENT,
    SessionId INTEGER NOT NULL,
    RunId TEXT,
    RunAttempt INTEGER,
    Href TEXT NOT NULL,
    VideoCode TEXT,
    ActorName TEXT,
    ActorGender TEXT,
    ActorLink TEXT,
    SupportingActors TEXT,
    DateTimeVisited TEXT NOT NULL,
    CreatedAt TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ApplyState TEXT NOT NULL DEFAULT 'pending'
        CHECK(ApplyState IN ('pending','applied'))
);
CREATE INDEX IF NOT EXISTS idx_pmhw_session ON PendingMovieHistoryWrites(SessionId);
CREATE INDEX IF NOT EXISTS idx_pmhw_run ON PendingMovieHistoryWrites(RunId, RunAttempt);
CREATE INDEX IF NOT EXISTS idx_pmhw_href ON PendingMovieHistoryWrites(Href);
CREATE INDEX IF NOT EXISTS idx_pmhw_session_state
    ON PendingMovieHistoryWrites(SessionId, ApplyState);

CREATE TABLE IF NOT EXISTS PendingTorrentHistoryWrites (
    Seq INTEGER PRIMARY KEY AUTOINCREMENT,
    SessionId INTEGER NOT NULL,
    RunId TEXT,
    RunAttempt INTEGER,
    Href TEXT NOT NULL,
    VideoCode TEXT,
    Category TEXT NOT NULL,
    SubtitleIndicator INTEGER NOT NULL,
    CensorIndicator INTEGER NOT NULL,
    MagnetUri TEXT,
    Size TEXT,
    FileCount INTEGER,
    ResolutionType INTEGER,
    DateTimeVisited TEXT NOT NULL,
    CreatedAt TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ApplyState TEXT NOT NULL DEFAULT 'pending'
        CHECK(ApplyState IN ('pending','applied'))
);
CREATE INDEX IF NOT EXISTS idx_pthw_session ON PendingTorrentHistoryWrites(SessionId);
CREATE INDEX IF NOT EXISTS idx_pthw_run ON PendingTorrentHistoryWrites(RunId, RunAttempt);
CREATE INDEX IF NOT EXISTS idx_pthw_href ON PendingTorrentHistoryWrites(Href);
CREATE INDEX IF NOT EXISTS idx_pthw_session_state
    ON PendingTorrentHistoryWrites(SessionId, ApplyState);
