# BFR-002: commit_session 报告 session 已 committed 但同步 drain 出 126 条 pending writes

**Status**: Fixed
**Date**: 2026-05-28
**Severity**: Medium
**Affected**: `apps/cli/db/commit_session.py`, `javdb/storage/sessions/commit.py`, `javdb/storage/db/_db_history_write.py`
**Related**: [Issue #106](https://github.com/TongWu/JAVDB_AutoSpider_CICD/issues/106), ADR-005 (pending-only write mode)

---

## 症状

DailyIngestion run 26512511043 (2026-05-27) 在 2 秒内打出两条矛盾日志：

```
21:07:03 Pending session committed: id=20260527T130201.940249Z-f385-0000 mode=pending
  drain={'movies_upserted': 27, 'torrents_upserted': 48, 'torrents_deleted': 10,
         'pending_marked_applied': 126, 'pending_deleted': 126, ...}
...
21:07:05 Commit done: committed=0 already_committed_or_missing=1 failed=0
```

session 看起来同时被 drain（126 条 pending 行）又已经 committed。

## 根因

`db_commit_session_history()` 是 canonical pending drain 入口，并负责 `in_progress → finalizing → committed` 状态转换。一次成功 drain 之后，CLI 仍会把 `db_mark_session_committed()` 作为幂等的第二步调用。由于 drain 已经把行翻到 `committed`，`db_mark_session_committed()` 返回 0，而 CLI 之前只根据这个 rowcount 将 session 分类为 `already_committed_or_missing`。

日志有误导性，但状态机是正确的。`_db_history_write.py` 里也有一个显式的 `status == "committed"` 分支，它不是 no-op：当调用方直接对已 committed 的 session 调用它时，会 DELETE 上次未完成 drain 留下的残留 Pending 行，并且现在会在返回值里标记 `residual_cleanup=True`。

D1 验证确认无数据丢失：该 session 的 `PendingMovieHistoryWrites` 和 `PendingTorrentHistoryWrites` 都为 0 行。

## 修复

追踪 pending drain 是否成功执行。若已成功执行，即使后续幂等的 `db_mark_session_committed()` 返回 0，也将该 session 计入 `committed`。同时区分 CLI/service 日志中的"清理残留"路径和"drain 新鲜 pending"路径，让运维人员一眼就能区分两种场景。

## 副作用

无——仅日志措辞变更。

## 后续工作

- [x] 将由 `db_commit_session_history()` 提交的 session 在 CLI summary 中计为 committed
- [x] 区分残留清理和新鲜 drain 的日志消息
- [ ] 调查为什么 Pending 行在 finalizing→committed 过渡后仍然存在（D1 批次时序？）
