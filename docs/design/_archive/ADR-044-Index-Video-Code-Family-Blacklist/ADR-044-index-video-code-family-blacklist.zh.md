# ADR-044：Index 视频码家族黑名单

| 字段 | 值 |
| --- | --- |
| **状态 (Status)** | Completed — 已于 2026-06-01 实现并验证 |
| **日期 (Date)** | 2026-06-01 |
| **作者 (Authors)** | Ted |
| **关联 (Related)** | [ADR-035](../../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.zh.md), [ADR-040](../../ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.zh.md), [ADR-042](../../ADR-042-D1-Atomic-Commit-Boundaries/ADR-042-d1-atomic-commit-boundaries.zh.md) |
| **关联实现计划 (Related Implementation Plans)** | [IMP-ADR044-01](IMP-ADR044-01-index-video-code-family-blacklist.md) |

> 这份 ADR 来自一次 drift 排查：parser 必须识别新的 index-card 家族，让 sentinel 看到非空的 `video_code`；但 daily ingestion 仍然要默认把这个家族排除在下载队列之外。

## 背景 (Context)

当前 parser 已经能区分这些页面类型：`index`、`detail`、`actors`、`makers`、`publishers`、`series`、`directors`、`video_codes`、`search`、`tags`、`top250`、`top_movies`、`top_playback` 和 `unknown`。这份 ADR 的范围比整个 parser surface 更窄：它只是给 index-card 的 `video_code` token 加一个分类标签，并改变 daily ingestion 如何处理一个新识别出来的家族。

在失败场景里（run [26716551239](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/26716551239)），400 张 daily 样本卡片里有 9 张的首个 token 是 western studio/date 形式（例如 `Wifey.2026.05.30`、`RKPrime.26.05.28`）。parser 的 `_is_plausible_video_code` 守卫会拒绝任何含 `[A-Za-z0-9_-]` 之外字符的 token——点号让这些 token 不通过，于是 `video_code` 被留空。这使 `index.video_code` 的 fill 率下降到足以触发 critical contract sentinel（ADR-035），尽管页面内容本身并没有缺失数据。与此同时，这类卡片本来就不属于 daily ingestion 的目标范围，所以如果只是单纯放宽 parser 而不加过滤，就会把它们放进下载队列。

这本质上是一个边界问题，不只是解析 bug：

1. parser 应该识别这个家族，让下游能显式处理——但**不能因此收紧已经被接受为合法 `video_code` 的 token 集合**。
2. daily ingestion 应该默认排除这个家族。
3. ad hoc ingestion 应该保持当前行为，不继承 daily 黑名单。

ADR-040 已经引入了 detail-page 级别的 content filter。那一层不适合解决这个问题，因为它发生在 detail parse 之后，而且处理的是 actors/tags/gender，不是 index-card 的 video-code 家族。

## 决策 (Decision)

为 index 页的 movie entry 添加显式的、**加法式**的 `video_code_family` 分类，并用 daily-only、**纯 config 驱动**的家族黑名单把指定 family 从 ingestion 中排除。

### 设计决策 (Design Decisions)

**D1. 用加法式分类器对 index 卡片的 video code 分类。**

`MovieIndexEntry` 增加稳定的 `video_code_family` 字段。新增 `classify_video_code_family()`，返回某个已识别的家族标签，或在 token 不匹配任何家族时返回空字符串。

已识别的家族如下：

- `classic_hyphenated` — 例如 `ABC-123`
- `multi_hyphen` — 例如 `FC2-PPV-1234567`
- `numeric_date_hyphen` — 例如 `062216-179`
- `numeric_date_underscore` — 例如 `062216_001`
- `hyphenless_studio` — 例如 `n0656`
- `western_studio_date` — 例如 `Wifey.2026.05.30`、`RKPrime.26.05.28`（同时覆盖 `Studio.YYYY.MM.DD` 和 `Studio.YY.MM.DD`）

family 标签**只是分类元数据**。这 6 个家族里，只有 `western_studio_date` 会被消费（D3 的 daily 黑名单）；其余的存在，是为了让这个字段成为一个完整、诚实的 taxonomy，也方便将来的过滤器引用。不匹配任何家族的 token（例如数字前缀厂牌码 `259LUXU-1234`）会得到空的 `video_code_family`，但**仍然是完全合法的 `video_code`**（见 D2）。

**D2. 识别是加法式的——绝不收紧 `video_code` 的 plausibility。**

这是整份 ADR 的承重约束。现有的 `_is_plausible_video_code` 启发式（Python 与 Rust）接受很宽的紧凑 token 空间：ASCII 字母数字加 `-`/`_` 分隔符、至少一个数字、并且要么有字母要么有分隔符。这个空间包含很多这 6 个家族**匹配不到**的真实 JavDB 番号——数字前缀厂牌码（`259LUXU-1234`、`300MIUM-0571`、`200GANA-…`）、Heydouga 形式（`H4610-ki220101`）、下划线混合码（`1pondo-010120_001`）。

因此 parser 改动必须严格加法式：

- 现有宽松的 plausibility 检查**原样保留**。
- **仅**额外放行点分隔的 western token（它们当前因为 `.` 不在允许字符集里而失败）。
- `classify_video_code_family()` 是一个**独立、只读的打标器**。绝不能用它来判断一个 token 是否是合法 `video_code`。把 plausibility 绑到 family 枚举上会拒绝上面那些大类，造成比最初 9 张卡片严重得多的 drift——以及静默丢号。

Python 和 Rust 必须做同样的加法式改动，让两套引擎保持一致。

**D3. 家族黑名单是 daily-only、config 驱动的，并作为一个独立的前置过滤步骤。**

黑名单只作用于 `DailyIngestion`，`AdHocIngestion` 会绕过它（它本来就向 selection 传 `is_adhoc_mode=True`）。

daily 流水线顺序是：

```text
parse index cards -> sentinel 统计 -> daily 家族黑名单（独立步骤） -> phase 1 / phase 2 选择
```

顺序很重要：sentinel 必须看到 family 作为已填充的 `video_code`（它在任何过滤之前统计原始解析卡片），而 daily ingestion 又必须在它进入下载候选之前把它排掉。黑名单是一个**对已解析卡片的独立过滤 pass**，在 sentinel 统计之后、phase-1/phase-2 选择调用之前，每页跑一次——不塞进 `select_index_entries`（它每页跑两次，否则会把排除数双重计数，并把 family 逻辑和 phase 逻辑搅在一起）。

黑名单来源**只有静态 config**：

```python
DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST = ["western_studio_date"]
```

在 GitHub Actions 里，它由仓库 Variable `DAILY_INDEX_VIDEO_CODE_FAMILY_BLACKLIST_JSON`（JSON 数组）渲染。运维要取消默认的 western 排除，就把 config 列表清空（或把 Variable 设为 `[]`）。**没有 D1 控制面**（见备选方案）：video-code 家族是一个**由 parser 定义的封闭枚举**——你无法拉黑一个 parser 不产出的 family，而新增 family 本身就需要改代码，所以顺手在同一次改动里调整黑名单是最自然的地方。

**D4. 不把 family 字段写入 CSV、report、history row。**

`video_code_family` 属于 parser contract 和 daily selection/debug surface，但不进入持久化下载报表。daily CSV 行、`ReportMovies`、`MovieHistory`、`TorrentHistory` 继续只使用现有 `video_code` 合约。它会出现在 `MovieIndexEntry.to_dict()`（debug/API），但**不**出现在 `to_legacy_dict()`。

**D5. 统计只做 family 级聚合，且每页只计一次。**

daily run 输出 family 级排除统计——一个总数加上按 family 的分布，例如 `western_studio_date=9`。因为黑名单作为单次前置过滤 pass 运行（D3），每张被排除的卡片恰好计一次。这足以解释「为什么候选没进来」，又不会把日志刷成逐条明细。

## 术语 (Domain Language)

- **Video code family** — 对 index 卡片 `video_code` token 的 parser 分类标签。只是分类元数据，绝不决定 `video_code` 是否合法。
- **Daily index family blacklist** — 只影响 daily ingestion 的家族排除集合，来源于静态 config。
- **Western studio/date family** — 这次新增并默认排除的 western 风格 token 家族（`Studio.YYYY.MM.DD` / `Studio.YY.MM.DD`），family 标签为 `western_studio_date`。

## 后果 (Consequences)

### 正面 (Positive)

- **sentinel 会如实反映填充率** — 已识别的 western-family 卡片会计入非空 `video_code`。
- **无回归** — 识别是加法式的，所以今天能解析的 token 仍然能解析；只是新增接受点分隔的 western token。
- **daily ingestion 范围保持正确** — 新家族被识别，但默认仍会被排除。
- **契约更显式** — 下游可以直接看 `video_code_family`，不用猜字符串形状。
- **ad hoc 行为不变** — 黑名单不会泄漏到自定义抓取里。
- **改动面最小** — 一个 config key、一个 parser 标签、一个前置过滤 pass；不新增表、repo、CLI 或 schema migration。

### 负面 (Negative)

- **parser/model surface 增长** — `MovieIndexEntry` 及其 debug/API 序列化多一个字段；分类器带 6 条正则（其中 5 条目前是未使用的标签）。
- **纯 config 意味着「改它要发版」** — 将来往黑名单里加 family 需要改 config / GitHub Variable，而不是运行时命令。这是可接受的，因为新增 family 本来就需要改 parser 代码。

## 备选方案 (Alternatives Considered)

- **保持 family 隐式，同时降低 sentinel 阈值** — 拒绝。这样只是掩盖 parser/contract 不一致，并没有修复它。
- **把 `video_code` 的 plausibility 绑到 family 枚举上**（只接受这 6 个家族）— 拒绝，且被 D2 明确禁止。它会拒绝常见真实番号（`259LUXU-1234`、`H4610-…`、`1pondo-…_…`），让 fill 率崩溃并静默丢弃合法下载。
- **复用 ADR-040 的 `ContentFilterRule`** — 拒绝。那一层在 detail parse 之后，处理的是另一种开放式数据形态（actors/tags/gender），那里运行时可变性确实有回报。
- **新增 D1 `IndexFilterRule` 控制面（表 + repo + ops CLI + schema bump）** — Phase 1 拒绝。video-code 家族是 parser 定义的封闭枚举，所以运行时可变规则带来的是一条令人困惑的 config∪D1 合并语义，以及三库 schema-version bump，换来的收益几乎为零。只有在出现具体的运行时可变排除需求时再重新考虑。
- **让 ad hoc ingestion 继承 daily 黑名单** — 拒绝。用户明确要求 ad hoc 继续保持更宽的 parser 接纳面，而不是一起套上 daily 排除策略。

## 实施路线图 (Implementation Roadmap)

| 阶段 | IMP | 交付内容 | 推迟内容 |
| --- | --- | --- | --- |
| Phase 1 | [IMP-ADR044-01](IMP-ADR044-01-index-video-code-family-blacklist.md) | 加法式 parser 家族分类（Python + Rust）；`video_code_family` 字段；为 western token 放宽 plausibility；config 驱动的 daily-only 家族黑名单作为前置过滤步骤；config/workflow wiring；family 级统计 | D1 运行时控制面；family allowlist 语义；更宽的 parser taxonomy；持久化 family 字段 |

## 参考 (References)

- [ADR-035 — Site Contract Sentinel](../../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.zh.md)
- [ADR-040 — Content Filter Rules](../../ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.zh.md)
- [ADR-042 — D1 Atomic Commit Boundaries](../../ADR-042-D1-Atomic-Commit-Boundaries/ADR-042-d1-atomic-commit-boundaries.zh.md)
- [`javdb/parsing/common.py`](../../../../javdb/parsing/common.py)
- [`javdb/rust_core/src/scraper/common.rs`](../../../../javdb/rust_core/src/scraper/common.rs)
- [`javdb/pipeline/index_selection.py`](../../../../javdb/pipeline/index_selection.py)
- [`javdb/spider/fetch/index.py`](../../../../javdb/spider/fetch/index.py)
- [`javdb/spider/fetch/index_parallel.py`](../../../../javdb/spider/fetch/index_parallel.py)
- [`javdb/ops/sentinel/field_health.py`](../../../../javdb/ops/sentinel/field_health.py)
- [`javdb/infra/config_generator.py`](../../../../javdb/infra/config_generator.py)
- [`.github/workflows/DailyIngestion.yml`](../../../../.github/workflows/DailyIngestion.yml)
- [`.github/workflows/AdHocIngestion.yml`](../../../../.github/workflows/AdHocIngestion.yml)

## 状态日志 (Status Log)

- 2026-06-01: Accepted（初版）— 确认 parser 识别与 daily-only 黑名单边界。
- 2026-06-01: 设计评审（grill）在实现前修订了三项决策：
  - **D1/D2** — 识别改为严格加法式；分类与 `video_code` plausibility 解耦（原方案会拒绝 `259LUXU-1234` 这类常见真实番号）。
  - **D3** — 删掉 D1 `IndexFilterRule` 控制面（表 + repo + ops CLI + 三库 schema bump）；黑名单改为纯 config，并作为独立的前置过滤步骤，而不是塞进 `select_index_entries`。
  - **D5** — 排除统计每页只计一次（前置过滤步骤的自然结果）。
