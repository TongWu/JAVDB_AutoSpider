# BFR-020：D1 recovery-outbox 重放复活了已回滚 session 的写入

**Status**: Mitigated
**Date**: 2026-06-15
**Severity**: Medium
**Affected**: `javdb/storage/d1_port.py`, `javdb/storage/d1_recovery.py`, `javdb/storage/db/_db_rollback.py`, `javdb/storage/db/_db_connection.py`
**Related**: [ADR-010](../_archive/ADR-010-D1-Access-Port/ADR-010-d1-access-port.zh.md), [BFR-015](../BFR-015-Session-Orphan-Cleanup-And-Rollback-Policy/BFR-015-session-orphan-cleanup-and-rollback-policy.zh.md), [run 27551555092](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/27551555092)

---

## Symptom

定时的 [Daily Ingestion run 27551555092](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/27551555092)
在 **Step 1 - Run Spider** 的 Phase-1 详情处理阶段失败。spider 在 flush 一笔批处理的
历史暂存写时，撞上 Cloudflare D1 的瞬时故障而崩溃（exit 1）：

```
javdb.storage.d1_client.D1TransientError: D1 API returned HTTP 500:
{"errors":[{"code":7500,"message":"internal error; reference = e_PVSMDp_b09a92a1761b4856a7e1e8bc6132a8b5"}]}
```

这次崩溃**不是**本 BFR 记录的缺陷——它是 Cloudflare 侧的抖动（`code 7500` +
`internal error` + 一个 CF 事故 `reference`），D1 port 已按 `D1_MAX_RETRIES=5`
做了 5 次指数退避重试，重跑即可成功。排查时 D1 完全健康（`SELECT 1` 由 `SIN`
节点返回），且最近 ~10 天仅此一次。

缺陷是在自动 `cleanup-on-failure` 回滚**之后**才暴露的。回滚报告成功、
`"history": {"PendingMovieHistoryWrites": 0}`，该 session 的 `ReportSessions`
行也已删除，但直接查询 D1 却发现**残留了 1 条孤儿 `pending` 行**：

| 表 | 行 | `ApplyState` | `CreatedAt` | 父 `ReportSessions`？ |
|---|---|---|---|---|
| `PendingMovieHistoryWrites` | `ALDN-131`（`SessionId=20260615T140318.589362Z-60d0-0000`） | `pending` | `2026-06-15 22:04:22` | **不存在（已回滚）** |

它是整个 history DB 中唯一的孤儿 pending 行。

## Root Cause

**D1 recovery outbox 与 session 回滚之间的时序竞态**。没有任何机制把一次 outbox
重放与“其父 session 仍存活”绑定起来，因此重放可以把一个**已被回滚**的 session 的
写入重新落盘。

逐步还原（均有该 run 的证据支撑）：

1. 历史暂存写走的是**批处理 + 可恢复**策略（`_pending_stage_policy`，
   [_db_history_write.py:92](../../../javdb/storage/db/_db_history_write.py#L92)）：
   `ordering_key="history:<session_id>"`、`batching_allowed=True`、
   `recovery_allowed=True`。
2. 批量 flush 撞上瞬时 HTTP 500。`D1Port.flush()` 的 `except D1TransientError`
   分支（[d1_port.py:267](../../../javdb/storage/d1_port.py#L267)）把失败批次——
   含 `ALDN-131` 的 `INSERT`——**持久化**进 recovery outbox
   （`reports/D1/d1_recovery_outbox.jsonl`）。随后异常上抛、spider 崩溃。
   **此刻 D1 里没有任何已提交的数据。**
3. `cleanup-on-failure` 执行回滚。`_rollback_pending_in_progress`
   （[_db_rollback.py:230](../../../javdb/storage/db/_db_rollback.py#L230)）以
   `policy=None` 发出 `DELETE FROM PendingMovieHistoryWrites WHERE SessionId=?`，
   因此该语句**立即执行**（不走批处理——见 `_should_queue`，
   [d1_port.py:558](../../../javdb/storage/d1_port.py#L558)）并返回 `rowcount=0`。
   一条立即 DELETE 返回 `0`，证明**回滚那一刻该行根本不在 D1**。随后
   `_rollback_reports`（[_db_rollback.py:261](../../../javdb/storage/db/_db_rollback.py#L261)）
   删除了 `ReportSessions` 行。
4. 在回滚**之后**，一次 recovery-outbox 重放（`startup_drain` /
   `replay_ordering_key`，[d1_recovery.py](../../../javdb/storage/d1_recovery.py)，
   由 `D1_STARTUP_REPLAY_ENABLED=true` 开启）把排队的 `INSERT` 重新应用，将
   `ALDN-131` 写入 D1，**并携带其原始暂存时间戳 `2026-06-15 22:04:22`**——这正是
   为何时间戳早于回滚、而行却是在回滚后才落地的原因。其父 `ReportSessions` 行已不
   存在，故成为孤儿。

设计缺陷在于**回滚与 recovery outbox 互不协调**：

- `db_rollback_session` 删除了 pending 行与 `ReportSessions` 行，却**没有**作废 /
  排空该 session 的 outbox 事件（`ordering_key="history:<session_id>"`）。
- 重放路径在应用一笔排队写入前，**没有任何前置校验**确认父 session 仍存在、未被回滚。

由此产生的孤儿还**无法被现有工具回收**：未来的 `db_commit_session_history` 按存活
session 过滤（`db_get_session_status` 为 `None` 时直接早退），而 `StaleSessionCleanup`
只遍历 `ReportSessions`——两者都看不到一条父 session 已消失的 pending 行。这与
[BFR-015](../BFR-015-Session-Orphan-Cleanup-And-Rollback-Policy/BFR-015-session-orphan-cleanup-and-rollback-policy.zh.md)
属于**同一类**孤儿（带 session 标签但无父行），但是**新机制**：BFR-015 的孤儿是
遗留 / 手工删除留下的 FK 子行，而这一条是 recovery-outbox 重放与回滚竞态**新造**出来的。

## Fix

仅做即时垃圾清理（根因代码修复列入 Follow-Up）。本着 D1 为权威源的原则，直接在 D1
上删除了这条孤儿：

```sql
DELETE FROM PendingMovieHistoryWrites
WHERE SessionId='20260615T140318.589362Z-60d0-0000';   -- changes=1
```

清理后验证：该 session 0 行，全库 pending movie 0 行。`ALDN-131` 本就已存在于
committed `MovieHistory`（一条 2026-04-10 的旧记录），故没有真实历史丢失——孤儿只是
惰性垃圾。

失败的 run 已重跑（`gh run rerun 27551555092 --failed`）；D1 已恢复，重试正常推进。

## Side Effects

- 清理本身无副作用。被删的是一条无父 session 的惰性 `pending` 孤儿；`ALDN-131` 在
  committed `MovieHistory` 中的行未受影响。
- 本地 SQLite 镜像未重新同步——按 D1 权威源策略，它是只读镜像，由下一次
  `sync_d1_to_sqlite --force-overwrite-all` 重新对齐。
- 尚未加入复发防护，因此未来某次对“批处理 + 可恢复”写入的 D1 瞬时失败**仍可能复现**
  此孤儿（见 Follow-Up）。

## Follow-Up

- [ ] **给重放加前置校验**：`replay_ordering_key` 在应用一笔历史写入前，先确认其父
      `ReportSessions` 行仍存在（且未被回滚）；否则跳过该事件并标记为 processed /
      dead-letter，而非将其复活。
- [ ] **回滚与 outbox 协调**：让 `db_rollback_session` 排空或墓碑标记该 session 的
      outbox 事件（`ordering_key="history:<session_id>"`），使后续重放无法再应用它们。
- [ ] **新增回收器 / 覆盖**：针对 `SessionId` 无 `ReportSessions` 父行的 pending 行
      （扩展 `javdb/migrations/tools/cleanup_orphaned_session_rows.py` 并加一个周期性
      检查），因为 commit 与 `StaleSessionCleanup` 都覆盖不到这种情况。
- [ ] **回归测试**：复现“回滚后重放”，并断言不残留任何孤儿 pending 行。
