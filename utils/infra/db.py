"""SQLite database management layer for JAVDB AutoSpider.

Data is stored across three independent SQLite databases, each
holding a logically separate group of tables:

- **history.db** — MovieHistory, TorrentHistory
- **reports.db** — ReportSessions, ReportMovies, ReportTorrents,
  SpiderStats, UploaderStats, PikpakStats
- **operations.db** — RcloneInventory, DedupRecords, PikpakHistory,
  ProxyBans

WAL mode is enabled on every connection for concurrent-read safety.
"""

import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from api.parsers.common import (
    movie_href_lookup_values,
    javdb_absolute_url,
    absolutize_supporting_actors_json,
)
from utils.infra.config_helper import cfg
from utils.infra.logging_config import get_logger
from utils.infra.db_layer.history_repo import (
    load_history_joined as _load_history_joined,
    batch_update_movie_actors as _batch_update_movie_actors,
    _has_meaningful_actor_data,
)
from utils.infra.db_layer.operations_repo import (
    replace_rclone_inventory as _replace_rclone_inventory,
    save_proxy_bans as _save_proxy_bans,
)

logger = get_logger(__name__)

_REPORTS_DIR = cfg('REPORTS_DIR', 'reports')

HISTORY_DB_PATH = cfg('HISTORY_DB_PATH', os.path.join(_REPORTS_DIR, 'history.db'))
REPORTS_DB_PATH = cfg('REPORTS_DB_PATH', os.path.join(_REPORTS_DIR, 'reports.db'))
OPERATIONS_DB_PATH = cfg('OPERATIONS_DB_PATH', os.path.join(_REPORTS_DIR, 'operations.db'))

# Legacy single-DB path — kept for migration source detection
DB_PATH = cfg('SQLITE_DB_PATH', os.path.join(_REPORTS_DIR, 'javdb_autospider.db'))

SCHEMA_VERSION = 10

# ── Connection management ────────────────────────────────────────────────

_local = threading.local()


def _is_valid_sqlite(path: str) -> bool:
    """Quick check: file must start with the SQLite magic header."""
    try:
        with open(path, 'rb') as f:
            header = f.read(16)
        return header[:6] == b'SQLite'
    except OSError:
        return False


def _get_connection(db_path: str) -> sqlite3.Connection:
    """Return a thread-local connection for *db_path*, creating it if needed.

    Multiple connections (one per distinct path) are cached per thread.
    """
    conns: dict = getattr(_local, 'conns', None)
    if conns is None:
        conns = {}
        _local.conns = conns

    conn = conns.get(db_path)
    if conn is not None:
        return conn

    os.makedirs(os.path.dirname(db_path) or '.', exist_ok=True)
    if os.path.exists(db_path) and os.path.getsize(db_path) > 0 and not _is_valid_sqlite(db_path):
        raise sqlite3.DatabaseError(
            f"Database file {db_path} is not a valid SQLite file. "
            "This usually means Git LFS did not pull the real file."
        )
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conns[db_path] = conn
    return conn


@contextmanager
def get_db(db_path: Optional[str] = None):
    """Context manager yielding a SQLite connection with auto-commit.

    *db_path* defaults to ``HISTORY_DB_PATH`` when ``None``; callers
    that need a specific DB should always pass the path explicitly.
    """
    conn = _get_connection(db_path or HISTORY_DB_PATH)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def close_db():
    """Close all thread-local connections (call before process exit)."""
    conns: dict = getattr(_local, 'conns', None)
    if not conns:
        return
    for path, conn in list(conns.items()):
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
    conns.clear()


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
    HiResIndicator INTEGER
);
CREATE INDEX IF NOT EXISTS idx_movie_history_video_code ON MovieHistory(VideoCode);

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
    DateTimeUpdated TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_torrent_type
    ON TorrentHistory(MovieHistoryId, SubtitleIndicator, CensorIndicator);
"""

_REPORTS_DDL = _SCHEMA_VERSION_DDL + """
CREATE TABLE IF NOT EXISTS ReportSessions (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    ReportType TEXT NOT NULL,
    ReportDate TEXT NOT NULL,
    UrlType TEXT,
    DisplayName TEXT,
    Url TEXT,
    StartPage INTEGER,
    EndPage INTEGER,
    CsvFilename TEXT NOT NULL,
    DateTimeCreated TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_report_sessions_type_date ON ReportSessions(ReportType, ReportDate);
CREATE INDEX IF NOT EXISTS idx_report_sessions_csv ON ReportSessions(CsvFilename);

CREATE TABLE IF NOT EXISTS ReportMovies (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    SessionId INTEGER NOT NULL REFERENCES ReportSessions(Id),
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
    SessionId INTEGER NOT NULL REFERENCES ReportSessions(Id),
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
    SessionId INTEGER NOT NULL REFERENCES ReportSessions(Id),
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
    SessionId INTEGER NOT NULL REFERENCES ReportSessions(Id),
    ThresholdDays     INTEGER,
    TotalTorrents     INTEGER,
    FilteredOld       INTEGER,
    SuccessfulCount   INTEGER,
    FailedCount       INTEGER,
    UploadedCount     INTEGER,
    DeleteFailedCount INTEGER,
    DateTimeCreated   TEXT
);
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
    DateTimeDeleted TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_dedup_active_path
    ON DedupRecords(ExistingGdrivePath)
    WHERE IsDeleted = 0 AND ExistingGdrivePath != '';

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
    ErrorMessage TEXT
);

CREATE TABLE IF NOT EXISTS ProxyBans (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    ProxyName TEXT,
    DateTimeBanned TEXT,
    DateTimeUnbanned TEXT
);
"""

# Combined DDL for single-DB mode (backward compat, csv_to_sqlite, testing)
_TABLES_SQL = _HISTORY_DDL + _REPORTS_DDL + _OPERATIONS_DDL


# ── Category ↔ Indicator mapping (delegated to contracts) ────────────────

from utils.domain.contracts import category_to_indicators, indicators_to_category  # noqa: E402


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

    # ── Step 5: proxy_bans → ProxyBans ──
    if _has_table(conn, 'proxy_bans'):
        conn.execute("""
            INSERT INTO ProxyBans (ProxyName, DateTimeBanned, DateTimeUnbanned)
            SELECT proxy_name, ban_time, unban_time
            FROM proxy_bans
        """)
        conn.execute("DROP TABLE proxy_bans")
        logger.info("Migrated proxy_bans → ProxyBans")

    session_map: dict[int, int] = {}

    # ── Step 6: report_sessions → ReportSessions ──
    if _has_table(conn, 'report_sessions'):
        conn.execute("""
            INSERT INTO ReportSessions (ReportType, ReportDate, UrlType, DisplayName,
                Url, StartPage, EndPage, CsvFilename, DateTimeCreated)
            SELECT report_type, report_date, url_type, display_name,
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
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.executescript(
            """
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
                HiResIndicator INTEGER
            );
            INSERT INTO MovieHistory__colorder (
                Id, VideoCode, Href, ActorName, ActorGender, ActorLink, SupportingActors,
                DateTimeCreated, DateTimeUpdated, DateTimeVisited,
                PerfectMatchIndicator, HiResIndicator
            )
            SELECT Id, VideoCode, Href,
                ActorName, ActorGender, ActorLink,
                SupportingActors,
                DateTimeCreated, DateTimeUpdated, DateTimeVisited,
                PerfectMatchIndicator, HiResIndicator
            FROM MovieHistory;
            DROP TABLE MovieHistory;
            ALTER TABLE MovieHistory__colorder RENAME TO MovieHistory;
            CREATE INDEX IF NOT EXISTS idx_movie_history_video_code ON MovieHistory(VideoCode);
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


def _init_single_db(db_path: str, ddl: str, *, force: bool = False):
    """Initialise one database file: create tables and set schema version."""
    if not force:
        from utils.infra.config_helper import use_sqlite
        if not use_sqlite():
            return

    if os.path.exists(db_path) and os.path.getsize(db_path) > 0 and not _is_valid_sqlite(db_path):
        logger.warning(
            f"Database file {db_path} is not a valid SQLite database "
            "(possibly a Git LFS pointer that was not pulled). "
            "Falling back to CSV storage mode for this run."
        )
        from utils.infra.config_helper import force_storage_mode
        force_storage_mode('csv')
        return

    with get_db(db_path) as conn:
        current = _detect_version(conn)
        conn.executescript(ddl)

        # Forward-compat migration: add FailedMovies to SpiderStats
        try:
            conn.execute("ALTER TABLE SpiderStats ADD COLUMN FailedMovies TEXT")
        except sqlite3.OperationalError:
            pass

        _ensure_moviehistory_actor_columns(conn)
        _normalize_moviehistory_actor_column_order(conn)

        if current > 0 and current < 10:
            _migrate_defaults_to_null(conn)

        if current == 0:
            conn.execute("INSERT INTO SchemaVersion (Version) VALUES (?)", (SCHEMA_VERSION,))
        elif current < SCHEMA_VERSION:
            conn.execute("UPDATE SchemaVersion SET Version = ?", (SCHEMA_VERSION,))

    logger.debug(f"Database initialised at {db_path} (schema v{SCHEMA_VERSION})")


def _detect_version(conn) -> int:
    """Read schema version from whichever version table exists."""
    if _has_table(conn, 'schema_version'):
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        return row[0] if row else 0
    if _has_table(conn, 'SchemaVersion'):
        row = conn.execute("SELECT Version FROM SchemaVersion LIMIT 1").fetchone()
        return row[0] if row else 0
    return 0


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
    """SQL expressions for ActorName…SupportingActors when copying ``old_db.MovieHistory``.

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
            'RcloneInventory', 'DedupRecords', 'PikpakHistory', 'ProxyBans',
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
            try:
                if table == 'MovieHistory':
                    actor_exprs = _moviehistory_actor_select_exprs_from_attached_old_db(
                        new_conn,
                    )
                    new_conn.execute(
                        f"""INSERT INTO main.MovieHistory (
                            Id, VideoCode, Href, ActorName, ActorGender, ActorLink, SupportingActors,
                            DateTimeCreated, DateTimeUpdated, DateTimeVisited,
                            PerfectMatchIndicator, HiResIndicator)
                        SELECT Id, VideoCode, Href, {actor_exprs},
                               DateTimeCreated, DateTimeUpdated, DateTimeVisited,
                               PerfectMatchIndicator, HiResIndicator
                        FROM old_db.MovieHistory"""
                    )
                else:
                    new_conn.execute(
                        f"INSERT INTO main.[{table}] SELECT * FROM old_db.[{table}]"
                    )
            except sqlite3.OperationalError:
                logger.debug(f"Table {table} not found in old DB, skipping")
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


def init_db(db_path: Optional[str] = None, *, force: bool = False):
    """Initialise all databases (or a single one when *db_path* is given).

    In csv-only storage mode this is a no-op unless *force* is True.

    When called without *db_path*, the three split databases are initialised.
    If a legacy single-DB file exists and the split files do not, an
    automatic migration is performed first.

    When called **with** *db_path*, only that single file is initialised
    using the combined DDL (backward compat for csv_to_sqlite.py and tests).
    """
    if not force:
        from utils.infra.config_helper import use_sqlite
        if not use_sqlite():
            return

    if db_path is not None:
        # Single-DB mode (testing, csv_to_sqlite, explicit path)
        _init_single_legacy_db(db_path, force=True)
        return

    # Try automatic split migration from legacy single DB
    _migrate_single_to_split()

    _init_single_db(HISTORY_DB_PATH, _HISTORY_DDL, force=True)
    _init_single_db(REPORTS_DB_PATH, _REPORTS_DDL, force=True)
    _init_single_db(OPERATIONS_DB_PATH, _OPERATIONS_DDL, force=True)


def _init_single_legacy_db(db_path: str, *, force: bool = False):
    """Initialise a single DB with all tables (legacy / testing mode)."""
    if os.path.exists(db_path) and os.path.getsize(db_path) > 0 and not _is_valid_sqlite(db_path):
        logger.warning(
            f"Database file {db_path} is not a valid SQLite database "
            "(possibly a Git LFS pointer that was not pulled). "
            "Falling back to CSV storage mode for this run."
        )
        from utils.infra.config_helper import force_storage_mode
        force_storage_mode('csv')
        return

    with get_db(db_path) as conn:
        current = _detect_version(conn)
        conn.executescript(_TABLES_SQL)

        # Forward-compat migration: add FailedMovies to SpiderStats
        try:
            conn.execute("ALTER TABLE SpiderStats ADD COLUMN FailedMovies TEXT")
        except sqlite3.OperationalError:
            pass

        _ensure_moviehistory_actor_columns(conn)
        _normalize_moviehistory_actor_column_order(conn)

        if current > 0 and current < 10:
            _migrate_defaults_to_null(conn)

        if current == 0:
            conn.execute("INSERT INTO SchemaVersion (Version) VALUES (?)", (SCHEMA_VERSION,))
        elif current < 6:
            _migrate_v5_to_v6(conn)
            existing = conn.execute("SELECT Version FROM SchemaVersion LIMIT 1").fetchone()
            if existing is None:
                conn.execute("INSERT INTO SchemaVersion (Version) VALUES (?)", (SCHEMA_VERSION,))
            else:
                conn.execute("UPDATE SchemaVersion SET Version = ?", (SCHEMA_VERSION,))
        elif current < SCHEMA_VERSION:
            conn.execute("UPDATE SchemaVersion SET Version = ?", (SCHEMA_VERSION,))

    logger.debug(f"Legacy single-DB initialised at {db_path} (schema v{SCHEMA_VERSION})")


# ── MovieHistory + TorrentHistory helpers ────────────────────────────────

def db_load_history(db_path: Optional[str] = None, phase: Optional[int] = None) -> Dict[str, dict]:
    """Load history from MovieHistory + TorrentHistory into a dict keyed by Href.

    The *phase* parameter is accepted for backward compatibility but ignored
    (the new schema does not store phase).
    """
    with get_db(db_path or HISTORY_DB_PATH) as conn:
        return _load_history_joined(conn)


def db_upsert_history(
    href: str,
    video_code: str,
    magnet_links: Optional[Dict[str, str]] = None,
    size_links: Optional[Dict[str, str]] = None,
    file_count_links: Optional[Dict[str, int]] = None,
    resolution_links: Optional[Dict[str, Optional[int]]] = None,
    actor_name: Optional[str] = None,
    actor_gender: Optional[str] = None,
    actor_link: Optional[str] = None,
    supporting_actors: Optional[str] = None,
    db_path: Optional[str] = None,
) -> None:
    """Insert or update history across MovieHistory + TorrentHistory.

    Actor fields: when ``None``, existing MovieHistory values are left unchanged
    on update; use ``''`` to clear. On insert, ``None`` stays NULL.
    """
    if magnet_links is None:
        magnet_links = {}
    if size_links is None:
        size_links = {}
    if file_count_links is None:
        file_count_links = {}
    if resolution_links is None:
        resolution_links = {}

    base_url = cfg('BASE_URL', 'https://javdb.com')
    path_href, absolute_href = movie_href_lookup_values(href, base_url)
    lookup_hrefs = [h for h in (path_href, absolute_href) if h]
    normalized_href = absolute_href or href
    prepared_actor_link = (
        javdb_absolute_url(actor_link, base_url) if actor_link is not None else None
    )
    prepared_supporting_actors = (
        absolutize_supporting_actors_json(supporting_actors, base_url)
        if supporting_actors is not None
        else None
    )

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _TORRENT_CATS = ('hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle')

    with get_db(db_path or HISTORY_DB_PATH) as conn:
        if len(lookup_hrefs) == 2:
            existing = conn.execute(
                "SELECT Id FROM MovieHistory WHERE Href IN (?, ?)",
                (lookup_hrefs[0], lookup_hrefs[1]),
            ).fetchone()
        elif len(lookup_hrefs) == 1:
            existing = conn.execute(
                "SELECT Id FROM MovieHistory WHERE Href = ?",
                (lookup_hrefs[0],),
            ).fetchone()
        else:
            existing = conn.execute(
                "SELECT Id FROM MovieHistory WHERE Href = ?",
                (href,),
            ).fetchone()

        if existing is None:
            cur = conn.execute(
                """INSERT INTO MovieHistory
                   (VideoCode, Href, DateTimeCreated, DateTimeUpdated, DateTimeVisited,
                    ActorName, ActorGender, ActorLink, SupportingActors)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (video_code, normalized_href, now, now, now,
                 actor_name, actor_gender, prepared_actor_link, prepared_supporting_actors),
            )
            movie_id = cur.lastrowid
        else:
            movie_id = existing['Id']
            if (
                actor_name is not None
                or actor_gender is not None
                or actor_link is not None
                or supporting_actors is not None
            ):
                row_m = conn.execute(
                    """SELECT ActorName, ActorGender, ActorLink, SupportingActors
                       FROM MovieHistory WHERE Id=?""",
                    (movie_id,),
                ).fetchone()
                new_an = (
                    actor_name if actor_name is not None else row_m['ActorName']
                )
                new_ag = (
                    actor_gender if actor_gender is not None else row_m['ActorGender']
                )
                new_al = (
                    prepared_actor_link if actor_link is not None else row_m['ActorLink']
                )
                new_sup = (
                    prepared_supporting_actors if supporting_actors is not None
                    else row_m['SupportingActors']
                )
                existing_an = (row_m['ActorName'] or '').strip()
                if existing_an and not _has_meaningful_actor_data(
                    new_an or '', new_al or '', new_sup or '',
                ):
                    new_an = row_m['ActorName']
                    new_ag = row_m['ActorGender']
                    new_al = row_m['ActorLink']
                    new_sup = row_m['SupportingActors']
                conn.execute(
                    """UPDATE MovieHistory SET DateTimeUpdated=?, DateTimeVisited=?,
                       Href=?, ActorName=?, ActorGender=?, ActorLink=?, SupportingActors=? WHERE Id=?""",
                    (now, now, normalized_href, new_an, new_ag, new_al, new_sup, movie_id),
                )
            else:
                conn.execute(
                    "UPDATE MovieHistory SET DateTimeUpdated=?, DateTimeVisited=?, Href=? WHERE Id=?",
                    (now, now, normalized_href, movie_id),
                )

        # Upsert torrents
        has_hacked_subtitle = False
        has_subtitle = False

        for tt, magnet in magnet_links.items():
            if tt not in _TORRENT_CATS or not magnet:
                continue
            sub_ind, cen_ind = category_to_indicators(tt)
            size = size_links.get(tt, '')
            fc = file_count_links.get(tt, 0)
            res = resolution_links.get(tt)

            existing_t = conn.execute(
                """SELECT Id FROM TorrentHistory
                   WHERE MovieHistoryId=? AND SubtitleIndicator=? AND CensorIndicator=?""",
                (movie_id, sub_ind, cen_ind),
            ).fetchone()

            if existing_t is None:
                conn.execute(
                    """INSERT INTO TorrentHistory
                       (MovieHistoryId, MagnetUri, SubtitleIndicator, CensorIndicator,
                        ResolutionType, Size, FileCount, DateTimeCreated, DateTimeUpdated)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (movie_id, magnet, sub_ind, cen_ind, res, size, fc, now, now),
                )
            else:
                conn.execute(
                    """UPDATE TorrentHistory
                       SET MagnetUri=?, Size=?, FileCount=?, ResolutionType=?, DateTimeUpdated=?
                       WHERE Id=?""",
                    (magnet, size, fc, res, now, existing_t['Id']),
                )

            if tt == 'hacked_subtitle':
                has_hacked_subtitle = True
            elif tt == 'subtitle':
                has_subtitle = True

        # If hacked_subtitle exists, remove hacked_no_subtitle
        if has_hacked_subtitle:
            conn.execute(
                "DELETE FROM TorrentHistory WHERE MovieHistoryId=? "
                "AND SubtitleIndicator=0 AND CensorIndicator=0",
                (movie_id,),
            )
        # If subtitle exists, remove no_subtitle
        if has_subtitle:
            conn.execute(
                "DELETE FROM TorrentHistory WHERE MovieHistoryId=? "
                "AND SubtitleIndicator=0 AND CensorIndicator=1",
                (movie_id,),
            )

        # Update indicators
        _update_movie_indicators(conn, movie_id)


def _update_movie_indicators(conn, movie_id: int):
    """Recompute PerfectMatchIndicator and HiResIndicator for a movie."""
    perfect = conn.execute("""
        SELECT 1 FROM TorrentHistory t1
        JOIN TorrentHistory t2 ON t1.MovieHistoryId = t2.MovieHistoryId
        WHERE t1.MovieHistoryId = ?
          AND t1.SubtitleIndicator = 1 AND t1.CensorIndicator = 0
          AND t2.SubtitleIndicator = 1 AND t2.CensorIndicator = 1
    """, (movie_id,)).fetchone()

    hires = conn.execute("""
        SELECT 1 FROM TorrentHistory
        WHERE MovieHistoryId = ? AND ResolutionType >= 2560
    """, (movie_id,)).fetchone()

    conn.execute(
        "UPDATE MovieHistory SET PerfectMatchIndicator=?, HiResIndicator=? WHERE Id=?",
        (1 if perfect else 0, 1 if hires else 0, movie_id),
    )


def db_batch_update_last_visited(hrefs: List[str], db_path: Optional[str] = None) -> int:
    """Update DateTimeVisited for a batch of hrefs."""
    if not hrefs:
        return 0
    base_url = cfg('BASE_URL', 'https://javdb.com')
    lookup_hrefs: List[str] = []
    for href in hrefs:
        path_href, abs_href = movie_href_lookup_values(href, base_url)
        if path_href:
            lookup_hrefs.append(path_href)
        if abs_href:
            lookup_hrefs.append(abs_href)
        if not path_href and not abs_href and href:
            lookup_hrefs.append(href)
    lookup_hrefs = list(dict.fromkeys(lookup_hrefs))
    if not lookup_hrefs:
        return 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db(db_path or HISTORY_DB_PATH) as conn:
        placeholders = ','.join('?' for _ in lookup_hrefs)
        cur = conn.execute(
            f"UPDATE MovieHistory SET DateTimeVisited=? WHERE Href IN ({placeholders})",
            [now] + lookup_hrefs,
        )
        return cur.rowcount


def db_batch_update_movie_actors(
    updates: List[Tuple[str, str, str, str, str]],
    db_path: Optional[str] = None,
) -> int:
    """Set actor columns and DateTimeUpdated for each
    ``(href, actor_name, actor_gender, actor_link, supporting_actors)``.

    Returns the number of rows matched by UPDATE (may be 0 for unknown hrefs).
    """
    if not updates:
        return 0
    with get_db(db_path or HISTORY_DB_PATH) as conn:
        return _batch_update_movie_actors(conn, updates)


def db_check_torrent_in_history(href: str, torrent_type: str, db_path: Optional[str] = None) -> bool:
    """Check if a specific torrent type exists for href."""
    sub_ind, cen_ind = category_to_indicators(torrent_type)
    base_url = cfg('BASE_URL', 'https://javdb.com')
    path_href, abs_href = movie_href_lookup_values(href, base_url)
    with get_db(db_path or HISTORY_DB_PATH) as conn:
        if path_href and abs_href:
            row = conn.execute(
                """
                SELECT t.MagnetUri FROM TorrentHistory t
                JOIN MovieHistory m ON t.MovieHistoryId = m.Id
                WHERE m.Href IN (?, ?)
                  AND t.SubtitleIndicator = ? AND t.CensorIndicator = ?
                """,
                (path_href, abs_href, sub_ind, cen_ind),
            ).fetchone()
        else:
            lookup = path_href or abs_href or href
            row = conn.execute(
                """
                SELECT t.MagnetUri FROM TorrentHistory t
                JOIN MovieHistory m ON t.MovieHistoryId = m.Id
                WHERE m.Href = ? AND t.SubtitleIndicator = ? AND t.CensorIndicator = ?
                """,
                (lookup, sub_ind, cen_ind),
            ).fetchone()
        if row is None:
            return False
        return bool(row['MagnetUri'] and row['MagnetUri'].startswith('magnet:'))


def db_get_all_history_records(db_path: Optional[str] = None) -> List[dict]:
    """Return all MovieHistory records as dicts (for migration verification)."""
    with get_db(db_path or HISTORY_DB_PATH) as conn:
        rows = conn.execute("SELECT * FROM MovieHistory ORDER BY Id").fetchall()
        return [dict(r) for r in rows]


# ── RcloneInventory helpers ──────────────────────────────────────────────

def db_replace_rclone_inventory(entries: List[dict], db_path: Optional[str] = None) -> int:
    """Replace the entire RcloneInventory table (full scan refresh)."""
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        return _replace_rclone_inventory(conn, entries)


def db_clear_rclone_inventory(db_path: Optional[str] = None) -> None:
    """Delete all rows from RcloneInventory."""
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        conn.execute("DELETE FROM RcloneInventory")


def db_append_rclone_inventory(entries: List[dict], db_path: Optional[str] = None) -> int:
    """Append rows to RcloneInventory using executemany for speed."""
    if not entries:
        return 0
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        conn.executemany(
            """INSERT INTO RcloneInventory
               (VideoCode, SensorCategory, SubtitleCategory,
                FolderPath, FolderSize, FileCount, DateTimeScanned)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (e.get('VideoCode', e.get('video_code', '')),
                 e.get('SensorCategory', e.get('sensor_category')),
                 e.get('SubtitleCategory', e.get('subtitle_category')),
                 e.get('FolderPath', e.get('folder_path')),
                 int(e.get('FolderSize', e.get('folder_size', 0)) or 0),
                 int(e.get('FileCount', e.get('file_count', 0)) or 0),
                 e.get('DateTimeScanned', e.get('scan_datetime')))
                for e in entries
            ],
        )
        return len(entries)


def db_load_rclone_inventory(db_path: Optional[str] = None) -> Dict[str, list]:
    """Load inventory grouped by VideoCode."""
    inventory: Dict[str, list] = {}
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        rows = conn.execute("SELECT * FROM RcloneInventory").fetchall()
    for row in rows:
        r = dict(row)
        code = r['VideoCode'].strip().upper()
        if not code:
            continue
        inventory.setdefault(code, []).append(r)
    return inventory


# ── DedupRecords helpers ─────────────────────────────────────────────────

def db_load_dedup_records(db_path: Optional[str] = None) -> List[dict]:
    """Load all dedup records."""
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        rows = conn.execute("SELECT * FROM DedupRecords ORDER BY Id").fetchall()
        return [dict(r) for r in rows]


def db_append_dedup_record(record: dict, db_path: Optional[str] = None) -> int:
    """Append a single dedup record. Returns the new row id, or -1 if duplicate."""
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO DedupRecords
               (VideoCode, ExistingSensor, ExistingSubtitle,
                ExistingGdrivePath, ExistingFolderSize,
                NewTorrentCategory, DeletionReason,
                DateTimeDetected, IsDeleted, DateTimeDeleted)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (record.get('VideoCode', record.get('video_code')),
             record.get('ExistingSensor', record.get('existing_sensor')),
             record.get('ExistingSubtitle', record.get('existing_subtitle')),
             record.get('ExistingGdrivePath', record.get('existing_gdrive_path')),
             int(record.get('ExistingFolderSize', record.get('existing_folder_size', 0)) or 0),
             record.get('NewTorrentCategory', record.get('new_torrent_category')),
             record.get('DeletionReason', record.get('deletion_reason')),
             record.get('DateTimeDetected', record.get('detect_datetime')),
             1 if str(record.get('IsDeleted', record.get('is_deleted', 'False'))).lower() in ('true', '1') else 0,
             record.get('DateTimeDeleted', record.get('delete_datetime'))),
        )
        if cur.rowcount == 0:
            return -1
        return cur.lastrowid


def db_mark_records_deleted(
    path_datetime_pairs: List[Tuple[str, str]],
    db_path: Optional[str] = None,
) -> int:
    """Mark specific dedup records as deleted by gdrive path."""
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        updated = 0
        for path, dt in path_datetime_pairs:
            cur = conn.execute(
                "UPDATE DedupRecords SET IsDeleted=1, DateTimeDeleted=? "
                "WHERE ExistingGdrivePath=? AND IsDeleted=0",
                (dt, path),
            )
            updated += cur.rowcount
        return updated


def db_cleanup_deleted_records(
    older_than_days: int = 30,
    db_path: Optional[str] = None,
) -> int:
    """Remove dedup records that were deleted more than *older_than_days* ago."""
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=older_than_days)).strftime('%Y-%m-%d %H:%M:%S')
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        cur = conn.execute(
            "DELETE FROM DedupRecords "
            "WHERE IsDeleted=1 AND DateTimeDeleted IS NOT NULL AND DateTimeDeleted < ?",
            (cutoff,),
        )
        return cur.rowcount


def db_save_dedup_records(rows: List[dict], db_path: Optional[str] = None) -> None:
    """Overwrite all dedup records (deprecated)."""
    logger.warning(
        "db_save_dedup_records is deprecated — use db_mark_records_deleted "
        "for targeted updates instead"
    )
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        conn.execute("DELETE FROM DedupRecords")
        for r in rows:
            conn.execute(
                """INSERT INTO DedupRecords
                   (VideoCode, ExistingSensor, ExistingSubtitle,
                    ExistingGdrivePath, ExistingFolderSize,
                    NewTorrentCategory, DeletionReason,
                    DateTimeDetected, IsDeleted, DateTimeDeleted)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (r.get('VideoCode', r.get('video_code')),
                 r.get('ExistingSensor', r.get('existing_sensor')),
                 r.get('ExistingSubtitle', r.get('existing_subtitle')),
                 r.get('ExistingGdrivePath', r.get('existing_gdrive_path')),
                 int(r.get('ExistingFolderSize', r.get('existing_folder_size', 0)) or 0),
                 r.get('NewTorrentCategory', r.get('new_torrent_category')),
                 r.get('DeletionReason', r.get('deletion_reason')),
                 r.get('DateTimeDetected', r.get('detect_datetime')),
                 1 if str(r.get('IsDeleted', r.get('is_deleted', 'False'))).lower() in ('true', '1') else 0,
                 r.get('DateTimeDeleted', r.get('delete_datetime'))),
            )


# ── PikpakHistory helpers ────────────────────────────────────────────────

def db_append_pikpak_history(record: dict, db_path: Optional[str] = None) -> int:
    """Append a PikPak transfer record."""
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        cur = conn.execute(
            """INSERT INTO PikpakHistory
               (TorrentHash, TorrentName, Category, MagnetUri,
                DateTimeAddedToQb, DateTimeDeletedFromQb,
                DateTimeUploadedToPikpak, TransferStatus, ErrorMessage)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (record.get('TorrentHash', record.get('torrent_hash')),
             record.get('TorrentName', record.get('torrent_name')),
             record.get('Category', record.get('category')),
             record.get('MagnetUri', record.get('magnet_uri')),
             record.get('DateTimeAddedToQb', record.get('added_to_qb_date')),
             record.get('DateTimeDeletedFromQb', record.get('deleted_from_qb_date')),
             record.get('DateTimeUploadedToPikpak', record.get('uploaded_to_pikpak_date')),
             record.get('TransferStatus', record.get('transfer_status')),
             record.get('ErrorMessage', record.get('error_message'))),
        )
        return cur.lastrowid


# ── ProxyBans helpers ────────────────────────────────────────────────────

def db_load_proxy_bans(db_path: Optional[str] = None) -> List[dict]:
    """Load all proxy ban records."""
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        rows = conn.execute("SELECT * FROM ProxyBans ORDER BY Id").fetchall()
        return [dict(r) for r in rows]


def db_save_proxy_bans(records: List[dict], db_path: Optional[str] = None) -> None:
    """Replace all proxy ban records."""
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        _save_proxy_bans(conn, records)


# ── ReportSessions + ReportMovies + ReportTorrents helpers ───────────────

def db_create_report_session(
    report_type: str,
    report_date: str,
    csv_filename: str,
    *,
    url_type: Optional[str] = None,
    display_name: Optional[str] = None,
    url: Optional[str] = None,
    start_page: Optional[int] = None,
    end_page: Optional[int] = None,
    created_at: Optional[str] = None,
    db_path: Optional[str] = None,
) -> int:
    """Create a new report session and return its id."""
    if created_at is None:
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        cur = conn.execute(
            """INSERT INTO ReportSessions
               (ReportType, ReportDate, UrlType, DisplayName,
                Url, StartPage, EndPage, CsvFilename, DateTimeCreated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (report_type, report_date, url_type, display_name,
             url, start_page, end_page, csv_filename, created_at),
        )
        return cur.lastrowid


def db_insert_report_rows(session_id: int, rows: List[dict], db_path: Optional[str] = None) -> int:
    """Insert report rows into ReportMovies + ReportTorrents.

    Accepts rows in the legacy flat dict format with keys like
    ``hacked_subtitle``, ``subtitle``, etc.  Each non-empty magnet
    becomes a separate ReportTorrents row.
    """
    _CATS = [
        ('hacked_subtitle',    'size_hacked_subtitle',    'file_count_hacked_subtitle',    'resolution_hacked_subtitle',    1, 0),
        ('hacked_no_subtitle', 'size_hacked_no_subtitle', 'file_count_hacked_no_subtitle', 'resolution_hacked_no_subtitle', 0, 0),
        ('subtitle',           'size_subtitle',           'file_count_subtitle',           'resolution_subtitle',           1, 1),
        ('no_subtitle',        'size_no_subtitle',        'file_count_no_subtitle',        'resolution_no_subtitle',        0, 1),
    ]
    base_url = cfg('BASE_URL', 'https://javdb.com')
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        for row in rows:
            href = javdb_absolute_url(row.get('href') or '', base_url)
            cur = conn.execute(
                """INSERT INTO ReportMovies
                   (SessionId, Href, VideoCode, Page, Actor, Rate, CommentNumber)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (session_id,
                 href, row.get('video_code'),
                 int(row['page']) if row.get('page') else None,
                 row.get('actor'),
                 float(row['rate']) if row.get('rate') else None,
                 int(row['comment_number']) if row.get('comment_number') else None),
            )
            rm_id = cur.lastrowid
            vc = row.get('video_code')
            for cat, size_cat, fc_cat, res_cat, sub_ind, cen_ind in _CATS:
                magnet = (row.get(cat) or '').strip()
                if magnet:
                    conn.execute(
                        """INSERT INTO ReportTorrents
                           (ReportMovieId, VideoCode, MagnetUri,
                            SubtitleIndicator, CensorIndicator,
                            ResolutionType, Size, FileCount)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (rm_id, vc, magnet, sub_ind, cen_ind,
                         row.get(res_cat),
                         row.get(size_cat),
                         int(row.get(fc_cat, 0) or 0)),
                    )
        return len(rows)


def db_get_report_rows(session_id: int, db_path: Optional[str] = None) -> List[dict]:
    """Get all rows for a session as flat dicts (backward compatible).

    Aggregates ReportMovies + ReportTorrents back into the legacy format
    with ``hacked_subtitle``, ``subtitle``, etc. columns.
    """
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        movies = conn.execute(
            "SELECT * FROM ReportMovies WHERE SessionId = ? ORDER BY Id",
            (session_id,),
        ).fetchall()

        result = []
        for m in movies:
            m = dict(m)
            flat = {
                'href': m.get('Href', ''),
                'video_code': m.get('VideoCode', ''),
                'page': m.get('Page'),
                'actor': m.get('Actor', ''),
                'rate': m.get('Rate'),
                'comment_number': m.get('CommentNumber'),
                'hacked_subtitle': '',
                'hacked_no_subtitle': '',
                'subtitle': '',
                'no_subtitle': '',
                'size_hacked_subtitle': '',
                'size_hacked_no_subtitle': '',
                'size_subtitle': '',
                'size_no_subtitle': '',
                'file_count_hacked_subtitle': 0,
                'file_count_hacked_no_subtitle': 0,
                'file_count_subtitle': 0,
                'file_count_no_subtitle': 0,
                'resolution_hacked_subtitle': None,
                'resolution_hacked_no_subtitle': None,
                'resolution_subtitle': None,
                'resolution_no_subtitle': None,
            }
            torrents = conn.execute(
                "SELECT * FROM ReportTorrents WHERE ReportMovieId = ?",
                (m['Id'],),
            ).fetchall()
            for t in torrents:
                t = dict(t)
                cat = indicators_to_category(t['SubtitleIndicator'], t['CensorIndicator'])
                flat[cat] = t.get('MagnetUri', '')
                flat[f'size_{cat}'] = t.get('Size', '')
                flat[f'file_count_{cat}'] = t.get('FileCount', 0)
                flat[f'resolution_{cat}'] = t.get('ResolutionType')
            result.append(flat)
        return result


def db_get_latest_session(report_type: Optional[str] = None, db_path: Optional[str] = None) -> Optional[dict]:
    """Get the most recent report session, optionally filtered by type."""
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        if report_type:
            row = conn.execute(
                "SELECT * FROM ReportSessions WHERE ReportType = ? ORDER BY Id DESC LIMIT 1",
                (report_type,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM ReportSessions ORDER BY Id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None


def db_get_sessions_by_date(report_date: str, report_type: Optional[str] = None,
                            db_path: Optional[str] = None) -> List[dict]:
    """Get all sessions for a given date."""
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        if report_type:
            rows = conn.execute(
                "SELECT * FROM ReportSessions WHERE ReportDate = ? AND ReportType = ? ORDER BY Id",
                (report_date, report_type),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM ReportSessions WHERE ReportDate = ? ORDER BY Id",
                (report_date,),
            ).fetchall()
        return [dict(r) for r in rows]


# ── Stats helpers ────────────────────────────────────────────────────────

def db_save_spider_stats(session_id: int, stats: dict, db_path: Optional[str] = None) -> int:
    """Save spider statistics for a session."""
    import json as _json
    failed_movies_json = _json.dumps(stats.get('failed_movies', []), ensure_ascii=False) if stats.get('failed_movies') else ''
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        cur = conn.execute(
            """INSERT INTO SpiderStats
               (SessionId,
                Phase1Discovered, Phase1Processed, Phase1Skipped,
                Phase1NoNew, Phase1Failed,
                Phase2Discovered, Phase2Processed, Phase2Skipped,
                Phase2NoNew, Phase2Failed,
                TotalDiscovered, TotalProcessed, TotalSkipped,
                TotalNoNew, TotalFailed, FailedMovies)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id,
             stats.get('phase1_discovered', 0), stats.get('phase1_processed', 0),
             stats.get('phase1_skipped', 0), stats.get('phase1_no_new', 0),
             stats.get('phase1_failed', 0),
             stats.get('phase2_discovered', 0), stats.get('phase2_processed', 0),
             stats.get('phase2_skipped', 0), stats.get('phase2_no_new', 0),
             stats.get('phase2_failed', 0),
             stats.get('total_discovered', 0), stats.get('total_processed', 0),
             stats.get('total_skipped', 0), stats.get('total_no_new', 0),
             stats.get('total_failed', 0), failed_movies_json),
        )
        return cur.lastrowid


def db_save_uploader_stats(session_id: int, stats: dict, db_path: Optional[str] = None) -> int:
    """Save uploader statistics for a session."""
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        cur = conn.execute(
            """INSERT INTO UploaderStats
               (SessionId, TotalTorrents, DuplicateCount, Attempted,
                SuccessfullyAdded, FailedCount, HackedSub, HackedNosub,
                SubtitleCount, NoSubtitleCount, SuccessRate)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id,
             stats.get('total_torrents', 0), stats.get('duplicate_count', 0),
             stats.get('attempted', 0), stats.get('successfully_added', 0),
             stats.get('failed_count', 0), stats.get('hacked_sub', 0),
             stats.get('hacked_nosub', 0), stats.get('subtitle_count', 0),
             stats.get('no_subtitle_count', 0), stats.get('success_rate', 0.0)),
        )
        return cur.lastrowid


def db_save_pikpak_stats(session_id: int, stats: dict, db_path: Optional[str] = None) -> int:
    """Save PikPak bridge statistics for a session."""
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        cur = conn.execute(
            """INSERT INTO PikpakStats
               (SessionId, ThresholdDays, TotalTorrents,
                FilteredOld, SuccessfulCount, FailedCount,
                UploadedCount, DeleteFailedCount)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id,
             stats.get('threshold_days', 3), stats.get('total_torrents', 0),
             stats.get('filtered_old', 0), stats.get('successful_count', 0),
             stats.get('failed_count', 0),
             stats.get('uploaded_count', stats.get('successful_count', 0)),
             stats.get('delete_failed_count', 0)),
        )
        return cur.lastrowid


def db_get_spider_stats(session_id: int, db_path: Optional[str] = None) -> Optional[dict]:
    """Get spider stats for a session."""
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM SpiderStats WHERE SessionId = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None


def db_get_uploader_stats(session_id: int, db_path: Optional[str] = None) -> Optional[dict]:
    """Get uploader stats for a session."""
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM UploaderStats WHERE SessionId = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None


def db_get_pikpak_stats(session_id: int, db_path: Optional[str] = None) -> Optional[dict]:
    """Get PikPak stats for a session."""
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        row = conn.execute(
            "SELECT * FROM PikpakStats WHERE SessionId = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None
