-- 2026-06-19: Add TorrentProbeCandidate queue table (ADR-024 IMP-10).
-- Write-Class: additive
--
-- Additive (ADR-042 D6): a replayable, rebuildable shadow work-queue of
-- runner-up magnet candidates. It sits OUTSIDE the Pending->Commit session flow
-- and never determines whether a session commits -- losing it just means the
-- next ingestion re-captures runner-ups.
--
-- Apply with:
--   wrangler d1 execute javdb-reports --remote \
--     --file=javdb/migrations/d1/2026_06_19_add_torrent_probe_candidate.sql
--
-- ADR-024 IMP-10: queue of runner-up magnet candidates awaiting a metadata-only
-- probe on the remote quality_probe qBittorrent endpoint. D1 is the source of
-- truth (the SQLite mirror is rebuilt from this DDL).
--
-- One row per (info_hash, movie_href): the same runner-up under the same movie
-- page is enqueued once, and re-capture UPSERTs are idempotent. Note for editors:
-- keep the semicolon character out of these comments, because the D1 apply path
-- (D1Connection.executescript) splits the script on that character.
CREATE TABLE IF NOT EXISTS TorrentProbeCandidate (
    info_hash        TEXT NOT NULL,
    movie_href       TEXT NOT NULL,
    video_code       TEXT,
    javdb_category   TEXT,
    magnet_uri       TEXT NOT NULL,
    magnet_name      TEXT,
    javdb_tags_json  TEXT,
    javdb_size_text  TEXT,
    status           TEXT NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending', 'probed', 'failed')),
    enqueued_at      TEXT NOT NULL,
    probed_at        TEXT,
    PRIMARY KEY (info_hash, movie_href)
);

CREATE INDEX IF NOT EXISTS idx_torrent_probe_candidate_status
    ON TorrentProbeCandidate(status);
