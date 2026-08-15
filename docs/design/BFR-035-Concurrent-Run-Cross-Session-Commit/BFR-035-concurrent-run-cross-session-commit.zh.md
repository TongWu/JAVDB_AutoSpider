# BFR-035：并发 ingestion run 互相提交对方的活跃 session，导致 pending 写入被搁浅

**状态**：Fixed
**日期**：2026-07-26
**严重程度**：High（每一对重叠的 AdHoc run 都会造成静默的历史数据丢失）
**影响范围**：`apps/cli/db/commit_session.py`、`.github/workflows/StaleSessionCleanup.yml`、`docs/handbook/{en,zh}/ops/d1-rollback.md`
**相关**：[ADR-006](../_archive/ADR-006-Pending-Mode-Rollout/ADR-006-pending-mode-default-rollout.zh.md)（暂停闸门）、[BFR-002](../_archive/BFR-002-Commit-Session-Misleading-Log/BFR-002-commit-session-misleading-log.zh.md)、[BFR-021](../BFR-021-D1-Session-Write-Silent-Loss/BFR-021-d1-session-write-silent-loss.zh.md)

---

## 症状

一次 Ad-Hoc run 报告成功，但通知邮件的主题里带着 pending 模式的严重告警：

```
[PENDING-PAUSE] (pending_residual_count=255 > 0 session=20260726T085643.729472Z-502b-0000) ✓ SUCCESS - JavDB Ad-Hoc Report 20260726 [彌生美月_弥生みづき]
```

该 run 自己的 `Mark sessions as committed` 步骤看起来毫无异常 —— 只是报告
session 已经处理完，没有任何东西需要 drain：

```
Commit done: committed=0 already_committed_or_missing=1 failed=0 claim_commits=1
"already_committed_or_missing": ["20260726T085643.729472Z-502b-0000"]
"pending_session_drains": []
```

事后查 D1：`Status='committed'`、`CommittedAt='2026-07-26T08:58:42.035Z'`
—— 比 spider 结束时间早了大约三分钟 —— 同时还有 153 条
`PendingMovieHistoryWrites` + 102 条 `PendingTorrentHistoryWrites` 停留在
`ApplyState='pending'`，而该 session 的 80 部影片里只有 29 部进了
`MovieHistory`。

## 根因

`commit_session` 允许同时传 `--session-id` 和 `--run-started-at`，并提交两者的
**并集**。后者是一个纯时间窗扫描（`find_in_progress_sessions(since=...)`），
**完全不带 run 作用域** —— 它会匹配该时间点之后创建的每一个
`Status='in_progress'` session，不管这个 session 属于哪个 workflow run。

`AdHocIngestion.yml` 刻意没有配置 `concurrency:` 分组（整套 D1 + Durable
Object + pending-write 架构存在的意义，正是让不同 URL 的并发 ingestion 互不
冲突），所以 run 重叠是常态。实际发生的事：

| 时间（UTC） | 事件 |
|---|---|
| 08:45:56 | run **A** `30195210787` 启动，`RUN_STARTED_AT=08:46:03` |
| 08:51:46 | run **B** `30195386608` 启动 |
| 08:56:43 | B 创建 session `20260726T085643…` —— 落在 A 的时间窗内 |
| 08:58:35 | A 提交自己的 session（245 行） |
| **08:58:42** | **A 提交了 B 还在爬的 session** —— 当时已 staged 的 145 行被应用，`Status→committed` |
| 08:58:45–09:01:29 | B 继续 staging → 又产生 255 行 pending，而 session 已经是 `committed` |
| 09:06:06 | B 自己的 commit 步骤看到 `sess_status == 'committed'`，直接跳过 drain（`commit_session.py` 中 `write_mode == 'pending' and sess_status != 'committed'` 那道守卫）→ 255 行被搁浅 |
| 09:07:53 | 邮件步骤读到 `pending_residual_count=255` → `[PENDING-PAUSE]` |

A 的日志是直接证据 —— 一次调用，两个 session，其中只有一个是它自己的：

```
Executing: python3 -m apps.cli.db.commit_session --run-started-at 2026-07-26T08:46:03Z --session-id 20260726T085050.063768Z-889e-0000
Pending session committed: id=20260726T085050.063768Z-889e-0000 … pending_marked_applied: 245
Pending session committed: id=20260726T085643.729472Z-502b-0000 … pending_marked_applied: 145
Commit done: committed=2 …
```

`ReportSessions.RunId` 在 session 创建时本来就会从 `GITHUB_RUN_ID` 写入，也就是说
防止这个问题所需的归属信息一直都在 —— 只是时间窗扫描从来没有去读它。

## 修复

1. **`apps/cli/db/commit_session.py`** —— `--run-started-at` 的时间窗现在与携带
   本次 run `RunId` 的 session 取交集（`SessionLifecycleRepo().find_sessions_by_run`，
   以 `GITHUB_RUN_ID` / `GITHUB_RUN_ATTEMPT` 为键）。不属于本 run 的 session 会被
   跳过，并记录一条说明性的 INFO 日志。本地运行时 `RunId` 为 NULL 且不存在并发同伴，
   因此扫描保持不受限。

   归属查询刻意**不**走 `find_run_sessions` helper —— 该 helper 吞掉异常并返回
   `[]`，会让 D1 的瞬时故障与「本 run 确实没有其他 session」无法区分，从而静默跳过
   整个窗口。现在查询失败一律以非零退出：没有显式 `--session-id` 时已无事可做；有显式
   id 时（两个 ingestion workflow 都是这种调用形态）先提交该 session —— `committed`
   状态可免受失败清理影响，其写入得以落地 —— 然后让该步骤变红，促使运维人员在 48 小时
   stale sweep 把未识别的兄弟 session 回滚掉之前介入。绿色运行里的一条 ERROR 日志保护
   不了任何数据。由 `tests/unit/test_rollback_commit_cli.py` 中的五个回归测试覆盖。

2. **`.github/workflows/StaleSessionCleanup.yml`** —— `INPUT_APPLY` 原本只读
   `${{ inputs.apply }}`，而 cron 触发时 `inputs` 未设置，于是这个每日安全网
   **一直只在跑 dry-run**，从未真正清理过任何东西。现在改为
   `${{ github.event_name == 'schedule' || inputs.apply }}`：cron 执行 apply，手动
   dispatch 保留 dry-run 默认值。

   仅仅打开 cron 是不够的。回滚 `in_progress` session 丢弃的是从未落地的写入 ——
   无人值守也安全；但**恢复** `finalizing` session 会把其 staged payload 重放到正式
   表上，而该 payload 按定义至少有 `--max-age-hours` 之久，更晚的 run 完全可能已经
   重爬过同一个 `Href`。因此 `cleanup_stale_in_progress` 新增了
   `--no-resume-finalizing` 开关，改为把这类 session 报告为 `needs_manual_review`；
   只有定时任务会传该开关，因此人工 dispatch 仍会恢复 —— 前提是运维人员勾选了
   `apply`，因为 `workflow_dispatch` 保持 dry-run 默认值。这类 session 被单独计数
   （`manual_review_count`）而不是并入 `sessions_cleaned`，每个都会输出 WARNING；
   但运行仍刻意以 0 退出，因为在 alignment 泄漏未修之前它们每周都会累积，而一个天天
   变红的 cron 就是一个没人再看的 cron。带冲突检测的自动恢复留作后续，不在此处
   半成品化。

3. **`docs/handbook/{en,zh}/ops/d1-rollback.md`** —— 「已提交 session 上
   `pending_residual_count > 0`」那一行原本断言正式表必然已经正确，并指导运维人员
   直接清除残留。在本次事故的情形下，这条建议会**销毁尚未应用的写入**：手动
   `DELETE` 和 `db_commit_session_history` 走的都是 committed 分支，会删除
   `ApplyState IN ('pending','applied')` 的行而不做任何应用。

   该行现在的结论是：只能 drain，绝不能删 —— 并明确写出 `pending_residual_count`
   **无法**确立的两件事。

   **它不能证明写入从未落地。** `_commit_session_bulk` 先在一个批次里 UPSERT 正式表，
   之后才在另一个批次里标记 `ApplyState='applied'`；在 D1 上这是两个独立请求，之间
   没有事务，因此中途失败会留下「数据已应用、行仍为 `'pending'`」的状态。`'pending'`
   的含义是**未确认**。这仍然使得删除永远是错的（万一数据真的缺失则不可恢复），但也
   意味着 drain 是一次**重放**：对已应用的行会用旧 payload 重新 UPSERT，覆盖
   `SessionId`、`DateTimeVisited`、演员字段以及种子的
   `MagnetUri`/`Size`/`FileCount`/`ResolutionType`。

   **它不能说明正式表里那一行现在归谁所有。** 对较老的 session 执行 drain，可能把更晚
   的 session 已更新的行回退掉。运维手册现在给出了 drain 前的检查查询，并说明在回退不可
   接受时应改为重爬受影响的 `Href`。

   本次事故的恢复没有触及这两个风险：全部 51 个 `Href` 在 drain 前已核实不存在于
   `MovieHistory`。这一个检查对**两张**正式表都足够，理由值得写清楚，因为它并不显然：
   `TorrentHistory` 根本没有 `Href` 列 —— 它挂在
   `MovieHistoryId INTEGER NOT NULL REFERENCES MovieHistory(Id)` 上，因此一个没有
   movie 行的 `Href` 也不可能有 torrent 行。drain 的结构也印证这一点：
   `live_movies_by_href.get(href)` 返回 `None` 会让它走 INSERT 路径并生成全新的
   `movie_id`，该 id 对应的 `live_torrents_by_mid` 必然为空，于是每一条 torrent 写入
   都是 INSERT。不存在能触及既有行的 UPDATE 或变体 DELETE。

   本条目的四个早期草稿都是错的，记录于此是因为其失败模式有参考价值 —— 它们无一例外
   都产生**假零值**，即告诉运维人员「没有冲突，可以 drain」：

   1. 用「pending 的 `Href` 是否不在 `MovieHistory` 中」做分类。不成立：`MovieHistory`
      按 `Href` 全局去重，任何重爬的影片本来就在表里。已用当时正在运行的 session
      `20260726T093214…` 验证 —— 1885 行未 drain 的 pending，该检查返回零。
   2. 矫枉过正，把 `ApplyState` 当成「未应用」的证据，而上述「先写表、后标记」的顺序
      否定了这一点。
   3. 冲突检查只查了 `PendingMovieHistoryWrites`。Phase F **分批**标记两张 pending 表，
      因此「movie 行已 `applied`、torrent 行仍 `pending`」是合法的半应用状态 —— 此时
      只查 movie 表会返回零，而那些未应用的 torrent 行对应的 `Href` 可能已被更晚的
      session 接管。已用复现该状态的 fixture 验证（只查 movie → 0，并集 → 1）。
   4. 覆盖了两张表，但仍然只筛 `ApplyState='pending'`，而 drain 通过
      `_pending_distinct_hrefs` 读的是 `ApplyState IN ('pending','applied')`，
      `applied` 残留同样会被重放。若某个 session 的冲突行恰好全是 `applied`，检查就
      返回零。再次用 fixture 验证：只查 pending → 0，按 drain 的状态集合 → 1。

   最终成立的规则，也是任何类似检查都值得沿用的一条：**drain 前的安全检查必须与 drain
   自身的范围完全一致** —— 相同的表，相同的状态。四个草稿各自以不同方式收窄了这个范围，
   而每一次都产生假零值，即告诉运维人员「没有冲突，动手吧」。从「残留」这个词的直觉含义
   出发去推导检查，而不是从 drain 实际读取的内容出发，正是这个 bug 反复回来的原因。

   同一轮 review 还移除了 `needs_manual_review` 那一行里一个批量形态的陷阱：它曾把
   「dispatch StaleSessionCleanup 并勾选 `apply`」作为恢复单个 session 的替代方案，但该
   workflow 没有 session id 输入，会恢复所有符合条件的过期 session —— 把运维人员没做过
   冲突检查的那些一并重放。两处现在都改为用 `commit_session --session-id` 逐个恢复。

## 恢复过程

那 255 行搁浅数据通过重开 session 并走正常路径 drain 完成恢复：

```sql
UPDATE ReportSessions SET Status='finalizing', CommittedAt=NULL
 WHERE Id='20260726T085643.729472Z-502b-0000' AND Status='committed';
```

```bash
python3 -m apps.cli.db.commit_session \
  --session-id 20260726T085643.729472Z-502b-0000 --no-claim-commit
```

结果：`pending_marked_applied: 255`、`movies_upserted: 51`、
`torrents_upserted: 102`、`hrefs_processed: 51`。该 session 的 `MovieHistory`
从 29 → 80，`TorrentHistory` 从 58 → 160，残留归零。

`committed → finalizing` 不是合法的生命周期转换，也没有对应 CLI —— 直接对 D1 执行
`UPDATE` 是有意为之，这也是运维手册现在把它写清楚的原因。

## 副作用

- **ADR-006 的暂停从未生效。** 邮件任务创建了 24 小时暂停的 commit，但 `git push`
  被并发 run 的 push 顶掉（`! [rejected] main -> main (fetch first)`），而该步骤用
  `git push || echo "::warning::pause commit push failed"` 把失败吞掉了。安全网的
  检测这一半正常工作，执行这一半静默失败。本次未修 —— 见「后续」。
- **另外 3 个无关的 `alignment` session**（2026-07-05 / 07-12 / 07-19）被发现卡在
  `finalizing`，合计 2185 行未 drain 的 pending，能存活至今全靠上面那个 cron
  dry-run bug。已在本次修复中通过 `cleanup_stale_in_progress --apply` resume
  （1021 部影片、1164 个种子、drift 为 0）。

  **这次 resume 本身造成了一处小规模回退**，是事后在 review 揭示重放风险时才发现的。
  那些 payload 已是 1-3 周前的，因此重放改写了 402 行**既有** `MovieHistory` 的
  `SessionId` / `DateTimeVisited` / `DateTimeUpdated`（这些行的 `DateTimeCreated`
  是 2025-06-29，并非新建行）。绝大多数在此期间没被动过：这些影片下的种子有 401 个
  属于 alignment session 自己，只有约 6 个属于更晚的 run（07-15 / 07-16 / 07-23 的
  daily），因此回退仅限于少量行的溯源元数据，没有丢失种子内容。resume 前的状态
  **无法**重建 —— 本地 SQLite 镜像停留在 2026-05-30，早于这几次 alignment 运行。

  教训在于顺序：cron 修复在真实数据上被启用并执行，发生在真正理解
  `db_resume_finalizing_session` 的重放语义**之前**。开启一条自动路径，应当与编写
  一条自动路径受到同等审视。
- 并发自动提交只影响共享的 `reports/D1/d1_drift.jsonl` 产物：最后推送的 run 覆盖
  前者，因此 B 自己的 verify 记录从未进入 git。告警路径是在 commit 之前于 runner
  上读取该文件的，所以检测不受影响。

## 后续

- [x] 让 ADR-006 的暂停 push 能扛住并发推送。已由 PR #258 修复并合并。在它落地之前，
      暂停闸门的执行这一半会像本次事故里那样静默失效 —— 检测正常，marker 从未到达
      `main`。
- [x] 调查 `align_inventory_with_moviehistory` 为何会把自己的 session 留在
      `finalizing` 且带着未 drain 的行。已由 PR #259 修复：该工具默认走 SQLite 镜像，
      而陈旧的镜像会让 drain 中途中止。在被发现之前，连续三次周任务受影响。

      这削弱但并未消除上文「cron 对 `needs_manual_review` 以 0 退出」的理由：#259
      合并后这类 session 应当不再每周累积，因此在若干个周期验证之后，该策略值得与
      下方的冲突感知恢复一并重新评估。
- [ ] 考虑让 `commit_session` 把「session 已是 `committed` 但
      `pending_residual_count > 0`」当作硬失败，而不是静默的
      `already_committed_or_missing`，这样 run 会变红而不是变绿。
- [ ] 为 `finalizing` session 实现带冲突检测的恢复：识别出已被更晚 session 接管的
      正式表行，跳过或合并，然后让 cron 重新自动恢复。在此之前 cron 只做报告。

## 教训

时间窗不等于归属声明。任何跑在「明确允许并发执行」的 workflow 里的「扫掉 T 之后的
所有东西」式查询，都必须按 run 身份限定作用域 —— 这个身份列一直存在、也一直有值，
只是没被用上。rollback CLI 做对了（它的时间窗扫描被 `--include-orphaned` 或
「其他来源都没找到东西」这两个条件挡着）；而破坏性更强的 commit CLI 没有做对。
