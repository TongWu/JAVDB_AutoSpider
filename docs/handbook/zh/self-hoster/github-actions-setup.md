# GitHub Actions 部署指南

从零开始通过 GitHub Actions 运行 JAVDB AutoSpider 自动化每日流水线的完整指南。

## 概述

GitHub Actions 部署提供：

- **每日自动抓取** —— 通过 cron 触发的工作流（12:00 UTC / 北京时间 20:00）
- **临时抓取** —— 通过手动触发自定义 URL（演员、标签等）
- **加密产物处理** —— config.py、日志和报告在任务之间使用 AES-256-CBC 加密
- **自动回滚** —— 流水线失败时自动回滚（pending 模式的写入被删除，不会提交）
- **邮件通知** —— 包含运行结果、统计数据和日志附件

## 步骤 1 —— Fork 或克隆仓库

将 `TongWu/JAVDB_AutoSpider` Fork 到你自己的 GitHub 账户，或将你的克隆推送到私有仓库。

## 步骤 2 —— 创建 `Production` 环境

进入 **Settings > Environments > New environment**，创建名为 **`Production`** 的环境。

`DailyIngestion` 和 `AdHocIngestion` 工作流在其任务定义中都引用了 `environment: Production`。范围限定到此环境的 Secrets 和 Variables 会在运行时注入。

> **提示：** 你可以为 Production 环境添加保护规则（必需审核人、等待计时器）以增加安全性，但这不是必需的。

## 步骤 3 —— 配置仓库 Secrets

进入 **Settings > Secrets and variables > Actions > Secrets**（或将其范围限定到 `Production` 环境）。

`config_generator` CLI（`python3 -m apps.cli.ops.config_generator --github-actions`）从以 `VAR_` 为前缀的环境变量中读取这些值，并在每次工作流运行开始时写入 `config.py`。下面列出的每个 Secret 对应工作流 YAML 中的一个 `VAR_*` 环境变量。

### 必需 Secrets

| Secret | 用途 | 示例 |
|---|---|---|
| `DEPLOY_KEY` | 用于 CI 中 git push 的 SSH 部署密钥（读写权限） | 使用 `ssh-keygen -t ed25519` 生成，并将公钥添加为仓库的部署密钥 |
| `ARTIFACT_KEY` | 用于任务间 config/日志/报告产物 AES-256-CBC 加密的密码 | 任意强随机字符串 |
| `QB_URL` | qBittorrent Web UI URL（每日模式） | `https://192.168.1.100:8080` |
| `QB_USERNAME` | qBittorrent Web UI 用户名 | `admin` |
| `QB_PASSWORD` | qBittorrent Web UI 密码 | |
| `SMTP_SERVER` | SMTP 主机 | `smtp.gmail.com` |
| `SMTP_USER` | SMTP 登录用户名 | `you@gmail.com` |
| `SMTP_PASSWORD` | SMTP 应用专用密码 | |
| `EMAIL_FROM` | 发件人邮箱地址 | `you@gmail.com` |
| `EMAIL_TO` | 收件人邮箱地址 | `you@gmail.com` |
| `PROXY_POOL_JSON` | Proxy 对象的 JSON 数组 | `[{"name":"Proxy-1","http":"http://1.2.3.4:7890","https":"http://1.2.3.4:7890"}]` |
| `JAVDB_USERNAME` | JavDB 登录邮箱/用户名 | |
| `JAVDB_PASSWORD` | JavDB 登录密码 | |
| `JAVDB_SESSION_COOKIE` | JavDB `_jdb_session` cookie 值（由登录自动刷新） | |
| `GPT_API_URL` | 用于验证码识别的 GPT-4o Vision API 端点 | `https://api.openai.com/v1/chat/completions` |
| `GPT_API_KEY` | GPT 验证码识别器的 API 密钥 | `sk-...` |
| `PIKPAK_EMAIL` | PikPak 账户邮箱 | |
| `PIKPAK_PASSWORD` | PikPak 账户密码 | |

### 可选 Secrets（Cloudflare D1 存储后端）

仅在 `STORAGE_BACKEND` 设为 `d1` 或 `dual` 时需要。

| Secret | 用途 |
|---|---|
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare 账户 ID |
| `CLOUDFLARE_API_TOKEN` | 具有 D1 读写权限的 Cloudflare API token |
| `D1_HISTORY_DB_ID` | history.db 的 D1 数据库 ID |
| `D1_REPORTS_DB_ID` | reports.db 的 D1 数据库 ID |
| `D1_OPERATIONS_DB_ID` | operations.db 的 D1 数据库 ID |

### 可选 Secrets（跨运行器 Proxy Coordinator）

仅在使用 Cloudflare Worker proxy coordinator 进行多运行器部署时需要。

| Secret | 用途 |
|---|---|
| `PROXY_COORDINATOR_TOKEN` | Proxy coordinator Worker 的 Bearer token |

### 可选 Secrets（专用临时 qBittorrent 实例）

设置后，临时工作流使用单独的 qBittorrent 实例。PikPak 桥接会扫描两个实例。

| Secret | 用途 |
|---|---|
| `QB_URL_ADHOC` | 临时下载的 qBittorrent Web UI URL |
| `QB_USERNAME_ADHOC` | 为空时回退到 `QB_USERNAME` |
| `QB_PASSWORD_ADHOC` | 为空时回退到 `QB_PASSWORD` |

### 可选 Secrets（Rclone / 去重）

| Secret | 用途 |
|---|---|
| `RCLONE_CONFIG_BASE64` | Base64 编码的 `rclone.conf` 内容（用于 Google Drive 库存和去重） |

### 可选 Secrets（Telegram 通知，ADR-039）

仅当 `NOTIFY_BACKENDS_JSON` 包含 `"telegram"` 时需要。

| Secret | 用途 |
|---|---|
| `TELEGRAM_BOT_TOKEN` | 来自 [@BotFather](https://t.me/BotFather) 的 bot token，例如 `123456:ABC-DEF...` |
| `TELEGRAM_CHAT_ID` | 目标 chat/channel id，例如 `-1001234567890` |

## 步骤 4 —— 配置仓库 Variables

进入 **Settings > Secrets and variables > Actions > Variables**。

这些是非敏感值。`config_generator` 通过 `VAR_*` 环境变量读取它们。

### 核心 Variables

| Variable | 默认值 | 用途 |
|---|---|---|
| `GIT_REPO_URL` | -- | 仓库 HTTPS URL（例如 `https://github.com/you/JAVDB_AutoSpider.git`） |
| `GIT_BRANCH` | `main` | git push 的分支 |
| `NOTIFY_BACKENDS_JSON` | `["email"]` | active 通知后端的 JSON 数组（ADR-039）。加 `"telegram"` 可收到 Telegram 运行摘要;设为 `["telegram"]` 则禁用邮件报告。telegram active 时需配套 `TELEGRAM_*` secrets。 |
| `PROXY_MODE` | `pool` | `pool`、`single` 或 `None` |
| `PROXY_MODULES_JSON` | `["spider"]` | 使用 proxy 的模块 JSON 数组：`spider`、`qbittorrent`、`pikpak`、`all` |
| `DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST_JSON` | `["western_studio_date"]` | 排除每日抓取的 video-code family 的 JSON 数组，默认 `["western_studio_date"]`。设为 `[]` 可停止排除 western studio/date family。 |
| `LOG_LEVEL` | `INFO` | `DEBUG`、`INFO`、`WARNING`、`ERROR` |
| `STORAGE_BACKEND` | `sqlite` | `sqlite`、`d1` 或 `dual` |
| `D1_RECOVERY_OUTBOX_ENABLED` | `false` | 启用 ADR-010 recovery outbox，处理安全 D1 写入失败。可识别的真值包括 `1`、`true`、`yes`、`on`。 |
| `D1_BATCHING_ENABLED` | `false` | 启用 ADR-010 safe-path D1 micro-batching。可识别的真值包括 `1`、`true`、`yes`、`on`。 |
| `D1_STARTUP_REPLAY_ENABLED` | `false` | 在首次 D1/Dual 操作前启用 ADR-010 startup replay。可识别的真值包括 `1`、`true`、`yes`、`on`。 |

### 爬虫调优 Variables

| Variable | 默认值 | 用途 |
|---|---|---|
| `PAGE_START` | `1` | 起始抓取页码 |
| `PAGE_END` | `20` | 结束抓取页码 |
| `PHASE2_MIN_RATE` | `4.0` | Phase 2 质量过滤的最低评分 |
| `PHASE2_MIN_COMMENTS` | `100` | Phase 2 质量过滤的最低评论数 |
| `BASE_URL` | `https://javdb.com` | JavDB 基础 URL |
| `IGNORE_RELEASE_DATE_FILTER` | `False` | 跳过发布日期过滤 |
| `INCLUDE_DOWNLOADED_IN_REPORT` | `False` | 在报告中包含已下载的影片 |
| `MOVIE_SLEEP` | （自适应） | 覆盖爬虫休眠范围（例如 `"2,5"`） |

### qBittorrent Variables

| Variable | 默认值 | 用途 |
|---|---|---|
| `TORRENT_CATEGORY` | `JavDB` | 每日模式种子的 qBittorrent 分类 |
| `TORRENT_CATEGORY_ADHOC` | `Ad Hoc` | 临时模式种子的 qBittorrent 分类 |
| `TORRENT_SAVE_PATH` | （空 = 默认） | 覆盖种子保存路径 |
| `AUTO_START` | `True` | 自动开始添加的种子 |
| `SKIP_CHECKING` | `False` | 跳过哈希校验 |
| `REQUEST_TIMEOUT` | `30` | API 请求超时时间（秒） |
| `DELAY_BETWEEN_ADDITIONS` | `1` | 种子添加间隔（秒） |
| `QB_FILE_FILTER_MIN_SIZE_MB` | `100` | 文件过滤器的最小文件大小阈值 |
| `TORRENT_QUALITY_EVIDENCE_ENABLED` | `false` | 在文件过滤器之后运行 ADR-024 影子证据采集 |
| `TORRENT_QUALITY_POLICY_MODE` | `shadow` | 预留的 rollout 模式；Phase 1 仍为 shadow-only |
| `TORRENT_QUALITY_CATEGORIES` | （空） | 可选：当 workflow dispatch 未提供 `categories` 输入时使用的 JSON 数组 |

### Proxy Variables

| Variable | 默认值 | 用途 |
|---|---|---|
| `PROXY_POOL_MAX_FAILURES` | `3` | 当前会话中封禁 proxy 前的最大连续失败次数 |
| `CF_BYPASS_SERVICE_PORT` | `8000` | CloudFlare 绕过服务端口 |
| `CF_BYPASS_ENABLED` | `True` | 启用/禁用 CF 绕过回退 |
| `LOGIN_PROXY_NAME` | （空） | 将登录绑定到特定 proxy 名称 |

### Proxy Coordinator Variables

| Variable | 默认值 | 用途 |
|---|---|---|
| `PROXY_COORDINATOR_URL` | （空） | 用于跨运行器协调的 Cloudflare Worker URL |
| `MOVIE_CLAIM_ENABLED` | `auto` | MovieClaim 互斥锁：`auto`、`true` 或 `false` |
| `RUNNER_REGISTRY_ENABLED` | `false` | 在 RunnerRegistry DO 中注册运行器 |

### 路径 Variables

| Variable | 默认值 | 用途 |
|---|---|---|
| `REPORTS_DIR` | `reports` | 所有报告的根目录 |
| `DAILY_REPORT_DIR` | `reports/DailyReport` | 每日 CSV 输出目录 |
| `AD_HOC_DIR` | `reports/AdHoc` | 临时 CSV 输出目录 |
| `PARSED_MOVIES_CSV` | `parsed_movies_history.csv` | 历史 CSV 文件名 |

### PikPak Variables

| Variable | 默认值 | 用途 |
|---|---|---|
| `PIKPAK_LOG_FILE` | `logs/pikpak_bridge.log` | PikPak 桥接日志路径 |
| `PIKPAK_REQUEST_DELAY` | `2` | PikPak API 调用间隔（秒） |
| `PIKPAK_ROOT_FOLDER` | `/Javdb_AutoSpider` | PikPak 离线下载根文件夹 |

### Rclone Variables

| Variable | 默认值 | 用途 |
|---|---|---|
| `RCLONE_FOLDER_PATH` | （空） | Rclone 远程路径（库存/去重用） |

### 日志文件 Variables

| Variable | 默认值 |
|---|---|
| `SPIDER_LOG_FILE` | `logs/spider.log` |
| `UPLOADER_LOG_FILE` | `logs/qb_uploader.log` |
| `PIPELINE_LOG_FILE` | `logs/pipeline.log` |
| `EMAIL_NOTIFICATION_LOG_FILE` | `logs/email_notification.log` |
| `SMTP_PORT` | `587` |

## 步骤 5 —— config.py 的生成方式

在 CI 中没有持久化的 `config.py` 文件。每个工作流的 **setup 任务**会运行：

```bash
python3 -m apps.cli.ops.config_generator --github-actions
```

该脚本读取所有 `VAR_*` 环境变量（由上述 Secrets 和 Variables 填充）并写入完整的 `config.py`。然后使用 `ARTIFACT_KEY` 加密该文件，并作为加密产物在任务之间传递。

每个下游任务（run-pipeline、email-notification、commit-results、cleanup-on-failure）在运行导入 `config` 的 Python 代码前，都会使用 `restore-encrypted-config` 组合 Action 解密产物。

## 步骤 6 —— 启用并验证每日抓取定时任务

`DailyIngestion.yml` 工作流有一个 `schedule` 触发器：

```yaml
schedule:
  - cron: '00 12 * * *'   # 12:00 UTC = 北京时间 20:00
```

GitHub Actions 的 cron 在高负载时可能延迟最多 15 分钟。cron 仅在**默认分支**上激活。

验证方式：

1. 进入 **Actions > JavDB Daily Ingestion Pipeline**
2. 点击 **Run workflow**（手动触发）进行测试运行
3. 检查运行是否成功完成
4. 等待下一次定时触发

### DailyIngestion 工作流结构

| 任务 | 用途 |
|---|---|
| `setup` | 检出代码、安装依赖、生成并加密 config.py |
| `run-pipeline` | 健康检查、爬虫、qBittorrent 上传、文件过滤、PikPak 桥接、Rclone 去重、会话提交 |
| `cleanup-on-failure` | 失败时回滚未提交的 D1/pending 写入 |
| `email-notification` | 发送结果邮件，对关键 pending 警报运行告警并暂停 |
| `commit-results` | 将 CSV 报告和数据库文件提交回仓库 |

### AdHocIngestion 工作流

仅通过**手动触发**（workflow_dispatch）启动。需要输入目标 URL。

进入 **Actions > JavDB Ad-Hoc Ingestion Pipeline > Run workflow** 并填入：

- **url**（必填）：目标 URL（例如 `https://javdb.com/actors/EvkJ`）
- **start_page** / **end_page**：页码范围（end_page 留空则扫描所有页面）
- **phase**：`all`、`1`（仅字幕）或 `2`（仅非字幕）
- **history_filter**：处理前检查历史记录
- **date_filter**：按发布日期过滤
- **qb_category**：自定义 qBittorrent 分类（空 = 默认 "Ad Hoc"；`顶级` 使用每日 qB 凭据）

### SubscriptionMonitor 工作流

`SubscriptionMonitor.yml` 每天 **14:00 UTC** 运行，也可以手动触发。它从 D1
读取 active 的 `ActorSubscription` 行，通过现有 AdHoc spider 路径抓取每个演员，并将真正的新作写入 `NewWorks` feed。

手动输入：

- **proxy_spider**：为演员页抓取启用代理。
- **dry_run**：只列出 active subscriptions，不执行抓取。
- **runner**：选择 `ubuntu-latest` 或 `self-hosted`。
- **log_level**：CLI 日志级别（`DEBUG`、`INFO`、`WARNING`、`ERROR`）。

该工作流复用摄取工作流所在的 `Production` environment secrets 和 variables，包括 D1 凭据、JavDB 登录凭据、代理设置、`DEPLOY_KEY` 和 `ARTIFACT_KEY`。

## 步骤 7 —— 监控

### 邮件通知

两个工作流在每次运行时都会发送邮件通知（成功、失败或取消）。邮件包含：

- 流水线状态摘要
- 爬虫统计（页面数、发现条目数、已解析数、已跳过数）
- 日志文件附件（加密后解密用于邮件）
- D1 漂移警告横幅（如适用）

### GitHub Actions UI

- **Step Summary**：爬虫将 Markdown 摘要表写入 `$GITHUB_STEP_SUMMARY`，显示页面数/发现数/解析数/跳过数/失败数
- **Artifacts**：加密的日志和报告作为运行产物上传（日志/报告保留 7 天，回滚日志保留 14 天）
- **工作流状态徽章**：将徽章添加到你的 README：
  ```markdown
  [![JavDB Daily Ingestion Pipeline](https://github.com/YOUR_USER/YOUR_REPO/actions/workflows/DailyIngestion.yml/badge.svg)](https://github.com/YOUR_USER/YOUR_REPO/actions/workflows/DailyIngestion.yml)
  ```

### 本地解密产物

要检查从 Actions UI 下载的加密产物：

```bash
# 解密日志
openssl enc -aes-256-cbc -d -pbkdf2 -iter 100000 \
  -in logs.tar.gz.enc -pass pass:"YOUR_ARTIFACT_KEY" | tar -xzf -

# 解密报告
openssl enc -aes-256-cbc -d -pbkdf2 -iter 100000 \
  -in reports.tar.gz.enc -pass pass:"YOUR_ARTIFACT_KEY" | tar -xzf -
```

## 其他工作流

| 工作流 | 触发方式 | 用途 |
|---|---|---|
| `QBFileFilter.yml` | 定时（每日抓取后 2 小时） | 过滤最近添加种子中的小文件 |
| `ReconcileLibrary.yml` | 每小时定时 / 手动触发 | 运行 ADR-033 闭环轮次：采集结果对账（实时 qB 状态）+ 所有权账本（gdrive/qb/pikpak/nas）+ 消费信号（媒体服务器） |
| `SubscriptionMonitor.yml` | 每日定时 / 手动触发 | 通过 AdHoc 路径抓取已关注演员并写入 New Works feed |
| `WeeklyDedup.yml` | 每周定时 | Rclone 去重 |
| `RollbackD1.yml` | 手动触发 | 手动会话回滚 |
| `StaleSessionCleanup.yml` | 每日定时 | 自动清理超过 48 小时的卡住会话 |
| `AuditArchive.yml` | 每周定时 | 清理超过 30 天的 audit 行 |
| `Migration.yml` | 手动触发 | 数据库迁移运行器 |
| `TestIngestion.yml` | Push / PR / 手动触发 | 烟雾测试完整摄取路径；清理阶段执行回滚 |
| `build-rust-extension.yml` | 推送/PR 时 | 为 CI 构建 Rust wheel |
| `unit-tests.yml` | 推送/PR 时 | 基于影响的测试选择 |

### ReconcileLibrary 工作流

`ReconcileLibrary.yml` 运行 ADR-033 Phase 1+2+3 对账轮次。它每小时运行一次，并调用：

```bash
STORAGE_BACKEND=d1 python3 -m apps.cli.ops.reconcile --pass all --json
```

`--pass all` 按顺序运行全部三个轮次：

1. **采集轮次（acquisition pass）** — 读取实时 qBittorrent 状态，将
   `AcquisitionOutcome` 行从 `queued` / `downloading` 推进到 `downloading`、
   `completed`、`stalled` 或 `failed`。若某个种子在 qB 中处于 `missingFiles`
   状态（下载完成后文件被从磁盘删除），则将其视作 `completed`；记录 outcome
   后，该残留种子会连同其剩余文件一起从 qB 删除。
2. **所有权轮次（ownership pass）** — 从四个来源收集所有权观测结果并 upsert
   到 `OwnershipLedger`：
   - `gdrive` — 投影现有 `RcloneInventory` 表（无需额外 rclone 调用；由
     `WeeklyDedup.yml` 填充）。
   - `qb` — 将已完成的 `AcquisitionOutcome` 行桥接为所有权证据。
   - `pikpak` — 读取 `PikpakHistory` success 行。
   - `nas` — 前向兼容 stub，当前无论如何配置都始终只记录日志、不产生观测结果。
     `config.py` 中的 `RCLONE_NAS_REMOTE` 是为未来 NAS 采集功能预留的占位符；
     今天设置该值不会产生任何效果。将 `nas` 保留在来源列表中不会有副作用。

   收集完成后，ownership pass 执行 **present sweep**：对每个来源，将前次快照
   存在但本次快照缺失的行的 `present` 标志置为 `0`（审计保留式，从不删行）。
   最后，对于 `video_code` 已出现在持久 Ledger 来源（`gdrive` 或 `nas`）的
   `AcquisitionOutcome` 行，将其推进到 `in_library`。
3. **消费轮次（consumption pass）** — 轮询 `MEDIA_SERVERS` 中的每个媒体服务器
   （通过 `MEDIA_SERVERS_JSON` secret 提供，详见
   [媒体服务器设置](media-servers.md)），通过 join-key 置信度阶梯将条目标题解析
   为 `video_code`，写入 `ConsumptionSignal` 和 `UnresolvedMediaItem` 行。若
   `MEDIA_SERVERS_JSON` 未设置，此轮次为 no-op。

整个工作流始终在 `self-hosted` runner 上运行，因为 qBittorrent 通常只在操作者内网可达。
生成的 `config.py` 会从仓库 Variables 读取 `TORRENT_CATEGORY` 和
`TORRENT_CATEGORY_ADHOC`，因此默认扫描会跟随上传器使用的同一组 qB 分类。

手动触发输入：

| 输入 | 默认值 | 用途 |
|---|---|---|
| `stalled_after_days` | `7` | 正整数。活跃 outcome 超过该天数未被观测到会变为 `stalled`；超过 2 倍窗口会变为 `failed`。 |
| `dry_run` | `false` | 只计算状态迁移并输出 JSON，不写入数据行。 |

### 媒体服务器 secret {#media-servers-secret}

消费轮次通过运行时生成的 `config.py` 读取 `MEDIA_SERVERS`（由加密的 `config.py.enc`
与仓库 secrets 合并生成）。要在 `ReconcileLibrary.yml` 中启用消费轮次，需添加
`MEDIA_SERVERS_JSON` 仓库 secret：

1. 进入 **Settings → Secrets and variables → Actions → New repository secret**。
2. 名称：`MEDIA_SERVERS_JSON`。
3. 值：将 `MEDIA_SERVERS` 列表序列化为 JSON，例如：

   ```json
   [{"type":"emby","instance":"emby-nas","base_url":"http://192.168.1.50:8096","token":"your_emby_api_key","libraries":["JAV"]}]
   ```

4. 保存。`config_generator` 会读取 `VAR_MEDIA_SERVERS_JSON` 并将其注入生成的
   `config.py` 中，格式为 `MEDIA_SERVERS = <value>`。

若 secret 不存在或为空，`MEDIA_SERVERS` 默认为 `[]`，消费轮次为 no-op。字段说明及
凭据获取步骤参见 [媒体服务器设置](media-servers.md)。

## 故障排查

- **`ARTIFACT_KEY secret is not configured`**：每个任务都会检查缺少的 `ARTIFACT_KEY`。将其添加为仓库 Secret。
- **`DEPLOY_KEY` 错误**：SSH 部署密钥必须具有写入权限。在 **Settings > Deploy keys** 中添加公钥，并勾选 "Allow write access"。
- **配置生成失败**：检查所有必需的 `VAR_*` 环境变量是否已填充。`config_generator` 会记录其读取的变量。
- **定时任务未触发**：GitHub 会禁用 60 天内无活动仓库的定时工作流。推送一次提交或手动触发一次运行即可重新启用。
- **邮件未发送**：检查 `SMTP_*` Secrets。Gmail 需要应用专用密码（不是你的登录密码），可能还需要 "Allow less secure apps" 或 OAuth 设置。

更多故障排查内容，请参见 [../ops/troubleshooting.md](../ops/troubleshooting.md)。
