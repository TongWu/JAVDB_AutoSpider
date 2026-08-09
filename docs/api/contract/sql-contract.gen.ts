// AUTO-GENERATED from javdb/storage/contract — DO NOT EDIT.
// Source of truth: ADR-055. Regenerate: python3 -m apps.cli.ops.dump_sql_contract
// version: 963af73ee22d0b70
/* eslint-disable */

export const WATCH_INTENT_UPSERT_SQL =
  `INSERT INTO WatchIntent (video_code, href, status, notes, status_at, updated_at) VALUES (?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), strftime('%Y-%m-%dT%H:%M:%fZ','now')) ON CONFLICT(video_code) DO UPDATE SET href = excluded.href, status = excluded.status, notes = COALESCE(excluded.notes, notes), status_at = strftime('%Y-%m-%dT%H:%M:%fZ','now'), updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')`;

export function prepareWatchIntentUpsert(
  db: D1Database,
  p: { videoCode: string; href: string; status: string; notes: string | null },
): D1PreparedStatement {
  return db.prepare(WATCH_INTENT_UPSERT_SQL).bind(p.videoCode, p.href, p.status, p.notes);
}

export const ACTOR_SUBSCRIPTION_UPSERT_SQL =
  `INSERT INTO ActorSubscription (actor_href, actor_name, active, created_at, updated_at) VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), strftime('%Y-%m-%dT%H:%M:%fZ','now')) ON CONFLICT(actor_href) DO UPDATE SET actor_name = excluded.actor_name, active = excluded.active, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')`;

export function prepareActorSubscriptionUpsert(
  db: D1Database,
  p: { actorHref: string; actorName: string | null; active: number },
): D1PreparedStatement {
  return db.prepare(ACTOR_SUBSCRIPTION_UPSERT_SQL).bind(p.actorHref, p.actorName, p.active);
}

export const SYSTEM_STATE_UPSERT_SQL =
  `INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, datetime('now')) ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now')`;

export function prepareSystemStateUpsert(
  db: D1Database,
  p: { key: string; value: string },
): D1PreparedStatement {
  return db.prepare(SYSTEM_STATE_UPSERT_SQL).bind(p.key, p.value);
}

export const OPS_ALERT_POLICY_UPSERT_SQL =
  `INSERT INTO OpsAlertPolicy ( policy_id, incident_type, min_confidence, enabled, channels_json, updated_by, created_at, updated_at ) VALUES ( ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), strftime('%Y-%m-%dT%H:%M:%fZ','now') ) ON CONFLICT(incident_type) DO UPDATE SET min_confidence = excluded.min_confidence, enabled = excluded.enabled, channels_json = excluded.channels_json, updated_by = excluded.updated_by, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')`;

export function prepareOpsAlertPolicyUpsert(
  db: D1Database,
  p: { policyId: string; incidentType: string; minConfidence: string; enabled: number; channelsJson: string; updatedBy: string | null },
): D1PreparedStatement {
  return db.prepare(OPS_ALERT_POLICY_UPSERT_SQL).bind(p.policyId, p.incidentType, p.minConfidence, p.enabled, p.channelsJson, p.updatedBy);
}

export const OPS_ALERT_POLICY_GET_BY_INCIDENT_TYPE_SQL =
  `SELECT policy_id, incident_type, min_confidence, enabled, channels_json, updated_by, created_at, updated_at FROM OpsAlertPolicy WHERE incident_type = ?`;

export function prepareOpsAlertPolicyGetByIncidentType(
  db: D1Database,
  p: { incidentType: string },
): D1PreparedStatement {
  return db.prepare(OPS_ALERT_POLICY_GET_BY_INCIDENT_TYPE_SQL).bind(p.incidentType);
}

export const OPS_ALERT_POLICIES_LIST_SQL =
  `SELECT policy_id, incident_type, min_confidence, enabled, channels_json, updated_by, created_at, updated_at FROM OpsAlertPolicy ORDER BY incident_type ASC`;

export function prepareOpsAlertPoliciesList(
  db: D1Database,
  _p?: Record<string, never>,
): D1PreparedStatement {
  return db.prepare(OPS_ALERT_POLICIES_LIST_SQL).bind();
}

export const OPS_ALERT_EVENTS_LIST_BY_INCIDENT_SQL =
  `SELECT alert_id, incident_id, policy_id, status, reason, fired_at FROM OpsAlertEvent WHERE incident_id = ? ORDER BY fired_at ASC`;

export function prepareOpsAlertEventsListByIncident(
  db: D1Database,
  p: { incidentId: string },
): D1PreparedStatement {
  return db.prepare(OPS_ALERT_EVENTS_LIST_BY_INCIDENT_SQL).bind(p.incidentId);
}

export const OPS_ALERT_POLICY_PROBE_SQL =
  `SELECT 1 FROM OpsAlertPolicy LIMIT 1`;

export function prepareOpsAlertPolicyProbe(
  db: D1Database,
  _p?: Record<string, never>,
): D1PreparedStatement {
  return db.prepare(OPS_ALERT_POLICY_PROBE_SQL).bind();
}

export const OPS_ALERT_EVENT_PROBE_SQL =
  `SELECT 1 FROM OpsAlertEvent LIMIT 1`;

export function prepareOpsAlertEventProbe(
  db: D1Database,
  _p?: Record<string, never>,
): D1PreparedStatement {
  return db.prepare(OPS_ALERT_EVENT_PROBE_SQL).bind();
}

export const OPS_ALERT_POLICY_ID_SALT = "alertpolicy|";

export const OPS_ALERT_POLICY_ID_PREFIX = "opspolicy_";

export const OPS_ALERT_POLICY_ID_HASH_LENGTH = 24;

export const VALID_RULE_MODES: ReadonlySet<string> = new Set([
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
]);

export const VALUE_REQUIRED: ReadonlySet<string> = new Set([
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
]);

export const REPORT_SESSION_COLUMNS: readonly string[] = [
  "Id",
  "Status",
  "WriteMode",
  "RunId",
  "RunAttempt",
  "DateTimeCreated",
  "ReportType",
  "ReportDate",
  "FailureReason",
];
