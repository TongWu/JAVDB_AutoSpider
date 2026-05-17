# pikpak

PikPak bridge CLI — transfers older torrents to PikPak when the qBittorrent path has aged out.

## Files

| File | Purpose |
|---|---|
| `bridge.py` | Run the PikPak bridge. Supports `--days <N>` (age threshold), `--dry-run`, and `--mode individual`. Aliases `javdb.integrations.pikpak.bridge`. |

## Invoked by

- **`DailyIngestion.yml`** — `python3 -m apps.cli.pikpak_bridge --days 3` (canonical: `apps.cli.pikpak.bridge`).
- **`AdHocIngestion.yml`** — `python3 -m apps.cli.pikpak_bridge --days 3`.

## Related

- [ADR-007 — Monorepo restructure](../../../docs/ai/adr/ADR-007-monorepo-restructure-2026-05.md)
