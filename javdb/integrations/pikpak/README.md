# pikpak

PikPak cloud-transfer bridge: moves old torrents that qBittorrent could not seed off to PikPak for long-term storage.

## Files

| File | Purpose |
|---|---|
| `bridge.py` | PikPak transfer CLI implementation; reads torrent metadata, computes hashes, transfers magnets, and updates `PikpakHistory`. |

## Subdirectories

(none)

## Depends on

- Upstream callers: `apps.cli.pikpak_bridge`, `javdb.pipeline.service`.
- Downstream: `javdb.integrations.qb.client`, `javdb.storage` (PikpakHistory), `javdb.infra.config`, `javdb.infra.logging`.
