"""Static SQL fragment registry (ADR-055). The single hand-edited source.

Each entry is mirrored to the TS Worker by apps/cli/ops/dump_sql_contract.py.
Add new static cross-backend mutations / static selects here, never by hand in
the other repo.
"""
from __future__ import annotations

from javdb.storage.contract.types import Param, SharedConstant, SqlFragment

WATCH_INTENT_UPSERT = SqlFragment(
    name="watch_intent_upsert",
    db="history",
    sql="""
    INSERT INTO WatchIntent (video_code, href, status, notes, status_at, updated_at)
    VALUES (?, ?, ?, ?,
        strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    ON CONFLICT(video_code) DO UPDATE SET
        href       = excluded.href,
        status     = excluded.status,
        notes      = COALESCE(excluded.notes, notes),
        status_at  = strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
""",
    params=(
        Param("video_code", "str", "string"),
        Param("href", "str", "string"),
        Param("status", "str", "string"),
        Param("notes", "str | None", "string | null"),
    ),
)

ACTOR_SUBSCRIPTION_UPSERT = SqlFragment(
    name="actor_subscription_upsert",
    db="history",
    sql="""
    INSERT INTO ActorSubscription (actor_href, actor_name, active, created_at, updated_at)
    VALUES (?, ?, ?,
        strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        strftime('%Y-%m-%dT%H:%M:%fZ','now'))
    ON CONFLICT(actor_href) DO UPDATE SET
        actor_name = excluded.actor_name,
        active     = excluded.active,
        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
""",
    params=(
        Param("actor_href", "str", "string"),
        Param("actor_name", "str | None", "string | null"),
        Param("active", "int", "number"),
    ),
)

SYSTEM_STATE_UPSERT = SqlFragment(
    name="system_state_upsert",
    db="operations",
    sql="""
    INSERT INTO system_state (key, value, updated_at)
    VALUES (?, ?, datetime('now'))
    ON CONFLICT(key) DO UPDATE SET
        value = excluded.value,
        updated_at = datetime('now')
""",
    params=(
        Param("key", "str", "string"),
        Param("value", "str", "string"),
    ),
)

OPS_ALERT_POLICY_UPSERT = SqlFragment(
    name="ops_alert_policy_upsert",
    db="reports",
    sql="""
    INSERT INTO OpsAlertPolicy (
        policy_id,
        incident_type,
        min_confidence,
        enabled,
        channels_json,
        updated_by,
        created_at,
        updated_at
    )
    VALUES (
        ?,
        ?,
        ?,
        ?,
        ?,
        ?,
        strftime('%Y-%m-%dT%H:%M:%fZ','now'),
        strftime('%Y-%m-%dT%H:%M:%fZ','now')
    )
    ON CONFLICT(incident_type) DO UPDATE SET
        min_confidence = excluded.min_confidence,
        enabled        = excluded.enabled,
        channels_json  = excluded.channels_json,
        updated_by     = excluded.updated_by,
        updated_at     = strftime('%Y-%m-%dT%H:%M:%fZ','now')
""",
    params=(
        Param("policy_id", "str", "string"),
        Param("incident_type", "str", "string"),
        Param("min_confidence", "str", "string"),
        Param("enabled", "int", "number"),
        Param("channels_json", "str", "string"),
        Param("updated_by", "str | None", "string | null"),
    ),
)

OPS_ALERT_POLICY_GET_BY_INCIDENT_TYPE = SqlFragment(
    name="ops_alert_policy_get_by_incident_type",
    db="reports",
    sql="""
    SELECT
        policy_id,
        incident_type,
        min_confidence,
        enabled,
        channels_json,
        updated_by,
        created_at,
        updated_at
    FROM OpsAlertPolicy
    WHERE incident_type = ?
""",
    params=(Param("incident_type", "str", "string"),),
)

OPS_ALERT_POLICIES_LIST = SqlFragment(
    name="ops_alert_policies_list",
    db="reports",
    sql="""
    SELECT
        policy_id,
        incident_type,
        min_confidence,
        enabled,
        channels_json,
        updated_by,
        created_at,
        updated_at
    FROM OpsAlertPolicy
    ORDER BY incident_type ASC
""",
    params=(),
)

OPS_ALERT_EVENTS_LIST_BY_INCIDENT = SqlFragment(
    name="ops_alert_events_list_by_incident",
    db="reports",
    sql="""
    SELECT
        alert_id,
        incident_id,
        policy_id,
        status,
        reason,
        fired_at
    FROM OpsAlertEvent
    WHERE incident_id = ?
    ORDER BY fired_at ASC
""",
    params=(Param("incident_id", "str", "string"),),
)

OPS_ALERT_POLICY_PROBE = SqlFragment(
    name="ops_alert_policy_probe",
    db="reports",
    sql="SELECT 1 FROM OpsAlertPolicy LIMIT 1",
    params=(),
)

OPS_ALERT_EVENT_PROBE = SqlFragment(
    name="ops_alert_event_probe",
    db="reports",
    sql="SELECT 1 FROM OpsAlertEvent LIMIT 1",
    params=(),
)

OPS_ALERT_POLICY_ID_SALT = SharedConstant(
    name="ops_alert_policy_id_salt",
    kind="string",
    values="alertpolicy|",
)

OPS_ALERT_POLICY_ID_PREFIX = SharedConstant(
    name="ops_alert_policy_id_prefix",
    kind="string",
    values="opspolicy_",
)

OPS_ALERT_POLICY_ID_HASH_LENGTH = SharedConstant(
    name="ops_alert_policy_id_hash_length",
    kind="number",
    values=24,
)

VALID_RULE_MODES = SharedConstant(
    name="valid_rule_modes",
    kind="string_set",
    values=(
        "actor:exclude",
        "tag:exclude",
        "tag:include",
        "gender:require_lead",
        "gender:exclude_all_male",
        "age:min_age",
        "age:max_age",
        "actor:regex_exclude",
        "actor:regex_include",
        "tag:regex_exclude",
        "tag:regex_include",
        "release_date:before",
        "release_date:after",
    ),
)

VALUE_REQUIRED = SharedConstant(
    name="value_required",
    kind="string_set",
    values=(
        "actor:exclude",
        "tag:exclude",
        "tag:include",
        "gender:require_lead",
        "age:min_age",
        "age:max_age",
        "actor:regex_exclude",
        "actor:regex_include",
        "tag:regex_exclude",
        "tag:regex_include",
        "release_date:before",
        "release_date:after",
    ),
)

REPORT_SESSION_COLUMNS = SharedConstant(
    name="report_session_columns",
    kind="string_array",
    values=(
        "Id",
        "Status",
        "WriteMode",
        "RunId",
        "RunAttempt",
        "DateTimeCreated",
        "ReportType",
        "ReportDate",
        "FailureReason",
    ),
)

FRAGMENTS: tuple[SqlFragment, ...] = (
    WATCH_INTENT_UPSERT,
    ACTOR_SUBSCRIPTION_UPSERT,
    SYSTEM_STATE_UPSERT,
    OPS_ALERT_POLICY_UPSERT,
    OPS_ALERT_POLICY_GET_BY_INCIDENT_TYPE,
    OPS_ALERT_POLICIES_LIST,
    OPS_ALERT_EVENTS_LIST_BY_INCIDENT,
    OPS_ALERT_POLICY_PROBE,
    OPS_ALERT_EVENT_PROBE,
)

CONSTANTS: tuple[SharedConstant, ...] = (
    OPS_ALERT_POLICY_ID_SALT,
    OPS_ALERT_POLICY_ID_PREFIX,
    OPS_ALERT_POLICY_ID_HASH_LENGTH,
    VALID_RULE_MODES,
    VALUE_REQUIRED,
    REPORT_SESSION_COLUMNS,
)
