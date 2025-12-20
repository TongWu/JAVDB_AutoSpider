"""
Unit tests for the git_helper module.
"""

import os
import sys
import pytest
from unittest.mock import patch, MagicMock

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from utils.git_helper import (
    is_github_actions,
    has_git_credentials,
    get_current_branch,
    mask_sensitive_info,
    flush_log_handlers
)


class TestIsGitHubActions:
    """Tests for is_github_actions function."""
    
    def test_returns_true_when_github_actions_env_is_true(self):
        """Should return True when GITHUB_ACTIONS env var is 'true'."""
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'}):
            assert is_github_actions() is True
    
    def test_returns_false_when_github_actions_env_is_false(self):
        """Should return False when GITHUB_ACTIONS env var is 'false'."""
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'false'}):
            assert is_github_actions() is False
    
    def test_returns_false_when_github_actions_env_is_missing(self):
        """Should return False when GITHUB_ACTIONS env var is not set."""
        env = os.environ.copy()
        env.pop('GITHUB_ACTIONS', None)
        with patch.dict(os.environ, env, clear=True):
            assert is_github_actions() is False
    
    def test_returns_false_when_github_actions_env_is_empty(self):
        """Should return False when GITHUB_ACTIONS env var is empty."""
        with patch.dict(os.environ, {'GITHUB_ACTIONS': ''}):
            assert is_github_actions() is False


class TestHasGitCredentials:
    """Tests for has_git_credentials function."""
    
    def test_returns_true_when_both_username_and_password_provided(self):
        """Should return True when both username and password are provided."""
        # Ensure we're not in GitHub Actions for this test
        env = os.environ.copy()
        env.pop('GITHUB_ACTIONS', None)
        with patch.dict(os.environ, env, clear=True):
            assert has_git_credentials('user', 'password') is True
    
    def test_returns_false_when_username_is_empty(self):
        """Should return False when username is empty."""
        env = os.environ.copy()
        env.pop('GITHUB_ACTIONS', None)
        with patch.dict(os.environ, env, clear=True):
            assert has_git_credentials('', 'password') is False
    
    def test_returns_false_when_password_is_empty(self):
        """Should return False when password is empty."""
        env = os.environ.copy()
        env.pop('GITHUB_ACTIONS', None)
        with patch.dict(os.environ, env, clear=True):
            assert has_git_credentials('user', '') is False
    
    def test_returns_false_when_both_are_empty(self):
        """Should return False when both username and password are empty."""
        env = os.environ.copy()
        env.pop('GITHUB_ACTIONS', None)
        with patch.dict(os.environ, env, clear=True):
            assert has_git_credentials('', '') is False
    
    def test_returns_false_when_username_is_none(self):
        """Should return False when username is None."""
        env = os.environ.copy()
        env.pop('GITHUB_ACTIONS', None)
        with patch.dict(os.environ, env, clear=True):
            assert has_git_credentials(None, 'password') is False
    
    def test_returns_false_when_password_is_none(self):
        """Should return False when password is None."""
        env = os.environ.copy()
        env.pop('GITHUB_ACTIONS', None)
        with patch.dict(os.environ, env, clear=True):
            assert has_git_credentials('user', None) is False
    
    def test_returns_false_in_github_actions_without_credentials(self):
        """Should return False in GitHub Actions when credentials are empty.
        
        This ensures scripts don't attempt commits when run from GitHub Actions
        workflow without credentials - the workflow handles commits itself.
        """
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'}):
            assert has_git_credentials('', '') is False
            assert has_git_credentials(None, None) is False
    
    def test_returns_true_in_github_actions_with_credentials(self):
        """Should return True in GitHub Actions when credentials are provided."""
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'}):
            assert has_git_credentials('user', 'password') is True


class TestGetCurrentBranch:
    """Tests for get_current_branch function."""
    
    def test_returns_branch_name_when_git_command_succeeds(self):
        """Should return branch name when git command succeeds."""
        mock_result = MagicMock()
        mock_result.stdout = 'main\n'
        mock_result.returncode = 0
        
        with patch('subprocess.run', return_value=mock_result):
            assert get_current_branch() == 'main'
    
    def test_returns_main_when_git_command_fails(self):
        """Should return 'main' when git command fails."""
        import subprocess
        with patch('subprocess.run', side_effect=subprocess.CalledProcessError(1, 'git')):
            assert get_current_branch() == 'main'
    
    def test_returns_main_when_branch_is_empty(self):
        """Should return 'main' when branch output is empty."""
        mock_result = MagicMock()
        mock_result.stdout = ''
        mock_result.returncode = 0
        
        with patch('subprocess.run', return_value=mock_result):
            assert get_current_branch() == 'main'
    
    def test_strips_whitespace_from_branch_name(self):
        """Should strip whitespace from branch name."""
        mock_result = MagicMock()
        mock_result.stdout = '  feature-branch  \n'
        mock_result.returncode = 0
        
        with patch('subprocess.run', return_value=mock_result):
            assert get_current_branch() == 'feature-branch'


class TestMaskSensitiveInfo:
    """Tests for mask_sensitive_info function."""
    
    def test_masks_github_personal_access_token(self):
        """Should mask GitHub personal access tokens (ghp_)."""
        text = 'Token: ghp_1234567890abcdefghijklmnopqrstuvwxyz12345'
        masked = mask_sensitive_info(text)
        assert 'ghp_***MASKED***' in masked
        assert '1234567890abcdefghij' not in masked
    
    def test_masks_github_oauth_token(self):
        """Should mask GitHub OAuth tokens (gho_)."""
        text = 'Token: gho_1234567890abcdefghijklmnopqrstuvwxyz12345'
        masked = mask_sensitive_info(text)
        assert 'gh*_***MASKED***' in masked
    
    def test_masks_password_in_url(self):
        """Should mask password in URL format."""
        text = 'URL: https://user:secretpassword@smtp.gmail.com/path'
        masked = mask_sensitive_info(text)
        assert 'secretpassword' not in masked
        assert '***MASKED***' in masked
    
    def test_masks_qbittorrent_password(self):
        """Should mask qBittorrent password."""
        text = 'password: mysecretpassword'
        masked = mask_sensitive_info(text)
        assert 'mysecretpassword' not in masked
        assert 'password:***MASKED***' in masked
    
    def test_masks_smtp_password(self):
        """Should mask SMTP password."""
        text = 'SMTP_PASSWORD: mysmtppassword'
        masked = mask_sensitive_info(text)
        assert 'mysmtppassword' not in masked
        assert 'SMTP_PASSWORD:***MASKED***' in masked
    
    def test_returns_none_for_none_input(self):
        """Should return None for None input."""
        assert mask_sensitive_info(None) is None
    
    def test_returns_empty_string_for_empty_input(self):
        """Should return empty string for empty input."""
        assert mask_sensitive_info('') == ''
    
    def test_preserves_non_sensitive_text(self):
        """Should preserve non-sensitive text."""
        text = 'Normal log message without sensitive info'
        assert mask_sensitive_info(text) == text


class TestFlushLogHandlers:
    """Tests for flush_log_handlers function."""
    
    def test_flushes_root_logger_handlers(self):
        """Should flush all handlers on root logger."""
        import logging
        
        # Create a mock handler
        mock_handler = MagicMock()
        
        # Get root logger and add mock handler
        root_logger = logging.getLogger()
        original_handlers = root_logger.handlers.copy()
        root_logger.addHandler(mock_handler)
        
        try:
            flush_log_handlers()
            mock_handler.flush.assert_called()
        finally:
            # Cleanup
            root_logger.handlers = original_handlers
    
    def test_flushes_named_logger_handlers(self):
        """Should flush handlers on named loggers."""
        import logging
        
        # Create a named logger with mock handler
        test_logger = logging.getLogger('test_flush_logger')
        mock_handler = MagicMock()
        test_logger.addHandler(mock_handler)
        
        try:
            flush_log_handlers()
            mock_handler.flush.assert_called()
        finally:
            # Cleanup
            test_logger.removeHandler(mock_handler)


class TestGitCommitAndPush:
    """Tests for git_commit_and_push function (integration tests with mocks)."""
    
    def test_skips_commit_when_no_changes(self):
        """Should skip commit when there are no changes."""
        from utils.git_helper import git_commit_and_push
        
        # Mock subprocess.run to simulate no changes
        mock_result = MagicMock()
        mock_result.stdout = ''  # No changes
        mock_result.returncode = 0
        
        with patch('subprocess.run', return_value=mock_result):
            result = git_commit_and_push(
                files_to_add=['test.txt'],
                commit_message='Test commit',
                from_pipeline=True,
                git_username='user',
                git_password='pass',
                git_repo_url='https://github.com/user/repo.git'
            )
            assert result is True
    
    def test_skips_push_in_github_actions_when_not_from_pipeline(self):
        """Should skip push in GitHub Actions when not from pipeline."""
        from utils.git_helper import git_commit_and_push
        
        mock_results = [
            MagicMock(stdout='main\n', returncode=0),  # get_current_branch
            MagicMock(returncode=0),  # git config user.name
            MagicMock(returncode=0),  # git config user.email
            MagicMock(returncode=0),  # git add
            MagicMock(stdout='M test.txt\n', returncode=0),  # git status
            MagicMock(returncode=0),  # git commit
        ]
        
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'}):
            with patch('subprocess.run', side_effect=mock_results):
                result = git_commit_and_push(
                    files_to_add=['test.txt'],
                    commit_message='Test commit',
                    from_pipeline=False,
                    git_username='user',
                    git_password='pass',
                    git_repo_url='https://github.com/user/repo.git'
                )
                # Should return True (commit succeeded, push skipped)
                assert result is True
    
    def test_returns_false_when_pipeline_mode_without_credentials(self):
        """Should return False when in pipeline mode without credentials."""
        from utils.git_helper import git_commit_and_push
        
        # Clear GitHub Actions env
        env = os.environ.copy()
        env.pop('GITHUB_ACTIONS', None)
        
        with patch.dict(os.environ, env, clear=True):
            result = git_commit_and_push(
                files_to_add=['test.txt'],
                commit_message='Test commit',
                from_pipeline=True,
                git_username='user',
                git_password='',  # Empty password
                git_repo_url='https://github.com/user/repo.git'
            )
            assert result is False

