# apps/cli

Canonical command-line entry points for JAVDB AutoSpider — all user-facing CLIs and workflow-invoked tools live here.

## Core entries

| File | Purpose |
|---|---|
| `spider.py` | Run the JAVDB scraper (daily mode or custom URL). Invoked as `python3 -m apps.cli.spider`. |
| `pipeline.py` | Full pipeline driver (spider + uploader + bridge). Invoked as `python3 -m apps.cli.pipeline`. |
| `login.py` | Refresh the JAVDB session cookie used by custom-URL scraping. |

## Subdirectories

| Directory | Purpose |
|---|---|
| `db/` | Session lifecycle CLIs — rollback, commit, audit-archive, pending-mode health/alert, stale-session cleanup, D1↔SQLite sync. |
| `qb/` | qBittorrent integration — torrent uploader and small-file filter. |
| `pikpak/` | PikPak bridge CLI for transferring older torrents. |
| `rclone/` | Rclone-backed cloud-storage tools — manager plus JAV-Sync directory cleanup / rename / NFO rewrite. |
| `notify/` | Email notification sender (pipeline summary). |
| `ops/` | Operator tooling — fetch-page debugger, health check, config generator, OpenAPI dump, bake-metric checks, hot-path profiler. |

## Conventions

- All entries are runnable via `python3 -m apps.cli.<subdir>.<name>`.
- Thin wrapper modules (e.g. `qb/uploader.py`) alias canonical implementations under `javdb.integrations.*` / `javdb.infra.*` via `compat.alias_module` so tests can patch attributes through the CLI import path.
- Workflow invocations live in `.github/workflows/*.yml`; per-subdir READMEs note which CLIs are workflow-invoked.

## Related

- [ADR-007 — Monorepo restructure](../../docs/ai/adr/archive/ADR-007-monorepo-restructure-2026-05.md)
- [scripts/README.md](../../scripts/README.md) — what remains in `scripts/` after Phase 2
