# ADR-055：双后端契约单一真源（SQL 片段 + 常量 codegen）

**状态 (Status):** Accepted —— Phase 1 已于 2026-06-15 交付；Phase 2/3 后续仍待推进
**日期 (Date):** 2026-06-15
**作者 (Author):** Ted
**关联实现计划 (Related Implementation Plans):** [IMP-ADR055-01](IMP-ADR055-01-registry-generator-watchintent.md)（Phase 1 —— registry + 生成器 + CI + WatchIntent 迁移）
**D1 写入类别 (D1 Write Class):** n/a（本 ADR 交付的是 codegen/契约机制；被收编的每个片段保留其原有写入类别 —— 例如 WatchIntent 仍是 `authoritative`）
**关联 (Related):** [ADR-018](../ADR-018-Dual-Backend-Query-Contract/ADR-018-dual-backend-query-contract.zh.md)（查询契约 —— 动态 SELECT builder 的守卫；本 ADR 执行其推迟的 D7）、[ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.zh.md)（双后端拆分）、[ADR-054](../ADR-054-User-Intent-Discovery-Layer/ADR-054-user-intent-discovery-layer.zh.md)（WS1 验证暴露 gap B6）、[ADR-029](../_archive/ADR-029-Web-Security-Hardening/ADR-029-web-security-hardening.zh.md)（auth —— 不在范围）、[ADR-042](../ADR-042-D1-Atomic-Commit-Boundaries/ADR-042-d1-atomic-commit-boundaries.zh.md)（D1 写入类别）

## 背景 (Context)

[ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.zh.md) 把同一个 Vue 前端拆给**两个独立部署、不同语言、不同 repo** 的后端：

- **Python** —— `apps/api/` + `javdb/storage/`（FastAPI，Docker / 本地）。
- **TypeScript** —— `JAVDB_AutoSpider_Web/server/`（Cloudflare Workers 上的 Hono）。

无论哪一边应答,重叠的查询/写入逻辑都必须产出等价结果。[ADR-018](../ADR-018-Dual-Backend-Query-Contract/ADR-018-dual-backend-query-contract.zh.md) 已机制化了其中一部分 —— 给**动态 SELECT builder** 做了一个 Python 真源的 **Contract Golden**,vendoring 给 TS 并由 CI 的 freshness + conformance 锁住。但 ADR-018 D3 把那个守卫**只**限定在动态 SELECT builder;静态语句、mutation、共享数据常量都被留给一条散文规则加各 repo 自测。ADR-018 D7 ——"**eliminate**"(真正的单一真源,而不只是漂移守卫)—— 被**推迟**,"until recurring drift justifies it"。

[ADR-054](../ADR-054-User-Intent-Discovery-Layer/ADR-054-user-intent-discovery-layer.zh.md) 的 WS1 验证把这个"复发"暴露为 **gap B6**。`WatchIntent` 的 UPSERT SQL 存在**四份手工维护的拷贝**：

| # | 位置 | 角色 |
| --- | --- | --- |
| 1 | `javdb/storage/repos/watchlist_repo.py` `WATCH_INTENT_UPSERT_SQL` | Python 生产 |
| 2 | `tests/unit/test_watch_intent_upsert_parity.py` `CANONICAL` | Python 测试基准 |
| 3 | `JAVDB_AutoSpider_Web/server/services/watchlist-service.ts` `WATCH_INTENT_UPSERT_SQL` | TS 生产 |
| 4 | `JAVDB_AutoSpider_Web/server/__tests__/watch-intent-upsert-parity.test.ts` `CANONICAL` | TS 测试基准 |

每个 repo 的 parity 测试只断言**自己的生产 == 自己的 CANONICAL**(repo 内)。**没有任何东西断言 `#2 == #4`。** 改了 #1+#2(Python 绿)却忘了 TS 侧,#4 没动,于是 TS 测试照样绿 —— **跨 repo 漂移是静默的**。所谓"守卫"是两个互不相关的 repo 内自查加一句 `// MUST be character-identical` 注释。同样的形态已经排队复发:[ADR-040](../ADR-040-Content-Filter-Rules/ADR-040-content-filter-rules.zh.md) WS4a 内容过滤允许表(`VALID_RULE_MODES` / `VALUE_REQUIRED`)、`system_state` upsert、`ReportSessions` 列清单。这就是足以触发执行 D7 的复发。

## 决策 (Decision)

把 ADR-018 推迟的 D7 作为一个专门机制执行,并**扩到所有静态跨后端片段**。设立一个 **Python 真源的契约 registry**,由它**生成** TypeScript 镜像 —— SQL 字符串、**typed bind helper**、共享常量 —— 使**两个 repo 都不再手写它们**。生成物沿用既有的 `openapi.json` / `api.gen.ts` 跨 repo 通道分发。动态 SELECT builder 继续归 ADR-018 守卫(明确非目标)。

### 设计决策 (Design Decisions)

**D1. 单一真源 = 一个 Python 契约 registry（`javdb/storage/contract/`）。** 每个共享片段/常量在此**声明一次**,作为结构化数据:SQL 文本(SQLite,`?` 占位符)+ 按序 typed 参数;或常量的 名称 + 值 + kind。这是唯一手工编辑处。遵循 ADR-018 D1 的粒度(真源 = Python)。

**D2. Eliminate 而非 guard —— 生成物是 TS import 的*生产*代码,不是测试 fixture。** 这是与 ADR-018 的结构性区别:ADR-018 的 golden 放在 `server/__tests__/fixtures/`,而 TS 仍保留自己手写的 builder(golden 只*检测*漂移)。这里,TS 的 SQL 与 bind 逻辑**就是**放在 `server/contract/` 下的生成模块,不存在第二份手写拷贝。因为只有一个作者,漂移无从发生。

**D3. 范围 = 所有*静态*可共享片段。** mutation SQL（`INSERT`/`UPSERT`/`UPDATE`/`DELETE`）、静态 `SELECT` 字符串、手抄数据常量(允许表/枚举)。**动态 SELECT builder 不在内** —— 它们是条件逻辑而非字符串;消除它们需要查询 DSL(已否决,见备选)。它们继续归 ADR-018 守卫。

**D4. 同一 SQLite 方言让消除很便宜。** D1 *就是* SQLite;Python `sqlite3` 与 D1 的 `prepare()` 都用 `?` 位置绑定。共享 SQL 字符串当前已逐字相同、且两边都能逐字执行(由现有 B6 代码证明)。因此"生成"是**发射,而非方言翻译**。

**D5. typed bind helper 消除 bind 顺序漂移。** registry 按序声明带类型的参数。生成器为每个片段吐出一个 TS `bindXxx(stmt, { camelCaseParams }): D1PreparedStatement`;Python 侧用一个通用 `order_params(fragment, **kwargs)` 按 registry 排序。**两边都不再手写 `.bind()` / 元组顺序**,从而堵死"token 不变但 bind 顺序变"这类配对字符串测试抓不到的语义漂移。`snake_case`(Python/SQL)→ `camelCase`(TS 对象键)的映射是机械且确定的。

**D6. 不生成 Python —— registry 直接被消费。** registry *本身就是* Python,所以生产 Python 直接从中 import SQL 常量与通用 binder。只有 TS 镜像被生成。这避免了"用 Python 生成 Python"的别扭,Python 开发体验不变。

**D7. 分发 + CI 复用 `openapi.json` / `api.gen.ts` 通道（ADR-018 D4/D5/D6）。** 生成器 `apps/cli/ops/dump_sql_contract.py`(紧挨 `dump_openapi` / `dump_query_contract`)吐出 `docs/api/contract/sql-contract.gen.ts`,在 Python repo 提交。Python CI 跑 **freshness** 测试(`regen == committed`)。web repo 新增 `scripts/fetch-sql-contract.mjs`(`gen:sql-contract`,仿 `fetch-openapi.mjs` / `fetch-query-golden.mjs`)把 artifact vendoring 到 `server/contract/sql-contract.gen.ts`;TS CI 跑 **freshness**(重拉 Python-`main`,`git diff --quiet`)+ **conformance**(执行每个片段,断言列→值语义)。跨 repo race(Python-`main` 一改就把 TS CI 变红,直到 re-vendor)被**接受**,与 `openapi.json` 完全一致。

**D8. 每个片段必须配行为 smoke。** 每个片段在每个 repo 配一个执行并读回的测试(各列写不同值 → 断言映射;有 `COALESCE`/删除语义处一并断言)。有了 typed helper,这是安全网而非主守卫,但它钉住运行时行为,并在 TS 侧兼作 conformance 测试。

**D9. 与 ADR-018 的关系 —— 扩展,不取代。** ADR-018 对动态 SELECT builder 的守卫保持不变。ADR-055 为*静态*面执行其推迟的 D7,并把范围扩到 mutation + 常量。在 ADR-018 的 Status Log 加一条反向引用。

## 影响 (Consequences)

### 正面 (Positive)

- **消除所有静态片段的跨 repo 漂移类** —— 单一作者、机械镜像、CI 锁定,可信度等同 `api.gen.ts`。
- **B6 被结构性解决** —— 四份拷贝坍缩为一条 registry 条目;排队中的 WS4a 允许表等实例免费获得该机制。
- **typed bind helper 彻底消除 bind 顺序语义漂移**(配对字符串测试唯一抓不到的点)。
- **复用已验证机制** —— openapi / query-contract 的"生成→vendoring→CI"形态;新概念极少。

### 负面 (Negative)

- 两个 repo 各多**一条生成物流水线 + CI**(缓解:与现有两条形态相同)。
- registry 编写比裸 SQL 常量**更啰嗦** —— 参数要带类型声明(typed helper 的代价)。
- **跨 repo race 仍在**(Python `main` 一改 TS CI 变红直到 re-vendor)—— 接受,与 openapi 一致。
- **动态 builder 仍双份维护**,归 ADR-018(本轮不消除)。

## 实施路线图 (Implementation Roadmap)

| 阶段 | IMP | 交付 | 推迟 |
| --- | --- | --- | --- |
| Phase 1 | [IMP-ADR055-01](IMP-ADR055-01-registry-generator-watchintent.md) | `javdb/storage/contract/` registry + `dump_sql_contract.py` + `sql-contract.gen.ts` + Python freshness CI + web `fetch-sql-contract.mjs` vendoring + TS freshness/conformance CI + **端到端迁移 WatchIntent upsert**(4 份 → 1 条 registry;删掉手抄 CANONICAL parity 测试) | 其余实例 |
| Phase 2 | IMP-ADR055-02（目标落地时对着真实形态写） | 迁移其余静态镜像点:WS4a 允许表(`VALID_RULE_MODES`/`VALUE_REQUIRED`)、`system_state` upsert、`ReportSessions` 列清单、被镜像的静态 SELECT | — |
| Phase 3 | （约定,无 IMP） | ADR 强制 + PR 清单:今后每个双后端静态 SQL/常量都进 registry | — |

### 明确的非目标 (YAGNI)

- **动态 SELECT builder** —— 归 ADR-018;此处不消除。
- **不引入 ORM / 查询 DSL** —— 只发射静态字符串。
- **不碰 auth 逻辑** —— 归 ADR-029(TS-only 唯一在线 auth 面)。
- **TS 端不做运行时 artifact 加载** —— 生成后 vendoring 的模块*就是* Workers 场景下的消除形态(编译期 import,而非运行时 fetch)。

## 备选方案 (Alternatives Considered)

- **只做更好的守卫**(一个跨 repo CANONICAL diff 测试)—— 否决:既定目标是消除;守卫仍留两份手写拷贝。
- **运行时共享 JSON、两边加载** —— 否决:Workers 端别扭、丢编译期类型、且造成 Python 既作者又消费者的循环。
- **中性 spec → 双向 codegen**(Python 与 TS 都生成)—— 否决:引入第三种 authoring 格式,违背 ADR-018"真源 = Python";最重。
- **用查询 DSL 消除动态 builder** —— 本轮否决:量大、风险高,且会替换掉 ADR-018 仍在用的守卫。

## 领域语言 (Domain Language，补充进 CONTEXT.md)

- **Contract registry（契约 registry）** —— Python 单一真源(`javdb/storage/contract/`),把每个静态跨后端 SQL 片段和共享常量声明一次。
- **SQL fragment（SQL 片段）** —— 一个具名的静态 SQL 语句(mutation 或静态 select),带按序 typed 参数,在两个后端间共享。
- **Generated contract module（生成契约模块）** —— `sql-contract.gen.ts`,从 registry 发射的 TS 镜像;生产代码,像 `api.gen.ts` 一样 vendoring。

## 参考 (References)

- [ADR-018 — 双后端查询契约](../ADR-018-Dual-Backend-Query-Contract/ADR-018-dual-backend-query-contract.zh.md)
- [ADR-017 — Cloudflare-First 部署](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.zh.md)
- [ADR-054 — 用户意图与发现层](../ADR-054-User-Intent-Discovery-Layer/ADR-054-user-intent-discovery-layer.zh.md)（WS1 验证,gap B6）
- [ADR-042 — D1 原子提交边界](../ADR-042-D1-Atomic-Commit-Boundaries/ADR-042-d1-atomic-commit-boundaries.zh.md)（D1 写入类别）

## 状态日志 (Status Log)

- 2026-06-15：Proposed。由 ADR-054 WS1 的 gap B6 brainstorm 而来。已定决策:eliminate(而非 guard);范围 = 所有静态片段(mutation SQL + 静态 select + 常量),动态 builder 留 ADR-018;机制 = Python registry → 经 openapi/`api.gen.ts` 通道 codegen TS;两边 typed bind helper(不手写 bind 顺序)。Phase 1 → [IMP-ADR055-01](IMP-ADR055-01-registry-generator-watchintent.md)。
- 2026-06-15：Phase 1 已交付（[IMP-ADR055-01](IMP-ADR055-01-registry-generator-watchintent.md)）—— registry + generator + freshness/conformance CI；WatchIntent upsert 已迁移（4 份 → 1 条 registry entry）；手写 CANONICAL parity tests 已移除。
- 2026-06-15：Closeout 将 ADR 状态推进为 Accepted。Phase 1 已实现并完成本地验证；Phase 2/3 仍是活跃后续，因此 ADR 文件夹不归档。
