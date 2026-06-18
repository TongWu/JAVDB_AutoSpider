"""Repository for ADR-026 Phase 4 alert policy and alert event rows."""

from __future__ import annotations

import logging
import sqlite3

from javdb.ops.diagnosis.models import OpsAlertEvent, OpsAlertPolicy
from javdb.storage.contract import fragments, order_params

logger = logging.getLogger(__name__)

# Single source of truth: the ADR-055 contract registry. Re-exported for any
# back-compat importers; the SQL itself lives only in javdb/storage/contract.
# Policy reads/writes are cross-backend (Python here + the TS Worker mirror), so
# they execute the registry fragments verbatim. The event helpers below
# (upsert_event / claim_fired_event / mark_no_delivery) are Python-only and
# legitimately stay inline — no Worker counterpart, out of registry scope.
OPS_ALERT_POLICY_UPSERT_SQL = fragments.OPS_ALERT_POLICY_UPSERT.sql

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
        # created_at / updated_at are set by the DB clock (strftime) inside the
        # registry fragment, so both backends produce identical writes — the
        # dataclass timestamps are ignored on write (ADR-055 D6).
        self._conn.execute(
            fragments.OPS_ALERT_POLICY_UPSERT.sql,
            order_params(
                fragments.OPS_ALERT_POLICY_UPSERT,
                policy_id=policy.policy_id,
                incident_type=policy.incident_type,
                min_confidence=policy.min_confidence,
                enabled=1 if policy.enabled else 0,
                channels_json=policy.channels_json,
                updated_by=policy.updated_by,
            ),
        )

    def get_policy(self, incident_type: str) -> OpsAlertPolicy | None:
        row = self._conn.execute(
            fragments.OPS_ALERT_POLICY_GET_BY_INCIDENT_TYPE.sql,
            order_params(
                fragments.OPS_ALERT_POLICY_GET_BY_INCIDENT_TYPE,
                incident_type=incident_type,
            ),
        ).fetchone()
        return None if row is None else _row_to_policy(row)

    def list_policies(self) -> list[OpsAlertPolicy]:
        rows = self._conn.execute(
            fragments.OPS_ALERT_POLICIES_LIST.sql,
            order_params(fragments.OPS_ALERT_POLICIES_LIST),
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
            fragments.OPS_ALERT_EVENTS_LIST_BY_INCIDENT.sql,
            order_params(
                fragments.OPS_ALERT_EVENTS_LIST_BY_INCIDENT,
                incident_id=incident_id,
            ),
        ).fetchall()
        return [_row_to_event(row) for row in rows]
