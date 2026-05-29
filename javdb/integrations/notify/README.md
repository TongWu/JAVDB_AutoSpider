# notify

Pipeline email notifications: log analysis, formatted report builder, and SMTP sender with log-file attachments.

## Files

| File | Purpose |
|---|---|
| `email/` | Email notification command-service package (ADR-015 split). |

## Subdirectories

### `email/`

| File | Purpose |
|---|---|
| `options.py` | `EmailNotificationOptions` dataclass (typed inputs). |
| `result.py` | `EmailNotificationResult` dataclass (`exit_code`: 2 on SMTP failure outside dry-run, else 0). |
| `service.py` | `run_email_notification(options)` end-to-end orchestration (no CLI surface). |
| `log_analysis.py` | Log parsing, statistics extraction, proxy-ban/dedup analysis, pending-mode verification records, dual-mode drift advisory. |
| `report_builder.py` | Subject/body formatting, Ad-Hoc CSV discovery, drift-diagnosis section, plain-text-to-HTML. |
| `delivery.py` | SMTP send, dry-run fingerprinting, log-to-`.txt` conversion. |
| `_config.py` | Shared module-level config constants + one-time logging setup. |

The real CLI adapter is `apps.cli.notify.email` (owns argparse + exit-code mapping). The package exports only `EmailNotificationOptions`, `EmailNotificationResult`, `run_email_notification`, and `send_email` (a non-CLI helper with live production callers in `apps.api.routers.operations`).

## Depends on

- Upstream callers: `apps.cli.notify.email`, `javdb.pipeline.service`, `apps.api.routers.operations` (lazy `send_email`).
- Downstream: `javdb.infra.config`, `javdb.infra.logging`, `javdb.infra.masking`, `javdb.infra.paths`, `javdb.infra.git_helper`, `javdb.storage` (stats reads).
