"""Completeness guard for session-scoped rollback coverage.

A whole class of orphan-row bug recurs whenever a new table tagged with a
session id is added (e.g. ADR-033 AcquisitionOutcome, ADR-035 ParseRunFieldFill,
ADR-036 PipelineEvent) without deciding how rollback should treat it. If it is
neither cleared nor an explicit append-only/durable exception, a failed run
leaves its rows orphaned once the ReportSessions parent is deleted.

This test fails the moment such a table appears in the schema, forcing the
author to wire it into ``javdb.storage.db._db_rollback`` (cleared) or add it to
``ROLLBACK_PRESERVED_TABLES`` (a conscious "keep it" decision).
"""

from __future__ import annotations

from javdb.storage.db import get_db
from javdb.storage.db._db_rollback import (
    ROLLBACK_REPORTS_TABLES,
    ROLLBACK_OPERATIONS_TABLES,
    ROLLBACK_HISTORY_PENDING_TABLES,
    ROLLBACK_PRESERVED_TABLES,
)

_SESSION_COLUMNS = {"SessionId", "session_id"}

# Per-session ephemeral tables created/dropped during a run (RcloneInventory
# staging, DedupRecords rollback backups). They are not part of the static
# schema and are torn down by rollback itself, so they are out of scope here.
_EPHEMERAL_PREFIXES = ("RcloneInventoryStaging_", "DedupRecordsRollback_")


def _session_tagged_tables() -> dict[str, str]:
    """{table: session-id column} for every static table in the unified test
    schema that carries a SessionId / session_id column."""
    found: dict[str, str] = {}
    with get_db() as conn:
        tables = [
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        for table in tables:
            if table.startswith(_EPHEMERAL_PREFIXES):
                continue
            cols = {c["name"] for c in conn.execute(f"PRAGMA table_info({table})")}
            session_col = cols & _SESSION_COLUMNS
            if session_col:
                found[table] = next(iter(session_col))
    return found


def test_every_session_tagged_table_has_a_rollback_decision():
    accounted = (
        set(ROLLBACK_REPORTS_TABLES)
        | set(ROLLBACK_OPERATIONS_TABLES)
        | set(ROLLBACK_HISTORY_PENDING_TABLES)
        | set(ROLLBACK_PRESERVED_TABLES)
    )
    tagged = _session_tagged_tables()
    unaccounted = set(tagged) - accounted
    assert not unaccounted, (
        "Session-tagged tables with no rollback decision: "
        f"{sorted(unaccounted)}. Either clear them in _db_rollback "
        "(_rollback_reports/_rollback_operations) or add them to "
        "ROLLBACK_PRESERVED_TABLES (an explicit append-only/durable keep)."
    )


def test_coverage_constants_use_the_actual_session_column():
    """The column recorded for each cleared table must match the schema, or the
    generated ``DELETE ... WHERE <col>=?`` would silently match nothing."""
    tagged = _session_tagged_tables()
    for mapping in (
        ROLLBACK_REPORTS_TABLES,
        ROLLBACK_OPERATIONS_TABLES,
        ROLLBACK_HISTORY_PENDING_TABLES,
    ):
        for table, col in mapping.items():
            assert tagged.get(table) == col, (
                f"{table}: rollback uses column {col!r} but the schema has "
                f"{tagged.get(table)!r}"
            )
