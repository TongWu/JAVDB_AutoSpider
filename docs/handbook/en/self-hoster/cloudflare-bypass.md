# CloudFlare Bypass

Integration with [CloudflareBypassForScraping](https://github.com/sarperavci/CloudflareBypassForScraping) for handling CloudFlare protection on JavDB.

## When to Use

Use CloudFlare Bypass when:
- JavDB shows a CloudFlare challenge page
- You get "Access Denied" or "Checking your browser" errors
- Direct access works in browser but fails in the spider
- Proxy alone doesn't bypass CloudFlare protection

## How It Works

CF bypass is a **fallback mechanism** — each request still starts with direct mode first. When direct fails:

1. Request is forwarded through the CF bypass service (Request Mirroring mode)
2. URL is rewritten: `https://javdb.com/page` → `http://localhost:8000/page`
3. Original hostname is sent via `x-hostname` header
4. CF bypass service handles cf_clearance cookies automatically

### Network Topology

**Local setup:**
```
Spider → http://localhost:8000 → CF Bypass Service → https://javdb.com
```

**With proxy:**
```
Spider → http://proxy_ip:8000 → CF Bypass on Proxy Server → https://javdb.com
```

**With proxy + `CF_BYPASS_VIA_PROXY=True` (bypass bound to loopback):**

```text
Spider → proxy (proxy_ip:7890) → http://127.0.0.1:8000 → CF Bypass → https://javdb.com
```

When using proxy pool, the CF bypass URL automatically adjusts to the current proxy's IP.

## Setup

### 1. Install CloudflareBypassForScraping

```bash
git clone https://github.com/sarperavci/CloudflareBypassForScraping.git
cd CloudflareBypassForScraping
pip install -r requirements.txt
```

### 2. Start the Service

```bash
python app.py              # Default port 8000
python app.py --port 8000  # Explicit port
```

### 3. Configure Spider

```python
# In config.py
CF_BYPASS_SERVICE_PORT = 8000  # Must match the service port
```

### 4. Optional: Sticky Bypass Mode

Use `--always-bypass-time` to keep a proxy on bypass mode after a successful fallback:

```bash
# Keep bypass active for 30 minutes after a fallback success
python3 -m apps.cli.spider --always-bypass-time 30

# Keep bypass active for the entire session
python3 -m apps.cli.spider --always-bypass-time 0
```

Without this flag, each request starts with direct mode first.

## Configuration

```python
# In config.py
CF_BYPASS_SERVICE_PORT = 8000  # CF bypass service port
```

**Service location logic:**
- **No proxy**: Uses `http://localhost:8000`
- **With proxy pool**: Uses `http://{proxy_ip}:8000` (extracts IP from current proxy URL)

This allows running CF bypass on the same server as your proxy.

### Keeping the bypass service off the public internet (`CF_BYPASS_VIA_PROXY`)

By default the spider dials `http://{proxy_ip}:8000` directly, so the bypass
service must be reachable at the proxy's public IP. To keep it private without
a firewall or VPN, set:

```python
# In config.py
CF_BYPASS_VIA_PROXY = True
```

The spider then tunnels the bypass request *through* the proxy to
`http://127.0.0.1:8000`. Because the proxy runs on the same host as the bypass
service, `127.0.0.1` resolves to that host's loopback — so you can bind the
bypass service to `127.0.0.1` only and remove it from the public internet.

**Requirement:** the proxy software must allow forwarding to `127.0.0.1`.
Clash/mihomo permit this by default. Squid blocks loopback via its built-in
`http_access deny to_localhost` rule — add `http_access allow to_localhost`
above that deny line (or remove the deny) so the proxy can reach the bypass
service:

```squid
# squid.conf — allow forwarding to the loopback-bound bypass service
http_access allow to_localhost
```

**Non-default ports:** if a proxy's bypass service listens on a port other than
`CF_BYPASS_SERVICE_PORT` (8000), map it per proxy with `CF_BYPASS_PORT_MAP`
(`{proxy_ip: local_port}`) so the tunnelled URL targets the right local port —
e.g. `{'10.0.0.5': 9001}` makes that proxy's bypass URL `http://127.0.0.1:9001`.

## Performance

- **First request**: Slower (CF challenge solving)
- **Subsequent requests**: Fast (cookie cached)
- **Cookie TTL**: Varies (usually hours to days)
- **Overhead**: Minimal after first request

## Troubleshooting

**"Connection refused to localhost:8000":**
- Verify CF bypass service is running
- Check port availability: `netstat -an | grep 8000`
- Update `CF_BYPASS_SERVICE_PORT` if using a different port

**"No movie list found" with CF bypass:**
- Check CF bypass service logs for errors
- Verify `x-hostname` header is being sent correctly
- Try restarting the CF bypass service

**CF Bypass + Proxy not working:**
- Ensure CF bypass service is running on the proxy server
- Verify proxy IP extraction is correct (check spider logs)
- Test CF bypass directly: `curl http://proxy_ip:8000/`
