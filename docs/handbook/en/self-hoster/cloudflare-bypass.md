# CloudFlare Bypass

Integration with [CloudflareBypassForScraping](https://github.com/sarperavci/CloudflareBypassForScraping) for handling CloudFlare protection on JavDB.

## When to Use

Use CloudFlare Bypass when:
- JavDB shows a CloudFlare challenge page
- You get "Access Denied" or "Checking your browser" errors
- Direct access works in browser but fails in the spider
- Proxy alone doesn't bypass CloudFlare protection

## How It Works

CF bypass is normally a **fallback mechanism** — each request starts with direct
mode first, and only falls back to the bypass tier when direct fails. (Two
triggers invert that order; see
[Bypass-first ordering](#bypass-first-ordering).)

### Request protocol

The spider talks to the service through a single endpoint — the target URL is
passed as a **query parameter**, not by rewriting the path:

```http
GET http://{service_host}:{port}/html?url={urlencoded_target}
```

So `https://javdb.com/?page=1` is requested as
`http://127.0.0.1:8000/html?url=https%3A%2F%2Fjavdb.com%2F%3Fpage%3D1`.

- **No custom request headers are sent.** The bypass service supplies its own
  User-Agent and handles cf_clearance cookies internally.
- The one exception is the cache-refresh path, which reuses the same endpoint
  with the header `x-bypass-cache: true` to force fresh cf_clearance cookies.

> **Not request mirroring.** Upstream CloudflareBypassForScraping v2 also offers
> a request-mirroring mode — gated on an `x-hostname` header — plus a
> `/cookies?url=` endpoint. This repo uses `/html?url=` only, and never sends
> `x-hostname`. Setting that header would switch the service into mirroring mode
> and make it forward the literal path `/html?url=...` to the target host.

### Network Topology

**Local setup:**

```text
Spider → http://localhost:8000 → CF Bypass Service → https://javdb.com
```

**With proxy:**

```text
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

## Bypass-first ordering

Two independent triggers make a fetch try the bypass tier *before* the direct
path. Neither disables the other.

| Trigger | Scope | Cleared by |
|---|---|---|
| `--always-bypass-time` sticky window | One proxy, after that proxy's own bypass fallback succeeded | The window elapsing (`0` or no value = whole session) |
| Live site-wide Cloudflare challenge | The whole run | The next successful direct fetch |

### Site-wide challenge inversion

When JavDB serves a challenge to every egress IP, direct cannot succeed on *any*
proxy. Leading with direct in that state spends one guaranteed-failed attempt per
proxy on every page before reaching the only tier that can answer. So the run
flips to bypass-first as soon as a site-wide challenge is detected:

```text
Site-wide Cloudflare challenge detected — switching the run to bypass-first (direct is walled off for every proxy)
```

The direct leg still runs, now as the **fallback** — which makes it double as the
recovery probe. The first direct fetch that succeeds clears the flag and the run
returns to direct-first:

```text
[<proxy>] Direct fetch succeeded — site-wide Cloudflare challenge cleared, returning to direct-first
```

Previously the bypass tier sat behind a queue-pressure heuristic, so under a
site-wide wall each page burned roughly one failed direct attempt per proxy
before the cascade reached the bypass tier at all.

With `CF_BYPASS_ENABLED = False` the detection message is still logged, but
there is no bypass tier to lead with — every request stays on the direct path.

### When the wall wins

A site-wide challenge bans no proxy — it is not any proxy's fault — so a run
that the wall shuts out entirely cannot be caught by ban accounting. The
summary report fails it instead, with exit code `2`:

```text
Spider produced ZERO usable entries (40 discovered entries all failed) and every fetch hit a Cloudflare challenge — the site walled off all proxies. Failing the run so this is not mistaken for an empty day.
```

This fires when a challenge was seen and the run produced nothing usable —
whether the index fetch itself was walled, or it survived and every detail
fetch was walled. Entries that failed do not count as a result. Entries skipped
against history do, so a partially-recovered run stays a success.

## Configuration

```python
# In config.py
CF_BYPASS_SERVICE_PORT = 8000  # CF bypass service port
CF_BYPASS_ENABLED = True       # Master switch for the whole bypass tier
```

Setting `CF_BYPASS_ENABLED = False` skips every bypass attempt, so
challenge-protected pages simply fail on the direct path. Both keys are also
settable as GitHub Actions repository variables — see
[GitHub Actions Setup](github-actions-setup.md).

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

### Per-proxy port overrides (`CF_BYPASS_PORT_MAP`)

If one proxy's bypass service listens on a port other than
`CF_BYPASS_SERVICE_PORT`, override it for that proxy alone — keyed by proxy IP:

```python
# In config.py — default is {} (every proxy uses CF_BYPASS_SERVICE_PORT)
CF_BYPASS_PORT_MAP = {'10.0.0.5': 9001}
```

That makes the one proxy's bypass URL `http://10.0.0.5:9001`, or
`http://127.0.0.1:9001` tunnelled through it when `CF_BYPASS_VIA_PROXY = True`.
Every other proxy is unaffected. This is mainly useful during a staged rollout,
when some hosts run a solver on a different port than the rest.

In GitHub Actions the value comes from the `CF_BYPASS_PORT_MAP_JSON` variable.
No workflow currently sets it, so CI runs use the empty default.

## When the bypass service is unreachable

The spider dials the bypass service with a **5-second connect timeout**. If the
TCP connect fails (service down, or port 8000 firewalled off), that proxy's
bypass is marked unreachable and skipped for the rest of the run:

```text
[CF Bypass] Proxy=Jeddah-ARM1: service unreachable at http://144.xxx.xxx.88:8000
  (ConnectTimeout) — bypass disabled for this proxy for the rest of the run
```

The proxy itself keeps being used for direct requests — only its bypass is
skipped. The mark never expires within a run, so a bypass service restarted
mid-run is picked up on the next run.

Seeing this warning for every proxy means the bypass tier is entirely down; fix
the ingress rule or the service before expecting challenge-protected pages to
parse. See [BFR-024](https://github.com/TongWu/JAVDB_AutoSpider_CICD/blob/main/docs/design/BFR-024-CF-Managed-Challenge-Blind-Spot/BFR-024-cf-managed-challenge-blind-spot.md)
for the incident where this failed silently.

## Site-wide challenges do not ban proxies

A Cloudflare challenge (`Just a moment...`, or the older
`Security Verification` page) is served to every egress IP alike, so it is
**not** counted against the proxy that fetched it:

- No local soft-ban.
- **No per-proxy `cf` event is reported to the proxy coordinator.** Every proxy
  sees the same wall, so reporting it per proxy makes all of them cross the
  auto-ban threshold at once and bans the entire pool for something no proxy
  caused. The challenge drives the run-level bypass-first switch instead.
- Local pacing still applies — the penalty tracker records the event, so the run
  keeps backing off while Cloudflare is pushing back.

A Cloudflare *block* page (error 1020, "Sorry, you have been blocked") is
IP-specific and does still count against the proxy.

## Diagnosing the bypass tier

`apps.cli.ops.cf_bypass_probe` sweeps the pool and reports what each proxy's
bypass service actually answers — including whether the payload is a real page
or the interstitial returned as a false success. Use it when challenge-protected
pages stop parsing but nothing obvious appears in the logs:

```bash
# Protocol probe over every proxy in PROXY_POOL
python3 -m apps.cli.ops.cf_bypass_probe

# Compare two solver ports on one proxy, 5 trials each
python3 -m apps.cli.ops.cf_bypass_probe --proxy Singapore-ARM1 --bench 5 --ports 8000,8002
```

See the [CLI Reference](../developer/cli-reference.md#cf-bypass-probe-cli) for
every flag, and `CFBypassProbe.yml` in
[GitHub Actions Setup](github-actions-setup.md) to run it from CI.

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
- Confirm the host answers `GET /html?url=` (CloudflareBypassForScraping) and
  not `POST /v1` (FlareSolverr) — run `apps.cli.ops.cf_bypass_probe`
- Try restarting the CF bypass service

**CF Bypass + Proxy not working:**
- Ensure CF bypass service is running on the proxy server
- Verify proxy IP extraction is correct (check spider logs)
- Test with the command matching your topology — and use the port
  `CF_BYPASS_PORT_MAP` assigns that proxy, if any:

```bash
# CF_BYPASS_VIA_PROXY=False — service listens on the proxy's public IP
curl "http://proxy_ip:8000/html?url=https%3A%2F%2Fjavdb.com%2F"

# CF_BYPASS_VIA_PROXY=True — service is loopback-bound, reached by
# tunnelling through the proxy
curl -x http://proxy_ip:7890 "http://127.0.0.1:8000/html?url=https%3A%2F%2Fjavdb.com%2F"
```

- Or run `python3 -m apps.cli.ops.cf_bypass_probe --proxy <name> --verbose`,
  which resolves the topology and the per-proxy port itself
