# CloudFlare 绕过

集成 [CloudflareBypassForScraping](https://github.com/sarperavci/CloudflareBypassForScraping)，用于处理 JavDB 上的 CloudFlare 防护。

## 何时使用

在以下情况下使用 CloudFlare 绕过：
- JavDB 显示 CloudFlare 验证页面
- 出现 "Access Denied" 或 "Checking your browser" 错误
- 浏览器可以直接访问，但爬虫访问失败
- 仅使用 proxy 无法绕过 CloudFlare 防护

## 工作原理

CF 绕过通常是一种**回退机制** —— 每个请求先尝试直连模式，只有直连失败时才回退到
绕过层。（有两个触发条件会反转这个顺序，见[绕过优先的顺序](#绕过优先的顺序)。）

### 请求协议

爬虫只通过一个端点与该服务通信 —— 目标 URL 作为**查询参数**传入，而不是重写路径：

```http
GET http://{service_host}:{port}/html?url={urlencoded_target}
```

因此 `https://javdb.com/?page=1` 实际请求的是
`http://127.0.0.1:8000/html?url=https%3A%2F%2Fjavdb.com%2F%3Fpage%3D1`。

- **不发送任何自定义请求头。** 绕过服务自行提供 User-Agent，并在内部处理
  cf_clearance cookie。
- 唯一的例外是缓存刷新路径：它复用同一个端点，并附带请求头
  `x-bypass-cache: true`，用于强制获取新的 cf_clearance cookie。

> **这不是 request mirroring。** 上游 CloudflareBypassForScraping v2 确实还提供
> request-mirroring 模式（由 `x-hostname` 请求头启用）以及 `/cookies?url=` 端点。
> 本仓库只使用 `/html?url=`，并且从不发送 `x-hostname`。设置该请求头会让服务切换到
> mirroring 模式，把字面路径 `/html?url=...` 转发给目标站点。

### 网络拓扑

**本地部署：**

```text
Spider → http://localhost:8000 → CF Bypass Service → https://javdb.com
```

**使用 proxy：**

```text
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

## 绕过优先的顺序

有两个互相独立的触发条件，会让一次抓取先走绕过层、再走直连。二者互不禁用。

| 触发条件 | 作用范围 | 何时解除 |
|---|---|---|
| `--always-bypass-time` 粘性窗口 | 单个 proxy，且该 proxy 自己的绕过回退曾经成功过 | 窗口到期（`0` 或不带值 = 整个会话） |
| 正在发生的全站 Cloudflare 验证 | 整次 run | 下一次成功的直连抓取 |

### 全站验证下的顺序反转

当 JavDB 对每个出口 IP 都返回验证页时，*任何* proxy 的直连都不可能成功。此时若仍先走
直连，每个页面都会在每个 proxy 上白白消耗一次注定失败的尝试，才轮到唯一能给出结果的
绕过层。因此一旦检测到全站验证，整次 run 就切换为绕过优先：

```text
Site-wide Cloudflare challenge detected — switching the run to bypass-first (direct is walled off for every proxy)
```

直连这一条腿仍然会跑，只是变成了**回退** —— 这也让它顺便充当恢复探针。第一次成功的
直连抓取会清除该标记，run 随即回到直连优先：

```text
[<proxy>] Direct fetch succeeded — site-wide Cloudflare challenge cleared, returning to direct-first
```

在此之前，绕过层被一个队列压力启发式挡在后面，因此在全站封锁下，每个页面都要先在每个
proxy 上烧掉大约一次失败的直连尝试，级联才会真正走到绕过层。

若 `CF_BYPASS_ENABLED = False`，这条检测日志仍会打印，但根本没有绕过层可以优先使用 ——
所有请求依旧走直连路径。

### 当这堵墙赢了

全站验证不会 ban 任何代理 —— 这不是任何一个代理的错 —— 所以被这堵墙彻底挡在外面的
run，无法靠 ban 记账来捕获。改由摘要报告让它失败，退出码为 `2`：

```text
Spider produced ZERO usable entries (40 discovered entries all failed) and every fetch hit a Cloudflare challenge — the site walled off all proxies. Failing the run so this is not mistaken for an empty day.
```

触发条件是：见到了验证，且本次 run 没有产出任何可用结果 —— 无论是索引抓取本身被墙，
还是索引挺过去了而每一次详情抓取都被墙。失败的条目不算结果；被历史跳过的条目算，
因此部分恢复的 run 仍然算成功。

## 配置

```python
# 在 config.py 中
CF_BYPASS_SERVICE_PORT = 8000  # CF 绕过服务端口
CF_BYPASS_ENABLED = True       # 整个绕过层的总开关
```

设置 `CF_BYPASS_ENABLED = False` 会跳过所有绕过尝试，受验证保护的页面便会直接在直连
路径上失败。这两个键也可以作为 GitHub Actions 仓库变量设置 —— 见
[GitHub Actions 部署](github-actions-setup.md)。

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

### 按 proxy 覆盖端口（`CF_BYPASS_PORT_MAP`）

若某一个 proxy 的绕过服务监听的端口不是 `CF_BYPASS_SERVICE_PORT`，可以只为该 proxy
覆盖端口 —— 以 proxy IP 作为键：

```python
# 在 config.py 中 —— 默认为 {}（所有 proxy 都用 CF_BYPASS_SERVICE_PORT）
CF_BYPASS_PORT_MAP = {'10.0.0.5': 9001}
```

这会让该 proxy 的绕过 URL 变为 `http://10.0.0.5:9001`；若 `CF_BYPASS_VIA_PROXY = True`，
则为经由该 proxy 隧道访问的 `http://127.0.0.1:9001`。其他 proxy 不受影响。它主要用于
灰度上线：部分主机上的 solver 监听的端口与其余主机不同。

在 GitHub Actions 中该值来自 `CF_BYPASS_PORT_MAP_JSON` 变量。目前没有任何工作流设置它，
因此 CI 运行使用的是空默认值。

## 绕过服务不可达时

spider 拨号绕过服务时使用 **5 秒 connect timeout**。若 TCP 连接失败（服务未运行，
或 8000 端口被防火墙拦截），该 proxy 的绕过会被标记为不可达，并在本次 run 剩余时间内跳过：

```text
[CF Bypass] Proxy=Jeddah-ARM1: service unreachable at http://144.xxx.xxx.88:8000
  (ConnectTimeout) — bypass disabled for this proxy for the rest of the run
```

该 proxy 本身仍会用于直连请求——只是跳过它的绕过服务。这个标记在一次 run 内不会过期，
因此 run 中途重启的绕过服务要到下一次 run 才会被重新使用。

若每个 proxy 都出现这条警告，说明整个绕过层都挂了；先修好入站规则或服务，再期待受
验证保护的页面能被解析。相关事故记录见 [BFR-024](https://github.com/TongWu/JAVDB_AutoSpider_CICD/blob/main/docs/design/BFR-024-CF-Managed-Challenge-Blind-Spot/BFR-024-cf-managed-challenge-blind-spot.zh.md)。

## 全站验证不会 ban 代理

Cloudflare 验证页（`Just a moment...`，或旧版的 `Security Verification` 页）对每个出口
IP 一视同仁，因此**不会**算到抓取它的那个 proxy 头上：

- 不做本地软 ban。
- **不会向 proxy coordinator 上报 per-proxy 的 `cf` 事件。** 每个 proxy 面对的是同一堵墙，
  按 proxy 上报会让它们同时越过自动 ban 阈值，为一件没有任何 proxy 造成的事情 ban 掉整个
  代理池。该验证转而驱动 run 级别的绕过优先切换。
- 本地节流仍然生效 —— penalty tracker 照常记录该事件，因此在 Cloudflare 施压期间 run 会
  持续退避。

Cloudflare 的 *block* 页（error 1020，"Sorry, you have been blocked"）是 IP 特定的，
仍然会算到该 proxy 头上。

## 诊断绕过层

`apps.cli.ops.cf_bypass_probe` 会扫描整个代理池，报告每个 proxy 的绕过服务实际返回了什么
—— 包括返回的内容究竟是真实页面，还是以 200 伪装成功的验证页。当受验证保护的页面无法解析、
日志里又看不出明显原因时，就用它：

```bash
# 对 PROXY_POOL 中的每个 proxy 做协议探测
python3 -m apps.cli.ops.cf_bypass_probe

# 在一个 proxy 上对比两个 solver 端口，每个端口各 5 次试探
python3 -m apps.cli.ops.cf_bypass_probe --proxy Singapore-ARM1 --bench 5 --ports 8000,8002
```

完整参数见 [CLI 参考手册](../developer/cli-reference.md#cf-bypass-probe-cli)；
要从 CI 运行它，见 [GitHub Actions 部署](github-actions-setup.md) 中的 `CFBypassProbe.yml`。

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
- 确认该主机响应的是 `GET /html?url=`（CloudflareBypassForScraping），
  而不是 `POST /v1`（FlareSolverr）—— 运行 `apps.cli.ops.cf_bypass_probe`
- 尝试重启 CF 绕过服务

**CF Bypass + Proxy 不工作：**
- 确保 CF 绕过服务运行在 proxy 服务器上
- 确认 proxy IP 提取正确（查看爬虫日志）
- 使用与你的拓扑相符的命令测试 —— 若 `CF_BYPASS_PORT_MAP` 为该 proxy 指定了端口，
  请改用该端口：

```bash
# CF_BYPASS_VIA_PROXY=False —— 服务监听在 proxy 的公网 IP 上
curl "http://proxy_ip:8000/html?url=https%3A%2F%2Fjavdb.com%2F"

# CF_BYPASS_VIA_PROXY=True —— 服务仅绑定回环地址，
# 需经由 proxy 隧道访问
curl -x http://proxy_ip:7890 "http://127.0.0.1:8000/html?url=https%3A%2F%2Fjavdb.com%2F"
```

- 也可以运行 `python3 -m apps.cli.ops.cf_bypass_probe --proxy <name> --verbose`，
  它会自行解析拓扑与该 proxy 的端口
