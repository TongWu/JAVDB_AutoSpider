# CloudFlare 绕过

集成 [CloudflareBypassForScraping](https://github.com/sarperavci/CloudflareBypassForScraping)，用于处理 JavDB 上的 CloudFlare 防护。

## 何时使用

在以下情况下使用 CloudFlare 绕过：
- JavDB 显示 CloudFlare 验证页面
- 出现 "Access Denied" 或 "Checking your browser" 错误
- 浏览器可以直接访问，但爬虫访问失败
- 仅使用 proxy 无法绕过 CloudFlare 防护

## 工作原理

CF 绕过是一种**回退机制** —— 每个请求仍然先尝试直连模式。当直连失败时：

1. 请求通过 CF 绕过服务转发（Request Mirroring 模式）
2. URL 被重写：`https://javdb.com/page` → `http://localhost:8000/page`
3. 原始主机名通过 `x-hostname` 请求头发送
4. CF 绕过服务自动处理 cf_clearance cookie

### 网络拓扑

**本地部署：**
```
Spider → http://localhost:8000 → CF Bypass Service → https://javdb.com
```

**使用 proxy：**
```
Spider → http://proxy_ip:8000 → CF Bypass on Proxy Server → https://javdb.com
```

**使用 proxy + `CF_BYPASS_VIA_PROXY=True`（绕过服务绑定回环地址）：**

```text
Spider → proxy (proxy_ip:7890) → http://127.0.0.1:8000 → CF Bypass → https://javdb.com
```

使用 proxy 池时，CF 绕过 URL 会自动调整为当前 proxy 的 IP。

## 安装配置

### 1. 安装 CloudflareBypassForScraping

```bash
git clone https://github.com/sarperavci/CloudflareBypassForScraping.git
cd CloudflareBypassForScraping
pip install -r requirements.txt
```

### 2. 启动服务

```bash
python app.py              # 默认端口 8000
python app.py --port 8000  # 显式指定端口
```

### 3. 配置爬虫

```python
# 在 config.py 中
CF_BYPASS_SERVICE_PORT = 8000  # 必须与服务端口一致
```

### 4. 可选：粘性绕过模式

使用 `--always-bypass-time` 在一次成功回退后保持 proxy 处于绕过模式：

```bash
# 在一次回退成功后保持绕过模式 30 分钟
python3 -m apps.cli.spider --always-bypass-time 30

# 在整个会话期间保持绕过模式
python3 -m apps.cli.spider --always-bypass-time 0
```

如果不使用此标志，每个请求都会先尝试直连模式。

**服务地址逻辑：**
- **无 proxy**：使用 `http://localhost:8000`
- **使用 proxy 池**：使用 `http://{proxy_ip}:8000`（从当前 proxy URL 中提取 IP）

这样可以将 CF 绕过服务部署在与 proxy 相同的服务器上。

### 让绕过服务脱离公网（`CF_BYPASS_VIA_PROXY`）

默认情况下爬虫直接拨号 `http://{proxy_ip}:8000`，因此绕过服务必须在 proxy 的公网
IP 上可达。若想在不使用防火墙或 VPN 的前提下让它保持私有，可设置：

```python
# 在 config.py 中
CF_BYPASS_VIA_PROXY = True
```

爬虫随后会将绕过请求*经由* proxy 隧道转发到 `http://127.0.0.1:8000`。由于 proxy 与
绕过服务运行在同一主机上，`127.0.0.1` 解析为该主机的回环地址 —— 因此你可以把绕过
服务仅绑定到 `127.0.0.1`，将其从公网移除。

**前提：** proxy 软件必须允许转发到 `127.0.0.1`。Clash/mihomo 默认允许；Squid 默认
通过内置的 `http_access deny to_localhost` 规则拦截回环——需在该 deny 行之前加上
`http_access allow to_localhost`（或删除该 deny），proxy 才能访问绕过服务：

```squid
# squid.conf —— 放行转发到仅绑定回环地址的绕过服务
http_access allow to_localhost
```

**非默认端口：** 若某 proxy 的绕过服务监听端口不是 `CF_BYPASS_SERVICE_PORT`（8000），
用 `CF_BYPASS_PORT_MAP`（`{proxy_ip: 本地端口}`）按 proxy 指定，使隧道 URL 指向正确的
本地端口——例如 `{'10.0.0.5': 9001}` 会让该 proxy 的绕过 URL 变为 `http://127.0.0.1:9001`。

## 绕过服务不可达时

spider 拨号绕过服务时使用 **5 秒 connect timeout**。若 TCP 连接失败（服务未运行，
或 8000 端口被防火墙拦截），该 proxy 的绕过会被标记为不可达，并在本次 run 剩余时间内跳过：

```
[CF Bypass] Proxy=Jeddah-ARM1: service unreachable at http://144.xxx.xxx.88:8000
  (ConnectTimeout) — bypass disabled for this proxy for the rest of the run
```

该 proxy 本身仍会用于直连请求——只是跳过它的绕过服务。这个标记在一次 run 内不会过期，
因此 run 中途重启的绕过服务要到下一次 run 才会被重新使用。

若每个 proxy 都出现这条警告，说明整个绕过层都挂了；先修好入站规则或服务，再期待受
验证保护的页面能被解析。相关事故记录见 [BFR-024](https://github.com/TongWu/JAVDB_AutoSpider_CICD/blob/main/docs/design/BFR-024-CF-Managed-Challenge-Blind-Spot/BFR-024-cf-managed-challenge-blind-spot.zh.md)。

## 全站验证不会 ban 代理

Cloudflare 验证页（`Just a moment...`，或旧版的 `Security Verification` 页）对每个出口
IP 一视同仁，因此**不会**算到抓取它的那个 proxy 头上：不软 ban，也不会把失败记入该 proxy
在 coordinator 的健康评分。这类 run 以 `all_proxies_failed` 结束，代理池保持完好。

Cloudflare 的 *block* 页（error 1020，"Sorry, you have been blocked"）是 IP 特定的，
仍然会算到该 proxy 头上。

## 性能

- **首次请求**：较慢（需要解决 CF 验证）
- **后续请求**：快速（cookie 已缓存）
- **Cookie TTL**：不固定（通常为数小时到数天）
- **额外开销**：首次请求之后开销极小

## 故障排查

**"Connection refused to localhost:8000"：**
- 确认 CF 绕过服务正在运行
- 检查端口是否可用：`netstat -an | grep 8000`
- 如果使用了不同的端口，请更新 `CF_BYPASS_SERVICE_PORT`

**使用 CF 绕过后出现 "No movie list found"：**
- 检查 CF 绕过服务的日志是否有错误
- 确认 `x-hostname` 请求头被正确发送
- 尝试重启 CF 绕过服务

**CF Bypass + Proxy 不工作：**
- 确保 CF 绕过服务运行在 proxy 服务器上
- 确认 proxy IP 提取正确（查看爬虫日志）
- 直接测试 CF 绕过：`curl http://proxy_ip:8000/`
