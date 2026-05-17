# spider

JavDB scraping runtime: fetches index/detail pages, parses HTML, runs parallel/sequential extraction, and returns canonical movie + torrent entries.

## Files

| File | Purpose |
|---|---|
| `parser.py` | HTML parser for index and detail pages; emits `MovieEntry` / `TorrentEntry`. Wraps `apps.api.parsers` layer. |
| `contracts.py` | Cross-module data contracts: torrent category mapping, sensor priority, indicator tables (mirrored in Rust core). |
| `url_helper.py` | JavDB URL parsing, type detection, and normalisation (Rust-accelerated with Python fallback). |
| `filename_helper.py` | Filename derivation for spider output CSVs, with optional HTML-based name resolution. |
| `magnet_extractor.py` | Magnet link extraction and categorisation (Rust-accelerated with Python fallback). |
| `spider_gateway.py` | Unified fetch-and-parse entrypoint for any URL; consolidates proxy pool, request handler, and parser dispatch. |
| `__main__.py` | `python -m javdb.spider` dispatch into `app.main.main`. |

## Subdirectories

- `app/` — CLI entrypoint glue (`main.py`, `cli.py`, `run_service.py`).
- `runtime/` — Per-run runtime helpers: config, state, proxy state, report, sleep.
- `fetch/` — Index/backend/fallback/session/login fetch coordinator and engine.
- `detail/` — Parallel and sequential detail-page extraction modes.
- `services/` — Domain services (`dedup.py`).
- `auth/` — JavDB login session refresh.
- `compat/` — Explicit backwards-compatibility helpers (`csv_builder.py`).

## Depends on

- Upstream callers: `javdb.pipeline`, `apps.cli.spider`, `apps.api`.
- Downstream: `javdb.infra.*`, `javdb.rust_core`, `javdb.storage` (for history checks), `javdb.proxy`.
