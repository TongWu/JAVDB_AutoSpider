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
- [qBittorrent 上传器](#qbittorrent-上传器)（`apps.cli.qb.uploader`）
- [qBittorrent 文件过滤器](#qbittorrent-文件过滤器)（`apps.cli.qb.file_filter`）
- [清理丢失文件种子](#清理丢失文件种子)（`apps.cli.qb.purge_missing_files`）
- [种子质量证据](#种子质量证据)（`apps.cli.qb.quality_evidence`）
- [PikPak Bridge](#pikpak-bridge)（`apps.cli.pikpak.bridge`）
- [Migration CLI](#migration-cli)（`apps.cli.db.migration`）
- [Login CLI](#login-cli)（`apps.cli.login`）
- [Rollback CLI](#rollback-cli)（`apps.cli.db.rollback`）
- [运维诊断 CLI](#运维诊断-cli)（`apps.cli.ops.diagnose_run`）
- [采集结果对账 CLI](#采集结果对账-cli)（`apps.cli.ops.reconcile`）
- [内容过滤 CLI](#内容过滤-cli)（`apps.cli.ops.content_filter`）
- [事件主线消费者 CLI](#事件主线消费者-cli)（`apps.cli.ops.events`）
- [站点契约哨兵 CLI](#站点契约哨兵-cli)（`apps.cli.ops.sentinel`）
- [CF Bypass Probe CLI](#cf-bypass-probe-cli)（`apps.cli.ops.cf_bypass_probe`）
- [Config Generator CLI](#config-generator-cli)（`apps.cli.ops.config_generator`）
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

**模块：** `apps.cli.qb.uploader`

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
python3 -m apps.cli.qb.uploader

# Ad-hoc 模式（用于自定义 URL 抓取结果）
python3 -m apps.cli.qb.uploader --mode adhoc

# 指定输入文件
python3 -m apps.cli.qb.uploader --input-file my_results.csv

# 为 qBittorrent API 使用代理
python3 -m apps.cli.qb.uploader --use-proxy

# 覆盖分类
python3 -m apps.cli.qb.uploader --mode adhoc --category "Custom Category"
```

---

## qBittorrent 文件过滤器

**模块：** `apps.cli.qb.file_filter`

过滤 qBittorrent 中最近添加的种子中的小文件。将低于大小阈值的不需要的文件设置为"不下载"优先级。对于刚添加的种子，过滤器会最多等待 90 秒让 qBittorrent metadata 就绪，以便小文件在开始下载前就被过滤。

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
python3 -m apps.cli.qb.file_filter

# 覆盖阈值（例如 50MB）和天数
python3 -m apps.cli.qb.file_filter --min-size 50
python3 -m apps.cli.qb.file_filter --min-size 100 --days 3

# 试运行（预览但不更改）
python3 -m apps.cli.qb.file_filter --dry-run

# 仅过滤特定分类
python3 -m apps.cli.qb.file_filter --category JavDB

# 过滤多个分类
python3 -m apps.cli.qb.file_filter --categories '["Ad Hoc", "Daily Ingestion"]'

# 使用代理
python3 -m apps.cli.qb.file_filter --use-proxy

# 删除已下载的小文件
python3 -m apps.cli.qb.file_filter --delete-local-files
```

---

## 清理丢失文件种子

**模块：** `apps.cli.qb.purge_missing_files`

清理卡在 qBittorrent `missingFiles` 状态的种子，覆盖主 qB 和 adhoc qB 的**所有分类**。种子进入 `missingFiles` 通常是内容上传到云端后本地文件被删除的正常归宿。

qB 不提供「当前磁盘文件夹大小」的 API，且对每个 `missingFiles` 种子一律报 `progress=0`。因此命令对每个种子先 **stop**，再强制 **recheck**（qB 重新核对磁盘），然后读取核对后的 per-file progress。仅当内容已缩小到原始大小的 50% 以下、**且**磁盘上还在的文件都不超过 `QB_FILE_FILTER_MIN_SIZE_MB` 阈值（100MB）时，才连同文件一起删除条目；磁盘上大文件确实还在的种子会原样保留（停止、文件完好）。只处理完成时间至少在 `--min-age-hours` 之前的种子。

recheck 前先 stop 是为了防止 qB 把文件其实还在的种子重新做种。注意即使 `--dry-run` 也会执行 stop+recheck（决策就是这么算出来的），种子会从 `missingFiles` 变为 `stopped`；这是无损且可逆的。

### 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--min-age-hours` | 只处理完成时间至少在这么多小时之前的种子 | `22` |
| `--dry-run` | 只列出决策不删除（仍会 stop + recheck） | `False` |
| `--json` | 以 JSON 输出每个实例的汇总 | `False` |

qB 采用直连（不走代理），与 reconcile 轮次的连接方式一致。当配置了 `QB_URL_ADHOC` 时会一并处理 adhoc 实例；adhoc qB 连不上会被跳过、不影响整个运行。

### 示例

```bash
# 预览决策但不删除（仍会 stop + recheck）
python3 -m apps.cli.qb.purge_missing_files --dry-run --json

# 执行清理（删除条目；仅对内容确实丢失的连文件一起删）
python3 -m apps.cli.qb.purge_missing_files

# 把完成时间门槛放宽到 7 天
python3 -m apps.cli.qb.purge_missing_files --min-age-hours 168
```

---

## 种子质量证据

**模块：** `apps.cli.qb.quality_evidence`

为生产选中/最近添加且 qBittorrent metadata 可用的种子采集 ADR-024 Phase 1
影子证据。采集器对 qBittorrent 只读，且只有在
`TORRENT_QUALITY_EVIDENCE_ENABLED=True` 或提供 `--force` 时才会运行。

### 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--days` | 向前查找生产种子的天数 | `2` |
| `--categories` | 要扫描的 qBittorrent 分类 JSON 数组 | `TORRENT_QUALITY_CATEGORIES` |
| `--force` | 即使配置中禁用了证据采集也运行 | `False` |
| `--use-proxy` | 强制启用代理用于 qBittorrent API 请求 | 自动 |
| `--no-proxy` | 强制禁用代理用于 qBittorrent API 请求 | 自动 |

### 示例

```bash
python3 -m apps.cli.qb.quality_evidence --days 2 --categories '["Daily Ingestion"]'
python3 -m apps.cli.qb.quality_evidence --force --categories '["Daily Ingestion"]'
```

---

## PikPak Bridge

**模块：** `apps.cli.pikpak.bridge`

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
python3 -m apps.cli.pikpak.bridge

# 自定义天数阈值
python3 -m apps.cli.pikpak.bridge --days 7

# 试运行模式
python3 -m apps.cli.pikpak.bridge --dry-run

# 单个模式（逐个处理而非批量）
python3 -m apps.cli.pikpak.bridge --individual

# 使用代理
python3 -m apps.cli.pikpak.bridge --use-proxy

# 自定义根文件夹
python3 -m apps.cli.pikpak.bridge --root-folder "/My Videos"

# 组合选项
python3 -m apps.cli.pikpak.bridge --days 5 --dry-run --use-proxy
```

---

## Migration CLI

**模块：** `apps.cli.db.migration`

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
python3 -m apps.cli.db.migration

# 预览迁移但不实际更改
python3 -m apps.cli.db.migration --dry-run

# 迁移前备份
python3 -m apps.cli.db.migration --backup

# 验证当前 schema 版本
python3 -m apps.cli.db.migration --verify

# 从 JavDB 回填演员名称（带限制）
python3 -m apps.cli.db.migration --backfill-actors --limit 100

# 使用 CF 绕过回填
python3 -m apps.cli.db.migration --backfill-actors --use-cf-bypass

# 规范化日期时间列
python3 -m apps.cli.db.migration --normalize-datetimes

# 对齐库存与历史记录
python3 -m apps.cli.db.migration --align-inventory-history --align-limit 50

# 使用随机队列和每 worker 限制进行对齐
python3 -m apps.cli.db.migration --align-inventory-history --align-shuffle --align-limit-per-worker 20
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

**模块：** `apps.cli.db.rollback`

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
python3 -m apps.cli.db.rollback --session-id 42

# 实际执行定向回滚
python3 -m apps.cli.db.rollback --session-id 42 --apply

# 按 GitHub 运行标识回滚
python3 -m apps.cli.db.rollback --run-id 12345 --attempt 1

# 失败时自动清理（自动化场景，不知道具体 session）
python3 -m apps.cli.db.rollback \
  --run-id 12345 --attempt 1 \
  --run-started-at 2026-05-04T19:30:00Z

# 限定范围
python3 -m apps.cli.db.rollback --session-id 42 --scope history

# 强制回滚已提交的 session
python3 -m apps.cli.db.rollback --session-id 42 --apply --force

# 遗留清扫（包含时间窗口内的孤立 session）
python3 -m apps.cli.db.rollback --session-id 42 \
  --run-started-at 2026-05-04T19:30:00Z --include-orphaned
```

---

## 运维诊断 CLI

**模块：** `apps.cli.ops.diagnose_run`

为失败的 workflow run、session、D1 drift 检查或 recovery 排查收集只读证据，并持久化一条 `OpsIncidents` 记录。该命令会输出结构化摘要，`DailyIngestion.yml` 和 `AdHocIngestion.yml` 会在发送邮件通知前调用它。

### 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--run-id` | 关联到 incident 的 GitHub Actions run ID。未提供 `--session-id` 时必填。 | `None` |
| `--attempt` | GitHub Actions run attempt，存为 `run_attempt`。 | `None` |
| `--session-id` | Pipeline session ID，格式为 `YYYYMMDDTHHMMSS.ffffffZ-TTTT-SSSS`。未提供 `--run-id` 时必填。 | `None` |
| `--workflow-name` | Workflow 名称，例如 `DailyIngestion`、`AdHocIngestion` 或 `TestIngestion`。 | `None` |
| `--workflow-result` | Workflow 结果。可选：`success`、`failure`、`cancelled`、`skipped`。 | `None` |
| `--trigger-source` | 诊断触发来源标签，例如 `manual_cli` 或 `workflow_failure`。 | `manual_cli` |
| `--session-status` | 可选 session lifecycle 状态，作为证据写入。 | `None` |
| `--drift-verdict` | 可选 D1 drift verdict，作为证据写入，例如 `CLEAN`、`SAFE_TO_APPLY` 或升级排查类 verdict。 | `None` |
| `--log` | 扫描错误片段的日志文件，可重复传入。 | `[]` |
| `--json` | 输出 JSON payload，而非文本摘要。 | `False` |
| `--log-level` | 日志级别。可选：`DEBUG`、`INFO`、`WARNING`、`ERROR`。 | `INFO` |

退出码 `0` 表示未检测到已知 incident 类型。退出码 `1` 表示已为已知 incident 类型生成诊断。退出码 `2` 表示缺少必需的 run/session 标识，`3` 表示诊断发生意外失败。

### 示例

```bash
# 诊断失败 workflow run，并输出 JSON 供后续邮件/API 使用
python3 -m apps.cli.ops.diagnose_run \
  --trigger-source workflow_failure \
  --run-id 123456789 \
  --attempt 1 \
  --session-id 20260527T120000.000000Z-0001-0001 \
  --workflow-name DailyIngestion \
  --workflow-result failure \
  --json \
  --log logs/pipeline.log \
  --log logs/spider.log

# 将 CLEAN drift 检查作为证据记录，但不把它分类为 D1 drift incident
python3 -m apps.cli.ops.diagnose_run \
  --run-id 123456789 \
  --drift-verdict CLEAN
```

---

## 采集结果对账 CLI

**模块：** `apps.cli.ops.reconcile`

运行 ADR-033 媒体闭环对账轮次（Phase 1+2+3）。默认运行全部三个轮次（`--pass all`）：

- **acquisition pass（采集轮次）** — 读取 qBittorrent 实时状态，将活跃
  `AcquisitionOutcome` 从 `queued` / `downloading` 推进到 `downloading`、
  `completed`、`stalled` 或 `failed`。
- **ownership pass（所有权轮次）** — 从四个来源收集所有权观测结果
  （`gdrive` 通过 `RcloneInventory` 投影、`qb` 通过 `AcquisitionOutcome`
  bridge、`pikpak` 通过 `PikpakHistory` success 行、`nas` 为前向兼容 stub，当前
  无论是否配置 `RCLONE_NAS_REMOTE` 都始终 no-op），upsert 到 `OwnershipLedger`，执行
  present sweep 将缺失行的 `present` 置 `0`，并将符合条件的
  `AcquisitionOutcome` 从 `completed` 推进到 `in_library`。
- **consumption pass（消费轮次）** — 轮询 `MEDIA_SERVERS` 中配置的每个媒体服务器
  实例（详见 [媒体服务器设置](../self-hoster/media-servers.md)），通过高/中/低
  置信度 join-key 阶梯将每个条目的标题解析为 `video_code`，将已解析条目写入
  `ConsumptionSignal`，将无法解析的条目写入 `UnresolvedMediaItem`。若
  `MEDIA_SERVERS` 为空，此轮次为 no-op；若 `MEDIA_SERVERS` 格式有误，退出码为 `1`。

使用 `--pass all` 并加 `--json` 时，输出 payload 格式为：
`{"acquisition": {...}, "ownership": {...}, "consumption": {...}}`。

生产环境应使用 `STORAGE_BACKEND=d1`，因为 `AcquisitionOutcome`、`OwnershipLedger`、
`ConsumptionSignal` 和 `UnresolvedMediaItem` 均是 operations 数据库中的 D1 canonical 表。

### 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--pass` | 要运行的对账轮次：`acquisition`、`ownership`、`consumption` 或 `all`（全部三个轮次顺序执行）。 | `all` |
| `--source` | 要对账的来源（acquisition pass），可重复传入。接受 `qb`。 | `qb` |
| `--category` | 要扫描的 qB 分类（acquisition pass），可重复传入。 | `TORRENT_CATEGORY`、`TORRENT_CATEGORY_ADHOC` |
| `--stalled-after-days` | 正整数。活跃 outcome 超过该天数未被观测到会变为 `stalled`；超过 2 倍窗口会变为 `failed`。 | `RECONCILE_STALLED_DAYS` 或 `7` |
| `--dry-run` | 只计算状态迁移，不写入数据库。 | `False` |
| `--json` | 输出 JSON result payload。`--pass all` 时格式为 `{"acquisition": {...}, "ownership": {...}, "consumption": {...}}`。 | `False` |
| `--log-level` | 日志级别。可选：`DEBUG`、`INFO`、`WARNING`、`ERROR`。 | `INFO` |

退出码 `0` 表示所有请求的轮次均完成且没有来源或写入错误。退出码 `2` 表示对账完成但记录了错误，`1` 表示 CLI 发生意外失败。

传入 `--category` 时（acquisition pass），本次运行会被视为部分扫描：已观测到的
hash 仍可推进到 `downloading` / `completed`，但不会把该子集里缺失的 outcome
标记为 `stalled` 或 `failed`。

### 示例

```bash
# 生产 cron 路径：运行三个轮次，对 D1 做对账，并输出 JSON
STORAGE_BACKEND=d1 python3 -m apps.cli.ops.reconcile --pass all --json

# 只运行 acquisition pass
STORAGE_BACKEND=d1 python3 -m apps.cli.ops.reconcile --pass acquisition --json

# 只运行 ownership pass
STORAGE_BACKEND=d1 python3 -m apps.cli.ops.reconcile --pass ownership --json

# 只运行 consumption pass（需在 config.py 中配置 MEDIA_SERVERS）
STORAGE_BACKEND=d1 python3 -m apps.cli.ops.reconcile --pass consumption --json

# 使用更宽阈值预览 stalled/failed 状态迁移（仅 acquisition pass）
STORAGE_BACKEND=d1 python3 -m apps.cli.ops.reconcile \
  --pass acquisition \
  --stalled-after-days 14 \
  --dry-run \
  --json

# 只对一个 qB 分类做对账（acquisition pass）；此时禁用缺失状态推断
STORAGE_BACKEND=d1 python3 -m apps.cli.ops.reconcile \
  --pass acquisition \
  --category "Daily Ingestion"
```

---

## 内容过滤 CLI

**模块：** `apps.cli.ops.content_filter`

管理 reports 数据库 `ContentFilterRule` 表中的 ADR-040 内容过滤规则。Spider
会在每次运行开始时加载一次启用规则，并在详情页解析后、写入 CSV/report 和上传
qBittorrent 前进行判定。

### 命令

| 命令 | 说明 |
|------|------|
| `add` | 新增规则。 |
| `list` | 列出所有规则，包括已禁用规则。 |
| `remove` | 按 id 删除规则。 |
| `enable` | 按 id 启用规则；带 `--off` 时禁用规则。 |

### 支持的规则形状

| 维度 | 模式 | 值 |
|------|------|----|
| `actor` | `exclude` | 必填：演员名或演员 href。 |
| `tag` | `exclude` | 必填：tag 名。 |
| `tag` | `include` | 必填：tag 名；存在 include 规则时，至少要命中一个 include tag。 |
| `gender` | `require_lead` | 必填：`female` 或 `male`。 |
| `gender` | `exclude_all_male` | 不需要值；传入 `--value` 会被拒绝。 |
| `age` | `min_age` | 必填：非负整数（若有已知演员年龄更小则丢弃该影片）。 |
| `age` | `max_age` | 必填：非负整数（若有已知演员年龄更大则丢弃该影片）。 |

### 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--dimension` | `add` 使用的规则维度。可选：`actor`、`tag`、`gender`、`age`。 | 必填 |
| `--mode` | `add` 使用的规则模式。可选：`exclude`、`include`、`require_lead`、`exclude_all_male`、`min_age`、`max_age`。 | 必填 |
| `--value` | `add` 使用的规则值：根据规则可为演员名/href、tag 名、lead gender 或非负整数（age 模式）。除 `gender exclude_all_male` 外均必填。 | `""` |
| `--id` | `remove` 和 `enable` 使用的规则 id。 | 必填 |
| `--off` | 在 `enable` 命令中禁用规则，而不是启用规则。 | `False` |
| `--log-level` | 日志级别。可选：`DEBUG`、`INFO`、`WARNING`、`ERROR`。 | `INFO` |

### 示例

```bash
# 丢弃演员名或 href 命中该值的影片
python3 -m apps.cli.ops.content_filter add \
  --dimension actor \
  --mode exclude \
  --value "/actors/EvkJ"

# 要求至少命中一个 include tag
python3 -m apps.cli.ops.content_filter add \
  --dimension tag \
  --mode include \
  --value subtitle

# 要求 lead actor 为 female
python3 -m apps.cli.ops.content_filter add \
  --dimension gender \
  --mode require_lead \
  --value female

# 丢弃全男演员详情页
python3 -m apps.cli.ops.content_filter add \
  --dimension gender \
  --mode exclude_all_male

# 查看和管理规则
python3 -m apps.cli.ops.content_filter list
python3 -m apps.cli.ops.content_filter enable --id 3 --off
python3 -m apps.cli.ops.content_filter enable --id 3
python3 -m apps.cli.ops.content_filter remove --id 3
```

#### 年龄规则（ADR-040 Phase 2，尽力而为）

```bash
# 丢弃包含发行日期时年龄已知且未满 18 岁演员的影片
python3 -m apps.cli.ops.content_filter add --dimension age --mode min_age --value 18

# 丢弃包含发行日期时年龄已知且超过 40 岁演员的影片
python3 -m apps.cli.ops.content_filter add --dimension age --mode max_age --value 40
```

年龄通过演员名从 minnano-av 尽力而为地解析，缓存在 `ActorMetadata` 中，并按影片发行日期计算。
未能解析生日的演员年龄未知，不会导致影片被丢弃。

```bash
python3 -m apps.cli.ops.actor_age list                       # 查看缓存
python3 -m apps.cli.ops.actor_age refresh --href /actors/EvkJ --name "<name>"  # 强制重新查询
python3 -m apps.cli.ops.actor_age clear --href /actors/EvkJ   # 删除缓存行
```

---

## 事件主线消费者 CLI

**模块：** `apps.cli.ops.events`

运行 ADR-036 事件主线（event spine）的示范消费者。它按游标读取新的 `PipelineEvent`
行，并将"每会话、每事件类型"的计数投影到 `RunEventSummary` 表（两者都在
`javdb-reports` 库中）。这是 Phase 1 对 emit → consume → replay 闭环的验证，
**不触碰**权威的 `pending→commit` 路径。

### 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--replay` | 重置消费者游标并清空投影，然后从 `seq` 0 重建。 | `False` |
| `--batch` | 每次 `read_since` 分页读取的事件数。必须 `>= 1`。 | `500` |
| `--log-level` | 日志级别。可选：`DEBUG`、`INFO`、`WARNING`、`ERROR`。 | `INFO` |

正常运行退出码恒为 `0`；投影的事件数会写入日志。

### 示例

```bash
# 将新事件投影进 RunEventSummary（推进游标）
python3 -m apps.cli.ops.events

# 从头重建投影（幂等 —— 结果一致）
python3 -m apps.cli.ops.events --replay
```

---

## 站点契约哨兵 CLI

**模块：** `apps.cli.ops.sentinel`

评估某个 session 已持久化的解析 field-health，检测站点契约漂移（ADR-035）。它读取该 run 记录的 `ParseRunFieldFill` 行，并将每个字段对照声明式解析契约打分：`critical` 字段对照绝对最低填充率，`soft` 字段对照其滚动基线。该命令为只读，检测到关键漂移时退出码为 `4`，便于 workflow 据此门控。

### 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--session-id` | 待评估 field-health 的 pipeline session ID，格式为 `YYYYMMDDTHHMMSS.ffffffZ-TTTT-SSSS`。必填。 | — |
| `--run-id` | 关联到本次评估的 GitHub Actions run ID。 | `None` |
| `--attempt` | GitHub Actions run attempt，存为 `run_attempt`。 | `None` |
| `--json` | 输出 JSON payload，而非日志摘要。 | `False` |
| `--log-level` | 日志级别。可选：`DEBUG`、`INFO`、`WARNING`、`ERROR`。 | `INFO` |

退出码 `4` 表示该 session 检测到关键漂移；否则退出码为 `0`。

### 示例

```bash
# 评估某个 session 的 field-health 并将 verdict 写入日志
python3 -m apps.cli.ops.sentinel --session-id 20260527T120000.000000Z-0001-0001

# 输出 JSON verdict 供后续邮件/API 使用
python3 -m apps.cli.ops.sentinel \
  --session-id 20260527T120000.000000Z-0001-0001 \
  --run-id 123456789 \
  --attempt 1 \
  --json
```

### 金丝雀模式（Phase 2）

对固定页面执行独立的两次 run 之间抓取+解析（`SiteContractSentinel.yml`）。
在 `config.py` 中配置 `SENTINEL_CANARY_INDEX_URL` 和 `SENTINEL_CANARY_ANCHORS`。

```bash
# Phase 1：评估某个 run 的字段填充率（门控）
python3 -m apps.cli.ops.sentinel --session-id <id>

# Phase 2：对固定页面执行独立金丝雀探测
python3 -m apps.cli.ops.sentinel --canary

# 以 JSON 格式输出当前锚点解析值（将结果填入 SENTINEL_CANARY_ANCHORS）
python3 -m apps.cli.ops.sentinel --capture-anchors --url <detail-url> [--url ...]
```

金丝雀模式适用的 flag：`--run-id`、`--attempt`、`--json`、`--log-level`。退出码：`0` 表示干净;`4` 表示检测到关键漂移(已记录为 `site_drift` incident);`3` 表示金丝雀无法完成(未抓取/解析到任何页面,或检测到漂移但 incident 写入失败——此时该 run 会失败,从而避免漂移被静默丢失);`1` 表示内部错误。

---

## Subscription Monitor CLI

**模块：** `apps.cli.ops.subscription_monitor`

抓取每个 active 的 `ActorSubscription`，复用现有 AdHoc spider 路径，并将真正的新作写入 `NewWorks` feed（ADR-054 WS2）。它不新增评分阈值绕过路径；AdHoc 索引选择本来就会忽略 phase-2 评分与评论数门槛。

### 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--use-proxy` | 使用已配置的代理池抓取演员页。 | `False` |
| `--dry-run` | 只列出 active subscriptions，不执行抓取。 | `False` |
| `--log-level` | 日志级别。可选：`DEBUG`、`INFO`、`WARNING`、`ERROR`。 | `INFO` |

### 示例

```bash
# 只列出 active subscriptions，不抓取
python3 -m apps.cli.ops.subscription_monitor --dry-run

# 生产风格：对 D1 运行，并启用 spider 代理
STORAGE_BACKEND=d1 python3 -m apps.cli.ops.subscription_monitor --use-proxy
```

计划任务入口是 `SubscriptionMonitor.yml`，它在主摄取窗口之后每日运行，也可以手动触发。

---

## CF Bypass Probe CLI

**模块：** `apps.cli.ops.cf_bypass_probe`

对代理池的 Cloudflare 绕过层做诊断扫描。只读：不触碰数据库，也不写入任何历史。

绕过请求遵循当前 run 自身的 `CF_BYPASS_VIA_PROXY` 配置：为 `True` 时**经由 proxy 隧道**
发往 `127.0.0.1`，为 `False` 时直接拨向 `{proxy_ip}:{port}`。若探测的拓扑与生产实际使用
的不一致，会把健康的绕过层报成故障，反之亦然。端口优先取自 `CF_BYPASS_PORT_MAP`，未配置
时回退到 `CF_BYPASS_SERVICE_PORT`。proxy 列表取自 `config.py` 中的 `PROXY_POOL`。

### 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--proxy` | 只探测该名称的 proxy。可重复传入。 | `PROXY_POOL` 全部 |
| `--limit` | 最多探测 N 个 proxy（`0` = 不限）。在 `--proxy` 之后生效。 | `0` |
| `--target` | 要求绕过服务抓取的 URL。必须是 `javdb.com` 或其子域名上的 `https://` 地址。 | `https://javdb.com/` |
| `--workers` | 并行探测 proxy 的线程池大小。必须 `> 0`。 | `8` |
| `--verbose` | 打印每个响应正文的前 300 个字符。 | `False` |
| `--json` | 输出机器可读的 JSON，而不是表格。 | `False` |
| `--bench` | 基准测试模式：每个 proxy 每个端口做 N 次串行试探（`0` = 协议探测）。 | `0` |
| `--ports` | 逗号分隔的待测端口。仅基准测试模式使用；一旦传入，会覆盖所有 proxy 各自解析出的端口。 | 按 proxy 解析出的端口 |

扫描完成时退出码为 `0`；当筛选后没有任何 proxy 时为 `1`；参数被拒绝时为 `2`。非法取值
会在发出任何请求之前被拒绝：`--workers` 非正数、`--limit` / `--bench` 为负数、`--ports`
中出现不在 `1..65535` 范围内的非整数值，以及任何不是允许主机上 `https://` 地址的
`--target`。之所以对 target 做白名单，是因为该值既会被直连抓取，也会被交给每个 proxy 的
绕过服务，而响应正文会打印到 CI 工作流上传为 artifact 的日志里。

**端口解析。** 不传 `--ports` 时，每个 proxy 都按生产环境为它选用的端口探测 ——
即 `CF_BYPASS_SERVICE_PORT`，或 `CF_BYPASS_PORT_MAP` 中以该 proxy IP 为键的覆盖值。

### 默认模式 —— 协议探测

对每个 proxy 拨号四种方言，并分别报告状态码、正文大小与内容标记
（`challenge`、`movie_list`、`json`、`blocked_1020`）：

| 探测项 | 回答的问题 |
|---|---|
| `DIRECT javdb.com` | 对该出口 IP 而言，站点当前是否被墙 |
| `GET :{port}/` | 服务根路径 —— FlareSolverr 在此返回版本 banner，CloudflareBypassForScraping 不会 |
| `GET :{port}/html?url=` | 爬虫实际使用的方言 |
| `GET :8191/` + `POST :8191/v1` | 原生 FlareSolverr，会拆开其 JSON 外层封装 |

结尾的汇总会按 proxy 列出哪些探测项有响应 —— 或者 `NOTHING`。

### 基准测试模式（`--bench N`）

对 `/html?url=` 端点，每个 proxy 每个端口做 N 次串行试探，报告准确率与延迟。在同一个
（proxy, 端口）组合内保持串行，是因为这类 solver 会驱动真实浏览器，并发试探测到的是
资源争用而不是求解时间。

只有**同时**满足以下三点，一次试探才算成功：

1. HTTP 200；
2. 正文不是验证页；
3. 正文中至少包含一个 `href="/v/"` 条目链接。

第三个条件才是关键：以 200 返回验证页的 solver，仅看状态码和大小时同样像是成功。输出
包含 `ok/trials`、p50 与最大延迟、条目数中位数，以及每个端口第一个失败响应的正文。

### 示例

```bash
# 对 PROXY_POOL 中的每个 proxy 做协议探测
python3 -m apps.cli.ops.cf_bypass_probe

# 只测一个 proxy，并打印响应正文
python3 -m apps.cli.ops.cf_bypass_probe --proxy Singapore-ARM1 --verbose

# 前 5 个 proxy 针对指定页面，输出 JSON
python3 -m apps.cli.ops.cf_bypass_probe --limit 5 --target "https://javdb.com/?page=1" --json

# 在一个 proxy 上对比两个 solver 端口，每个各 5 次试探
python3 -m apps.cli.ops.cf_bypass_probe --proxy Singapore-ARM1 --bench 5 --ports 8000,8002
```

CI 入口是 `CFBypassProbe.yml`，参见
[GitHub Actions 部署](../self-hoster/github-actions-setup.md)。

---

## Config Generator CLI

**模块：** `apps.cli.ops.config_generator`

从环境变量生成 `config.py`。GitHub Actions 工作流使用此工具，根据 `VAR_*` 环境变量（来自仓库 secrets / variables）在运行时物化配置文件。通常不需要手动运行，除非在本地调试 GH Actions 配置。

### 用法

```bash
# GitHub Actions 模式 —— 读取 VAR_* 环境变量并写入 config.py
python3 -m apps.cli.ops.config_generator --github-actions
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
| `--end-page` | 整数 | 结束页码。每日模式下是**下限**而非上限——`PAGE_SCAN_DYNAMIC` 开启时，只要页面还带今日/昨日徽章，扫描就会继续越过它，直到 `PAGE_SCAN_MAX`。要限制每日运行的量级，请调低 `PAGE_SCAN_MAX` 或设 `PAGE_SCAN_DYNAMIC=False` | `10` | `--end-page 10` |
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
| `--enable-redownload` | 标志 | 当同类别种子显著更大时启用重新下载 | `ENABLE_REDOWNLOAD` 配置值 | `--enable-redownload` |
| `--redownload-threshold` | 浮点数 | 重新下载的大小增长阈值（0.30 = 30%） | `0.30` | `--redownload-threshold 0.50` |
| `--from-pipeline` | 标志 | 内部参数：从 pipeline 运行（使用 GIT_USERNAME 进行提交） | `False` | `--from-pipeline` |
