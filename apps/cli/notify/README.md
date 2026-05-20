# notify

Notification CLIs — currently just the post-pipeline email sender.

## Files

| File | Purpose |
|---|---|
| `email.py` | Render and send the pipeline summary email (daily / adhoc / weekly modes). Aliases `javdb.integrations.notify.email`. Reads `reports/D1/pending_health_24h.json` for the Pending Mode Health Snapshot section. |

## Invoked by

- **`DailyIngestion.yml`** — `python3 -m apps.cli.email_notification --mode daily` (canonical: `apps.cli.notify.email`).
- **`AdHocIngestion.yml`** — `python3 -m apps.cli.email_notification --mode adhoc`.

## Related

- [ADR-007 — Monorepo restructure](../../../docs/design/adr/archive/ADR-007-monorepo-restructure-2026-05.md)
