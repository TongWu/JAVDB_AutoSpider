#!/usr/bin/env python3
"""
JavDB Auto Login Script

Automatically logs into JavDB and extracts the session cookie.
Updates config.py with the new session cookie.

Uses RequestHandler with curl_cffi for Cloudflare bypass via TLS fingerprint
impersonation. Falls back to CF bypass service warming if direct requests
are blocked by Cloudflare Turnstile.

Usage:
    python3 scripts/login.py
    
Requirements:
    - JAVDB_USERNAME and JAVDB_PASSWORD must be set in config.py
"""

import requests
import re
import sys
import os
import base64
import json
import time
import logging
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote

# Change to project root directory (parent of scripts folder)
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(project_root)
sys.path.insert(0, project_root)

# Setup logging - use existing logger if available, otherwise create a basic one
try:
    from utils.logging_config import get_logger
    logger = get_logger(__name__)
except ImportError:
    # Fallback: create a basic logger for standalone use
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger(__name__)

# Import configuration
try:
    from config import JAVDB_USERNAME, JAVDB_PASSWORD, BASE_URL
except ImportError:
    logger.error("Could not import config.py")
    logger.error("Make sure config.py exists and contains JAVDB_USERNAME and JAVDB_PASSWORD")
    sys.exit(1)

# Try to import GPT API settings (optional)
try:
    from config import GPT_API_KEY, GPT_API_URL
    GPT_API_AVAILABLE = bool(GPT_API_KEY and GPT_API_URL)
except ImportError:
    GPT_API_KEY = None
    GPT_API_URL = None
    GPT_API_AVAILABLE = False

# Try to import proxy settings (optional)
try:
    from config import PROXY_HTTP, PROXY_HTTPS
except ImportError:
    PROXY_HTTP = None
    PROXY_HTTPS = None

# Try to import CF bypass and proxy pool settings (optional)
try:
    from config import CF_BYPASS_SERVICE_PORT, CF_BYPASS_ENABLED
except ImportError:
    CF_BYPASS_SERVICE_PORT = 8000
    CF_BYPASS_ENABLED = True

try:
    from config import PROXY_MODE, PROXY_MODULES
except ImportError:
    PROXY_MODE = 'single'
    PROXY_MODULES = ['all']

try:
    from config import CF_TURNSTILE_COOLDOWN, FALLBACK_COOLDOWN
except ImportError:
    CF_TURNSTILE_COOLDOWN = 10
    FALLBACK_COOLDOWN = 30

# Import RequestHandler for Cloudflare bypass via curl_cffi TLS fingerprint
from utils.request_handler import RequestHandler, RequestConfig


# ---------------------------------------------------------------------------
# Cloudflare detection & bypass helpers
# ---------------------------------------------------------------------------

def _is_cloudflare_challenge(response):
    """
    Check if an HTTP response is a Cloudflare challenge page.
    
    Detects both Turnstile interactive challenges and 403 blocks.
    """
    if response.status_code == 403:
        server = ''
        if hasattr(response, 'headers'):
            server = response.headers.get('server', '').lower()
        if 'cloudflare' in server:
            return True
        text = getattr(response, 'text', '')
        if any(kw in text.lower() for kw in ['cloudflare', 'cf-browser-verification', 'turnstile']):
            return True

    text = getattr(response, 'text', '')
    if 'Security Verification' in text and 'turnstile' in text.lower():
        return True
    return False


def _create_handler():
    """Create a RequestHandler instance configured for login."""
    config = RequestConfig(
        base_url=BASE_URL,
        cf_bypass_service_port=CF_BYPASS_SERVICE_PORT,
        cf_bypass_enabled=CF_BYPASS_ENABLED,
        cf_turnstile_cooldown=CF_TURNSTILE_COOLDOWN,
        fallback_cooldown=FALLBACK_COOLDOWN,
        proxy_http=PROXY_HTTP,
        proxy_https=PROXY_HTTPS,
        proxy_mode=PROXY_MODE,
        proxy_modules=PROXY_MODULES,
    )
    return RequestHandler(config=config)


def _attempt_cf_warmup(handler, url, proxies=None):
    """
    Use CF bypass service to solve the Cloudflare challenge for the current IP.
    
    After the bypass service solves Turnstile, subsequent requests from the
    same IP may pass Cloudflare without an interactive challenge.
    """
    if not handler.config.cf_bypass_enabled:
        logger.info("CF bypass service is disabled in config, skipping warmup")
        return False

    proxy_ip = None
    if proxies:
        proxy_url = proxies.get('https') or proxies.get('http')
        if proxy_url:
            proxy_ip = RequestHandler.extract_ip_from_proxy_url(proxy_url)

    bypass_base_url = handler.get_cf_bypass_service_url(proxy_ip)
    encoded_url = quote(url, safe='')
    bypass_url = f"{bypass_base_url}/html?url={encoded_url}"

    try:
        logger.info("Attempting CF bypass warmup via bypass service...")
        warmup_resp = requests.get(bypass_url, timeout=120)
        if warmup_resp.status_code == 200 and len(warmup_resp.content) > 1000:
            logger.info(f"CF bypass warmup successful ({len(warmup_resp.content)} bytes)")
            return True
        logger.warning(f"CF bypass warmup returned insufficient response "
                       f"(status={warmup_resp.status_code}, size={len(warmup_resp.content)})")
        return False
    except Exception as e:
        logger.warning(f"CF bypass warmup failed: {e}")
        return False


def _build_proxies_from_config():
    """Build a proxies dict from config settings for standalone usage."""
    if PROXY_HTTP or PROXY_HTTPS:
        proxies = {}
        if PROXY_HTTP:
            proxies['http'] = PROXY_HTTP
        if PROXY_HTTPS:
            proxies['https'] = PROXY_HTTPS
        return proxies

    try:
        from config import PROXY_POOL as _pool
        if _pool and len(_pool) > 0:
            first = _pool[0]
            proxies = {}
            if first.get('http'):
                proxies['http'] = first['http']
            if first.get('https'):
                proxies['https'] = first['https']
            if proxies:
                logger.info(f"Using first proxy from pool: {first.get('name', 'unnamed')}")
                return proxies
    except ImportError:
        pass

    return None


# ---------------------------------------------------------------------------
# CSRF / captcha helpers
# ---------------------------------------------------------------------------

def extract_csrf_token(html_content):
    """Extract CSRF token from login page"""
    soup = BeautifulSoup(html_content, 'html.parser')

    csrf_meta = soup.find('meta', attrs={'name': 'csrf-token'})
    if csrf_meta and csrf_meta.get('content'):
        return csrf_meta.get('content')

    csrf_input = soup.find('input', attrs={'name': 'authenticity_token'})
    if csrf_input and csrf_input.get('value'):
        return csrf_input.get('value')

    return None


def save_captcha_image(image_data, filename='captcha.png'):
    """Save captcha image to file"""
    try:
        with open(filename, 'wb') as f:
            f.write(image_data)
        return True
    except Exception as e:
        logger.warning(f"Could not save captcha image: {e}")
        return False


def solve_captcha_with_ai(image_data, proxies=None):
    """
    Use GPT-4o API to solve captcha
    
    Args:
        image_data: Raw image bytes
        proxies: Optional proxy dict for requests (e.g., {'http': '...', 'https': '...'})
    
    Returns:
        str: Captcha code or None if failed
    """
    if not GPT_API_AVAILABLE:
        logger.warning("GPT API not configured, skipping AI captcha solving")
        return None

    try:
        logger.info("Calling AI API to solve captcha...")

        image_base64 = base64.b64encode(image_data).decode('utf-8')

        image_type = "image/png"
        if image_data[:3] == b'\xff\xd8\xff':
            image_type = "image/jpeg"
        elif image_data[:4] == b'\x89PNG':
            image_type = "image/png"
        elif image_data[:4] == b'GIF8':
            image_type = "image/gif"

        prompt = (
            "Analyze the provided image and return the characters displayed. "
            "Output the result as plain text only. "
            "Do not describe the image, do not explain your reasoning, "
            "and do not include anything except the recognized characters."
        )

        payload = {
            "model": "gpt-5-chat-latest",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{image_type};base64,{image_base64}"
                            }
                        }
                    ]
                }
            ],
            "max_tokens": 50
        }

        headers = {
            'Authorization': f'Bearer {GPT_API_KEY}',
            'Content-Type': 'application/json'
        }

        response = requests.post(
            GPT_API_URL,
            headers=headers,
            data=json.dumps(payload),
            timeout=60,
            proxies=proxies
        )

        if response.status_code == 200:
            result = response.json()
            if 'choices' in result and len(result['choices']) > 0:
                captcha_code = result['choices'][0]['message']['content'].strip().lower()
                logger.info(f"AI recognized captcha: {captcha_code}")
                return captcha_code
            logger.warning("AI API returned empty response")
            return None
        else:
            logger.warning(f"AI API request failed (status: {response.status_code})")
            try:
                error_info = response.json()
                logger.warning(f"Error: {error_info.get('error', {}).get('message', 'Unknown error')}")
            except Exception:
                pass
            return None

    except requests.Timeout:
        logger.warning("AI API request timed out")
        return None
    except Exception as e:
        logger.warning(f"Error calling AI API: {e}")
        return None


def get_captcha_from_user(captcha_url, session, headers, use_auto_solve=True, proxies=None):
    """
    Download captcha image and solve it using AI.
    
    Works with both ``requests.Session`` and ``curl_cffi.requests.Session``.
    """
    try:
        logger.info("Fetching captcha image...")
        captcha_response = session.get(captcha_url, headers=headers, timeout=30, proxies=proxies)

        if captcha_response.status_code == 200:
            logger.info("Captcha image downloaded")

            captcha_file = 'javdb_captcha.png'
            save_captcha_image(captcha_response.content, captcha_file)

            if use_auto_solve:
                if GPT_API_AVAILABLE:
                    captcha_code = solve_captcha_with_ai(captcha_response.content, proxies=proxies)
                    if captcha_code:
                        return captcha_code
                    logger.warning("AI captcha solving failed")
                    return None
                else:
                    logger.warning("GPT API not configured, cannot solve captcha")
                    return None

            return None
        else:
            logger.warning(f"Failed to fetch captcha (status: {captcha_response.status_code})")
            return None

    except Exception as e:
        logger.warning(f"Error processing captcha: {e}")
        return None


# ---------------------------------------------------------------------------
# Core login logic
# ---------------------------------------------------------------------------

def _extract_session_cookie(session, is_curl_cffi):
    """Extract ``_jdb_session`` cookie from session, handling both session types."""
    if is_curl_cffi:
        return session.cookies.get('_jdb_session')
    for cookie in session.cookies:
        if cookie.name == '_jdb_session':
            return cookie.value
    return None


def login_javdb(username, password, proxies=None):
    """
    Login to JavDB and return session cookie.
    
    Uses RequestHandler's curl_cffi session for browser-like TLS fingerprint
    which helps bypass Cloudflare bot detection. If Cloudflare still blocks
    (Turnstile challenge), attempts a CF bypass service warmup then retries.
    
    Args:
        username: JavDB username/email
        password: JavDB password
        proxies: Optional proxy dict for requests (e.g., {'http': '...', 'https': '...'})
    
    Returns:
        tuple: (success: bool, session_cookie: str, message: str)
    """
    if not username or not password:
        return False, None, "Username or password not configured in config.py"

    # Log proxy usage (masked)
    if proxies:
        masked_proxies = {}
        for k, v in proxies.items():
            if v:
                match = re.match(r'(https?://)([^:]+):([^@]+)@([^:]+):(\d+)', v)
                if match:
                    masked_proxies[k] = f"{match.group(1)}***:***@***:{match.group(5)}"
                else:
                    match2 = re.match(r'(https?://)([^:]+):(\d+)', v)
                    if match2:
                        masked_proxies[k] = f"{match2.group(1)}***:{match2.group(3)}"
                    else:
                        masked_proxies[k] = "***"
        logger.info(f"Using proxy for login: {masked_proxies}")

    # --- Initialise RequestHandler (curl_cffi + CF bypass) ---
    handler = _create_handler()

    is_curl_cffi = handler.use_curl_cffi and handler.curl_cffi_session is not None
    if is_curl_cffi:
        session = handler.curl_cffi_session
        logger.info(f"Using curl_cffi session (impersonate={handler.config.curl_cffi_impersonate}) for Cloudflare bypass")
    else:
        session = requests.Session()
        logger.info("Using standard requests session (curl_cffi not available)")

    headers = handler.BROWSER_HEADERS.copy()

    try:
        # ==================================================================
        # Step 1: Fetch login page
        # ==================================================================
        logger.info("Step 1: Fetching login page...")
        login_page_url = urljoin(BASE_URL, '/login')

        response = session.get(login_page_url, headers=headers, timeout=30, proxies=proxies)

        # --- Cloudflare challenge handling ---
        if _is_cloudflare_challenge(response):
            logger.warning(f"Cloudflare challenge detected on login page (HTTP {response.status_code})")

            # Attempt 1: CF bypass service warmup
            if _attempt_cf_warmup(handler, login_page_url, proxies):
                logger.info("CF warmup succeeded, retrying login page fetch after cooldown...")
                time.sleep(5)
                response = session.get(login_page_url, headers=headers, timeout=30, proxies=proxies)

                if _is_cloudflare_challenge(response):
                    # Attempt 2: refresh bypass cache and retry once more
                    logger.warning("Still blocked after warmup, refreshing bypass cache...")
                    handler._refresh_bypass_cache(
                        login_page_url,
                        proxies,
                        force_local=(proxies is None),
                    )
                    time.sleep(5)
                    response = session.get(login_page_url, headers=headers, timeout=30, proxies=proxies)

                    if _is_cloudflare_challenge(response):
                        return False, None, (
                            f"Blocked by Cloudflare (HTTP {response.status_code}). "
                            "CF bypass warmup and cache refresh both failed."
                        )
            else:
                return False, None, (
                    f"Cloudflare challenge detected (HTTP {response.status_code}). "
                    "CF bypass warmup failed or service unavailable."
                )

        if response.status_code != 200:
            if response.status_code == 403:
                logger.error("HTTP 403 Forbidden - Access denied by JavDB")
                logger.error("This may indicate IP blocking. If using proxy, ensure 'spider' is in PROXY_MODULES config.")
            return False, None, f"Failed to fetch login page (status: {response.status_code})"

        logger.info(f"Login page fetched (status: {response.status_code}, size: {len(response.text)} bytes)")

        # Extract CSRF token
        csrf_token = extract_csrf_token(response.text)
        if csrf_token:
            logger.info(f"CSRF token extracted: {csrf_token[:20]}...")
        else:
            logger.warning("Could not extract CSRF token, proceeding without it...")

        # ==================================================================
        # Step 2: Handle age verification if present
        # ==================================================================
        soup = BeautifulSoup(response.text, 'html.parser')
        age_modal = soup.find('div', class_='modal is-active over18-modal')

        if age_modal:
            logger.info("Step 1.5: Age verification detected, bypassing...")
            age_links = age_modal.find_all('a', href=True)
            for link in age_links:
                if 'over18' in link.get('href', ''):
                    age_url = urljoin(BASE_URL, link.get('href'))
                    age_response = session.get(age_url, headers=headers, timeout=30, proxies=proxies)
                    if age_response.status_code == 200:
                        logger.info("Age verification bypassed")
                        response = session.get(login_page_url, headers=headers, timeout=30, proxies=proxies)
                        csrf_token = extract_csrf_token(response.text)
                    break

        # ==================================================================
        # Step 2.5: Handle captcha
        # ==================================================================
        logger.info("Step 2: Checking for captcha...")
        soup = BeautifulSoup(response.text, 'html.parser')

        captcha_img = (soup.find('img', id='captcha') or
                       soup.find('img', class_='captcha') or
                       soup.find('img', attrs={'alt': 'captcha'}) or
                       soup.find('img', src=re.compile(r'captcha', re.I)))

        if captcha_img and captcha_img.get('src'):
            captcha_url = urljoin(BASE_URL, captcha_img.get('src'))
            logger.info(f"Captcha detected: {captcha_url}")

            captcha_code = get_captcha_from_user(captcha_url, session, headers, proxies=proxies)
            if not captcha_code:
                return False, None, "Failed to get captcha code from user"
        else:
            logger.info("No captcha detected (or could not find captcha image)")
            captcha_code = None

        # ==================================================================
        # Step 3: Submit login form
        # ==================================================================
        logger.info("Step 3: Submitting login credentials...")
        login_url = urljoin(BASE_URL, '/user_sessions')

        login_data = {
            'email': username,
            'password': password,
            'remember': '1',
            'commit': '登入'
        }

        if captcha_code:
            login_data['_rucaptcha'] = captcha_code
        else:
            return False, None, "Captcha code is required but not provided"

        if csrf_token:
            login_data['authenticity_token'] = csrf_token

        post_headers = headers.copy()
        post_headers.update({
            'Content-Type': 'application/x-www-form-urlencoded',
            'Origin': BASE_URL,
            'Referer': login_page_url,
        })

        logger.info("Submitting to: /user_sessions")

        login_response = session.post(login_url, data=login_data, headers=post_headers,
                                      timeout=30, allow_redirects=True, proxies=proxies)

        # Check for Cloudflare on POST response
        if _is_cloudflare_challenge(login_response):
            return False, None, (
                f"Cloudflare challenge detected during login POST (HTTP {login_response.status_code}). "
                "The session may have expired or IP was flagged."
            )

        logger.info(f"Login request submitted (status: {login_response.status_code})")
        logger.info(f"Final URL: {login_response.url}")

        # ==================================================================
        # Check login result
        # ==================================================================
        if login_response.status_code == 200:
            if '/login' in login_response.url or 'user_sessions' in login_response.url:
                soup = BeautifulSoup(login_response.text, 'html.parser')

                error_div = (soup.find('div', class_='alert-danger') or
                             soup.find('div', class_='alert-error') or
                             soup.find('div', class_='error') or
                             soup.find('div', class_='notice') or
                             soup.find('p', class_='error-message'))
                if error_div:
                    error_msg = error_div.get_text(strip=True)
                    return False, None, f"Login failed: {error_msg}"

                if '验证码' in login_response.text or '驗證碼' in login_response.text:
                    return False, None, "Login failed: Incorrect captcha code (验证码错误). Please try again."

                if '密码' in login_response.text or '密碼' in login_response.text:
                    if '错误' in login_response.text or '錯誤' in login_response.text:
                        return False, None, "Login failed: Incorrect username or password."

                return False, None, "Login failed: Still on login page. Check username/password and captcha."
            else:
                logger.info("Login successful (redirected away from login page)")
        elif login_response.status_code in (302, 303):
            logger.info("Login successful (got redirect)")
        else:
            if login_response.status_code == 403:
                logger.error("HTTP 403 Forbidden - Access denied by JavDB during login")
                logger.error("This may indicate IP blocking. If using proxy, ensure 'spider' is in PROXY_MODULES config.")
            return False, None, f"Login failed: Unexpected status code {login_response.status_code}"

        # ==================================================================
        # Step 4: Extract session cookie
        # ==================================================================
        logger.info("Step 4: Extracting session cookie...")
        session_cookie = _extract_session_cookie(session, is_curl_cffi)

        if not session_cookie:
            return False, None, "Login might have succeeded, but could not extract session cookie"

        logger.info(f"Session cookie extracted: {session_cookie[:10]}***{session_cookie[-10:]}")

        # ==================================================================
        # Step 5: Verify session cookie
        # ==================================================================
        logger.info("Step 5: Verifying session cookie...")
        test_url = urljoin(BASE_URL, '/')
        test_headers = headers.copy()
        test_headers['Cookie'] = f'_jdb_session={session_cookie}'

        test_response = session.get(test_url, headers=test_headers, timeout=30, proxies=proxies)

        if test_response.status_code == 200:
            soup = BeautifulSoup(test_response.text, 'html.parser')
            user_menu = soup.find('a', href='/users/edit') or soup.find('a', href='/logout')
            if user_menu:
                logger.info("Session cookie verified (user logged in)")
            else:
                logger.warning("Could not verify login status, but cookie was extracted")

        return True, session_cookie, "Login successful"

    except requests.RequestException as e:
        return False, None, f"Network error: {e}"
    except Exception as e:
        return False, None, f"Unexpected error: {e}"


def login_with_retry(username, password, max_retries=5, proxies=None):
    """
    Login to JavDB with retry logic for captcha failures.
    
    This function wraps login_javdb() with retry logic, automatically
    retrying on captcha-related errors.
    
    Args:
        username: JavDB username/email
        password: JavDB password
        max_retries: Maximum number of retry attempts (default: 5)
        proxies: Optional proxy dict for requests (e.g., {'http': '...', 'https': '...'})
    
    Returns:
        tuple: (success: bool, session_cookie: str, message: str)
    """
    success = False
    session_cookie = None
    message = None

    for attempt in range(1, max_retries + 1):
        logger.info(f"Login attempt {attempt}/{max_retries}")

        success, session_cookie, message = login_javdb(username, password, proxies=proxies)

        if success:
            break
        else:
            is_captcha_error = any(keyword in message.lower() for keyword in [
                'captcha', '验证码', '驗證碼', 'verification'
            ])
            is_cf_error = any(keyword in message.lower() for keyword in [
                'cloudflare', 'cf bypass', 'turnstile'
            ])

            if attempt < max_retries:
                if is_captcha_error:
                    logger.warning("Captcha error detected, retrying with new captcha...")
                elif is_cf_error:
                    logger.warning("Cloudflare error detected, retrying after cooldown...")
                    time.sleep(CF_TURNSTILE_COOLDOWN)
                else:
                    logger.warning(f"Login failed: {message}")
                    logger.info("Retrying...")
                time.sleep(2)
            else:
                logger.error(f"All {max_retries} attempts failed")

    return success, session_cookie, message


def update_config_file(session_cookie):
    """
    Update JAVDB_SESSION_COOKIE in config.py
    
    Args:
        session_cookie: New session cookie value
    
    Returns:
        bool: True if successful, False otherwise
    """
    config_path = 'config.py'

    if not os.path.exists(config_path):
        logger.error(f"{config_path} not found")
        return False

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            content = f.read()

        pattern = r"(JAVDB_SESSION_COOKIE\s*=\s*['\"])([^'\"]*?)(['\"])"

        if not re.search(pattern, content):
            logger.error("Could not find JAVDB_SESSION_COOKIE in config.py")
            return False

        new_content = re.sub(pattern, rf"\g<1>{session_cookie}\g<3>", content)

        with open(config_path, 'w', encoding='utf-8') as f:
            f.write(new_content)

        logger.info(f"Updated {config_path} with new session cookie")
        return True

    except Exception as e:
        logger.error(f"Error updating config.py: {e}")
        return False


def main():
    """Main function"""
    logger.info("=" * 60)
    logger.info("JavDB Auto Login Script (with Captcha + Cloudflare Bypass)")
    logger.info("=" * 60)

    # Check credentials
    if not JAVDB_USERNAME or not JAVDB_PASSWORD:
        logger.error("JAVDB_USERNAME and JAVDB_PASSWORD must be set in config.py")
        logger.info("To use this script:")
        logger.info("1. Open config.py")
        logger.info("2. Set JAVDB_USERNAME = 'your_email_or_username'")
        logger.info("3. Set JAVDB_PASSWORD = 'your_password'")
        logger.info("4. Run: python3 scripts/login.py")
        sys.exit(1)

    # Mask username for privacy
    masked_username = JAVDB_USERNAME[:3] + "***" + JAVDB_USERNAME[-3:] if len(JAVDB_USERNAME) > 6 else JAVDB_USERNAME[:2] + "***"
    logger.info(f"Username: {masked_username}")
    logger.info(f"Base URL: {BASE_URL}")

    # Show captcha solving method
    if GPT_API_AVAILABLE:
        logger.info("Captcha Solving: AI (GPT Vision)")
        logger.info("   - Using AI API for automatic recognition")
    else:
        logger.warning("Captcha Solving: NOT AVAILABLE")
        logger.warning("   - Configure GPT_API_KEY and GPT_API_URL in config.py for AI solving")

    # Show Cloudflare bypass method
    logger.info(f"Cloudflare Bypass: curl_cffi TLS fingerprint + CF bypass service (port {CF_BYPASS_SERVICE_PORT})")
    logger.info(f"CF Bypass Enabled: {CF_BYPASS_ENABLED}")

    # Build proxy config for standalone usage
    proxies = _build_proxies_from_config()
    if proxies:
        logger.info(f"Proxy: configured ({len(proxies)} endpoint(s))")
    else:
        logger.info("Proxy: not configured (direct connection)")

    # Perform login with retry logic
    success, session_cookie, message = login_with_retry(
        JAVDB_USERNAME, JAVDB_PASSWORD, max_retries=5, proxies=proxies
    )

    logger.info("=" * 60)
    if success:
        logger.info("LOGIN SUCCESSFUL")
        logger.info("=" * 60)
        logger.info(f"Session Cookie: {session_cookie[:10]}***{session_cookie[-10:]}")

        # Update config.py
        logger.info("Updating config.py...")
        if update_config_file(session_cookie):
            logger.info("=" * 60)
            logger.info("ALL DONE!")
            logger.info("=" * 60)
            logger.info("The new session cookie has been saved to config.py")
            logger.info("You can now use the spider with --url parameter:")
            logger.info("  python3 scripts/spider.py --url https://javdb.com/actors/...")

            try:
                if os.path.exists('javdb_captcha.png'):
                    os.remove('javdb_captcha.png')
                    logger.info("Cleaned up captcha image file")
            except Exception:
                pass
        else:
            logger.warning("Login successful but failed to update config.py")
            logger.warning(f"Please manually update JAVDB_SESSION_COOKIE in config.py with:")
            logger.warning(f"  {session_cookie[:10]}***{session_cookie[-10:]}")
    else:
        logger.error("LOGIN FAILED")
        logger.info("=" * 60)
        logger.error(f"Error: {message}")
        logger.info("Troubleshooting:")
        logger.info("1. Check your username and password in config.py")
        logger.info("2. Make sure you can login via web browser")
        logger.info("3. AI may have recognized the captcha incorrectly")
        logger.info("4. Check if JavDB changed their login form")
        logger.info("5. Check Cloudflare bypass status - ensure CF bypass service is running")
        logger.info("6. Try with proxy if direct connection is blocked")
        sys.exit(1)


if __name__ == '__main__':
    main()
