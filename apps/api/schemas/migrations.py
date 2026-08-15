"""Pydantic schemas for Migrations endpoints."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class MigrationItem(BaseModel):
    id: str
    filename: str
    applied: bool
    applied_at: Optional[str] = None


class MigrationListResponse(BaseModel):
    migrations: list[MigrationItem]


class RunMigrationRequest(BaseModel):
    dry_run: bool = True
    # Operator assertion that this migration has NOT already been applied out of
    # band. The ledger only records what this endpoint applied, so for any file
    # without a marker "already applied?" is genuinely unknown — see
    # migrations.unrecorded. A migration this endpoint did apply is stopped
    # earlier by migrations.already_applied and never needs the flag.
    acknowledge_unrecorded: bool = False


class RunMigrationResponse(BaseModel):
    migration_id: str
    dry_run: bool
    sql_preview: str
    statements: int
    applied: Optional[bool] = None


__all__ = [
    "MigrationItem",
    "MigrationListResponse",
    "RunMigrationRequest",
    "RunMigrationResponse",
]
