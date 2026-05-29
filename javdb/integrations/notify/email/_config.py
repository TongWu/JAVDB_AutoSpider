"""Shared configuration constants and logging setup for the notify email package.

These module-level constants were historically defined at the top of the
single-file ``email.py`` module. They are gathered here so the responsibility
submodules (``log_analysis``, ``report_builder``, ``delivery``) and the
``service`` orchestration can share a single source without circular imports.

Behaviour is byte-for-byte identical to the pre-split module: same ``cfg()``
keys, defaults, repo-root chdir, ``sys.path`` insert, and one-time
``setup_logging`` call.
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
os.chdir(REPO_ROOT)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Import unified configuration
from javdb.infra.config import cfg

GIT_USERNAME = cfg('GIT_USERNAME', 'your_github_username')
GIT_PASSWORD = cfg('GIT_PASSWORD', 'your_github_password_or_token')
GIT_REPO_URL = cfg('GIT_REPO_URL', 'https://github.com/your_username/your_repo_name.git')
GIT_BRANCH = cfg('GIT_BRANCH', 'main')

SMTP_SERVER = cfg('SMTP_SERVER', 'smtp.gmail.com')
SMTP_PORT = cfg('SMTP_PORT', 587)
SMTP_USER = cfg('SMTP_USER', 'your_email@gmail.com')
SMTP_PASSWORD = cfg('SMTP_PASSWORD', 'your_email_password')
EMAIL_FROM = cfg('EMAIL_FROM', 'your_email@gmail.com')
EMAIL_TO = cfg('EMAIL_TO', 'your_email@gmail.com')

PIPELINE_LOG_FILE = cfg('PIPELINE_LOG_FILE', 'logs/pipeline.log')
SPIDER_LOG_FILE = cfg('SPIDER_LOG_FILE', 'logs/spider.log')
UPLOADER_LOG_FILE = cfg('UPLOADER_LOG_FILE', 'logs/qb_uploader.log')
DAILY_REPORT_DIR = cfg('DAILY_REPORT_DIR', 'reports/DailyReport')
AD_HOC_DIR = cfg('AD_HOC_DIR', 'reports/AdHoc')
LOG_LEVEL = cfg('LOG_LEVEL', 'INFO')
PIKPAK_LOG_FILE = cfg('PIKPAK_LOG_FILE', 'logs/pikpak_bridge.log')

_EMAIL_REPORTS_DIR = cfg('REPORTS_DIR', 'reports')
DEDUP_CSV = cfg('DEDUP_CSV', 'dedup.csv')
DEDUP_DIR = cfg('DEDUP_DIR', 'reports/Dedup')
DEDUP_LOG_FILE = cfg('DEDUP_LOG_FILE', 'logs/rclone_dedup.log')

EMAIL_NOTIFICATION_LOG_FILE = cfg('EMAIL_NOTIFICATION_LOG_FILE', 'logs/email_notification.log')

# --- LOGGING SETUP ---
from javdb.infra.logging import setup_logging

setup_logging(EMAIL_NOTIFICATION_LOG_FILE, LOG_LEVEL)

_MAX_READ_BYTES = 10 * 1024 * 1024  # 10 MB
