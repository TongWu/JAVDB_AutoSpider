# ADR-005: db.py 彻底退役 + Repo 类抽象 + Audit Mode 退役

**状态**: 已接受，但**启动前置阻塞于 [ADR-006](ADR-006-pending-mode-default-rollout.md)**
**日期**: 2026-05-16
**决策者**: 架构深化第二轮
**前置**: [ADR-006](ADR-006-pending-mode-default-rollout.md) — 必须先把 Pending Mode 默认推到 100% + 重设计 auto-fallback，本 ADR 才能执行 D10 gate
**后继关系**: [ADR-001](ADR-001-split-db-module.md) — 完成其未交付的 Phase 3，并修正其"按读/写拆分"的过细决策

## 修订记录 (Amendments)

- **2026-05-17 amendment 1**：本 ADR 接受后，[ADR-007](ADR-007-monorepo-restructure-2026-05.md) 对 Python namespace 做了重组（`packages/python/javdb_*` → 顶层 `javdb/`）。本 ADR 实施顺序里**尚未合并的 PR**，在 ADR-007 Phase 1 落地后必须按新路径操作：

  | 本 ADR 引用的路径 | ADR-007 Phase 1 后 |
  |---|---|
  | `packages/python/javdb_platform/db.py` | （ADR-005 D1 删除；内部模块在 ADR-007 中已迁至 `javdb/storage/db/`） |
  | `packages/python/javdb_platform/db_layer/history_repo.py` | `javdb/storage/repos/history_repo.py` |
  | `packages/python/javdb_platform/db_layer/operations_repo.py` | `javdb/storage/repos/operations_repo.py` |
  | `packages/python/javdb_platform/db_layer/reports_repo.py`（D1 新建） | `javdb/storage/repos/reports_repo.py` |
  | `packages/python/javdb_platform/db_layer/stats_repo.py`（D1 新建） | `javdb/storage/repos/stats_repo.py` |
  | `packages/python/javdb_platform/db_session.py` | `javdb/storage/db/db_session.py` |
  | `packages/python/javdb_platform/db_history_write.py` 等 | `javdb/storage/db/db_history_write.py` 等 |
  | `packages/python/javdb_spider/` 下的 caller | `javdb/spider/` |
  | `apps/cli/`、`apps/api/`、`scripts/` 下的 caller | `apps/cli/<subdir>/`、`apps/api/`、`apps/cli/`（按 ADR-007 Phase 2） |
  | `packages/python/javdb_migrations/tools/` 下的 caller | `javdb/migrations/tools/` |

  纯路径重命名——Repo class 语义、D1–D10 gate 逻辑、对 ADR-006 的 30 天 bake 依赖都不变。ADR-007 的 deletion manifest 保证未完成的工作不再引用 legacy 路径。

- **2026-05-17 amendment 2**：**Repo 签名模式 + 命名对齐已落地代码。** PR-1 启动前核查 `javdb/storage/repos/`，发现两套合法的 Repo 模式已共存，各自匹配自己的访问形态：

  | 类 | 文件 | 签名 | 为什么是这个形态 |
  |---|---|---|---|
  | `SessionsRepo` | `javdb/storage/repos/sessions_repo.py` | `__init__(conn)`；方法层接 `session_id` | API 层**读** surface——FastAPI 请求上下文持有 open conn；读操作短、无事务边界 |
  | `SystemStateRepo` | `javdb/storage/repos/system_state_repo.py` | `__init__(conn)`；方法层接 `key` | 同上——单次 API 调用内的 KV 读写 |

  PR-1 三个新 Repo（`HistoryRepo` / `OperationsRepo` / `StatsRepo`）要包的是 `javdb/storage/db/*.py` 下的**写域函数族**（`db_load_history` / `db_stage_history_write` / `db_save_spider_stats` 等）——这些函数都接 `db_path: Optional[str] = None`，自己 open conn 以保证事务安全，**不**接 caller 提供的 conn。强行套用 SessionsRepo 模式会要么 (a) 把 SQL inline 进 Repo 体（不再是"thin delegate"，bake 干扰风险升高），要么 (b) 打破每个函数族内部的 `with get_db(...) as conn:` 事务边界（正确性风险）。

  决策：

  1. **写域 Repo 签名**：`HistoryRepo` / `OperationsRepo` / `StatsRepo` 用 `__init__(*, db_path: Optional[str] = None)`；方法层接 `session_id`。Repo 不持有 per-call 状态——它是已有函数族的类型化 surface。D5 原"(conn, session_id=None) 构造"措辞对这三个类失效；D5 的真实目标（消灭 `db_session._active` 线程本地全局态）仍然满足，因为每个需要 session 的方法都显式接 `session_id`。
  2. **`ReportsRepo` 由已落地的 `SessionsRepo` 覆盖**——D6 的命名是草稿，实现按其唯一一张表（`ReportSessions`）命名更准确。为同一职责再加一个类会造成测试面重叠 + caller 困惑。**无需重命名**：`SessionsRepo` 就是 D6 里的 ReportsRepo。

  D6 的"四类"计划变为"三个新写域 Repo 类 + 复用 SessionsRepo"。其余 D 级决策不变。

---

## D10 Gate 核查结果（2026-05-16）

ADR-005 起草后立即跑了 D10 Audit Mode 退役安全核查，**两项失败**：

| Gate 项 | 状态 | 证据 |
|---|---|---|
| #1 近 30 天 `WriteMode='audit'` 计数 = 0 | ❌ FAIL | 近 30 天 audit=54 / pending=13；全时段 audit=354 / pending=13 |
| #2 无孤儿审计行 | ✅ PASS | `MovieHistoryAudit`=9, `TorrentHistoryAudit`=3，0 个绑定 committed session |
| #3 workflow 7 天前已移除 audit 选项 | ❌ FAIL | 3 个 workflow 仍把 `audit` 列为 `write_mode_override` 合法值；`DailyIngestion.yml` L1093 有 auto-fallback 到 audit 的活机制 |

同时发现的**文档失实**：CONTEXT.md / CLAUDE.md / ADR-001 docstring 声称 "Pending Mode is default"，但 `db_session.py:188` 的代码 fallback 与 SQLite schema 的 `WriteMode TEXT DEFAULT 'audit'` 都说明**实际默认仍是 audit**——这是愿景而非事实。

**结论**：D2(c) "完全退役 Audit Mode" 当前不可执行，因为 Audit Mode 是 80% session 的实际运行模式 + 是 Pending Mode 失败时的 live safety net。在 ADR-006 落地前，本 ADR 的 PR-1 不可启动。

---

---

## 背景 (Context)

ADR-001 计划把 6,370 行的 `db.py` 拆为 9 个按功能划分的模块（`db_connection.py` / `db_session.py` / `db_history_read.py` / `db_history_write.py` / `db_reports.py` / `db_stats.py` / `db_operations.py` / `db_rollback.py` / `db_migrations.py`），分 3 阶段执行：Phase 1 抽模块 → Phase 2 迁移 importer → Phase 3 删 db.py 门面 + 消除全局状态。

### 实际现状（架构深化第二轮探测）

- `db.py` 仍有 **5,298 行 + 131 个 `def/class`**，**承载实质实现**：
  - `db_upsert_history` (line 2373) — Audit Mode 写入路径
  - `_audit_*` 辅助 (line 2178–2361)
  - 全部 schema migration (`_migrate_v5_to_v6` / `_migrate_single_to_split` / `init_db` / `_ensure_*_columns`)
  - 全部 Operations 域助手（与几乎空壳的 `db_operations.py` 并存）
  - Connection 池 / Session ID 生成（与 `db_connection.py` / `db_session.py` 并存）
- 新抽出的 `db_history_read.py` (371 行) / `db_history_write.py` (238 行) 多数函数**就是转发到 db.py**，例如 `db_history_write.db_upsert_history(*args, **kwargs)` 一行代理。
- 第三层抽象 `db_layer/history_repo.py` 已存在，但只有 4 个模块级函数（无 `HistoryRepo` 类——CLAUDE.md 示例代码写的是空头支票）。
- `db.py` 反向 import `apps.api.parsers.common` (line 45–50)，破坏 monorepo 分层。
- `db_session._active` 全局状态仍是写入路径隐式契约。
- Audit Mode 在 CONTEXT.md 中标"计划 2026-08-13 sunset"，但代码仍承载主写入路径；kill switch `JAVDB_AUDIT_WRITES_DISABLED` 已存在但未默认启用。

### 问题

1. **三层抽象 forward 来 forward 去**，违反 ADR-001 自己的 **locality** 原则——理解一次 history 写入仍需在 3 个文件间跳。
2. ADR-001 关于"读/写拆分"的决策 #1 经实测**未带来收益**：所有真实 caller (`history_manager.py`、`db_rollback.py`、CLI tools) 同时跨用读和写两个 seam——这不是 seam，是单一使用方式上的多余切口。
3. ADR-001 Phase 3 计划的"消除全局状态"未启动，写入接口的 invariant（"线程已设置 active session"）藏在签名之外。
4. Audit Mode 与 Pending Mode 双轨长期共存意味着 `db_history_write.py` 必须同时支持两条路径——增加 surface area 而无业务收益（默认就是 pending）。
5. Migrations 全在 db.py 里没拆。

---

## 决策 (Decision)

下面 11 项作为一组生效，不可拆分挑选——其中任何一项单独存在都会让另一些项失去意义。

### D1：清空 db.py 内的全部实质实现

四个域（History / Operations / Migrations / Connection+Session 工具）的代码一并迁出。`db.py` 最终删除，不保留任何门面。

### D2：完全退役 Audit Mode（含读写）

- 删除 `db_upsert_history` audit 路径与所有 `_audit_*` 辅助；
- 删除 `MovieHistoryAudit` / `TorrentHistoryAudit` 两张表（migration v14）；
- 删除 `db_rollback` 中"读 audit 表 → 恢复"的分支；
- workflow 输入 `write_mode_override` 移除选项 `audit`，仅保留 `pending`；
- 删除环境变量 `JAVDB_HISTORY_WRITE_MODE`、`JAVDB_AUDIT_WRITES_DISABLED`；
- `ReportSessions.WriteMode` 列保留（兼容历史行），新写入始终为 `pending`。

### D3：引入 Repo 类风格

不再用模块级函数族暴露 DB 访问入口，改用类：

```python
class HistoryRepo:
    def __init__(self, conn, session_id: str | None = None): ...
    def stage_movie(self, href: str, ...): ...
    def stage_torrent(self, ...): ...
    def commit(self): ...
    def rollback(self): ...
    def load_history(self, phase: int | None = None): ...
    def load_history_snapshot(self): ...
    def check_torrent_in_history(self, href: str, kind: str): ...
```

写入方法要求构造时传入非空 `session_id`；读取方法允许 `session_id=None`。

### D4：URL/解析工具下沉

`apps.api.parsers.common` 中被 `db.py` 使用的 3 个函数（`movie_href_lookup_values`、`javdb_absolute_url`、`absolutize_supporting_actors_json`）下沉到 `packages/python/javdb_core/url_utils.py`。`apps.api.parsers.common` 改为 re-export 这些工具，逐步迁移 caller 后删除 re-export。

确立**分层不变量**：`packages/**` 不得 import `apps/**`（CI lint 强制）。

### D5：`Repo(conn, session_id)` 构造签名

`session_id` 绑定到构造时刻；不再有 `db_session._active` 全局；测试用 `HistoryRepo(test_conn, "test-session-id")` 显式构造即可，无需 patch 全局。

### D6：四个 Repo 同步转类

| Repo | 物理位置 | 状态 |
|---|---|---|
| `HistoryRepo` | `packages/python/javdb_platform/db_layer/history_repo.py` | 升级（已有文件，添加类） |
| `OperationsRepo` | `packages/python/javdb_platform/db_layer/operations_repo.py` | 升级（已有文件，添加类） |
| `ReportsRepo` | `packages/python/javdb_platform/db_layer/reports_repo.py` | 新建 |
| `StatsRepo` | `packages/python/javdb_platform/db_layer/stats_repo.py` | 新建 |

所有 Repo 共享 `BaseRepo(conn, session_id=None)` 构造协议；写入方法以 `_require_session()` 守卫确保 `session_id` 非空。

四个 Repo 之外，新增 `RollbackCoordinator(conn).rollback_session(session_id)` 协调器，负责按顺序调用各 Repo 的 `.rollback()`（替代 `db_rollback.db_rollback_session()`）。

### D7：Migrations 一文件一版本

按 schema 版本切片：

```
packages/python/javdb_migrations/
├── runner.py                       # dispatcher: init_db(), detect_schema_version()
├── versions/
│   ├── __init__.py
│   ├── v6_split_dbs.py             # def migrate(conn) -> None
│   ├── v7_actor_columns.py
│   ├── v8_rollback_columns.py
│   ├── v9_to_v13_*.py              # 现有版本
│   └── v14_drop_audit_tables.py    # 新增：drop MovieHistoryAudit / TorrentHistoryAudit
└── tools/                          # ad-hoc 维护脚本，结构不变
    ├── cleanup_history_priorities.py
    └── ...
```

`packages/python/javdb_platform/db_migrations.py` 删除。

### D8：迁移接口是函数不是类

每个 `v{N}_*.py` 暴露 `def migrate(conn) -> None`。Migration 是一次性 schema 变更，无状态，无需类模板；Repo 类是有状态的领域操作。两者形式可以不同。

### D9：分层 PR rollout（零破坏）

```
PR-1  在 db_layer/ 下建立 4 个新 Repo 类（与现有函数族并存，零 caller 改动）
PR-2  db.py 内部转调 Repo 类（双写并行：caller 用 db.py 一切照旧，但底层走新代码）
PR-3a 迁移 packages/python/javdb_spider/ 与 javdb_platform/history_manager.py 的 caller
PR-3b 迁移 packages/python/javdb_ingestion/ 与 javdb_integrations/ 的 caller
PR-3c 迁移 apps/cli/、apps/api/、scripts/、packages/python/javdb_migrations/tools/ 的 caller
PR-4  在 ReportSessions 中确认全部 in_progress session 退场 → 启用 v14 migration drop audit 表
      → 删除 db.py / db_history_read.py / db_history_write.py / db_session.py 中的 audit 代码
PR-5  删除 db.py、删除 db_history_read.py / db_history_write.py / db_stats.py 等 ADR-001 抽出的
      空壳模块；删除 db_session 全局；删除 JAVDB_HISTORY_WRITE_MODE 环境变量
PR-6  下沉 apps.api.parsers.common 的 3 个工具到 packages/python/javdb_core/url_utils.py，
      启用 CI lint 强制"packages 不依赖 apps"
PR-7  按 D7 重排 migrations 为版本文件，删 db_migrations.py
```

每个 PR 独立可回滚。PR-1/PR-2 不引入行为变更，可独立合并；PR-3a/b/c 渐进。

### D10：Audit Mode 退役安全 gate

PR-4 启动前必须确认：

1. `ReportSessions` 最近 30 天 `WriteMode='audit'` 计数为 0；
2. `MovieHistoryAudit` / `TorrentHistoryAudit` 中无 `committed` session 的孤儿审计（或已通过 `StaleSessionCleanup` 清完）；
3. 三个 workflow（`DailyIngestion` / `AdHocIngestion` / `TestIngestion`）的 `write_mode_override` input 已经移除 `audit` 选项至少 7 天。

如核查失败，先 set `JAVDB_AUDIT_WRITES_DISABLED=1` org-wide bake 1–2 周再继续。

### D11：测试策略——替换不叠加

- 现有针对 `db_history_write.py` / `db_session.py` 全局的 unit test 在新 Repo 接口完成后**删除**（per DEEPENING.md "Replace, don't layer"）；
- 新 unit test 写在 Repo 类接口上：`HistoryRepo(in_memory_conn, "session-x").stage_movie(...)` → assert observable outcome；
- 集成测试（跨 Repo 协调，如 `RollbackCoordinator.rollback_session`）保留；
- 测试不 mock `get_active_session_id()`——构造时传即可。

---

## 备选方案 (Alternatives Considered)

### 备选 A：保留 ADR-001 的 9 模块函数族，只完成 Phase 3 删 db.py

**否决原因**：经 caller 走查 (`history_manager.py` 50 行内同时 import `db_history_read` + `db_history_write`)，"读/写分离"的 seam 在真实使用模式下根本没被独立用过——LANGUAGE.md "two adapters = real seam" 原则下这两个文件不构成真 seam。继续保留只增加 import 路径与文件计数。

### 备选 B：只把 `HistoryRepo` 转类，其他保持函数族

**否决原因**：单 Repo 不构成模式（"one Repo = hypothetical seam"）。要么所有写入域统一为 Repo 类形式以获取"测试时不需要 mock 全局状态"的好处，要么完全保持函数族——混用会让新 contributor 反复猜测。

### 备选 C：保留 Audit Mode 写入路径作为 D1/SQLite 间漂移诊断的回退

**否决原因**：Audit Mode 解决的是 ADR-001 之前的"无 Pending 表的回滚"问题；Pending Mode 已完整覆盖。Dual Mode 漂移诊断由 `dual_connection.DualConnection.drift_jsonl` 负责，与 Audit Mode 无关。保留双轨意味着 `HistoryRepo.stage_movie` 永远要 `if write_mode == 'audit': ...`，破坏 D5 的简单签名。

### 备选 D：单 PR 一次性完成

**否决原因**：~4,000–5,000 行迁移 + 数十 caller 改动 + 测试重写，单 PR 评审风险过高，回滚粒度过粗。D9 的 7-PR rollout 是必要代价。

### 备选 E：把 db.py 的 Connection/Session 工具留在 db.py 当门面

**否决原因**：D1 的目标就是"db.py 真正消失"。留任何工具都意味着 `db.py` 继续作为"逃生通道"——新代码再次倾向于 `from packages.python.javdb_platform.db import ...` 而非 `from db_connection import ...`。要彻底就要彻底。

---

## 后果 (Consequences)

### 正面影响

1. **Locality 真正落地**——理解一次 history 写入只需读 `HistoryRepo`，不必跨 3 个文件
2. **Interface 就是测试面**——`HistoryRepo(test_conn, "x").stage_movie(...)` 直接调，无须 patch 全局
3. **Audit Mode 彻底退役** —— 删除 ~1,200 行旧代码（audit 写入 + audit helpers + audit rollback 分支 + audit 表）
4. **分层不变量** —— `packages` 单向依赖 `apps`，避免新代码无意中加反向 import
5. **ADR-001 真正完成** —— Phase 3 + 修正 ADR-001 决策 #1（读/写拆分）

### 负面影响

1. **7 个 PR 的协调成本**——每个独立但需要顺序合并
2. **caller 改动面广**——估算 50+ 文件需要换 import 与调用形式
3. **测试重写**——`tests/unit/test_workflow_resolve_write_mode.py` 类的全局 mock 测试整批删除并重写
4. **CLAUDE.md 中给出的 `HistoryRepo` 示例代码现在是契约**——必须实现，不能再"空头支票"

### 风险

1. **PR-4 之前 audit 仍能写入** → 升级路径上 in-flight audit session 残留
   - **缓解**：D10 三项 gate；先 bake `JAVDB_AUDIT_WRITES_DISABLED=1` 再删码
2. **`db.py` 删除时漏掉某个隐式 caller**（外部脚本、用户私有自动化）
   - **缓解**：PR-5 之前先在 `db.py` 顶端加 `DeprecationWarning("use HistoryRepo")` 一个 release cycle，看日志是否还有命中
3. **Repo 类的 `session_id=None` 默认让"忘传 session"重新变成隐式 bug**
   - **缓解**：所有 stage/commit/rollback 方法首行 `self._require_session()` 抛 `RuntimeError`，**接口契约可执行**
4. **`apps.api.parsers.common` re-export 期间仍可能被外部 import**
   - **缓解**：PR-6 中 re-export 保留一个 release cycle，加 `DeprecationWarning`

---

## 相关决策 (Related Decisions)

- **ADR-001**（部分修正 + 完成）：本 ADR 是 ADR-001 Phase 3 的实际交付，并修正其"按读/写拆分 History 模块"的决策。
- **ADR-002 / ADR-003 / ADR-004**：Worker 侧改造，与本 ADR 无直接耦合。

---

## 参考资料 (References)

- [CONTEXT.md](../../../CONTEXT.md) — 领域术语词汇表（已随本 ADR 更新 Repo / Layering Invariant / Audit Mode 退役状态）
- [LANGUAGE.md](https://example.invalid/skill/improve-codebase-architecture/LANGUAGE.md) — 架构语言（Module / Interface / Seam / Adapter / Depth）
- [DEEPENING.md](https://example.invalid/skill/improve-codebase-architecture/DEEPENING.md) — 测试策略 "Replace, don't layer"
- ADR-001 经验教训 §4：模块边界应基于职责而非物理结构 —— 本 ADR 进一步修正：**也要基于真实使用模式而非假设的使用模式**

---

## 附录 A：迁移前后接口对照

```python
# 之前
from packages.python.javdb_platform.db_session import set_active_session_id
from packages.python.javdb_platform.db_history_write import db_stage_history_write
set_active_session_id("20260516T093000.000000Z-0001-0001")
db_stage_history_write(conn, movie_data)  # 隐式依赖 thread-local session

# 之后
from packages.python.javdb_platform.db_layer.history_repo import HistoryRepo
repo = HistoryRepo(conn, "20260516T093000.000000Z-0001-0001")
repo.stage_movie(href="/movies/abc123", ...)  # session 显式
repo.commit()
```

## 附录 B：v14 migration 草图

```python
# packages/python/javdb_migrations/versions/v14_drop_audit_tables.py
def migrate(conn) -> None:
    """v14: drop MovieHistoryAudit / TorrentHistoryAudit per ADR-005."""
    conn.execute("DROP TABLE IF EXISTS MovieHistoryAudit")
    conn.execute("DROP TABLE IF EXISTS TorrentHistoryAudit")
```
