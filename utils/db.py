"""SQLite database management layer for JAVDB AutoSpider.

Replaces CSV-based storage with a single SQLite database file.
All tables are created on first access; WAL mode is enabled for
concurrent-read safety.
"""

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from utils.config_helper import cfg
from utils.logging_config import get_logger

logger = get_logger(__name__)

_REPORTS_DIR = cfg('REPORTS_DIR', 'reports')
DB_PATH = cfg('SQLITE_DB_PATH', os.path.join(_REPORTS_DIR, 'javdb_autospider.db'))

SCHEMA_VERSION = 1

# ── Connection management ────────────────────────────────────────────────

_local = threading.local()


def _get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Return a thread-local connection, creating it if needed."""
    path = db_path or DB_PATH
    conn = getattr(_local, 'conn', None)
    conn_path = getattr(_local, 'conn_path', None)
    if conn is None or conn_path != path:
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        conn = sqlite3.connect(path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        _local.conn = conn
        _local.conn_path = path
    return conn


@contextmanager
def get_db(db_path: Optional[str] = None):
    """Context manager yielding a SQLite connection with auto-commit."""
    conn = _get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def close_db():
    """Close the thread-local connection (call before process exit)."""
    conn = getattr(_local, 'conn', None)
    if conn is not None:
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
        conn.close()
        _local.conn = None
        _local.conn_path = None


# ── Schema DDL ───────────────────────────────────────────────────────────

_TABLES_SQL = """
-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

-- 1. parsed_movies_history (replaces parsed_movies_history.csv)
CREATE TABLE IF NOT EXISTS parsed_movies_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    href TEXT NOT NULL UNIQUE,
    phase INTEGER,
    video_code TEXT NOT NULL,
    create_datetime TEXT,
    update_datetime TEXT,
    last_visited_datetime TEXT,
    hacked_subtitle TEXT DEFAULT '',
    hacked_no_subtitle TEXT DEFAULT '',
    subtitle TEXT DEFAULT '',
    no_subtitle TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_history_video_code ON parsed_movies_history(video_code);
CREATE INDEX IF NOT EXISTS idx_history_phase ON parsed_movies_history(phase);

-- 2. rclone_inventory (replaces rclone_inventory.csv)
CREATE TABLE IF NOT EXISTS rclone_inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_code TEXT NOT NULL,
    sensor_category TEXT DEFAULT '',
    subtitle_category TEXT DEFAULT '',
    folder_path TEXT DEFAULT '',
    folder_size INTEGER DEFAULT 0,
    file_count INTEGER DEFAULT 0,
    scan_datetime TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_inventory_video_code ON rclone_inventory(video_code);

-- 3. dedup_records (replaces dedup.csv)
CREATE TABLE IF NOT EXISTS dedup_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_code TEXT DEFAULT '',
    existing_sensor TEXT DEFAULT '',
    existing_subtitle TEXT DEFAULT '',
    existing_gdrive_path TEXT DEFAULT '',
    existing_folder_size INTEGER DEFAULT 0,
    new_torrent_category TEXT DEFAULT '',
    deletion_reason TEXT DEFAULT '',
    detect_datetime TEXT DEFAULT '',
    is_deleted INTEGER DEFAULT 0,
    delete_datetime TEXT DEFAULT ''
);

-- 4. pikpak_history (replaces pikpak_bridge_history.csv)
CREATE TABLE IF NOT EXISTS pikpak_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    torrent_hash TEXT DEFAULT '',
    torrent_name TEXT DEFAULT '',
    category TEXT DEFAULT '',
    magnet_uri TEXT DEFAULT '',
    added_to_qb_date TEXT DEFAULT '',
    deleted_from_qb_date TEXT DEFAULT '',
    uploaded_to_pikpak_date TEXT DEFAULT '',
    transfer_status TEXT DEFAULT '',
    error_message TEXT DEFAULT ''
);

-- 5. proxy_bans (replaces proxy_bans.csv)
CREATE TABLE IF NOT EXISTS proxy_bans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proxy_name TEXT DEFAULT '',
    ban_time TEXT DEFAULT '',
    unban_time TEXT DEFAULT ''
);

-- 6. report_sessions
CREATE TABLE IF NOT EXISTS report_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_type TEXT NOT NULL,
    report_date TEXT NOT NULL,
    url_type TEXT,
    display_name TEXT,
    url TEXT,
    start_page INTEGER,
    end_page INTEGER,
    csv_filename TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_type_date ON report_sessions(report_type, report_date);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_csv ON report_sessions(csv_filename);

-- 7. report_rows
CREATE TABLE IF NOT EXISTS report_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES report_sessions(id),
    href TEXT DEFAULT '',
    video_code TEXT DEFAULT '',
    page INTEGER,
    actor TEXT DEFAULT '',
    rate REAL,
    comment_number INTEGER,
    hacked_subtitle TEXT DEFAULT '',
    hacked_no_subtitle TEXT DEFAULT '',
    subtitle TEXT DEFAULT '',
    no_subtitle TEXT DEFAULT '',
    size_hacked_subtitle TEXT DEFAULT '',
    size_hacked_no_subtitle TEXT DEFAULT '',
    size_subtitle TEXT DEFAULT '',
    size_no_subtitle TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_rows_session ON report_rows(session_id);
CREATE INDEX IF NOT EXISTS idx_rows_video_code ON report_rows(video_code);

-- 8. spider_stats
CREATE TABLE IF NOT EXISTS spider_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES report_sessions(id),
    phase1_discovered INTEGER DEFAULT 0,
    phase1_processed  INTEGER DEFAULT 0,
    phase1_skipped    INTEGER DEFAULT 0,
    phase1_no_new     INTEGER DEFAULT 0,
    phase1_failed     INTEGER DEFAULT 0,
    phase2_discovered INTEGER DEFAULT 0,
    phase2_processed  INTEGER DEFAULT 0,
    phase2_skipped    INTEGER DEFAULT 0,
    phase2_no_new     INTEGER DEFAULT 0,
    phase2_failed     INTEGER DEFAULT 0,
    total_discovered  INTEGER DEFAULT 0,
    total_processed   INTEGER DEFAULT 0,
    total_skipped     INTEGER DEFAULT 0,
    total_no_new      INTEGER DEFAULT 0,
    total_failed      INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

-- 9. uploader_stats
CREATE TABLE IF NOT EXISTS uploader_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES report_sessions(id),
    total_torrents     INTEGER DEFAULT 0,
    duplicate_count    INTEGER DEFAULT 0,
    attempted          INTEGER DEFAULT 0,
    successfully_added INTEGER DEFAULT 0,
    failed_count       INTEGER DEFAULT 0,
    hacked_sub         INTEGER DEFAULT 0,
    hacked_nosub       INTEGER DEFAULT 0,
    subtitle_count     INTEGER DEFAULT 0,
    no_subtitle_count  INTEGER DEFAULT 0,
    success_rate       REAL DEFAULT 0.0,
    created_at TEXT DEFAULT (datetime('now'))
);

-- 10. pikpak_stats
CREATE TABLE IF NOT EXISTS pikpak_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES report_sessions(id),
    threshold_days   INTEGER DEFAULT 3,
    total_torrents   INTEGER DEFAULT 0,
    filtered_old     INTEGER DEFAULT 0,
    successful_count INTEGER DEFAULT 0,
    failed_count     INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


def init_db(db_path: Optional[str] = None):
    """Create all tables if they don't exist and set the schema version.

    In csv-only storage mode this is a no-op (no database file is created).
    """
    from utils.config_helper import use_sqlite
    if not use_sqlite():
        return
    with get_db(db_path) as conn:
        conn.executescript(_TABLES_SQL)
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        if row is None:
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        logger.debug(f"Database initialised at {db_path or DB_PATH} (schema v{SCHEMA_VERSION})")


# ── parsed_movies_history helpers ────────────────────────────────────────

def db_load_history(db_path: Optional[str] = None, phase: Optional[int] = None) -> Dict[str, dict]:
    """Load history into the same dict structure used by the CSV loader."""
    history: Dict[str, dict] = {}
    with get_db(db_path) as conn:
        if phase == 1:
            rows = conn.execute("SELECT * FROM parsed_movies_history WHERE phase != 2").fetchall()
        elif phase == 2:
            rows = conn.execute("SELECT * FROM parsed_movies_history").fetchall()
        else:
            rows = conn.execute("SELECT * FROM parsed_movies_history").fetchall()

    for row in rows:
        r = dict(row)
        torrent_types = []
        for cat in ('hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle'):
            val = (r.get(cat) or '').strip()
            if val and ('magnet:' in val):
                torrent_types.append(cat)

        history[r['href']] = {
            'phase': r['phase'],
            'video_code': r['video_code'],
            'create_datetime': r.get('create_datetime', ''),
            'update_datetime': r.get('update_datetime', ''),
            'last_visited_datetime': r.get('last_visited_datetime', ''),
            'torrent_types': torrent_types,
            'hacked_subtitle': r.get('hacked_subtitle', ''),
            'hacked_no_subtitle': r.get('hacked_no_subtitle', ''),
            'subtitle': r.get('subtitle', ''),
            'no_subtitle': r.get('no_subtitle', ''),
        }
    return history


def db_upsert_history(
    href: str,
    phase: int,
    video_code: str,
    magnet_links: Optional[Dict[str, str]] = None,
    db_path: Optional[str] = None,
) -> None:
    """Insert or update a single history record (replaces CSV full-rewrite)."""
    if magnet_links is None:
        magnet_links = {}

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today = datetime.now().strftime("%Y-%m-%d")

    with get_db(db_path) as conn:
        existing = conn.execute(
            "SELECT * FROM parsed_movies_history WHERE href = ?", (href,)
        ).fetchone()

        if existing is None:
            cols = {
                'href': href,
                'phase': phase,
                'video_code': video_code,
                'create_datetime': now,
                'update_datetime': now,
                'last_visited_datetime': now,
                'hacked_subtitle': '',
                'hacked_no_subtitle': '',
                'subtitle': '',
                'no_subtitle': '',
            }
            for tt, magnet in magnet_links.items():
                if tt in cols and magnet:
                    cols[tt] = f"[{today}]{magnet}"

            if cols['hacked_subtitle']:
                cols['hacked_no_subtitle'] = ''
            if cols['subtitle']:
                cols['no_subtitle'] = ''

            conn.execute(
                """INSERT INTO parsed_movies_history
                   (href, phase, video_code, create_datetime, update_datetime,
                    last_visited_datetime, hacked_subtitle, hacked_no_subtitle,
                    subtitle, no_subtitle)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (cols['href'], cols['phase'], cols['video_code'],
                 cols['create_datetime'], cols['update_datetime'],
                 cols['last_visited_datetime'],
                 cols['hacked_subtitle'], cols['hacked_no_subtitle'],
                 cols['subtitle'], cols['no_subtitle']),
            )
        else:
            row = dict(existing)
            for tt, magnet in magnet_links.items():
                if tt not in ('hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle'):
                    continue
                if not magnet:
                    continue
                old_content = (row.get(tt) or '').strip()
                old_date = None
                if old_content.startswith('[') and ']' in old_content:
                    try:
                        old_date = old_content[1:old_content.index(']')]
                    except Exception:
                        pass
                if old_date:
                    try:
                        old_dt = datetime.strptime(old_date, "%Y-%m-%d")
                        new_dt = datetime.strptime(today, "%Y-%m-%d")
                        if new_dt > old_dt:
                            row[tt] = f"[{today}]{magnet}"
                    except Exception:
                        row[tt] = f"[{today}]{magnet}"
                else:
                    row[tt] = f"[{today}]{magnet}"

            if (row.get('hacked_subtitle') or '').strip():
                row['hacked_no_subtitle'] = ''
            if (row.get('subtitle') or '').strip():
                row['no_subtitle'] = ''

            conn.execute(
                """UPDATE parsed_movies_history
                   SET phase=?, update_datetime=?, last_visited_datetime=?,
                       hacked_subtitle=?, hacked_no_subtitle=?,
                       subtitle=?, no_subtitle=?
                   WHERE href=?""",
                (phase, now, now,
                 row.get('hacked_subtitle', ''), row.get('hacked_no_subtitle', ''),
                 row.get('subtitle', ''), row.get('no_subtitle', ''),
                 href),
            )


def db_batch_update_last_visited(hrefs: List[str], db_path: Optional[str] = None) -> int:
    """Update last_visited_datetime for a batch of hrefs."""
    if not hrefs:
        return 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db(db_path) as conn:
        placeholders = ','.join('?' for _ in hrefs)
        cur = conn.execute(
            f"UPDATE parsed_movies_history SET last_visited_datetime=? WHERE href IN ({placeholders})",
            [now] + list(hrefs),
        )
        return cur.rowcount


def db_check_torrent_in_history(href: str, torrent_type: str, db_path: Optional[str] = None) -> bool:
    """Check if a specific torrent type exists for href."""
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM parsed_movies_history WHERE href = ?", (href,)
        ).fetchone()
        if row is None:
            return False
        val = (dict(row).get(torrent_type) or '').strip()
        if not val:
            return False
        if val.startswith('[') and ']' in val:
            magnet = val.split(']', 1)[1]
            return magnet.startswith('magnet:')
        return val.startswith('magnet:')


def db_get_all_history_records(db_path: Optional[str] = None) -> List[dict]:
    """Return all history records as dicts (for migration verification)."""
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM parsed_movies_history ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


# ── rclone_inventory helpers ─────────────────────────────────────────────

def db_replace_rclone_inventory(entries: List[dict], db_path: Optional[str] = None) -> int:
    """Replace the entire rclone_inventory table (full scan refresh)."""
    with get_db(db_path) as conn:
        conn.execute("DELETE FROM rclone_inventory")
        for e in entries:
            conn.execute(
                """INSERT INTO rclone_inventory
                   (video_code, sensor_category, subtitle_category,
                    folder_path, folder_size, file_count, scan_datetime)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (e.get('video_code', ''), e.get('sensor_category', ''),
                 e.get('subtitle_category', ''), e.get('folder_path', ''),
                 int(e.get('folder_size', 0) or 0),
                 int(e.get('file_count', 0) or 0),
                 e.get('scan_datetime', '')),
            )
        return len(entries)


def db_load_rclone_inventory(db_path: Optional[str] = None) -> Dict[str, list]:
    """Load inventory grouped by video_code (same structure as CSV loader)."""
    inventory: Dict[str, list] = {}
    with get_db(db_path) as conn:
        rows = conn.execute("SELECT * FROM rclone_inventory").fetchall()
    for row in rows:
        r = dict(row)
        code = r['video_code'].strip().upper()
        if not code:
            continue
        inventory.setdefault(code, []).append(r)
    return inventory


# ── dedup_records helpers ────────────────────────────────────────────────

def db_load_dedup_records(db_path: Optional[str] = None) -> List[dict]:
    """Load all dedup records."""
    with get_db(db_path) as conn:
        rows = conn.execute("SELECT * FROM dedup_records ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def db_append_dedup_record(record: dict, db_path: Optional[str] = None) -> int:
    """Append a single dedup record. Returns the new row id."""
    with get_db(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO dedup_records
               (video_code, existing_sensor, existing_subtitle,
                existing_gdrive_path, existing_folder_size,
                new_torrent_category, deletion_reason,
                detect_datetime, is_deleted, delete_datetime)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (record.get('video_code', ''),
             record.get('existing_sensor', ''),
             record.get('existing_subtitle', ''),
             record.get('existing_gdrive_path', ''),
             int(record.get('existing_folder_size', 0) or 0),
             record.get('new_torrent_category', ''),
             record.get('deletion_reason', ''),
             record.get('detect_datetime', ''),
             1 if str(record.get('is_deleted', 'False')).lower() == 'true' else 0,
             record.get('delete_datetime', '')),
        )
        return cur.lastrowid


def db_save_dedup_records(rows: List[dict], db_path: Optional[str] = None) -> None:
    """Overwrite all dedup records (used after updating is_deleted flags)."""
    with get_db(db_path) as conn:
        conn.execute("DELETE FROM dedup_records")
        for r in rows:
            conn.execute(
                """INSERT INTO dedup_records
                   (video_code, existing_sensor, existing_subtitle,
                    existing_gdrive_path, existing_folder_size,
                    new_torrent_category, deletion_reason,
                    detect_datetime, is_deleted, delete_datetime)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (r.get('video_code', ''),
                 r.get('existing_sensor', ''),
                 r.get('existing_subtitle', ''),
                 r.get('existing_gdrive_path', ''),
                 int(r.get('existing_folder_size', 0) or 0),
                 r.get('new_torrent_category', ''),
                 r.get('deletion_reason', ''),
                 r.get('detect_datetime', ''),
                 1 if str(r.get('is_deleted', 'False')).lower() in ('true', '1') else 0,
                 r.get('delete_datetime', '')),
            )


# ── pikpak_history helpers ───────────────────────────────────────────────

def db_append_pikpak_history(record: dict, db_path: Optional[str] = None) -> int:
    """Append a PikPak transfer record."""
    with get_db(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO pikpak_history
               (torrent_hash, torrent_name, category, magnet_uri,
                added_to_qb_date, deleted_from_qb_date,
                uploaded_to_pikpak_date, transfer_status, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (record.get('torrent_hash', ''), record.get('torrent_name', ''),
             record.get('category', ''), record.get('magnet_uri', ''),
             record.get('added_to_qb_date', ''), record.get('deleted_from_qb_date', ''),
             record.get('uploaded_to_pikpak_date', ''),
             record.get('transfer_status', ''), record.get('error_message', '')),
        )
        return cur.lastrowid


# ── proxy_bans helpers ───────────────────────────────────────────────────

def db_load_proxy_bans(db_path: Optional[str] = None) -> List[dict]:
    """Load all proxy ban records."""
    with get_db(db_path) as conn:
        rows = conn.execute("SELECT * FROM proxy_bans ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def db_save_proxy_bans(records: List[dict], db_path: Optional[str] = None) -> None:
    """Replace all proxy ban records."""
    with get_db(db_path) as conn:
        conn.execute("DELETE FROM proxy_bans")
        for r in records:
            conn.execute(
                "INSERT INTO proxy_bans (proxy_name, ban_time, unban_time) VALUES (?, ?, ?)",
                (r.get('proxy_name', ''), r.get('ban_time', ''), r.get('unban_time', '')),
            )


# ── report_sessions + report_rows helpers ────────────────────────────────

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
    with get_db(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO report_sessions
               (report_type, report_date, url_type, display_name,
                url, start_page, end_page, csv_filename, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (report_type, report_date, url_type, display_name,
             url, start_page, end_page, csv_filename, created_at),
        )
        return cur.lastrowid


def db_insert_report_rows(session_id: int, rows: List[dict], db_path: Optional[str] = None) -> int:
    """Bulk insert report rows for a session."""
    with get_db(db_path) as conn:
        for row in rows:
            conn.execute(
                """INSERT INTO report_rows
                   (session_id, href, video_code, page, actor, rate,
                    comment_number, hacked_subtitle, hacked_no_subtitle,
                    subtitle, no_subtitle, size_hacked_subtitle,
                    size_hacked_no_subtitle, size_subtitle, size_no_subtitle)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id,
                 row.get('href', ''), row.get('video_code', ''),
                 int(row['page']) if row.get('page') else None,
                 row.get('actor', ''),
                 float(row['rate']) if row.get('rate') else None,
                 int(row['comment_number']) if row.get('comment_number') else None,
                 row.get('hacked_subtitle', ''), row.get('hacked_no_subtitle', ''),
                 row.get('subtitle', ''), row.get('no_subtitle', ''),
                 row.get('size_hacked_subtitle', ''),
                 row.get('size_hacked_no_subtitle', ''),
                 row.get('size_subtitle', ''),
                 row.get('size_no_subtitle', '')),
            )
        return len(rows)


def db_get_report_rows(session_id: int, db_path: Optional[str] = None) -> List[dict]:
    """Get all rows for a session, ordered by insertion order."""
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM report_rows WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def db_get_latest_session(report_type: Optional[str] = None, db_path: Optional[str] = None) -> Optional[dict]:
    """Get the most recent report session, optionally filtered by type."""
    with get_db(db_path) as conn:
        if report_type:
            row = conn.execute(
                "SELECT * FROM report_sessions WHERE report_type = ? ORDER BY id DESC LIMIT 1",
                (report_type,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM report_sessions ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None


def db_get_sessions_by_date(report_date: str, report_type: Optional[str] = None,
                            db_path: Optional[str] = None) -> List[dict]:
    """Get all sessions for a given date."""
    with get_db(db_path) as conn:
        if report_type:
            rows = conn.execute(
                "SELECT * FROM report_sessions WHERE report_date = ? AND report_type = ? ORDER BY id",
                (report_date, report_type),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM report_sessions WHERE report_date = ? ORDER BY id",
                (report_date,),
            ).fetchall()
        return [dict(r) for r in rows]


# ── stats helpers ────────────────────────────────────────────────────────

def db_save_spider_stats(session_id: int, stats: dict, db_path: Optional[str] = None) -> int:
    """Save spider statistics for a session."""
    with get_db(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO spider_stats
               (session_id,
                phase1_discovered, phase1_processed, phase1_skipped,
                phase1_no_new, phase1_failed,
                phase2_discovered, phase2_processed, phase2_skipped,
                phase2_no_new, phase2_failed,
                total_discovered, total_processed, total_skipped,
                total_no_new, total_failed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id,
             stats.get('phase1_discovered', 0), stats.get('phase1_processed', 0),
             stats.get('phase1_skipped', 0), stats.get('phase1_no_new', 0),
             stats.get('phase1_failed', 0),
             stats.get('phase2_discovered', 0), stats.get('phase2_processed', 0),
             stats.get('phase2_skipped', 0), stats.get('phase2_no_new', 0),
             stats.get('phase2_failed', 0),
             stats.get('total_discovered', 0), stats.get('total_processed', 0),
             stats.get('total_skipped', 0), stats.get('total_no_new', 0),
             stats.get('total_failed', 0)),
        )
        return cur.lastrowid


def db_save_uploader_stats(session_id: int, stats: dict, db_path: Optional[str] = None) -> int:
    """Save uploader statistics for a session."""
    with get_db(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO uploader_stats
               (session_id, total_torrents, duplicate_count, attempted,
                successfully_added, failed_count, hacked_sub, hacked_nosub,
                subtitle_count, no_subtitle_count, success_rate)
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
    with get_db(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO pikpak_stats
               (session_id, threshold_days, total_torrents,
                filtered_old, successful_count, failed_count)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session_id,
             stats.get('threshold_days', 3), stats.get('total_torrents', 0),
             stats.get('filtered_old', 0), stats.get('successful_count', 0),
             stats.get('failed_count', 0)),
        )
        return cur.lastrowid


def db_get_spider_stats(session_id: int, db_path: Optional[str] = None) -> Optional[dict]:
    """Get spider stats for a session."""
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM spider_stats WHERE session_id = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None


def db_get_uploader_stats(session_id: int, db_path: Optional[str] = None) -> Optional[dict]:
    """Get uploader stats for a session."""
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM uploader_stats WHERE session_id = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None


def db_get_pikpak_stats(session_id: int, db_path: Optional[str] = None) -> Optional[dict]:
    """Get PikPak stats for a session."""
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM pikpak_stats WHERE session_id = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None
