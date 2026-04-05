import logging
import os
import sys

from packages.python.javdb_platform.config_helper import cfg


# ---------------------------------------------------------------------------
# Short logger name mapping
# ---------------------------------------------------------------------------
# Maps fully-qualified module paths to concise display names shown in logs.
# Call ``get_logger_name_mapping()`` to retrieve this for debugging.

_MODULE_SHORT_NAMES = {
    # javdb_platform
    'packages.python.javdb_platform.request_handler': 'RequestHandler',
    'packages.python.javdb_platform.proxy_pool': 'ProxyPool',
    'packages.python.javdb_platform.proxy_ban_manager': 'BanManager',
    'packages.python.javdb_platform.proxy_policy': 'ProxyPolicy',
    'packages.python.javdb_platform.logging_config': 'LogConfig',
    'packages.python.javdb_platform.config_helper': 'Config',
    'packages.python.javdb_platform.history_manager': 'History',
    'packages.python.javdb_platform.pipeline_service': 'Pipeline',
    'packages.python.javdb_platform.db': 'DB',
    'packages.python.javdb_platform.csv_writer': 'CSVWriter',
    'packages.python.javdb_platform.git_helper': 'Git',
    'packages.python.javdb_platform.spider_gateway': 'Gateway',
    'packages.python.javdb_platform.path_helper': 'PathHelper',
    'packages.python.javdb_platform.qb_config': 'QBConfig',
    # javdb_spider
    'packages.python.javdb_spider.fetch.fetch_engine': 'FetchEngine',
    'packages.python.javdb_spider.fetch.index': 'IndexFetch',
    'packages.python.javdb_spider.fetch.fallback': 'Fallback',
    'packages.python.javdb_spider.fetch.session': 'Session',
    'packages.python.javdb_spider.fetch.login_coordinator': 'Login',
    'packages.python.javdb_spider.fetch.sequential_backend': 'SeqBackend',
    'packages.python.javdb_spider.detail.runner': 'DetailRunner',
    'packages.python.javdb_spider.detail.parallel_mode': 'ParallelMode',
    'packages.python.javdb_spider.runtime.sleep': 'SleepMgr',
    'packages.python.javdb_spider.runtime.state': 'SpiderState',
    'packages.python.javdb_spider.runtime.config': 'SpiderConfig',
    'packages.python.javdb_spider.runtime.report': 'Report',
    'packages.python.javdb_spider.app.run_service': 'Spider',
    'packages.python.javdb_spider.services.dedup': 'Dedup',
    # javdb_core
    'packages.python.javdb_core.parser': 'Parser',
    'packages.python.javdb_core.masking': 'Masking',
    'packages.python.javdb_core.url_helper': 'URLHelper',
    'packages.python.javdb_core.filename_helper': 'FileHelper',
    'packages.python.javdb_core.magnet_extractor': 'MagnetExtractor',
    # javdb_integrations
    'packages.python.javdb_integrations.email_notification': 'Email',
    'packages.python.javdb_integrations.qb_uploader': 'QBUploader',
    'packages.python.javdb_integrations.pikpak_bridge': 'PikPak',
    'packages.python.javdb_integrations.qb_file_filter': 'QBFilter',
    'packages.python.javdb_integrations.rclone_manager': 'Rclone',
    'packages.python.javdb_integrations.rclone_helper': 'RcloneHelper',
    'packages.python.javdb_integrations.health_check': 'HealthCheck',
    'packages.python.javdb_integrations.login': 'JavDBLogin',
    'packages.python.javdb_integrations.fetch_page': 'FetchPage',
    # javdb_ingestion
    'packages.python.javdb_ingestion.adapters': 'Adapters',
    'packages.python.javdb_ingestion.policies': 'Policies',
    # javdb_migrations
    'packages.python.javdb_migrations.migrate_to_current': 'Migration',
    'packages.python.javdb_migrations.tools.csv_to_sqlite': 'MigCSVtoSQLite',
}

# Build reverse mapping for debug lookup
_SHORT_TO_FULL = {v: k for k, v in _MODULE_SHORT_NAMES.items()}


def get_logger_name_mapping():
    """Return a copy of the short-name → full-module-path mapping.

    Useful for debugging to find which source file corresponds to a
    short logger name seen in logs.
    """
    return dict(_SHORT_TO_FULL)


def _shorten_logger_name(name):
    """Return the short display name for a module, or a truncated fallback."""
    if name in _MODULE_SHORT_NAMES:
        return _MODULE_SHORT_NAMES[name]
    # For unmapped modules, strip common prefixes for readability
    for prefix in ('packages.python.', 'apps.cli.', 'scripts.'):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


class _ShortNameFormatter(logging.Formatter):
    """Formatter that replaces long module names with concise aliases."""

    def format(self, record):
        saved = record.name
        record.name = _shorten_logger_name(record.name)
        try:
            return super().format(record)
        finally:
            record.name = saved


_primary_log_file = None


def _reset_logging_state():
    """Reset internal state. Only intended for unit tests."""
    global _primary_log_file
    _primary_log_file = None
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
    root.handlers = []


def setup_logging(log_file=None, log_level=None):
    """Setup logging configuration for all modules.

    When *log_file* is provided this is an authoritative call from an
    entry-point script — existing handlers are replaced so the new file
    handler takes effect.

    When *log_file* is ``None`` (level-only update) and the root logger
    already has handlers, only the level is adjusted.  This prevents
    transitive imports from accidentally stripping a file handler that
    was set up earlier.

    If a file handler was already established for a *different* log file
    (e.g. the email-notification process already writes to its own log),
    a second call with a different *log_file* is silently skipped.  This
    guards against transitive module-level ``setup_logging`` calls that
    would otherwise truncate unrelated log files.

    Args:
        log_file: Log file path (optional).
        log_level: Log level string, e.g. ``"INFO"`` (optional).
    """
    global _primary_log_file

    if log_level is None:
        log_level = cfg('LOG_LEVEL', 'INFO')

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Level-only call and handlers already exist — just update levels.
    if log_file is None and root_logger.handlers:
        for h in root_logger.handlers:
            h.setLevel(numeric_level)
        return root_logger

    # Guard: if a primary log file was already set up for this process
    # and the new call targets a DIFFERENT file, skip it to prevent
    # accidental truncation (e.g. importing spider config from the
    # email-notification process).
    if log_file and _primary_log_file and os.path.abspath(log_file) != os.path.abspath(_primary_log_file):
        for h in root_logger.handlers:
            h.setLevel(numeric_level)
        return root_logger

    formatter = _ShortNameFormatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)

        file_handler = logging.FileHandler(log_file, mode='w', encoding='utf-8')
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
        _primary_log_file = os.path.abspath(log_file)

    # Silence noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.INFO)
    logging.getLogger("httpcore").setLevel(logging.INFO)

    return root_logger


def get_logger(name):
    """Get a logger with the specified name.

    Args:
        name: Logger name (usually ``__name__``).

    Returns:
        Logger instance.  The underlying Python logger retains the full
        module name so ``logging.getLogger()`` lookups still work.  The
        short display name is applied only at formatting time.
    """
    return logging.getLogger(name)