"""Email notification service package.

Selected legacy exports remain during ADR-015 Phase 6 and are removed by
IMP-ADR015-07 after the bake window. Each bake re-export points at the
function's final module home (``log_analysis`` / ``report_builder`` /
``delivery``), not at ``_legacy``.
"""

from javdb.integrations.notify.email.options import EmailNotificationOptions
from javdb.integrations.notify.email.result import EmailNotificationResult
from javdb.integrations.notify.email.service import run_email_notification

from javdb.integrations.notify.email.delivery import (
    convert_log_to_txt,
    send_email,
)
from javdb.integrations.notify.email.log_analysis import (
    analyze_pikpak_log,
    analyze_pipeline_log,
    analyze_spider_log,
    analyze_uploader_log,
    check_workflow_job_status,
    extract_dedup_statistics,
    extract_pikpak_statistics,
    extract_proxy_ban_summary,
    extract_spider_statistics,
    extract_uploader_statistics,
    find_proxy_ban_html_files,
    get_proxy_ban_summary,
)
from javdb.integrations.notify.email.report_builder import (
    extract_adhoc_info_from_csv,
    find_latest_adhoc_csv,
    find_latest_daily_csv,
    format_adhoc_info,
    format_email_report,
    get_report_display_datetime,
)

__all__ = [
    "EmailNotificationOptions",
    "EmailNotificationResult",
    "run_email_notification",
    "analyze_pikpak_log",
    "analyze_pipeline_log",
    "analyze_spider_log",
    "analyze_uploader_log",
    "check_workflow_job_status",
    "convert_log_to_txt",
    "extract_adhoc_info_from_csv",
    "extract_dedup_statistics",
    "extract_pikpak_statistics",
    "extract_proxy_ban_summary",
    "extract_spider_statistics",
    "extract_uploader_statistics",
    "find_latest_adhoc_csv",
    "find_latest_daily_csv",
    "find_proxy_ban_html_files",
    "format_adhoc_info",
    "format_email_report",
    "get_proxy_ban_summary",
    "get_report_display_datetime",
    "send_email",
]
