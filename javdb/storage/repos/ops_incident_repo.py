"""Repository for ADR-026 OpsIncidents rows."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from javdb.ops.diagnosis.models import OpsIncidentFeatures, OpsIncidentRecord


logger = logging.getLogger(__name__)

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


_FEATURE_COLUMNS = (
    "incident_id",
    "incident_type",
    "status",
    "confidence",
    "workflow_name",
    "run_id",
    "run_attempt",
    "session_id",
    "feature_version",
    "categorical_features_json",
    "text_tokens_json",
    "unsafe_action_tokens_json",
    "evidence_kinds_json",
    "created_at",
    "updated_at",
)


def _row_get(row: Any, column: str):
    return row[column]


def _row_to_record(row: Any) -> OpsIncidentRecord:
    return OpsIncidentRecord(**{column: _row_get(row, column) for column in _COLUMNS})


def _row_to_features(row: sqlite3.Row) -> OpsIncidentFeatures:
    return OpsIncidentFeatures(**{column: row[column] for column in _FEATURE_COLUMNS})


class OpsIncidentRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        try:
            self._conn.row_factory = sqlite3.Row
        except Exception:
            logger.debug("Failed to set row_factory on ops incident connection", exc_info=True)

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
        incident_type: str | None = None,
        confidence: str | None = None,
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
        if incident_type:
            clauses.append("incident_type = ?")
            params.append(incident_type)
        if confidence:
            clauses.append("confidence = ?")
            params.append(confidence)

        sql = f"SELECT {', '.join(_COLUMNS)} FROM OpsIncidents"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, limit))
        return [
            _row_to_record(row)
            for row in self._conn.execute(sql, params).fetchall()
        ]

    def upsert_features(self, features: OpsIncidentFeatures) -> None:
        values = [getattr(features, column) for column in _FEATURE_COLUMNS]
        placeholders = ", ".join(["?"] * len(_FEATURE_COLUMNS))
        columns = ", ".join(_FEATURE_COLUMNS)
        update_columns = [column for column in _FEATURE_COLUMNS if column != "incident_id"]
        updates = ", ".join([f"{column}=excluded.{column}" for column in update_columns])
        self._conn.execute(
            f"""
            INSERT INTO OpsIncidentFeatures ({columns})
            VALUES ({placeholders})
            ON CONFLICT(incident_id) DO UPDATE SET {updates}
            """,
            values,
        )

    def get_features(self, incident_id: str) -> OpsIncidentFeatures | None:
        row = self._conn.execute(
            f"SELECT {', '.join(_FEATURE_COLUMNS)} FROM OpsIncidentFeatures WHERE incident_id = ?",
            [incident_id],
        ).fetchone()
        return None if row is None else _row_to_features(row)

    def list_features(self, *, limit: int = 500) -> list[OpsIncidentFeatures]:
        rows = self._conn.execute(
            f"SELECT {', '.join(_FEATURE_COLUMNS)} FROM OpsIncidentFeatures ORDER BY updated_at DESC LIMIT ?",
            [limit],
        ).fetchall()
        return [_row_to_features(row) for row in rows]
