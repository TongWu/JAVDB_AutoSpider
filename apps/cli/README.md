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
- `apps.cli.*` is the only user-facing CLI surface. Some integration wrappers still alias `javdb.integrations.*` during the [ADR-015](../../docs/design/adr/ADR-015-integrations-interface-boundary.md) migration; those aliases are tracked by architecture allowlists and are removed by IMP-031 through IMP-036.
- Workflow invocations live in `.github/workflows/*.yml`; per-subdir READMEs note which CLIs are workflow-invoked.

## Related

- [ADR-007 — Monorepo restructure](../../docs/design/adr/archive/ADR-007-monorepo-restructure-2026-05.md)
- [scripts/README.md](../../scripts/README.md) — what remains in `scripts/` after Phase 2
