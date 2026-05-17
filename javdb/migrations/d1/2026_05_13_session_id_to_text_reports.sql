-- 2026-05-13 — convert ReportSessions.Id (and FK SessionId columns) from
-- INTEGER to TEXT on the reports database (reports.db ↔ javdb-reports).
--
-- See ``migration/d1/2026_05_13_session_id_to_text_history.sql`` for the
-- full background. This file is the reports-side companion.
--
-- ``ReportSessions.Id`` loses ``AUTOINCREMENT`` along with the type
-- change — :func:`_generate_session_id` has been supplying the id
-- explicitly since 2026-05-08, so the autoincrement counter was already
-- dead weight.
--
-- Apply
-- -----
--   wrangler d1 execute javdb-reports --remote \
--     --file=migration/d1/2026_05_13_session_id_to_text_reports.sql

PRAGMA foreign_keys = OFF;

-- ── ReportSessions ─────────────────────────────────────────────────────
CREATE TABLE ReportSessions_new (
    Id TEXT PRIMARY KEY,
    ReportType TEXT NOT NULL,
    ReportDate TEXT NOT NULL,
    UrlType TEXT,
    DisplayName TEXT,
    Url TEXT,
    StartPage INTEGER,
    EndPage INTEGER,
    CsvFilename TEXT NOT NULL,
    DateTimeCreated TEXT NOT NULL,
    Status TEXT DEFAULT 'in_progress',
    RunId TEXT,
    RunAttempt INTEGER,
    FailureReason TEXT,
    WriteMode TEXT DEFAULT 'audit'
);
INSERT INTO ReportSessions_new
    (Id, ReportType, ReportDate, UrlType, DisplayName, Url, StartPage,
     EndPage, CsvFilename, DateTimeCreated, Status, RunId, RunAttempt,
     FailureReason, WriteMode)
SELECT CAST(Id AS TEXT), ReportType, ReportDate, UrlType, DisplayName,
       Url, StartPage, EndPage, CsvFilename, DateTimeCreated, Status,
       RunId, RunAttempt, FailureReason, WriteMode
FROM ReportSessions;
DROP TABLE ReportSessions;
ALTER TABLE ReportSessions_new RENAME TO ReportSessions;
CREATE INDEX IF NOT EXISTS idx_report_sessions_type_date
    ON ReportSessions(ReportType, ReportDate);
CREATE INDEX IF NOT EXISTS idx_report_sessions_write_mode
    ON ReportSessions(WriteMode, Status);
CREATE INDEX IF NOT EXISTS idx_report_sessions_csv ON ReportSessions(CsvFilename);
CREATE INDEX IF NOT EXISTS idx_report_sessions_status
    ON ReportSessions(Status, DateTimeCreated);
CREATE INDEX IF NOT EXISTS idx_report_sessions_run
    ON ReportSessions(RunId, RunAttempt);
CREATE UNIQUE INDEX IF NOT EXISTS uq_reportsessions_runidentity_csv
    ON ReportSessions(RunId, RunAttempt, CsvFilename)
    WHERE Status = 'in_progress' AND RunId IS NOT NULL;

-- ── ReportMovies ───────────────────────────────────────────────────────
CREATE TABLE ReportMovies_new (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    SessionId TEXT NOT NULL REFERENCES ReportSessions(Id),
    Href TEXT,
    VideoCode TEXT,
    Page INTEGER,
    Actor TEXT,
    Rate REAL,
    CommentNumber INTEGER
);
INSERT INTO ReportMovies_new
    (Id, SessionId, Href, VideoCode, Page, Actor, Rate, CommentNumber)
SELECT Id, CAST(SessionId AS TEXT), Href, VideoCode, Page, Actor, Rate, CommentNumber
FROM ReportMovies;
DROP TABLE ReportMovies;
ALTER TABLE ReportMovies_new RENAME TO ReportMovies;
CREATE INDEX IF NOT EXISTS idx_report_movies_session ON ReportMovies(SessionId);
CREATE INDEX IF NOT EXISTS idx_report_movies_video_code ON ReportMovies(VideoCode);

-- ── SpiderStats ────────────────────────────────────────────────────────
CREATE TABLE SpiderStats_new (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    SessionId TEXT NOT NULL REFERENCES ReportSessions(Id),
    Phase1Discovered INTEGER,
    Phase1Processed  INTEGER,
    Phase1Skipped    INTEGER,
    Phase1NoNew      INTEGER,
    Phase1Failed     INTEGER,
    Phase2Discovered INTEGER,
    Phase2Processed  INTEGER,
    Phase2Skipped    INTEGER,
    Phase2NoNew      INTEGER,
    Phase2Failed     INTEGER,
    TotalDiscovered  INTEGER,
    TotalProcessed   INTEGER,
    TotalSkipped     INTEGER,
    TotalNoNew       INTEGER,
    TotalFailed      INTEGER,
    FailedMovies     TEXT,
    DateTimeCreated  TEXT
);
INSERT INTO SpiderStats_new
SELECT Id, CAST(SessionId AS TEXT),
       Phase1Discovered, Phase1Processed, Phase1Skipped, Phase1NoNew, Phase1Failed,
       Phase2Discovered, Phase2Processed, Phase2Skipped, Phase2NoNew, Phase2Failed,
       TotalDiscovered, TotalProcessed, TotalSkipped, TotalNoNew, TotalFailed,
       FailedMovies, DateTimeCreated
FROM SpiderStats;
DROP TABLE SpiderStats;
ALTER TABLE SpiderStats_new RENAME TO SpiderStats;
CREATE UNIQUE INDEX IF NOT EXISTS uq_spiderstats_session ON SpiderStats(SessionId);

-- ── UploaderStats ──────────────────────────────────────────────────────
CREATE TABLE UploaderStats_new (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    SessionId TEXT NOT NULL REFERENCES ReportSessions(Id),
    TotalTorrents     INTEGER,
    DuplicateCount    INTEGER,
    Attempted         INTEGER,
    SuccessfullyAdded INTEGER,
    FailedCount       INTEGER,
    HackedSub         INTEGER,
    HackedNosub       INTEGER,
    SubtitleCount     INTEGER,
    NoSubtitleCount   INTEGER,
    SuccessRate       REAL,
    DateTimeCreated   TEXT
);
INSERT INTO UploaderStats_new
SELECT Id, CAST(SessionId AS TEXT),
       TotalTorrents, DuplicateCount, Attempted, SuccessfullyAdded, FailedCount,
       HackedSub, HackedNosub, SubtitleCount, NoSubtitleCount, SuccessRate,
       DateTimeCreated
FROM UploaderStats;
DROP TABLE UploaderStats;
ALTER TABLE UploaderStats_new RENAME TO UploaderStats;
CREATE UNIQUE INDEX IF NOT EXISTS uq_uploaderstats_session ON UploaderStats(SessionId);

-- ── PikpakStats ────────────────────────────────────────────────────────
CREATE TABLE PikpakStats_new (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    SessionId TEXT NOT NULL REFERENCES ReportSessions(Id),
    ThresholdDays     INTEGER,
    TotalTorrents     INTEGER,
    FilteredOld       INTEGER,
    SuccessfulCount   INTEGER,
    FailedCount       INTEGER,
    UploadedCount     INTEGER,
    DeleteFailedCount INTEGER,
    DateTimeCreated   TEXT
);
INSERT INTO PikpakStats_new
SELECT Id, CAST(SessionId AS TEXT),
       ThresholdDays, TotalTorrents, FilteredOld, SuccessfulCount, FailedCount,
       UploadedCount, DeleteFailedCount, DateTimeCreated
FROM PikpakStats;
DROP TABLE PikpakStats;
ALTER TABLE PikpakStats_new RENAME TO PikpakStats;
CREATE UNIQUE INDEX IF NOT EXISTS uq_pikpakstats_session ON PikpakStats(SessionId);

PRAGMA foreign_keys = ON;

