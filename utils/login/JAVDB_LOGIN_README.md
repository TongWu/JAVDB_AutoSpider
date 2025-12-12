# JavDB Auto Login Guide

This guide explains how to automatically login to JavDB and keep your session cookie up-to-date.

## Why Auto Login?

When using custom URLs (like actor pages) with `--url` parameter, JavDB requires a valid session cookie. This cookie expires after some time, causing the spider to fail with login/age verification issues.

The auto-login script solves this by:
- ✅ Automatically logging into JavDB
- ✅ Handling age verification automatically
- ✅ **Captcha support**: Saves captcha image and prompts for manual input
- ✅ Extracting the session cookie
- ✅ Updating `config.py` with the new cookie

**Note:** JavDB requires image captcha verification during login. The script will:
1. Download and save the captcha image
2. Automatically open it (if possible)
3. Prompt you to enter the captcha code

## Setup

### 1. Configure Your Credentials

Edit `config.py` and add your JavDB login credentials:

```python
# JavDB login credentials
JAVDB_USERNAME = 'your_email@example.com'  # or your username
JAVDB_PASSWORD = 'your_password'
```

**Security Note:** 
- Keep your `config.py` file secure and never commit it to public repositories
- The `.gitignore` file should already exclude `config.py`

### 2. Run the Login Script

```bash
python3 javdb_login.py
```

**Expected Output:**

```
============================================================
JavDB Auto Login Script (with Captcha Support)
============================================================

Username: your_email@example.com
Base URL: https://javdb.com

📝 Note: You will need to manually enter the captcha code
         A captcha image file will be saved and opened automatically

Step 1: Fetching login page...
✓ Login page fetched (status: 200)
✓ CSRF token extracted: abc123...

Step 1.5: Age verification detected, bypassing...
✓ Age verification bypassed

Step 2: Checking for captcha...
✓ Captcha detected: https://javdb.com/captcha.jpg

🔐 Fetching captcha image...
✓ Captcha image saved to: javdb_captcha.png
  Please open the image to view the captcha

🔐 Please enter the captcha code: ABCD    <-- You enter this

Step 3: Submitting login credentials...
  Trying endpoint: /users/sign_in...
  ✓ Got 200 response from /users/sign_in
✓ Login request submitted (status: 200)
✓ Login successful (redirected to home page)

Step 4: Extracting session cookie...
✓ Session cookie extracted: UT1DS4CuOJaCRpHyoSxkftWW3mi1po9uGAhN37v3pKBSx...

Step 5: Verifying session cookie...
✓ Session cookie verified (user logged in)

============================================================
✅ LOGIN SUCCESSFUL
============================================================

Session Cookie: UT1DS4CuOJaCRpHyoSxkftWW3mi1po9uGAhN37v3pKBSx...

Updating config.py...
✓ Updated config.py with new session cookie

============================================================
✅ ALL DONE!
============================================================

The new session cookie has been saved to config.py
You can now use the spider with --url parameter:
  python3 Javdb_Spider.py --url https://javdb.com/actors/...
```

### 3. Use the Spider

Now you can use custom URLs without worrying about cookie expiration:

```bash
# Spider only
python3 Javdb_Spider.py --url "https://javdb.com/actors/RdEb4"

# Full pipeline (spider + uploader + pikpak)
python3 pipeline_run_and_notify.py --url "https://javdb.com/actors/RdEb4"
```

## When to Re-run?

Re-run the login script when:
- ✅ Session cookie expires (usually after a few days/weeks)
- ✅ You get age verification or login errors
- ✅ Spider reports "No movie list found" on valid URLs

## Automation (Optional)

### Add to Cron Job

You can automate cookie refresh by adding to crontab:

```bash
# Refresh cookie every 7 days
0 0 */7 * * cd ~/JAVDB_AutoSpider && python3 javdb_login.py >> logs/javdb_login.log 2>&1
```

### Add to Pipeline Script

Or integrate into your pipeline script to auto-refresh before scraping.

## Troubleshooting

### Login Failed

**Error:** "Login failed: Incorrect captcha code"

**Solution:**
1. Make sure you entered the captcha correctly (case-sensitive)
2. Check the saved `javdb_captcha.png` file
3. Run the script again to get a new captcha
4. If captcha image is unclear, try multiple times

---

**Error:** "Login failed: Invalid email or password"

**Solution:**
1. Check your credentials in `config.py`
2. Try logging in via web browser to confirm they work
3. Make sure you're using the correct username format (email or username)
4. Check if you entered the captcha correctly

---

**Error:** "Could not extract CSRF token"

**Solution:**
- JavDB might have changed their login form
- Check if you can access JavDB from your network
- Try using a proxy if JavDB blocks your IP

---

**Error:** "All login endpoints failed" or "404"

**Solution:**
- JavDB changed their login URL
- Check the actual login form action in your browser:
  1. Open JavDB login page in browser
  2. Right-click → Inspect → Network tab
  3. Submit login form and see which endpoint is used
  4. Update the script's `login_endpoints` list

---

**Error:** "Network error: Connection timeout"

**Solution:**
1. Check your internet connection
2. Try using a proxy (configure in `config.py`)
3. JavDB might be temporarily down

### Cookie Not Working

**Error:** Spider still shows "No movie list found" after login

**Solution:**
1. Verify the cookie was actually updated in `config.py`
2. Check if you're using the same proxy/network for login and spider
3. Try logging in again - the cookie might have been rejected

### Permission Errors

**Error:** "Permission denied" when updating config.py

**Solution:**
```bash
chmod 644 config.py
```

## Security Best Practices

1. **Never share your config.py** - It contains your password
2. **Use environment variables (optional)**:
   ```python
   import os
   JAVDB_USERNAME = os.getenv('JAVDB_USER', '')
   JAVDB_PASSWORD = os.getenv('JAVDB_PASS', '')
   ```
3. **Change password regularly** on JavDB website
4. **Check .gitignore** to ensure config.py is excluded

## Technical Details

### How It Works

1. **Fetch Login Page**: Get CSRF token and initial cookies
2. **Handle Age Verification**: Automatically bypass the 18+ confirmation
3. **Extract Captcha**: 
   - Find captcha image in login form
   - Download captcha image
   - Save to `javdb_captcha.png`
   - Auto-open image (platform-dependent)
4. **Get User Input**: Prompt user to manually enter captcha code
5. **Submit Login Form**: POST credentials + captcha + CSRF token
   - Tries multiple possible login endpoints
   - Handles different form field names
6. **Extract Cookie**: Get `_jdb_session` cookie from response
7. **Verify**: Test cookie by making an authenticated request
8. **Update Config**: Replace old cookie in `config.py` using regex
9. **Cleanup**: Remove captcha image file (optional)

### Cookie Format

The cookie is a URL-encoded string that looks like:
```
UT1DS4CuOJaCRpHyoSxkftWW3mi1po9uGAhN37v3pKBSx%2B04...
```

### Manual Cookie Extraction (Alternative)

If auto-login fails, you can manually get the cookie:

1. Open Chrome/Firefox
2. Login to JavDB
3. Press F12 (Developer Tools)
4. Go to "Application" → "Cookies" → "https://javdb.com"
5. Find `_jdb_session` cookie
6. Copy the "Value" column (without the name)
7. Paste into `config.py`:
   ```python
   JAVDB_SESSION_COOKIE = 'paste_here'
   ```

## Files Modified

- `config.py` - Adds login credentials and updated cookie
- `config.py.example` - Template with login configuration
- `javdb_login.py` - New auto-login script
- `JAVDB_LOGIN_README.md` - This documentation

## Support

If you encounter issues:
1. Check the troubleshooting section above
2. Run with `--debug` for more details
3. Check `logs/` folder for error messages
4. Ensure you're using the latest version of the spider

