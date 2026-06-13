-- 2026-05-27: Add ADR-026 gated remediation proposal ledger.
--
-- Apply with:
--   wrangler d1 execute javdb-reports --remote \
--     --file=javdb/migrations/d1/2026_05_27_add_ops_remediation_proposals.sql
--
-- Write-Class: diagnostic
--
-- This table records suggestions and human decisions. It does not execute
-- rollback, rerun, drift apply, qB cleanup, or recovery mutation.

CREATE TABLE IF NOT EXISTS OpsRemediationProposals (
  proposal_id TEXT PRIMARY KEY,
  incident_id TEXT NOT NULL,
  action_type TEXT NOT NULL
    CHECK (action_type IN (
      'open_runbook',
      'prepare_rollback_workflow',
      'prepare_rerun_workflow',
      'prepare_drift_apply_command',
      'inspect_qb_side_effects',
      'inspect_recovery_outbox'
    )),
  status TEXT NOT NULL DEFAULT 'proposed'
    CHECK (status IN ('proposed', 'approved', 'rejected', 'expired')),
  safety_level TEXT NOT NULL
    CHECK (safety_level IN ('safe_to_prepare', 'requires_review', 'blocked')),
  title TEXT NOT NULL,
  rationale TEXT NOT NULL,
  command_preview TEXT,
  runbook_ref TEXT,
  evidence_refs_json TEXT NOT NULL DEFAULT '[]',
  required_checks_json TEXT NOT NULL DEFAULT '[]',
  blocked_reasons_json TEXT NOT NULL DEFAULT '[]',
  proposed_by TEXT NOT NULL DEFAULT 'adr026-policy-v1',
  decided_by TEXT,
  decision_note TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  decided_at TEXT,
  FOREIGN KEY (incident_id) REFERENCES OpsIncidents(incident_id)
);

CREATE INDEX IF NOT EXISTS idx_ops_remediation_incident
  ON OpsRemediationProposals(incident_id);

CREATE INDEX IF NOT EXISTS idx_ops_remediation_status
  ON OpsRemediationProposals(status);

CREATE INDEX IF NOT EXISTS idx_ops_remediation_action_type
  ON OpsRemediationProposals(action_type);
