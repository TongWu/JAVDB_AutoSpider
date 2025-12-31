#!/usr/bin/env python3
"""
JavDB Auto Login Script

Automatically logs into JavDB and extracts the session cookie.
Updates config.py with the new session cookie.

Usage:
    python3 scripts/login.py
    
Requirements:
    - JAVDB_USERNAME and JAVDB_PASSWORD must be set in config.py
    - User will need to manually input the captcha code when prompted
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
from urllib.parse import urljoin

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

# OCR captcha solver (deprecated, using AI instead)
# try:
#     from utils.login.javdb_captcha_solver import solve_captcha
#     CAPTCHA_SOLVER_AVAILABLE = True
# except ImportError:
#     CAPTCHA_SOLVER_AVAILABLE = False
#     print("⚠️  Warning: javdb_captcha_solver.py not found, will use manual input only")

# Import configuration
try:
    from config import JAVDB_USERNAME, JAVDB_PASSWORD, BASE_URL
except ImportError:
    logger.error("Could not import config.py")
    logger.error("Make sure config.py exists and contains JAVDB_USERNAME and JAVDB_PASSWORD")
    sys.exit(1)

# 2Captcha API key (deprecated, using AI instead)
# try:
#     from config import TWOCAPTCHA_API_KEY
# except ImportError:
#     TWOCAPTCHA_API_KEY = None

# Try to import GPT API settings (optional)
try:
    from config import GPT_API_KEY, GPT_API_URL
    GPT_API_AVAILABLE = bool(GPT_API_KEY and GPT_API_URL)
except ImportError:
    GPT_API_KEY = None
    GPT_API_URL = None
    GPT_API_AVAILABLE = False

# Try to import proxy settings (optional, for use when called from spider.py)
try:
    from config import PROXY_HTTP, PROXY_HTTPS
except ImportError:
    PROXY_HTTP = None
    PROXY_HTTPS = None


def extract_csrf_token(html_content):
    """Extract CSRF token from login page"""
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
        
        # Convert image to base64
        image_base64 = base64.b64encode(image_data).decode('utf-8')
        
        # Determine image type (assume PNG for captcha)
        image_type = "image/png"
        if image_data[:3] == b'\xff\xd8\xff':
            image_type = "image/jpeg"
        elif image_data[:4] == b'\x89PNG':
            image_type = "image/png"
        elif image_data[:4] == b'GIF8':
            image_type = "image/gif"
        
        # Prepare the API request
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
                        {
                            "type": "text",
                            "text": prompt
                        },
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
                captcha_code = result['choices'][0]['message']['content'].strip()
                # Clean up the response - remove any extra whitespace or newlines
                captcha_code = captcha_code.strip().lower()
                logger.info(f"AI recognized captcha: {captcha_code}")
                return captcha_code
            else:
                logger.warning("AI API returned empty response")
                return None
        else:
            logger.warning(f"AI API request failed (status: {response.status_code})")
            try:
                error_info = response.json()
                logger.warning(f"Error: {error_info.get('error', {}).get('message', 'Unknown error')}")
            except:
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
    Download captcha image and solve it using AI
    
    Args:
        captcha_url: URL of the captcha image
        session: requests.Session object
        headers: HTTP headers
        use_auto_solve: Whether to try automatic solving (AI)
        proxies: Optional proxy dict for requests (e.g., {'http': '...', 'https': '...'})
    
    Returns:
        str: Captcha code or None if failed
    """
    try:
        logger.info("Fetching captcha image...")
        captcha_response = session.get(captcha_url, headers=headers, timeout=30, proxies=proxies)
        
        if captcha_response.status_code == 200:
            logger.info("Captcha image downloaded")
            
            # Save captcha image first
            captcha_file = 'javdb_captcha.png'
            save_captcha_image(captcha_response.content, captcha_file)
            
            if use_auto_solve:
                # Use AI API to solve captcha
                if GPT_API_AVAILABLE:
                    captcha_code = solve_captcha_with_ai(captcha_response.content, proxies=proxies)
                    if captcha_code:
                        return captcha_code
                    logger.warning("AI captcha solving failed")
                    return None
                else:
                    logger.warning("GPT API not configured, cannot solve captcha")
                    return None
            
            # # Manual input as fallback (currently disabled)
            # print(f"✓ Captcha image saved to: {captcha_file}")
            # print(f"  Please open the image to view the captcha")
            # 
            # # Try to open the image automatically
            # try:
            #     import platform
            #     system = platform.system()
            #     if system == 'Darwin':  # macOS
            #         os.system(f'open {captcha_file}')
            #     elif system == 'Linux':
            #         os.system(f'xdg-open {captcha_file} 2>/dev/null')
            #     elif system == 'Windows':
            #         os.system(f'start {captcha_file}')
            # except:
            #     pass
            # 
            # print()
            # captcha_code = input("🔐 Please enter the captcha code: ").strip().lower()
            # return captcha_code if captcha_code else None
            return None
        else:
            logger.warning(f"Failed to fetch captcha (status: {captcha_response.status_code})")
            return None
            
    except Exception as e:
        logger.warning(f"Error processing captcha: {e}")
        return None


def login_javdb(username, password, proxies=None):
    """
    Login to JavDB and return session cookie
    
    Args:
        username: JavDB username/email
        password: JavDB password
        proxies: Optional proxy dict for requests (e.g., {'http': '...', 'https': '...'})
    
    Returns:
        tuple: (success: bool, session_cookie: str, message: str)
    """
    if not username or not password:
        return False, None, "Username or password not configured in config.py"
    
    # Log proxy usage
    if proxies:
        # Mask proxy URL for logging
        masked_proxies = {}
        for k, v in proxies.items():
            if v:
                # Simple masking: show protocol and port only
                import re
                match = re.match(r'(https?://)([^:]+):([^@]+)@([^:]+):(\d+)', v)
                if match:
                    masked_proxies[k] = f"{match.group(1)}***:***@***:{match.group(5)}"
                else:
                    match = re.match(r'(https?://)([^:]+):(\d+)', v)
                    if match:
                        masked_proxies[k] = f"{match.group(1)}***:{match.group(3)}"
                    else:
                        masked_proxies[k] = "***"
        logger.info(f"Using proxy for login: {masked_proxies}")
    
    # Create session
    session = requests.Session()
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }
    
    try:
        logger.info("Step 1: Fetching login page...")
        # Step 1: Get login page to extract CSRF token
        login_page_url = urljoin(BASE_URL, '/login')
        response = session.get(login_page_url, headers=headers, timeout=30, proxies=proxies)
        
        if response.status_code != 200:
            if response.status_code == 403:
                logger.error("HTTP 403 Forbidden - Access denied by JavDB")
                logger.error("This may indicate IP blocking. If using proxy, ensure 'spider' is in PROXY_MODULES config.")
                logger.error("Example: PROXY_MODULES = ['spider'] or PROXY_MODULES = ['all']")
            return False, None, f"Failed to fetch login page (status: {response.status_code})"
        
        logger.info(f"Login page fetched (status: {response.status_code})")
        
        # Extract CSRF token
        csrf_token = extract_csrf_token(response.text)
        if not csrf_token:
            logger.warning("Could not extract CSRF token, proceeding without it...")
        else:
            logger.info(f"CSRF token extracted: {csrf_token[:20]}...")
        
        # Step 2: Handle age verification if present
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
                        # Re-fetch login page
                        response = session.get(login_page_url, headers=headers, timeout=30, proxies=proxies)
                        # Re-extract CSRF token
                        csrf_token = extract_csrf_token(response.text)
                    break
        
        # Step 2.5: Extract and handle captcha
        logger.info("Step 2: Checking for captcha...")
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find captcha image - common patterns
        captcha_img = None
        captcha_url = None
        
        # Try different selectors for captcha
        captcha_img = (soup.find('img', id='captcha') or 
                      soup.find('img', class_='captcha') or
                      soup.find('img', attrs={'alt': 'captcha'}) or
                      soup.find('img', src=re.compile(r'captcha', re.I)))
        
        if captcha_img and captcha_img.get('src'):
            captcha_url = urljoin(BASE_URL, captcha_img.get('src'))
            logger.info(f"Captcha detected: {captcha_url}")
            
            # Get captcha input from user
            captcha_code = get_captcha_from_user(captcha_url, session, headers, proxies=proxies)
            if not captcha_code:
                return False, None, "Failed to get captcha code from user"
        else:
            logger.info("No captcha detected (or could not find captcha image)")
            captcha_code = None
        
        logger.info("Step 3: Submitting login credentials...")
        # Step 3: Submit login form
        # Actual JavDB login endpoint (found via debug script)
        login_url = urljoin(BASE_URL, '/user_sessions')
        
        # Prepare login data (exact field names from JavDB form)
        login_data = {
            'email': username,
            'password': password,
            'remember': '1',  # Remember me checkbox
            'commit': '登入'
        }
        
        # Add captcha (IMPORTANT: field name is _rucaptcha, not captcha)
        if captcha_code:
            login_data['_rucaptcha'] = captcha_code
        else:
            return False, None, "Captcha code is required but not provided"
        
        # Add CSRF token
        if csrf_token:
            login_data['authenticity_token'] = csrf_token
        
        # Update headers for form submission
        headers.update({
            'Content-Type': 'application/x-www-form-urlencoded',
            'Origin': BASE_URL,
            'Referer': login_page_url,
        })
        
        logger.info("Submitting to: /user_sessions")
        
        # Submit login
        login_response = session.post(login_url, data=login_data, headers=headers, 
                                     timeout=30, allow_redirects=True, proxies=proxies)
        
        logger.info(f"Login request submitted (status: {login_response.status_code})")
        
        # Check if login was successful
        logger.info(f"Final URL: {login_response.url}")
        
        # Check response
        if login_response.status_code == 200:
            # Check if we're back on login page (failed) or home page (success)
            if '/login' in login_response.url or 'user_sessions' in login_response.url:
                # Still on login/session page - login failed
                soup = BeautifulSoup(login_response.text, 'html.parser')
                
                # Check for error messages
                error_div = (soup.find('div', class_='alert-danger') or 
                           soup.find('div', class_='alert-error') or
                           soup.find('div', class_='error') or
                           soup.find('div', class_='notice') or
                           soup.find('p', class_='error-message'))
                if error_div:
                    error_msg = error_div.get_text(strip=True)
                    return False, None, f"Login failed: {error_msg}"
                
                # Check for captcha error (verification code incorrect)
                if '验证码' in login_response.text or '驗證碼' in login_response.text:
                    return False, None, "Login failed: Incorrect captcha code (验证码错误). Please try again."
                
                # Check for password error
                if '密码' in login_response.text or '密碼' in login_response.text:
                    if '错误' in login_response.text or '錯誤' in login_response.text:
                        return False, None, "Login failed: Incorrect username or password."
                
                return False, None, "Login failed: Still on login page. Check username/password and captcha."
            else:
                # Redirected away from login page - success
                logger.info("Login successful (redirected away from login page)")
        elif login_response.status_code == 302 or login_response.status_code == 303:
            logger.info("Login successful (got redirect)")
        else:
            if login_response.status_code == 403:
                logger.error("HTTP 403 Forbidden - Access denied by JavDB during login")
                logger.error("This may indicate IP blocking. If using proxy, ensure 'spider' is in PROXY_MODULES config.")
                logger.error("Example: PROXY_MODULES = ['spider'] or PROXY_MODULES = ['all']")
            return False, None, f"Login failed: Unexpected status code {login_response.status_code}"
        
        # Step 4: Extract session cookie
        logger.info("Step 4: Extracting session cookie...")
        session_cookie = None
        for cookie in session.cookies:
            if cookie.name == '_jdb_session':
                session_cookie = cookie.value
                break
        
        if not session_cookie:
            return False, None, "Login might have succeeded, but could not extract session cookie"
        
        logger.info(f"Session cookie extracted: {session_cookie[:10]}***{session_cookie[-10:]}")
        
        # Verify cookie works
        logger.info("Step 5: Verifying session cookie...")
        test_url = urljoin(BASE_URL, '/')
        test_headers = {
            'User-Agent': headers['User-Agent'],
            'Cookie': f'_jdb_session={session_cookie}'
        }
        test_response = requests.get(test_url, headers=test_headers, timeout=30, proxies=proxies)
        
        if test_response.status_code == 200:
            # Check if we're logged in by looking for logout link or user menu
            soup = BeautifulSoup(test_response.text, 'html.parser')
            # Look for user-related elements (adjust selector based on JavDB's HTML)
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
            # Check if it's a captcha-related error (worth retrying)
            is_captcha_error = any(keyword in message.lower() for keyword in [
                'captcha', '验证码', '驗證碼', 'verification'
            ])
            
            if attempt < max_retries:
                if is_captcha_error:
                    logger.warning("Captcha error detected, retrying with new captcha...")
                else:
                    logger.warning(f"Login failed: {message}")
                    logger.info("Retrying...")
                # Small delay before retry
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
        # Read current config
        with open(config_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Find and replace JAVDB_SESSION_COOKIE
        # Pattern to match the cookie assignment
        pattern = r"(JAVDB_SESSION_COOKIE\s*=\s*['\"])([^'\"]*?)(['\"])"
        
        # Check if pattern exists
        if not re.search(pattern, content):
            logger.error("Could not find JAVDB_SESSION_COOKIE in config.py")
            return False
        
        # Replace with new cookie
        new_content = re.sub(pattern, rf"\g<1>{session_cookie}\g<3>", content)
        
        # Write back to file
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
    logger.info("JavDB Auto Login Script (with Captcha Support)")
    logger.info("=" * 60)
    
    # Check credentials
    if not JAVDB_USERNAME or not JAVDB_PASSWORD:
        logger.error("JAVDB_USERNAME and JAVDB_PASSWORD must be set in config.py")
        logger.info("To use this script:")
        logger.info("1. Open config.py")
        logger.info("2. Set JAVDB_USERNAME = 'your_email_or_username'")
        logger.info("3. Set JAVDB_PASSWORD = 'your_password'")
        logger.info("4. Run: python3 scripts/login.py")
        logger.info("5. Enter captcha code when prompted")
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
    
    # Perform login with retry logic
    success, session_cookie, message = login_with_retry(JAVDB_USERNAME, JAVDB_PASSWORD, max_retries=5)
    
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
            
            # Cleanup captcha image
            try:
                if os.path.exists('javdb_captcha.png'):
                    os.remove('javdb_captcha.png')
                    logger.info("Cleaned up captcha image file")
            except:
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
        sys.exit(1)


if __name__ == '__main__':
    main()

