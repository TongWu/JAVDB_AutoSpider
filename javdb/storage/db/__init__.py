"""javdb.storage.db — public package API.

Re-exports the most commonly used symbols from the shell modules so
callers can write::

    from javdb.storage.db import get_db, init_db, REPORTS_DB_PATH

instead of reaching into individual sub-modules.
"""

# ── Connection management ───────────────────────────────────────────────
from .db_connection import (  # noqa: F401
    get_db,
    get_local_sqlite_db,
    close_db,
    current_backend,
    HISTORY_DB_PATH,
    REPORTS_DB_PATH,
    OPERATIONS_DB_PATH,
    DB_PATH,
    SCHEMA_VERSION,
    _DB_OPERATIONAL_ERRORS,
    _DB_INTEGRITY_ERRORS,
    _execute_backend_batch,
    _row_to_jsonable_dict,
    _backend_mode,
    _local,
)

# ── Schema DDL & init ──────────────────────────────────────────────────
from .db_migrations import (  # noqa: F401
    init_db,
    _init_single_db,
    _OPERATIONS_DDL,
    moviehistory_actor_layout_ok,
    _ensure_rollback_columns,
)

# ── Session state ──────────────────────────────────────────────────────
from .db_session import (  # noqa: F401
    set_active_session_id,
    get_active_session_id,
    set_active_run_identity,
    get_active_run_identity,
    set_active_write_mode,
    get_active_write_mode,
    generate_session_id,
    generate_integer_id,
    SESSION_ID_PATTERN,
    _SESSION_ID_SENTINEL,
    _resolve_session_id,
)
