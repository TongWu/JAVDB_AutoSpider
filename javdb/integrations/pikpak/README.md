# pikpak

PikPak cloud-transfer bridge: moves old torrents that qBittorrent could not seed off to PikPak for long-term storage.

## Subdirectories

| Directory | Purpose |
|---|---|
| `bridge/` | Bridge service package (`options.py`, `result.py`, `service.py`); reads torrent metadata, transfers magnets, deletes from qB, and updates `PikpakHistory`. No CLI surface — the CLI lives in `apps.cli.pikpak.bridge`. The package-level `pikpak_bridge` is the programmatic API consumed by the REST layer (`apps/api/routers/operations.py`). |

## Depends on

- Upstream callers: `apps.cli.pikpak.bridge`, `apps.api.routers.operations`, `javdb.pipeline.service`.
- Downstream: `javdb.integrations.qb.client`, `javdb.integrations.qb.config`, `javdb.storage` (PikpakHistory), `javdb.workflow.stats_sink`, `javdb.workflow.git_side_effects`, `javdb.infra.config`, `javdb.infra.logging`.
