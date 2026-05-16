# 配置参考

JAVDB AutoSpider 所有配置变量的完整参考。

主配置文件为 **`config.py`**。将 `config.py.example` 复制为 `config.py` 并填入你的值。该文件已被 git 忽略，因此凭据不会被提交。

Web API 和 Docker 的环境变量在[第 14 节](#14-环境变量)中介绍。

---

## 目录

1. [Git 配置](#1-git-配置)
2. [qBittorrent 配置](#2-qbittorrent-配置)
3. [SMTP / 邮件](#3-smtp--邮件)
4. [Proxy 配置](#4-proxy-配置)
5. [CloudFlare 绕过](#5-cloudflare-绕过)
6. [爬虫配置](#6-爬虫配置)
7. [JavDB 登录](#7-javdb-登录)
8. [日志](#8-日志)
9. [解析 / 洗版](#9-解析--洗版)
10. [文件路径 / 数据库路径](#10-文件路径--数据库路径)
11. [PikPak](#11-pikpak)
12. [Rclone / 去重](#12-rclone--去重)
13. [qBittorrent 文件过滤器](#13-qbittorrent-文件过滤器)
14. [环境变量](#14-环境变量)

---

## 1. Git 配置

用于将报告和历史文件推送到 GitHub 仓库。

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `GIT_USERNAME` | `str` | `''` | GitHub 用户名。 |
| `GIT_PASSWORD` | `str` | `''` | GitHub 密码或个人访问令牌（PAT）。建议使用具有 `repo` 权限的 PAT 而非密码。在 *GitHub Settings > Developer settings > Personal access tokens* 中生成。 |
| `GIT_REPO_URL` | `str` | `''` | 仓库的完整 HTTPS 克隆 URL，例如 `https://github.com/user/repo.git`。 |
| `GIT_BRANCH` | `str` | `'main'` | 推送的目标分支。 |

---

## 2. qBittorrent 配置

控制上传器如何连接 qBittorrent Web UI 并添加种子。

### 主实例

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `QB_URL` | `str` | `''` | qBittorrent Web UI 的完整 URL（含协议），例如 `https://192.168.1.100:8080`。如果省略协议，应用会先尝试 HTTPS，失败后自动重试 HTTP。 |
| `QB_ALLOW_INSECURE_HTTP` | `bool` | `False` | 当 `QB_URL` 使用 `http://` 时设置为 `True`。验证时必需；`config_generator` 在基于 HTTP URL 生成时会自动设置。 |
| `QB_VERIFY_TLS` | `bool` | `True` | 连接 qBittorrent 时是否验证 TLS 证书。自签名证书请设为 `False`。 |
| `QB_USERNAME` | `str` | `''` | qBittorrent Web UI 用户名。 |
| `QB_PASSWORD` | `str` | `''` | qBittorrent Web UI 密码。 |

### 种子设置

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `TORRENT_CATEGORY` | `str` | `'JavDB'` | 每日模式下添加种子时分配的分类。 |
| `TORRENT_CATEGORY_ADHOC` | `str` | `'Ad Hoc'` | 临时模式下添加种子时分配的分类。 |
| `TORRENT_SAVE_PATH` | `str` | `''` | 自定义下载保存路径。留空则使用 qBittorrent 默认路径。 |
| `AUTO_START` | `bool` | `True` | 添加后立即开始下载。设为 `False` 则以暂停状态添加。 |
| `SKIP_CHECKING` | `bool` | `False` | 添加种子时跳过哈希校验。 |

### 临时实例（可选）

专用于临时抓取的第二个 qBittorrent 实例。配置后，`pikpak_bridge` 会同时扫描主实例和临时实例。临时实例仅限 "Ad Hoc" 分类。

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `QB_URL_ADHOC` | `str` | `''` | 临时 qBittorrent 实例的 URL。留空则禁用。 |
| `QB_USERNAME_ADHOC` | `str` | `''` | 临时实例的用户名。为空时回退到 `QB_USERNAME`。 |
| `QB_PASSWORD_ADHOC` | `str` | `''` | 临时实例的密码。为空时回退到 `QB_PASSWORD`。 |

### 连接设置

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `REQUEST_TIMEOUT` | `int` | `30` | qBittorrent API 请求的超时时间（秒）。 |
| `DELAY_BETWEEN_ADDITIONS` | `int` | `1` | 连续添加种子之间的延迟（秒）。 |

---

## 3. SMTP / 邮件

邮件通知设置。流水线运行后会发送新种子摘要通知。

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `SMTP_SERVER` | `str` | `'smtp.gmail.com'` | SMTP 服务器主机名。 |
| `SMTP_PORT` | `int` | `587` | SMTP 服务器端口。Gmail 默认使用 `587`（STARTTLS），或使用 `465`（SSL）。 |
| `SMTP_USER` | `str` | `''` | SMTP 登录用户名（通常是你的邮箱地址）。 |
| `SMTP_PASSWORD` | `str` | `''` | SMTP 登录密码。对于 Gmail，请使用应用专用密码（先启用两步验证，然后在 *Google Account > Security > App passwords* 中生成）。 |
| `EMAIL_FROM` | `str` | `''` | 通知邮件中显示的发件人地址。 |
| `EMAIL_TO` | `str` | `''` | 通知邮件的收件人地址。 |

---

## 4. Proxy 配置

### Proxy 模式

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `PROXY_MODE` | `str` | `'pool'` | 控制 proxy 的使用方式。**`'pool'`**（默认）—— 使用 `PROXY_POOL` 中的所有 proxy，自动故障转移。**`'single'`** —— 仅使用 `PROXY_POOL` 中的第一个 proxy。**`'None'`** —— 完全禁用 proxy（直连）。 |

### Proxy 池

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `PROXY_POOL` | `list[dict]` | *（见下方）* | Proxy 字典列表。每个条目包含 `name`（可选标签）、`http` 和 `https` 键。`single` 模式下仅使用第一个条目。`pool` 模式下所有条目参与自动故障转移。支持的协议：`http://`、`https://`、`socks5://`（需要 `pip install requests[socks]`）。认证 proxy 使用 `http://user:pass@host:port` 格式 —— 密码中的特殊字符需要 URL 编码（例如 `@` 编码为 `%40`）。 |
| `PROXY_POOL_MAX_FAILURES` | `int` | `3` | 当前会话中 proxy 被封禁前的最大连续失败次数。封禁记录仅存于内存中（会话范围），每次新运行时重置。仅在 `PROXY_MODE = 'pool'` 时生效。 |

**默认 `PROXY_POOL` 结构：**

```python
PROXY_POOL = [
    {
        'name': 'Main-Proxy',
        'http': 'http://127.0.0.1:7890',
        'https': 'http://127.0.0.1:7890'
    },
    {
        'name': 'Backup-Proxy-1',
        'http': 'http://127.0.0.1:7891',
        'https': 'http://127.0.0.1:7891'
    },
]
```

### 旧版 Proxy 设置（已弃用）

为向后兼容而保留。如果设置了这些变量，它们会覆盖 `PROXY_POOL` 中的第一个条目。

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `PROXY_HTTP` | `str \| None` | `None` | 已弃用。请改用 `PROXY_POOL`。 |
| `PROXY_HTTPS` | `str \| None` | `None` | 已弃用。请改用 `PROXY_POOL`。 |

### Proxy 模块控制

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `PROXY_MODULES` | `list[str]` | `['spider']` | 在自动模式下哪些模块通过 proxy 路由流量。可用模块：`'spider'`（所有 JavDB 请求，包括登录/会话刷新）、`'qbittorrent'`（qBittorrent Web UI API）、`'pikpak'`（PikPak 桥接 API）。使用 `['all']` 代理所有模块，或使用 `[]` 默认禁用所有模块的 proxy。CLI 标志 `--use-proxy` / `--no-proxy` 可按运行覆盖此设置。 |

### Proxy Coordinator（Cloudflare Worker）

跨运行器的 proxy 协调，用于限流同步、封禁共享、登录状态和影片认领互斥锁。两者都留空即可禁用。

在 GitHub Actions 中，设置仓库变量 `PROXY_COORDINATOR_URL` 和 Secret `PROXY_COORDINATOR_TOKEN`；`config_generator` 会将它们写入 `config.py`。

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `PROXY_COORDINATOR_URL` | `str` | `''` | Cloudflare Worker proxy coordinator 的 URL。 |
| `PROXY_COORDINATOR_TOKEN` | `str` | `''` | 用于 proxy coordinator 认证的 Bearer token。 |

### 影片认领互斥锁

每日互斥锁，防止并发运行器之间的重复工作。

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `MOVIE_CLAIM_ENABLED` | `str` | `'auto'` | 三态控制（不区分大小写，自动去除空格）。**`'auto'`**（默认）—— 仅当 Runner Registry 报告有足够的活跃运行器时才挂载认领互斥锁（由 Worker 变量 `MOVIE_CLAIM_MIN_RUNNERS` 控制，默认为 2）。单运行器部署零开销。**`'true'` / `'1'` / `'yes'`** —— 强制启用，无条件挂载。在混合部署窗口期很有用。**`'false'` / `'0'` / `'no'` / `''`** —— 强制禁用，等同于"未配置 coordinator"。注意：*未设置*默认为 `'auto'`；*空字符串*是显式的强制禁用。通过 `config_generator` 从 GH Variable `MOVIE_CLAIM_ENABLED` 获取。 |

### Runner Registry

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `RUNNER_REGISTRY_ENABLED` | `str` | `'false'` | 当为 `'true'` 时，爬虫在启动时向 Cloudflare `RunnerRegistry` Durable Object 注册自身，每 60 秒发送心跳，退出时注销。这使得运行对等方可以发现该运行，用于 MovieClaim 自动挂载/卸载、proxy 池漂移检测和队列汇总。通过 `config_generator` 从 GH Variable `RUNNER_REGISTRY_ENABLED` 获取。 |

---

## 5. CloudFlare 绕过

[CloudflareBypassForScraping](https://github.com/sarperavci/CloudflareBypassForScraping) 服务的配置。该服务必须使用相同端口部署在每台 proxy 服务器上。

完整的服务 URL 在运行时动态构建：
- 无 proxy：`http://localhost:{CF_BYPASS_SERVICE_PORT}`
- 使用 proxy 池：`http://{PROXY_IP}:{CF_BYPASS_SERVICE_PORT}`（使用当前 proxy 的 IP）

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `CF_BYPASS_SERVICE_PORT` | `int` | `8000` | CloudFlare 绕过服务监听的端口。必须与服务 `docker-compose.yml` 中配置的端口一致。 |

---

## 6. 爬虫配置

控制抓取阶段的页面范围和过滤阈值。

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `PAGE_START` | `int` | `1` | 起始抓取页码。 |
| `PAGE_END` | `int` | `20` | 结束抓取页码（含）。 |
| `PHASE2_MIN_RATE` | `float` | `4.0` | Phase 2（高评分非字幕条目）中影片的最低用户评分。 |
| `PHASE2_MIN_COMMENTS` | `int` | `100` | Phase 2 中影片的最低评论数。 |
| `BASE_URL` | `str` | `'https://javdb.com'` | JavDB 基础 URL。仅在使用镜像站时更改。 |

---

## 7. JavDB 登录

自动刷新会话 cookie 的凭据和设置。自定义 URL 抓取（如演员页面、用户观看列表）时必需。

### 凭据

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `JAVDB_USERNAME` | `str` | `''` | JavDB 邮箱或用户名。 |
| `JAVDB_PASSWORD` | `str` | `''` | JavDB 密码。 |
| `JAVDB_SESSION_COOKIE` | `str` | `''` | JavDB `_jdb_session` cookie 值。可从浏览器开发者工具（*Application > Cookies*）手动设置，或由登录脚本自动更新。 |

### 基于 GPT 的验证码识别

登录流程使用 GPT-4o Vision 识别验证码图片。支持任何 OpenAI 兼容的 API 端点（例如 `api.gpt.ge`、`api.openai.com`）。

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `GPT_API_URL` | `str` | `'https://api.gpt.ge/v1/chat/completions'` | 用于验证码识别的 OpenAI 兼容 chat completions 端点。 |
| `GPT_API_KEY` | `str` | `''` | GPT 端点的 API 密钥（例如 `sk-xxx`）。留空则回退到手动验证码输入。 |

### 登录重试策略

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `LOGIN_ATTEMPTS_PER_PROXY_LIMIT` | `int` | `6` | 单个 proxy 每次运行的最大登录刷新尝试次数。全局预算初始为 `len(PROXY_POOL) * LOGIN_ATTEMPTS_PER_PROXY_LIMIT`，当 proxy 被封禁时减少。 |
| `LOGIN_MAX_FAILURES_BEFORE_PROXY_SWITCH` | `int` | `3` | 触发 proxy 切换的过期会话失败次数。 |

### 登录验证

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `LOGIN_VERIFICATION_URLS` | `list[str]` | `['/users/want_watch_videos', '/users']` | 登录成功后请求的 URL，用于验证 cookie 有效性。可以是绝对 URL 或相对于 `BASE_URL` 的路径。只有所有 URL 都返回非登录响应时，登录才被视为已验证。设为 `[]` 可禁用验证（旧版行为）。 |

### 休眠调优

请求之间的休眠间隔由自适应 `MovieSleepManager` 自动调优。通常无需手动配置。在 CI 或测试中，可通过环境变量 `VAR_MOVIE_SLEEP` 覆盖（例如 `"0,0"`）。所有冷却时间（CloudFlare、回退、登录重试）均由休眠管理器自适应派生。

---

## 8. 日志

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `LOG_LEVEL` | `str` | `'INFO'` | 最低日志级别。可选 `DEBUG`、`INFO`、`WARNING`、`ERROR`。 |
| `SPIDER_LOG_FILE` | `str` | `'logs/spider.log'` | 爬虫模块的日志文件路径。 |
| `UPLOADER_LOG_FILE` | `str` | `'logs/qb_uploader.log'` | qBittorrent 上传器的日志文件路径。 |
| `PIPELINE_LOG_FILE` | `str` | `'logs/pipeline.log'` | 流水线编排器的日志文件路径。 |
| `EMAIL_NOTIFICATION_LOG_FILE` | `str` | `'logs/email_notification.log'` | 邮件通知的日志文件路径。 |

其他日志行为由环境变量控制（见[第 14 节](#14-环境变量)）：
`LOG_STYLE`（`compact` | `plain` | `verbose`）和
`LOG_GITHUB_GROUPS`（`on` | `off` | `auto`）。

---

## 9. 解析 / 洗版

控制哪些影片包含在报告中，以及洗版逻辑是否启用。

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `IGNORE_RELEASE_DATE_FILTER` | `bool` | `False` | 为 `True` 时，解析所有带字幕标签的条目，不受发布日期限制。为 `False` 时，仅解析同时具有字幕标签和今天/昨天发布标签的条目。也可通过 CLI 标志 `--ignore-release-date` 设置。 |
| `INCLUDE_DOWNLOADED_IN_REPORT` | `bool` | `False` | 为 `True` 时，即使影片的所有种子分类已下载（标记为 `[DOWNLOADED PREVIOUSLY]`），仍将其包含在报告中。为 `False` 时，跳过已全部下载的影片。 |
| `ENABLE_REDOWNLOAD` | `bool` | `False` | 启用洗版模式。启用后，爬虫会检查同分类种子是否显著大于之前下载的版本，并触发重新下载。 |
| `REDOWNLOAD_SIZE_THRESHOLD` | `float` | `0.30` | 洗版的大小增长阈值。`0.30` 表示新种子必须至少比现有种子大 30% 才能触发洗版。仅在 `ENABLE_REDOWNLOAD = True` 时生效。 |

---

## 10. 文件路径 / 数据库路径

除非给出绝对路径，否则所有路径相对于仓库根目录。

### 目录

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `REPORTS_DIR` | `str` | `'reports'` | 所有报告、历史文件和数据库的根目录。 |
| `DAILY_REPORT_DIR` | `str` | `'reports/DailyReport'` | 每日 CSV 报告的输出目录。报告存储在 `YYYY/MM/` 子目录中。 |
| `AD_HOC_DIR` | `str` | `'reports/AdHoc'` | 临时 CSV 报告的输出目录。报告存储在 `YYYY/MM/` 子目录中。 |

### 数据库文件

系统使用三个独立的 SQLite 数据库，以实现并发和隔离。

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `HISTORY_DB_PATH` | `str` | `'reports/history.db'` | 历史数据库路径，包含 `MovieHistory` 和 `TorrentHistory` 表。 |
| `REPORTS_DB_PATH` | `str` | `'reports/reports.db'` | 报告数据库路径，包含 `ReportSessions`、`ReportMovies`、`ReportTorrents`、`SpiderStats`、`UploaderStats` 和 `PikpakStats` 表。 |
| `OPERATIONS_DB_PATH` | `str` | `'reports/operations.db'` | 操作数据库路径，包含 `RcloneInventory`、`DedupRecords` 和 `PikpakHistory` 表。 |

### 旧版文件

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `PARSED_MOVIES_CSV` | `str` | `'parsed_movies_history.csv'` | 旧版已解析影片 CSV 的文件名（存储在 `REPORTS_DIR` 中）。 |

---

## 11. PikPak

PikPak 云下载桥接的配置。桥接从 qBittorrent 读取磁力链接并将其作为离线下载提交到 PikPak。

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `PIKPAK_EMAIL` | `str` | `''` | PikPak 账户邮箱。 |
| `PIKPAK_PASSWORD` | `str` | `''` | PikPak 账户密码。 |
| `PIKPAK_LOG_FILE` | `str` | `'logs/pikpak_bridge.log'` | PikPak 桥接操作的日志文件路径。 |
| `PIKPAK_REQUEST_DELAY` | `int` | `2` | PikPak 离线下载请求之间的延迟（秒），以避免触发频率限制。 |
| `PIKPAK_ROOT_FOLDER` | `str` | `'/Javdb_AutoSpider'` | PikPak 离线下载的根文件夹。种子存储在 `{PIKPAK_ROOT_FOLDER}/{qB category}` 下，例如 qBittorrent 分类为 "Ad Hoc" 的种子存放在 `/Javdb_AutoSpider/Ad Hoc`。缺失的文件夹会自动创建。可在运行时通过 `--root-folder` 或 GitHub Variable `PIKPAK_ROOT_FOLDER` 覆盖。 |

---

## 12. Rclone / 去重

Google Drive 库存扫描和重复文件清理的设置。

### Rclone

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `RCLONE_CONFIG_BASE64` | `str` | `''` | Base64 编码的 `rclone.conf` 内容。生成方式：`base64 -w0 ~/.config/rclone/rclone.conf`。 |
| `RCLONE_FOLDER_PATH` | `str` | `'gdrive:/...'` | `remote:path` 格式的远程根路径，例如 `'gdrive:/Movies/JAV-Sync'` 或 `'gdrive:'` 表示远程根目录。 |

### 去重

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `RCLONE_INVENTORY_CSV` | `str` | `'rclone_inventory.csv'` | Rclone 库存 CSV 的文件名（存储在 `REPORTS_DIR` 中）。 |
| `DEDUP_CSV` | `str` | `'dedup.csv'` | 去重记录的文件名（存储在 `REPORTS_DIR` 中，跨运行持久化）。 |
| `DEDUP_LOG_FILE` | `str` | `'logs/rclone_dedup.log'` | Rclone 去重执行器的日志文件路径。 |

---

## 13. qBittorrent 文件过滤器

文件过滤器将种子中的小文件（NFO、样本、截图等）设为"不下载"优先级。

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `QB_FILE_FILTER_MIN_SIZE_MB` | `int` | `100` | 最小文件大小（MB）。小于此阈值的文件将被设为"不下载"优先级。 |
| `QB_FILE_FILTER_LOG_FILE` | `str` | `'logs/qb_file_filter.log'` | 文件过滤器脚本的日志文件路径。 |

---

## 14. 环境变量

这些变量在 `.env` 文件中设置，而非 `config.py`。

### 14.1 根目录 `.env`（Web API + Docker）

在仓库根目录的 `.env.example` 中定义。供 FastAPI 服务器（`apps/api/`）和 Docker Compose 使用。

#### Web API / 管理控制台

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `API_SECRET_KEY` | `str` | *（无）* | JWT 签名密钥。**生产环境必需。** 生成方式：`openssl rand -base64 48`。 |
| `ADMIN_USERNAME` | `str` | `'admin'` | Web 控制台的管理员用户名。 |
| `ADMIN_PASSWORD` | `str` | *（无）* | 管理员密码（明文）。与 `ADMIN_PASSWORD_HASH` 互斥。 |
| `ADMIN_PASSWORD_HASH` | `str` | *（无）* | 管理员密码的预计算 bcrypt 哈希。`ADMIN_PASSWORD` 的替代方案。 |
| `READONLY_USERNAME` | `str` | *（无）* | 可选的只读用户名。 |
| `READONLY_PASSWORD` | `str` | *（无）* | 可选的只读用户密码。 |
| `SECRETS_ENCRYPTION_KEY` | `str` | *（无）* | 用于在存储时加密敏感配置值的 Fernet 密钥。生成方式：`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`。 |

#### Docker 定时任务调度

所有 cron 表达式使用标准五字段格式：`MINUTE HOUR DAY MONTH WEEKDAY`。

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `CRON_SPIDER` | `str` | `'0 3 * * *'` | 每日爬虫任务的 cron 调度（默认：凌晨 3:00）。 |
| `SPIDER_COMMAND` | `str` | *（见 .env.example）* | 爬虫 cron 任务执行的 Shell 命令。 |
| `CRON_PIPELINE` | `str` | `'0 4 * * *'` | 流水线任务的 cron 调度（默认：凌晨 4:00）。 |
| `PIPELINE_COMMAND` | `str` | *（见 .env.example）* | 流水线 cron 任务执行的 Shell 命令。 |
| `CRON_QBTORRENT` | `str` | `'30 3 * * *'` | qBittorrent 上传器的 cron 调度（默认：凌晨 3:30）。 |
| `QBTORRENT_COMMAND` | `str` | *（见 .env.example）* | qBittorrent cron 任务执行的 Shell 命令。 |
| `CRON_PIKPAK` | `str` | `'0 5 * * *'` | PikPak 桥接的 cron 调度（默认：凌晨 5:00）。 |
| `PIKPAK_COMMAND` | `str` | *（见 .env.example）* | PikPak cron 任务执行的 Shell 命令。 |

#### Docker 任务开关

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `ENABLE_SPIDER` | `str` | `'true'` | 启用或禁用爬虫 cron 任务（`'true'` / `'false'`）。 |
| `ENABLE_PIPELINE` | `str` | `'true'` | 启用或禁用流水线 cron 任务。 |
| `ENABLE_QBTORRENT` | `str` | `'true'` | 启用或禁用 qBittorrent 上传器 cron 任务。 |
| `ENABLE_PIKPAK` | `str` | `'true'` | 启用或禁用 PikPak 桥接 cron 任务。 |

#### Docker 杂项

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `TZ` | `str` | *（无）* | 容器时区，例如 `Asia/Shanghai`、`America/New_York`。 |
| `MAX_LOG_SIZE` | `str` | *（无）* | 日志文件轮转前的最大大小，例如 `100M`。 |
| `MAX_LOG_FILES` | `int` | *（无）* | 保留的轮转日志文件最大数量。 |

### 14.2 Shell / CI 环境变量

在 Shell 或 GitHub Actions 工作流文件中设置，由各模块在运行时读取。

| 变量 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `STORAGE_BACKEND` | `str` | `'sqlite'` | 存储后端。`'sqlite'` —— 本地 SQLite 文件。`'d1'` —— Cloudflare D1（GitHub Actions）。`'dual'` —— 双写，从 D1 读取。 |
| `WRITE_MODE` | `str` | `'pending'` | 会话管理的写入模式。`'pending'`（默认）—— 将写入暂存到 pending 表，在结束时提交。`'audit'`（旧版）—— 在 upsert 前将旧行保存到 audit 表。 |
| `STRICT_DUAL_WRITE` | `str` | `''` | 设为 `'1'` 时，在 dual 模式下 D1 写入失败会导致运行失败。 |
| `LOG_LEVEL` | `str` | `'INFO'` | 当设为环境变量时，覆盖 `config.py` 中的 `LOG_LEVEL`。 |
| `LOG_STYLE` | `str` | `'compact'` | 日志输出格式。`'compact'` —— 简洁单行。`'plain'` —— 标准格式。`'verbose'` —— 完整四字段格式。 |
| `LOG_GITHUB_GROUPS` | `str` | `'auto'` | GitHub Actions 日志分组。`'on'` —— 始终输出 `::group::` 标记。`'off'` —— 从不。`'auto'` —— 自动检测 CI 环境。 |
| `VAR_MOVIE_SLEEP` | `str` | *（无）* | 覆盖自适应休眠范围，格式为 `"min,max"`（秒），例如 CI 中使用 `"0,0"`。 |
| `JAVDB_AUDIT_WRITES_DISABLED` | `str` | *（无）* | 设置后，阻止直接写入 audit 表（audit 模式仅用于只读取证）。 |

### 14.3 Docker 专用 `.env`（`docker/.env.example`）

`docker/.env.example` 文件提供了 Docker 容器的简化 cron 配置格式。使用与[Docker 定时任务调度](#docker-定时任务调度)中相同的变量，但采用替代的内联格式：

```bash
CRON_DAILY="0 8 * * * python3 pipeline.py --use-proxy"
```

这将调度和命令合并为一个变量。有关更多示例和流水线参数参考，请查看 `docker/.env.example` 中的注释。
