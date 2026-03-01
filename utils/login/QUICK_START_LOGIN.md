# Quick Start: JavDB Auto Login

## TL;DR

```bash
# 1. Configure credentials
nano config.py
# Set JAVDB_USERNAME and JAVDB_PASSWORD

# 2. Run login script
python3 javdb_login.py

# 3. Enter captcha when prompted
# (Image will open automatically)

# 4. Done! Cookie is saved
```

## Step-by-Step

### 1. Edit config.py

```python
JAVDB_USERNAME = 'your_email@example.com'
JAVDB_PASSWORD = 'your_password'
```

### 2. Run Script

```bash
python3 javdb_login.py
```

### 3. Enter Captcha

When you see this:

```
🔐 Fetching captcha image...
✓ Captcha image saved to: javdb_captcha.png
  Please open the image to view the captcha

🔐 Please enter the captcha code: _
```

- Look at the captcha image (should open automatically)
- Type the code you see
- Press Enter

### 4. Success!

```
✅ ALL DONE!

The new session cookie has been saved to config.py
You can now use the spider with --url parameter:
  python3 scripts/spider --url https://javdb.com/actors/...
```

## Common Issues

### ❌ "Incorrect captcha code"

**Solution:** Run again, enter captcha more carefully

```bash
python3 javdb_login.py  # Try again
```

### ❌ "404 - endpoint not found"

**Solution:** JavDB changed their login URL. Check browser DevTools to find the correct endpoint.

### ❌ Image doesn't open automatically

**Solution:** Manually open `javdb_captcha.png`:

```bash
# macOS
open javdb_captcha.png

# Linux
xdg-open javdb_captcha.png

# Windows
start javdb_captcha.png
```

## Tips

- ✅ Captcha is **case-sensitive** - enter exactly as shown
- ✅ Usually 4-6 characters (letters/numbers)
- ✅ If unclear, run script again for new captcha
- ✅ Cookie typically lasts several days/weeks

## After Login

Use the spider with custom URLs:

```bash
# Example: Actor page
python3 pipeline_run_and_notify.py --url="https://javdb.com/actors/RdEb4"

# Example: Series page
python3 pipeline_run_and_notify.py --url="https://javdb.com/series/67B"

# Example: Tag page
python3 pipeline_run_and_notify.py --url="https://javdb.com/tags?c2=b"
```

## Security

⚠️ **Never commit config.py to git!**

Check `.gitignore`:
```bash
grep config.py .gitignore
```

Should show:
```
config.py
```


