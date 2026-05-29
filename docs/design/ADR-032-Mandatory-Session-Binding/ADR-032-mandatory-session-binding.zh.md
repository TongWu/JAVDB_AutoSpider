# ADR-032: session_id 强制绑定 与 Repo 接口整合

| 字段       | 值                                                                    |
| ---------- | --------------------------------------------------------------------- |
| **状态**   | Accepted — Phase 1 已实现 2026-05-29；Phase 2 待做、Phase 3 延后 |
| **日期**   | 2026-05-29                                                            |
| **作者**   | Ted                                                                   |
| **关联**   | [ADR-005](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md)（本 ADR 补完 ADR-005 amendment-2 的目标；见 ADR-005 amendment-8） |

> 源自 2026-05-29 架构审查（候选 C）：[architecture-review-2026-05-29.zh.html](../architecture/architecture-review-2026-05-29.zh.html)。

## 背景（Context）

ADR-005 退役了 `db.py` 并引入 Repo 类。其 **amendment-2**（2026-05-17）把原 D5 的"(conn, session_id) 构造"改为 per-method `session_id`，并断言：

> "D5's actual goal (eliminate the `db_session._active` thread-local global) is satisfied either way, because `session_id` still flows explicitly through every method that needs it."

**这句断言在当前代码里不成立。** 进程全局 session id（`_SESSION_ID_SENTINEL` → `get_active_session_id()`）仍以隐式回退的形式存活于若干写入函数：

- `javdb/storage/db/_db_operations.py` —— ~10 个函数把 `session_id` 默认成 sentinel 并调 `_resolve_session_id(session_id)`（`:104,115,257,326,368,427,474,490,521,580`）。
- `javdb/storage/db/_db_history_write.py` —— `db_batch_update_last_visited`（`:570-575`）与 `db_batch_update_movie_actors`（`:652-657`）同理。

所以 `session_id` **并非**在每个写方法都显式流动——有些会无声回退到全局。忘记传的调用方会写出一行**未打标**的记录而非报错。ADR-005 自己的 Alternative-3 正点出了这点：*"The Repo class's `session_id=None` default lets 'forgot to pass session' become an implicit bug again."*

另外，接口是**双重**的：同一批写操作既能经 Repo 类、又能经从 `javdb/storage/db/__init__.py` `__all__` 重导出的模块级 `db_*` 函数到达。两个入口；测试打函数；调用方要学两套。

### 本 ADR 不是什么

这**不是**对 amendment-2 的反转。per-method `session_id`（amendment-2 的形态）**保留**。构造时绑定（原 D5 措辞）仍被**拒绝**——没有生产调用方依赖实例绑定的 session 状态，且单个 Repo 实例合法地服务多个 session（rollback sweep）。本 ADR **补完** amendment-2 的目标，不撤销它。

### 范围校正（来自核查）

`HistoryRepo` **不是**薄壳——它已拥有大量深 SQL（`search_movies`、`search_torrents`、`export_*`、`load_history_joined`、真正的 `batch_update_movie_actors`）。只有少数方法是薄委托（具体集合在实现时枚举）。`db_stage_history_write` / `db_commit_session_history` 已经**必填** `session_id`（无全局回退）。所以真正的摩擦比"Repo 是浅的"窄：它是 (a) operations + 2 个 batch 函数里残存的全局回退、与 (b) 双重公开接口。

## 决策（Decision）

补完 amendment-2 的目标、并整合为单一公开存储接口，分两阶段。

### 设计决策（Design Decisions）

**D1. 让 `session_id` 必填——移除全局回退（Phase 1）。** 从 operations 函数和两个 history-batch 函数移除 `_SESSION_ID_SENTINEL` 默认值；显式要求 `session_id`。忘记传的调用方现在报错，而非无声写出未打标行——这是预期的硬化（ADR-005 Alternative-3）。per-method 绑定保留。**"必填"必要但不充分：** 两个 history-batch 函数在 pending 写模式（唯一支持的模式）下若显式传 `session_id=None` 也会以 `ValueError` 拒绝——`None` session 仍会绕过 `PendingMovieHistoryWrites` staging、写出不可回滚的 live 行。所以 pending 模式下该值必须*存在且非 None*；`None` session 仅在（当前不存在的）非 pending 模式下才有意义。（审查硬化——见状态日志。）

**D2. 让 Repo 成为单一公开接口（Phase 2）。** 把剩余直接调 `db_*` 的调用方（~28 个非测试文件、~67 处调用）迁到 Repo 方法；停止从 `__all__` 重导出已迁移的 `db_*` 写/operations 函数；补几个缺失的薄 Repo 方法（如 `HistoryRepo.resume_finalizing_session`）。继续导出无状态原语（`get_db`、`*_DB_PATH`、`init_db`、`generate_session_id`、`generate_integer_id`）。

**D3. 必填即报错是刻意的行为变更。** 对已经传 `session_id` 的调用方（生产写路径——`history_manager.py:177`、`rclone/manager.py:1255`、经 `OperationsRepo` 的 dedup）这是行为保持。对任何依赖隐式全局的调用方，新失败模式（报错）正是想要的硬化；逐一审计并显式 thread（要核实的一处：`pikpak/bridge.py` 的 `db_append_pikpak_history`）。

**D4. Phase 3（删全局读取者）延后/gated。** 彻底移除 `set/get_active_session_id` 需要把 `session_id` thread 过那些编排读取者（在 Phase 1 枚举），含**跨进程**的 detail-runner MovieClaim DO 调用（`detail/runner.py:155`）与 subprocess worker。风险高，且在 D1 让写路径够不到全局之后边际价值低。本 ADR 不做；另行重启。

**D5. Phase 4（构造时绑定）被拒。** 它会反转 amendment-2、增加接口面（构造 *加* per-method override），且没有任何现存调用方需要。明确不做。

**D6. 部分 `db_*` 保持模块函数。** 无状态原语（`generate_session_id`、`get_db`、`init_db`、`*_DB_PATH`）不是领域操作，**不**强塞进 Repo。一次性 migration 工具（`migrations/tools/*`）与单次使用的 `align_*` 函数**排除**出 Phase-2 迁移——纯 churn、无可维护性回报。

## 后果（Consequences）

### 正面

- **补完 amendment-2**——`session_id` 真正显式流动；写路径够不到全局。
- **修掉潜在的未打标写入 bug**——"忘记传 session" 现在报错，而非写出不可回滚的行。
- **单一公开接口**——Repo 类；`db_*` 门面不再是第二个前门。
- **测试面改善**——契约测试迁到 Repo；边界测试禁止回退到裸 `db_*`。

### 负面

- **Phase 2 churn 大**——~28 文件、~67 处调用；一个大的机械 diff。
- **行为变更**——新必填的 `session_id` 把无声回退变成报错（刻意，但须先抓全所有依赖它的调用方）。

### 风险

- **漏掉某个依赖全局的调用方** → 调用时 `TypeError`。这是*想要*的失败模式，但须在上生产前找到（审计 + 测试）。首要嫌疑：`pikpak/bridge.py`。
- **repo↔db↔repo import shim**（`_db_history_write.py` import `history_repo`）必须在裁 `__init__` 导出后仍可用——它从子模块直接 import，故安全，但要验证。

## 实施路线图（Implementation Roadmap）

| 阶段 | IMP | 交付 | 延后 |
| --- | --- | --- | --- |
| Phase 1 | [IMP-ADR032-01](IMP-ADR032-01-mandatory-session-id.md) | 从 operations + 2 batch 函数移除 `_SESSION_ID_SENTINEL` 默认；在每个调用方 thread `session_id`；"无静默全局" 测试 | — |
| Phase 2 | [IMP-ADR032-02](IMP-ADR032-02-single-repo-interface.md) | 迁 ~67 处 `db_*` 调用到 Repo；裁 `__init__.__all__`；边界测试 | — |
| Phase 3 | （延后） | 删 `set/get_active_session_id` 读取者 | Gated——跨进程风险 |

## 不在范围（Out of Scope）

- 构造时 session 绑定（D5，拒绝）。
- 删 session 全局读取者（D4，延后）。
- migration 工具 + `align_*` 函数（D6，排除出迁移）。

## 状态日志（Status Log）

- 2026-05-29：Proposed（源自架构审查候选 C 的 grilling）。已作为指针记入 ADR-005 amendment-8。
- 2026-05-29：Phase 1 已实现并验证（[IMP-ADR032-01](IMP-ADR032-01-mandatory-session-id.md)）——`_db_operations` 写函数 + 两个 `_db_history_write` batch 函数的 `session_id` 现已必填，进程全局回退对它们不可达；调用方已 thread；新增"无静默全局"测试。Phase 2（单一 Repo 接口）与 Phase 3（删全局）尚待。
- 2026-05-29：Phase 1 审查硬化（CodeRabbit，PR #122）。把 `session_id` 设为*必填*阻止了意外省略，但在 pending 模式下显式传 `session_id=None` 仍会在 `db_batch_update_last_visited` / `db_batch_update_movie_actors` 中绕过 staging、写出未打标 live 行（已修订 D1）。两者现在对 pending+None 在任何 DB 访问前抛 `ValueError`；`test_mandatory_session_id.py` 新增回归测试。生产调用方（detail runner、legacy spider）始终在 active session 内运行，对它们行为保持；`test_history_manager.py` 中三个无 session 的单测已迁移到 session+commit 流程。
