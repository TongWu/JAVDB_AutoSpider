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
