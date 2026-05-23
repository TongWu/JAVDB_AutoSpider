"""Database connection management for JAVDB AutoSpider.

Handles connection pooling, backend routing (SQLite/D1/Dual), and WAL mode setup.

The active storage backend is controlled by the STORAGE_BACKEND environment variable:
- 'sqlite' (default) — Local SQLite files only
- 'd1' — Cloudflare D1 only (GitHub Actions)
- 'dual' — Both SQLite and D1 with drift detection

Connections are cached per-thread and per-backend to avoid repeated setup overhead.
"""

import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, List, Optional, Tuple

from javdb.infra.config import cfg
from javdb.infra.logging import get_logger

logger = get_logger(__name__)

# ── Database paths ───────────────────────────────────────────────────────

_REPORTS_DIR = cfg('REPORTS_DIR', 'reports')

HISTORY_DB_PATH = cfg('HISTORY_DB_PATH', os.path.join(_REPORTS_DIR, 'history.db'))
REPORTS_DB_PATH = cfg('REPORTS_DB_PATH', os.path.join(_REPORTS_DIR, 'reports.db'))
OPERATIONS_DB_PATH = cfg('OPERATIONS_DB_PATH', os.path.join(_REPORTS_DIR, 'operations.db'))

# Legacy single-DB path — kept for migration source detection
DB_PATH = cfg('SQLITE_DB_PATH', os.path.join(_REPORTS_DIR, 'javdb_autospider.db'))

SCHEMA_VERSION = 14

# Logical-name mapping for D1 / dual backends
_DB_PATH_TO_LOGICAL_NAME = {
    HISTORY_DB_PATH: 'history',
    REPORTS_DB_PATH: 'reports',
    OPERATIONS_DB_PATH: 'operations',
}

# ── Backend-agnostic error tuples ────────────────────────────────────────
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
# deployment without the d1_client deps still loads db_connection.py.
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

# ── Shared utility functions ────────────────────────────────────────────


def _execute_backend_batch(conn, statements: List[Tuple[str, Tuple[Any, ...]]]):
    """Execute a list of SQL statements, using D1's batch_execute when available."""
    if not statements:
        return []
    batch = getattr(conn, "batch_execute", None)
    if callable(batch):
        return batch(statements)
    cursors = []
    for sql, params in statements:
        cursors.append(conn.execute(sql, params))
    return cursors


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


# ── Thread-local connection cache ────────────────────────────────────────

_local = threading.local()


# ── Backend mode resolution ──────────────────────────────────────────────


def _backend_mode() -> str:
    """Resolve the active storage backend.

    STORAGE_BACKEND env var (or config.STORAGE_BACKEND) selects between:

    * 'sqlite' (default) — original behaviour, local files only.
    * 'd1' — all reads/writes go to Cloudflare D1.
    * 'dual' — writes mirror to both SQLite and D1; reads come from D1
      (used during migration validation).

    During init_db under the 'dual' backend we temporarily downgrade
    to sqlite-only init so the DDL plumbing only touches the local file.
    That override lives in a thread-local (_local._storage_backend_init_override);
    init_db deliberately does NOT mirror it into the process-wide environment
    because doing so would leak the override to sibling threads.

    The _STORAGE_BACKEND_INIT_OVERRIDE env var is still consulted as a
    deliberate escape hatch for callers (e.g. external scripts or test
    harnesses) that want to force sqlite-only behaviour for an entire process.
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
    """Public alias of _backend_mode() for use in non-db modules.

    Returns one of 'sqlite', 'd1', 'dual'. Useful when callers
    want to log or branch on the configured storage backend without
    importing private helpers.
    """
    return _backend_mode()


def _logical_name_for(db_path: str) -> str:
    """Map a db_path to its D1 logical name.

    Args:
        db_path: Database file path (e.g., 'reports/history.db')

    Returns:
        Logical name for D1 (e.g., 'history')

    Raises:
        ValueError: If db_path has no D1 mapping
    """
    name = _DB_PATH_TO_LOGICAL_NAME.get(db_path)
    if name is None:
        raise ValueError(
            f"No D1 logical-name mapping for db_path={db_path!r}. "
            "Add it to _DB_PATH_TO_LOGICAL_NAME or use STORAGE_BACKEND=sqlite."
        )
    return name


# ── SQLite connection helpers ────────────────────────────────────────────


def _is_valid_sqlite(path: str) -> bool:
    """Check if a file is a valid SQLite database."""
    try:
        with open(path, 'rb') as f:
            header = f.read(16)
        return header[:16] == b'SQLite format 3\x00'
    except Exception:
        return False


def _open_sqlite_connection(db_path: str) -> sqlite3.Connection:
    """Open and configure a fresh local SQLite connection.

    Args:
        db_path: Path to SQLite database file

    Returns:
        Configured sqlite3.Connection with WAL mode enabled

    Raises:
        sqlite3.DatabaseError: If file exists but is not a valid SQLite database
    """
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


# ── Connection pooling ───────────────────────────────────────────────────


def _get_connection(db_path: str):
    """Return a thread-local connection for db_path, creating it if needed.

    Multiple connections (one per distinct path) are cached per thread.

    Honours STORAGE_BACKEND to return either a plain sqlite3.Connection
    (default), a D1Connection, or a DualConnection mirroring writes
    across both backends.

    Args:
        db_path: Database file path

    Returns:
        Connection object (sqlite3.Connection, D1Connection, or DualConnection)

    Raises:
        RuntimeError: If STORAGE_BACKEND is unknown
    """
    conns: dict = getattr(_local, 'conns', None)
    if conns is None:
        conns = {}
        _local.conns = conns

    backend = _backend_mode()

    # Key the cache on (db_path, backend) so a runtime flip of
    # STORAGE_BACKEND returns the right connection type instead of
    # a stale facade from before the switch.
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


# ── Public connection APIs ───────────────────────────────────────────────


@contextmanager
def get_db(db_path: Optional[str] = None):
    """Context manager yielding a database connection with auto-commit.

    Args:
        db_path: Database path (defaults to HISTORY_DB_PATH)

    Yields:
        Connection object (type depends on STORAGE_BACKEND)

    Example:
        with get_db(REPORTS_DB_PATH) as conn:
            conn.execute("INSERT INTO ...")
            # Auto-commits on exit, rolls back on exception
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
    """Context manager that always yields a raw sqlite3.Connection.

    Under STORAGE_BACKEND=dual the default get_db() returns a DualConnection
    whose reads are served by D1. That is the right behaviour for the
    application's hot path, but several observability code paths (email
    notification, drift reconciler, operator dashboards) MUST read the
    locally-canonical state instead so they do not paper over D1 lag.

    This helper opens a dedicated SQLite connection irrespective of the
    configured backend, with auto-commit on exit. The connection is NOT
    cached in the thread-local registry.

    Args:
        db_path: Database path (defaults to HISTORY_DB_PATH)

    Yields:
        sqlite3.Connection (always local SQLite, never D1)

    Example:
        # Force read from local SQLite even in dual mode
        with get_local_sqlite_db(REPORTS_DB_PATH) as conn:
            row = conn.execute("SELECT * FROM SpiderStats WHERE SessionId = ?", (sid,)).fetchone()
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


def verify_d1_schema_versions() -> None:
    """Fail fast if any D1 database is behind the expected schema version.

    Only meaningful when STORAGE_BACKEND is 'd1' or 'dual'. Under 'sqlite'
    this is a no-op (local init_db handles migrations directly).

    Raises:
        SystemExit: If any D1 DB reports a SchemaVersion < SCHEMA_VERSION.
    """
    backend = _backend_mode()
    if backend not in ('d1', 'dual'):
        return

    from javdb.storage.d1_client import make_d1_connection

    failed: list[tuple[str, int]] = []
    for logical_name in ('history', 'reports', 'operations'):
        d1 = None
        try:
            d1 = make_d1_connection(logical_name)
            cur = d1.execute(
                "SELECT Version FROM SchemaVersion LIMIT 1"
            )
            row = cur.fetchone()
            ver = 0
            if row is not None:
                ver = int(row[0] if not isinstance(row, dict) else row.get('Version', 0))
            if ver < SCHEMA_VERSION:
                failed.append((logical_name, ver))
        except Exception as exc:
            logger.warning(
                "Could not check D1 schema version for %s: %s",
                logical_name, exc,
            )
        finally:
            if d1 is not None:
                try:
                    d1.close()
                except Exception:
                    pass

    if failed:
        for name, ver in failed:
            logger.error(
                "D1 %s schema version %d < required %d. "
                "Apply pending migrations before running the pipeline.",
                name, ver, SCHEMA_VERSION,
            )
        raise SystemExit(1)


def close_db():
    """Close all thread-local connections (call before process exit).

    Performs WAL checkpoint on SQLite connections to flush pending writes.
    D1 and Dual connections are closed without checkpoint (not applicable).
    """
    conns: dict = getattr(_local, 'conns', None)
    if not conns:
        return
    for key, conn in list(conns.items()):
        # Cache keys are (db_path, backend) tuples (see _get_connection).
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
