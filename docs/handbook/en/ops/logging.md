# Logging

The system provides comprehensive, dual-mode logging with four severity levels and progress tracking.

## Log Levels

| Level | Purpose |
|---|---|
| `INFO` | General progress information with tracking |
| `WARNING` | Non-critical issues (proxy failures, missing optional config, etc.) |
| `DEBUG` | Detailed debugging information (SQL queries, HTTP details, per-proxy stats) |
| `ERROR` | Critical errors that may halt execution |

### Setting the Log Level

The environment variable takes precedence over config.py:

```bash
# Via environment variable (highest priority)
export LOG_LEVEL=DEBUG

# Or in config.py
LOG_LEVEL = 'INFO'
```

## Progress Tracking

The spider and uploader include structured progress indicators in log output:

- `[Page 1/5]` -- Page-level progress during index scraping
- `[15/75]` -- Entry-level progress across all pages
- `[1/25]` -- Upload progress for qBittorrent torrent additions

## Console / File Dual-Mode Formatting

Console output and file logs use different formats by default:

- **Console**: Rendered in a mobile-friendly compact format
- **File logs** (`logs/spider.log`, etc.): Always use the verbose 4-field format for grep-friendly forensic analysis

Sections and summaries are emitted via shared helpers (`log_section`, `log_summary_block`, `log_group_start|end`) so the same call renders correctly in every output target.

## LOG_STYLE

Control the console output format with the `LOG_STYLE` environment variable:

| `LOG_STYLE` | Console Behaviour |
|---|---|
| `compact` (default) | `HH:MM:SS  > Component  message` + Unicode section dividers; emoji-anchored phase/summary blocks. Optimised for GitHub mobile and 30-second eye-scan. |
| `plain` | `HH:MM:SS LVL Component  message` (ASCII only); sections render as `==== TITLE ====`. Best for `tail | grep` pipelines and minimal terminals. |
| `verbose` | Legacy `<asctime> - <name> - <level> - <message>` format. Full rollback escape hatch -- use when bisecting log-format changes. |

```bash
# Use plain ASCII format
export LOG_STYLE=plain

# Use legacy verbose format
export LOG_STYLE=verbose
```

File logs (`logs/spider.log`, etc.) always use the verbose 4-field format regardless of `LOG_STYLE`.

## GitHub Actions Group Folding

When running in GitHub Actions (`GITHUB_ACTIONS=true` in the environment), the logging system automatically uses `::group::TITLE` / `::endgroup::` markers to create collapsible sections in the Actions UI.

Control this with `LOG_GITHUB_GROUPS`:

| Value | Behaviour |
|---|---|
| `auto` (default) | Fold when `GITHUB_ACTIONS=true`, no folding otherwise |
| `on` | Always emit group markers |
| `off` | Never emit group markers |

```bash
# Force off (useful when scraping raw spider logs from CI)
export LOG_GITHUB_GROUPS=off
```

With folding enabled, long but low-density blocks (per-proxy detail, dual-write deltas, JSON dumps) collapse by default in the GitHub Actions UI, so the run summary panel surfaces the key metrics first.

## GitHub Actions Step Summary

The ingestion workflows (`DailyIngestion` / `AdHocIngestion`) parse the spider's `SPIDER_STAT_*` stdout lines and write a Markdown table to `$GITHUB_STEP_SUMMARY`. This surfaces the run's key metrics (pages, found, parsed, skipped, failed counts, CSV filename, and session_id) in the Actions UI summary panel without expanding the spider log block.

## Log File Paths

Default log file paths (configurable in config.py or via `VAR_*` environment variables in CI):

| Setting | Default Path |
|---|---|
| `SPIDER_LOG_FILE` | `logs/spider.log` |
| `UPLOADER_LOG_FILE` | `logs/qb_uploader.log` |
| `PIPELINE_LOG_FILE` | `logs/pipeline.log` |
| `EMAIL_NOTIFICATION_LOG_FILE` | `logs/email_notification.log` |
| `PIKPAK_LOG_FILE` | `logs/pikpak_bridge.log` |
| `QB_FILE_FILTER_LOG_FILE` | `logs/qb_file_filter.log` |
| `DEDUP_LOG_FILE` | `logs/rclone_dedup.log` |

## DEBUG-Level Details

At `DEBUG` level, the following additional information is surfaced:

- **Proxy pool**: Per-proxy detail rows showing success rate, last-ok time, and last-fail time for each proxy. At `INFO` level, only the single-line summary is shown: `available=N/total / cooldown=K / banned=B`.
- **Rust extension logs**: Rust-side log targets (`javdb_rust_core::proxy::pool`, etc.) flow through the Python formatter via `pyo3_log` and are mapped to short display names: `ProxyPool`, `BanManager`, `FetchEngine`, `Parser`.
- **Database queries**: SQL statements and row counts for history and session operations.
- **HTTP request details**: Headers, status codes, and response timing.

## Using Logging in Code

The project uses Python's standard `logging` module with shared formatting helpers:

```python
import logging
from javdb.infra.logging import log_section, log_summary_block

logger = logging.getLogger(__name__)

# Section headers (auto-formats for console/file/GitHub Actions)
log_section("Phase 1: Subtitle Entries")

# Summary blocks (emoji-anchored, collapsible in GitHub Actions)
log_summary_block("Spider Statistics", {
    "Pages processed": 5,
    "Movies found": 75,
    "Torrents extracted": 150,
})

# Regular logging
logger.info("Processing movie: %s", movie_title)
logger.warning("Proxy failed, switching to backup")
logger.error("Failed to connect to qBittorrent", exc_info=True)
```
