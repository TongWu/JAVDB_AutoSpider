"""
Request Handler for JavDB Spider

This module provides a unified HTTP request handler that supports:
- Direct requests with browser-like headers
- Proxy-based requests with automatic failover
- CloudFlare bypass service integration
- Age verification bypass
- Retry mechanisms with configurable fallback strategies

Usage:
    from utils.request_handler import RequestHandler
    
    handler = RequestHandler(proxy_pool=my_proxy_pool, config=my_config)
    html = handler.get_page(url, use_proxy=True, use_cf_bypass=True)
"""

import requests
import time
import logging
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urljoin, urlparse, quote
from bs4 import BeautifulSoup
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Import masking utilities
from utils.masking import mask_ip_address, mask_proxy_url, mask_full


@dataclass
class RequestConfig:
    """Configuration for request handler"""
    base_url: str = 'https://javdb.com'
    cf_bypass_service_port: int = 8000
    cf_bypass_enabled: bool = True
    cf_bypass_max_failures: int = 3
    cf_turnstile_cooldown: int = 10
    fallback_cooldown: int = 30
    javdb_session_cookie: Optional[str] = None
    proxy_http: Optional[str] = None
    proxy_https: Optional[str] = None
    proxy_modules: list = None
    proxy_mode: str = 'single'
    
    def __post_init__(self):
        if self.proxy_modules is None:
            self.proxy_modules = ['all']


class RequestHandler:
    """
    Unified HTTP request handler with proxy and CF bypass support.
    
    Features:
    - Browser-like headers to avoid detection
    - Proxy pool integration with automatic failover
    - CloudFlare bypass service integration
    - Age verification modal bypass
    - Configurable retry mechanisms
    """
    
    # Browser-like headers for direct requests to javdb.com
    # These mimic a real Chrome browser on macOS to avoid detection
    BROWSER_HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Sec-Ch-Ua': '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"macOS"',
        'Cache-Control': 'max-age=0',
    }
    
    # Minimal headers for CF bypass service requests
    # The bypass service handles its own headers (User-Agent, cookies, etc.)
    BYPASS_HEADERS = {}
    
    def __init__(self, proxy_pool=None, config: Optional[RequestConfig] = None):
        """
        Initialize request handler.
        
        Args:
            proxy_pool: ProxyPool instance for proxy management
            config: RequestConfig instance with configuration settings
        """
        self.proxy_pool = proxy_pool
        self.config = config or RequestConfig()
        self.session = requests.Session()
        
        # Counter for consecutive CF bypass failures (small responses)
        self.cf_bypass_failure_count: int = 0
        self.cf_bypass_force_refresh: bool = False
        
    def should_use_proxy_for_module(self, module_name: str, use_proxy_flag: bool) -> bool:
        """
        Check if a specific module should use proxy based on configuration.
        
        Args:
            module_name: Name of the module ('spider_index', 'spider_detail', 'spider_age_verification')
            use_proxy_flag: Whether --use-proxy flag is enabled
        
        Returns:
            bool: True if the module should use proxy, False otherwise
        """
        if not use_proxy_flag:
            return False
        
        if not self.config.proxy_modules:
            # Empty list means no modules use proxy
            return False
        
        if 'all' in self.config.proxy_modules:
            # 'all' means all modules use proxy
            return True
        
        # Check if specific module is in the list
        return module_name in self.config.proxy_modules
    
    @staticmethod
    def extract_ip_from_proxy_url(proxy_url: str) -> Optional[str]:
        """
        Extract IP address or hostname from a proxy URL.
        
        Args:
            proxy_url: Proxy URL (e.g., 'http://user:pass@192.168.1.1:8080')
        
        Returns:
            IP address or hostname, or None if extraction fails
        """
        try:
            parsed = urlparse(proxy_url)
            return parsed.hostname
        except Exception as e:
            # Don't log the actual proxy URL for security
            logger.warning(f"Failed to extract IP from proxy URL: {type(e).__name__}")
            return None
    
    def get_cf_bypass_service_url(self, proxy_ip: Optional[str] = None) -> str:
        """
        Get the CF bypass service URL based on proxy configuration.
        
        Args:
            proxy_ip: IP address of the proxy server (if using proxy)
        
        Returns:
            CF bypass service URL:
            - Without proxy: http://127.0.0.1:{CF_BYPASS_SERVICE_PORT}
            - With proxy: http://{proxy_ip}:{CF_BYPASS_SERVICE_PORT}
        """
        if proxy_ip:
            return f"http://{proxy_ip}:{self.config.cf_bypass_service_port}"
        else:
            return f"http://127.0.0.1:{self.config.cf_bypass_service_port}"
    
    @staticmethod
    def is_cf_bypass_failure(html_content: str) -> bool:
        """
        Check if the CF bypass response indicates a failure.
        
        Failure criteria: HTML size < 1000 bytes AND contains 'fail' keyword
        
        Args:
            html_content: The HTML content returned by bypass service
        
        Returns:
            True if the response is considered a failure
        """
        if html_content is None:
            return True
        
        content_size = len(html_content)
        contains_fail = 'fail' in html_content.lower()
        
        is_failure = content_size < 1000 and contains_fail
        
        if is_failure:
            logger.debug(f"[CF Bypass] Failure detected: size={content_size} bytes, contains_fail={contains_fail}")
        
        return is_failure
    
    def _get_proxies_config(self, module_name: str, use_proxy: bool) -> Tuple[Optional[Dict[str, str]], bool]:
        """
        Get current proxy configuration based on settings.
        Uses round-robin proxy selection to distribute requests across all available proxies.
        
        Returns:
            Tuple of (proxies_dict, use_proxy_pool_mode)
        """
        if not self.should_use_proxy_for_module(module_name, use_proxy):
            return None, False
        
        if self.config.proxy_mode in ('pool', 'single') and self.proxy_pool is not None:
            # Use round-robin proxy selection for load balancing
            proxies = self.proxy_pool.get_next_proxy()
            if proxies:
                return proxies, True
            else:
                logger.warning(f"[{module_name}] Proxy mode '{self.config.proxy_mode}' enabled but no proxy available")
                return None, False
        elif self.config.proxy_http or self.config.proxy_https:
            proxies = {}
            if self.config.proxy_http:
                proxies['http'] = self.config.proxy_http
            if self.config.proxy_https:
                proxies['https'] = self.config.proxy_https
            return proxies, False
        
        return None, False
    
    def _do_request(self, target_url: str, req_headers: Dict, req_proxies: Optional[Dict], 
                    timeout: int, context_msg: str, session: Optional[requests.Session] = None) -> Tuple[Optional[str], Optional[Exception]]:
        """Execute a single HTTP request."""
        use_session = session or self.session
        
        try:
            logger.debug(f"[{context_msg}] Requesting: {target_url}")
            logger.debug(f"[{context_msg}] Headers: {req_headers}")
            if req_proxies:
                logger.debug(f"[{context_msg}] Using proxies: {req_proxies}")
            
            response = use_session.get(target_url, headers=req_headers, proxies=req_proxies, timeout=timeout)
            response.raise_for_status()
            
            # Log response details
            content_len = len(response.content)
            text_len = len(response.text)
            logger.debug(f"[{context_msg}] Response: HTTP {response.status_code}, Content-Length: {content_len} bytes, Text-Length: {text_len} chars")
            
            # Log first 200 chars of response for debugging
            preview = response.text[:200].replace('\n', ' ').replace('\r', '')
            logger.debug(f"[{context_msg}] Response preview: {preview}...")
            
            return response.text, None
        except requests.RequestException as e:
            logger.error(f"[{context_msg}] Error: {e}")
            return None, e
    
    def _get_bypass_ip(self, req_proxies: Optional[Dict], force_local: bool = False) -> Optional[str]:
        """Get the bypass service IP based on proxy configuration."""
        if force_local or not req_proxies:
            return None  # Will use 127.0.0.1
        proxy_url = req_proxies.get('https') or req_proxies.get('http')
        if proxy_url:
            return self.extract_ip_from_proxy_url(proxy_url)
        return None
    
    def _refresh_bypass_cache(self, url: str, req_proxies: Optional[Dict], 
                               force_local: bool = False, session: Optional[requests.Session] = None) -> bool:
        """
        Refresh the CF bypass cache by sending a request with x-bypass-cache header.
        
        This forces the bypass service to get fresh cf_clearance cookies.
        
        Request format:
        curl "http://{ip}:8000/html?url={encoded_url}" \
          -H "x-bypass-cache: true"
        """
        use_session = session or self.session
        proxy_ip = self._get_bypass_ip(req_proxies, force_local)
        
        # Validate bypass IP based on mode
        if not force_local:
            if req_proxies is None:
                logger.warning(f"[CF Bypass] Cannot refresh cache: no proxy available")
                return False
            elif proxy_ip is None:
                logger.warning(f"[CF Bypass] Cannot refresh cache: failed to extract IP from proxy config")
                return False
        
        bypass_base_url = self.get_cf_bypass_service_url(proxy_ip)
        encoded_url = quote(url, safe='')
        refresh_url = f"{bypass_base_url}/html?url={encoded_url}"
        
        # Build headers for cache refresh
        refresh_headers = {
            'x-bypass-cache': 'true'
        }
        
        # Mask the proxy IP in the URL for logging (use 127.0.0.1 when proxy_ip is None)
        masked_ip = mask_ip_address(proxy_ip) if proxy_ip else '127.0.0.1'
        masked_bypass_url = f"http://{masked_ip}:{self.config.cf_bypass_service_port}/html?url=..."
        logger.debug(f"[CF Bypass] Refreshing bypass cache: {masked_bypass_url}")
        
        try:
            response = use_session.get(refresh_url, headers=refresh_headers, timeout=120)
            if response.status_code == 200:
                content_size = len(response.content)
                if content_size > 10000:
                    logger.debug(f"[CF Bypass] Cache refresh successful (size={content_size} bytes)")
                    return True
                else:
                    logger.warning(f"[CF Bypass] Cache refresh returned small response (size={content_size} bytes)")
                    return False
            else:
                logger.warning(f"[CF Bypass] Cache refresh failed: HTTP {response.status_code}")
                return False
        except requests.RequestException as e:
            logger.error(f"[CF Bypass] Cache refresh error: {e}")
            return False
    
    def _fetch_with_cf_bypass(self, url: str, req_proxies: Optional[Dict], context_msg: str,
                               force_local: bool = False, use_proxy_bypass: bool = False,
                               session: Optional[requests.Session] = None) -> Tuple[Optional[str], bool, bool]:
        """
        Fetch using CF bypass service.
        
        Args:
            url: Target URL to fetch
            req_proxies: Proxy configuration (used to determine bypass service IP)
            context_msg: Context message for logging
            force_local: If True, always use local bypass (127.0.0.1) regardless of proxy settings
            use_proxy_bypass: If True, requires proxy for bypass mode
            session: Optional custom session to use
        
        Returns:
            tuple: (html_content, success, is_turnstile)
        """
        proxy_ip = self._get_bypass_ip(req_proxies, force_local)
        
        # Validate bypass IP based on mode
        if not force_local:
            if req_proxies is None and use_proxy_bypass:
                logger.error(f"[CF Bypass] {context_msg}: No proxy available for proxy bypass mode")
                return None, False, False
            elif req_proxies is not None and proxy_ip is None:
                logger.error(f"[CF Bypass] {context_msg}: Failed to extract IP from proxy config")
                return None, False, False
        
        # Build CF bypass URL: http://{ip}:8000/html?url={encoded_target_url}
        bypass_base_url = self.get_cf_bypass_service_url(proxy_ip)
        encoded_url = quote(url, safe='')
        bypass_url = f"{bypass_base_url}/html?url={encoded_url}"
        
        # Mask the proxy IP in the URL for logging (use 127.0.0.1 when proxy_ip is None)
        masked_ip = mask_ip_address(proxy_ip) if proxy_ip else '127.0.0.1'
        masked_bypass_base = f"http://{masked_ip}:{self.config.cf_bypass_service_port}"
        logger.debug(f"[CF Bypass] {context_msg}: {url} -> {masked_bypass_base}/html?url=...")
        
        # CF bypass requests are always sent directly (no proxy forwarding)
        html_content, error = self._do_request(bypass_url, self.BYPASS_HEADERS, None, 
                                                timeout=60, context_msg=f"CF Bypass {context_msg}",
                                                session=session)
        
        if html_content:
            content_size = len(html_content)
            has_turnstile_keyword = 'turnstile' in html_content.lower()
            has_security_verification = 'Security Verification' in html_content
            is_bypass_failure = self.is_cf_bypass_failure(html_content)
            
            logger.debug(f"[CF Bypass] {context_msg} response: size={content_size}, turnstile_keyword={has_turnstile_keyword}, security_verification={has_security_verification}, bypass_failure={is_bypass_failure}")
            
            if not is_bypass_failure:
                is_turnstile = has_security_verification and has_turnstile_keyword
                if is_turnstile:
                    logger.warning(f"[CF Bypass] {context_msg} returned Turnstile page (size={content_size} bytes)")
                    return html_content, False, True
                
                # Check for age verification modal without content
                # If age modal exists but no movie-list/video-detail, we need to handle over18 via CF bypass
                soup = BeautifulSoup(html_content, 'html.parser')
                age_modal = soup.find('div', class_='modal is-active over18-modal')
                
                if age_modal:
                    movie_list = soup.find('div', class_=lambda x: x and 'movie-list' in x)
                    detail_content = soup.find('div', class_='video-detail')
                    
                    if not movie_list and not detail_content:
                        # Age modal exists but no content - need to click over18 via CF bypass
                        logger.debug(f"[CF Bypass] {context_msg}: Age modal detected without content, attempting over18 bypass via CF...")
                        
                        over18_link_found = False
                        age_links = age_modal.find_all('a', href=True)
                        for link in age_links:
                            if 'over18' in link.get('href', ''):
                                over18_link_found = True
                                over18_path = link.get('href')
                                over18_url = urljoin(self.config.base_url, over18_path)
                                
                                # Use CF bypass to visit over18 link (sets cookie on bypass server)
                                encoded_over18_url = quote(over18_url, safe='')
                                bypass_over18_url = f"{bypass_base_url}/html?url={encoded_over18_url}"
                                
                                logger.debug(f"[CF Bypass] {context_msg}: Visiting over18 URL via bypass: {over18_url}")
                                
                                over18_content, over18_error = self._do_request(
                                    bypass_over18_url, self.BYPASS_HEADERS, None,
                                    timeout=60, context_msg=f"CF Bypass Over18 {context_msg}",
                                    session=session
                                )
                                
                                if over18_content:
                                    logger.debug(f"[CF Bypass] {context_msg}: Over18 bypass returned {len(over18_content)} bytes, re-fetching original URL...")
                                    
                                    # Re-fetch the original URL after over18 cookie is set
                                    html_content2, error2 = self._do_request(
                                        bypass_url, self.BYPASS_HEADERS, None,
                                        timeout=60, context_msg=f"CF Bypass Retry {context_msg}",
                                        session=session
                                    )
                                    
                                    if html_content2:
                                        content_size2 = len(html_content2)
                                        logger.debug(f"[CF Bypass] {context_msg}: After over18 bypass, got {content_size2} bytes")
                                        
                                        # Check if we now have content
                                        soup2 = BeautifulSoup(html_content2, 'html.parser')
                                        movie_list2 = soup2.find('div', class_=lambda x: x and 'movie-list' in x)
                                        detail_content2 = soup2.find('div', class_='video-detail')
                                        
                                        if movie_list2 or detail_content2:
                                            logger.debug(f"[CF Bypass] {context_msg}: Over18 bypass successful, got content!")
                                            return html_content2, True, False
                                        else:
                                            logger.warning(f"[CF Bypass] {context_msg}: Over18 bypass did not help, still no content")
                                else:
                                    logger.warning(f"[CF Bypass] {context_msg}: Over18 bypass request failed")
                                break
                        
                        # If we reach here, age modal was detected without content and bypass failed or wasn't possible
                        if not over18_link_found:
                            logger.warning(f"[CF Bypass] {context_msg}: Age modal detected but no over18 link found")
                        
                        # Return failure since we have age modal but no content
                        logger.warning(f"[CF Bypass] {context_msg}: Age verification bypass failed - returning HTML without valid content")
                        return html_content, False, False
                
                logger.debug(f"[CF Bypass] {context_msg} SUCCESS - got valid HTML (size={content_size} bytes)")
                return html_content, True, False
            else:
                logger.warning(f"[CF Bypass] {context_msg} returned failure response (size={content_size} bytes)")
                return html_content, False, False
        else:
            logger.error(f"[CF Bypass] {context_msg} returned no content")
            return None, False, False
    
    def _fetch_direct(self, url: str, req_proxies: Optional[Dict], context_msg: str,
                      use_cookie: bool = False, session: Optional[requests.Session] = None) -> Tuple[Optional[str], bool, bool]:
        """
        Fetch directly without CF bypass. Uses browser-like headers.
        
        Returns:
            tuple: (html_content, success, is_turnstile)
        """
        headers = self.BROWSER_HEADERS.copy()
        if use_cookie and self.config.javdb_session_cookie:
            headers['Cookie'] = f'_jdb_session={self.config.javdb_session_cookie}'
        
        html_content, error = self._do_request(url, headers, req_proxies, timeout=30, 
                                                context_msg=f"Direct {context_msg}",
                                                session=session)
        if html_content:
            is_turnstile = 'Security Verification' in html_content and 'turnstile' in html_content.lower()
            if is_turnstile:
                logger.warning(f"[Direct] {context_msg} returned Turnstile page (size={len(html_content)} bytes)")
                return html_content, False, True
            return html_content, error is None, False
        return None, False, False
    
    def _process_html(self, url: str, html_content: Optional[str], req_proxies: Optional[Dict],
                      use_cookie: bool = False, session: Optional[requests.Session] = None,
                      from_cf_bypass: bool = False) -> Optional[str]:
        """Process HTML content: check for Cloudflare and age verification.
        
        Args:
            url: Original URL that was fetched
            html_content: HTML content to process
            req_proxies: Proxy configuration
            use_cookie: Whether to use session cookie
            session: Optional custom session to use
            from_cf_bypass: If True, HTML was fetched via CF bypass service
        """
        if not html_content:
            return None
        
        use_session = session or self.session
        headers = self.BROWSER_HEADERS.copy()
        if use_cookie and self.config.javdb_session_cookie:
            headers['Cookie'] = f'_jdb_session={self.config.javdb_session_cookie}'
        
        # Check for Cloudflare Turnstile verification page
        if 'Security Verification' in html_content and 'turnstile' in html_content.lower():
            logger.warning(f"Cloudflare Turnstile verification page detected for {url} (Size: {len(html_content)} bytes)")
            return None
        
        # Check for age verification modal
        soup = BeautifulSoup(html_content, 'html.parser')
        age_modal = soup.find('div', class_='modal is-active over18-modal')
        
        if age_modal:
            # If HTML came from CF bypass and already contains useful content (movie-list),
            # skip the age verification bypass as the content is already valid.
            # The modal HTML may still be present but the page content is accessible.
            if from_cf_bypass:
                movie_list = soup.find('div', class_=lambda x: x and 'movie-list' in x)
                if movie_list:
                    logger.debug("Age verification modal detected but HTML from CF bypass already contains movie-list, skipping re-fetch")
                    return html_content
                
                # Also check for detail page content (for /v/ pages)
                detail_content = soup.find('div', class_='video-detail')
                if detail_content:
                    logger.debug("Age verification modal detected but HTML from CF bypass already contains video-detail, skipping re-fetch")
                    return html_content
                
                # CF bypass HTML has age modal but no content - need to handle via CF bypass
                # Using direct requests here would fail because the page requires CF bypass to access
                logger.debug("Age verification modal detected in CF bypass HTML but no valid content found - returning as-is (bypass service should handle over18 cookie)")
                # Return the HTML as-is; the validation will fail but at least we don't 
                # cause infinite redirect loops by trying direct requests
                return html_content
            
            logger.debug("Age verification modal detected, attempting to bypass...")
            
            age_links = age_modal.find_all('a', href=True)
            for link in age_links:
                if 'over18' in link.get('href', ''):
                    age_url = urljoin(self.config.base_url, link.get('href'))
                    logger.debug(f"Found age verification link: {age_url}")
                    
                    try:
                        age_response = use_session.get(age_url, headers=headers, proxies=req_proxies, timeout=30)
                        if age_response.status_code == 200:
                            logger.debug("Successfully bypassed age verification")
                            # Re-fetch the original page
                            final_response = use_session.get(url, headers=headers, proxies=req_proxies, timeout=30)
                            if final_response.status_code == 200:
                                logger.debug("Successfully re-fetched page after age verification")
                                return final_response.text
                    except requests.RequestException as e:
                        logger.debug(f"Failed to bypass age verification: {e}")
                    break
            
            logger.debug("Could not find or access age verification link")
        
        return html_content
    
    def get_page(self, url: str, session: Optional[requests.Session] = None, use_cookie: bool = False,
                 use_proxy: bool = False, module_name: str = 'unknown', max_retries: int = 3,
                 use_cf_bypass: bool = False) -> Optional[str]:
        """
        Fetch a webpage with proper headers, age verification bypass, and proxy pool support.
        
        Mode combinations:
        - --use-proxy only: Use proxy to access website directly (no bypass)
        - --use-cf-bypass only: Use local CF bypass service (http://127.0.0.1:8000/html?url=...)
        - --use-proxy --use-cf-bypass: Use proxy's CF bypass service (http://{proxy_ip}:8000/html?url=...)
        
        CF Bypass failure detection: HTML size < 1000 bytes AND contains 'fail' keyword
        
        Retry sequence on CF bypass failure:
          a. Retry current method (bypass)
          b. Without bypass, use current proxy
          c. Switch to another proxy, without bypass
          d. Use bypass with new proxy
        
        Service repository: https://github.com/sarperavci/CloudflareBypassForScraping
        
        Args:
            url: URL to fetch
            session: requests.Session object for connection reuse
            use_cookie: Whether to add session cookie
            use_proxy: Whether --use-proxy flag is enabled
            module_name: Module name for proxy control ('spider_index', 'spider_detail', 'spider_age_verification')
            max_retries: Maximum number of retries with different proxies (only for proxy pool mode)
            use_cf_bypass: Whether to use CF bypass service
            
        Returns:
            HTML content as string, or None if failed
        """
        use_session = session or self.session
        
        # Check if CF bypass is globally disabled
        effective_use_cf_bypass = use_cf_bypass and self.config.cf_bypass_enabled
        if use_cf_bypass and not self.config.cf_bypass_enabled:
            logger.debug(f"[CF Bypass] Globally disabled via CF_BYPASS_ENABLED=False")
        
        # Get initial proxy configuration
        proxies, use_proxy_pool_mode = self._get_proxies_config(module_name, use_proxy)
        proxy_name = self.proxy_pool.get_current_proxy_name() if (use_proxy_pool_mode and self.proxy_pool) else "None"
        
        # Determine the mode based on flags
        use_local_bypass = effective_use_cf_bypass and not use_proxy
        use_proxy_bypass = effective_use_cf_bypass and use_proxy
        
        if use_local_bypass:
            logger.debug(f"[{module_name}] Mode: Local CF Bypass only (127.0.0.1:{self.config.cf_bypass_service_port})")
        elif use_proxy_bypass:
            logger.debug(f"[{module_name}] Mode: Proxy + Proxy's CF Bypass (Proxy={proxy_name})")
        elif use_proxy:
            logger.debug(f"[{module_name}] Mode: Proxy only (no bypass, Proxy={proxy_name})")
        else:
            logger.debug(f"[{module_name}] Mode: Direct request (no proxy, no bypass)")
        
        # If CF bypass is enabled, use the new retry sequence
        if effective_use_cf_bypass:
            return self._get_page_with_cf_bypass(
                url=url,
                session=use_session,
                use_cookie=use_cookie,
                use_proxy=use_proxy,
                module_name=module_name,
                max_retries=max_retries,
                proxies=proxies,
                use_proxy_pool_mode=use_proxy_pool_mode,
                proxy_name=proxy_name,
                use_local_bypass=use_local_bypass,
                use_proxy_bypass=use_proxy_bypass
            )
        
        # Non-CF bypass mode: standard retry logic
        return self._get_page_direct(
            url=url,
            session=use_session,
            use_cookie=use_cookie,
            module_name=module_name,
            max_retries=max_retries,
            proxies=proxies,
            use_proxy_pool_mode=use_proxy_pool_mode,
            proxy_name=proxy_name
        )
    
    def _get_page_with_cf_bypass(self, url: str, session: requests.Session, use_cookie: bool,
                                  use_proxy: bool, module_name: str, max_retries: int,
                                  proxies: Optional[Dict], use_proxy_pool_mode: bool, 
                                  proxy_name: str, use_local_bypass: bool, use_proxy_bypass: bool) -> Optional[str]:
        """Handle page fetching with CF bypass enabled."""
        turnstile_detected = False
        
        # Check if using proxy+bypass mode but no proxy available
        if use_proxy_bypass and proxies is None:
            logger.error(f"[{module_name}] Proxy+CF Bypass mode but no proxy available. Cannot proceed.")
            return None
        
        # Step: Initial CF bypass attempt
        html_content, success, is_turnstile = self._fetch_with_cf_bypass(
            url, proxies, f"Proxy={proxy_name}", 
            force_local=use_local_bypass, use_proxy_bypass=use_proxy_bypass, session=session
        )
        if success:
            result = self._process_html(url, html_content, proxies, use_cookie, session, from_cf_bypass=True)
            if result and len(result) >= 10000:
                if use_proxy_pool_mode and self.proxy_pool:
                    self.proxy_pool.mark_success()
                self.cf_bypass_failure_count = 0
                return result
            elif result:
                logger.warning(f"[{module_name}] Initial CF bypass returned small response ({len(result)} bytes), continuing to fallback")
        
        turnstile_detected = is_turnstile
        logger.warning(f"[{module_name}] CF Bypass initial attempt failed. Starting fallback sequence (cooldown: {self.config.fallback_cooldown}s between steps)...")
        self.cf_bypass_failure_count += 1
        
        # Cooldown before entering fallback
        if self.config.fallback_cooldown > 0:
            logger.debug(f"[{module_name}] Fallback cooldown: {self.config.fallback_cooldown}s before step (a)")
            time.sleep(self.config.fallback_cooldown)
        
        # Step (a): Retry CF bypass (one more attempt)
        logger.debug(f"[{module_name}] Fallback step (a): Retry CF bypass")
        html_content, success, is_turnstile = self._fetch_with_cf_bypass(
            url, proxies, f"Retry Proxy={proxy_name}",
            force_local=use_local_bypass, use_proxy_bypass=use_proxy_bypass, session=session
        )
        if success:
            result = self._process_html(url, html_content, proxies, use_cookie, session, from_cf_bypass=True)
            if result and len(result) >= 10000:
                if use_proxy_pool_mode and self.proxy_pool:
                    self.proxy_pool.mark_success()
                self.cf_bypass_failure_count = 0
                return result
            elif result:
                logger.warning(f"[{module_name}] Step (a) returned small response ({len(result)} bytes), continuing to next step")
        
        turnstile_detected = turnstile_detected or is_turnstile
        
        # Refresh bypass cache between step (a) and (b) if turnstile detected
        if turnstile_detected:
            logger.info(f"[{module_name}] Turnstile detected, refreshing bypass cache...")
            if self.config.fallback_cooldown > 0:
                time.sleep(self.config.fallback_cooldown)
            self._refresh_bypass_cache(url, proxies, force_local=use_local_bypass, session=session)
            turnstile_detected = False
        
        # Step (b): Try direct (no bypass) with current proxy (only if using proxy)
        if use_proxy and proxies:
            if self.config.fallback_cooldown > 0:
                logger.debug(f"[{module_name}] Fallback cooldown: {self.config.fallback_cooldown}s before step (b)")
                time.sleep(self.config.fallback_cooldown)
            
            logger.debug(f"[{module_name}] Fallback step (b): Direct request with current proxy (no bypass)")
            html_content, success, is_turnstile = self._fetch_direct(url, proxies, f"Proxy={proxy_name}", use_cookie, session)
            if success:
                result = self._process_html(url, html_content, proxies, use_cookie, session)
                if result and len(result) >= 10000:
                    if use_proxy_pool_mode and self.proxy_pool:
                        self.proxy_pool.mark_success()
                    return result
            turnstile_detected = turnstile_detected or is_turnstile
        
        # Step (c) & (d): Try other proxies if in pool mode
        if use_proxy and use_proxy_pool_mode and self.proxy_pool and self.config.proxy_mode == 'pool':
            max_proxy_switches = min(len(self.proxy_pool.proxies) - 1, 5)
            
            for switch_count in range(max_proxy_switches):
                if self.config.fallback_cooldown > 0:
                    logger.debug(f"[{module_name}] Fallback cooldown: {self.config.fallback_cooldown}s before switching proxy")
                    time.sleep(self.config.fallback_cooldown)
                
                # Switch to next proxy
                switched = self.proxy_pool.mark_failure_and_switch()
                if not switched:
                    logger.warning(f"[{module_name}] No more proxies available in pool")
                    break
                
                proxies = self.proxy_pool.get_current_proxy()
                proxy_name = self.proxy_pool.get_current_proxy_name()
                
                # Step (c): Try direct with new proxy
                logger.debug(f"[{module_name}] Fallback step (c): Direct request with new proxy={proxy_name} (no bypass)")
                html_content, success, is_turnstile = self._fetch_direct(url, proxies, f"Proxy={proxy_name}", use_cookie, session)
                if success:
                    result = self._process_html(url, html_content, proxies, use_cookie, session)
                    if result and len(result) >= 10000:
                        self.proxy_pool.mark_success()
                        return result
                turnstile_detected = turnstile_detected or is_turnstile
                
                if self.config.fallback_cooldown > 0:
                    logger.debug(f"[{module_name}] Fallback cooldown: {self.config.fallback_cooldown}s before step (d)")
                    time.sleep(self.config.fallback_cooldown)
                
                # Step (d): Try CF bypass with new proxy
                logger.debug(f"[{module_name}] Fallback step (d): CF bypass with new proxy={proxy_name}")
                html_content, success, is_turnstile = self._fetch_with_cf_bypass(
                    url, proxies, f"Proxy={proxy_name}", force_local=False, 
                    use_proxy_bypass=True, session=session
                )
                if success:
                    result = self._process_html(url, html_content, proxies, use_cookie, session, from_cf_bypass=True)
                    if result and len(result) >= 10000:
                        self.proxy_pool.mark_success()
                        self.cf_bypass_failure_count = 0
                        return result
                    elif result:
                        logger.warning(f"[{module_name}] Step (d) returned small response ({len(result)} bytes), continuing to next proxy")
                
                turnstile_detected = turnstile_detected or is_turnstile
                
                # Refresh bypass cache after step (d) if turnstile detected
                if turnstile_detected:
                    logger.info(f"[{module_name}] Turnstile detected after step (d), refreshing bypass cache for proxy={proxy_name}...")
                    if self.config.fallback_cooldown > 0:
                        time.sleep(self.config.fallback_cooldown)
                    self._refresh_bypass_cache(url, proxies, force_local=False, session=session)
                    turnstile_detected = False
        
        # All fallbacks failed
        logger.error(f"[{module_name}] All CF bypass fallback attempts exhausted for {url}")
        self.cf_bypass_failure_count += 1
        if self.cf_bypass_failure_count >= self.config.cf_bypass_max_failures:
            logger.error(f"[{module_name}] CF Bypass has failed {self.cf_bypass_failure_count} times. Service may not be working properly.")
        return None
    
    def _get_page_direct(self, url: str, session: requests.Session, use_cookie: bool,
                          module_name: str, max_retries: int, proxies: Optional[Dict],
                          use_proxy_pool_mode: bool, proxy_name: str) -> Optional[str]:
        """Handle page fetching without CF bypass (direct mode)."""
        retry_count = 0
        
        while retry_count < max_retries:
            logger.debug(f"Fetching URL: {url} (attempt {retry_count + 1}/{max_retries})")
            if proxies:
                logger.debug(f"Using proxies: {proxies}")
            
            html_content, success, is_turnstile = self._fetch_direct(
                url, proxies, f"Proxy={proxy_name}" if proxies else "No proxy", 
                use_cookie, session
            )
            
            if success:
                if use_proxy_pool_mode and self.proxy_pool:
                    self.proxy_pool.mark_success()
                
                result = self._process_html(url, html_content, proxies, use_cookie, session)
                if result and len(result) >= 10000:
                    return result
                elif result:
                    # Small response - for detail pages this is likely a failed response, retry
                    if '/v/' in url:
                        logger.warning(f"[{module_name}] Small response for detail page ({len(result)} bytes), retrying...")
                    else:
                        # For index pages, small response might be valid (empty page)
                        return result
            
            # If Turnstile detected, wait before retry
            if is_turnstile:
                logger.warning(f"[{module_name}] Turnstile detected, waiting {self.config.cf_turnstile_cooldown}s before retry...")
                time.sleep(self.config.cf_turnstile_cooldown)
            
            # Request failed, try to switch proxy
            if use_proxy_pool_mode and self.proxy_pool and retry_count < max_retries - 1:
                switched = self.proxy_pool.mark_failure_and_switch()
                if switched:
                    proxies = self.proxy_pool.get_current_proxy()
                    proxy_name = self.proxy_pool.get_current_proxy_name()
                    logger.info(f"[{module_name}] Switched to proxy: {proxy_name}, retrying...")
                    retry_count += 1
                    continue
                else:
                    logger.error(f"[{module_name}] Failed to switch proxy, no more proxies available")
                    break
            else:
                retry_count += 1
        
        return None
    
    def reset_cf_bypass_state(self):
        """Reset CF bypass failure counters."""
        self.cf_bypass_failure_count = 0
        self.cf_bypass_force_refresh = False


def create_request_handler_from_config(proxy_pool=None, **config_kwargs) -> RequestHandler:
    """
    Create a RequestHandler instance from configuration.
    
    Args:
        proxy_pool: Optional ProxyPool instance
        **config_kwargs: Configuration parameters for RequestConfig
    
    Returns:
        Configured RequestHandler instance
    """
    config = RequestConfig(**config_kwargs)
    return RequestHandler(proxy_pool=proxy_pool, config=config)


class ProxyHelper:
    """
    Helper class for proxy-related operations.
    
    This class provides static methods for modules that need proxy support
    but don't require the full RequestHandler functionality (like CF bypass).
    
    Usage:
        from utils.request_handler import ProxyHelper
        
        helper = ProxyHelper(proxy_pool, proxy_modules=['qbittorrent'])
        proxies = helper.get_proxies_dict('qbittorrent', use_proxy=True)
    """
    
    def __init__(self, proxy_pool=None, proxy_modules: Optional[list] = None,
                 proxy_mode: str = 'single', proxy_http: Optional[str] = None,
                 proxy_https: Optional[str] = None):
        """
        Initialize ProxyHelper.
        
        Args:
            proxy_pool: ProxyPool instance for proxy management
            proxy_modules: List of module names that should use proxy, or ['all']
            proxy_mode: Proxy mode ('single' or 'pool')
            proxy_http: Legacy HTTP proxy URL
            proxy_https: Legacy HTTPS proxy URL
        """
        self.proxy_pool = proxy_pool
        self.proxy_modules = proxy_modules if proxy_modules is not None else ['all']
        self.proxy_mode = proxy_mode
        self.proxy_http = proxy_http
        self.proxy_https = proxy_https
    
    def should_use_proxy_for_module(self, module_name: str, use_proxy_flag: bool) -> bool:
        """
        Check if a specific module should use proxy based on configuration.
        
        Args:
            module_name: Name of the module (e.g., 'qbittorrent', 'pikpak')
            use_proxy_flag: Whether --use-proxy flag is enabled
        
        Returns:
            bool: True if the module should use proxy, False otherwise
        """
        if not use_proxy_flag:
            return False
        
        if not self.proxy_modules:
            return False
        
        if 'all' in self.proxy_modules:
            return True
        
        return module_name in self.proxy_modules
    
    def get_proxies_dict(self, module_name: str, use_proxy_flag: bool) -> Optional[Dict[str, str]]:
        """
        Get proxies dictionary for requests if module should use proxy.
        
        Args:
            module_name: Name of the module
            use_proxy_flag: Whether --use-proxy flag is enabled
        
        Returns:
            dict or None: Proxies dictionary for requests, or None
        """
        if not self.should_use_proxy_for_module(module_name, use_proxy_flag):
            return None
        
        # Try proxy pool first (both pool and single modes)
        if self.proxy_mode in ('pool', 'single') and self.proxy_pool is not None:
            proxies = self.proxy_pool.get_current_proxy()
            if proxies:
                proxy_name = self.proxy_pool.get_current_proxy_name()
                logger.debug(f"[{module_name}] Using proxy mode '{self.proxy_mode}' - Current proxy: {proxy_name}")
            else:
                logger.warning(f"[{module_name}] Proxy mode '{self.proxy_mode}' enabled but no proxy available")
            return proxies
        
        # Fallback to legacy PROXY_HTTP/PROXY_HTTPS
        if not (self.proxy_http or self.proxy_https):
            return None
        
        proxies = {}
        if self.proxy_http:
            proxies['http'] = self.proxy_http
        if self.proxy_https:
            proxies['https'] = self.proxy_https
        
        logger.debug(f"[{module_name}] Using single proxy: {proxies}")
        return proxies
    
    def get_current_proxy_name(self) -> str:
        """Get the name of current active proxy."""
        if self.proxy_pool is not None:
            return self.proxy_pool.get_current_proxy_name()
        return "Legacy-Proxy" if (self.proxy_http or self.proxy_https) else "None"
    
    def mark_success(self):
        """Mark the current proxy as successful."""
        if self.proxy_pool is not None:
            self.proxy_pool.mark_success()
    
    def mark_failure_and_switch(self) -> bool:
        """Mark current proxy as failed and switch to next available proxy."""
        if self.proxy_pool is not None:
            return self.proxy_pool.mark_failure_and_switch()
        return False
    
    def get_statistics(self) -> Dict:
        """Get statistics about proxy pool usage."""
        if self.proxy_pool is not None:
            return self.proxy_pool.get_statistics()
        return {
            'total_proxies': 1 if (self.proxy_http or self.proxy_https) else 0,
            'available_proxies': 1 if (self.proxy_http or self.proxy_https) else 0,
            'in_cooldown': 0,
            'no_proxy_mode': False,
            'proxies': []
        }


def create_proxy_helper_from_config(proxy_pool=None, proxy_modules=None, proxy_mode='single',
                                     proxy_http=None, proxy_https=None) -> ProxyHelper:
    """
    Create a ProxyHelper instance from configuration.
    
    Args:
        proxy_pool: Optional ProxyPool instance
        proxy_modules: List of module names that should use proxy
        proxy_mode: Proxy mode ('single' or 'pool')
        proxy_http: Legacy HTTP proxy URL
        proxy_https: Legacy HTTPS proxy URL
    
    Returns:
        Configured ProxyHelper instance
    """
    return ProxyHelper(
        proxy_pool=proxy_pool,
        proxy_modules=proxy_modules,
        proxy_mode=proxy_mode,
        proxy_http=proxy_http,
        proxy_https=proxy_https
    )

