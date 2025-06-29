# JavDB Auto Spider

A comprehensive Python automation system for extracting torrent links from javdb.com and automatically adding them to qBittorrent. The system includes intelligent history tracking, git integration, and automated pipeline execution.

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
- Comprehensive logging and progress tracking
- Detailed summary reports

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

2. Configure the system by copying and editing configuration files:
```bash
cp git_config.py.example git_config.py
cp qbtorrent_config.py.example qbtorrent_config.py
```

## Usage

### Individual Scripts

**Run the spider to extract data:**
```bash
python Javdb_Spider.py
```

**Run the qBittorrent uploader:**
```bash
python qbtorrent_uploader.py
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

### Git Configuration (`git_config.py`)

Configure git operations for the pipeline:

```python
# GitHub username
GIT_USERNAME = 'your_github_username'

# GitHub personal access token (recommended) or password
GIT_PASSWORD = 'your_github_token_or_password'

# GitHub repository URL
GIT_REPO_URL = 'https://github.com/your_username/your_repo_name.git'

# Git branch to push to
GIT_BRANCH = 'main'
```

**GitHub Authentication Setup:**
1. Go to GitHub Settings → Developer settings → Personal access tokens
2. Generate a new token with `repo` permissions
3. Use this token as `GIT_PASSWORD`

### qBittorrent Configuration (`qbtorrent_config.py`)

Configure qBittorrent connection settings:

```python
# Connection settings
QB_HOST = 'your_qbittorrent_ip'
QB_PORT = 'your_qbittorrent_port'
QB_USERNAME = 'your_qbittorrent_username'
QB_PASSWORD = 'your_qbittorrent_password'

# Torrent settings
TORRENT_CATEGORY = 'JavDB'
TORRENT_SAVE_PATH = ''  # Leave empty for default
AUTO_START = True
SKIP_CHECKING = False

# Performance settings
REQUEST_TIMEOUT = 30
DELAY_BETWEEN_ADDITIONS = 1
```

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
- **CSV files**: `Daily Report/Javdb_TodayTitle_YYYYMMDD.csv`
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
- **Phase 2**: Only processes movies that can be upgraded from `no_subtitle` to `hacked_no_subtitle`
- **New Movies**: Always processed regardless of history

### Preference Rules
- **Hacked Category**: Always prefer `hacked_subtitle` over `hacked_no_subtitle`
- **Subtitle Category**: Always prefer `subtitle` over `no_subtitle`
- **Complete Collection Goal**: Each movie should have both categories represented

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
