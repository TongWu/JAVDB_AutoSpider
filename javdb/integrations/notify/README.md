# notify

Pipeline email notifications: log analysis, formatted report builder, and SMTP sender with log-file attachments.

## Files

| File | Purpose |
|---|---|
| `email/` | Email notification command-service package (ADR-015 Phase 6 split). |

## Subdirectories

### `email/`

| File | Purpose |
|---|---|
| `options.py` | `EmailNotificationOptions` dataclass (typed inputs). |
| `result.py` | `EmailNotificationResult` dataclass (`exit_code`: 2 on SMTP failure outside dry-run, else 0). |
| `service.py` | `run_email_notification(options)` orchestration entrypoint. |
| `log_analysis.py` | Log parsing, statistics extraction, proxy-ban/dedup analysis, pending-mode verification records, dual-mode drift advisory. |
| `report_builder.py` | Subject/body formatting, Ad-Hoc CSV discovery, drift-diagnosis section, plain-text-to-HTML. |
| `delivery.py` | SMTP send, dry-run fingerprinting, log-to-`.txt` conversion. |
| `_config.py` | Shared module-level config constants + one-time logging setup. |
| `_legacy.py` | **Phase 6 bake wrapper.** Retains the legacy CLI surface (`parse_arguments` / `main` / `__main__`) plus the extracted `run_email_notification_from_options` orchestration. Removed by IMP-ADR015-07. |

**Phase 6 bake state:** the real CLI adapter is `apps.cli.notify.email` (owns argparse + exit-code mapping). Selected legacy package exports (`send_email`, `format_email_report`, `analyze_*`, `extract_*`, …) remain re-exported from `email/__init__.py` until IMP-ADR015-07.

## Depends on

- Upstream callers: `apps.cli.notify.email`, `javdb.pipeline.service`, `apps.api.routers.operations` (lazy `send_email`).
- Downstream: `javdb.infra.config`, `javdb.infra.logging`, `javdb.infra.masking`, `javdb.infra.paths`, `javdb.infra.git_helper`, `javdb.storage` (stats reads).
