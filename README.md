# JavDB Auto Spider

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/TongWu/JAVDB_AutoSpider)
![JadvDB Daily Ingestion](https://cronitor.io/badges/9uDCq6/production/qJMA3fMzsCxqf9S3tKJ0BxkfoBk.svg)

A comprehensive Python automation system for extracting torrent links from javdb.com and automatically adding them to qBittorrent. The system includes intelligent history tracking, git integration, automated pipeline execution, and duplicate download prevention.

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

### qBittorrent Integration
- Automatically reads current date's CSV file
- Connects to qBittorrent via Web UI API
- Adds torrents with proper categorization and settings
- **Dual Mode Support**: 
  - **Daily Mode**: Uses "JavDB" category for regular daily reports
  - **Ad Hoc Mode**: Uses "Ad Hoc" category for custom URL scraping results
- Comprehensive logging and progress tracking
- Detailed summary reports

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

## Installation

1. Install Python dependencies:
```bash
pip install -r requirements.txt
```

2. Configure the system by copying and editing the configuration file:
```bash
cp config.py.example config.py
```

## Usage

### Individual Scripts

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
# Ignore history file and scrape all pages
python Javdb_Spider.py --ignore-history

# Custom URL scraping (creates "Ad Hoc" directory)
python Javdb_Spider.py --url "https://javdb.com/?vft=2"
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
```

#### Argument Reference

| Argument | Description | Default | Example |
|----------|-------------|---------|---------|
| `--dry-run` | Print items without writing CSV | False | `--dry-run` |
| `--output-file` | Custom CSV filename | Auto-generated | `--output-file results.csv` |
| `--start-page` | Starting page number | 1 | `--start-page 5` |
| `--end-page` | Ending page number | 20 | `--end-page 10` |
| `--all` | Parse until empty page | False | `--all` |
| `--ignore-history` | Skip history checking | False | `--ignore-history` |
| `--url` | Custom URL to scrape | None | `--url "https://javdb.com/?vft=2"` |
| `--phase` | Phase to run (1/2/all) | all | `--phase 1` |

### Automated Pipeline

**Run the complete workflow:**
```bash
python pipeline_run_and_notify.py
```

The pipeline will:
1. Run the JavDB Spider to extract data
2. Commit spider results to GitHub immediately
3. Run the qBittorrent Uploader to add torrents
4. Commit uploader results to GitHub immediately
5. Perform final commit and push to GitHub
6. Send email notifications with results

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
# SPIDER CONFIGURATION
# =============================================================================
START_PAGE = 1
END_PAGE = 20
BASE_URL = 'https://javdb.com'

# =============================================================================
# LOGGING CONFIGURATION
# =============================================================================
LOG_LEVEL = 'INFO'
SPIDER_LOG_FILE = 'logs/Javdb_Spider.log'
UPLOADER_LOG_FILE = 'logs/qbtorrent_uploader.log'
PIPELINE_LOG_FILE = 'logs/pipeline_run_and_notify.log'

# =============================================================================
# FILE PATHS
# =============================================================================
DAILY_REPORT_DIR = 'Daily Report'
AD_HOC_DIR = 'Ad Hoc'
PARSED_MOVIES_CSV = 'parsed_movies_history.csv'
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
- **Daily Report CSV files**: `Daily Report/Javdb_TodayTitle_YYYYMMDD.csv`
- **Ad Hoc CSV files**: `Ad Hoc/Javdb_TodayTitle_YYYYMMDD.csv`
- **History file**: `Daily Report/parsed_movies_history.csv`
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

### Debug Mode

To see detailed operations, you can temporarily increase logging level in the scripts.

## Security Notes

- Configuration files (`git_config.py`, `qbtorrent_config.py`) are automatically excluded from git commits
- Never commit actual credentials to the repository
- Use personal access tokens instead of passwords for GitHub authentication
- Consider using environment variables for production deployments

## Notes

- The system includes delays between requests to be respectful to servers
- 1-second delay between detail page requests
- 2-second delay between index page requests
- 1-second delay between qBittorrent additions
- The system uses proper headers to mimic a real browser
- CSV files are automatically saved to the "Daily Report" directory
- The pipeline provides incremental commits for monitoring progress in real-time
