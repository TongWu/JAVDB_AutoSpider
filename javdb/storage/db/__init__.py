"""javdb.storage.db — public package API.

Re-exports the most commonly used symbols from the shell modules so
callers can write::

    from javdb.storage.db import get_db, init_db, REPORTS_DB_PATH

instead of reaching into individual sub-modules.
"""

# ── Connection management ───────────────────────────────────────────────
from ._db_connection import (  # noqa: F401
    get_db,
    get_local_sqlite_db,
    close_db,
    current_backend,
    HISTORY_DB_PATH,
    REPORTS_DB_PATH,
    OPERATIONS_DB_PATH,
    DB_PATH,
    SCHEMA_VERSION,
    verify_d1_schema_versions,
    _DB_OPERATIONAL_ERRORS,
    _DB_INTEGRITY_ERRORS,
    _execute_backend_batch,
    _row_to_jsonable_dict,
    _backend_mode,
    _local,
)

# ── Schema DDL & init ──────────────────────────────────────────────────
from ._db_migrations import (  # noqa: F401
    init_db,
    _init_single_db,
    _init_single_legacy_db,
    _HISTORY_DDL,
    _REPORTS_DDL,
    _OPERATIONS_DDL,
    moviehistory_actor_layout_ok,
    _ensure_rollback_columns,
    _normalize_moviehistory_actor_column_order,
)

# ── Session state ──────────────────────────────────────────────────────
from ._db_session import (  # noqa: F401
    set_active_run_identity,
    get_active_run_identity,
    set_active_write_mode,
    get_active_write_mode,
    generate_session_id,
    generate_integer_id,
    SESSION_ID_PATTERN,
    _resolve_write_mode,
    _INT_ID_EPOCH_BASE_MS,
)

# ── History writes (private helpers re-exported for internal consumers) ─
from ._db_history_write import (  # noqa: F401
    _compute_indicators,
    _pending_torrent_overlay,
    _commit_one_movie,
)

# ── Rollback (private helper re-exported for internal consumers) ───────
from ._db_rollback import (  # noqa: F401
    _session_id_to_identifier_suffix,
)

# ── Public API surface ─────────────────────────────────────────────────
__all__ = [
    "DB_PATH",
    "HISTORY_DB_PATH",
    "OPERATIONS_DB_PATH",
    "REPORTS_DB_PATH",
    "SCHEMA_VERSION",
    "SESSION_ID_PATTERN",
    "close_db",
    "current_backend",
    "generate_integer_id",
    "generate_session_id",
    "get_active_run_identity",
    "get_active_write_mode",
    "get_db",
    "get_local_sqlite_db",
    "init_db",
    "moviehistory_actor_layout_ok",
    "set_active_run_identity",
    "set_active_write_mode",
    "verify_d1_schema_versions",
]
