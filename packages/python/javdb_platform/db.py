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
import secrets
import sqlite3
import threading
import time
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
    merge_rclone_inventory_from_stage as _merge_rclone_inventory_from_stage,
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
_active_run_id_value: Optional[str] = None
_active_run_attempt_value: Optional[int] = None
# Ingestion Perfect Rollback (Phase 2): the spider sets this once after
# creating the report session so that every history-write code path
# (`save_parsed_movie_to_history`, etc.) can decide between the legacy
# audit upsert and the new pending-stage path without re-querying
# `ReportSessions` per movie.  ``None`` means "not set — fall back to
# the JAVDB_HISTORY_WRITE_MODE env var, then to 'audit'".
_active_write_mode_value: Optional[str] = None


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


def set_active_run_identity(
    run_id: Optional[str],
    run_attempt: Optional[int],
) -> None:
    """Set the GitHub Actions workflow identity for subsequent audit rows.

    Called by the spider alongside :func:`set_active_session_id` so that
    every ``MovieHistoryAudit`` / ``TorrentHistoryAudit`` row written by
    this process is stamped with the run that produced it.  Allows the
    rollback CLI to look up audit rows by ``(RunId, RunAttempt)`` —
    independent of any ``ReportSessions.Id`` drift between SQLite and D1.
    """
    global _active_run_id_value, _active_run_attempt_value
    with _active_session_id_lock:
        _active_run_id_value = run_id
        _active_run_attempt_value = (
            int(run_attempt) if run_attempt is not None else None
        )


def get_active_session_id() -> Optional[int]:
    """Return the currently-active ``ReportSessions.Id`` or ``None``."""
    with _active_session_id_lock:
        return _active_session_id_value


def get_active_run_identity() -> Tuple[Optional[str], Optional[int]]:
    """Return ``(RunId, RunAttempt)`` from the active session context."""
    with _active_session_id_lock:
        return _active_run_id_value, _active_run_attempt_value


def set_active_write_mode(write_mode: Optional[str]) -> None:
    """Pin the active session's WriteMode for the current process.

    Set by the spider (and the rclone staging session) immediately after
    :func:`db_create_report_session` so the write-path helpers
    (`save_parsed_movie_to_history`, etc.) can branch to
    :func:`db_stage_history_write` without re-reading ``ReportSessions``
    for every movie.  Pass ``None`` to clear (e.g. between phases in
    long-lived processes / tests).
    """
    global _active_write_mode_value
    if write_mode is not None:
        write_mode = _resolve_write_mode(write_mode)
    with _active_session_id_lock:
        _active_write_mode_value = write_mode


def get_active_write_mode() -> str:
    """Return the resolved active WriteMode (``'audit'`` or ``'pending'``).

    Resolution order, mirrors :func:`_resolve_write_mode` so the helpers
    used at session-create time and at write time agree:

      1. Process-local override set by :func:`set_active_write_mode`.
      2. Env var ``JAVDB_HISTORY_WRITE_MODE``.
      3. Default ``'audit'``.
    """
    with _active_session_id_lock:
        cached = _active_write_mode_value
    if cached:
        return cached
    return _resolve_write_mode(None)


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


# ── Application-generated session id ─────────────────────────────────────
#
# Why not just use ReportSessions.Id AUTOINCREMENT?  Under STORAGE_BACKEND=
# dual the SQLite-side and D1-side AUTOINCREMENT counters are independent,
# and any past asymmetric INSERT (one side committed, the other failed)
# leaves them permanently out of sync.  ``cur.lastrowid`` returns whichever
# backend the cursor wraps; trusting it as ``SessionId`` for downstream
# tables is what caused the 2026-05-08 incident where the local id 332
# collided with a stale 332 on D1 from a prior run.
#
# Solution: generate the id ourselves and INSERT with an explicit ``Id``
# column on both backends.  The id needs to be (a) monotonic-enough that
# concurrent processes don't collide, (b) representable as a 64-bit
# signed INTEGER for D1 / SQLite compatibility, (c) easy to glance at
# when debugging.
#
# Layout: ``millisecond_timestamp << 10 | counter`` where the counter
# wraps at 1024 per millisecond.
#   * milliseconds since the epoch fit in 41 bits through year 2039.
#   * 10 bits of counter give 1024 ids per millisecond — well above any
#     realistic concurrent-INSERT rate from a single Python process.
#   * 41 + 10 = 51 bits, comfortably inside int64 (max 63 bits).
#
# Across processes we still rely on the fact that two GitHub Actions
# runners almost never start a session in the same millisecond *and*
# also collide on the bottom 10 bits.  The
# ``db_count_in_progress_sessions_for_run`` self-check (Phase 5) is the
# real defence against duplicate sessions per workflow run.
_SESSION_ID_LOCK = threading.Lock()
_SESSION_ID_LAST = 0
_SESSION_ID_COUNTER_BITS = 10
_SESSION_ID_COUNTER_MASK = (1 << _SESSION_ID_COUNTER_BITS) - 1
# Per-process random tag (12 bit) that sits between the ms timestamp and
# the in-process counter (B.2, 2026-05-11). Without it, two GitHub Actions
# runners that start a session in the same millisecond *and* happen to be
# at counter=0 would mint the same Id and then race a PRIMARY KEY collision
# on ReportSessions / Pending* INSERTs (the existing inline comment above
# acknowledged the gap and pointed at db_count_in_progress_sessions_for_run
# as the only defence; that self-check runs *after* the INSERT, so a
# collision still aborts the runner). The 12-bit tag gives 1/4096 odds of
# two peers picking the same tag on top of the (much rarer) same-ms +
# same-counter coincidence — effectively eliminates the collision in
# practice. Layout: 41 bit ms + 12 bit tag + 10 bit counter = 63 bit, still
# inside signed int64 on both SQLite and Cloudflare D1.
_SESSION_ID_PROCESS_TAG_BITS = 12
_SESSION_ID_PROCESS_TAG = secrets.randbits(_SESSION_ID_PROCESS_TAG_BITS)


def _generate_session_id() -> int:
    """Return a 63-bit INTEGER suitable for ``ReportSessions.Id``.

    Format: ``ms << 22 | process_tag << 10 | counter`` where
    ``process_tag`` is a per-process 12-bit random value, ``counter``
    is a 10-bit in-process monotonic counter that resets every ms, and
    ``ms`` is ``time.time_ns() // 1_000_000``. Strictly increasing
    within a process; comfortably fits a signed 64-bit INTEGER on both
    SQLite and Cloudflare D1.
    """
    global _SESSION_ID_LAST
    process_shift = _SESSION_ID_PROCESS_TAG_BITS + _SESSION_ID_COUNTER_BITS
    with _SESSION_ID_LOCK:
        ms = time.time_ns() // 1_000_000
        candidate = (ms << process_shift) | (
            _SESSION_ID_PROCESS_TAG << _SESSION_ID_COUNTER_BITS
        )
        if candidate <= _SESSION_ID_LAST:
            candidate = _SESSION_ID_LAST + 1
        _SESSION_ID_LAST = candidate
        return candidate


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

    backend = _backend_mode()

    # P1: key the cache on ``(db_path, backend)`` so a runtime flip of
    # ``STORAGE_BACKEND`` (kill-switch, JAVDB_FORBID_DB_WRITES escalation,
    # operator toggling between sqlite-only and dual) returns the right
    # connection type instead of a stale facade from before the switch.
    cache_key = (db_path, backend)
    conn = conns.get(cache_key)
    if conn is not None:
        return conn

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

    conns[cache_key] = conn
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


@contextmanager
def get_local_sqlite_db(db_path: Optional[str] = None):
    """Context manager that always yields a raw ``sqlite3.Connection``.

    P0-6: under ``STORAGE_BACKEND=dual`` the default :func:`get_db` returns
    a :class:`DualConnection` whose reads are served by D1. That is the
    right behaviour for the application's hot path, but several
    observability code paths (email notification, drift reconciler,
    operator dashboards) MUST read the locally-canonical state instead so
    they do not paper over D1 lag. This helper opens a dedicated SQLite
    connection irrespective of the configured backend, with auto-commit
    on exit. The connection is NOT cached in the thread-local registry.
    """
    path = db_path or HISTORY_DB_PATH
    conn = _open_sqlite_connection(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


def close_db():
    """Close all thread-local connections (call before process exit)."""
    conns: dict = getattr(_local, 'conns', None)
    if not conns:
        return
    for key, conn in list(conns.items()):
        # Cache keys are ``(db_path, backend)`` tuples (see _get_connection).
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
--
-- (RunId, RunAttempt) duplicate the GitHub Actions identity already stored
-- on ReportSessions so the rollback CLI can address audit rows directly
-- by run identity (the primary lookup path) without first joining through
-- ReportSessions. Both columns are NULLABLE: legacy audit rows predate the
-- 2026-05-08 migration and the rollback CLI falls back to SessionId for
-- those rows.
CREATE TABLE IF NOT EXISTS MovieHistoryAudit (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    TargetId INTEGER NOT NULL,
    Action TEXT NOT NULL,
    OldRowJson TEXT,
    SessionId INTEGER NOT NULL,
    DateTimeCreated TEXT NOT NULL,
    RunId TEXT,
    RunAttempt INTEGER
);
CREATE INDEX IF NOT EXISTS idx_mh_audit_session ON MovieHistoryAudit(SessionId, Id);
CREATE INDEX IF NOT EXISTS idx_mh_audit_run ON MovieHistoryAudit(RunId, RunAttempt);

CREATE TABLE IF NOT EXISTS TorrentHistoryAudit (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    TargetId INTEGER NOT NULL,
    Action TEXT NOT NULL,
    OldRowJson TEXT,
    SessionId INTEGER NOT NULL,
    DateTimeCreated TEXT NOT NULL,
    RunId TEXT,
    RunAttempt INTEGER
);
CREATE INDEX IF NOT EXISTS idx_th_audit_session ON TorrentHistoryAudit(SessionId, Id);
CREATE INDEX IF NOT EXISTS idx_th_audit_run ON TorrentHistoryAudit(RunId, RunAttempt);

-- Pending history write tables (Ingestion Perfect Rollback, Phase 0).
--
-- Every ingestion mutation against MovieHistory / TorrentHistory under
-- ``WriteMode='pending'`` is staged here first.  ``apps.cli.commit_session``
-- promotes them into the live tables atomically per movie at the end of
-- a successful run; ``apps.cli.rollback`` deletes them on in_progress
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
    Seq INTEGER PRIMARY KEY NOT NULL,
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
    CreatedAt TEXT NOT NULL,
    ApplyState TEXT NOT NULL DEFAULT 'pending'
);
CREATE INDEX IF NOT EXISTS idx_pmhw_session ON PendingMovieHistoryWrites(SessionId);
CREATE INDEX IF NOT EXISTS idx_pmhw_run ON PendingMovieHistoryWrites(RunId, RunAttempt);
CREATE INDEX IF NOT EXISTS idx_pmhw_href ON PendingMovieHistoryWrites(Href);
CREATE INDEX IF NOT EXISTS idx_pmhw_session_state
    ON PendingMovieHistoryWrites(SessionId, ApplyState);

CREATE TABLE IF NOT EXISTS PendingTorrentHistoryWrites (
    Seq INTEGER PRIMARY KEY NOT NULL,
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
-- ReportSessions.Id is a PRIMARY KEY but new rows MUST supply Id explicitly
-- (see :func:`_generate_session_id`).  AUTOINCREMENT is retained only for
-- legacy callers (csv_to_sqlite migration tool, older test fixtures) that
-- still rely on auto-assignment.  The application layer stopped using it
-- on 2026-05-08 to fix sqlite-vs-D1 lastrowid drift under STORAGE_BACKEND=
-- dual where each backend's autoincrement counter is independent.
--
-- (RunId, RunAttempt) carry the GitHub Actions workflow run identity so
-- the rollback CLI can locate sessions by run rather than relying solely
-- on the application-generated Id.  FailureReason is set by the rollback
-- CLI alongside Status='failed' to capture *why* a session was unwound
-- (timeout, workflow_cancel, runtime error, ...).
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
    Status TEXT DEFAULT 'in_progress',
    RunId TEXT,
    RunAttempt INTEGER,
    FailureReason TEXT,
    -- Ingestion Perfect Rollback (Phase 0): ``audit`` keeps the
    -- legacy X3 audit-replay rollback path; ``pending`` stages
    -- writes into PendingMovie/TorrentHistoryWrites and drains
    -- them via ``db_commit_session_history`` /
    -- ``db_resume_finalizing_session``.  Defaults to ``audit`` so
    -- the new tables ship dark until ``JAVDB_HISTORY_WRITE_MODE``
    -- (read by ``db_create_report_session``) is flipped per-workflow.
    WriteMode TEXT DEFAULT 'audit'
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

-- P1: Per-session stats rows must be unique per SessionId. Without a
-- UNIQUE index the legacy `db_save_*` paths' plain INSERT silently
-- duplicates rows whenever a retry or re-run hits the same SessionId,
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
      - ReportSessions.RunId, ReportSessions.RunAttempt,
        ReportSessions.FailureReason  (added 2026-05-08; identifies the
        owning GitHub Actions workflow run and stores rollback context)
      - MovieHistory.SessionId, TorrentHistory.SessionId
      - PikpakHistory.SessionId, DedupRecords.SessionId,
        InventoryAlignNoExactMatch.SessionId
      - MovieHistoryAudit, TorrentHistoryAudit tables and indexes
      - MovieHistoryAudit.RunId, MovieHistoryAudit.RunAttempt and the
        symmetric pair on TorrentHistoryAudit (added 2026-05-08).

    This handles existing databases that were created before the X3 rollback
    schema. New DBs are created with the columns directly via the DDL
    constants in ``_HISTORY_DDL`` / ``_REPORTS_DDL`` / ``_OPERATIONS_DDL``,
    so the ALTER calls below silently no-op.
    """
    add_column_specs = [
        ('ReportSessions', 'Status', "TEXT DEFAULT 'in_progress'"),
        ('ReportSessions', 'RunId', 'TEXT'),
        ('ReportSessions', 'RunAttempt', 'INTEGER'),
        ('ReportSessions', 'FailureReason', 'TEXT'),
        ('MovieHistory', 'SessionId', 'INTEGER'),
        ('TorrentHistory', 'SessionId', 'INTEGER'),
        ('MovieHistoryAudit', 'RunId', 'TEXT'),
        ('MovieHistoryAudit', 'RunAttempt', 'INTEGER'),
        ('TorrentHistoryAudit', 'RunId', 'TEXT'),
        ('TorrentHistoryAudit', 'RunAttempt', 'INTEGER'),
        ('PikpakHistory', 'SessionId', 'INTEGER'),
        ('DedupRecords', 'SessionId', 'INTEGER'),
        ('InventoryAlignNoExactMatch', 'SessionId', 'INTEGER'),
        # Ingestion Perfect Rollback (Phase 0): WriteMode column on
        # ReportSessions, gating the audit-vs-pending dispatch.
        ('ReportSessions', 'WriteMode', "TEXT DEFAULT 'audit'"),
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
            DateTimeCreated TEXT NOT NULL,
            RunId TEXT,
            RunAttempt INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_mh_audit_session
            ON MovieHistoryAudit(SessionId, Id);
        CREATE INDEX IF NOT EXISTS idx_mh_audit_run
            ON MovieHistoryAudit(RunId, RunAttempt);
        CREATE TABLE IF NOT EXISTS TorrentHistoryAudit (
            Id INTEGER PRIMARY KEY AUTOINCREMENT,
            TargetId INTEGER NOT NULL,
            Action TEXT NOT NULL,
            OldRowJson TEXT,
            SessionId INTEGER NOT NULL,
            DateTimeCreated TEXT NOT NULL,
            RunId TEXT,
            RunAttempt INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_th_audit_session
            ON TorrentHistoryAudit(SessionId, Id);
        CREATE INDEX IF NOT EXISTS idx_th_audit_run
            ON TorrentHistoryAudit(RunId, RunAttempt);
        """
    )
    if _has_table(conn, 'MovieHistory'):
        conn.executescript(audit_ddl)

    pending_ddl = (
        """
        CREATE TABLE IF NOT EXISTS PendingMovieHistoryWrites (
            Seq INTEGER PRIMARY KEY NOT NULL,
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
            Seq INTEGER PRIMARY KEY NOT NULL,
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
        ('idx_mh_audit_run',
         'MovieHistoryAudit', 'RunId, RunAttempt'),
        ('idx_th_audit_run',
         'TorrentHistoryAudit', 'RunId, RunAttempt'),
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
            # Ingestion Perfect Rollback (Phase 0): legacy rows pre-date
            # the WriteMode column; backfill them to 'audit' so the
            # rollback dispatcher (Phase 2) treats them like the existing
            # X3 audit-replay path.
            conn.execute(
                "UPDATE ReportSessions "
                "SET WriteMode='audit' WHERE WriteMode IS NULL"
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
        _materialize_report_session_status_default(conn)

        if current < 6:
            _migrate_v5_to_v6(conn)
        if current < 10:
            _migrate_defaults_to_null(conn)

        existing = conn.execute("SELECT Version FROM SchemaVersion LIMIT 1").fetchone()
        if existing is None:
            conn.execute("INSERT INTO SchemaVersion (Version) VALUES (?)", (SCHEMA_VERSION,))
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
# The audit row is sent in the same backend batch as the matching
# mutation whenever a session_id is active. SQLite executes the batch
# inside the surrounding transaction; D1 treats each backend batch as
# atomic, so the mutation and audit row succeed or fail together.

_MOVIE_AUDIT_SQL = """INSERT INTO MovieHistoryAudit
   (TargetId, Action, OldRowJson, SessionId, DateTimeCreated, RunId, RunAttempt)
   VALUES (?, ?, ?, ?, ?, ?, ?)"""

_MOVIE_AUDIT_FOR_HREF_SQL = """INSERT INTO MovieHistoryAudit
   (TargetId, Action, OldRowJson, SessionId, DateTimeCreated, RunId, RunAttempt)
   VALUES ((SELECT Id FROM MovieHistory WHERE Href=?), ?, ?, ?, ?, ?, ?)"""

_TORRENT_AUDIT_SQL = """INSERT INTO TorrentHistoryAudit
   (TargetId, Action, OldRowJson, SessionId, DateTimeCreated, RunId, RunAttempt)
   VALUES (?, ?, ?, ?, ?, ?, ?)"""

_TORRENT_AUDIT_FOR_TYPE_SQL = """INSERT INTO TorrentHistoryAudit
   (TargetId, Action, OldRowJson, SessionId, DateTimeCreated, RunId, RunAttempt)
   VALUES (
       (SELECT Id FROM TorrentHistory
        WHERE MovieHistoryId=? AND SubtitleIndicator=? AND CensorIndicator=?),
       ?, ?, ?, ?, ?, ?
   )"""


def _execute_backend_batch(conn, statements: List[Tuple[str, Tuple[Any, ...]]]):
    if not statements:
        return []
    batch = getattr(conn, "batch_execute", None)
    if callable(batch):
        return batch(statements)
    cursors = []
    for sql, params in statements:
        cursors.append(conn.execute(sql, params))
    return cursors


def _audit_old_json(old_row: Any = None) -> Optional[str]:
    if old_row is None:
        return None
    return json.dumps(
        _row_to_jsonable_dict(old_row),
        ensure_ascii=False,
        default=str,
    )


def _movie_audit_statement(
    target_id: int,
    *,
    action: str,
    session_id: Optional[int],
    old_row: Any = None,
    when: Optional[str] = None,
) -> Optional[Tuple[str, Tuple[Any, ...]]]:
    if session_id is None:
        return None
    if when is None:
        when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_id, run_attempt = get_active_run_identity()
    return (
        _MOVIE_AUDIT_SQL,
        (target_id, action, _audit_old_json(old_row), session_id, when,
         run_id, run_attempt),
    )


def _movie_insert_audit_statement_for_href(
    href: str,
    *,
    session_id: Optional[int],
    when: Optional[str] = None,
) -> Optional[Tuple[str, Tuple[Any, ...]]]:
    if session_id is None:
        return None
    if when is None:
        when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_id, run_attempt = get_active_run_identity()
    return (
        _MOVIE_AUDIT_FOR_HREF_SQL,
        (href, 'INSERT', None, session_id, when, run_id, run_attempt),
    )


def _torrent_audit_statement(
    target_id: int,
    *,
    action: str,
    session_id: Optional[int],
    old_row: Any = None,
    when: Optional[str] = None,
) -> Optional[Tuple[str, Tuple[Any, ...]]]:
    if session_id is None:
        return None
    if when is None:
        when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_id, run_attempt = get_active_run_identity()
    return (
        _TORRENT_AUDIT_SQL,
        (target_id, action, _audit_old_json(old_row), session_id, when,
         run_id, run_attempt),
    )


def _torrent_insert_audit_statement_for_type(
    movie_id: int,
    sub_ind: int,
    cen_ind: int,
    *,
    session_id: Optional[int],
    when: Optional[str] = None,
) -> Optional[Tuple[str, Tuple[Any, ...]]]:
    if session_id is None:
        return None
    if when is None:
        when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_id, run_attempt = get_active_run_identity()
    return (
        _TORRENT_AUDIT_FOR_TYPE_SQL,
        (movie_id, sub_ind, cen_ind, 'INSERT', None, session_id, when,
         run_id, run_attempt),
    )


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
    stmt = _movie_audit_statement(
        target_id,
        action=action,
        session_id=session_id,
        old_row=old_row,
        when=when,
    )
    if stmt is not None:
        conn.execute(stmt[0], stmt[1])


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
    stmt = _torrent_audit_statement(
        target_id,
        action=action,
        session_id=session_id,
        old_row=old_row,
        when=when,
    )
    if stmt is not None:
        conn.execute(stmt[0], stmt[1])


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
            insert_movie = (
                """INSERT INTO MovieHistory
                   (VideoCode, Href, DateTimeCreated, DateTimeUpdated, DateTimeVisited,
                    ActorName, ActorGender, ActorLink, SupportingActors, SessionId)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (video_code, normalized_href, now, now, now,
                 actor_name, actor_gender, prepared_actor_link,
                 prepared_supporting_actors, sid),
            )
            statements = [insert_movie]
            audit_stmt = _movie_insert_audit_statement_for_href(
                normalized_href,
                session_id=sid,
                when=now,
            )
            if audit_stmt is not None:
                statements.append(audit_stmt)
            cursors = _execute_backend_batch(conn, statements)
            movie_id = getattr(cursors[0], "lastrowid", None) if cursors else None
            if not movie_id:
                row = conn.execute(
                    "SELECT Id FROM MovieHistory WHERE Href=?",
                    (normalized_href,),
                ).fetchone()
                movie_id = row['Id']
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
                update_movie = (
                    """UPDATE MovieHistory SET DateTimeUpdated=?, DateTimeVisited=?,
                       Href=?, ActorName=?, ActorGender=?, ActorLink=?,
                       SupportingActors=?, SessionId=? WHERE Id=?""",
                    (now, now, normalized_href, new_an, new_ag, new_al, new_sup,
                     sid, movie_id),
                )
            else:
                update_movie = (
                    """UPDATE MovieHistory SET DateTimeUpdated=?, DateTimeVisited=?,
                       Href=?, SessionId=? WHERE Id=?""",
                    (now, now, normalized_href, sid, movie_id),
                )
            statements = [update_movie]
            audit_stmt = _movie_audit_statement(
                movie_id,
                action='UPDATE',
                session_id=sid,
                old_row=old_full,
                when=now,
            )
            if audit_stmt is not None:
                statements.append(audit_stmt)
            _execute_backend_batch(conn, statements)

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
                insert_torrent = (
                    """INSERT INTO TorrentHistory
                       (MovieHistoryId, MagnetUri, SubtitleIndicator, CensorIndicator,
                        ResolutionType, Size, FileCount, DateTimeCreated,
                        DateTimeUpdated, SessionId)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (movie_id, magnet, sub_ind, cen_ind, res, size, fc, now, now,
                     sid),
                )
                statements = [insert_torrent]
                audit_stmt = _torrent_insert_audit_statement_for_type(
                    movie_id,
                    sub_ind,
                    cen_ind,
                    session_id=sid,
                    when=now,
                )
                if audit_stmt is not None:
                    statements.append(audit_stmt)
                _execute_backend_batch(conn, statements)
            else:
                update_torrent = (
                    """UPDATE TorrentHistory
                       SET MagnetUri=?, Size=?, FileCount=?, ResolutionType=?,
                           DateTimeUpdated=?, SessionId=?
                       WHERE Id=?""",
                    (magnet, size, fc, res, now, sid, existing_t['Id']),
                )
                statements = [update_torrent]
                audit_stmt = _torrent_audit_statement(
                    existing_t['Id'],
                    action='UPDATE',
                    session_id=sid,
                    old_row=existing_t,
                    when=now,
                )
                if audit_stmt is not None:
                    statements.append(audit_stmt)
                _execute_backend_batch(conn, statements)

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
    if rows:
        statements = [
            (
                "DELETE FROM TorrentHistory WHERE MovieHistoryId=? "
                "AND SubtitleIndicator=? AND CensorIndicator=?",
                (movie_id, sub_ind, cen_ind),
            )
        ]
        for row in rows:
            audit_stmt = _torrent_audit_statement(
                row['Id'],
                action='DELETE',
                session_id=session_id,
                old_row=row,
                when=when,
            )
            if audit_stmt is not None:
                statements.append(audit_stmt)
        _execute_backend_batch(conn, statements)


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
            update_movie = (
                """UPDATE MovieHistory SET PerfectMatchIndicator=?,
                   HiResIndicator=?, SessionId=? WHERE Id=?""",
                (perfect_val, hires_val, session_id, movie_id),
            )
            statements = [update_movie]
            audit_stmt = _movie_audit_statement(
                movie_id,
                action='UPDATE',
                session_id=session_id,
                old_row=old_full,
                when=when,
            )
            if audit_stmt is not None:
                statements.append(audit_stmt)
            _execute_backend_batch(conn, statements)
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

    Ingestion Perfect Rollback (Phase 2): when the active session runs
    under ``WriteMode='pending'`` the visit timestamps are staged into
    :data:`PendingMovieHistoryWrites` (a sparse "DateTimeVisited only"
    row per href) and applied to live in :func:`db_commit_session_history`.
    The audit-mode in-place UPDATE path is preserved for legacy sessions.
    """
    if not hrefs:
        return 0
    sid = _resolve_session_id(session_id)
    if sid is not None and get_active_write_mode() == 'pending':
        # Pending route: dedupe + stage one sparse pending movie row
        # per href.  ``_pending_movie_overlay`` will sparse-merge this
        # with any earlier stages from the same session at commit time
        # so we never clobber the actor fields with the visit row's
        # NULLs.
        unique_hrefs = list(dict.fromkeys(h for h in hrefs if h))
        if not unique_hrefs:
            return 0
        for href in unique_hrefs:
            db_stage_history_write(
                int(sid),
                _KIND_MOVIE,
                {
                    "Href": href,
                    # Explicit visited timestamp — db_stage_history_write
                    # would otherwise default to "now" via its own
                    # fallback, which is what we want anyway, but pin
                    # it here so every href in the batch shares the
                    # exact same value (mirrors the audit path).
                    "DateTimeVisited": (
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    ),
                },
                db_path=db_path,
            )
        return len(unique_hrefs)
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
    # D1 caps bound parameters and batch statements. Keep session-tagged
    # chunks small enough for one UPDATE plus per-row audit INSERTs.
    CHUNK = 40 if sid is not None else 90
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
                statements = [
                    (
                        f"UPDATE MovieHistory SET DateTimeVisited=?, SessionId=? "
                        f"WHERE Href IN ({placeholders})",
                        tuple([now, sid] + chunk),
                    )
                ]
                for row in affected_rows:
                    audit_stmt = _movie_audit_statement(
                        row['Id'],
                        action='UPDATE',
                        session_id=sid,
                        old_row=row,
                        when=now,
                    )
                    if audit_stmt is not None:
                        statements.append(audit_stmt)
                cursors = _execute_backend_batch(conn, statements)
                cur = cursors[0]
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

    Ingestion Perfect Rollback (Phase 2): pending-mode sessions stage a
    sparse "actor fields only" pending movie row per href instead of
    UPDATE-ing live + writing audit; commit merges with the earlier
    stages from the same session.
    """
    if not updates:
        return 0
    sid = _resolve_session_id(session_id)
    if sid is not None and get_active_write_mode() == 'pending':
        for href, actor_name, actor_gender, actor_link, supporting_actors in (
            updates
        ):
            if not href:
                continue
            db_stage_history_write(
                int(sid),
                _KIND_MOVIE,
                {
                    "Href": href,
                    "ActorName": actor_name,
                    "ActorGender": actor_gender,
                    "ActorLink": actor_link,
                    "SupportingActors": supporting_actors,
                },
                db_path=db_path,
            )
        return len([u for u in updates if u and u[0]])
    with get_db(db_path or HISTORY_DB_PATH) as conn:
        return _batch_update_movie_actors(
            conn, updates,
            session_id=sid,
            audit_record_movie_change=_audit_record_movie_change,
            audit_movie_change_statement=_movie_audit_statement,
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


def db_merge_rclone_inventory_from_stage(
    session_id: Any = _SESSION_ID_SENTINEL,
    years: Optional[Iterable[str]] = None,
    db_path: Optional[str] = None,
) -> int:
    """Merge this session's staging rows into selected RcloneInventory years."""
    sid = _resolve_session_id(session_id)
    if sid is None:
        raise ValueError(
            "db_merge_rclone_inventory_from_stage requires an active "
            "session_id (set via set_active_session_id or pass explicitly)."
        )
    if years is None:
        raise ValueError("db_merge_rclone_inventory_from_stage requires years")
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        return _merge_rclone_inventory_from_stage(conn, sid, years)


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
    :func:`get_active_session_id`.

    Ingestion Perfect Rollback (Phase 3): the legacy implementation used
    a naked ``INSERT OR REPLACE`` which overwrote any prior session's
    tag whenever the same VideoCode was recorded a second time.  That
    silently broke rollback for the *original* writer: a daily session
    that staked the row could be ripped out by an unrelated adhoc
    re-recording with the same reason.  We now switch to an
    ``INSERT … ON CONFLICT(VideoCode) DO UPDATE`` whose UPDATE branch
    only triggers when the row *meaningfully* changes (a new ``Reason``).
    For an unchanged re-record the existing SessionId / DateTimeRecorded
    are preserved verbatim — the original writer keeps ownership.
    """
    sid = _resolve_session_id(session_id)
    normalized = video_code.strip().upper()
    if not normalized:
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_db(db_path or OPERATIONS_DB_PATH) as conn:
        conn.execute(
            """INSERT INTO InventoryAlignNoExactMatch
                   (VideoCode, Reason, DateTimeRecorded, SessionId)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(VideoCode) DO UPDATE SET
                   Reason = excluded.Reason,
                   DateTimeRecorded = excluded.DateTimeRecorded,
                   SessionId = excluded.SessionId
               WHERE InventoryAlignNoExactMatch.Reason
                     IS NOT excluded.Reason""",
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



# ── Ingestion Perfect Rollback: WriteMode + state machine helpers ────────
#
# The state machine handled here:
#
#     in_progress → finalizing  (set by db_begin_finalize_session)
#     finalizing  → committed   (set by db_finish_commit_session)
#     in_progress → failed      (rollback path; existing behaviour)
#     finalizing  → finalizing  (idempotent resume; see
#                                db_resume_finalizing_session)
#
# The rollback CLI dispatches based on the (WriteMode, Status) pair so
# legacy ``audit`` sessions and Phase-2 ``pending`` sessions can coexist
# inside the same workflow run.
_ALLOWED_STATUSES = ("in_progress", "finalizing", "committed", "failed")
_ALLOWED_WRITE_MODES = ("audit", "pending")


def _resolve_write_mode(explicit: Optional[str]) -> str:
    """Return a validated WriteMode (``audit`` or ``pending``).

    Resolution order:
      1. Explicit *explicit* argument (when set).
      2. ``JAVDB_HISTORY_WRITE_MODE`` env var.
      3. Default ``'audit'`` so the historic X3 path stays in effect for
         every workflow that has not opted in.
    """
    candidate = explicit
    if candidate is None:
        candidate = os.environ.get("JAVDB_HISTORY_WRITE_MODE")
    if not candidate:
        return "audit"
    candidate = candidate.strip().lower()
    if candidate not in _ALLOWED_WRITE_MODES:
        raise ValueError(
            f"Unknown WriteMode {candidate!r}; "
            f"expected one of {_ALLOWED_WRITE_MODES}"
        )
    return candidate


def db_get_session_status(
    session_id: int,
    *,
    db_path: Optional[str] = None,
) -> Optional[Tuple[str, str]]:
    """Return ``(WriteMode, Status)`` for *session_id*, or ``None`` if absent.

    Centralised so the rollback dispatcher and the commit / resume helpers
    all see the same view of the session row even when the underlying
    table briefly lacks the WriteMode column on legacy schemas.
    """
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        try:
            row = conn.execute(
                "SELECT WriteMode, Status FROM ReportSessions WHERE Id=?",
                (session_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            row = conn.execute(
                "SELECT Status FROM ReportSessions WHERE Id=?",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            return ("audit", row["Status"])
    if row is None:
        return None
    write_mode = row["WriteMode"] if row["WriteMode"] else "audit"
    return (write_mode, row["Status"])


def db_begin_finalize_session(
    session_id: int,
    *,
    db_path: Optional[str] = None,
) -> int:
    """Flip ``Status`` from ``in_progress`` to ``finalizing`` for *session_id*.

    Idempotent: a session already in ``finalizing`` returns 0 (no row
    change) so a crashed-during-finalize resume can call this without
    raising.  Sessions that are already ``committed`` or ``failed``
    refuse the transition (returns 0) — the caller is expected to call
    :func:`db_resume_finalizing_session` only when status is finalizing.
    """
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        cur = conn.execute(
            "UPDATE ReportSessions SET Status='finalizing' "
            "WHERE Id=? AND Status='in_progress'",
            (session_id,),
        )
        return cur.rowcount or 0


def db_finish_commit_session(
    session_id: int,
    *,
    db_path: Optional[str] = None,
) -> int:
    """Flip ``Status`` from ``finalizing`` to ``committed`` for *session_id*.

    Used by :func:`db_commit_session_history` once every per-movie commit
    has applied successfully.  Idempotent against ``committed`` rows.
    """
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        cur = conn.execute(
            "UPDATE ReportSessions SET Status='committed' "
            "WHERE Id=? AND Status='finalizing'",
            (session_id,),
        )
        return cur.rowcount or 0

# ── Ingestion Perfect Rollback: pending write path (Phase 2) ─────────────
#
# These four functions form the new ingestion write surface.  Phase 3
# default-on: every ingestion workflow (DailyIngestion, AdHocIngestion,
# TestIngestion) ships under ``WriteMode='pending'`` unless the
# ``write_mode_override`` workflow input or the
# ``pending_mode_disabled_until`` marker in ``.publish-config.yml`` flips
# it back to audit.  ``db_upsert_history`` is therefore only reached via
# legacy / fallback routes today; Phase 4 will mark it ``@deprecated``
# and reject new writes.
#
# Lifecycle:
#   db_stage_history_write(...)     # zero or more, while Status='in_progress'
#   db_load_history_snapshot(sid)   # any number of reads, returns committed
#                                   # live + this session's pending overlay
#   db_commit_session_history(sid)  # set finalizing → per-movie lock,
#                                   # recompute derived fields, UPSERT live,
#                                   # mark applied, set committed, delete
#                                   # applied pending rows
#   db_resume_finalizing_session(sid)  # idempotent re-run of commit, called
#                                      # by rollback CLI when a finalizing
#                                      # session crashed
#
# Rollback dispatch (see db_rollback_session):
#   WriteMode='audit'                       → existing X3 audit replay
#   WriteMode='pending', Status='in_progress'   → DELETE pending rows
#   WriteMode='pending', Status='finalizing'    → resume commit
#   Status='committed'                      → refused (X3 behaviour)


_PENDING_HREF_LOCKS_LOCK = threading.Lock()
_PENDING_HREF_LOCKS: "dict[str, threading.Lock]" = {}


def _href_lock(href: str) -> threading.Lock:
    """Return a process-local lock for *href*.

    Phase 2 runs spider / detail / qb_uploader / pikpak_bridge as separate
    processes that share a SessionId; the per-process lock here protects
    the in-process commit loop from accidentally running twice for the
    same Href when commit / resume race inside one CLI invocation.  The
    *cross-process* lease is the caller's job (Worker / MovieClaim
    coordinator); see Phase 1 of the plan.
    """
    with _PENDING_HREF_LOCKS_LOCK:
        lock = _PENDING_HREF_LOCKS.get(href)
        if lock is None:
            lock = threading.Lock()
            _PENDING_HREF_LOCKS[href] = lock
    return lock


_KIND_MOVIE = "movie"
_KIND_TORRENT = "torrent"
_PENDING_KINDS = (_KIND_MOVIE, _KIND_TORRENT)


def db_stage_history_write(
    session_id: int,
    kind: str,
    payload: Dict[str, Any],
    *,
    db_path: Optional[str] = None,
) -> int:
    """Append a row to PendingMovie/TorrentHistoryWrites.

    *kind* must be ``'movie'`` or ``'torrent'``; *payload* carries the
    row columns expected by the matching table (the helper extracts
    them and supplies defaults so callers can be dict-shape lenient).

    Returns the new ``Seq`` value.  The active ``(RunId, RunAttempt)``
    context (set via :func:`set_active_run_identity`) is mirrored into
    the pending row so the rollback CLI can join across the run identity
    when the ReportSessions row was reaped early.

    Phase 2 ``Seq`` is generated via :func:`_generate_session_id` (the
    same 51-bit snowflake used by ``ReportSessions.Id``) and inserted
    explicitly.  Both Pending tables are listed in
    ``APPLICATION_GENERATED_ID_TABLES`` so the dual-backend guard catches
    any case where SQLite and D1 see different ``Seq`` for the same
    logical row — a drift here would silently leave residual pending
    rows after commit because ``_commit_one_movie`` marks ``applied`` by
    ``Seq IN (...)``.
    """
    if kind not in _PENDING_KINDS:
        raise ValueError(
            f"db_stage_history_write: kind must be one of {_PENDING_KINDS}, "
            f"got {kind!r}"
        )
    if not payload.get("Href") and not payload.get("href"):
        raise ValueError("db_stage_history_write: payload requires 'Href'")
    href = payload.get("Href") or payload.get("href")
    video_code = payload.get("VideoCode") or payload.get("video_code")
    visited = (
        payload.get("DateTimeVisited")
        or payload.get("date_time_visited")
        or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    run_id, run_attempt = get_active_run_identity()
    seq = _generate_session_id()
    # Defence in depth: catch any future code path that bypasses
    # ``_generate_session_id`` (and would otherwise let SQLite emit a
    # small AUTOINCREMENT id that diverges from D1). Post-B.2 (2026-05-11)
    # layout is ``ms (41 bit) << 22 | process_tag (12 bit) << 10 |
    # counter (10 bit)``, so any real snowflake is >= ``1 << 52`` for
    # any time after 2004-09-17. We keep the assertion at ``1 << 40``
    # so older callers that minted Ids under the pre-B.2 layout
    # (``ms << 10``, lower bound ``1 << 40``) still pass — the goal is
    # to catch a stray ``1`` / small AUTOINCREMENT, not enforce the
    # exact layout.
    if seq < (1 << 40):
        raise ValueError(
            f"db_stage_history_write: refusing to INSERT with Seq={seq!r} "
            f"(expected a 63-bit snowflake from _generate_session_id; "
            f"a small int here means a caller bypassed the snowflake path "
            f"and would diverge from D1 under STORAGE_BACKEND=dual)."
        )
    with get_db(db_path or HISTORY_DB_PATH) as conn:
        if kind == _KIND_MOVIE:
            conn.execute(
                """INSERT INTO PendingMovieHistoryWrites
                   (Seq, SessionId, RunId, RunAttempt, Href, VideoCode,
                    ActorName, ActorGender, ActorLink, SupportingActors,
                    DateTimeVisited, CreatedAt, ApplyState)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (
                    seq,
                    int(session_id),
                    run_id,
                    run_attempt,
                    href,
                    video_code,
                    payload.get("ActorName") or payload.get("actor_name"),
                    payload.get("ActorGender") or payload.get("actor_gender"),
                    payload.get("ActorLink") or payload.get("actor_link"),
                    (
                        payload.get("SupportingActors")
                        or payload.get("supporting_actors")
                    ),
                    visited,
                    now,
                ),
            )
        else:
            sub_ind = payload.get("SubtitleIndicator")
            cen_ind = payload.get("CensorIndicator")
            category = payload.get("Category") or payload.get("category")
            if sub_ind is None or cen_ind is None:
                if not category:
                    raise ValueError(
                        "db_stage_history_write(torrent): payload needs "
                        "either Category or (SubtitleIndicator, CensorIndicator)"
                    )
                sub_ind, cen_ind = category_to_indicators(category)
            if not category:
                category = indicators_to_category(int(sub_ind), int(cen_ind))
            conn.execute(
                """INSERT INTO PendingTorrentHistoryWrites
                   (Seq, SessionId, RunId, RunAttempt, Href, VideoCode,
                    Category, SubtitleIndicator, CensorIndicator,
                    MagnetUri, Size, FileCount, ResolutionType,
                    DateTimeVisited, CreatedAt, ApplyState)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (
                    seq,
                    int(session_id),
                    run_id,
                    run_attempt,
                    href,
                    video_code,
                    category,
                    int(sub_ind),
                    int(cen_ind),
                    payload.get("MagnetUri") or payload.get("magnet_uri"),
                    payload.get("Size") or payload.get("size"),
                    int(payload.get("FileCount") or payload.get("file_count") or 0),
                    payload.get("ResolutionType") or payload.get("resolution_type"),
                    visited,
                    now,
                ),
            )
        return seq


def _pending_movie_overlay(
    conn,
    session_id: int,
    *,
    href: Optional[str] = None,
    include_states: Tuple[str, ...] = ("pending",),
) -> Dict[str, dict]:
    """Return ``{href: merged_pending_movie_row}`` for *session_id*.

    Each pending row is sparse: a write coming from
    :func:`save_parsed_movie_to_history` carries the actor / supporting
    fields, while a write coming from :func:`db_batch_update_last_visited`
    only carries ``DateTimeVisited``.  We merge across the rows for a
    single Href so a later sparse stage cannot accidentally clobber the
    earlier stage's columns just because its own copy of those columns
    is ``NULL``.

    Merge rule, ordered by ``Seq ASC``:
      * later rows override earlier rows whenever their value is not
        ``None``;
      * ``Seq`` reflects the latest stage; the full list of Seqs that
        contributed to the merged payload is exposed under the
        ``_merged_seqs`` private key so the commit path can mark
        every contributing row as ``applied`` (otherwise an earlier
        sparse stage would leak as a permanent ``pending`` row after
        commit).
    """
    placeholders = ",".join("?" for _ in include_states)
    params: list = [int(session_id)]
    params.extend(include_states)
    where_extra = ""
    if href is not None:
        where_extra = " AND Href=?"
        params.append(href)
    sql = (
        "SELECT * FROM PendingMovieHistoryWrites "
        f"WHERE SessionId=? AND ApplyState IN ({placeholders}){where_extra}"
        " ORDER BY Seq ASC"
    )
    overlay: Dict[str, dict] = {}
    for row in conn.execute(sql, params).fetchall():
        d = dict(row)
        key = d["Href"]
        existing = overlay.get(key)
        if existing is None:
            d["_merged_seqs"] = [int(d["Seq"])]
            overlay[key] = d
            continue
        existing["_merged_seqs"].append(int(d["Seq"]))
        for col, value in d.items():
            if col == "_merged_seqs":
                continue
            if col == "Seq":
                existing[col] = value
                continue
            if value is not None:
                existing[col] = value
    return overlay


def _pending_torrent_overlay(
    conn,
    session_id: int,
    *,
    href: Optional[str] = None,
    include_states: Tuple[str, ...] = ("pending",),
) -> Dict[Tuple[str, int, int], dict]:
    """Return ``{(href, sub, cen): merged_pending_torrent_row}`` for *session_id*.

    Mirrors :func:`_pending_movie_overlay`: when multiple pending rows
    share the same ``(href, sub, cen)`` key (e.g. a retry / re-fetch
    staged a second time for the same torrent type), the **latest**
    non-NULL field values shadow earlier ones, but the merged row's
    ``_merged_seqs`` list carries **every** consumed ``Seq`` so the
    commit path can mark them all ``ApplyState='applied'``.

    P0-4: the legacy implementation only retained the last ``Seq``,
    leaving earlier rows stuck in ``pending`` after
    ``db_commit_session_history``. That residue then triggered the
    Phase 3 critical pending-mode alert and the auto-fallback to
    audit mode for the next run.
    """
    placeholders = ",".join("?" for _ in include_states)
    params: list = [int(session_id)]
    params.extend(include_states)
    where_extra = ""
    if href is not None:
        where_extra = " AND Href=?"
        params.append(href)
    sql = (
        "SELECT * FROM PendingTorrentHistoryWrites "
        f"WHERE SessionId=? AND ApplyState IN ({placeholders}){where_extra}"
        " ORDER BY Seq ASC"
    )
    overlay: Dict[Tuple[str, int, int], dict] = {}
    for row in conn.execute(sql, params).fetchall():
        d = dict(row)
        key = (
            d["Href"],
            int(d["SubtitleIndicator"]),
            int(d["CensorIndicator"]),
        )
        existing = overlay.get(key)
        if existing is None:
            d["_merged_seqs"] = [int(d["Seq"])]
            overlay[key] = d
            continue
        existing["_merged_seqs"].append(int(d["Seq"]))
        for col, value in d.items():
            if col == "_merged_seqs":
                continue
            if col == "Seq":
                # Track the newest Seq as the canonical row pointer; the
                # full list lives in _merged_seqs.
                existing[col] = value
                continue
            if value is not None:
                existing[col] = value
    return overlay


def db_load_history_snapshot(
    session_id: Optional[int] = None,
    *,
    db_path: Optional[str] = None,
) -> Dict[str, dict]:
    """Return committed-live history with the *session_id* pending overlay.

    When *session_id* is ``None``, returns just the committed live state
    (equivalent to :func:`load_history_joined`).  Otherwise, the pending
    rows for that session shadow the live values per Href / per torrent
    type, giving the caller a "what would we see if we committed right
    now" view without polluting other sessions' reads.
    """
    with get_db(db_path or HISTORY_DB_PATH) as conn:
        snapshot = _load_history_joined(conn)
        if session_id is None:
            return snapshot
        movie_overlay = _pending_movie_overlay(conn, session_id)
        torrent_overlay = _pending_torrent_overlay(conn, session_id)

    for href, row in movie_overlay.items():
        item = snapshot.get(href)
        if item is None:
            item = {
                "VideoCode": row.get("VideoCode") or "",
                "DateTimeCreated": row.get("CreatedAt") or "",
                "DateTimeUpdated": row.get("CreatedAt") or "",
                "DateTimeVisited": row.get("DateTimeVisited") or "",
                "PerfectMatchIndicator": False,
                "HiResIndicator": False,
                "ActorName": row.get("ActorName"),
                "ActorGender": row.get("ActorGender"),
                "ActorLink": row.get("ActorLink"),
                "SupportingActors": row.get("SupportingActors"),
                "torrent_types": [],
                "torrents": {},
            }
            snapshot[href] = item
        else:
            for col in (
                "VideoCode", "ActorName", "ActorGender",
                "ActorLink", "SupportingActors",
            ):
                if row.get(col) is not None:
                    item[col] = row.get(col)
            if row.get("DateTimeVisited"):
                item["DateTimeVisited"] = row["DateTimeVisited"]

    for (href, sub, cen), row in torrent_overlay.items():
        item = snapshot.get(href)
        if item is None:
            item = {
                "VideoCode": row.get("VideoCode") or "",
                "DateTimeCreated": row.get("CreatedAt") or "",
                "DateTimeUpdated": row.get("CreatedAt") or "",
                "DateTimeVisited": row.get("DateTimeVisited") or "",
                "PerfectMatchIndicator": False,
                "HiResIndicator": False,
                "ActorName": None,
                "ActorGender": None,
                "ActorLink": None,
                "SupportingActors": None,
                "torrent_types": [],
                "torrents": {},
            }
            snapshot[href] = item
        cat = indicators_to_category(int(sub), int(cen))
        if cat not in item["torrent_types"]:
            item["torrent_types"].append(cat)
        item["torrents"][(int(sub), int(cen))] = {
            "MagnetUri": row.get("MagnetUri") or "",
            "Size": row.get("Size") or "",
            "FileCount": row.get("FileCount") or 0,
            "ResolutionType": row.get("ResolutionType"),
            "DateTimeCreated": row.get("CreatedAt") or "",
            "DateTimeUpdated": row.get("CreatedAt") or "",
        }

    # Recompute the derived indicators so callers see the same value the
    # commit step would land in MovieHistory.PerfectMatchIndicator /
    # HiResIndicator.  Live-only callers (session_id=None) skip this.
    for item in snapshot.values():
        torrents = item.get("torrents", {})
        item["PerfectMatchIndicator"] = bool(
            (1, 0) in torrents and (1, 1) in torrents
        )
        item["HiResIndicator"] = any(
            (t.get("ResolutionType") or 0) >= 2560
            for t in torrents.values()
        )

    return snapshot


def _commit_one_movie(
    conn,
    session_id: int,
    href: str,
    *,
    when: str,
) -> Dict[str, int]:
    """Apply one Href's pending writes onto the live tables.

    The function is idempotent: it always recomputes the live row from
    ``MovieHistory + TorrentHistory + (every pending row for this href in
    this session)``, then upserts the result and marks every consumed
    pending row ``ApplyState='applied'``.  A crash + resume re-runs it
    against the same inputs and lands the same outputs.
    """
    counts = {
        "movies_upserted": 0,
        "torrents_upserted": 0,
        "torrents_deleted": 0,
        "pending_marked_applied": 0,
    }
    movie_overlay = _pending_movie_overlay(
        conn, session_id, href=href, include_states=("pending", "applied"),
    )
    torrent_overlay = _pending_torrent_overlay(
        conn, session_id, href=href, include_states=("pending", "applied"),
    )
    movie_payload = movie_overlay.get(href)

    base_url = cfg("BASE_URL", "https://javdb.com")
    path_href, abs_href = movie_href_lookup_values(href, base_url)
    lookup_hrefs = [h for h in (path_href, abs_href, href) if h]

    placeholders = ",".join("?" for _ in lookup_hrefs)
    existing = conn.execute(
        f"SELECT * FROM MovieHistory WHERE Href IN ({placeholders})",
        lookup_hrefs,
    ).fetchone()

    if movie_payload is None and existing is None and not torrent_overlay:
        return counts

    if existing is None:
        video_code = (
            (movie_payload or {}).get("VideoCode")
            or next(
                (
                    r.get("VideoCode") for r in torrent_overlay.values()
                    if r.get("VideoCode")
                ),
                "",
            )
            or ""
        )
        normalized_href = abs_href or href
        cur = conn.execute(
            """INSERT INTO MovieHistory
               (VideoCode, Href, DateTimeCreated, DateTimeUpdated,
                DateTimeVisited, ActorName, ActorGender, ActorLink,
                SupportingActors, SessionId)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                video_code,
                normalized_href,
                when,
                when,
                (movie_payload or {}).get("DateTimeVisited") or when,
                (movie_payload or {}).get("ActorName"),
                (movie_payload or {}).get("ActorGender"),
                (movie_payload or {}).get("ActorLink"),
                (movie_payload or {}).get("SupportingActors"),
                int(session_id),
            ),
        )
        movie_id = int(cur.lastrowid or 0)
        counts["movies_upserted"] += 1
    else:
        movie_id = int(existing["Id"])
        update_fields = ["DateTimeUpdated=?", "SessionId=?"]
        params: list = [when, int(session_id)]
        if movie_payload is not None:
            update_fields.append("DateTimeVisited=?")
            params.append(
                movie_payload.get("DateTimeVisited") or when
            )
            for column, payload_key in (
                ("ActorName", "ActorName"),
                ("ActorGender", "ActorGender"),
                ("ActorLink", "ActorLink"),
                ("SupportingActors", "SupportingActors"),
                ("VideoCode", "VideoCode"),
            ):
                value = movie_payload.get(payload_key)
                if value is not None:
                    update_fields.append(f"{column}=?")
                    params.append(value)
        params.append(movie_id)
        conn.execute(
            f"UPDATE MovieHistory SET {', '.join(update_fields)} WHERE Id=?",
            params,
        )
        counts["movies_upserted"] += 1

    consumed_movie_seqs: list = []
    for r in movie_overlay.values():
        # ``_merged_seqs`` is populated by ``_pending_movie_overlay``
        # for sparse-merge mode (Phase 2: visit-only / actor-only
        # stages contribute multiple rows per href).  Fall back to
        # ``Seq`` for the legacy single-row case to stay defensive.
        seqs = r.get("_merged_seqs") or [int(r["Seq"])]
        consumed_movie_seqs.extend(int(s) for s in seqs)

    consumed_torrent_seqs: list = []
    for (_, sub, cen), payload in torrent_overlay.items():
        existing_t = conn.execute(
            "SELECT Id FROM TorrentHistory "
            "WHERE MovieHistoryId=? AND SubtitleIndicator=? AND CensorIndicator=?",
            (movie_id, int(sub), int(cen)),
        ).fetchone()
        if existing_t is None:
            conn.execute(
                """INSERT INTO TorrentHistory
                   (MovieHistoryId, MagnetUri, SubtitleIndicator,
                    CensorIndicator, ResolutionType, Size, FileCount,
                    DateTimeCreated, DateTimeUpdated, SessionId)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    movie_id,
                    payload.get("MagnetUri"),
                    int(sub),
                    int(cen),
                    payload.get("ResolutionType"),
                    payload.get("Size") or "",
                    int(payload.get("FileCount") or 0),
                    when,
                    when,
                    int(session_id),
                ),
            )
        else:
            conn.execute(
                """UPDATE TorrentHistory
                   SET MagnetUri=?, Size=?, FileCount=?, ResolutionType=?,
                       DateTimeUpdated=?, SessionId=?
                   WHERE Id=?""",
                (
                    payload.get("MagnetUri"),
                    payload.get("Size") or "",
                    int(payload.get("FileCount") or 0),
                    payload.get("ResolutionType"),
                    when,
                    int(session_id),
                    int(existing_t["Id"]),
                ),
            )
        counts["torrents_upserted"] += 1
        # P0-4: consume EVERY pending row that fed into this merged
        # payload, not just the last Seq. ``_pending_torrent_overlay``
        # now populates ``_merged_seqs`` for the same reason
        # ``_pending_movie_overlay`` does — re-staging (retry / re-fetch
        # / sparse-merge) creates multiple rows per (href, sub, cen)
        # and the legacy single-Seq update silently left the earlier
        # rows stuck in ``ApplyState='pending'``, which then tripped
        # the Phase 3 residual-pending alert.
        merged = payload.get("_merged_seqs")
        if merged:
            consumed_torrent_seqs.extend(int(s) for s in merged)
        else:
            consumed_torrent_seqs.append(int(payload["Seq"]))

    # Apply the same "hacked_subtitle wins over hacked_no_subtitle, subtitle
    # wins over no_subtitle" rule the audit path enforces in db_upsert_history.
    has_hacked_sub = any(
        sub == 1 and cen == 0 for (_, sub, cen) in torrent_overlay.keys()
    )
    has_subtitle = any(
        sub == 1 and cen == 1 for (_, sub, cen) in torrent_overlay.keys()
    )
    if has_hacked_sub:
        cur = conn.execute(
            "DELETE FROM TorrentHistory WHERE MovieHistoryId=? "
            "AND SubtitleIndicator=0 AND CensorIndicator=0",
            (movie_id,),
        )
        counts["torrents_deleted"] += cur.rowcount or 0
    if has_subtitle:
        cur = conn.execute(
            "DELETE FROM TorrentHistory WHERE MovieHistoryId=? "
            "AND SubtitleIndicator=0 AND CensorIndicator=1",
            (movie_id,),
        )
        counts["torrents_deleted"] += cur.rowcount or 0

    # Recompute derived indicators directly (avoid the audit-tagged
    # _update_movie_indicators path; pending mode never writes audit rows).
    perfect_row = conn.execute(
        "SELECT 1 FROM TorrentHistory t1 "
        "JOIN TorrentHistory t2 ON t1.MovieHistoryId=t2.MovieHistoryId "
        "WHERE t1.MovieHistoryId=? "
        "AND t1.SubtitleIndicator=1 AND t1.CensorIndicator=0 "
        "AND t2.SubtitleIndicator=1 AND t2.CensorIndicator=1",
        (movie_id,),
    ).fetchone()
    hires_row = conn.execute(
        "SELECT 1 FROM TorrentHistory "
        "WHERE MovieHistoryId=? AND ResolutionType >= 2560",
        (movie_id,),
    ).fetchone()
    conn.execute(
        "UPDATE MovieHistory SET PerfectMatchIndicator=?, "
        "HiResIndicator=? WHERE Id=?",
        (1 if perfect_row else 0, 1 if hires_row else 0, movie_id),
    )

    consumed_seqs = consumed_movie_seqs
    if consumed_seqs:
        ph = ",".join("?" for _ in consumed_seqs)
        cur = conn.execute(
            f"UPDATE PendingMovieHistoryWrites SET ApplyState='applied' "
            f"WHERE Seq IN ({ph})",
            consumed_seqs,
        )
        counts["pending_marked_applied"] += cur.rowcount or 0
    if consumed_torrent_seqs:
        ph = ",".join("?" for _ in consumed_torrent_seqs)
        cur = conn.execute(
            f"UPDATE PendingTorrentHistoryWrites SET ApplyState='applied' "
            f"WHERE Seq IN ({ph})",
            consumed_torrent_seqs,
        )
        counts["pending_marked_applied"] += cur.rowcount or 0
    return counts


def _pending_distinct_hrefs(conn, session_id: int) -> List[str]:
    """Return every Href that has at least one pending row for *session_id*."""
    rows = conn.execute(
        "SELECT Href FROM ("
        "  SELECT Href FROM PendingMovieHistoryWrites "
        "  WHERE SessionId=? AND ApplyState IN ('pending','applied') "
        "  UNION "
        "  SELECT Href FROM PendingTorrentHistoryWrites "
        "  WHERE SessionId=? AND ApplyState IN ('pending','applied')"
        ") ORDER BY Href",
        (int(session_id), int(session_id)),
    ).fetchall()
    return [r["Href"] for r in rows]


def db_commit_session_history(
    session_id: int,
    *,
    history_db_path: Optional[str] = None,
    reports_db_path: Optional[str] = None,
) -> Dict[str, int]:
    """Drain pending writes for *session_id* into MovieHistory / TorrentHistory.

    State transitions executed:

      in_progress → finalizing  (set up-front)
      finalizing → committed    (set when every Href has applied)

    Returns aggregate per-table counts.  Callers should treat the
    function as the canonical "drain pending" entry point; recovery
    from a crash midway through is via :func:`db_resume_finalizing_session`.
    """
    when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    counts = {
        "movies_upserted": 0,
        "torrents_upserted": 0,
        "torrents_deleted": 0,
        "pending_marked_applied": 0,
        "pending_deleted": 0,
        "hrefs_processed": 0,
    }

    state = db_get_session_status(
        session_id, db_path=reports_db_path,
    )
    if state is None:
        return counts
    write_mode, status = state
    if write_mode != "pending":
        raise ValueError(
            f"db_commit_session_history: session {session_id} has "
            f"WriteMode={write_mode!r}; expected 'pending'"
        )
    if status not in ("in_progress", "finalizing", "committed"):
        raise ValueError(
            f"db_commit_session_history: session {session_id} has "
            f"Status={status!r}; expected one of in_progress / "
            f"finalizing / committed"
        )

    if status == "in_progress":
        db_begin_finalize_session(session_id, db_path=reports_db_path)

    # P1: snapshot the href list, but re-scan at the end so any pending
    # rows staged AFTER the initial scan (by a concurrent stager that
    # raced this finalize) are not left stuck in ``ApplyState='pending'``
    # — that residue is the Phase 3 critical alert trigger.
    processed: set = set()
    with get_db(history_db_path or HISTORY_DB_PATH) as conn:
        hrefs = _pending_distinct_hrefs(conn, session_id)

    def _drain(href_list):
        for href in href_list:
            if href in processed:
                continue
            with _href_lock(href):
                with get_db(history_db_path or HISTORY_DB_PATH) as conn:
                    per_movie = _commit_one_movie(
                        conn, session_id, href, when=when,
                    )
                    for k, v in per_movie.items():
                        counts[k] = counts.get(k, 0) + v
            processed.add(href)

    _drain(hrefs)

    # Re-scan for hrefs that arrived after the initial snapshot. Bounded
    # by a small loop count to avoid the (pathological) case where a
    # stager keeps adding pending rows in lock-step with this finalize.
    for _ in range(3):
        with get_db(history_db_path or HISTORY_DB_PATH) as conn:
            extra = [h for h in _pending_distinct_hrefs(conn, session_id)
                     if h not in processed]
        if not extra:
            break
        logger.info(
            "db_commit_session_history(session=%s): rescan found %d "
            "additional pending href(s) staged after initial snapshot",
            session_id, len(extra),
        )
        _drain(extra)

    counts["hrefs_processed"] = len(processed)

    # Flip Status to 'committed' BEFORE the final pending-table DELETE so a
    # crash between the two leaves a recoverable footprint.  Failure modes:
    #   * crash before flip → Status='finalizing' + applied rows.  Resume
    #     re-runs the loop (idempotent per ``_commit_one_movie`` docstring),
    #     reaches this point, flips, deletes.
    #   * crash after flip, before delete → Status='committed' + applied
    #     rows.  Resume re-enters via ``db_resume_finalizing_session`` which
    #     accepts 'committed', re-runs the loop (idempotent on already-
    #     applied rows since ``_pending_*_overlay`` reads both states),
    #     reaches the no-op flip, deletes.
    # The reverse order (delete first, flip last) was monitoring-hostile:
    # a crash mid-flip left ``Status='finalizing'`` with zero pending rows,
    # which any "stuck session" alert misreads as a hung commit.
    db_finish_commit_session(session_id, db_path=reports_db_path)

    with get_db(history_db_path or HISTORY_DB_PATH) as conn:
        cur_m = conn.execute(
            "DELETE FROM PendingMovieHistoryWrites "
            "WHERE SessionId=? AND ApplyState='applied'",
            (int(session_id),),
        )
        cur_t = conn.execute(
            "DELETE FROM PendingTorrentHistoryWrites "
            "WHERE SessionId=? AND ApplyState='applied'",
            (int(session_id),),
        )
        counts["pending_deleted"] = (cur_m.rowcount or 0) + (cur_t.rowcount or 0)

    return counts


def db_resume_finalizing_session(
    session_id: int,
    *,
    history_db_path: Optional[str] = None,
    reports_db_path: Optional[str] = None,
) -> Dict[str, int]:
    """Idempotently finish a session left in ``Status='finalizing'``.

    Identical to :func:`db_commit_session_history` aside from the
    pre-condition: the session must already be in ``finalizing`` (or
    ``committed`` — then the call is a no-op).  Used by the rollback CLI
    to drive a crashed-mid-commit session to ``committed`` instead of
    rewinding it.
    """
    state = db_get_session_status(
        session_id, db_path=reports_db_path,
    )
    if state is None:
        return {
            "movies_upserted": 0,
            "torrents_upserted": 0,
            "torrents_deleted": 0,
            "pending_marked_applied": 0,
            "pending_deleted": 0,
            "hrefs_processed": 0,
        }
    write_mode, status = state
    if write_mode != "pending":
        raise ValueError(
            f"db_resume_finalizing_session: session {session_id} has "
            f"WriteMode={write_mode!r}; expected 'pending'"
        )
    if status not in ("finalizing", "committed"):
        raise ValueError(
            f"db_resume_finalizing_session: session {session_id} has "
            f"Status={status!r}; expected 'finalizing' or 'committed'"
        )
    return db_commit_session_history(
        session_id,
        history_db_path=history_db_path,
        reports_db_path=reports_db_path,
    )


def db_pending_session_stats(
    session_id: int,
    *,
    db_path: Optional[str] = None,
) -> Dict[str, int]:
    """Snapshot pending-table counts for *session_id* (Phase 2 verify).

    Returns a dict with three keys:

    * ``pending_residual_count`` — rows still flagged ``ApplyState='pending'``
      across both ``PendingMovieHistoryWrites`` and
      ``PendingTorrentHistoryWrites``.  After a successful commit this
      number must be 0; a non-zero value means
      :func:`db_commit_session_history` did not drain every staged row
      and the session is half-applied.
    * ``pending_applied_count`` — rows currently flagged
      ``ApplyState='applied'``.  ``db_commit_session_history`` deletes
      these at the end of its run, so once the session is committed
      this is also 0.  A non-zero value here on a committed session
      points at an interrupted commit (operator must re-run
      :func:`db_resume_finalizing_session`).
    * ``pending_total_count`` — sum of the two above (the row count that
      is still resident in the pending tables for this session).

    Tables that don't exist yet (e.g. legacy-schema test fixtures) are
    treated as zero so callers in the metric-emission path never raise.
    """
    counts = {
        "pending_residual_count": 0,
        "pending_applied_count": 0,
        "pending_total_count": 0,
    }
    with get_db(db_path or HISTORY_DB_PATH) as conn:
        for tbl in (
            "PendingMovieHistoryWrites",
            "PendingTorrentHistoryWrites",
        ):
            try:
                row = conn.execute(
                    f"SELECT "
                    f"  SUM(CASE WHEN ApplyState='pending' THEN 1 ELSE 0 END) "
                    f"    AS pending_n, "
                    f"  SUM(CASE WHEN ApplyState='applied' THEN 1 ELSE 0 END) "
                    f"    AS applied_n "
                    f"FROM {tbl} WHERE SessionId=?",
                    (int(session_id),),
                ).fetchone()
            except sqlite3.OperationalError:
                continue
            if row is None:
                continue
            counts["pending_residual_count"] += int(row["pending_n"] or 0)
            counts["pending_applied_count"] += int(row["applied_n"] or 0)
    counts["pending_total_count"] = (
        counts["pending_residual_count"] + counts["pending_applied_count"]
    )
    return counts


def db_get_session_run_identity(
    session_id: int,
    *,
    db_path: Optional[str] = None,
) -> Optional[Tuple[Optional[str], Optional[int]]]:
    """Return ``(RunId, RunAttempt)`` for *session_id*, or ``None`` if absent.

    Used by the verify-metric writers so the emitted JSONL line can be
    correlated with the GitHub Actions workflow run that produced it
    (workflow logs only capture the env values; the verify line lets
    operators look up the original run after the fact).
    """
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        row = conn.execute(
            "SELECT RunId, RunAttempt FROM ReportSessions WHERE Id=?",
            (int(session_id),),
        ).fetchone()
    if row is None:
        return None
    return (row["RunId"], row["RunAttempt"])


def _rollback_pending_in_progress(
    session_id: int,
    *,
    dry_run: bool,
    db_path: Optional[str] = None,
    run_started_at: Optional[str] = None,
) -> Dict[str, int]:
    """Drop pending writes for an in-progress pending-mode session.

    Mirrors the structure of :func:`_rollback_history` for the audit
    path: returns per-table counts, supports dry-run, never touches
    other sessions' rows.

    Safety net (Phase 2 transition): even though pending-mode sessions
    SHOULD only hold rows in PendingMovie/TorrentHistoryWrites, any code
    path that still calls :func:`db_upsert_history` directly under a
    pending session writes live rows + audit rows.  We replay those
    audit rows here so a half-migrated callsite cannot silently leak
    writes into the live MovieHistory / TorrentHistory tables.  Once
    every ingestion path goes pending the replay finds zero rows and
    is a cheap no-op.
    """
    counts: Dict[str, int] = {
        "PendingMovieHistoryWrites": 0,
        "PendingTorrentHistoryWrites": 0,
        "drift_skipped": 0,
        "orphan_pruned": 0,
    }
    with get_db(db_path or HISTORY_DB_PATH) as conn:
        if dry_run:
            counts["PendingMovieHistoryWrites"] = (conn.execute(
                "SELECT COUNT(*) AS n FROM PendingMovieHistoryWrites "
                "WHERE SessionId=?",
                (int(session_id),),
            ).fetchone() or {"n": 0})["n"]
            counts["PendingTorrentHistoryWrites"] = (conn.execute(
                "SELECT COUNT(*) AS n FROM PendingTorrentHistoryWrites "
                "WHERE SessionId=?",
                (int(session_id),),
            ).fetchone() or {"n": 0})["n"]
        else:
            cur_m = conn.execute(
                "DELETE FROM PendingMovieHistoryWrites WHERE SessionId=?",
                (int(session_id),),
            )
            cur_t = conn.execute(
                "DELETE FROM PendingTorrentHistoryWrites WHERE SessionId=?",
                (int(session_id),),
            )
            counts["PendingMovieHistoryWrites"] = cur_m.rowcount or 0
            counts["PendingTorrentHistoryWrites"] = cur_t.rowcount or 0

    # Safety net: replay any leftover audit rows for this session.
    # Returns its own per-table counts; merge into ours so the caller
    # sees both halves in a single dict (legacy keys stay intact).
    audit_counts = _rollback_history(
        session_id,
        dry_run=dry_run,
        db_path=db_path,
        run_started_at=run_started_at,
    )
    for k, v in audit_counts.items():
        counts[k] = counts.get(k, 0) + v
    return counts

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
    run_id: Optional[str] = None,
    run_attempt: Optional[int] = None,
    session_id: Optional[int] = None,
    write_mode: Optional[str] = None,
) -> int:
    """Create a new report session and return its id.

    Behaviour change (2026-05-08): the *Id* is now generated by the
    application (via :func:`_generate_session_id`) and inserted explicitly,
    so SQLite and Cloudflare D1 see the same value. The previous behaviour
    relied on each backend's AUTOINCREMENT and ``cur.lastrowid``, which
    drifts under ``STORAGE_BACKEND=dual``.

    *run_id* / *run_attempt* (optional) record the GitHub Actions workflow
    run that owns this session. The rollback CLI's primary lookup path is
    by ``(RunId, RunAttempt)``; ``Id`` is the secondary path for callers
    that already know the snowflake id.

    *session_id* (optional, advanced) lets the caller pin a specific id
    instead of generating one — used by the migration tool that needs to
    preserve historical ids when copying CSVs into the DB.

    The new row is tagged with ``Status='in_progress'`` so the rollback CLI
    can identify uncommitted runs. Call :func:`db_mark_session_committed`
    after the pipeline successfully finishes to flip the flag and protect
    the session's writes from being cleaned up.
    """
    from packages.python.javdb_platform.config_helper import (
        db_writes_forbidden,
    )
    if db_writes_forbidden():
        raise RuntimeError(
            "db_create_report_session refused: "
            "JAVDB_FORBID_DB_WRITES=1 is engaged. This kill switch is "
            "set by smoke-test workflows (TestIngestion) that must not "
            "pollute production D1 / SQLite. Unset it if you really "
            "need to write to a database."
        )
    if created_at is None:
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sid = int(session_id) if session_id is not None else _generate_session_id()
    resolved_mode = _resolve_write_mode(write_mode)
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        conn.execute(
            """INSERT INTO ReportSessions
               (Id, ReportType, ReportDate, UrlType, DisplayName,
                Url, StartPage, EndPage, CsvFilename, DateTimeCreated,
                Status, RunId, RunAttempt, WriteMode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'in_progress', ?, ?, ?)""",
            (sid, report_type, report_date, url_type, display_name,
             url, start_page, end_page, csv_filename, created_at,
             run_id, run_attempt, resolved_mode),
        )
    return sid


def db_mark_session_committed(
    session_id: int,
    db_path: Optional[str] = None,
) -> int:
    """Mark a session as ``committed`` so it survives any future cleanup.

    Once a session is committed, its audit rows in
    ``MovieHistoryAudit`` / ``TorrentHistoryAudit`` are pruned (the
    rollback CLI refuses committed sessions anyway, so the audit log is
    no longer needed and only adds noise + table bloat).

    Returns the number of ReportSessions rows updated (0 if session not
    found or already committed). Idempotent: re-running on a committed
    session is a no-op (and prunes any leftover audit rows).
    """
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        cur = conn.execute(
            "UPDATE ReportSessions SET Status='committed' WHERE Id=? "
            "AND Status IS NOT 'committed'",
            (session_id,),
        )
        marked = cur.rowcount or 0

    # Audit retention: prune audit rows for this session even when the
    # ReportSessions update was a no-op (Status was already 'committed' on
    # a previous call). Keeps the tables bounded if commit is retried.
    try:
        with get_db(HISTORY_DB_PATH) as conn:
            mh = conn.execute(
                "DELETE FROM MovieHistoryAudit WHERE SessionId=?",
                (session_id,),
            )
            th = conn.execute(
                "DELETE FROM TorrentHistoryAudit WHERE SessionId=?",
                (session_id,),
            )
        pruned = (mh.rowcount or 0) + (th.rowcount or 0)
        if pruned > 0:
            logger.info(
                "Pruned %d audit row(s) on commit of session %s",
                pruned, session_id,
            )
    except Exception as exc:  # noqa: BLE001 - best-effort retention
        logger.warning(
            "Could not prune audit rows for committed session %s: %s",
            session_id, exc,
        )

    return marked


def db_mark_session_failed(
    session_id: int,
    db_path: Optional[str] = None,
    *,
    reason: Optional[str] = None,
) -> int:
    """Mark a session as ``failed`` (debug-only flag set right before delete).

    *reason* is persisted to ``ReportSessions.FailureReason`` so post-
    incident analysis can distinguish workflow_cancel vs runtime crash
    vs stale_timeout (set by the daily stale-cleanup cron).
    """
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        cur = conn.execute(
            "UPDATE ReportSessions SET Status='failed', FailureReason=? "
            "WHERE Id=?",
            (reason, session_id),
        )
        return cur.rowcount or 0


def db_find_in_progress_sessions(
    *,
    since: Optional[str] = None,
    db_path: Optional[str] = None,
    max_age_hours: Optional[float] = None,
    require_run_identity: bool = False,
) -> List[int]:
    """Return ``ReportSessions.Id`` rows still flagged ``in_progress``.

    *since* (ISO timestamp) restricts the search to sessions created on
    or after the given moment — typically the workflow ``run_started_at``
    so the cleanup job only sees sessions belonging to the failed run.

    *max_age_hours* — alternative window for the stale-session cleanup
    cron: returns sessions whose ``DateTimeCreated`` is older than
    ``now - max_age_hours``.  Mutually exclusive with *since*.

    *require_run_identity* — when true, only include sessions with a
    non-empty ``RunId``.  This helps stale-session cleanup skip legacy
    pre-run-identity rows that were historically marked ``in_progress``.
    """
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        if since and max_age_hours is not None:
            raise ValueError(
                "db_find_in_progress_sessions: pass either 'since' or "
                "'max_age_hours', not both"
            )
        if max_age_hours is not None:
            cutoff_ts = time.time() - (max_age_hours * 3600)
            cutoff = datetime.utcfromtimestamp(cutoff_ts).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            run_identity_clause = (
                " AND RunId IS NOT NULL AND TRIM(RunId) != ''"
                if require_run_identity else ""
            )
            rows = conn.execute(
                "SELECT Id FROM ReportSessions "
                "WHERE Status='in_progress' AND DateTimeCreated < ?"
                + run_identity_clause,
                (cutoff,),
            ).fetchall()
        elif since:
            run_identity_clause = (
                " AND RunId IS NOT NULL AND TRIM(RunId) != ''"
                if require_run_identity else ""
            )
            rows = conn.execute(
                "SELECT Id FROM ReportSessions "
                "WHERE Status='in_progress' AND DateTimeCreated >= ?"
                + run_identity_clause,
                (since,),
            ).fetchall()
        else:
            run_identity_clause = (
                " AND RunId IS NOT NULL AND TRIM(RunId) != ''"
                if require_run_identity else ""
            )
            rows = conn.execute(
                "SELECT Id FROM ReportSessions WHERE Status='in_progress'"
                + run_identity_clause,
            ).fetchall()
    return [r['Id'] for r in rows]


def db_find_stale_pending_sessions(
    *,
    db_path: Optional[str] = None,
    max_age_hours: float = 48.0,
    require_run_identity: bool = True,
) -> List[Tuple[int, str, str]]:
    """Return ``[(Id, Status, WriteMode), ...]`` for stale Phase 3 sessions.

    Used by :mod:`apps.cli.cleanup_stale_in_progress` to dispatch
    in_progress sessions to rollback and finalizing sessions to
    resume_commit.  Filters on ``Status IN ('in_progress', 'finalizing')``
    so audit-mode sessions still get rolled back through this cron path.

    *require_run_identity* — when True, skips legacy rows with empty
    RunId (matches the existing :func:`db_find_in_progress_sessions`
    contract).
    """
    cutoff_ts = time.time() - (max_age_hours * 3600)
    cutoff = datetime.utcfromtimestamp(cutoff_ts).strftime(
        "%Y-%m-%d %H:%M:%S",
    )
    run_identity_clause = (
        " AND RunId IS NOT NULL AND TRIM(RunId) != ''"
        if require_run_identity else ""
    )
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        try:
            rows = conn.execute(
                "SELECT Id, Status, COALESCE(WriteMode,'audit') AS WriteMode "
                "FROM ReportSessions "
                "WHERE Status IN ('in_progress','finalizing') "
                "AND DateTimeCreated < ?" + run_identity_clause,
                (cutoff,),
            ).fetchall()
        except sqlite3.OperationalError:
            # Legacy schema without WriteMode column — fall back to audit.
            rows = conn.execute(
                "SELECT Id, Status FROM ReportSessions "
                "WHERE Status IN ('in_progress','finalizing') "
                "AND DateTimeCreated < ?" + run_identity_clause,
                (cutoff,),
            ).fetchall()
            return [(int(r["Id"]), r["Status"], "audit") for r in rows]
    return [
        (int(r["Id"]), r["Status"], r["WriteMode"]) for r in rows
    ]


def db_count_in_progress_sessions_for_run(
    run_id: str,
    run_attempt: Optional[int] = None,
    *,
    db_path: Optional[str] = None,
) -> int:
    """Count ``in_progress`` sessions belonging to a (RunId, RunAttempt) pair.

    Used by the spider self-check: a single workflow run must own at most
    one in-progress session at any given time.  Detecting >0 here means a
    prior step already created one; the caller should refuse to start a
    fresh session and instead re-use / fail loudly.
    """
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        if run_attempt is None:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM ReportSessions "
                "WHERE Status='in_progress' AND RunId=?",
                (run_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM ReportSessions "
                "WHERE Status='in_progress' AND RunId=? AND RunAttempt=?",
                (run_id, int(run_attempt)),
            ).fetchone()
    return int((row or {'n': 0})['n'] or 0)


def db_find_in_progress_session_ids_for_run_csv(
    run_id: str,
    run_attempt: Optional[int],
    csv_filename: str,
    *,
    db_path: Optional[str] = None,
) -> List[int]:
    """Return ``in_progress`` SessionIds for the same (RunId, RunAttempt, CSVFilename).

    Used by the spider self-check to distinguish *legitimate* sibling
    sessions in the same workflow run (e.g. DailyIngestion's TodayTitle
    spider followed by an AdHoc spider — different CSV files) from a
    *true* duplicate (same CSV being ingested twice in the same run, which
    indicates a re-entry / retry-without-cleanup bug worth aborting on).
    Matching by CSV filename is exact and case-sensitive; callers should
    pass the basename only.
    """
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        if run_attempt is None:
            rows = conn.execute(
                "SELECT Id FROM ReportSessions "
                "WHERE Status='in_progress' AND RunId=? AND CSVFilename=?",
                (run_id, csv_filename),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT Id FROM ReportSessions WHERE Status='in_progress' "
                "AND RunId=? AND RunAttempt=? AND CSVFilename=?",
                (run_id, int(run_attempt), csv_filename),
            ).fetchall()
    return [int(r['Id']) for r in rows]


def db_find_sessions_by_run(
    run_id: str,
    run_attempt: Optional[int] = None,
    *,
    reports_db_path: Optional[str] = None,
    history_db_path: Optional[str] = None,
) -> List[int]:
    """Return every session id touched by a (RunId, RunAttempt) workflow run.

    Looks at ``ReportSessions`` first (the canonical owner record) and
    then unions in ``MovieHistoryAudit`` / ``TorrentHistoryAudit`` for any
    audit rows tagged with the run identity but whose owning
    ReportSessions row is missing (e.g. the row was deleted by a previous
    failed rollback attempt).  Returns ids sorted ascending.
    """
    found: set = set()
    with get_db(reports_db_path or REPORTS_DB_PATH) as conn:
        if run_attempt is None:
            rows = conn.execute(
                "SELECT Id FROM ReportSessions WHERE RunId=?", (run_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT Id FROM ReportSessions WHERE RunId=? AND RunAttempt=?",
                (run_id, int(run_attempt)),
            ).fetchall()
        for r in rows:
            found.add(int(r['Id']))

    with get_db(history_db_path or HISTORY_DB_PATH) as conn:
        for table in ('MovieHistoryAudit', 'TorrentHistoryAudit'):
            try:
                if run_attempt is None:
                    rows = conn.execute(
                        f"SELECT DISTINCT SessionId FROM {table} WHERE RunId=?",
                        (run_id,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        f"SELECT DISTINCT SessionId FROM {table} "
                        f"WHERE RunId=? AND RunAttempt=?",
                        (run_id, int(run_attempt)),
                    ).fetchall()
            except sqlite3.OperationalError:
                # Table doesn't exist (history db wasn't initialised yet)
                continue
            for r in rows:
                if r['SessionId'] is not None:
                    found.add(int(r['SessionId']))

    return sorted(found)


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


_ORPHAN_PRUNE_AGE_HOURS = 24


def _rollback_history(
    session_id: int,
    *,
    dry_run: bool,
    db_path: Optional[str] = None,
    run_started_at: Optional[str] = None,
) -> Dict[str, int]:
    """Reverse-apply MovieHistoryAudit + TorrentHistoryAudit for *session_id*.

    Logic:
      - Action='INSERT' → DELETE FROM <main> WHERE Id=TargetId
      - Action='UPDATE' → restore main row from OldRowJson WHERE Id=TargetId
        (only if the row's current SessionId matches; otherwise log drift)
      - Action='DELETE' → re-INSERT main row from OldRowJson
    Audit rows must be replayed in *reverse* order (highest Id first) so
    multi-step audits applied in the same run unwind correctly.

    Idempotency
    -----------
    Each successfully applied audit row is DELETEd from the audit table
    immediately, before processing the next row.  This means a partial
    failure (e.g. D1 transient error halfway through) can be retried
    safely: the rerun won't re-process audit rows that already succeeded.

    Orphan pruning
    --------------
    When *run_started_at* is supplied and an audit row is older than
    ``run_started_at - 24h`` AND the corresponding ``main_table`` row
    cannot be located (either deleted or never existed), the audit row
    is treated as a phantom from a long-ago run and pruned without
    counting as drift.  The :func:`db_rollback_session` caller is
    responsible for ensuring *run_started_at* is recent — otherwise the
    24h grace might prune legitimate audit rows.
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
        'orphan_pruned': 0,
    }
    orphan_cutoff: Optional[str] = None
    if run_started_at:
        try:
            from datetime import timedelta
            base = datetime.strptime(run_started_at, "%Y-%m-%d %H:%M:%S")
            orphan_cutoff = (
                base - timedelta(hours=_ORPHAN_PRUNE_AGE_HOURS)
            ).strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            orphan_cutoff = None

    def _delete_audit_row(conn, table: str, audit_id: int) -> None:
        conn.execute(f"DELETE FROM {table} WHERE Id=?", (audit_id,))

    with get_db(db_path or HISTORY_DB_PATH) as conn:
        for kind, audit_table, main_table in (
            ('torrent', 'TorrentHistoryAudit', 'TorrentHistory'),
            ('movie', 'MovieHistoryAudit', 'MovieHistory'),
        ):
            audit_rows = conn.execute(
                f"SELECT Id, TargetId, Action, OldRowJson, DateTimeCreated "
                f"FROM {audit_table} "
                f"WHERE SessionId=? ORDER BY Id DESC",
                (session_id,),
            ).fetchall()
            counts[audit_table] = len(audit_rows)
            if dry_run or not audit_rows:
                continue
            drifted = 0
            applied = 0
            for row in audit_rows:
                audit_id = int(row['Id'])
                action = row['Action']
                target_id = row['TargetId']
                old_json = row['OldRowJson']
                created = row['DateTimeCreated']
                try:
                    if action == 'INSERT':
                        # Only delete if the current row is still tagged
                        # with this session; otherwise another run later
                        # updated it and we must not erase their work.
                        cur = conn.execute(
                            f"DELETE FROM {main_table} "
                            f"WHERE Id=? AND SessionId=?",
                            (target_id, session_id),
                        )
                        if (cur.rowcount or 0) > 0:
                            counts[f'{main_table}.deleted'] += 1
                            _delete_audit_row(conn, audit_table, audit_id)
                            applied += 1
                        elif _is_orphan_audit(
                            conn, main_table, target_id,
                            created, orphan_cutoff,
                        ):
                            counts['orphan_pruned'] += 1
                            _delete_audit_row(conn, audit_table, audit_id)
                            logger.info(
                                "Orphan audit pruned: %s TargetId=%s "
                                "(action=INSERT; row missing and audit "
                                "older than %s)",
                                main_table, target_id, orphan_cutoff,
                            )
                        else:
                            counts['drift_skipped'] += 1
                            drifted += 1
                            logger.warning(
                                "Rollback drift: %s row Id=%s SessionId "
                                "mismatch or row already gone "
                                "(action=INSERT)",
                                main_table, target_id,
                            )
                    elif action == 'UPDATE':
                        if not old_json:
                            counts['drift_skipped'] += 1
                            drifted += 1
                            continue
                        old = json.loads(old_json)
                        # Build column list dynamically to support both
                        # MovieHistory and TorrentHistory.
                        cols = [c for c in old.keys() if c != 'Id']
                        set_clause = ', '.join(f'{c}=?' for c in cols)
                        params = (
                            [old[c] for c in cols]
                            + [target_id, session_id]
                        )
                        cur = conn.execute(
                            f"UPDATE {main_table} SET {set_clause} "
                            f"WHERE Id=? AND SessionId=?",
                            params,
                        )
                        if (cur.rowcount or 0) > 0:
                            counts[f'{main_table}.restored'] += 1
                            _delete_audit_row(conn, audit_table, audit_id)
                            applied += 1
                        elif _is_orphan_audit(
                            conn, main_table, target_id,
                            created, orphan_cutoff,
                        ):
                            counts['orphan_pruned'] += 1
                            _delete_audit_row(conn, audit_table, audit_id)
                            logger.info(
                                "Orphan audit pruned: %s TargetId=%s "
                                "(action=UPDATE; row missing and audit "
                                "older than %s)",
                                main_table, target_id, orphan_cutoff,
                            )
                        else:
                            # Concurrent run touched the row after us;
                            # can't safely overwrite their state — log drift.
                            counts['drift_skipped'] += 1
                            drifted += 1
                            logger.warning(
                                "Rollback drift: %s row Id=%s SessionId "
                                "mismatch (action=UPDATE) — manual review "
                                "needed",
                                main_table, target_id,
                            )
                    elif action == 'DELETE':
                        if not old_json:
                            counts['drift_skipped'] += 1
                            drifted += 1
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
                            _delete_audit_row(conn, audit_table, audit_id)
                            applied += 1
                        except sqlite3.IntegrityError as e:
                            # E.g. UNIQUE conflict — a concurrent run
                            # already reinserted something with the same
                            # business key. Skip + drift log.
                            counts['drift_skipped'] += 1
                            drifted += 1
                            logger.warning(
                                "Rollback drift: cannot re-insert %s row "
                                "(action=DELETE): %s", main_table, e,
                            )
                except Exception as e:
                    counts['drift_skipped'] += 1
                    drifted += 1
                    logger.error(
                        "Rollback step failed (table=%s action=%s id=%s): %s",
                        main_table, action, target_id, e,
                    )

            if drifted > 0:
                logger.warning(
                    "Kept %s unapplied %s row(s) for SessionId=%s because "
                    "rollback encountered drift or row-level errors",
                    drifted, audit_table, session_id,
                )
    return counts


def _is_orphan_audit(
    conn,
    main_table: str,
    target_id: Any,
    audit_created: Optional[str],
    orphan_cutoff: Optional[str],
) -> bool:
    """Return True when an audit row's main-table target is gone AND old.

    Used to decide whether a drift can be safely pruned (orphan from a
    long-departed run) vs. preserved for manual review (potentially fresh
    contention).
    """
    if not orphan_cutoff or not audit_created:
        return False
    if audit_created >= orphan_cutoff:
        return False
    try:
        row = conn.execute(
            f"SELECT 1 FROM {main_table} WHERE Id=?",
            (target_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is None


def db_rollback_session(
    session_id: int,
    *,
    dry_run: bool = False,
    scope: str = 'all',
    force: bool = False,
    history_db_path: Optional[str] = None,
    reports_db_path: Optional[str] = None,
    operations_db_path: Optional[str] = None,
    run_started_at: Optional[str] = None,
    failure_reason: Optional[str] = None,
    auto_resume_finalizing: bool = True,
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

    *run_started_at* (optional, ISO ``YYYY-MM-DD HH:MM:SS``): used by
    history rollback to decide which drift rows are stale-enough to
    prune as orphans (audit rows older than ``run_started_at - 24h``
    whose target has been deleted long ago).  Pass-through to
    :func:`_rollback_history`.

    *failure_reason* (optional): persisted to ``ReportSessions.
    FailureReason`` alongside ``Status='failed'`` so post-incident
    analysis can distinguish ``workflow_cancel`` / ``runtime_error`` /
    ``stale_timeout`` etc.  Defaults to no annotation when omitted.

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

    # Ingestion Perfect Rollback (Phase 2): pending-mode sessions
    # already in 'finalizing' must NOT be flipped to 'failed' before
    # the dispatcher runs — that would reroute the resume_commit
    # branch into rollback_pending and silently lose the in-flight
    # commit.  We only flip on legacy audit sessions and on pending
    # sessions that are still in 'in_progress' (true rollback path).
    pre_state = db_get_session_status(
        session_id, db_path=reports_db_path,
    )
    pre_write_mode = pre_state[0] if pre_state else 'audit'
    pre_status = pre_state[1] if pre_state else current_status
    skip_mark_failed = (
        pre_write_mode == 'pending'
        and pre_status == 'finalizing'
    )
    if (
        not dry_run
        and current_status != 'committed'
        and not skip_mark_failed
    ):
        # Best-effort flag — failure here shouldn't block the rollback.
        try:
            db_mark_session_failed(
                session_id,
                db_path=reports_db_path,
                reason=failure_reason,
            )
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
        # Ingestion Perfect Rollback (Phase 2): dispatch on
        # (WriteMode, Status).  Pending sessions never have audit
        # rows so the legacy replay would be a no-op; we want to
        # either DELETE pending (in_progress) or resume the commit
        # (finalizing).
        # NOTE: _rollback_reports above DELETEs the ReportSessions row,
        # so a fresh db_get_session_status() here would always return
        # None and silently fall back to 'audit'.  Reuse the snapshot we
        # captured before any deletion ran.
        write_mode = pre_write_mode
        sess_status = pre_status
        if write_mode == 'pending':
            if sess_status == 'finalizing':
                if not auto_resume_finalizing:
                    raise ValueError(
                        f"Refusing to roll back ReportSessions."
                        f"Id={session_id}: pending-mode session is "
                        "in Status='finalizing' and "
                        "auto_resume_finalizing=False. Pass "
                        "--auto-resume-finalizing to drive it to "
                        "committed instead, or --force-fail-finalizing "
                        "to give up."
                    )
                if dry_run:
                    result['history'] = {
                        'mode': 'resume_commit',
                        'dry_run': 1,
                    }
                else:
                    counts = db_resume_finalizing_session(
                        session_id,
                        history_db_path=history_db_path,
                        reports_db_path=reports_db_path,
                    )
                    counts['mode'] = 'resume_commit'
                    result['history'] = counts
            else:
                counts = _rollback_pending_in_progress(
                    session_id,
                    dry_run=dry_run,
                    db_path=history_db_path,
                    run_started_at=run_started_at,
                )
                counts['mode'] = 'rollback_pending'
                result['history'] = counts
        else:
            counts = _rollback_history(
                session_id,
                dry_run=dry_run,
                db_path=history_db_path,
                run_started_at=run_started_at,
            )
            counts['mode'] = 'audit_replay'
            result['history'] = counts
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
    """Save spider statistics for a session.

    P1: idempotent via ``ON CONFLICT(SessionId) DO UPDATE`` so a re-run
    (e.g. retry after timeout, manual operator re-execution) replaces
    the row instead of duplicating it. The legacy plain INSERT path
    silently created duplicate rows that then caused SQLite to diverge
    from D1 by exactly the number of retries.
    """
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
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(SessionId) DO UPDATE SET
                   Phase1Discovered=excluded.Phase1Discovered,
                   Phase1Processed=excluded.Phase1Processed,
                   Phase1Skipped=excluded.Phase1Skipped,
                   Phase1NoNew=excluded.Phase1NoNew,
                   Phase1Failed=excluded.Phase1Failed,
                   Phase2Discovered=excluded.Phase2Discovered,
                   Phase2Processed=excluded.Phase2Processed,
                   Phase2Skipped=excluded.Phase2Skipped,
                   Phase2NoNew=excluded.Phase2NoNew,
                   Phase2Failed=excluded.Phase2Failed,
                   TotalDiscovered=excluded.TotalDiscovered,
                   TotalProcessed=excluded.TotalProcessed,
                   TotalSkipped=excluded.TotalSkipped,
                   TotalNoNew=excluded.TotalNoNew,
                   TotalFailed=excluded.TotalFailed,
                   FailedMovies=excluded.FailedMovies""",
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
    """Save uploader statistics for a session (idempotent via ON CONFLICT)."""
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        cur = conn.execute(
            """INSERT INTO UploaderStats
               (SessionId, TotalTorrents, DuplicateCount, Attempted,
                SuccessfullyAdded, FailedCount, HackedSub, HackedNosub,
                SubtitleCount, NoSubtitleCount, SuccessRate)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(SessionId) DO UPDATE SET
                   TotalTorrents=excluded.TotalTorrents,
                   DuplicateCount=excluded.DuplicateCount,
                   Attempted=excluded.Attempted,
                   SuccessfullyAdded=excluded.SuccessfullyAdded,
                   FailedCount=excluded.FailedCount,
                   HackedSub=excluded.HackedSub,
                   HackedNosub=excluded.HackedNosub,
                   SubtitleCount=excluded.SubtitleCount,
                   NoSubtitleCount=excluded.NoSubtitleCount,
                   SuccessRate=excluded.SuccessRate""",
            (session_id,
             stats.get('total_torrents', 0), stats.get('duplicate_count', 0),
             stats.get('attempted', 0), stats.get('successfully_added', 0),
             stats.get('failed_count', 0), stats.get('hacked_sub', 0),
             stats.get('hacked_nosub', 0), stats.get('subtitle_count', 0),
             stats.get('no_subtitle_count', 0), stats.get('success_rate', 0.0)),
        )
        return cur.lastrowid


def db_save_pikpak_stats(session_id: int, stats: dict, db_path: Optional[str] = None) -> int:
    """Save PikPak bridge statistics for a session (idempotent via ON CONFLICT)."""
    with get_db(db_path or REPORTS_DB_PATH) as conn:
        cur = conn.execute(
            """INSERT INTO PikpakStats
               (SessionId, ThresholdDays, TotalTorrents,
                FilteredOld, SuccessfulCount, FailedCount,
                UploadedCount, DeleteFailedCount)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(SessionId) DO UPDATE SET
                   ThresholdDays=excluded.ThresholdDays,
                   TotalTorrents=excluded.TotalTorrents,
                   FilteredOld=excluded.FilteredOld,
                   SuccessfulCount=excluded.SuccessfulCount,
                   FailedCount=excluded.FailedCount,
                   UploadedCount=excluded.UploadedCount,
                   DeleteFailedCount=excluded.DeleteFailedCount""",
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


# ── P0-6 SQLite-canonical readers for observability tooling ─────────────
#
# In ``STORAGE_BACKEND=dual``, the regular ``db_get_*_stats`` helpers
# above resolve reads via D1 (see ``DualConnection.execute``). That is
# intentional for the application's hot path — it proves D1 can serve
# reads before cutover. It is **wrong** for the email notifier and any
# other "what actually happened this run" reporter, because D1 may be
# behind SQLite by N rows when a dual-write asymmetry occurred (the
# 2026-05 ``ReportSessions``/``SpiderStats`` -1 drift). Reading from
# D1 there would silently understate the pipeline's real output.
#
# These ``*_local`` variants always go through :func:`get_local_sqlite_db`
# so the email body / drift advisory reflect the canonical local state.


def db_get_spider_stats_local(
    session_id: int, db_path: Optional[str] = None,
) -> Optional[dict]:
    """SQLite-only counterpart to :func:`db_get_spider_stats`."""
    with get_local_sqlite_db(db_path or REPORTS_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM SpiderStats WHERE SessionId = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None


def db_get_uploader_stats_local(
    session_id: int, db_path: Optional[str] = None,
) -> Optional[dict]:
    """SQLite-only counterpart to :func:`db_get_uploader_stats`."""
    with get_local_sqlite_db(db_path or REPORTS_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM UploaderStats WHERE SessionId = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None


def db_get_pikpak_stats_local(
    session_id: int, db_path: Optional[str] = None,
) -> Optional[dict]:
    """SQLite-only counterpart to :func:`db_get_pikpak_stats`."""
    with get_local_sqlite_db(db_path or REPORTS_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM PikpakStats WHERE SessionId = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None


def db_get_latest_session_local(
    report_type: Optional[str] = None, db_path: Optional[str] = None,
) -> Optional[dict]:
    """SQLite-only counterpart to :func:`db_get_latest_session`."""
    with get_local_sqlite_db(db_path or REPORTS_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if report_type is not None:
            row = conn.execute(
                "SELECT * FROM ReportSessions WHERE ReportType = ? "
                "ORDER BY Id DESC LIMIT 1",
                (report_type,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM ReportSessions ORDER BY Id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None
