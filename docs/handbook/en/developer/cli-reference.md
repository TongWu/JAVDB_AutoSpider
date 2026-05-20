# CLI Reference

Complete command-line reference for all JAVDB AutoSpider CLI tools.

Every CLI is invoked as a Python module from the repository root:

```bash
python3 -m apps.cli.<command> [options]
```

---

## Table of Contents

- [Spider CLI](#spider-cli) (`apps.cli.spider`)
- [Pipeline CLI](#pipeline-cli) (`apps.cli.pipeline`)
- [qBittorrent Uploader](#qbittorrent-uploader) (`apps.cli.qb_uploader`)
- [qBittorrent File Filter](#qbittorrent-file-filter) (`apps.cli.qb_file_filter`)
- [PikPak Bridge](#pikpak-bridge) (`apps.cli.pikpak_bridge`)
- [Migration CLI](#migration-cli) (`apps.cli.migration`)
- [Login CLI](#login-cli) (`apps.cli.login`)
- [Rollback CLI](#rollback-cli) (`apps.cli.rollback`)
- [Config Generator CLI](#config-generator-cli) (`apps.cli.config_generator`)
- [Complete Spider Argument Reference](#complete-spider-argument-reference)

---

## Spider CLI

**Module:** `apps.cli.spider`

Extracts torrent links from javdb.com. Operates in two modes:

- **Daily mode** (default) — scrapes the main index pages for today/yesterday releases.
- **Ad-hoc mode** — activated by `--url`; scrapes an arbitrary URL (actor page, search query, etc.).

### Basic Options

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

### Phase Control

The spider runs in two phases with configurable selection:

- **Phase 1** — subtitle entries + today/yesterday tags
- **Phase 2** — today/yesterday tags with quality filter

```bash
# Run only Phase 1
python3 -m apps.cli.spider --phase 1

# Run only Phase 2
python3 -m apps.cli.spider --phase 2

# Run both phases (default)
python3 -m apps.cli.spider --phase all
```

### History and Filter Control

```bash
# Ignore history for READING (scrape all pages) but still SAVE to history
# Note: ad-hoc mode already ignores history for reading by default
python3 -m apps.cli.spider --ignore-history

# Enable history filter in ad-hoc mode (ad-hoc ignores history by default)
python3 -m apps.cli.spider --url "https://javdb.com/actors/EvkJ" --use-history

# Custom URL scraping (enables ad-hoc mode; add ?page=x for paginated URLs)
python3 -m apps.cli.spider --url "https://javdb.com/?vft=2"

# Ignore today/yesterday release date tags and download all entries matching phase criteria
python3 -m apps.cli.spider --ignore-release-date

# Disable rclone inventory filter
python3 -m apps.cli.spider --no-rclone-filter

# Disable ALL filters (history, rclone inventory, release date)
python3 -m apps.cli.spider --disable-all-filters

# Enable rclone dedup detection
python3 -m apps.cli.spider --enable-dedup

# Enable torrent re-download when same-category torrent is significantly larger
python3 -m apps.cli.spider --enable-redownload

# Set a custom re-download size threshold (default: 30%)
python3 -m apps.cli.spider --enable-redownload --redownload-threshold 0.50
```

### Proxy Control

Proxy behaviour defaults to `PROXY_MODULES` in `config.py`. CLI flags override it for a single run.

```bash
# Follow proxy modules from config.py (default auto mode)
python3 -m apps.cli.spider

# Force-enable proxy for this run
python3 -m apps.cli.spider --use-proxy

# Force-disable proxy for this run
python3 -m apps.cli.spider --no-proxy

# Force sequential detail processing in proxy pool mode
python3 -m apps.cli.spider --sequential
```

`--use-proxy` and `--no-proxy` are mutually exclusive.

### Cloudflare Bypass

```bash
# Keep using CF bypass after fallback success for 30 minutes
python3 -m apps.cli.spider --always-bypass-time 30

# Keep using CF bypass for the entire session (omit value or pass 0)
python3 -m apps.cli.spider --always-bypass-time
```

### Testing Helpers

```bash
# Limit phase 1 movies (for testing)
python3 -m apps.cli.spider --max-movies-phase1 10

# Limit phase 2 movies (for testing)
python3 -m apps.cli.spider --max-movies-phase2 5

# Quick test run with limited pages
python3 -m apps.cli.spider --start-page 1 --end-page 3 --dry-run
```

### Complete Examples

```bash
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

# Ad-hoc: download specific actor's movies (skips already downloaded)
python3 -m apps.cli.spider --url "https://javdb.com/actors/EvkJ" --ignore-release-date

# Ad-hoc: re-download everything from an actor (ignores history)
python3 -m apps.cli.spider --url "https://javdb.com/actors/EvkJ" --ignore-history --ignore-release-date

# Combine: force proxy + custom URL + ignore release date
python3 -m apps.cli.spider --url "https://javdb.com/actors/EvkJ" --use-proxy --ignore-release-date

# Re-download with custom threshold (50% larger to trigger)
python3 -m apps.cli.spider --enable-redownload --redownload-threshold 0.50

# Disable all filters and process every entry from the index
python3 -m apps.cli.spider --disable-all-filters --start-page 1 --end-page 5
```

---

## Pipeline CLI

**Module:** `apps.cli.pipeline`

Runs the full automation workflow: spider, qBittorrent uploader, PikPak bridge, git commits, and email notification. Accepts all spider arguments and passes them through.

The pipeline **enables re-download by default** (unlike the spider, which does not). Use `--no-redownload` to opt out.

### Pipeline-Specific Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `--pikpak-individual` | Use individual mode for PikPak Bridge (instead of batch) | `False` |
| `--no-redownload` | Disable torrent re-download; pipeline enables it by default | `False` |
| `--redownload-threshold` | Size increase threshold for re-download (uses spider default if omitted) | Spider default |
| `--enable-dedup` | Enable rclone dedup detection and execution | `False` |

All spider arguments (`--url`, `--start-page`, `--end-page`, `--all`, `--ignore-history`, `--phase`, `--output-file`, `--dry-run`, `--ignore-release-date`, `--use-proxy`, `--no-proxy`, `--always-bypass-time`) are also accepted and forwarded to the spider step.

### Examples

```bash
# Basic pipeline run (auto proxy mode from config.py)
python3 -m apps.cli.pipeline

# Pipeline with custom URL
python3 -m apps.cli.pipeline --url "https://javdb.com/actors/EvkJ"

# Pipeline with proxy override
python3 -m apps.cli.pipeline --use-proxy

# Pipeline ignoring release date tags
python3 -m apps.cli.pipeline --ignore-release-date --phase 1

# Pipeline with PikPak individual mode
python3 -m apps.cli.pipeline --pikpak-individual

# Pipeline with dedup enabled
python3 -m apps.cli.pipeline --enable-dedup

# Pipeline without re-download
python3 -m apps.cli.pipeline --no-redownload

# Pipeline with custom re-download threshold
python3 -m apps.cli.pipeline --redownload-threshold 0.50
```

### Pipeline Steps

The pipeline executes these steps in order:

1. Run the spider to extract data (with provided arguments)
2. Commit spider results to GitHub
3. Run the qBittorrent uploader to add torrents
4. Commit uploader results to GitHub
5. Run PikPak Bridge to handle old torrents (batch mode by default, individual with `--pikpak-individual`)
6. Final commit and push to GitHub
7. Analyze logs for critical errors
8. Send email notification with status

**Note:** By default the pipeline does not inject `--use-proxy` or `--no-proxy`; each step follows `config.py` via `PROXY_MODULES`. If you pass `--use-proxy` or `--no-proxy`, the override is forwarded to spider, qBittorrent uploader, and PikPak Bridge.

---

## qBittorrent Uploader

**Module:** `apps.cli.qb_uploader`

Uploads torrent magnet links from spider CSV output to qBittorrent.

### Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `--mode` | Upload mode: `adhoc` or `daily` | `daily` |
| `--input-file` | Input CSV file name (overrides default date-based name) | Auto-detected |
| `--use-proxy` | Force-enable proxy for qBittorrent API requests | Auto |
| `--no-proxy` | Force-disable proxy for qBittorrent API requests | Auto |
| `--category` | Override qBittorrent category | Mode-dependent default |
| `--from-pipeline` | Internal: running from pipeline | `False` |
| `--session-id` | Report session ID for saving uploader stats | `None` |

### Examples

```bash
# Daily mode (default)
python3 -m apps.cli.qb_uploader

# Ad-hoc mode (for custom URL scraping results)
python3 -m apps.cli.qb_uploader --mode adhoc

# Specify input file
python3 -m apps.cli.qb_uploader --input-file my_results.csv

# Use proxy for qBittorrent API
python3 -m apps.cli.qb_uploader --use-proxy

# Override category
python3 -m apps.cli.qb_uploader --mode adhoc --category "Custom Category"
```

---

## qBittorrent File Filter

**Module:** `apps.cli.qb_file_filter`

Filters out small files from recently added torrents in qBittorrent. Sets unwanted files (below the size threshold) to "do not download" priority.

### Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `--min-size` | Minimum file size in MB; files smaller are skipped | `QB_FILE_FILTER_MIN_SIZE_MB` from config (100 if unset) |
| `--days` | Number of days to look back for recently added torrents | `2` |
| `--use-proxy` | Force-enable proxy for qBittorrent API requests | Auto |
| `--no-proxy` | Force-disable proxy for qBittorrent API requests | Auto |
| `--dry-run` | Show what would be filtered without making changes | `False` |
| `--category` | Filter only torrents in this category (deprecated; use `--categories`) | All categories |
| `--categories` | JSON array of categories to filter; overrides `--category` | All categories |
| `--delete-local-files` | Delete local files already downloaded but below the size threshold | `False` |

### Examples

```bash
# Default: use threshold from config
python3 -m apps.cli.qb_file_filter

# Override threshold (e.g. 50MB) and days
python3 -m apps.cli.qb_file_filter --min-size 50
python3 -m apps.cli.qb_file_filter --min-size 100 --days 3

# Dry run (preview without changes)
python3 -m apps.cli.qb_file_filter --dry-run

# Filter specific category only
python3 -m apps.cli.qb_file_filter --category JavDB

# Filter multiple categories
python3 -m apps.cli.qb_file_filter --categories '["Ad Hoc", "Daily Ingestion"]'

# With proxy
python3 -m apps.cli.qb_file_filter --use-proxy

# Delete already-downloaded small files
python3 -m apps.cli.qb_file_filter --delete-local-files
```

---

## PikPak Bridge

**Module:** `apps.cli.pikpak_bridge`

Transfers old torrents from qBittorrent to PikPak cloud storage.

### Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `--days` | Filter torrents older than N days | `3` |
| `--dry-run` | Test mode: no delete or PikPak add | `False` |
| `--individual` | Process torrents individually instead of batch mode | `False` (batch) |
| `--use-proxy` | Force-enable proxy for PikPak and qBittorrent requests | Auto |
| `--no-proxy` | Force-disable proxy for PikPak and qBittorrent requests | Auto |
| `--from-pipeline` | Internal: running from pipeline | `False` |
| `--session-id` | Report session ID for saving PikPak stats | `None` |
| `--root-folder` | PikPak root folder for uploads; each torrent goes under `{root}/{qB category}` | `PIKPAK_ROOT_FOLDER` from config |

### Examples

```bash
# Default: process torrents older than 3 days in batch mode
python3 -m apps.cli.pikpak_bridge

# Custom days threshold
python3 -m apps.cli.pikpak_bridge --days 7

# Dry run mode
python3 -m apps.cli.pikpak_bridge --dry-run

# Individual mode (one by one instead of batch)
python3 -m apps.cli.pikpak_bridge --individual

# With proxy
python3 -m apps.cli.pikpak_bridge --use-proxy

# Custom root folder
python3 -m apps.cli.pikpak_bridge --root-folder "/My Videos"

# Combine options
python3 -m apps.cli.pikpak_bridge --days 5 --dry-run --use-proxy
```

---

## Migration CLI

**Module:** `apps.cli.migration`

Migrates SQLite databases to the current schema version. Also provides backfill and alignment sub-commands.

### Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `--history-db` | Path to history.db (for `--backfill-actors`) | From config |
| `--backup` | Backup DB files before migration | `False` |
| `--verify` | Verify schema version and MovieHistory actor columns | `False` |
| `--dry-run` | Schema: preview only. With `--backfill-actors`: fetch but do not UPDATE | `False` |
| `--skip-schema` | Skip schema init (use with `--backfill-actors` or `--normalize-datetimes` only) | `False` |
| `--normalize-datetimes` | Normalize DateTime TEXT columns (history / reports / operations) | `False` |
| `--backfill-actors` | Fill empty ActorName (and related columns) from live detail pages | `False` |
| `--limit` | Backfill: max rows (0 = all) | `0` |
| `--no-proxy` | Backfill: direct HTTP without proxy (debug) | `False` |
| `--use-cf-bypass` | Backfill: enable CF bypass on first fetch attempt | `False` |

#### Inventory-History Alignment Arguments

These arguments control the `--align-inventory-history` sub-command, which aligns inventory-only codes into MovieHistory with JavDB search/detail enrichment.

| Argument | Description | Default |
|----------|-------------|---------|
| `--align-inventory-history` | Run inventory-history alignment | `False` |
| `--align-limit` | Max missing codes to process (0 = all) | `0` |
| `--align-limit-per-worker` | Max completed tasks per proxy worker (0 = use `--align-limit` or all) | `0` |
| `--align-codes` | Comma-separated video codes override | `""` |
| `--align-no-proxy` | Direct HTTP without proxy (debug; proxy enabled by default) | `False` |
| `--align-no-login` | Skip movies requiring JavDB login instead of attempting authentication | `False` |
| `--align-shuffle` | Randomise processing queue to avoid consecutive failures on similar prefixes | `False` |
| `--align-enqueue-qb` | Enqueue upgrade magnets to qBittorrent | `False` |
| `--align-execute-delete` | Run rclone purge on purge-plan CSV (destructive) | `False` |
| `--align-output-dir` | Output directory for generated reports/plan files | `""` |
| `--align-qb-category` | qBittorrent category override for upgrade enqueue | `""` |

### Examples

```bash
# Run schema migration
python3 -m apps.cli.migration

# Preview migration without changes
python3 -m apps.cli.migration --dry-run

# Backup before migration
python3 -m apps.cli.migration --backup

# Verify current schema version
python3 -m apps.cli.migration --verify

# Backfill actor names from JavDB (with limit)
python3 -m apps.cli.migration --backfill-actors --limit 100

# Backfill with CF bypass
python3 -m apps.cli.migration --backfill-actors --use-cf-bypass

# Normalize datetime columns
python3 -m apps.cli.migration --normalize-datetimes

# Align inventory with history
python3 -m apps.cli.migration --align-inventory-history --align-limit 50

# Align with shuffled queue and per-worker limit
python3 -m apps.cli.migration --align-inventory-history --align-shuffle --align-limit-per-worker 20
```

---

## Login CLI

**Module:** `apps.cli.login`

Logs into JavDB and extracts the session cookie. Updates `config.py` with the new `JAVDB_SESSION_COOKIE`. Required before using `--url` for custom URL scraping when the existing cookie has expired.

This CLI takes no arguments. It reads credentials from `config.py` (`JAVDB_USERNAME`, `JAVDB_PASSWORD`).

### Usage

```bash
python3 -m apps.cli.login
```

The script will:

1. Log in to JavDB with your credentials
2. Handle captcha (AI-based via GPT Vision API if configured)
3. Extract and update the session cookie in `config.py`
4. Verify the cookie works

### Prerequisites

- `JAVDB_USERNAME` and `JAVDB_PASSWORD` must be set in `config.py`
- Optional: `GPT_API_KEY` and `GPT_API_URL` for AI-based captcha solving

---

## Rollback CLI

**Module:** `apps.cli.rollback`

Undoes D1/SQLite writes from an in-progress or failed workflow run. Supports both automated cleanup-on-failure and manual targeted rollback.

**Default mode is dry-run.** Pass `--apply` to actually perform the rollback.

### Arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `--session-id` | ReportSessions.Id to roll back | `None` |
| `--run-id` | GITHUB_RUN_ID of the failed run | `None` |
| `--attempt` | GITHUB_RUN_ATTEMPT (used with `--run-id`) | `None` |
| `--run-started-at` | ISO timestamp of the failed run's start | `None` |
| `--scope` | Limit cleanup to one logical DB: `reports`, `operations`, `history`, or `all` | `all` |
| `--include-orphaned` | Also include in_progress sessions in the `--run-started-at` window | `False` |
| `--failure-reason` | Annotation persisted to ReportSessions.FailureReason | Auto-derived |
| `--dry-run` | Show what would be deleted (default) | `True` |
| `--apply` | Actually perform the rollback | `False` |
| `--force` | Allow rolling back a committed session | `False` |
| `--shard-date` | YYYY-MM-DD shard date for MovieClaim coordinator rollback | Today |
| `--no-claim-rollback` | Skip MovieClaim coordinator's rollback_staged_movies call | `False` |
| `--auto-resume-finalizing` | For pending-mode sessions in `finalizing` status, drive them to `committed` | `True` |
| `--no-auto-resume-finalizing` | Refuse to act on a `finalizing` session; surface it as failed | `False` |
| `--claim-rollback-attempts` | Retry count for rollback_staged_movies on transient failures | `3` |
| `--log-level` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` | `INFO` |

### Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success (or dry-run finished with no errors) |
| `2` | Session is committed (refused without `--force`), or cross-day reject |
| `3` | Cannot connect to D1/SQLite |
| `4` | Partial failure (some sessions left `failed` with non-zero drift) |

### Examples

```bash
# Dry-run targeted rollback
python3 -m apps.cli.rollback --session-id 42

# Apply targeted rollback
python3 -m apps.cli.rollback --session-id 42 --apply

# Rollback by GitHub run identity
python3 -m apps.cli.rollback --run-id 12345 --attempt 1

# Cleanup-on-failure (automated, no specific session known)
python3 -m apps.cli.rollback \
  --run-id 12345 --attempt 1 \
  --run-started-at 2026-05-04T19:30:00Z

# Partial scope
python3 -m apps.cli.rollback --session-id 42 --scope history

# Force rollback of committed session
python3 -m apps.cli.rollback --session-id 42 --apply --force

# Legacy sweep (include orphaned sessions in window)
python3 -m apps.cli.rollback --session-id 42 \
  --run-started-at 2026-05-04T19:30:00Z --include-orphaned
```

---

## Config Generator CLI

**Module:** `apps.cli.config_generator`

Generates `config.py` from environment variables. Used by GitHub Actions workflows to materialize a runtime config from `VAR_*` env vars (which in turn come from repository secrets / variables). Not typically run manually except for debugging the GH Actions setup locally.

### Usage

```bash
# GitHub Actions mode — reads VAR_* env vars and writes config.py
python3 -m apps.cli.config_generator --github-actions
```

### Behavior

- Reads every `VAR_*` env var (e.g. `VAR_QB_URL`, `VAR_QB_USERNAME`)
- Maps `VAR_FOO` → `FOO` in the output `config.py`
- Logs which variables were read (without values, for safety)
- Exits non-zero if any required variable is missing

See [GitHub Actions Setup](../self-hoster/github-actions-setup.md) for the full list of `VAR_*` mappings.

---

## Complete Spider Argument Reference

All arguments accepted by `apps.cli.spider`:

| Argument | Type | Description | Default | Example |
|----------|------|-------------|---------|---------|
| `--dry-run` | flag | Print items without writing CSV | `False` | `--dry-run` |
| `--output-file` | string | Custom CSV filename (without changing directory) | Auto-generated | `--output-file results.csv` |
| `--start-page` | int | Starting page number | `1` | `--start-page 5` |
| `--end-page` | int | Ending page number | `20` | `--end-page 10` |
| `--all` | flag | Parse until empty page (ignores `--end-page`) | `False` | `--all` |
| `--ignore-history` | flag | Ignore history for READING (scrape all pages) but still SAVE to history. Ad-hoc mode already ignores history for reading by default | `False` | `--ignore-history` |
| `--use-history` | flag | Enable history filter in ad-hoc mode (ad-hoc ignores history for reading by default) | `False` | `--use-history` |
| `--url` | string | Custom URL to scrape (enables ad-hoc mode; add `?page=x` for paginated URLs) | `None` | `--url "https://javdb.com/?vft=2"` |
| `--phase` | choice | Phase to run: `1` (subtitle+today), `2` (today only), `all` (both) | `all` | `--phase 1` |
| `--ignore-release-date` | flag | Ignore today/yesterday tags and download all entries matching phase criteria | `False` | `--ignore-release-date` |
| `--use-proxy` | flag | Force-enable proxy for this run | Auto (`PROXY_MODULES`) | `--use-proxy` |
| `--no-proxy` | flag | Force-disable proxy for this run | Auto (`PROXY_MODULES`) | `--no-proxy` |
| `--sequential` | flag | Force sequential detail processing in proxy pool mode | `False` | `--sequential` |
| `--always-bypass-time` | int (optional) | Minutes to keep using CF bypass after fallback success (omit value or 0 = whole session; omit flag = always direct-first) | `None` | `--always-bypass-time 30` |
| `--max-movies-phase1` | int | Limit phase 1 movies (testing) | `None` | `--max-movies-phase1 10` |
| `--max-movies-phase2` | int | Limit phase 2 movies (testing) | `None` | `--max-movies-phase2 5` |
| `--no-rclone-filter` | flag | Disable rclone inventory filter (do not skip entries already in rclone inventory) | `False` | `--no-rclone-filter` |
| `--disable-all-filters` | flag | Disable all filters (history, rclone inventory, release date) -- process every entry from index | `False` | `--disable-all-filters` |
| `--enable-dedup` | flag | Enable rclone dedup detection (compare against rclone inventory) | `False` | `--enable-dedup` |
| `--enable-redownload` | flag | Enable torrent re-download when a same-category torrent is significantly larger | `False` | `--enable-redownload` |
| `--redownload-threshold` | float | Size increase threshold for re-download (0.30 = 30%) | `0.30` | `--redownload-threshold 0.50` |
| `--from-pipeline` | flag | Internal: running from pipeline (uses GIT_USERNAME for commits) | `False` | `--from-pipeline` |
