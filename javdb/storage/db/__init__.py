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
    verify_d1_schema_versions,
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
    _init_single_legacy_db,
    _HISTORY_DDL,
    _REPORTS_DDL,
    _OPERATIONS_DDL,
    moviehistory_actor_layout_ok,
    _ensure_rollback_columns,
    _normalize_moviehistory_actor_column_order,
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
    _resolve_write_mode,
    _INT_ID_EPOCH_BASE_MS,
)

# ── History reads ──────────────────────────────────────────────────────
from .db_history_read import (  # noqa: F401
    db_load_history,
    db_load_history_snapshot,
    db_check_torrent_in_history,
    db_get_all_history_records,
)

# ── History writes ─────────────────────────────────────────────────────
from .db_history_write import (  # noqa: F401
    db_stage_history_write,
    db_commit_session_history,
    db_resume_finalizing_session,
    db_batch_update_last_visited,
    db_batch_update_movie_actors,
    _compute_indicators,
    _pending_torrent_overlay,
    _commit_one_movie,
)

# ── Report sessions ────────────────────────────────────────────────────
from .db_reports import (  # noqa: F401
    db_create_report_session,
    db_get_session_status,
    db_insert_report_rows,
    db_find_stale_pending_sessions,
    db_find_in_progress_sessions,
    db_count_in_progress_sessions_for_run,
    db_find_in_progress_session_ids_for_run_csv,
    db_find_sessions_by_run,
    db_get_session_run_identity,
    db_mark_session_committed,
    db_mark_session_failed,
    db_get_latest_session_local,
    db_pending_session_stats,
    db_get_report_rows,
    db_get_latest_session,
    db_get_sessions_by_date,
    db_begin_finalize_session,
    db_finish_commit_session,
)

# ── Operations (rclone / dedup / pikpak / align) ───────────────────────
from .db_operations import (  # noqa: F401
    db_replace_rclone_inventory,
    db_load_rclone_inventory,
    db_append_rclone_inventory,
    db_clear_rclone_inventory,
    db_delete_rclone_inventory_paths,
    db_open_rclone_staging,
    db_append_rclone_staging,
    db_swap_rclone_inventory,
    db_merge_rclone_inventory_from_stage,
    db_drop_rclone_staging,
    db_save_dedup_records,
    db_load_dedup_records,
    db_append_dedup_record,
    db_append_pikpak_history,
    db_mark_records_deleted,
    db_mark_orphan_records,
    db_cleanup_deleted_records,
    db_upsert_align_no_exact_match,
    db_load_align_no_exact_match_codes,
    db_delete_align_no_exact_match,
)

# ── Stats ──────────────────────────────────────────────────────────────
from .db_stats import (  # noqa: F401
    db_save_spider_stats,
    db_get_spider_stats,
    db_get_spider_stats_local,
    db_save_uploader_stats,
    db_get_uploader_stats,
    db_get_uploader_stats_local,
    db_save_pikpak_stats,
    db_get_pikpak_stats,
    db_get_pikpak_stats_local,
)

# ── Rollback ───────────────────────────────────────────────────────────
from .db_rollback import (  # noqa: F401
    db_rollback_session,
    _session_id_to_identifier_suffix,
)

# ── Public API surface ─────────────────────────────────────────────────
__all__ = [
    # db_connection
    "get_db",
    "get_local_sqlite_db",
    "close_db",
    "current_backend",
    "HISTORY_DB_PATH",
    "REPORTS_DB_PATH",
    "OPERATIONS_DB_PATH",
    "DB_PATH",
    "SCHEMA_VERSION",
    "verify_d1_schema_versions",
    # db_migrations
    "init_db",
    "moviehistory_actor_layout_ok",
    # db_session
    "set_active_session_id",
    "get_active_session_id",
    "set_active_run_identity",
    "get_active_run_identity",
    "set_active_write_mode",
    "get_active_write_mode",
    "generate_session_id",
    "generate_integer_id",
    "SESSION_ID_PATTERN",
    "_resolve_write_mode",
    # db_history_read
    "db_load_history",
    "db_load_history_snapshot",
    "db_check_torrent_in_history",
    "db_get_all_history_records",
    # db_history_write
    "db_stage_history_write",
    "db_commit_session_history",
    "db_resume_finalizing_session",
    "db_batch_update_last_visited",
    "db_batch_update_movie_actors",
    # db_reports
    "db_create_report_session",
    "db_get_session_status",
    "db_insert_report_rows",
    "db_find_stale_pending_sessions",
    "db_find_in_progress_sessions",
    "db_find_in_progress_session_ids_for_run_csv",
    "db_find_sessions_by_run",
    "db_get_session_run_identity",
    "db_mark_session_committed",
    "db_mark_session_failed",
    "db_get_latest_session_local",
    "db_pending_session_stats",
    # db_operations
    "db_replace_rclone_inventory",
    "db_load_rclone_inventory",
    "db_append_rclone_inventory",
    "db_clear_rclone_inventory",
    "db_delete_rclone_inventory_paths",
    "db_open_rclone_staging",
    "db_append_rclone_staging",
    "db_swap_rclone_inventory",
    "db_merge_rclone_inventory_from_stage",
    "db_drop_rclone_staging",
    "db_save_dedup_records",
    "db_load_dedup_records",
    "db_append_dedup_record",
    "db_append_pikpak_history",
    "db_mark_records_deleted",
    "db_mark_orphan_records",
    "db_cleanup_deleted_records",
    "db_upsert_align_no_exact_match",
    "db_load_align_no_exact_match_codes",
    "db_delete_align_no_exact_match",
    # db_stats
    "db_save_spider_stats",
    "db_get_spider_stats",
    "db_get_spider_stats_local",
    "db_save_uploader_stats",
    "db_get_uploader_stats",
    "db_get_uploader_stats_local",
    "db_save_pikpak_stats",
    "db_get_pikpak_stats",
    "db_get_pikpak_stats_local",
    # db_rollback
    "db_rollback_session",
]
