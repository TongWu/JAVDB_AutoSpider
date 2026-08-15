-- 2026-08-09: Add AcquisitionOutcome.state_changed_at (ADR-033 Phase 1 repair).
--
-- Apply with:
--   wrangler d1 execute javdb-operations --remote \
--     --file=javdb/migrations/d1/2026_08_09_add_acquisition_state_changed_at.sql
--
-- The acquisition trend grouped every terminal state by last_seen_at. For
-- 'stalled' and 'failed' that column is the last *successful* qB observation:
-- the reconciler waits stalled_after_days (7) / 2x stalled_after_days (14) past
-- that value and flips the state without touching it. A failure detected today
-- was therefore plotted up to two weeks in the past, and could fall out of a
-- 7-day trend window entirely.
--
-- state_changed_at is stamped by the reconciler whenever the state actually
-- changes, so the trend can group on when the transition happened rather than
-- on when the torrent was last seen alive.
--
-- Backfill: existing rows carry no transition history, so seed the closest
-- proxy available per state (landed_at / completed_at where the transition did
-- record a timestamp, else last_seen_at, else queued_at). Historical stalled and
-- failed points keep their current — imprecise — placement; only transitions
-- made after this migration are exact.

ALTER TABLE AcquisitionOutcome ADD COLUMN state_changed_at TEXT;

UPDATE AcquisitionOutcome
SET state_changed_at = COALESCE(landed_at, completed_at, last_seen_at, queued_at)
WHERE state_changed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_acq_outcome_state_changed
  ON AcquisitionOutcome(state_changed_at);
