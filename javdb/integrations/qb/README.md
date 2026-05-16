# qb

qBittorrent integration: shared Web API client, daily/adhoc torrent uploader, small-file filter, and connection-config helpers.

## Files

| File | Purpose |
|---|---|
| `client.py` | Shared qBittorrent Web API client (login, list, delete) consolidating logic previously duplicated across consumers. |
| `config.py` | Shared connection configuration helpers (URL normalisation, masked logging). |
| `uploader.py` | Torrent uploader CLI implementation (daily + ad-hoc modes); reads CSV and adds magnets to qB. |
| `file_filter.py` | Filters out small files (default 100 MB threshold) from recently-added torrents by setting per-file priority 0. |

## Subdirectories

(none)

## Depends on

- Upstream callers: `apps.cli.qb_uploader`, `apps.cli.qb_file_filter`, `javdb.pipeline.service`, `javdb.integrations.pikpak.bridge`.
- Downstream: `javdb.infra.config`, `javdb.infra.logging`, `javdb.infra.masking`, `javdb.infra.request`.
