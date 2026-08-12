# BFR-034：把跨后端 AUTOINCREMENT 当作双写外键来信任（用 SQLite 的 `lastrowid` 作为 D1 `ReportTorrents` 的外键）

**Status**: Fixed
**Date**: 2026-08-12
**Severity**: High（D1 上静默且无法回滚的跨会话外键损坏）
**Affected**: `javdb/storage/db/_db_reports.py`、`javdb/storage/dual_connection.py`
**Related**: [ADR-047](../_archive/ADR-047-Dual-Backend-Drift-Reconciliation/ADR-047-dual-backend-drift-reconciliation.zh.md)、[ADR-042](../_archive/ADR-042-D1-Atomic-Commit-Boundaries/ADR-042-d1-atomic-commit-boundaries.zh.md)、[BFR-021](../BFR-021-D1-Session-Write-Silent-Loss/BFR-021-d1-session-write-silent-loss.zh.md)、[BFR-033](../BFR-033-Rebase-Ours-Discards-Run-Results/BFR-033-rebase-ours-discards-run-results.zh.md)（同一轮审查批次的姊妹缺陷）、`javdb/migrations/d1/2026_05_08_sessionid_decouple.md`（2026-05-08 的前例）、提交 `cd507c88`

---

## 现象

没有事故报告。该缺陷是通过审查双写路径发现、并对照代码确认的——而这本身就是重点：**这个缺陷没有症状。** 它不产生异常、不产生失败的运行、不产生 drift 日志条目、也不产生外键违规。它写入了错误的数据，但看起来一切正常。

`db_insert_report_rows` 插入一行 `ReportMovies`，从游标读回新生成的 id，然后把它当作子行的外键：

```python
cur = conn.execute(
    """INSERT INTO ReportMovies
       (SessionId, Href, VideoCode, Page, Actor, Rate, CommentNumber)
       VALUES (?, ?, ?, ?, ?, ?, ?)""",
    (session_id, href, ...),
)
rm_id = cur.lastrowid
...
conn.execute(
    """INSERT INTO ReportTorrents
       (ReportMovieId, VideoCode, MagnetUri, ...)
       VALUES (?, ?, ?, ...)""",
    (rm_id, vc, magnet, ...),
)
```

在 `STORAGE_BACKEND=dual` 下，这唯一的 `rm_id` 被写入了**两个**后端，但它始终只是 **SQLite** 那一侧的 id。

## 根因

设计缺陷在于：信任两个相互独立的数据库的 `AUTOINCREMENT` 计数器会保持一致，然后把其中一个后端生成的 id 当作另一个后端上的外键使用。

`ReportMovies.Id` 是 `INTEGER PRIMARY KEY AUTOINCREMENT`（`javdb/migrations/d1/2026_05_13_session_id_to_text_reports.sql:62`）。在 dual 模式下，这条 INSERT 会扇出到两个真实数据库，各自分配自己的 id。而游标只暴露其中一个：

```python
self.lastrowid = (
    sqlite_cur.lastrowid if sqlite_cur is not None else getattr(d1_cur, "lastrowid", None)
)
```

—— `javdb/storage/dual_connection.py:381-383`

D1 那一侧自己的 id 以 `meta.last_row_id` 形式返回（`javdb/storage/d1_client.py:181`），却被直接丢弃了。于是，一旦两个计数器出现 *N* 的偏差，每一行写入 D1 的 `ReportTorrents` 都指向 `Id - N`——一部属于**更早的、另一个会话**的影片。

**而外键校验是通过的。** 那个陈旧 id 在 D1 上几乎总是存在的，因为计数器的偏差方向是往*前*漂移，也就是说被引用的 id 是一行更早的、实实在在存在的记录。SQLite 自己的 `PRAGMA foreign_keys` 在镜像上校验通过（那里的 id 是正确的），D1 上也通过（那里的 id 是错的但可解析）。任何地方都不会报错。

有两个次级失误让它长期躲过了审查：

- **一段 docstring 断言了相反的结论。** `DualConnection._maybe_warn_id_drift`（`javdb/storage/dual_connection.py:1213-1214`）声称 SQLite↔D1 之间恒定的 `lastrowid` 偏移「is the normal post-migration steady state and is harmless (FK resolution uses business keys)」。对它所描述的那个 drift 对账器而言这是真的（[ADR-047](../_archive/ADR-047-Dual-Backend-Drift-Reconciliation/ADR-047-dual-backend-drift-reconciliation.zh.md)，它按 `Href` / `MagnetUri` 匹配行），但对 `db_insert_report_rows` 而言是假的——后者是按原始 id 解析的。一个局部的真相被当成了全局真相，而该模块正是基于它把「恒定偏移」从*错误*降级成了 *INFO*。
- **已有的防护没有为这张表启用。** `dual_connection.py` 里本来就有恰当的防线——`APPLICATION_GENERATED_ID_PK_COLUMN`，其 `DualCursor._check_id_consistency` 会在受保护表的两个 `lastrowid` 不一致时抛出 `DualWriteIdMismatchError`。它列了 `ReportSessions`、`PendingMovieHistoryWrites`、`PendingTorrentHistoryWrites`、`MovieHistory`、`TorrentHistory`——却没有 `ReportMovies`。于是，唯一一张其 id 被原样当作跨后端外键使用的表，恰恰就是唯一一张没被保护的表。

这是一类已知缺陷的重演，不是新问题。`APPLICATION_GENERATED_ID_PK_COLUMN` 那段注释自己就引用了 2026-05-08 那起由信任 SQLite 侧 `AUTOINCREMENT` 引发的事故（`javdb/migrations/d1/2026_05_08_sessionid_decouple.md`），而其中的 "Batch C" 条目记录的，正是出于完全相同的理由（`TorrentHistory.MovieHistoryId` 指向错误的行）已经对 `MovieHistory` / `TorrentHistory` 施加过的同一个修复。`ReportMovies` 在那次清扫中被漏掉了。

## 加重因素：这类损坏会绕过会话回滚

回滚是*经由该会话自己的影片行*来定位其种子行的：

```sql
DELETE FROM ReportTorrents
WHERE ReportMovieId IN (SELECT Id FROM ReportMovies WHERE SessionId=?)
```

—— `javdb/storage/db/_db_rollback.py:299-301`

在 D1 上，那些指错的种子行携带的 id 属于*另一个*会话的影片，因此这条 `DELETE` 匹配不到它们。紧接着，父级 `ReportMovies` 行会被 `ROLLBACK_REPORTS_TABLES` 循环删除，于是那些种子行作为**指向一个从不属于它们的会话的孤儿**被留了下来。回滚创建它们的那个会话不会删掉它们；回滚它们所指向的那个会话也不会（那只会删除*它自己*那些指向正确的子行）。

结果是：在 D1——也就是权威数据源——上留下永久存在、且永久被错误归属的数据行，而项目自己的回滚工具在结构上根本触及不到它们。`javdb/migrations/tools/cleanup_orphaned_session_rows.py:206-208` 用的是同样的 `ReportMovieId IN (SELECT Id FROM ReportMovies WHERE SessionId IN ...)` 形状，所以孤儿清理工具同样看不见它们。

在下游，`apps/api/routers/stats_query_builders.py:81` 用 `LEFT JOIN ReportTorrents rt ON rt.ReportMovieId = rm.Id` 统计 torrents 指标，因此仪表盘会把这些指错的行归到错误的日期和错误的会话上。

## 暴露面

计数器偏离在本系统中并非假想；有三种机制会产生它：

1. **D1 按语句自动提交，SQLite 不会。** 当一个 dual 事务回滚时，SQLite 侧的 INSERT 被撤销，而 D1 侧的已经提交——该模块在多处都写明了这一点（`dual_connection.py:1004`、`:1155`、`:1176`）。每发生一次这样的事件，两个计数器就会被永久拉开。
2. **在 `d1` 模式下 SQLite 镜像是冻结的。** 当 `STORAGE_BACKEND=d1` 时 `reports/*.db` 从不被流水线写入（DB 层用的是 `D1Connection`），所以 D1 的计数器每天前进，而镜像的不动。之后任何一次切换到 `dual`，起点就已经是一个很大的偏移量。
3. **`sync_d1_to_sqlite` 只按 `max(Id)` 对齐。** `apps/cli/db/sync_d1_to_sqlite.py:566-575` 把 `UPDATE sqlite_sequence SET seq=? WHERE name=?` 设为导入的最大 id。这能避免*未来本地*的 id 冲突；但它不能、也无法让两个计数器此后保持同步。

这条路径在 CI 中就能命中生产数据：`TestIngestion.yml:100` 固定了 `STORAGE_BACKEND: dual`（这是刻意的，以免仓库级的 `vars.STORAGE_BACKEND` 漂移把它静默降级），并对生产 D1 凭据运行。

## 修复

提交 [`cd507c88`](https://github.com/TongWu/JAVDB_AutoSpider_CICD/commit/cd507c88) —— `fix(db): stop using SQLite lastrowid as the dual-mode ReportTorrents FK`。

1. **显式提供 `ReportMovies.Id`。** `db_insert_report_rows` 现在调用 `generate_integer_id()`（52 位应用侧 snowflake，恒 `< 2**53`，因此能安全通过 D1 的 JSON 传输），把它放进 INSERT 的列清单，然后把同一个值复用为 `ReportTorrents.ReportMovieId`。两个后端现在存的是*同一个* id，外键在两侧含义一致。`cur.lastrowid` 完全不再被读取。这与 `MovieHistory` / `TorrentHistory` 已采用的 Batch C 方案保持一致。
2. **启用防护。** 在 `APPLICATION_GENERATED_ID_PK_COLUMN` 中加入 `ReportMovies: "Id"`（`javdb/storage/dual_connection.py:298-306`），把这条不变式从*假定*变成*强制*：将来若有调用方在 dual 模式下插入 `ReportMovies` 而不带显式 `Id`，现在会以 `DualWriteIdMismatchError` 中止，而不是静默损坏 D1。
3. **回归测试**位于 `tests/unit/test_report_rows_dual_movie_id.py`，它用第二个真实 SQLite 数据库模拟 D1 那一侧，并**刻意**让其 `ReportMovies` 计数器领先于镜像——正是旧代码无法承受的那个条件：`test_torrent_fk_matches_movie_id_on_both_backends`、`test_movie_id_is_identical_on_both_backends`、`test_multiple_rows_do_not_share_a_movie_id`、`test_reportmovies_is_registered_as_application_generated_id`、`test_insert_supplies_explicit_id_column`。

## 副作用

- **新的 `ReportMovies.Id` 是 52 位 snowflake，不再是小的连续整数。** 已有行保留其原来的小 id；列类型仍是 `INTEGER PRIMARY KEY AUTOINCREMENT`，无需 schema 变更或迁移。任何假定 `ReportMovies.Id` 稠密、数值小、或按插入顺序单调的代码都会受影响——仓库中并没有这样的代码；读取方都是对它做 join 或按它 select。
- **本地镜像上 `ReportMovies` 的 `sqlite_sequence` 会跳到 snowflake 量级**（首次插入之后），这与 `MovieHistory` / `TorrentHistory` 已有的表现完全一致。无害：没有代码依赖该计数器的数值。
- **本次修复不会修补 D1 上已存在的指错行。** 它阻止新的损坏，但不会查找或纠正历史损伤。见「后续工作」。
- `db_insert_report_rows` 的签名、返回值，以及在 `sqlite` / `d1` 后端下的行为均未变化。没有 API 响应结构变化，因此在 [ADR-055](../ADR-055-Dual-Backend-Contract-Single-Source/ADR-055-dual-backend-contract-single-source.zh.md) 的同步规则下不需要对应的 TypeScript 后端改动。

## 后续工作

- [x] 显式提供 `ReportMovies.Id`，并在两侧复用它作为外键
- [x] 在 `APPLICATION_GENERATED_ID_PK_COLUMN` 中登记 `ReportMovies`
- [x] 用双后端回归测试固定该不变式
- [x] **`javdb/migrations/tools/csv_to_sqlite.py` 曾存在完全相同的模式** —— 在 `INSERT INTO ReportMovies` 之后 `report_movie_id = cur.lastrowid`，喂给同一个 `ReportTorrents.ReportMovieId`；此外还有针对 `MovieHistory` 的 `SELECT Id ... WHERE Href=?` 回读（同样不安全，因为 `DualConnection` 会把读路由到 D1）。两处现在都改用 `generate_integer_id()`，并把 id 放进 INSERT 的列清单。该项已在同一轮审查批次中落地，提交 [`5bf26062`](https://github.com/TongWu/JAVDB_AutoSpider_CICD/commit/5bf26062)，回归测试位于 `tests/unit/test_csv_to_sqlite_dual_ids.py`。
- [x] **`apps/api/routers/test_mode.py` 曾在播种受保护表时没有显式 id** —— 在 `INSERT INTO MovieHistory` 与 `INSERT INTO TorrentHistory` 之后 `return int(cur.lastrowid)`，而这两张表都在 `APPLICATION_GENERATED_ID_PK_COLUMN` 中。`_insert_movie` / `_insert_torrent` 现在自行生成 id 并返回它。同一轮审查批次、同一个提交（`5bf26062`）。（它们接收的是普通 `sqlite3.Connection`，所以防护本来就不可能触发——而这恰恰说明该模式需要被移除，而不是依赖防护；这些 fixture 现在也携带与生产同形的 id，而不再是 1/2/3。）
- [x] **`apps/cli/ops/profile_hot_paths.py` 曾经经由后端路由器播种受保护的历史表** —— `_seed_history` 从 `cur.lastrowid` 读回新的 `MovieHistory.Id`，并把它用作 `TorrentHistory.MovieHistoryId`，而写入走的是 `get_db(<临时文件>)`——它只按 `STORAGE_BACKEND` 路由（*路径*参数完全不参与这个判断）。这些写入实际上从未真正到达 D1：`_logical_name_for()` 会以 `ValueError` 拒绝这个未登记映射的临时文件路径，而基准驱动把它吞成 `!! db_load_history FAILED`，所以在 `d1` / `dual` 下这个 profiler 只是坏掉了，并没有造成损坏——但它离写进权威数据库只差一条映射表条目。`bench_db_load_history` 现在整段运行在文档化的 `_STORAGE_BACKEND_INIT_OVERRIDE=sqlite` 逃生开关内（因此 `init_db`、播种器与 `db_load_history` 都可证明是本地的，并且该基准现在在三种后端下都能跑，而不只是两种），播种器也用 `generate_integer_id()` 自行生成两个 id。测试位于 `tests/unit/test_profile_hot_paths_seed_ids.py`。
- [x] **修正 `_maybe_warn_id_drift` 的 docstring**（`javdb/storage/dual_connection.py:1213-1214`）。「Harmless (FK resolution uses business keys)」只对对账器成立；照现在的写法，它会引诱下一位作者犯同样的错。应把该论断的适用范围明确限定为按业务键解析的场景。
      **已完成（2026-08-12）：** docstring 现已把「harmless」的适用范围限定为从不跨后端复用 `lastrowid` 的代码，点名本 BFR，并指向防护映射与 `test_lastrowid_call_sites.py` 的清单。
- [ ] **评估并修复 D1 上的历史损伤。** 审计工具已经交付：`python3 -m apps.cli.db.audit_report_torrent_fks` 会针对 `STORAGE_BACKEND` 解析到的后端报告三类检测信号（`ORPHANED_TORRENT` 与 `VIDEO_CODE_MISMATCH` 为确定性信号，`UNEXPLAINED_DUPLICATE_SLOT` 为启发式信号），修复动作则被 `--apply --force --delete-orphans` 门控（仅删除孤儿行——重新挂接只做报告，因为这套 schema 无法证明一个指错的行原本应属于哪个父级）。**剩余工作是运维层面的：** 在 `d1` 环境下运行它（D1 是权威数据源）、对报告做甄别，然后执行补救——孤儿行走门控删除，不匹配清单则人工修补。注意 `apps.cli.db.rollback` 与 `cleanup_orphaned_session_rows.py` 都触及不到这些行——两者都是经由 `ReportMovieId IN (SELECT Id FROM ReportMovies WHERE SessionId=?)` 定位子行的。
- [x] **清扫这一整类缺陷。** 审计所有可能在 `STORAGE_BACKEND=dual` 下执行的路径上的每一处 `cur.lastrowid` 读取，而不仅是这里发现的这几处。2026-05-08 事故、Batch C 以及本 BFR 是同一模式的三次实例；与其等着第四次再修，不如用一条 lint 规则或一个枚举 `lastrowid` 调用点的契约测试来终结这种复发。**已完成（2026-08-12）：** 已清扫当时 `javdb/` `apps/` `scripts/` 下 9 个文件中的全部 20 处 `lastrowid` 读取（基于 AST，因此关于 `lastrowid` 的注释与 docstring 不会掩盖真实读取）—— 1 处已修（即上面那个 profiler 播种器），因此固定下来的清单为 **8 个文件、19 处读取**：6 处在 `dual` 下可达但返回值被所有仓内调用方丢弃（`_db_stats` ×3、`_db_operations` ×2、`pipeline_event_repo` ×1），4 处可证明是单后端（`_db_migrations` 的 v5→v6 步骤只在 `init_db` 的 sqlite 覆盖下运行；`reconcile_d1_drift` ×2 与 `d1_port` ×1 读取的是 D1 *自己*的游标并用于 D1 行），2 处刻意解析为 D1 那一侧（`content_filter_repo._canonical_lastrowid`），以及 7 处属于 `DualCursor` 防护机制本身。复发现在由一个契约测试终结：`tests/unit/test_lastrowid_call_sites.py` 以「每文件一条分类」的形式固定了这份清单，任何新增的读取都会让 CI 失败直到作者为其分类，同时断言持有已修写入方的那 4 个文件永不再回读 rowid。
