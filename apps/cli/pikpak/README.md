# pikpak

PikPak bridge CLI — transfers older torrents to PikPak when the qBittorrent path has aged out.

## Files

| File | Purpose |
|---|---|
| `bridge.py` | Real CLI adapter (argparse + exit-code mapping) for the bridge service `javdb.integrations.pikpak.bridge`. Supports `--days <N>` (age threshold), `--dry-run`, `--individual`, proxy flags, `--from-pipeline`, `--session-id`, and `--root-folder`. |

## Invoked by

- **`DailyIngestion.yml`** — `python3 -m apps.cli.pikpak_bridge --days 3` (canonical: `apps.cli.pikpak.bridge`).
- **`AdHocIngestion.yml`** — `python3 -m apps.cli.pikpak_bridge --days 3`.

## Related

- [ADR-007 — Monorepo restructure](../../../docs/design/_archive/ADR-007-Monorepo-Restructure/ADR-007-monorepo-restructure-2026-05.md)
