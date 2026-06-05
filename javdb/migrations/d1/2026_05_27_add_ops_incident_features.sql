-- 2026-05-27: Add derived feature rows for ADR-026 Phase 2.
--
-- Apply with:
--   wrangler d1 execute javdb-reports --remote \
--     --file=javdb/migrations/d1/2026_05_27_add_ops_incident_features.sql
--
-- Write-Class: diagnostic
--
-- OpsIncidentFeatures stores compact, explainable similarity metadata.
-- It is derived from OpsIncidents and never stores full raw logs.

CREATE TABLE IF NOT EXISTS OpsIncidentFeatures (
  incident_id TEXT PRIMARY KEY,
  incident_type TEXT NOT NULL,
  status TEXT NOT NULL,
  confidence TEXT NOT NULL,
  workflow_name TEXT,
  run_id TEXT,
  run_attempt INTEGER,
  session_id TEXT,
  feature_version TEXT NOT NULL,
  categorical_features_json TEXT NOT NULL DEFAULT '{}',
  text_tokens_json TEXT NOT NULL DEFAULT '[]',
  unsafe_action_tokens_json TEXT NOT NULL DEFAULT '[]',
  evidence_kinds_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  FOREIGN KEY (incident_id) REFERENCES OpsIncidents(incident_id)
);

CREATE INDEX IF NOT EXISTS idx_ops_incident_features_type_status
  ON OpsIncidentFeatures(incident_type, status);

CREATE INDEX IF NOT EXISTS idx_ops_incident_features_workflow
  ON OpsIncidentFeatures(workflow_name);

CREATE INDEX IF NOT EXISTS idx_ops_incident_features_run
  ON OpsIncidentFeatures(run_id, run_attempt);

CREATE INDEX IF NOT EXISTS idx_ops_incident_features_session
  ON OpsIncidentFeatures(session_id);
