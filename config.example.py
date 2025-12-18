"""
Example configuration file for JavDB Pipeline
Copy this file to config.py and fill in your actual values
"""

# === Git Configuration ===
GIT_USERNAME = 'your_github_username'
GIT_PASSWORD = 'your_github_token'  # Use GitHub Personal Access Token
GIT_REPO_URL = 'https://github.com/your_username/your_repo.git'
GIT_BRANCH = 'main'

# === Email Configuration ===
SMTP_SERVER = 'smtp.gmail.com'
SMTP_PORT = 587
SMTP_USER = 'your_email@gmail.com'
SMTP_PASSWORD = 'your_app_password'
EMAIL_FROM = 'your_email@gmail.com'
EMAIL_TO = 'your_email@gmail.com'

# === Logging Configuration ===
LOG_LEVEL = 'INFO'  # Options: DEBUG, INFO, WARNING, ERROR, CRITICAL
PIPELINE_LOG_FILE = 'logs/pipeline_run_and_notify.log'
SPIDER_LOG_FILE = 'logs/Javdb_Spider.log'
UPLOADER_LOG_FILE = 'logs/qbtorrent_uploader.log'
PIKPAK_LOG_FILE = 'logs/pikpak_bridge.log'

# === Directory Configuration ===
DAILY_REPORT_DIR = 'Daily Report'
AD_HOC_DIR = 'Ad Hoc'

# === Spider Configuration ===
DETAIL_PAGE_SLEEP = 5  # Seconds to wait between detail page requests
PHASE2_MIN_RATE = 4.0  # Minimum rating for Phase 2 entries
PHASE2_MIN_COMMENTS = 85  # Minimum comments for Phase 2 entries
IGNORE_RELEASE_DATE_FILTER = False  # Set to True to ignore release date filtering

# === Proxy Configuration ===
# List of proxies to use (optional)
# Format: [{'http': 'http://proxy:port', 'https': 'http://proxy:port', 'name': 'Proxy1'}, ...]
PROXY_LIST = []

# Proxy pool configuration
PROXY_COOLDOWN_SECONDS = 691200  # 8 days in seconds
PROXY_MAX_FAILURES = 3  # Max failures before cooldown

# === qBittorrent Configuration ===
QB_HOST = 'http://localhost:8080'
QB_USERNAME = 'admin'
QB_PASSWORD = 'your_qb_password'

# === PikPak Configuration ===
PIKPAK_USERNAME = 'your_pikpak_email@example.com'
PIKPAK_PASSWORD = 'your_pikpak_password'

# === CloudFlare Bypass Configuration (Optional) ===
USE_CF_BYPASS = False  # Enable CloudFlare bypass service
CF_BYPASS_URL = ''  # CloudFlare bypass service URL (if using)
