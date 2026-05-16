# CLI 参考手册

JAVDB AutoSpider 所有 CLI 工具的完整命令行参考。

所有 CLI 均以 Python 模块的方式从仓库根目录调用：

```bash
python3 -m apps.cli.<command> [options]
```

---

## 目录

- [Spider CLI](#spider-cli)（`apps.cli.spider`）
- [Pipeline CLI](#pipeline-cli)（`apps.cli.pipeline`）
- [qBittorrent 上传器](#qbittorrent-上传器)（`apps.cli.qb_uploader`）
- [qBittorrent 文件过滤器](#qbittorrent-文件过滤器)（`apps.cli.qb_file_filter`）
- [PikPak Bridge](#pikpak-bridge)（`apps.cli.pikpak_bridge`）
- [Migration CLI](#migration-cli)（`apps.cli.migration`）
- [Login CLI](#login-cli)（`apps.cli.login`）
- [Rollback CLI](#rollback-cli)（`apps.cli.rollback`）
- [Config Generator CLI](#config-generator-cli)（`apps.cli.config_generator`）
- [Spider 完整参数参考](#spider-完整参数参考)

---

## Spider CLI

**模块：** `apps.cli.spider`

从 javdb.com 提取种子链接。支持两种运行模式：

- **每日模式**（默认）— 抓取主索引页面中今日/昨日发布的内容。
- **Ad-hoc 模式** — 通过 `--url` 激活；抓取任意 URL（演员页面、搜索查询等）。

### 基本选项

```bash
# 试运行模式（不写入 CSV 文件）
python3 -m apps.cli.spider --dry-run

# 指定自定义输出文件名
python3 -m apps.cli.spider --output-file my_results.csv

# 自定义页码范围
python3 -m apps.cli.spider --start-page 3 --end-page 10

# 解析所有页面直到遇到空页面
python3 -m apps.cli.spider --all
```

### 阶段控制

Spider 分两个阶段运行，可自由选择：

- **Phase 1** — 字幕条目 + 今日/昨日标签
- **Phase 2** — 今日/昨日标签 + 质量过滤

```bash
# 仅运行 Phase 1
python3 -m apps.cli.spider --phase 1

# 仅运行 Phase 2
python3 -m apps.cli.spider --phase 2

# 运行两个阶段（默认）
python3 -m apps.cli.spider --phase all
```

### 历史记录和过滤控制

```bash
# 读取时忽略历史记录（抓取所有页面）但仍然保存到历史记录
# 注意：ad-hoc 模式默认已忽略读取历史记录
python3 -m apps.cli.spider --ignore-history

# 在 ad-hoc 模式中启用历史记录过滤（ad-hoc 默认忽略历史记录）
python3 -m apps.cli.spider --url "https://javdb.com/actors/EvkJ" --use-history

# 自定义 URL 抓取（启用 ad-hoc 模式；分页 URL 请添加 ?page=x）
python3 -m apps.cli.spider --url "https://javdb.com/?vft=2"

# 忽略今日/昨日发布日期标签，下载所有符合阶段条件的条目
python3 -m apps.cli.spider --ignore-release-date

# 禁用 rclone 库存过滤
python3 -m apps.cli.spider --no-rclone-filter

# 禁用所有过滤器（历史记录、rclone 库存、发布日期）
python3 -m apps.cli.spider --disable-all-filters

# 启用 rclone 去重检测
python3 -m apps.cli.spider --enable-dedup

# 当同类别种子显著更大时启用重新下载
python3 -m apps.cli.spider --enable-redownload

# 设置自定义重新下载大小阈值（默认：30%）
python3 -m apps.cli.spider --enable-redownload --redownload-threshold 0.50
```

### 代理控制

代理行为默认由 `config.py` 中的 `PROXY_MODULES` 决定。CLI 标志可在单次运行中覆盖该设置。

```bash
# 按照 config.py 中的代理模块配置（默认自动模式）
python3 -m apps.cli.spider

# 本次运行强制启用代理
python3 -m apps.cli.spider --use-proxy

# 本次运行强制禁用代理
python3 -m apps.cli.spider --no-proxy

# 在代理池模式下强制串行处理详情页
python3 -m apps.cli.spider --sequential
```

`--use-proxy` 和 `--no-proxy` 互斥。

### Cloudflare 绕过

```bash
# 在回退成功后继续使用 CF 绕过 30 分钟
python3 -m apps.cli.spider --always-bypass-time 30

# 整个会话期间持续使用 CF 绕过（省略值或传入 0）
python3 -m apps.cli.spider --always-bypass-time
```

### 测试辅助选项

```bash
# 限制 Phase 1 的电影数量（用于测试）
python3 -m apps.cli.spider --max-movies-phase1 10

# 限制 Phase 2 的电影数量（用于测试）
python3 -m apps.cli.spider --max-movies-phase2 5

# 快速测试运行，限制页面数
python3 -m apps.cli.spider --start-page 1 --end-page 3 --dry-run
```

### 完整示例

```bash
# 完整抓取并忽略历史记录
python3 -m apps.cli.spider --all --ignore-history

# 自定义 URL 并指定输出文件
python3 -m apps.cli.spider --url "https://javdb.com/?vft=2" --output-file custom_results.csv

# 仅 Phase 1 并自定义页码范围
python3 -m apps.cli.spider --phase 1 --start-page 5 --end-page 15

# 下载所有字幕条目（忽略发布日期）
python3 -m apps.cli.spider --ignore-release-date --phase 1

# 下载所有高质量条目（忽略发布日期）
python3 -m apps.cli.spider --ignore-release-date --phase 2 --start-page 1 --end-page 10

# Ad-hoc：下载指定演员的电影（跳过已下载的）
python3 -m apps.cli.spider --url "https://javdb.com/actors/EvkJ" --ignore-release-date

# Ad-hoc：重新下载演员的所有内容（忽略历史记录）
python3 -m apps.cli.spider --url "https://javdb.com/actors/EvkJ" --ignore-history --ignore-release-date

# 组合：强制代理 + 自定义 URL + 忽略发布日期
python3 -m apps.cli.spider --url "https://javdb.com/actors/EvkJ" --use-proxy --ignore-release-date

# 使用自定义阈值重新下载（大 50% 时触发）
python3 -m apps.cli.spider --enable-redownload --redownload-threshold 0.50

# 禁用所有过滤器，处理索引中的每一个条目
python3 -m apps.cli.spider --disable-all-filters --start-page 1 --end-page 5
```

---

## Pipeline CLI

**模块：** `apps.cli.pipeline`

运行完整的自动化工作流：spider、qBittorrent 上传器、PikPak bridge、git 提交以及邮件通知。接受所有 spider 参数并透传。

Pipeline **默认启用重新下载**（与 spider 不同，spider 默认不启用）。使用 `--no-redownload` 可以关闭。

### Pipeline 特有参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--pikpak-individual` | PikPak Bridge 使用单个模式（而非批量） | `False` |
| `--no-redownload` | 禁用种子重新下载；pipeline 默认启用 | `False` |
| `--redownload-threshold` | 重新下载的大小增长阈值（省略时使用 spider 默认值） | Spider 默认值 |
| `--enable-dedup` | 启用 rclone 去重检测和执行 | `False` |

所有 spider 参数（`--url`、`--start-page`、`--end-page`、`--all`、`--ignore-history`、`--phase`、`--output-file`、`--dry-run`、`--ignore-release-date`、`--use-proxy`、`--no-proxy`、`--always-bypass-time`）同样可用，会被转发到 spider 步骤。

### 示例

```bash
# 基本 pipeline 运行（使用 config.py 中的自动代理模式）
python3 -m apps.cli.pipeline

# 使用自定义 URL 运行 pipeline
python3 -m apps.cli.pipeline --url "https://javdb.com/actors/EvkJ"

# 使用代理覆盖运行 pipeline
python3 -m apps.cli.pipeline --use-proxy

# 忽略发布日期标签运行 pipeline
python3 -m apps.cli.pipeline --ignore-release-date --phase 1

# 使用 PikPak 单个模式运行 pipeline
python3 -m apps.cli.pipeline --pikpak-individual

# 启用去重运行 pipeline
python3 -m apps.cli.pipeline --enable-dedup

# 不启用重新下载运行 pipeline
python3 -m apps.cli.pipeline --no-redownload

# 使用自定义重新下载阈值运行 pipeline
python3 -m apps.cli.pipeline --redownload-threshold 0.50
```

### Pipeline 步骤

Pipeline 按以下顺序执行这些步骤：

1. 运行 spider 提取数据（使用提供的参数）
2. 将 spider 结果提交到 GitHub
3. 运行 qBittorrent 上传器添加种子
4. 将上传器结果提交到 GitHub
5. 运行 PikPak Bridge 处理旧种子（默认批量模式，使用 `--pikpak-individual` 可切换为单个模式）
6. 最终提交并推送到 GitHub
7. 分析日志中的严重错误
8. 发送包含状态信息的邮件通知

**注意：** Pipeline 默认不注入 `--use-proxy` 或 `--no-proxy`；每个步骤通过 `PROXY_MODULES` 遵循 `config.py` 的配置。如果你传入了 `--use-proxy` 或 `--no-proxy`，该覆盖会被转发给 spider、qBittorrent 上传器和 PikPak Bridge。

---

## qBittorrent 上传器

**模块：** `apps.cli.qb_uploader`

将 spider CSV 输出中的种子磁力链接上传到 qBittorrent。

### 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--mode` | 上传模式：`adhoc` 或 `daily` | `daily` |
| `--input-file` | 输入 CSV 文件名（覆盖默认的基于日期的文件名） | 自动检测 |
| `--use-proxy` | 强制启用代理用于 qBittorrent API 请求 | 自动 |
| `--no-proxy` | 强制禁用代理用于 qBittorrent API 请求 | 自动 |
| `--category` | 覆盖 qBittorrent 分类 | 取决于模式的默认值 |
| `--from-pipeline` | 内部参数：从 pipeline 运行 | `False` |
| `--session-id` | 用于保存上传器统计信息的报告 session ID | `None` |

### 示例

```bash
# 每日模式（默认）
python3 -m apps.cli.qb_uploader

# Ad-hoc 模式（用于自定义 URL 抓取结果）
python3 -m apps.cli.qb_uploader --mode adhoc

# 指定输入文件
python3 -m apps.cli.qb_uploader --input-file my_results.csv

# 为 qBittorrent API 使用代理
python3 -m apps.cli.qb_uploader --use-proxy

# 覆盖分类
python3 -m apps.cli.qb_uploader --mode adhoc --category "Custom Category"
```

---

## qBittorrent 文件过滤器

**模块：** `apps.cli.qb_file_filter`

过滤 qBittorrent 中最近添加的种子中的小文件。将低于大小阈值的不需要的文件设置为"不下载"优先级。

### 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--min-size` | 最小文件大小（MB）；小于此值的文件将被跳过 | config 中的 `QB_FILE_FILTER_MIN_SIZE_MB`（未设置时为 100） |
| `--days` | 向前查找最近添加种子的天数 | `2` |
| `--use-proxy` | 强制启用代理用于 qBittorrent API 请求 | 自动 |
| `--no-proxy` | 强制禁用代理用于 qBittorrent API 请求 | 自动 |
| `--dry-run` | 预览将被过滤的内容但不实际更改 | `False` |
| `--category` | 仅过滤此分类中的种子（已弃用；请使用 `--categories`） | 所有分类 |
| `--categories` | JSON 数组格式的分类列表；覆盖 `--category` | 所有分类 |
| `--delete-local-files` | 删除已下载但低于大小阈值的本地文件 | `False` |

### 示例

```bash
# 默认：使用 config 中的阈值
python3 -m apps.cli.qb_file_filter

# 覆盖阈值（例如 50MB）和天数
python3 -m apps.cli.qb_file_filter --min-size 50
python3 -m apps.cli.qb_file_filter --min-size 100 --days 3

# 试运行（预览但不更改）
python3 -m apps.cli.qb_file_filter --dry-run

# 仅过滤特定分类
python3 -m apps.cli.qb_file_filter --category JavDB

# 过滤多个分类
python3 -m apps.cli.qb_file_filter --categories '["Ad Hoc", "Daily Ingestion"]'

# 使用代理
python3 -m apps.cli.qb_file_filter --use-proxy

# 删除已下载的小文件
python3 -m apps.cli.qb_file_filter --delete-local-files
```

---

## PikPak Bridge

**模块：** `apps.cli.pikpak_bridge`

将旧种子从 qBittorrent 转移到 PikPak 云存储。

### 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--days` | 过滤超过 N 天的种子 | `3` |
| `--dry-run` | 测试模式：不删除也不添加到 PikPak | `False` |
| `--individual` | 逐个处理种子而非批量模式 | `False`（批量） |
| `--use-proxy` | 强制启用代理用于 PikPak 和 qBittorrent 请求 | 自动 |
| `--no-proxy` | 强制禁用代理用于 PikPak 和 qBittorrent 请求 | 自动 |
| `--from-pipeline` | 内部参数：从 pipeline 运行 | `False` |
| `--session-id` | 用于保存 PikPak 统计信息的报告 session ID | `None` |
| `--root-folder` | PikPak 上传根文件夹；每个种子存放在 `{root}/{qB category}` 下 | config 中的 `PIKPAK_ROOT_FOLDER` |

### 示例

```bash
# 默认：批量模式处理超过 3 天的种子
python3 -m apps.cli.pikpak_bridge

# 自定义天数阈值
python3 -m apps.cli.pikpak_bridge --days 7

# 试运行模式
python3 -m apps.cli.pikpak_bridge --dry-run

# 单个模式（逐个处理而非批量）
python3 -m apps.cli.pikpak_bridge --individual

# 使用代理
python3 -m apps.cli.pikpak_bridge --use-proxy

# 自定义根文件夹
python3 -m apps.cli.pikpak_bridge --root-folder "/My Videos"

# 组合选项
python3 -m apps.cli.pikpak_bridge --days 5 --dry-run --use-proxy
```

---

## Migration CLI

**模块：** `apps.cli.migration`

将 SQLite 数据库迁移到当前 schema 版本。还提供回填和对齐子命令。

### 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--history-db` | history.db 的路径（用于 `--backfill-actors`） | 来自 config |
| `--backup` | 迁移前备份数据库文件 | `False` |
| `--verify` | 验证 schema 版本和 MovieHistory 的 actor 列 | `False` |
| `--dry-run` | Schema：仅预览。配合 `--backfill-actors`：获取数据但不执行 UPDATE | `False` |
| `--skip-schema` | 跳过 schema 初始化（仅配合 `--backfill-actors` 或 `--normalize-datetimes` 使用） | `False` |
| `--normalize-datetimes` | 规范化 DateTime TEXT 列（history / reports / operations） | `False` |
| `--backfill-actors` | 从线上详情页回填空的 ActorName（及相关列） | `False` |
| `--limit` | 回填：最大行数（0 = 全部） | `0` |
| `--no-proxy` | 回填：不使用代理直接 HTTP 访问（调试用） | `False` |
| `--use-cf-bypass` | 回填：首次获取时启用 CF 绕过 | `False` |

#### 库存-历史对齐参数

这些参数控制 `--align-inventory-history` 子命令，该命令将仅存在于库存中的代码通过 JavDB 搜索/详情页信息充实后对齐到 MovieHistory 中。

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--align-inventory-history` | 运行库存-历史对齐 | `False` |
| `--align-limit` | 处理的最大缺失代码数（0 = 全部） | `0` |
| `--align-limit-per-worker` | 每个代理 worker 的最大完成任务数（0 = 使用 `--align-limit` 或全部） | `0` |
| `--align-codes` | 逗号分隔的视频代码覆盖列表 | `""` |
| `--align-no-proxy` | 不使用代理直接 HTTP 访问（调试用；默认启用代理） | `False` |
| `--align-no-login` | 跳过需要 JavDB 登录的电影而非尝试认证 | `False` |
| `--align-shuffle` | 随机化处理队列以避免相似前缀的连续失败 | `False` |
| `--align-enqueue-qb` | 将升级磁力链接加入 qBittorrent 队列 | `False` |
| `--align-execute-delete` | 对清除计划 CSV 执行 rclone purge（破坏性操作） | `False` |
| `--align-output-dir` | 生成的报告/计划文件的输出目录 | `""` |
| `--align-qb-category` | 升级入队时覆盖 qBittorrent 分类 | `""` |

### 示例

```bash
# 运行 schema 迁移
python3 -m apps.cli.migration

# 预览迁移但不实际更改
python3 -m apps.cli.migration --dry-run

# 迁移前备份
python3 -m apps.cli.migration --backup

# 验证当前 schema 版本
python3 -m apps.cli.migration --verify

# 从 JavDB 回填演员名称（带限制）
python3 -m apps.cli.migration --backfill-actors --limit 100

# 使用 CF 绕过回填
python3 -m apps.cli.migration --backfill-actors --use-cf-bypass

# 规范化日期时间列
python3 -m apps.cli.migration --normalize-datetimes

# 对齐库存与历史记录
python3 -m apps.cli.migration --align-inventory-history --align-limit 50

# 使用随机队列和每 worker 限制进行对齐
python3 -m apps.cli.migration --align-inventory-history --align-shuffle --align-limit-per-worker 20
```

---

## Login CLI

**模块：** `apps.cli.login`

登录 JavDB 并提取 session cookie。使用新的 `JAVDB_SESSION_COOKIE` 更新 `config.py`。在使用 `--url` 进行自定义 URL 抓取且现有 cookie 已过期时需要执行。

此 CLI 不接受参数。它从 `config.py` 中读取凭据（`JAVDB_USERNAME`、`JAVDB_PASSWORD`）。

### 用法

```bash
python3 -m apps.cli.login
```

该脚本将：

1. 使用你的凭据登录 JavDB
2. 处理验证码（如果配置了 GPT Vision API 则使用 AI 自动识别）
3. 提取并更新 `config.py` 中的 session cookie
4. 验证 cookie 是否有效

### 前置条件

- `config.py` 中必须设置 `JAVDB_USERNAME` 和 `JAVDB_PASSWORD`
- 可选：设置 `GPT_API_KEY` 和 `GPT_API_URL` 以启用基于 AI 的验证码识别

---

## Rollback CLI

**模块：** `apps.cli.rollback`

撤销来自进行中或失败的工作流运行的 D1/SQLite 写入。支持自动的失败清理和手动的定向回滚。

**默认模式为试运行。** 传入 `--apply` 以实际执行回滚。

### 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--session-id` | 要回滚的 ReportSessions.Id | `None` |
| `--run-id` | 失败运行的 GITHUB_RUN_ID | `None` |
| `--attempt` | GITHUB_RUN_ATTEMPT（配合 `--run-id` 使用） | `None` |
| `--run-started-at` | 失败运行的 ISO 时间戳 | `None` |
| `--scope` | 限制清理范围到单个逻辑数据库：`reports`、`operations`、`history` 或 `all` | `all` |
| `--include-orphaned` | 同时包含 `--run-started-at` 时间窗口内的 in_progress session | `False` |
| `--failure-reason` | 持久化到 ReportSessions.FailureReason 的注释 | 自动推导 |
| `--dry-run` | 显示将被删除的内容（默认） | `True` |
| `--apply` | 实际执行回滚 | `False` |
| `--force` | 允许回滚已提交的 session | `False` |
| `--shard-date` | MovieClaim coordinator 回滚的 YYYY-MM-DD 分片日期 | 今天 |
| `--no-claim-rollback` | 跳过 MovieClaim coordinator 的 rollback_staged_movies 调用 | `False` |
| `--auto-resume-finalizing` | 对于处于 `finalizing` 状态的 pending mode session，驱动其完成到 `committed` | `True` |
| `--no-auto-resume-finalizing` | 拒绝处理 `finalizing` 状态的 session；将其标记为失败 | `False` |
| `--claim-rollback-attempts` | 发生暂时性故障时 rollback_staged_movies 的重试次数 | `3` |
| `--log-level` | 日志级别：`DEBUG`、`INFO`、`WARNING`、`ERROR` | `INFO` |

### 退出码

| 退出码 | 含义 |
|--------|------|
| `0` | 成功（或试运行完成且无错误） |
| `2` | Session 已提交（未使用 `--force` 时拒绝），或跨日拒绝 |
| `3` | 无法连接到 D1/SQLite |
| `4` | 部分失败（某些 session 仍为 `failed` 状态且 drift 不为零） |

### 示例

```bash
# 试运行定向回滚
python3 -m apps.cli.rollback --session-id 42

# 实际执行定向回滚
python3 -m apps.cli.rollback --session-id 42 --apply

# 按 GitHub 运行标识回滚
python3 -m apps.cli.rollback --run-id 12345 --attempt 1

# 失败时自动清理（自动化场景，不知道具体 session）
python3 -m apps.cli.rollback \
  --run-id 12345 --attempt 1 \
  --run-started-at 2026-05-04T19:30:00Z

# 限定范围
python3 -m apps.cli.rollback --session-id 42 --scope history

# 强制回滚已提交的 session
python3 -m apps.cli.rollback --session-id 42 --apply --force

# 遗留清扫（包含时间窗口内的孤立 session）
python3 -m apps.cli.rollback --session-id 42 \
  --run-started-at 2026-05-04T19:30:00Z --include-orphaned
```

---

## Config Generator CLI

**模块：** `apps.cli.config_generator`

从环境变量生成 `config.py`。GitHub Actions 工作流使用此工具，根据 `VAR_*` 环境变量（来自仓库 secrets / variables）在运行时物化配置文件。通常不需要手动运行，除非在本地调试 GH Actions 配置。

### 用法

```bash
# GitHub Actions 模式 —— 读取 VAR_* 环境变量并写入 config.py
python3 -m apps.cli.config_generator --github-actions
```

### 行为

- 读取所有 `VAR_*` 环境变量（如 `VAR_QB_URL`、`VAR_QB_USERNAME`）
- 映射 `VAR_FOO` → 输出 `config.py` 中的 `FOO`
- 记录读取了哪些变量（不记录值，确保安全）
- 任何必需变量缺失时以非零状态退出

完整的 `VAR_*` 映射列表请参见 [GitHub Actions 部署](../self-hoster/github-actions-setup.md)。

---

## Spider 完整参数参考

`apps.cli.spider` 接受的所有参数：

| 参数 | 类型 | 说明 | 默认值 | 示例 |
|------|------|------|--------|------|
| `--dry-run` | 标志 | 打印条目但不写入 CSV | `False` | `--dry-run` |
| `--output-file` | 字符串 | 自定义 CSV 文件名（不改变目录） | 自动生成 | `--output-file results.csv` |
| `--start-page` | 整数 | 起始页码 | `1` | `--start-page 5` |
| `--end-page` | 整数 | 结束页码 | `20` | `--end-page 10` |
| `--all` | 标志 | 解析直到空页面（忽略 `--end-page`） | `False` | `--all` |
| `--ignore-history` | 标志 | 读取时忽略历史记录（抓取所有页面）但仍保存到历史记录。Ad-hoc 模式默认已忽略读取历史记录 | `False` | `--ignore-history` |
| `--use-history` | 标志 | 在 ad-hoc 模式中启用历史记录过滤（ad-hoc 默认忽略读取历史记录） | `False` | `--use-history` |
| `--url` | 字符串 | 要抓取的自定义 URL（启用 ad-hoc 模式；分页 URL 请添加 `?page=x`） | `None` | `--url "https://javdb.com/?vft=2"` |
| `--phase` | 选择 | 运行阶段：`1`（字幕+今日）、`2`（仅今日）、`all`（两者） | `all` | `--phase 1` |
| `--ignore-release-date` | 标志 | 忽略今日/昨日标签，下载所有符合阶段条件的条目 | `False` | `--ignore-release-date` |
| `--use-proxy` | 标志 | 本次运行强制启用代理 | 自动（`PROXY_MODULES`） | `--use-proxy` |
| `--no-proxy` | 标志 | 本次运行强制禁用代理 | 自动（`PROXY_MODULES`） | `--no-proxy` |
| `--sequential` | 标志 | 在代理池模式下强制串行处理详情页 | `False` | `--sequential` |
| `--always-bypass-time` | 整数（可选） | 回退成功后继续使用 CF 绕过的分钟数（省略值或 0 = 整个会话；省略标志 = 始终直连优先） | `None` | `--always-bypass-time 30` |
| `--max-movies-phase1` | 整数 | 限制 Phase 1 电影数量（测试用） | `None` | `--max-movies-phase1 10` |
| `--max-movies-phase2` | 整数 | 限制 Phase 2 电影数量（测试用） | `None` | `--max-movies-phase2 5` |
| `--no-rclone-filter` | 标志 | 禁用 rclone 库存过滤（不跳过已在 rclone 库存中的条目） | `False` | `--no-rclone-filter` |
| `--disable-all-filters` | 标志 | 禁用所有过滤器（历史记录、rclone 库存、发布日期）— 处理索引中的每一个条目 | `False` | `--disable-all-filters` |
| `--enable-dedup` | 标志 | 启用 rclone 去重检测（与 rclone 库存对比） | `False` | `--enable-dedup` |
| `--enable-redownload` | 标志 | 当同类别种子显著更大时启用重新下载 | `False` | `--enable-redownload` |
| `--redownload-threshold` | 浮点数 | 重新下载的大小增长阈值（0.30 = 30%） | `0.30` | `--redownload-threshold 0.50` |
| `--from-pipeline` | 标志 | 内部参数：从 pipeline 运行（使用 GIT_USERNAME 进行提交） | `False` | `--from-pipeline` |
