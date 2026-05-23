"""Rollback coordinator for JAVDB AutoSpider.

Coordinates rollback operations across all databases (history, reports, operations).

Uses pending mode rollback: delete from Pending* tables for uncommitted sessions,
or resume commit for sessions stuck in 'finalizing'.

Dedup rollback helpers snapshot pre-update rows into per-session backup tables
so that ``_rollback_operations`` can restore them if the session fails.
"""

import json
import re
from typing import Any, Dict, Optional, Tuple

from javdb.infra.logging import get_logger

logger = get_logger(__name__)

# Lazy imports to avoid circular dependencies
_get_db = None
_HISTORY_DB_PATH = None
_REPORTS_DB_PATH = None
_OPERATIONS_DB_PATH = None


def _ensure_imports():
    """Lazy import to avoid circular dependency."""
    global _get_db, _HISTORY_DB_PATH, _REPORTS_DB_PATH, _OPERATIONS_DB_PATH
    if _get_db is None:
        from javdb.storage.db._db_connection import (
            get_db,
            HISTORY_DB_PATH,
            REPORTS_DB_PATH,
            OPERATIONS_DB_PATH,
        )
        _get_db = get_db
        _HISTORY_DB_PATH = HISTORY_DB_PATH
        _REPORTS_DB_PATH = REPORTS_DB_PATH
        _OPERATIONS_DB_PATH = OPERATIONS_DB_PATH


# ── Dedup rollback helpers ──────────────────────────────────────────────

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


# ── Rollback sub-scopes ─────────────────────────────────────────────────


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
    _ensure_imports()
    counts: Dict[str, int] = {
        "PendingMovieHistoryWrites": 0,
        "PendingTorrentHistoryWrites": 0,
    }
    with _get_db(db_path or _HISTORY_DB_PATH) as conn:
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


def _rollback_reports(
    session_id: str,
    *,
    dry_run: bool,
    db_path: Optional[str] = None,
) -> Dict[str, int]:
    """Delete all reports-DB rows tagged with *session_id*.

    Returns a dict of ``{table: rows_affected}`` for logging / dry-run.
    """
    _ensure_imports()
    counts: Dict[str, int] = {}
    with _get_db(db_path or _REPORTS_DB_PATH) as conn:
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
    _ensure_imports()
    counts: Dict[str, int] = {}
    staging_table = f"RcloneInventoryStaging_{_session_id_to_identifier_suffix(session_id)}"
    dedup_backup_table = _dedup_rollback_table(session_id)
    with _get_db(db_path or _OPERATIONS_DB_PATH) as conn:
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


# ── Rollback coordinator ────────────────────────────────────────────────


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
    _ensure_imports()
    from javdb.storage.db._db_reports import (
        db_get_session_status,
        db_mark_session_failed,
    )
    from javdb.storage.db._db_history_write import db_resume_finalizing_session

    if scope not in ('reports', 'operations', 'history', 'all'):
        raise ValueError(
            f"Unknown rollback scope {scope!r}; "
            "expected one of reports/operations/history/all"
        )

    # Refuse to roll back committed sessions unless explicitly forced.
    with _get_db(reports_db_path or _REPORTS_DB_PATH) as conn:
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
    if (
        pre_write_mode == 'pending'
        and pre_status == 'finalizing'
        and scope in ('reports', 'history', 'all')
    ):
        if not auto_resume_finalizing:
            raise ValueError(
                f"Refusing to roll back ReportSessions.Id={session_id}: "
                "pending-mode session is in Status='finalizing' and "
                "auto_resume_finalizing=False. Pass "
                "--auto-resume-finalizing to drive it to committed instead, "
                "or --force-fail-finalizing to give up."
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
        return result

    if scope in ('reports', 'all'):
        result['reports'] = _rollback_reports(
            session_id, dry_run=dry_run, db_path=reports_db_path,
        )
    if scope in ('operations', 'all'):
        result['operations'] = _rollback_operations(
            session_id, dry_run=dry_run, db_path=operations_db_path,
        )
    if scope in ('history', 'all'):
        # Dispatch on the pre-rollback snapshot.  Pending finalizing
        # sessions are resumed above before report rows can be deleted;
        # remaining pending sessions are in_progress rollbacks.
        # NOTE: _rollback_reports above DELETEs the ReportSessions row,
        # so a fresh db_get_session_status() here would always return
        # None.  Reuse the snapshot we captured before any deletion ran.
        write_mode = pre_write_mode
        sess_status = pre_status
        if write_mode == 'pending':
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


def db_resume_finalizing_session(session_id, **kwargs):
    """Resume a session stuck in 'finalizing' status. Delegates to db_history_write."""
    from javdb.storage.db._db_history_write import db_resume_finalizing_session as _f
    return _f(session_id, **kwargs)
