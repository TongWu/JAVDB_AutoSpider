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
import warnings
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from apps.api.parsers.common import (
    movie_href_lookup_values,
    javdb_absolute_url,
    absolutize_supporting_actors_json,
)
from javdb.infra.config import cfg
from javdb.infra.logging import get_logger
from javdb.storage.repos.history_repo import (
    load_history_joined as _load_history_joined,
    batch_update_movie_actors as _batch_update_movie_actors,
    _has_meaningful_actor_data,
)
logger = get_logger(__name__)

# MR-3 (multi-runtime): backend-agnostic exception tuples.
#
# Several best-effort code paths catch ``sqlite3.OperationalError`` to mean
# "table / column doesn't exist on a legacy schema, fall back" and
# ``sqlite3.IntegrityError`` to mean "UNIQUE conflict, a concurrent run
# already did this". Under ``STORAGE_BACKEND=d1`` the connection is a
# ``D1Connection`` whose ``execute`` raises ``D1PermanentError`` (HTTP 400
# application-level error from Cloudflare) for BOTH situations — it never
# raises the ``sqlite3.*`` types. Without broadening the catch, a missing
# table or a UNIQUE conflict on D1 would propagate out of those
# best-effort paths and abort an otherwise-recoverable rollback / verify.
#
# ``D1PermanentError`` (not the ``D1Error`` base) is intentionally the only
# D1 type added: ``D1TransientError`` means retries were already exhausted
# by ``_post_with_retry`` and must NOT be silently swallowed as "legacy
# schema" / "concurrent run". The import is guarded so a sqlite-only
# deployment without the d1_client deps still loads db.py.
try:  # pragma: no cover - import wiring
    from javdb.storage.d1_client import (
        D1PermanentError as _D1PermanentError,
    )
    _DB_OPERATIONAL_ERRORS: Tuple[type, ...] = (
        sqlite3.OperationalError, _D1PermanentError,
    )
    _DB_INTEGRITY_ERRORS: Tuple[type, ...] = (
        sqlite3.IntegrityError, _D1PermanentError,
    )
except Exception:  # noqa: BLE001 - d1_client optional in sqlite-only installs
    _DB_OPERATIONAL_ERRORS = (sqlite3.OperationalError,)
    _DB_INTEGRITY_ERRORS = (sqlite3.IntegrityError,)

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

SCHEMA_VERSION = 13

# ── Connection management ────────────────────────────────────────────────

_local = threading.local()

# ── Active session context ─────────────────────────────────────────────────
# Delegate to db_session.py so there is a single source of truth.
from javdb.storage.db.db_session import (
    _SESSION_ID_SENTINEL,
    _resolve_session_id,
    set_active_session_id,
    get_active_session_id,
    set_active_run_identity,
    get_active_run_identity,
    set_active_write_mode,
    get_active_write_mode,
    _resolve_write_mode,
)

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
# Why not an INTEGER snowflake?  Cloudflare D1's HTTP /query endpoint parses
# JSON parameters and serializes result rows through a JS layer whose Number
# type is IEEE-754 double.  Any integer with |x| > 2**53 - 1 silently loses
# precision in transit.  A 63-bit snowflake (today's IDs are ~7e18) overruns
# that ceiling by ~780×, so the local SQLite value and the D1-stored value
# diverge — breaking every downstream join keyed on SessionId (2026-05-12).
#
# Solution: store ``ReportSessions.Id`` as TEXT in a human-readable, sortable
# format that round-trips losslessly through JSON.
#
# Layout: ``YYYYMMDDTHHMMSS.ffffffZ-TTTT-SSSS`` (33 chars, fixed width),
# where ``TTTT`` is 4 lowercase hex digits of per-process random tag (16
# bits, ~256-concurrent-process birthday bound) and ``SSSS`` is 4 hex digits
# of in-process monotonic counter that resets every microsecond. Fixed
# width and zero-padded throughout, so lexicographic sort equals
# chronological sort.
#
# Across processes we still rely on the fact that two GitHub Actions
# runners almost never start a session in the same microsecond *and* also
# pick the same 16-bit tag.  The ``db_count_in_progress_sessions_for_run``
# self-check (Phase 5) remains the real defence against duplicate sessions
# per workflow run.
_SESSION_ID_LOCK = threading.Lock()
_SESSION_ID_LAST: str = ""
_SESSION_ID_LAST_US: int = -1
_SESSION_ID_COUNTER: int = 0
_SESSION_ID_PROCESS_TAG_BITS = 16
_SESSION_ID_PROCESS_TAG = secrets.randbits(_SESSION_ID_PROCESS_TAG_BITS)
_SESSION_ID_TAG_HEX = f"{_SESSION_ID_PROCESS_TAG:04x}"
# Regex matching the canonical session-id shape. Useful for tests and
# defensive validation in callers (e.g. rollback CLI that takes an id from
# operator input). Old-format decimal-string ids minted before the
# 2026-05-13 migration won't match — that's intentional.
_SESSION_ID_PATTERN = re.compile(
    r"^\d{8}T\d{6}\.\d{6}Z-[0-9a-f]{4}-[0-9a-f]{4}$"
)


def _generate_session_id() -> str:
    """Return a TEXT session id suitable for ``ReportSessions.Id``.

    Format: ``YYYYMMDDTHHMMSS.ffffffZ-TTTT-SSSS`` (UTC, microsecond
    precision, per-process random 16-bit tag, in-process monotonic 16-bit
    counter that resets every microsecond). Strictly increasing within a
    process under lexicographic ordering; round-trips losslessly through
    JSON to Cloudflare D1.
    """
    global _SESSION_ID_LAST, _SESSION_ID_LAST_US, _SESSION_ID_COUNTER
    with _SESSION_ID_LOCK:
        us = time.time_ns() // 1_000
        if us == _SESSION_ID_LAST_US:
            _SESSION_ID_COUNTER += 1
        else:
            _SESSION_ID_LAST_US = us
            _SESSION_ID_COUNTER = 0
        # Wrap-around guard: 16-bit counter can only represent 65 536 ids
        # within a single microsecond; bump to next µs if exhausted (an
        # absurd burst rate, but better to spend a µs of skew than mint a
        # duplicate id).
        if _SESSION_ID_COUNTER > 0xFFFF:
            _SESSION_ID_LAST_US += 1
            _SESSION_ID_COUNTER = 0
            us = _SESSION_ID_LAST_US
        dt = datetime.fromtimestamp(us / 1_000_000, tz=timezone.utc)
        ts = dt.strftime("%Y%m%dT%H%M%S") + f".{us % 1_000_000:06d}Z"
        candidate = f"{ts}-{_SESSION_ID_TAG_HEX}-{_SESSION_ID_COUNTER:04x}"
        if candidate <= _SESSION_ID_LAST:
            # Clock went backwards (NTP step, VM resume). Force monotonicity
            # by appending an extra counter increment beyond the last seen
            # id; tag stays stable, so we extend via the µs portion.
            _SESSION_ID_LAST_US += 1
            _SESSION_ID_COUNTER = 0
            us = _SESSION_ID_LAST_US
            dt = datetime.fromtimestamp(us / 1_000_000, tz=timezone.utc)
            ts = dt.strftime("%Y%m%dT%H%M%S") + f".{us % 1_000_000:06d}Z"
            candidate = f"{ts}-{_SESSION_ID_TAG_HEX}-0000"
        _SESSION_ID_LAST = candidate
        return candidate


# ── Integer snowflake for INTEGER PRIMARY KEY tables ─────────────────────
#
# MovieHistory.Id and TorrentHistory.Id are declared INTEGER, so they cannot
# use the TEXT session-id format above.  To keep dual-write consistent we
# need to supply explicit ids that are identical on both SQLite and D1.
#
# Constraints:
#   • Must stay within D1's JSON-safe range: |x| < 2**53
#   • Must be strictly increasing within a process so two concurrent
#     inserts cannot collide
#   • Must be unlikely to collide across processes (dual-mode multi-runner)
#
# Layout (52 bits, little-headroom below 2**53):
#   relative_ms (40 bits) — ms since 2026-01-01T00:00:00Z; overflows year 2060
#   process_tag  (6 bits) — secrets.randbits(6) per process (64 slots)
#   counter      (6 bits) — monotonic per-ms in-process counter (64 per ms)
#
# Collision probability for two simultaneous processes within the same ms:
#   P ≈ 1 / 64  (process_tag birthday) × 1/(64 slots) = ~0.02 % per ms burst.
# The MovieClaim DO (MR-4) guarantees at most one runner processes a given
# href at a time, so same-href concurrent inserts are already prevented;
# cross-href collisions would require two distinct hrefs to be new at the
# exact same ms with the same 6-bit tag and counter — acceptable risk.
_INT_ID_EPOCH_BASE_MS: int = 1_735_689_600_000  # 2026-01-01T00:00:00Z
_INT_ID_PROCESS_TAG: int = secrets.randbits(6)
_INT_ID_LOCK = threading.Lock()
_INT_ID_LAST_MS: int = -1
_INT_ID_COUNTER: int = 0


def _generate_integer_id() -> int:
    """Return a 52-bit integer PK for INTEGER PRIMARY KEY tables.

    Safe for Cloudflare D1 JSON transport (all values < 2**53).
    Strictly increasing within a process; monotonicity forced on clock skew.
    """
    global _INT_ID_LAST_MS, _INT_ID_COUNTER
    with _INT_ID_LOCK:
        ms = int(time.time() * 1000) - _INT_ID_EPOCH_BASE_MS
        if ms > _INT_ID_LAST_MS:
            _INT_ID_LAST_MS = ms
            _INT_ID_COUNTER = 0
        else:
            # Same ms or clock went backwards — stay monotonic on _INT_ID_LAST_MS.
            _INT_ID_COUNTER += 1
            if _INT_ID_COUNTER >= 64:
                _INT_ID_LAST_MS += 1
                _INT_ID_COUNTER = 0
        return (_INT_ID_LAST_MS << 12) | (_INT_ID_PROCESS_TAG << 6) | _INT_ID_COUNTER


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
        from javdb.storage.d1_client import make_d1_connection
        conn = make_d1_connection(_logical_name_for(db_path))
    elif backend == 'dual':
        from javdb.storage.d1_client import make_d1_connection
        from javdb.storage.dual_connection import DualConnection
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
    SessionId TEXT NOT NULL,
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
    SessionId TEXT NOT NULL,
    DateTimeCreated TEXT NOT NULL,
    RunId TEXT,
    RunAttempt INTEGER
);
CREATE INDEX IF NOT EXISTS idx_th_audit_session ON TorrentHistoryAudit(SessionId, Id);
CREATE INDEX IF NOT EXISTS idx_th_audit_run ON TorrentHistoryAudit(RunId, RunAttempt);

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
    Status          TEXT NOT NULL DEFAULT 'sent',
    ErrorMessage    TEXT,
    AttachmentNames TEXT,
    SentAt          TEXT NOT NULL,
    ResentAt        TEXT,
    CreatedBy       TEXT DEFAULT 'pipeline'
);
CREATE INDEX IF NOT EXISTS idx_email_history_session ON EmailNotificationHistory(SessionId);
CREATE INDEX IF NOT EXISTS idx_email_history_status ON EmailNotificationHistory(Status);
"""

# Combined DDL for single-DB mode (backward compat, csv_to_sqlite, testing)
_TABLES_SQL = _HISTORY_DDL + _REPORTS_DDL + _OPERATIONS_DDL


# ── Category ↔ Indicator mapping (delegated to contracts) ────────────────

from javdb.spider.contracts import category_to_indicators, indicators_to_category  # noqa: E402


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
        ('MovieHistory', 'SessionId', 'TEXT'),
        ('TorrentHistory', 'SessionId', 'TEXT'),
        ('MovieHistoryAudit', 'RunId', 'TEXT'),
        ('MovieHistoryAudit', 'RunAttempt', 'INTEGER'),
        ('TorrentHistoryAudit', 'RunId', 'TEXT'),
        ('TorrentHistoryAudit', 'RunAttempt', 'INTEGER'),
        ('PikpakHistory', 'SessionId', 'TEXT'),
        ('DedupRecords', 'SessionId', 'TEXT'),
        ('InventoryAlignNoExactMatch', 'SessionId', 'TEXT'),
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
            SessionId TEXT NOT NULL,
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
            SessionId TEXT NOT NULL,
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

        existing = conn.execute("SELECT Version FROM SchemaVersion LIMIT 1").fetchone()
        if existing is None:
            conn.execute("INSERT INTO SchemaVersion (Version) VALUES (?)", (SCHEMA_VERSION,))
        elif current < SCHEMA_VERSION:
            conn.execute("UPDATE SchemaVersion SET Version = ?", (SCHEMA_VERSION,))

    logger.debug(f"Legacy single-DB initialised at {db_path} (schema v{SCHEMA_VERSION})")


# ── MovieHistory + TorrentHistory helpers ────────────────────────────────

def db_load_history(db_path: Optional[str] = None, phase: Optional[int] = None) -> Dict[str, dict]:
    """Load history from MovieHistory + TorrentHistory into a dict keyed by Href."""
    from javdb.storage.db.db_history_read import (
        db_load_history as _f,
    )
    return _f(db_path=db_path or HISTORY_DB_PATH, phase=phase)


# ── Audit helpers (X3 rollback) ──────────────────────────────────────────
#
# Every mutation of ``MovieHistory`` / ``TorrentHistory`` that originates
# from a tagged session (``session_id is not None``) records a companion
# audit row in ``MovieHistoryAudit`` / ``TorrentHistoryAudit`` describing
# what changed. ``apps.cli.db.rollback`` later replays the audit log in
# reverse order to undo the mutations of a failed run while leaving the
# committed state of any other concurrent run untouched.
#
# The audit row is sent in the same backend batch as the matching
# mutation whenever a session_id is active. SQLite executes the batch
# inside the surrounding transaction; D1 treats each backend batch as
# atomic, so the mutation and audit row succeed or fail together.
#
# Phase 4 deprecation (2026-05-13)
# --------------------------------
# Setting ``JAVDB_AUDIT_WRITES_DISABLED=1`` (or ``true``) turns every
# audit-row INSERT here into a no-op so the audit tables become append-
# never — only the existing rows remain queryable for forensic /
# historical-session rollback.  The default is ``0`` (writes still
# enabled) because the legacy ``WriteMode='audit'`` rollback fallback
# still depends on audit rows being recorded for any sessions running
# under that mode.  Operators flip the env to ``1`` once they're
# confident no ingestion workflow is still pinning ``audit``.


def _audit_writes_disabled() -> bool:
    """Return True when Phase 4 has pinned ``JAVDB_AUDIT_WRITES_DISABLED``.

    Accepts ``1``, ``true``, ``yes`` (case-insensitive).  Used to gate
    the ``_movie_*`` / ``_torrent_*`` audit-statement helpers so flipping
    the env turns audit appends into no-ops while preserving the rest of
    the historic write path (live ``MovieHistory`` / ``TorrentHistory``
    rows still get written for any session still running under
    ``WriteMode='audit'``).
    """
    raw = os.environ.get("JAVDB_AUDIT_WRITES_DISABLED", "")
    return raw.strip().lower() in ("1", "true", "yes", "on")

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
    session_id: Optional[str],
    old_row: Any = None,
    when: Optional[str] = None,
) -> Optional[Tuple[str, Tuple[Any, ...]]]:
    if session_id is None:
        return None
    if _audit_writes_disabled():
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
    session_id: Optional[str],
    when: Optional[str] = None,
) -> Optional[Tuple[str, Tuple[Any, ...]]]:
    if session_id is None:
        return None
    if _audit_writes_disabled():
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
    session_id: Optional[str],
    old_row: Any = None,
    when: Optional[str] = None,
) -> Optional[Tuple[str, Tuple[Any, ...]]]:
    if session_id is None:
        return None
    if _audit_writes_disabled():
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
    session_id: Optional[str],
    when: Optional[str] = None,
) -> Optional[Tuple[str, Tuple[Any, ...]]]:
    if session_id is None:
        return None
    if _audit_writes_disabled():
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
    session_id: Optional[str],
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
    session_id: Optional[str],
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

    .. deprecated:: Phase 4 (2026-05-13)
        Direct callers must switch to
        :func:`javdb.storage.history_manager.save_parsed_movie_to_history`
        (or :func:`db_stage_history_write` when the active session is in
        ``WriteMode='pending'``).  Calling this function emits
        :class:`DeprecationWarning` so the remaining audit-mode callsites
        surface in CI logs; the implementation is **still** functional
        for the audit fallback path and for the migration tool, but
        every ingestion workflow now defaults to pending mode where this
        upsert is no longer reached.  See
        :doc:`docs/D1_ROLLBACK.md` (Appendix A) for the sunset timeline.

    Actor fields: when ``None``, existing MovieHistory values are left unchanged
    on update; use ``''`` to clear. On insert, ``None`` stays NULL.

    *session_id*: the active ``ReportSessions.Id`` for X3 rollback bookkeeping.
    Defaults to :func:`get_active_session_id`. When set, the row's ``SessionId``
    column is populated and a companion ``MovieHistoryAudit`` /
    ``TorrentHistoryAudit`` row is written for each INSERT/UPDATE — unless
    ``JAVDB_AUDIT_WRITES_DISABLED=1`` is set (Phase 4 kill switch), in which
    case the audit row is silently skipped.
    """
    warnings.warn(
        "db_upsert_history is deprecated (Phase 4, 2026-05). "
        "Route ingestion writes through "
        "javdb_platform.history_manager.save_parsed_movie_to_history "
        "(pending-mode auto-stage) or db_stage_history_write. "
        "This entry-point will keep working through the audit fallback "
        "sunset window — see docs/D1_ROLLBACK.md Appendix A.",
        DeprecationWarning,
        stacklevel=2,
    )
    if magnet_links is None:
        magnet_links = {}
    if size_links is None:
        size_links = {}
    if file_count_links is None:
        file_count_links = {}
    if resolution_links is None:
        resolution_links = {}

    sid = _resolve_session_id(session_id)

    with get_db(db_path or HISTORY_DB_PATH) as conn:
        _upsert_one_history_on_conn(
            conn,
            href=href,
            video_code=video_code,
            magnet_links=magnet_links,
            size_links=size_links,
            file_count_links=file_count_links,
            resolution_links=resolution_links,
            actor_name=actor_name,
            actor_gender=actor_gender,
            actor_link=actor_link,
            supporting_actors=supporting_actors,
            session_id=sid,
        )


def _upsert_one_history_on_conn(
    conn,
    *,
    href: str,
    video_code: str,
    magnet_links: Dict[str, str],
    size_links: Dict[str, str],
    file_count_links: Dict[str, int],
    resolution_links: Dict[str, Optional[int]],
    actor_name: Optional[str],
    actor_gender: Optional[str],
    actor_link: Optional[str],
    supporting_actors: Optional[str],
    session_id: Optional[str],
) -> None:
    """Per-row upsert body, factored out so a batch caller can reuse one
    connection across many rows without re-opening / re-committing per row.

    ``session_id`` here is the already-resolved value (not the sentinel) —
    callers must run it through :func:`_resolve_session_id` first so the
    batch wrapper does not pay that resolution cost N times.
    """
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
    sid = session_id
    _TORRENT_CATS = ('hacked_subtitle', 'hacked_no_subtitle', 'subtitle', 'no_subtitle')

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
        movie_id = _generate_integer_id()
        insert_movie = (
            """INSERT INTO MovieHistory
               (Id, VideoCode, Href, DateTimeCreated, DateTimeUpdated, DateTimeVisited,
                ActorName, ActorGender, ActorLink, SupportingActors, SessionId)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (movie_id, video_code, normalized_href, now, now, now,
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
        _execute_backend_batch(conn, statements)
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
            torrent_id = _generate_integer_id()
            insert_torrent = (
                """INSERT INTO TorrentHistory
                   (Id, MovieHistoryId, MagnetUri, SubtitleIndicator, CensorIndicator,
                    ResolutionType, Size, FileCount, DateTimeCreated,
                    DateTimeUpdated, SessionId)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (torrent_id, movie_id, magnet, sub_ind, cen_ind, res, size, fc,
                 now, now, sid),
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


def db_upsert_history_batch(
    rows: List[Dict[str, Any]],
    *,
    db_path: Optional[str] = None,
    session_id: Any = _SESSION_ID_SENTINEL,
) -> None:
    """Upsert ``MovieHistory`` / ``TorrentHistory`` for a batch of movies.

    Each ``rows`` entry is a dict with the same keys
    :func:`db_upsert_history` takes (``href``, ``video_code``,
    ``magnet_links``, ``size_links``, ``file_count_links``,
    ``resolution_links``, ``actor_name``, ``actor_gender``, ``actor_link``,
    ``supporting_actors``). All rows write through a single connection so
    that:

    * Under ``STORAGE_BACKEND=dual``, the whole batch lands in one
      :class:`~javdb.storage.dual_connection.DualConnection` transaction —
      drift accounting consolidates to a single commit decision, and
      ``STRICT_DUAL_WRITE=1`` callers see a single all-or-nothing failure
      surface instead of N independent ones.
    * SQLite WAL fsync amortises across the batch instead of per row.
    * Statements that ``_execute_backend_batch`` already groups (movie +
      audit, torrent + audit) keep their per-row D1 ``batch_execute``
      grouping; the larger win — true cross-row ``executemany`` on the
      INSERT-only fast path — is deliberately out of scope here, see the
      "Future work" comment below.

    ``session_id`` is resolved once for the whole batch (default: active
    session). Pass an explicit id to override.

    Future work: an INSERT-only fast-path that bulk-fetches existing rows
    in one SELECT and emits per-table ``executemany`` calls would cut D1
    round-trips from ~5 per movie to ~5 per batch on the alignment
    workload (where most rows are new). It is non-trivial because the
    audit + indicator + torrent-dedup logic needs separate fast variants;
    the per-row path through this wrapper is the safe baseline.
    """
    if not rows:
        return
    sid = _resolve_session_id(session_id)
    with get_db(db_path or HISTORY_DB_PATH) as conn:
        for row in rows:
            magnet_links = row.get('magnet_links') or {}
            size_links = row.get('size_links') or {}
            file_count_links = row.get('file_count_links') or {}
            resolution_links = row.get('resolution_links') or {}
            _upsert_one_history_on_conn(
                conn,
                href=row['href'],
                video_code=row['video_code'],
                magnet_links=magnet_links,
                size_links=size_links,
                file_count_links=file_count_links,
                resolution_links=resolution_links,
                actor_name=row.get('actor_name'),
                actor_gender=row.get('actor_gender'),
                actor_link=row.get('actor_link'),
                supporting_actors=row.get('supporting_actors'),
                session_id=sid,
            )


def _delete_torrents_with_audit(
    conn,
    movie_id: int,
    *,
    sub_ind: int,
    cen_ind: int,
    session_id: Optional[str],
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
    session_id: Optional[str] = None,
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
                sid,
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
                sid,
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
    from javdb.storage.db.db_history_read import (
        db_check_torrent_in_history as _f,
    )
    return _f(href, torrent_type, db_path=db_path)


def db_get_all_history_records(db_path: Optional[str] = None) -> List[dict]:
    """Return all MovieHistory records as dicts (for migration verification)."""
    from javdb.storage.db.db_history_read import (
        db_get_all_history_records as _f,
    )
    return _f(db_path=db_path)


# ── RcloneInventory helpers ──────────────────────────────────────────────

def db_replace_rclone_inventory(
    entries: List[dict],
    db_path: Optional[str] = None,
    session_id: Any = _SESSION_ID_SENTINEL,
) -> int:
    """Replace the entire RcloneInventory table (full scan refresh)."""
    from javdb.storage.db.db_operations import (
        db_replace_rclone_inventory as _f,
    )
    return _f(entries, db_path=db_path or OPERATIONS_DB_PATH, session_id=session_id)


def db_open_rclone_staging(
    session_id: Any = _SESSION_ID_SENTINEL,
    db_path: Optional[str] = None,
) -> Optional[str]:
    """Initialise this session's RcloneInventory staging table."""
    from javdb.storage.db.db_operations import (
        db_open_rclone_staging as _f,
    )
    return _f(session_id=session_id, db_path=db_path or OPERATIONS_DB_PATH)


def db_append_rclone_staging(
    entries: List[dict],
    session_id: Any = _SESSION_ID_SENTINEL,
    db_path: Optional[str] = None,
) -> int:
    """Append rows to this session's RcloneInventory staging table."""
    from javdb.storage.db.db_operations import (
        db_append_rclone_staging as _f,
    )
    return _f(entries, session_id=session_id, db_path=db_path or OPERATIONS_DB_PATH)


def db_swap_rclone_inventory(
    session_id: Any = _SESSION_ID_SENTINEL,
    db_path: Optional[str] = None,
) -> int:
    """Atomically swap this session's staging into the live RcloneInventory."""
    from javdb.storage.db.db_operations import (
        db_swap_rclone_inventory as _f,
    )
    return _f(session_id=session_id, db_path=db_path)


def db_merge_rclone_inventory_from_stage(
    session_id: Any = _SESSION_ID_SENTINEL,
    years: Optional[Iterable[str]] = None,
    db_path: Optional[str] = None,
) -> int:
    """Merge this session's staging rows into selected RcloneInventory years."""
    from javdb.storage.db.db_operations import (
        db_merge_rclone_inventory_from_stage as _f,
    )
    return _f(session_id=session_id, years=years, db_path=db_path or OPERATIONS_DB_PATH)


def db_drop_rclone_staging(
    session_id: str,
    db_path: Optional[str] = None,
) -> None:
    """DROP TABLE IF EXISTS RcloneInventoryStaging_<session_id> (idempotent)."""
    from javdb.storage.db.db_operations import (
        db_drop_rclone_staging as _f,
    )
    _f(session_id, db_path=db_path or OPERATIONS_DB_PATH)


def db_clear_rclone_inventory(db_path: Optional[str] = None) -> None:
    """Delete all rows from RcloneInventory."""
    from javdb.storage.db.db_operations import (
        db_clear_rclone_inventory as _f,
    )
    _f(db_path=db_path)


def db_append_rclone_inventory(entries: List[dict], db_path: Optional[str] = None) -> int:
    """Append rows to RcloneInventory using executemany for speed."""
    from javdb.storage.db.db_operations import (
        db_append_rclone_inventory as _f,
    )
    return _f(entries, db_path=db_path)


def db_load_rclone_inventory(db_path: Optional[str] = None) -> Dict[str, list]:
    """Load inventory grouped by VideoCode."""
    from javdb.storage.db.db_operations import (
        db_load_rclone_inventory as _f,
    )
    return _f(db_path=db_path or OPERATIONS_DB_PATH)


def db_delete_rclone_inventory_paths(
    paths: Iterable[str],
    db_path: Optional[str] = None,
) -> int:
    """Bulk delete RcloneInventory rows by FolderPath."""
    from javdb.storage.db.db_operations import (
        db_delete_rclone_inventory_paths as _f,
    )
    return _f(paths, db_path=db_path or OPERATIONS_DB_PATH)


# ── DedupRecords helpers ─────────────────────────────────────────────────

def db_load_dedup_records(db_path: Optional[str] = None) -> List[dict]:
    """Load all dedup records."""
    from javdb.storage.db.db_operations import (
        db_load_dedup_records as _f,
    )
    return _f(db_path=db_path or OPERATIONS_DB_PATH)


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


def _session_id_to_identifier_suffix(session_id: Any) -> str:
    """Sanitize a session id for safe use as a SQL identifier suffix.

    The post-2026-05-13 TEXT snowflake contains ``.`` and ``-`` (and was
    historically a pure decimal string), neither of which is valid in a
    SQL identifier without quoting. Map every non-``[A-Za-z0-9_]`` byte
    to ``_`` so derived table names like ``RcloneInventoryStaging_…``
    stay unquoted-safe.
    """
    return re.sub(r'[^0-9A-Za-z_]', '_', str(session_id))


def _dedup_rollback_table(session_id: str) -> str:
    return f"DedupRecordsRollback_{_session_id_to_identifier_suffix(session_id)}"


def _dedup_rollback_table_exists(conn, session_id: str) -> bool:
    table = _dedup_rollback_table(session_id)
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _ensure_dedup_rollback_table(conn, session_id: str) -> str:
    table = _dedup_rollback_table(session_id)
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS {table} (
            DedupRecordId INTEGER PRIMARY KEY,
            OldRowJson TEXT NOT NULL
        )"""
    )
    return table


def _snapshot_dedup_rows_for_rollback(conn, session_id: Optional[str], rows) -> None:
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


def _same_session_id(value, session_id: str) -> bool:
    if value is None:
        return False
    return str(value) == str(session_id)


def _restore_dedup_records_from_rollback(conn, session_id: str) -> Tuple[int, int]:
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
    """Append a single dedup record. Returns the new row id, or -1 if duplicate."""
    from javdb.storage.db.db_operations import (
        db_append_dedup_record as _f,
    )
    return _f(record, db_path=db_path or OPERATIONS_DB_PATH, session_id=session_id)


def db_mark_records_deleted(
    path_datetime_pairs: List[Tuple[str, str]],
    db_path: Optional[str] = None,
    session_id: Any = _SESSION_ID_SENTINEL,
) -> int:
    """Mark specific dedup records as deleted by gdrive path."""
    from javdb.storage.db.db_operations import (
        db_mark_records_deleted as _f,
    )
    return _f(path_datetime_pairs, db_path=db_path or OPERATIONS_DB_PATH, session_id=session_id)


def db_mark_orphan_records(
    paths: Iterable[str],
    reason_suffix: str,
    when: str,
    db_path: Optional[str] = None,
    session_id: Any = _SESSION_ID_SENTINEL,
) -> int:
    """Mark dedup pending rows as deleted with custom reason suffix appended."""
    from javdb.storage.db.db_operations import (
        db_mark_orphan_records as _f,
    )
    return _f(paths, reason_suffix, when, db_path=db_path or OPERATIONS_DB_PATH, session_id=session_id)


def db_cleanup_deleted_records(
    older_than_days: int = 30,
    db_path: Optional[str] = None,
) -> int:
    """Remove dedup records that were deleted more than *older_than_days* ago."""
    from javdb.storage.db.db_operations import (
        db_cleanup_deleted_records as _f,
    )
    return _f(older_than_days, db_path=db_path or OPERATIONS_DB_PATH)


def db_save_dedup_records(rows: List[dict], db_path: Optional[str] = None) -> None:
    """Overwrite all dedup records (deprecated)."""
    from javdb.storage.db.db_operations import (
        db_save_dedup_records as _f,
    )
    return _f(rows, db_path=db_path)


# ── PikpakHistory helpers ────────────────────────────────────────────────

def db_append_pikpak_history(
    record: dict,
    db_path: Optional[str] = None,
    session_id: Any = _SESSION_ID_SENTINEL,
) -> int:
    """Append a PikPak transfer record."""
    from javdb.storage.db.db_operations import (
        db_append_pikpak_history as _f,
    )
    return _f(record, db_path=db_path or OPERATIONS_DB_PATH, session_id=session_id)


# ── InventoryAlignNoExactMatch helpers ───────────────────────────────────

def db_upsert_align_no_exact_match(
    video_code: str,
    reason: str = 'exact_video_code_not_found',
    db_path: Optional[str] = None,
    session_id: Any = _SESSION_ID_SENTINEL,
) -> None:
    """Record a video code that had no exact match on JavDB search."""
    from javdb.storage.db.db_operations import (
        db_upsert_align_no_exact_match as _f,
    )
    return _f(video_code, reason=reason, db_path=db_path, session_id=session_id)


def db_load_align_no_exact_match_codes(db_path: Optional[str] = None) -> set:
    """Return the set of normalised video codes previously marked as no-exact-match."""
    from javdb.storage.db.db_operations import (
        db_load_align_no_exact_match_codes as _f,
    )
    return _f(db_path=db_path)


def db_delete_align_no_exact_match(
    video_code: str,
    db_path: Optional[str] = None,
) -> None:
    """Remove a video code from the no-exact-match table."""
    from javdb.storage.db.db_operations import (
        db_delete_align_no_exact_match as _f,
    )
    return _f(video_code, db_path=db_path)



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


def db_get_session_status(
    session_id: str,
    *,
    db_path: Optional[str] = None,
) -> Optional[Tuple[str, str]]:
    """Return ``(WriteMode, Status)`` for *session_id*, or ``None`` if absent."""
    from javdb.storage.db.db_reports import (
        db_get_session_status as _f,
    )
    return _f(session_id, db_path=db_path or REPORTS_DB_PATH)


def db_begin_finalize_session(
    session_id: str,
    *,
    db_path: Optional[str] = None,
) -> int:
    """Flip ``Status`` from ``in_progress`` to ``finalizing``."""
    from javdb.storage.db.db_reports import (
        db_begin_finalize_session as _f,
    )
    return _f(session_id, db_path=db_path)


def db_finish_commit_session(
    session_id: str,
    *,
    db_path: Optional[str] = None,
) -> int:
    """Flip ``Status`` from ``finalizing`` to ``committed``."""
    from javdb.storage.db.db_reports import (
        db_finish_commit_session as _f,
    )
    return _f(session_id, db_path=db_path)

# ── Ingestion Perfect Rollback: pending write path (Phase 2) ─────────────
#
# These four functions form the new ingestion write surface.  Phase 3
# default-on: every ingestion workflow (DailyIngestion, AdHocIngestion,
# TestIngestion) ships under ``WriteMode='pending'`` unless the
# ``write_mode_override`` workflow input or the
# ``JAVDB_HISTORY_WRITE_MODE`` env var explicitly selects ``audit``.
# (ADR-006 PR-D retired the automatic ``pending_mode_disabled_until``
# fallback to audit; critical alerts now pause the pipeline via
# ``pipeline_paused_until`` instead.) ``db_upsert_history`` is therefore
# only reached via legacy / fallback routes today; ADR-005 PR-5 will
# remove it entirely after the ADR-006 bake completes.
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
    session_id: str,
    kind: str,
    payload: Dict[str, Any],
    *,
    db_path: Optional[str] = None,
) -> str:
    """Append a row to PendingMovie/TorrentHistoryWrites.

    *kind* must be ``'movie'`` or ``'torrent'``; *payload* carries the
    row columns expected by the matching table (the helper extracts
    them and supplies defaults so callers can be dict-shape lenient).

    Returns the new ``Seq`` value.  The active ``(RunId, RunAttempt)``
    context (set via :func:`set_active_run_identity`) is mirrored into
    the pending row so the rollback CLI can join across the run identity
    when the ReportSessions row was reaped early.

    ``Seq`` is generated via :func:`_generate_session_id` (the same TEXT
    snowflake used by ``ReportSessions.Id``) and inserted explicitly.
    Both Pending tables are listed in ``APPLICATION_GENERATED_ID_TABLES``
    so the dual-backend guard catches any case where SQLite and D1 see
    different ``Seq`` for the same logical row — a drift here would
    silently leave residual pending rows after commit because
    ``_commit_one_movie`` marks ``applied`` by ``Seq IN (...)``.
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
    # ``_generate_session_id`` (e.g. a forgotten AUTOINCREMENT that
    # would emit a small int and diverge from D1 under STORAGE_BACKEND=
    # dual). The canonical generator returns a fixed-shape TEXT id, so
    # any value that doesn't match the regex came from somewhere else.
    if not _SESSION_ID_PATTERN.match(seq):
        raise ValueError(
            f"db_stage_history_write: refusing to INSERT with Seq={seq!r} "
            f"(expected a TEXT snowflake from _generate_session_id; a "
            f"malformed value here means a caller bypassed the snowflake "
            f"path and would diverge from D1 under STORAGE_BACKEND=dual)."
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
                    session_id,
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
                    session_id,
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


def _href_lookup_variants(href: str) -> List[str]:
    """Return the up-to-3 Href values to look up against ``MovieHistory``.

    Mirrors the per-href lookup that :func:`_commit_one_movie` performs
    inline; extracted so the bulk session-level commit path uses the
    same variant set. Order is preserved (path-relative, absolute,
    original) and duplicates are dropped while keeping first occurrence.
    """
    base_url = cfg("BASE_URL", "https://javdb.com")
    path_href, abs_href = movie_href_lookup_values(href, base_url)
    seen: set = set()
    out: List[str] = []
    for h in (path_href, abs_href, href):
        if h and h not in seen:
            seen.add(h)
            out.append(h)
    return out


def _compute_indicators(
    torrents: Iterable[Tuple[int, int, Optional[int]]],
) -> Tuple[int, int]:
    """Return ``(PerfectMatchIndicator, HiResIndicator)`` for a torrent set.

    *torrents* is an iterable of ``(SubtitleIndicator, CensorIndicator,
    ResolutionType)`` tuples representing the projected post-write state
    for a single ``MovieHistoryId``. Pure function used by the bulk
    commit path to replace the per-href JOIN SELECT + ResolutionType
    SELECT pair (see ``_commit_one_movie`` indicator-recompute block).
    """
    keys = set()
    hires = 0
    for sub, cen, resolution in torrents:
        keys.add((int(sub), int(cen)))
        if (resolution or 0) >= 2560:
            hires = 1
    perfect = 1 if ((1, 0) in keys and (1, 1) in keys) else 0
    return perfect, hires


def _merge_movie_overlay_rows(rows: Iterable[Any]) -> Dict[str, dict]:
    """Merge pending-movie rows (Seq-ascending order) into a sparse overlay."""
    from javdb.storage.db.db_history_read import (
        _merge_movie_overlay_rows as _f,
    )
    return _f(rows)


def _pending_movie_overlay(
    conn,
    session_id: str,
    *,
    href: Optional[str] = None,
    include_states: Tuple[str, ...] = ("pending",),
) -> Dict[str, dict]:
    """Return ``{href: merged_pending_movie_row}`` for *session_id*."""
    from javdb.storage.db.db_history_read import (
        _pending_movie_overlay_impl,
    )
    return _pending_movie_overlay_impl(
        conn, session_id, href=href, include_states=include_states,
    )


def _merge_torrent_overlay_rows(
    rows: Iterable[Any],
) -> Dict[Tuple[str, int, int], dict]:
    """Merge pending-torrent rows (Seq-ascending) into a sparse overlay."""
    from javdb.storage.db.db_history_read import (
        _merge_torrent_overlay_rows as _f,
    )
    return _f(rows)


def _pending_torrent_overlay(
    conn,
    session_id: str,
    *,
    href: Optional[str] = None,
    include_states: Tuple[str, ...] = ("pending",),
) -> Dict[Tuple[str, int, int], dict]:
    """Return ``{(href, sub, cen): merged_pending_torrent_row}`` for *session_id*."""
    from javdb.storage.db.db_history_read import (
        _pending_torrent_overlay_impl,
    )
    return _pending_torrent_overlay_impl(
        conn, session_id, href=href, include_states=include_states,
    )


def db_load_history_snapshot(
    session_id: Optional[str] = None,
    *,
    db_path: Optional[str] = None,
) -> Dict[str, dict]:
    """Return committed-live history with the *session_id* pending overlay."""
    from javdb.storage.db.db_history_read import (
        db_load_history_snapshot as _f,
    )
    return _f(session_id, db_path=db_path)


def _commit_one_movie(
    conn,
    session_id: str,
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
        movie_id = _generate_integer_id()
        conn.execute(
            """INSERT INTO MovieHistory
               (Id, VideoCode, Href, DateTimeCreated, DateTimeUpdated,
                DateTimeVisited, ActorName, ActorGender, ActorLink,
                SupportingActors, SessionId)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                movie_id,
                video_code,
                normalized_href,
                when,
                when,
                (movie_payload or {}).get("DateTimeVisited") or when,
                (movie_payload or {}).get("ActorName"),
                (movie_payload or {}).get("ActorGender"),
                (movie_payload or {}).get("ActorLink"),
                (movie_payload or {}).get("SupportingActors"),
                session_id,
            ),
        )
        counts["movies_upserted"] += 1
    else:
        movie_id = int(existing["Id"])
        update_fields = ["DateTimeUpdated=?", "SessionId=?"]
        params: list = [when, session_id]
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
        seqs = r.get("_merged_seqs") or [r["Seq"]]
        consumed_movie_seqs.extend(seqs)

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
                   (Id, MovieHistoryId, MagnetUri, SubtitleIndicator,
                    CensorIndicator, ResolutionType, Size, FileCount,
                    DateTimeCreated, DateTimeUpdated, SessionId)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    _generate_integer_id(),
                    movie_id,
                    payload.get("MagnetUri"),
                    int(sub),
                    int(cen),
                    payload.get("ResolutionType"),
                    payload.get("Size") or "",
                    int(payload.get("FileCount") or 0),
                    when,
                    when,
                    session_id,
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
                    session_id,
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
            consumed_torrent_seqs.extend(merged)
        else:
            consumed_torrent_seqs.append(payload["Seq"])

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


def _bulk_run(conn, statements):
    """Run a list of ``(sql, params)`` via ``batch_execute`` if available.

    Falls back to a per-statement ``execute()`` loop when *conn* does not
    expose ``batch_execute`` (raw SQLite, used in tests). Under
    :class:`DualConnection` the call wraps :meth:`D1Connection.batch_execute`
    which auto-chunks at ``D1_BATCH_LIMIT`` (default 50) per HTTP round-trip
    while preserving submission order in the returned cursor list.
    """
    if not statements:
        return []
    batch = getattr(conn, "batch_execute", None)
    if callable(batch):
        return batch(list(statements))
    return [conn.execute(sql, params) for sql, params in statements]


def _chunked(seq, size: int):
    """Yield successive *size*-length slices of *seq* as lists."""
    items = list(seq)
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _commit_session_bulk(
    conn,
    session_id: str,
    *,
    when: str,
    exclude_movie_seqs: Optional[set] = None,
    exclude_torrent_seqs: Optional[set] = None,
) -> Tuple[Dict[str, int], set, set]:
    """Session-level bulk variant of the per-href :func:`_commit_one_movie`.

    Semantically equivalent to applying :func:`_commit_one_movie` to every
    pending href in the session, but collapses ~13–20 D1 round-trips per
    href into O(N/50 + const) batched HTTP requests. See
    ``.claude/plans/apps-cli-commit-session-ingestion-spide-gentle-sloth.md``.

    Returns ``(counts, consumed_movie_seqs, consumed_torrent_seqs)``. The
    Seq sets let the drain wrapper exclude already-processed rows on the
    next pass without an extra ``NOT IN`` round-trip (we filter in Python
    after the SELECT to stay under D1's 100-param-per-statement cap).
    """
    exclude_movie_seqs = exclude_movie_seqs or set()
    exclude_torrent_seqs = exclude_torrent_seqs or set()
    counts: Dict[str, int] = {
        "movies_upserted": 0,
        "torrents_upserted": 0,
        "torrents_deleted": 0,
        "pending_marked_applied": 0,
    }

    # ── Phase A: bulk prefetch (2 SELECTs over Pending* tables) ─────────
    overlay_cursors = _bulk_run(conn, [
        (
            "SELECT * FROM PendingMovieHistoryWrites "
            "WHERE SessionId=? AND ApplyState IN ('pending','applied') "
            "ORDER BY Seq ASC",
            (session_id,),
        ),
        (
            "SELECT * FROM PendingTorrentHistoryWrites "
            "WHERE SessionId=? AND ApplyState IN ('pending','applied') "
            "ORDER BY Seq ASC",
            (session_id,),
        ),
    ])
    raw_movie_rows = [
        r for r in overlay_cursors[0].fetchall()
        if r["Seq"] not in exclude_movie_seqs
    ]
    raw_torrent_rows = [
        r for r in overlay_cursors[1].fetchall()
        if r["Seq"] not in exclude_torrent_seqs
    ]
    movie_overlay = _merge_movie_overlay_rows(raw_movie_rows)
    torrent_overlay = _merge_torrent_overlay_rows(raw_torrent_rows)

    if not movie_overlay and not torrent_overlay:
        return counts, set(), set()

    hrefs = sorted(
        set(movie_overlay.keys()) | {k[0] for k in torrent_overlay.keys()}
    )
    href_to_variants = {h: _href_lookup_variants(h) for h in hrefs}
    variant_to_href: Dict[str, str] = {}
    for h, variants in href_to_variants.items():
        for v in variants:
            variant_to_href.setdefault(v, h)

    # ── Phase A.2: bulk-lookup existing MovieHistory by Href variants ──
    # Chunk by 99 params (under D1's 100-param-per-statement cap).
    live_movies_by_href: Dict[str, dict] = {}
    movie_lookup_stmts = []
    all_variants = list(variant_to_href.keys())
    for chunk in _chunked(all_variants, 99):
        ph = ",".join("?" for _ in chunk)
        movie_lookup_stmts.append((
            f"SELECT * FROM MovieHistory WHERE Href IN ({ph})",
            tuple(chunk),
        ))
    for cur in _bulk_run(conn, movie_lookup_stmts):
        for row in cur.fetchall():
            d = dict(row)
            canonical = variant_to_href.get(d["Href"])
            if canonical and canonical not in live_movies_by_href:
                live_movies_by_href[canonical] = d

    # Build torrent-overlay grouped by canonical href.
    torrents_by_href: Dict[str, Dict[Tuple[int, int], dict]] = {}
    for (h, sub, cen), payload in torrent_overlay.items():
        torrents_by_href.setdefault(h, {})[(int(sub), int(cen))] = payload

    # ── Phase B: classify each href into INSERT-new / UPDATE / skip ──
    base_url = cfg("BASE_URL", "https://javdb.com")
    # href → (sql, params) for new-movie INSERTs.  Ids are pre-generated so
    # we never need cur.lastrowid (which is unreliable across dual-write
    # backends under STORAGE_BACKEND=dual — see C.1 in the audit plan).
    new_movie_insert_stmts: List[Tuple[str, tuple]] = []
    movie_updates: List[Tuple[str, tuple]] = []
    consumed_movie_seqs: List[str] = []
    consumed_torrent_seqs: List[str] = []

    href_to_movie_id: Dict[str, int] = {
        h: int(row["Id"]) for h, row in live_movies_by_href.items()
    }

    for href in hrefs:
        movie_payload = movie_overlay.get(href)
        existing = live_movies_by_href.get(href)
        torrents_here = torrents_by_href.get(href, {})

        if movie_payload is None and existing is None and not torrents_here:
            continue

        if movie_payload is not None:
            seqs = movie_payload.get("_merged_seqs") or [movie_payload["Seq"]]
            consumed_movie_seqs.extend(seqs)

        if existing is None:
            video_code = (
                (movie_payload or {}).get("VideoCode")
                or next(
                    (
                        r.get("VideoCode") for r in torrents_here.values()
                        if r.get("VideoCode")
                    ),
                    "",
                )
                or ""
            )
            _, abs_href = movie_href_lookup_values(href, base_url)
            normalized_href = abs_href or href
            movie_id = _generate_integer_id()
            href_to_movie_id[href] = movie_id
            new_movie_insert_stmts.append((
                """INSERT INTO MovieHistory
                   (Id, VideoCode, Href, DateTimeCreated, DateTimeUpdated,
                    DateTimeVisited, ActorName, ActorGender, ActorLink,
                    SupportingActors, SessionId)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    movie_id,
                    video_code,
                    normalized_href,
                    when,
                    when,
                    (movie_payload or {}).get("DateTimeVisited") or when,
                    (movie_payload or {}).get("ActorName"),
                    (movie_payload or {}).get("ActorGender"),
                    (movie_payload or {}).get("ActorLink"),
                    (movie_payload or {}).get("SupportingActors"),
                    session_id,
                ),
            ))
        else:
            movie_id = int(existing["Id"])
            update_fields = ["DateTimeUpdated=?", "SessionId=?"]
            up_params: list = [when, session_id]
            if movie_payload is not None:
                update_fields.append("DateTimeVisited=?")
                up_params.append(movie_payload.get("DateTimeVisited") or when)
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
                        up_params.append(value)
            up_params.append(movie_id)
            movie_updates.append((
                f"UPDATE MovieHistory SET {', '.join(update_fields)} WHERE Id=?",
                tuple(up_params),
            ))
        counts["movies_upserted"] += 1

    # ── Phase C1: flush new-movie INSERTs in batches ─────────────────────
    # Ids are already in href_to_movie_id; no lastrowid needed.
    for chunk in _chunked(new_movie_insert_stmts, 50):
        _bulk_run(conn, chunk)

    # ── Phase C2: bulk-read live TorrentHistory by MovieHistoryId ───────
    live_torrents_by_mid: Dict[int, Dict[Tuple[int, int], dict]] = {}
    torrent_lookup_stmts = []
    for chunk in _chunked(href_to_movie_id.values(), 100):
        ph = ",".join("?" for _ in chunk)
        torrent_lookup_stmts.append((
            f"SELECT * FROM TorrentHistory WHERE MovieHistoryId IN ({ph})",
            tuple(chunk),
        ))
    for cur in _bulk_run(conn, torrent_lookup_stmts):
        for row in cur.fetchall():
            d = dict(row)
            mid = int(d["MovieHistoryId"])
            live_torrents_by_mid.setdefault(mid, {})[
                (int(d["SubtitleIndicator"]), int(d["CensorIndicator"]))
            ] = d

    # ── Phase D: torrent writes + queued movie UPDATEs ──────────────────
    write_stmts: List[Tuple[str, tuple]] = list(movie_updates)
    # Projected post-write torrent state — used by Phase E indicator recompute.
    projected: Dict[int, Dict[Tuple[int, int], Optional[int]]] = {}
    for mid, live in live_torrents_by_mid.items():
        projected[mid] = {
            (s, c): row.get("ResolutionType") for (s, c), row in live.items()
        }

    for href in hrefs:
        mid = href_to_movie_id.get(href)
        if mid is None:
            continue
        torrents_here = torrents_by_href.get(href, {})
        live_for_movie = live_torrents_by_mid.get(mid, {})
        proj = projected.setdefault(mid, {})

        for (sub, cen), payload in torrents_here.items():
            sub_i, cen_i = int(sub), int(cen)
            resolution = payload.get("ResolutionType")
            existing_t = live_for_movie.get((sub_i, cen_i))
            if existing_t is None:
                write_stmts.append((
                    """INSERT INTO TorrentHistory
                       (Id, MovieHistoryId, MagnetUri, SubtitleIndicator,
                        CensorIndicator, ResolutionType, Size, FileCount,
                        DateTimeCreated, DateTimeUpdated, SessionId)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        _generate_integer_id(),
                        mid,
                        payload.get("MagnetUri"),
                        sub_i,
                        cen_i,
                        resolution,
                        payload.get("Size") or "",
                        int(payload.get("FileCount") or 0),
                        when,
                        when,
                        session_id,
                    ),
                ))
            else:
                write_stmts.append((
                    """UPDATE TorrentHistory
                       SET MagnetUri=?, Size=?, FileCount=?, ResolutionType=?,
                           DateTimeUpdated=?, SessionId=?
                       WHERE Id=?""",
                    (
                        payload.get("MagnetUri"),
                        payload.get("Size") or "",
                        int(payload.get("FileCount") or 0),
                        resolution,
                        when,
                        session_id,
                        int(existing_t["Id"]),
                    ),
                ))
            proj[(sub_i, cen_i)] = resolution
            counts["torrents_upserted"] += 1
            merged = payload.get("_merged_seqs")
            if merged:
                consumed_torrent_seqs.extend(merged)
            else:
                consumed_torrent_seqs.append(payload["Seq"])

        # Conflict-deletion rules (mirror _commit_one_movie):
        #   hacked_subtitle (1,0) shadows no_subtitle (0,0)
        #   subtitle        (1,1) shadows no_subtitle_cen (0,1)
        has_hacked_sub = any(k == (1, 0) for k in torrents_here.keys())
        has_subtitle = any(k == (1, 1) for k in torrents_here.keys())
        if has_hacked_sub:
            write_stmts.append((
                "DELETE FROM TorrentHistory WHERE MovieHistoryId=? "
                "AND SubtitleIndicator=0 AND CensorIndicator=0",
                (mid,),
            ))
            if (0, 0) in proj:
                del proj[(0, 0)]
                counts["torrents_deleted"] += 1
        if has_subtitle:
            write_stmts.append((
                "DELETE FROM TorrentHistory WHERE MovieHistoryId=? "
                "AND SubtitleIndicator=0 AND CensorIndicator=1",
                (mid,),
            ))
            if (0, 1) in proj:
                del proj[(0, 1)]
                counts["torrents_deleted"] += 1

    for chunk in _chunked(write_stmts, 50):
        _bulk_run(conn, chunk)

    # ── Phase E: indicator recompute in memory ──────────────────────────
    indicator_updates: List[Tuple[str, tuple]] = []
    for mid in sorted(set(href_to_movie_id.values())):
        proj = projected.get(mid, {})
        perfect, hires = _compute_indicators(
            (s, c, r) for (s, c), r in proj.items()
        )
        indicator_updates.append((
            "UPDATE MovieHistory SET PerfectMatchIndicator=?, "
            "HiResIndicator=? WHERE Id=?",
            (perfect, hires, mid),
        ))

    # ── Phase F: indicator UPDATEs + apply-mark UPDATEs ─────────────────
    apply_mark_stmts: List[Tuple[str, tuple]] = []
    for chunk in _chunked(consumed_movie_seqs, 99):
        ph = ",".join("?" for _ in chunk)
        apply_mark_stmts.append((
            f"UPDATE PendingMovieHistoryWrites SET ApplyState='applied' "
            f"WHERE Seq IN ({ph})",
            tuple(chunk),
        ))
    for chunk in _chunked(consumed_torrent_seqs, 99):
        ph = ",".join("?" for _ in chunk)
        apply_mark_stmts.append((
            f"UPDATE PendingTorrentHistoryWrites SET ApplyState='applied' "
            f"WHERE Seq IN ({ph})",
            tuple(chunk),
        ))

    final_stmts = indicator_updates + apply_mark_stmts
    for chunk in _chunked(final_stmts, 50):
        cursors = _bulk_run(conn, chunk)
        for (sql, _p), cur in zip(chunk, cursors):
            if sql.startswith("UPDATE PendingMovieHistoryWrites") \
                    or sql.startswith("UPDATE PendingTorrentHistoryWrites"):
                counts["pending_marked_applied"] += int(
                    getattr(cur, "rowcount", 0) or 0
                )

    return counts, set(consumed_movie_seqs), set(consumed_torrent_seqs)


def _pending_distinct_hrefs(conn, session_id: str) -> List[str]:
    """Return every Href that has at least one pending row for *session_id*."""
    rows = conn.execute(
        "SELECT Href FROM ("
        "  SELECT Href FROM PendingMovieHistoryWrites "
        "  WHERE SessionId=? AND ApplyState IN ('pending','applied') "
        "  UNION "
        "  SELECT Href FROM PendingTorrentHistoryWrites "
        "  WHERE SessionId=? AND ApplyState IN ('pending','applied')"
        ") ORDER BY Href",
        (session_id, session_id),
    ).fetchall()
    return [r["Href"] for r in rows]


def _d1_retry_pending_cleanup(session_id: str) -> None:
    """Best-effort D1-direct retry for pending-row cleanup.

    After the normal DualConnection commit flow, any D1-side failures on
    the ApplyState UPDATE or the final DELETE leave orphaned 'pending'
    rows in D1. Since the session is already committed and the live
    tables are consistent, we can safely mark remaining pending rows as
    applied and delete them directly on D1.
    """
    from javdb.storage.db.db_connection import current_backend
    if current_backend() not in ('d1', 'dual'):
        return
    try:
        from javdb.storage.d1_client import make_d1_connection
    except Exception:
        return
    d1 = None
    try:
        d1 = make_d1_connection('history')
        for table in ('PendingMovieHistoryWrites', 'PendingTorrentHistoryWrites'):
            d1.execute(
                f"UPDATE {table} SET ApplyState='applied' "
                f"WHERE SessionId=? AND ApplyState='pending'",
                (session_id,),
            )
            d1.execute(
                f"DELETE FROM {table} "
                f"WHERE SessionId=? AND ApplyState='applied'",
                (session_id,),
            )
    except Exception as exc:
        logger.warning(
            "D1 retry pending cleanup failed for session %s: %s",
            session_id, exc,
        )
    finally:
        if d1 is not None:
            try:
                d1.close()
            except Exception:
                pass


def db_commit_session_history(
    session_id: str,
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

    use_bulk = os.getenv("COMMIT_SESSION_BULK", "0").strip().lower() in (
        "1", "true", "yes", "on",
    )

    if use_bulk:
        # Bulk path: collapse the per-href loop into 2 SELECTs + chunked
        # batched writes per drain pass. See plan at
        # .claude/plans/apps-cli-commit-session-ingestion-spide-gentle-sloth.md
        # Drain across up to 4 passes (1 initial + 3 rescans) so that pending
        # rows staged AFTER our prefetch by a concurrent stager are still
        # absorbed — Seqs are excluded post-SELECT (no NOT IN, under D1's
        # 100-param cap).
        seen_movie: set = set()
        seen_torrent: set = set()
        hrefs_seen: set = set()
        for attempt in range(4):
            with get_db(history_db_path or HISTORY_DB_PATH) as conn:
                pass_counts, new_m, new_t = _commit_session_bulk(
                    conn, session_id, when=when,
                    exclude_movie_seqs=seen_movie,
                    exclude_torrent_seqs=seen_torrent,
                )
                # Capture which hrefs were touched in this pass via the
                # consumed Seq sets — we re-derive hrefs in the bulk
                # function so this is a cheap follow-up SELECT only when
                # we need a final hrefs_processed count.
            if not new_m and not new_t:
                break
            for k, v in pass_counts.items():
                counts[k] = counts.get(k, 0) + v
            seen_movie |= new_m
            seen_torrent |= new_t
            if attempt >= 1:
                logger.info(
                    "db_commit_session_history(session=%s, bulk=1): "
                    "rescan pass %d absorbed %d movie + %d torrent Seq(s)",
                    session_id, attempt, len(new_m), len(new_t),
                )
        # hrefs_processed: count distinct hrefs touched. Re-derive from the
        # union of consumed Seqs by sampling Pending tables one more time.
        # Cheap (single SELECT) and stays consistent with the audit path.
        with get_db(history_db_path or HISTORY_DB_PATH) as conn:
            counts["hrefs_processed"] = len(
                _pending_distinct_hrefs(conn, session_id)
            )
    else:
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
            (session_id,),
        )
        cur_t = conn.execute(
            "DELETE FROM PendingTorrentHistoryWrites "
            "WHERE SessionId=? AND ApplyState='applied'",
            (session_id,),
        )
        counts["pending_deleted"] = (cur_m.rowcount or 0) + (cur_t.rowcount or 0)

    _d1_retry_pending_cleanup(session_id)

    return counts


def db_resume_finalizing_session(
    session_id: str,
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
    session_id: str,
    *,
    db_path: Optional[str] = None,
) -> Dict[str, int]:
    """Snapshot pending-table counts for *session_id* (Phase 2 verify)."""
    from javdb.storage.db.db_reports import (
        db_pending_session_stats as _f,
    )
    return _f(session_id, db_path=db_path or HISTORY_DB_PATH)


def db_get_session_run_identity(
    session_id: str,
    *,
    db_path: Optional[str] = None,
) -> Optional[Tuple[Optional[str], Optional[int]]]:
    """Return ``(RunId, RunAttempt)`` for *session_id*, or ``None`` if absent."""
    from javdb.storage.db.db_reports import (
        db_get_session_run_identity as _f,
    )
    return _f(session_id, db_path=db_path or REPORTS_DB_PATH)


def _rollback_pending_in_progress(
    session_id: str,
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
                (session_id,),
            ).fetchone() or {"n": 0})["n"]
            counts["PendingTorrentHistoryWrites"] = (conn.execute(
                "SELECT COUNT(*) AS n FROM PendingTorrentHistoryWrites "
                "WHERE SessionId=?",
                (session_id,),
            ).fetchone() or {"n": 0})["n"]
        else:
            cur_m = conn.execute(
                "DELETE FROM PendingMovieHistoryWrites WHERE SessionId=?",
                (session_id,),
            )
            cur_t = conn.execute(
                "DELETE FROM PendingTorrentHistoryWrites WHERE SessionId=?",
                (session_id,),
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
    session_id: Optional[str] = None,
    write_mode: Optional[str] = None,
) -> int:
    """Create a new report session and return its id."""
    from javdb.storage.db.db_reports import (
        db_create_report_session as _f,
    )
    return _f(report_type, report_date, csv_filename, url_type=url_type,
              display_name=display_name, url=url, start_page=start_page,
              end_page=end_page, created_at=created_at,
              db_path=db_path or REPORTS_DB_PATH,
              run_id=run_id, run_attempt=run_attempt, session_id=session_id,
              write_mode=write_mode)


def db_mark_session_committed(
    session_id: str,
    db_path: Optional[str] = None,
) -> int:
    """Mark a session as ``committed`` so it survives any future cleanup."""
    from javdb.storage.db.db_reports import (
        db_mark_session_committed as _f,
    )
    return _f(session_id, db_path or REPORTS_DB_PATH)


def db_mark_session_failed(
    session_id: str,
    db_path: Optional[str] = None,
    *,
    reason: Optional[str] = None,
) -> int:
    """Mark a session as ``failed`` (debug-only flag set right before delete)."""
    from javdb.storage.db.db_reports import (
        db_mark_session_failed as _f,
    )
    return _f(session_id, db_path or REPORTS_DB_PATH, reason=reason)


def db_find_in_progress_sessions(
    *,
    since: Optional[str] = None,
    db_path: Optional[str] = None,
    max_age_hours: Optional[float] = None,
    require_run_identity: bool = False,
) -> List[str]:
    """Return ``ReportSessions.Id`` rows still flagged ``in_progress``."""
    from javdb.storage.db.db_reports import (
        db_find_in_progress_sessions as _f,
    )
    return _f(since=since, db_path=db_path or REPORTS_DB_PATH,
              max_age_hours=max_age_hours,
              require_run_identity=require_run_identity)


def db_find_stale_pending_sessions(
    *,
    db_path: Optional[str] = None,
    max_age_hours: float = 48.0,
    require_run_identity: bool = True,
) -> List[Tuple[int, str, str]]:
    """Return ``[(Id, Status, WriteMode), ...]`` for stale Phase 3 sessions."""
    from javdb.storage.db.db_reports import (
        db_find_stale_pending_sessions as _f,
    )
    return _f(db_path=db_path or REPORTS_DB_PATH, max_age_hours=max_age_hours,
              require_run_identity=require_run_identity)


def db_count_in_progress_sessions_for_run(
    run_id: str,
    run_attempt: Optional[int] = None,
    *,
    db_path: Optional[str] = None,
) -> int:
    """Count ``in_progress`` sessions belonging to a (RunId, RunAttempt) pair."""
    from javdb.storage.db.db_reports import (
        db_count_in_progress_sessions_for_run as _f,
    )
    return _f(run_id, run_attempt, db_path=db_path or REPORTS_DB_PATH)


def db_find_in_progress_session_ids_for_run_csv(
    run_id: str,
    run_attempt: Optional[int],
    csv_filename: str,
    *,
    db_path: Optional[str] = None,
) -> List[str]:
    """Return ``in_progress`` SessionIds for the same (RunId, RunAttempt, CSVFilename)."""
    from javdb.storage.db.db_reports import (
        db_find_in_progress_session_ids_for_run_csv as _f,
    )
    return _f(run_id, run_attempt, csv_filename,
              db_path=db_path or REPORTS_DB_PATH)


def db_find_sessions_by_run(
    run_id: str,
    run_attempt: Optional[int] = None,
    *,
    reports_db_path: Optional[str] = None,
    history_db_path: Optional[str] = None,
) -> List[str]:
    """Return every session id touched by a (RunId, RunAttempt) workflow run."""
    from javdb.storage.db.db_reports import (
        db_find_sessions_by_run as _f,
    )
    return _f(run_id, run_attempt,
              reports_db_path=reports_db_path or REPORTS_DB_PATH,
              history_db_path=history_db_path or HISTORY_DB_PATH)


# ── Rollback orchestration (X3 hybrid) ───────────────────────────────────

def _rollback_reports(
    session_id: str,
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
    session_id: str,
    *,
    dry_run: bool,
    db_path: Optional[str] = None,
) -> Dict[str, int]:
    """Delete operations-DB rows tagged with *session_id* and DROP its staging."""
    counts: Dict[str, int] = {}
    staging_table = f"RcloneInventoryStaging_{_session_id_to_identifier_suffix(session_id)}"
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
    session_id: str,
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
                        except _DB_INTEGRITY_ERRORS as e:
                            # E.g. UNIQUE conflict — a concurrent run
                            # already reinserted something with the same
                            # business key. Skip + drift log. d1-only
                            # surfaces a UNIQUE violation as
                            # D1PermanentError (D1 collapses all HTTP-400
                            # application errors into that type), hence
                            # _DB_INTEGRITY_ERRORS rather than the bare
                            # sqlite3.IntegrityError. D1TransientError is
                            # deliberately NOT caught here — retries are
                            # already exhausted by then and a network
                            # failure mid-reinsert must abort, not skip.
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
    except _DB_OPERATIONAL_ERRORS:
        # Main table missing — can't prove the row is orphaned, so play
        # safe and keep the audit row. d1-only surfaces a missing table
        # as D1PermanentError.
        return False
    return row is None


def db_rollback_session(
    session_id: str,
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


def db_insert_report_rows(session_id: str, rows: List[dict], db_path: Optional[str] = None) -> int:
    """Insert report rows into ReportMovies + ReportTorrents."""
    from javdb.storage.db.db_reports import (
        db_insert_report_rows as _f,
    )
    return _f(session_id, rows, db_path or REPORTS_DB_PATH)


def db_get_report_rows(session_id: str, db_path: Optional[str] = None) -> List[dict]:
    """Get all rows for a session as flat dicts (backward compatible)."""
    from javdb.storage.db.db_reports import (
        db_get_report_rows as _f,
    )
    return _f(session_id, db_path or REPORTS_DB_PATH)


def db_get_latest_session(report_type: Optional[str] = None, db_path: Optional[str] = None) -> Optional[dict]:
    """Get the most recent report session, optionally filtered by type."""
    from javdb.storage.db.db_reports import (
        db_get_latest_session as _f,
    )
    return _f(report_type, db_path or REPORTS_DB_PATH)


def db_get_sessions_by_date(report_date: str, report_type: Optional[str] = None,
                            db_path: Optional[str] = None) -> List[dict]:
    """Get all sessions for a given date."""
    from javdb.storage.db.db_reports import (
        db_get_sessions_by_date as _f,
    )
    return _f(report_date, report_type=report_type, db_path=db_path)


# ── Stats helpers ────────────────────────────────────────────────────────

def db_save_spider_stats(session_id: str, stats: dict, db_path: Optional[str] = None) -> int:
    """Save spider statistics for a session (idempotent via ON CONFLICT)."""
    from javdb.storage.db.db_stats import (
        db_save_spider_stats as _f,
    )
    return _f(session_id, stats, db_path=db_path or REPORTS_DB_PATH)


def db_save_uploader_stats(session_id: str, stats: dict, db_path: Optional[str] = None) -> int:
    """Save uploader statistics for a session (idempotent via ON CONFLICT)."""
    from javdb.storage.db.db_stats import (
        db_save_uploader_stats as _f,
    )
    return _f(session_id, stats, db_path=db_path or REPORTS_DB_PATH)


def db_save_pikpak_stats(session_id: str, stats: dict, db_path: Optional[str] = None) -> int:
    """Save PikPak bridge statistics for a session (idempotent via ON CONFLICT)."""
    from javdb.storage.db.db_stats import (
        db_save_pikpak_stats as _f,
    )
    return _f(session_id, stats, db_path=db_path or REPORTS_DB_PATH)


def db_get_spider_stats(session_id: str, db_path: Optional[str] = None) -> Optional[dict]:
    """Get spider stats for a session."""
    from javdb.storage.db.db_stats import (
        db_get_spider_stats as _f,
    )
    return _f(session_id, db_path=db_path or REPORTS_DB_PATH)


def db_get_uploader_stats(session_id: str, db_path: Optional[str] = None) -> Optional[dict]:
    """Get uploader stats for a session."""
    from javdb.storage.db.db_stats import (
        db_get_uploader_stats as _f,
    )
    return _f(session_id, db_path=db_path or REPORTS_DB_PATH)


def db_get_pikpak_stats(session_id: str, db_path: Optional[str] = None) -> Optional[dict]:
    """Get PikPak stats for a session."""
    from javdb.storage.db.db_stats import (
        db_get_pikpak_stats as _f,
    )
    return _f(session_id, db_path=db_path or REPORTS_DB_PATH)


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
    session_id: str, db_path: Optional[str] = None,
) -> Optional[dict]:
    """SQLite-only counterpart to :func:`db_get_spider_stats`."""
    from javdb.storage.db.db_stats import (
        db_get_spider_stats_local as _f,
    )
    return _f(session_id, db_path=db_path or REPORTS_DB_PATH)


def db_get_uploader_stats_local(
    session_id: str, db_path: Optional[str] = None,
) -> Optional[dict]:
    """SQLite-only counterpart to :func:`db_get_uploader_stats`."""
    from javdb.storage.db.db_stats import (
        db_get_uploader_stats_local as _f,
    )
    return _f(session_id, db_path=db_path or REPORTS_DB_PATH)


def db_get_pikpak_stats_local(
    session_id: str, db_path: Optional[str] = None,
) -> Optional[dict]:
    """SQLite-only counterpart to :func:`db_get_pikpak_stats`."""
    from javdb.storage.db.db_stats import (
        db_get_pikpak_stats_local as _f,
    )
    return _f(session_id, db_path=db_path or REPORTS_DB_PATH)


def db_get_latest_session_local(
    report_type: Optional[str] = None, db_path: Optional[str] = None,
) -> Optional[dict]:
    """SQLite-only counterpart to :func:`db_get_latest_session`."""
    from javdb.storage.db.db_reports import (
        db_get_latest_session_local as _f,
    )
    return _f(report_type, db_path=db_path or REPORTS_DB_PATH)
