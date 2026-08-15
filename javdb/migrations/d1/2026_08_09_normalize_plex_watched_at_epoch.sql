-- 2026-08-09: Normalize Plex ConsumptionSignal.watched_at epochs (ADR-033 Phase 3).
--
-- Apply with:
--   wrangler d1 execute javdb-operations --remote \
--     --file=javdb/migrations/d1/2026_08_09_normalize_plex_watched_at_epoch.sql
--
-- The Plex adapter stored Plex's numeric `lastViewedAt` epoch verbatim (e.g.
-- '1712345678') instead of the UTC ISO timestamp ConsumptionSignal expects. The
-- consumption trend compares watched_at against a 'YYYY-MM-DD' cutoff and groups
-- with substr(watched_at, 1, 10), so those rows were either excluded from the
-- trend or surfaced under an invalid day key ('1712345678' sorts above any real
-- date, so they can also pollute a window they do not belong in).
--
-- The adapter now converts on write; this repairs the rows already stored.
-- Match on shape rather than on source_type alone: an all-digit value is
-- unambiguously an epoch (a real value always contains '-' from the date), so
-- the statement is idempotent and cannot touch an already-normalized row.

-- Two guards beyond the digits-only shape, both mirroring the adapter:
--   * `> 0` — epoch 0 means "never viewed", not 1970-01-01. Without it a stored
--     '0' becomes a real-looking day in the trend.
--   * `strftime(...) IS NOT NULL` — an out-of-range epoch makes strftime return
--     NULL, and writing that back would destroy the original value instead of
--     leaving the odd row alone for inspection.
UPDATE ConsumptionSignal
SET watched_at = strftime('%Y-%m-%dT%H:%M:%SZ', CAST(watched_at AS INTEGER), 'unixepoch')
WHERE watched_at IS NOT NULL
  AND watched_at <> ''
  AND watched_at NOT GLOB '*[^0-9]*'
  AND CAST(watched_at AS INTEGER) > 0
  AND strftime('%Y-%m-%dT%H:%M:%SZ', CAST(watched_at AS INTEGER), 'unixepoch')
      IS NOT NULL;
