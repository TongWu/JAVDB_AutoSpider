# 日志系统

系统提供全面的双模式日志功能，支持四个严重级别和进度追踪。

## 日志级别

| 级别 | 用途 |
|---|---|
| `INFO` | 带进度追踪的一般信息 |
| `WARNING` | 非关键问题（代理失败、缺少可选配置等） |
| `DEBUG` | 详细调试信息（SQL 查询、HTTP 详情、每个代理的统计数据） |
| `ERROR` | 可能导致执行中断的关键错误 |

### 设置日志级别

环境变量的优先级高于 config.py：

```bash
# 通过环境变量设置（最高优先级）
export LOG_LEVEL=DEBUG

# 或在 config.py 中设置
LOG_LEVEL = 'INFO'
```

## 进度追踪

爬虫和上传器在日志输出中包含结构化进度指示器：

- `[Page 1/5]` -- 索引抓取期间的页面级进度
- `[15/75]` -- 所有页面中的条目级进度
- `[1/25]` -- qBittorrent 种子添加的上传进度

## 控制台 / 文件双模式格式

控制台输出和文件日志默认使用不同的格式：

- **控制台**：以适合移动端的紧凑格式渲染
- **文件日志**（`logs/spider.log` 等）：始终使用详细的 4 字段格式，便于 grep 搜索和取证分析

章节和摘要通过共享辅助函数（`log_section`、`log_summary_block`、`log_group_start|end`）输出，因此同一调用在每个输出目标中都能正确渲染。

## LOG_STYLE

通过 `LOG_STYLE` 环境变量控制控制台输出格式：

| `LOG_STYLE` | 控制台行为 |
|---|---|
| `compact`（默认） | `HH:MM:SS  > Component  message` + Unicode 章节分隔符；emoji 锚定的阶段/摘要块。针对 GitHub 移动端和 30 秒快速浏览优化。 |
| `plain` | `HH:MM:SS LVL Component  message`（仅 ASCII）；章节渲染为 `==== TITLE ====`。最适合 `tail | grep` 管道和最小化终端。 |
| `verbose` | 旧版 `<asctime> - <name> - <level> - <message>` 格式。完整的回退方案——用于二分查找日志格式变更时使用。 |

```bash
# 使用纯 ASCII 格式
export LOG_STYLE=plain

# 使用旧版详细格式
export LOG_STYLE=verbose
```

文件日志（`logs/spider.log` 等）始终使用详细的 4 字段格式，不受 `LOG_STYLE` 影响。

## GitHub Actions 折叠分组

在 GitHub Actions 中运行时（环境中 `GITHUB_ACTIONS=true`），日志系统自动使用 `::group::TITLE` / `::endgroup::` 标记在 Actions UI 中创建可折叠的章节。

通过 `LOG_GITHUB_GROUPS` 控制此行为：

| 值 | 行为 |
|---|---|
| `auto`（默认） | 当 `GITHUB_ACTIONS=true` 时折叠，否则不折叠 |
| `on` | 始终输出分组标记 |
| `off` | 从不输出分组标记 |

```bash
# 强制关闭（从 CI 中提取原始爬虫日志时有用）
export LOG_GITHUB_GROUPS=off
```

启用折叠后，冗长但信息密度低的块（每代理详情、双写差异、JSON 转储）在 GitHub Actions UI 中默认折叠，使运行摘要面板优先显示关键指标。

## GitHub Actions 步骤摘要

摄入工作流（`DailyIngestion` / `AdHocIngestion`）解析爬虫的 `SPIDER_STAT_*` 标准输出行，并将 Markdown 表格写入 `$GITHUB_STEP_SUMMARY`。这使得运行的关键指标（页数、发现数、解析数、跳过数、失败数、CSV 文件名和 session_id）直接显示在 Actions UI 摘要面板中，无需展开爬虫日志块。

## 日志文件路径

默认日志文件路径（可在 config.py 中配置，或通过 CI 中的 `VAR_*` 环境变量配置）：

| 设置项 | 默认路径 |
|---|---|
| `SPIDER_LOG_FILE` | `logs/spider.log` |
| `UPLOADER_LOG_FILE` | `logs/qb_uploader.log` |
| `PIPELINE_LOG_FILE` | `logs/pipeline.log` |
| `EMAIL_NOTIFICATION_LOG_FILE` | `logs/email_notification.log` |
| `PIKPAK_LOG_FILE` | `logs/pikpak_bridge.log` |
| `QB_FILE_FILTER_LOG_FILE` | `logs/qb_file_filter.log` |
| `DEDUP_LOG_FILE` | `logs/rclone_dedup.log` |

## DEBUG 级别详情

在 `DEBUG` 级别下，以下额外信息会被输出：

- **代理池**：显示每个代理的详细行，包括成功率、最近成功时间和最近失败时间。在 `INFO` 级别下，仅显示单行摘要：`available=N/total / cooldown=K / banned=B`。
- **Rust 扩展日志**：Rust 端日志目标（`javdb_rust_core::proxy::pool` 等）通过 `pyo3_log` 流经 Python 格式化器，并映射为简短的显示名称：`ProxyPool`、`BanManager`、`FetchEngine`、`Parser`。
- **数据库查询**：历史和会话操作的 SQL 语句和行数。
- **HTTP 请求详情**：请求头、状态码和响应耗时。

## 在代码中使用日志

项目使用 Python 标准 `logging` 模块和共享格式化辅助函数：

```python
import logging
from javdb.infra.logging import log_section, log_summary_block

logger = logging.getLogger(__name__)

# 章节标题（自动适配控制台/文件/GitHub Actions 格式）
log_section("Phase 1: Subtitle Entries")

# 摘要块（emoji 锚定，在 GitHub Actions 中可折叠）
log_summary_block("Spider Statistics", {
    "Pages processed": 5,
    "Movies found": 75,
    "Torrents extracted": 150,
})

# 常规日志
logger.info("Processing movie: %s", movie_title)
logger.warning("Proxy failed, switching to backup")
logger.error("Failed to connect to qBittorrent", exc_info=True)
```
