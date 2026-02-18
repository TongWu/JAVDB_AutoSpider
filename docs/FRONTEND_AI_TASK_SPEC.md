# JAVDB AutoSpider 前端开发 —— AI 任务规格说明

本文档面向 **AI 开发者**，用于在现有 JAVDB AutoSpider 项目上实现一个**美观、现代的前端**，通过 Web UI 完成配置管理、Daily Ingestion、Adhoc Ingestion 等核心功能的运行与调参。

---

## 一、项目背景与现状

### 1.1 项目做什么

- **JAVDB AutoSpider**：从 JAVDB 自动抓取种子信息，筛选后添加到 qBittorrent，并可选同步到 PikPak、发送邮件报告。
- 当前实现为 **纯命令行**：通过 `pipeline.py`、`scripts/spider.py`、`scripts/qb_uploader.py` 等脚本及 GitHub Actions 工作流完成全流程。
- 配置通过 **`config.py`** 管理，由 `utils/config_generator.py` 从环境变量生成（支持本地与 GitHub Actions 两套来源）。

### 1.2 前端需要覆盖的“关键功能”

1. **配置管理**：对 `config` 中**每一个选项**进行查看与编辑（含敏感项脱敏展示与安全存储）。
2. **Daily Ingestion**：配置并触发“每日抓取”流程（爬虫 → qBittorrent 上传 → PikPak 桥接 → 邮件通知）。
3. **Adhoc Ingestion**：按用户输入的 URL 与参数，配置并触发“按需抓取”流程。
4. （可选）**健康检查**、**登录/会话刷新**、**qb 文件过滤**、**Rclone 去重**等辅助功能的入口与状态展示。

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
| START_PAGE | int | 起始页 | `1` |
| END_PAGE | int | 结束页 | `10` 或 `20` |
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
| DETAIL_PAGE_SLEEP | int | 详情页间隔（秒） | `5`–`30` |
| PAGE_SLEEP | int | 翻页间隔（秒） | `2`–`15` |
| MOVIE_SLEEP | int | 影片间间隔（秒） | `1`–`15` |
| CF_TURNSTILE_COOLDOWN | int | Turnstile 冷却（秒） | `10`–`30` |
| PHASE_TRANSITION_COOLDOWN | int | 阶段切换冷却（秒） | `30`–`60` |
| FALLBACK_COOLDOWN | int | 回退冷却（秒） | `30` |

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
| PIKPAK_REQUEST_DELAY | int | 请求间隔（秒） | `3` |

### 2.12 QBITTORRENT FILE FILTER CONFIGURATION

| 配置名 | 类型 | 说明 | 默认/示例 |
|--------|------|------|-----------|
| QB_FILE_FILTER_MIN_SIZE_MB | int | 最小文件大小（MB），小于则设为不下载 | `50` 或 `100` |
| QB_FILE_FILTER_LOG_FILE | string | 文件过滤日志路径 | `logs/qb_file_filter.log` |

---

## 三、Daily Ingestion（每日抓取）

### 3.1 含义

- 不传自定义 URL，按 **config 中的 BASE_URL + 分页** 抓取“今日/昨日”新作，经 Phase1（字幕+今日）、Phase2（今日高分）筛选，生成 CSV → 上传 qBittorrent → 可选 PikPak 桥接 → 邮件报告。

### 3.2 入口与参数

- **推荐入口**：`pipeline.py`（会依次执行 spider → qb_uploader → pikpak_bridge → email_notification）。
- **仅爬虫**：`scripts/spider.py`（不带上传与邮件）。

前端需支持以下参数（与 `pipeline.py` / `scripts/spider.py` 的 CLI 一致）：

| 参数 | 类型 | 说明 | 默认 |
|------|------|------|------|
| start_page | int | 起始页 | config 中 START_PAGE |
| end_page | int | 结束页 | config 中 END_PAGE |
| all | bool | 是否抓取到空页为止（忽略 end_page） | false |
| ignore_history | bool | 是否忽略历史文件仍写入历史 | false |
| phase | choice | `1` / `2` / `all` | `all` |
| output_file | string | 指定输出 CSV 文件名（可选） | 按日期自动 |
| dry_run | bool | 只打印不写 CSV/不实际上传 | false |
| ignore_release_date | bool | 忽略今日/昨日标签 | false |
| use_proxy | bool | 启用代理 | false |
| max_movies_phase1 | int | Phase1 最大处理影片数（测试用） | 无限制 |
| max_movies_phase2 | int | Phase2 最大处理影片数（测试用） | 无限制 |
| pikpak_individual | bool | PikPak 是否按单个种子处理 | false |

前端应提供“运行完整 Pipeline（Spider + 上传 + PikPak + 邮件）”与“仅运行 Spider”两种执行模式选项（对应是否只调 spider 还是调 pipeline）。

### 3.3 实现要点

- 后端根据前端提交的参数，在**项目根目录**下生成或补全环境变量，再调用 `python3 utils/config_generator.py` 生成 `config.py`（若采用“先写 env 再生成 config”的方案）。
- 然后执行：  
  `python3 pipeline.py [--start-page N] [--end-page N] [--phase 1|2|all] [--use-proxy] ...`  
  或仅：  
  `python3 scripts/spider.py --from-pipeline [相同参数]`  
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
- **Rclone 去重**：`scripts/rclone_dedup.py` 参数较多，可作为“高级/工具”页的可选功能，或仅提供文档链接与命令行示例。

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
    - Daily/Adhoc：在项目根目录、正确虚拟环境中执行 `pipeline.py` 或 `scripts/spider.py`，建议**异步**（后台进程或任务队列），并返回 job_id。  
    - 提供“任务状态/日志”接口：按 job_id 返回运行状态及最近日志（可从 `logs/` 读取或捕获子进程 stdout/stderr）。
  - **健康检查 / 登录 / 文件过滤**：对应脚本的调用与输出返回。
- API 需考虑**认证与授权**（至少本地或内网使用时的简单认证，避免未授权触发抓取与修改配置）。

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

---

## 七、验收标准（供 AI 自检）

1. **配置**：前端能列出并编辑本文档 2.1–2.12 中的**每一个**配置项；保存后 `config.py` 或环境变量正确更新，且敏感项在 UI 中脱敏。
2. **Daily Ingestion**：能设置 3.2 节全部参数并触发“完整 Pipeline”或“仅 Spider”；后端正确调用 `pipeline.py` 或 `scripts/spider.py`，前端能查看运行状态或日志。
3. **Adhoc Ingestion**：能设置 4.2 节全部参数（含必填 URL）；后端正确调用 `pipeline.py --url ...`，前端能查看运行状态或日志。
4. **UI**：整体风格现代、统一，非默认模板式；表单与按钮清晰，错误与成功有明确反馈。
5. **安全**：配置中的密码/Token/Cookie/代理池等不以明文展示；若提供认证，未授权用户无法执行任务或修改配置。

---

## 八、参考文件清单

| 用途 | 路径/说明 |
|------|-----------|
| **UI 美化与技术参考** | Docker 镜像 **`mdcng/mdc`**（界面风格与前端实现参考） |
| 配置生成与字段定义 | `utils/config_generator.py` |
| 配置示例与注释 | `config.py.example` |
| 流水线入口（Daily/Adhoc） | `pipeline.py` |
| 爬虫 CLI 参数 | `scripts/spider.py`（`parse_arguments()`） |
| 上传器 | `scripts/qb_uploader.py` |
| Daily 工作流参考 | `.github/workflows/DailyIngestion.yml` |
| Adhoc 工作流参考 | `.github/workflows/AdHocIngestion.yml` |
| 健康检查 | `scripts/health_check.py` |
| 登录 | `scripts/login.py` |
| qB 文件过滤 | `scripts/qb_file_filter.py` |

---

**文档版本**：1.1  
**适用项目**：JAVDB_AutoSpider_CICD（命令行已实现 JAVDB 自动抓种并添加至 qBittorrent；API 层已完成，本规格用于在此基础上完成 Web 前端；UI 美化与技术参考 Docker 镜像 `mdcng/mdc`。）
