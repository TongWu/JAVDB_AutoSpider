"""Database migration helpers for JAVDB AutoSpider.

Handles schema initialization, version detection, and migrations between
schema versions. Manages the split from single-DB to three-DB layout.

Schema versions:
- v5: Single database (javdb_autospider.db)
- v6: Split into history.db, reports.db, operations.db
- v7: Added Actor columns to MovieHistory
- v8: Added Rollback columns (SessionId, WriteMode)
- v13: Current version (includes Pending tables)
- v14: Dropped audit tables (ADR-005)
"""

import os
import re
import sqlite3
import threading
from typing import List, Optional

from javdb.infra.logging import get_logger

from .db_connection import (
    HISTORY_DB_PATH,
    REPORTS_DB_PATH,
    OPERATIONS_DB_PATH,
    DB_PATH,
    SCHEMA_VERSION,
    _backend_mode,
    _is_valid_sqlite,
    _local,
    get_db,
)

logger = get_logger(__name__)

# Serializes the dual-backend init window. Without this, two threads racing
# into ``init_db`` would both try to mutate ``_local.conns`` and the env-var /
# thread-local override, with the second thread potentially observing a
# half-applied state. ``_do_init`` also touches the file system, which is
# itself unsafe to run concurrently against the same SQLite paths.
_init_lock = threading.Lock()


# ── Schema DDL (split across three databases) ────────────────────────────

_SCHEMA_VERSION_DDL = """
CREATE TABLE IF NOT EXISTS SchemaVersion (
    Version INTEGER NOT NULL
);
"""

_HISTORY_DDL = _SCHEMA_VERSION_DDL + """
CREATE TABLE IF NOT EXISTS MovieHistory (
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
CREATE INDEX IF NOT EXISTS idx_movie_history_video_code ON MovieHistory(VideoCode);
CREATE INDEX IF NOT EXISTS idx_movie_history_session ON MovieHistory(SessionId);

CREATE TABLE IF NOT EXISTS TorrentHistory (
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
CREATE UNIQUE INDEX IF NOT EXISTS uq_torrent_type
    ON TorrentHistory(MovieHistoryId, SubtitleIndicator, CensorIndicator);
CREATE INDEX IF NOT EXISTS idx_torrent_history_session ON TorrentHistory(SessionId);

-- Pending history write tables (Ingestion Perfect Rollback, Phase 0).
--
-- Every ingestion mutation against MovieHistory / TorrentHistory under
-- ``WriteMode='pending'`` is staged here first.  ``apps.cli.db.commit_session``
-- promotes them into the live tables atomically per movie at the end of
-- a successful run; ``apps.cli.db.rollback`` deletes them on in_progress
-- failure or resumes the commit on finalizing failure (so the audit
-- replay path is never needed in pending mode).
--
-- Overlay semantics: ``db_load_history_snapshot`` joins the live tables
-- with the per-session pending overlay using ``MAX(Seq)`` so callers see
-- a consistent "committed live + this session's tentative writes" view.
-- Other sessions' pending rows are intentionally invisible to avoid the
-- dirty-read window that derived-field recomputation
-- (PerfectMatchIndicator / HiResIndicator) is sensitive to.
-- Seq is application-generated (51-bit snowflake from
-- ``_generate_session_id``).  AUTOINCREMENT is intentionally absent: the
-- only writer (``db_stage_history_write``) supplies Seq explicitly, and
-- under STORAGE_BACKEND=dual any forgotten Seq would let SQLite silently
-- emit small ints (1, 2, 3...) that would diverge from D1 and trip
-- ``DualWriteIdMismatchError`` only after the bad row has been written.
-- Removing AUTOINCREMENT makes the omitted-Seq case a hard NULL constraint
-- failure at INSERT time instead of catching it later via drift detection.
CREATE TABLE IF NOT EXISTS PendingMovieHistoryWrites (
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
CREATE INDEX IF NOT EXISTS idx_pmhw_session ON PendingMovieHistoryWrites(SessionId);
CREATE INDEX IF NOT EXISTS idx_pmhw_run ON PendingMovieHistoryWrites(RunId, RunAttempt);
CREATE INDEX IF NOT EXISTS idx_pmhw_href ON PendingMovieHistoryWrites(Href);
CREATE INDEX IF NOT EXISTS idx_pmhw_session_state
    ON PendingMovieHistoryWrites(SessionId, ApplyState);

CREATE TABLE IF NOT EXISTS PendingTorrentHistoryWrites (
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
CREATE INDEX IF NOT EXISTS idx_pthw_session ON PendingTorrentHistoryWrites(SessionId);
CREATE INDEX IF NOT EXISTS idx_pthw_run ON PendingTorrentHistoryWrites(RunId, RunAttempt);
CREATE INDEX IF NOT EXISTS idx_pthw_href ON PendingTorrentHistoryWrites(Href);
CREATE INDEX IF NOT EXISTS idx_pthw_session_state
    ON PendingTorrentHistoryWrites(SessionId, ApplyState);
"""

_REPORTS_DDL = _SCHEMA_VERSION_DDL + """
-- ReportSessions.Id is a TEXT PRIMARY KEY supplied explicitly by the
-- application via :func:`_generate_session_id`.  TEXT (rather than INTEGER)
-- so the id round-trips losslessly through Cloudflare D1's JSON layer,
-- whose IEEE-754 Number representation truncates integers > 2**53 - 1
-- (2026-05-13 — see javdb/migrations/d1/2026_05_13_session_id_to_text.sql).
-- AUTOINCREMENT was retired on 2026-05-08 to fix sqlite-vs-D1 lastrowid
-- drift under STORAGE_BACKEND=dual.
--
-- (RunId, RunAttempt) carry the GitHub Actions workflow run identity so
-- the rollback CLI can locate sessions by run rather than relying solely
-- on the application-generated Id.  FailureReason is set by the rollback
-- CLI alongside Status='failed' to capture *why* a session was unwound
-- (timeout, workflow_cancel, runtime error, ...).
CREATE TABLE IF NOT EXISTS ReportSessions (
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
    -- Ingestion Perfect Rollback: only ``pending`` is supported after
    -- ADR-005 retired the legacy audit-replay path.  Keep the schema
    -- default aligned with ``db_session._resolve_write_mode()`` so
    -- direct SQL / old tools do not silently create audit sessions.
    WriteMode TEXT DEFAULT 'pending'
);
CREATE INDEX IF NOT EXISTS idx_report_sessions_type_date ON ReportSessions(ReportType, ReportDate);
CREATE INDEX IF NOT EXISTS idx_report_sessions_write_mode ON ReportSessions(WriteMode, Status);
CREATE INDEX IF NOT EXISTS idx_report_sessions_csv ON ReportSessions(CsvFilename);
CREATE INDEX IF NOT EXISTS idx_report_sessions_status ON ReportSessions(Status, DateTimeCreated);
CREATE INDEX IF NOT EXISTS idx_report_sessions_run ON ReportSessions(RunId, RunAttempt);
-- Partial UNIQUE index (added 2026-05-08 evening) — schema-level invariant
-- "no two in-progress sessions share the same (RunId, RunAttempt, CsvFilename)".
-- Already-resolved (committed/failed) sessions are intentionally excluded so
-- the same CSV can be re-ingested in a later attempt.  Legacy rows where
-- RunId IS NULL are also excluded to keep the migration backwards compatible.
-- This is the real defence against dual-write lastrowid drift / spider
-- re-entry double-INSERT — the application-layer self-check is now
-- defence-in-depth on top of this DB-enforced invariant.
CREATE UNIQUE INDEX IF NOT EXISTS uq_reportsessions_runidentity_csv
    ON ReportSessions(RunId, RunAttempt, CsvFilename)
    WHERE Status = 'in_progress' AND RunId IS NOT NULL;

CREATE TABLE IF NOT EXISTS ReportMovies (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    SessionId TEXT NOT NULL REFERENCES ReportSessions(Id),
    Href TEXT,
    VideoCode TEXT,
    Page INTEGER,
    Actor TEXT,
    Rate REAL,
    CommentNumber INTEGER
);
CREATE INDEX IF NOT EXISTS idx_report_movies_session ON ReportMovies(SessionId);
CREATE INDEX IF NOT EXISTS idx_report_movies_video_code ON ReportMovies(VideoCode);

CREATE TABLE IF NOT EXISTS ReportTorrents (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    ReportMovieId INTEGER NOT NULL REFERENCES ReportMovies(Id),
    VideoCode TEXT,
    MagnetUri TEXT,
    SubtitleIndicator INTEGER,
    CensorIndicator INTEGER,
    ResolutionType INTEGER,
    Size TEXT,
    FileCount INTEGER
);
CREATE INDEX IF NOT EXISTS idx_report_torrents_movie ON ReportTorrents(ReportMovieId);
CREATE INDEX IF NOT EXISTS idx_report_torrents_video_code ON ReportTorrents(VideoCode);

CREATE TABLE IF NOT EXISTS SpiderStats (
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

CREATE TABLE IF NOT EXISTS UploaderStats (
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

CREATE TABLE IF NOT EXISTS PikpakStats (
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

-- P1: Per-session stats rows must be unique per SessionId. Without a
-- UNIQUE index the legacy `db_save_*` paths' plain INSERT silently
-- duplicates rows whenever a retry/re-run hits the same SessionId,
-- producing the "SpiderStats -1" type drift between SQLite and D1.
-- ``CREATE UNIQUE INDEX IF NOT EXISTS`` is non-destructive: it
-- succeeds when the table is already clean and raises a clear error
-- (caught by the migration) if duplicates exist so the operator can
-- dedupe before re-running.
CREATE UNIQUE INDEX IF NOT EXISTS uq_spiderstats_session
    ON SpiderStats(SessionId);
CREATE UNIQUE INDEX IF NOT EXISTS uq_uploaderstats_session
    ON UploaderStats(SessionId);
CREATE UNIQUE INDEX IF NOT EXISTS uq_pikpakstats_session
    ON PikpakStats(SessionId);
"""

_OPERATIONS_DDL = _SCHEMA_VERSION_DDL + """
CREATE TABLE IF NOT EXISTS RcloneInventory (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    VideoCode TEXT NOT NULL,
    SensorCategory TEXT,
    SubtitleCategory TEXT,
    FolderPath TEXT,
    FolderSize INTEGER,
    FileCount INTEGER,
    DateTimeScanned TEXT
);
CREATE INDEX IF NOT EXISTS idx_rclone_inventory_video_code ON RcloneInventory(VideoCode);

CREATE TABLE IF NOT EXISTS DedupRecords (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    VideoCode TEXT,
    ExistingSensor TEXT,
    ExistingSubtitle TEXT,
    ExistingGdrivePath TEXT,
    ExistingFolderSize INTEGER,
    NewTorrentCategory TEXT,
    DeletionReason TEXT,
    DateTimeDetected TEXT,
    IsDeleted INTEGER,
    DateTimeDeleted TEXT,
    SessionId TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_dedup_active_path
    ON DedupRecords(ExistingGdrivePath)
    WHERE IsDeleted = 0 AND ExistingGdrivePath != '';
CREATE INDEX IF NOT EXISTS idx_dedup_records_session ON DedupRecords(SessionId);

CREATE TABLE IF NOT EXISTS PikpakHistory (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    TorrentHash TEXT,
    TorrentName TEXT,
    Category TEXT,
    MagnetUri TEXT,
    DateTimeAddedToQb TEXT,
    DateTimeDeletedFromQb TEXT,
    DateTimeUploadedToPikpak TEXT,
    TransferStatus TEXT,
    ErrorMessage TEXT,
    SessionId TEXT
);
CREATE INDEX IF NOT EXISTS idx_pikpak_history_session ON PikpakHistory(SessionId);

CREATE TABLE IF NOT EXISTS InventoryAlignNoExactMatch (
    VideoCode TEXT PRIMARY KEY,
    Reason TEXT,
    DateTimeRecorded TEXT,
    SessionId TEXT
);
CREATE INDEX IF NOT EXISTS idx_align_no_match_session ON InventoryAlignNoExactMatch(SessionId);

CREATE TABLE IF NOT EXISTS system_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_system_state_updated_at ON system_state(updated_at);

CREATE TABLE IF NOT EXISTS EmailNotificationHistory (
    Id              INTEGER PRIMARY KEY AUTOINCREMENT,
    SessionId       TEXT,
    Recipient       TEXT NOT NULL,
    Subject         TEXT NOT NULL,
    Status          TEXT NOT NULL DEFAULT 'sent' CHECK (Status IN ('sent', 'failed', 'resent')),
    ErrorMessage    TEXT,
    AttachmentNames TEXT,
    SentAt          TEXT NOT NULL,
    ResentAt        TEXT,
    CreatedBy       TEXT DEFAULT 'pipeline' CHECK (CreatedBy IN ('pipeline', 'manual', 'resend'))
);
CREATE INDEX IF NOT EXISTS idx_email_history_session ON EmailNotificationHistory(SessionId);
CREATE INDEX IF NOT EXISTS idx_email_history_status ON EmailNotificationHistory(Status);
"""

# Combined DDL for single-DB mode (backward compat, csv_to_sqlite, testing)
_TABLES_SQL = _HISTORY_DDL + _REPORTS_DDL + _OPERATIONS_DDL


# ── Migration helpers (private) ─────────────────────────────────────────


def _has_table(conn, name: str) -> bool:
    """Check if a table exists in the database."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _migrate_v5_to_v6(conn):
    """Migrate from schema v5 (or earlier) to v6.

    Handles:
    - parsed_movies_history → MovieHistory + TorrentHistory
    - report_rows → ReportMovies + ReportTorrents
    - All other tables: column rename to BigCamelCase
    - PerfectMatchIndicator / HiResIndicator computation
    """
    logger.info("Starting schema migration v5 → v6 ...")

    # Ensure v4→v5 size columns exist on old tables before migration
    if _has_table(conn, 'parsed_movies_history'):
        for col in ('size_hacked_subtitle', 'size_hacked_no_subtitle',
                     'size_subtitle', 'size_no_subtitle'):
            try:
                conn.execute(
                    f"ALTER TABLE parsed_movies_history ADD COLUMN {col} TEXT"
                )
            except sqlite3.OperationalError:
                pass

    # Create all new tables (executescript creates them via _TABLES_SQL already)

    # ── Step 1: parsed_movies_history → MovieHistory + TorrentHistory ──
    if _has_table(conn, 'parsed_movies_history'):
        conn.execute("""
            INSERT OR IGNORE INTO MovieHistory (VideoCode, Href, DateTimeCreated,
                DateTimeUpdated, DateTimeVisited)
            SELECT video_code, href, create_datetime, update_datetime,
                last_visited_datetime
            FROM parsed_movies_history
        """)
        _CATS = [
            ('hacked_subtitle',    'size_hacked_subtitle',    1, 0),
            ('hacked_no_subtitle', 'size_hacked_no_subtitle', 0, 0),
            ('subtitle',           'size_subtitle',           1, 1),
            ('no_subtitle',        'size_no_subtitle',        0, 1),
        ]
        for cat, size_cat, sub_ind, cen_ind in _CATS:
            conn.execute(f"""
                INSERT OR IGNORE INTO TorrentHistory
                    (MovieHistoryId, MagnetUri, SubtitleIndicator, CensorIndicator,
                     Size, DateTimeCreated, DateTimeUpdated)
                SELECT m.Id,
                       CASE WHEN h.{cat} LIKE '[%]%'
                            THEN SUBSTR(h.{cat}, INSTR(h.{cat}, ']') + 1)
                            ELSE h.{cat} END,
                       {sub_ind}, {cen_ind},
                       COALESCE(h.{size_cat}, ''),
                       CASE WHEN h.{cat} LIKE '[%]%'
                            THEN SUBSTR(h.{cat}, 2, INSTR(h.{cat}, ']') - 2)
                            ELSE h.create_datetime END,
                       h.update_datetime
                FROM parsed_movies_history h
                JOIN MovieHistory m ON m.Href = h.href
                WHERE h.{cat} != ''
                  AND h.{cat} LIKE '%magnet:%'
            """)

        # Compute PerfectMatchIndicator
        conn.execute("""
            UPDATE MovieHistory SET PerfectMatchIndicator = 1
            WHERE Id IN (
                SELECT t1.MovieHistoryId
                FROM TorrentHistory t1
                JOIN TorrentHistory t2 ON t1.MovieHistoryId = t2.MovieHistoryId
                WHERE t1.SubtitleIndicator = 1 AND t1.CensorIndicator = 0
                  AND t2.SubtitleIndicator = 1 AND t2.CensorIndicator = 1
            )
        """)
        # HiResIndicator stays 0 since old data has no ResolutionType

        conn.execute("DROP TABLE parsed_movies_history")
        logger.info("Migrated parsed_movies_history → MovieHistory + TorrentHistory")

    # ── Step 2: rclone_inventory → RcloneInventory ──
    if _has_table(conn, 'rclone_inventory'):
        conn.execute("""
            INSERT INTO RcloneInventory (VideoCode, SensorCategory, SubtitleCategory,
                FolderPath, FolderSize, FileCount, DateTimeScanned)
            SELECT video_code, sensor_category, subtitle_category,
                folder_path, folder_size, file_count, scan_datetime
            FROM rclone_inventory
        """)
        conn.execute("DROP TABLE rclone_inventory")
        logger.info("Migrated rclone_inventory → RcloneInventory")

    # ── Step 3: dedup_records → DedupRecords ──
    if _has_table(conn, 'dedup_records'):
        conn.execute("""
            INSERT INTO DedupRecords (VideoCode, ExistingSensor, ExistingSubtitle,
                ExistingGdrivePath, ExistingFolderSize, NewTorrentCategory,
                DeletionReason, DateTimeDetected, IsDeleted, DateTimeDeleted)
            SELECT video_code, existing_sensor, existing_subtitle,
                existing_gdrive_path, existing_folder_size, new_torrent_category,
                deletion_reason, detect_datetime, is_deleted, delete_datetime
            FROM dedup_records
        """)
        conn.execute("DROP TABLE dedup_records")
        logger.info("Migrated dedup_records → DedupRecords")

    # ── Step 4: pikpak_history → PikpakHistory ──
    if _has_table(conn, 'pikpak_history'):
        conn.execute("""
            INSERT INTO PikpakHistory (TorrentHash, TorrentName, Category, MagnetUri,
                DateTimeAddedToQb, DateTimeDeletedFromQb, DateTimeUploadedToPikpak,
                TransferStatus, ErrorMessage)
            SELECT torrent_hash, torrent_name, category, magnet_uri,
                added_to_qb_date, deleted_from_qb_date, uploaded_to_pikpak_date,
                transfer_status, error_message
            FROM pikpak_history
        """)
        conn.execute("DROP TABLE pikpak_history")
        logger.info("Migrated pikpak_history → PikpakHistory")

    # ── Step 5: proxy_bans → dropped (no longer persisted) ──
    if _has_table(conn, 'proxy_bans'):
        conn.execute("DROP TABLE proxy_bans")
        logger.info("Dropped legacy proxy_bans table (proxy bans are now session-scoped)")

    session_map: dict[int, int] = {}

    # ── Step 6: report_sessions → ReportSessions ──
    # ``Id`` is supplied explicitly as the stringified legacy ``id`` so the
    # session_map below maps cleanly to the new PK. ReportSessions.Id is
    # TEXT post-2026-05-13 (no AUTOINCREMENT to fall back on).
    if _has_table(conn, 'report_sessions'):
        conn.execute("""
            INSERT INTO ReportSessions (Id, ReportType, ReportDate, UrlType, DisplayName,
                Url, StartPage, EndPage, CsvFilename, DateTimeCreated)
            SELECT CAST(id AS TEXT), report_type, report_date, url_type, display_name,
                url, start_page, end_page, csv_filename, created_at
            FROM report_sessions
        """)

    # ── Step 7: report_rows → ReportMovies + ReportTorrents ──
    if _has_table(conn, 'report_rows') and _has_table(conn, 'report_sessions'):
        # Build old→new session id mapping
        mapping_rows = conn.execute("""
            SELECT rs_old.id AS old_id, rs_new.Id AS new_id
            FROM report_sessions rs_old
            JOIN ReportSessions rs_new
                ON rs_new.CsvFilename = rs_old.csv_filename
               AND rs_new.DateTimeCreated = rs_old.created_at
        """).fetchall()
        session_map = {r['old_id']: r['new_id'] for r in mapping_rows}

        old_rows = conn.execute("SELECT * FROM report_rows ORDER BY id").fetchall()
        for r in old_rows:
            r = dict(r)
            new_session_id = session_map.get(r['session_id'])
            if new_session_id is None:
                continue
            cur = conn.execute(
                """INSERT INTO ReportMovies (SessionId, Href, VideoCode, Page,
                    Actor, Rate, CommentNumber)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (new_session_id, r.get('href', ''), r.get('video_code', ''),
                 r.get('page'), r.get('actor', ''), r.get('rate'),
                 r.get('comment_number')),
            )
            rm_id = cur.lastrowid
            vc = r.get('video_code', '')
            _REPORT_CATS = [
                ('hacked_subtitle',    'size_hacked_subtitle',    1, 0),
                ('hacked_no_subtitle', 'size_hacked_no_subtitle', 0, 0),
                ('subtitle',           'size_subtitle',           1, 1),
                ('no_subtitle',        'size_no_subtitle',        0, 1),
            ]
            for cat, size_cat, sub_ind, cen_ind in _REPORT_CATS:
                magnet = (r.get(cat) or '').strip()
                if magnet:
                    conn.execute(
                        """INSERT INTO ReportTorrents (ReportMovieId, VideoCode,
                            MagnetUri, SubtitleIndicator, CensorIndicator, Size)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (rm_id, vc, magnet, sub_ind, cen_ind,
                         (r.get(size_cat) or '')),
                    )

        conn.execute("DROP TABLE report_rows")
        logger.info("Migrated report_rows → ReportMovies + ReportTorrents")

    # ── Step 8: spider_stats → SpiderStats ──
    if _has_table(conn, 'spider_stats'):
        rows = conn.execute("SELECT * FROM spider_stats ORDER BY id").fetchall()
        for r in rows:
            r = dict(r)
            new_sid = session_map.get(r['session_id'])
            if new_sid is None:
                continue
            conn.execute(
                """INSERT INTO SpiderStats (SessionId,
                    Phase1Discovered, Phase1Processed, Phase1Skipped,
                    Phase1NoNew, Phase1Failed,
                    Phase2Discovered, Phase2Processed, Phase2Skipped,
                    Phase2NoNew, Phase2Failed,
                    TotalDiscovered, TotalProcessed, TotalSkipped,
                    TotalNoNew, TotalFailed, DateTimeCreated)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (new_sid,
                 r.get('phase1_discovered', 0), r.get('phase1_processed', 0),
                 r.get('phase1_skipped', 0), r.get('phase1_no_new', 0),
                 r.get('phase1_failed', 0),
                 r.get('phase2_discovered', 0), r.get('phase2_processed', 0),
                 r.get('phase2_skipped', 0), r.get('phase2_no_new', 0),
                 r.get('phase2_failed', 0),
                 r.get('total_discovered', 0), r.get('total_processed', 0),
                 r.get('total_skipped', 0), r.get('total_no_new', 0),
                 r.get('total_failed', 0),
                 r.get('created_at', '')),
            )
        conn.execute("DROP TABLE spider_stats")
        logger.info("Migrated spider_stats → SpiderStats")

    # ── Step 9: uploader_stats → UploaderStats ──
    if _has_table(conn, 'uploader_stats'):
        rows = conn.execute("SELECT * FROM uploader_stats ORDER BY id").fetchall()
        for r in rows:
            r = dict(r)
            new_sid = session_map.get(r['session_id'])
            if new_sid is None:
                continue
            conn.execute(
                """INSERT INTO UploaderStats (SessionId, TotalTorrents, DuplicateCount,
                    Attempted, SuccessfullyAdded, FailedCount, HackedSub, HackedNosub,
                    SubtitleCount, NoSubtitleCount, SuccessRate, DateTimeCreated)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (new_sid,
                 r.get('total_torrents', 0), r.get('duplicate_count', 0),
                 r.get('attempted', 0), r.get('successfully_added', 0),
                 r.get('failed_count', 0), r.get('hacked_sub', 0),
                 r.get('hacked_nosub', 0), r.get('subtitle_count', 0),
                 r.get('no_subtitle_count', 0), r.get('success_rate', 0.0),
                 r.get('created_at', '')),
            )
        conn.execute("DROP TABLE uploader_stats")
        logger.info("Migrated uploader_stats → UploaderStats")

    # ── Step 10: pikpak_stats → PikpakStats ──
    if _has_table(conn, 'pikpak_stats'):
        rows = conn.execute("SELECT * FROM pikpak_stats ORDER BY id").fetchall()
        for r in rows:
            r = dict(r)
            new_sid = session_map.get(r['session_id'])
            if new_sid is None:
                continue
            conn.execute(
                """INSERT INTO PikpakStats (SessionId, ThresholdDays, TotalTorrents,
                    FilteredOld, SuccessfulCount, FailedCount, UploadedCount,
                    DeleteFailedCount, DateTimeCreated)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (new_sid,
                 r.get('threshold_days', 3), r.get('total_torrents', 0),
                 r.get('filtered_old', 0), r.get('successful_count', 0),
                 r.get('failed_count', 0),
                 r.get('uploaded_count', r.get('successful_count', 0)),
                 r.get('delete_failed_count', 0),
                 r.get('created_at', '')),
            )
        conn.execute("DROP TABLE pikpak_stats")
        logger.info("Migrated pikpak_stats → PikpakStats")

    # ── Cleanup: drop old report_sessions (after stats tables that reference it) ──
    if _has_table(conn, 'report_sessions'):
        conn.execute("DROP TABLE report_sessions")

    if _has_table(conn, 'schema_version'):
        conn.execute("DROP TABLE schema_version")

    logger.info("Schema migration v5 → v6 complete")


def _ensure_moviehistory_actor_columns(conn: sqlite3.Connection) -> None:
    """Add actor-related columns to MovieHistory when missing (v7 → v8 → v9).

    Storage order must match ``_HISTORY_DDL``: ActorName, ActorGender, ActorLink,
    SupportingActors (Gender between Name and Link; supporting cast after Link).
    """
    if not _has_table(conn, 'MovieHistory'):
        return
    try:
        conn.execute("ALTER TABLE MovieHistory ADD COLUMN ActorName TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE MovieHistory ADD COLUMN ActorGender TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE MovieHistory ADD COLUMN ActorLink TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE MovieHistory ADD COLUMN SupportingActors TEXT")
    except sqlite3.OperationalError:
        pass


def _moviehistory_actor_column_names(conn: sqlite3.Connection) -> List[str]:
    rows = conn.execute("PRAGMA table_info(MovieHistory)").fetchall()
    return [r[1] for r in rows]


def _moviehistory_actor_columns_all_present(names: List[str]) -> bool:
    req = frozenset(
        ("ActorName", "ActorGender", "ActorLink", "SupportingActors"),
    )
    return req.issubset(set(names))


def _moviehistory_actor_columns_physical_order_ok(names: List[str]) -> bool:
    """True iff the four actor columns appear in storage order: Name < Gender < Link < Supporting."""
    if not _moviehistory_actor_columns_all_present(names):
        return False
    idx = {k: names.index(k) for k in ("ActorName", "ActorGender", "ActorLink", "SupportingActors")}
    return (
        idx["ActorName"]
        < idx["ActorGender"]
        < idx["ActorLink"]
        < idx["SupportingActors"]
    )


def _ensure_rollback_columns(conn: sqlite3.Connection) -> None:
    """Add Status/SessionId columns and pending tables for rollback (idempotent).

    Adds:
      - ReportSessions.Status TEXT DEFAULT 'in_progress'
      - ReportSessions.RunId, ReportSessions.RunAttempt,
        ReportSessions.FailureReason  (added 2026-05-08; identifies the
        owning GitHub Actions workflow run and stores rollback context)
      - MovieHistory.SessionId, TorrentHistory.SessionId
      - PikpakHistory.SessionId, DedupRecords.SessionId,
        InventoryAlignNoExactMatch.SessionId

    This handles existing databases that were created before the rollback
    schema. New DBs are created with the columns directly via the DDL
    constants in ``_HISTORY_DDL`` / ``_REPORTS_DDL`` / ``_OPERATIONS_DDL``,
    so the ALTER calls below silently no-op.
    """
    add_column_specs = [
        ('ReportSessions', 'Status', "TEXT DEFAULT 'in_progress'"),
        ('ReportSessions', 'RunId', 'TEXT'),
        ('ReportSessions', 'RunAttempt', 'INTEGER'),
        ('ReportSessions', 'FailureReason', 'TEXT'),
        ('MovieHistory', 'SessionId', 'TEXT'),
        ('TorrentHistory', 'SessionId', 'TEXT'),
        ('PikpakHistory', 'SessionId', 'TEXT'),
        ('DedupRecords', 'SessionId', 'TEXT'),
        ('InventoryAlignNoExactMatch', 'SessionId', 'TEXT'),
        # Ingestion Perfect Rollback (Phase 0): WriteMode column on
        # ReportSessions, gating the pending dispatch.
        ('ReportSessions', 'WriteMode', "TEXT DEFAULT 'pending'"),
    ]
    for table, column, ddl in add_column_specs:
        if not _has_table(conn, table):
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        except sqlite3.OperationalError:
            # Column already exists; ALTER raises "duplicate column name" — fine.
            pass

    pending_ddl = (
        """
        CREATE TABLE IF NOT EXISTS PendingMovieHistoryWrites (
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
        CREATE INDEX IF NOT EXISTS idx_pmhw_session
            ON PendingMovieHistoryWrites(SessionId);
        CREATE INDEX IF NOT EXISTS idx_pmhw_run
            ON PendingMovieHistoryWrites(RunId, RunAttempt);
        CREATE INDEX IF NOT EXISTS idx_pmhw_href
            ON PendingMovieHistoryWrites(Href);
        CREATE INDEX IF NOT EXISTS idx_pmhw_session_state
            ON PendingMovieHistoryWrites(SessionId, ApplyState);
        CREATE TABLE IF NOT EXISTS PendingTorrentHistoryWrites (
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
        CREATE INDEX IF NOT EXISTS idx_pthw_session
            ON PendingTorrentHistoryWrites(SessionId);
        CREATE INDEX IF NOT EXISTS idx_pthw_run
            ON PendingTorrentHistoryWrites(RunId, RunAttempt);
        CREATE INDEX IF NOT EXISTS idx_pthw_href
            ON PendingTorrentHistoryWrites(Href);
        CREATE INDEX IF NOT EXISTS idx_pthw_session_state
            ON PendingTorrentHistoryWrites(SessionId, ApplyState);
        """
    )
    if _has_table(conn, 'MovieHistory'):
        conn.executescript(pending_ddl)

    extra_indexes = [
        ('idx_movie_history_session', 'MovieHistory', 'SessionId'),
        ('idx_torrent_history_session', 'TorrentHistory', 'SessionId'),
        ('idx_report_sessions_status',
         'ReportSessions', 'Status, DateTimeCreated'),
        ('idx_report_sessions_run',
         'ReportSessions', 'RunId, RunAttempt'),
        ('idx_pikpak_history_session', 'PikpakHistory', 'SessionId'),
        ('idx_dedup_records_session', 'DedupRecords', 'SessionId'),
        ('idx_align_no_match_session',
         'InventoryAlignNoExactMatch', 'SessionId'),
    ]
    for idx_name, table, columns in extra_indexes:
        if not _has_table(conn, table):
            continue
        try:
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table}({columns})"
            )
        except sqlite3.OperationalError:
            pass

    # Partial UNIQUE index — DB-level invariant against same-CSV double-INSERT
    # in the same workflow run.  Only enforced for in_progress + RunId NOT NULL
    # so legacy rows and resolved sessions remain free.
    if _has_table(conn, 'ReportSessions'):
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "uq_reportsessions_runidentity_csv "
                "ON ReportSessions(RunId, RunAttempt, CsvFilename) "
                "WHERE Status = 'in_progress' AND RunId IS NOT NULL"
            )
        except sqlite3.OperationalError:
            # Older SQLite without partial index support — should never
            # happen for our minimum (3.8+) but stay defensive.
            pass


def _materialize_report_session_status_default(conn: sqlite3.Connection) -> None:
    """Persist the rollback Status default before default-stripping migrations."""
    if not _has_table(conn, 'ReportSessions'):
        return
    try:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(ReportSessions)")
        }
        if 'Status' in columns:
            conn.execute(
                "UPDATE ReportSessions "
                "SET Status='in_progress' WHERE Status IS NULL"
            )
        if 'WriteMode' in columns:
            conn.execute(
                "UPDATE ReportSessions "
                "SET WriteMode='pending' WHERE WriteMode IS NULL"
            )
    except sqlite3.OperationalError:
        pass


def _normalize_moviehistory_actor_column_order(conn: sqlite3.Connection) -> None:
    """Rebuild MovieHistory if actor columns were added in a non-canonical order (legacy ALTER)."""
    if not _has_table(conn, 'MovieHistory'):
        return
    names = _moviehistory_actor_column_names(conn)
    if not _moviehistory_actor_columns_all_present(names):
        return
    if _moviehistory_actor_columns_physical_order_ok(names):
        return
    logger.info(
        "MovieHistory: rebuilding table so columns are ordered "
        "ActorName, ActorGender, ActorLink, SupportingActors (SQLite storage order)",
    )
    session_id_expr = "SessionId" if "SessionId" in names else "NULL"
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.executescript(
            f"""
            CREATE TABLE MovieHistory__colorder (
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
            INSERT INTO MovieHistory__colorder (
                Id, VideoCode, Href, ActorName, ActorGender, ActorLink, SupportingActors,
                DateTimeCreated, DateTimeUpdated, DateTimeVisited,
                PerfectMatchIndicator, HiResIndicator, SessionId
            )
            SELECT Id, VideoCode, Href,
                ActorName, ActorGender, ActorLink,
                SupportingActors,
                DateTimeCreated, DateTimeUpdated, DateTimeVisited,
                PerfectMatchIndicator, HiResIndicator,
                {session_id_expr}
            FROM MovieHistory;
            DROP TABLE MovieHistory;
            ALTER TABLE MovieHistory__colorder RENAME TO MovieHistory;
            CREATE INDEX IF NOT EXISTS idx_movie_history_video_code ON MovieHistory(VideoCode);
            CREATE INDEX IF NOT EXISTS idx_movie_history_session ON MovieHistory(SessionId);
            """
        )
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def moviehistory_actor_layout_ok(conn: sqlite3.Connection) -> bool:
    """True if MovieHistory exists with ActorName, ActorGender, ActorLink, SupportingActors in that storage order."""
    if not _has_table(conn, "MovieHistory"):
        return False
    names = _moviehistory_actor_column_names(conn)
    return _moviehistory_actor_columns_all_present(names) and _moviehistory_actor_columns_physical_order_ok(
        names
    )


_DEFAULT_RE = re.compile(
    r'\s+DEFAULT\s+(?:\'[^\']*\'|\([^()]*(?:\([^()]*\)[^()]*)*\)|\d+(?:\.\d+)?)',
    re.IGNORECASE,
)


def _migrate_defaults_to_null(conn: sqlite3.Connection) -> None:
    """Remove all non-NULL DEFAULT clauses from every table schema (v9 -> v10).

    Uses PRAGMA writable_schema to rewrite CREATE TABLE statements in
    sqlite_master directly — no table rebuild required.
    """
    conn.execute("PRAGMA writable_schema = ON")
    try:
        rows = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL"
        ).fetchall()
        for row in rows:
            name, sql = row[0], row[1]
            new_sql = _DEFAULT_RE.sub('', sql)
            if new_sql != sql:
                conn.execute(
                    "UPDATE sqlite_master SET sql = ? WHERE type='table' AND name = ?",
                    (new_sql, name),
                )
                logger.info(f"Removed DEFAULT clauses from table {name}")
    finally:
        conn.execute("PRAGMA writable_schema = OFF")
    integrity = conn.execute("PRAGMA integrity_check").fetchone()
    if integrity[0] != 'ok':
        logger.warning(f"Integrity check after schema update: {integrity[0]}")


# Regexes for the v11 → v12 type migration. Each matches one declaration
# shape that the canonical DDLs use today (INTEGER variants) and rewrites
# it to TEXT. They're anchored on the column name so unrelated INTEGER
# columns in the same CREATE TABLE statement are not touched.
_V12_SESSION_ID_RE = re.compile(r'\bSessionId\s+INTEGER\b', re.IGNORECASE)
_V12_REPORTSESSIONS_ID_RE = re.compile(
    r'\bId\s+INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b',
    re.IGNORECASE,
)
_V12_PENDING_SEQ_RE = re.compile(
    r'\bSeq\s+INTEGER\s+PRIMARY\s+KEY\s+(?:NOT\s+NULL|AUTOINCREMENT)\b',
    re.IGNORECASE,
)
# Tables whose `Id INTEGER PRIMARY KEY AUTOINCREMENT` is the snowflake PK
# we want as TEXT. All other tables in the schema use `Id` as an internal
# auto-incrementing row counter that we DO want to leave as INTEGER.
_V12_REPORTSESSIONS_TABLES = ("ReportSessions",)


def _migrate_session_id_to_text(conn: sqlite3.Connection) -> None:
    """Rewrite SessionId / ReportSessions.Id / Pending*.Seq columns from
    INTEGER to TEXT (v11 -> v12, re-run at v13 for partial-migration fix).

    Cloudflare D1's HTTP JSON path serializes integers as IEEE-754 doubles
    and silently truncates anything above 2**53 - 1, so the 63-bit
    snowflake Ids diverge between SQLite and D1 the moment they cross the
    wire. The fix is to store the id as TEXT on both backends; this
    migration aligns the local SQLite schema with that decision.

    Strategy:

    * For ``ReportSessions.Id``, ``PendingMovieHistoryWrites.Seq`` and
      ``PendingTorrentHistoryWrites.Seq`` the old declaration was
      ``INTEGER PRIMARY KEY [AUTOINCREMENT]`` — a rowid alias. Changing
      the declared type in-place would leave the actual data stored under
      the rowid (which is now type-mismatched) and corrupt the table, so
      we do a full table rebuild (12-step ALTER).
    * Every other ``SessionId INTEGER`` column is a regular row; SQLite's
      type affinity makes a ``PRAGMA writable_schema`` rewrite safe.

    ``AUTOINCREMENT`` is dropped from ``ReportSessions`` along with the
    type change — :func:`_generate_session_id` has been supplying the id
    explicitly since 2026-05-08, so the autoincrement counter was already
    dead weight.

    v13 fix: the original ``_V12_PENDING_SEQ_RE`` only matched
    ``Seq INTEGER PRIMARY KEY NOT NULL`` but the v9 creation DDL used
    ``AUTOINCREMENT``.  Databases that migrated at v12 had Seq left as
    INTEGER (causing ``datatype mismatch`` on insert).  The regex now
    matches both suffixes and the migration re-runs at v13 to repair
    partially-migrated databases.

    Idempotent: on a fresh DB or a DB already fully at v13 nothing matches.
    """
    rebuild_specs = [
        # (table, old_pk_regex, new_pk_decl, also_change_session_id)
        ("ReportSessions", _V12_REPORTSESSIONS_ID_RE, "Id TEXT PRIMARY KEY", False),
        ("PendingMovieHistoryWrites", _V12_PENDING_SEQ_RE,
         "Seq TEXT PRIMARY KEY NOT NULL", True),
        ("PendingTorrentHistoryWrites", _V12_PENDING_SEQ_RE,
         "Seq TEXT PRIMARY KEY NOT NULL", True),
    ]
    rebuilt: set = set()
    for table, pk_re, _new_pk, _ in rebuild_specs:
        if not _has_table(conn, table):
            continue
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if row is None or not row[0]:
            continue
        old_sql = row[0]
        new_sql = pk_re.sub(_new_pk, old_sql)
        new_sql = _V12_SESSION_ID_RE.sub("SessionId TEXT", new_sql)
        if new_sql == old_sql:
            continue
        _rebuild_table_with_new_ddl(conn, table, new_sql)
        rebuilt.add(table)
        logger.info("v12 type migration: rebuilt table %s", table)

    # All other tables with a SessionId INTEGER column — rebuild them using the
    # same 12-step ALTER so that existing rows get their values coerced to TEXT
    # storage class via INSERT ... SELECT with TEXT-affinity destination columns.
    # (PRAGMA writable_schema would only change the declared type in
    # sqlite_master; the in-connection schema cache wouldn't refresh until
    # reconnect, causing subsequent UPDATEs to revert values back to INTEGER.)
    schema_changed = False
    table_rows = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL"
    ).fetchall()
    for name, sql in table_rows:
        if name in rebuilt:
            continue
        new_sql = _V12_SESSION_ID_RE.sub("SessionId TEXT", sql)
        if new_sql != sql:
            _rebuild_table_with_new_ddl(conn, name, new_sql)
            rebuilt.add(name)
            logger.info("v12 type migration: rebuilt table %s", name)
            schema_changed = True

    if rebuilt or schema_changed:
        conn.commit()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()
        if integrity[0] != "ok":
            logger.warning(
                "v12 type migration integrity check: %s", integrity[0],
            )


def _rebuild_table_with_new_ddl(
    conn: sqlite3.Connection,
    table: str,
    new_ddl: str,
) -> None:
    """12-step ALTER: rebuild *table* using *new_ddl* and copy all rows.

    Reads existing column names from ``PRAGMA table_info`` and copies
    them verbatim (no ``CAST``) — SQLite's type affinity handles the
    coercion from old INTEGER values to the new TEXT declaration on read
    (existing data stays bit-for-bit; only future writes pick up TEXT
    affinity). Indexes are reapplied from ``sqlite_master`` after the
    rename.
    """
    info = conn.execute(f"PRAGMA table_info({table})").fetchall()
    columns = [r[1] for r in info]
    cols_csv = ", ".join(columns)
    tmp_name = f"{table}__v12_new"

    # Capture indexes (non-PK / non-UNIQUE-implicit) so we can recreate
    # them on the rebuilt table.
    idx_rows = conn.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
        (table,),
    ).fetchall()

    # Swap *table* -> *tmp_name* in the DDL so the new CREATE doesn't
    # collide with the existing one.  SQLite may store the table name with
    # or without double-quote delimiters (e.g. `"MovieHistory"` vs
    # `MovieHistory`); the pattern handles both forms.
    create_new = re.sub(
        rf'\bCREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?"?{re.escape(table)}"?(?=\s*\()',
        f"CREATE TABLE {tmp_name}",
        new_ddl,
        count=1,
        flags=re.IGNORECASE,
    )

    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute(create_new)
        conn.execute(
            f"INSERT INTO {tmp_name} ({cols_csv}) "
            f"SELECT {cols_csv} FROM {table}"
        )
        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"ALTER TABLE {tmp_name} RENAME TO {table}")
        for _idx_name, idx_sql in idx_rows:
            try:
                conn.execute(idx_sql)
            except sqlite3.OperationalError:
                # Indexes whose name SQLite auto-generates for UNIQUE
                # constraints get recreated implicitly with the table;
                # the explicit CREATE INDEX would then collide. Ignore
                # those — they're already in place.
                pass
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _migrate_v14_drop_audit_tables(conn: sqlite3.Connection) -> None:
    """v14: drop MovieHistoryAudit / TorrentHistoryAudit per ADR-005."""
    conn.execute("DROP TABLE IF EXISTS MovieHistoryAudit")
    conn.execute("DROP TABLE IF EXISTS TorrentHistoryAudit")
    logger.info("v14 migration: dropped audit tables")


def _dedupe_session_keyed_stats_rows(conn: sqlite3.Connection) -> None:
    """Collapse duplicate ``(SessionId)`` rows in per-session stats tables.

    Each ``db_save_*_stats`` historically used a plain ``INSERT`` and
    therefore produced multiple rows whenever a retry/re-run landed on
    the same SessionId. The DDL below now adds ``UNIQUE(SessionId)``;
    creating that index against a table with existing duplicates would
    raise. This helper runs *before* the DDL and keeps only the row
    with the largest ``Id`` (the most recent write) per session.

    Idempotent: on a fresh DB or a DB that's already clean this is a
    no-op. Logs the count of rows removed so an operator inspecting
    the init log sees what happened on first migration.
    """
    for table in ("SpiderStats", "UploaderStats", "PikpakStats"):
        if not _has_table(conn, table):
            continue
        try:
            cur = conn.execute(
                f"DELETE FROM {table} WHERE Id NOT IN ("
                f"SELECT MAX(Id) FROM {table} GROUP BY SessionId"
                f")"
            )
        except sqlite3.OperationalError as exc:
            # Table missing the Id column on extremely old schemas — skip.
            logger.warning(
                "_dedupe_session_keyed_stats_rows: skipped %s (%s)",
                table, exc,
            )
            continue
        removed = cur.rowcount or 0
        if removed > 0:
            logger.info(
                "_dedupe_session_keyed_stats_rows: removed %d duplicate "
                "row(s) from %s before unique-index migration",
                removed, table,
            )


# ── Version detection ───────────────────────────────────────────────────


def _detect_version(conn) -> int:
    """Read schema version from whichever version table exists."""
    if _has_table(conn, 'schema_version'):
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        return row[0] if row else 0
    if _has_table(conn, 'SchemaVersion'):
        row = conn.execute("SELECT Version FROM SchemaVersion LIMIT 1").fetchone()
        return row[0] if row else 0
    return 0


# ── Cross-DB backfill / migration helpers ───────────────────────────────


def _backfill_torrent_sizes_after_split(history_db: str, reports_db: str):
    """Backfill empty TorrentHistory.Size from ReportTorrents.Size.

    Uses ATTACH to join across history.db and reports.db.  Only updates
    rows where Size is NULL or empty, picking the most recent matching
    ReportTorrents entry.
    """
    try:
        conn = sqlite3.connect(history_db)
        conn.row_factory = sqlite3.Row
        conn.execute("ATTACH DATABASE ? AS rpt", (reports_db,))
        cur = conn.execute("""
            UPDATE TorrentHistory
            SET Size = (
                SELECT rt.Size
                FROM rpt.ReportTorrents rt
                JOIN rpt.ReportMovies rm ON rt.ReportMovieId = rm.Id
                JOIN MovieHistory mh ON rm.Href = mh.Href
                WHERE mh.Id = TorrentHistory.MovieHistoryId
                  AND rt.SubtitleIndicator = TorrentHistory.SubtitleIndicator
                  AND rt.CensorIndicator = TorrentHistory.CensorIndicator
                  AND rt.Size IS NOT NULL AND rt.Size != ''
                ORDER BY rt.Id DESC
                LIMIT 1
            )
            WHERE (TorrentHistory.Size IS NULL OR TorrentHistory.Size = '')
              AND EXISTS (
                SELECT 1
                FROM rpt.ReportTorrents rt
                JOIN rpt.ReportMovies rm ON rt.ReportMovieId = rm.Id
                JOIN MovieHistory mh ON rm.Href = mh.Href
                WHERE mh.Id = TorrentHistory.MovieHistoryId
                  AND rt.SubtitleIndicator = TorrentHistory.SubtitleIndicator
                  AND rt.CensorIndicator = TorrentHistory.CensorIndicator
                  AND rt.Size IS NOT NULL AND rt.Size != ''
              )
        """)
        updated = cur.rowcount
        conn.commit()
        conn.execute("DETACH DATABASE rpt")
        conn.close()
        if updated > 0:
            logger.info(f"Backfilled {updated} TorrentHistory.Size values from ReportTorrents")
    except Exception as e:
        logger.warning(f"TorrentHistory.Size backfill skipped: {e}")


def _moviehistory_actor_select_exprs_from_attached_old_db(conn: sqlite3.Connection) -> str:
    """SQL expressions for ActorName...SupportingActors when copying ``old_db.MovieHistory``.

    Legacy single DBs may predate some actor columns; missing columns become ``''``.
    Existing values are preserved via ``COALESCE(col, '')``.
    """
    try:
        rows = conn.execute("PRAGMA old_db.table_info(MovieHistory)").fetchall()
    except sqlite3.OperationalError:
        rows = []
    names = {r[1] for r in rows}
    parts: List[str] = []
    for col in ("ActorName", "ActorGender", "ActorLink", "SupportingActors"):
        if col in names:
            parts.append(col)
        else:
            parts.append("NULL")
    return ", ".join(parts)


def _quote_ident(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


def _attached_table_info(
    conn: sqlite3.Connection,
    schema: str,
    table: str,
) -> List[sqlite3.Row]:
    return conn.execute(
        f"PRAGMA {schema}.table_info({_quote_ident(table)})"
    ).fetchall()


def _attached_table_column_names(
    conn: sqlite3.Connection,
    schema: str,
    table: str,
) -> List[str]:
    return [row[1] for row in _attached_table_info(conn, schema, table)]


def _copy_attached_table_by_common_columns(
    conn: sqlite3.Connection,
    table: str,
) -> None:
    """Copy ``old_db.table`` into ``main.table`` using explicit common columns."""
    main_info = _attached_table_info(conn, "main", table)
    old_info = _attached_table_info(conn, "old_db", table)
    if not old_info:
        logger.debug(f"Table {table} not found in old DB, skipping")
        return

    old_names = {row[1] for row in old_info}
    missing_required = [
        row[1]
        for row in main_info
        if row[1] not in old_names
        and row[3]
        and row[4] is None
        and not row[5]
    ]
    if missing_required:
        raise sqlite3.OperationalError(
            f"Table {table} missing required column(s) in old DB: "
            f"{', '.join(missing_required)}"
        )

    columns = [row[1] for row in main_info if row[1] in old_names]
    if not columns:
        raise sqlite3.OperationalError(
            f"Table {table} has no compatible columns in old DB"
        )
    column_sql = ", ".join(_quote_ident(col) for col in columns)
    conn.execute(
        f"INSERT INTO main.{_quote_ident(table)} ({column_sql}) "
        f"SELECT {column_sql} FROM old_db.{_quote_ident(table)}"
    )


def _migrate_single_to_split():
    """Migrate a legacy single-DB (v6) into three separate databases.

    Uses ``ATTACH DATABASE`` to copy tables from the old DB into the
    correct new DB.  The old file is renamed to ``.v6.bak`` on success.
    """
    old_path = DB_PATH
    if not os.path.exists(old_path) or os.path.getsize(old_path) == 0:
        return False
    if not _is_valid_sqlite(old_path):
        return False

    split_exists = [
        os.path.exists(HISTORY_DB_PATH),
        os.path.exists(REPORTS_DB_PATH),
        os.path.exists(OPERATIONS_DB_PATH),
    ]
    if all(split_exists):
        return False
    if any(split_exists):
        # Partial split detected — clean up incomplete files and re-migrate
        logger.warning(
            "Partial DB split detected (legacy DB still present but only "
            "some split DBs exist). Removing incomplete split files and "
            "re-running migration ..."
        )
        for p in (HISTORY_DB_PATH, REPORTS_DB_PATH, OPERATIONS_DB_PATH):
            if os.path.exists(p):
                os.remove(p)
                logger.info(f"  Removed partial split file: {p}")

    # Close any thread-local connections to the old DB before attaching it
    conns: dict = getattr(_local, 'conns', None)
    if conns:
        old_conn = conns.pop(old_path, None)
        if old_conn is not None:
            try:
                old_conn.close()
            except Exception:
                pass

    # Detect version in old DB
    tmp_conn = sqlite3.connect(old_path)
    tmp_conn.row_factory = sqlite3.Row
    old_version = _detect_version(tmp_conn)
    tmp_conn.close()

    if old_version < 6:
        # Need v5→v6 migration first (run on old single DB)
        logger.info("Old single DB is below v6 — running v5→v6 migration first ...")
        with get_db(old_path) as conn:
            conn.executescript(_TABLES_SQL)
            _migrate_v5_to_v6(conn)
            existing = conn.execute("SELECT Version FROM SchemaVersion LIMIT 1").fetchone()
            if existing is None:
                conn.execute("INSERT INTO SchemaVersion (Version) VALUES (6)")
            else:
                conn.execute("UPDATE SchemaVersion SET Version = 6")

    logger.info("Splitting single DB into three databases ...")

    _DB_SPLIT_MAP = [
        (HISTORY_DB_PATH, _HISTORY_DDL, ['MovieHistory', 'TorrentHistory']),
        (REPORTS_DB_PATH, _REPORTS_DDL, [
            'ReportSessions', 'ReportMovies', 'ReportTorrents',
            'SpiderStats', 'UploaderStats', 'PikpakStats',
        ]),
        (OPERATIONS_DB_PATH, _OPERATIONS_DDL, [
            'RcloneInventory', 'DedupRecords', 'PikpakHistory',
            'InventoryAlignNoExactMatch', 'EmailNotificationHistory',
        ]),
    ]

    for new_path, ddl, tables in _DB_SPLIT_MAP:
        os.makedirs(os.path.dirname(new_path) or '.', exist_ok=True)
        new_conn = sqlite3.connect(new_path)
        new_conn.execute("PRAGMA journal_mode=WAL")
        new_conn.execute("PRAGMA foreign_keys=OFF")
        new_conn.executescript(ddl)
        new_conn.execute("ATTACH DATABASE ? AS old_db", (old_path,))
        try:
            new_conn.execute("ALTER TABLE old_db.SpiderStats ADD COLUMN FailedMovies TEXT")
        except sqlite3.OperationalError:
            pass
        for table in tables:
            if not _attached_table_info(new_conn, "old_db", table):
                logger.debug(f"Table {table} not found in old DB, skipping")
                continue
            try:
                if table == 'MovieHistory':
                    actor_exprs = _moviehistory_actor_select_exprs_from_attached_old_db(
                        new_conn,
                    )
                    old_columns = set(
                        _attached_table_column_names(new_conn, "old_db", table)
                    )
                    session_id_expr = (
                        "SessionId" if "SessionId" in old_columns else "NULL"
                    )
                    new_conn.execute(
                        f"""INSERT INTO main.MovieHistory (
                            Id, VideoCode, Href, ActorName, ActorGender, ActorLink, SupportingActors,
                            DateTimeCreated, DateTimeUpdated, DateTimeVisited,
                            PerfectMatchIndicator, HiResIndicator, SessionId)
                        SELECT Id, VideoCode, Href, {actor_exprs},
                               DateTimeCreated, DateTimeUpdated, DateTimeVisited,
                               PerfectMatchIndicator, HiResIndicator, {session_id_expr}
                        FROM old_db.MovieHistory"""
                    )
                else:
                    _copy_attached_table_by_common_columns(new_conn, table)
            except sqlite3.OperationalError as exc:
                logger.error(f"Failed migrating table {table} from old DB: {exc}")
                raise
        new_conn.execute("INSERT OR REPLACE INTO SchemaVersion (Version) VALUES (?)",
                         (SCHEMA_VERSION,))
        new_conn.commit()
        new_conn.execute("DETACH DATABASE old_db")
        new_conn.execute("PRAGMA foreign_keys=ON")
        new_conn.close()
        logger.info(f"  Created {new_path} with tables: {', '.join(tables)}")

    # Backfill TorrentHistory.Size from ReportTorrents.Size
    _backfill_torrent_sizes_after_split(HISTORY_DB_PATH, REPORTS_DB_PATH)

    backup_path = old_path + '.v6.bak'
    os.rename(old_path, backup_path)
    logger.info(f"Old single DB backed up to {backup_path}")
    return True


# ── Init functions ──────────────────────────────────────────────────────


def _init_single_db(db_path: str, ddl: str, *, force: bool = False):
    """Initialise one database file: create tables and set schema version."""
    if not force:
        from javdb.infra.config import use_sqlite
        if not use_sqlite():
            return

    if os.path.exists(db_path) and os.path.getsize(db_path) > 0 and not _is_valid_sqlite(db_path):
        logger.warning(
            f"Database file {db_path} is not a valid SQLite database "
            "(possibly a Git LFS pointer that was not pulled). "
            "Falling back to CSV storage mode for this run."
        )
        from javdb.infra.config import force_storage_mode
        force_storage_mode('csv')
        return

    with get_db(db_path) as conn:
        current = _detect_version(conn)

        # Pre-DDL rollback-column migration: existing tables created before
        # X3 rollback (schema v11) lack SessionId / Status columns, so any
        # CREATE INDEX in *ddl* that references those columns would raise
        # "no such column" before _ensure_rollback_columns gets a chance
        # to ALTER TABLE them in. Running it first is safe — _has_table
        # gates every ALTER, so on a fresh DB this is a no-op and the DDL
        # below creates everything from scratch. The post-DDL call kept
        # below is a redundant idempotent safety net.
        _ensure_rollback_columns(conn)

        # P1: dedupe pre-existing SpiderStats / UploaderStats / PikpakStats
        # rows that share a SessionId before the DDL's new UNIQUE indexes
        # land. Without this, ``CREATE UNIQUE INDEX IF NOT EXISTS`` would
        # raise on any database that ever ran a stats save twice for the
        # same session (which is exactly the regression the index is
        # designed to prevent). We keep the row with the largest Id (most
        # recent) and drop the rest.
        _dedupe_session_keyed_stats_rows(conn)

        conn.executescript(ddl)

        # Forward-compat migration: add FailedMovies to SpiderStats
        try:
            conn.execute("ALTER TABLE SpiderStats ADD COLUMN FailedMovies TEXT")
        except sqlite3.OperationalError:
            pass

        _ensure_moviehistory_actor_columns(conn)
        _normalize_moviehistory_actor_column_order(conn)
        _ensure_rollback_columns(conn)
        _materialize_report_session_status_default(conn)

        if current > 0 and current < 10:
            _migrate_defaults_to_null(conn)

        if current > 0 and current < 13:
            _migrate_session_id_to_text(conn)

        if current > 0 and current < 14:
            _migrate_v14_drop_audit_tables(conn)

        if current == 0:
            conn.execute("INSERT INTO SchemaVersion (Version) VALUES (?)", (SCHEMA_VERSION,))
        elif current < SCHEMA_VERSION:
            conn.execute("UPDATE SchemaVersion SET Version = ?", (SCHEMA_VERSION,))

    logger.debug(f"Database initialised at {db_path} (schema v{SCHEMA_VERSION})")


def _init_single_legacy_db(db_path: str, *, force: bool = False):
    """Initialise a single DB with all tables (legacy / testing mode)."""
    if os.path.exists(db_path) and os.path.getsize(db_path) > 0 and not _is_valid_sqlite(db_path):
        logger.warning(
            f"Database file {db_path} is not a valid SQLite database "
            "(possibly a Git LFS pointer that was not pulled). "
            "Falling back to CSV storage mode for this run."
        )
        from javdb.infra.config import force_storage_mode
        force_storage_mode('csv')
        return

    with get_db(db_path) as conn:
        current = _detect_version(conn)

        # Pre-DDL rollback-column migration — see _init_single_db for the
        # rationale. Without this, _TABLES_SQL's CREATE INDEX statements
        # that reference SessionId / Status would fail on a legacy DB
        # whose tables predate X3 rollback.
        _ensure_rollback_columns(conn)

        conn.executescript(_TABLES_SQL)

        # Forward-compat migration: add FailedMovies to SpiderStats
        try:
            conn.execute("ALTER TABLE SpiderStats ADD COLUMN FailedMovies TEXT")
        except sqlite3.OperationalError:
            pass

        _ensure_moviehistory_actor_columns(conn)
        _normalize_moviehistory_actor_column_order(conn)
        _ensure_rollback_columns(conn)
        _materialize_report_session_status_default(conn)

        if current < 6:
            _migrate_v5_to_v6(conn)
        if current < 10:
            _migrate_defaults_to_null(conn)
        if current < 13:
            _migrate_session_id_to_text(conn)
        if current > 0 and current < 14:
            _migrate_v14_drop_audit_tables(conn)

        existing = conn.execute("SELECT Version FROM SchemaVersion LIMIT 1").fetchone()
        if existing is None:
            conn.execute("INSERT INTO SchemaVersion (Version) VALUES (?)", (SCHEMA_VERSION,))
        elif current < SCHEMA_VERSION:
            conn.execute("UPDATE SchemaVersion SET Version = ?", (SCHEMA_VERSION,))

    logger.debug(f"Legacy single-DB initialised at {db_path} (schema v{SCHEMA_VERSION})")


def _do_init(db_path: Optional[str]) -> None:
    """Original sqlite-only init path."""
    if db_path is not None:
        _init_single_legacy_db(db_path, force=True)
        return

    _migrate_single_to_split()
    _init_single_db(HISTORY_DB_PATH, _HISTORY_DDL, force=True)
    _init_single_db(REPORTS_DB_PATH, _REPORTS_DDL, force=True)
    _init_single_db(OPERATIONS_DB_PATH, _OPERATIONS_DDL, force=True)


def init_db(db_path: Optional[str] = None, *, force: bool = False):
    """Initialise all databases (or a single one when *db_path* is given).

    In csv-only storage mode this is a no-op unless *force* is True.

    When called without *db_path*, the three split databases are initialised.
    If a legacy single-DB file exists and the split files do not, an
    automatic migration is performed first.

    When called **with** *db_path*, only that single file is initialised
    using the combined DDL (backward compat for csv_to_sqlite.py and tests).

    Under ``STORAGE_BACKEND=d1``, this is a no-op — schema is managed via
    ``wrangler d1 migrations`` outside the Python process.  Under
    ``STORAGE_BACKEND=dual`` the local SQLite side is still initialised so
    that the dual-write path has somewhere to write; D1 is assumed to
    already match.
    """
    if not force:
        from javdb.infra.config import use_sqlite
        if not use_sqlite():
            return

    backend = _backend_mode()

    if backend == 'd1':
        logger.debug("init_db skipped under STORAGE_BACKEND=d1 (schema managed via wrangler)")
        return

    # For backend == 'dual' we temporarily downgrade to sqlite-only init so
    # that the DDL/migration plumbing only touches the local file (D1 schema
    # was created out-of-band by `wrangler d1 import`).
    #
    # The override is set ONLY on the thread-local sentinel — we deliberately
    # do NOT write ``os.environ['_STORAGE_BACKEND_INIT_OVERRIDE']`` here.
    # Doing so would leak the 'sqlite' downgrade to sibling threads (which
    # have no thread-local of their own and would then return 'sqlite' from
    # ``_backend_mode`` and cache plain ``sqlite3.Connection`` objects
    # instead of DualConnections). The module-level ``_init_lock`` serializes
    # concurrent ``init_db(dual)`` callers so the thread-local + ``_local.conns``
    # bookkeeping stays consistent.
    if backend == 'dual':
        with _init_lock:
            _local._storage_backend_init_override = 'sqlite'
            prev_conns = getattr(_local, 'conns', None)
            _local.conns = {}
            try:
                _do_init(db_path)
            finally:
                temp_conns = getattr(_local, 'conns', {})
                for conn in set(temp_conns.values()):
                    try:
                        conn.close()
                    except Exception as exc:  # noqa: BLE001 - best-effort cleanup
                        logger.warning("Failed to close temporary init_db connection: %s", exc)
                try:
                    del _local._storage_backend_init_override
                except AttributeError:
                    pass
                if prev_conns is not None:
                    _local.conns = prev_conns
                else:
                    _local.conns = {}
        return

    _do_init(db_path)


# ── Public facade API ───────────────────────────────────────────────────
# These functions provide convenience wrappers for external callers that
# don't want to manage connections directly.


def detect_schema_version(db_path: str) -> int:
    """Detect the current schema version of a database.

    Args:
        db_path: Path to SQLite database file

    Returns:
        Schema version number (0 if no SchemaVersion table exists)
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return _detect_version(conn)
    finally:
        conn.close()


def migrate_single_to_split() -> None:
    """Migrate from single javdb_autospider.db to split databases.

    Splits the legacy single database into:
    - history.db (MovieHistory, TorrentHistory)
    - reports.db (ReportSessions, ReportMovies, ReportTorrents, Stats)
    - operations.db (RcloneInventory, DedupRecords, PikpakHistory)
    """
    _migrate_single_to_split()


def ensure_moviehistory_actor_columns(db_path: Optional[str] = None) -> None:
    """Ensure MovieHistory has Actor columns (ActorName, ActorGender, ActorLink, SupportingActors).

    Args:
        db_path: Database path (defaults to HISTORY_DB_PATH)
    """
    conn = sqlite3.connect(db_path or HISTORY_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_moviehistory_actor_columns(conn)
        conn.commit()
    finally:
        conn.close()


def ensure_rollback_columns(db_path: Optional[str] = None) -> None:
    """Ensure tables have rollback columns (SessionId, WriteMode).

    Args:
        db_path: Database path (defaults to HISTORY_DB_PATH)
    """
    conn = sqlite3.connect(db_path or HISTORY_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_rollback_columns(conn)
        conn.commit()
    finally:
        conn.close()
