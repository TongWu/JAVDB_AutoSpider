# Proxy Setup

The system supports **single proxy** and **proxy pool** modes. Pool mode is recommended for reliability and automatic failover.

## Proxy Pool Mode (Recommended)

Configure multiple proxies for automatic failover and load distribution.

### Quick Setup

```python
# In config.py
PROXY_MODE = 'pool'
PROXY_POOL = [
    {'name': 'Proxy-1', 'http': 'http://127.0.0.1:7890', 'https': 'http://127.0.0.1:7890'},
    {'name': 'Proxy-2', 'http': 'http://127.0.0.1:7891', 'https': 'http://127.0.0.1:7891'},
]
PROXY_POOL_MAX_FAILURES = 3  # Max failures before banning proxy for this session
```

### Pool Features

- **Automatic Switching** — When one proxy fails, automatically switches to another
- **Passive Health Checking** — Only marks proxies as failed on actual failures (no active probing)
- **Cooldown Mechanism** — Failed/banned proxies are skipped only for the remainder of the current process session (no persistent TTL); the next run starts from a clean slate
- **Ban Detection** — Detects bans via HTTP 403 responses and ban-page HTML patterns; immediately re-queues the page to another worker
- **Session-Scoped Bans** — Bans exist only in memory for the current process. The next run starts with a clean slate — no CSV file, no database table
- **Statistics Tracking** — Detailed success rates and usage statistics per proxy

### Ban Behavior

When a proxy is banned during a run:
- The proxy is skipped for the remainder of that session (in-memory only)
- Spider exits with code 2 when all proxies are banned
- Pipeline email reports include proxy/ban context for that run
- The next run retries all proxies automatically

Observe ban activity in spider log output (`logs/spider.log`). There is no persistent ban storage.

## Single Proxy Mode (Legacy)

Traditional single-proxy configuration for HTTP/HTTPS/SOCKS5.

```python
# In config.py
PROXY_MODE = 'single'

# HTTP/HTTPS proxy
PROXY_HTTP = 'http://127.0.0.1:7890'
PROXY_HTTPS = 'http://127.0.0.1:7890'

# Or SOCKS5 proxy
PROXY_HTTP = 'socks5://127.0.0.1:1080'
PROXY_HTTPS = 'socks5://127.0.0.1:1080'

# With authentication
PROXY_HTTP = 'http://username:password@proxy.example.com:8080'
PROXY_HTTPS = 'http://username:password@proxy.example.com:8080'
```

### Installing SOCKS5 Support

```bash
pip install requests[socks]
```

## Modular Proxy Control

The `PROXY_MODULES` setting controls which components use the proxy:

| Module | Description | Use Case |
|--------|-------------|----------|
| `spider` | JavDB spider (includes login) | Geo-restricted JavDB access |
| `qbittorrent` | qBittorrent Web UI API | qB behind firewall |
| `pikpak` | PikPak bridge operations | PikPak API access |
| `all` | All modules | Route everything through proxy |

```python
# Default: only spider
PROXY_MODULES = ['spider']

# Spider + qBittorrent
PROXY_MODULES = ['spider', 'qbittorrent']

# Everything
PROXY_MODULES = ['all']

# Disable proxy for all modules by default
PROXY_MODULES = []
```

## Command-Line Overrides

Commands follow `PROXY_MODULES` by default. Override per-run:

```bash
# Auto mode (follows config.py)
python3 -m apps.cli.spider

# Force proxy on
python3 -m apps.cli.spider --use-proxy

# Force proxy off
python3 -m apps.cli.spider --no-proxy

# Pipeline override (applies to all steps)
python3 -m apps.cli.pipeline --use-proxy
```

The Web UI and task API mirror the same tri-state behavior: omit both flags for auto mode, `use_proxy=true` to force on, `no_proxy=true` to force off.

## Supported Proxy Types

| Protocol | URL Format |
|----------|-----------|
| HTTP | `http://proxy.example.com:8080` |
| HTTPS | `https://proxy.example.com:8080` |
| SOCKS5 | `socks5://proxy.example.com:1080` |

## Troubleshooting

**500 Internal Server Error:**
- Verify proxy is running and accessible
- Check credentials; URL-encode special characters in passwords:
  ```python
  from urllib.parse import quote
  password = "My@Pass!"
  encoded = quote(password, safe='')  # My%40Pass%21
  ```
- Test manually: `curl -x http://user:pass@proxy:port https://javdb.com`

**Connection refused or timeout:**
- Check if proxy server is running: `telnet proxy_ip proxy_port`
- Verify firewall rules allow the connection
- Check if the proxy requires authentication

**Proxy works but downloads fail:**
- Some proxies don't support magnet links or torrents
- Use proxy only for spider, direct for qB/PikPak:
  ```python
  PROXY_MODULES = ['spider']
  ```

**Password special characters reference:**

| Character | Encoded |
|-----------|---------|
| `@` | `%40` |
| `:` | `%3A` |
| `/` | `%2F` |
| `?` | `%3F` |
| `#` | `%23` |
| `!` | `%21` |
| `+` | `%2B` |
| Space | `%20` |
