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


class TestAdhocLoginFailedOnIndexPage:
    """Test that adhoc mode aborts the spider when login fails on index page."""

    def _simulate_index_login_fallback(self, is_adhoc_mode, login_success,
                                       still_login_page_after_refresh=False):
        """Simulate the login fallback logic in fetch_index_page_with_fallback.

        Mirrors the actual raise/continue behaviour in fallback.py.

        Raises:
            AdhocLoginFailedError when adhoc + index page login fails.
        Returns:
            str: 'proxy_fallback' if control continues to the proxy pool phase.
        """
        from scripts.spider.fallback import AdhocLoginFailedError

        if login_success:
            if still_login_page_after_refresh:
                if is_adhoc_mode:
                    raise AdhocLoginFailedError(
                        "Login succeeded but index page still requires authentication")
                return 'proxy_fallback'
            return 'success'
        else:
            if is_adhoc_mode:
                raise AdhocLoginFailedError(
                    "Login refresh failed on index page")
            return 'proxy_fallback'

    def test_adhoc_login_failure_raises(self):
        """Adhoc mode: login fails on index page → AdhocLoginFailedError."""
        from scripts.spider.fallback import AdhocLoginFailedError

        with pytest.raises(AdhocLoginFailedError, match="Login refresh failed"):
            self._simulate_index_login_fallback(
                is_adhoc_mode=True, login_success=False)

    def test_adhoc_login_success_but_still_login_page_raises(self):
        """Adhoc mode: login succeeds but page is still a login page → AdhocLoginFailedError."""
        from scripts.spider.fallback import AdhocLoginFailedError

        with pytest.raises(AdhocLoginFailedError, match="still requires authentication"):
            self._simulate_index_login_fallback(
                is_adhoc_mode=True, login_success=True,
                still_login_page_after_refresh=True)

    def test_adhoc_login_success_returns_success(self):
        """Adhoc mode: login succeeds and page loads correctly → no error."""
        result = self._simulate_index_login_fallback(
            is_adhoc_mode=True, login_success=True,
            still_login_page_after_refresh=False)
        assert result == 'success'

    def test_daily_mode_login_failure_continues_fallback(self):
        """Daily mode: login failure continues to proxy fallback (no raise)."""
        result = self._simulate_index_login_fallback(
            is_adhoc_mode=False, login_success=False)
        assert result == 'proxy_fallback'

    def test_daily_mode_still_login_page_continues_fallback(self):
        """Daily mode: still login page after refresh continues to proxy fallback."""
        result = self._simulate_index_login_fallback(
            is_adhoc_mode=False, login_success=True,
            still_login_page_after_refresh=True)
        assert result == 'proxy_fallback'


class TestPerProxyLoginRouting:
    """Test per-proxy login routing logic for parallel mode.

    Cookie is bound to the machine that performed login. Workers that
    encounter a login page should route the task to the logged-in worker
    instead of sharing the cookie globally.
    """

    def test_attempt_login_refresh_returns_three_tuple(self):
        """attempt_login_refresh returns (success, cookie, proxy_name)."""
        import scripts.spider.state as st

        original = st.login_attempted
        st.login_attempted = True
        try:
            from scripts.spider.session import attempt_login_refresh
            result = attempt_login_refresh()
            assert len(result) == 3
            success, cookie, proxy_name = result
            assert success is False
            assert cookie is None
            assert proxy_name is None
        finally:
            st.login_attempted = original

    def test_attempt_login_refresh_accepts_explicit_proxies(self):
        """attempt_login_refresh accepts explicit_proxies / explicit_proxy_name."""
        import scripts.spider.state as st

        original = st.login_attempted
        st.login_attempted = True
        try:
            from scripts.spider.session import attempt_login_refresh
            result = attempt_login_refresh(
                explicit_proxies={'http': 'http://1.2.3.4:8080'},
                explicit_proxy_name='TestProxy',
            )
            assert len(result) == 3
            assert result[0] is False
        finally:
            st.login_attempted = original

    def test_logged_in_proxy_name_set_on_state(self):
        """state.logged_in_proxy_name starts as None."""
        import scripts.spider.state as st
        assert hasattr(st, 'logged_in_proxy_name')
        original = st.logged_in_proxy_name
        try:
            st.logged_in_proxy_name = 'ARM-Proxy-1'
            assert st.logged_in_proxy_name == 'ARM-Proxy-1'
        finally:
            st.logged_in_proxy_name = original

    def test_worker_login_page_returns_needs_login(self):
        """_try_fetch_and_parse returns needs_login=True on login page."""
        from scripts.spider.parallel import ProxyWorker, DetailTask
        import queue as queue_module

        dq = queue_module.Queue()
        rq = queue_module.Queue()
        lq = queue_module.Queue()

        proxy_cfg = {'name': 'TestProxy', 'http': 'http://1.2.3.4:8080'}
        w = ProxyWorker(
            worker_id=0, proxy_config=proxy_cfg,
            detail_queue=dq, result_queue=rq, login_queue=lq,
            total_workers=1, use_cookie=True, is_adhoc_mode=True,
            movie_sleep_min=0, movie_sleep_max=0, fallback_cooldown=0,
            ban_log_file='', all_workers=[],
        )

        task = DetailTask(url='http://example.com/v/abc',
                          entry={'video_code': 'ABC-123', 'href': '/v/abc', 'page': 1},
                          phase=1, entry_index='1/10')

        login_html = '<html><head><title>登入 JavDB</title></head><body></body></html>'
        original_fetch = w._fetch_html
        w._fetch_html = lambda url, use_cf: login_html

        try:
            magnets, actor, success, needs_login = w._try_fetch_and_parse(
                task, False, "test")
            assert success is False
            assert needs_login is True
        finally:
            w._fetch_html = original_fetch

    def test_try_direct_then_cf_short_circuits_on_login(self):
        """_try_direct_then_cf returns needs_login=True and skips CF bypass."""
        from scripts.spider.parallel import ProxyWorker, DetailTask
        import queue as queue_module

        dq = queue_module.Queue()
        rq = queue_module.Queue()
        lq = queue_module.Queue()

        proxy_cfg = {'name': 'TestProxy', 'http': 'http://1.2.3.4:8080'}
        w = ProxyWorker(
            worker_id=0, proxy_config=proxy_cfg,
            detail_queue=dq, result_queue=rq, login_queue=lq,
            total_workers=1, use_cookie=True, is_adhoc_mode=True,
            movie_sleep_min=0, movie_sleep_max=0, fallback_cooldown=0,
            ban_log_file='', all_workers=[],
        )

        task = DetailTask(url='http://example.com/v/abc',
                          entry={'video_code': 'ABC-123', 'href': '/v/abc', 'page': 1},
                          phase=1, entry_index='1/10')

        login_html = '<html><head><title>登入 JavDB</title></head><body></body></html>'
        call_count = {'n': 0}
        def mock_fetch(url, use_cf):
            call_count['n'] += 1
            return login_html

        w._fetch_html = mock_fetch

        m, a, success, used_cf, needs_login = w._try_direct_then_cf(task)
        assert success is False
        assert needs_login is True
        assert call_count['n'] == 1, "Should short-circuit after Direct detects login page"

    def test_handle_login_required_routes_to_login_queue(self):
        """When a logged-in worker exists, tasks are routed to login_queue."""
        import scripts.spider.parallel as parallel
        from scripts.spider.parallel import ProxyWorker, DetailTask
        import queue as queue_module

        dq = queue_module.Queue()
        rq = queue_module.Queue()
        lq = queue_module.Queue()

        all_workers = []
        for i, name in enumerate(['ProxyA', 'ProxyB']):
            cfg = {'name': name, 'http': f'http://10.0.0.{i+1}:8080'}
            w = ProxyWorker(
                worker_id=i, proxy_config=cfg,
                detail_queue=dq, result_queue=rq, login_queue=lq,
                total_workers=2, use_cookie=True, is_adhoc_mode=True,
                movie_sleep_min=0, movie_sleep_max=0, fallback_cooldown=0,
                ban_log_file='', all_workers=all_workers,
            )
            all_workers.append(w)

        task = DetailTask(url='http://example.com/v/abc',
                          entry={'video_code': 'ABC-123', 'href': '/v/abc', 'page': 1},
                          phase=1, entry_index='1/10')

        original_id = parallel._logged_in_worker_id
        try:
            parallel._logged_in_worker_id = 0  # ProxyA is logged in

            worker_b = all_workers[1]
            worker_b._handle_login_required(task)

            assert not lq.empty(), "Task should be in login_queue"
            routed_task = lq.get_nowait()
            assert routed_task is task
            assert 'ProxyA' not in task.failed_proxies
        finally:
            parallel._logged_in_worker_id = original_id

    def test_handle_login_required_clears_logged_in_proxy_from_failed(self):
        """Routing to login_queue clears the logged-in proxy from failed_proxies."""
        import scripts.spider.parallel as parallel
        from scripts.spider.parallel import ProxyWorker, DetailTask
        import queue as queue_module

        dq = queue_module.Queue()
        rq = queue_module.Queue()
        lq = queue_module.Queue()

        all_workers = []
        for i, name in enumerate(['ProxyA', 'ProxyB']):
            cfg = {'name': name, 'http': f'http://10.0.0.{i+1}:8080'}
            w = ProxyWorker(
                worker_id=i, proxy_config=cfg,
                detail_queue=dq, result_queue=rq, login_queue=lq,
                total_workers=2, use_cookie=True, is_adhoc_mode=True,
                movie_sleep_min=0, movie_sleep_max=0, fallback_cooldown=0,
                ban_log_file='', all_workers=all_workers,
            )
            all_workers.append(w)

        task = DetailTask(url='http://example.com/v/abc',
                          entry={'video_code': 'ABC-123', 'href': '/v/abc', 'page': 1},
                          phase=1, entry_index='1/10',
                          failed_proxies={'ProxyA'})

        original_id = parallel._logged_in_worker_id
        try:
            parallel._logged_in_worker_id = 0  # ProxyA is logged in

            worker_b = all_workers[1]
            worker_b._handle_login_required(task)

            routed_task = lq.get_nowait()
            assert 'ProxyA' not in routed_task.failed_proxies, \
                "logged-in proxy should be cleared from failed_proxies"
        finally:
            parallel._logged_in_worker_id = original_id

    def test_index_login_handoff_to_parallel_worker(self):
        """When index page login happened, matching parallel worker inherits it."""
        import scripts.spider.state as st
        import scripts.spider.parallel as parallel
        from scripts.spider.parallel import ProxyWorker
        import queue as queue_module

        dq = queue_module.Queue()
        rq = queue_module.Queue()
        lq = queue_module.Queue()

        orig_proxy = st.logged_in_proxy_name
        orig_cookie = st.refreshed_session_cookie
        orig_id = parallel._logged_in_worker_id
        try:
            st.logged_in_proxy_name = 'ARM-2'
            st.refreshed_session_cookie = 'test_cookie_value'
            parallel._logged_in_worker_id = None

            all_workers = []
            for i, name in enumerate(['ARM-1', 'ARM-2', 'ARM-3']):
                cfg = {'name': name, 'http': f'http://10.0.0.{i+1}:8080'}
                w = ProxyWorker(
                    worker_id=i, proxy_config=cfg,
                    detail_queue=dq, result_queue=rq, login_queue=lq,
                    total_workers=3, use_cookie=True, is_adhoc_mode=True,
                    movie_sleep_min=0, movie_sleep_max=0, fallback_cooldown=0,
                    ban_log_file='', all_workers=all_workers,
                )
                all_workers.append(w)

            # Simulate the handoff logic from process_detail_entries_parallel
            if st.logged_in_proxy_name and st.refreshed_session_cookie:
                for w in all_workers:
                    if w.proxy_name == st.logged_in_proxy_name:
                        w._handler.config.javdb_session_cookie = st.refreshed_session_cookie
                        parallel._logged_in_worker_id = w.worker_id
                        break

            assert parallel._logged_in_worker_id == 1, "ARM-2 is worker_id=1"
            assert all_workers[1]._handler.config.javdb_session_cookie == 'test_cookie_value'
            assert all_workers[0]._handler.config.javdb_session_cookie != 'test_cookie_value'
        finally:
            st.logged_in_proxy_name = orig_proxy
            st.refreshed_session_cookie = orig_cookie
            parallel._logged_in_worker_id = orig_id


# Use temp_dir fixture
@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    import tempfile
    import shutil
    temp_path = tempfile.mkdtemp()
    yield temp_path
    shutil.rmtree(temp_path, ignore_errors=True)

