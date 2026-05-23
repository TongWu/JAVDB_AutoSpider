"""Migrations management endpoints.

GET  /api/migrations                    — list D1 SQL migration files + applied state
POST /api/migrations/{migration_id}/run — preview (and eventually run) a migration
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from apps.api.infra.auth import require_role
from apps.api.schemas.migrations import (
    MigrationItem,
    MigrationListResponse,
    RunMigrationRequest,
    RunMigrationResponse,
)

router = APIRouter(prefix="/api/migrations", tags=["migrations"])

_MIGRATIONS_DIR = Path("javdb/migrations/d1")


def _get_applied_migrations() -> dict[str, str]:
    """Query system_state for applied migration timestamps.

    Returns {migration_id: applied_at_timestamp}.
    """
    try:
        from javdb.storage.db._db_connection import get_db

        with get_db() as conn:
            rows = conn.execute(
                "SELECT key, value FROM system_state WHERE key LIKE 'migration_applied:%'"
            ).fetchall()
            return {
                row[0].removeprefix("migration_applied:"): row[1]
                for row in rows
            }
    except Exception:
        return {}


@router.get("/", response_model=MigrationListResponse)
def list_migrations(
    _user: Dict[str, Any] = Depends(require_role("admin")),
) -> MigrationListResponse:
    """List all D1 SQL migration files with their applied state."""
    if not _MIGRATIONS_DIR.exists():
        return MigrationListResponse(migrations=[])

    applied = _get_applied_migrations()
    files = sorted(_MIGRATIONS_DIR.glob("*.sql"))

    migrations = [
        MigrationItem(
            id=f.stem,
            filename=f.name,
            applied=f.stem in applied,
            applied_at=applied.get(f.stem),
        )
        for f in files
    ]
    return MigrationListResponse(migrations=migrations)


@router.post("/{migration_id}/run", response_model=RunMigrationResponse)
def run_migration(
    migration_id: str,
    body: RunMigrationRequest,
    _user: Dict[str, Any] = Depends(require_role("admin")),
) -> RunMigrationResponse:
    """Preview or run a migration."""
    migration_file = _MIGRATIONS_DIR / f"{migration_id}.sql"

    # Prevent path traversal before existence check
    try:
        migration_file.resolve().relative_to(_MIGRATIONS_DIR.resolve())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": "migrations.invalid_id",
                    "message": "Invalid migration ID",
                }
            },
        )

    if not migration_file.exists():
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "migrations.not_found",
                    "message": f"Migration '{migration_id}' not found",
                }
            },
        )

    sql = migration_file.read_text()
    statement_count = len([s for s in sql.split(";") if s.strip()])

    if body.dry_run:
        return RunMigrationResponse(
            migration_id=migration_id,
            dry_run=True,
            sql_preview=sql,
            statements=statement_count,
        )

    # Non-dry-run: not yet implemented
    raise HTTPException(
        status_code=501,
        detail={
            "error": {
                "code": "migrations.remote_execution_not_supported",
                "message": (
                    "Remote migration execution is not yet supported. "
                    "Run migrations via Wrangler CLI: "
                    "wrangler d1 execute <db> --file=javdb/migrations/d1/<file>.sql"
                ),
            }
        },
    )


__all__ = [
    "list_migrations",
    "run_migration",
    "router",
]
