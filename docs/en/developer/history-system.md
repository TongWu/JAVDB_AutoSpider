# History System and Downloaded Indicator

The spider includes an intelligent history system that tracks which torrent types have been found for each movie. This document covers the history tracking architecture, torrent classification, processing rules, and the duplicate download prevention feature.

---

## History System

### Multiple Torrent Type Tracking

The history system tracks ALL available torrent types per movie. Each movie can have up to four torrent categories:

| Category | Description |
|---|---|
| `hacked_subtitle` | Hacked (uncensored) version with subtitles -- highest value |
| `hacked_no_subtitle` | Hacked (uncensored) version without subtitles |
| `subtitle` | Standard version with subtitles |
| `no_subtitle` | Standard version without subtitles |

The system prevents redundant processing when movies already have complete torrent collections, and only searches for torrent types that are missing based on the preference rules below.

### Storage

History is stored in two places:

1. **SQLite database** (`reports/history.db`) -- Primary storage via `MovieHistory` and `TorrentHistory` tables
2. **CSV file** (`reports/parsed_movies_history.csv`) -- Legacy format, still maintained for backward compatibility

When using `STORAGE_BACKEND=d1` or `dual`, history is also stored in Cloudflare D1 databases.

---

## Processing Rules

### Phase 1 (Subtitle Entries)

Phase 1 targets movies with subtitle tags ("含中字磁鏈" and similar language variations). Processing rules:

- **New movies**: Always processed, regardless of history
- **Existing movies**: Only processed if they are missing torrent types based on the preference rules
- Entries are filtered by release date by default (today/yesterday tags), unless `--ignore-release-date` is set

### Phase 2 (Non-Subtitle / Quality Entries)

Phase 2 targets movies without subtitle tags that meet quality criteria. Processing rules:

- Only processes movies that can be **upgraded** from `no_subtitle` to `hacked_no_subtitle`
- Must meet configurable quality thresholds:
  - **Minimum Rating**: `PHASE2_MIN_RATE` (default: 4.0)
  - **Minimum Comments**: `PHASE2_MIN_COMMENTS` (default: 100)
- New movies that meet quality criteria are also processed

### Preference Rules

The system follows a strict preference hierarchy within each category:

**Hacked category priority:**
1. `hacked_subtitle` (always preferred over `hacked_no_subtitle`)

**Subtitle category priority:**
1. `subtitle` (always preferred over `no_subtitle`)

**Complete collection goal:** Each movie should ideally have both category groups represented (one hacked variant + one subtitle variant).

---

## Release Date Filtering

By default, the spider filters entries based on release date tags ("今日新種" = today's new torrents, "昨日新種" = yesterday's new torrents).

### Override via Command-Line (Recommended)

```bash
# Ignore release date tags for a single run
python3 -m apps.cli.spider --ignore-release-date

# Or via the pipeline
python3 -m apps.cli.pipeline --ignore-release-date
```

### Override via Configuration File

Set `IGNORE_RELEASE_DATE_FILTER = True` in `config.py` to permanently ignore release date tags.

### Behaviour When Release Date Filtering is Disabled

- **Phase 1**: Downloads ALL entries with subtitle tags, regardless of release date
- **Phase 2**: Downloads ALL entries meeting quality criteria (rating and comment thresholds), regardless of release date

This is useful when:
- Backfilling your collection with older content
- Scraping a custom URL (actor/tag page) where release date is not relevant
- Downloading everything matching the quality criteria

---

## Related Subsystems

The history system interacts with three subsystems that have their own dedicated documentation:

- **Proxy support** — pool mode, modular control (`PROXY_MODULES`), session-scoped bans, CLI overrides (`--use-proxy` / `--no-proxy`). See [Proxy Setup](../self-hoster/proxy-setup.md).
- **CloudFlare bypass** — automatic fallback via `CloudflareBypassForScraping`, sticky bypass window (`--always-bypass-time`), per-proxy service URL resolution. See [CloudFlare Bypass](../self-hoster/cloudflare-bypass.md).
- **JavDB auto login** — session cookie management for `--url` scraping, captcha solving (manual / GPT / OCR / 2Captcha), when to re-run. See [JavDB Login](../self-hoster/javdb-login.md).

The history system itself is **independent of these subsystems** — it works the same way regardless of which proxy mode is active or whether CF bypass is engaged.

---

## Downloaded Indicator Feature

The duplicate download prevention feature automatically marks downloaded torrents in daily reports and skips them in the qBittorrent uploader.

### How It Works

1. **Daily Report Generation**: The spider generates a CSV report with magnet links
2. **History Check**: The uploader checks the history database/CSV when starting
3. **Add Indicators**: `[DOWNLOADED]` prefix is added to magnet links of already-downloaded torrents
4. **Skip Processing**: The uploader skips torrents with `[DOWNLOADED]` indicators
5. **Upload New Torrents**: Only torrents not in history are uploaded to qBittorrent
6. **Update History**: When new torrent types are found for an existing movie, the `update_date` is modified

### CSV Format

**Before indicator processing:**
```csv
href,video_code,hacked_subtitle,subtitle
/v/mOJnXY,IPZZ-574,magnet:?xt=...,magnet:?xt=...
```

**After indicator processing:**
```csv
href,video_code,hacked_subtitle,subtitle
/v/mOJnXY,IPZZ-574,[DOWNLOADED] magnet:?xt=...,[DOWNLOADED] magnet:?xt=...
```

### Enhanced History Format

The history CSV uses an enhanced format with individual columns for each torrent type:

```csv
href,phase,video_code,create_date,update_date,last_visited_datetime,hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle
/v/mOJnXY,1,IPZZ-574,2025-07-09 20:00:57,2025-07-09 20:05:30,2025-07-09 20:05:30,2025-07-09 20:05:30,,2025-07-09 20:05:30,
```

| Column | Description |
|---|---|
| `href` | Movie detail page path |
| `phase` | Phase in which the movie was first discovered (1 or 2) |
| `video_code` | Video identifier code |
| `create_date` | When the movie was first discovered and logged |
| `update_date` | When the movie was last updated with new torrent types |
| `last_visited_datetime` | When the movie detail page was last visited |
| `hacked_subtitle` | Download date for hacked version with subtitles (empty if not downloaded) |
| `hacked_no_subtitle` | Download date for hacked version without subtitles (empty if not downloaded) |
| `subtitle` | Download date for subtitle version (empty if not downloaded) |
| `no_subtitle` | Download date for regular version (empty if not downloaded) |

**Old format** (automatically migrated):
```csv
href,phase,video_code,parsed_date,torrent_type
```

The system automatically handles migration from the old format to the new format. Existing files are converted with backward compatibility preserved.

### Torrent Type Merging

When updating existing records, new torrent types are merged with existing ones:
- Only new (previously empty) torrent type columns are filled in
- Existing download dates are never overwritten
- `update_date` is refreshed whenever any new torrent type is added

### Important Notes

1. **History file dependency**: The feature depends on `reports/parsed_movies_history.csv` (CSV) or `reports/history.db` (SQLite)
2. **Indicator format**: The downloaded indicator is `[DOWNLOADED] ` (note the trailing space before the magnet link)
3. **Backward compatibility**: If the history file does not exist, the feature degrades gracefully without affecting normal operation
4. **Performance**: History checks use efficient CSV reading / SQLite queries and do not significantly impact performance
5. **Timestamp tracking**: `create_date` remains constant; `update_date` changes with each modification

---

## Re-Download (Torrent Upgrade) Mode

When enabled, the spider checks if a same-category torrent is significantly larger than the previously downloaded one and triggers a re-download.

### Configuration

```python
# In config.py
ENABLE_REDOWNLOAD = True
REDOWNLOAD_SIZE_THRESHOLD = 0.30  # 30% larger triggers re-download
```

### CLI Flags

```bash
# Enable re-download for a single run
python3 -m apps.cli.spider --enable-redownload

# With custom threshold
python3 -m apps.cli.spider --enable-redownload --redownload-threshold 0.50
```

In GitHub Actions, re-download is enabled by default for scheduled daily runs and can be toggled via the `enable_redownload` workflow dispatch input.
