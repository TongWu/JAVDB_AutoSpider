-- 2026-05-20: Add EmailNotificationHistory table (mirrors SQLite _OPERATIONS_DDL change).
--
-- Apply with:
--   wrangler d1 execute javdb-operations --remote \
--     --file=migration/d1/2026_05_20_add_email_notification_history.sql
--
-- Mirror table used by:
--   - javdb/integrations/notify/email.py (records every send attempt)
--   - apps/api/routers/ (email history listing + resend)

-- Status values: sent | failed | resent
-- AttachmentNames: JSON array of filenames
-- SentAt: ISO 8601 timestamp
-- CreatedBy values: pipeline | manual | resend
CREATE TABLE IF NOT EXISTS EmailNotificationHistory (
    Id              INTEGER PRIMARY KEY AUTOINCREMENT,
    SessionId       TEXT,
    Recipient       TEXT NOT NULL,
    Subject         TEXT NOT NULL,
    Status          TEXT NOT NULL DEFAULT 'sent',
    ErrorMessage    TEXT,
    AttachmentNames TEXT,
    SentAt          TEXT NOT NULL,
    ResentAt        TEXT,
    CreatedBy       TEXT DEFAULT 'pipeline'
);

CREATE INDEX IF NOT EXISTS idx_email_history_session ON EmailNotificationHistory(SessionId);
CREATE INDEX IF NOT EXISTS idx_email_history_status ON EmailNotificationHistory(Status);
