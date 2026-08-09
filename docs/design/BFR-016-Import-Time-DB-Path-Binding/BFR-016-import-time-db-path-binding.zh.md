# BFR-016: 导入期绑定 `*_DB_PATH` 常量破坏 pytest 的 DB 隔离

**Status**: Fixed
**Date**: 2026-06-04
**Severity**: High
**Affected**: `javdb/ops/reconcile/persistence.py`、`javdb/ops/sentinel/persistence.py`、`javdb/ops/diagnosis/persistence.py`、`javdb/spider/detail/runner.py`、`javdb/storage/repos/preference_repo.py`、`javdb/storage/repos/metadata_repo.py`、`javdb/migrations/tools/backfill_movie_metadata.py`、`javdb/migrations/tools/absolutize_javdb_urls_in_history.py`
**Related**: [BFR-011](../BFR-011-OpsIncident-GetDb-Logical-Name/BFR-011-opsincident-get-db-logical-name.zh.md)(同一 `get_db` 路径混淆家族——逻辑名变体）、[IMP-ADR037-02](../_archive/ADR-037-Pipeline-Test-Harness/IMP-ADR037-02-scenario-library-record-seams.md)(其 harness 的 `record_queued` 接缝是*绕过*本 bug 的临时手段;本 BFR 才是根因修复）、`apps/cli/ops/events.py`(正确的「调用期解析」参考写法）

---

## 症状

测试期间,acquisition-outcome 对账器、漂移哨兵、诊断事件写入器都静默地写入(并读取)
了一个**非测试**数据库,而不是 `tests/conftest.py` 的 autouse `_isolate_sqlite`
fixture 为每个测试准备的临时 DB。

暴露它的具体探针(在编写 ADR-037 Phase-2 计划时):一次会暂存两条排队种子的进程内
pipeline harness 运行之后,`PipelineHarness.acquisition_outcomes()` 返回 `[]`——这些
排队行不可见,是因为 `record_queued()` 把它们写到了真实的 `reports/operations.db`
(或被 chdir 后的 `reports/operations.db`),而不是 harness 回读的那个隔离临时 DB。

该故障在正常运行中不可见,因为 `record_queued()`(以及哨兵/诊断写入器)用 best-effort
的 `try/except` 包住持久化,所以写错文件——或在某个无关的桩文件上抛
`sqlite3.DatabaseError`——都不会向上传播;行只是从未出现在测试查看的位置。

## 根因

这些模块在**导入期**绑定了一个 DB 路径常量:

```python
from javdb.storage.db import OPERATIONS_DB_PATH, get_db   # 模块级
...
def open_outcome_repo():
    with get_db(OPERATIONS_DB_PATH) as conn:              # 使用导入期的值
        ...
```

`from javdb.storage.db import OPERATIONS_DB_PATH` 会在该模块首次被导入时(测试收集期)
把属性的**值**拷贝进导入方的命名空间一次。autouse 的 `_isolate_sqlite` fixture 随后
通过重新赋值*包属性*(`javdb.storage.db.OPERATIONS_DB_PATH = <temp>`)来改指路径,但
出错模块的本地名仍引用着原始对象。于是 `get_db(OPERATIONS_DB_PATH)` 解析到的是**陈旧
的**生产路径,完全绕过了 fixture。

**为什么是设计缺陷,而不只是某处写错:** `from module import CONST` 是值绑定,不是活
引用。任何代码只要(a)在导入期绑定一个可变的模块级常量,且(b)在调用期把它喂给路由
函数,就已经在最早的时刻悄悄冻结了取值。它在生产中能正常工作,只是因为 `*_DB_PATH`
常量在那里从不改变——而这恰恰使该缺陷在某个测试(或 ADR-037 harness)改指它们之前
始终不可见。同一模式在 repo 里也以默认参数捕获的形式出现
(`self._db_path = db_path or HISTORY_DB_PATH`):导入期常量在构造时被冻结进实例。

这是一个**反复出现的类别**,而非一次性问题:它是
[BFR-011](../BFR-011-OpsIncident-GetDb-Logical-Name/BFR-011-opsincident-get-db-logical-name.zh.md)
逻辑名 `get_db("reports")` bug 的「导入期绑定」孪生兄弟(两者都是同一批
`ops/*/persistence.py` 文件里 `get_db` 参数解析的故障);而且 ADR-037 harness 与
`apps/cli/ops/events.py` 早已各自通过「在调用期经包属性解析路径」绕过了它。

## 修复

通过导入**包**(而非常量)、并在打开连接时读取属性,把路径解析推迟到**调用期**——
这正是 `apps/cli/ops/events.py` 与 ADR-037 harness 已采用的写法:

```python
from javdb.storage import db as _db
from javdb.storage.db import get_db
...
    with get_db(_db.OPERATIONS_DB_PATH) as conn:   # 活读包属性
        ...
```

应用到全部 8 处把「导入期绑定常量」喂给 `get_db()` 的调用期消费者:

- `javdb/ops/reconcile/persistence.py` —— `open_outcome_repo()`
- `javdb/ops/sentinel/persistence.py` —— `open_fill_repo()`、`open_incident_repo()`
- `javdb/ops/diagnosis/persistence.py` —— `persist_incident()`
- `javdb/spider/detail/runner.py` —— `_load_content_filter_rules()`
- `javdb/storage/repos/preference_repo.py` —— `PreferenceRepo.__init__` 默认值
- `javdb/storage/repos/metadata_repo.py` —— `MetadataRepo.__init__` 默认值
- `javdb/migrations/tools/backfill_movie_metadata.py`
- `javdb/migrations/tools/absolutize_javdb_urls_in_history.py`

**刻意未改动:**
`javdb/migrations/tools/restore_moviehistory_supporting_actors_from_csv.py` 导入
`HISTORY_DB_PATH` 仅用于提供 argparse 的 `default=` 与帮助字符串(用于展示,而非作为
`get_db()` 参数)——属于低风险类别。其他地方的函数级导入(例如
`javdb/storage/repos/history_repo.py`、`javdb/storage/repos/operations_repo.py`、
`javdb/integrations/...`)**不受影响**:函数*内部*的 import 语句在每次调用时重新执行,
因此本就读取活属性。

测试:

- 新增 `test_record_queued_honours_monkeypatched_operations_db_path`
  (`tests/unit/test_reconcile_service.py`):在不注入 repo 的情况下,把
  `javdb.storage.db.OPERATIONS_DB_PATH` monkeypatch 到一个临时 DB,调用
  `record_queued()`,并断言排队行可通过 `get_db(<临时路径>)` 读到。已验证它在修复**前
  失败**(行落到别处 → `[]`),修复**后通过**。
- 更新了原先针对「导入期绑定」做断言的测试,使其与现在的代码一样在调用期解析路径:
  `tests/unit/test_reconcile_persistence.py`(`_db.OPERATIONS_DB_PATH`)与
  `tests/unit/test_ops_incident_repo.py`(`persistence._db.REPORTS_DB_PATH`)。

## 副作用

生产环境无副作用:真实运行中 `*_DB_PATH` 常量在导入期即稳定,因此调用期解析得到的是
完全相同的值。该改动只在「包属性于导入后被改指」时才改变行为——即 pytest 隔离与
ADR-037 harness 下,而那里现在是正确的。

## 后续工作

- [x] 已对全部 8 处 `get_db()` 消费者应用根因修复(见上)。
- [x] 已新增回归测试并验证「修复前红 / 修复后绿」。
- [ ] 可选护栏:一个轻量的 import-lint(或单测),在模块级 `from javdb.storage.db
      import *_DB_PATH` 的名字之后又被传给 `get_db()` 时给出告警,阻止该模式重新进入
      代码树。当前为避免过度设计未加入;若再次复发再考虑。
