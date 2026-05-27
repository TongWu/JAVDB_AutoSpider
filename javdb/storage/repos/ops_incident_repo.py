"""Repository for ADR-026 OpsIncidents rows."""

from __future__ import annotations

import sqlite3
from typing import Any

from javdb.ops.diagnosis.models import OpsIncidentRecord


_COLUMNS = (
    "incident_id",
    "trigger_source",
    "run_id",
    "run_attempt",
    "session_id",
    "incident_type",
    "status",
    "persistence_status",
    "model_version",
    "detector_version",
    "bundle_schema_version",
    "confidence",
    "confirmed_findings_json",
    "likely_causes_json",
    "unknowns_json",
    "recommended_next_actions_json",
    "unsafe_actions_json",
    "evidence_refs_json",
    "created_at",
    "updated_at",
    "resolved_at",
)


def _row_get(row: Any, column: str):
    if isinstance(row, dict):
        return row[column]
    return row[column]


def _row_to_record(row: Any) -> OpsIncidentRecord:
    return OpsIncidentRecord(**{column: _row_get(row, column) for column in _COLUMNS})


class OpsIncidentRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        try:
            self._conn.row_factory = sqlite3.Row
        except Exception:
            pass

    def upsert(self, record: OpsIncidentRecord) -> None:
        values = [getattr(record, column) for column in _COLUMNS]
        placeholders = ", ".join(["?"] * len(_COLUMNS))
        columns = ", ".join(_COLUMNS)
        update_columns = [column for column in _COLUMNS if column != "incident_id"]
        updates = ", ".join([f"{column}=excluded.{column}" for column in update_columns])
        self._conn.execute(
            f"""
            INSERT INTO OpsIncidents ({columns})
            VALUES ({placeholders})
            ON CONFLICT(incident_id) DO UPDATE SET {updates}
            """,
            values,
        )

    def get(self, incident_id: str) -> OpsIncidentRecord | None:
        row = self._conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM OpsIncidents WHERE incident_id = ?",
            [incident_id],
        ).fetchone()
        return None if row is None else _row_to_record(row)

    def list(
        self,
        *,
        status: str | None = None,
        run_id: str | None = None,
        session_id: str | None = None,
        limit: int = 50,
    ) -> list[OpsIncidentRecord]:
        clauses: list[str] = []
        params: list[object] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)

        sql = f"SELECT {', '.join(_COLUMNS)} FROM OpsIncidents"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(limit, 100)))
        return [
            _row_to_record(row)
            for row in self._conn.execute(sql, params).fetchall()
        ]
