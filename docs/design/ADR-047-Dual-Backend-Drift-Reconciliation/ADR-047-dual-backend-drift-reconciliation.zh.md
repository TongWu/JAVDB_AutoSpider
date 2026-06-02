# ADR-047：双后端漂移校正与定向 guard 扩展

| 字段       | 值                                                                    |
| ---------- | --------------------------------------------------------------------- |
| **状态**   | Proposed                                                              |
| **日期**   | 2026-06-02                                                           |
| **作者**   | Ted                                                                  |
| **关联**   | [ADR-018](../ADR-018-Dual-Backend-Query-Contract/ADR-018-dual-backend-query-contract.zh.md)（Contract Golden——本 ADR 扩展其 guard）、[ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.zh.md)（双后端拆分）、[ADR-029](../_archive/ADR-029-Web-Security-Hardening/ADR-029-web-security-hardening.zh.md)（auth——拥有 token 吊销）、[ADR-005](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.zh.md)（`audit` 写模式已退役） |

> 源自 2026-05-29 架构评审（候选 B）：[architecture-review-2026-05-29.zh.html](../architecture/architecture-review-2026-05-29.zh.html)。2026-06-02 的复扫发现：ADR-018 有意不守护的那一面其实**已经漂移**——并带来用户可见的 bug。本 ADR 校正该漂移，并把 guard 精确扩展到漂移实际发生之处。

## 背景

两个后端通过同一个 D1 服务同一个 Vue 前端（CLAUDE.md / [ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.zh.md) 所称的 *Backend Overlap*）：

- **Python** —— `apps/api/` + `javdb/storage/repos/`（FastAPI，Docker / 自托管）。
- **TypeScript Worker** —— `JAVDB_AutoSpider_Web/server/`（Cloudflare Workers 上的 Hono，云端）。独立 git 仓库。

[ADR-018](../ADR-018-Dual-Backend-Query-Contract/ADR-018-dual-backend-query-contract.zh.md) 引入了 **Contract Golden**，机械地守护**动态查询 builder**（history / sessions / stats-trend 的 WHERE + cursor 逻辑，31 个用例）。该 guard 有效——2026-06-02 复扫确认**被守护的 builder 零漂移**。ADR-018 D3 有意**不守护**静态单语句查询、count 语句、行映射、以及响应 shape 的**值**，理由是它们"很少漂"，且把全部 ~46 个 `prepare()` 点都钉住是低杠杆。

2026-06-02 复扫推翻了这个判断——在几个高频面上，两个后端对同一请求**返回了不同答案**：

| # | 分歧 | Python | TypeScript | 严重度 |
| --- | --- | --- | --- | --- |
| 1 | session `write_mode` 默认值（NULL `WriteMode`）—— `sessions_repo.py:24,91` ↔ `server/routes/sessions.ts:32` | `"audit"` | `"pending"` | **Bug** —— `audit` 已被 [ADR-005](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.zh.md) PR-4 退役；Python 默认值过期 |
| 3 | history `total_estimate` 计数 —— `history_repo.py:505,584` ↔ `server/routes/history.ts:136` | 封顶 `≤10000` | 不封顶 | **Bug** —— 超过 10k 后计数不同 |
| 6 | stats `/summary` —— `apps/api/routers/stats.py:170,183,188` ↔ `server/routes/stats.ts:38,67,111` | `total_torrents` ← `ReportTorrents`；`avg_duration`=null；`proxy_bans` 由日志推导 | `total_torrents` ← `TorrentHistory`；`avg_duration` 计算得出；`proxy_bans`=`0` | **Bug** —— 数字不同 |
| 2 | `SessionList.total_estimate` 死字段 —— `sessions_repo.py:39` | dataclass 有该字段，router 省略 | n/a | 表面 |
| 4 | 吊销执行范围 —— `auth.py:222` ↔ `server/middleware/auth.ts:65` | 每次 decode | 仅 mutation | 有意（ADR-029） |
| 5 | `plain:` 密码后门 —— `server/routes/auth.ts:16` | 无 | 仅 dev | 有意（TS dev） |
| 7 | `/capabilities` 默认值 —— `capabilities.py:49` ↔ `server/routes/capabilities.ts:24` | `storage_backend="sqlite"`、真实 `git_sha` | `"d1"`、字面量 `"cloudflare"` | 部署内禀 |

2026-05-29 评审标记的 JWT 吊销缺口**已闭合**——两个后端现在都实现了吊销（`token-revocation.ts`）；唯一差异（#4）是仅-mutation 的执行范围，按 [ADR-029](../_archive/ADR-029-Web-Security-Hardening/ADR-029-web-security-hardening.zh.md) 属有意为之。

**关键洞察。** ADR-018 D7 把"消除"（Phase 3——把两个 builder 合并为单一共享 spec）推迟到"guard 显示出反复漂移"之时。证据表明**被守护的 builder 没有在漂**；**未守护的静态/响应面才在漂**。所以正确做法**不是**消除 builder（ADR-018 Phase 3），而是**修掉漂移、并把 guard 扩到漂移实际发生之处**——对不漂的大多数，保留 ADR-018 的"低杠杆"判断。

## 决策

把已确认的漂移逐项校正到单一正确行为，然后**窄幅**把 Contract Golden 扩展到确实漂过的面。

### 设计决策

**D1. 把三个真实 bug 校正到单一正确行为（决策已固化）。**

- **D1a. `write_mode` NULL 默认值 → 两端都 `"pending"`。** `audit` 已退役（[ADR-005](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.zh.md) PR-4）；pending 是唯一模式。**Python 改动**（`sessions_repo.py` dataclass 默认值 + 行映射）。
- **D1b. history `total_estimate` → 两端都封顶 10000。** D1 大表 `COUNT` 成本有界；UX 显示"10000+"。**TS 改动**（给 count 语句加 `MIN(COUNT(*), 10000)` 上限）。
- **D1c. stats `/summary` → `total_torrents` 两端都数 `TorrentHistory`。** "总种子数"指历史规范台账，而非每会话报告行。**Python 改动**（`ReportTorrents` → `TorrentHistory`）。`avg_duration_seconds` 两端都计算（从 `ReportSessions.CommittedAt`，TS 已如此）；**Python 改动**（不再返回 null）。

**D2. `proxy_bans_last_7d` 与 `/capabilities` 的 env 字段属部署内禀——不纳入契约。** `proxy_bans` 在 Python 由日志推导（Python 有日志），在 Worker 上不可得（D1 无此日志）→ 各报告各自的尽力值；它**不是**跨后端相等契约。`storage_backend` / `deployment` / `git_sha` 同理：各后端如实报告自己的部署。这些被记为有意的后端特定值，而非漂移。

**D3. 有意的 auth 差异保持原样（记录在案）。** 吊销执行范围（#4，TS 仅 mutation）由 [ADR-029](../_archive/ADR-029-Web-Security-Hardening/ADR-029-web-security-hardening.zh.md) 拥有；`plain:` dev 后门（#5）是 TS 非生产便利。两者均不在此校正。

**D4. 清理表面死字段（#2）。** 从 Python `SessionList` dataclass 移除未填充的 `total_estimate`（或填充它）；它悬空，且有潜在 shape 分歧风险。

**D5. 窄幅扩展 Contract Golden——仅扩到漂过的点。** 两种 guard：

- **D5a. 静态-SQL 用例。** 把已校正的 count 语句（history `total_estimate` count，以及任何被钉住上限/来源的同类 count）作为**固定-SQL 用例**加入 ADR-018 golden（同样的 `{normalized_sql, bindings}` 机制，无需 DB）。这能捕捉这些特定语句未来的再分歧。
- **D5b. 用两端对称单测钉住静态 `/summary` 查询 + `write_mode` 默认值。** 非动态 builder 的已校正面，按 ADR-018 D3 守护静态查询的方式来守——在**每个**后端各放一个单测，钉住其 `/summary` 查询来源（`TorrentHistory`；`CommittedAt` 的 avg-duration 公式；`IsDeleted=1` 的 dedup 过滤）与 `write_mode` NULL→`"pending"` 默认值。*（IMP-ADR047-02 编写期修订：原计划的独立 `docs/api/contract/response-values.golden.json` fixture **不再构建**——已漂的面绝大多数是 SQL，故 count 语句并入 ADR-018 既有 SQL golden（D5a），而唯一的非-SQL 值加上少数静态查询，用对称单测钉住比新建一种 fixture 类型 + 跨仓库 vendor 流水线更划算。）*

**D6. 保持窄——不守护宽泛静态面。** ADR-018 D3 的判断（~46 个不漂的 `prepare()` 点钉住是低杠杆）**仍成立**。本 ADR 只守护有*实证*漂移的点。不引入结果等价或全静态查询钉住。

**D7. 跨仓库、同步执行。** 每个修复都触及两个仓库；扩展后的 golden 是保证两端对齐的机械 guard（Python 重新生成 → TS vendored + CI 校验，正如 ADR-018 D5/D6 已通过 `repository_dispatch` 接好）。

**D8. 与 ADR-018 的关系——扩展，不 supersede。** ADR-018 的"先守护/仅 builder"宪章不变；ADR-047 增加（a）一次性漂移校正与（b）对同一 guard 机制的*定向、证据驱动*的加宽。会在 ADR-018 的 Status Log 加一条回指。

## 后果

### 正面

- **用户从任一后端得到一致答案**——三个真实 bug（过期 `audit`、封顶 vs 不封顶计数、错误的 `total_torrents` 表）被移除。
- **漂过的面无法再静默分歧**——窄 golden + Contract-Values fixture 把它机械化，复用 ADR-018 已验证的分发路径。
- **最小、证据驱动**——只守漂过的，对其余尊重 ADR-018 的"低杠杆"判断。
- **建立在既有基础设施上**——无新跨仓库机制；`repository_dispatch` 重新 vendored 流水线已存在。

### 负面

- **跨仓库 PR**——每个修复在 Python 仓库与 TS 仓库各落一份，由 golden 绑定（ADR-018 的摩擦，现扩到多几个用例）。
- **一种新 guard 类型**（Contract-Values fixture）在两端各加少量测试基础设施。

### 风险

- **Contract-Values 机制可能越界。** 缓解：仅限这几个已校正的默认值；抵制其膨胀为通用响应快照测试（那是 OpenAPI 契约的职责）。
- **某校正决策日后被证明有误**（如 `total_torrents` 语义）。缓解：该值现钉在一个 fixture 里，改它就是跨两后端的一处可见 diff。

## 实施路线图

| 阶段 | 交付 | 推迟 |
| --- | --- | --- |
| **Phase 1 —— 校正** | 在两个仓库修掉 3 个真实 bug（D1a Python `write_mode`→pending；D1b TS `total_estimate` 封顶；D1c Python stats `/summary` `total_torrents`→TorrentHistory + `avg_duration`）；清理表面死字段（D4）；记录有意/部署内禀项（D2/D3） | guard |
| **Phase 2 —— guard** | 用已校正的 count 语句扩展 golden（D5a）+ 新增 `response-values.golden.json` Contract-Values fixture（D5b），经既有流水线在 TS vendored + CI 校验 | 宽泛静态查询 guard（明确排除——D6） |

### 明确的非目标（YAGNI）

- **不做宽泛静态查询/响应快照 guard**——对不漂的大多数，ADR-018 D3 的低杠杆判断成立（D6）。
- **不做 ADR-018 Phase 3（消除）**——被守护的 builder 没在漂，按当前证据无须合并为共享 spec。
- **不做 auth 校正**——吊销范围与 `plain:` 后门保留（auth 归 ADR-029）。
- **不把部署内禀字段纳入契约**——`proxy_bans`、`storage_backend`、`deployment`、`git_sha` 按部署如实不同（D2）。

## 领域语言（CONTEXT.md 新增）

- **Contract Values fixture（契约值夹具）** —— 由 Python 生成的 golden（`docs/api/contract/response-values.golden.json`），钉住已校正的*响应值*默认/语义（非 SQL），两个后端都对它断言；是 ADR-018 SQL Contract Golden 的值级别同胞。
- **Deployment-intrinsic field（部署内禀字段）** —— 一个 API 字段，因为各报告各自部署的环境而*正确地*在后端间不同（`storage_backend`、`deployment`、`git_sha`、`proxy_bans_last_7d`）；明确排除在跨后端相等契约之外。

## 考虑过的替代方案

- **只修 bug、不扩 guard** —— 否决：同样的面会再静默漂；ADR-018 的全部意义就是机械检测。
- **消除（ADR-018 Phase 3——共享 filter spec）** —— 暂否决：证据显示*builder* 没在漂；消除它们是把力气花在没问题的地方。
- **宽泛守护全部静态查询/完整响应快照** —— 否决：ADR-018 D3 已权衡其为低杠杆；2026-06-02 的证据只支持守护漂过的点，而非全部 ~46 个站点。

## 参考

- [ADR-018 —— Dual-Backend Query Contract](../ADR-018-Dual-Backend-Query-Contract/ADR-018-dual-backend-query-contract.zh.md)
- [ADR-017 —— Cloudflare-First Deployment](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.zh.md)
- [ADR-029 —— Web Security Hardening](../_archive/ADR-029-Web-Security-Hardening/ADR-029-web-security-hardening.zh.md)
- [ADR-005 —— db.py 退役与 Repo 模式](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.zh.md)
- 2026-05-29 架构评审（候选 B）：[architecture-review-2026-05-29.zh.html](../architecture/architecture-review-2026-05-29.zh.html)

## 状态日志

- 2026-06-02：Proposed。源自 2026-05-29 评审候选 B + 2026-06-02 跨仓库复扫——后者发现 ADR-018 有意不守护的静态/响应面已漂移（7 项分歧；3 个真实用户可见 bug）。决策已固化：`write_mode`→`pending`（D1a）、`total_estimate` 两端封顶 10000（D1b）、stats `total_torrents`→`TorrentHistory`（D1c）；仅窄幅扩展 guard（D6）。把候选 B 从 ADR-018 Phase 3（"消除"）重新定向为修漂移 + 加宽 guard，因为被守护的 builder 是干净的、漂的是未守护面。
- 2026-06-02：**两个 IMP 已写出**（IMP-ADR047-01 校正、IMP-ADR047-02 守护）。编写期**修订 D5b**：弃用独立 `response-values.golden.json` fixture —— count 语句并入 ADR-018 SQL golden（D5a）；静态 `/summary` 查询与 `write_mode` 默认值由两端对称单测钉住。理由：已漂的面大多是 SQL，单个非-SQL 值不值得新建一种工件类型 + vendor 流水线。
