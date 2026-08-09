-- 2026-06-20: Add TorrentQualityReviewLabel table (ADR-024 IMP-08).
-- Write-Class: diagnostic
--
-- Apply with:
--   wrangler d1 execute javdb-reports --remote \
--     --file=javdb/migrations/d1/2026_06_20_add_torrent_quality_review_label.sql
--
-- Diagnostic (ADR-042 D6): operator accept/reject labels over shadow quality
-- evaluations. It is the labelled dataset Phase 3 tunes thresholds against. It
-- never gates production correctness. Keep the semicolon character out of these
-- comments because the D1 apply path splits scripts on that character.
CREATE TABLE IF NOT EXISTS TorrentQualityReviewLabel (
    info_hash        TEXT NOT NULL,
    movie_href       TEXT NOT NULL,
    scoring_version  TEXT NOT NULL,
    label            TEXT NOT NULL
                         CHECK (label IN ('accept', 'reject', 'skip')),
    reviewer         TEXT,
    note             TEXT,
    reviewed_at      TEXT NOT NULL,
    PRIMARY KEY (info_hash, movie_href, scoring_version)
);

CREATE INDEX IF NOT EXISTS idx_quality_review_label_movie
    ON TorrentQualityReviewLabel(movie_href);
