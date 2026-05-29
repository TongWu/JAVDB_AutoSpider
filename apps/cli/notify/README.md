# notify

Notification CLIs — currently just the post-pipeline email sender.

## Files

| File | Purpose |
|---|---|
| `email.py` | Real CLI adapter (ADR-015 Phase 6): owns argparse parsing + exit-code mapping, maps args to `EmailNotificationOptions`, and calls `javdb.integrations.notify.email.service.run_email_notification`. Reads `reports/D1/pending_health_24h.json` for the Pending Mode Health Snapshot section. Replaces the previous `sys.modules` alias of `javdb.integrations.notify.email`. |

## Invoked by

- **`DailyIngestion.yml`** — `python3 -m apps.cli.notify.email --mode daily`.
- **`AdHocIngestion.yml`** — `python3 -m apps.cli.notify.email --mode adhoc`.

## Related

- [ADR-007 — Monorepo restructure](../../../docs/design/_archive/ADR-007-Monorepo-Restructure/ADR-007-monorepo-restructure-2026-05.md)
