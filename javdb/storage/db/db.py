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
        _execute_backend_batch(conn, [insert_movie])
    else:
        movie_id = existing['Id']
        if (
            actor_name is not None
            or actor_gender is not None
            or actor_link is not None
            or supporting_actors is not None
        ):
            row_m = conn.execute(
                "SELECT * FROM MovieHistory WHERE Id=?", (movie_id,),
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
        _execute_backend_batch(conn, [update_movie])

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
            _execute_backend_batch(conn, [insert_torrent])
        else:
            update_torrent = (
                """UPDATE TorrentHistory
                   SET MagnetUri=?, Size=?, FileCount=?, ResolutionType=?,
                       DateTimeUpdated=?, SessionId=?
                   WHERE Id=?""",
                (magnet, size, fc, res, now, sid, existing_t['Id']),
            )
            _execute_backend_batch(conn, [update_torrent])

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
    session_id: Optional[str],
    when: Optional[str],
) -> None:
    """Delete TorrentHistory rows matching ``(movie_id, sub_ind, cen_ind)``."""
    conn.execute(
        "DELETE FROM TorrentHistory WHERE MovieHistoryId=? "
        "AND SubtitleIndicator=? AND CensorIndicator=?",
        (movie_id, sub_ind, cen_ind),
    )


def _update_movie_indicators(
    conn,
    movie_id: int,
    *,
    session_id: Optional[str] = None,
    when: Optional[str] = None,
):
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

    perfect_val = 1 if perfect else 0
    hires_val = 1 if hires else 0

    if session_id is not None:
        conn.execute(
            """UPDATE MovieHistory SET PerfectMatchIndicator=?,
               HiResIndicator=?, SessionId=? WHERE Id=?""",
            (perfect_val, hires_val, session_id, movie_id),
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
    value), each affected MovieHistory row also gets ``SessionId=?``.

    Ingestion Perfect Rollback (Phase 2): when the active session runs
    under ``WriteMode='pending'`` the visit timestamps are staged into
    :data:`PendingMovieHistoryWrites` (a sparse "DateTimeVisited only"
    row per href) and applied to live in :func:`db_commit_session_history`.
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
    CHUNK = 90
    total = 0
    with get_db(db_path or HISTORY_DB_PATH) as conn:
        for i in range(0, len(lookup_hrefs), CHUNK):
            chunk = lookup_hrefs[i:i + CHUNK]
            placeholders = ','.join('?' for _ in chunk)
            if sid is not None:
                cur = conn.execute(
                    f"UPDATE MovieHistory SET DateTimeVisited=?, SessionId=? "
                    f"WHERE Href IN ({placeholders})",
                    tuple([now, sid] + chunk),
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
    value), each affected MovieHistory row also gets ``SessionId=?``.

    Ingestion Perfect Rollback (Phase 2): pending-mode sessions stage a
    sparse "actor fields only" pending movie row per href instead of
    UPDATE-ing live; commit merges with the earlier stages from the same
    session.
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
    return HistoryRepo(db_path=db_path or HISTORY_DB_PATH).stage_history_write(
        session_id, kind, payload,
    )


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
    return HistoryRepo(db_path=db_path).load_history_snapshot(session_id)


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
    # wins over no_subtitle" rule enforced in _upsert_one_history_on_conn.
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

    # Recompute derived indicators directly.
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
