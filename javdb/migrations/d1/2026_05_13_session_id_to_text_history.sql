-- 2026-05-13 — convert SessionId / Seq columns from INTEGER to TEXT on
-- the history database (history.db on SQLite ↔ javdb-history on D1).
--
-- Background
-- ----------
-- Cloudflare D1's /query HTTP endpoint round-trips parameters and result
-- rows through a JavaScript JSON layer whose Number is IEEE-754 double,
-- so any integer with |x| > 2**53 - 1 silently truncates in transit.
-- The 63-bit snowflake ``ReportSessions.Id`` lands around 7e18 — three
-- orders of magnitude past the safe ceiling — and was diverging between
-- SQLite and D1 the moment it crossed the wire.
--
-- The fix (2026-05-13): store session-id-like columns as TEXT on both
-- backends. The application now generates an ISO-8601-style
-- ``YYYYMMDDTHHMMSS.ffffffZ-tag-seq`` snowflake instead of an integer.
--
-- This migration rebuilds each affected table on D1. The same migration
-- is mirrored on the local SQLite side by SCHEMA_VERSION 11 -> 12 in
-- ``packages/python/javdb_platform/db.py``.
--
-- IMPORTANT: legacy rows whose ``Id`` / ``SessionId`` was already
-- truncated on D1 (i.e. written before the
-- ``d1_client._params_for_d1_json`` stringify patch landed on 2026-05-12)
-- will keep that truncated value here, just stored as TEXT. Repairing
-- those rows is a separate "resync D1 from SQLite as source of truth"
-- task and is intentionally OUT OF SCOPE for this DDL change.
--
-- Apply
-- -----
--   wrangler d1 execute javdb-history --remote \
--     --file=migration/d1/2026_05_13_session_id_to_text_history.sql
--
-- Rollback
-- --------
-- Not supported as a single SQL roll-back; the application no longer
-- emits integer ids, so reverting would orphan every post-migration row.
-- If a rollback is truly needed, restore the database from the
-- pre-migration backup taken before this file was run.

-- ── MovieHistory ────────────────────────────────────────────────────────
CREATE TABLE MovieHistory_new (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    VideoCode TEXT NOT NULL,
    Href TEXT NOT NULL UNIQUE,
    ActorName TEXT,
    ActorGender TEXT,
    ActorLink TEXT,
    SupportingActors TEXT,
    DateTimeCreated TEXT,
    DateTimeUpdated TEXT,
    DateTimeVisited TEXT,
    PerfectMatchIndicator INTEGER,
    HiResIndicator INTEGER,
    SessionId TEXT
);
INSERT INTO MovieHistory_new
    (Id, VideoCode, Href, ActorName, ActorGender, ActorLink,
     SupportingActors, DateTimeCreated, DateTimeUpdated, DateTimeVisited,
     PerfectMatchIndicator, HiResIndicator, SessionId)
SELECT Id, VideoCode, Href, ActorName, ActorGender, ActorLink,
       SupportingActors, DateTimeCreated, DateTimeUpdated, DateTimeVisited,
       PerfectMatchIndicator, HiResIndicator, CAST(SessionId AS TEXT)
FROM MovieHistory;
DROP TABLE MovieHistory;
ALTER TABLE MovieHistory_new RENAME TO MovieHistory;
CREATE INDEX IF NOT EXISTS idx_movie_history_video_code ON MovieHistory(VideoCode);
CREATE INDEX IF NOT EXISTS idx_movie_history_session ON MovieHistory(SessionId);

-- ── TorrentHistory ─────────────────────────────────────────────────────
CREATE TABLE TorrentHistory_new (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    MovieHistoryId INTEGER NOT NULL REFERENCES MovieHistory(Id),
    MagnetUri TEXT,
    SubtitleIndicator INTEGER,
    CensorIndicator INTEGER,
    ResolutionType INTEGER,
    Size TEXT,
    FileCount INTEGER,
    DateTimeCreated TEXT,
    DateTimeUpdated TEXT,
    SessionId TEXT
);
INSERT INTO TorrentHistory_new
    (Id, MovieHistoryId, MagnetUri, SubtitleIndicator, CensorIndicator,
     ResolutionType, Size, FileCount, DateTimeCreated, DateTimeUpdated,
     SessionId)
SELECT Id, MovieHistoryId, MagnetUri, SubtitleIndicator, CensorIndicator,
       ResolutionType, Size, FileCount, DateTimeCreated, DateTimeUpdated,
       CAST(SessionId AS TEXT)
FROM TorrentHistory;
DROP TABLE TorrentHistory;
ALTER TABLE TorrentHistory_new RENAME TO TorrentHistory;
CREATE UNIQUE INDEX IF NOT EXISTS uq_torrent_type
    ON TorrentHistory(MovieHistoryId, SubtitleIndicator, CensorIndicator);
CREATE INDEX IF NOT EXISTS idx_torrent_history_session ON TorrentHistory(SessionId);

-- ── MovieHistoryAudit ──────────────────────────────────────────────────
CREATE TABLE MovieHistoryAudit_new (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    TargetId INTEGER NOT NULL,
    Action TEXT NOT NULL,
    OldRowJson TEXT,
    SessionId TEXT NOT NULL,
    DateTimeCreated TEXT NOT NULL,
    RunId TEXT,
    RunAttempt INTEGER
);
INSERT INTO MovieHistoryAudit_new
    (Id, TargetId, Action, OldRowJson, SessionId, DateTimeCreated, RunId, RunAttempt)
SELECT Id, TargetId, Action, OldRowJson, CAST(SessionId AS TEXT),
       DateTimeCreated, RunId, RunAttempt
FROM MovieHistoryAudit;
DROP TABLE MovieHistoryAudit;
ALTER TABLE MovieHistoryAudit_new RENAME TO MovieHistoryAudit;
CREATE INDEX IF NOT EXISTS idx_mh_audit_session ON MovieHistoryAudit(SessionId, Id);
CREATE INDEX IF NOT EXISTS idx_mh_audit_run ON MovieHistoryAudit(RunId, RunAttempt);

-- ── TorrentHistoryAudit ────────────────────────────────────────────────
CREATE TABLE TorrentHistoryAudit_new (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    TargetId INTEGER NOT NULL,
    Action TEXT NOT NULL,
    OldRowJson TEXT,
    SessionId TEXT NOT NULL,
    DateTimeCreated TEXT NOT NULL,
    RunId TEXT,
    RunAttempt INTEGER
);
INSERT INTO TorrentHistoryAudit_new
    (Id, TargetId, Action, OldRowJson, SessionId, DateTimeCreated, RunId, RunAttempt)
SELECT Id, TargetId, Action, OldRowJson, CAST(SessionId AS TEXT),
       DateTimeCreated, RunId, RunAttempt
FROM TorrentHistoryAudit;
DROP TABLE TorrentHistoryAudit;
ALTER TABLE TorrentHistoryAudit_new RENAME TO TorrentHistoryAudit;
CREATE INDEX IF NOT EXISTS idx_th_audit_session ON TorrentHistoryAudit(SessionId, Id);
CREATE INDEX IF NOT EXISTS idx_th_audit_run ON TorrentHistoryAudit(RunId, RunAttempt);

-- ── PendingMovieHistoryWrites ──────────────────────────────────────────
-- Seq is also generated by ``_generate_session_id`` and therefore needs
-- TEXT for the same JSON precision reason.
CREATE TABLE PendingMovieHistoryWrites_new (
    Seq TEXT PRIMARY KEY NOT NULL,
    SessionId TEXT NOT NULL,
    RunId TEXT,
    RunAttempt INTEGER,
    Href TEXT NOT NULL,
    VideoCode TEXT,
    ActorName TEXT,
    ActorGender TEXT,
    ActorLink TEXT,
    SupportingActors TEXT,
    DateTimeVisited TEXT NOT NULL,
    CreatedAt TEXT NOT NULL,
    ApplyState TEXT NOT NULL DEFAULT 'pending'
);
INSERT INTO PendingMovieHistoryWrites_new
    (Seq, SessionId, RunId, RunAttempt, Href, VideoCode, ActorName,
     ActorGender, ActorLink, SupportingActors, DateTimeVisited,
     CreatedAt, ApplyState)
SELECT CAST(Seq AS TEXT), CAST(SessionId AS TEXT), RunId, RunAttempt,
       Href, VideoCode, ActorName, ActorGender, ActorLink,
       SupportingActors, DateTimeVisited, CreatedAt, ApplyState
FROM PendingMovieHistoryWrites;
DROP TABLE PendingMovieHistoryWrites;
ALTER TABLE PendingMovieHistoryWrites_new RENAME TO PendingMovieHistoryWrites;
CREATE INDEX IF NOT EXISTS idx_pmhw_session ON PendingMovieHistoryWrites(SessionId);
CREATE INDEX IF NOT EXISTS idx_pmhw_run ON PendingMovieHistoryWrites(RunId, RunAttempt);
CREATE INDEX IF NOT EXISTS idx_pmhw_href ON PendingMovieHistoryWrites(Href);
CREATE INDEX IF NOT EXISTS idx_pmhw_session_state
    ON PendingMovieHistoryWrites(SessionId, ApplyState);

-- ── PendingTorrentHistoryWrites ────────────────────────────────────────
CREATE TABLE PendingTorrentHistoryWrites_new (
    Seq TEXT PRIMARY KEY NOT NULL,
    SessionId TEXT NOT NULL,
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
    CreatedAt TEXT NOT NULL,
    ApplyState TEXT NOT NULL DEFAULT 'pending'
);
INSERT INTO PendingTorrentHistoryWrites_new
    (Seq, SessionId, RunId, RunAttempt, Href, VideoCode, Category,
     SubtitleIndicator, CensorIndicator, MagnetUri, Size, FileCount,
     ResolutionType, DateTimeVisited, CreatedAt, ApplyState)
SELECT CAST(Seq AS TEXT), CAST(SessionId AS TEXT), RunId, RunAttempt,
       Href, VideoCode, Category, SubtitleIndicator, CensorIndicator,
       MagnetUri, Size, FileCount, ResolutionType, DateTimeVisited,
       CreatedAt, ApplyState
FROM PendingTorrentHistoryWrites;
DROP TABLE PendingTorrentHistoryWrites;
ALTER TABLE PendingTorrentHistoryWrites_new RENAME TO PendingTorrentHistoryWrites;
CREATE INDEX IF NOT EXISTS idx_pthw_session ON PendingTorrentHistoryWrites(SessionId);
CREATE INDEX IF NOT EXISTS idx_pthw_run ON PendingTorrentHistoryWrites(RunId, RunAttempt);
CREATE INDEX IF NOT EXISTS idx_pthw_href ON PendingTorrentHistoryWrites(Href);
CREATE INDEX IF NOT EXISTS idx_pthw_session_state
    ON PendingTorrentHistoryWrites(SessionId, ApplyState);

