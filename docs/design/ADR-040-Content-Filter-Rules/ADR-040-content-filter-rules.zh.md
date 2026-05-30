# ADR-040：内容过滤规则

| 字段       | 值                                                                    |
| ---------- | --------------------------------------------------------------------- |
| **状态**   | Proposed — 伞型;执行下放给各期 IMP                                    |
| **日期**   | 2026-05-29                                                            |
| **作者**   | Ted                                                                   |
| **关联**   | [ADR-022](../ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md), [ADR-025](../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md), [ADR-036](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md), [ADR-038](../ADR-038-Agentic-Operator-MCP/ADR-038-agentic-operator-mcp-surface.md) |

> 源自 2026-05-29 一次头脑风暴:起初是"流式 / 持续摄取"(方向七),后**纠偏**为内容过滤——见背景。

## 背景 (Context)

每日摄取（`DailyIngestion.yml`,cron `00 12 * * *`）每天爬一次首页新作,按**评分与打分人数**（数量/热度信号）选择条目。本次头脑风暴起初是"流式/持续摄取"——更频繁轮询求更新鲜。

**这个出发点对本系统是错的,设计随之纠偏。** 影片发行很慢:一次 daily run 产出 <50 部。频繁轮询毫无收益;现有 daily 节奏（甚至更稀）已足够。运维者陈述的真实缺口是**过滤能力**:当前的 评分/打分人数 过滤无法按**身份或属性**排除或包含——没法黑名单指定演员或 tag,也没法按 主演/全部演员 性别过滤。（年龄过滤也被提到,但演员年龄**不在影片详情页上**——要查演员主页——故推迟。）

因此本 ADR 用一个**内容过滤规则层**增强 daily 摄取:确定性的身份/属性 include/exclude 规则,作为额外一道闸应用。无流式,无新频繁 cron。

## 决策 (Decision)

加一个 D1 背书的 `ContentFilterRule` 层与一个确定性过滤阶段,在**详情解析后、入 qBittorrent 队列前**运行,与现有 评分/打分人数 过滤 **AND**。Phase 1 覆盖现有详情解析可得的维度——演员黑名单、tag include/exclude、性别——采用黑名单最高优先级。年龄与订阅推迟。

### 设计决策 (Design Decisions)

**D1. 记录纠偏:内容过滤,非流式。** 频繁轮询被明确否决——慢发行速度使 daily 节奏足够。价值在过滤,不在新鲜度。（"流式"出发点退役;本 ADR 取而代之。）

**D2. 一张动态的 `ContentFilterRule` D1 表。** 规则放 D1,以便运行时管理（日后经 web/MCP），而非硬编码在 `config.py`:

```sql
CREATE TABLE ContentFilterRule (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  dimension  TEXT NOT NULL,   -- actor | tag | gender
  mode       TEXT NOT NULL,   -- exclude | include | require_lead | exclude_all_male ...
  value      TEXT,            -- actor name/href | tag | gender value
  enabled    INTEGER NOT NULL DEFAULT 1,
  created_at TEXT
);
```

**D3. 详情解析后、入 qB 队列前的新过滤阶段。** 身份与属性数据（演员、性别、tags）只有在详情页解析**之后**才可得,所以内容过滤在那里运行——位于现有索引阶段 评分/打分人数 过滤的下游,后者不变。影片须**两关都过**才入队。

**D4. 优先级:黑名单最高;规则 AND 在一起。** 任一命中的 **exclude** 规则立即 drop。其余 **include/属性** 规则 AND（如 tag-include 集要求至少一个匹配 tag;性别规则要求配置的条件）。内容过滤与现有评分过滤 AND——彼此不削弱。

**D5. Phase 1 维度来自现有解析。** 从 `MovieDetail`（`actors` 带 name/href/**gender**、`tags`）:**演员黑名单**（按 name/href exclude）、**tag include/exclude**、**性别**（如要求女主演、排除全男）。**年龄推迟（Phase 2）**——它需要管道今天不做的演员主页查询。**订阅（越过评分门槛的白名单）推迟（Phase 2）**——它是包含侧的对应物,且改动更大。

**D6. 确定性、可解释;与偏好模型正交。** 引擎返回 `FilterDecision(keep, reasons)`;drop 原因被surface（stats / `MovieFiltered` 事件 / MCP）。这是一个**硬的、确定性规则**层——区别于 [ADR-022](../ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md) / [ADR-025](../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md) 的 **ML 偏好分**。两者正交:规则决定*资格*,模型日后决定*排序*。

**D7. 模块形态。** `javdb/spider/services/content_filter.py`（与 `dedup.py` 同级）暴露 `evaluate(detail, rules) -> FilterDecision`;一个 `ContentFilterRepo` 读规则;详情选择路径在入队前调用它。

## 后果 (Consequences)

### 正面 (Positive)

- **精准摄取**——黑名单不想要的演员/tag;要求性别条件;只 include 选定的 tag。陈述的缺口被补上。
- **动态**——规则在 D1,运行时可管理（日后 web/MCP），无需改 config 重部署。
- **可解释**——每次 drop 都有原因;无静默消失。
- **附加且安全**——与不变的评分过滤 AND 的第二道闸。
- **大小合适**——没有发行速度不值得的流式机器。

### 负面 (Negative)

- **多一道要理解的闸**——运维者要懂 黑名单最高 + AND 优先级。
- **属性覆盖受解析器限**——仅 性别/tags;年龄需推迟的演员主页拓展。
- **规则管理面**——Phase 1 经 CLI 管理规则;web/MCP 管理是 Phase 2。

## 实施路线图 (Implementation Roadmap)

| 阶段 | IMP | 交付内容 | 推迟内容 |
| --- | --- | --- | --- |
| Phase 1 — 排除 + 属性 | [IMP-ADR040-01](IMP-ADR040-01-content-filter.md) | `ContentFilterRule` 表 + repo;`content_filter` 引擎（演员黑名单、tag include/exclude、性别）;详情后过滤阶段;管理规则的 CLI | 年龄;订阅;web/MCP 管理 |
| Phase 2 — 订阅 + 年龄 | IMP-ADR040-02（占位） | D1 订阅（越过评分门槛的白名单）;经演员主页拓展的年龄过滤;web/MCP 规则管理 | — |
| Phase 3 — 组合（可选） | IMP-ADR040-03（占位） | 与 ADR-025 偏好分组合 | — |

Phase 1 附加且向后兼容（无规则 → 无变化）。Phase 2/3 拓宽包含侧与属性覆盖。

### 明确的非目标 (YAGNI)

- **不做流式 / 频繁 cron**——纠偏所在;daily 节奏保留。
- **Phase 1 不做年龄过滤**——需演员主页拓展（Phase 2）。
- **Phase 1 不做订阅**——包含/白名单侧是 Phase 2。
- **不做 ML**——只确定性规则;偏好打分是 ADR-022/025。
- **不重写 评分/打分人数 过滤**——并联第二道闸（D3）。

## 领域语言 (CONTEXT.md 待补充项)

- **Content filter rule（内容过滤规则）**——`ContentFilterRule` 中一行:一个维度（actor/tag/gender）、一个 mode（exclude/include/…）、一个 value。
- **Blacklist（黑名单）**——exclude 模式的内容过滤规则（最高优先级）。
- **Attribute filter（属性过滤）**——对某个解析属性（gender、tag）的规则。
- **Filter decision（过滤判定）**——引擎对一部影片的 `keep` + `reasons`。
- **Subscription（订阅）**——（Phase 2）一个被关注的实体,其新作越过评分门槛。

## 备选方案 (Alternatives Considered)

- **流式 / 频繁轮询**——否决（D1）:最初的出发点,但慢发行速度使其无意义;价值在过滤而非新鲜度。
- **规则只放 `config.py`**——否决（D2）:静态;D1 支持运行时/web/MCP 管理。
- **只用 ML 过滤（靠 ADR-022/025）**——否决（D6）:硬黑名单确定且即时;偏好模型是单独、后期的排序关切。

## 参考 (References)

- [ADR-022 — User Preference Data Foundation](../ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md)
- [ADR-025 — User Preference Model](../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md)
- [ADR-036 — Event-Sourced Pipeline Spine](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md)
- [ADR-038 — Agentic Operator MCP Surface](../ADR-038-Agentic-Operator-MCP/ADR-038-agentic-operator-mcp-surface.md)

## 状态日志 (Status Log)

- 2026-05-29: Proposed（从"流式摄取"纠偏为内容过滤;三期已划定,IMP 待出）。
