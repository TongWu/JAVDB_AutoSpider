# JavDB Auto Spider

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/TongWu/JAVDB_AutoSpider)
[![JavDB Daily Ingestion Pipeline](https://github.com/TongWu/JAVDB_AutoSpider/actions/workflows/DailyIngestion.yml/badge.svg)](https://github.com/TongWu/JAVDB_AutoSpider/actions/workflows/DailyIngestion.yml)
[![codecov](https://codecov.io/gh/TongWu/JAVDB_AutoSpider/branch/main/graph/badge.svg)](https://codecov.io/gh/TongWu/JAVDB_AutoSpider)

A comprehensive Python automation system for extracting torrent links from javdb.com and automatically adding them to qBittorrent. The system includes intelligent history tracking, git integration, automated pipeline execution, and duplicate download prevention.

It can be played as an ingestion pipeline before the automated scrapping platform for JAV (e.g. [MDC-NG](https://github.com/mdc-ng/mdc-ng)).

English | [简体中文](README_CN.md)

## Features

### Core Spider Functionality
- Fetches data in real-time from `javdb.com/?vft=2` to `javdb.com/?page=5&vft=2`
- Filters entries with both "含中字磁鏈" and "今日新種" tags (supports multiple language variations)
- Extracts magnet links based on specific categories with priority ordering
- Saves results to timestamped CSV files in "Daily Report" directory
- Comprehensive logging with different levels (INFO, WARNING, DEBUG, ERROR)
- Multi-page processing with progress tracking
- Additional metadata extraction (actor, rating, comment count)

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
- Saves results to `Daily Report/` directory
- Checks history by default to avoid re-downloading
- Uses "JavDB" category in qBittorrent

#### Ad Hoc Mode (Custom URL)
- Activated with `--url` parameter for custom URLs (actors, tags, etc.)
- Saves results to `Ad Hoc/` directory
- **Now checks history by default** to skip already downloaded entries
- Use `--ignore-history` to re-download everything
- Uses "Ad Hoc" category in qBittorrent
- Example: `python Javdb_Spider.py --url "https://javdb.com/actors/EvkJ"`

### qBittorrent Integration
- Automatically reads current date's CSV file
- Connects to qBittorrent via Web UI API
- Adds torrents with proper categorization and settings
- Comprehensive logging and progress tracking
- Detailed summary reports

### qBittorrent File Filter
- Automatically filters small files from recently added torrents
- Configurable minimum file size threshold (default: 50MB)
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
- Enable with `--use-cf-bypass` flag

## Installation

1. Install Python dependencies:
```bash
pip install -r requirements.txt
```

2. (Optional) Install SOCKS5 proxy support if you want to use SOCKS5 proxies:
```bash
pip install requests[socks]
```

3. Configure the system by copying and editing the configuration file:
```bash
cp config.py.example config.py
```

4. (Optional) For CloudFlare bypass feature, install and run [CloudflareBypassForScraping](https://github.com/sarperavci/CloudflareBypassForScraping) service:
```bash
# See CloudFlare Bypass section below for setup instructions
```

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

For detailed Docker documentation, see [DOCKER_README.md](docs/DOCKER_README.md) or [DOCKER_QUICKSTART.md](docs/DOCKER_QUICKSTART.md).

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
docker exec javdb-spider python scripts/spider.py --use-proxy

# Run pipeline manually
docker exec javdb-spider python pipeline.py

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
SPIDER_COMMAND=cd /app && /usr/local/bin/python scripts/spider.py --use-proxy >> /var/log/cron.log 2>&1

# Pipeline runs daily at 4:00 AM
CRON_PIPELINE=0 4 * * *
PIPELINE_COMMAND=cd /app && /usr/local/bin/python pipeline.py >> /var/log/cron.log 2>&1
```

After modifying `.env`, restart the container:
```bash
docker-compose -f docker/docker-compose.yml restart
```

### Individual Scripts (Local Installation)

**Run the spider to extract data:**
```bash
python Javdb_Spider.py
```

**Run the qBittorrent uploader:**
```bash
# Daily mode (default)
python qbtorrent_uploader.py

# Ad hoc mode (for custom URL scraping results)
python qbtorrent_uploader.py --mode adhoc

# Use proxy for qBittorrent API requests
python qbtorrent_uploader.py --use-proxy
```

**Run the qBittorrent File Filter (filter out small files):**
```bash
# Default: filter files smaller than 50MB from last 2 days
python scripts/qb_file_filter.py --min-size 50

# Custom threshold and days
python scripts/qb_file_filter.py --min-size 100 --days 3

# Dry run (preview without changes)
python scripts/qb_file_filter.py --min-size 50 --dry-run

# Filter specific category only
python scripts/qb_file_filter.py --min-size 50 --category JavDB

# With proxy
python scripts/qb_file_filter.py --min-size 50 --use-proxy
```

**Run the PikPak bridge (transfer old torrents from qBittorrent to PikPak):**
```bash
# Default: process torrents older than 3 days in batch mode
python pikpak_bridge.py

# Custom days threshold
python pikpak_bridge.py --days 7

# Dry run mode (test without actual transfers)
python pikpak_bridge.py --dry-run

# Individual mode (process torrents one by one instead of batch)
python pikpak_bridge.py --individual

# Use proxy for qBittorrent API requests
python pikpak_bridge.py --use-proxy

# Combine options
python pikpak_bridge.py --days 5 --dry-run --use-proxy
```

### Command-Line Arguments

The JavDB Spider supports various command-line arguments for customization:

#### Basic Options
```bash
# Dry run mode (no CSV file written)
python Javdb_Spider.py --dry-run

# Specify custom output filename
python Javdb_Spider.py --output-file my_results.csv

# Custom page range
python Javdb_Spider.py --start-page 3 --end-page 10

# Parse all pages until empty page is found
python Javdb_Spider.py --all
```

#### Phase Control
```bash
# Run only Phase 1 (subtitle + today/yesterday tags)
python Javdb_Spider.py --phase 1

# Run only Phase 2 (today/yesterday tags with quality filter)
python Javdb_Spider.py --phase 2

# Run both phases (default)
python Javdb_Spider.py --phase all
```

#### History Control
```bash
# Ignore history file and scrape all pages (for both daily and ad hoc modes)
python Javdb_Spider.py --ignore-history

# Custom URL scraping (creates "Ad Hoc" directory, checks history by default)
python Javdb_Spider.py --url "https://javdb.com/?vft=2"

# Custom URL scraping, ignoring history to re-download everything
python Javdb_Spider.py --url "https://javdb.com/actors/EvkJ" --ignore-history

# Ignore today/yesterday release date tags and process all matching entries
python Javdb_Spider.py --ignore-release-date

# Use proxy for all HTTP requests
python Javdb_Spider.py --use-proxy
```

#### Complete Examples
```bash
# Quick test run with limited pages
python Javdb_Spider.py --start-page 1 --end-page 3 --dry-run

# Full scrape ignoring history
python Javdb_Spider.py --all --ignore-history

# Custom URL with specific output file
python Javdb_Spider.py --url "https://javdb.com/?vft=2" --output-file custom_results.csv

# Phase 1 only with custom page range
python Javdb_Spider.py --phase 1 --start-page 5 --end-page 15

# Download all subtitle entries regardless of release date
python Javdb_Spider.py --ignore-release-date --phase 1

# Download all high-quality entries regardless of release date
python Javdb_Spider.py --ignore-release-date --phase 2 --start-page 1 --end-page 10

# Ad hoc mode: Download specific actor's movies (skips already downloaded)
python Javdb_Spider.py --url "https://javdb.com/actors/EvkJ" --ignore-release-date

# Ad hoc mode: Re-download everything from an actor (ignores history)
python Javdb_Spider.py --url "https://javdb.com/actors/EvkJ" --ignore-history --ignore-release-date

# Use proxy to access JavDB (useful for geo-restricted regions)
python Javdb_Spider.py --use-proxy --start-page 1 --end-page 5

# Combine multiple options: proxy + custom URL + ignore release date
python Javdb_Spider.py --url "https://javdb.com/actors/EvkJ" --use-proxy --ignore-release-date
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
| `--use-proxy` | Enable proxy from config.py | False | `--use-proxy` |
| `--use-cf-bypass` | Use CloudFlare bypass service | False | `--use-cf-bypass` |

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
```bash
# View ban records
cat "Daily Report/proxy_bans.csv"

# Ban information is also included in pipeline email reports
```

**Run Migration Scripts:**
```bash
cd migration

# Clean up duplicate history entries
python3 cleanup_history_priorities.py

# Update history file format (if upgrading from older version)
python3 update_history_format.py

# Reclassify torrents (after classification rule changes)
python3 reclassify_c_hacked_torrents.py
```

### Automated Pipeline

**Run the complete workflow:**
```bash
# Basic pipeline run
python pipeline_run_and_notify.py

# Pipeline with custom arguments (passed to Javdb_Spider)
python pipeline_run_and_notify.py --start-page 1 --end-page 5

# Pipeline ignoring release date tags
python pipeline_run_and_notify.py --ignore-release-date --phase 1

# Pipeline with custom URL
python pipeline_run_and_notify.py --url "https://javdb.com/actors/EvkJ"

# Pipeline with proxy enabled
python pipeline_run_and_notify.py --use-proxy

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

**Note**: The pipeline accepts the same arguments as `Javdb_Spider.py` and passes them through automatically. Additional pipeline-specific arguments include `--pikpak-individual` for PikPak Bridge mode control.

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
QB_HOST = 'your_qbittorrent_ip'
QB_PORT = 'your_qbittorrent_port'
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

# Modular proxy control - which modules use proxy
PROXY_MODULES = ['all']  # 'all' or list: 'spider_index', 'spider_detail', 'spider_age_verification', 'qbittorrent', 'pikpak'

# =============================================================================
# SPIDER CONFIGURATION
# =============================================================================
START_PAGE = 1
END_PAGE = 20
BASE_URL = 'https://javdb.com'

# Phase 2 filtering criteria
PHASE2_MIN_RATE = 4.0  # Minimum rating score for phase 2 entries
PHASE2_MIN_COMMENTS = 80  # Minimum comment count for phase 2 entries

# Release date filter
IGNORE_RELEASE_DATE_FILTER = False  # Set True to ignore today/yesterday tags

# Sleep time configuration (in seconds)
DETAIL_PAGE_SLEEP = 5  # Sleep before parsing detail pages
PAGE_SLEEP = 2  # Sleep between index pages
MOVIE_SLEEP = 1  # Sleep between movies

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
DAILY_REPORT_DIR = 'Daily Report'
AD_HOC_DIR = 'Ad Hoc'
PARSED_MOVIES_CSV = 'parsed_movies_history.csv'

# =============================================================================
# PIKPAK CONFIGURATION (for PikPak Bridge)
# =============================================================================

# PikPak login credentials
PIKPAK_EMAIL = 'your_pikpak_email@example.com'
PIKPAK_PASSWORD = 'your_pikpak_password'

# PikPak settings
PIKPAK_LOG_FILE = 'logs/pikpak_bridge.log'
PIKPAK_REQUEST_DELAY = 3  # Delay between requests (seconds) to avoid rate limiting

# =============================================================================
# qBittorrent File Filter Configuration
# =============================================================================

# Minimum file size threshold in MB
# Files smaller than this will be set to "do not download" priority
# This helps filter out small files like NFO, samples, screenshots, etc.
QB_FILE_FILTER_MIN_SIZE_MB = 50

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
2. Note the IP address, port, username, and password
3. Update the qBittorrent configuration section in `config.py`

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

CSV report files are organized by year and month in dated subdirectories:

- **Daily Report CSV files**: `Daily Report/YYYY/MM/Javdb_TodayTitle_YYYYMMDD.csv`
- **Ad Hoc CSV files**: `Ad Hoc/YYYY/MM/Javdb_AdHoc_*.csv`
- **History file**: `Daily Report/parsed_movies_history.csv` (stays at root level)
- **PikPak history**: `Daily Report/pikpak_bridge_history.csv` (stays at root level)
- **Proxy ban records**: `Daily Report/proxy_bans.csv` (stays at root level)
- **Log files**: `logs/` directory
  - `Javdb_Spider.log`
  - `qbtorrent_uploader.log`
  - `pipeline_run_and_notify.log`

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
python Javdb_Spider.py --ignore-release-date

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
- **Ban Detection**: Automatically detects when proxies are banned by JavDB
- **Persistent Ban Records**: Ban history stored in `Daily Report/proxy_bans.csv` and persists across runs
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
- **Persistent Records**: Ban history stored in `Daily Report/proxy_bans.csv`
- **8-Day Cooldown**: Default cooldown matches JavDB's 7-day ban period
- **Exit Code 2**: Spider exits with code 2 when proxies are banned (helps with automation)
- **Ban Summary**: Detailed ban status included in pipeline email reports

**Checking Ban Status:**
```bash
# Ban records are logged in:
cat "Daily Report/proxy_bans.csv"

# Pipeline emails include ban summary with:
# - Proxy name and IP
# - Ban timestamp
# - Cooldown expiry time
# - Current status (BANNED/AVAILABLE)
```

Then run with `--use-proxy` flag:
```bash
python Javdb_Spider.py --use-proxy
```

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
PROXY_MODULES = ['all']  # Enable for all modules
# PROXY_MODULES = ['spider_index', 'spider_detail']  # Only index and detail pages
# PROXY_MODULES = ['spider_detail']  # Only detail pages
# PROXY_MODULES = []  # Disable for all modules
```

**2. Enable proxy with command-line flag:**
```bash
# Enable proxy for spider
python Javdb_Spider.py --use-proxy

# Enable proxy for qBittorrent uploader
python qbtorrent_uploader.py --use-proxy

# Enable proxy for PikPak bridge
python pikpak_bridge.py --use-proxy

# Combine with other options
python Javdb_Spider.py --use-proxy --url "https://javdb.com/actors/EvkJ"

# Via pipeline (enables proxy for all components)
python pipeline_run_and_notify.py --use-proxy
```

**Note:** 
- Proxy is **disabled by default**. You must use `--use-proxy` to enable it.
- If `--use-proxy` is set but no proxy is configured in `config.py`, a warning will be logged.
- You can control which parts of the spider use proxy via `PROXY_MODULES` configuration.

#### Modular Proxy Control

The `PROXY_MODULES` setting allows fine-grained control over which parts use proxy:

| Module | Description | Use Case |
|--------|-------------|----------|
| `spider_index` | Index/listing pages | Use proxy to access main listing pages |
| `spider_detail` | Movie detail pages | Use proxy for individual movie pages |
| `spider_age_verification` | Age verification bypass | Use proxy for age verification requests |
| `qbittorrent` | qBittorrent Web UI API | Use proxy for qBittorrent API requests |
| `pikpak` | PikPak bridge qBittorrent API | Use proxy for PikPak bridge operations |
| `all` | All modules | Use proxy for everything (default) |

**Examples:**
```python
# Use proxy for everything
PROXY_MODULES = ['all']

# Only use proxy for detail pages (save bandwidth on index pages)
PROXY_MODULES = ['spider_detail']

# Use proxy for index and detail, but not age verification
PROXY_MODULES = ['spider_index', 'spider_detail']

# Only use proxy for qBittorrent and PikPak, not spider
PROXY_MODULES = ['qbittorrent', 'pikpak']

# Use proxy for spider only, not qBittorrent/PikPak
PROXY_MODULES = ['spider_index', 'spider_detail', 'spider_age_verification']

# Disable proxy for all modules (even if --use-proxy is set)
PROXY_MODULES = []
```

**Common Scenarios:**
- **Geo-restricted JavDB only**: `PROXY_MODULES = ['spider_index', 'spider_detail', 'spider_age_verification']`
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
  PROXY_MODULES = ['spider_index', 'spider_detail', 'spider_age_verification']
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

**4. Run Spider with CF Bypass:**

```bash
# Enable CF bypass for spider
python Javdb_Spider.py --use-cf-bypass

# Combine with proxy
python Javdb_Spider.py --use-proxy --use-cf-bypass

# Via pipeline
python pipeline_run_and_notify.py --use-cf-bypass
```

#### How It Works

When `--use-cf-bypass` is enabled:
1. **Request Mirroring**: All requests are forwarded through the CF bypass service
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
python3 Javdb_Spider.py --url "https://javdb.com/actors/RdEb4"

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
href,phase,video_code,create_date,update_date,hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle
```

- `create_date`: When the movie was first discovered and logged
- `update_date`: When the movie was last updated with new torrent types
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

1. **History File Dependency**: Feature depends on `Daily Report/parsed_movies_history.csv` file
2. **Indicator Format**: Downloaded indicator format is `[DOWNLOADED] ` (note the space)
3. **Backward Compatibility**: If history file doesn't exist, feature will gracefully degrade without affecting normal use
4. **Performance Optimization**: History check uses efficient CSV reading, won't significantly impact performance
5. **Timestamp Tracking**: create_date remains constant while update_date changes with each modification
6. **Torrent Type Merging**: When updating existing records, new torrent types are merged with existing ones

### Migration

The system automatically handles migration from the old format (`parsed_date`) to the new format (`create_date`, `update_date`). Existing files are automatically converted with backward compatibility.

This feature ensures system stability and efficiency, avoiding duplicate downloads while maintaining comprehensive history tracking with enhanced timestamp management.

## Migration Scripts

The `migration/` directory contains utility scripts for maintaining and upgrading the system:

### Available Scripts

**cleanup_history_priorities.py**
- Removes duplicate entries from history file
- Ensures data integrity
- Safe to run multiple times

**update_history_format.py**
- Migrates old history format to new format
- Converts `parsed_date` to `create_date`/`update_date`
- Automatic backward compatibility

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

```bash
cd migration
python3 cleanup_history_priorities.py
python3 update_history_format.py
python3 reclassify_c_hacked_torrents.py
```

**Note:** Always backup your `Daily Report/parsed_movies_history.csv` before running migration scripts.

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
- **CSV not generated**: Check if the "Daily Report" directory exists

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
- **All proxies banned**: Check `Daily Report/proxy_bans.csv` for ban status
- **Spider exits with code 2**: Indicates proxy ban detected, wait for cooldown or add new proxies
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
  - **Detail pages**: 5 seconds (configurable via `DETAIL_PAGE_SLEEP`)
  - **Index pages**: 2 seconds (configurable via `PAGE_SLEEP`)
  - **Movies**: 1 second (configurable via `MOVIE_SLEEP`)
  - **qBittorrent additions**: 1 second (configurable via `DELAY_BETWEEN_ADDITIONS`)
  - **PikPak requests**: 3 seconds (configurable via `PIKPAK_REQUEST_DELAY`)

### System Behavior
- The system uses proper headers to mimic a real browser
- CSV files are automatically saved to the "Daily Report" or "Ad Hoc" directory
- The pipeline provides incremental commits for monitoring progress in real-time
- History file tracks all downloaded movies with timestamps
- Exit code 2 indicates proxy ban detection (useful for automation)
- Logs automatically mask sensitive information (passwords, tokens, etc.)

### File Structure
- **Daily Report/**: Contains daily scraping results and history
  - `YYYY/MM/`: Dated subdirectories for CSV report files
  - `parsed_movies_history.csv`: History tracking (at root level)
  - `pikpak_bridge_history.csv`: PikPak transfer history (at root level)
  - `proxy_bans.csv`: Proxy ban records (at root level)
- **Ad Hoc/**: Contains custom URL scraping results
  - `YYYY/MM/`: Dated subdirectories for CSV report files
- **logs/**: Contains all log files
  - `Javdb_Spider.log`: Spider execution logs
  - `qbtorrent_uploader.log`: Upload execution logs
  - `pipeline_run_and_notify.log`: Pipeline execution logs
  - `qb_pikpak.log`: PikPak bridge execution logs
  - `qb_file_filter.log`: File filter execution logs
  - `proxy_bans.csv`: Proxy ban history (persistent across runs)
- **migration/**: Contains database migration scripts
- **utils/**: Utility modules (history, parser, proxy pool, etc.)
- **utils/login/**: JavDB login related files and documentation

## Quick Reference

### Common Commands

```bash
# Basic daily scraping
python3 Javdb_Spider.py
python3 qbtorrent_uploader.py

# Full automated pipeline
python3 pipeline_run_and_notify.py

# Scrape with proxy
python3 Javdb_Spider.py --use-proxy
python3 pipeline_run_and_notify.py --use-proxy

# Scrape with CloudFlare bypass
python3 Javdb_Spider.py --use-cf-bypass
python3 pipeline_run_and_notify.py --use-proxy --use-cf-bypass

# Custom URL scraping (requires login)
python3 javdb_login.py  # First time setup
python3 Javdb_Spider.py --url "https://javdb.com/actors/RdEb4"
python3 pipeline_run_and_notify.py --url "https://javdb.com/actors/RdEb4"

# Scrape ignoring release date
python3 Javdb_Spider.py --ignore-release-date --phase 1
python3 pipeline_run_and_notify.py --ignore-release-date

# Ad hoc mode
python3 Javdb_Spider.py --url "https://javdb.com/tags/xyz"
python3 qbtorrent_uploader.py --mode adhoc

# PikPak bridge
python3 pikpak_bridge.py  # Default: 3 days, batch mode
python3 pikpak_bridge.py --days 7 --individual  # Custom days, individual mode

# qBittorrent File Filter
python3 scripts/qb_file_filter.py --min-size 50  # Filter files < 50MB
python3 scripts/qb_file_filter.py --min-size 100 --days 3 --dry-run  # Preview mode
```

### Configuration Files

- **Main config**: `config.py` (copy from `config.py.example`)
- **History file**: `Daily Report/parsed_movies_history.csv`
- **Ban records**: `Daily Report/proxy_bans.csv`
- **Login docs**: `utils/login/JAVDB_LOGIN_README.md`

### Important Links

- [CloudFlare Bypass Service](https://github.com/sarperavci/CloudflareBypassForScraping)
- [2Captcha API](https://2captcha.com/) (optional, for automatic captcha solving)
- [JavDB Login Guide](utils/login/JAVDB_LOGIN_README.md)

## Contributing

Contributions are welcome! Please feel free to submit issues or pull requests.

## License

This project is for educational and personal use only. Please respect the terms of service of the websites you scrape.
