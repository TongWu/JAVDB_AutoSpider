# integrations

External-service integrations split by tool: qBittorrent (downloader), PikPak (cloud), Rclone (remote sync), and email notifications.

> **ADR-015 migration note:** `javdb.integrations.*` is converging to service/client modules only. User-facing `argparse`, `main()`, `sys.exit()`, and `python -m` entrypoints belong under `apps.cli.*`.

## Files

(none — this is a grouping namespace; consult each subdirectory.)

## Subdirectories

- `qb/` — qBittorrent Web API client, uploader, file filter, and connection config.
- `pikpak/` — PikPak cloud transfer bridge for old torrents.
- `rclone/` — Rclone remote scan, dedup, and execution helpers.
- `notify/` — Pipeline email notification builder + sender.

## Depends on

- Upstream callers: `apps.cli.qb_uploader`, `apps.cli.qb_file_filter`, `apps.cli.pikpak_bridge`, `apps.cli.rclone_manager`, `javdb.pipeline.service`, `javdb.infra.health_check`.
- Downstream: `javdb.infra.config`, `javdb.infra.logging`, `javdb.infra.masking`, `javdb.infra.request`, `javdb.storage`.
