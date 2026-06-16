-- 2026-06-13: Add ADR-026 Phase 4 proactive alerting tables.
-- Write-Class: diagnostic
--
-- Apply with:
--   wrangler d1 execute javdb-reports --remote \
--     --file=javdb/migrations/d1/2026_06_13_add_ops_alert_tables.sql
--
-- OpsAlertPolicy is operator-tunable. OpsAlertEvent is a dedupe + audit ledger
-- of what alerted. Neither table delivers notifications; delivery stays in
-- ADR-039 NotifyPlugin dispatch.

CREATE TABLE IF NOT EXISTS OpsAlertPolicy (
  policy_id TEXT PRIMARY KEY,
  incident_type TEXT NOT NULL,
  min_confidence TEXT NOT NULL DEFAULT 'medium'
    CHECK (min_confidence IN ('low', 'medium', 'high')),
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
  channels_json TEXT NOT NULL DEFAULT '[]',
  updated_by TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ops_alert_policy_incident_type
  ON OpsAlertPolicy(incident_type);

CREATE TABLE IF NOT EXISTS OpsAlertEvent (
  alert_id TEXT PRIMARY KEY,
  incident_id TEXT NOT NULL,
  policy_id TEXT,
  status TEXT NOT NULL DEFAULT 'fired'
    CHECK (status IN ('fired', 'suppressed', 'skipped')),
  reason TEXT,
  fired_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  FOREIGN KEY (incident_id) REFERENCES OpsIncidents(incident_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ops_alert_event_incident
  ON OpsAlertEvent(incident_id);

CREATE INDEX IF NOT EXISTS idx_ops_alert_event_status
  ON OpsAlertEvent(status);
