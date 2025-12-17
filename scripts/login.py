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
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# Change to project root directory (parent of scripts folder)
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(project_root)
sys.path.insert(0, project_root)

# Import captcha solver
try:
    from utils.login.javdb_captcha_solver import solve_captcha
    CAPTCHA_SOLVER_AVAILABLE = True
except ImportError:
    CAPTCHA_SOLVER_AVAILABLE = False
    print("⚠️  Warning: javdb_captcha_solver.py not found, will use manual input only")

# Import configuration
try:
    from config import JAVDB_USERNAME, JAVDB_PASSWORD, BASE_URL
except ImportError:
    print("❌ Error: Could not import config.py")
    print("   Make sure config.py exists and contains JAVDB_USERNAME and JAVDB_PASSWORD")
    sys.exit(1)

# Try to import 2Captcha API key (optional)
try:
    from config import TWOCAPTCHA_API_KEY
except ImportError:
    TWOCAPTCHA_API_KEY = None


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
        print(f"⚠️  Warning: Could not save captcha image: {e}")
        return False


def get_captcha_from_user(captcha_url, session, headers, use_auto_solve=True):
    """
    Download captcha image and solve it (automatically or manually)
    
    Args:
        captcha_url: URL of the captcha image
        session: requests.Session object
        headers: HTTP headers
        use_auto_solve: Whether to try automatic solving (OCR/2Captcha)
    
    Returns:
        str: Captcha code or None if failed
    """
    try:
        print()
        print("🔐 Fetching captcha image...")
        captcha_response = session.get(captcha_url, headers=headers, timeout=30)
        
        if captcha_response.status_code == 200:
            print("✓ Captcha image downloaded")
            
            if use_auto_solve and CAPTCHA_SOLVER_AVAILABLE:
                # Try automatic solving
                captcha_code = solve_captcha(
                    captcha_response.content, 
                    method='auto',
                    api_key=TWOCAPTCHA_API_KEY,
                    save_path='javdb_captcha.png',
                    auto_confirm=True,  # Auto-accept if confidence > 60%
                    confidence_threshold=0.6
                )
                
                if captcha_code:
                    return captcha_code
                else:
                    print("⚠️  Automatic solving failed")
                    return None
            else:
                # Manual input only
                captcha_file = 'javdb_captcha.png'
                if save_captcha_image(captcha_response.content, captcha_file):
                    print(f"✓ Captcha image saved to: {captcha_file}")
                    print(f"  Please open the image to view the captcha")
                    
                    # Try to open the image automatically
                    try:
                        import platform
                        system = platform.system()
                        if system == 'Darwin':  # macOS
                            os.system(f'open {captcha_file}')
                        elif system == 'Linux':
                            os.system(f'xdg-open {captcha_file} 2>/dev/null')
                        elif system == 'Windows':
                            os.system(f'start {captcha_file}')
                    except:
                        pass
                    
                    print()
                    captcha_code = input("🔐 Please enter the captcha code: ").strip().lower()
                    return captcha_code if captcha_code else None
                else:
                    print("⚠️  Could not save captcha image")
                    return None
        else:
            print(f"⚠️  Failed to fetch captcha (status: {captcha_response.status_code})")
            return None
            
    except Exception as e:
        print(f"⚠️  Error processing captcha: {e}")
        return None


def login_javdb(username, password):
    """
    Login to JavDB and return session cookie
    
    Returns:
        tuple: (success: bool, session_cookie: str, message: str)
    """
    if not username or not password:
        return False, None, "Username or password not configured in config.py"
    
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
        print("Step 1: Fetching login page...")
        # Step 1: Get login page to extract CSRF token
        login_page_url = urljoin(BASE_URL, '/login')
        response = session.get(login_page_url, headers=headers, timeout=30)
        
        if response.status_code != 200:
            return False, None, f"Failed to fetch login page (status: {response.status_code})"
        
        print(f"✓ Login page fetched (status: {response.status_code})")
        
        # Extract CSRF token
        csrf_token = extract_csrf_token(response.text)
        if not csrf_token:
            print("⚠️  Warning: Could not extract CSRF token, proceeding without it...")
        else:
            print(f"✓ CSRF token extracted: {csrf_token[:20]}...")
        
        # Step 2: Handle age verification if present
        soup = BeautifulSoup(response.text, 'html.parser')
        age_modal = soup.find('div', class_='modal is-active over18-modal')
        
        if age_modal:
            print("\nStep 1.5: Age verification detected, bypassing...")
            age_links = age_modal.find_all('a', href=True)
            for link in age_links:
                if 'over18' in link.get('href', ''):
                    age_url = urljoin(BASE_URL, link.get('href'))
                    age_response = session.get(age_url, headers=headers, timeout=30)
                    if age_response.status_code == 200:
                        print("✓ Age verification bypassed")
                        # Re-fetch login page
                        response = session.get(login_page_url, headers=headers, timeout=30)
                        # Re-extract CSRF token
                        csrf_token = extract_csrf_token(response.text)
                    break
        
        # Step 2.5: Extract and handle captcha
        print("\nStep 2: Checking for captcha...")
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
            print(f"✓ Captcha detected: {captcha_url}")
            
            # Get captcha input from user
            captcha_code = get_captcha_from_user(captcha_url, session, headers)
            if not captcha_code:
                return False, None, "Failed to get captcha code from user"
        else:
            print("✓ No captcha detected (or could not find captcha image)")
            captcha_code = None
        
        print("\nStep 3: Submitting login credentials...")
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
        
        print(f"  Submitting to: /user_sessions")
        
        # Submit login
        login_response = session.post(login_url, data=login_data, headers=headers, 
                                     timeout=30, allow_redirects=True)
        
        print(f"✓ Login request submitted (status: {login_response.status_code})")
        
        # Check if login was successful
        print(f"  Final URL: {login_response.url}")
        
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
                print("✓ Login successful (redirected away from login page)")
        elif login_response.status_code == 302 or login_response.status_code == 303:
            print("✓ Login successful (got redirect)")
        else:
            return False, None, f"Login failed: Unexpected status code {login_response.status_code}"
        
        # Step 4: Extract session cookie
        print("\nStep 4: Extracting session cookie...")
        session_cookie = None
        for cookie in session.cookies:
            if cookie.name == '_jdb_session':
                session_cookie = cookie.value
                break
        
        if not session_cookie:
            return False, None, "Login might have succeeded, but could not extract session cookie"
        
        print(f"✓ Session cookie extracted: {session_cookie[:50]}...")
        
        # Verify cookie works
        print("\nStep 5: Verifying session cookie...")
        test_url = urljoin(BASE_URL, '/')
        test_headers = {
            'User-Agent': headers['User-Agent'],
            'Cookie': f'_jdb_session={session_cookie}'
        }
        test_response = requests.get(test_url, headers=test_headers, timeout=30)
        
        if test_response.status_code == 200:
            # Check if we're logged in by looking for logout link or user menu
            soup = BeautifulSoup(test_response.text, 'html.parser')
            # Look for user-related elements (adjust selector based on JavDB's HTML)
            user_menu = soup.find('a', href='/users/edit') or soup.find('a', href='/logout')
            if user_menu:
                print("✓ Session cookie verified (user logged in)")
            else:
                print("⚠️  Warning: Could not verify login status, but cookie was extracted")
        
        return True, session_cookie, "Login successful"
        
    except requests.RequestException as e:
        return False, None, f"Network error: {e}"
    except Exception as e:
        return False, None, f"Unexpected error: {e}"


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
        print(f"❌ Error: {config_path} not found")
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
            print("❌ Error: Could not find JAVDB_SESSION_COOKIE in config.py")
            return False
        
        # Replace with new cookie
        new_content = re.sub(pattern, rf"\g<1>{session_cookie}\g<3>", content)
        
        # Write back to file
        with open(config_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        print(f"✓ Updated {config_path} with new session cookie")
        return True
        
    except Exception as e:
        print(f"❌ Error updating config.py: {e}")
        return False


def main():
    """Main function"""
    print("=" * 60)
    print("JavDB Auto Login Script (with Captcha Support)")
    print("=" * 60)
    print()
    
    # Check credentials
    if not JAVDB_USERNAME or not JAVDB_PASSWORD:
        print("❌ Error: JAVDB_USERNAME and JAVDB_PASSWORD must be set in config.py")
        print()
        print("To use this script:")
        print("1. Open config.py")
        print("2. Set JAVDB_USERNAME = 'your_email_or_username'")
        print("3. Set JAVDB_PASSWORD = 'your_password'")
        print("4. Run: python3 scripts/login.py")
        print("5. Enter captcha code when prompted")
        sys.exit(1)
    
    print(f"Username: {JAVDB_USERNAME}")
    print(f"Base URL: {BASE_URL}")
    print()
    
    # Show captcha solving method
    if CAPTCHA_SOLVER_AVAILABLE:
        print("🤖 Captcha Solving: AUTO (OCR + Manual fallback)")
        print("   - Will try OCR automatic recognition first")
        print("   - Falls back to manual input if OCR fails")
        if TWOCAPTCHA_API_KEY:
            print("   - 2Captcha API configured (optional)")
    else:
        print("📝 Captcha Solving: MANUAL ONLY")
        print("   - Install dependencies for automatic solving:")
        print("     brew install tesseract  # macOS")
        print("     pip install pytesseract pillow")
    print()
    
    # Perform login
    success, session_cookie, message = login_javdb(JAVDB_USERNAME, JAVDB_PASSWORD)
    
    print()
    print("=" * 60)
    if success:
        print("✅ LOGIN SUCCESSFUL")
        print("=" * 60)
        print()
        print(f"Session Cookie: {session_cookie[:50]}...")
        print()
        
        # Update config.py
        print("Updating config.py...")
        if update_config_file(session_cookie):
            print()
            print("=" * 60)
            print("✅ ALL DONE!")
            print("=" * 60)
            print()
            print("The new session cookie has been saved to config.py")
            print("You can now use the spider with --url parameter:")
            print("  python3 scripts/spider.py --url https://javdb.com/actors/...")
            
            # Cleanup captcha image
            try:
                if os.path.exists('javdb_captcha.png'):
                    os.remove('javdb_captcha.png')
                    print("\n✓ Cleaned up captcha image file")
            except:
                pass
        else:
            print()
            print("⚠️  Warning: Login successful but failed to update config.py")
            print(f"Please manually update JAVDB_SESSION_COOKIE in config.py with:")
            print(f"  {session_cookie}")
    else:
        print("❌ LOGIN FAILED")
        print("=" * 60)
        print()
        print(f"Error: {message}")
        print()
        print("Troubleshooting:")
        print("1. Check your username and password in config.py")
        print("2. Make sure you can login via web browser")
        print("3. Make sure you entered the captcha code correctly")
        print("4. Try running the script again (captcha changes each time)")
        print("5. Check if JavDB changed their login form")
        sys.exit(1)


if __name__ == '__main__':
    main()

