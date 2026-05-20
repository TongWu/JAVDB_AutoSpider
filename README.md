# JavDB Auto Spider

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/TongWu/JAVDB_AutoSpider)
[![JavDB Daily Ingestion Pipeline](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/workflows/DailyIngestion.yml/badge.svg)](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/workflows/DailyIngestion.yml)
[![codecov](https://codecov.io/gh/TongWu/JAVDB_AutoSpider/branch/main/graph/badge.svg)](https://codecov.io/gh/TongWu/JAVDB_AutoSpider)

A Python + Rust automation system for extracting torrent links from javdb.com and automatically adding them to qBittorrent. Designed as an ingestion pipeline before scraping platforms like [MDC-NG](https://github.com/mdc-ng/mdc-ng).

English | [简体中文](README_CN.md)

## Features

- **Modular Spider** — 14 specialized modules in `javdb/spider/`, fetches and filters entries with subtitle/today tags, extracts magnet links with priority ordering
- **Rust Acceleration** (optional) — PyO3 + maturin extension for 5-10x faster HTML parsing; falls back to pure Python automatically
- **Parallel Processing** — Multi-threaded detail page fetching with one worker per proxy; auto-activates in pool mode with 2+ proxies
- **Torrent Classification** — Priority-based categories: 字幕 (subtitle), hacked (UC无码破解 > UC > U无码破解 > U), no_subtitle
- **Dual Mode** — Daily mode (default pages) and Ad Hoc mode (custom URLs for actors, tags, etc.)
- **qBittorrent Integration** — Auto-upload torrents with categorization, file size filtering, and duplicate prevention
- **PikPak Bridge** — Transfer old torrents from qBittorrent to PikPak cloud storage
- **History Tracking** — SQLite/Cloudflare D1 dual storage with session-based rollback and pending-mode writes
- **Automated Pipeline** — GitHub Actions workflows for daily ingestion, ad hoc scraping, file filtering, dedup, and more
- **Cross-Runner Coordination** (optional) — Cloudflare Worker + Durable Objects for per-proxy throttling and login state sharing across concurrent runners
- **Re-download Detection** — Automatically re-downloads when a significantly larger torrent becomes available for the same category
- **Email Notifications** — Pipeline results with intelligent error detection (critical vs. non-critical)

## Quick Start

```bash
# Clone and install
git clone https://github.com/TongWu/JAVDB_AutoSpider_CICD.git
cd JAVDB_AutoSpider_CICD
pip install -r requirements.txt

# Configure
cp config.py.example config.py
# Edit config.py: set proxy, qBittorrent credentials, etc.

# Run
python3 -m apps.cli.spider              # Daily scraping
python3 -m apps.cli.spider --dry-run    # Preview without writing
python3 -m apps.cli.pipeline            # Full pipeline (spider + upload + notify)
```

For complete setup instructions, see the [Local Setup Guide](docs/handbook/en/self-hoster/local-setup.md).

## Architecture

```
apps/
├── cli/          Canonical CLI entrypoints (spider, pipeline, db/, qb/, pikpak/, rclone/, notify/, ops/)
└── api/          FastAPI REST API

javdb/            Python namespace (PEP 420 — no top-level __init__.py)
├── spider/       Scraping runtime + parser/contracts/url/filename/magnet + auth/login
├── pipeline/     Ingestion orchestration
├── storage/      DB layer + sessions + rollback + d1 + dual + history_manager
├── proxy/        Pool + ban_manager + policy + coordinator (Worker DO clients)
├── integrations/ qb/, pikpak/, rclone/, notify/
├── infra/        Cross-cutting: config, logging, paths, csv_writer, git, request, masking
├── migrations/   SQL + Python migrate tools
├── legacy/       Pre-Phase-1 spider, preserved for rollback only
└── rust_core/    Rust crate source (PyO3 + maturin; installs as javdb.rust_core)
```

Canonical layout established by [ADR-007](docs/design/adr/archive/ADR-007-monorepo-restructure-2026-05.md); all legacy paths (`utils/`, `api/`, `migration/`, `legacy/`, `scripts/spider/`, `scripts/ingestion/`, root `compat.py`/`pipeline.py`) were retired in Phase 3.

## Configuration

Copy `config.py.example` to `config.py` and configure:

```python
# Minimum required settings
PROXY_MODE = 'pool'                    # 'pool', 'single', or 'None'
PROXY_POOL = [{'name': 'Proxy-1', 'http': 'http://127.0.0.1:7890', 'https': 'http://127.0.0.1:7890'}]
QB_URL = 'https://192.168.1.100:8080'  # qBittorrent Web UI
QB_USERNAME = 'admin'
QB_PASSWORD = 'password'
```

For the full configuration reference (60+ options), see [Configuration Guide](docs/handbook/en/self-hoster/configuration.md).

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `STORAGE_BACKEND` | `sqlite` | `sqlite`, `d1`, or `dual` |
| `WRITE_MODE` | `pending` | `pending` (default) or `audit` (legacy, sunset 2026-08-13) |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `STRICT_DUAL_WRITE` | unset | Set `1` to fail on D1 write errors |

## Common Commands

```bash
# Spider
python3 -m apps.cli.spider                                    # Daily scraping
python3 -m apps.cli.spider --url "https://javdb.com/actors/EvkJ"  # Ad hoc mode
python3 -m apps.cli.spider --use-proxy --phase 1              # Force proxy, phase 1 only
python3 -m apps.cli.spider --ignore-release-date              # All entries, not just today

# Pipeline
python3 -m apps.cli.pipeline                                  # Full workflow
python3 -m apps.cli.pipeline --use-proxy                      # With proxy override

# Uploaders
python3 -m apps.cli.qb_uploader                               # Upload to qBittorrent
python3 -m apps.cli.qb_file_filter --min-size 100 --dry-run   # Filter small files

# Maintenance
python3 -m apps.cli.migration --help                           # Database migrations
python3 -m apps.cli.rollback --session-id 332                  # Rollback a session
python3 -m apps.cli.login                                      # Refresh JavDB session cookie
```

For the full CLI reference, see [CLI Reference](docs/handbook/en/developer/cli-reference.md).

## Deployment Options

| Method | Guide | Best For |
|--------|-------|----------|
| **Local** | [Local Setup](docs/handbook/en/self-hoster/local-setup.md) | Development, manual runs |
| **GitHub Actions** | [GH Actions Setup](docs/handbook/en/self-hoster/github-actions-setup.md) | Automated daily pipeline |
| **Docker** | [Docker Deploy](docs/handbook/en/self-hoster/docker-deploy.md) | Self-hosted server |
| **Proxy Coordinator** | [Proxy Coordinator](docs/handbook/en/self-hoster/proxy-coordinator.md) | Multi-runner coordination |

## GitHub Actions Workflows

| Workflow | Trigger | Description |
|----------|---------|-------------|
| `DailyIngestion.yml` | Cron 12:00 UTC + manual | Daily scraping pipeline |
| `AdHocIngestion.yml` | Manual | Custom URL scraping |
| `QBFileFilter.yml` | Cron 16:00 UTC + manual | Filter small files (4h after daily) |
| `WeeklyDedup.yml` | Cron Sunday + manual | Rclone deduplication |
| `RollbackD1.yml` | Manual | Session rollback |
| `StaleSessionCleanup.yml` | Cron daily 02:00 UTC | Clean up stuck sessions (>48h) |
| `AuditArchive.yml` | Cron weekly Monday | Prune old audit rows |
| `Migration.yml` | Manual | Database migration runner |
| `TestIngestion.yml` | Manual | Dry-run test pipeline |

## Storage Backend

The system supports three storage modes via `STORAGE_BACKEND`:

- **SQLite** (default) — Local files in `reports/` (history.db, reports.db, operations.db)
- **D1** — Cloudflare D1 for GitHub Actions environments
- **Dual** — Writes mirror to both; reads from D1

Every pipeline run is tagged with a session ID and follows the lifecycle: `in_progress → finalizing → committed / failed`. Pending-mode writes only land in history tables at commit time; failed runs delete pending rows cleanly.

For rollback procedures, see [D1 Rollback Guide](docs/handbook/en/ops/d1-rollback.md).

## Documentation

### For Self-Hosters
- [Local Setup](docs/handbook/en/self-hoster/local-setup.md) — From-scratch installation
- [GitHub Actions Setup](docs/handbook/en/self-hoster/github-actions-setup.md) — CI/CD deployment
- [Docker Deploy](docs/handbook/en/self-hoster/docker-deploy.md) — Container deployment
- [Configuration Reference](docs/handbook/en/self-hoster/configuration.md) — All 60+ config options
- [Proxy Coordinator](docs/handbook/en/self-hoster/proxy-coordinator.md) — Cross-runner coordination
- [Proxy Setup](docs/handbook/en/self-hoster/proxy-setup.md) — Proxy pool configuration
- [CloudFlare Bypass](docs/handbook/en/self-hoster/cloudflare-bypass.md) — CF challenge fallback
- [JavDB Login](docs/handbook/en/self-hoster/javdb-login.md) — Session cookie refresh
- [Web UI Deploy](docs/handbook/en/self-hoster/web-ui-deploy.md) — Web UI + API stack
- [Rust Installation](docs/handbook/en/self-hoster/rust-installation.md) — Optional Rust extension

### For Developers
- [CLI Reference](docs/handbook/en/developer/cli-reference.md) — All CLI commands and arguments
- [API Usage Guide](docs/handbook/en/developer/api-usage-guide.md) — Python module and REST API
- [History System](docs/handbook/en/developer/history-system.md) — Duplicate prevention and tracking

### For Operators
- [D1 Rollback](docs/handbook/en/ops/d1-rollback.md) — Rollback SOP and dispatch matrix
- [Troubleshooting](docs/handbook/en/ops/troubleshooting.md) — Common issues and solutions
- [Logging](docs/handbook/en/ops/logging.md) — Log configuration and formats
- [Migration Scripts](docs/handbook/en/ops/migration-scripts.md) — Database migration tools

### Other Resources
- [CONTEXT.md](CONTEXT.md) — Domain language glossary
- [JavDB Login Guide](docs/handbook/en/self-hoster/javdb-login.md) — Login troubleshooting
- [Proxy Coordinator Worker](https://github.com/TongWu/JAVDB_AutoSpider_Proxycoordinator) — Cloudflare Worker source
- [DeepWiki](https://deepwiki.com/TongWu/JAVDB_AutoSpider) — AI-powered documentation explorer

## Security

- Never commit `config.py` (excluded in `.gitignore`)
- Do not commit files under `reports/`
- Use GitHub personal access tokens, not passwords
- Store sensitive values in environment variables for CI/CD
- Session cookies auto-expire; refresh via `python3 -m apps.cli.login`

## Contributing

Contributions are welcome! Please feel free to submit issues or pull requests.

## License

This project is for educational and personal use only. Please respect the terms of service of the websites you scrape.
