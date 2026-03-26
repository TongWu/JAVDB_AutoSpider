# JavDB Auto Spider

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/TongWu/JAVDB_AutoSpider)
[![JavDB Daily Ingestion Pipeline](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/workflows/DailyIngestion.yml/badge.svg)](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/workflows/DailyIngestion.yml)
[![codecov](https://codecov.io/gh/TongWu/JAVDB_AutoSpider/branch/main/graph/badge.svg)](https://codecov.io/gh/TongWu/JAVDB_AutoSpider)

A comprehensive Python + Rust automation system for extracting torrent links from javdb.com and automatically adding them to qBittorrent. The system features a high-performance Rust core (via PyO3) for HTML parsing and proxy management, multi-threaded parallel processing, intelligent history tracking, git integration, automated pipeline execution, and duplicate download prevention.

It can be played as an ingestion pipeline before the automated scrapping platform for JAV (e.g. [MDC-NG](https://github.com/mdc-ng/mdc-ng)).

English | [简体中文](README_CN.md)

Canonical app entrypoints now live under `apps/cli/`, `apps/api/`, `apps/web/`, and `apps/desktop/`. Legacy root paths such as `scripts/`, `pipeline.py`, `migration/`, and `api/` are kept as compatibility wrappers.

## Features

### Core Spider Functionality
- Modular spider package (`packages/python/javdb_spider/`) with 14 specialized modules
- Fetches data in real-time from `javdb.com/?vft=2` to `javdb.com/?page=5&vft=2`
- Filters entries with both "含中字磁鏈" and "今日新種" tags (supports multiple language variations)
- Extracts magnet links based on specific categories with priority ordering
- Saves results to timestamped CSV files in `reports/DailyReport/` directory
- Comprehensive logging with different levels (INFO, WARNING, DEBUG, ERROR)
- Multi-page processing with progress tracking
- Additional metadata extraction (actor, rating, comment count)

### Rust Acceleration (Optional)
- High-performance Rust core extension (`javdb_rust_core`) built with PyO3 + maturin
- **"Rust first, Python fallback"** pattern — all features work without Rust installed
- HTML parsing 5-10x faster than BeautifulSoup (index, detail, category pages)
- Thread-safe proxy pool management with `Arc<Mutex>`
- Accelerated history management, CSV operations, magnet extraction, URL helpers
- Automatic detection: system uses Rust when available, falls back to pure Python

### Parallel Processing
- Unified detail runner that switches between parallel and sequential fetch backends
- Multi-threaded detail page processing with one worker thread per proxy
- Activated automatically when using proxy pool mode with 2+ proxies
- Task queue / result queue architecture for safe concurrent scraping
- Independent `MovieSleepManager` per worker for rate limiting
- Thread-safe login refresh with `_login_lock`
- Force sequential mode with `--sequential` flag

### Torrent Classification System
- **字幕 (subtitle)**: Magnet links with "Subtitle" tag
- **hacked**: Magnet links with priority order:
  1. UC无码破解 (-UC.无码破解.torrent) - Highest priority
  2. UC (-UC.torrent)
  3. U无码破解 (-U.无码破解.torrent)
  4. U (-U.torrent) - Lowest priority

### Dual Mode Support
The spider operates in two modes:

#### Daily Mode (Default)
- Uses base URL: `https://javdb.com/?vft=2`
- Saves results to `reports/DailyReport/` directory
- Checks history by default to avoid re-downloading
- Uses "JavDB" category in qBittorrent

#### Ad Hoc Mode (Custom URL)
- Activated with `--url` parameter for custom URLs (actors, tags, etc.)
- Saves results to `reports/AdHoc/` directory
- **Now checks history by default** to skip already downloaded entries
- Use `--ignore-history` to re-download everything
- Uses "Ad Hoc" category in qBittorrent
- Example: `python3 -m apps.cli.spider --url "https://javdb.com/actors/EvkJ"`

### qBittorrent Integration
- Automatically reads current date's CSV file
- Connects to qBittorrent via Web UI API
- Adds torrents with proper categorization and settings
- Comprehensive logging and progress tracking
- Detailed summary reports

### qBittorrent File Filter
- Automatically filters small files from recently added torrents
- Configurable minimum file size threshold (default: 100MB from `QB_FILE_FILTER_MIN_SIZE_MB`)
- Sets priority to 0 (do not download) for files below threshold
- Filters out NFO files, samples, screenshots, etc.
- Supports dry-run mode for preview
- Category-based filtering option
- Scheduled via GitHub Actions (2 hours after daily ingestion)

### Duplicate Download Prevention
- **Automatic Downloaded Detection**: Automatically identifies which torrents have been downloaded by checking the history CSV file
- **Download Indicators**: Adds `[DOWNLOADED]` prefix to downloaded torrents in daily report CSV files
- **Skip Duplicate Downloads**: qBittorrent uploader automatically skips torrents with `[DOWNLOADED]` indicators
- **Multiple Torrent Type Support**: Supports four types: hacked_subtitle, hacked_no_subtitle, subtitle, no_subtitle
- **Enhanced History Tracking**: Tracks create_date (first discovery) and update_date (latest modification) for each movie

### Git Integration & Pipeline
- Automated git commit and push functionality
- Incremental commits throughout pipeline execution
- Email notifications with results and logs
- Complete workflow automation

### JavDB Auto Login
- Automatic session cookie refresh
- Captcha handling (manual input or 2Captcha API)
- Updates config.py automatically
- Supports custom URL scraping with authentication
- See [JavDB Login Guide](utils/login/JAVDB_LOGIN_README.md) for setup

### CloudFlare Bypass (Optional)
- Integration with [CloudflareBypassForScraping](https://github.com/sarperavci/CloudflareBypassForScraping)
- Request Mirroring mode for transparent CF bypass
- Automatic cookie caching and management
- Works with both local and remote proxy setups
- Automatically activated as a fallback when direct requests fail

## Installation

1. Install Python dependencies:
```bash
pip install -r requirements.txt
```

2. (Optional) Install SOCKS5 proxy support if you want to use SOCKS5 proxies:
```bash
pip install requests[socks]
```

3. (Optional) Install Rust acceleration extension for 5-10x faster HTML parsing:
```bash
# Install Rust toolchain
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Build and install the extension
cd packages/rust/javdb_rust_core
pip install maturin
maturin develop --release
cd ..
```
> **Note:** The Rust extension is optional. All features work without it — the system automatically falls back to pure Python implementations.

4. Configure the system by copying and editing the configuration file:
```bash
cp config.py.example config.py
```

5. (Optional) For CloudFlare bypass feature, install and run [CloudflareBypassForScraping](https://github.com/sarperavci/CloudflareBypassForScraping) service:
```bash
# See CloudFlare Bypass section below for setup instructions
```

## Electron Shell (MVP, Dev Mode)

This repository now includes an Electron shell for desktop development.  
Current scope is MVP only (dev runtime), without packaging/release installer.

### Prerequisites

- Node.js installed
- Python 3 available (`python`, `python3`, or set `PYTHON` env)
- Project dependencies installed (`pip install -r requirements.txt`)

### Start Electron dev

From repository root:

```bash
npm install
cd web && npm install && cd ..
npm run electron:dev
```

What it does:

- Starts Vite dev server at `http://127.0.0.1:5173`
- Electron main process auto-starts FastAPI backend at `http://127.0.0.1:8100`
- Opens desktop window and loads the existing frontend

### Electron security tradeoffs

The Electron shell currently keeps a minimal-but-pragmatic setup for the Explore flow:

- `BrowserWindow.webPreferences.webviewTag = true` is enabled because `ExplorePage.vue` renders an embedded `<webview>` for in-app browsing.
- `BrowserWindow.webPreferences.sandbox = false` is currently required because `preload.js` reads `process.argv` to extract the `--api-base` value passed from `additionalArguments`.
- `contextIsolation` remains enabled and `nodeIntegration` remains disabled.
- `preload.js` exposes only a minimal `contextBridge` surface: `isElectron` and `apiBase`.

Risk and mitigation notes for maintainers:

- Keep the preload bridge surface minimal; do not expose arbitrary IPC helpers.
- Treat `<webview>` navigation as untrusted content and keep allowlists/validation in `ExplorePage.vue` and backend URL validators.
- Revisit sandboxing if `apiBase` passing can be moved away from `process.argv` (for example via safer IPC/bootstrap handoff).

### Docker Installation (Alternative)

You can also run the application using Docker containers, which simplifies dependency management and deployment.

#### Quick Start with Docker

1. **Pull the image from GitHub Container Registry:**
```bash
docker pull ghcr.io/YOUR_USERNAME/javdb-autospider:latest
```

2. **Prepare configuration files:**
```bash
cp config.py.example config.py
cp env.example .env
# Edit config.py with your settings
```

3. **Run the container:**
```bash
docker run -d \
  --name javdb-spider \
  --restart unless-stopped \
  -v $(pwd)/config.py:/app/config.py:ro \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/Ad\ Hoc:/app/Ad\ Hoc \
  -v $(pwd)/Daily\ Report:/app/Daily\ Report \
  --env-file .env \
  ghcr.io/YOUR_USERNAME/javdb-autospider:latest
```

#### Using Docker Compose (Recommended)

1. **Use the automated build script:**
```bash
./docker/docker-build.sh
```

Or manually:

```bash
# Prepare configuration
cp config.py.example config.py
cp env.example .env

# Build and start
docker-compose -f docker/docker-compose.yml build
docker-compose -f docker/docker-compose.yml up -d
```

2. **View logs:**
```bash
docker-compose -f docker/docker-compose.yml logs -f
```

The Docker image uses multi-stage builds: a Rust builder stage compiles the `javdb_rust_core` extension, and the runtime stage only includes the compiled wheel.

## Usage

### Docker Usage

If you installed via Docker, you can manage the container with the following commands:

#### Basic Commands

```bash
# View container logs
docker logs -f javdb-spider

# View cron logs
docker exec javdb-spider tail -f /var/log/cron.log

# Run spider manually
docker exec javdb-spider python3 -m apps.cli.spider --use-proxy

# Run pipeline manually
docker exec javdb-spider python3 -m apps.cli.pipeline

# Execute commands inside container
docker exec -it javdb-spider bash

# Stop container
docker stop javdb-spider

# Start container
docker start javdb-spider

# Restart container
docker restart javdb-spider
```

#### With Docker Compose

```bash
# Start containers
docker-compose -f docker/docker-compose.yml up -d

# Stop containers
docker-compose -f docker/docker-compose.yml down

# View logs
docker-compose -f docker/docker-compose.yml logs -f

# Restart containers
docker-compose -f docker/docker-compose.yml restart

# Rebuild and restart
docker-compose -f docker/docker-compose.yml build --no-cache
docker-compose -f docker/docker-compose.yml up -d
```

#### Configure Cron Jobs

Edit the `.env` file to configure scheduled tasks:

```bash
# Spider runs daily at 3:00 AM
CRON_SPIDER=0 3 * * *
SPIDER_COMMAND=cd /app && /usr/local/bin/python -m apps.cli.spider --use-proxy >> /var/log/cron.log 2>&1

# Pipeline runs daily at 4:00 AM
CRON_PIPELINE=0 4 * * *
PIPELINE_COMMAND=cd /app && /usr/local/bin/python -m apps.cli.pipeline >> /var/log/cron.log 2>&1
```

After modifying `.env`, restart the container:
```bash
docker-compose -f docker/docker-compose.yml restart
```

### Individual Scripts (Local Installation)

**Run the spider to extract data:**
```bash
python3 -m apps.cli.spider

# Or equivalently:
python -m scripts.spider
```

**Run the qBittorrent uploader:**
```bash
# Daily mode (default)
python3 -m apps.cli.qb_uploader

# Ad hoc mode (for custom URL scraping results)
python3 -m apps.cli.qb_uploader --mode adhoc

# Use proxy for qBittorrent API requests
python3 -m apps.cli.qb_uploader --use-proxy
```

**Run the qBittorrent File Filter (filter out small files):**
```bash
# Default: uses QB_FILE_FILTER_MIN_SIZE_MB from config (100 if unset)
python3 -m apps.cli.qb_file_filter

# Override threshold (e.g. 50MB) and days
python3 -m apps.cli.qb_file_filter --min-size 50
python3 -m apps.cli.qb_file_filter --min-size 100 --days 3

# Dry run (preview without changes)
python3 -m apps.cli.qb_file_filter --dry-run

# Filter specific category only
python3 -m apps.cli.qb_file_filter --category JavDB

# With proxy
python3 -m apps.cli.qb_file_filter --use-proxy
```

**Run the PikPak bridge (transfer old torrents from qBittorrent to PikPak):**
```bash
# Default: process torrents older than 3 days in batch mode
python3 -m apps.cli.pikpak_bridge

# Custom days threshold
python3 -m apps.cli.pikpak_bridge --days 7

# Dry run mode (test without actual transfers)
python3 -m apps.cli.pikpak_bridge --dry-run

# Individual mode (process torrents one by one instead of batch)
python3 -m apps.cli.pikpak_bridge --individual

# Use proxy for qBittorrent API requests
python3 -m apps.cli.pikpak_bridge --use-proxy

# Combine options
python3 -m apps.cli.pikpak_bridge --days 5 --dry-run --use-proxy
```

### Command-Line Arguments

The JavDB Spider supports various command-line arguments for customization:

#### Basic Options
```bash
# Dry run mode (no CSV file written)
python3 -m apps.cli.spider --dry-run

# Specify custom output filename
python3 -m apps.cli.spider --output-file my_results.csv

# Custom page range
python3 -m apps.cli.spider --start-page 3 --end-page 10

# Parse all pages until empty page is found
python3 -m apps.cli.spider --all
```

#### Phase Control
```bash
# Run only Phase 1 (subtitle + today/yesterday tags)
python3 -m apps.cli.spider --phase 1

# Run only Phase 2 (today/yesterday tags with quality filter)
python3 -m apps.cli.spider --phase 2

# Run both phases (default)
python3 -m apps.cli.spider --phase all
```

#### History Control
```bash
# Ignore history file and scrape all pages (for both daily and ad hoc modes)
python3 -m apps.cli.spider --ignore-history

# Custom URL scraping (saves to reports/AdHoc/, checks history by default)
python3 -m apps.cli.spider --url "https://javdb.com/?vft=2"

# Custom URL scraping, ignoring history to re-download everything
python3 -m apps.cli.spider --url "https://javdb.com/actors/EvkJ" --ignore-history

# Ignore today/yesterday release date tags and process all matching entries
python3 -m apps.cli.spider --ignore-release-date

# Use proxy for all HTTP requests
python3 -m apps.cli.spider --use-proxy
```

#### Complete Examples
```bash
# Quick test run with limited pages
python3 -m apps.cli.spider --start-page 1 --end-page 3 --dry-run

# Full scrape ignoring history
python3 -m apps.cli.spider --all --ignore-history

# Custom URL with specific output file
python3 -m apps.cli.spider --url "https://javdb.com/?vft=2" --output-file custom_results.csv

# Phase 1 only with custom page range
python3 -m apps.cli.spider --phase 1 --start-page 5 --end-page 15

# Download all subtitle entries regardless of release date
python3 -m apps.cli.spider --ignore-release-date --phase 1

# Download all high-quality entries regardless of release date
python3 -m apps.cli.spider --ignore-release-date --phase 2 --start-page 1 --end-page 10

# Ad hoc mode: Download specific actor's movies (skips already downloaded)
python3 -m apps.cli.spider --url "https://javdb.com/actors/EvkJ" --ignore-release-date

# Ad hoc mode: Re-download everything from an actor (ignores history)
python3 -m apps.cli.spider --url "https://javdb.com/actors/EvkJ" --ignore-history --ignore-release-date

# Follow proxy modules from config.py (default auto mode)
python3 -m apps.cli.spider --start-page 1 --end-page 5

# Force-enable proxy for this run
python3 -m apps.cli.spider --use-proxy --start-page 1 --end-page 5

# Force-disable proxy for this run
python3 -m apps.cli.spider --no-proxy --start-page 1 --end-page 5

# Combine multiple options: force proxy + custom URL + ignore release date
python3 -m apps.cli.spider --url "https://javdb.com/actors/EvkJ" --use-proxy --ignore-release-date
```

#### Argument Reference

| Argument | Description | Default | Example |
|----------|-------------|---------|---------|
| `--dry-run` | Print items without writing CSV | False | `--dry-run` |
| `--output-file` | Custom CSV filename | Auto-generated | `--output-file results.csv` |
| `--start-page` | Starting page number | 1 | `--start-page 5` |
| `--end-page` | Ending page number | 20 | `--end-page 10` |
| `--all` | Parse until empty page | False | `--all` |
| `--ignore-history` | Skip history checking (both daily & ad hoc) | False | `--ignore-history` |
| `--url` | Custom URL to scrape (enables ad hoc mode) | None | `--url "https://javdb.com/?vft=2"` |
| `--phase` | Phase to run (1/2/all) | all | `--phase 1` |
| `--ignore-release-date` | Ignore today/yesterday tags | False | `--ignore-release-date` |
| `--use-proxy` | Force-enable proxy for this run | Auto (`PROXY_MODULES`) | `--use-proxy` |
| `--no-proxy` | Force-disable proxy for this run | Auto (`PROXY_MODULES`) | `--no-proxy` |
| `--always-bypass-time [MINUTES]` | Keep using CF bypass after fallback success (omit value or 0 = whole session; omit flag = always direct-first) | None | `--always-bypass-time 30` |
| `--sequential` | Force sequential processing (disable parallel) | False | `--sequential` |
| `--max-movies-phase1` | Limit phase 1 movies (for testing) | None | `--max-movies-phase1 10` |
| `--max-movies-phase2` | Limit phase 2 movies (for testing) | None | `--max-movies-phase2 5` |
| `--use-history` | Enable history filter in ad-hoc mode | False | `--use-history` |

### Additional Tools

**JavDB Auto Login (for custom URL scraping):**
```bash
# Run when session cookie expires or before using --url parameter
python3 javdb_login.py

# The script will:
# 1. Login to JavDB with your credentials
# 2. Handle captcha (manual input or 2Captcha API)
# 3. Extract and update session cookie in config.py
# 4. Verify the cookie works

# See JavDB Auto Login section above for setup details
```

**Check Proxy Ban Status:**

Proxy bans are **session-scoped** (in-memory only): when a proxy is banned during a run, it applies only to that process. The next run starts with no bans—all proxies are automatically retried. Ban activity appears in spider log output; pipeline email reports may still summarize proxy issues for that run. There is no `reports/proxy_bans.csv`, and bans are not stored in the database (the `ProxyBans` table no longer exists).

**Run Migration Scripts** (from repository root):
```bash
# SQLite schema / actor backfill (primary entry)
python3 -m apps.cli.migration --help

# Ad hoc CSV / legacy helpers live under packages/python/javdb_migrations/tools/
python3 packages/python/javdb_migrations/tools/cleanup_history_priorities.py
python3 packages/python/javdb_migrations/tools/update_history_format.py
python3 packages/python/javdb_migrations/tools/reclassify_c_hacked_torrents.py
```

### Automated Pipeline

**Run the complete workflow:**
```bash
# Basic pipeline run (auto proxy mode from config.py)
python pipeline_run_and_notify.py

# Pipeline with custom arguments (passed to Javdb_Spider)
python pipeline_run_and_notify.py --start-page 1 --end-page 5

# Pipeline ignoring release date tags
python pipeline_run_and_notify.py --ignore-release-date --phase 1

# Pipeline with custom URL
python pipeline_run_and_notify.py --url "https://javdb.com/actors/EvkJ"

# Force-enable proxy for all pipeline steps
python pipeline_run_and_notify.py --use-proxy

# Force-disable proxy for all pipeline steps
python pipeline_run_and_notify.py --no-proxy

# Pipeline with PikPak individual mode (process torrents one by one)
python pipeline_run_and_notify.py --pikpak-individual
```

The pipeline will:
1. Run the JavDB Spider to extract data (with provided arguments)
2. Commit spider results to GitHub immediately
3. Run the qBittorrent Uploader to add torrents
4. Commit uploader results to GitHub immediately
5. Run PikPak Bridge to handle old torrents (batch mode by default, individual mode with `--pikpak-individual`)
6. Perform final commit and push to GitHub
7. **Analyze logs for critical errors**
8. Send email notifications with appropriate status

**Note**: The pipeline accepts the same arguments as `python3 -m apps.cli.spider` and passes them through automatically. By default it does **not** inject `--use-proxy` or `--no-proxy`; each step follows `config.py` via `PROXY_MODULES`. If you manually pass `--use-proxy` or `--no-proxy` to the pipeline, that override is forwarded to spider, qBittorrent uploader, and PikPak Bridge. Additional pipeline-specific arguments include `--pikpak-individual` for PikPak Bridge mode control.
When dedup runs, the email report now breaks out `Redownload Upgrade` entries separately from regular dedup upgrades.

#### Intelligent Error Detection

The pipeline now includes sophisticated error analysis that distinguishes between:

**Critical Errors (email marked as FAILED):**
- Cannot access JavDB main site (all pages fail)
- Cannot connect to qBittorrent
- Cannot login to qBittorrent
- All torrent additions failed
- Network completely unreachable

**Non-Critical Errors (email marked as SUCCESS):**
- Some specific JavDB pages failed (but main site accessible)
- Some individual torrents failed to add (but qBittorrent accessible)
- PikPak API issues (PikPak service problem, not infrastructure)
- No new torrents found (expected behavior)

This ensures you only get FAILED emails when there's a real infrastructure problem that needs attention, not just when there's no new content or minor issues.

## Configuration

### Unified Configuration (`config.py`)

All configuration settings are now centralized in a single `config.py` file:

```python
# =============================================================================
# GIT CONFIGURATION
# =============================================================================
GIT_USERNAME = 'your_github_username'
GIT_PASSWORD = 'your_github_token_or_password'
GIT_REPO_URL = 'https://github.com/your_username/your_repo_name.git'
GIT_BRANCH = 'main'

# =============================================================================
# QBITTORRENT CONFIGURATION
# =============================================================================
QB_URL = 'https://your_qbittorrent_ip:your_qbittorrent_port'  # Include http:// or https://
QB_VERIFY_TLS = True  # Verify qBittorrent TLS certificates
QB_USERNAME = 'your_qbittorrent_username'
QB_PASSWORD = 'your_qbittorrent_password'
TORRENT_CATEGORY = 'JavDB'  # Category for daily mode torrents
TORRENT_CATEGORY_ADHOC = 'Ad Hoc'  # Category for adhoc mode torrents
TORRENT_SAVE_PATH = ''
AUTO_START = True
SKIP_CHECKING = False
REQUEST_TIMEOUT = 30
DELAY_BETWEEN_ADDITIONS = 1

# =============================================================================
# SMTP CONFIGURATION (for email notifications)
# =============================================================================
SMTP_SERVER = 'smtp.gmail.com'
SMTP_PORT = 587
SMTP_USER = 'your_email@gmail.com'
SMTP_PASSWORD = 'your_email_password_or_app_password'
EMAIL_FROM = 'your_email@gmail.com'
EMAIL_TO = 'your_email@gmail.com'

# =============================================================================
# PROXY CONFIGURATION
# =============================================================================

# Proxy mode: 'single' (use first proxy only) or 'pool' (automatic failover)
PROXY_MODE = 'single'

# Proxy pool - list of proxies (first one used in single mode, all used in pool mode)
PROXY_POOL = [
    {'name': 'Main-Proxy', 'http': 'http://127.0.0.1:7890', 'https': 'http://127.0.0.1:7890'},
    {'name': 'Backup-Proxy', 'http': 'http://127.0.0.1:7891', 'https': 'http://127.0.0.1:7891'},
]

# Proxy pool behavior (only for pool mode)
PROXY_POOL_COOLDOWN_SECONDS = 691200  # 8 days cooldown for banned proxies
PROXY_POOL_MAX_FAILURES = 3  # Max failures before cooldown

# Legacy proxy config (deprecated - use PROXY_POOL instead)
PROXY_HTTP = None
PROXY_HTTPS = None

# Modular proxy control - which modules use proxy by default
PROXY_MODULES = ['spider']  # default; or use 'all' / list: 'spider', 'qbittorrent', 'pikpak'

# =============================================================================
# SPIDER CONFIGURATION
# =============================================================================
PAGE_START = 1
PAGE_END = 20
BASE_URL = 'https://javdb.com'

# Phase 2 filtering criteria
PHASE2_MIN_RATE = 4.0  # Minimum rating score for phase 2 entries
PHASE2_MIN_COMMENTS = 80  # Minimum comment count for phase 2 entries

# Release date filter
IGNORE_RELEASE_DATE_FILTER = False  # Set True to ignore today/yesterday tags

# Sleep time configuration (in seconds)
PAGE_SLEEP = 2  # Sleep between index pages
MOVIE_SLEEP_MIN = 5   # Minimum random sleep between movies
MOVIE_SLEEP_MAX = 15  # Maximum random sleep between movies

# =============================================================================
# JAVDB LOGIN CONFIGURATION (for automatic session cookie refresh)
# =============================================================================

# JavDB login credentials (optional - for custom URL scraping)
JAVDB_USERNAME = ''  # Your JavDB email or username
JAVDB_PASSWORD = ''  # Your JavDB password

# Session cookie (auto-updated by javdb_login.py)
JAVDB_SESSION_COOKIE = ''

# Optional: 2Captcha API key for automatic captcha solving
# Get from: https://2captcha.com/ (~$1 per 1000 captchas)
TWOCAPTCHA_API_KEY = ''  # Leave empty for manual captcha input

# =============================================================================
# CLOUDFLARE BYPASS CONFIGURATION (Optional)
# =============================================================================

# CloudFlare bypass service port (must match the service port)
# See: https://github.com/sarperavci/CloudflareBypassForScraping
CF_BYPASS_SERVICE_PORT = 8000

# =============================================================================
# LOGGING CONFIGURATION
# =============================================================================
LOG_LEVEL = 'INFO'
SPIDER_LOG_FILE = 'logs/spider.log'
UPLOADER_LOG_FILE = 'logs/qb_uploader.log'
PIPELINE_LOG_FILE = 'logs/pipeline.log'
EMAIL_NOTIFICATION_LOG_FILE = 'logs/email_notification.log'

# =============================================================================
# FILE PATHS
# =============================================================================
REPORTS_DIR = 'reports'
DAILY_REPORT_DIR = 'reports/DailyReport'
AD_HOC_DIR = 'reports/AdHoc'
PARSED_MOVIES_CSV = 'parsed_movies_history.csv'

# =============================================================================
# PIKPAK CONFIGURATION (for PikPak Bridge)
# =============================================================================

# PikPak login credentials
PIKPAK_EMAIL = 'your_pikpak_email@example.com'
PIKPAK_PASSWORD = 'your_pikpak_password'

# PikPak settings
PIKPAK_LOG_FILE = 'logs/pikpak_bridge.log'
PIKPAK_REQUEST_DELAY = 2  # Delay between requests (seconds) to avoid rate limiting

# =============================================================================
# qBittorrent File Filter Configuration
# =============================================================================

# Minimum file size threshold in MB
# Files smaller than this will be set to "do not download" priority
# This helps filter out small files like NFO, samples, screenshots, etc.
QB_FILE_FILTER_MIN_SIZE_MB = 100

# Log file for the file filter script
QB_FILE_FILTER_LOG_FILE = 'logs/qb_file_filter.log'
```

**Setup Instructions:**
1. Copy `config.py.example` to `config.py`
2. Update all the placeholder values with your actual credentials
3. The `config.py` file is automatically excluded from git commits for security

**GitHub Authentication Setup:**
1. Go to GitHub Settings → Developer settings → Personal access tokens
2. Generate a new token with `repo` permissions
3. Use this token as `GIT_PASSWORD`

**qBittorrent Setup:**
1. Enable Web UI in qBittorrent settings
2. Note the full Web UI URL, username, and password
3. Update the qBittorrent configuration section in `config.py`
4. Set `QB_URL` to the full Web UI address, such as `https://192.168.1.100:8080`; if you omit the scheme, the app tries HTTPS first and then retries HTTP automatically

**Email Setup (Optional):**
1. For Gmail, use an App Password instead of your regular password
2. Enable 2-factor authentication and generate an App Password
3. Update the SMTP configuration section in `config.py`

## Output Structure

### CSV File Columns
The spider generates CSV files with the following columns:
- `href`: The video page URL
- `video-title`: The video title
- `page`: The page number where the entry was found
- `actor`: The main actor/actress name
- `rate`: The rating score
- `comment_number`: Number of user comments/ratings
- `hacked_subtitle`: Magnet link for hacked version with subtitles
- `hacked_no_subtitle`: Magnet link for hacked version without subtitles
- `subtitle`: Magnet link for subtitle version
- `no_subtitle`: Magnet link for regular version (prefers 4K if available)
- `size_hacked_subtitle`, `size_hacked_no_subtitle`, `size_subtitle`, `size_no_subtitle`: Corresponding sizes

### File Locations

All report files are organized under the `reports/` directory:

```
reports/
├── DailyReport/YYYY/MM/         # Daily report CSV files
│   └── Javdb_TodayTitle_YYYYMMDD.csv
├── AdHoc/YYYY/MM/               # Ad hoc report CSV files
│   └── Javdb_AdHoc_*.csv
├── Dedup/                       # Rclone dedup reports
├── parsed_movies_history.csv    # History tracking
└── pikpak_bridge_history.csv    # PikPak transfer history
```

- **Daily Report CSV files**: `reports/DailyReport/YYYY/MM/Javdb_TodayTitle_YYYYMMDD.csv`
- **Ad Hoc CSV files**: `reports/AdHoc/YYYY/MM/Javdb_AdHoc_*.csv`
- **History file**: `reports/parsed_movies_history.csv`
- **PikPak history**: `reports/pikpak_bridge_history.csv`
- **Log files**: `logs/` directory
  - `spider.log`
  - `qb_uploader.log`
  - `pipeline.log`

## History System

The spider includes an intelligent history system that tracks which torrent types have been found for each movie:

### Multiple Torrent Type Tracking
- Tracks ALL available torrent types per movie (e.g., both `hacked_subtitle` and `subtitle`)
- Prevents redundant processing when movies already have complete torrent collections
- Only searches for torrent types that are missing based on preference rules

### Processing Rules
- **Phase 1**: Processes movies with missing torrent types based on preferences
- **Phase 2**: Only processes movies that can be upgraded from `no_subtitle` to `hacked_no_subtitle` or meet quality criteria
- **New Movies**: Always processed regardless of history

### Phase 2 Quality Filtering
Phase 2 includes configurable quality filtering based on user ratings and comment counts:
- **Minimum Rating**: Configurable via `PHASE2_MIN_RATE` (default: 4.0)
- **Minimum Comments**: Configurable via `PHASE2_MIN_COMMENTS` (default: 80)
- **Purpose**: Ensures only high-quality content is processed in phase 2

### Preference Rules
- **Hacked Category**: Always prefer `hacked_subtitle` over `hacked_no_subtitle`
- **Subtitle Category**: Always prefer `subtitle` over `no_subtitle`
- **Complete Collection Goal**: Each movie should have both categories represented

### Release Date Filtering

By default, the spider filters entries based on release date tags ("今日新種" or "昨日新種"). You can override this behavior in two ways:

#### Command-Line Argument (Recommended)
```bash
# Ignore release date tags for a single run
python3 -m apps.cli.spider --ignore-release-date

# Or via pipeline
python pipeline_run_and_notify.py --ignore-release-date
```

#### Configuration File
Set `IGNORE_RELEASE_DATE_FILTER = True` in `config.py` to permanently ignore release date tags.

**Behavior with `--ignore-release-date` or `IGNORE_RELEASE_DATE_FILTER = True`:**
- **Phase 1**: Downloads ALL entries with subtitle tags, regardless of release date
- **Phase 2**: Downloads ALL entries meeting quality criteria (rate > 4.0, comments > 80), regardless of release date

This is useful when:
- You want to backfill your collection with older content
- You're scraping a custom URL (like an actor's page) where release date is not relevant
- You want to download everything matching the quality criteria

### Proxy Support

The system supports both **single proxy** and **proxy pool** modes for improved reliability:

#### Proxy Pool Mode (✨ NEW - Recommended)

Configure multiple proxies for automatic failover:
- **Automatic Switching**: When one proxy fails, automatically switches to another
- **Passive Health Checking**: Only marks proxies as failed on actual failures (no active probing)
- **Cooldown Mechanism**: Failed proxies are temporarily disabled to allow recovery (8 days default)
- **Ban Detection**: Automatically detects proxy bans via HTTP 403 responses and ban-page HTML patterns, immediately bans the proxy and re-queues the page to another worker
- **Session-Scoped Bans**: Proxies banned during a run are skipped only for that session (in-memory). The next run starts with a clean slate—no CSV file, no `ProxyBans` database table, and all proxies are retried automatically
- **Statistics Tracking**: Detailed success rates and usage statistics for each proxy
- **Perfect for JavDB**: Respects strict rate limiting while providing redundancy

See [PROXY_POOL_GUIDE.md](PROXY_POOL_GUIDE.md) for detailed configuration and usage guide.

**Quick Setup:**
```python
# In config.py
PROXY_MODE = 'pool'
PROXY_POOL = [
    {'name': 'Proxy-1', 'http': 'http://127.0.0.1:7890', 'https': 'http://127.0.0.1:7890'},
    {'name': 'Proxy-2', 'http': 'http://127.0.0.1:7891', 'https': 'http://127.0.0.1:7891'},
]
PROXY_POOL_COOLDOWN_SECONDS = 691200  # 8 days cooldown (JavDB bans for 7 days)
PROXY_POOL_MAX_FAILURES = 3  # Max failures before cooldown
```

**Proxy Ban Management:**

The system includes intelligent ban detection and management:
- **Automatic Detection**: Detects when JavDB blocks a proxy IP
- **Session-Scoped State**: Ban state exists only in memory for the current process; it does not persist to `reports/proxy_bans.csv`, SQLite, or the next run
- **8-Day Cooldown**: Default cooldown matches JavDB's 7-day ban period (applies within the session for temporarily disabled proxies)
- **Exit Code 2**: Spider exits with code 2 when proxies are banned (helps with automation)
- **Ban Summary**: Pipeline email reports may include proxy/ban context for that run

**Observing ban activity during a run:**

Review spider log output (e.g. `logs/spider.log`) for ban-related messages. There is no ban CSV file or `ProxyBans` table; the next session always begins with no stored bans.

Commands use `PROXY_MODULES` automatically by default. Use `--use-proxy` to force proxy on for one run, or `--no-proxy` to force it off:
```bash
python3 -m apps.cli.spider
python3 -m apps.cli.spider --use-proxy
python3 -m apps.cli.spider --no-proxy
```
The Web UI and task API now mirror the same tri-state behavior: omit both flags for auto mode, set `use_proxy=true` to force proxy on, or `no_proxy=true` to force it off.

#### Single Proxy Mode (Legacy)

The spider also supports traditional single proxy configuration for HTTP/HTTPS/SOCKS5 proxies. This is useful if:
- JavDB is geo-restricted in your region
- You need to route traffic through a specific network
- You want to use a VPN or proxy service

#### Setup

**1. Configure proxy in `config.py`:**
```python
# HTTP/HTTPS proxy
PROXY_HTTP = 'http://127.0.0.1:7890'
PROXY_HTTPS = 'http://127.0.0.1:7890'

# Or SOCKS5 proxy
PROXY_HTTP = 'socks5://127.0.0.1:1080'
PROXY_HTTPS = 'socks5://127.0.0.1:1080'

# With authentication
PROXY_HTTP = 'http://username:password@proxy.example.com:8080'
PROXY_HTTPS = 'http://username:password@proxy.example.com:8080'

# Control which modules use proxy (modular control)
PROXY_MODULES = ['spider']  # Default: only spider module (includes login)
# PROXY_MODULES = ['all']  # Enable for all modules
# PROXY_MODULES = ['spider', 'qbittorrent']  # Spider and qBittorrent
# PROXY_MODULES = []  # Disable for all modules
```

**2. Optional command-line overrides:**
```bash
# Auto mode: follow PROXY_MODULES from config.py
python3 -m apps.cli.spider

# Force-enable proxy for spider
python3 -m apps.cli.spider --use-proxy

# Force-enable proxy for qBittorrent uploader
python3 -m apps.cli.qb_uploader --use-proxy

# Force-enable proxy for PikPak bridge
python3 -m apps.cli.pikpak_bridge --use-proxy

# Force-disable proxy regardless of PROXY_MODULES
python3 -m apps.cli.spider --no-proxy

# Combine with other options
python3 -m apps.cli.spider --use-proxy --url "https://javdb.com/actors/EvkJ"

# Via pipeline (forces proxy for all components in that run)
python pipeline_run_and_notify.py --use-proxy
```

**Note:** 
- The default behavior is **auto mode**: commands follow `PROXY_MODULES` from `config.py`.
- `--use-proxy` forces proxy on for every module in that command.
- `--no-proxy` forces proxy off for every module in that command.
- If proxy is forced on but no proxy is configured in `config.py`, a warning will be logged.

#### Modular Proxy Control

The `PROXY_MODULES` setting allows fine-grained control over which parts use proxy:

| Module | Description | Use Case |
|--------|-------------|----------|
| `spider` | JavDB Spider | Use proxy to access all JavDB pages (index, detail, login/session refresh) |
| `qbittorrent` | qBittorrent Web UI API | Use proxy for qBittorrent API requests |
| `pikpak` | PikPak bridge qBittorrent API | Use proxy for PikPak bridge operations |
| `all` | All modules | Use proxy for everything |

**Examples:**
```python
# Default: only use proxy for spider module (includes login)
PROXY_MODULES = ['spider']

# Use proxy for everything
PROXY_MODULES = ['all']

# Use proxy for spider and qBittorrent
PROXY_MODULES = ['spider', 'qbittorrent']

# Only use proxy for qBittorrent and PikPak, not spider
PROXY_MODULES = ['qbittorrent', 'pikpak']

# Use proxy for spider only, not qBittorrent/PikPak
PROXY_MODULES = ['spider']

# Disable proxy for all modules by default
PROXY_MODULES = []
```

**Common Scenarios:**
- **Geo-restricted JavDB only**: `PROXY_MODULES = ['spider']`
- **Local qBittorrent behind firewall**: `PROXY_MODULES = ['qbittorrent', 'pikpak']`
- **Everything through proxy**: `PROXY_MODULES = ['all']`

#### Supported Proxy Types
- **HTTP**: `http://proxy.example.com:8080`
- **HTTPS**: `https://proxy.example.com:8080`
- **SOCKS5**: `socks5://proxy.example.com:1080` (requires `requests[socks]` package)

#### Installing SOCKS5 Support
If you want to use SOCKS5 proxy, install the additional dependency:
```bash
pip install requests[socks]
```

#### Troubleshooting Proxy Issues

**Error: 500 Internal Server Error**
- Check if proxy server is running and accessible
- Verify proxy credentials (username/password)
- If password contains special characters, URL-encode them:
  ```python
  from urllib.parse import quote
  password = "My@Pass!"
  encoded = quote(password, safe='')
  print(encoded)  # Output: My%40Pass%21
  ```
- Test proxy manually:
  ```bash
  curl -x http://username:password@proxy:port https://javdb.com
  ```

**Error: Connection refused or timeout**
- Check if proxy server is running: `telnet proxy_ip proxy_port`
- Verify firewall rules allow connection to proxy
- Check if proxy requires authentication

**Proxy works but downloads fail**
- Some proxies don't support magnet links or torrents
- Try different proxy or use direct connection for qBittorrent/PikPak:
  ```python
  PROXY_MODULES = ['spider']
  ```

**Password with special characters**
Common special characters that need URL encoding:
- `@` → `%40`
- `:` → `%3A` (only in password, not after `@`)
- `/` → `%2F`
- `?` → `%3F`
- `#` → `%23`
- `&` → `%26`
- `=` → `%3D`
- `+` → `%2B`
- Space → `%20`
- `!` → `%21`

Example: `http://user:My@Pass!123@proxy:8080` becomes `http://user:My%40Pass%21123@proxy:8080`

### CloudFlare Bypass Support

The system supports integration with [CloudflareBypassForScraping](https://github.com/sarperavci/CloudflareBypassForScraping) for handling CloudFlare protection on JavDB.

#### What is CloudFlare Bypass?

CloudFlare Bypass is an optional feature that helps you access JavDB when CloudFlare protection is enabled. It uses the CloudflareBypassForScraping service which automatically:
- Handles CloudFlare challenges
- Manages cf_clearance cookies
- Provides transparent request forwarding (Request Mirroring mode)

#### Setup

**1. Install CloudflareBypassForScraping:**

```bash
# Clone the repository
git clone https://github.com/sarperavci/CloudflareBypassForScraping.git
cd CloudflareBypassForScraping

# Install dependencies
pip install -r requirements.txt

# Configure (edit config.json if needed)
# Default port is 8000
```

**2. Start the CF Bypass Service:**

```bash
# Local setup (default)
python app.py

# Custom port (update CF_BYPASS_SERVICE_PORT in config.py to match)
python app.py --port 8000
```

**3. Configure Spider:**

Edit `config.py` to set the CF bypass service port:

```python
# CloudFlare Bypass Configuration
CF_BYPASS_SERVICE_PORT = 8000  # Must match the service port
```

**4. CF Bypass Behavior:**

CF bypass is automatically activated as a fallback when direct requests fail during the proxy pool fallback mechanism. By default, each request still starts with direct mode first; you can add `--always-bypass-time [MINUTES]` to temporarily (or session-wide with `0`) keep a proxy on bypass mode after a fallback success.

#### How It Works

When CF bypass is activated during fallback:
1. **Request Mirroring**: Requests are forwarded through the CF bypass service
2. **URL Rewriting**: Original URL `https://javdb.com/page` → `http://localhost:8000/page`
3. **Host Header**: The original hostname is sent via `x-hostname` header
4. **Cookie Management**: CF bypass service handles cf_clearance cookies automatically
5. **Transparent**: Your spider code doesn't need any changes

#### Network Topology

**Local Setup:**
```
Spider → http://localhost:8000 → CloudFlare Bypass Service → https://javdb.com
```

**With Proxy:**
```
Spider → http://proxy_ip:8000 → CF Bypass on Proxy Server → https://javdb.com
```

When using proxy pool, the CF bypass service URL automatically adjusts to match the current proxy IP.

#### Configuration

```python
# In config.py
CF_BYPASS_SERVICE_PORT = 8000  # CF bypass service port (default: 8000)
```

**Service Location Logic:**
- **No Proxy**: Uses `http://localhost:8000`
- **With Proxy Pool**: Uses `http://{proxy_ip}:8000` (extracts IP from current proxy URL)

This allows you to run CF bypass service on the same server as your proxy for better performance.

#### When to Use

Use CloudFlare Bypass when:
- ✅ JavDB shows CloudFlare challenge page
- ✅ You get "Access Denied" or "Checking your browser" errors
- ✅ Direct access works in browser but fails in script
- ✅ Proxy alone doesn't bypass CloudFlare protection

#### Troubleshooting

**Error: "Connection refused to localhost:8000"**
- Make sure CF bypass service is running
- Check if port 8000 is available: `netstat -an | grep 8000`
- Update `CF_BYPASS_SERVICE_PORT` if using different port

**Error: "No movie list found" with CF bypass**
- Check CF bypass service logs for errors
- Verify `x-hostname` header is being sent correctly
- Try restarting the CF bypass service

**CF Bypass + Proxy Not Working**
- Ensure CF bypass service is running on the proxy server
- Verify proxy IP extraction is correct (check logs)
- Test CF bypass service directly: `curl http://proxy_ip:8000/`

#### Performance Notes

- **First Request**: Slower (CF challenge solving)
- **Subsequent Requests**: Fast (cookie cached)
- **Cookie TTL**: Varies (usually hours to days)
- **Overhead**: Minimal after first request

### JavDB Auto Login

The system includes automatic login functionality to maintain session cookies for custom URL scraping.

#### Why Use Auto Login?

When scraping custom URLs (actors, tags, etc.) with `--url` parameter, JavDB requires a valid session cookie. This cookie expires after some time, causing failures with age verification or login issues.

Auto login solves this by:
- ✅ Automatically logging into JavDB
- ✅ Handling age verification automatically
- ✅ Extracting and updating session cookie
- ✅ Supporting captcha (manual input or 2Captcha API)

#### Quick Start

**1. Configure credentials in `config.py`:**

```python
# JavDB login credentials (for automatic session cookie refresh)
JAVDB_USERNAME = 'your_email@example.com'  # or username
JAVDB_PASSWORD = 'your_password'

# Optional: 2Captcha API key for automatic captcha solving
TWOCAPTCHA_API_KEY = ''  # Leave empty for manual captcha input
```

**2. Run the login script:**

```bash
python3 javdb_login.py
```

**3. Enter captcha when prompted:**

The script will:
- Download and save captcha image to `javdb_captcha.png`
- Automatically open the image (if possible)
- Prompt you to enter the captcha code

**4. Use the spider with custom URLs:**

```bash
# Spider with custom URL
python3 -m apps.cli.spider --url "https://javdb.com/actors/RdEb4"

# Pipeline with custom URL
python3 pipeline_run_and_notify.py --url "https://javdb.com/actors/RdEb4"
```

#### Captcha Handling

**Manual Input (Default):**
1. Script downloads captcha image
2. Opens image automatically (platform-dependent)
3. You enter the code when prompted
4. Simple and free

**2Captcha API (Optional):**
1. Sign up at [2Captcha](https://2captcha.com/)
2. Add API key to `config.py`: `TWOCAPTCHA_API_KEY = 'your_key'`
3. Script automatically solves captchas (~$1 per 1000 captchas)
4. Fully automated but costs money

#### Configuration Options

```python
# In config.py

# Login credentials (required)
JAVDB_USERNAME = 'your_email@example.com'
JAVDB_PASSWORD = 'your_password'

# Session cookie (auto-updated by javdb_login.py)
JAVDB_SESSION_COOKIE = ''

# Optional: 2Captcha API key
TWOCAPTCHA_API_KEY = ''  # For automatic captcha solving

# Optional: Manual cookie extraction
# Get from browser DevTools → Application → Cookies → _jdb_session
# JAVDB_SESSION_COOKIE = 'your_session_cookie_here'
```

#### When to Re-run

Re-run `python3 javdb_login.py` when:
- ✅ Session cookie expires (usually after days/weeks)
- ✅ Spider shows "No movie list found" on valid URLs
- ✅ Age verification or login errors appear
- ✅ Before using `--url` parameter for first time

#### Automation (Optional)

**Cron Job (Linux/Mac):**
```bash
# Refresh cookie every 7 days
0 0 */7 * * cd ~/JAVDB_AutoSpider && python3 javdb_login.py >> logs/javdb_login.log 2>&1
```

**Task Scheduler (Windows):**
- Set up scheduled task to run `javdb_login.py` weekly

#### Advanced: OCR-based Captcha Solving

The script includes an optional OCR-based captcha solver in `utils/login/javdb_captcha_solver.py`:

```python
# Free methods (included)
solve_captcha(image_data, method='ocr')      # Local OCR (Tesseract)
solve_captcha(image_data, method='manual')   # Manual input

# Paid method (requires API key)
solve_captcha(image_data, method='2captcha') # 2Captcha API
solve_captcha(image_data, method='auto')     # Try OCR first, fallback to 2Captcha
```

**Installing Tesseract OCR (Optional):**
```bash
# Ubuntu/Debian
sudo apt-get install tesseract-ocr

# macOS
brew install tesseract

# Windows
# Download installer from: https://github.com/UB-Mannheim/tesseract/wiki
```

#### Troubleshooting

**Login Failed - Incorrect Captcha:**
- Captcha is case-sensitive
- Try again for a new captcha
- Consider using 2Captcha API

**Login Failed - Invalid Credentials:**
- Verify username/password in config.py
- Test credentials in browser first
- Check for typos

**Session Cookie Not Working:**
- Verify cookie updated in config.py
- Use same proxy/network for login and spider
- Try logging in again

**For detailed troubleshooting and manual cookie extraction, see [JavDB Login Guide](utils/login/JAVDB_LOGIN_README.md).**

## Downloaded Indicator Feature

The system includes an advanced duplicate download prevention feature that automatically marks downloaded torrents and skips them in future runs.

### Feature Overview

This feature implements automatic marking of downloaded torrents in daily reports and skips these downloaded torrents in the qBittorrent uploader to avoid duplicate downloads. The system also includes enhanced history tracking with create and update timestamps.

### Feature Characteristics

1. **Automatic Detection of Downloaded Torrents**: Automatically identifies which torrents have been downloaded by checking the history CSV file
2. **Add Indicators**: Adds `[DOWNLOADED]` prefix to downloaded torrents in daily report CSV files
3. **Skip Duplicate Downloads**: qBittorrent uploader automatically skips torrents with `[DOWNLOADED]` indicators
4. **Support Multiple Torrent Types**: Supports four types: hacked_subtitle, hacked_no_subtitle, subtitle, no_subtitle
5. **Enhanced History Tracking**: Tracks create_date (first discovery) and update_date (latest modification) for each movie

### Enhanced History Format

The history CSV file now uses an enhanced format with individual columns for each torrent type:

**Old Format:**
```
href,phase,video_code,parsed_date,torrent_type
```

**New Format:**
```
href,phase,video_code,create_date,update_date,last_visited_datetime,hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle
```

- `create_date`: When the movie was first discovered and logged
- `update_date`: When the movie was last updated with new torrent types
- `last_visited_datetime`: When the movie detail page was last visited
- `hacked_subtitle`: Download date for hacked version with subtitles (empty if not downloaded)
- `hacked_no_subtitle`: Download date for hacked version without subtitles (empty if not downloaded)
- `subtitle`: Download date for subtitle version (empty if not downloaded)
- `no_subtitle`: Download date for regular version (empty if not downloaded)
- Backward compatibility is maintained for existing files

### Workflow

1. **Daily Report Generation**: Spider generates daily report CSV file
2. **History Check**: Uploader checks history CSV file when starting
3. **Add Indicators**: Add `[DOWNLOADED]` prefix to downloaded torrents
4. **Skip Processing**: Skip torrents with indicators when reading CSV
5. **Upload New Torrents**: Only upload torrents that haven't been downloaded
6. **Update History**: When new torrent types are found, update_date is modified

### Example Output

**CSV Before Modification:**
```
href,video_code,hacked_subtitle,subtitle
/v/mOJnXY,IPZZ-574,magnet:?xt=...,magnet:?xt=...
```

**CSV After Modification:**
```
href,video_code,hacked_subtitle,subtitle
/v/mOJnXY,IPZZ-574,[DOWNLOADED] magnet:?xt=...,[DOWNLOADED] magnet:?xt=...
```

**History File Format:**
```
href,phase,video_code,create_date,update_date,hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle
/v/mOJnXY,1,IPZZ-574,2025-07-09 20:00:57,2025-07-09 20:05:30,2025-07-09 20:05:30,,2025-07-09 20:05:30,
```

**Uploader Log:**
```
2025-07-09 22:09:23,182 - INFO - Adding downloaded indicators to CSV file...
2025-07-09 22:09:23,183 - INFO - Added downloaded indicators to Daily Report/Javdb_TodayTitle_20250709.csv
2025-07-09 22:09:23,183 - INFO - Found 0 torrent links in Daily Report/Javdb_TodayTitle_20250709.csv
2025-07-09 22:09:23,183 - INFO - Skipped 20 already downloaded torrents
```

### Important Notes

1. **History File Dependency**: Feature depends on `reports/parsed_movies_history.csv` file
2. **Indicator Format**: Downloaded indicator format is `[DOWNLOADED] ` (note the space)
3. **Backward Compatibility**: If history file doesn't exist, feature will gracefully degrade without affecting normal use
4. **Performance Optimization**: History check uses efficient CSV reading, won't significantly impact performance
5. **Timestamp Tracking**: create_date remains constant while update_date changes with each modification
6. **Torrent Type Merging**: When updating existing records, new torrent types are merged with existing ones

### Migration

The system automatically handles migration from the old format (`parsed_date`) to the new format (`create_date`, `update_date`). Existing files are automatically converted with backward compatibility.

This feature ensures system stability and efficiency, avoiding duplicate downloads while maintaining comprehensive history tracking with enhanced timestamp management.

## Migration Scripts

- **`packages/python/javdb_migrations/migrate_to_current.py`** — primary entry for SQLite schema upgrades, optional datetime normalization, and actor backfill (see `--help`).
- **`packages/python/javdb_migrations/tools/`** — one-off and legacy helpers (CSV cleanup, old format conversion, `csv_to_sqlite`, older version jumps, etc.).

### Available Scripts (tools/)

**cleanup_history_priorities.py**
- Removes duplicate entries from history file
- Ensures data integrity
- Safe to run multiple times

**update_history_format.py**
- Migrates old history format to new format
- Converts `parsed_date` to `create_date`/`update_date`
- Automatic backward compatibility

**rename_columns_add_last_visited.py**
- Renames date columns and adds `last_visited_datetime` field
- Required when upgrading to support the new history format

**migrate_reports_to_dated_dirs.py**
- Migrates flat report files into `YYYY/MM/` dated subdirectories
- Required when upgrading to the new reports directory structure

**reclassify_c_hacked_torrents.py**
- Reclassifies torrents with specific naming patterns
- Updates torrent type classification
- Useful after classification rule changes

### When to Use

Run migration scripts when:
- ✅ Upgrading from older versions
- ✅ History file shows duplicate entries
- ✅ Format changes are introduced
- ✅ Data cleanup is needed

### How to Run

From the repository root:

```bash
python3 packages/python/javdb_migrations/tools/cleanup_history_priorities.py
python3 packages/python/javdb_migrations/tools/update_history_format.py
python3 packages/python/javdb_migrations/tools/rename_columns_add_last_visited.py
python3 packages/python/javdb_migrations/tools/reclassify_c_hacked_torrents.py
python3 packages/python/javdb_migrations/tools/migrate_reports_to_dated_dirs.py --dry-run
```

**Note:** Always backup your `reports/parsed_movies_history.csv` before running migration scripts.

## Logging

The system provides comprehensive logging:
- **INFO**: General progress information with tracking
- **WARNING**: Non-critical issues
- **DEBUG**: Detailed debugging information
- **ERROR**: Critical errors

Progress tracking includes:
- `[Page 1/5]` - Page-level progress
- `[15/75]` - Entry-level progress across all pages
- `[1/25]` - Upload progress for qBittorrent

## Troubleshooting

### Common Issues

**Spider Issues:**
- **No entries found**: Check if the website structure has changed
- **Connection errors**: Verify internet connection and website accessibility
- **CSV not generated**: Check if the `reports/DailyReport` directory exists

**qBittorrent Issues:**
- **Cannot connect**: Check if qBittorrent is running and Web UI is enabled
- **Login failed**: Verify username and password in configuration
- **CSV file not found**: Run the spider first to generate the CSV file

**Git Issues:**
- **Authentication failed**: Verify username and token/password
- **Repository not found**: Check repository URL and access permissions
- **Branch issues**: Ensure the branch exists in your repository

**Downloaded Indicator Issues:**
- **Indicators not added**: Check if history file exists and has correct format
- **Uploader skipping too many torrents**: Check if history file contains outdated records
- **Import errors**: Ensure `utils/history_manager.py` file exists
- **History format issues**: Ensure history file has correct column structure with backward compatibility

**JavDB Login Issues:**
- **Login failed**: Check credentials in config.py
- **Captcha errors**: Try again for new captcha, or use 2Captcha API
- **Cookie not working**: Verify cookie updated in config.py, use same proxy for login and spider
- **See [JavDB Login Guide](utils/login/JAVDB_LOGIN_README.md) for detailed troubleshooting**

**CloudFlare Bypass Issues:**
- **Connection refused**: Ensure CF bypass service is running
- **Port errors**: Verify CF_BYPASS_SERVICE_PORT matches service port
- **No movie list found**: Check CF bypass service logs
- **Proxy + CF not working**: Ensure CF bypass service runs on proxy server

**Proxy Ban Issues:**
- **All proxies banned during a run**: Check spider logs; bans are session-only, so a new run retries all proxies from a clean slate—add more proxies or address JavDB-side blocks if needed
- **Spider exits with code 2**: Proxy ban detected during this session; cooldowns apply in-memory for that run, or add new proxies
- **Cooldown not working**: Default is 8 days, adjust PROXY_POOL_COOLDOWN_SECONDS if needed
- **Ban false positives**: Check if JavDB is actually accessible from proxy IP

### Debug Mode

To see detailed operations, you can temporarily increase logging level in the scripts:

```python
# In config.py
LOG_LEVEL = 'DEBUG'  # Shows detailed debug information
```

## Security Notes

- **Configuration file**: `config.py` is automatically excluded from git commits (check `.gitignore`)
- **Never commit credentials**: GitHub tokens, passwords, API keys should stay in `config.py` only
- **GitHub authentication**: Use personal access tokens instead of passwords
- **JavDB credentials**: Only stored locally in `config.py`, never transmitted except to JavDB
- **PikPak credentials**: Stored in `config.py`, used only for PikPak API
- **2Captcha API key**: Optional, only used if configured for automatic captcha solving
- **Proxy passwords**: Use URL encoding for special characters in passwords
- **Session cookies**: Auto-updated by login script, expire after some time
- **Sensitive logs**: Pipeline automatically masks sensitive info in logs and emails
- **Environment variables (optional)**: Consider for production deployments
  ```python
  import os
  JAVDB_USERNAME = os.getenv('JAVDB_USER', '')
  JAVDB_PASSWORD = os.getenv('JAVDB_PASS', '')
  ```

## Notes

### Rate Limiting and Delays
- The system includes delays between requests to be respectful to servers:
  - **Index pages**: 2 seconds (configurable via `PAGE_SLEEP`)
  - **Movies**: 5-15 seconds random (configurable via `MOVIE_SLEEP_MIN` / `MOVIE_SLEEP_MAX`)
  - **Volume-based adjustment**: `MovieSleepManager` automatically increases sleep intervals when processing large batches
  - **qBittorrent additions**: 1 second (configurable via `DELAY_BETWEEN_ADDITIONS`)
  - **PikPak requests**: 2 seconds default (configurable via `PIKPAK_REQUEST_DELAY`)

### System Behavior
- The system uses proper headers to mimic a real browser
- CSV files are automatically saved to the `reports/DailyReport/YYYY/MM/` or `reports/AdHoc/YYYY/MM/` directory
- The pipeline provides incremental commits for monitoring progress in real-time
- History file tracks all downloaded movies with timestamps
- Rust acceleration is automatically detected and used when available
- Exit code 2 indicates proxy ban detection during the current run; bans are session-scoped and do not persist to the next run (useful for automation)
- Logs automatically mask sensitive information (passwords, tokens, etc.)

### File Structure
- **apps/cli/**: Canonical CLI entrypoints for spider, pipeline, migration, qBittorrent, PikPak, email, login, health check, and helper utilities
- **apps/api/**: FastAPI REST API layer
  - `server.py`: thin ASGI bootstrap alias for the canonical runtime module
  - `routers/`: real `APIRouter` groups for auth, config, tasks, explore, and system endpoints
  - `schemas/`: canonical Pydantic request/response models
  - `infra/`: shared auth, token, rate-limit, CSRF, and URL/file guard helpers
  - `services/`: split business services; `services/runtime.py` is now bootstrap + compatibility facade only
- **apps/web/** / **apps/desktop/**: Canonical web UI and Electron shell applications
- **packages/python/javdb_spider/**: Spider package (modular architecture)
  - `__main__.py`: Package entry point (`python3 -m apps.cli.spider`)
  - `app/`: CLI and top-level runtime orchestration (`cli.py`, `main.py`)
  - `runtime/`: Config, mutable state, adaptive sleep, and reporting
  - `fetch/`: Index fetch, fallback flow, session/login coordination, and detail fetch backends
  - `detail/`: Unified detail-stage runner with thin parallel/sequential compatibility wrappers
  - `services/`: Spider-specific domain services such as dedup and rclone filtering
  - `compat/`: Compatibility exports such as CSV builder facade
- **packages/rust/javdb_rust_core/**: Rust acceleration extension (PyO3 + maturin)
  - `src/scraper/`: HTML parsing (index, detail, category pages)
  - `src/proxy/`: Proxy pool, ban manager, masking
  - `src/requester/`: HTTP request handler
  - `src/history/`: History CSV management
  - `src/csv_writer.rs`, `src/magnet_extractor.rs`, `src/url_helper.rs`
- **scripts/** / **pipeline.py** / **migration/** / **api/**: Compatibility wrappers that forward to the canonical `apps/` and `packages/` layout
- **reports/**: Contains all report files and history
  - `DailyReport/YYYY/MM/`: Daily scraping results
  - `AdHoc/YYYY/MM/`: Custom URL scraping results
  - `parsed_movies_history.csv`: History tracking
  - `pikpak_bridge_history.csv`: PikPak transfer history
- **logs/**: Contains all log files
  - `spider.log`: Spider execution logs
  - `qb_uploader.log`: Upload execution logs
  - `pipeline.log`: Pipeline execution logs
  - `pikpak_bridge.log`: PikPak bridge execution logs
  - `qb_file_filter.log`: File filter execution logs
- **migration/**: `migrate_to_current.py` (main DB migration); **packages/python/javdb_migrations/tools/** for ad hoc / legacy scripts
- **utils/**: Compatibility re-export layer for legacy imports
  - `infra/`, `domain/`, `bridges/`: forward to `packages/python/javdb_platform/`, `javdb_core/`, and related canonical packages
  - Top-level compatibility modules retained for stable legacy imports: `history_manager.py`, `parser.py`, `proxy_ban_manager.py`, `rclone_helper.py`, `spider_gateway.py`, `sqlite_datetime.py`
- **tests/**: Structured test suite
  - `tests/unit/`: module-level unit tests
  - `tests/integration/`: multi-module and API/runtime integration coverage
  - `tests/smoke/`: CLI/entrypoint/backends smoke coverage
- **utils/login/**: JavDB login related files and documentation
- **docker/**: Docker configuration files

## Quick Reference

### Common Commands

```bash
# Basic daily scraping
python3 -m apps.cli.spider
python3 qbtorrent_uploader.py

# Full automated pipeline
python3 pipeline_run_and_notify.py

# Scrape with proxy
python3 -m apps.cli.spider --use-proxy
python3 pipeline_run_and_notify.py --use-proxy

# Scrape with proxy (CF bypass activates automatically as fallback)
python3 -m apps.cli.spider --use-proxy
python3 pipeline_run_and_notify.py --use-proxy

# Custom URL scraping (requires login)
python3 javdb_login.py  # First time setup
python3 -m apps.cli.spider --url "https://javdb.com/actors/RdEb4"
python3 pipeline_run_and_notify.py --url "https://javdb.com/actors/RdEb4"

# Scrape ignoring release date
python3 -m apps.cli.spider --ignore-release-date --phase 1
python3 pipeline_run_and_notify.py --ignore-release-date

# Ad hoc mode
python3 -m apps.cli.spider --url "https://javdb.com/tags/xyz"
python3 qbtorrent_uploader.py --mode adhoc

# PikPak bridge
python3 pikpak_bridge.py  # Default: 3 days, batch mode
python3 pikpak_bridge.py --days 7 --individual  # Custom days, individual mode

# qBittorrent File Filter
python3 -m apps.cli.qb_file_filter  # Default threshold from config (100MB if unset)
python3 -m apps.cli.qb_file_filter --min-size 50  # Stricter: < 50MB
python3 -m apps.cli.qb_file_filter --min-size 100 --days 3 --dry-run  # Preview mode
```

### Configuration Files

- **Main config**: `config.py` (copy from `config.py.example`)
- **History file**: `reports/parsed_movies_history.csv`
- **Login docs**: `utils/login/JAVDB_LOGIN_README.md`

### Important Links

- [CloudFlare Bypass Service](https://github.com/sarperavci/CloudflareBypassForScraping)
- [2Captcha API](https://2captcha.com/) (optional, for automatic captcha solving)
- [JavDB Login Guide](utils/login/JAVDB_LOGIN_README.md)
- [Rust Installation Guide (macOS)](docs/RUST_INSTALLATION_MAC.md)
- [API Usage Guide](docs/API_USAGE_GUIDE.md)

## Contributing

Contributions are welcome! Please feel free to submit issues or pull requests.

## License

This project is for educational and personal use only. Please respect the terms of service of the websites you scrape.
