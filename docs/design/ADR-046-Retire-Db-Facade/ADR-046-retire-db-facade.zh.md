# ADR-046：退役 `db_*` 门面 —— Repo 作为存储的深接缝（写操作绑定 session）

| 字段       | 值                                                                    |
| ---------- | --------------------------------------------------------------------- |
| **状态**   | Proposed —— Phase 1 执行见 IMP-ADR046-01                              |
| **日期**   | 2026-06-02                                                           |
| **作者**   | Ted                                                                  |
| **关联**   | [ADR-005](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.zh.md)（前序——拆掉了 `db.py` 巨石、引入 Repo 模式，但把 `db_*` 留作公开）、[ADR-019](../_archive/ADR-019-Session-Lifecycle-Authority/ADR-019-session-lifecycle-authority.zh.md)（用 transition 校验器把 session **commit 生命周期**做深）、[ADR-014](../_archive/ADR-014-Storage-Cli-Layering/ADR-014-storage-cli-layering.zh.md)（storage/CLI 分层） |

> 源自 2026-05-29 架构评审（候选 C ——“让 Repo 变深；退役 `db_*` 函数门面”）：[architecture-review-2026-05-29.zh.html](../architecture/architecture-review-2026-05-29.zh.html)。2026-06-02 的复验确认该候选仍然存活，并将其切分为下文的分阶段计划。

## 背景

[ADR-005](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.zh.md) 通过把 4,497 行的 `db.py` 巨石**重分布**到 `_` 前缀的 shell 模块（`javdb/storage/db/_db_history_write.py`、`_db_reports.py`、`_db_rollback.py`、`_db_operations.py` …）来删除它，并引入仓库类（`HistoryRepo`、`OperationsRepo` …）作为带类型的接口。但它**没有**把这些被搬迁的模块级函数改为私有。于是今天的现状是：

- **约 60 个公开 `db_*` 函数**散落在（约定私有的）`_db_*.py` 模块里，并经 `javdb/storage/db/__init__.py` 重新导出、或被直接 import。
- **Repo 是浅薄的转发壳。** `HistoryRepo`/`OperationsRepo`/`StatsRepo`/`SessionLifecycleRepo` 大多逐方法转发给 `db_*` 函数；于是**每个写操作都有两条公开入口**（repo 方法 + `db_*` 函数），而且 **~42 处生产调用 + ~40 个测试文件**直接绑定到 `db_*` 层、而非 repo。
- **`session_id` 从进程级全局态解析。** `javdb/storage/db/_db_session.py` 持有模块级全局 `_active_session_id_value`，外加 `_SESSION_ID_SENTINEL` / `_resolve_session_id()` 回退。spider 在每轮开始时设置它一次（`javdb/spider/app/run_service.py:592`），结束时清空。省略 `session_id` 的写操作会静默读取这个全局。

真正引发 bug 的就是这个 session 全局态。`HistoryRepo` 的 `batch_update_last_visited` 和 `batch_update_movie_actors` 在 spider 调用树深处调用 `get_active_session_id()`（`javdb/storage/history_manager.py:228`、`javdb/spider/detail/runner.py:1165`）。如果全局未设置、过期、或与当前工作单元不匹配，写操作会静默落到错误的 session —— 这正是 session/rollback bug 簇背后的失败模式（孤儿 pending 行、归属错乱的 history）。这是**环境态（ambient state）**穿过了一个本该显式的接缝。

[ADR-019](../_archive/ADR-019-Session-Lifecycle-Authority/ADR-019-session-lifecycle-authority.zh.md) 已经把其中一半做深了：session 的 **commit 生命周期**现在经过一个 `transition()` 校验器，使非法状态转移无法表达。剩下的泄漏是 **session *身份***，它仍然是环境态。

注：commit/rollback 编排（`javdb/storage/sessions/commit.py`、`javdb/storage/rollback/core.py`）虽然直接调用 `db_commit_session_history` / `db_rollback_session`，但**已经传入显式 `session_id`** —— 所以它不属于环境态问题，只属于更宽泛的“两条入口”重复问题。

## 决策

完成 ADR-005 的方向：**仓库是存储的深接缝，且 session 身份是显式的、而非环境态。** 分阶段执行，爆炸半径最小、bug 价值最高者先行。

### 设计决策

**D1. Repo 是唯一的深接口；`db_*` 是实现细节（分阶段退役）。** 终态是：调用方通过 repo 实例访问，`db_*` 函数变成真正私有（`_` 前缀）的、藏在 repo 背后的辅助函数，不再从 `__init__.py` 重新导出。这是一个多阶段迁移（见路线图），不是单个 PR。

**D2. `session_id` 是显式的，绝非环境态。** 可写 repo 可以携带它的 session：`HistoryRepo(*, db_path=None, session_id=None)`。**写解析顺序为：显式 `session_id` 参数 → 构造绑定的 `self._session_id` → 抛 `RuntimeError`**（带可操作信息，例如 *”HistoryRepo write requires a session_id (pass it, or bind via HistoryRepo(session_id=...))”*）。**进程级 `get_active_session_id()` 绝不被读取。** 已经接收显式 `session_id` 的方法（`commit_session`、`resume_finalizing_session`、`stage_*`、`pending_session_stats`）保持原样——单个 repo 仍可扫过多个 session（例如 `apps/cli/db/cleanup_stale_in_progress.py`）。因此 Phase 1 的具体修复点仅限于当前在无显式参数时读全局的两个写方法——`batch_update_last_visited` 与 `batch_update_movie_actors`——它们改为从构造绑定的 session 解析、缺失即抛错。

**D3. 读保持 session 无关；单类 + 运行时写守卫。** 按选定的接缝方案（单类 + 构造绑定 + 写守卫），读方法（`load_history`、`search_movies`、`export_*`、`check_torrent_in_history` …）在没有 session 的普通 `HistoryRepo()` 上照常工作。只有写方法断言已绑定 session。这让迁移保持小巧，并使该模式成为其它 repo 可复制的模板，同时仍然拔掉了环境态地雷。（独立的 `HistoryWriter` 类型与工厂 classmethod 方案见“考虑过的替代方案”。）

**D4. 全局 session 态机制仅在所有写方迁移完成后才移除。** `OperationsRepo` 和 `_db_operations.py` 也依赖 `get_active_session_id()`。Phase 1 移除 *History* 对全局的依赖；全局本身（`_active_session_id_value`、`_SESSION_ID_SENTINEL`、`_resolve_session_id`、`set/get_active_session_id`）只在 Operations 与 Stats 也绑定 session 后才删除（后续阶段）。在此之前它为未迁移的写方保留，并标记为 deprecated。

**D5. 与 ADR-005 / ADR-019 的关系 —— 完成，不重开。** ADR-005（已归档）拆掉巨石、引入 repo；ADR-019（已归档）把 *commit 生命周期*做深。ADR-046 是**两者都未走的下一步**：让 repo（而非函数层）成为接缝，让 session 身份显式化。它不 supersede 任何一者 —— 而是完成这条轨迹。会在 ADR-005 的 Status Log 加一条回指。

## 后果

### 正面

- **移除 History 写路径的环境-session 地雷** —— 存储核心里 bug 价值最高的改进。写操作再也不会静默落到错误 session；失败变成调用点处一声响亮、即时的 `RuntimeError`，而不是事后才发现的归属错乱行。
- **一条写路径，一个测试面。** 测试构造一个 session 绑定的 repo，而不是设置进程级全局再调模块函数；无需全局 setup/teardown。
- **确立可复制模板**（“session 绑定的深 repo”），供后续阶段的 Operations/Stats 套用。
- **反向删除测试成立** —— 一旦某写方完成迁移，其 `db_*` 写函数就没有剩余公开调用方，可改为私有而复杂度不重现。

### 负面

- **迁移触及真实调用点。** 仅 Phase 1 就要改写 ~6 个 History 写构造点（把 `session_id` 从全局/方法参数移到构造器）外加其测试。后续阶段更大。
- **过渡期双态。** 阶段之间，部分 repo 已绑定 session、部分仍读全局；全局会一直保留到最后一个写方迁移完（D4）。这是有意为之，且被路线图限定边界。

### 风险

- **某个依赖全局的写调用方现在会抛错。** 缓解：History 写构造点已被枚举（Phase 1 IMP），且在接缝处响亮抛错严格优于静默写错 session。读调用方不受影响（D3）。
- **某调用方绕过 repo 直接调 `db_*` 写函数。** Phase 1 尚未把 `db_*` 私有化，所以在 Phase 4 之前这仍可能；IMP 会 grep 直接的写函数调用方并把 History 那些改道走 repo。

## 实施路线图

| 阶段 | IMP | 交付 | 推迟 |
| --- | --- | --- | --- |
| **Phase 1 —— History 写接缝** | IMP-ADR046-01 | `HistoryRepo(*, db_path=None, session_id=None)`；写方法用 `self._session_id`、缺失即抛错；从 `batch_update_last_visited` / `batch_update_movie_actors` 移除 `get_active_session_id()` 回退；迁移 ~6 个 History 写点 + 2 个 CLI 写点；测试（无 session 写即抛错；读仍可无 session 工作） | 下面全部 |
| Phase 2 —— Operations/Stats 写接缝 | IMP-ADR046-02 | 给 `OperationsRepo`/`StatsRepo` 写操作绑定 `session_id`；**然后删除** `_db_session.py` 的全局 session 机制 | —— |
| Phase 3 —— 编排改道走 repo | IMP-ADR046-03 | `sessions/commit.py` + `rollback/core.py` 调用 repo，而非直接调 `db_commit_session_history` / `db_rollback_session` | —— |
| Phase 4 —— 私有化 `db_*` | IMP-ADR046-04 | 给 `db_*` 函数加 `_` 前缀、去掉 `__init__.py` 重新导出、迁移剩余直接调用方 + 测试 | —— |

### 明确的非目标（YAGNI）

- **不是一次性退役** —— 60 函数 / 42 调用 / 40 测试的迁移明确分阶段；没有单个无法 review 的大 PR。
- **Phase 1 不私有化 `db_*`** —— 函数保持公开，直到其调用方迁移完（Phase 4）。
- **Phase 1 不删除全局 session 态** —— Operations/Stats 仍在用（D4）；删除是 Phase 2。
- **不改读接口** —— 读本身没问题；只有读的 `db_*` 私有化（Phase 4）在范围内，其形状不动。
- **不重做 ADR-019 的 commit 生命周期** —— 它保留；ADR-046 只改变 `session_id` 如何抵达它。

## 领域语言（CONTEXT.md 新增）

- **Session-Bound Repo（session 绑定的 repo）** —— 在构造时携带 `session_id` 的仓库实例（`HistoryRepo(session_id=...)`）。写方法使用绑定的 session；未绑定 session 的写会抛错。取代环境式的 `get_active_session_id()` 回退。
- **Deep Storage Seam（存储深接缝）** —— 一条原则：仓库（而非模块级 `db_*` 函数层）是读写存储的唯一公开入口；`db_*` 变成藏在其背后的私有实现细节（按本 ADR 分阶段退役）。

## 考虑过的替代方案

- **(b) 独立的 `HistoryWriter(session_id)` 写句柄**（读在 `HistoryRepo`、写在 session 类型化的句柄上）—— *在 Phase 1* 否决：它在类型级别让“无 session 写”无法表达（更干净），但拆分了内聚类、放大了迁移。若运行时守卫被证明不够，可再议。
- **(c) 工厂 classmethod**（`HistoryRepo.for_session(sid)` / `.read_only()`）—— Phase 1 否决：折中方案，但增加了接口面却没有 (b) 的类型级保证；选定的 (a) 作模板更简单。
- **一次性全量退役**（60 个 `db_*` 全私有、~42 调用 + ~40 测试一个 PR）—— 否决：无法 review、爆炸半径大，且尾部大多是 bug 价值低的机械迁移。
- **维持现状**（环境式全局 session）—— 否决：全局是 session/rollback bug 的活跃来源；它正是深接缝应当显式化的那类环境态。

## 参考

- [ADR-005 —— db.py 退役与 Repo 模式](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.zh.md)
- [ADR-019 —— Session 生命周期权威](../_archive/ADR-019-Session-Lifecycle-Authority/ADR-019-session-lifecycle-authority.zh.md)
- [ADR-014 —— Storage/CLI 分层](../_archive/ADR-014-Storage-Cli-Layering/ADR-014-storage-cli-layering.zh.md)
- 2026-05-29 架构评审（候选 C）：[architecture-review-2026-05-29.zh.html](../architecture/architecture-review-2026-05-29.zh.html)

## 状态日志

- 2026-06-02：Proposed。源自 2026-05-29 架构评审（候选 C），经 2026-06-02 复验后切分：60 个公开 `db_*` 函数仍在、repo 是浅转发、`session_id` 从 `_db_session.py` 的进程级全局解析。选定接缝：单类 + 构造绑定 `session_id` + 运行时写守卫（D2/D3）。Phase 1（IMP-ADR046-01）仅覆盖 History 写接缝；全局 session 机制在 Phase 2（Operations/Stats 迁移后）删除。
- 2026-06-02：D2 在 IMP-ADR046-01 编写期澄清（design-feedback-loop）。读码后发现 `commit_session` / `resume_finalizing_session` / `stage_*` 已经接收**显式** `session_id`，且 `cleanup_stale_in_progress` 正依赖此特性用一个 repo 扫过多个 session。故修复不是“所有写都改成构造绑定”，而是解析顺序 **显式参数 → 构造绑定 → 抛错**；Phase 1 的具体改动仅限于在无显式参数时读进程级全局的两个方法（`batch_update_last_visited`、`batch_update_movie_actors`）。D2 据此改写。
