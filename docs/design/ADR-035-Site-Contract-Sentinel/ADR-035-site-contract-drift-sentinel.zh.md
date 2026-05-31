# ADR-035：站点契约漂移哨兵

| 字段       | 值                                                                    |
| ---------- | --------------------------------------------------------------------- |
| **状态**   | Proposed — 伞型;执行下放给各期 IMP                                    |
| **日期**   | 2026-05-29                                                            |
| **作者**   | Ted                                                                   |
| **关联**   | [ADR-026](../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md), [ADR-020](../_archive/ADR-020-Parser-Interface-Consolidation/ADR-020-parser-interface-consolidation.md), [ADR-019](../_archive/ADR-019-Session-Lifecycle-Authority/ADR-019-session-lifecycle-authority.md), [ADR-011](../_archive/ADR-011-Parsing-Module/ADR-011-javdb-parsing-module.md) |

> 源自 2026-05-29 一次关于全新方向(方向二——主动可靠性)的头脑风暴。

## 背景 (Context)

整个系统压在 javdb.com 的 HTML 结构上。Rust scraper 在 `javdb/rust_core/src/scraper/` 里硬编码选择器（`div.item`、`a.box`、`div.video-title`、`div.score`、`span.value`、`div.meta`、`span.tag`…）。站点一变,解析就**静默退化**——而今天系统看不见:

- **`html_validators.py` 只抓灾难级失败**——`validate_index_html() -> (is_valid, is_empty)` 识别 `empty-message`、登录墙、维护页。它回答的是"这页到底能不能用",而非"某字段是不是悄悄不解析了"。
- **没有字段级漂移检测。** 若 `div.score` 失配,`score` 字段对 100% 的 item 变 `null`,而其它字段照常解析;daily run 跑完、写库,数据静默出错。
- **golden fixture 仅供单测**——`tests/fixtures/parser/*.html`（7 个）是离线边缘 case 回归,不是实时漂移基线。
- **`health_check.py` 只查基础设施**（qB / 代理 / SMTP），从不碰解析/站点健康。

真正要紧、且完全没覆盖的失败模式是**静默的字段级解析漂移**:一个结构上合法、但个别字段悄悄崩塌的页面。[ADR-026](../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md) 是*反应式*（事后诊断）;本 ADR 是*主动式*——在漂移首次出现的那次 run 上就检测到它、并阻止它污染 DB。

## 决策 (Decision)

构建一个**站点契约漂移哨兵**:一份声明式的逐字段解析契约,两个观测源（每次 run 的搭车遥测 + 一个小型独立 canary）喂同一个检测核心,外加**分级动作**——关键漂移门控提交,软漂移发告警——复用现有 pending→commit 生命周期与 ADR-026 incident 面。

### 设计决策 (Design Decisions)

**D1. 混合观测:搭车遥测 + 独立 canary,一个核心。** 两个源喂同一个 `detectors` 核心:(a) 给现有 daily 解析路径插桩,发出**每次 run 的逐字段填充率**（无新增抓取）;(b) 一个小型独立 **canary** 定时抓固定页面集并解析。源 (a) 在站点改版后的首次 daily run 上即捕获漂移;源 (b) 在两次 run *之间*、下一次 daily 之前就捕获。

**D2. 声明式解析契约 + 严重度分级 + 分层检测。** `PARSE_CONTRACT`（`javdb/spider/parse_contract.py`）按 `page_type × field` 声明 `severity`（`critical` | `soft`）与期望:

```python
PARSE_CONTRACT = {
  "index": {
    "href":        {"severity": "critical", "min_fill": 0.99},
    "video_code":  {"severity": "critical", "min_fill": 0.99},
    "title":       {"severity": "critical", "min_fill": 0.95},
    "score":       {"severity": "soft",     "baseline_rel": 0.5},
    "comments":    {"severity": "soft",     "baseline_rel": 0.5},
  },
  "detail": {
    "magnets":     {"severity": "critical", "min_fill": 0.90},
    "actors":      {"severity": "soft",     "baseline_rel": 0.5},
    "release_date":{"severity": "soft",     "baseline_rel": 0.5},
  },
}
```

- **关键**字段:`fill_rate < min_fill` → critical drift（绝对阈值;这些必须近 100%,否则产品本身坏了）。
- **软**字段:`fill_rate < baseline_rel × 滚动基线` → soft drift（相对崩塌;自校准,低误报）。
- **样本量护栏:** 观测 item 数低于下限时跳过判定（避免小样本抖动）。

**D3. 分级动作——关键门控提交、软告警。** 在会话提交前,对本次 run 的 field-health 评估:

- **关键漂移 →** 会话**不提交**:置 `failed`（`FailureReason='site_drift'`），pending 行不晋升（走现有 failed-session 清理路径），raise **critical OpsIncident + 邮件告警**。`MovieHistory` / `TorrentHistory` 不被静默垃圾污染。
- **软漂移 →** soft OpsIncident + 邮件 advisory;run 照常提交。

**D4. 与 `html_validators` 划界——漂移 ≠ 灾难。** 哨兵**只在**页面结构上是合法 index/detail、但某字段崩塌时才触发。登录墙、维护页、空结果仍归 `html_validators`（另一种失败模式），所以哨兵从不重复告警,也不会把登录墙误判成"漂移"。

**D5. 基线侵蚀对策(温水煮蛙)。** 缓慢衰减的软字段绝不能把自己的基线一路拖低直到不再告警。对策:(a) **慢 EMA** 基线;(b) 一旦某字段进入 soft-drift,**冻结其基线更新直到 ack**;(c) **只从干净(未漂移)、已提交的 run 学习基线**。

**D6. 复用 ADR-026 incident 面——哨兵是一个新检测器。** 漂移事件写成 `OpsIncidents`,用新 `incident_type='site_drift'`,并走现有邮件 advisory 路径。不另起告警系统;ADR-026 的 AI 诊断可像处理其它 incident 一样总结漂移。

**D7. 模块形态镜像 ADR-033 / ADR-026。** `javdb/ops/sentinel/` 含 `service.py`（`run(SentinelOptions) -> SentinelResult`）、`contract.py`、`detectors.py`、`probes.py`（canary）、`field_health.py`（搭车聚合）、`models.py`、`persistence.py`。`apps/cli/ops/sentinel.py` 是 CLI adapter;`.github/workflows/SiteContractSentinel.yml` 调度 canary;搭车钩子在解析边界;commit 门控在会话提交路径。

**D8. 数据模型:一张字段健康表;不另起告警表。** `ParseRunFieldFill` 按
`session_id × page_type × field` 记录每次 run 的填充率; Phase 1 的
已提交行中位数即 D1-canonical 基线。漂移事件复用 `OpsIncidents`（D6）。

## 后果 (Consequences)

### 正面 (Positive)

- **静默的字段级漂移变可见**——完全没覆盖的失败模式,现在在它出现的首次 run 上即被检测。
- **坏数据被拦在门口**——关键漂移拒绝提交,而非写入静默垃圾,复用 pending→commit。
- **主动提前量**——canary 在两次 run 之间就报"站点变了;下次 daily 会失败"。
- **不另起告警面**——复用 ADR-026 incident + 邮件。
- **自校准**——软阈值跟随学习到的基线,而非魔法数字。

### 负面 (Negative)

- **多一份契约要维护**——critical/soft 分类与阈值需手写,且要随解析器演进。
- **调参风险**——阈值太紧会门控好 run;样本量护栏与相对基线缓解但不消除。
- **canary 增加抓取/Cloudflare/login 成本**（Phase 2）——靠设计保持极小。
- **commit 门控耦合**——门控位于会话提交路径,须与 ADR-019 生命周期保持对齐。

## 实施路线图 (Implementation Roadmap)

| 阶段 | IMP | 交付内容 | 推迟内容 |
| --- | --- | --- | --- |
| Phase 1 — 搭车 + 门控 | [IMP-ADR035-01](IMP-ADR035-01-piggyback-and-gate.md) | `parse_contract`;`field_health` 每次 run 遥测;`detectors`;`ParseRunFieldFill` 已提交行中位数基线;daily run 上的分级动作（关键门控 + 软 incident）;`site_drift` incident 类型 | 独立 canary;web/AI 面 |
| Phase 2 — 独立 canary | IMP-ADR035-02（占位） | `probes` + `SiteContractSentinel.yml` cron + pinned 页 + golden anchor;两次 run 之间检测 | — |
| Phase 3 — 面（可选） | IMP-ADR035-03（占位） | 逐字段健康度上 web（ADR-034 模式）/ AI 漂移摘要 | — |

Phase 1 以**零新增抓取**交付头号价值（抓住漂移 + 保护 DB）。Phase 2 增加两次 run 之间的提前量。Phase 3 是可选打磨。

### 明确的非目标 (YAGNI)

- **不做选择器自愈 / 自动改写解析器**——风险太高;哨兵检测并门控,由人去修选择器。
- **不做 ML**——填充率 vs 基线是确定性且可解释的。
- **canary 保持极小**——一个索引页 + 几个 pinned 详情页。
- **不重复灾难级检测**——登录墙/维护页/空结果仍归 `html_validators`（D4）。

## 领域语言 (CONTEXT.md 待补充项)

- **Parse contract（解析契约）**——逐 `(page_type, field)` 的严重度与期望填充的声明式规格,"健康解析长什么样"的真相源。
- **Field fill-rate（字段填充率）**——解析出的 item 中某字段非空的比例;漂移度量。
- **Site drift（站点漂移）**——结构合法、但个别字段跌破契约/基线的页面;`critical`（门控）或 `soft`（告警）。
- **Sentinel（哨兵）**——根据契约评估 field-health 的 service。
- **Canary probe（金丝雀探针）**——用于两次 run 之间漂移检测的小型独立定时抓取+解析。

## 备选方案 (Alternatives Considered)

- **所有漂移都只告警**——否决（D3）:让静默垃圾落库;而本设计的核心正是把关键漂移拦在门口。
- **任何漂移都严格门控**——否决（D3）:单个软字段就停掉整条管道;误报/运维成本高。
- **仅 canary 或仅搭车**——否决（D1）:仅搭车没有两次 run 之间的提前量;仅 canary 错过完整 daily 样本,且以最少覆盖换最大 Cloudflare 成本。
- **完全学习式契约(无手写严重度)**——否决（D2）:学习式基线无法知道哪些字段*关键到要门控*;critical/soft 之分必须声明。

## 参考 (References)

- [ADR-026 — AI Operations Diagnosis](../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md)
- [ADR-020 — Parser Interface Consolidation](../_archive/ADR-020-Parser-Interface-Consolidation/ADR-020-parser-interface-consolidation.md)
- [ADR-019 — Session Lifecycle Authority](../_archive/ADR-019-Session-Lifecycle-Authority/ADR-019-session-lifecycle-authority.md)
- [ADR-011 — Parsing Module](../_archive/ADR-011-Parsing-Module/ADR-011-javdb-parsing-module.md)

## 状态日志 (Status Log)

- 2026-05-29: Proposed(伞型;三期已划定,IMP 待出)。
- 2026-05-31: 第一期([IMP-ADR035-01](IMP-ADR035-01-piggyback-and-gate.md))
  已实现 —— `parse_contract`、`field_health` 搭车观测(顺序与并行两条索引路径
  均已接入)、`ParseRunFieldFill` 表 + repo、纯函数 `detectors`、`service`
  (唯一写入方 + `site_drift` 事件)、提交闸门(critical → 失败;哨兵自身出错时
  fail-open 放行),`apps.cli.ops.sentinel`,以及 API 提交路径。生产 D1 迁移已在
  `javdb-reports` 上验证完成。偏离计划之处见 IMP 的 "As-Built Notes"。第二、三期仍
  待定,故伞型 ADR 维持 Proposed。
