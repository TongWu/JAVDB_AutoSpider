-- 2026-05-20: Add EmailNotificationHistory table (mirrors SQLite _OPERATIONS_DDL change).
--
-- Apply with:
--   wrangler d1 execute javdb-operations --remote \
--     --file=migration/d1/2026_05_20_add_email_notification_history.sql
--
-- Mirror table used by:
--   - javdb/integrations/notify/email.py (records every send attempt)
--   - apps/api/routers/ (email history listing + resend)

CREATE TABLE IF NOT EXISTS EmailNotificationHistory (
    Id              INTEGER PRIMARY KEY AUTOINCREMENT,
    SessionId       TEXT,
    Recipient       TEXT NOT NULL,
    Subject         TEXT NOT NULL,
    Status          TEXT NOT NULL DEFAULT 'sent',  -- sent | failed | resent
    ErrorMessage    TEXT,
    AttachmentNames TEXT,                           -- JSON array of filenames
    SentAt          TEXT NOT NULL,                  -- ISO 8601
    ResentAt        TEXT,
    CreatedBy       TEXT DEFAULT 'pipeline'         -- pipeline | manual | resend
);

CREATE INDEX IF NOT EXISTS idx_email_history_session ON EmailNotificationHistory(SessionId);
CREATE INDEX IF NOT EXISTS idx_email_history_status ON EmailNotificationHistory(Status);
