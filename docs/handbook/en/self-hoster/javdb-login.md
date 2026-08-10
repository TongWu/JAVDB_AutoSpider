# JavDB Login

The system includes automatic login to maintain session cookies for custom URL scraping (actors, tags, etc.).

## Why You Need This

When scraping custom URLs with `--url`, JavDB requires a valid session cookie. Without one, you'll hit age verification or login walls. Auto login handles:
- Logging into JavDB
- Age verification
- Session cookie extraction and update
- Captcha solving (AI vision model via an OpenAI-compatible API)

## Quick Start

### 1. Configure Credentials

```python
# In config.py
JAVDB_USERNAME = 'your_email@example.com'
JAVDB_PASSWORD = 'your_password'

# Required for automatic captcha solving (login is non-interactive)
GPT_API_URL = 'https://api.gpt.ge/v1/chat/completions'  # OpenAI-compatible endpoint
GPT_API_KEY = ''   # Your API key
```

### 2. Run Login

```bash
python3 -m apps.cli.login
```

The script will:
1. Fetch the login page and handle age verification
2. Download the captcha image and solve it with the configured AI vision model
3. Log in and extract the session cookie (retrying on wrong captchas)
4. Update `JAVDB_SESSION_COOKIE` in `config.py`

### 3. Use Custom URLs

```bash
python3 -m apps.cli.spider --url "https://javdb.com/actors/RdEb4"
python3 -m apps.cli.pipeline --url "https://javdb.com/actors/RdEb4"
```

## Captcha Handling

JavDB shows a distorted image captcha on the login form. The script solves it
automatically by sending the image to an OpenAI-compatible vision model
(`GPT_API_URL` + `GPT_API_KEY`). There is no manual-entry fallback — if the GPT
API is not configured, login cannot proceed.

### How it works

1. The script downloads the captcha image from the login page.
2. It sends the image to the configured model (`CAPTCHA_MODEL`) with a prompt
   asking for the characters only.
3. The reply is sanitized (code fences / quotes / whitespace stripped, then
   lowercased — JavDB's comparison is case-sensitive against a lowercase code).
4. The candidate is submitted. A wrong captcha is retried with a fresh image up
   to `LOGIN_MAX_RETRIES` times.

### Why retries matter

Single-shot accuracy on JavDB's distorted captcha is inherently low (~25% for
the best models in a 720-attempt benchmark), so the script relies on retries:
overall success `= 1 - (1 - p)^n`. At `p≈0.25`, 8 retries lifts login success
to roughly 90%. If logins often fail, raise `LOGIN_MAX_RETRIES`.

### Model selection

`CAPTCHA_MODEL` accepts any vision-capable model your endpoint exposes.
Benchmarked options (accuracy statistically tied within the top tier):

| Model | Single-shot acc | Notes |
|---|---|---|
| `qwen-vl-ocr` (default) | ~25% | Fastest + cheapest, clean output |
| `gpt-4o` | ~28% | Highest, but sometimes wraps output in code fences |
| `gpt-4o-mini` | ~19% | Cheaper, lower accuracy |

> **Reasoning models** (e.g. `gpt-5-mini-high`) work but are slower and no more
> accurate. They consume hundreds of hidden tokens before answering, so
> `CAPTCHA_MAX_TOKENS` must stay high (≥2000) or they return an empty answer
> with `finish_reason=length`. This is why the default is 2000, not 50.

## Configuration

```python
# Required
JAVDB_USERNAME = 'your_email@example.com'
JAVDB_PASSWORD = 'your_password'

# Auto-updated by login script
JAVDB_SESSION_COOKIE = ''

# GPT captcha solving (required for automatic login)
GPT_API_URL = ''
GPT_API_KEY = ''

# Captcha solver tuning (optional — sensible defaults shown)
CAPTCHA_MODEL = 'qwen-vl-ocr'   # any vision model your endpoint exposes
CAPTCHA_MAX_TOKENS = 2000       # keep >=2000 for reasoning models
LOGIN_MAX_RETRIES = 8           # retries per login (single-shot acc is low)

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
