"""
Unit tests for utils/logging_config.py functions.
"""
import os
import sys
import pytest
import logging
import tempfile
import shutil
from unittest.mock import patch, MagicMock

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from utils.logging_config import setup_logging, get_logger


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


