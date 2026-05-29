# ADR-019: Session 生命周期权威

| 字段       | 值                                                                    |
| ---------- | --------------------------------------------------------------------- |
| **状态**   | Proposed                                                              |
| **日期**   | 2026-05-29                                                            |
| **作者**   | Ted                                                                   |
| **关联**   | [ADR-005](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md)（Repo 模式、pending 模式 commit/rollback）、[ADR-012](../_archive/ADR-012-Pipeline-Run-Boundary/ADR-012-pipeline-run-structured-boundary.md)（run 边界） |

> 源自 2026-05-29 架构审查（候选 E）：[architecture-review-2026-05-29.zh.html](../architecture/architecture-review-2026-05-29.zh.html)。

## 背景（Context）

一次 pipeline **Session** 经过状态机——`in_progress → finalizing → committed`，或 `→ failed`。如今这台状态机**没有单一所有者**：合法转换内联在散落于四个原语函数的 SQL `WHERE` 子句里，代码库其余部分还从 15+ 处直接读写 `ReportSessions.Status`。

### 四个转换原语的守卫不一致

均在 `javdb/storage/db/_db_reports.py`：

| 原语 | file:line | SQL 守卫 | 允许 |
| --- | --- | --- | --- |
| `db_begin_finalize_session` | `:741`（SQL `:750`） | `WHERE Id=? AND Status='in_progress'` | `in_progress → finalizing`（严格） |
| `db_finish_commit_session` | `:757`（SQL `:766`） | `WHERE Id=? AND Status='finalizing'` | `finalizing → committed`（严格） |
| `db_mark_session_committed` | `:147`（SQL `:164`） | `WHERE Id=? AND Status IS NOT 'committed'` | **任何非 committed → committed**（松——含 `failed → committed`） |
| `db_mark_session_failed` | `:173`（SQL `:193`） | `WHERE Id=?`（**无状态守卫**） | **任何 → failed**，含 `committed → failed` |

后两个是**潜在的数据损坏路径**：`db_mark_session_failed` 能把 `committed` 翻成 `failed`，`db_mark_session_committed` 能让 `failed` 复活。`db_rollback_session` 在 Python 里有防御（`_db_rollback.py:403-427` 对 committed/finalizing 跳过该标记），但**原语本身**没有保护——任何现在或将来的调用方都能损坏一个已 committed run 的保护。

### 没有 `can_transition`，无法隔离测试

全代码库没有任何 `can_transition(from, to)`——合法性活在 SQL 字符串和散文 docstring 里。因此转换合法性**无法脱离真实数据库做单测**；每个 commit/rollback 测试都建真实 SQLite。

### commit 编排庞大、无法分块测试

`_commit_session_bulk`（`_db_history_write.py:1047-1380`，约 333 行）与 `db_commit_session_history`（`:1454-1653`，约 199 行）把*状态机*关切（`:1535`、`:1636` 的状态翻转）和*数据搬运*关切（prefetch → classify → batch-upsert → mark-applied）交织在一起。分类核心其实已把"决策"与"执行"分开（它在任何 `_bulk_run` 之前就构造好语句列表），但那个边界是局部变量、不是返回值——所以子步无法单独执行。

## 决策（Decision）

引入 **`SessionLifecycle`** 作为合法 session 状态转换的单一权威，并（延后 Phase 2）抽出一组命名、可单独测试的 **`CommitPipeline`** 子步。**这是两个不同关切、按序进行**——Phase 1 单独先发。

### 设计决策（Design Decisions）

**D1. `SessionLifecycle` 是单一转换权威。** 新建深模块 `javdb/storage/sessions/lifecycle.py`，暴露极小接口：`get_state(session_id) -> SessionState`、**纯函数** `can_transition(from, to) -> bool`（零 DB——合法图的单一真相源）、以及先校验再分派到现有原语的 `transition(session_id, to)`。所有状态**写入**都路由过它。

**D2. 合法图；非法边抛异常、幂等边返回 0。**

```
in_progress → finalizing        in_progress → committed（staging 快路径，rclone/commit 必需）
in_progress → failed            finalizing  → committed        finalizing → failed
X → X（幂等，返回 0）           committed/failed 已在目标态 → 返回 0
committed → failed   ── 非法 → 抛 IllegalTransition
failed    → committed ── 非法 → 抛 IllegalTransition
```

`transition` **仅**在真正非法的边（`committed→failed`、`failed→committed`）抛 `IllegalTransition`；对幂等/no-op 边返回 `0`，**保留**今天 `sessions/commit.py:222-228` 和 `apps/cli/db/commit_session.py:434` 的 `n==0` 控制流。这些非法边没有任何现有调用方有意触发，故在此抛错纯属安全提升。

**D3. 写入路由过它；读保持；策略在它之上。** ~9 个状态写入点（`_db_history_write.py:1535,1636`；`_db_rollback.py:430`；`sessions/commit.py:216`；`apps/cli/db/commit_session.py:430`；`rclone/manager.py:1262,1267,1273,1536,1609,1660,1687`）改调 `transition`。读继续用 `db_get_session_status` / `SessionsRepo`；`get_state` 作为带类型的替代提供，不强制迁移。`_db_rollback.py` 里现有的 **Python 策略守卫**（committed 拒绝、finalizing 跳过）留在 `transition` *之上*——它们决定*是否*打 failed 标记，是策略而非合法性。

**D4. Phase 2 — 抽出 `CommitPipeline`（延后）。** 把 `_commit_session_bulk` 拆为 `prefetch_pending` / `classify_and_resolve`（纯，注入 live-lookup）/ `batch_upsert` / `mark_applied`，藏在小接口后，状态翻转委托给 `SessionLifecycle.transition`。**诚实提醒**：这**不**减少 LOC——本质复杂度（4 轮重扫、双后端 ID 预生成、D1 100-参数分块、冲突删除遮蔽）不可约。赢的是**隔离可测**（用内存 overlay 测 classify、零 DB），不是行数。

**D5. 两个关切、按序；Phase 1 先发。** `SessionLifecycle` 与 `CommitPipeline` 触及不同文件、解决不同问题（合法性 vs 数据搬运）、风险不同。唯一耦合是那一行 `transition_status` 委托，故权威必须先于 pipeline 使用它而存在。先发 Phase 1、生产验证、再 Phase 2。

**D6. 调和 CONTEXT.md 漂移。** `CONTEXT.md` 描述了一个并不存在的 `ReportsRepo(conn, session_id).mark_committed()` / `.mark_failed()` 接口（实际是 `_db_reports.py` 的自由函数 + 一个无 mutator 的 `SessionsRepo`）。本次工作正是把 CONTEXT.md 与现实对齐、并加入 `SessionLifecycle` 词汇的时机。

## 后果（Consequences）

### 正面

- **单一合法性来源**——纯 `can_transition` 可对 4×4 状态矩阵零 DB 穷举单测。
- **修掉潜在数据损坏 bug**——`committed→failed` 与 `failed→committed` 变得无法表达。
- **局部性**——转换知识集中到一个模块，而非四个 SQL `WHERE`。
- **Phase 2：可测的 commit 子步**——`classify_and_resolve` 用内存 overlay 测、无真实 DB。

### 负面

- **Phase 1 改写 ~9 个写入点**——行为保持，但确是横跨 storage + rclone + CLI 的（小）改动。
- **Phase 2 可能增加 LOC**——接口 + dataclass 样板；回报是可测性而非体积。
- **核心写路径风险**——两阶段都触及 commit/rollback，行为保持至关重要。

### 风险

- **静默 no-op → 抛错** 若某调用方依赖那条无守卫的边，会改变控制流。由 D2 缓解（仅真正非法边抛错；幂等边仍返回 0）。
- **rclone staging session** 合法地做 `in_progress→committed` / `→failed`——图必须保留二者合法。
- session-id **进程全局**（`_db_session`）是另一个关切（候选 C），本 ADR 不处理。

## 实施路线图（Implementation Roadmap）

| 阶段 | IMP | 交付 | 延后 |
| --- | --- | --- | --- |
| Phase 1 | [IMP-ADR019-01](IMP-ADR019-01-session-lifecycle.md) | `SessionLifecycle` 模块 + 改写 ~9 个写入 + 纯转换测试 + 调和 CONTEXT.md | — |
| Phase 2 | [IMP-ADR019-02](IMP-ADR019-02-commit-pipeline.md) | 从 `_commit_session_bulk` 抽出 `CommitPipeline` 子步、状态翻转委托给 `SessionLifecycle` | 直到 Phase 1 在生产验证 |

## 不在范围（Out of Scope）

- session-id 进程全局（`set/get_active_session_id`）——候选 C。
- 非 bulk 的 `_commit_one_movie` 回退路径与 `_d1_retry_pending_cleanup`——留在编排里。
- 把状态*读取*批量迁到 `get_state`——无谓 churn。

## 状态日志（Status Log）

- 2026-05-29：Proposed（源自架构审查候选 E 的 grilling）。
