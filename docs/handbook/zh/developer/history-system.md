# 历史记录系统与已下载标记

Spider 包含一个智能历史记录系统，用于跟踪每部电影已找到的种子类型。本文档涵盖历史记录跟踪架构、种子分类、处理规则以及重复下载防护功能。

---

## 历史记录系统

### 多种子类型跟踪

历史记录系统跟踪每部电影的所有可用种子类型。每部电影最多可以有四个种子类别：

| 类别 | 说明 |
|------|------|
| `hacked_subtitle` | 破解（无码）版本带字幕 — 最高价值 |
| `hacked_no_subtitle` | 破解（无码）版本无字幕 |
| `subtitle` | 标准版本带字幕 |
| `no_subtitle` | 标准版本无字幕 |

系统会在电影已拥有完整种子集合时防止冗余处理，仅根据下方的优先级规则搜索缺失的种子类型。

### 存储

历史记录存储在两个地方：

1. **SQLite 数据库**（`reports/history.db`）— 通过 `MovieHistory` 和 `TorrentHistory` 表进行主存储
2. **CSV 文件**（`reports/parsed_movies_history.csv`）— 遗留格式，仍为向后兼容而维护

使用 `STORAGE_BACKEND=d1` 或 `dual` 时，历史记录也会存储在 Cloudflare D1 数据库中。

---

## 处理规则

### Phase 1（字幕条目）

Phase 1 针对带有字幕标签的电影（"含中字磁鏈"及类似语言变体）。处理规则：

- **新电影**：无论历史记录如何，始终处理
- **已有电影**：仅在根据优先级规则缺少种子类型时才处理
- 默认按发布日期过滤条目（今日/昨日标签），除非设置了 `--ignore-release-date`

### Phase 2（非字幕 / 质量条目）

Phase 2 针对没有字幕标签但满足质量条件的电影。处理规则：

- 仅处理可以从 `no_subtitle` **升级**为 `hacked_no_subtitle` 的电影
- 必须满足可配置的质量阈值：
  - **最低评分**：`PHASE2_MIN_RATE`（默认：4.0）
  - **最低评论数**：`PHASE2_MIN_COMMENTS`（默认：100）
- 满足质量条件的新电影也会被处理

### 优先级规则

系统在每个类别内遵循严格的优先级层次：

**破解类别优先级：**
1. `hacked_subtitle`（始终优先于 `hacked_no_subtitle`）

**字幕类别优先级：**
1. `subtitle`（始终优先于 `no_subtitle`）

**完整收藏目标：** 每部电影理想情况下应同时拥有两个类别组的代表（一个破解变体 + 一个字幕变体）。

---

## 发布日期过滤

默认情况下，spider 根据发布日期标签过滤条目（"今日新種" = 今日新种子，"昨日新種" = 昨日新种子）。

### 通过命令行覆盖（推荐）

```bash
# 单次运行忽略发布日期标签
python3 -m apps.cli.spider --ignore-release-date

# 或通过 pipeline
python3 -m apps.cli.pipeline --ignore-release-date
```

### 通过配置文件覆盖

在 `config.py` 中设置 `IGNORE_RELEASE_DATE_FILTER = True` 可永久忽略发布日期标签。

### 禁用发布日期过滤时的行为

- **Phase 1**：下载所有带字幕标签的条目，无论发布日期
- **Phase 2**：下载所有满足质量条件（评分和评论阈值）的条目，无论发布日期

适用场景：
- 用较早的内容回填你的收藏
- 抓取自定义 URL（演员/标签页面），此时发布日期不相关
- 下载所有符合质量条件的内容

---

## 相关子系统

历史记录系统会与三个独立子系统交互，它们各自有专门的文档：

- **代理支持** —— 池模式、模块化控制（`PROXY_MODULES`）、会话级封禁、CLI 覆盖（`--use-proxy` / `--no-proxy`）。详见 [Proxy 设置](../self-hoster/proxy-setup.md)。
- **CloudFlare 绕过** —— 通过 `CloudflareBypassForScraping` 自动回退、粘性绕过窗口（`--always-bypass-time`）、按代理动态解析服务 URL。详见 [CloudFlare 绕过](../self-hoster/cloudflare-bypass.md)。
- **JavDB 自动登录** —— 自定义 URL 抓取所需的会话 cookie 管理、验证码识别（手动 / GPT / OCR / 2Captcha）、何时重新登录。详见 [JavDB 登录](../self-hoster/javdb-login.md)。

历史记录系统本身**不依赖**这些子系统 —— 无论使用哪种代理模式、是否启用 CF 绕过，它的工作方式都相同。

---

## 已下载标记功能

重复下载防护功能会自动在每日报告中标记已下载的种子，并在 qBittorrent 上传器中跳过它们。

### 工作原理

1. **每日报告生成**：Spider 生成包含磁力链接的 CSV 报告
2. **历史记录检查**：上传器启动时检查历史数据库/CSV
3. **添加标记**：已下载种子的磁力链接前面会添加 `[DOWNLOADED]` 前缀
4. **跳过处理**：上传器跳过带有 `[DOWNLOADED]` 标记的种子
5. **上传新种子**：仅将不在历史记录中的种子上传到 qBittorrent
6. **更新历史记录**：当为已有电影找到新的种子类型时，修改 `update_date`

### CSV 格式

**标记处理前：**
```csv
href,video_code,hacked_subtitle,subtitle
/v/mOJnXY,IPZZ-574,magnet:?xt=...,magnet:?xt=...
```

**标记处理后：**
```csv
href,video_code,hacked_subtitle,subtitle
/v/mOJnXY,IPZZ-574,[DOWNLOADED] magnet:?xt=...,[DOWNLOADED] magnet:?xt=...
```

### 增强的历史记录格式

历史记录 CSV 使用增强格式，为每种种子类型提供单独的列：

```csv
href,phase,video_code,create_date,update_date,last_visited_datetime,hacked_subtitle,hacked_no_subtitle,subtitle,no_subtitle
/v/mOJnXY,1,IPZZ-574,2025-07-09 20:00:57,2025-07-09 20:05:30,2025-07-09 20:05:30,2025-07-09 20:05:30,,2025-07-09 20:05:30,
```

| 列 | 说明 |
|----|------|
| `href` | 电影详情页路径 |
| `phase` | 电影首次发现时所处的阶段（1 或 2） |
| `video_code` | 视频识别代码 |
| `create_date` | 电影首次发现和记录的时间 |
| `update_date` | 电影最后一次更新新种子类型的时间 |
| `last_visited_datetime` | 电影详情页最后一次被访问的时间 |
| `hacked_subtitle` | 破解版带字幕的下载日期（未下载则为空） |
| `hacked_no_subtitle` | 破解版无字幕的下载日期（未下载则为空） |
| `subtitle` | 字幕版本的下载日期（未下载则为空） |
| `no_subtitle` | 普通版本的下载日期（未下载则为空） |

**旧格式**（会自动迁移）：
```csv
href,phase,video_code,parsed_date,torrent_type
```

系统会自动处理从旧格式到新格式的迁移。现有文件会在保持向后兼容的前提下进行转换。

### 种子类型合并

更新现有记录时，新种子类型会与现有的合并：
- 仅填充新的（之前为空的）种子类型列
- 不会覆盖已有的下载日期
- 每当添加任何新种子类型时，`update_date` 会被刷新

### 重要说明

1. **历史文件依赖**：该功能依赖 `reports/parsed_movies_history.csv`（CSV）或 `reports/history.db`（SQLite）
2. **标记格式**：已下载标记为 `[DOWNLOADED] `（注意磁力链接前有一个尾随空格）
3. **向后兼容**：如果历史文件不存在，该功能会优雅降级，不影响正常运行
4. **性能**：历史记录检查使用高效的 CSV 读取 / SQLite 查询，不会显著影响性能
5. **时间戳跟踪**：`create_date` 保持不变；`update_date` 在每次修改时更新

---

## 重新下载（种子升级）模式

启用后，spider 会检查同类别种子是否显著大于之前下载的版本，如果是则触发重新下载。

### 配置

```python
# 在 config.py 中
ENABLE_REDOWNLOAD = True
REDOWNLOAD_SIZE_THRESHOLD = 0.30  # 大 30% 时触发重新下载
```

### CLI 标志

```bash
# 单次运行启用重新下载
python3 -m apps.cli.spider --enable-redownload

# 使用自定义阈值
python3 -m apps.cli.spider --enable-redownload --redownload-threshold 0.50
```

在 GitHub Actions 中，计划的每日运行默认启用重新下载，可通过 `enable_redownload` workflow dispatch 输入进行切换。
