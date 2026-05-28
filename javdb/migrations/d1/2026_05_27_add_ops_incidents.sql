-- 2026-05-27: Add OpsIncidents table (ADR-026 Phase 1).
--
-- Apply with:
--   wrangler d1 execute javdb-reports --remote \
--     --file=javdb/migrations/d1/2026_05_27_add_ops_incidents.sql
--
-- OpsIncidents stores structured diagnosis summaries and evidence pointers.
-- It does not store full raw workflow logs.

CREATE TABLE IF NOT EXISTS OpsIncidents (
  incident_id TEXT PRIMARY KEY,
  trigger_source TEXT NOT NULL,
  run_id TEXT,
  run_attempt INTEGER,
  session_id TEXT,
  incident_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open'
    CHECK (status IN ('open', 'acknowledged', 'resolved', 'dismissed')),
  persistence_status TEXT NOT NULL DEFAULT 'd1_written',
  model_version TEXT NOT NULL,
  detector_version TEXT NOT NULL,
  bundle_schema_version TEXT NOT NULL,
  confidence TEXT NOT NULL DEFAULT 'low'
    CHECK (confidence IN ('low', 'medium', 'high')),
  confirmed_findings_json TEXT NOT NULL DEFAULT '[]',
  likely_causes_json TEXT NOT NULL DEFAULT '[]',
  unknowns_json TEXT NOT NULL DEFAULT '[]',
  recommended_next_actions_json TEXT NOT NULL DEFAULT '[]',
  unsafe_actions_json TEXT NOT NULL DEFAULT '[]',
  evidence_refs_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_ops_incidents_created
  ON OpsIncidents(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ops_incidents_run
  ON OpsIncidents(run_id, run_attempt);

CREATE INDEX IF NOT EXISTS idx_ops_incidents_session
  ON OpsIncidents(session_id);

CREATE INDEX IF NOT EXISTS idx_ops_incidents_status_type
  ON OpsIncidents(status, incident_type);
