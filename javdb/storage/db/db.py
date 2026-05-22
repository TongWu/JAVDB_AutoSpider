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
    HistoryRepo,
    load_history_joined as _load_history_joined,
    batch_update_movie_actors as _batch_update_movie_actors,
    _has_meaningful_actor_data,
)
from javdb.storage.repos.operations_repo import OperationsRepo
from javdb.storage.repos.stats_repo import StatsRepo
logger = get_logger(__name__)

# MR-3 (multi-runtime): backend-agnostic exception tuples.
# Canonical definitions live in db_connection.py; re-exported here for
# backwards compatibility.
from .db_connection import _DB_OPERATIONAL_ERRORS, _DB_INTEGRITY_ERRORS

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

SCHEMA_VERSION = 14

# ── Connection management ────────────────────────────────────────────────
# Use the same thread-local as db_connection.py so init_db's dual-backend
# override (set in db_migrations via db_connection._local) is visible to
# _backend_mode / _get_connection calls from any module.
from .db_connection import _local

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

# Serializes the dual-backend init window — canonical definition moved to
# db_migrations.py; re-exported here for backwards compatibility.
from .db_migrations import _init_lock


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


# ── Schema DDL — canonical definitions moved to db_migrations.py ────────
from .db_migrations import (
    _SCHEMA_VERSION_DDL,
    _HISTORY_DDL,
    _REPORTS_DDL,
    _OPERATIONS_DDL,
    _TABLES_SQL,
)



# ── Category ↔ Indicator mapping (delegated to contracts) ────────────────

from javdb.spider.contracts import category_to_indicators, indicators_to_category  # noqa: E402


# ── Migration & init — canonical definitions moved to db_migrations.py ──
from .db_migrations import (  # noqa: E402
    _has_table,
    _migrate_v5_to_v6,
    _ensure_moviehistory_actor_columns,
    _moviehistory_actor_column_names,
    _moviehistory_actor_columns_all_present,
    _moviehistory_actor_columns_physical_order_ok,
    _ensure_rollback_columns,
    _materialize_report_session_status_default,
    _normalize_moviehistory_actor_column_order,
    moviehistory_actor_layout_ok,
    _DEFAULT_RE,
    _migrate_defaults_to_null,
    _V12_SESSION_ID_RE,
    _V12_REPORTSESSIONS_ID_RE,
    _V12_PENDING_SEQ_RE,
    _V12_REPORTSESSIONS_TABLES,
    _migrate_session_id_to_text,
    _rebuild_table_with_new_ddl,
    _migrate_v14_drop_audit_tables,
    _dedupe_session_keyed_stats_rows,
    _detect_version,
    _backfill_torrent_sizes_after_split,
    _moviehistory_actor_select_exprs_from_attached_old_db,
    _quote_ident,
    _attached_table_info,
    _attached_table_column_names,
    _copy_attached_table_by_common_columns,
    _migrate_single_to_split,
    _init_single_db,
    _init_single_legacy_db,
    _do_init,
    init_db,
)


# ── MovieHistory + TorrentHistory helpers ────────────────────────────────

def db_load_history(db_path: Optional[str] = None, phase: Optional[int] = None) -> Dict[str, dict]:
    """Load history from MovieHistory + TorrentHistory into a dict keyed by Href."""
    return HistoryRepo(db_path=db_path or HISTORY_DB_PATH).load_history(phase=phase)


# ── Backend batch helper ───────────────────────────────────────────────
# Canonical definitions live in db_connection.py; re-exported here for
# backwards compatibility.
from .db_connection import _execute_backend_batch, _row_to_jsonable_dict


# ── Upsert + delete + indicator helpers — moved to db_history_write.py ──
from .db_history_write import (  # noqa: E402
    _upsert_one_history_on_conn,
    _delete_torrents_with_audit,
    _update_movie_indicators,
)


# ── Batch update functions — moved to db_history_write.py ──
from .db_history_write import (  # noqa: E402
    db_batch_update_last_visited,
    db_batch_update_movie_actors,
)


def db_check_torrent_in_history(href: str, torrent_type: str, db_path: Optional[str] = None) -> bool:
    """Check if a specific torrent type exists for href."""
    return HistoryRepo(db_path=db_path).check_torrent_in_history(
        href, torrent_type,
    )


def db_get_all_history_records(db_path: Optional[str] = None) -> List[dict]:
    """Return all MovieHistory records as dicts (for migration verification)."""
    return HistoryRepo(db_path=db_path).get_all_history_records()


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
    return OperationsRepo(db_path=db_path or OPERATIONS_DB_PATH).open_rclone_staging(
        session_id,
    )


def db_append_rclone_staging(
    entries: List[dict],
    session_id: Any = _SESSION_ID_SENTINEL,
    db_path: Optional[str] = None,
) -> int:
    """Append rows to this session's RcloneInventory staging table."""
    return OperationsRepo(db_path=db_path or OPERATIONS_DB_PATH).append_rclone_staging(
        entries, session_id,
    )


def db_swap_rclone_inventory(
    session_id: Any = _SESSION_ID_SENTINEL,
    db_path: Optional[str] = None,
) -> int:
    """Atomically swap this session's staging into the live RcloneInventory."""
    return OperationsRepo(db_path=db_path).swap_rclone_inventory(session_id)


def db_merge_rclone_inventory_from_stage(
    session_id: Any = _SESSION_ID_SENTINEL,
    years: Optional[Iterable[str]] = None,
    db_path: Optional[str] = None,
) -> int:
    """Merge this session's staging rows into selected RcloneInventory years."""
    return OperationsRepo(
        db_path=db_path or OPERATIONS_DB_PATH,
    ).merge_rclone_inventory_from_stage(session_id, years)


def db_drop_rclone_staging(
    session_id: str,
    db_path: Optional[str] = None,
) -> None:
    """DROP TABLE IF EXISTS RcloneInventoryStaging_<session_id> (idempotent)."""
    OperationsRepo(db_path=db_path or OPERATIONS_DB_PATH).drop_rclone_staging(
        session_id,
    )


def db_clear_rclone_inventory(db_path: Optional[str] = None) -> None:
    """Delete all rows from RcloneInventory."""
    OperationsRepo(db_path=db_path).clear_rclone_inventory()


def db_append_rclone_inventory(entries: List[dict], db_path: Optional[str] = None) -> int:
    """Append rows to RcloneInventory using executemany for speed."""
    from javdb.storage.db.db_operations import (
        db_append_rclone_inventory as _f,
    )
    return _f(entries, db_path=db_path)


def db_load_rclone_inventory(db_path: Optional[str] = None) -> Dict[str, list]:
    """Load inventory grouped by VideoCode."""
    return OperationsRepo(db_path=db_path or OPERATIONS_DB_PATH).load_rclone_inventory()


def db_delete_rclone_inventory_paths(
    paths: Iterable[str],
    db_path: Optional[str] = None,
) -> int:
    """Bulk delete RcloneInventory rows by FolderPath."""
    return OperationsRepo(
        db_path=db_path or OPERATIONS_DB_PATH,
    ).delete_rclone_inventory_paths(paths)


# ── DedupRecords helpers ─────────────────────────────────────────────────

def db_load_dedup_records(db_path: Optional[str] = None) -> List[dict]:
    """Load all dedup records."""
    return OperationsRepo(db_path=db_path or OPERATIONS_DB_PATH).load_dedup_records()


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
    return OperationsRepo(
        db_path=db_path or OPERATIONS_DB_PATH,
    ).append_dedup_record(record, session_id=session_id)


def db_mark_records_deleted(
    path_datetime_pairs: List[Tuple[str, str]],
    db_path: Optional[str] = None,
    session_id: Any = _SESSION_ID_SENTINEL,
) -> int:
    """Mark specific dedup records as deleted by gdrive path."""
    return OperationsRepo(
        db_path=db_path or OPERATIONS_DB_PATH,
    ).mark_records_deleted(path_datetime_pairs, session_id=session_id)


def db_mark_orphan_records(
    paths: Iterable[str],
    reason_suffix: str,
    when: str,
    db_path: Optional[str] = None,
    session_id: Any = _SESSION_ID_SENTINEL,
) -> int:
    """Mark dedup pending rows as deleted with custom reason suffix appended."""
    return OperationsRepo(
        db_path=db_path or OPERATIONS_DB_PATH,
    ).mark_orphan_records(
        paths, reason_suffix, when, session_id=session_id,
    )


def db_cleanup_deleted_records(
    older_than_days: int = 30,
    db_path: Optional[str] = None,
) -> int:
    """Remove dedup records that were deleted more than *older_than_days* ago."""
    return OperationsRepo(
        db_path=db_path or OPERATIONS_DB_PATH,
    ).cleanup_deleted_records(older_than_days=older_than_days)


def db_save_dedup_records(rows: List[dict], db_path: Optional[str] = None) -> None:
    """Overwrite all dedup records (deprecated)."""
    return OperationsRepo(db_path=db_path).save_dedup_records(rows)


# ── PikpakHistory helpers ────────────────────────────────────────────────

def db_append_pikpak_history(
    record: dict,
    db_path: Optional[str] = None,
    session_id: Any = _SESSION_ID_SENTINEL,
) -> int:
    """Append a PikPak transfer record."""
    return OperationsRepo(
        db_path=db_path or OPERATIONS_DB_PATH,
    ).append_pikpak_history(record, session_id=session_id)


# ── InventoryAlignNoExactMatch helpers ───────────────────────────────────

def db_upsert_align_no_exact_match(
    video_code: str,
    reason: str = 'exact_video_code_not_found',
    db_path: Optional[str] = None,
    session_id: Any = _SESSION_ID_SENTINEL,
) -> None:
    """Record a video code that had no exact match on JavDB search."""
    return OperationsRepo(db_path=db_path).upsert_align_no_exact_match(
        video_code, reason=reason, session_id=session_id,
    )


def db_load_align_no_exact_match_codes(db_path: Optional[str] = None) -> set:
    """Return the set of normalised video codes previously marked as no-exact-match."""
    return OperationsRepo(db_path=db_path).load_align_no_exact_match_codes()


def db_delete_align_no_exact_match(
    video_code: str,
    db_path: Optional[str] = None,
) -> None:
    """Remove a video code from the no-exact-match table."""
    return OperationsRepo(db_path=db_path).delete_align_no_exact_match(
        video_code,
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
from .db_history_write import _ALLOWED_STATUSES  # noqa: E402


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
# Canonical definitions moved to db_history_write.py; re-exported here for
# backwards compatibility.
from .db_history_write import (  # noqa: E402
    _PENDING_HREF_LOCKS_LOCK,
    _PENDING_HREF_LOCKS,
    _href_lock,
    _href_lookup_variants,
    _compute_indicators,
    _merge_movie_overlay_rows,
    _pending_movie_overlay,
    _merge_torrent_overlay_rows,
    _pending_torrent_overlay,
)

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
    """Append a row to PendingMovie/TorrentHistoryWrites. Delegates to HistoryRepo."""
    return HistoryRepo(db_path=db_path or HISTORY_DB_PATH).stage_history_write(
        session_id, kind, payload,
    )


def db_load_history_snapshot(
    session_id: Optional[str] = None,
    *,
    db_path: Optional[str] = None,
) -> Dict[str, dict]:
    """Return committed-live history with the *session_id* pending overlay."""
    return HistoryRepo(db_path=db_path).load_history_snapshot(session_id)


# ── Commit workflow — moved to db_history_write.py ──
from .db_history_write import (  # noqa: E402
    _commit_one_movie,
    _bulk_run,
    _chunked,
    _commit_session_bulk,
    _pending_distinct_hrefs,
    _d1_retry_pending_cleanup,
    db_commit_session_history,
    db_resume_finalizing_session,
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

    Returns per-table counts, supports dry-run, never touches other
    sessions' rows.
    """
    counts: Dict[str, int] = {
        "PendingMovieHistoryWrites": 0,
        "PendingTorrentHistoryWrites": 0,
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
    foreign-key like dependencies are unwound cleanly.  For pending-mode
    sessions the history scope deletes pending writes (in_progress) or
    resumes the commit (finalizing).

    *scope* may be one of ``'reports'``, ``'operations'``, ``'history'``,
    or ``'all'`` (default). Useful for partial rollbacks during incident
    response.

    *force=False* (default) refuses to operate on a session whose
    ``ReportSessions.Status='committed'`` to prevent accidental data loss
    on successful runs. Set ``force=True`` for explicit recovery
    scenarios (the manual workflow exposes this as an opt-in flag).

    *failure_reason* (optional): persisted to ``ReportSessions.
    FailureReason`` alongside ``Status='failed'`` so post-incident
    analysis can distinguish ``workflow_cancel`` / ``runtime_error`` /
    ``stale_timeout`` etc.  Defaults to no annotation when omitted.

    Marks the ``ReportSessions`` row ``Status='failed'`` BEFORE the
    deletions for traceability (committed sessions are intentionally
    skipped).

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

    # Pending-mode sessions already in 'finalizing' must NOT be flipped
    # to 'failed' before the dispatcher runs — that would reroute the
    # resume_commit branch into rollback_pending and silently lose the
    # in-flight commit.
    pre_state = db_get_session_status(
        session_id, db_path=reports_db_path,
    )
    pre_write_mode = pre_state[0] if pre_state else 'pending'
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
        # Dispatch on (WriteMode, Status).  Pending sessions either
        # DELETE pending writes (in_progress) or resume the commit
        # (finalizing).
        # NOTE: _rollback_reports above DELETEs the ReportSessions row,
        # so a fresh db_get_session_status() here would always return
        # None.  Reuse the snapshot we captured before any deletion ran.
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
            logger.warning(
                "Session %s has unexpected write_mode=%r — "
                "audit replay retired by ADR-005; skipping history rollback",
                session_id, write_mode,
            )
            result['history'] = {'mode': 'skipped', 'reason': 'audit_retired'}
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
    return StatsRepo(db_path=db_path or REPORTS_DB_PATH).save_spider_stats(
        session_id, stats,
    )


def db_save_uploader_stats(session_id: str, stats: dict, db_path: Optional[str] = None) -> int:
    """Save uploader statistics for a session (idempotent via ON CONFLICT)."""
    return StatsRepo(db_path=db_path or REPORTS_DB_PATH).save_uploader_stats(
        session_id, stats,
    )


def db_save_pikpak_stats(session_id: str, stats: dict, db_path: Optional[str] = None) -> int:
    """Save PikPak bridge statistics for a session (idempotent via ON CONFLICT)."""
    return StatsRepo(db_path=db_path or REPORTS_DB_PATH).save_pikpak_stats(
        session_id, stats,
    )


def db_get_spider_stats(session_id: str, db_path: Optional[str] = None) -> Optional[dict]:
    """Get spider stats for a session."""
    return StatsRepo(db_path=db_path or REPORTS_DB_PATH).get_spider_stats(
        session_id,
    )


def db_get_uploader_stats(session_id: str, db_path: Optional[str] = None) -> Optional[dict]:
    """Get uploader stats for a session."""
    return StatsRepo(db_path=db_path or REPORTS_DB_PATH).get_uploader_stats(
        session_id,
    )


def db_get_pikpak_stats(session_id: str, db_path: Optional[str] = None) -> Optional[dict]:
    """Get PikPak stats for a session."""
    return StatsRepo(db_path=db_path or REPORTS_DB_PATH).get_pikpak_stats(
        session_id,
    )


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
    return StatsRepo(db_path=db_path or REPORTS_DB_PATH).get_spider_stats_local(
        session_id,
    )


def db_get_uploader_stats_local(
    session_id: str, db_path: Optional[str] = None,
) -> Optional[dict]:
    """SQLite-only counterpart to :func:`db_get_uploader_stats`."""
    return StatsRepo(db_path=db_path or REPORTS_DB_PATH).get_uploader_stats_local(
        session_id,
    )


def db_get_pikpak_stats_local(
    session_id: str, db_path: Optional[str] = None,
) -> Optional[dict]:
    """SQLite-only counterpart to :func:`db_get_pikpak_stats`."""
    return StatsRepo(db_path=db_path or REPORTS_DB_PATH).get_pikpak_stats_local(
        session_id,
    )


def db_get_latest_session_local(
    report_type: Optional[str] = None, db_path: Optional[str] = None,
) -> Optional[dict]:
    """SQLite-only counterpart to :func:`db_get_latest_session`."""
    from javdb.storage.db.db_reports import (
        db_get_latest_session_local as _f,
    )
    return _f(report_type, db_path=db_path or REPORTS_DB_PATH)
