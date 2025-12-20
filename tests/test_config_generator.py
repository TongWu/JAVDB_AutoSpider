"""
Unit tests for the config_generator module.
"""

import os
import sys
import pytest
import tempfile
from unittest.mock import patch

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from utils.config_generator import (
    get_env,
    get_env_int,
    get_env_float,
    get_env_bool,
    get_env_json,
    format_python_value,
    get_config_map,
    generate_config_content,
    mask_sensitive_values,
    write_config,
    EMPTY_PLACEHOLDERS
)


class TestGetEnv:
    """Tests for get_env function."""
    
    def test_returns_value_from_var_prefix(self):
        """Should return value from VAR_ prefixed environment variable."""
        with patch.dict(os.environ, {'VAR_TEST_VAR': 'test_value'}):
            assert get_env('TEST_VAR', 'default') == 'test_value'
    
    def test_returns_value_from_direct_env(self):
        """Should return value from direct environment variable when VAR_ not set."""
        env = {'TEST_VAR': 'direct_value'}
        # Ensure VAR_TEST_VAR is not set
        with patch.dict(os.environ, env, clear=True):
            assert get_env('TEST_VAR', 'default') == 'direct_value'
    
    def test_returns_default_when_not_set(self):
        """Should return default when environment variable is not set."""
        env = {}
        with patch.dict(os.environ, env, clear=True):
            assert get_env('NONEXISTENT_VAR', 'default') == 'default'
    
    def test_returns_empty_for_empty_placeholder(self):
        """Should return empty string for __EMPTY__ placeholder."""
        with patch.dict(os.environ, {'VAR_TEST_VAR': '__EMPTY__'}):
            assert get_env('TEST_VAR', 'default') == ''
    
    def test_returns_empty_for_null_placeholder(self):
        """Should return empty string for __NULL__ placeholder."""
        with patch.dict(os.environ, {'VAR_TEST_VAR': '__NULL__'}):
            assert get_env('TEST_VAR', 'default') == ''
    
    def test_returns_empty_for_none_placeholder(self):
        """Should return empty string for 'none' placeholder."""
        with patch.dict(os.environ, {'VAR_TEST_VAR': 'none'}):
            assert get_env('TEST_VAR', 'default') == ''


class TestGetEnvInt:
    """Tests for get_env_int function."""
    
    def test_returns_integer_value(self):
        """Should return integer value from environment variable."""
        with patch.dict(os.environ, {'VAR_INT_VAR': '42'}):
            assert get_env_int('INT_VAR', 0) == 42
    
    def test_returns_default_for_invalid_value(self):
        """Should return default for non-integer value."""
        with patch.dict(os.environ, {'VAR_INT_VAR': 'not_a_number'}):
            assert get_env_int('INT_VAR', 100) == 100
    
    def test_returns_default_for_empty_value(self):
        """Should return default for empty value."""
        with patch.dict(os.environ, {'VAR_INT_VAR': ''}):
            assert get_env_int('INT_VAR', 50) == 50
    
    def test_returns_default_for_empty_placeholder(self):
        """Should return default for __EMPTY__ placeholder."""
        with patch.dict(os.environ, {'VAR_INT_VAR': '__EMPTY__'}):
            assert get_env_int('INT_VAR', 25) == 25


class TestGetEnvFloat:
    """Tests for get_env_float function."""
    
    def test_returns_float_value(self):
        """Should return float value from environment variable."""
        with patch.dict(os.environ, {'VAR_FLOAT_VAR': '3.14'}):
            assert get_env_float('FLOAT_VAR', 0.0) == 3.14
    
    def test_returns_default_for_invalid_value(self):
        """Should return default for non-float value."""
        with patch.dict(os.environ, {'VAR_FLOAT_VAR': 'not_a_float'}):
            assert get_env_float('FLOAT_VAR', 1.5) == 1.5
    
    def test_returns_integer_as_float(self):
        """Should return integer as float."""
        with patch.dict(os.environ, {'VAR_FLOAT_VAR': '10'}):
            assert get_env_float('FLOAT_VAR', 0.0) == 10.0


class TestGetEnvBool:
    """Tests for get_env_bool function."""
    
    def test_returns_true_for_true_string(self):
        """Should return True for 'true' string."""
        with patch.dict(os.environ, {'VAR_BOOL_VAR': 'true'}):
            assert get_env_bool('BOOL_VAR', False) is True
    
    def test_returns_true_for_yes_string(self):
        """Should return True for 'yes' string."""
        with patch.dict(os.environ, {'VAR_BOOL_VAR': 'yes'}):
            assert get_env_bool('BOOL_VAR', False) is True
    
    def test_returns_true_for_one_string(self):
        """Should return True for '1' string."""
        with patch.dict(os.environ, {'VAR_BOOL_VAR': '1'}):
            assert get_env_bool('BOOL_VAR', False) is True
    
    def test_returns_false_for_false_string(self):
        """Should return False for 'false' string."""
        with patch.dict(os.environ, {'VAR_BOOL_VAR': 'false'}):
            assert get_env_bool('BOOL_VAR', True) is False
    
    def test_returns_false_for_no_string(self):
        """Should return False for 'no' string."""
        with patch.dict(os.environ, {'VAR_BOOL_VAR': 'no'}):
            assert get_env_bool('BOOL_VAR', True) is False
    
    def test_returns_default_for_empty_placeholder(self):
        """Should return default for __EMPTY__ placeholder."""
        with patch.dict(os.environ, {'VAR_BOOL_VAR': '__empty__'}):
            assert get_env_bool('BOOL_VAR', True) is True
    
    def test_case_insensitive(self):
        """Should be case insensitive."""
        with patch.dict(os.environ, {'VAR_BOOL_VAR': 'TRUE'}):
            assert get_env_bool('BOOL_VAR', False) is True


class TestGetEnvJson:
    """Tests for get_env_json function."""
    
    def test_returns_parsed_json_list(self):
        """Should return parsed JSON list."""
        with patch.dict(os.environ, {'VAR_JSON_VAR': '["a", "b", "c"]'}):
            assert get_env_json('JSON_VAR', []) == ['a', 'b', 'c']
    
    def test_returns_parsed_json_dict(self):
        """Should return parsed JSON dict."""
        with patch.dict(os.environ, {'VAR_JSON_VAR': '{"key": "value"}'}):
            assert get_env_json('JSON_VAR', {}) == {'key': 'value'}
    
    def test_returns_default_for_invalid_json(self):
        """Should return default for invalid JSON."""
        with patch.dict(os.environ, {'VAR_JSON_VAR': 'not valid json'}):
            assert get_env_json('JSON_VAR', ['default']) == ['default']
    
    def test_returns_default_for_empty_value(self):
        """Should return default for empty value."""
        with patch.dict(os.environ, {'VAR_JSON_VAR': ''}):
            assert get_env_json('JSON_VAR', ['default']) == ['default']


class TestFormatPythonValue:
    """Tests for format_python_value function."""
    
    def test_formats_string_with_repr(self):
        """Should format string with repr (single quotes)."""
        assert format_python_value('test') == "'test'"
    
    def test_formats_boolean_true(self):
        """Should format True as 'True'."""
        assert format_python_value(True) == 'True'
    
    def test_formats_boolean_false(self):
        """Should format False as 'False'."""
        assert format_python_value(False) == 'False'
    
    def test_formats_list_as_json(self):
        """Should format list as JSON."""
        assert format_python_value(['a', 'b']) == '["a", "b"]'
    
    def test_formats_dict_as_json(self):
        """Should format dict as JSON."""
        result = format_python_value({'key': 'value'})
        assert result == '{"key": "value"}'
    
    def test_formats_none(self):
        """Should format None as 'None'."""
        assert format_python_value(None) == 'None'
    
    def test_formats_integer(self):
        """Should format integer as string."""
        assert format_python_value(42) == '42'
    
    def test_formats_float(self):
        """Should format float as string."""
        assert format_python_value(3.14) == '3.14'


class TestGetConfigMap:
    """Tests for get_config_map function."""
    
    def test_returns_list_of_tuples(self):
        """Should return a list of 5-element tuples."""
        config_map = get_config_map()
        assert isinstance(config_map, list)
        assert all(len(item) == 5 for item in config_map)
    
    def test_github_actions_mode_has_empty_git_password(self):
        """Should have empty GIT_PASSWORD in GitHub Actions mode."""
        config_map = get_config_map(github_actions_mode=True)
        git_password_entry = next(
            (item for item in config_map if item[0] == 'GIT_PASSWORD'),
            None
        )
        assert git_password_entry is not None
        # In GitHub Actions mode, env_name should be None (hardcoded)
        assert git_password_entry[1] is None
    
    def test_local_mode_has_git_password_from_env(self):
        """Should read GIT_PASSWORD from env in local mode."""
        config_map = get_config_map(github_actions_mode=False)
        git_password_entry = next(
            (item for item in config_map if item[0] == 'GIT_PASSWORD'),
            None
        )
        assert git_password_entry is not None
        # In local mode, env_name should be 'GIT_PASSWORD'
        assert git_password_entry[1] == 'GIT_PASSWORD'
    
    def test_contains_expected_sections(self):
        """Should contain expected configuration sections."""
        config_map = get_config_map()
        sections = set(item[4] for item in config_map)
        expected_sections = {
            'GIT CONFIGURATION',
            'QBITTORRENT CONFIGURATION',
            'SMTP CONFIGURATION',
            'PROXY CONFIGURATION',
            'SPIDER CONFIGURATION',
        }
        assert expected_sections.issubset(sections)


class TestGenerateConfigContent:
    """Tests for generate_config_content function."""
    
    def test_generates_valid_python_syntax(self):
        """Should generate valid Python syntax."""
        env = {}
        with patch.dict(os.environ, env, clear=True):
            content = generate_config_content()
            # Should be able to compile without syntax errors
            compile(content, '<string>', 'exec')
    
    def test_includes_header_comment(self):
        """Should include header comment."""
        env = {}
        with patch.dict(os.environ, env, clear=True):
            content = generate_config_content()
            assert '# JavDB Auto Spider' in content
    
    def test_includes_section_headers(self):
        """Should include section headers."""
        env = {}
        with patch.dict(os.environ, env, clear=True):
            content = generate_config_content()
            assert '# GIT CONFIGURATION' in content
            assert '# QBITTORRENT CONFIGURATION' in content
    
    def test_github_actions_mode_note(self):
        """Should include GitHub Actions note in that mode."""
        env = {}
        with patch.dict(os.environ, env, clear=True):
            content = generate_config_content(github_actions_mode=True)
            assert 'GIT_PASSWORD is empty' in content


class TestMaskSensitiveValues:
    """Tests for mask_sensitive_values function."""
    
    def test_masks_password_values(self):
        """Should mask password values."""
        content = "QB_PASSWORD = 'my_secret_password'"
        masked = mask_sensitive_values(content)
        assert 'my_secret_password' not in masked
        assert '***MASKED***' in masked
    
    def test_masks_github_tokens(self):
        """Should mask GitHub personal access tokens."""
        content = "TOKEN = 'ghp_1234567890abcdefghijklmnopqrstuvwxyz12345'"
        masked = mask_sensitive_values(content)
        assert 'ghp_1234567890' not in masked
        assert 'ghp_***MASKED***' in masked
    
    def test_masks_cookies(self):
        """Should mask cookie values."""
        content = "JAVDB_SESSION_COOKIE = 'session_id=abc123xyz'"
        masked = mask_sensitive_values(content)
        assert 'session_id=abc123xyz' not in masked
        assert '***MASKED***' in masked
    
    def test_masks_proxy_pool(self):
        """Should mask proxy pool values."""
        content = "PROXY_POOL = [{'host': '192.168.1.1', 'password': 'secret'}]"
        masked = mask_sensitive_values(content)
        assert '192.168.1.1' not in masked
        assert '***MASKED***' in masked
    
    def test_preserves_non_sensitive_values(self):
        """Should preserve non-sensitive values."""
        content = "LOG_LEVEL = 'INFO'\nSTART_PAGE = 1"
        masked = mask_sensitive_values(content)
        assert "LOG_LEVEL = 'INFO'" in masked
        assert "START_PAGE = 1" in masked


class TestWriteConfig:
    """Tests for write_config function."""
    
    def test_writes_config_file(self):
        """Should write config file to specified path."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            temp_path = f.name
        
        try:
            env = {}
            with patch.dict(os.environ, env, clear=True):
                result = write_config(output_path=temp_path, show_masked=False)
                assert result is True
                
                # Verify file was created and has content
                with open(temp_path, 'r') as f:
                    content = f.read()
                assert len(content) > 0
                assert '# JavDB Auto Spider' in content
        finally:
            os.unlink(temp_path)
    
    def test_dry_run_does_not_write_file(self):
        """Should not write file in dry run mode."""
        # Use a path that doesn't exist
        temp_path = '/tmp/test_config_dry_run_should_not_exist.py'
        
        try:
            env = {}
            with patch.dict(os.environ, env, clear=True):
                result = write_config(output_path=temp_path, dry_run=True, show_masked=False)
                assert result is True
                
                # File should not exist
                assert not os.path.exists(temp_path)
        finally:
            # Cleanup just in case
            if os.path.exists(temp_path):
                os.unlink(temp_path)
    
    def test_github_actions_mode_empty_git_password(self):
        """Should have empty GIT_PASSWORD in GitHub Actions mode."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
            temp_path = f.name
        
        try:
            env = {}
            with patch.dict(os.environ, env, clear=True):
                write_config(output_path=temp_path, github_actions_mode=True, show_masked=False)
                
                with open(temp_path, 'r') as f:
                    content = f.read()
                
                # GIT_PASSWORD should be empty string
                assert "GIT_PASSWORD = ''" in content
        finally:
            os.unlink(temp_path)


class TestEmptyPlaceholders:
    """Tests for EMPTY_PLACEHOLDERS constant."""
    
    def test_contains_common_empty_values(self):
        """Should contain common empty placeholder values."""
        assert '__EMPTY__' in EMPTY_PLACEHOLDERS
        assert '__NULL__' in EMPTY_PLACEHOLDERS
        assert 'null' in EMPTY_PLACEHOLDERS
        assert 'none' in EMPTY_PLACEHOLDERS
        assert 'NULL' in EMPTY_PLACEHOLDERS
        assert 'NONE' in EMPTY_PLACEHOLDERS

