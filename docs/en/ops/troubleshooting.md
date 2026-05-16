# Troubleshooting

Common issues and their solutions for JAVDB AutoSpider.

## Spider Issues

**No entries found / "No movie list found"**
- Check if javdb.com is accessible from your machine or proxy (open in a browser).
- If using a proxy, verify the proxy is running and reachable.
- For custom URL scraping (`--url`), ensure you have a valid session cookie. Run `python3 -m apps.cli.login` to refresh it.
- Check if CloudFlare is blocking access. If CF bypass is configured, verify the CF bypass service is running.

**Connection errors / timeouts**
- Verify internet connectivity.
- Check if javdb.com is experiencing downtime.
- If behind a corporate firewall, ensure outbound HTTPS traffic is allowed.

**CSV not generated**
- Check that the `reports/DailyReport/` directory exists (it is created automatically, but verify if running in an unusual environment).
- In `--dry-run` mode, CSV files are intentionally not written.
- Review spider logs (`logs/spider.log`) for errors during the run.

## qBittorrent Issues

**Cannot connect to qBittorrent**
- Verify qBittorrent is running and the Web UI is enabled (**Tools > Preferences > Web UI > Enable**).
- Check `QB_URL` in config.py includes the protocol (`http://` or `https://`) and port.
- If using HTTPS with a self-signed certificate, set `QB_VERIFY_TLS = False` in config.py.
- Test connectivity: `curl -k https://YOUR_QB_URL/api/v2/app/version`

**Login failed**
- Verify `QB_USERNAME` and `QB_PASSWORD` in config.py.
- Check if qBittorrent has IP-based access restrictions enabled.
- Some qBittorrent versions require the "Bypass authentication for clients on localhost" option.

**CSV file not found**
- Run the spider first to generate the CSV file.
- Check that the spider completed successfully (exit code 0).
- Verify the CSV path in the uploader matches the spider output directory.

## Git Issues

**Authentication failed**
- Use a personal access token (PAT) instead of a password. Generate one at **GitHub > Settings > Developer settings > Personal access tokens**.
- Verify `GIT_USERNAME` and `GIT_PASSWORD` (the PAT) in config.py.

**Repository not found**
- Check `GIT_REPO_URL` for typos.
- Ensure the PAT has `repo` scope for private repositories.

**Branch issues**
- Ensure `GIT_BRANCH` matches an existing branch in your repository.
- For new repositories, create the branch first or use `main`.

## Proxy Issues

**All proxies banned during a run**
- Ban state is session-scoped (in-memory only). The next run starts with a clean slate and retries all proxies.
- Check spider logs for ban-related messages.
- Consider adding more proxies to the pool.
- Verify proxies can actually reach javdb.com: `curl -x http://proxy:port https://javdb.com`

**Spider exits with code 2**
- Exit code 2 indicates a proxy ban was detected during the session.
- Session-scoped cooldowns apply for that run only.
- Add more proxies or wait for the next scheduled run.

**Cooldown not working as expected**
- Proxy bans are session-scoped (in-memory only). Restarting the spider resets all ban state.
- There is no persistent ban file or database table.

**Ban false positives**
- Verify javdb.com is actually accessible from the proxy IP (test in a browser through the proxy).
- Check for CloudFlare challenges that look like bans.

**500 Internal Server Error / connection refused**
- Check if the proxy server is running and accessible.
- Verify proxy credentials (username/password).
- If the password contains special characters, URL-encode them:
  ```python
  from urllib.parse import quote
  password = "My@Pass!"
  encoded = quote(password, safe='')
  # Output: My%40Pass%21
  ```
- Test the proxy manually: `curl -x http://username:password@proxy:port https://javdb.com`

**Special characters in proxy passwords**

Common characters that need URL encoding:

| Character | Encoded |
|---|---|
| `@` | `%40` |
| `:` | `%3A` (only in password, not the separator after `@`) |
| `/` | `%2F` |
| `?` | `%3F` |
| `#` | `%23` |
| `&` | `%26` |
| `=` | `%3D` |
| `+` | `%2B` |
| Space | `%20` |
| `!` | `%21` |

Example: `http://user:My@Pass!123@proxy:8080` becomes `http://user:My%40Pass%21123@proxy:8080`

## JavDB Login Issues

**Login failed -- incorrect captcha**
- Captcha is case-sensitive. Try again for a new captcha.
- Consider using the GPT-4o Vision API (`GPT_API_URL` / `GPT_API_KEY` in config.py) for automatic captcha solving.

**Login failed -- invalid credentials**
- Verify `JAVDB_USERNAME` and `JAVDB_PASSWORD` in config.py.
- Test the credentials in a browser first.

**Session cookie not working**
- Verify the cookie was updated in config.py after running the login script.
- Use the same proxy/network for both login and spider runs.
- Try logging in again -- cookies expire after days to weeks.

**When to re-run login:**
- Session cookie has expired (usually after days/weeks)
- Spider shows "No movie list found" on valid custom URLs
- JavDB returns age verification or login errors
- Before using `--url` for the first time

For detailed login troubleshooting and manual cookie extraction, see the [JavDB Login Guide](../../../utils/login/JAVDB_LOGIN_README.md).

## CloudFlare Bypass Issues

**Connection refused to localhost:8000**
- Ensure the CF bypass service is running.
- Check if port 8000 is available: `netstat -an | grep 8000`
- Update `CF_BYPASS_SERVICE_PORT` in config.py if using a different port.

**"No movie list found" with CF bypass**
- Check CF bypass service logs for errors.
- Verify the `x-hostname` header is being sent correctly.
- Try restarting the CF bypass service.

**Proxy + CF bypass not working**
- The CF bypass service must be running on the same server as the proxy.
- Verify proxy IP extraction is correct (check spider logs).
- Test directly: `curl http://proxy_ip:8000/`

## Downloaded Indicator Issues

**Indicators not added**
- Check if the history file (`reports/parsed_movies_history.csv`) exists and has the correct format.
- The history database (`reports/history.db`) is the primary source; the CSV is a legacy fallback.

**Uploader skipping too many torrents**
- Check if the history file contains outdated records that should be cleaned up.
- Use `--ignore-history` to bypass history checking for a single run.

**History format issues**
- The system automatically migrates old formats. If issues persist, run:
  ```bash
  python3 packages/python/javdb_migrations/tools/update_history_format.py
  ```
- See [migration-scripts.md](migration-scripts.md) for all available migration tools.

## Debug Mode

To see detailed operations, increase the log level. The environment variable takes precedence over config.py:

```bash
# Enable debug logging
export LOG_LEVEL=DEBUG

# Optional: force the legacy 4-field format on the console while
# investigating a log-format issue
export LOG_STYLE=verbose

# Optional: turn off ::group:: folding in CI when scraping raw logs
export LOG_GITHUB_GROUPS=off
```

Or set it in config.py:

```python
LOG_LEVEL = 'DEBUG'
```

### What DEBUG reveals

- **Proxy pool details**: Per-proxy success rate, last-ok, last-fail timestamps (at `INFO` only the single-line summary `available=N/total / cooldown=K / banned=B` is shown)
- **Rust extension logs**: Rust-side log targets (`javdb_rust_core::proxy::pool`, etc.) flow through the Python formatter via `pyo3_log` and appear with short display names: `ProxyPool`, `BanManager`, `FetchEngine`, `Parser`
- **Database operations**: Detailed SQL queries and row counts
- **HTTP requests**: Full request/response details for debugging connectivity

## GitHub Actions Specific Issues

**ARTIFACT_KEY not configured**
- Every job guards against a missing `ARTIFACT_KEY` secret. Add it under **Settings > Secrets and variables > Actions > Secrets**.

**Cron not firing**
- GitHub disables scheduled workflows on repositories with no commit activity for 60 days. Push a commit or manually trigger a run.

**Email not sent from CI**
- Check `SMTP_*` secrets are configured.
- Gmail requires an App Password, not your regular login password.
- The email job exits with code 2 when SMTP send fails (so CI does not silently mark it as "notified").

**Rollback failures**
- Check the rollback log artifact (14-day retention).
- Manual rollback: **Actions > RollbackD1 > Run workflow** with the session ID.
- See [d1-rollback.md](d1-rollback.md) for the full SOP and dispatch matrix.
