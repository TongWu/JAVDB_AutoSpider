# JavDB Login

The system includes automatic login to maintain session cookies for custom URL scraping (actors, tags, etc.).

## Why You Need This

When scraping custom URLs with `--url`, JavDB requires a valid session cookie. Without one, you'll hit age verification or login walls. Auto login handles:
- Logging into JavDB
- Age verification
- Session cookie extraction and update
- Captcha solving (manual, OCR, or GPT-based)

## Quick Start

### 1. Configure Credentials

```python
# In config.py
JAVDB_USERNAME = 'your_email@example.com'
JAVDB_PASSWORD = 'your_password'

# Optional: GPT-based captcha solving
GPT_API_URL = ''   # Your GPT API endpoint
GPT_API_KEY = ''   # Your GPT API key
```

### 2. Run Login

```bash
python3 -m apps.cli.login
```

The script will:
1. Download and display a captcha image
2. Prompt you to enter the captcha code (or solve it automatically if GPT is configured)
3. Log in and extract the session cookie
4. Update `JAVDB_SESSION_COOKIE` in `config.py`

### 3. Use Custom URLs

```bash
python3 -m apps.cli.spider --url "https://javdb.com/actors/RdEb4"
python3 -m apps.cli.pipeline --url "https://javdb.com/actors/RdEb4"
```

## Captcha Handling

### Manual Input (Default)

1. Script downloads captcha image
2. Opens image automatically (platform-dependent)
3. You enter the code when prompted

### GPT-Based (Recommended for Automation)

Configure `GPT_API_URL` and `GPT_API_KEY` in `config.py`. The script sends the captcha image to the GPT API for automatic solving.

### OCR (Tesseract)

Local OCR using Tesseract. Install:

```bash
# macOS
brew install tesseract

# Ubuntu/Debian
sudo apt-get install tesseract-ocr

# Windows — download from https://github.com/UB-Mannheim/tesseract/wiki
```

### Solver Methods

```python
# In utils/login/javdb_captcha_solver.py
solve_captcha(image_data, method='manual')    # Manual input
solve_captcha(image_data, method='ocr')       # Local Tesseract OCR
solve_captcha(image_data, method='2captcha')  # 2Captcha API (legacy)
solve_captcha(image_data, method='auto')      # Try OCR first, fallback
```

## Configuration

```python
# Required
JAVDB_USERNAME = 'your_email@example.com'
JAVDB_PASSWORD = 'your_password'

# Auto-updated by login script
JAVDB_SESSION_COOKIE = ''

# GPT captcha (recommended)
GPT_API_URL = ''
GPT_API_KEY = ''

# Login policy (advanced)
LOGIN_ATTEMPTS_PER_PROXY_LIMIT = 3
LOGIN_MAX_FAILURES_BEFORE_PROXY_SWITCH = 2
LOGIN_VERIFICATION_URLS = []  # URLs to verify session validity
```

## When to Re-run

Re-run `python3 -m apps.cli.login` when:
- Session cookie expires (usually after days/weeks)
- Spider shows "No movie list found" on valid URLs
- Age verification or login errors appear
- Before using `--url` for the first time

## Automation

### Cron Job (Linux/Mac)

```bash
# Refresh cookie every 7 days
0 0 */7 * * cd ~/JAVDB_AutoSpider_CICD && python3 -m apps.cli.login >> logs/javdb_login.log 2>&1
```

### GitHub Actions

The `DailyIngestion.yml` and `AdHocIngestion.yml` workflows include a login step that refreshes the session cookie automatically before each run.

## Sharing Login With CI Runners

GitHub Actions runners share a session cookie through the Proxy Coordinator's
`GlobalLoginState` Durable Object: the first runner to log in publishes its
cookie, and every other runner adopts it and skips its own login. That cache is
only filled by a **successful** login — when Cloudflare blocks the CI login
(captcha rejected, HTTP 403 on the POST), nothing is published and every run
re-logins from scratch.

To break that deadlock, run `python3 -m apps.cli.login` locally — where you can use an
unflagged IP and solve the captcha — and it will publish the resulting cookie
to the coordinator on success. Subsequent CI runs then adopt it and skip login.

This requires the coordinator to be configured (the same values the spider
uses):

```python
# In config.py
PROXY_COORDINATOR_URL = 'https://proxy-coordinator.<account>.workers.dev'
PROXY_COORDINATOR_TOKEN = 'your_shared_secret'
```

For the published cookie to bind to a CI worker, log in through a proxy that
also exists in the runners' `PROXY_POOL` — setting `LOGIN_PROXY_NAME` to a
pooled proxy does this. When the coordinator is unset or unreachable, the
publish step is silently skipped; the local cookie and `config.py` update still
succeed.

## Manual Cookie Extraction

If auto login fails, extract the cookie manually:

1. Open JavDB in your browser and log in
2. Open DevTools → Application → Cookies
3. Copy the `_jdb_session` cookie value
4. Set it in `config.py`:
   ```python
   JAVDB_SESSION_COOKIE = 'your_session_cookie_here'
   ```

## Troubleshooting

**Login failed — incorrect captcha:**
- Captcha is case-sensitive
- Try again for a new captcha
- Consider configuring GPT-based solving

**Login failed — invalid credentials:**
- Verify username/password in `config.py`
- Test credentials in browser first

**Session cookie not working:**
- Verify cookie was updated in `config.py`
- Use the same proxy/network for login and spider
- Try logging in again
