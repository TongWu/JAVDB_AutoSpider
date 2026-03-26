# JAVDB AutoSpider 前端开发 —— AI 任务规格说明

本文档面向 **AI 开发者**，用于在现有 JAVDB AutoSpider 项目上实现一个**美观、现代的前端**，通过 Web UI 完成配置管理、Daily Ingestion、Adhoc Ingestion 等核心功能的运行与调参。

---

## 一、项目背景与现状

### 1.1 项目做什么

- **JAVDB AutoSpider**：从 JAVDB 自动抓取种子信息，筛选后添加到 qBittorrent，并可选同步到 PikPak、发送邮件报告。
- 当前实现为 **纯命令行**：通过 `pipeline.py`、`scripts/spider`、`scripts/qb_uploader.py` 等脚本及 GitHub Actions 工作流完成全流程。
- 配置通过 **`config.py`** 管理，由 `utils/config_generator.py` 从环境变量生成（支持本地与 GitHub Actions 两套来源）。

### 1.2 前端需要覆盖的“关键功能”

1. **配置管理**：对 `config` 中**每一个选项**进行查看与编辑（含敏感项脱敏展示与安全存储）。
2. **Daily Ingestion**：配置并触发“每日抓取”流程（爬虫 → qBittorrent 上传 → PikPak 桥接 → 邮件通知）。
3. **Adhoc Ingestion**：按用户输入的 URL 与参数，配置并触发“按需抓取”流程。
4. （可选）**健康检查**、**登录/会话刷新**、**qb 文件过滤**、**Rclone 去重**等辅助功能的入口与状态展示。

---

## 一·五、Rust Core 与前端的集成架构

### 1.5.1 总体架构

Rust 核心（`rust_core/`）通过 **PyO3 + maturin** 编译为 Python 原生扩展模块 `javdb_rust_core`（`.so` / `.pyd`），由 Python 直接 `import` 使用——**不是**独立的 HTTP / gRPC 服务。前端通过唯一的 HTTP 入口——**FastAPI 薄层**（`api/server.py`，默认端口 `8100`）——间接调用 Rust 功能。

```
┌──────────┐  HTTP/REST   ┌──────────────────┐  import   ┌─────────────────┐
│ Frontend │ ───────────→ │ FastAPI (Python)  │ ────────→ │ javdb_rust_core  │
│ (Web UI) │              │ api/server.py     │           │ (PyO3 extension) │
└──────────┘              └──────────────────┘           └─────────────────┘
                                   │
                          subprocess.Popen
                                   ↓
                          ┌──────────────────┐
                          │ pipeline.py      │
                          │ scripts/spider│
                          │ scripts/qb_*.py  │
                          └──────────────────┘
```

### 1.5.2 Rust 与 Python 的职责划分

| 领域 | 实现 | 模块路径 | 说明 |
|------|------|----------|------|
| HTML 解析 | **Rust** | `rust_core/src/scraper/` | `parse_index_page`, `parse_detail_page`, `parse_category_page`, `parse_top_page`, `parse_tag_page`, `detect_page_type` |
| 代理管理 | **Rust** | `rust_core/src/proxy/` | `ProxyPool`（轮询 + 冷却 + 自动切换）、`ProxyInfo`、`ProxyBanManager`、IP/URL 脱敏函数 |
| HTTP 客户端 | **Rust** | `rust_core/src/requester/` | `RequestHandler`（直连 / CF Bypass / 重试 / 代理回退）、`RequestConfig`、`ProxyHelper` |
| 历史记录管理 | **Rust** | `rust_core/src/history/` | CSV 读写、去重、限额维护、种子类型判断、下载标记 |
| 数据模型 | **Rust** | `rust_core/src/models.rs` | `MovieIndexEntry`, `MovieDetail`, `MagnetInfo`, `IndexPageResult` 等全部结构体 |
| 流水线编排 | **Python** | `pipeline.py` | 按顺序调用 spider → qb_uploader → pikpak_bridge → email_notification（`subprocess.Popen`） |
| 爬虫业务逻辑 | **Python** | `scripts/spider` | Phase1/Phase2 筛选、翻页、限速控制、CSV 生成（~2700 行） |
| qBittorrent 上传 | **Python** | `scripts/qb_uploader.py` | 种子添加、分类管理 |
| PikPak 桥接 | **Python** | `scripts/pikpak_bridge.py` | 旧种子迁移到 PikPak |
| 邮件通知 | **Python** | `scripts/email_notification.py` | SMTP 发送报告 |
| 配置管理 | **Python** | `config.py`, `utils/config_generator.py` | 从环境变量生成 `config.py`；前端通过 API 读写 |
| 健康检查 / 登录 | **Python** | `scripts/health_check.py`, `scripts/login.py` | SMTP / QB / JAVDB 连通性检查、会话刷新 |

> Python 端的 `utils/infra/proxy_pool.py`、`utils/history_manager.py`、`api/parsers/__init__.py` 均为薄包装——优先 `import javdb_rust_core`，若不可用则回退到纯 Python 实现。

### 1.5.3 前端必须调用的 HTTP 端点

#### 现有端点（`api/server.py`，已实现）

> **认证**：按 **6.4.1** 要求，仅 `GET /api/health`、`POST /api/auth/login` 为免认证；实现认证后，下表除 health 外所有端点 **MUST** 要求 Token/Session。

| 方法 | 路由 | 认证 | 请求体 | 响应 | 说明 |
|------|------|------|--------|------|------|
| GET | `/api/health` | 无 | — | `{"status":"ok","rust_core_available":true}` | 存活探针 + Rust 状态（唯一始终免认证） |
| POST | `/api/parse/index` | Token/Session（6.4） | `HtmlPayload` | `IndexPageResult` dict | 解析首页/列表页 |
| POST | `/api/parse/detail` | Token/Session（6.4） | `HtmlPayload` | `MovieDetail` dict | 解析影片详情页 |
| POST | `/api/parse/category` | Token/Session（6.4） | `HtmlPayload` | `CategoryPageResult` dict | 解析分类页 |
| POST | `/api/parse/top` | Token/Session（6.4） | `HtmlPayload` | `TopPageResult` dict | 解析排行榜页 |
| POST | `/api/parse/tags` | Token/Session（6.4） | `HtmlPayload` | `TagPageResult` dict | 解析标签筛选页 |
| POST | `/api/detect-page-type` | Token/Session（6.4） | `HtmlPayload` | `{"page_type":"index"}` | 检测页面类型 |

`HtmlPayload` 格式：

```json
{"html": "<html>...</html>", "page_num": 1}
```

#### 待实现端点（前端开发时必须依赖，后端需按此规格补全）

| 方法 | 路由 | 认证 | 请求体 | 响应 | 说明 |
|------|------|------|--------|------|------|
| GET | `/api/config` | Token/Session | — | 全量配置 JSON（敏感项脱敏） | 读取当前配置 |
| PUT | `/api/config` | Token/Session | `{"key":"value",...}` | `{"status":"ok"}` | 写入配置并重新生成 `config.py` |
| POST | `/api/tasks/daily` | Token/Session | Daily 参数 JSON | `{"job_id":"...","status":"queued"}` | 触发 Daily Ingestion |
| POST | `/api/tasks/adhoc` | Token/Session | Adhoc 参数 JSON | `{"job_id":"...","status":"queued"}` | 触发 Adhoc Ingestion |
| GET | `/api/tasks/{job_id}` | Token/Session | — | `{"job_id":"...","status":"running","log":"..."}` | 查询任务状态与日志 |
| POST | `/api/health-check` | Token/Session | `{"check_smtp":true,"use_proxy":false}` | 检查结果 | 触发健康检查脚本 |
| POST | `/api/login/refresh` | Token/Session | — | `{"status":"ok","cookie":"..."}` | 刷新 JAVDB 会话 |

### 1.5.4 请求/响应示例

#### Health Check

```
GET /api/health
→ 200 {"status": "ok", "rust_core_available": true}
```

#### Daily Ingestion（待实现）

```
POST /api/tasks/daily
Content-Type: application/json
Authorization: Bearer <token>

{
  "start_page": 1,
  "end_page": 10,
  "phase": "all",
  "use_proxy": true,
  "dry_run": false,
  "mode": "pipeline"
}

→ 202 {
  "job_id": "daily-20260227-143021",
  "status": "queued",
  "created_at": "2026-02-27T14:30:21Z"
}
```

后端收到请求后，异步执行：

```bash
python3 pipeline.py --start-page 1 --end-page 10 --phase all --use-proxy
```

#### Adhoc Ingestion（待实现）

```
POST /api/tasks/adhoc
Content-Type: application/json
Authorization: Bearer <token>

{
  "url": "https://javdb.com/actors/xxxx",
  "start_page": 1,
  "end_page": 5,
  "phase": "all",
  "use_proxy": true,
  "history_filter": false,
  "date_filter": false,
  "dry_run": false
}

→ 202 {
  "job_id": "adhoc-20260227-150012",
  "status": "queued",
  "created_at": "2026-02-27T15:00:12Z"
}
```

后端异步执行：

```bash
python3 pipeline.py --url "https://javdb.com/actors/xxxx" \
  --start-page 1 --end-page 5 --phase all --use-proxy --ignore-release-date
```

#### Config Read（待实现）

```
GET /api/config
Authorization: Bearer <token>

→ 200 {
  "QB_HOST": "localhost",
  "QB_PORT": "8080",
  "QB_PASSWORD": "********",
  "PROXY_POOL": [{"name":"Proxy-1","http":"http://***:***@xxx.xxx.xxx.xxx:12300"}],
  "JAVDB_SESSION_COOKIE": "********",
  ...
}
```

敏感字段（`QB_PASSWORD`, `SMTP_PASSWORD`, `JAVDB_PASSWORD`, `JAVDB_SESSION_COOKIE`, `GPT_API_KEY`, `GIT_PASSWORD`, `PIKPAK_PASSWORD`, `PROXY_POOL` 中的 URL 凭证）返回时应脱敏为 `"********"` 或使用 Rust 端的 `mask_*` 系列函数。

### 1.5.5 性能、并发与超时

| 指标 | 值 | 来源 |
|------|-----|------|
| HTTP 直连超时 | 30 s | `RequestHandler.do_request` |
| CF Bypass 超时 | 60 s | `fetch_with_cf_bypass` |
| CF Cache 刷新超时 | 120 s | `refresh_bypass_cache` |
| 直连最大重试 | 3 次（可配 `max_retries`） | `get_page_direct` |
| CF Bypass 回退最大代理切换 | 5 次 | `get_page_with_cf_bypass` |
| 代理冷却时间 | 默认 300 s，生产配置 691200 s（8 天） | `PROXY_POOL_COOLDOWN_SECONDS` |
| 代理最大连续失败 | 3 次触发冷却 | `PROXY_POOL_MAX_FAILURES` |
| 代理封禁时长 | 7 天（仅当前 session，不持久化） | `ProxyBanManager::BAN_DURATION_DAYS` |
| 翻页间隔 | `PAGE_SLEEP`（默认 5 s） | `config.py` |
| 影片间隔 | `MOVIE_SLEEP`（默认 5 s） | `config.py` |
| Turnstile 冷却 | `CF_TURNSTILE_COOLDOWN`（默认 5 s） | `config.py` |
| 回退冷却 | `FALLBACK_COOLDOWN`（默认 15 s） | `config.py` |
| 历史记录上限 | 默认 1000 条（`maintain_history_limit`） | `history/manager.rs` |
| 线程安全 | `parking_lot::Mutex` + `Arc`，Rust 端 GIL 释放（`py.allow_threads`） | 全部 Rust 模块 |

### 1.5.6 错误处理、超时与重试语义

- **解析端点**（`/api/parse/*`）：同步调用 Rust 解析器；解析失败返回 HTTP 500 + `{"detail": "error message"}`。
- **任务端点**（`/api/tasks/*`）：异步执行。后端启动子进程后立即返回 `job_id`（HTTP 202）。前端需轮询 `/api/tasks/{job_id}` 获取状态。子进程非零退出码 → `status: "failed"`。
- **Rust `RequestHandler.get_page`** 的回退链路（前端无需直接调用，仅影响任务执行耗时）：  
  1. CF Bypass 首次尝试  
  2. 等待 `fallback_cooldown` → 重试 CF Bypass  
  3. Turnstile 检测 → 刷新缓存  
  4. 直连 + 当前代理  
  5. 切换代理（最多 5 次），每次尝试直连 + CF Bypass  
  6. 全部失败 → 返回 `None`（Python 端记录日志并跳过该页面）  
- **HTTP 客户端层**：非 2xx 状态码 → 返回 `None` + 日志记录；网络超时 → 同上。
- **前端重试建议**：对任务端点使用指数退避轮询（初始 2 s，最大 30 s）；解析端点失败可直接重试 1 次。

### 1.5.7 配置管理集成触点

| 操作 | 文件/入口 | 前端关注点 |
|------|-----------|-----------|
| 读配置 | `config.py`（Python 模块，直接 `import`） | API 层读取后以 JSON 返回；敏感字段脱敏 |
| 写配置 | 方案 A：修改 `.env` → 调用 `python3 utils/config_generator.py` 重新生成 `config.py` | 与 GitHub Actions 一致；推荐 |
| 写配置 | 方案 B：直接覆写 `config.py` 对应变量 | 仅本地使用时可选 |
| 敏感字段脱敏 | Rust `mask_*` 函数：`mask_full`, `mask_partial`, `mask_email`, `mask_ip_address`, `mask_proxy_url`, `mask_username`, `mask_server` | UI 展示时调用 |
| 配置字段类型与 key | `utils/config_generator.py` 中 `get_config_map()` 定义完整映射 | 必须与之对齐 |
| 配置分组参考 | 本文档 2.1–2.12 节 | UI 按分组展示 |

### 1.5.8 认证/会话流程

当前 API 层（`api/server.py`）**无认证**——零 JWT、零 Session、零中间件。完整安全要求见 **6.4 安全要求**，此处仅概述会话相关要点：

1. **Web UI 认证**：后端需实现 JWT 或 Session 登录机制（详见 6.4.1）。
2. **JAVDB 会话**：`JAVDB_SESSION_COOKIE` 由 `scripts/login.py` 刷新，存入 `config.py`。前端通过 `/api/login/refresh` 触发刷新，无需直接管理此 Cookie——该接口本身须经 Web UI 认证。

---

## 二、配置（Config）完整清单

前端必须支持对以下**所有配置项**的查看与编辑。配置按**逻辑分组**展示；类型包括：字符串、整数、浮点数、布尔、JSON（如列表/对象）。敏感项（密码、Token、Cookie、代理池等）在 UI 中需脱敏显示与安全输入。

配置来源以 `utils/config_generator.py` 的 `get_config_map()` 及 `config.py.example` 为准，下表为规范清单。

### 2.1 GIT CONFIGURATION

| 配置名 | 类型 | 说明 | 默认/示例 |
|--------|------|------|-----------|
| GIT_USERNAME | string | GitHub 用户名 | `github-actions` |
| GIT_PASSWORD | string | GitHub PAT 或密码（敏感） | 空 |
| GIT_REPO_URL | string | 仓库 URL | `https://github.com/user/repo.git` |
| GIT_BRANCH | string | 推送分支 | `main` |

### 2.2 QBITTORRENT CONFIGURATION

| 配置名 | 类型 | 说明 | 默认/示例 |
|--------|------|------|-----------|
| QB_HOST | string | qBittorrent Web UI 主机 | `localhost` |
| QB_PORT | string/int | 端口 | `8080` |
| QB_USERNAME | string | 用户名 | `admin` |
| QB_PASSWORD | string | 密码（敏感） | 空 |
| TORRENT_CATEGORY | string | Daily 模式分类名 | `Daily Ingestion` |
| TORRENT_CATEGORY_ADHOC | string | Adhoc 模式分类名 | `Ad Hoc` |
| TORRENT_SAVE_PATH | string | 保存路径，空为默认 | 空 |
| AUTO_START | bool | 添加后自动开始 | `True` |
| SKIP_CHECKING | bool | 跳过校验 | `False` |
| REQUEST_TIMEOUT | int | API 请求超时（秒） | `30` |
| DELAY_BETWEEN_ADDITIONS | int | 添加种子间隔（秒） | `1` |

### 2.3 SMTP CONFIGURATION

| 配置名 | 类型 | 说明 | 默认/示例 |
|--------|------|------|-----------|
| SMTP_SERVER | string | SMTP 服务器 | `smtp.gmail.com` |
| SMTP_PORT | int | 端口 | `587` |
| SMTP_USER | string | 登录用户 | 空 |
| SMTP_PASSWORD | string | 密码（敏感） | 空 |
| EMAIL_FROM | string | 发件人 | 空 |
| EMAIL_TO | string | 收件人 | 空 |

### 2.4 PROXY CONFIGURATION

| 配置名 | 类型 | 说明 | 默认/示例 |
|--------|------|------|-----------|
| PROXY_MODE | string | `single` 或 `pool` | `pool` |
| PROXY_POOL | JSON array | 代理列表（敏感，可脱敏） | `[]` |
| PROXY_POOL_COOLDOWN_SECONDS | int | 代理冷却时间（秒） | `691200` |
| PROXY_POOL_MAX_FAILURES | int | 最大连续失败次数 | `3` |
| PROXY_MODULES | JSON array | 使用代理的模块：如 `['spider']`、`['spider','qbittorrent']`、`['all']` | `['spider']` |

### 2.5 CLOUDFLARE BYPASS CONFIGURATION

| 配置名 | 类型 | 说明 | 默认/示例 |
|--------|------|------|-----------|
| CF_BYPASS_SERVICE_PORT | int | CF 绕过服务端口 | `8000` |
| CF_BYPASS_ENABLED | bool | 是否启用 CF 绕过 | `True` |

### 2.6 SPIDER CONFIGURATION

| 配置名 | 类型 | 说明 | 默认/示例 |
|--------|------|------|-----------|
| PAGE_START | int | 起始页 | `1` |
| PAGE_END | int | 结束页 | `10` 或 `20` |
| PHASE2_MIN_RATE | float | Phase2 最低评分 | `4.0` |
| PHASE2_MIN_COMMENTS | int | Phase2 最低评论数 | `85` 或 `100` |
| BASE_URL | string | JAVDB 基础 URL | `https://javdb.com` |

### 2.7 JAVDB LOGIN CONFIGURATION

| 配置名 | 类型 | 说明 | 默认/示例 |
|--------|------|------|-----------|
| JAVDB_USERNAME | string | JAVDB 用户名/邮箱 | 空 |
| JAVDB_PASSWORD | string | 密码（敏感） | 空 |
| JAVDB_SESSION_COOKIE | string | 会话 Cookie（敏感） | 空 |
| GPT_API_URL | string | GPT 兼容 API URL（验证码） | 空 |
| GPT_API_KEY | string | API Key（敏感） | 空 |
| PAGE_SLEEP | int | 翻页间隔（秒） | 默认 `5`（建议 `2`–`15`） |
| MOVIE_SLEEP | int | 影片间间隔（秒，含详情页限速） | `5`–`30` |
| CF_TURNSTILE_COOLDOWN | int | Turnstile 冷却（秒） | 默认 `5`（建议 `5`–`60`；过短可能增加触发风控概率） |
| PHASE_TRANSITION_COOLDOWN | int | 阶段切换冷却（秒） | `30`–`60` |
| FALLBACK_COOLDOWN | int | 回退冷却（秒） | 默认 `15`（建议 `15`–`60`） |

### 2.8 LOGGING CONFIGURATION

| 配置名 | 类型 | 说明 | 默认/示例 |
|--------|------|------|-----------|
| LOG_LEVEL | string | DEBUG/INFO/WARNING/ERROR | `INFO` |
| SPIDER_LOG_FILE | string | 爬虫日志路径 | `logs/spider.log` |
| UPLOADER_LOG_FILE | string | 上传器日志路径 | `logs/qb_uploader.log` |
| PIPELINE_LOG_FILE | string | 流水线日志路径 | `logs/pipeline.log` |
| EMAIL_NOTIFICATION_LOG_FILE | string | 邮件日志路径 | `logs/email_notification.log` |

### 2.9 PARSING CONFIGURATION

| 配置名 | 类型 | 说明 | 默认/示例 |
|--------|------|------|-----------|
| IGNORE_RELEASE_DATE_FILTER | bool | 是否忽略“今日/昨日”发布日期筛选 | `False` |
| INCLUDE_DOWNLOADED_IN_REPORT | bool | 报告中是否包含已下载项 | `False` |

### 2.10 FILE PATHS

| 配置名 | 类型 | 说明 | 默认/示例 |
|--------|------|------|-----------|
| REPORTS_DIR | string | 报告根目录 | `reports` |
| DAILY_REPORT_DIR | string | Daily 报告目录 | `reports/DailyReport` |
| AD_HOC_DIR | string | Adhoc 报告目录 | `reports/AdHoc` |
| PARSED_MOVIES_CSV | string | 历史解析记录 CSV | `parsed_movies_history.csv` |

### 2.11 PIKPAK CONFIGURATION

| 配置名 | 类型 | 说明 | 默认/示例 |
|--------|------|------|-----------|
| PIKPAK_EMAIL | string | PikPak 邮箱 | 空 |
| PIKPAK_PASSWORD | string | 密码（敏感） | 空 |
| PIKPAK_LOG_FILE | string | 日志路径 | `logs/pikpak_bridge.log` |
| PIKPAK_REQUEST_DELAY | int | 请求间隔（秒） | 默认 `2`（过短可能触发 API 限流） |

### 2.12 QBITTORRENT FILE FILTER CONFIGURATION

| 配置名 | 类型 | 说明 | 默认/示例 |
|--------|------|------|-----------|
| QB_FILE_FILTER_MIN_SIZE_MB | int | 最小文件大小（MB），小于则设为不下载 | 默认 `100`（与 `config_generator` / `qb_file_filter` 无 config 回退一致） |
| QB_FILE_FILTER_LOG_FILE | string | 文件过滤日志路径 | `logs/qb_file_filter.log` |

---

## 三、Daily Ingestion（每日抓取）

### 3.1 含义

- 不传自定义 URL，按 **config 中的 BASE_URL + 分页** 抓取“今日/昨日”新作，经 Phase1（字幕+今日）、Phase2（今日高分）筛选，生成 CSV → 上传 qBittorrent → 可选 PikPak 桥接 → 邮件报告。

### 3.2 入口与参数

- **推荐入口**：`pipeline.py`（会依次执行 spider → qb_uploader → pikpak_bridge → email_notification）。
- **仅爬虫**：`scripts/spider`（不带上传与邮件）。

前端需支持以下参数（与 `pipeline.py` / `scripts/spider` 的 CLI 一致）：

| 参数 | 类型 | 说明 | 默认 |
|------|------|------|------|
| start_page | int | 起始页 | config 中 `PAGE_START`（旧键 `START_PAGE` 仍可由 `scripts/spider/runtime/config.py` 回退读取） |
| end_page | int | 结束页 | config 中 `PAGE_END`（旧键 `END_PAGE` 同上） |
| all | bool | 是否抓取到空页为止（忽略 end_page） | false |
| ignore_history | bool | 是否忽略历史文件仍写入历史 | false |
| phase | choice | `1` / `2` / `all` | `all` |
| output_file | string | 指定输出 CSV 文件名（可选） | 按日期自动 |
| dry_run | bool | 只打印不写 CSV/不实际上传 | false |
| ignore_release_date | bool | 忽略今日/昨日标签 | false |
| use_proxy | bool | 启用代理 | false |
| always_bypass_time | int | fallback 成功后持续使用 bypass 的分钟数（空=禁用，0=本次 session） | 空 |
| max_movies_phase1 | int | Phase1 最大处理影片数（测试用） | 无限制 |
| max_movies_phase2 | int | Phase2 最大处理影片数（测试用） | 无限制 |
| pikpak_individual | bool | PikPak 是否按单个种子处理 | false |

前端应提供“运行完整 Pipeline（Spider + 上传 + PikPak + 邮件）”与“仅运行 Spider”两种执行模式选项（对应是否只调 spider 还是调 pipeline）。

### 3.3 实现要点

- 后端根据前端提交的参数，在**项目根目录**下生成或补全环境变量，再调用 `python3 utils/config_generator.py` 生成 `config.py`（若采用“先写 env 再生成 config”的方案）。
- 然后执行：  
  `python3 pipeline.py [--start-page N] [--end-page N] [--phase 1|2|all] [--use-proxy] ...`  
  或仅：  
  `python3 scripts/spider --from-pipeline [相同参数]`  
- 运行环境需与当前项目一致（Python 3.11、依赖见 `requirements.txt`）；长时间任务建议异步执行并轮询状态或通过 WebSocket/SSE 流式输出日志。

---

## 四、Adhoc Ingestion（按需抓取）

### 4.1 含义

- 用户提供**自定义 URL**（如某演员页、某合集页），只抓取该 URL 下的页面，生成 CSV 并上传到 qBittorrent（Ad Hoc 分类），可选 PikPak 与邮件。

### 4.2 入口与参数

- **入口**：`pipeline.py --url <URL> [其他参数]`（内部会传 `--url` 给 spider，并令 uploader 使用 adhoc 模式）。
- 前端需支持的参数（与 `pipeline.py` 及 `AdHocIngestion.yml` 的 workflow_dispatch inputs 对齐）：

| 参数 | 类型 | 说明 | 默认 |
|------|------|------|------|
| url | string | 目标 URL（必填），如 `https://javdb.com/actors/xxx` 或 `https://javdb.com/video_codes/xxx` | - |
| start_page | int | 起始页 | 1 |
| end_page | int | 结束页（空表示仅第一页或与 start 同） | 1 或空 |
| history_filter | bool | 是否启用历史过滤（跳过已在历史中的条目） | false |
| date_filter | bool | 是否启用发布日期过滤（仅今日/昨日） | false（adhoc 常为 false） |
| phase | choice | `1` / `2` / `all` | `all` |
| use_proxy | bool | Spider 使用代理 | true |
| always_bypass_time | int | fallback 成功后持续使用 bypass 的分钟数（空=禁用，0=本次 session） | 空 |
| proxy_uploader | bool | 上传器使用代理 | false |
| proxy_pikpak | bool | PikPak 使用代理 | false |
| qb_category | string | 覆盖 qBittorrent 分类（空则用 TORRENT_CATEGORY_ADHOC） | 空 |
| dry_run | bool | 仅打印不写 CSV/不上传 | false |
| ignore_release_date | bool | 忽略发布日期筛选 | 常 true（adhoc） |
| max_movies_phase1 / max_movies_phase2 | int | 同 Daily | 可选 |

注意：`history_filter` 对应 spider 的 `--use-history`；`date_filter` 为 true 时不要传 `--ignore-release-date`，为 false 时传 `--ignore-release-date`。

### 4.3 实现要点

- 后端必须校验 `url` 存在且为合法 JAVDB 链接（可限制 host 为 javdb.com）。
- 执行：  
  `python3 pipeline.py --url <url> --start-page N --end-page M [--ignore-release-date] [--use-history] [--phase ...] [--use-proxy] ...`  
- Adhoc 的 CSV 输出路径由 spider 按 `AD_HOC_DIR` 与日期生成，pipeline 会从 spider 输出中解析 `SPIDER_OUTPUT_CSV=...` 并传给 uploader。

---

## 五、其他可暴露功能（建议）

- **健康检查**：`python3 scripts/health_check.py [--check-smtp] [--use-proxy]`，前端提供按钮，展示输出或“通过/失败”状态。
- **JAVDB 登录/会话刷新**：`python3 scripts/login.py`，用于刷新 `JAVDB_SESSION_COOKIE`；前端可提供“刷新会话”按钮并提示需要 JAVDB_USERNAME/JAVDB_PASSWORD 与可选 GPT API。
- **qBittorrent 文件过滤**：`python3 scripts/qb_file_filter.py --min-size N [--days D] [--use-proxy] [--dry-run] [--categories '["Ad Hoc","Daily Ingestion"]']`，可选在 UI 中提供表单并显示最近运行结果。
- **Rclone 管理**：`scripts/rclone_manager.py` 通过 `--scan` / `--report` / `--execute` 三个可组合 flag 实现六种模式，参数较多，可作为“高级/工具”页的可选功能，或仅提供文档链接与命令行示例。

---

## 六、前端与后端架构要求

### 6.1 后端 API 层（已完成）

- **状态**：API 层已创建完成。
- 在**项目根目录**或指定子目录（如 `web/` 或 `backend/`）的薄 API 服务（推荐 **FastAPI** 或 **Flask**）职责包括：
  - **配置的读写**：  
    - 读：从当前 `config.py` 或从环境变量 + `config_generator` 的映射反推可编辑配置（注意敏感项脱敏）。  
    - 写：接收前端提交的键值对，写入到“配置存储”（见下），再调用 `config_generator` 生成 `config.py`，或直接写 `config.py`（需与 `get_config_map()` 的 key 与类型一致）。
  - **配置存储**：  
    - 若希望与 GitHub Actions 一致，可维护一份“.env”或 JSON 文件，再通过 `config_generator` 生成 `config.py`；  
    - 若仅本地使用，也可直接读写 `config.py`（注意安全与备份）。
  - **任务执行**：  
    - Daily/Adhoc：在项目根目录、正确虚拟环境中执行 `pipeline.py` 或 `scripts/spider`，建议**异步**（后台进程或任务队列），并返回 job_id。  
    - 提供“任务状态/日志”接口：按 job_id 返回运行状态及最近日志（可从 `logs/` 读取或捕获子进程 stdout/stderr）。
  - **健康检查 / 登录 / 文件过滤**：对应脚本的调用与输出返回。
- API 的认证、授权、加密、输入校验等安全要求见 **6.4 安全要求**。

### 6.2 前端（由 AI 实现）

- **技术栈**：不限，但需**美观、现代**（如 React/Vue/Svelte + 现代 UI 库，或轻量 HTML+CSS+JS）。避免“默认 Bootstrap 模板”式外观。
- **UI 美化与技术参考**：页面风格与实现可参考 Docker 镜像 **`mdcng/mdc`** 的界面与交互，作为 UI 美化和前端技术实现的参考。
- **页面与功能**：
  1. **仪表盘/首页**：概览（最近 Daily/Adhoc 运行状态、健康检查状态、快捷入口）。
  2. **配置页**：  
     - 按上面 2.1–2.12 分组展示**所有**配置项；  
     - 每项：类型对应输入（文本/数字/开关/JSON 等），敏感项脱敏显示与“仅修改时输入”的占位符；  
     - 保存后调用后端“写配置”并视需要重新生成 `config.py`。
  3. **Daily Ingestion 页**：  
     - 表单包含 3.2 节所有参数；  
     - 可选“仅 Spider”/“完整 Pipeline”；  
     - 提交后触发任务，展示任务 ID、状态与实时/最近日志。
  4. **Adhoc Ingestion 页**：  
     - 表单包含 4.2 节所有参数，URL 必填并做基本校验；  
     - 提交后触发任务，同样展示状态与日志。
  5. **（可选）任务历史/日志页**：按时间或 job_id 查看历史任务与完整日志。
  6. **（可选）健康检查、登录、qb 文件过滤**：独立区域或页面，按钮 + 结果展示。

### 6.3 部署与运行

- 前端可静态构建后由后端 serve，或单独用任意静态服务器；开发时可用后端 CORS 支持前后端分离。
- 后端运行环境：项目根目录、`python3`（建议 3.11）、`pip install -r requirements.txt`，且能执行 `config_generator`、`pipeline`、`spider` 等；若用 Docker，需在镜像内包含项目代码与依赖，并在容器内以项目根为工作目录执行上述命令。
- 默认前后端会运行在docker内，请对运行在docker内做细致的优化。

### 6.4 安全要求

> **现状**：当前 `api/server.py` 无任何认证、加密或限流机制；`config.py` 以 **明文 Python 变量** 存储所有凭证。以下规格为前端/后端开发必须实现的安全底线。

#### 6.4.1 认证（Authentication）

| 要求 | 说明 | 涉及端点 |
|------|------|----------|
| 方案选择 | 实现 **JWT（推荐）** 或 **Session Cookie** 二选一。JWT 使用 HS256 签名，密钥从环境变量 `API_SECRET_KEY` 读取（≥ 32 字符随机字符串）；Session 方案须设置 `HttpOnly`、`Secure`（HTTPS 时）、`SameSite=Lax` 属性 | 所有写端点 + `/api/config`（GET） |
| 密码哈希 | Web UI 登录密码使用 **bcrypt**（推荐 `passlib[bcrypt]`）或 **argon2** 进行哈希；禁止明文、MD5、SHA-256 单次哈希。首次部署时从环境变量 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 读取并哈希存储 | `POST /api/auth/login` |
| Token / Session 有效期 | JWT `exp` 或 Session 有效期 **30 分钟**；支持 Refresh Token（有效期 7 天）或滑动窗口续期 | 所有需认证端点 |
| 登出 | 若用 JWT 则维护服务端黑名单（内存或 Redis）；若用 Session 则销毁服务端 Session | `POST /api/auth/logout` |
| 公开端点（免认证） | **MUST** 仅允许以下端点免认证：`GET /api/health`、`POST /api/auth/login`。**所有其他端点 MUST 要求认证**，包括：`/api/parse/*`、`/api/detect-page-type`、`GET/PUT /api/config`、`POST /api/tasks/*`、`GET /api/tasks/{job_id}`、`POST /api/login/refresh`、`POST /api/health-check` 等。解析服务必须受认证保护以防滥用。 | — |

登录流程示例（JWT 方案）：

```
POST /api/auth/login
Content-Type: application/json
{"username": "admin", "password": "..."}

→ 200 {
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_in": 1800
}

后续请求：
Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
```

#### 6.4.2 授权（Authorization）

| 角色 | 权限 | 说明 |
|------|------|------|
| **admin** | 全部操作 | 读写配置（`GET/PUT /api/config`）、触发任务（`POST /api/tasks/*`）、刷新会话（`POST /api/login/refresh`）、查看日志、执行健康检查 |
| **readonly** | 仅读 | `GET /api/config`（脱敏）、`GET /api/tasks/{job_id}`（查看状态/日志）、`GET /api/health`。不可修改配置、不可触发任务 |

- 角色检查在每个受保护端点的 **FastAPI Dependency** 中实现（如 `Depends(require_role("admin"))`）。
- 首次部署只需 admin 角色；readonly 为可选扩展。

#### 6.4.3 敏感凭证存储与加密

**当前问题**：`config.py` 存储以下敏感字段为 **明文**：

| 敏感字段 | 所属服务 |
|----------|----------|
| `GIT_PASSWORD` | GitHub |
| `QB_PASSWORD` | qBittorrent |
| `SMTP_PASSWORD` | 邮件服务 |
| `JAVDB_PASSWORD` | JAVDB 登录 |
| `JAVDB_SESSION_COOKIE` | JAVDB 会话 |
| `GPT_API_KEY` | GPT 验证码 API |
| `PIKPAK_PASSWORD` | PikPak |
| `PROXY_POOL[*].http/https` | 代理（内含用户名密码） |

**要求**：

- **落盘加密**：敏感字段写入持久存储（`.env` 文件或 JSON secrets 文件）时，使用 **AES-256-GCM**（推荐 `cryptography.fernet.Fernet`，底层 AES-128-CBC + HMAC，或直接 `AESGCM`）加密。加密密钥从环境变量 `SECRETS_ENCRYPTION_KEY` 读取。
- **`config.py` 生成时解密**：`config_generator.py` 在生成 `config.py` 前从加密存储中解密。`config.py` 本身仍为明文（因 Python `import` 需要），但文件权限设置为 `0600`（仅所有者可读写）。
- **Docker 场景**：加密密钥通过 Docker Secret 或环境变量注入，不写入镜像或 Dockerfile。
- **API 响应脱敏**：`GET /api/config` 返回的敏感字段必须经 Rust `mask_full()` 或 `mask_proxy_url()` 处理。前端提交修改时，若敏感字段值为 `"********"`（未修改标记），后端跳过该字段不覆写。

#### 6.4.4 传输安全

- **HTTPS / TLS 1.2+**：生产部署时后端必须通过 **反向代理**（Nginx / Traefik / Caddy）终止 TLS，或在 uvicorn 中配置 `--ssl-keyfile` / `--ssl-certfile`。Docker Compose 模板中应包含 TLS 配置示例。
- **HSTS**：反向代理应设置 `Strict-Transport-Security: max-age=31536000; includeSubDomains` 响应头。
- **内网例外**：若仅在 `127.0.0.1` / Docker 内网使用，可允许 HTTP，但须在文档与 UI 中明确提示风险。

#### 6.4.5 输入校验

| 校验对象 | 规则 | 涉及端点 |
|----------|------|----------|
| **Adhoc URL** | 必须为合法 URL；**host 白名单**仅允许 `javdb.com`（与 `utils/domain/url_helper.py:~32` 的 `_py_detect_url_type` 函数中 `'javdb.com' not in url` 检查对齐）；禁止 `file://`、`javascript:`、内网 IP（`127.0.0.1`、`10.*`、`192.168.*`） | `POST /api/tasks/adhoc` |
| **分页参数** | `start_page` ≥ 1，`end_page` ≥ `start_page`，上限 ≤ 200 | `POST /api/tasks/daily`, `/api/tasks/adhoc` |
| **phase** | 枚举值 `1` / `2` / `all` | 同上 |
| **配置字段类型** | 按 `utils/config_generator.py` 的 `get_config_map()` 定义的类型函数校验（`get_env_int` → int、`get_env_bool` → bool、`get_env_float` → float、`get_env_json` → valid JSON） | `PUT /api/config` |
| **字符串长度** | 普通字符串 ≤ 2048 字符；`JAVDB_SESSION_COOKIE` ≤ 4096 字符；`PROXY_POOL` JSON ≤ 65536 字符 | `PUT /api/config` |
| **HTML payload** | `html` 字段 ≤ 5 MB（防止 DoS） | `POST /api/parse/*` |
| **job_id** | 仅允许 `[a-zA-Z0-9_-]`，长度 ≤ 64 | `GET /api/tasks/{job_id}` |

校验应使用 **Pydantic `Field` 验证器**（FastAPI 原生支持），在路由层统一拦截并返回 HTTP 422 + 具体错误信息。

#### 6.4.6 CSRF 防护

- **SPA 架构**（前后端分离、JSON API）：使用 **`Double Submit Cookie`** 或 **`Custom Header`** 方案——前端在每个状态变更请求中附加 `X-CSRF-Token` 头，后端校验其与 Cookie 中的值一致。
- **涉及端点**：所有 `POST`、`PUT`、`DELETE` 端点（`/api/config`、`/api/tasks/*`、`/api/auth/*`、`/api/login/refresh`、`/api/health-check`）。
- **`GET /api/health`** 和 `GET /api/tasks/{job_id}` 等只读端点免 CSRF。
- FastAPI 可使用 `starlette-csrf` 或自定义中间件实现。

#### 6.4.7 限流（Rate Limiting）

| 端点 | 限制 | 说明 |
|------|------|------|
| `POST /api/auth/login` | **5 次/分钟**（按 IP） | 防暴力破解；超限返回 HTTP 429 + `Retry-After` 头 |
| `POST /api/tasks/*` | **10 次/分钟**（按用户） | 防止并发触发过多任务 |
| `PUT /api/config` | **20 次/分钟**（按用户） | 防止高频写入 |
| `POST /api/parse/*` | **60 次/分钟**（按 IP） | 防 DoS |
| 其他端点 | **120 次/分钟**（按 IP） | 全局兜底 |

推荐使用 **`slowapi`**（基于 `limits`）集成到 FastAPI，或在反向代理层（Nginx `limit_req`）实现。

#### 6.4.8 Session 管理与并发控制

- **单用户并发 Session**：默认允许同一用户最多 **3 个并发 Session**（或 JWT 无硬限制，依赖短有效期）。新登录不挤掉旧 Session，但超过上限时拒绝新登录并提示。
- **Session 存储**：内存字典（单实例部署）或 Redis（多实例）。Docker 部署时推荐 Redis sidecar。
- **登出清理**：登出时立即使 Token/Session 失效；关闭浏览器时前端不自动清理（依赖服务端过期）。
- **活动检测**：无用户操作超过 30 分钟 → Token/Session 过期 → 前端重定向到登录页。

#### 6.4.9 安全审计日志

- 记录以下事件到独立审计日志文件（如 `logs/audit.log`）：
  - 登录成功/失败（含 IP、用户名、时间）
  - 配置修改（修改了哪些字段，不记录敏感值）
  - 任务触发（谁触发、什么类型、参数摘要）
  - 会话刷新（`/api/login/refresh`）
- 日志格式与现有 `utils/infra/logging_config.py` 一致，使用 Python `logging` 模块。

---

## 七、验收标准（供 AI 自检）

1. **配置**：前端能列出并编辑本文档 2.1–2.12 中的**每一个**配置项；保存后 `config.py` 或环境变量正确更新，且敏感项在 UI 中脱敏。
2. **Daily Ingestion**：能设置 3.2 节全部参数并触发“完整 Pipeline”或“仅 Spider”；后端正确调用 `pipeline.py` 或 `scripts/spider`，前端能查看运行状态或日志。
3. **Adhoc Ingestion**：能设置 4.2 节全部参数（含必填 URL）；后端正确调用 `pipeline.py --url ...`，前端能查看运行状态或日志。
4. **UI**：整体风格现代、统一，非默认模板式；表单与按钮清晰，错误与成功有明确反馈。
5. **安全**：满足 6.4 节全部要求——认证（JWT 或 Session）、角色授权（admin/readonly）、敏感字段落盘加密与 API 脱敏、HTTPS 传输、输入校验（URL 白名单 + 类型检查）、CSRF 防护、登录限流（5 次/分钟）、Session 管理（30 分钟过期）；审计日志记录关键操作。

---

## 八、参考文件清单

| 用途 | 路径/说明 |
|------|-----------|
| **UI 美化与技术参考** | Docker 镜像 **`mdcng/mdc`**（界面风格与前端实现参考） |
| 配置生成与字段定义 | `utils/config_generator.py` |
| 配置示例与注释 | `config.py.example` |
| 流水线入口（Daily/Adhoc） | `pipeline.py` |
| 爬虫 CLI 参数 | `scripts/spider`（`parse_arguments()`） |
| 上传器 | `scripts/qb_uploader.py` |
| Daily 工作流参考 | `.github/workflows/DailyIngestion.yml` |
| Adhoc 工作流参考 | `.github/workflows/AdHocIngestion.yml` |
| 健康检查 | `scripts/health_check.py` |
| 登录 | `scripts/login.py` |
| qB 文件过滤 | `scripts/qb_file_filter.py` |

---

**文档版本**：1.1  
**适用项目**：JAVDB_AutoSpider_CICD（命令行已实现 JAVDB 自动抓种并添加至 qBittorrent；API 层已完成，本规格用于在此基础上完成 Web 前端；UI 美化与技术参考 Docker 镜像 `mdcng/mdc`。）
