from __future__ import annotations

import sqlite3

from javdb.ops.diagnosis.alerting import build_alert_id
from javdb.ops.diagnosis.models import OpsAlertEvent, OpsAlertPolicy
from javdb.storage.contract import fragments
from javdb.storage.repos import ops_alert_repo
from javdb.storage.repos.ops_alert_repo import OpsAlertRepo


DDL = """
CREATE TABLE OpsAlertPolicy (
  policy_id TEXT PRIMARY KEY,
  incident_type TEXT NOT NULL,
  min_confidence TEXT NOT NULL DEFAULT 'medium'
    CHECK (min_confidence IN ('low', 'medium', 'high')),
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
  channels_json TEXT NOT NULL DEFAULT '[]',
  updated_by TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_ops_alert_policy_incident_type
  ON OpsAlertPolicy(incident_type);

CREATE TABLE OpsAlertEvent (
  alert_id TEXT PRIMARY KEY,
  incident_id TEXT NOT NULL,
  policy_id TEXT,
  status TEXT NOT NULL DEFAULT 'fired'
    CHECK (status IN ('fired', 'suppressed', 'skipped')),
  reason TEXT,
  fired_at TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_ops_alert_event_incident
  ON OpsAlertEvent(incident_id);

CREATE INDEX idx_ops_alert_event_status
  ON OpsAlertEvent(status);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    return conn


def _policy(
    incident_type: str = "failed_ingestion",
    *,
    min_confidence: str = "medium",
    enabled: bool = True,
    channels: list[str] | None = None,
    updated_by: str | None = "admin",
) -> OpsAlertPolicy:
    return OpsAlertPolicy.create(
        incident_type=incident_type,
        min_confidence=min_confidence,
        enabled=enabled,
        channels=channels or ["email"],
        updated_by=updated_by,
    )


def _event(
    *,
    alert_id: str | None = None,
    incident_id: str = "opsinc_abc",
    policy_id: str | None = "opspolicy_x",
    status: str = "fired",
    reason: str | None = "sent",
    fired_at: str = "2026-06-13T00:00:00Z",
) -> OpsAlertEvent:
    return OpsAlertEvent(
        alert_id=alert_id or build_alert_id(incident_id),
        incident_id=incident_id,
        policy_id=policy_id,
        status=status,
        reason=reason,
        fired_at=fired_at,
    )


def test_upsert_policy_idempotently_inserts_and_updates_one_policy() -> None:
    repo = OpsAlertRepo(_conn())
    original = _policy(min_confidence="medium", channels=["email"])
    updated = OpsAlertPolicy(
        **{
            **original.__dict__,
            "min_confidence": "high",
            "enabled": False,
            "channels_json": '["email","telegram"]',
            "updated_by": "operator",
            "updated_at": "2026-06-13T01:00:00Z",
        }
    )

    repo.upsert_policy(original)
    # created_at / updated_at are server-clock (strftime in the ADR-055 registry
    # fragment), not the app-supplied dataclass values.
    inserted = repo.get_policy("failed_ingestion")
    assert inserted is not None
    repo.upsert_policy(updated)
    policies = repo.list_policies()

    assert len(policies) == 1
    assert policies[0].policy_id == original.policy_id
    assert policies[0].incident_type == "failed_ingestion"
    # created_at is set at insert and preserved across the conflict update.
    assert policies[0].created_at == inserted.created_at
    assert policies[0].min_confidence == "high"
    assert policies[0].enabled is False
    assert policies[0].channels_json == '["email","telegram"]'
    assert policies[0].updated_by == "operator"
    # The app-supplied updated_at is ignored; the DB stamps it instead.
    assert policies[0].updated_at != "2026-06-13T01:00:00Z"
    assert policies[0].updated_at


def test_upsert_policy_dedupes_by_incident_type_and_preserves_identity() -> None:
    repo = OpsAlertRepo(_conn())
    original = _policy()
    regenerated = OpsAlertPolicy(
        **{
            **original.__dict__,
            "policy_id": "opspolicy_regenerated",
            "min_confidence": "high",
            "updated_at": "2026-06-13T01:00:00Z",
        }
    )

    repo.upsert_policy(original)
    inserted = repo.get_policy(original.incident_type)
    assert inserted is not None
    repo.upsert_policy(regenerated)
    policies = repo.list_policies()

    assert len(policies) == 1
    assert policies[0].policy_id == original.policy_id
    assert policies[0].incident_type == original.incident_type
    # created_at is server-set and preserved across the conflict update.
    assert policies[0].created_at == inserted.created_at
    assert policies[0].min_confidence == "high"


def test_get_policy_returns_matching_policy_or_none() -> None:
    repo = OpsAlertRepo(_conn())
    repo.upsert_policy(_policy("d1_drift"))

    found = repo.get_policy("d1_drift")
    missing = repo.get_policy("stale_session")

    assert found is not None
    assert found.incident_type == "d1_drift"
    assert missing is None


def test_list_policies_orders_by_incident_type_ascending() -> None:
    repo = OpsAlertRepo(_conn())
    repo.upsert_policy(_policy("stale_session"))
    repo.upsert_policy(_policy("failed_ingestion"))
    repo.upsert_policy(_policy("d1_drift"))

    assert [policy.incident_type for policy in repo.list_policies()] == [
        "d1_drift",
        "failed_ingestion",
        "stale_session",
    ]


def test_upsert_event_inserts_and_lists_for_incident_ordered_by_fired_at() -> None:
    repo = OpsAlertRepo(_conn())
    later = _event(alert_id="opsalert_later", fired_at="2026-06-13T02:00:00Z")
    earlier = _event(
        alert_id="opsalert_earlier",
        incident_id="opsinc_def",
        fired_at="2026-06-13T01:00:00Z",
    )

    repo.upsert_event(later)
    repo.upsert_event(earlier)

    assert repo.list_events_for_incident("opsinc_def") == [earlier]
    assert repo.list_events_for_incident("opsinc_abc") == [later]


def test_upsert_event_dedupes_by_incident_id_not_alert_id() -> None:
    repo = OpsAlertRepo(_conn())
    later = _event(
        alert_id="opsalert_second",
        incident_id="opsinc_abc",
        policy_id="opspolicy_y",
        status="suppressed",
        reason="duplicate after policy evaluation",
        fired_at="2026-06-13T01:00:00Z",
    )

    repo.upsert_event(
        _event(
            alert_id="opsalert_first",
            incident_id="opsinc_abc",
            policy_id="opspolicy_x",
            status="skipped",
            reason="confidence below threshold",
            fired_at="2026-06-13T00:00:00Z",
        )
    )
    repo.upsert_event(later)

    events = repo.list_events_for_incident("opsinc_abc")
    assert len(events) == 1
    assert events[0] == later


def test_upsert_event_preserves_existing_fired_audit_row() -> None:
    repo = OpsAlertRepo(_conn())
    fired = _event(
        alert_id="opsalert_fired",
        incident_id="opsinc_abc",
        status="fired",
        reason="sent to email",
        fired_at="2026-06-13T00:00:00Z",
    )

    repo.upsert_event(fired)
    repo.upsert_event(
        _event(
            alert_id="opsalert_suppressed",
            incident_id="opsinc_abc",
            status="suppressed",
            reason="already fired",
            fired_at="2026-06-13T01:00:00Z",
        )
    )

    assert repo.list_events_for_incident("opsinc_abc") == [fired]


def test_upsert_event_promotes_existing_non_fired_row_to_fired() -> None:
    repo = OpsAlertRepo(_conn())
    skipped = _event(
        alert_id="opsalert_skipped",
        incident_id="opsinc_abc",
        status="skipped",
        reason="confidence below threshold",
        fired_at="2026-06-13T00:00:00Z",
    )
    fired = _event(
        alert_id="opsalert_fired",
        incident_id="opsinc_abc",
        status="fired",
        reason="sent after policy threshold changed",
        fired_at="2026-06-13T01:00:00Z",
    )

    repo.upsert_event(skipped)
    repo.upsert_event(fired)

    assert repo.list_events_for_incident("opsinc_abc") == [fired]


def test_claim_fired_event_allows_only_first_fired_claim() -> None:
    repo = OpsAlertRepo(_conn())
    first = _event(
        alert_id="opsalert_first",
        incident_id="opsinc_abc",
        status="fired",
        reason="first claim",
        fired_at="2026-06-13T00:00:00Z",
    )
    second = _event(
        alert_id="opsalert_second",
        incident_id="opsinc_abc",
        status="fired",
        reason="second claim",
        fired_at="2026-06-13T00:00:01Z",
    )

    assert repo.claim_fired_event(first) is True
    assert repo.claim_fired_event(second) is False

    assert repo.list_events_for_incident("opsinc_abc") == [first]


def test_claim_fired_event_promotes_existing_non_fired_row() -> None:
    repo = OpsAlertRepo(_conn())
    skipped = _event(
        alert_id="opsalert_skipped",
        incident_id="opsinc_abc",
        status="skipped",
        reason="no delivery",
        fired_at="2026-06-13T00:00:00Z",
    )
    fired = _event(
        alert_id="opsalert_fired",
        incident_id="opsinc_abc",
        status="fired",
        reason="policy matched after backend enabled",
        fired_at="2026-06-13T01:00:00Z",
    )

    repo.upsert_event(skipped)

    assert repo.claim_fired_event(fired) is True
    assert repo.list_events_for_incident("opsinc_abc") == [fired]


def test_mark_no_delivery_demotes_own_fired_claim_to_skipped() -> None:
    repo = OpsAlertRepo(_conn())
    fired = _event(
        alert_id="opsalert_fired",
        incident_id="opsinc_abc",
        status="fired",
        reason="claim before delivery",
        fired_at="2026-06-13T00:00:00Z",
    )
    no_delivery = _event(
        alert_id="opsalert_fired",
        incident_id="opsinc_abc",
        status="skipped",
        reason="no_delivery: dispatch returned no notify delivery results.",
        fired_at="2026-06-13T00:00:01Z",
    )

    assert repo.claim_fired_event(fired) is True
    repo.mark_no_delivery(no_delivery)

    assert repo.list_events_for_incident("opsinc_abc") == [no_delivery]


class _CapturingCursor:
    def fetchone(self) -> None:
        return None

    def fetchall(self) -> list:
        return []


class _CapturingConn:
    """Connection double that records the SQL each call executes."""

    def __init__(self) -> None:
        self.row_factory = None
        self.statements: list[str] = []

    # _params mirrors sqlite3.Connection.execute(sql, params) so the repo's
    # positional calls bind unchanged; the double only needs the SQL.
    def execute(self, sql: str, _params: tuple = ()) -> _CapturingCursor:
        self.statements.append(sql)
        return _CapturingCursor()


def test_cross_backend_statements_execute_the_adr055_registry_fragments() -> None:
    # ADR-055 D6: production Python executes the registry SQL verbatim for every
    # cross-backend statement, so the Python backend and the TS Worker mirror
    # issue byte-identical writes/reads. Pin the repo's SQL to the registry so a
    # regression back to hand-built inline SQL fails here.
    conn = _CapturingConn()
    repo = OpsAlertRepo(conn)

    repo.upsert_policy(_policy())
    repo.get_policy("failed_ingestion")
    repo.list_policies()
    repo.list_events_for_incident("opsinc_abc")

    assert conn.statements == [
        fragments.OPS_ALERT_POLICY_UPSERT.sql,
        fragments.OPS_ALERT_POLICY_GET_BY_INCIDENT_TYPE.sql,
        fragments.OPS_ALERT_POLICIES_LIST.sql,
        fragments.OPS_ALERT_EVENTS_LIST_BY_INCIDENT.sql,
    ]
    # The re-exported pin stays anchored to the registry, mirroring how
    # WatchIntentRepo pins WATCH_INTENT_UPSERT_SQL = fragments.WATCH_INTENT_UPSERT.sql.
    assert (
        ops_alert_repo.OPS_ALERT_POLICY_UPSERT_SQL
        is fragments.OPS_ALERT_POLICY_UPSERT.sql
    )
