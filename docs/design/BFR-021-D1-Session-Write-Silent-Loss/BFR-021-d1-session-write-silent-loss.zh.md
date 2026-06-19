# BFR-021: Session 创建不校验写入是否落库,掩盖了一次 D1 静默写丢失

**Status**: Fixed
**Date**: 2026-06-19
**Severity**: Medium
**Affected**: `javdb/storage/db/_db_reports.py`, `javdb/spider/app/run_service.py`
**Related**: [ADR-010](../_archive/ADR-010-D1-Access-Port/ADR-010-d1-access-port.zh.md), [BFR-002](../_archive/BFR-002-Commit-Session-Misleading-Log/BFR-002-commit-session-misleading-log.zh.md), [BFR-020](../BFR-020-D1-Recovery-Outbox-Replay-After-Rollback/BFR-020-d1-recovery-outbox-replay-after-rollback.zh.md), [run 27810377978](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/27810377978)

---

## Symptom

手动触发的 **Ad-Hoc Ingestion** [run 27810377978](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/27810377978)
(session `20260619T065506.230983Z-d5f5-0000`)把 spider、qB 上传、文件过滤、
PikPak bridge、rclone dedup 全部跑完,只在最后的 **Mark sessions as committed**
步骤失败:

```
✗ __main__  Failed to commit session 20260619T065506.230983Z-d5f5-0000:
            20260619T065506.230983Z-d5f5-0000: None -> committed is not allowed
Commit done: committed=0 already_committed_or_missing=0 failed=1
```

从 Phase 2 开始,日志里有 24 条被吞掉的告警(全部 `code 7500`、
`FOREIGN KEY constraint failed`):

| 组件 | 目标表 | 次数 |
|---|---|---|
| `CSVWriter` | `ReportMovies` / `ReportTorrents` | 21 |
| `Spider` | `SpiderStats` | 1 |
| `QBUploader` | `UploaderStats` | 1 |
| `PikPak` | `PikpakStats` | 1 |

直接查询 canonical 的 D1 **reports** 库,确认 session 及其**所有**子表行都不存在:

```
ReportSessions WHERE Id='20260619T065506.230983Z-d5f5-0000'  -> 0 行
ReportMovies / SpiderStats / UploaderStats / PikpakStats      -> 各 0 行
```

对本地 SQLite 镜像查询同样为空(该镜像是 `STORAGE_BACKEND=d1` 下的陈旧快照,
最新行停在 `2026-05-30`)。然而 spider 在 `06:55:14` 打印了一条干净的创建日志:

```
Spider  Created report session: id=20260619T065506.230983Z-d5f5-0000 ... write_mode=pending
```

13 分钟后的下一个定时 run(`27810934159`,`07:08`)正常创建**并** commit 了它的
session —— 说明 D1 是健康的,这次丢失是本 run 独有的。

## Root Cause

分两层 —— 一层是**触发原因(未完全确定)**,一层是**设计缺陷(本次修复)**。

**触发原因(未确定)。** `db_create_report_session`
([_db_reports.py](../../../javdb/storage/db/_db_reports.py))执行的是一条普通的
同步 `INSERT INTO ReportSessions (... 'in_progress' ...)`。它不带写策略
(policy),因此 D1 port 会立即执行(不批处理、不进 recovery outbox)并返回
cursor;调用没有抛异常(否则创建会中止,"Created report session" 这行不会打印)。
尽管表面成功,这一行从未在 D1 上可见。这条语句的失败在任何地方都没有记录 ——
job 日志没有、归档的 `reports/D1/d1_drift.jsonl` 没有(它只记录
`rollback_summary` / `pending_session_verify`,不记录单条写失败)、recovery
outbox 也没有。`reports/D1/d1_port_summary.json` 记录了 `permanent_errors: 24`
(即 FK 级联)、`transient_errors: 4` 且 `retry_successes: 2`(2 个瞬时错误在别处
重试耗尽)、`outbox_queued: 0`。最一致的解释是一次**静默写丢失** —— 后端 ACK 了
请求但行没有持久化 —— 但产物无法证明确切原因,且此后未再复现。

**设计缺陷(真正的 defect)。** 在 pipeline 向一个 session 提交六分钟的工作之前,
没有任何环节校验 session 写入是否落库:

1. `db_create_report_session` 信任 INSERT 表面成功,直接返回 `SessionId`。
2. `run_service.py` 确实回读了一次该行
   ([run_service.py:617](../../../javdb/spider/app/run_service.py#L617)) ——
   但只用于镜像 `WriteMode`;回读结果为 `None`(行不存在)时被**静默忽略**,
   "Created report session" 照常打印。
3. 所有下游写入都通过外键引用 `ReportSessions(Id)`
   (`ReportMovies` / `SpiderStats` / `UploaderStats` / `PikpakStats`;
   `ReportTorrents` 再挂在 `ReportMovies` 之下)。父行缺失时,全部 24 条写入
   报 `FOREIGN KEY constraint failed` —— 但每条都是 best-effort,只打了告警,
   于是 pipeline 继续运行。
4. `commit_session` 读取 session 状态;行不存在时 `get_state` 返回
   `status=None`([lifecycle.py:95](../../../javdb/storage/sessions/lifecycle.py#L95)),
   于是合法跳转检查拒绝了 `None -> committed`
   ([lifecycle.py:126](../../../javdb/storage/sessions/lifecycle.py#L126))并以 1 退出。

一次 D1 静默写丢失,就这样变成了一次完整浪费的 run,在末尾以晦涩信息失败,
而不是在创建时立即失败。

## Fix

让 session 创建在返回前**做 read-your-write 校验**,放在所有调用方共用的唯一入口
—— `db_create_report_session`。回读复用同一连接(因此 read-your-write 一致,
不受 D1 读副本延迟影响),且在 `dual` 模式下会路由到 D1,因而还能额外捕获
session 创建时 D1-leg 的 drift:

```python
with _get_db(db_path or _REPORTS_DB_PATH) as conn:
    conn.execute("""INSERT INTO ReportSessions (...) VALUES (..., 'in_progress', ...)""", (...))
    # dual 模式:直接读 D1 leg,避免 SQLite 镜像回退掩盖 D1 写丢失;
    # d1/sqlite 模式:conn 本身即 canonical 存储。
    verify_conn = getattr(conn, "_d1", conn)
    if verify_conn.execute("SELECT 1 FROM ReportSessions WHERE Id=?", (sid,)).fetchone() is None:
        raise RuntimeError(
            f"ReportSessions row {sid!r} is absent immediately after INSERT; "
            f"the session write did not durably land on the reports backend. ..."
        )
return sid
```

该 `RuntimeError` 通过 `SessionLifecycleRepo.create_report_session` 传播到
`run_service.py` 中已有的处理器
([run_service.py:648](../../../javdb/spider/app/run_service.py#L648))—— 其日志
本就写着 *"Aborting after init_db/db_create_report_session failure"* —— 处理器
打印日志并 `sys.exit(1)`。现在 run 会**在创建阶段**失败,在任何抓取之前,并点名
出问题的 `SessionId`。

回归测试:`tests/unit/test_bfr021_session_write_verification.py`(happy path 仍能
落库;一个会 ACK INSERT 但回读为空的连接会触发 `RuntimeError`)。

## Side Effects

- 每次 session 创建多一次轻量 `SELECT 1` 往返(每个 run 一次)—— 可忽略。
- 在 `STORAGE_BACKEND=dual` 下,校验**直接读 D1 leg**(`conn._d1`),而非走
  `DualConnection.execute`。这点很关键:`DualConnection` 先写 SQLite leg,且在
  D1 读出错时会回退到 SQLite 镜像(`dual_connection.py`)—— 那样本地行会掩盖
  D1 写丢失。读 `conn._d1` 让 D1-leg 的 session 丢失中止创建,符合 "D1 is the
  canonical source of truth"。(感谢 [PR #238](https://github.com/TongWu/JAVDB_AutoSpider_CICD/pull/238)
  上 CodeRabbit 的 review 指出了这个回退漏洞。)
- 预期无误报:回读与写入共用连接,D1 query API 服务 primary(且会重试瞬时抖动),
  刚写入的行可见;d1/sqlite 模式下 `conn` 本身即 canonical 连接。

## Follow-Up

- [ ] **触发原因仍未解释。** 若复现,新的 `RuntimeError` 会带着 `SessionId`
      立即触发;届时立即抓取一次对该行的直接 D1 `SELECT`,以及该 run 的
      `reports/D1/d1_port_summary.json`,以刻画这次丢失。
- [ ] 若在其他地方也观察到类似静默丢失,考虑把同样的 read-your-write 校验扩展到
      其他关键的、不带 policy 的 D1 写入(例如 lifecycle 状态跳转)。
- [x] `20260619T065506.230983Z-d5f5-0000` 无需清理:D1 上没有它的任何行,本地
      SQLite 镜像也已陈旧。
