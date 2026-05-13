-- 2026-05-13 — convert SessionId columns from INTEGER to TEXT on the
-- operations database (operations.db ↔ javdb-operations).
--
-- See ``migration/d1/2026_05_13_session_id_to_text_history.sql`` for the
-- full background.
--
-- Apply
-- -----
--   wrangler d1 execute javdb-operations --remote \
--     --file=migration/d1/2026_05_13_session_id_to_text_operations.sql

-- ── DedupRecords ───────────────────────────────────────────────────────
CREATE TABLE DedupRecords_new (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    VideoCode TEXT,
    ExistingSensor TEXT,
    ExistingSubtitle TEXT,
    ExistingGdrivePath TEXT,
    ExistingFolderSize INTEGER,
    NewTorrentCategory TEXT,
    DeletionReason TEXT,
    DateTimeDetected TEXT,
    IsDeleted INTEGER,
    DateTimeDeleted TEXT,
    SessionId TEXT
);
INSERT INTO DedupRecords_new
SELECT Id, VideoCode, ExistingSensor, ExistingSubtitle, ExistingGdrivePath,
       ExistingFolderSize, NewTorrentCategory, DeletionReason,
       DateTimeDetected, IsDeleted, DateTimeDeleted,
       CAST(SessionId AS TEXT)
FROM DedupRecords;
DROP TABLE DedupRecords;
ALTER TABLE DedupRecords_new RENAME TO DedupRecords;
CREATE UNIQUE INDEX IF NOT EXISTS uq_dedup_active_path
    ON DedupRecords(ExistingGdrivePath)
    WHERE IsDeleted = 0 AND ExistingGdrivePath != '';
CREATE INDEX IF NOT EXISTS idx_dedup_records_session ON DedupRecords(SessionId);

-- ── PikpakHistory ──────────────────────────────────────────────────────
CREATE TABLE PikpakHistory_new (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    TorrentHash TEXT,
    TorrentName TEXT,
    Category TEXT,
    MagnetUri TEXT,
    DateTimeAddedToQb TEXT,
    DateTimeDeletedFromQb TEXT,
    DateTimeUploadedToPikpak TEXT,
    TransferStatus TEXT,
    ErrorMessage TEXT,
    SessionId TEXT
);
INSERT INTO PikpakHistory_new
SELECT Id, TorrentHash, TorrentName, Category, MagnetUri,
       DateTimeAddedToQb, DateTimeDeletedFromQb, DateTimeUploadedToPikpak,
       TransferStatus, ErrorMessage, CAST(SessionId AS TEXT)
FROM PikpakHistory;
DROP TABLE PikpakHistory;
ALTER TABLE PikpakHistory_new RENAME TO PikpakHistory;
CREATE INDEX IF NOT EXISTS idx_pikpak_history_session ON PikpakHistory(SessionId);

-- ── InventoryAlignNoExactMatch ─────────────────────────────────────────
CREATE TABLE InventoryAlignNoExactMatch_new (
    VideoCode TEXT PRIMARY KEY,
    Reason TEXT,
    DateTimeRecorded TEXT,
    SessionId TEXT
);
INSERT INTO InventoryAlignNoExactMatch_new
SELECT VideoCode, Reason, DateTimeRecorded, CAST(SessionId AS TEXT)
FROM InventoryAlignNoExactMatch;
DROP TABLE InventoryAlignNoExactMatch;
ALTER TABLE InventoryAlignNoExactMatch_new RENAME TO InventoryAlignNoExactMatch;
CREATE INDEX IF NOT EXISTS idx_align_no_match_session
    ON InventoryAlignNoExactMatch(SessionId);

