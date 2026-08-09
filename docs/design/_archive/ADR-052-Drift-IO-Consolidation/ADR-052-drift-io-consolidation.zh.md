# ADR-052：把 drift-cell helper 与重复的 drift-log writer 整合进 `drift_io`

| 字段       | 值                                                                 |
| ---------- | ----------------------------------------------------------------- |
| **状态**   | Completed（2026-06-14）——已由 [IMP-ADR052-01](IMP-ADR052-01-drift-io-consolidation.md) 实现 |
| **日期**   | 2026-06-13                                                        |
| **作者**   | Ted                                                              |
| **关联**   | [BFR-016](../../BFR-016-Import-Time-DB-Path-Binding/BFR-016-import-time-db-path-binding.md)（import-时路径绑定——本 ADR 为 drift 日志兑现其教训）、[ADR-050](../ADR-050-Pending-Verify-Builder/ADR-050-pending-verify-record-builder.md)（共享 `append_jsonl_record` 调用方——协调导入路径）、[ADR-009](../ADR-009-D1-Drift-Classifier/ADR-009-d1-drift-classifier-and-diagnose.md)（拥有 drift-诊断**语义**——不动）、[ADR-047](../ADR-047-Dual-Backend-Drift-Reconciliation/ADR-047-dual-backend-drift-reconciliation.md)（拥有 reconcile **逻辑**——不碰这些 helper）、[ADR-042](../ADR-042-D1-Atomic-Commit-Boundaries/ADR-042-d1-atomic-commit-boundaries.md)（drift 日志是**诊断写入**） |

> 源自 2026-06-13 架构评审（候选 5 ——"整合 drift-cell helper 与重复的 drift-log writer"）：[architecture-review-2026-06-13.html](../../architecture/architecture-review-2026-06-13.html)。

## 背景（Context）

drift 检测与 drift 日志分散在四个文件,各自携带一份相同的底层机制副本。勘察确认重复属实且（大体）value 相同:

**三个 stdlib helper,被复制:**

- `_values_equal` —— `drift_diagnose.py:91` 与 `reconcile_d1_drift.py:249` **功能完全相同**（同样 int/int → `==`、`(int,float)` → `float()` 比较、否则 `str==str`；仅 `reconcile` docstring 更详,解释 `>2**53` 精度理由）。
- `_row_to_dict` —— `drift_diagnose.py:138` 与 `reconcile_d1_drift.py:203` **逐字节相同**。
- JSONL reader —— **三份两种行为**：`drift_diagnose._read_jsonl:110` 与 `pending_health._read_jsonl:67` 静默跳过坏行；`reconcile._read_drift_log:157` 对坏行记 `WARNING`。

**两个 drift-log writer——并不相同**,这是真正的 locality 缺陷:

| | `dual_connection._append_drift_record`（L206） | `lifecycle_helpers.append_jsonl_record`（L311） |
| --- | --- | --- |
| 路径绑定 | 模块级 `_DRIFT_LOG_PATH`（**import-时**从 `$REPORTS_DIR`） | **call-时**（`reports_dir=` → `$REPORTS_DIR` → `"reports"`） |
| 线程安全 | 持 `_DRIFT_LOG_LOCK` | 无锁 |
| pytest 守卫 | `_DRIFT_LOG_PATH` 解析到 tracked 路径时拒写 | 缺 `reports_dir` 与 `$REPORTS_DIR` 时拒写 |
| 错误日志 | `logger.error` | `logger.warning` |

两个 writer 带着**手动保持同步**的 pytest 守卫,注释互相引用（*"The sibling writer carries the same guard, so both drift-log writers are protected symmetrically"*）—— 一个被分进两文件的 fix-once-fixed-everywhere 缺陷。另外 `_DRIFT_LOG_PATH` 还被 **6 处用户日志串**引用（L451、L498、L750、L930、L1213、L1219）,不只在写入处——故它身兼写入目标*与*消息串,而其 **import-时绑定**正是 [BFR-016](../../BFR-016-Import-Time-DB-Path-Binding/BFR-016-import-time-db-path-binding.md) 记录的隐患。测试经**两个不同 seam** 隔离 drift 日志:3 个文件 monkeypatch `_DRIFT_LOG_PATH`;lifecycle 测试 set `$REPORTS_DIR` / 传 `reports_dir=`。

删除测试:删 helper 副本把 ~30 行集中一处（小赢——极少改）。删两个 writer 之一把锁、守卫、路径解析集中到一个模块,并把两个测试隔离 seam 收为一个——这才是候选名副其实之处。

## 决策（Decision）

抽出一个 stdlib-only 的 `drift_io` 模块,以 **call-时**路径绑定统一两个 writer,并退役 import-时 `_DRIFT_LOG_PATH` 常量。

### 设计决策（Design Decisions）

**D1. 新建 `javdb/storage/drift_io.py`,stdlib-only（零 `javdb` 依赖）。** 承载 `_values_equal`、`_row_to_dict`、`read_jsonl`、`append_jsonl_record` 与 `drift_log_path` resolver。stdlib-only 是刻意约束:`javdb/migrations/tools/reconcile_d1_drift.py` 已导入它,零-`javdb`-依赖的模块可从任意处（storage、migrations/tools）导入而无循环。

**D2. 整合三个 stdlib helper。** 移入 `_values_equal`（保留 `reconcile` 更详的精度 docstring）、`_row_to_dict`（逐字相同）、单一 `read_jsonl(path)`。`drift_diagnose`、`reconcile_d1_drift`、`pending_health` 删本地副本并从 `drift_io` 导入。

**D3. `read_jsonl` 对坏行记 warning。** Reconcile 行为胜出——信息更全,且坏 drift 行罕见、值得浮现。两个原本静默的调用方多一条无害 `WARNING`;无调用方丢失信息。

**D4. 单一 `append_jsonl_record(record, *, reports_dir=None, filename="d1_drift.jsonl")`,call-时绑定,携带锁与单一守卫。** 这是统一 writer:call-时解析 `reports_dir`（param → `$REPORTS_DIR` → `"reports"`）,持 `_DRIFT_LOG_LOCK`（drift 路径多线程;给 lifecycle 路径加锁无害且更安全）,带一个 pytest 守卫。名字保留 `append_jsonl_record`（记录并不全是 "drift"——pending-verify 指标共用该文件）,体现其通用角色。

**D5. 退役 import-时 `_DRIFT_LOG_PATH` 常量（BFR-016）。** 删 `dual_connection._DRIFT_LOG_PATH`、`_DRIFT_LOG_LOCK`、`_append_drift_record`。其 3 个调用方（L446、L485、L1207）改 `drift_io.append_jsonl_record(record)`;6 处日志串引用改新的 `drift_io.drift_log_path(reports_dir=None) -> str` resolver（同解析,call-时）。这把 drift 日志从 import-时绑定路径——BFR-016 记录的失败类——转为 call-时解析。

**D6. 单一测试隔离 seam:`$REPORTS_DIR`。** 3 个 monkeypatch `_DRIFT_LOG_PATH` 的测试（`test_d1_dual.py`、`test_batch_c_movie_history_id.py`、`test_system_state_repo.py`）改为 set `$REPORTS_DIR`（env）,与 lifecycle/`drift_diagnose` 测试既有隔离方式一致。之后只剩一个 seam,不再两个。

**D7. 不留 re-export shim——重定向 writer 调用方。** `append_jsonl_record` 从 `lifecycle_helpers` 移到 `drift_io`;其 6 个调用方（`drift_diagnose`、`sessions/commit`、`rollback/core`、`apps/cli/db/commit_session`）重定向导入。**与 [ADR-050](../ADR-050-Pending-Verify-Builder/ADR-050-pending-verify-record-builder.md) 协调**:ADR-050 的 pending-verify emitter 调 `append_jsonl_record`;后落地者重定向到 `drift_io`。若同时落地,builder 调 `drift_io.append_jsonl_record`。

## 后果（Consequences）

### 正面（Positive）

- **locality** —— 一个 `append_jsonl_record`（锁 + 守卫 + 路径解析）取代两个手动同步的 writer;一处 `_values_equal` 精度修复同时惠及 `reconcile` 与 `drift_diagnose`。
- **interface 收缩** —— 四个文件不再携带私有副本;drift 日志有一个 writer、一个隔离 seam。
- **移除一个 BFR-016 类隐患** —— drift 日志路径变为 call-时解析,不再 import-时绑定。
- **测试命中单一 seam** —— `$REPORTS_DIR` 隔离每个 drift-log writer;不再 `_DRIFT_LOG_PATH`-vs-`REPORTS_DIR` 分裂。
- **删除测试成立** —— 被删副本不重现;其行为集中在 `drift_io`。

### 负面（Negative）

- **`drift_io` 是低深度工具模块。** 接受:它是刻意的 stdlib-only 共享机制 sink,可无循环导入;其杠杆是跨四个调用方的 locality,非藏行为。
- **中等改动** —— 4 个 writer 调用方导入 + 3 个 helper 调用方导入 + 3 个测试隔离改写 + 6 处日志串引用替换,在一个 PR。缓解:纯重定位;现有 drift/dual/rollback 套件即回归门。

### 风险（Risks）

- **合并了一个微妙不同的 helper 副本,改了行为。** 缓解:勘察已验 `_values_equal`/`_row_to_dict` value 相同,唯一 JSONL 差异是静默-vs-warn（D3 选 warn,不丢信息）。
- **依赖 import-时 `$REPORTS_DIR` 的某次 drift 写入现在解析不同。** 缓解:call-时解析读同一 `$REPORTS_DIR`;唯一可观测变化是进程中途改 `$REPORTS_DIR` 现在会生效（BFR-016-正确行为）。测试在写入前 set `$REPORTS_DIR`。
- **ADR-050 导入路径冲突。** 由 D7 缓解——协调 `append_jsonl_record` 的移动。

## 实施路线图（Implementation Roadmap）

| 阶段 | IMP | 交付 | 推迟 |
| --- | --- | --- | --- |
| Phase 1（唯一） | [IMP-ADR052-01](IMP-ADR052-01-drift-io-consolidation.md) | `drift_io.py`（helper + `read_jsonl` + 统一 `append_jsonl_record` + `drift_log_path`）；退役 `_DRIFT_LOG_PATH`/`_DRIFT_LOG_LOCK`/`_append_drift_record`；重定向所有 helper/writer 调用方 + 6 处日志引用；3 个测试改 `$REPORTS_DIR`；CONTEXT.md 术语 | — |

### 明确的非目标（YAGNI）

- **不**改任何 drift 分类、诊断或对账逻辑（ADR-009 / ADR-047 拥有）。
- **不**改写入 drift 日志的内容或格式（诊断写入,ADR-042,不变）。
- **不**整合 pending-verify *builder*——那是 [ADR-050](../ADR-050-Pending-Verify-Builder/ADR-050-pending-verify-record-builder.md)（本 ADR 拥有其下的 *writer*）。

## 领域语言（CONTEXT.md 增补）

- **Drift IO（`javdb/storage/drift_io.py`）** —— 拥有共享 drift-日志机制的 stdlib-only 模块:cell 比较 helper（`_values_equal`、`_row_to_dict`）、JSONL reader（`read_jsonl`,对坏行 warning）、统一的 call-时绑定线程安全 writer（`append_jsonl_record`）、路径 resolver（`drift_log_path`）。零 `javdb` 依赖,故 `storage` 与 `migrations/tools` 均可导入。
- **Drift-log writer** —— 单一 `drift_io.append_jsonl_record(record, *, reports_dir=None, filename="d1_drift.jsonl")`,在进程锁下追加一行 JSON、带一个 pytest 守卫;取代原 `dual_connection._append_drift_record`（import-时路径）与 `lifecycle_helpers.append_jsonl_record`（call-时路径）。测试经 `$REPORTS_DIR` 隔离。

## 备选方案（Alternatives Considered）

- **仅 helper;不动两个 writer。** grilling 中否决:留下两个手动同步的守卫——候选名副其实的部分。
- **保留 `_DRIFT_LOG_PATH` 作已解析默认值;保留 import-时绑定。** 否决（D5）:保留 BFR-016 隐患与两个测试 seam。
- **在 `lifecycle_helpers` 保留 `append_jsonl_record` 作 re-export shim。** 否决（D7）:一个 writer 两个名;调用点集封闭且小。

## 参考（References）

- [BFR-016 — Import-Time DB Path Binding](../../BFR-016-Import-Time-DB-Path-Binding/BFR-016-import-time-db-path-binding.md)
- [ADR-050 — Pending Verify Record Builder](../ADR-050-Pending-Verify-Builder/ADR-050-pending-verify-record-builder.md)
- [ADR-009 — D1 Drift Classifier & Diagnose](../ADR-009-D1-Drift-Classifier/ADR-009-d1-drift-classifier-and-diagnose.md)
- [ADR-047 — Dual-Backend Drift Reconciliation](../ADR-047-Dual-Backend-Drift-Reconciliation/ADR-047-dual-backend-drift-reconciliation.md)
- 2026-06-13 架构评审：[architecture-review-2026-06-13.html](../../architecture/architecture-review-2026-06-13.html)

## 状态日志（Status Log）

- 2026-06-14：Completed 于 [IMP-ADR052-01](IMP-ADR052-01-drift-io-consolidation.md)。计划中的单一阶段已交付 `drift_io.py`、整合 JSONL/helper/writer 路径、退役 import-时 drift-log 绑定、把测试移到 `$REPORTS_DIR` seam，并更新 CONTEXT.md 术语。本 ADR 不再有后续 IMP。
- 2026-06-13：Proposed（源自 2026-06-13 架构评审候选 5）。勘察核实:`_values_equal`/`_row_to_dict` value 相同;JSONL reader 2 静默/1 warn;两个 writer 在路径绑定（import-时 `_DRIFT_LOG_PATH` vs call-时）、加锁、日志级别上分歧,且守卫手动同步。决定（grilling）:整合 helper + 以 **call-时**绑定统一 writer + 退役 `_DRIFT_LOG_PATH`（对齐 BFR-016）;`read_jsonl` warning;单一 `$REPORTS_DIR` 测试 seam;不留 shim。ADR-047（本地已实现）与 ADR-009（归档）拥有 drift 逻辑/语义,非这些 helper——无碰撞。IMP-ADR052-01 待办。
