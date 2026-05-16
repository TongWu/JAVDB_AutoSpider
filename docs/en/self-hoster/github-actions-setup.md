# GitHub Actions Deployment Guide

A from-scratch guide for running JAVDB AutoSpider as an automated daily pipeline via GitHub Actions.

## Overview

The GitHub Actions deployment provides:

- **Daily automated scraping** via a cron-triggered workflow (12:00 UTC / 20:00 Beijing Time)
- **Ad-hoc scraping** of custom URLs (actors, tags, etc.) via manual dispatch
- **Encrypted artifact handling** -- config.py, logs, and reports are AES-256-CBC encrypted between jobs
- **Automatic rollback** on pipeline failure (pending-mode writes are deleted, not committed)
- **Email notifications** with run results, statistics, and log attachments

## Step 1 -- Fork or Clone the Repository

Fork `TongWu/JAVDB_AutoSpider` to your own GitHub account, or push your clone to a private repository.

## Step 2 -- Create the `Production` Environment

Go to **Settings > Environments > New environment** and create an environment named **`Production`**.

Both the `DailyIngestion` and `AdHocIngestion` workflows reference `environment: Production` in their job definitions. Secrets and variables scoped to this environment are injected at runtime.

> **Tip:** You can add protection rules (required reviewers, wait timers) to the Production environment for extra safety, but it is not required.

## Step 3 -- Configure Repository Secrets

Go to **Settings > Secrets and variables > Actions > Secrets** (or scope them to the `Production` environment).

The `config_generator` CLI (`python3 -m apps.cli.config_generator --github-actions`) reads these from environment variables prefixed with `VAR_` and writes a `config.py` at the start of each workflow run. Every secret listed below maps to a `VAR_*` env var in the workflow YAML.

### Required Secrets

| Secret | Purpose | Example |
|---|---|---|
| `DEPLOY_KEY` | SSH deploy key (read/write) for git push from CI | Generate with `ssh-keygen -t ed25519` and add the public key as a deploy key on the repo |
| `ARTIFACT_KEY` | Passphrase for AES-256-CBC encryption of config/logs/reports artifacts between jobs | Any strong random string |
| `QB_URL` | qBittorrent Web UI URL (daily mode) | `https://192.168.1.100:8080` |
| `QB_USERNAME` | qBittorrent Web UI username | `admin` |
| `QB_PASSWORD` | qBittorrent Web UI password | |
| `SMTP_SERVER` | SMTP host | `smtp.gmail.com` |
| `SMTP_USER` | SMTP login username | `you@gmail.com` |
| `SMTP_PASSWORD` | SMTP app password | |
| `EMAIL_FROM` | Sender email address | `you@gmail.com` |
| `EMAIL_TO` | Recipient email address | `you@gmail.com` |
| `PROXY_POOL_JSON` | JSON array of proxy objects | `[{"name":"Proxy-1","http":"http://1.2.3.4:7890","https":"http://1.2.3.4:7890"}]` |
| `JAVDB_USERNAME` | JavDB login email/username | |
| `JAVDB_PASSWORD` | JavDB login password | |
| `JAVDB_SESSION_COOKIE` | JavDB `_jdb_session` cookie value (auto-refreshed by login) | |
| `GPT_API_URL` | GPT-4o Vision API endpoint for captcha solving | `https://api.openai.com/v1/chat/completions` |
| `GPT_API_KEY` | API key for the GPT captcha solver | `sk-...` |
| `PIKPAK_EMAIL` | PikPak account email | |
| `PIKPAK_PASSWORD` | PikPak account password | |

### Optional Secrets (Cloudflare D1 Storage Backend)

Only required when `STORAGE_BACKEND` is set to `d1` or `dual`.

| Secret | Purpose |
|---|---|
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account ID |
| `CLOUDFLARE_API_TOKEN` | Cloudflare API token with D1 read/write permissions |
| `D1_HISTORY_DB_ID` | D1 database ID for history.db |
| `D1_REPORTS_DB_ID` | D1 database ID for reports.db |
| `D1_OPERATIONS_DB_ID` | D1 database ID for operations.db |

### Optional Secrets (Cross-Runner Proxy Coordinator)

Only needed when using the Cloudflare Worker proxy coordinator for multi-runner deployments.

| Secret | Purpose |
|---|---|
| `PROXY_COORDINATOR_TOKEN` | Bearer token for the proxy coordinator Worker |

### Optional Secrets (Dedicated Ad-Hoc qBittorrent Instance)

When set, the ad-hoc workflow uses a separate qBittorrent instance. PikPak bridge scans both instances.

| Secret | Purpose |
|---|---|
| `QB_URL_ADHOC` | qBittorrent Web UI URL for ad-hoc downloads |
| `QB_USERNAME_ADHOC` | Falls back to `QB_USERNAME` when empty |
| `QB_PASSWORD_ADHOC` | Falls back to `QB_PASSWORD` when empty |

### Optional Secrets (Rclone / Dedup)

| Secret | Purpose |
|---|---|
| `RCLONE_CONFIG_BASE64` | Base64-encoded `rclone.conf` content (for Google Drive inventory and dedup) |

## Step 4 -- Configure Repository Variables

Go to **Settings > Secrets and variables > Actions > Variables**.

These are non-sensitive values. The `config_generator` reads them via `VAR_*` env vars.

### Core Variables

| Variable | Default | Purpose |
|---|---|---|
| `GIT_REPO_URL` | -- | Repository HTTPS URL (e.g. `https://github.com/you/JAVDB_AutoSpider.git`) |
| `GIT_BRANCH` | `main` | Branch for git push |
| `PROXY_MODE` | `pool` | `pool`, `single`, or `None` |
| `PROXY_MODULES_JSON` | `["spider"]` | JSON array of modules that use proxy: `spider`, `qbittorrent`, `pikpak`, `all` |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `STORAGE_BACKEND` | `sqlite` | `sqlite`, `d1`, or `dual` |

### Spider Tuning Variables

| Variable | Default | Purpose |
|---|---|---|
| `PAGE_START` | `1` | First page to scrape |
| `PAGE_END` | `20` | Last page to scrape |
| `PHASE2_MIN_RATE` | `4.0` | Minimum rating for Phase 2 quality filter |
| `PHASE2_MIN_COMMENTS` | `100` | Minimum comment count for Phase 2 quality filter |
| `BASE_URL` | `https://javdb.com` | JavDB base URL |
| `IGNORE_RELEASE_DATE_FILTER` | `False` | Skip release date filtering |
| `INCLUDE_DOWNLOADED_IN_REPORT` | `False` | Include already-downloaded movies in reports |
| `MOVIE_SLEEP` | (adaptive) | Override spider sleep range (e.g. `"2,5"`) |

### qBittorrent Variables

| Variable | Default | Purpose |
|---|---|---|
| `TORRENT_CATEGORY` | `JavDB` | qBittorrent category for daily mode torrents |
| `TORRENT_CATEGORY_ADHOC` | `Ad Hoc` | qBittorrent category for ad-hoc mode torrents |
| `TORRENT_SAVE_PATH` | (empty = default) | Override torrent save path |
| `AUTO_START` | `True` | Auto-start added torrents |
| `SKIP_CHECKING` | `False` | Skip hash checking |
| `REQUEST_TIMEOUT` | `30` | API request timeout in seconds |
| `DELAY_BETWEEN_ADDITIONS` | `1` | Delay in seconds between torrent additions |
| `QB_FILE_FILTER_MIN_SIZE_MB` | `100` | Minimum file size threshold for the file filter |

### Proxy Variables

| Variable | Default | Purpose |
|---|---|---|
| `PROXY_POOL_MAX_FAILURES` | `3` | Max consecutive failures before banning a proxy for the session |
| `CF_BYPASS_SERVICE_PORT` | `8000` | CloudFlare bypass service port |
| `CF_BYPASS_ENABLED` | `True` | Enable/disable CF bypass fallback |
| `LOGIN_PROXY_NAME` | (empty) | Pin login to a specific proxy name |

### Proxy Coordinator Variables

| Variable | Default | Purpose |
|---|---|---|
| `PROXY_COORDINATOR_URL` | (empty) | Cloudflare Worker URL for cross-runner coordination |
| `MOVIE_CLAIM_ENABLED` | `auto` | MovieClaim mutex: `auto`, `true`, or `false` |
| `RUNNER_REGISTRY_ENABLED` | `false` | Register runner in the RunnerRegistry DO |

### Path Variables

| Variable | Default | Purpose |
|---|---|---|
| `REPORTS_DIR` | `reports` | Root directory for all reports |
| `DAILY_REPORT_DIR` | `reports/DailyReport` | Daily CSV output directory |
| `AD_HOC_DIR` | `reports/AdHoc` | Ad-hoc CSV output directory |
| `PARSED_MOVIES_CSV` | `parsed_movies_history.csv` | History CSV filename |

### PikPak Variables

| Variable | Default | Purpose |
|---|---|---|
| `PIKPAK_LOG_FILE` | `logs/pikpak_bridge.log` | PikPak bridge log path |
| `PIKPAK_REQUEST_DELAY` | `2` | Delay in seconds between PikPak API calls |
| `PIKPAK_ROOT_FOLDER` | `/Javdb_AutoSpider` | PikPak root folder for offline downloads |

### Rclone Variables

| Variable | Default | Purpose |
|---|---|---|
| `RCLONE_FOLDER_PATH` | (empty) | Rclone remote path for inventory/dedup |

### Log File Variables

| Variable | Default |
|---|---|
| `SPIDER_LOG_FILE` | `logs/spider.log` |
| `UPLOADER_LOG_FILE` | `logs/qb_uploader.log` |
| `PIPELINE_LOG_FILE` | `logs/pipeline.log` |
| `EMAIL_NOTIFICATION_LOG_FILE` | `logs/email_notification.log` |
| `SMTP_PORT` | `587` |

## Step 5 -- How config.py is Generated

In CI, there is no persistent `config.py` file. Instead, the **setup job** in each workflow runs:

```bash
python3 -m apps.cli.config_generator --github-actions
```

This script reads every `VAR_*` environment variable (populated from Secrets and Variables above) and writes a complete `config.py`. The file is then encrypted with `ARTIFACT_KEY` and passed between jobs as an encrypted artifact.

Each downstream job (run-pipeline, email-notification, commit-results, cleanup-on-failure) decrypts the artifact using the `restore-encrypted-config` composite action before it can run Python code that imports `config`.

## Step 6 -- Enable and Verify the Daily Ingestion Cron

The `DailyIngestion.yml` workflow has a `schedule` trigger:

```yaml
schedule:
  - cron: '00 12 * * *'   # 12:00 UTC = 20:00 Beijing Time
```

GitHub Actions crons can be delayed by up to 15 minutes during high-load periods. The cron is active on the **default branch** only.

To verify:

1. Go to **Actions > JavDB Daily Ingestion Pipeline**
2. Click **Run workflow** (manual dispatch) for a test run
3. Check the run completes successfully
4. Wait for the next scheduled trigger

### DailyIngestion Workflow Structure

| Job | Purpose |
|---|---|
| `setup` | Checkout, install dependencies, generate + encrypt config.py |
| `run-pipeline` | Health check, spider, qBittorrent uploader, file filter, PikPak bridge, rclone dedup, session commit |
| `cleanup-on-failure` | Rolls back uncommitted D1/pending writes on failure |
| `email-notification` | Sends result email, runs auto-fallback on critical pending alerts |
| `commit-results` | Commits CSV reports and database files back to the repo |

### AdHocIngestion Workflow

Triggered only by **manual dispatch** (workflow_dispatch). Requires a target URL input.

Go to **Actions > JavDB Ad-Hoc Ingestion Pipeline > Run workflow** and fill in:

- **url** (required): Target URL (e.g. `https://javdb.com/actors/EvkJ`)
- **start_page** / **end_page**: Page range (leave end_page empty to scan all pages)
- **phase**: `all`, `1` (subtitle only), or `2` (non-subtitle only)
- **history_filter**: Check history before processing
- **date_filter**: Filter by release date
- **qb_category**: Custom qBittorrent category (empty = default "Ad Hoc"; `顶级` uses the daily qB credentials)

## Step 7 -- Monitoring

### Email Notifications

Both workflows send email notifications on every run (success, failure, or cancellation). The email includes:

- Pipeline status summary
- Spider statistics (pages, entries found, parsed, skipped)
- Log file attachments (encrypted, then decrypted for email)
- D1 drift advisory banner (if applicable)

### GitHub Actions UI

- **Step Summary**: The spider writes a Markdown summary table to `$GITHUB_STEP_SUMMARY` showing pages/found/parsed/skipped/failed counts
- **Artifacts**: Encrypted logs and reports are uploaded as run artifacts (7-day retention for logs/reports, 14-day for rollback logs)
- **Workflow status badge**: Add the badge to your README:
  ```markdown
  [![JavDB Daily Ingestion Pipeline](https://github.com/YOUR_USER/YOUR_REPO/actions/workflows/DailyIngestion.yml/badge.svg)](https://github.com/YOUR_USER/YOUR_REPO/actions/workflows/DailyIngestion.yml)
  ```

### Decrypting Artifacts Locally

To inspect encrypted artifacts downloaded from the Actions UI:

```bash
# Decrypt logs
openssl enc -aes-256-cbc -d -pbkdf2 -iter 100000 \
  -in logs.tar.gz.enc -pass pass:"YOUR_ARTIFACT_KEY" | tar -xzf -

# Decrypt reports
openssl enc -aes-256-cbc -d -pbkdf2 -iter 100000 \
  -in reports.tar.gz.enc -pass pass:"YOUR_ARTIFACT_KEY" | tar -xzf -
```

## Other Workflows

| Workflow | Trigger | Purpose |
|---|---|---|
| `QBFileFilter.yml` | Cron (2h after daily ingestion) | Filter small files from recently added torrents |
| `WeeklyDedup.yml` | Weekly cron | Rclone deduplication |
| `RollbackD1.yml` | Manual dispatch | Manual session rollback |
| `StaleSessionCleanup.yml` | Daily cron | Auto-cleanup sessions stuck > 48h |
| `AuditArchive.yml` | Weekly cron | Prune audit rows older than 30 days |
| `Migration.yml` | Manual dispatch | Database migration runner |
| `TestIngestion.yml` | Push / PR / manual dispatch | Smoke-test the full ingestion path; rollback runs on cleanup |
| `build-rust-extension.yml` | On push/PR | Build Rust wheel for CI |
| `unit-tests.yml` | On push/PR | Impact-based test selection |

## Troubleshooting

- **`ARTIFACT_KEY secret is not configured`**: Every job guards against a missing `ARTIFACT_KEY`. Add it as a repository secret.
- **`DEPLOY_KEY` errors**: The SSH deploy key must have write access. Add the public key under **Settings > Deploy keys** with "Allow write access" checked.
- **Config generation fails**: Check that all required `VAR_*` environment variables are populated. The `config_generator` logs which variables it reads.
- **Cron not firing**: GitHub disables scheduled workflows on repositories with no activity for 60 days. Push a commit or manually trigger a run to re-enable.
- **Email not sent**: Check `SMTP_*` secrets. Gmail requires an App Password (not your login password) and may require "Allow less secure apps" or an OAuth setup.

For more troubleshooting, see [../ops/troubleshooting.md](../ops/troubleshooting.md).
