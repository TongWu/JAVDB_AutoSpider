# Local Setup Guide

A from-scratch guide for running JAVDB AutoSpider on your local machine.

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10+ (3.11 recommended) | The CI pipeline uses 3.11 |
| pip | latest | Bundled with Python |
| Git | any recent version | For cloning and version control |
| Rust toolchain + maturin | latest stable | **Optional** -- only needed for the Rust acceleration extension |

The spider, uploader, and full pipeline all work without the Rust extension. When it is absent the system silently falls back to pure-Python HTML parsing.

## Step 1 -- Clone the Repository

```bash
git clone https://github.com/TongWu/JAVDB_AutoSpider.git
cd JAVDB_AutoSpider
```

If the repository uses Git LFS for binary files (SQLite databases), install LFS and pull:

```bash
git lfs install
git lfs pull
```

## Step 2 -- Install Python Dependencies

```bash
pip install -r requirements.txt
```

Key packages installed:

- `requests` -- HTTP client for JavDB and qBittorrent
- `beautifulsoup4` + `lxml` -- HTML parsing (Python fallback)
- `pikpakapi` -- PikPak cloud transfer integration
- `curl_cffi` -- TLS fingerprint-resistant HTTP client
- `fastapi` + `uvicorn` -- REST API server (optional)

### Optional extras

```bash
# SOCKS5 proxy support
pip install "requests[socks]"

# OCR-based captcha solving (also requires the tesseract-ocr system package)
pip install pytesseract Pillow
```

## Step 3 -- Create config.py

```bash
cp config.py.example config.py
```

Open `config.py` in your editor and fill in the **minimum required** settings:

### Minimum for a dry-run test

No external credentials are needed for `--dry-run`. The defaults in `config.py.example` are sufficient.

### Minimum for a real spider run

| Setting | Purpose |
|---|---|
| `PROXY_MODE` | `'pool'`, `'single'`, or `'None'`. Set to `'None'` if you can reach javdb.com directly. |
| `PROXY_POOL` | At least one proxy entry if `PROXY_MODE` is not `'None'`. |

### Minimum for the full pipeline (spider + qBittorrent upload)

| Setting | Purpose |
|---|---|
| `QB_URL` | qBittorrent Web UI URL, e.g. `'https://192.168.1.100:8080'` |
| `QB_USERNAME` | qBittorrent Web UI username |
| `QB_PASSWORD` | qBittorrent Web UI password |

### For email notifications

| Setting | Purpose |
|---|---|
| `SMTP_SERVER` | SMTP host, e.g. `'smtp.gmail.com'` |
| `SMTP_PORT` | SMTP port (587 for TLS) |
| `SMTP_USER` | Email account |
| `SMTP_PASSWORD` | App password (not your login password for Gmail) |
| `EMAIL_FROM` | Sender address |
| `EMAIL_TO` | Recipient address |

### For custom URL scraping (actors, tags, etc.)

| Setting | Purpose |
|---|---|
| `JAVDB_USERNAME` | JavDB email or username |
| `JAVDB_PASSWORD` | JavDB password |
| `GPT_API_URL` / `GPT_API_KEY` | (Optional) GPT-4o Vision API for captcha solving |

Run the login script before your first custom-URL scrape:

```bash
python3 -m apps.cli.login
```

### For Git auto-commit (optional)

| Setting | Purpose |
|---|---|
| `GIT_USERNAME` | GitHub username |
| `GIT_PASSWORD` | GitHub personal access token |
| `GIT_REPO_URL` | Repository HTTPS URL |
| `GIT_BRANCH` | Target branch (usually `main`) |

See the comments in `config.py.example` for the full list of available settings and their defaults.

## Step 4 -- Verify with a Dry Run

```bash
python3 -m apps.cli.spider --dry-run --start-page 1 --end-page 1
```

Expected behaviour:

- The spider fetches one page from javdb.com
- Entries are parsed and logged to the console
- No CSV file is written (dry-run mode)
- Exit code 0 on success

If you see `No movie list found`, check:
1. Whether javdb.com is accessible from your machine (try in a browser)
2. Whether you need a proxy (`PROXY_MODE` and `PROXY_POOL` in config.py)

## Step 5 -- (Optional) Build the Rust Extension

The Rust extension (`javdb_rust_core`) provides 5-10x faster HTML parsing. It is entirely optional.

```bash
# 1. Install the Rust toolchain (if not already installed)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"

# 2. Install maturin (Python-Rust build tool)
pip install maturin

# 3. Build and install the extension in release mode
cd packages/rust/javdb_rust_core
maturin develop --release
cd ../../..
```

Verify the extension is loaded:

```bash
python3 -c "import javdb_rust_core; print('Rust extension loaded:', javdb_rust_core.__version__)"
```

If this prints a version string, the Rust extension is active. If it raises `ModuleNotFoundError`, the system will use the pure-Python fallback automatically -- no action needed.

## Step 6 -- Run the Full Pipeline

Once config.py is populated:

```bash
# Daily mode (scrapes the default JavDB index pages)
python3 -m apps.cli.pipeline

# With proxy override
python3 -m apps.cli.pipeline --use-proxy

# Custom URL (requires login first)
python3 -m apps.cli.login
python3 -m apps.cli.pipeline --url "https://javdb.com/actors/EvkJ"
```

## Verification Checklist

- [ ] `python3 --version` reports 3.10+
- [ ] `pip install -r requirements.txt` completes without errors
- [ ] `config.py` exists and is **not** tracked by git (check `.gitignore`)
- [ ] `python3 -m apps.cli.spider --dry-run --start-page 1 --end-page 1` exits with code 0
- [ ] (Optional) `python3 -c "import javdb_rust_core"` succeeds
- [ ] (If using qBittorrent) `QB_URL`, `QB_USERNAME`, `QB_PASSWORD` are set and the Web UI is reachable
- [ ] (If using email) SMTP settings are configured and a test email sends successfully

## Directory Layout After First Run

```
reports/
  DailyReport/          # Daily CSV reports (YYYY/MM/ subdirectories)
  AdHoc/                # Ad-hoc CSV reports (YYYY/MM/ subdirectories)
  history.db            # MovieHistory, TorrentHistory (SQLite)
  reports.db            # ReportSessions, stats (SQLite)
  operations.db         # RcloneInventory, DedupRecords, PikpakHistory (SQLite)
  parsed_movies_history.csv   # Legacy CSV history (still maintained)
logs/
  spider.log            # Spider run log
  qb_uploader.log       # qBittorrent uploader log
  pipeline.log          # Pipeline orchestration log
  email_notification.log
```

## Next Steps

- **GitHub Actions deployment**: See [github-actions-setup.md](github-actions-setup.md) for automated daily scraping via CI.
- **Troubleshooting**: See [../ops/troubleshooting.md](../ops/troubleshooting.md) for common issues and debug mode.
- **Logging configuration**: See [../ops/logging.md](../ops/logging.md) for log style and level customization.
