# notify

Pipeline email notifications: log analysis, formatted report builder, and SMTP sender with log-file attachments.

## Files

| File | Purpose |
|---|---|
| `email.py` | Email notification flow — analyses spider/uploader/pikpak logs for errors, builds the report body, attaches `.txt` logs, and sends via SMTP. |

## Subdirectories

(none)

## Depends on

- Upstream callers: `apps.cli.email_notification`, `javdb.pipeline.service`.
- Downstream: `javdb.infra.config`, `javdb.infra.logging`, `javdb.infra.masking`, `javdb.infra.paths`, `javdb.storage` (stats reads).
