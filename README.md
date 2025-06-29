# JavDB Auto Spider

A Python script to automatically fetch and extract torrent links from javdb.com across multiple pages.

## Features

- Fetches data in real-time from `javdb.com/?vft=2` to `javdb.com/?page=5&vft=2`
- Filters entries with both "含中字磁鏈" and "今日新種" tags (supports multiple language variations)
- Extracts magnet links based on specific categories:
  - 字幕 (subtitle) - magnet links with "Subtitle" tag
  - hacked - magnet links with priority order:
    1. UC无码破解 (-UC.无码破解.torrent)
    2. UC (-UC.torrent)
    3. U无码破解 (-U.无码破解.torrent)
    4. U (-U.torrent)
- Saves results to a timestamped CSV file in "Daily Report" directory
- Comprehensive logging with different levels (INFO, WARNING, DEBUG, ERROR)
- Multi-page processing with progress tracking
- Page tracking in CSV output
- Additional metadata extraction (actor, rating, comment count)

## Installation

1. Install Python dependencies:
```bash
pip install -r requirements.txt
```

## Usage

Run the script:
```bash
python Javdb_Spider.py
```

The script will:
1. Fetch pages 1-5 from javdb.com (from `?vft=2` to `?page=5&vft=2`)
2. Parse entries with required tags from each page
3. For each entry, fetch the detail page and extract magnet links
4. Save results to `Daily Report/Javdb_TodayTitle_{timestamp}.csv`
5. Log all activities to both console and `javdb_spider.log` file

## Output

The CSV file contains the following columns:
- `href`: The video page URL
- `video-title`: The video title
- `page`: The page number where the entry was found
- `actor`: The main actor/actress name
- `rate`: The rating score (e.g., "4.47")
- `comment_number`: Number of user comments/ratings
- `hacked_subtitle`: Magnet link for hacked version with subtitles (preferred)
- `hacked_no_subtitle`: Magnet link for hacked version without subtitles
- `subtitle`: Magnet link for subtitle version
- `no_subtitle`: Magnet link for regular version (prefers 4K if available)
- `size_hacked_subtitle`: Size of hacked subtitle torrent
- `size_hacked_no_subtitle`: Size of hacked no subtitle torrent
- `size_subtitle`: Size of subtitle torrent
- `size_no_subtitle`: Size of no subtitle torrent

## Logging

The script provides comprehensive logging:
- **INFO**: General progress information with page tracking
- **WARNING**: Non-critical issues
- **DEBUG**: Detailed debugging information
- **ERROR**: Critical errors

Logs are written to both:
- Console output
- `javdb_spider.log` file

Progress tracking includes:
- `[Page 1/5]` - Page-level progress
- `[15/75]` - Entry-level progress across all pages
- `[Page 2]` - Page-specific information

## Configuration

You can modify the page range by changing these variables in the script:
```python
START_PAGE = 1  # First page to process
END_PAGE = 5    # Last page to process
```

## Notes

- The script includes delays between requests to be respectful to the server
- 1-second delay between detail page requests
- 2-second delay between index page requests
- Make sure you have a stable internet connection
- The script uses proper headers to mimic a real browser
- The "hacked" column uses priority-based selection - only the highest priority match is kept
- Page information is tracked and included in the CSV output
- CSV files are automatically saved to the "Daily Report" directory
- Additional metadata (actor, rating, comments) is extracted from the index pages

## History System

The spider includes an intelligent history system that tracks which torrent types have been found for each movie:

### Multiple Torrent Type Tracking
- **Recent Fix**: Now correctly tracks ALL available torrent types per movie (e.g., both `hacked_subtitle` and `subtitle`)
- **Smart Processing**: Avoids re-processing movies that already have complete torrent collections
- **Missing Type Detection**: Only searches for torrent types that are missing based on preference rules

### History File
- Stored in `Daily Report/parsed_movies_history.csv`
- Records href, phase, video title, parsed date, and all torrent types found
- Automatically maintains one record per movie (removes duplicates)

### Processing Rules
- **Phase 1**: Processes movies with missing torrent types based on preferences
- **Phase 2**: Only processes movies that can be upgraded from `no_subtitle` to `hacked_no_subtitle`
- **New Movies**: Always processed regardless of history

See `README_torrent_types.md` for detailed information about the history system and torrent type classification.

## Pipeline and Automation

### Automated Pipeline

Use the pipeline script to run the complete workflow:
```bash
python pipeline_run_and_notify.py
```

The pipeline will:
1. Run the JavDB Spider to extract data
2. **Commit spider results to GitHub immediately** (so you can see progress even while pipeline is running)
3. Run the qBittorrent Uploader to add torrents
4. **Commit uploader results to GitHub immediately** (so you can see progress even while pipeline is running)
5. Perform final commit and push to GitHub (in case of any remaining changes)
6. Send email notifications with results

### Git Integration

The pipeline includes automatic git commit and push functionality with **incremental commits**:
- **Spider Step**: Commits results immediately after spider completes
- **Uploader Step**: Commits results immediately after uploader completes  
- **Final Step**: Commits any remaining changes
- Each commit includes timestamped messages indicating the pipeline step
- Pushes to your configured GitHub repository after each step
- Allows you to monitor progress in GitHub even while the pipeline is running

**Setup Git Configuration:**
1. Copy `git_config.py.example` to `git_config.py`
2. Update with your GitHub credentials
3. See `README_git_setup.md` for detailed instructions

### Email Notifications

The pipeline sends email notifications with:
- Success/failure status
- Log summaries
- CSV file attachments
- Git operation status

Configure email settings in `pipeline_run_and_notify.py`.
