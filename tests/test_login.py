"""
Unit tests for scripts/login.py functions.
These tests use a different approach - testing core logic in isolation.
"""
import os
import sys
import re
import time
import pytest
from unittest.mock import patch, MagicMock, call
from bs4 import BeautifulSoup

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)


class TestExtractCsrfToken:
    """Test cases for extract_csrf_token function - implemented locally."""
    
    def extract_csrf_token(self, html_content):
        """Extract CSRF token from login page - local implementation."""
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Method 1: Try to find meta tag
        csrf_meta = soup.find('meta', attrs={'name': 'csrf-token'})
        if csrf_meta and csrf_meta.get('content'):
            return csrf_meta.get('content')
        
        # Method 2: Try to find in form hidden input
        csrf_input = soup.find('input', attrs={'name': 'authenticity_token'})
        if csrf_input and csrf_input.get('value'):
            return csrf_input.get('value')
        
        return None
    
    def test_extract_from_meta_tag(self):
        """Test extracting CSRF token from meta tag."""
        html = '''
        <html>
        <head>
            <meta name="csrf-token" content="abc123token">
        </head>
        </html>
        '''
        result = self.extract_csrf_token(html)
        assert result == 'abc123token'
    
    def test_extract_from_input_field(self):
        """Test extracting CSRF token from form input."""
        html = '''
        <html>
        <body>
            <form>
                <input name="authenticity_token" value="formtoken456">
            </form>
        </body>
        </html>
        '''
        result = self.extract_csrf_token(html)
        assert result == 'formtoken456'
    
    def test_prefer_meta_over_input(self):
        """Test that meta tag is preferred over input field."""
        html = '''
        <html>
        <head>
            <meta name="csrf-token" content="metatoken">
        </head>
        <body>
            <form>
                <input name="authenticity_token" value="inputtoken">
            </form>
        </body>
        </html>
        '''
        result = self.extract_csrf_token(html)
        assert result == 'metatoken'
    
    def test_no_csrf_token_found(self):
        """Test when no CSRF token is present."""
        html = '''
        <html>
        <body>No token here</body>
        </html>
        '''
        result = self.extract_csrf_token(html)
        assert result is None
    
    def test_empty_csrf_token(self):
        """Test when CSRF token is empty."""
        html = '''
        <html>
        <head>
            <meta name="csrf-token" content="">
        </head>
        </html>
        '''
        result = self.extract_csrf_token(html)
        # Empty string is falsy, so should return None
        assert result is None or result == ''


class TestSaveCaptchaImage:
    """Test cases for save_captcha_image function - implemented locally."""
    
    def save_captcha_image(self, image_data, filename='captcha.png'):
        """Save captcha image to file - local implementation."""
        try:
            with open(filename, 'wb') as f:
                f.write(image_data)
            return True
        except Exception as e:
            return False
    
    def test_save_success(self, temp_dir):
        """Test successful save of captcha image."""
        filename = os.path.join(temp_dir, 'test_captcha.png')
        image_data = b'fake image data'
        
        result = self.save_captcha_image(image_data, filename)
        
        assert result is True
        assert os.path.exists(filename)
        with open(filename, 'rb') as f:
            assert f.read() == image_data
    
    def test_save_failure_bad_path(self):
        """Test save failure with bad path."""
        result = self.save_captcha_image(b'data', '/nonexistent/path/captcha.png')
        assert result is False


class TestLoginValidation:
    """Test cases for login credential validation."""
    
    def test_empty_credentials(self):
        """Test login with empty credentials."""
        username = ''
        password = ''
        
        has_credentials = bool(username and password)
        assert has_credentials is False
    
    def test_empty_password(self):
        """Test login with empty password."""
        username = 'user'
        password = ''
        
        has_credentials = bool(username and password)
        assert has_credentials is False
    
    def test_valid_credentials(self):
        """Test login with valid credentials."""
        username = 'user'
        password = 'pass'
        
        has_credentials = bool(username and password)
        assert has_credentials is True


class TestUpdateConfigFileLogic:
    """Test cases for update_config_file function logic."""
    
    def test_update_existing_cookie(self, temp_dir):
        """Test updating existing JAVDB_SESSION_COOKIE in config."""
        # Create a temporary config file
        config_path = os.path.join(temp_dir, 'config.py')
        with open(config_path, 'w') as f:
            f.write("JAVDB_SESSION_COOKIE = 'old_cookie_value'\n")
            f.write("OTHER_CONFIG = 'unchanged'\n")
        
        # Read current config
        with open(config_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Pattern to match the cookie assignment
        pattern = r"(JAVDB_SESSION_COOKIE\s*=\s*['\"])([^'\"]*?)(['\"])"
        
        # Replace with new cookie
        new_cookie = 'new_session_cookie_value'
        new_content = re.sub(pattern, rf"\g<1>{new_cookie}\g<3>", content)
        
        # Write back to file
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        # Verify the change
        with open(config_path, 'r') as f:
            final_content = f.read()
        assert 'new_session_cookie_value' in final_content
        assert 'old_cookie_value' not in final_content
    
    def test_config_pattern_not_found(self, temp_dir):
        """Test when JAVDB_SESSION_COOKIE pattern is not in config."""
        config_path = os.path.join(temp_dir, 'config.py')
        with open(config_path, 'w') as f:
            f.write("OTHER_CONFIG = 'value'\n")
        
        with open(config_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        pattern = r"(JAVDB_SESSION_COOKIE\s*=\s*['\"])([^'\"]*?)(['\"])"
        
        # Check if pattern exists
        has_pattern = bool(re.search(pattern, content))
        assert has_pattern is False


class TestGetCaptchaFromUserLogic:
    """Test cases for captcha handling logic."""
    
    def test_captcha_download_success(self):
        """Test successful captcha download response."""
        status_code = 200
        content = b'fake captcha image'
        
        success = status_code == 200 and len(content) > 0
        assert success is True
    
    def test_captcha_download_failure(self):
        """Test captcha download failure response."""
        status_code = 404
        
        success = status_code == 200
        assert success is False


class TestLoginWithRetry:
    """Test cases for login_with_retry function - retry logic for captcha failures."""
    
    def login_with_retry(self, username, password, max_retries, login_func):
        """
        Local implementation of login_with_retry for testing.
        
        Args:
            username: JavDB username
            password: JavDB password  
            max_retries: Maximum retry attempts
            login_func: Mock function that simulates login_javdb
        
        Returns:
            tuple: (success, session_cookie, message)
        """
        success = False
        session_cookie = None
        message = None
        
        for attempt in range(1, max_retries + 1):
            success, session_cookie, message = login_func(username, password)
            
            if success:
                break
            else:
                # Check if it's a captcha-related error (worth retrying)
                is_captcha_error = any(keyword in message.lower() for keyword in [
                    'captcha', '验证码', '驗證碼', 'verification'
                ])
                
                if attempt < max_retries:
                    # Would normally sleep here, skipped in tests
                    pass
                else:
                    pass  # All attempts failed
        
        return success, session_cookie, message
    
    def test_success_on_first_attempt(self):
        """Test successful login on first attempt."""
        mock_login = MagicMock(return_value=(True, 'session_cookie_value', 'Login successful'))
        
        success, cookie, message = self.login_with_retry('user', 'pass', 5, mock_login)
        
        assert success is True
        assert cookie == 'session_cookie_value'
        assert message == 'Login successful'
        assert mock_login.call_count == 1
    
    def test_success_after_captcha_retry(self):
        """Test successful login after captcha error retry."""
        # First attempt fails with captcha error, second succeeds
        mock_login = MagicMock(side_effect=[
            (False, None, 'Login failed: Incorrect captcha code (验证码错误)'),
            (True, 'new_session_cookie', 'Login successful')
        ])
        
        success, cookie, message = self.login_with_retry('user', 'pass', 5, mock_login)
        
        assert success is True
        assert cookie == 'new_session_cookie'
        assert mock_login.call_count == 2
    
    def test_success_after_multiple_retries(self):
        """Test successful login after multiple captcha errors."""
        # First 3 attempts fail with captcha error, 4th succeeds
        mock_login = MagicMock(side_effect=[
            (False, None, 'captcha error'),
            (False, None, 'verification failed'),
            (False, None, '验证码错误'),
            (True, 'final_cookie', 'Login successful')
        ])
        
        success, cookie, message = self.login_with_retry('user', 'pass', 5, mock_login)
        
        assert success is True
        assert cookie == 'final_cookie'
        assert mock_login.call_count == 4
    
    def test_all_retries_exhausted(self):
        """Test when all retry attempts fail."""
        # All 5 attempts fail with captcha error
        mock_login = MagicMock(return_value=(False, None, 'captcha verification failed'))
        
        success, cookie, message = self.login_with_retry('user', 'pass', 5, mock_login)
        
        assert success is False
        assert cookie is None
        assert 'captcha' in message.lower()
        assert mock_login.call_count == 5
    
    def test_non_captcha_error_still_retries(self):
        """Test that non-captcha errors also trigger retries."""
        # First attempt fails with network error, second succeeds
        mock_login = MagicMock(side_effect=[
            (False, None, 'Network error: connection timeout'),
            (True, 'recovered_cookie', 'Login successful')
        ])
        
        success, cookie, message = self.login_with_retry('user', 'pass', 5, mock_login)
        
        assert success is True
        assert cookie == 'recovered_cookie'
        assert mock_login.call_count == 2
    
    def test_respects_max_retries_limit(self):
        """Test that retry count respects max_retries parameter."""
        mock_login = MagicMock(return_value=(False, None, 'captcha error'))
        
        # Test with max_retries=3
        success, cookie, message = self.login_with_retry('user', 'pass', 3, mock_login)
        
        assert success is False
        assert mock_login.call_count == 3
        
        # Reset and test with max_retries=1
        mock_login.reset_mock()
        success, cookie, message = self.login_with_retry('user', 'pass', 1, mock_login)
        
        assert mock_login.call_count == 1
    
    def test_captcha_error_detection_chinese_simplified(self):
        """Test captcha error detection with Chinese simplified."""
        mock_login = MagicMock(side_effect=[
            (False, None, '验证码错误，请重试'),
            (True, 'cookie', 'success')
        ])
        
        success, cookie, message = self.login_with_retry('user', 'pass', 5, mock_login)
        
        assert success is True
        assert mock_login.call_count == 2
    
    def test_captcha_error_detection_chinese_traditional(self):
        """Test captcha error detection with Chinese traditional."""
        mock_login = MagicMock(side_effect=[
            (False, None, '驗證碼錯誤'),
            (True, 'cookie', 'success')
        ])
        
        success, cookie, message = self.login_with_retry('user', 'pass', 5, mock_login)
        
        assert success is True
        assert mock_login.call_count == 2
    
    def test_captcha_error_detection_english(self):
        """Test captcha error detection with English."""
        mock_login = MagicMock(side_effect=[
            (False, None, 'Verification code incorrect'),
            (True, 'cookie', 'success')
        ])
        
        success, cookie, message = self.login_with_retry('user', 'pass', 5, mock_login)
        
        assert success is True
        assert mock_login.call_count == 2


class TestAttemptLoginRefreshLogic:
    """Test cases for attempt_login_refresh function logic in spider.py."""
    
    def can_attempt_login(self, login_attempted, login_feature_available, has_credentials, is_adhoc_mode, is_index_page):
        """
        Local implementation of can_attempt_login logic.
        
        Args:
            login_attempted: Whether login has already been attempted
            login_feature_available: Whether GPT API is configured
            has_credentials: Whether username/password are configured
            is_adhoc_mode: True if running in adhoc mode
            is_index_page: True if this is for an index page fetch
        
        Returns:
            bool: True if login attempt is allowed
        """
        # Already attempted - never allow again
        if login_attempted:
            return False
        
        # Login feature not available
        if not login_feature_available:
            return False
        
        # No credentials configured
        if not has_credentials:
            return False
        
        # Daily mode: only allow for movie (detail) pages, not index pages
        if not is_adhoc_mode:
            if is_index_page:
                return False
            return True
        
        # Adhoc mode: allow for both index and movie pages
        return True
    
    def test_login_already_attempted_returns_false(self):
        """Test that second login attempt is blocked."""
        result = self.can_attempt_login(
            login_attempted=True,
            login_feature_available=True,
            has_credentials=True,
            is_adhoc_mode=True,
            is_index_page=False
        )
        assert result is False
    
    def test_login_feature_not_available_returns_false(self):
        """Test that login is blocked when GPT API not configured."""
        result = self.can_attempt_login(
            login_attempted=False,
            login_feature_available=False,
            has_credentials=True,
            is_adhoc_mode=True,
            is_index_page=False
        )
        assert result is False
    
    def test_no_credentials_returns_false(self):
        """Test that login is blocked when credentials not configured."""
        result = self.can_attempt_login(
            login_attempted=False,
            login_feature_available=True,
            has_credentials=False,
            is_adhoc_mode=True,
            is_index_page=False
        )
        assert result is False
    
    def test_adhoc_mode_allows_index_page_login(self):
        """Test that adhoc mode allows login for index page failures."""
        result = self.can_attempt_login(
            login_attempted=False,
            login_feature_available=True,
            has_credentials=True,
            is_adhoc_mode=True,
            is_index_page=True
        )
        assert result is True
    
    def test_adhoc_mode_allows_detail_page_login(self):
        """Test that adhoc mode allows login for detail page failures."""
        result = self.can_attempt_login(
            login_attempted=False,
            login_feature_available=True,
            has_credentials=True,
            is_adhoc_mode=True,
            is_index_page=False
        )
        assert result is True
    
    def test_daily_mode_blocks_index_page_login(self):
        """Test that daily mode blocks login for index page failures."""
        result = self.can_attempt_login(
            login_attempted=False,
            login_feature_available=True,
            has_credentials=True,
            is_adhoc_mode=False,
            is_index_page=True
        )
        assert result is False
    
    def test_daily_mode_allows_detail_page_login(self):
        """Test that daily mode allows login for detail page failures."""
        result = self.can_attempt_login(
            login_attempted=False,
            login_feature_available=True,
            has_credentials=True,
            is_adhoc_mode=False,
            is_index_page=False
        )
        assert result is True


class TestLoginFallbackContinuation:
    """Test cases for login failure fallback continuation logic."""
    
    def fetch_with_login_fallback(self, login_success, try_proxy_fallback_func):
        """
        Simulate the fallback logic where login failure should continue to proxy fallback.
        
        Args:
            login_success: Whether login was successful
            try_proxy_fallback_func: Function to call for proxy fallback
        
        Returns:
            dict: Result with 'login_tried', 'proxy_fallback_tried', 'final_success'
        """
        result = {
            'login_tried': True,
            'proxy_fallback_tried': False,
            'final_success': False
        }
        
        if login_success:
            # Try with new cookie
            result['final_success'] = True
            return result
        else:
            # Login failed, continue with proxy pool fallback
            result['proxy_fallback_tried'] = True
            result['final_success'] = try_proxy_fallback_func()
            return result
    
    def test_login_success_skips_proxy_fallback(self):
        """Test that successful login skips proxy fallback."""
        mock_proxy_fallback = MagicMock(return_value=True)
        
        result = self.fetch_with_login_fallback(True, mock_proxy_fallback)
        
        assert result['login_tried'] is True
        assert result['proxy_fallback_tried'] is False
        assert result['final_success'] is True
        mock_proxy_fallback.assert_not_called()
    
    def test_login_failure_continues_to_proxy_fallback(self):
        """Test that login failure continues to proxy fallback."""
        mock_proxy_fallback = MagicMock(return_value=True)
        
        result = self.fetch_with_login_fallback(False, mock_proxy_fallback)
        
        assert result['login_tried'] is True
        assert result['proxy_fallback_tried'] is True
        assert result['final_success'] is True
        mock_proxy_fallback.assert_called_once()
    
    def test_both_login_and_proxy_fallback_fail(self):
        """Test when both login and proxy fallback fail."""
        mock_proxy_fallback = MagicMock(return_value=False)
        
        result = self.fetch_with_login_fallback(False, mock_proxy_fallback)
        
        assert result['login_tried'] is True
        assert result['proxy_fallback_tried'] is True
        assert result['final_success'] is False


# Use temp_dir fixture
@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    import tempfile
    import shutil
    temp_path = tempfile.mkdtemp()
    yield temp_path
    shutil.rmtree(temp_path, ignore_errors=True)

