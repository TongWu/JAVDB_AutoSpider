-- 2026-05-31: Add TorrentQualityEvidence + TorrentQualityEvaluation tables (ADR-024 Phase 1).
-- Write-Class: additive
--
-- Additive (ADR-042 D6): neither table participates in the Pending->Commit
-- session flow (see below), so no write here decides whether a session commits
-- -- that is what puts both outside `authoritative`, and they are not drift /
-- recovery observability either, which rules out `diagnostic`.
--
-- Rebuildability differs between the two, and the class does not claim
-- otherwise. TorrentQualityEvidence is re-derivable by re-probing the torrent.
-- TorrentQualityEvaluation is derived from that evidence by scoring, but a
-- re-score repopulates it at the CURRENT scoring_version -- it does not
-- reproduce historical rows, so the decision / would_replace_current_choice /
-- shadow_rank / timestamp values behind list_needs_review(),
-- list_recent_evaluations() and the quality API are not recoverable verbatim
-- once dropped. Back it up like state you cannot re-derive; the write class
-- governs the commit boundary, not the retention policy.
--
-- Apply with:
--   wrangler d1 execute javdb-reports --remote \
--     --file=javdb/migrations/d1/2026_05_31_add_torrent_quality_tables.sql
--
-- Both tables sit OUTSIDE the Pending->Commit session flow. Rows are written
-- via direct UPSERT in shadow mode. TorrentQualityEvidence holds torrent-level
-- objective facts keyed by (info_hash, probe_schema_version, target_role).
-- TorrentQualityEvaluation holds movie-context shadow scoring keyed by
-- (info_hash, movie_href, scoring_version). The policy_mode / decision /
-- would_replace_current_choice / shadow_rank columns are reserved for the
-- ADR-024 Phase 2 (assist) and Phase 3 (enforce) rollouts; Phase 1 only writes
-- shadow values.

CREATE TABLE IF NOT EXISTS TorrentQualityEvidence (
    info_hash             TEXT NOT NULL,
    probe_schema_version  TEXT NOT NULL,
    target_role           TEXT NOT NULL,
    probe_target_name     TEXT,
    metadata_status       TEXT,
    metadata_started_at   TEXT,
    metadata_completed_at TEXT,
    total_size_bytes      INTEGER,
    main_video_size_bytes INTEGER,
    main_video_ratio      REAL,
    video_file_count      INTEGER,
    subtitle_file_count   INTEGER,
    non_video_file_count  INTEGER,
    junk_size_bytes       INTEGER,
    junk_size_ratio       REAL,
    suspicious_file_count INTEGER,
    features_json         TEXT,
    reasons_json          TEXT,
    source_fingerprint    TEXT,
    created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (info_hash, probe_schema_version, target_role)
);

CREATE INDEX IF NOT EXISTS idx_torrent_quality_evidence_created_at
    ON TorrentQualityEvidence(created_at);

CREATE TABLE IF NOT EXISTS TorrentQualityEvaluation (
    info_hash                   TEXT NOT NULL,
    movie_href                  TEXT NOT NULL,
    scoring_version             TEXT NOT NULL,
    video_code                  TEXT,
    javdb_category              TEXT,
    magnet_name                 TEXT,
    javdb_tags_json             TEXT,
    javdb_size_text             TEXT,
    inferred_category           TEXT,
    category_consistent         INTEGER,
    subtitle_evidence           TEXT,
    resolution_consistent       INTEGER,
    source_trust                TEXT,
    score                       REAL,
    shadow_rank                 INTEGER,
    would_replace_current_choice INTEGER,
    policy_mode                 TEXT,
    decision                    TEXT,
    reasons_json                TEXT,
    created_at                  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at                  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (info_hash, movie_href, scoring_version)
);

CREATE INDEX IF NOT EXISTS idx_torrent_quality_eval_movie_href
    ON TorrentQualityEvaluation(movie_href);

CREATE INDEX IF NOT EXISTS idx_torrent_quality_eval_video_code
    ON TorrentQualityEvaluation(video_code);

CREATE INDEX IF NOT EXISTS idx_torrent_quality_eval_created_at
    ON TorrentQualityEvaluation(created_at);
