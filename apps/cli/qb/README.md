# qb

qBittorrent integration CLIs — torrent upload and small-file filtering.

## Files

| File | Purpose |
|---|---|
| `uploader.py` | Upload `.torrent` files to qBittorrent Web UI. Modes: `daily` (default) and `adhoc`. Aliases `javdb.integrations.qb.uploader`. |
| `file_filter.py` | Filter out small files inside already-added torrents (default 100 MiB threshold). Supports `--min-size` and `--dry-run`. Aliases `javdb.integrations.qb.file_filter`. |

## Invoked by

- **`DailyIngestion.yml`** — `python3 -m apps.cli.qb_uploader --mode daily` (canonical: `apps.cli.qb.uploader`) and `python3 -m apps.cli.qb_file_filter` (canonical: `apps.cli.qb.file_filter`).
- **`AdHocIngestion.yml`** — same two CLIs with `--mode adhoc`.
- **`QBFileFilter.yml`** — `apps.cli.qb_file_filter` (small-file sweep 2h after Daily).

## Related

- [ADR-007 — Monorepo restructure](../../../docs/ai/adr/ADR-007-monorepo-restructure-2026-05.md)
