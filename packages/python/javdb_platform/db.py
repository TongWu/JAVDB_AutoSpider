"""SQLite database management layer for JAVDB AutoSpider.

Data is stored across three independent SQLite databases, each
holding a logically separate group of tables:

- **history.db** — MovieHistory, TorrentHistory
- **reports.db** — ReportSessions, ReportMovies, ReportTorrents,
  SpiderStats, UploaderStats, PikpakStats
- **operations.db** — RcloneInventory, DedupRecords, PikpakHistory

WAL mode is enabled on every connection for concurrent-read safety.

Rollback support (X3 hybrid strategy)
--------------------------------------
Each row mutation associated with a workflow run carries the
``ReportSessions.Id`` of that run via the ``SessionId`` column on every
mutated table. For the ``MovieHistory`` / ``TorrentHistory`` tables —
which use upsert semantics — every INSERT/UPDATE is mirrored to a
companion ``*Audit`` table that captures the prior row JSON, enabling
a failed run to be rolled back precisely without disturbing the
committed state of any other concurrent run.

The active session id is tracked process-wide via
:func:`set_active_session_id` (which the spider sets once at the start
of each pipeline). Helper functions accept an explicit ``session_id``
keyword that overrides the thread-local context when callers (e.g.
ad-hoc maintenance scripts) want fine-grained control.
"""

import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from apps.api.parsers.common import (
    movie_href_lookup_values,
    javdb_absolute_url,
    absolutize_supporting_actors_json,
)
from packages.python.javdb_platform.config_helper import cfg
from packages.python.javdb_platform.logging_config import get_logger
from packages.python.javdb_platform.db_layer.history_repo import (
    load_history_joined as _load_history_joined,
    batch_update_movie_actors as _batch_update_movie_actors,
    _has_meaningful_actor_data,
)
from packages.python.javdb_platform.db_layer.operations_repo import (
    replace_rclone_inventory as _replace_rclone_inventory,
    open_rclone_staging as _open_rclone_staging,
    append_rclone_staging as _append_rclone_staging,
    swap_rclone_inventory as _swap_rclone_inventory,
    drop_rclone_staging as _drop_rclone_staging,
)

logger = get_logger(__name__)

_REPORTS_DIR = cfg('REPORTS_DIR', 'reports')

HISTORY_DB_PATH = cfg('HISTORY_DB_PATH', os.path.join(_REPORTS_DIR, 'history.db'))
REPORTS_DB_PATH = cfg('REPORTS_DB_PATH', os.path.join(_REPORTS_DIR, 'reports.db'))
OPERATIONS_DB_PATH = cfg('OPERATIONS_DB_PATH', os.path.join(_REPORTS_DIR, 'operations.db'))

# Logical-name mapping for D1 / dual backends.
_DB_PATH_TO_LOGICAL_NAME = {
    HISTORY_DB_PATH: 'history',
    REPORTS_DB_PATH: 'reports',
    OPERATIONS_DB_PATH: 'operations',
}


def _backend_mode() -> str:
    """Resolve the active storage backend.

    ``STORAGE_BACKEND`` env var (or ``config.STORAGE_BACKEND``) selects between:

    * ``sqlite`` (default) — original behaviour, local files only.
    * ``d1`` — all reads/writes go to Cloudflare D1.
    * ``dual`` — writes mirror to both SQLite and D1; reads come from D1
      (used during migration validation).

    During ``init_db`` under the ``dual`` backend we temporarily downgrade
    to sqlite-only init so the DDL plumbing only touches the local file.
    That override lives in a *thread-local*
    (``_local._storage_backend_init_override``); ``init_db`` deliberately
    does NOT mirror it into the process-wide environment because doing so
    would leak the override to sibling threads — those threads have no
    thread-local of their own and would then fall through to the env-var
    branch below, incorrectly caching plain ``sqlite3`` connections in
    place of the configured DualConnections.

    The ``_STORAGE_BACKEND_INIT_OVERRIDE`` env var is still consulted as a
    deliberate escape hatch for callers (e.g. external scripts or test
    harnesses) that want to force sqlite-only behaviour for an entire
    process. It is intentionally **not** written from inside ``init_db``.
    """
    tl_override = getattr(_local, '_storage_backend_init_override', None)
    if tl_override:
        return tl_override.strip().lower()
    override = os.environ.get('_STORAGE_BACKEND_INIT_OVERRIDE')
    if override:
        return override.strip().lower()
    val = os.environ.get('STORAGE_BACKEND') or cfg('STORAGE_BACKEND', None)
    if isinstance(val, str):
        val = val.strip().lower()
    if val in ('d1', 'dual'):
        return val
    return 'sqlite'


def current_backend() -> str:
    """Public alias of :func:`_backend_mode` for use in non-db modules.

    Returns one of ``'sqlite'``, ``'d1'``, ``'dual'``. Useful when callers
    want to log or branch on the configured storage backend without
    importing private helpers.
    """
    return _backend_mode()


def _logical_name_for(db_path: str) -> str:
    name = _DB_PATH_TO_LOGICAL_NAME.get(db_path)
    if name is None:
        raise ValueError(
            f"No D1 logical-name mapping for db_path={db_path!r}. "
            "Add it to _DB_PATH_TO_LOGICAL_NAME or use STORAGE_BACKEND=sqlite."
        )
    return name

# Legacy single-DB path — kept for migration source detection
DB_PATH = cfg('SQLITE_DB_PATH', os.path.join(_REPORTS_DIR, 'javdb_autospider.db'))

SCHEMA_VERSION = 11

# ── Connection management ────────────────────────────────────────────────

_local = threading.local()

_SESSION_ID_SENTINEL = object()


# ── Active session context (X3 rollback) ─────────────────────────────────
# Module-global so subprocess workers and main pipeline share a single
# "current session" once the spider sets it via ``set_active_session_id``.
# Callers may also pass ``session_id=`` explicitly to override.
_active_session_id_lock = threading.Lock()
_active_session_id_value: Optional[int] = None


def set_active_session_id(session_id: Optional[int]) -> None:
    """Set the current pipeline ``ReportSessions.Id``.

    Called by the spider once after creating the report session. All
    subsequent ``db_upsert_history`` / ``db_batch_update_last_visited``
    / ``db_batch_update_movie_actors`` / etc. that don't pass an explicit
    ``session_id=`` will tag their writes with this value (and audit
    rows where applicable).

    Pass ``None`` to clear the context (e.g. between pipeline phases in
    long-lived processes / tests).
    """
    global _active_session_id_value
    with _active_session_id_lock:
        _active_session_id_value = session_id


def get_active_session_id() -> Optional[int]:
    """Return the currently-active ``ReportSessions.Id`` or ``None``."""
    with _active_session_id_lock:
        return _active_session_id_value


def _resolve_session_id(explicit: Any = _SESSION_ID_SENTINEL) -> Optional[int]:
    """Pick the explicit override or fall back to the active context."""
    if explicit is _SESSION_ID_SENTINEL:
        return get_active_session_id()
    return explicit

# Serializes the dual-backend init window. Without this, two threads racing
# into ``init_db`` would both try to mutate ``_local.conns`` and the env-var /
# thread-local override, with the second thread potentially observing a
# half-applied state. ``_do_init`` also touches the file system, which is
# itself unsafe to run concurrently against the same SQLite paths.
_init_lock = threading.Lock()


def _is_valid_sqlite(path: str) -> bool:
    """Quick check: file must start with the SQLite magic header."""
    try:
        with open(path, 'rb') as f:
            header = f.read(16)
        return header[:6] == b'SQLite'
    except OSError:
        return False


def _open_sqlite_connection(db_path: str) -> sqlite3.Connection:
    """Open and configure a fresh local SQLite connection."""
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
    return conn


def _get_connection(db_path: str):
    """Return a thread-local connection for *db_path*, creating it if needed.

    Multiple connections (one per distinct path) are cached per thread.

    Honours ``STORAGE_BACKEND`` to return either a plain ``sqlite3.Connection``
    (default), a ``D1Connection``, or a ``DualConnection`` mirroring writes
    across both backends.
    """
    conns: dict = getattr(_local, 'conns', None)
    if conns is None:
        conns = {}
        _local.conns = conns

    conn = conns.get(db_path)
    if conn is not None:
        return conn

    backend = _backend_mode()

    if backend == 'sqlite':
        conn = _open_sqlite_connection(db_path)
    elif backend == 'd1':
        from packages.python.javdb_platform.d1_client import make_d1_connection
        conn = make_d1_connection(_logical_name_for(db_path))
    elif backend == 'dual':
        from packages.python.javdb_platform.d1_client import make_d1_connection
        from packages.python.javdb_platform.dual_connection import DualConnection
        sqlite_conn = _open_sqlite_connection(db_path)
        d1_conn = make_d1_connection(_logical_name_for(db_path))
        conn = DualConnection(sqlite_conn, d1_conn, logical_name=_logical_name_for(db_path))
    else:
        raise RuntimeError(f"Unknown STORAGE_BACKEND={backend!r}")

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
        # Only attempt WAL checkpoint on real SQLite connections; D1 / Dual
        # facades reject PRAGMA writes so we skip them silently.
        if isinstance(conn, sqlite3.Connection):
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
    HiResIndicator INTEGER,
    SessionId INTEGER
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
    SessionId INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_torrent_type
    ON TorrentHistory(MovieHistoryId, SubtitleIndicator, CensorIndicator);
CREATE INDEX IF NOT EXISTS idx_torrent_history_session ON TorrentHistory(SessionId);

-- Audit tables for D1 rollback (X3 hybrid strategy). Every mutating write
-- to MovieHistory / TorrentHistory captures a row here in the same D1 batch
-- (atomic per Cloudflare D1 contract), so a failed run can be rolled back
-- by replaying the audit log in reverse order.
CREATE TABLE IF NOT EXISTS MovieHistoryAudit (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    TargetId INTEGER NOT NULL,
    Action TEXT NOT NULL,
    OldRowJson TEXT,
    SessionId INTEGER NOT NULL,
    DateTimeCreated TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mh_audit_session ON MovieHistoryAudit(SessionId, Id);

CREATE TABLE IF NOT EXISTS TorrentHistoryAudit (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    TargetId INTEGER NOT NULL,
    Action TEXT NOT NULL,
    OldRowJson TEXT,
    SessionId INTEGER NOT NULL,
    DateTimeCreated TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_th_audit_session ON TorrentHistoryAudit(SessionId, Id);
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
    DateTimeCreated TEXT NOT NULL,
    Status TEXT DEFAULT 'in_progress'
);
CREATE INDEX IF NOT EXISTS idx_report_sessions_type_date ON ReportSessions(ReportType, ReportDate);
CREATE INDEX IF NOT EXISTS idx_report_sessions_csv ON ReportSessions(CsvFilename);
CREATE INDEX IF NOT EXISTS idx_report_sessions_status ON ReportSessions(Status, DateTimeCreated);

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
    DateTimeDeleted TEXT,
    SessionId INTEGER
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
    SessionId INTEGER
);
CREATE INDEX IF NOT EXISTS idx_pikpak_history_session ON PikpakHistory(SessionId);

CREATE TABLE IF NOT EXISTS InventoryAlignNoExactMatch (
    VideoCode TEXT PRIMARY KEY,
    Reason TEXT,
    DateTimeRecorded TEXT,
    SessionId INTEGER
);
CREATE INDEX IF NOT EXISTS idx_align_no_match_session ON InventoryAlignNoExactMatch(SessionId);
"""

# Combined DDL for single-DB mode (backward compat, csv_to_sqlite, testing)
_TABLES_SQL = _HISTORY_DDL + _REPORTS_DDL + _OPERATIONS_DDL


# ── Category ↔ Indicator mapping (delegated to contracts) ────────────────

from packages.python.javdb_core.contracts import category_to_indicators, indicators_to_category  # noqa: E402


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


def _ensure_rollback_columns(conn: sqlite3.Connection) -> None:
    """Add Status/SessionId columns and audit tables for X3 rollback (idempotent).

    Adds:
      - ReportSessions.Status TEXT DEFAULT 'in_progress'
      - MovieHistory.SessionId, TorrentHistory.SessionId
      - PikpakHistory.SessionId, DedupRecords.SessionId,
        InventoryAlignNoExactMatch.SessionId
      - MovieHistoryAudit, TorrentHistoryAudit tables and indexes

    This handles existing databases that were created before the X3 rollback
    schema. New DBs are created with the columns directly via the DDL
    constants in ``_HISTORY_DDL`` / ``_REPORTS_DDL`` / ``_OPERATIONS_DDL``,
    so the ALTER calls below silently no-op.
    """
    add_column_specs = [
        ('ReportSessions', 'Status', "TEXT DEFAULT 'in_progress'"),
        ('MovieHistory', 'SessionId', 'INTEGER'),
        ('TorrentHistory', 'SessionId', 'INTEGER'),
        ('PikpakHistory', 'SessionId', 'INTEGER'),
        ('DedupRecords', 'SessionId', 'INTEGER'),
        ('InventoryAlignNoExactMatch', 'SessionId', 'INTEGER'),
    ]
    for table, column, ddl in add_column_specs:
        if not _has_table(conn, table):
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        except sqlite3.OperationalError:
            # Column already exists; ALTER raises "duplicate column name" — fine.
            pass

    audit_ddl = (
        """
        CREATE TABLE IF NOT EXISTS MovieHistoryAudit (
            Id INTEGER PRIMARY KEY AUTOINCREMENT,
            TargetId INTEGER NOT NULL,
            Action TEXT NOT NULL,
            OldRowJson TEXT,
            SessionId INTEGER NOT NULL,
            DateTimeCreated TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_mh_audit_session
            ON MovieHistoryAudit(SessionId, Id);
        CREATE TABLE IF NOT EXISTS TorrentHistoryAudit (
            Id INTEGER PRIMARY KEY AUTOINCREMENT,
            TargetId INTEGER NOT NULL,
            Action TEXT NOT NULL,
            OldRowJson TEXT,
            SessionId INTEGER NOT NULL,
            DateTimeCreated TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_th_audit_session
            ON TorrentHistoryAudit(SessionId, Id);
        """
    )
    if _has_table(conn, 'MovieHistory'):
        conn.executescript(audit_ddl)

    extra_indexes = [
        ('idx_movie_history_session', 'MovieHistory', 'SessionId'),
        ('idx_torrent_history_session', 'TorrentHistory', 'SessionId'),
        ('idx_report_sessions_status',
         'ReportSessions', 'Status, DateTimeCreated'),
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
                SessionId INTEGER
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


def _init_single_db(db_path: str, ddl: str, *, force: bool = False):
    """Initialise one database file: create tables and set schema version."""
    if not force:
        from packages.python.javdb_platform.config_helper import use_sqlite
        if not use_sqlite():
            return

    if os.path.exists(db_path) and os.path.getsize(db_path) > 0 and not _is_valid_sqlite(db_path):
        logger.warning(
            f"Database file {db_path} is not a valid SQLite database "
            "(possibly a Git LFS pointer that was not pulled). "
            "Falling back to CSV storage mode for this run."
        )
        from packages.python.javdb_platform.config_helper import force_storage_mode
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

        conn.executescript(ddl)

        # Forward-compat migration: add FailedMovies to SpiderStats
        try:
            conn.execute("ALTER TABLE SpiderStats ADD COLUMN FailedMovies TEXT")
        except sqlite3.OperationalError:
            pass

        _ensure_moviehistory_actor_columns(conn)
        _normalize_moviehistory_actor_column_order(conn)
        _ensure_rollback_columns(conn)

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
            'InventoryAlignNoExactMatch',
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
        from packages.python.javdb_platform.config_helper import use_sqlite
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


def _do_init(db_path: Optional[str]) -> None:
    """Original sqlite-only init path."""
    if db_path is not None:
        _init_single_legacy_db(db_path, force=True)
        return

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
        from packages.python.javdb_platform.config_helper import force_storage_mode
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


# ── Audit helpers (X3 rollback) ──────────────────────────────────────────
#
# Every mutation of ``MovieHistory`` / ``TorrentHistory`` that originates
# from a tagged session (``session_id is not None``) records a companion
# audit row in ``MovieHistoryAudit`` / ``TorrentHistoryAudit`` describing
# what changed. ``apps.cli.rollback`` later replays the audit log in
# reverse order to undo the mutations of a failed run while leaving the
# committed state of any other concurrent run untouched.
#
# The audit row is written WITHIN the same ``with get_db(...)`` block as
# the main mutation, so:
#   - SQLite: implicit transaction ties them; if the main write fails,
#     the audit row is rolled back together (or vice versa).
#   - D1 / Dual: each statement auto-commits, but the worst case is an
#     orphan audit row (Action='INSERT' with no corresponding TargetId
#     in the main table). Rollback handles this gracefully — DELETE on
#     a non-existent row is a no-op, and UPDATE-restore is idempotent.

def _audit_record_movie_change(
    conn,
    target_id: int,
    *,
    action: str,
    session_id: Optional[int],
    old_row: Any = None,
    when: Optional[str] = None,
) -> None:
    """Append a row to ``MovieHistoryAudit`` for this session_id."""
    if session_id is None:
        return
    if when is None:
        when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    old_json = None
    if old_row is not None:
        old_json = json.dumps(_row_to_jsonable_dict(old_row),
                              ensure_ascii=False, default=str)
    conn.execute(
        """INSERT INTO MovieHistoryAudit
           (TargetId, Action, OldRowJson, SessionId, DateTimeCreated)
           VALUES (?, ?, ?, ?, ?)""",
        (target_id, action, old_json, session_id, when),
    )


def _audit_record_torrent_change(
    conn,
    target_id: int,
    *,
    action: str,
    session_id: Optional[int],
    old_row: Any = None,
    when: Optional[str] = None,
) -> None:
    """Append a row to ``TorrentHistoryAudit`` for this session_id."""
    if session_id is None:
        return
    if when is None:
        when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    old_json = None
    if old_row is not None:
        old_json = json.dumps(_row_to_jsonable_dict(old_row),
                              ensure_ascii=False, default=str)
    conn.execute(
        """INSERT INTO TorrentHistoryAudit
           (TargetId, Action, OldRowJson, SessionId, DateTimeCreated)
           VALUES (?, ?, ?, ?, ?)""",
        (target_id, action, old_json, session_id, when),
    )


def _row_to_jsonable_dict(row) -> dict:
    """Convert a sqlite3.Row / dict / mapping into a plain JSON-friendly dict."""
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return {k: row[k] for k in row.keys()}
    except Exception:
        return dict(row)


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
    session_id: Any = _SESSION_ID_SENTINEL,
) -> None:
    """Insert or update history across MovieHistory + TorrentHistory.

    Actor fields: when ``None``, existing MovieHistory values are left unchanged
    on update; use ``''`` to clear. On insert, ``None`` stays NULL.

    *session_id*: the active ``ReportSessions.Id`` for X3 rollback bookkeeping.
    Defaults to :func:`get_active_session_id`. When set, the row's ``SessionId``
    column is populated and a companion ``MovieHistoryAudit`` /
    ``TorrentHistoryAudit`` row is written for each INSERT/UPDATE.
    """
    if magnet_links is None:
        magnet_links = {}
    if size_links is None:
        size_links = {}
    if file_count_links is None:
        file_count_links = {}
    if resolution_links is None:
        resolution_links = {}

    sid = _resolve_session_id(session_id)

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
                    ActorName, ActorGender, ActorLink, SupportingActors, SessionId)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (video_code, normalized_href, now, now, now,
                 actor_name, actor_gender, prepared_actor_link,
                 prepared_supporting_actors, sid),
            )
            movie_id = cur.lastrowid
            _audit_record_movie_change(
                conn, movie_id, action='INSERT', session_id=sid, when=now,
            )
        else:
            movie_id = existing['Id']
            old_full = conn.execute(
                "SELECT * FROM MovieHistory WHERE Id=?", (movie_id,),
            ).fetchone()
            if (
                actor_name is not None
                or actor_gender is not None
                or actor_link is not None
                or supporting_actors is not None
            ):
                row_m = old_full  # contains the actor columns we need
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
                       Href=?, ActorName=?, ActorGender=?, ActorLink=?,
                       SupportingActors=?, SessionId=? WHERE Id=?""",
                    (now, now, normalized_href, new_an, new_ag, new_al, new_sup,
                     sid, movie_id),
                )
            else:
                conn.execute(
                    """UPDATE MovieHistory SET DateTimeUpdated=?, DateTimeVisited=?,
                       Href=?, SessionId=? WHERE Id=?""",
                    (now, now, normalized_href, sid, movie_id),
                )
            _audit_record_movie_change(
                conn, movie_id, action='UPDATE', session_id=sid,
                old_row=old_full, when=now,
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
                """SELECT * FROM TorrentHistory
                   WHERE MovieHistoryId=? AND SubtitleIndicator=? AND CensorIndicator=?""",
                (movie_id, sub_ind, cen_ind),
            ).fetchone()

            if existing_t is None:
                cur_t = conn.execute(
                    """INSERT INTO TorrentHistory
                       (MovieHistoryId, MagnetUri, SubtitleIndicator, CensorIndicator,
                        ResolutionType, Size, FileCount, DateTimeCreated,
                        DateTimeUpdated, SessionId)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (movie_id, magnet, sub_ind, cen_ind, res, size, fc, now, now,
                     sid),
                )
                _audit_record_torrent_change(
                    conn, cur_t.lastrowid, action='INSERT',
                    session_id=sid, when=now,
                )
            else:
                conn.execute(
                    """UPDATE TorrentHistory
                       SET MagnetUri=?, Size=?, FileCount=?, ResolutionType=?,
                           DateTimeUpdated=?, SessionId=?
                       WHERE Id=?""",
                    (magnet, size, fc, res, now, sid, existing_t['Id']),
                )
                _audit_record_torrent_change(
                    conn, existing_t['Id'], action='UPDATE', session_id=sid,
                    old_row=existing_t, when=now,
                )

            if tt == 'hacked_subtitle':
                has_hacked_subtitle = True
            elif tt == 'subtitle':
                has_subtitle = True

        # If hacked_subtitle exists, remove hacked_no_subtitle
        if has_hacked_subtitle:
            _delete_torrents_with_audit(
                conn, movie_id, sub_ind=0, cen_ind=0,
                session_id=sid, when=now,
            )
        # If subtitle exists, remove no_subtitle
        if has_subtitle:
            _delete_torrents_with_audit(
                conn, movie_id, sub_ind=0, cen_ind=1,
                session_id=sid, when=now,
            )

        # Update indicators
        _update_movie_indicators(conn, movie_id, session_id=sid, when=now)


def _delete_torrents_with_audit(
    conn,
    movie_id: int,
    *,
    sub_ind: int,
    cen_ind: int,
    session_id: Optional[int],
    when: Optional[str],
) -> None:
    """Delete TorrentHistory rows matching ``(movie_id, sub_ind, cen_ind)``,
    capturing each as a 'DELETE' audit row so rollback can re-insert them.
    """
    if session_id is None:
        conn.execute(
            "DELETE FROM TorrentHistory WHERE MovieHistoryId=? "
            "AND SubtitleIndicator=? AND CensorIndicator=?",
            (movie_id, sub_ind, cen_ind),
        )
        return
    rows = conn.execute(
        "SELECT * FROM TorrentHistory WHERE MovieHistoryId=? "
        "AND SubtitleIndicator=? AND CensorIndicator=?",
        (movie_id, sub_ind, cen_ind),
    ).fetchall()
    for row in rows:
        _audit_record_torrent_change(
            conn, row['Id'], action='DELETE', session_id=session_id,
            old_row=row, when=when,
        )
    if rows:
        conn.execute(
            "DELETE FROM TorrentHistory WHERE MovieHistoryId=? "
            "AND SubtitleIndicator=? AND CensorIndicator=?",
            (movie_id, sub_ind, cen_ind),
        )


def _update_movie_indicators(
    conn,
    movie_id: int,
    *,
    session_id: Optional[int] = None,
    when: Optional[str] = None,
):
    """Recompute PerfectMatchIndicator and HiResIndicator for a movie.

    When ``session_id`` is provided, the audit log captures the prior
    ``MovieHistory`` row so rollback can restore the original indicators
    along with everything else.
    """
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

    perfect_val = 1 if perfect else 0
    hires_val = 1 if hires else 0

    if session_id is not None:
        old_full = conn.execute(
            "SELECT * FROM MovieHistory WHERE Id=?", (movie_id,),
        ).fetchone()
        if (
            old_full is not None
            and (
                (old_full['PerfectMatchIndicator'] or 0) != perfect_val
                or (old_full['HiResIndicator'] or 0) != hires_val
            )
        ):
            conn.execute(
                """UPDATE MovieHistory SET PerfectMatchIndicator=?,
                   HiResIndicator=?, SessionId=? WHERE Id=?""",
                (perfect_val, hires_val, session_id, movie_id),
            )
            _audit_record_movie_change(
                conn, movie_id, action='UPDATE', session_id=session_id,
                old_row=old_full, when=when,
            )
        return

    conn.execute(
        "UPDATE MovieHistory SET PerfectMatchIndicator=?, HiResIndicator=? WHERE Id=?",
        (perfect_val, hires_val, movie_id),
    )


def db_batch_update_last_visited(
    hrefs: List[str],
    db_path: Optional[str] = None,
    session_id: Any = _SESSION_ID_SENTINEL,
) -> int:
    """Update DateTimeVisited for a batch of hrefs.

    When *session_id* is set (or :func:`get_active_session_id` returns a
    value), each affected MovieHistory row also gets ``SessionId=?`` and a
    companion ``MovieHistoryAudit`` row capturing the prior state so the
    visit timestamp change can be rolled back.
    """
    if not hrefs:
        return 0
    sid = _resolve_session_id(session_id)
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
    # D1 caps bound parameters at ~100 per statement, so chunk the IN-list.
    # Leave 2 slots for ``now`` and ``SessionId``; use 90 hrefs per batch for safety.
    CHUNK = 90
    total = 0
    with get_db(db_path or HISTORY_DB_PATH) as conn:
        for i in range(0, len(lookup_hrefs), CHUNK):
            chunk = lookup_hrefs[i:i + CHUNK]
            placeholders = ','.join('?' for _ in chunk)
            if sid is not None:
                # Snapshot affected rows BEFORE the update so we can audit.
                affected_rows = conn.execute(
                    f"SELECT * FROM MovieHistory WHERE Href IN ({placeholders})",
                    chunk,
                ).fetchall()
                for row in affected_rows:
                    _audit_record_movie_change(
                        conn, row['Id'], action='UPDATE',
                        session_id=sid, old_row=row, when=now,
                    )
                cur = conn.execute(
                    f"UPDATE MovieHistory SET DateTimeVisited=?, SessionId=? "
                    f"WHERE Href IN ({placeholders})",
                    [now, sid] + chunk,
                )
            else:
                cur = conn.execute(
                    f"UPDATE MovieHistory SET DateTimeVisited=? WHERE Href IN ({placeholders})",
                    [now] + chunk,
                )
            total += cur.rowcount or 0
        return total


def db_batch_update_movie_actors(
    updates: List[Tuple[str, str, str, str, str]],
    db_path: Optional[str] = None,
    session_id: Any = _SESSION_ID_SENTINEL,
) -> int:
    """Set actor columns and DateTimeUpdated for each
    ``(href, actor_name, actor_gender, actor_link, supporting_actors)``.

    Returns the number of rows matched by UPDATE (may be 0 for unknown hrefs).

    When *session_id* is set (or :func:`get_active_session_id` returns a
    value), each affected MovieHistory row also gets ``SessionId=?`` and a
    companion ``MovieHistoryAudit`` row capturing the prior state.
    """
    if not updates:
        return 0
    sid = _resolve_session_id(session_id)
    with get_db(db_path or HISTORY_DB_PATH) as conn:
        return _batch_update_movie_actors(
            conn, updates,
            session_id=sid,
            audit_record_movie_change=_audit_record_movie_change,
        )


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

def db_replace_rclone_inventory(
    entries: List[dict],
    db_path: Optional[str] = None,
    session_id: Any = _SESSION_ID_SENTINEL,
) -> int:
    """Replace the entire RcloneInventory table (full scan refresh).

    When *session_id* is provided the staging-then-swap pattern is used:
    rows go to ``RcloneInventoryStaging_<session_id>`` first and only
    swap into the live table once everything has been written. A failed
    or stalled run leaves the main table untouched.
    """
    sid = _resolve_session_id(session_id)
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        return _replace_rclone_inventory(conn, entries, session_id=sid)


def db_open_rclone_staging(
    session_id: Any = _SESSION_ID_SENTINEL,
    db_path: Optional[str] = None,
) -> Optional[str]:
    """Initialise this session's RcloneInventory staging table.

    Returns the staging table name, or ``None`` when no session_id is
    available — callers in that case should keep using the legacy
    clear+append flow.
    """
    sid = _resolve_session_id(session_id)
    if sid is None:
        return None
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        return _open_rclone_staging(conn, sid)


def db_append_rclone_staging(
    entries: List[dict],
    session_id: Any = _SESSION_ID_SENTINEL,
    db_path: Optional[str] = None,
) -> int:
    """Append rows to this session's RcloneInventory staging table."""
    if not entries:
        return 0
    sid = _resolve_session_id(session_id)
    if sid is None:
        # No active session — fall back to direct main-table append so
        # callers that opted out of rollback still work.
        return db_append_rclone_inventory(entries, db_path=db_path)
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        return _append_rclone_staging(conn, entries, sid)


def db_swap_rclone_inventory(
    session_id: Any = _SESSION_ID_SENTINEL,
    db_path: Optional[str] = None,
) -> int:
    """Atomically swap this session's staging into the live RcloneInventory."""
    sid = _resolve_session_id(session_id)
    if sid is None:
        raise ValueError(
            "db_swap_rclone_inventory requires an active session_id "
            "(set via set_active_session_id or pass explicitly)."
        )
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        return _swap_rclone_inventory(conn, sid)


def db_drop_rclone_staging(
    session_id: int,
    db_path: Optional[str] = None,
) -> None:
    """DROP TABLE IF EXISTS RcloneInventoryStaging_<session_id> (idempotent)."""
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        _drop_rclone_staging(conn, session_id)


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


def db_delete_rclone_inventory_paths(
    paths: Iterable[str],
    db_path: Optional[str] = None,
) -> int:
    """Bulk delete RcloneInventory rows by FolderPath. Returns affected row count.

    Uses chunked ``IN (...)`` deletes so each chunk is a single statement
    (one D1 round-trip per chunk instead of one per path). The chunk size
    matches :func:`db_batch_update_last_visited` — D1 caps bound parameters
    at ~100 per statement, so we use 90 placeholders per batch for safety.
    """
    path_list = [p for p in paths if p]
    if not path_list:
        return 0
    CHUNK = 90
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        deleted = 0
        for i in range(0, len(path_list), CHUNK):
            chunk = path_list[i:i + CHUNK]
            placeholders = ','.join('?' for _ in chunk)
            cur = conn.execute(
                f"DELETE FROM RcloneInventory WHERE FolderPath IN ({placeholders})",
                chunk,
            )
            deleted += cur.rowcount or 0
        return deleted


# ── DedupRecords helpers ─────────────────────────────────────────────────

def db_load_dedup_records(db_path: Optional[str] = None) -> List[dict]:
    """Load all dedup records."""
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        rows = conn.execute("SELECT * FROM DedupRecords ORDER BY Id").fetchall()
        return [dict(r) for r in rows]


_DEDUP_RECORD_COLUMNS = (
    'VideoCode',
    'ExistingSensor',
    'ExistingSubtitle',
    'ExistingGdrivePath',
    'ExistingFolderSize',
    'NewTorrentCategory',
    'DeletionReason',
    'DateTimeDetected',
    'IsDeleted',
    'DateTimeDeleted',
    'SessionId',
)


def _dedup_rollback_table(session_id: int) -> str:
    return f"DedupRecordsRollback_{int(session_id)}"


def _dedup_rollback_table_exists(conn, session_id: int) -> bool:
    table = _dedup_rollback_table(session_id)
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _ensure_dedup_rollback_table(conn, session_id: int) -> str:
    table = _dedup_rollback_table(session_id)
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS {table} (
            DedupRecordId INTEGER PRIMARY KEY,
            OldRowJson TEXT NOT NULL
        )"""
    )
    return table


def _snapshot_dedup_rows_for_rollback(conn, session_id: Optional[int], rows) -> None:
    if session_id is None or not rows:
        return
    table = _ensure_dedup_rollback_table(conn, session_id)
    conn.executemany(
        f"INSERT OR IGNORE INTO {table} (DedupRecordId, OldRowJson) VALUES (?, ?)",
        [
            (
                row['Id'],
                json.dumps(dict(row), ensure_ascii=False),
            )
            for row in rows
        ],
    )


def _same_session_id(value, session_id: int) -> bool:
    if value is None:
        return False
    try:
        return int(value) == int(session_id)
    except (TypeError, ValueError):
        return False


def _restore_dedup_records_from_rollback(conn, session_id: int) -> Tuple[int, int]:
    table = _dedup_rollback_table(session_id)
    if not _dedup_rollback_table_exists(conn, session_id):
        return 0, 0
    rows = conn.execute(
        f"SELECT DedupRecordId, OldRowJson FROM {table} ORDER BY DedupRecordId"
    ).fetchall()
    restored = 0
    skipped = 0
    for row in rows:
        try:
            old = json.loads(row['OldRowJson'])
        except (TypeError, ValueError) as e:
            skipped += 1
            logger.warning(
                "Malformed DedupRecords rollback backup for Id=%s: %s",
                row['DedupRecordId'], e,
            )
            continue

        if _same_session_id(old.get('SessionId'), session_id):
            # The row was created by this same session and should be removed
            # by the session-scoped DELETE below, not restored.
            continue

        set_clause = ', '.join(f'{col}=?' for col in _DEDUP_RECORD_COLUMNS)
        params = [old.get(col) for col in _DEDUP_RECORD_COLUMNS]
        params.extend([row['DedupRecordId'], session_id])
        cur = conn.execute(
            f"UPDATE DedupRecords SET {set_clause} WHERE Id=? AND SessionId=?",
            params,
        )
        if (cur.rowcount or 0) > 0:
            restored += 1
        else:
            skipped += 1
            logger.warning(
                "Rollback drift: DedupRecords row Id=%s SessionId mismatch "
                "or row already gone",
                row['DedupRecordId'],
            )
    return restored, skipped


def db_append_dedup_record(
    record: dict,
    db_path: Optional[str] = None,
    session_id: Any = _SESSION_ID_SENTINEL,
) -> int:
    """Append a single dedup record. Returns the new row id, or -1 if duplicate.

    *session_id*: tags the row for X3 rollback; defaults to
    :func:`get_active_session_id`. Pass ``None`` explicitly for ad-hoc
    backfills that should not be rolled back with the current run.
    """
    sid = _resolve_session_id(session_id)
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO DedupRecords
               (VideoCode, ExistingSensor, ExistingSubtitle,
                ExistingGdrivePath, ExistingFolderSize,
                NewTorrentCategory, DeletionReason,
                DateTimeDetected, IsDeleted, DateTimeDeleted, SessionId)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (record.get('VideoCode', record.get('video_code')),
             record.get('ExistingSensor', record.get('existing_sensor')),
             record.get('ExistingSubtitle', record.get('existing_subtitle')),
             record.get('ExistingGdrivePath', record.get('existing_gdrive_path')),
             int(record.get('ExistingFolderSize', record.get('existing_folder_size', 0)) or 0),
             record.get('NewTorrentCategory', record.get('new_torrent_category')),
             record.get('DeletionReason', record.get('deletion_reason')),
             record.get('DateTimeDetected', record.get('detect_datetime')),
             1 if str(record.get('IsDeleted', record.get('is_deleted', 'False'))).lower() in ('true', '1') else 0,
             record.get('DateTimeDeleted', record.get('delete_datetime')),
             sid),
        )
        if cur.rowcount == 0:
            return -1
        return cur.lastrowid


def db_mark_records_deleted(
    path_datetime_pairs: List[Tuple[str, str]],
    db_path: Optional[str] = None,
    session_id: Any = _SESSION_ID_SENTINEL,
) -> int:
    """Mark specific dedup records as deleted by gdrive path.

    Real-world callers (e.g. the rclone purge loop) use a single
    ``DateTimeDeleted`` value for all paths in one batch, so we group by
    datetime and issue one chunked ``IN (...)`` update per group instead of
    one statement per pair. The chunk size matches
    :func:`db_batch_update_last_visited` (90 placeholders per batch, leaving
    headroom under D1's ~100-parameter cap once the ``DateTimeDeleted`` slot
    is reserved).
    """
    if not path_datetime_pairs:
        return 0
    grouped: Dict[str, List[str]] = {}
    for path, dt in path_datetime_pairs:
        if not path:
            continue
        grouped.setdefault(dt, []).append(path)
    if not grouped:
        return 0
    sid = _resolve_session_id(session_id)
    CHUNK = 90
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        updated = 0
        for dt, paths in grouped.items():
            for i in range(0, len(paths), CHUNK):
                chunk = paths[i:i + CHUNK]
                placeholders = ','.join('?' for _ in chunk)
                if sid is not None:
                    rows = conn.execute(
                        f"SELECT * FROM DedupRecords "
                        f"WHERE ExistingGdrivePath IN ({placeholders}) AND IsDeleted=0",
                        chunk,
                    ).fetchall()
                    _snapshot_dedup_rows_for_rollback(conn, sid, rows)
                    cur = conn.execute(
                        f"UPDATE DedupRecords "
                        f"SET IsDeleted=1, DateTimeDeleted=?, SessionId=? "
                        f"WHERE ExistingGdrivePath IN ({placeholders}) AND IsDeleted=0",
                        [dt, sid] + chunk,
                    )
                else:
                    cur = conn.execute(
                        f"UPDATE DedupRecords SET IsDeleted=1, DateTimeDeleted=? "
                        f"WHERE ExistingGdrivePath IN ({placeholders}) AND IsDeleted=0",
                        [dt] + chunk,
                    )
                updated += cur.rowcount or 0
        return updated


def db_mark_orphan_records(
    paths: Iterable[str],
    reason_suffix: str,
    when: str,
    db_path: Optional[str] = None,
    session_id: Any = _SESSION_ID_SENTINEL,
) -> int:
    """Mark dedup pending rows as deleted with custom reason suffix appended.

    For each path in ``paths`` whose ``DedupRecords`` row has ``IsDeleted=0``:
      - sets ``IsDeleted=1`` and ``DateTimeDeleted=when``
      - appends ``reason_suffix`` to the existing ``DeletionReason`` (space-
        separated). If ``DeletionReason`` is NULL/empty, it becomes the suffix.

    Returns total affected row count.
    """
    path_list = [p for p in paths if p]
    if not path_list:
        return 0
    sid = _resolve_session_id(session_id)
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        updated = 0
        for path in path_list:
            if sid is not None:
                rows = conn.execute(
                    "SELECT * FROM DedupRecords "
                    "WHERE ExistingGdrivePath = ? AND IsDeleted = 0",
                    (path,),
                ).fetchall()
                _snapshot_dedup_rows_for_rollback(conn, sid, rows)
                cur = conn.execute(
                    """UPDATE DedupRecords
                       SET IsDeleted = 1,
                           DateTimeDeleted = ?,
                           DeletionReason = TRIM(
                             COALESCE(DeletionReason, '') || ' ' || ?
                           ),
                           SessionId = ?
                       WHERE ExistingGdrivePath = ? AND IsDeleted = 0""",
                    (when, reason_suffix, sid, path),
                )
            else:
                cur = conn.execute(
                    """UPDATE DedupRecords
                       SET IsDeleted = 1,
                           DateTimeDeleted = ?,
                           DeletionReason = TRIM(
                             COALESCE(DeletionReason, '') || ' ' || ?
                           )
                       WHERE ExistingGdrivePath = ? AND IsDeleted = 0""",
                    (when, reason_suffix, path),
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

def db_append_pikpak_history(
    record: dict,
    db_path: Optional[str] = None,
    session_id: Any = _SESSION_ID_SENTINEL,
) -> int:
    """Append a PikPak transfer record.

    *session_id*: tags the row for X3 rollback; defaults to
    :func:`get_active_session_id`.
    """
    sid = _resolve_session_id(session_id)
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        cur = conn.execute(
            """INSERT INTO PikpakHistory
               (TorrentHash, TorrentName, Category, MagnetUri,
                DateTimeAddedToQb, DateTimeDeletedFromQb,
                DateTimeUploadedToPikpak, TransferStatus, ErrorMessage,
                SessionId)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (record.get('TorrentHash', record.get('torrent_hash')),
             record.get('TorrentName', record.get('torrent_name')),
             record.get('Category', record.get('category')),
             record.get('MagnetUri', record.get('magnet_uri')),
             record.get('DateTimeAddedToQb', record.get('added_to_qb_date')),
             record.get('DateTimeDeletedFromQb', record.get('deleted_from_qb_date')),
             record.get('DateTimeUploadedToPikpak', record.get('uploaded_to_pikpak_date')),
             record.get('TransferStatus', record.get('transfer_status')),
             record.get('ErrorMessage', record.get('error_message')),
             sid),
        )
        return cur.lastrowid


# ── InventoryAlignNoExactMatch helpers ───────────────────────────────────

def db_upsert_align_no_exact_match(
    video_code: str,
    reason: str = 'exact_video_code_not_found',
    db_path: Optional[str] = None,
    session_id: Any = _SESSION_ID_SENTINEL,
) -> None:
    """Record a video code that had no exact match on JavDB search.

    *session_id*: tags the row for X3 rollback; defaults to
    :func:`get_active_session_id`. Note: this table uses INSERT OR
    REPLACE, so on conflict any prior session's tag is overwritten —
    rollback acceptably loses that earlier tag (the table is small and
    idempotent).
    """
    sid = _resolve_session_id(session_id)
    normalized = video_code.strip().upper()
    if not normalized:
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        conn.execute(
            """INSERT OR REPLACE INTO InventoryAlignNoExactMatch
               (VideoCode, Reason, DateTimeRecorded, SessionId)
               VALUES (?, ?, ?, ?)""",
            (normalized, reason, now, sid),
        )


def db_load_align_no_exact_match_codes(db_path: Optional[str] = None) -> set:
    """Return the set of normalised video codes previously marked as no-exact-match."""
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        rows = conn.execute(
            "SELECT VideoCode FROM InventoryAlignNoExactMatch"
        ).fetchall()
    return {r['VideoCode'] for r in rows}


def db_delete_align_no_exact_match(
    video_code: str,
    db_path: Optional[str] = None,
) -> None:
    """Remove a video code from the no-exact-match table (e.g. after a successful match)."""
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        conn.execute(
            "DELETE FROM InventoryAlignNoExactMatch WHERE VideoCode = ?",
            (video_code.strip().upper(),),
        )


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
    """Create a new report session and return its id.

    The new row is tagged with ``Status='in_progress'`` so the rollback CLI
    can identify uncommitted runs. Call :func:`db_mark_session_committed`
    after the pipeline successfully finishes to flip the flag and protect
    the session's writes from being cleaned up.
    """
    if created_at is None:
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        cur = conn.execute(
            """INSERT INTO ReportSessions
               (ReportType, ReportDate, UrlType, DisplayName,
                Url, StartPage, EndPage, CsvFilename, DateTimeCreated, Status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'in_progress')""",
            (report_type, report_date, url_type, display_name,
             url, start_page, end_page, csv_filename, created_at),
        )
        return cur.lastrowid


def db_mark_session_committed(
    session_id: int,
    db_path: Optional[str] = None,
) -> int:
    """Mark a session as ``committed`` so it survives any future cleanup.

    Returns the number of rows updated (0 if session not found / already
    committed). Idempotent: re-running on a committed session is a no-op.
    """
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        cur = conn.execute(
            "UPDATE ReportSessions SET Status='committed' WHERE Id=? "
            "AND Status IS NOT 'committed'",
            (session_id,),
        )
        return cur.rowcount or 0


def db_mark_session_failed(
    session_id: int,
    db_path: Optional[str] = None,
) -> int:
    """Mark a session as ``failed`` (debug-only flag set right before delete)."""
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        cur = conn.execute(
            "UPDATE ReportSessions SET Status='failed' WHERE Id=?",
            (session_id,),
        )
        return cur.rowcount or 0


def db_find_in_progress_sessions(
    *,
    since: Optional[str] = None,
    db_path: Optional[str] = None,
) -> List[int]:
    """Return ``ReportSessions.Id`` rows still flagged ``in_progress``.

    *since* (ISO timestamp) restricts the search to sessions created on
    or after the given moment — typically the workflow ``run_started_at``
    so the cleanup job only sees sessions belonging to the failed run.
    """
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        if since:
            rows = conn.execute(
                "SELECT Id FROM ReportSessions "
                "WHERE Status='in_progress' AND DateTimeCreated >= ?",
                (since,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT Id FROM ReportSessions WHERE Status='in_progress'",
            ).fetchall()
    return [r['Id'] for r in rows]


# ── Rollback orchestration (X3 hybrid) ───────────────────────────────────

def _rollback_reports(
    session_id: int,
    *,
    dry_run: bool,
    db_path: Optional[str] = None,
) -> Dict[str, int]:
    """Delete all reports-DB rows tagged with *session_id*.

    Returns a dict of ``{table: rows_affected}`` for logging / dry-run.
    """
    counts: Dict[str, int] = {}
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        if dry_run:
            counts['ReportTorrents'] = (conn.execute(
                "SELECT COUNT(*) AS n FROM ReportTorrents "
                "WHERE ReportMovieId IN (SELECT Id FROM ReportMovies WHERE SessionId=?)",
                (session_id,),
            ).fetchone() or {'n': 0})['n']
            for table in (
                'ReportMovies', 'SpiderStats', 'UploaderStats',
                'PikpakStats',
            ):
                counts[table] = (conn.execute(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE SessionId=?",
                    (session_id,),
                ).fetchone() or {'n': 0})['n']
            counts['ReportSessions'] = (conn.execute(
                "SELECT COUNT(*) AS n FROM ReportSessions "
                "WHERE Id=? AND Status IS NOT 'committed'",
                (session_id,),
            ).fetchone() or {'n': 0})['n']
            return counts

        counts['ReportTorrents'] = (conn.execute(
            "DELETE FROM ReportTorrents "
            "WHERE ReportMovieId IN (SELECT Id FROM ReportMovies WHERE SessionId=?)",
            (session_id,),
        ).rowcount or 0)
        for table in (
            'ReportMovies', 'SpiderStats', 'UploaderStats',
            'PikpakStats',
        ):
            counts[table] = (conn.execute(
                f"DELETE FROM {table} WHERE SessionId=?", (session_id,),
            ).rowcount or 0)
        # Only delete the ReportSessions row if it isn't committed (so a
        # late-arriving rollback can never wipe a successful run).
        counts['ReportSessions'] = (conn.execute(
            "DELETE FROM ReportSessions "
            "WHERE Id=? AND Status IS NOT 'committed'",
            (session_id,),
        ).rowcount or 0)
    return counts


def _rollback_operations(
    session_id: int,
    *,
    dry_run: bool,
    db_path: Optional[str] = None,
) -> Dict[str, int]:
    """Delete operations-DB rows tagged with *session_id* and DROP its staging."""
    counts: Dict[str, int] = {}
    staging_table = f"RcloneInventoryStaging_{int(session_id)}"
    dedup_backup_table = _dedup_rollback_table(session_id)
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        op_specs = [
            ('PikpakHistory', "DELETE FROM PikpakHistory WHERE SessionId=?"),
            ('DedupRecords',
             "DELETE FROM DedupRecords WHERE SessionId=?"),
            ('InventoryAlignNoExactMatch',
             "DELETE FROM InventoryAlignNoExactMatch WHERE SessionId=?"),
        ]
        if dry_run:
            for table, _ in op_specs:
                where = "WHERE SessionId=?"
                counts[table] = (conn.execute(
                    f"SELECT COUNT(*) AS n FROM {table} {where}",
                    (session_id,),
                ).fetchone() or {'n': 0})['n']
            if _dedup_rollback_table_exists(conn, session_id):
                counts['DedupRecords.restored'] = (conn.execute(
                    f"SELECT COUNT(*) AS n FROM {dedup_backup_table}",
                ).fetchone() or {'n': 0})['n']
            else:
                counts['DedupRecords.restored'] = 0
            counts[staging_table] = 0
            try:
                row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (staging_table,),
                ).fetchone()
                if row:
                    counts[staging_table] = 1  # would DROP this many tables
            except Exception:
                pass
            counts[dedup_backup_table] = 1 if _dedup_rollback_table_exists(
                conn, session_id,
            ) else 0
            return counts

        restored, restore_skipped = _restore_dedup_records_from_rollback(
            conn, session_id,
        )
        counts['DedupRecords.restored'] = restored
        counts['DedupRecords.restore_skipped'] = restore_skipped
        for table, sql in op_specs:
            counts[table] = (conn.execute(sql, (session_id,)).rowcount or 0)
        if restore_skipped == 0:
            try:
                conn.execute(f"DROP TABLE IF EXISTS {dedup_backup_table}")
                counts[dedup_backup_table] = 1
            except Exception as e:
                logger.warning(
                    f"DROP TABLE {dedup_backup_table} failed during rollback: {e}"
                )
                counts[dedup_backup_table] = 0
        else:
            counts[dedup_backup_table] = 0
        try:
            conn.execute(f"DROP TABLE IF EXISTS {staging_table}")
            counts[staging_table] = 1
        except Exception as e:
            logger.warning(
                f"DROP TABLE {staging_table} failed during rollback: {e}"
            )
            counts[staging_table] = 0
    return counts


def _rollback_history(
    session_id: int,
    *,
    dry_run: bool,
    db_path: Optional[str] = None,
) -> Dict[str, int]:
    """Reverse-apply MovieHistoryAudit + TorrentHistoryAudit for *session_id*.

    Logic:
      - Action='INSERT' → DELETE FROM <main> WHERE Id=TargetId
      - Action='UPDATE' → restore main row from OldRowJson WHERE Id=TargetId
        (only if the row's current SessionId matches; otherwise log drift)
      - Action='DELETE' → re-INSERT main row from OldRowJson
    Audit rows must be replayed in *reverse* order (highest Id first) so
    multi-step audits applied in the same run unwind correctly.

    After replay, audit rows for this session are deleted to keep the
    table tidy.
    """
    counts: Dict[str, int] = {
        'MovieHistoryAudit': 0,
        'TorrentHistoryAudit': 0,
        'MovieHistory.deleted': 0,
        'MovieHistory.restored': 0,
        'TorrentHistory.deleted': 0,
        'TorrentHistory.restored': 0,
        'MovieHistory.reinserted': 0,
        'TorrentHistory.reinserted': 0,
        'drift_skipped': 0,
    }
    with get_db(db_path or HISTORY_DB_PATH) as conn:
        for kind, audit_table, main_table in (
            ('movie', 'MovieHistoryAudit', 'MovieHistory'),
            ('torrent', 'TorrentHistoryAudit', 'TorrentHistory'),
        ):
            audit_rows = conn.execute(
                f"SELECT Id, TargetId, Action, OldRowJson FROM {audit_table} "
                f"WHERE SessionId=? ORDER BY Id DESC",
                (session_id,),
            ).fetchall()
            counts[audit_table] = len(audit_rows)
            if dry_run or not audit_rows:
                continue
            applied_ids: List[int] = []
            for row in audit_rows:
                audit_id = int(row['Id'])
                action = row['Action']
                target_id = row['TargetId']
                old_json = row['OldRowJson']
                try:
                    if action == 'INSERT':
                        # Only delete if the current row is still tagged with
                        # this session; otherwise another run later updated
                        # it and we must not erase their work.
                        cur = conn.execute(
                            f"DELETE FROM {main_table} "
                            f"WHERE Id=? AND SessionId=?",
                            (target_id, session_id),
                        )
                        if (cur.rowcount or 0) > 0:
                            counts[f'{main_table}.deleted'] += 1
                            applied_ids.append(audit_id)
                        else:
                            counts['drift_skipped'] += 1
                            logger.warning(
                                "Rollback drift: %s row Id=%s SessionId mismatch "
                                "or row already gone (action=INSERT)",
                                main_table, target_id,
                            )
                    elif action == 'UPDATE':
                        if not old_json:
                            counts['drift_skipped'] += 1
                            continue
                        old = json.loads(old_json)
                        # Build column list dynamically to support both tables.
                        cols = [c for c in old.keys() if c != 'Id']
                        set_clause = ', '.join(f'{c}=?' for c in cols)
                        params = [old[c] for c in cols] + [target_id, session_id]
                        cur = conn.execute(
                            f"UPDATE {main_table} SET {set_clause} "
                            f"WHERE Id=? AND SessionId=?",
                            params,
                        )
                        if (cur.rowcount or 0) > 0:
                            counts[f'{main_table}.restored'] += 1
                            applied_ids.append(audit_id)
                        else:
                            # Concurrent run touched the row after us; can't
                            # safely overwrite their state — log drift.
                            counts['drift_skipped'] += 1
                            logger.warning(
                                "Rollback drift: %s row Id=%s SessionId mismatch "
                                "(action=UPDATE) — manual review needed",
                                main_table, target_id,
                            )
                    elif action == 'DELETE':
                        if not old_json:
                            counts['drift_skipped'] += 1
                            continue
                        old = json.loads(old_json)
                        cols = list(old.keys())
                        placeholders = ', '.join('?' for _ in cols)
                        col_names = ', '.join(cols)
                        params = [old[c] for c in cols]
                        try:
                            conn.execute(
                                f"INSERT INTO {main_table} ({col_names}) "
                                f"VALUES ({placeholders})",
                                params,
                            )
                            counts[f'{main_table}.reinserted'] += 1
                            applied_ids.append(audit_id)
                        except sqlite3.IntegrityError as e:
                            # E.g. UNIQUE conflict — concurrent run reinserted
                            # something with the same key. Skip + drift log.
                            counts['drift_skipped'] += 1
                            logger.warning(
                                "Rollback drift: cannot re-insert %s row "
                                "(action=DELETE): %s", main_table, e,
                            )
                except Exception as e:
                    counts['drift_skipped'] += 1
                    logger.error(
                        "Rollback step failed (table=%s action=%s id=%s): %s",
                        main_table, action, target_id, e,
                    )

            if applied_ids:
                # Tidy only the audit rows that replayed successfully. Any
                # drifted tail remains for manual recovery.
                for i in range(0, len(applied_ids), 900):
                    chunk = applied_ids[i:i + 900]
                    placeholders = ', '.join('?' for _ in chunk)
                    conn.execute(
                        f"DELETE FROM {audit_table} WHERE Id IN ({placeholders})",
                        tuple(chunk),
                    )
            if len(applied_ids) != len(audit_rows):
                logger.warning(
                    "Kept %s unapplied %s row(s) for SessionId=%s because "
                    "rollback encountered drift or row-level errors",
                    len(audit_rows) - len(applied_ids), audit_table, session_id,
                )
    return counts


def db_rollback_session(
    session_id: int,
    *,
    dry_run: bool = False,
    scope: str = 'all',
    force: bool = False,
    history_db_path: Optional[str] = None,
    reports_db_path: Optional[str] = None,
    operations_db_path: Optional[str] = None,
) -> Dict[str, Dict[str, int]]:
    """Roll back all D1/SQLite writes that belong to *session_id*.

    Performs deletions in the order *reports → operations → history* so
    foreign-key like dependencies are unwound cleanly. The history scope
    walks ``MovieHistoryAudit`` / ``TorrentHistoryAudit`` in reverse Id
    order and replays each row (INSERT→DELETE, UPDATE→restore from
    OldRowJson, DELETE→re-INSERT).

    *scope* may be one of ``'reports'``, ``'operations'``, ``'history'``,
    or ``'all'`` (default). Useful for partial rollbacks during incident
    response.

    *force=False* (default) refuses to operate on a session whose
    ``ReportSessions.Status='committed'`` to prevent accidental data loss
    on successful runs. Set ``force=True`` for explicit recovery
    scenarios (the manual workflow exposes this as an opt-in flag).

    Marks the ``ReportSessions`` row ``Status='failed'`` BEFORE the
    deletions for traceability (committed sessions are intentionally
    skipped — :func:`_rollback_reports` won't delete them and the audit
    rows never touch them either).

    Returns a nested dict of ``{scope: {table: rows_affected}}`` suitable
    for logging or dry-run output.
    """
    if scope not in ('reports', 'operations', 'history', 'all'):
        raise ValueError(
            f"Unknown rollback scope {scope!r}; "
            "expected one of reports/operations/history/all"
        )

    # Refuse to roll back committed sessions unless explicitly forced.
    with get_db(reports_db_path or REPORTS_DB_PATH) as conn:
        row = conn.execute(
            "SELECT Status FROM ReportSessions WHERE Id=?", (session_id,),
        ).fetchone()
    current_status = row['Status'] if row else None
    if current_status == 'committed' and not force:
        raise ValueError(
            f"Refusing to roll back ReportSessions.Id={session_id} because "
            f"Status='committed'. Pass force=True if you really intend to "
            f"undo a successful run's writes."
        )

    if not dry_run:
        # Best-effort flag — failure here shouldn't block the rollback.
        try:
            db_mark_session_failed(session_id, db_path=reports_db_path)
        except Exception as e:
            logger.warning(
                f"Could not mark session {session_id} as failed "
                f"before rollback: {e}"
            )

    result: Dict[str, Dict[str, int]] = {}
    if scope in ('reports', 'all'):
        result['reports'] = _rollback_reports(
            session_id, dry_run=dry_run, db_path=reports_db_path,
        )
    if scope in ('operations', 'all'):
        result['operations'] = _rollback_operations(
            session_id, dry_run=dry_run, db_path=operations_db_path,
        )
    if scope in ('history', 'all'):
        result['history'] = _rollback_history(
            session_id, dry_run=dry_run, db_path=history_db_path,
        )
    return result


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
