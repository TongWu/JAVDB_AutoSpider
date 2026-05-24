# ADR-021: API 资源边界 — 日志扫描上限与 Explore 缓存限制

| 字段       | 值                                                                     |
| ---------- | ---------------------------------------------------------------------- |
| **状态**   | 已接受                                                                  |
| **日期**   | 2026-05-24                                                              |
| **作者**   | Ted                                                                     |
| **关联**   | [ADR-018](../ADR-018-Web-Security-Hardening/ADR-018-web-security-hardening.md) |

## 背景

代码审查发现两个 API 端点存在无界资源消耗：

1. **`GET /api/logs/search`**（[logs.py](../../../apps/api/routers/logs.py)）— 扫描 `logs/jobs/` 下所有 `*.meta.json` 文件，没有上限。每个 meta 文件触发一次文件系统读取和 JSON 解析。虽然当前生产环境的文件量较小（30–270 个），但没有清理机制，数量只增不减。I/O 延迟（同步 glob + 读取 N 个文件）和内存（candidates 列表 + 后续日志行扫描）都是隐患。

2. **`_DOWNLOADED_MAP_CACHE`**（[explore_service.py](../../../apps/api/services/explore_service.py)）— 一个普通 `dict`，有 TTL 过期（10 秒）但没有大小上限。缓存 key 是解析后的 CSV 文件路径；实际上只有一个 key（`reports/parsed_movies_history.csv`）。风险不在当下而在结构上：如果配置变更引入了新的 CSV 路径，缓存会无限增长。相邻的 `EXPLORE_DETAIL_CACHE` 已有 `CACHE_MAX_ITEMS` 守卫；`_DOWNLOADED_MAP_CACHE` 应当对齐。

两个问题目前严重性都很低，但属于缺失的安全网——随着数据累积可能导致内存耗尽或请求超时。

## 决策

### 1. 日志 Meta 扫描上限

在 `logs.py` 中添加模块级常量 `_MAX_META_SCAN = 200`。现有的 `sorted(..., reverse=True)` 循环已经按最新优先排序；用 `itertools.islice` 包装后在扫描 `_MAX_META_SCAN` 个文件后停止。

**为什么是 200：** 生产环境文件量为 30–270 个。200 的上限在典型上限之上留有 2–3 倍余量，同时防止在清理被忽略数月后的失控扫描。

**客户端可观测性：** 在 `LogSearchResponse` 中新增 `scanned_files: int` 字段，让前端在结果因扫描上限被截断时（即 `scanned_files == _MAX_META_SCAN`）显示提示。

**不通过环境变量配置。** 上限是安全网，不是调优旋钮。如果默认值不合适，修改常量并部署——与调整 `_HARD_CAP` 的工作流程相同。

### 2. Downloaded-Map 缓存大小限制

在 `explore_service.py` 中添加 `_MAX_DOWNLOADED_MAP_CACHE_SIZE = 8` 常量。向 `_DOWNLOADED_MAP_CACHE` 插入新条目后，如果 `len(cache) > _MAX_DOWNLOADED_MAP_CACHE_SIZE`，淘汰时间戳最旧的条目（LRU 风格，与 `EXPLORE_DETAIL_CACHE` 的淘汰模式一致）。

**为什么是 8：** 实际上只有 1 个 key。上限 8 足够宽裕，正常使用下永远不会触发，同时防止配置面变化时的无界增长。

**为什么选 LRU 而非硬拒绝：** 硬拒绝（拒绝新写入）会在达到上限时降低 explore 功能的可用性。LRU 通过淘汰不太可能在 10 秒 TTL 窗口内被复用的旧条目来保持缓存功能正常。

## 影响

- **日志搜索：** 对非常旧的日志（超过 200 个 job 之前、且未使用日期过滤）的搜索可能漏掉结果。这是可以接受的，因为（a）端点仅限 admin 访问，（b）日期过滤在上限生效前缩小扫描范围，（c）`scanned_files` 字段使截断可见。
- **Explore 缓存：** 正常使用下无行为变化。当只配置了 1 个 CSV 路径时，LRU 淘汰是空操作。
- **不引入新环境变量。** 两个上限都是编译时常量，保持配置面不变。
- **测试：** 单元测试应验证（a）扫描上限正确截断并报告 `scanned_files`，（b）缓存在超过上限时淘汰最旧条目。
