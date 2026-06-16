"""Repository for ADR-026 Phase 4 alert policy and alert event rows."""

from __future__ import annotations

import logging
import sqlite3

from javdb.ops.diagnosis.models import OpsAlertEvent, OpsAlertPolicy

logger = logging.getLogger(__name__)

_POLICY_COLUMNS = (
    "policy_id",
    "incident_type",
    "min_confidence",
    "enabled",
    "channels_json",
    "updated_by",
    "created_at",
    "updated_at",
)

_EVENT_COLUMNS = (
    "alert_id",
    "incident_id",
    "policy_id",
    "status",
    "reason",
    "fired_at",
)


def _row_to_policy(row: sqlite3.Row) -> OpsAlertPolicy:
    data = {column: row[column] for column in _POLICY_COLUMNS}
    data["enabled"] = bool(data["enabled"])
    return OpsAlertPolicy(**data)


def _row_to_event(row: sqlite3.Row) -> OpsAlertEvent:
    return OpsAlertEvent(**{column: row[column] for column in _EVENT_COLUMNS})


class OpsAlertRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        try:
            self._conn.row_factory = sqlite3.Row
        except (AttributeError, TypeError):
            logger.debug("row_factory set failed", exc_info=True)

    def upsert_policy(self, policy: OpsAlertPolicy) -> None:
        values = [
            policy.policy_id,
            policy.incident_type,
            policy.min_confidence,
            1 if policy.enabled else 0,
            policy.channels_json,
            policy.updated_by,
            policy.created_at,
            policy.updated_at,
        ]
        columns = ", ".join(_POLICY_COLUMNS)
        placeholders = ", ".join(["?"] * len(_POLICY_COLUMNS))
        updates = ", ".join(
            f"{column}=excluded.{column}"
            for column in _POLICY_COLUMNS
            if column not in ("policy_id", "incident_type", "created_at")
        )
        self._conn.execute(
            f"""
            INSERT INTO OpsAlertPolicy ({columns})
            VALUES ({placeholders})
            ON CONFLICT(incident_type) DO UPDATE SET {updates}
            """,
            values,
        )

    def get_policy(self, incident_type: str) -> OpsAlertPolicy | None:
        row = self._conn.execute(
            f"""
            SELECT {', '.join(_POLICY_COLUMNS)}
            FROM OpsAlertPolicy
            WHERE incident_type = ?
            """,
            [incident_type],
        ).fetchone()
        return None if row is None else _row_to_policy(row)

    def list_policies(self) -> list[OpsAlertPolicy]:
        rows = self._conn.execute(
            f"""
            SELECT {', '.join(_POLICY_COLUMNS)}
            FROM OpsAlertPolicy
            ORDER BY incident_type ASC
            """
        ).fetchall()
        return [_row_to_policy(row) for row in rows]

    def upsert_event(self, event: OpsAlertEvent) -> None:
        values = [getattr(event, column) for column in _EVENT_COLUMNS]
        columns = ", ".join(_EVENT_COLUMNS)
        placeholders = ", ".join(["?"] * len(_EVENT_COLUMNS))
        updates = ", ".join(f"{column}=excluded.{column}" for column in _EVENT_COLUMNS)
        self._conn.execute(
            f"""
            INSERT INTO OpsAlertEvent ({columns})
            VALUES ({placeholders})
            ON CONFLICT(incident_id) DO UPDATE SET {updates}
            -- Preserve the first fired audit row for an incident.
            WHERE status != 'fired'
            """,
            values,
        )

    def claim_fired_event(self, event: OpsAlertEvent) -> bool:
        """Atomically claim alert delivery for an incident.

        Returns True only for the process that inserted/promoted the incident to
        ``fired``. Existing fired rows keep the incident suppressed.
        """
        values = [getattr(event, column) for column in _EVENT_COLUMNS]
        columns = ", ".join(_EVENT_COLUMNS)
        placeholders = ", ".join(["?"] * len(_EVENT_COLUMNS))
        cursor = self._conn.execute(
            f"""
            INSERT INTO OpsAlertEvent ({columns})
            VALUES ({placeholders})
            ON CONFLICT(incident_id) DO UPDATE SET
                alert_id=excluded.alert_id,
                policy_id=excluded.policy_id,
                status=excluded.status,
                reason=excluded.reason,
                fired_at=excluded.fired_at
            WHERE OpsAlertEvent.status != 'fired'
            """,
            values,
        )
        return cursor.rowcount > 0

    def mark_no_delivery(self, event: OpsAlertEvent) -> None:
        self._conn.execute(
            """
            UPDATE OpsAlertEvent
            SET status = 'skipped',
                reason = ?,
                fired_at = ?
            WHERE incident_id = ?
              AND alert_id = ?
              AND status = 'fired'
            """,
            [event.reason, event.fired_at, event.incident_id, event.alert_id],
        )

    def list_events_for_incident(self, incident_id: str) -> list[OpsAlertEvent]:
        rows = self._conn.execute(
            f"""
            SELECT {', '.join(_EVENT_COLUMNS)}
            FROM OpsAlertEvent
            WHERE incident_id = ?
            ORDER BY fired_at ASC
            """,
            [incident_id],
        ).fetchall()
        return [_row_to_event(row) for row in rows]
