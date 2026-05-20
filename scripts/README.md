# scripts

This directory is **no longer the home of user-facing CLIs** — after ADR-007 Phase 2, every Python entry point that ships to operators or workflows lives under [`apps/cli/`](../apps/cli/README.md).

## What's left in `scripts/`

| Path | Status | Purpose |
|---|---|---|
| `ci/` | Active | CI-internal Python tools (impact-based test selection, wiki mapping, drift checkers). Not invoked from user docs. |
| `verify_proxy_coordinator_deploy.sh` | Active | Shell-only verification script for the Cloudflare Worker proxy coordinator deployment. |
| `_spider_legacy.py` | Deferred deletion (Phase 3) | Backward-compat shim that forwards to `apps.cli.spider`. |
| `qb_uploader.py` | Deferred deletion (Phase 3) | Backward-compat shim that forwards to `apps.cli.qb.uploader`. |
| `qb_file_filter.py` | Deferred deletion (Phase 3) | Backward-compat shim that forwards to `apps.cli.qb.file_filter`. |
| `rclone_manager.py` | Deferred deletion (Phase 3) | Backward-compat shim that forwards to `apps.cli.rclone.manager`. |
| `email_notification.py` | Deferred deletion (Phase 3) | Backward-compat shim that forwards to `apps.cli.notify.email`. |
| `spider/` | Deferred deletion (Phase 3) | Legacy spider-internal helpers superseded by `javdb.spider.*`. |
| `ingestion/` | Deferred deletion (Phase 3) | Legacy ingestion helpers superseded by `apps.cli.pipeline` + `javdb.ingestion.*`. |

## Where to look instead

- **User-facing CLIs:** [`apps/cli/`](../apps/cli/README.md) — spider, pipeline, login, db, qb, pikpak, rclone, notify, ops.
- **Workflow invocations:** `.github/workflows/*.yml` — all reference `python3 -m apps.cli.*`.
- **Deferred-deletion rationale:** the Phase-3 plan keeps the compat shims live for one release cycle so external orchestrators / cron jobs that still call the old paths get a deprecation warning before the path disappears.

## Related

- [ADR-007 — Monorepo restructure](../docs/design/adr/archive/ADR-007-monorepo-restructure-2026-05.md)
- [`apps/cli/README.md`](../apps/cli/README.md)
