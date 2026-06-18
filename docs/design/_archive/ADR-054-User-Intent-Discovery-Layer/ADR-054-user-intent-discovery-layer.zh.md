# ADR-054: User-Intent & Discovery Layer（用户意图与发现层）

| 字段 (Field) | 值 (Value)                                                            |
| ----------- | --------------------------------------------------------------------- |
| **状态 (Status)**  | Completed — umbrella；把工作路由到各阶段/子 ADR；自身不交付代码 |
| **日期 (Date)**    | 2026-06-13                                                            |
| **作者 (Authors)** | Ted                                                                   |
| **D1 写入类别 (D1 Write Class)** | n/a（umbrella 自身不交付代码；WS1/WS2 引入 **authoritative** 写入，在各自 IMP 中分类） |
| **关联 (Related)** | [ADR-033](../../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md), [ADR-034](../ADR-034-Media-Closed-Loop-Web-Surface/ADR-034-media-closed-loop-web-surface.md), [ADR-040](../../ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.md), [ADR-039](../../ADR-039-Pluggable-Integration-Platform/ADR-039-pluggable-integration-platform.md), [ADR-024](../../ADR-024-Torrent-Quality-Evidence/ADR-024-torrent-quality-evidence.md), [ADR-022](../ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md), [ADR-025](../../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md), [ADR-017](../ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md) |

> 起源于 2026-06-13 的一次 brainstorming 会话，对
> [Adsryen/JavdBviewed](https://github.com/Adsryen/JavdBviewed) 浏览器扩展
> （一套成熟的 JAVDB 增强套件，约 39 个 feature 模块）进行功能甄别，
> 评估哪些值得移植进我们的 Vue + Cloudflare web 应用。

## 背景 (Context)

[JavdBviewed](https://github.com/Adsryen/JavdBviewed) 是一个成熟的 JAVDB 伴侣扩展：
一套 Manifest-V3 内容脚本套件，增强 javdb.com 页面并自带 dashboard，在 IndexedDB
中存储 `VideoRecord` / `ActorRecord` / `ListRecord` 模型，含
`viewed | browsed | want | untracked` 状态、带定时新作监控的演员订阅、多源磁链聚合、
内容过滤、AI 翻译等。它展示了我们系统所缺的那一类面向用户的 JAVDB 功能。

我们的 media closed-loop（[ADR-033](../../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md)
后端、[ADR-034](../ADR-034-Media-Closed-Loop-Web-Surface/ADR-034-media-closed-loop-web-surface.md)
web 面）记录的是**系统做了什么**——`AcquisitionOutcome`（入队/下载了什么）、
`OwnershipLedger`（在 qB/NAS/GDrive/PikPak 各源持有什么）、`ConsumptionSignal`
（媒体服务器报告看过什么）。但**完全没有"操作者想要什么"的表示**——没有想看清单、
没有"我看过这个"、没有关注的演员。这条需求侧缺口正是扩展核心功能所填补的，
也是 closed-loop 供给侧的天然互补。

两条约束界定了本初衷：

1. **交付载体（已定）。** 我们**只把数据与逻辑移植进独立 SPA**——不做浏览器扩展、
   不注入 javdb.com DOM。扩展里最"原生"的那批功能（悬停预告/预览、封面替换、
   详情页按钮注入、键盘快捷键、截图打码、评论/付费墙解锁、锚点导航、通用密码自动填充）
   只有在叠加 javdb.com 本身时才成立，因此**不在范围内**（记录于 Non-Goals；
   作为未来"伴侣扩展"或"应用内代理浏览增强"的 backlog）。

2. **不重复既有设计。** 对我们自己 `docs/design/` 的甄别发现，若干候选功能已有归属：
   [ADR-040](../../ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.md)
   拥有内容过滤**以及**一个（定义不同的）延期 "Subscription"；
   [ADR-039](../../ADR-039-Pluggable-Integration-Platform/ADR-039-pluggable-integration-platform.md)
   是翻译/可用性后端应当注册其下的插件平台；
   [ADR-024](../../ADR-024-Torrent-Quality-Evidence/ADR-024-torrent-quality-evidence.md)
   拥有单条磁链的画质/字幕评分；
   [ADR-022](../ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md)
   建立了用户自填的 `MovieRatings`/`ContentPreferences` D1 模式。既有的 web 路线图
   （[ADR-028](../ADR-028-Web-Platform-Completeness-Roadmap/ADR-028-web-platform-completeness-roadmap.md)
   ——一份封闭的 2026-05-29 快照审计，其子 ADR 已归档）和 ADR-034
   （已完整交付，是 ADR-033 的 1:1 只读镜像）**都不是**可以续加阶段的活动容器。
   因此**新建一个 umbrella** 才是正确载体，但它必须**委派与扩展**，而非重造。

## 决策 (Decision)

确立 **ADR-054 作为 umbrella**，统辖用户意图与发现初衷。它**自有**真正净新增的部分——
用户意图 watchlist、统一的 subscription + 新作 feed、跨源磁链聚合——并把其余部分
**委派**给既有归属（内容过滤给 ADR-040；AI 翻译与可用性检测给 ADR-039 插件类目），
**复用** ADR-024 的画质评分与 ADR-034 的 web-surface 模式。每个工作流通过各自的
ADR/spec → IMP 设计与交付；本路线图自身不交付代码。

### 设计决策 (Design Decisions)

**D1. 范围：把数据/逻辑移植进 SPA；不做扩展、不注入页面。** 我们承接的价值是服务端支撑的：
D1 表 + 双后端端点 + Vue 视图，而非 chrome.storage + 内容脚本。扩展的 DOM 注入类功能
（见 Non-Goals）本轮明确排除。这让初衷留在既有架构内（ADR-008 SPA、ADR-017 双后端），
而不另开第二条交付通道。

**D2. ADR-054 是新 umbrella，而非 ADR-028/034 的延伸。** ADR-028 是一份封闭的快照审计，
自身不交付代码、治理一个固定的 2026-05-29 backlog；ADR-034 已完整实现，是 ADR-033 只读表的
严格 1:1 镜像。两者都没有给一个净新增的内容智能域留下开放阶段槽位。ADR-054 沿用
ADR-033/ADR-039 的 umbrella 模式（一份 "Accepted — umbrella" 记录，把工作路由到各阶段/子 ADR）。

**D3. WS1 — 用户意图 Watchlist。** 一张净新增的 **authoritative** D1 表
（`WatchIntent`，落 `HISTORY_DB`，与 ADR-022 的 `MovieRatings` / `ContentPreferences` **并列**，
复用其 D1-first、用户自填的模式——而非塞进 rating/heart 模型，那是 ADR-022 明确排除的）。它是
ADR-033 媒体推导的 `ConsumptionSignal` 的**手动意图互补**（引用 ADR-033 D11："Scope is the
signal, not the model"）；WS1 绝不从 `ConsumptionSignal` 派生状态、也不回写——二者保持独立，对账延期。
web 面沿用 ADR-034 Library 模式（capability 门控、双后端、en/zh）。

_2026-06-13 定（brainstorming + 跨 repo 精读）：_ 状态枚举为 **`want | viewed`**——`browsed`
**丢弃**（在源扩展里它仅作内容脚本访问页面的默认值，而我们无注入的 SPA 产生不了这个事件；
把它重定义为自动"surfaced/opened"层的方案延期）。`untracked` 用**无行**表示（显式取消追踪即
`DELETE`）；无 `user_id`（单操作者）；无编辑锁（除操作者外无人写此表）。主键为 `video_code`
（与 closed-loop 各表对齐），并带 `href` 桥接列，使内联 UI 能按 `href` 写入/join。capability flag
为 **`watch_intent`**。两个呈现面：既有影片列表上的内联 `StatusControl` 设置点（SETTER），与
Library **Watchlist** tab（`want`/`viewed` 项的聚合 VIEW）。**读写在同一个 IMP 交付**，并配一个
跨后端 upsert 并行测试（Query Golden 不覆盖 upsert）。完整执行细节见
[IMP-ADR054-01](IMP-ADR054-01-watchlist.md)。

**D4. WS2 — Subscriptions，统一（supersede ADR-040 Phase-3）。** 一个 **Subscription** 概念：
关注一个实体（先演员；后续 tag/series），使其新作 (a) 出现在 **New-Works Feed** 中，且
(b) 在入队时**绕过 ADR-040 的评分阈值**。这**吸收并 supersede ADR-040 延期的 Phase-3
"Subscription"**（其原义仅为评分阈值绕过白名单），从而使代码库中"subscription"只有一个含义。
新作通过**定时抓取**（Cron / GitHub Actions）发现，复用既有 AdHoc 抓取路径，每条都可一键标记
**want**（回流 WS1）。Subscription 存储（关注实体 + last-seen 游标）是净新增的 authoritative 表。
WS2 设计时会交叉引用/更新 ADR-040，将其 Phase-3 行指向此处。

**D5. WS3 — 多源磁链聚合。** 在多个源（Sukebei、BTdig、BTSOW、JAVBUS）**抓取并去重磁链**
——净新增——把每个源注册为**新 ADR-039 插件类目**（暂名 `magnet-source` / `indexer`）下的后端，
而非自造聚合器。单条磁链的**画质/字幕评分复用 ADR-024 的证据层**（字幕文件证据、分辨率/类目一致性、
reason codes），而非重新解析。Cloudflare 质询在**服务端**处理（当我们掌控抓取路径时，扩展的隐藏
标签页抓取技巧并无必要）。在既有 Browse / 详情流程中呈现。

**D6. WS4 — 叠加项，委派（不归 ADR-054 自有）。**
- **WS4a 内容过滤**（关键词 / 正则 / 发布日期）→ **扩展 ADR-040**：在既有
  `ContentFilterRule` 引擎上增加 `regex` 与 `release-date` 维度/模式，以及其规划中的 Phase-4
  web 规则 CRUD；SPA 内的展示侧叠加是对同一套规则的**读侧复用**。**不要做平行过滤系统。** 归 ADR-040。
- **WS4b AI 标题翻译** → 一个**新 ADR-039 插件类目**（暂名 `enrichment` / `translation`），
  遵循 ADR-026 的结构化 LLM 调用范式（provider 在契约之后、限流）。净新增功能、既有接缝。
- **WS4c 在线可用性检测** → 一个**新 ADR-039 插件类目**（暂名 `availability`），带 TTL 缓存地
  探测流媒体源。净新增功能、既有接缝。

**D7. 双后端、跨 repo、按构造保持 capability 诚实。** 每个新增读/写面都遵守 ADR-017/018/030
的 parity 规则：在 TS Worker（`server/`，web repo）与 Python 后端（`apps/api/`，本 monorepo）
**两侧都实现**，以 `openapi.json` 为共享契约；以 capability flag 门控（ADR-034 D4 模式，flag
`watch_intent`（WS1） / `subscriptions`（WS2）），让没有相应表的部署永不显示坏页面；所有新字符串保持 en/zh i18n
对齐。跨 repo 划分明确——Vue + TS Worker 落在 `javdb-autospider-web`；D1 迁移、Python router、
以及 WS2 的 Cron/GH-Actions 抓取落在本 monorepo——每个 WS 的 IMP 都列明哪个文件落在哪个 repo。

**D8. 分期且排序；本路线图不交付代码。** 推荐顺序：**WS1 → WS2 → WS3 → WS4**。WS1 是地基
（"want" 状态正是 WS2 新作的落点）；WS2 依赖 WS1；WS3 独立、可并行；WS4 委派且增量。每个工作流走
各自的 `brainstorming`（spec/ADR）→ `writing-plans`（IMP）流程，基于届时真实的表/端点形状——
不在此处预先锁定。与 ADR-028 一样，本 umbrella 是排序记录，而非实现。

**D9. "复用而非重复"账本。** 统辖整个初衷的甄别映射：

| 移植功能 | 关系 | 归属 / 载体 |
| --- | --- | --- |
| Watchlist（`want/viewed/browsed`） | **净新增**状态存储；复用 ADR-022 D1 模式；互补 ADR-033 `ConsumptionSignal` | **ADR-054 WS1** |
| 演员订阅 + 新作 | **净新增**；**统一并 supersede** ADR-040 Phase-3 "Subscription" | **ADR-054 WS2** |
| 多源磁链聚合 | 跨源抓取+去重**净新增**；画质/字幕**扩展 ADR-024**；各源为 **ADR-039** 插件 | **ADR-054 WS3**（+ ADR-039、ADR-024） |
| 内容过滤（关键词/正则/日期） | **扩展**既有 `ContentFilterRule`（+ regex/date 维度） | **ADR-040**（交叉引用） |
| AI 标题翻译 | **净新增**功能；新 **ADR-039** 插件类目 | **ADR-039**（交叉引用） |
| 在线可用性检测 | **净新增**功能；新 **ADR-039** 插件类目 | **ADR-039**（交叉引用） |

## 后果 (Consequences)

### 正面 (Positive)

- **从需求侧闭合环路**——操作者终于能表达 want / seen 并关注演员，互补 ADR-033/034 的供给侧记录。
- **无重复设计**——每个功能恰好落在一个归属；账本（D9）杜绝两套过滤、两种 "subscription" 含义、
  或重造的画质评分器。
- **域语言里只有一个 "subscription"**——D4 在交付前消解了 ADR-040 撞名，而非事后补救。
- **壮大既有平台**——WS3/WS4b/WS4c 让 ADR-039 成为真正的生态；WS4a 深化 ADR-040；
  WS1 扩展 ADR-022 的用户数据模式。
- **架构诚实**——按构造双后端 + capability 门控 + i18n（D7）；不另开第二交付通道（D1）。

### 负面 (Negative)

- **每个工作流的跨 repo、双后端成本**——每个 WS 都付 ADR-018/030 的"同一段 SQL 两份实现"税，
  且横跨两 repo（D7）；在共享查询接缝出现前，这是已接受的现状。
- **协调债**——WS2 须修订 ADR-040（supersede Phase-3），WS3/WS4b/4c 须新增 ADR-039 类目；
  这些跨 ADR 编辑被追踪，而非免费。
- **把最受喜爱的扩展功能放进 backlog**——DOM 注入类 UX（预览、页面内角标）被延期（D1）；
  部分用户会怀念"在 javdb 页面上就能看到"。
- **范围跨度**——六个工作流、四个 ADR；唯有逐个推进、而非一拥而上，才能交付。

## 实施路线图 (Implementation Roadmap)

| 阶段 | 归属 | 子 ADR / IMP | 交付内容 | 推迟内容 |
| --- | --- | --- | --- | --- |
| WS1 — Watchlist | ADR-054 | [IMP-ADR054-01](IMP-ADR054-01-watchlist.md) | `WatchIntent` D1 表（`want/viewed`，`video_code`+`href`）；内联 `StatusControl` 设置点 + Library Watchlist tab；双后端 `/api/watchlist` 读+写 + upsert 并行测试；`watch_intent` capability flag；en/zh | `browsed` 自动信号；与 `ConsumptionSignal` 对账；批量操作 |
| WS2 — Subscriptions + New-Works | ADR-054 | [IMP-ADR054-02](IMP-ADR054-02-subscriptions.md) | `ActorSubscription` + `NewWorks`（HISTORY_DB，仅演员）；`SubscriptionMonitor.yml` GH-cron 经 AdHoc 路径抓关注演员（评分阈值**天然绕过**——无新代码）；New-Works feed 复用 WS1 `StatusControl`（一键 → want）；`subscriptions` flag；以修订 supersede ADR-040 P3 | tag/series；通知；Worker-cron |
| WS3 — Magnet Aggregation | ADR-054（+ ADR-039、ADR-024） | [IMP-ADR054-03](IMP-ADR054-03-magnet-aggregation.md) | ADR-039 `indexer` 类目（JAVBUS + Sukebei）；服务端抓取 + infohash 去重；**live** 复用 ADR-024 评分（文件信号 → `probe_unavailable`）；`POST /api/explore/aggregate-magnets`（Worker 501）；`magnet_aggregation` flag（配置非空）；**v1 ephemeral** | 缓存表；BTdig/BTSOW；负缓存/退避 |
| WS4a — 内容过滤 | **ADR-040** | [IMP-ADR040-03](../../ADR-040-Content-Filter-Rules/IMP-ADR040-03-content-filter-regex-date.md)（引擎） + [IMP-ADR040-04](../../ADR-040-Content-Filter-Rules/IMP-ADR040-04-content-filter-web-crud.md)（web CRUD） | 在既有 `ContentFilterRule` 三元组上加 `regex_exclude/include` + `release_date before/after`（**无迁移**）；双后端 `/api/content-filter` CRUD + Settings 页 + 读侧 Browse 叠加（**REPORTS_DB**）；`content_filter` flag | — |
| WS4b — AI 翻译 | **ADR-039** | backlog（2026-06-14 延期） | `translation` 插件类目；仓库首个真实 OpenAI 兼容 LLM 客户端；按标题懒加载、memoize | 整个子项（本轮不做） |
| WS4c — 可用性检测 | **ADR-039** | backlog（2026-06-14 延期） | `availability` 插件类目；TTL 缓存表；每源隔离 | 整个子项（服务端探测流媒体有封禁/合规风险） |

每个阶段在决策后的 `brainstorming` + `writing-plans` 流程中，基于届时真实形状详化（节奏参照 ADR-034）。

### 明确的非目标 (YAGNI)

- **不做浏览器扩展、不注入 javdb.com DOM**——扩展的页面内功能（悬停预告/预览、封面替换、
  详情页按钮注入、键盘快捷键、截图打码隐私模式、评论/付费墙解锁、锚点/super-ranking 导航、
  通用密码自动填充）不在范围内。Backlog：未来的伴侣扩展或应用内代理浏览增强。
- **不移植 WebDAV / 云同步**——我们的 D1 后端**就是**同步层（ADR-017）；扩展的 WebDAV diff/merge
  被替代而非移植（其 `manuallyEditedFields` 编辑锁思路记录在案，供 WS1 写路径参考，但不整体照搬）。
- **本轮不做 115 网盘集成**——qB/PikPak 已覆盖下载；115 进 backlog（若日后需要，可作为 ADR-039
  downloader/destination 插件接入）。
- **不引入第二个 "subscription" 概念**——D4 已统一。
- **不引入新图表/UI 库**——复用 SPA 中已有的 Naive UI + vue-chartjs。
- **本 umbrella 不交付代码**——它做排序；每个 WS 经各自 IMP 交付。

## 域语言 (Domain Language)（补入 CONTEXT.md）

- **Watch Intent** — 操作者手动设定的影片状态（`want | viewed`；无行即 untracked）。
  与 **Consumption Signal**（ADR-033，媒体服务器推导）不同：intent 是操作者声明的，
  signal 是媒体服务器观测到的。（源扩展的 `browsed` 延期——它无注入即无触发事件；见 D3。）
- **Watchlist** — Watch Intent 为 `want` 的影片集合。
- **Subscription** — 一个关注的实体（演员；后续 tag/series），其新作 (a) 出现在 **New-Works Feed**，
  且 (b) 绕过 ADR-040 入队评分阈值。**统一并 supersede** ADR-040 延期的 Phase-3 "Subscription"
  （原义仅为评分阈值绕过白名单）。
- **New-Works Feed** — 由定时抓取产出的、来自 Subscriptions 的新发现释出流。
- **Magnet source (plugin)** — 一个 ADR-039 插件类目后端，从单个外部 indexer（如 Sukebei、BTdig、
  BTSOW、JAVBUS）抓取磁链；聚合器跨活跃源去重。

## 备选方案 (Alternatives Considered)

- **在本 repo 内做伴侣浏览器扩展** — 本轮否决（D1）：另开第二交付通道、并拉回我们选择延期的
  DOM 注入范围；记为 backlog。
- **增强应用内代理 javdb 浏览以注入叠加** — 本轮否决（D1）：受限于代理能改写的范围；backlog。
- **把这些功能作为 ADR-028 或 ADR-034 的阶段** — 否决（D2）：ADR-028 是封闭快照审计、ADR-034
  是已交付的 1:1 镜像；两者都非活动容器。
- **保留两种 "subscription" 含义**（ADR-040 白名单 + 新的"关注"）— 否决（D4）：域语言缺陷；改为统一。
- **从零重造过滤与画质评分** — 否决（D6）：扩展 ADR-040、复用 ADR-024；造平行系统正是本 umbrella
  要杜绝的重复。
- **把六个功能塞进一份大 spec** — 否决（D8）：过大；拆成逐个推进的 ADR/spec → IMP 流程。

## 参考 (References)

- [ADR-033 — Media Closed-Loop](../../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md)
- [ADR-034 — Media Closed-Loop Web Surface](../ADR-034-Media-Closed-Loop-Web-Surface/ADR-034-media-closed-loop-web-surface.md)
- [ADR-040 — Content Filter Rules](../../ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.md)
- [ADR-039 — Pluggable Integration Platform](../../ADR-039-Pluggable-Integration-Platform/ADR-039-pluggable-integration-platform.md)
- [ADR-024 — Torrent Quality Evidence Foundation](../../ADR-024-Torrent-Quality-Evidence/ADR-024-torrent-quality-evidence.md)
- [ADR-022 — User Preference Foundation](../ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md)
- [ADR-025 — User Preference Model](../../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md)
- [ADR-026 — AI Operations Diagnosis](../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md)
- [ADR-028 — Web Platform Completeness Roadmap](../ADR-028-Web-Platform-Completeness-Roadmap/ADR-028-web-platform-completeness-roadmap.md)
- [ADR-017 — Cloudflare-First Deployment](../ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md)
- [Adsryen/JavdBviewed](https://github.com/Adsryen/JavdBviewed) — 本初衷甄别的浏览器扩展

## 状态日志 (Status Log)

- 2026-06-13: Proposed（umbrella；六个工作流横跨四个 ADR；记录 WS1→WS4 排序；尚无 IMP）。
  待办协调项：WS2 须修订 ADR-040 以 supersede 其 Phase-3 "Subscription"；WS3/WS4b/WS4c
  须新增 ADR-039 插件类目。
- 2026-06-13: WS1 设计已定（brainstorming + 跨 repo 精读）。枚举锁定 `want | viewed`
  （`browsed` 丢弃/延期——无注入即无触发）；`WatchIntent` 落 `HISTORY_DB`，以 `video_code`
  为键（+`href` 桥接），`untracked` = 无行，无 `user_id`、无编辑锁；capability flag
  `watch_intent`；内联 `StatusControl` 设置点 + Library Watchlist tab；读写在同一个 IMP 交付，
  配跨后端 upsert 并行测试。详见 [IMP-ADR054-01](IMP-ADR054-01-watchlist.md)。
- 2026-06-14: WS2/WS3/WS4a 设计已定（brainstorming + 跨 repo 精读），IMP 已写出
  ——**本轮不实现**。WS2 → [IMP-ADR054-02](IMP-ADR054-02-subscriptions.md)（仅演员；
  GH-cron `SubscriptionMonitor.yml` 复用 AdHoc 路径；`ActorSubscription`+`NewWorks`
  落 `HISTORY_DB`；`subscriptions` flag）。WS3 →
  [IMP-ADR054-03](IMP-ADR054-03-magnet-aggregation.md)（JAVBUS+Sukebei `indexer` 插件；
  ephemeral；`POST /api/explore/aggregate-magnets` + Worker 501；`magnet_aggregation`
  配置非空 flag）。WS4a →
  [IMP-ADR040-03](../../ADR-040-Content-Filter-Rules/IMP-ADR040-03-content-filter-regex-date.md)
  +
  [IMP-ADR040-04](../../ADR-040-Content-Filter-Rules/IMP-ADR040-04-content-filter-web-crud.md)
  （扩展 `REPORTS_DB` 里的 `ContentFilterRule`，无迁移）。**WS4b（AI 翻译）+ WS4c
  （可用性）本轮延期进 backlog**。关键发现：WS2 的评分阈值绕过在 AdHoc 路径上本就免费
  （无新代码；ADR-040 P3 在 WS2 实现时以修订 supersede）。
- 2026-06-15：WS2 backend/server 切片已通过
  [IMP-ADR054-02](IMP-ADR054-02-subscriptions.md) 实现：`ActorSubscription` +
  `NewWorks` D1 表落 `HISTORY_DB`；Python `/api/subscriptions` 与
  `/api/new-works`；TS Worker server mirror；两后端均有 `subscriptions`
  capability flag；`SubscriptionMonitor.yml` GitHub cron + CLI/pipeline；
  OpenAPI 合约已更新；新增跨后端 UPSERT parity 测试。ADR-040 Phase-3
  "Subscriptions" 已通过修订 supersede。Vue/UI 工作留给前端 Sprint 3 切片。
