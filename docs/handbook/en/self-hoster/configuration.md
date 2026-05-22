# Configuration Reference

Complete reference for every configuration variable in JAVDB AutoSpider.

The primary configuration file is **`config.py`**. Copy `config.py.example` to
`config.py` and fill in your values. The file is git-ignored so credentials are
never committed.

Environment variables for the Web API and Docker are covered in
[Section 14](#14-environment-variables).

---

## Table of Contents

1. [Git Configuration](#1-git-configuration)
2. [qBittorrent Configuration](#2-qbittorrent-configuration)
3. [SMTP / Email](#3-smtp--email)
4. [Proxy Configuration](#4-proxy-configuration)
5. [CloudFlare Bypass](#5-cloudflare-bypass)
6. [Spider Configuration](#6-spider-configuration)
7. [JavDB Login](#7-javdb-login)
8. [Logging](#8-logging)
9. [Parsing / Re-download](#9-parsing--re-download)
10. [File Paths / Database Paths](#10-file-paths--database-paths)
11. [PikPak](#11-pikpak)
12. [Rclone / Dedup](#12-rclone--dedup)
13. [qBittorrent File Filter](#13-qbittorrent-file-filter)
14. [Environment Variables](#14-environment-variables)

---

## 1. Git Configuration

Used for pushing reports and history files to a GitHub repository.

| Variable | Type | Default | Description |
|---|---|---|---|
| `GIT_USERNAME` | `str` | `''` | GitHub username. |
| `GIT_PASSWORD` | `str` | `''` | GitHub password or personal access token (PAT). A PAT with `repo` scope is recommended over a password. Generate one at *GitHub Settings > Developer settings > Personal access tokens*. |
| `GIT_REPO_URL` | `str` | `''` | Full HTTPS clone URL of the repository, e.g. `https://github.com/user/repo.git`. |
| `GIT_BRANCH` | `str` | `'main'` | Branch name to push to. |

---

## 2. qBittorrent Configuration

Controls how the uploader connects to the qBittorrent Web UI and adds torrents.

### Primary Instance

| Variable | Type | Default | Description |
|---|---|---|---|
| `QB_URL` | `str` | `''` | Full URL of the qBittorrent Web UI including scheme, e.g. `https://192.168.1.100:8080`. If the scheme is omitted the app tries HTTPS first then retries HTTP automatically. |
| `QB_ALLOW_INSECURE_HTTP` | `bool` | `False` | Set to `True` when `QB_URL` uses plain `http://`. Required for validation; `config_generator` sets this automatically when generating from an HTTP URL. |
| `QB_VERIFY_TLS` | `bool` | `True` | Whether to verify TLS certificates when connecting to qBittorrent. Set to `False` for self-signed certificates. |
| `QB_USERNAME` | `str` | `''` | qBittorrent Web UI username. |
| `QB_PASSWORD` | `str` | `''` | qBittorrent Web UI password. |

### Torrent Settings

| Variable | Type | Default | Description |
|---|---|---|---|
| `TORRENT_CATEGORY` | `str` | `'JavDB'` | Category assigned to torrents added in daily mode. |
| `TORRENT_CATEGORY_ADHOC` | `str` | `'Ad Hoc'` | Category assigned to torrents added in ad-hoc mode. |
| `TORRENT_SAVE_PATH` | `str` | `''` | Custom download save path. Leave empty to use the qBittorrent default. |
| `AUTO_START` | `bool` | `True` | Start torrents immediately after adding. Set to `False` to add in paused state. |
| `SKIP_CHECKING` | `bool` | `False` | Skip hash checking when adding torrents. |

### Adhoc Instance (Optional)

A dedicated second qBittorrent instance for ad-hoc scraping. When configured,
`pikpak_bridge` scans both the primary and adhoc instances. The adhoc instance
is limited to the "Ad Hoc" category only.

| Variable | Type | Default | Description |
|---|---|---|---|
| `QB_URL_ADHOC` | `str` | `''` | URL of the adhoc qBittorrent instance. Leave empty to disable. |
| `QB_USERNAME_ADHOC` | `str` | `''` | Username for the adhoc instance. Falls back to `QB_USERNAME` when empty. |
| `QB_PASSWORD_ADHOC` | `str` | `''` | Password for the adhoc instance. Falls back to `QB_PASSWORD` when empty. |

### Connection Settings

| Variable | Type | Default | Description |
|---|---|---|---|
| `REQUEST_TIMEOUT` | `int` | `30` | Timeout in seconds for qBittorrent API requests. |
| `DELAY_BETWEEN_ADDITIONS` | `int` | `1` | Delay in seconds between consecutive torrent additions. |

---

## 3. SMTP / Email

Email notification settings. Notifications are sent after pipeline runs with a
summary of new torrents found.

| Variable | Type | Default | Description |
|---|---|---|---|
| `SMTP_SERVER` | `str` | `'smtp.gmail.com'` | SMTP server hostname. |
| `SMTP_PORT` | `int` | `587` | SMTP server port. Use `587` for STARTTLS (Gmail default) or `465` for SSL. |
| `SMTP_USER` | `str` | `''` | SMTP login username (usually your email address). |
| `SMTP_PASSWORD` | `str` | `''` | SMTP login password. For Gmail, use an App Password (enable 2FA first, then generate at *Google Account > Security > App passwords*). |
| `EMAIL_FROM` | `str` | `''` | Sender address shown in notification emails. |
| `EMAIL_TO` | `str` | `''` | Recipient address for notification emails. |

---

## 4. Proxy Configuration

### Proxy Mode

| Variable | Type | Default | Description |
|---|---|---|---|
| `PROXY_MODE` | `str` | `'pool'` | Controls how proxies are used. **`'pool'`** (default) -- use all proxies in `PROXY_POOL` with automatic failover. **`'single'`** -- use only the first proxy in `PROXY_POOL`. **`'None'`** -- disable proxies entirely (direct connection). |

### Proxy Pool

| Variable | Type | Default | Description |
|---|---|---|---|
| `PROXY_POOL` | `list[dict]` | *(see below)* | List of proxy dictionaries. Each entry has keys `name` (optional label), `http`, and `https`. In `single` mode only the first entry is used. In `pool` mode all entries participate with automatic failover. Supported schemes: `http://`, `https://`, `socks5://` (requires `pip install requests[socks]`). Authenticated proxies use the format `http://user:pass@host:port` -- URL-encode special characters in the password (e.g. `@` becomes `%40`). |
| `PROXY_POOL_MAX_FAILURES` | `int` | `3` | Maximum consecutive failures before a proxy is banned for the current session. Ban records are session-scoped (in-memory only) and reset on each new run. Only applies when `PROXY_MODE = 'pool'`. |

**Default `PROXY_POOL` structure:**

```python
PROXY_POOL = [
    {
        'name': 'Main-Proxy',
        'http': 'http://127.0.0.1:7890',
        'https': 'http://127.0.0.1:7890'
    },
    {
        'name': 'Backup-Proxy-1',
        'http': 'http://127.0.0.1:7891',
        'https': 'http://127.0.0.1:7891'
    },
]
```

### Legacy Proxy Settings (Deprecated)

These are retained for backward compatibility. If set, they override the first
entry in `PROXY_POOL`.

| Variable | Type | Default | Description |
|---|---|---|---|
| `PROXY_HTTP` | `str \| None` | `None` | Deprecated. Use `PROXY_POOL` instead. |
| `PROXY_HTTPS` | `str \| None` | `None` | Deprecated. Use `PROXY_POOL` instead. |

### Proxy Module Control

| Variable | Type | Default | Description |
|---|---|---|---|
| `PROXY_MODULES` | `list[str]` | `['spider']` | Which modules route traffic through the proxy in auto mode. Available modules: `'spider'` (all JavDB requests including login/session refresh), `'qbittorrent'` (qBittorrent Web UI API), `'pikpak'` (PikPak bridge API). Use `['all']` to proxy every module, or `[]` to disable proxy for all modules by default. CLI flags `--use-proxy` / `--no-proxy` override this per-run. |

### Proxy Coordinator (Cloudflare Worker)

Cross-runner proxy coordination for throttle sync, ban sharing, login-state,
and movie-claim mutex. Leave both empty to disable.

In GitHub Actions, set the repo Variable `PROXY_COORDINATOR_URL` and Secret
`PROXY_COORDINATOR_TOKEN`; `config_generator` writes them into `config.py`.

| Variable | Type | Default | Description |
|---|---|---|---|
| `PROXY_COORDINATOR_URL` | `str` | `''` | URL of the Cloudflare Worker proxy coordinator. |
| `PROXY_COORDINATOR_TOKEN` | `str` | `''` | Bearer token for authenticating with the proxy coordinator. |

### Movie Claim Mutex

Per-day mutex that prevents duplicate work across concurrent runners.

| Variable | Type | Default | Description |
|---|---|---|---|
| `MOVIE_CLAIM_ENABLED` | `str` | `'auto'` | Three-state control (case-insensitive, whitespace-trimmed). **`'auto'`** (default) -- mount the claim mutex only when the Runner Registry reports enough active runners (controlled by the Worker variable `MOVIE_CLAIM_MIN_RUNNERS`, default 2). Single-runner deployments pay zero overhead. **`'true'` / `'1'` / `'yes'`** -- force-on, mount unconditionally. Useful during mixed rollout windows. **`'false'` / `'0'` / `'no'` / `''`** -- force-off, equivalent to "coordinator not configured". Note: *unset* defaults to `'auto'`; the *empty string* is an explicit force-off. Fed by GH Variable `MOVIE_CLAIM_ENABLED` via `config_generator`. |

### Runner Registry

| Variable | Type | Default | Description |
|---|---|---|---|
| `RUNNER_REGISTRY_ENABLED` | `str` | `'false'` | When `'true'`, the spider registers itself with the Cloudflare `RunnerRegistry` Durable Object at startup, sends 60-second heartbeats, and unregisters on exit. This makes the run visible to peers for MovieClaim auto mount/unmount, proxy-pool drift detection, and cohort summaries. Fed by GH Variable `RUNNER_REGISTRY_ENABLED` via `config_generator`. |

---

## 5. CloudFlare Bypass

Configuration for the [CloudflareBypassForScraping](https://github.com/sarperavci/CloudflareBypassForScraping)
service. The service must be deployed on each proxy server using the same port.

The full service URL is built dynamically at runtime:
- Without proxy: `http://localhost:{CF_BYPASS_SERVICE_PORT}`
- With proxy pool: `http://{PROXY_IP}:{CF_BYPASS_SERVICE_PORT}` (uses the IP
  of the current proxy)

| Variable | Type | Default | Description |
|---|---|---|---|
| `CF_BYPASS_SERVICE_PORT` | `int` | `8000` | Port the CloudFlare bypass service listens on. Must match the port configured in the service's `docker-compose.yml`. |

---

## 6. Spider Configuration

Controls page range and filtering thresholds for the scraping phases.

| Variable | Type | Default | Description |
|---|---|---|---|
| `PAGE_START` | `int` | `1` | First page number to scrape. |
| `PAGE_END` | `int` | `20` | Last page number to scrape (inclusive). |
| `PHASE2_MIN_RATE` | `float` | `4.0` | Minimum user rating for a movie to qualify in Phase 2 (high-rated non-subtitle entries). |
| `PHASE2_MIN_COMMENTS` | `int` | `100` | Minimum comment count for a movie to qualify in Phase 2. |
| `BASE_URL` | `str` | `'https://javdb.com'` | Base URL for JavDB. Change only if using a mirror. |

---

## 7. JavDB Login

Credentials and settings for automatic session cookie refresh. Required for
custom-URL scraping (e.g. actor pages, user watchlists).

### Credentials

| Variable | Type | Default | Description |
|---|---|---|---|
| `JAVDB_USERNAME` | `str` | `''` | JavDB email or username. |
| `JAVDB_PASSWORD` | `str` | `''` | JavDB password. |
| `JAVDB_SESSION_COOKIE` | `str` | `''` | JavDB `_jdb_session` cookie value. Can be set manually from browser DevTools (*Application > Cookies*) or auto-updated by the login script. |

### GPT-Based Captcha Solving

The login flow uses GPT-4o Vision to solve captcha images. Any OpenAI-compatible
API endpoint is supported (e.g. `api.gpt.ge`, `api.openai.com`).

| Variable | Type | Default | Description |
|---|---|---|---|
| `GPT_API_URL` | `str` | `'https://api.gpt.ge/v1/chat/completions'` | OpenAI-compatible chat completions endpoint for captcha solving. |
| `GPT_API_KEY` | `str` | `''` | API key for the GPT endpoint (e.g. `sk-xxx`). Leave empty to fall back to manual captcha input. |

### Login Retry Policy

| Variable | Type | Default | Description |
|---|---|---|---|
| `LOGIN_ATTEMPTS_PER_PROXY_LIMIT` | `int` | `6` | Maximum login refresh attempts any single proxy may perform per run. The global budget is initially `len(PROXY_POOL) * LOGIN_ATTEMPTS_PER_PROXY_LIMIT` and is reduced when a proxy is banned. |
| `LOGIN_MAX_FAILURES_BEFORE_PROXY_SWITCH` | `int` | `3` | Number of stale-session failures that trigger a proxy switch. |

### Login Verification

| Variable | Type | Default | Description |
|---|---|---|---|
| `LOGIN_VERIFICATION_URLS` | `list[str]` | `['/users/want_watch_videos', '/users']` | URLs fetched after a successful login to verify the cookie works. Items can be absolute URLs or paths relative to `BASE_URL`. Login is only treated as verified when every URL returns a non-login response. Set to `[]` to disable verification (legacy behavior). |

### Sleep Tuning

Sleep intervals between requests are auto-tuned by the adaptive
`MovieSleepManager`. You generally do not need to configure these. For CI or
testing, override via the environment variable `VAR_MOVIE_SLEEP` (e.g.
`"0,0"`). All cooldowns (CloudFlare, fallback, login retry) are derived
adaptively from the sleep manager.

---

## 8. Logging

| Variable | Type | Default | Description |
|---|---|---|---|
| `LOG_LEVEL` | `str` | `'INFO'` | Minimum log level. One of `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `SPIDER_LOG_FILE` | `str` | `'logs/spider.log'` | Log file path for the spider module. |
| `UPLOADER_LOG_FILE` | `str` | `'logs/qb_uploader.log'` | Log file path for the qBittorrent uploader. |
| `PIPELINE_LOG_FILE` | `str` | `'logs/pipeline.log'` | Log file path for the pipeline orchestrator. |
| `EMAIL_NOTIFICATION_LOG_FILE` | `str` | `'logs/email_notification.log'` | Log file path for email notifications. |

Additional logging behavior is controlled by environment variables (see
[Section 14](#14-environment-variables)):
`LOG_STYLE` (`compact` | `plain` | `verbose`) and
`LOG_GITHUB_GROUPS` (`on` | `off` | `auto`).

---

## 9. Parsing / Re-download

Controls which movies are included in reports and whether re-download (re-acquiring a higher-quality release of an already-downloaded movie, known as `洗版` in CN) logic is active.

| Variable | Type | Default | Description |
|---|---|---|---|
| `IGNORE_RELEASE_DATE_FILTER` | `bool` | `False` | When `True`, parse all entries with subtitle tags regardless of release date. When `False`, only parse entries that have both subtitle tags AND today/yesterday release tags. Can also be set via CLI flag `--ignore-release-date`. |
| `INCLUDE_DOWNLOADED_IN_REPORT` | `bool` | `False` | When `True`, include movies in the report even if all torrent categories are already downloaded (marked with `[DOWNLOADED PREVIOUSLY]`). When `False`, skip fully-downloaded movies. |
| `ENABLE_REDOWNLOAD` | `bool` | `False` | Enable re-download mode (acquiring a higher-quality release of an already-downloaded movie, `洗版`). When enabled, the spider checks if a same-category torrent is significantly larger than the previously downloaded one and triggers a re-download. |
| `REDOWNLOAD_SIZE_THRESHOLD` | `float` | `0.30` | Size increase threshold for re-download. `0.30` means a new torrent must be at least 30% larger than the existing one to trigger a re-download. Only applies when `ENABLE_REDOWNLOAD = True`. |

---

## 10. File Paths / Database Paths

All paths are relative to the repository root unless an absolute path is given.

### Directories

| Variable | Type | Default | Description |
|---|---|---|---|
| `REPORTS_DIR` | `str` | `'reports'` | Root directory for all reports, history files, and databases. |
| `DAILY_REPORT_DIR` | `str` | `'reports/DailyReport'` | Output directory for daily CSV reports. Reports are stored in `YYYY/MM/` subdirectories. |
| `AD_HOC_DIR` | `str` | `'reports/AdHoc'` | Output directory for ad-hoc CSV reports. Reports are stored in `YYYY/MM/` subdirectories. |

### Database Files

The system uses three separate SQLite databases for concurrency and isolation.

| Variable | Type | Default | Description |
|---|---|---|---|
| `HISTORY_DB_PATH` | `str` | `'reports/history.db'` | Path to the history database containing `MovieHistory` and `TorrentHistory` tables. |
| `REPORTS_DB_PATH` | `str` | `'reports/reports.db'` | Path to the reports database containing `ReportSessions`, `ReportMovies`, `ReportTorrents`, `SpiderStats`, `UploaderStats`, and `PikpakStats` tables. |
| `OPERATIONS_DB_PATH` | `str` | `'reports/operations.db'` | Path to the operations database containing `RcloneInventory`, `DedupRecords`, and `PikpakHistory` tables. |

### Legacy Files

| Variable | Type | Default | Description |
|---|---|---|---|
| `PARSED_MOVIES_CSV` | `str` | `'parsed_movies_history.csv'` | Filename of the legacy parsed-movies CSV (stored in `REPORTS_DIR`). |

---

## 11. PikPak

Configuration for the PikPak cloud download bridge. The bridge reads magnet
links from qBittorrent and submits them as offline downloads to PikPak.

| Variable | Type | Default | Description |
|---|---|---|---|
| `PIKPAK_EMAIL` | `str` | `''` | PikPak account email. |
| `PIKPAK_PASSWORD` | `str` | `''` | PikPak account password. |
| `PIKPAK_LOG_FILE` | `str` | `'logs/pikpak_bridge.log'` | Log file path for PikPak bridge operations. |
| `PIKPAK_REQUEST_DELAY` | `int` | `2` | Delay in seconds between PikPak offline download requests to avoid rate limiting. |
| `PIKPAK_ROOT_FOLDER` | `str` | `'/Javdb_AutoSpider'` | Root folder on PikPak for offline downloads. Torrents are stored under `{PIKPAK_ROOT_FOLDER}/{qB category}`, e.g. a torrent in qBittorrent category "Ad Hoc" lands in `/Javdb_AutoSpider/Ad Hoc`. Missing folders are auto-created. Override at runtime via `--root-folder` or the GitHub Variable `PIKPAK_ROOT_FOLDER`. |

---

## 12. Rclone / Dedup

Settings for Google Drive inventory scanning and duplicate file cleanup.

### Rclone

| Variable | Type | Default | Description |
|---|---|---|---|
| `RCLONE_CONFIG_BASE64` | `str` | `''` | Base64-encoded `rclone.conf` content. Generate with: `base64 -w0 ~/.config/rclone/rclone.conf`. |
| `RCLONE_FOLDER_PATH` | `str` | `'gdrive:/...'` | Remote root path in `remote:path` format, e.g. `'gdrive:/Movies/JAV-Sync'` or `'gdrive:'` for the remote root. |

### Dedup

| Variable | Type | Default | Description |
|---|---|---|---|
| `RCLONE_INVENTORY_CSV` | `str` | `'rclone_inventory.csv'` | Filename for the rclone inventory CSV (stored in `REPORTS_DIR`). |
| `DEDUP_CSV` | `str` | `'dedup.csv'` | Filename for dedup records (stored in `REPORTS_DIR`, persistent across runs). |
| `DEDUP_LOG_FILE` | `str` | `'logs/rclone_dedup.log'` | Log file path for the rclone dedup executor. |

---

## 13. qBittorrent File Filter

The file filter sets small files (NFO, samples, screenshots, etc.) to "do not
download" priority inside torrents.

| Variable | Type | Default | Description |
|---|---|---|---|
| `QB_FILE_FILTER_MIN_SIZE_MB` | `int` | `100` | Minimum file size in MB. Files smaller than this threshold are set to "do not download" priority. |
| `QB_FILE_FILTER_LOG_FILE` | `str` | `'logs/qb_file_filter.log'` | Log file path for the file filter script. |

---

## 14. Environment Variables

These variables are set in `.env` files rather than `config.py`.

### 14.1 Root `.env` (Docker / cron entrypoint)

Defined in `.env.example` at the repository root. **Bare-metal uvicorn does
not auto-load this file** — `apps/api/services/context.py` deliberately omits
`load_dotenv` so stale `.env` entries cannot silently override fresh
`config.py` values (see `apps/api/infra/auth.py::_resolve`, precedence
`env > config.py > override store > default`).

Consumers of this file:

- **Docker Compose** (`docker/docker-compose*.yml`) — `env_file: ../.env` plus
  `environment:` blocks expand these into the container's environment, where
  the FastAPI process reads them via `os.environ`.
- **Cron entrypoint** (`docker-entrypoint.sh`) — sources the file directly.

For self-hosting **without Docker**, put the Web API / Admin Console values in
`config.py` (see `config.py.example` § "API CONSOLE / BACKEND SERVICE"), or
`export VAR=...` in the shell before launching uvicorn.

#### Web API / Admin Console (Docker / shell-export)

| Variable | Type | Default | Description |
|---|---|---|---|
| `API_SECRET_KEY` | `str` | *(none)* | JWT signing secret. **Required in production.** Generate with: `openssl rand -base64 48`. |
| `ADMIN_USERNAME` | `str` | `'admin'` | Admin username for the web console. |
| `ADMIN_PASSWORD` | `str` | *(none)* | Admin password (plain text). Mutually exclusive with `ADMIN_PASSWORD_HASH`. |
| `ADMIN_PASSWORD_HASH` | `str` | *(none)* | Pre-computed bcrypt hash of the admin password. Alternative to `ADMIN_PASSWORD`. |
| `READONLY_USERNAME` | `str` | *(none)* | Optional read-only user username. |
| `READONLY_PASSWORD` | `str` | *(none)* | Optional read-only user password. |
| `SECRETS_ENCRYPTION_KEY` | `str` | *(none)* | Fernet key for encrypting sensitive config values at rest. Generate with: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. |

#### Docker Cron Schedules

All cron expressions use the standard five-field format:
`MINUTE HOUR DAY MONTH WEEKDAY`.

| Variable | Type | Default | Description |
|---|---|---|---|
| `CRON_SPIDER` | `str` | `'0 3 * * *'` | Cron schedule for the daily spider job (default: 3:00 AM). |
| `SPIDER_COMMAND` | `str` | *(see .env.example)* | Shell command executed by the spider cron job. |
| `CRON_PIPELINE` | `str` | `'0 4 * * *'` | Cron schedule for the pipeline job (default: 4:00 AM). |
| `PIPELINE_COMMAND` | `str` | *(see .env.example)* | Shell command executed by the pipeline cron job. |
| `CRON_QBTORRENT` | `str` | `'30 3 * * *'` | Cron schedule for the qBittorrent uploader (default: 3:30 AM). |
| `QBTORRENT_COMMAND` | `str` | *(see .env.example)* | Shell command executed by the qBittorrent cron job. |
| `CRON_PIKPAK` | `str` | `'0 5 * * *'` | Cron schedule for the PikPak bridge (default: 5:00 AM). |
| `PIKPAK_COMMAND` | `str` | *(see .env.example)* | Shell command executed by the PikPak cron job. |

#### Docker Job Toggles

| Variable | Type | Default | Description |
|---|---|---|---|
| `ENABLE_SPIDER` | `str` | `'true'` | Enable or disable the spider cron job (`'true'` / `'false'`). |
| `ENABLE_PIPELINE` | `str` | `'true'` | Enable or disable the pipeline cron job. |
| `ENABLE_QBTORRENT` | `str` | `'true'` | Enable or disable the qBittorrent uploader cron job. |
| `ENABLE_PIKPAK` | `str` | `'true'` | Enable or disable the PikPak bridge cron job. |

#### Docker Miscellaneous

| Variable | Type | Default | Description |
|---|---|---|---|
| `TZ` | `str` | *(none)* | Container timezone, e.g. `Asia/Shanghai`, `America/New_York`. |
| `MAX_LOG_SIZE` | `str` | *(none)* | Maximum log file size before rotation, e.g. `100M`. |
| `MAX_LOG_FILES` | `int` | *(none)* | Maximum number of rotated log files to keep. |

### 14.2 Shell / CI Environment Variables

These are set in the shell or in GitHub Actions workflow files and are read at
runtime by various modules.

| Variable | Type | Default | Description |
|---|---|---|---|
| `STORAGE_BACKEND` | `str` | `'sqlite'` | Storage backend. `'sqlite'` -- local SQLite files. `'d1'` -- Cloudflare D1 (GitHub Actions). `'dual'` -- write to both, read from D1. |
| `WRITE_MODE` | `str` | `'pending'` | Write mode for session management. Only `'pending'` is supported; legacy `'audit'` requests fall back to pending. |
| `STRICT_DUAL_WRITE` | `str` | `''` | When set to `'1'`, fail the run if a D1 write fails in dual mode. |
| `LOG_LEVEL` | `str` | `'INFO'` | Overrides the `LOG_LEVEL` in `config.py` when set as an environment variable. |
| `LOG_STYLE` | `str` | `'compact'` | Log output format. `'compact'` -- concise single-line. `'plain'` -- standard format. `'verbose'` -- full 4-field format. |
| `LOG_GITHUB_GROUPS` | `str` | `'auto'` | GitHub Actions log grouping. `'on'` -- always emit `::group::` markers. `'off'` -- never. `'auto'` -- detect CI environment. |
| `VAR_MOVIE_SLEEP` | `str` | *(none)* | Override adaptive sleep range as `"min,max"` in seconds, e.g. `"0,0"` for CI. |

### 14.3 Docker-Specific `.env` (`docker/.env.example`)

The `docker/.env.example` file provides a simplified cron configuration format
for the Docker container. It uses the same variables described in
[Docker Cron Schedules](#docker-cron-schedules) above, with an alternative
inline format:

```bash
CRON_DAILY="0 8 * * * python3 pipeline.py --use-proxy"
```

This combines the schedule and command into a single variable. Refer to the
comments in `docker/.env.example` for additional examples and pipeline argument
reference.
