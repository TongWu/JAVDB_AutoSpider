"""Centralised logging configuration for the JAVDB spider stack.

This module is the single source of truth for log formatting across:

- the **console / GitHub Actions** output (compact mobile-friendly format
  by default), and
- the **on-disk file logs** (verbose 4-field format, kept verbatim for
  forensic / grep workflows).

Three console styles are available via the ``LOG_STYLE`` env var (or the
``log_style`` keyword argument to :func:`setup_logging`):

- ``compact`` *(default)* — section dividers, single-character anchors
  for non-INFO levels, ``HH:MM:SS`` timestamps. Optimised for mobile
  reading and 30-second eye-scan over a daily ingestion run.
- ``plain`` — single-line ASCII, ``HH:MM:SS LVL Component msg``. No
  Unicode dividers, ideal for ``tail | grep`` pipelines.
- ``verbose`` — the legacy ``%(asctime)s - %(name)s - %(levelname)s -
  %(message)s`` format, kept as a full-rollback escape hatch.

GitHub Actions ``::group::`` folding is auto-detected when
``GITHUB_ACTIONS=true`` is in the environment, and can be forced
on/off via ``LOG_GITHUB_GROUPS=on|off|auto``.

The :func:`log_section`, :func:`log_summary_block`, :func:`log_group_start`
and :func:`log_group_end` helpers emit special log records that each
formatter renders appropriately:

- compact console: Unicode section dividers and indented summary blocks;
- plain console: ASCII ``==== TITLE ====`` banners;
- file (verbose): plain ``=== TITLE ===`` ASCII so file logs stay
  emoji-/control-character-free for grep.
"""

from __future__ import annotations

import logging
import os
import time

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
    # javdb_platform — coordinator / D1 clients (added in log redesign)
    'packages.python.javdb_platform.runner_registry_client': 'RunnerRegistry',
    'packages.python.javdb_platform.movie_claim_client': 'MovieClaim',
    'packages.python.javdb_platform.proxy_coordinator_client': 'ProxyCoord',
    'packages.python.javdb_platform.login_state_client': 'LoginState',
    'packages.python.javdb_platform.d1_client': 'D1',
    'packages.python.javdb_platform.dual_connection': 'DualDB',
    # javdb_spider
    'packages.python.javdb_spider.fetch.fetch_engine': 'FetchEngine',
    'packages.python.javdb_spider.fetch.index': 'IndexFetch',
    'packages.python.javdb_spider.fetch.index_parallel': 'IndexFetch',
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
    'packages.python.javdb_migrations.tools.reconcile_d1_drift': 'D1Reconcile',
    # Rust → Python bridge (pyo3_log targets).  Cover both `::` and `.`
    # separators since the bridge has historically emitted either.
    # The Rust [lib] name is `rust_core` (the crate installs as
    # `javdb.rust_core` but the log crate's module-path target is based
    # on the Rust lib name only).
    'rust_core.proxy.pool': 'ProxyPool',
    'rust_core::proxy::pool': 'ProxyPool',
    'rust_core.proxy.ban_manager': 'BanManager',
    'rust_core::proxy::ban_manager': 'BanManager',
    'rust_core.fetch.engine': 'FetchEngine',
    'rust_core::fetch::engine': 'FetchEngine',
    'rust_core.parser': 'Parser',
    'rust_core::parser': 'Parser',
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


# ---------------------------------------------------------------------------
# LogRecord metadata keys used by the section/summary/group helpers.
# Keys live in ``record.__dict__`` via ``logger.log(..., extra={...})``.
# ---------------------------------------------------------------------------

_SECTION_KEY = '_logfmt_section_title'
_SECTION_EMOJI_KEY = '_logfmt_section_emoji'
_SUMMARY_KEY = '_logfmt_summary_block'
_GROUP_ACTION_KEY = '_logfmt_group_action'
_GROUP_TITLE_KEY = '_logfmt_group_title'

# Console anchor characters by level — one column wide, padded so all
# level rows align in the compact format.  Empty (space) for INFO/DEBUG
# keeps the eye on actual content, not log level chrome.
_LEVEL_ANCHOR = {
    logging.DEBUG: ' ',
    logging.INFO: ' ',
    logging.WARNING: '⚠',
    logging.ERROR: '✗',
    logging.CRITICAL: '✗',
}


def _section_divider(title: str, emoji=None, width: int = 44) -> str:
    """Render a single section header line of the form ``──── 🎬 TITLE ─────``."""
    head_parts = ['──── ']
    if emoji:
        head_parts.append(emoji)
        head_parts.append(' ')
    head_parts.append(title)
    head_parts.append(' ')
    head = ''.join(head_parts)
    pad = max(4, width - len(head))
    return head + '─' * pad


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


class _ShortNameFormatter(logging.Formatter):
    """Base formatter that replaces long module names with concise aliases.

    Subclasses override :meth:`format` to render the rest of the line.
    All subclasses delegate the short-name substitution to this base
    class via :meth:`_short_name`.
    """

    def _short_name(self, record):
        return _shorten_logger_name(record.name)

    def format(self, record):
        # Default behaviour preserved for callers that instantiated this
        # class directly with a custom format string.
        saved = record.name
        record.name = self._short_name(record)
        try:
            return super().format(record)
        finally:
            record.name = saved


class _LegacyVerboseFormatter(_ShortNameFormatter):
    """Legacy 4-field format: ``<asctime> - <name> - <level> - <msg>``.

    Used by the **file** handler by default so on-disk logs preserve
    their pre-redesign forensic shape.  Section / group / summary
    records are rendered as plain ASCII (``=== TITLE ===`` and
    ``--- begin: TITLE ---``) so the file stays grep-friendly and
    free of CI-only ``::group::`` markers.
    """

    def __init__(self):
        super().__init__('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    def format(self, record):
        section_title = getattr(record, _SECTION_KEY, None)
        group_action = getattr(record, _GROUP_ACTION_KEY, None)

        if section_title or group_action:
            saved_msg = record.msg
            saved_args = record.args
            try:
                if section_title:
                    record.msg = '=== ' + str(section_title) + ' ==='
                elif group_action == 'start':
                    title = getattr(record, _GROUP_TITLE_KEY, '') or ''
                    record.msg = '--- begin: ' + str(title) + ' ---'
                else:  # 'end'
                    record.msg = '--- end ---'
                record.args = ()
                return super().format(record)
            finally:
                record.msg = saved_msg
                record.args = saved_args

        return super().format(record)


class _CompactConsoleFormatter(_ShortNameFormatter):
    """Mobile-friendly compact console format.

    Layout::

        HH:MM:SS  ▸ Component   message text

    - ``HH:MM:SS`` only — no date (run timestamp lives in the file
      name / GitHub Actions run metadata) and no millis.
    - One-character anchor for non-INFO levels (``⚠`` warn, ``✗``
      error/critical); blank for INFO/DEBUG so prose floats to the
      front of the eye.
    - Logger name truncated/padded to a fixed 12-column field so the
      message column always starts at the same offset.

    Section / summary records render as Unicode banners.  When
    ``github_groups`` is ``True``, group markers emit literal
    column-0 ``::group::TITLE`` / ``::endgroup::`` lines that GitHub
    Actions UI folds into collapsible regions.
    """

    NAME_WIDTH = 12

    def __init__(self, *, github_groups: bool = False):
        super().__init__('%(message)s')
        self._github_groups = github_groups

    def format(self, record):
        # 1. Group markers.
        action = getattr(record, _GROUP_ACTION_KEY, None)
        if action:
            title = getattr(record, _GROUP_TITLE_KEY, '') or ''
            if self._github_groups:
                if action == 'start':
                    return f'::group::{title}'
                return '::endgroup::'
            # Without GH groups, use a section divider for ``start`` and
            # an empty line for ``end`` (visually closes the section
            # without producing a stray ``─`` row).
            if action == 'start':
                return '\n' + _section_divider(title)
            return ''

        # 2. Section header.
        section_title = getattr(record, _SECTION_KEY, None)
        if section_title:
            emoji = getattr(record, _SECTION_EMOJI_KEY, None)
            return '\n' + _section_divider(str(section_title), emoji=emoji)

        # 3. Summary body line — pre-formatted by ``log_summary_block``;
        #    emit verbatim, no prefix.
        if getattr(record, _SUMMARY_KEY, False):
            return record.getMessage()

        # 4. Regular log line.
        ts = time.strftime('%H:%M:%S', time.localtime(record.created))
        anchor = _LEVEL_ANCHOR.get(record.levelno, ' ')
        name = self._short_name(record)
        if len(name) > self.NAME_WIDTH:
            name_disp = name[: self.NAME_WIDTH]
        else:
            name_disp = name.ljust(self.NAME_WIDTH)

        msg = record.getMessage()
        line = f'{ts}  {anchor} {name_disp}  {msg}'
        if record.exc_info:
            line += '\n' + self.formatException(record.exc_info)
        return line


class _PlainConsoleFormatter(_ShortNameFormatter):
    """Single-line ASCII console format (Option A in the design plan).

    Layout::

        HH:MM:SS LVL Component   message text

    No Unicode dividers, no GitHub Actions group folding.  Section
    records render as ``==== TITLE ====``; summary lines stream as-is.
    Best for terminals without good Unicode support and for ``tail |
    grep`` pipelines.
    """

    LEVEL_ABBR = {
        logging.DEBUG: 'DBG',
        logging.INFO: 'INF',
        logging.WARNING: 'WRN',
        logging.ERROR: 'ERR',
        logging.CRITICAL: 'CRT',
    }
    NAME_WIDTH = 12

    def __init__(self):
        super().__init__('%(message)s')

    def format(self, record):
        action = getattr(record, _GROUP_ACTION_KEY, None)
        if action:
            title = getattr(record, _GROUP_TITLE_KEY, '') or ''
            if action == 'start':
                return f'==== {title} ===='
            return ''

        section_title = getattr(record, _SECTION_KEY, None)
        if section_title:
            return f'==== {section_title} ===='

        if getattr(record, _SUMMARY_KEY, False):
            return record.getMessage()

        ts = time.strftime('%H:%M:%S', time.localtime(record.created))
        lvl = self.LEVEL_ABBR.get(record.levelno, 'INF')
        name = self._short_name(record)
        if len(name) > self.NAME_WIDTH:
            name_disp = name[: self.NAME_WIDTH]
        else:
            name_disp = name.ljust(self.NAME_WIDTH)

        msg = record.getMessage()
        line = f'{ts} {lvl} {name_disp}  {msg}'
        if record.exc_info:
            line += '\n' + self.formatException(record.exc_info)
        return line


# ---------------------------------------------------------------------------
# Section / summary / group emit helpers
# ---------------------------------------------------------------------------


def log_section(logger, title, *, emoji=None, level=logging.INFO):
    """Emit a section header.

    On compact / plain consoles this renders as a banner divider; on
    file handlers it falls back to ``=== TITLE ===`` so the file log
    remains grep-friendly without Unicode artefacts.
    """
    logger.log(
        level,
        title,
        extra={
            _SECTION_KEY: title,
            _SECTION_EMOJI_KEY: emoji,
        },
    )


def log_summary_block(
    logger,
    title,
    kv_pairs,
    *,
    emoji='📊',
    level=logging.INFO,
    indent='   ',
):
    """Emit a section header followed by indented ``key  value`` lines.

    ``kv_pairs`` may be a mapping or any iterable of ``(key, value)``
    tuples.  Keys are left-padded so values align in a table-like
    column on the compact console.
    """
    log_section(logger, title, emoji=emoji, level=level)
    if hasattr(kv_pairs, 'items'):
        kv_pairs = kv_pairs.items()
    pairs = [(str(k), v) for k, v in kv_pairs]
    if not pairs:
        return
    max_key = max(len(k) for k, _ in pairs)
    for key, value in pairs:
        line = f'{indent}{key.ljust(max_key)}  {value}'
        logger.log(level, line, extra={_SUMMARY_KEY: True})


def log_group_start(logger, title, *, level=logging.INFO):
    """Begin a collapsible group.

    On GitHub Actions consoles emits ``::group::TITLE`` (folded by the
    Actions UI); on regular consoles emits a section divider; on file
    handlers emits ``--- begin: TITLE ---``.
    """
    logger.log(
        level,
        title,
        extra={
            _GROUP_ACTION_KEY: 'start',
            _GROUP_TITLE_KEY: title,
        },
    )


def log_group_end(logger, *, level=logging.INFO):
    """End the most-recently-opened group.

    No-op on consoles without folding (a blank line is emitted to
    visually close the section).
    """
    logger.log(
        level,
        '',
        extra={
            _GROUP_ACTION_KEY: 'end',
            _GROUP_TITLE_KEY: '',
        },
    )


# ---------------------------------------------------------------------------
# setup_logging — entry point
# ---------------------------------------------------------------------------

_VALID_STYLES = ('compact', 'plain', 'verbose')

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


def _resolve_console_style(log_style):
    if log_style is None:
        log_style = os.environ.get('LOG_STYLE') or cfg('LOG_STYLE', 'compact')
    style = (log_style or 'compact').strip().lower()
    if style not in _VALID_STYLES:
        style = 'compact'
    return style


def _resolve_github_groups():
    setting = (os.environ.get('LOG_GITHUB_GROUPS') or 'auto').strip().lower()
    if setting == 'on':
        return True
    if setting == 'off':
        return False
    return os.environ.get('GITHUB_ACTIONS', '').strip().lower() == 'true'


def _make_console_formatter(style):
    if style == 'plain':
        return _PlainConsoleFormatter()
    if style == 'verbose':
        return _LegacyVerboseFormatter()
    return _CompactConsoleFormatter(github_groups=_resolve_github_groups())


def _make_file_formatter():
    # File logs always keep the verbose 4-field format — its grep-friendly
    # layout is the forensic baseline the rest of the system relies on.
    return _LegacyVerboseFormatter()


def setup_logging(log_file=None, log_level=None, *, log_style=None):
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
        log_style: Console formatter style — ``compact`` (default),
            ``plain``, or ``verbose``.  When ``None`` falls back to
            the ``LOG_STYLE`` env var, then to the ``compact`` default.
            File handlers always use the verbose format regardless of
            this argument.
    """
    global _primary_log_file

    if log_level is None:
        log_level = cfg('LOG_LEVEL', 'INFO')

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Determine whether one of *our* console handlers is already installed
    # on the root logger.  Foreign handlers (e.g. pytest's caplog) don't
    # count: we want to ensure the canonical console formatter wins on
    # the first ``setup_logging`` call regardless of who else is there.
    _our_handler_present = any(
        h.formatter is not None
        and type(h.formatter).__module__ == __name__
        and not isinstance(h, logging.FileHandler)
        for h in root_logger.handlers
    )

    # Level-only call AND we've already configured our handlers — just
    # update levels and return.  This preserves the long-standing
    # behaviour of transitive ``setup_logging`` calls being non-clobbering
    # (e.g. spider config triggers a level update without rebuilding the
    # file handler that the entry-point script set up earlier).
    if log_file is None and _our_handler_present and log_style is None:
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

    console_style = _resolve_console_style(log_style)
    console_formatter = _make_console_formatter(console_style)
    file_formatter = _make_file_formatter()

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)

        file_handler = logging.FileHandler(log_file, mode='w', encoding='utf-8')
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)
        _primary_log_file = os.path.abspath(log_file)

    # Silence noisy third-party loggers. urllib3 in particular emits two DEBUG
    # lines per HTTP request ("Starting new HTTPS connection" + the response
    # status line), which buries the JAVDB-side logs whenever the user runs
    # with --log-level DEBUG (e.g. against the D1 client / reconciler).
    logging.getLogger("httpx").setLevel(logging.INFO)
    logging.getLogger("httpcore").setLevel(logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.INFO)
    logging.getLogger("requests").setLevel(logging.INFO)

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
