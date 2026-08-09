# BFR-015: D1 中的孤儿 session 标记行——残留清理 + 回滚表策略

**Status**: Fixed
**Date**: 2026-05-31
**Severity**: Medium
**Affected**: `javdb/storage/db/_db_rollback.py`, `javdb/migrations/tools/cleanup_orphaned_session_rows.py`
**Related**: [ADR-033](../_archive/ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md), [ADR-035](../_archive/ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md), [ADR-036](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md)

---

## 症状

三个 D1 数据库存在父 `ReportSessions.Id` 已不存在的 session 标记行,表现为 reports DB 中
**14 条 `PRAGMA foreign_key_check` 违规**及其它表的悬挂行。全量扫描发现
**5 个孤儿 session id / 97 行**:

| Session id | 孤儿行 |
|---|---|
| `332`(旧整数) | MovieHistory(2), TorrentHistory(9), PikpakHistory(2), ReportMovies(10), SpiderStats(1), UploaderStats(1), PikpakStats(1) |
| `1820929777505280` | SpiderStats(1) |
| `1821066622578688` | MovieHistory(1), TorrentHistory(1) |
| `1821066695619584` | MovieHistory(2), TorrentHistory(2) |
| `20260531T122613.805503Z-8382-0000`(今天失败) | PipelineEvent(2), ParseRunFieldFill(6), AcquisitionOutcome(56) |

全部 14 条 FK 违规都来自两个旧整数 session 的四个 FK 子表
(`ReportMovies(10) + SpiderStats(2) + UploaderStats(1) + PikpakStats(1)`)。

## 根因

有两类完全不同的东西都以"孤儿"的形式出现,而把它们混为一谈正是真正的陷阱:

1. **真实残留(真 bug,但前向已被阻止)。** 四个 reports FK 子表
   (`ReportMovies` / `SpiderStats` / `UploaderStats` / `PikpakStats`)声明了
   `REFERENCES ReportSessions(Id)`。两个旧整数 session 的父行被早期工具/手工操作删除,
   而子行残留,产生 14 条 FK 违规。**当前**的 `db_rollback_session` 已经删除这四个表,
   因此新的失败运行不会再复现。

2. **设计如此的溯源记录(并非 bug)。** 较新的 ADR-033/035/036 表把行的 `session_id`
   作为**溯源信息而非归属**,并明确与 session/rollback 解耦:
   - `AcquisitionOutcome`——ADR-033 **D10**:"Enrichment 写入绕过 session/rollback……
     `session_id` 仅作溯源。"以 `qb_hash` 为键;它跟踪真实存在于 qB 中的种子的最终结局。
   - `ParseRunFieldFill`——ADR-035:"Enrichment,在 Pending→Commit 路径之外";提交闸门
     的基线只读 `committed=1` 的行。
   - `OpsIncidents`——ADR-035 D3 在失败 session 路径中*作为其一部分*抛出关键事件;它是该
     运行自身的诊断。
   - `PipelineEvent` / `RunEventSummary`——ADR-036 append-only 事件主线(记录
     `SessionFailed`)及其投影。
   对已回滚的 session,这些行理应保留;它们对 `ReportSessions` 没有 FK,因此永远不会触发
   `foreign_key_check` 违规。

缺陷在于**缺少一个明确、强制的策略**来区分 (1) 与 (2)。没有任何机制把"某表有 session id"
绑定到一个刻意的"清除还是保留"决策——正是这一点让本修复的第一版错误地把回滚级联进了溯源表
(见副作用)。

## 修复

1. **一次性残留清理**——`javdb/migrations/tools/cleanup_orphaned_session_rows.py`
   从 D1 删除了真实孤儿(四个 FK 子表 + 旧历史行,采用包含一行 NULL-`SessionId` torrent
   子行的 movie-aggregate 级联)。清理后三个库的 `PRAGMA foreign_key_check` 均返回 **0**。

2. **明确策略 + 防复发守卫**(`javdb/storage/db/_db_rollback.py`)。"清除/保留"分桶提升为
   模块级常量(`ROLLBACK_REPORTS_TABLES`、`ROLLBACK_OPERATIONS_TABLES`、
   `ROLLBACK_HISTORY_PENDING_TABLES`、`ROLLBACK_PRESERVED_TABLES`)。回滚**只**清除 FK
   子表 + 既有的 session 拥有的 ops/pending 表;六个溯源表(`PipelineEvent`、
   `RunEventSummary`、`ParseRunFieldFill`、`OpsIncidents`、`AcquisitionOutcome`、
   `EmailNotificationHistory`)以及持久去重历史(`MovieHistory`/`TorrentHistory`)被保留。
   `tests/unit/test_rollback_table_coverage.py` 断言 schema 中每个 session 标记表恰好归入
   一个桶,因此新表会让 CI 失败,直到决定其处置。

3. **文档**——[d1-rollback.md](../../handbook/zh/ops/d1-rollback.md)(en + zh)回滚表现在把溯源表
   列为**不回滚**。

## 副作用

- **自我纠正的过度级联。** 本修复第一版错误地把 `RunEventSummary` / `ParseRunFieldFill` /
  `OpsIncidents` / `AcquisitionOutcome` 加入了回滚级联,清理工具也删除了最近失败 session 的
  `ParseRunFieldFill(6)` + `AcquisitionOutcome(56)`。一次代码审查(Codex,关于
  `AcquisitionOutcome` 的 P2)发现了它;对照 ADR-033 D10 / ADR-035 确认了误分类。已回退——
  这些表现在被保留。
- **数据丢失(有界)。** 过度级联清空了 `AcquisitionOutcome`(它当时只有那一个 session 的 56
  行)。ADR-033 的 reconcile 循环会在下次运行时按 `qb_hash` 从 qB 的存活种子重新推导
  `AcquisitionOutcome`,因此对仍在 qB 中的种子,跟踪会自愈;那些行的历史 `session_id` 溯源丢失
  (可接受——它本就只是溯源)。被删的 6 行 `ParseRunFieldFill` 是基线本就忽略的 `committed=0`
  遥测。
- 不影响任何已提交运行;`Status='committed'` 回滚守卫不变。
- 本地 SQLite 镜像未重新同步——按 D1 为权威的策略,它是只读镜像,将在下次
  `sync_d1_to_sqlite --force-overwrite-all` 时重新对齐。

## 后续工作

- [x] 清除 D1 上的真实孤儿;确认 `foreign_key_check` 为空
- [x] 建立"清除/保留"策略 + 覆盖守卫
- [x] 审查后回退过度级联;保留溯源表
- [x] 更新 [d1-rollback.md](../../handbook/zh/ops/d1-rollback.md)(en + zh)
- [ ] 让下次 `reconcile.run()` 从 qB 重新填充 `AcquisitionOutcome`
- [ ]（可选）下次方便同步时从 D1 重新对齐本地 SQLite 镜像
