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
