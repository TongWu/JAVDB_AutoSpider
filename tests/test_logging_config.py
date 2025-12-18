"""
Unit tests for utils/logging_config.py
Tests for logging setup and configuration
"""
import pytest
import os
import sys
import logging
import tempfile

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)


class TestSetupLogging:
    """Tests for setup_logging function"""
    
    def test_setup_logging_basic(self):
        """Test basic logging setup without file"""
        from utils.logging_config import setup_logging, get_logger
        
        # Setup logging
        root_logger = setup_logging(log_level='DEBUG')
        
        assert root_logger is not None
        assert root_logger.level == logging.DEBUG
    
    def test_setup_logging_with_file(self, temp_dir):
        """Test logging setup with log file"""
        from utils.logging_config import setup_logging, get_logger
        
        log_file = os.path.join(temp_dir, 'test.log')
        
        root_logger = setup_logging(log_file=log_file, log_level='INFO')
        
        # Verify file handler was added
        has_file_handler = any(
            isinstance(h, logging.FileHandler) 
            for h in root_logger.handlers
        )
        assert has_file_handler
        
        # Log something and verify file exists
        logger = get_logger('test')
        logger.info('Test message')
        
        assert os.path.exists(log_file)
    
    def test_setup_logging_creates_directory(self, temp_dir):
        """Test that setup_logging creates log directory if needed"""
        from utils.logging_config import setup_logging
        
        log_dir = os.path.join(temp_dir, 'subdir', 'logs')
        log_file = os.path.join(log_dir, 'test.log')
        
        setup_logging(log_file=log_file, log_level='INFO')
        
        assert os.path.exists(log_dir)
    
    def test_setup_logging_level_from_string(self):
        """Test converting string log levels"""
        from utils.logging_config import setup_logging
        
        # Test different level strings
        for level_str, expected_level in [
            ('DEBUG', logging.DEBUG),
            ('INFO', logging.INFO),
            ('WARNING', logging.WARNING),
            ('ERROR', logging.ERROR),
            ('CRITICAL', logging.CRITICAL)
        ]:
            root_logger = setup_logging(log_level=level_str)
            assert root_logger.level == expected_level
    
    def test_setup_logging_case_insensitive(self):
        """Test that log level is case insensitive"""
        from utils.logging_config import setup_logging
        
        root_logger = setup_logging(log_level='debug')
        assert root_logger.level == logging.DEBUG
        
        root_logger = setup_logging(log_level='Debug')
        assert root_logger.level == logging.DEBUG


class TestGetLogger:
    """Tests for get_logger function"""
    
    def test_get_logger_returns_logger(self):
        """Test that get_logger returns a Logger instance"""
        from utils.logging_config import get_logger
        
        logger = get_logger('test_module')
        
        assert isinstance(logger, logging.Logger)
        assert logger.name == 'test_module'
    
    def test_get_logger_with_module_name(self):
        """Test get_logger with __name__ pattern"""
        from utils.logging_config import get_logger
        
        logger = get_logger(__name__)
        
        assert 'test_logging_config' in logger.name
    
    def test_get_logger_same_instance(self):
        """Test that same name returns same logger"""
        from utils.logging_config import get_logger
        
        logger1 = get_logger('same_name')
        logger2 = get_logger('same_name')
        
        assert logger1 is logger2
    
    def test_logger_can_log_messages(self, temp_dir):
        """Test that logger can log messages"""
        from utils.logging_config import setup_logging, get_logger
        
        log_file = os.path.join(temp_dir, 'test_messages.log')
        setup_logging(log_file=log_file, log_level='DEBUG')
        
        logger = get_logger('test_messages')
        
        # Log different levels
        logger.debug('Debug message')
        logger.info('Info message')
        logger.warning('Warning message')
        logger.error('Error message')
        
        # Read log file and verify messages
        with open(log_file, 'r') as f:
            content = f.read()
        
        assert 'Debug message' in content
        assert 'Info message' in content
        assert 'Warning message' in content
        assert 'Error message' in content


class TestLoggingFormat:
    """Tests for logging format"""
    
    def test_log_format_contains_timestamp(self, temp_dir):
        """Test that log format includes timestamp"""
        from utils.logging_config import setup_logging, get_logger
        
        log_file = os.path.join(temp_dir, 'format_test.log')
        setup_logging(log_file=log_file, log_level='INFO')
        
        logger = get_logger('format_test')
        logger.info('Test message')
        
        with open(log_file, 'r') as f:
            content = f.read()
        
        # Check for timestamp pattern (YYYY-MM-DD HH:MM:SS)
        import re
        assert re.search(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', content)
    
    def test_log_format_contains_level(self, temp_dir):
        """Test that log format includes level name"""
        from utils.logging_config import setup_logging, get_logger
        
        log_file = os.path.join(temp_dir, 'level_test.log')
        setup_logging(log_file=log_file, log_level='DEBUG')
        
        logger = get_logger('level_test')
        logger.info('Test info')
        logger.warning('Test warning')
        
        with open(log_file, 'r') as f:
            content = f.read()
        
        assert 'INFO' in content
        assert 'WARNING' in content
    
    def test_log_format_contains_logger_name(self, temp_dir):
        """Test that log format includes logger name"""
        from utils.logging_config import setup_logging, get_logger
        
        log_file = os.path.join(temp_dir, 'name_test.log')
        setup_logging(log_file=log_file, log_level='INFO')
        
        logger = get_logger('my_custom_logger')
        logger.info('Test message')
        
        with open(log_file, 'r') as f:
            content = f.read()
        
        assert 'my_custom_logger' in content
