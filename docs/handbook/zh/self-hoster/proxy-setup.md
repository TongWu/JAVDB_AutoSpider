# Proxy 设置

系统支持**单 proxy** 和 **proxy 池**模式。推荐使用池模式以获得更好的可靠性和自动故障转移。

## Proxy 池模式（推荐）

配置多个 proxy，实现自动故障转移和负载分配。

### 快速配置

```python
# 在 config.py 中
PROXY_MODE = 'pool'
PROXY_POOL = [
    {'name': 'Proxy-1', 'http': 'http://127.0.0.1:7890', 'https': 'http://127.0.0.1:7890'},
    {'name': 'Proxy-2', 'http': 'http://127.0.0.1:7891', 'https': 'http://127.0.0.1:7891'},
]
PROXY_POOL_MAX_FAILURES = 3  # 本会话内封禁前的最大失败次数
```

### 池功能

- **自动切换** —— 当一个 proxy 失败时，自动切换到另一个
- **被动健康检查** —— 仅在实际失败时标记 proxy 为失败（无主动探测）
- **冷却机制** —— 失败/被封禁的 proxy 仅在当前进程会话内被跳过（无持久 TTL）；下次运行从零开始
- **封禁检测** —— 通过 HTTP 403 响应和封禁页面 HTML 模式检测封禁；立即将页面重新分配给另一个 worker
- **会话范围封禁** —— 封禁仅存于当前进程的内存中。下次运行时从零开始 —— 无 CSV 文件，无数据库表
- **统计跟踪** —— 每个 proxy 的详细成功率和使用统计

### 封禁行为

当 proxy 在运行期间被封禁时：
- 该 proxy 在当前会话剩余时间内被跳过（仅内存）
- 当所有 proxy 都被封禁时，爬虫以退出码 2 退出
- 流水线邮件报告包含该次运行的 proxy/封禁上下文
- 下次运行会自动重试所有 proxy

在爬虫日志输出（`logs/spider.log`）中观察封禁活动。没有持久化的封禁存储。

## 单 Proxy 模式（旧版）

传统的单 proxy 配置，支持 HTTP/HTTPS/SOCKS5。

```python
# 在 config.py 中
PROXY_MODE = 'single'

# HTTP/HTTPS proxy
PROXY_HTTP = 'http://127.0.0.1:7890'
PROXY_HTTPS = 'http://127.0.0.1:7890'

# 或 SOCKS5 proxy
PROXY_HTTP = 'socks5://127.0.0.1:1080'
PROXY_HTTPS = 'socks5://127.0.0.1:1080'

# 带认证
PROXY_HTTP = 'http://username:password@proxy.example.com:8080'
PROXY_HTTPS = 'http://username:password@proxy.example.com:8080'
```

### 安装 SOCKS5 支持

```bash
pip install requests[socks]
```

## 模块化 Proxy 控制

`PROXY_MODULES` 设置控制哪些组件使用 proxy：

| 模块 | 说明 | 使用场景 |
|--------|-------------|----------|
| `spider` | JavDB 爬虫（包含登录） | 受地域限制的 JavDB 访问 |
| `qbittorrent` | qBittorrent Web UI API | qB 位于防火墙后 |
| `pikpak` | PikPak 桥接操作 | PikPak API 访问 |
| `all` | 所有模块 | 所有流量通过 proxy 路由 |

```python
# 默认：仅 spider
PROXY_MODULES = ['spider']

# Spider + qBittorrent
PROXY_MODULES = ['spider', 'qbittorrent']

# 全部模块
PROXY_MODULES = ['all']

# 默认禁用所有模块的 proxy
PROXY_MODULES = []
```

## 命令行覆盖

命令默认遵循 `PROXY_MODULES` 设置。可按运行覆盖：

```bash
# 自动模式（遵循 config.py）
python3 -m apps.cli.spider

# 强制启用 proxy
python3 -m apps.cli.spider --use-proxy

# 强制禁用 proxy
python3 -m apps.cli.spider --no-proxy

# 流水线覆盖（适用于所有步骤）
python3 -m apps.cli.pipeline --use-proxy
```

Web UI 和任务 API 镜像相同的三态行为：省略两个标志为自动模式，`use_proxy=true` 强制启用，`no_proxy=true` 强制禁用。

## 支持的 Proxy 类型

| 协议 | URL 格式 |
|----------|-----------|
| HTTP | `http://proxy.example.com:8080` |
| HTTPS | `https://proxy.example.com:8080` |
| SOCKS5 | `socks5://proxy.example.com:1080` |

## 故障排查

**500 Internal Server Error：**
- 确认 proxy 正在运行且可访问
- 检查凭据；密码中的特殊字符需要 URL 编码：
  ```python
  from urllib.parse import quote
  password = "My@Pass!"
  encoded = quote(password, safe='')  # My%40Pass%21
  ```
- 手动测试：`curl -x http://user:pass@proxy:port https://javdb.com`

**连接被拒绝或超时：**
- 检查 proxy 服务器是否正在运行：`telnet proxy_ip proxy_port`
- 确认防火墙规则允许连接
- 检查 proxy 是否需要认证

**Proxy 工作但下载失败：**
- 某些 proxy 不支持磁力链接或种子
- 仅对爬虫使用 proxy，qB/PikPak 直连：
  ```python
  PROXY_MODULES = ['spider']
  ```

**密码特殊字符参考：**

| 字符 | 编码后 |
|-----------|---------|
| `@` | `%40` |
| `:` | `%3A` |
| `/` | `%2F` |
| `?` | `%3F` |
| `#` | `%23` |
| `!` | `%21` |
| `+` | `%2B` |
| 空格 | `%20` |
