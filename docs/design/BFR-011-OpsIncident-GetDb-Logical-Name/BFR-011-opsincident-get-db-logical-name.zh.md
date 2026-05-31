# BFR-011: OpsIncident 持久化用逻辑名调用 get_db()，静默降级到 JSONL

**Status**: Fixed
**Date**: 2026-05-31
**Severity**: High
**Affected**: `javdb/ops/diagnosis/persistence.py`(`persist_incident`)、`apps/api/routers/diagnostics.py`(`_list_ops_incident_records`、`_get_ops_incident_record`)
**Related**: [ADR-026](../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.zh.md)(AI 运维诊断——`OpsIncidents` 表的归属）、[ADR-035](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.zh.md)(站点契约哨兵——其 IMP 计划带有相同的错误调用形式）、`CLAUDE.md` →「Database Access」（传播了错误形式的示例）

---

## 症状

`OpsIncidents` 行通过诊断写入路径从未真正落到 canonical 的 D1 存储。每次调用
`persist_incident()` 都静默走了 JSONL 兜底分支——事件被写入
`reports/ops/ops_incidents.jsonl`,`persistence_status="d1_failed_jsonl_written"`
而非 `"d1_written"`;`/api/diag/ops-incidents` 的 list/detail 端点也会抛错而非返回数据。

该故障在正常运行中不可见,原因是:

- `persist_incident()` 用一个宽泛的 `try/except Exception` 包住 DB 写入,其唯一可观察
  的效果是一行 `WARNING` 日志加一次兜底写入——没有错误向调用方传播。
- 原本"覆盖"该路径的单测(`test_persist_incident_uses_reports_logical_db`)用一个假
  `get_db` 打桩——它接受*任意*字符串并返回连接,因此断言的是错误契约
  (`seen == ["reports"]`),从未真正调用过真实的 `get_db`。它掩盖了 bug 而非捕获它。

在 `STORAGE_BACKEND=sqlite` 下实测复现:

```text
sqlite3.DatabaseError: Database file reports is not a valid SQLite file.
This usually means Git LFS did not pull the real file.
```

## 根因

`get_db()` 接收的是**文件路径**(`HISTORY_DB_PATH`、`REPORTS_DB_PATH`、
`OPERATIONS_DB_PATH`),不是逻辑名。其后端路由
(`javdb/storage/db/_db_connection.py`)用这些路径作为 D1 逻辑名映射
`_DB_PATH_TO_LOGICAL_NAME` 的键(例如 `reports/reports.db → reports`)。因此传入裸
字符串 `"reports"` 在两种后端下都是错的:

- **sqlite** —— `_open_sqlite_connection("reports")` 试图把 `reports/` 目录当作
  SQLite 文件打开,抛 `sqlite3.DatabaseError`。
- **d1** —— `_logical_name_for("reports")` 在 `_DB_PATH_TO_LOGICAL_NAME`(以*路径*为
  键,而非名字)中找不到条目,抛 `ValueError`。

诊断代码 `javdb/ops/diagnosis/persistence.py:29` 写的是
`with get_db("reports") as conn:`。上下文管理器内部抛出的异常被
`persist_incident()` 的宽泛 `except` 吞掉,于是函数总是降级到 JSONL。同样的错误形式
还被复制进了 `apps/api/routers/diagnostics.py` 的两个 API helper
(`_list_ops_incident_records`、`_get_ops_incident_record`)。

**为什么是设计错了而不只是哪里坏了:** `get_db()` 的签名把一个字符串参数复用为既像
"路径"又像"逻辑名"的东西,而 D1 路由内部又确实接受逻辑名——所以在调用处看
字符串 `"reports"` 显得合理。错误只在运行时、在连接工厂深处才被发现,而这里又被
(a) 宽泛的 catch-and-fallback 和 (b) 一个从不调用真实路由的打桩测试进一步掩盖。这是
一个**会传播的文档 bug**:`CLAUDE.md` 的「Database Access」示例本身就写着
`conn = get_db('history')`,而四份活跃的 IMP 计划(ADR-033/035/036/037)复现了相同
形式——所以工程师照抄到的就是这个错误调用形状。

## 修复

引入 `REPORTS_DB_PATH`,在全部三处调用点传入**路径**(与 `diagnostics.py` 中已正确的
`get_db(OPERATIONS_DB_PATH)` 用法一致):

- `javdb/ops/diagnosis/persistence.py` —— `get_db("reports")` →
  `get_db(REPORTS_DB_PATH)`(并补充 import)。
- `apps/api/routers/diagnostics.py` —— `_list_ops_incident_records` 与
  `_get_ops_incident_record` 都改为 `get_db(REPORTS_DB_PATH)`(并补充 import)。

测试(`tests/unit/test_ops_incident_repo.py`):

- 将固化 bug 的旧测试重写为
  `test_persist_incident_calls_get_db_with_reports_path`,现在断言 `get_db` 是用
  `REPORTS_DB_PATH`(路径)调用,而非逻辑名。
- 新增 `test_persist_incident_writes_to_reports_db_without_jsonl_fallback`,一个真实
  后端的回归测试:把 `persist_incident` 指向一个临时 reports DB,在 sqlite 下走**真实**
  的 `get_db`(不打桩),断言行确实落入 reports DB 且
  `persistence_status="d1_written"`,并断言 JSONL 兜底文件*未*被创建。已验证该测试在
  修复前的代码上失败(复现 `sqlite3.DatabaseError`),修复后通过。

文档:

- 修正 `CLAUDE.md`「Database Access」示例,改用
  `with get_db(HISTORY_DB_PATH) as conn:`,并加注说明 `get_db()` 接收路径(而非逻辑名)
  且是上下文管理器。

## 副作用

功能上无副作用。修复后新建的事件按设计正常持久化到 reports DB。在故障窗口期内写入
`ops_incidents.jsonl` 的历史事件**不会**被自动回填——见后续工作。

## 后续工作

- [ ] 回填(可选):若仍有价值,将 `reports/ops/ops_incidents.jsonl` 中以
      `persistence_status="d1_failed_jsonl_written"` 写入的行重放进 reports/D1 的
      `OpsIncidents` 表。
- [x] 已修正活跃 IMP 计划中相同的错误形式 `get_db("…")` / `get_db('…')`(逻辑名),避免
      工程师继续照抄:
      `IMP-ADR035-01-piggyback-and-gate.md`(哨兵持久化——正是本 BFR 报告人引用为
      "参考"的形式,而该计划本身就是错的)、
      `IMP-ADR033-01-acquisition-outcome.md`(`get_db('operations')`——散文、表格、
      Task 4 标题与代码块)、以及 `IMP-ADR036-01-event-spine.md` 中遗留的一处过期写法
      (该文档其余部分在实现时已修正)。**有意保留** `IMP-ADR037-01-harness-core.md`:
      它已实现(发布的 `tests/harness/pipeline_harness.py` 用的是正确形式),且其计划清单
      已带有明确的"get_db() 取路径而非逻辑名"更正说明,并注明发布文件为 canonical——
      去改一个自带更正、已被取代的清单只是徒增改动。
- [x] 已加护栏:`get_db()` / `get_local_sqlite_db()` 现在通过 `_reject_logical_name()`
      拒绝裸逻辑名(`"history"`/`"reports"`/`"operations"`),并给出指向 `*_DB_PATH`
      常量的清晰错误——把原先的静默降级变成快速失败。由
      `tests/unit/test_db.py::TestGetDbLogicalNameGuard` 覆盖。
- [ ] 另行处理(同属 `get_db` 路径混淆家族,但是另一个 bug):发布的
      `tests/harness/pipeline_harness.py` 中 `events()` / `acquisition_outcomes()`
      读取 `PipelineEvent` / `AcquisitionOutcome`(它们位于 reports / operations DB),
      却用**无参**的 `get_db()`——其默认值是 *history* DB,导致这些 harness 视图静默返回
      `[]`。上面新增的护栏**抓不到**这一类(无参默认是合法路径,只是用错了库)。本 BFR
      范围之外,另起修复。
