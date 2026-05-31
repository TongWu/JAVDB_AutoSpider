# BFR-015: 回滚遗漏较新的 session 标记表，留下孤儿行

**Status**: Fixed
**Date**: 2026-05-31
**Severity**: Medium
**Affected**: `javdb/storage/db/_db_rollback.py`, `javdb/migrations/tools/cleanup_orphaned_session_rows.py`
**Related**: [ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md), [ADR-035](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md), [ADR-036](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md)

---

## 症状

三个 D1 数据库累积了带 session 标记的孤儿行——其父 `ReportSessions.Id` 已不存在
（由 session 回滚/删除遗留）。它们表现为 reports DB 中 **14 条
`PRAGMA foreign_key_check` 违规**，以及其它表中的悬挂行：

```
PRAGMA foreign_key_check (reports) → 14 行
  ReportMovies(10) + SpiderStats(2) + UploaderStats(1) + PikpakStats(1)
```

枚举三个 DB 中每个 `SessionId` / `session_id` 列，发现 **5 个孤儿 session id /
97 行**：

| Session id | 孤儿行 |
|---|---|
| `332`（旧整数） | MovieHistory(2), TorrentHistory(9), PikpakHistory(2), ReportMovies(10), SpiderStats(1), UploaderStats(1), PikpakStats(1) |
| `1820929777505280`（旧） | SpiderStats(1) |
| `1821066622578688`（旧） | MovieHistory(1), TorrentHistory(1) |
| `1821066695619584`（旧） | MovieHistory(2), TorrentHistory(2) |
| `20260531T122613.805503Z-8382-0000`（最近，**今天失败**） | PipelineEvent(2), ParseRunFieldFill(6), AcquisitionOutcome(56) |

最近这条是铁证：它的 `PipelineEvent` 日志在 `2026-05-31T12:49:32Z` 记录了一条
`SessionFailed` 事件，即它**今天**才回滚，却在 `ParseRunFieldFill` 和
`AcquisitionOutcome` 里留下了行。

## 根因

`db_rollback_session`（`javdb/storage/db/_db_rollback.py`）按一份**硬编码列表**
逐表删除失败 session 的行，然后删除 `ReportSessions` 父行。该列表早于后来三个
ADR，它们各自新增了 session 标记表却**没有接入回滚**：

- ADR-036 → `PipelineEvent`、`RunEventSummary`（reports）
- ADR-035 → `ParseRunFieldFill`（reports）
- ADR-027/035 → `OpsIncidents`（reports）
- ADR-033 → `AcquisitionOutcome`（operations）
- ADR-020 → `EmailNotificationHistory`（operations）

由于 `_rollback_reports` / `_rollback_operations` 从不触碰这些表，每次写入它们的失败
运行都会删除 `ReportSessions` 父行，把子行悬挂。四个 FK 子表（`ReportMovies` /
`SpiderStats` / `UploaderStats` / `PikpakStats`）确实声明了
`REFERENCES ReportSessions(Id)`，因此其孤儿成为 `foreign_key_check` 违规；较新的表
没有 FK（跨库或无 FK），其孤儿便悄悄累积。

更深层的缺陷是**结构性的**：没有任何机制把"某表有 session id"与"某表有回滚处置"
绑定，因此每新增一个 session 标记表，缺口就重新打开。旧整数孤儿（`332` 等）是
text-id 时代之前的更早残留（其 `ReportSessions` 行被早期工具删除，而已提交的历史
仍保留），但同一类缺口也产生了今天的 `20260531…8382` 孤儿。

## 修复

1. **前向修复**——`_rollback_reports` 现在也清除 `RunEventSummary`、
   `ParseRunFieldFill`、`OpsIncidents`；`_rollback_operations` 现在清除
   `AcquisitionOutcome`（其列名是 `session_id` 而非 `SessionId`）。这些表列表被提升
   为模块级单一可信源常量（`ROLLBACK_REPORTS_TABLES`、`ROLLBACK_OPERATIONS_TABLES`、
   `ROLLBACK_HISTORY_PENDING_TABLES`、`ROLLBACK_PRESERVED_TABLES`）。

2. **刻意例外**——`PipelineEvent`（ADR-036 append-only 事件主线；它记录的正是
   `SessionFailed` 事件本身）与 `EmailNotificationHistory`（记录真实发出的邮件——
   回滚无法撤销的外部动作）被**保留**。它们的 session id 是溯源信息，而非归属 FK；
   对 `ReportSessions` 没有 FK，因此永远不会触发 `foreign_key_check` 违规。
   `MovieHistory` / `TorrentHistory` 仍是回滚保留的持久去重记忆（只撤销 `Pending*`
   写入）。

3. **防复发守卫**——`tests/unit/test_rollback_table_coverage.py` 枚举 schema 中每个
   session 标记表，断言其要么被回滚清除，要么列在 `ROLLBACK_PRESERVED_TABLES` 中。
   新增的 session 标记表现在会让 CI 失败，直到决定其回滚处置。

4. **一次性数据清理**——`javdb/migrations/tools/cleanup_orphaned_session_rows.py`
   从 D1 删除了 96 行耦合孤儿（历史采用 movie-aggregate 级联：先删 torrent 子行——
   包括一行 NULL `SessionId`——再删 movie）。2 行 `PipelineEvent` 被刻意保留。清理后
   `PRAGMA foreign_key_check`（reports）返回 **0**。

## 副作用

- 回滚现在对每个失败 session 删除更多行（四个较新的耦合表）。对 in-progress/失败
  session 而言这是预期清理；不影响任何已提交运行（`Status='committed'` 守卫不变）。
- 一次性清理删除了 3 个已死旧 session 的持久 `MovieHistory` / `TorrentHistory` 去重
  行（操作者批准）。那几部旧影片（如 `PFES-133`）若被某次抓取再次遇到，将被重新评估。
- 本地 SQLite 镜像未重新同步；按 D1 为权威的策略，它是只读镜像，将在下次
  `sync_d1_to_sqlite --force-overwrite-all` 时重新对齐。

## 后续工作

- [x] 清除 D1 上既有孤儿；确认 `foreign_key_check` 为空
- [x] 扩展回滚级联 + 新增防复发守卫测试
- [x] 更新 [d1-rollback.md](../../handbook/zh/ops/d1-rollback.md)（en + zh）
- [ ]（可选）下次方便同步时从 D1 重新对齐本地 SQLite 镜像
