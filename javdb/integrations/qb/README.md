# qb

qBittorrent integration: shared Web API client, daily/adhoc torrent uploader, small-file filter, and connection-config helpers.

## Files

| File | Purpose |
|---|---|
| `client.py` | qB Web API primitives — shared client (login, list, delete) consolidating logic previously duplicated across consumers. |
| `config.py` | qB connection config — shared helpers (URL normalisation, masked logging). |

## Subdirectories

| Directory | Purpose |
|---|---|
| `uploader/` | Uploader service package (`options.py`, `result.py`, `service.py`); reads CSV and adds magnets to qB (daily + ad-hoc modes). No CLI surface — the CLI lives in `apps.cli.qb.uploader`. |
| `file_filter/` | File-filter service package (`options.py`, `result.py`, `service.py`); filters out small files (default 100 MB threshold) from recently-added torrents by setting per-file priority 0. The package-level `run_file_filter` is the legacy programmatic API consumed by the REST layer. No CLI surface — the CLI lives in `apps.cli.qb.file_filter`. |

## Depends on

- Upstream callers: `apps.cli.qb_uploader`, `apps.cli.qb_file_filter`, `javdb.pipeline.service`, `javdb.integrations.pikpak.bridge`.
- Downstream: `javdb.infra.config`, `javdb.infra.logging`, `javdb.infra.masking`, `javdb.infra.request`.
