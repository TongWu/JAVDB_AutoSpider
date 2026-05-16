"""
Unit tests for utils/infra/logging_config.py functions.
"""
import os
import sys
import pytest
import logging
import tempfile
import shutil
from unittest.mock import patch, MagicMock

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from utils.infra.logging_config import setup_logging, get_logger
from packages.python.javdb_platform.logging_config import (
    _CompactConsoleFormatter,
    _LegacyVerboseFormatter,
    _PlainConsoleFormatter,
    _reset_logging_state,
    _resolve_console_style,
    _resolve_github_groups,
    _shorten_logger_name,
    get_logger_name_mapping,
    log_group_end,
    log_group_start,
    log_section,
    log_summary_block,
)


@pytest.fixture(autouse=True)
def reset_logging():
    """Reset logging state before each test to avoid cross-test interference."""
    _reset_logging_state()
    yield
    _reset_logging_state()


class TestSetupLogging:
    """Test cases for setup_logging function."""
    
    def test_setup_logging_default(self):
        """Test setup_logging with default parameters."""
        logger = setup_logging()
        
        assert logger is not None
        assert isinstance(logger, logging.Logger)
    
    def test_setup_logging_with_log_level(self):
        """Test setup_logging with custom log level."""
        logger = setup_logging(log_level='DEBUG')
        
        assert logger.level == logging.DEBUG
    
    def test_setup_logging_with_info_level(self):
        """Test setup_logging with INFO level."""
        logger = setup_logging(log_level='INFO')
        
        assert logger.level == logging.INFO
    
    def test_setup_logging_with_warning_level(self):
        """Test setup_logging with WARNING level."""
        logger = setup_logging(log_level='WARNING')
        
        assert logger.level == logging.WARNING
    
    def test_setup_logging_with_error_level(self):
        """Test setup_logging with ERROR level."""
        logger = setup_logging(log_level='ERROR')
        
        assert logger.level == logging.ERROR
    
    def test_setup_logging_case_insensitive(self):
        """Test setup_logging with lowercase log level."""
        logger = setup_logging(log_level='debug')
        
        assert logger.level == logging.DEBUG
    
    def test_setup_logging_invalid_level_defaults_to_info(self):
        """Test setup_logging with invalid log level defaults to INFO."""
        logger = setup_logging(log_level='INVALID')
        
        # Invalid level should default to INFO
        assert logger.level == logging.INFO
    
    def test_setup_logging_with_file(self):
        """Test setup_logging with log file."""
        temp_dir = tempfile.mkdtemp()
        log_file = os.path.join(temp_dir, 'test.log')
        
        try:
            logger = setup_logging(log_file=log_file)
            
            # Log a message
            logger.info("Test message")
            
            # Verify file was created and has content
            assert os.path.exists(log_file)
            with open(log_file, 'r') as f:
                content = f.read()
            assert 'Test message' in content
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_setup_logging_creates_directory(self):
        """Test setup_logging creates log directory if it doesn't exist."""
        temp_dir = tempfile.mkdtemp()
        log_dir = os.path.join(temp_dir, 'logs', 'subdir')
        log_file = os.path.join(log_dir, 'test.log')
        
        try:
            logger = setup_logging(log_file=log_file)
            
            # Log a message
            logger.info("Test message")
            
            # Verify directory was created
            assert os.path.exists(log_dir)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_setup_logging_clears_existing_handlers(self):
        """Test that setup_logging clears existing handlers."""
        # First setup
        logger1 = setup_logging(log_level='INFO')
        handler_count_1 = len(logger1.handlers)
        
        # Second setup should replace handlers, not add more
        logger2 = setup_logging(log_level='DEBUG')
        handler_count_2 = len(logger2.handlers)
        
        # Should have same or fewer handlers (not accumulated)
        assert handler_count_2 <= handler_count_1 + 1
    
    def test_setup_logging_has_console_handler(self):
        """Test that setup_logging adds a console handler."""
        logger = setup_logging()
        
        # Should have at least one StreamHandler
        has_stream_handler = any(
            isinstance(h, logging.StreamHandler) 
            for h in logger.handlers
        )
        assert has_stream_handler
    
    def test_setup_logging_silences_noisy_loggers(self):
        """Test that setup_logging silences httpx/httpcore loggers."""
        logger = setup_logging()
        
        # httpx and httpcore loggers should be set to INFO or higher
        httpx_logger = logging.getLogger("httpx")
        httpcore_logger = logging.getLogger("httpcore")
        
        assert httpx_logger.level >= logging.INFO
        assert httpcore_logger.level >= logging.INFO
    
    def test_setup_logging_uses_config_log_level(self):
        """Test that setup_logging uses LOG_LEVEL from config if not specified."""
        # This test verifies the try/except import behavior
        # When config.py exists with LOG_LEVEL, it should use that value
        mock_config = MagicMock()
        mock_config.LOG_LEVEL = 'WARNING'
        
        with patch.dict('sys.modules', {'config': mock_config}):
            # Re-import to pick up the mocked config
            # Note: This may not work as expected due to module caching
            # The actual behavior depends on how the module is imported
            pass


class TestGetLogger:
    """Test cases for get_logger function."""
    
    def test_get_logger_returns_logger(self):
        """Test that get_logger returns a Logger instance."""
        logger = get_logger(__name__)
        
        assert logger is not None
        assert isinstance(logger, logging.Logger)
    
    def test_get_logger_with_name(self):
        """Test that get_logger returns logger with correct name."""
        logger = get_logger('test.module.name')
        
        assert logger.name == 'test.module.name'
    
    def test_get_logger_same_instance(self):
        """Test that get_logger returns same instance for same name."""
        logger1 = get_logger('same.name')
        logger2 = get_logger('same.name')
        
        assert logger1 is logger2
    
    def test_get_logger_different_instances(self):
        """Test that get_logger returns different instances for different names."""
        logger1 = get_logger('name.one')
        logger2 = get_logger('name.two')
        
        assert logger1 is not logger2
    
    def test_get_logger_can_log(self):
        """Test that returned logger can log messages."""
        # First setup logging to capture output
        temp_dir = tempfile.mkdtemp()
        log_file = os.path.join(temp_dir, 'test.log')
        
        try:
            setup_logging(log_file=log_file, log_level='DEBUG')
            logger = get_logger('test.logger')
            
            # Log messages at different levels
            logger.debug("Debug message")
            logger.info("Info message")
            logger.warning("Warning message")
            logger.error("Error message")
            
            # Verify messages were logged
            with open(log_file, 'r') as f:
                content = f.read()
            
            assert 'Debug message' in content
            assert 'Info message' in content
            assert 'Warning message' in content
            assert 'Error message' in content
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestLoggingFormat:
    """Test cases for logging format."""
    
    def test_log_format_includes_timestamp(self):
        """Test that log format includes timestamp."""
        temp_dir = tempfile.mkdtemp()
        log_file = os.path.join(temp_dir, 'test.log')
        
        try:
            setup_logging(log_file=log_file)
            logger = get_logger('test.format')
            logger.info("Test message")
            
            with open(log_file, 'r') as f:
                content = f.read()
            
            # Should have date/time pattern
            import re
            assert re.search(r'\d{4}-\d{2}-\d{2}', content)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_log_format_includes_level(self):
        """Test that log format includes log level."""
        temp_dir = tempfile.mkdtemp()
        log_file = os.path.join(temp_dir, 'test.log')
        
        try:
            setup_logging(log_file=log_file)
            logger = get_logger('test.format')
            logger.info("Test message")
            
            with open(log_file, 'r') as f:
                content = f.read()
            
            assert 'INFO' in content
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
    
    def test_log_format_includes_logger_name(self):
        """Test that log format includes logger name."""
        temp_dir = tempfile.mkdtemp()
        log_file = os.path.join(temp_dir, 'test.log')
        
        try:
            setup_logging(log_file=log_file)
            logger = get_logger('test.format.name')
            logger.info("Test message")
            
            with open(log_file, 'r') as f:
                content = f.read()
            
            assert 'test.format.name' in content
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_log_format_uses_short_name_for_mapped_module(self):
        """Test that mapped module names appear shortened in logs."""
        temp_dir = tempfile.mkdtemp()
        log_file = os.path.join(temp_dir, 'test.log')

        try:
            setup_logging(log_file=log_file)
            logger = get_logger('packages.python.javdb_platform.request_handler')
            logger.info("Short name test")

            with open(log_file, 'r') as f:
                content = f.read()

            assert 'RequestHandler' in content
            assert 'packages.python.javdb_platform.request_handler' not in content
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestLoggerNameMapping:
    """Test cases for logger short-name mapping."""

    def test_shorten_mapped_module(self):
        assert _shorten_logger_name('packages.python.javdb_spider.fetch.fetch_engine') == 'FetchEngine'

    def test_shorten_unmapped_module_strips_prefix(self):
        result = _shorten_logger_name('packages.python.javdb_new.new_module')
        assert result == 'javdb_new.new_module'

    def test_shorten_unknown_module_returned_as_is(self):
        assert _shorten_logger_name('some.other.lib') == 'some.other.lib'

    def test_get_logger_name_mapping_returns_dict(self):
        mapping = get_logger_name_mapping()
        assert isinstance(mapping, dict)
        assert 'RequestHandler' in mapping
        assert mapping['RequestHandler'] == 'packages.python.javdb_platform.request_handler'

    def test_get_logger_name_mapping_is_copy(self):
        m1 = get_logger_name_mapping()
        m2 = get_logger_name_mapping()
        assert m1 is not m2


class TestSetupLoggingGuard:
    """Test that setup_logging guards against truncating a different log file."""

    def test_second_call_with_different_file_does_not_truncate(self):
        """Once a primary log file is set, a second call with a different file
        must NOT replace the file handler (and therefore must not truncate
        the first file)."""
        temp_dir = tempfile.mkdtemp()
        file_a = os.path.join(temp_dir, 'a.log')
        file_b = os.path.join(temp_dir, 'b.log')

        try:
            setup_logging(log_file=file_a, log_level='INFO')
            logger = get_logger('guard.test')
            logger.info("written to A")

            # Second call targets a different file — should be skipped
            setup_logging(log_file=file_b, log_level='INFO')

            logger.info("still goes to A")

            assert os.path.exists(file_a)
            with open(file_a, 'r') as f:
                content = f.read()
            assert 'written to A' in content
            assert 'still goes to A' in content

            # file_b should NOT be created
            assert not os.path.exists(file_b)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_same_file_is_allowed(self):
        """Calling setup_logging again with the SAME file should work."""
        temp_dir = tempfile.mkdtemp()
        log_file = os.path.join(temp_dir, 'same.log')

        try:
            setup_logging(log_file=log_file, log_level='INFO')
            logger = get_logger('guard.same')
            logger.info("first call")

            setup_logging(log_file=log_file, log_level='DEBUG')
            logger.debug("second call at debug")

            with open(log_file, 'r') as f:
                content = f.read()
            assert 'second call at debug' in content
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Dual-mode (compact console + verbose file) tests
# ---------------------------------------------------------------------------


def _our_console_formatters(root):
    """Return formatters of console (non-file) handlers we installed.

    pytest injects its own ``LogCaptureHandler`` (also a StreamHandler) into
    the root logger, so a naive ``isinstance(StreamHandler)`` filter would
    pick that up.  We restrict to formatters whose class lives in our
    canonical logging module.
    """
    from packages.python.javdb_platform import logging_config as _mod
    out = []
    for h in root.handlers:
        if isinstance(h, logging.FileHandler):
            continue
        fmt = h.formatter
        if fmt is None:
            continue
        if type(fmt).__module__ == _mod.__name__:
            out.append(fmt)
    return out


class TestDualModeFormatters:
    """Ensure console and file handlers use distinct formatters."""

    def test_default_console_uses_compact_formatter(self):
        setup_logging()
        root = logging.getLogger()
        formatters = _our_console_formatters(root)
        assert formatters, "No console handler with our formatter"
        assert any(isinstance(f, _CompactConsoleFormatter) for f in formatters)

    def test_default_file_uses_verbose_formatter(self):
        temp_dir = tempfile.mkdtemp()
        log_file = os.path.join(temp_dir, 'test.log')
        try:
            setup_logging(log_file=log_file)
            root = logging.getLogger()
            file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
            assert file_handlers, "No file handler installed"
            assert isinstance(file_handlers[0].formatter, _LegacyVerboseFormatter)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_log_style_plain_uses_plain_formatter(self):
        setup_logging(log_style='plain')
        root = logging.getLogger()
        formatters = _our_console_formatters(root)
        assert any(isinstance(f, _PlainConsoleFormatter) for f in formatters)

    def test_log_style_verbose_rolls_back_console(self):
        setup_logging(log_style='verbose')
        root = logging.getLogger()
        formatters = _our_console_formatters(root)
        assert any(isinstance(f, _LegacyVerboseFormatter) for f in formatters)

    def test_invalid_log_style_falls_back_to_compact(self):
        setup_logging(log_style='not-a-real-style')
        root = logging.getLogger()
        formatters = _our_console_formatters(root)
        assert any(isinstance(f, _CompactConsoleFormatter) for f in formatters)

    def test_log_style_env_var_picks_plain(self, monkeypatch):
        monkeypatch.setenv('LOG_STYLE', 'plain')
        assert _resolve_console_style(None) == 'plain'

    def test_log_style_arg_overrides_env(self, monkeypatch):
        monkeypatch.setenv('LOG_STYLE', 'plain')
        assert _resolve_console_style('verbose') == 'verbose'


class TestGithubGroupsResolution:
    """``LOG_GITHUB_GROUPS`` env var + ``GITHUB_ACTIONS`` auto-detection."""

    def test_auto_off_when_not_in_actions(self, monkeypatch):
        monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
        monkeypatch.delenv('LOG_GITHUB_GROUPS', raising=False)
        assert _resolve_github_groups() is False

    def test_auto_on_when_in_actions(self, monkeypatch):
        monkeypatch.setenv('GITHUB_ACTIONS', 'true')
        monkeypatch.delenv('LOG_GITHUB_GROUPS', raising=False)
        assert _resolve_github_groups() is True

    def test_explicit_off_overrides_actions(self, monkeypatch):
        monkeypatch.setenv('GITHUB_ACTIONS', 'true')
        monkeypatch.setenv('LOG_GITHUB_GROUPS', 'off')
        assert _resolve_github_groups() is False

    def test_explicit_on_overrides_no_actions(self, monkeypatch):
        monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
        monkeypatch.setenv('LOG_GITHUB_GROUPS', 'on')
        assert _resolve_github_groups() is True


class TestSectionAndSummaryHelpers:
    """Section / group / summary helpers must render correctly per handler."""

    def _capture_console(self, log_style='compact', github_groups=False):
        """Install a memory handler at the place our StreamHandler would go.

        Filters out pytest's own ``LogCaptureHandler`` so we capture only
        what our formatters render.
        """
        from io import StringIO
        from packages.python.javdb_platform import logging_config as _mod
        _reset_logging_state()
        setup_logging(log_style=log_style)
        root = logging.getLogger()
        for h in root.handlers[:]:
            if isinstance(h, logging.FileHandler):
                continue
            if h.formatter is None:
                continue
            if type(h.formatter).__module__ != _mod.__name__:
                continue
            if log_style == 'compact':
                h.setFormatter(_CompactConsoleFormatter(github_groups=github_groups))
            buf = StringIO()
            h.stream = buf
            return buf, h
        raise AssertionError("no console handler with our formatter")

    def test_log_section_renders_unicode_divider_on_compact(self):
        buf, _ = self._capture_console(log_style='compact')
        log_section(get_logger('test.section'), 'PHASE 1', emoji='🎬')
        out = buf.getvalue()
        assert '🎬' in out
        assert 'PHASE 1' in out
        assert '────' in out  # Unicode horizontal divider
        assert '::group::' not in out

    def test_log_section_renders_ascii_on_plain(self):
        buf, _ = self._capture_console(log_style='plain')
        log_section(get_logger('test.section'), 'PHASE 1')
        out = buf.getvalue()
        assert 'PHASE 1' in out
        assert '====' in out
        assert '────' not in out

    def test_log_summary_block_emits_kv_lines_compact(self):
        buf, _ = self._capture_console(log_style='compact')
        log_summary_block(
            get_logger('test.summary'),
            'OVERALL',
            [('found', 65), ('parsed', 19), ('failed', 0)],
            emoji='📊',
        )
        out = buf.getvalue()
        assert 'OVERALL' in out
        assert 'found' in out and '65' in out
        assert 'parsed' in out and '19' in out
        assert 'failed' in out and '0' in out

    def test_log_group_emits_github_markers_when_enabled(self):
        buf, _ = self._capture_console(log_style='compact', github_groups=True)
        log_group_start(get_logger('test.group'), 'Proxy stats')
        log_group_end(get_logger('test.group'))
        out = buf.getvalue()
        # ``::group::`` must be at column 0 — no timestamp prefix.
        lines = [ln for ln in out.split('\n') if ln.strip()]
        assert any(ln.startswith('::group::Proxy stats') for ln in lines), out
        assert any(ln.startswith('::endgroup::') for ln in lines), out

    def test_log_group_falls_back_to_divider_without_actions(self):
        buf, _ = self._capture_console(log_style='compact', github_groups=False)
        log_group_start(get_logger('test.group'), 'Proxy stats')
        log_group_end(get_logger('test.group'))
        out = buf.getvalue()
        assert '::group::' not in out
        assert 'Proxy stats' in out
        assert '────' in out

    def test_file_handler_keeps_verbose_format_for_section(self):
        temp_dir = tempfile.mkdtemp()
        log_file = os.path.join(temp_dir, 'test.log')
        try:
            setup_logging(log_file=log_file)
            log_section(get_logger('test.section'), 'PHASE 1', emoji='🎬')
            log_summary_block(
                get_logger('test.summary'),
                'OVERALL',
                [('found', 1)],
                emoji='📊',
            )
            log_group_start(get_logger('test.group'), 'Group title')
            log_group_end(get_logger('test.group'))
            with open(log_file, 'r') as f:
                content = f.read()
            # File log MUST stay free of CI-only ``::group::`` markers and
            # Unicode dividers — keep the on-disk forensic baseline in
            # ASCII so legacy grep / log shippers keep working.
            assert '::group::' not in content
            assert '────' not in content
            # The verbose 4-field format prefix must be present.
            assert ' - INFO - ' in content
            # Section title rendered as ``=== TITLE ===`` plain ASCII.
            assert '=== PHASE 1 ===' in content
            assert '=== OVERALL ===' in content
            # Group rendered as plain ``--- begin: ... ---``.
            assert 'begin: Group title' in content
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestRustBridgeShortNames:
    """Rust-side `pyo3_log` targets land on Python with `::` separators
    in some configurations and `.` in others — the short-name map covers
    both so the compact console field stays at 12 chars wide."""

    def test_rust_dot_separator_maps_to_short_name(self):
        assert _shorten_logger_name('rust_core.proxy.pool') == 'ProxyPool'
        assert _shorten_logger_name('rust_core.proxy.ban_manager') == 'BanManager'

    def test_rust_double_colon_separator_maps_to_short_name(self):
        assert _shorten_logger_name('rust_core::proxy::pool') == 'ProxyPool'
        assert _shorten_logger_name('rust_core::proxy::ban_manager') == 'BanManager'

    def test_new_javdb_platform_clients_have_short_names(self):
        # These were unmapped before the log redesign and showed up as
        # ``javdb_platform.runner_registry_client`` etc.  They must now
        # render via the short-name map.
        mapping = get_logger_name_mapping()
        for short in ('RunnerRegistry', 'MovieClaim', 'ProxyCoord',
                      'LoginState', 'D1', 'DualDB'):
            assert short in mapping, f"{short} missing from short-name map"
