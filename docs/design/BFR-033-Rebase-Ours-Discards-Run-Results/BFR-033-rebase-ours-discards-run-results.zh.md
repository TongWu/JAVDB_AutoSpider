# BFR-033：自动提交推送冲突时 `git rebase -X ours` 丢弃了本次运行自己的结果

**Status**: Fixed
**Date**: 2026-08-12
**Severity**: High（每次发生冲突的并发自动提交都会静默丢失部分数据）
**Affected**: `.github/workflows/DailyIngestion.yml`、`.github/workflows/AdHocIngestion.yml`
**Related**: [BFR-035](../BFR-035-Concurrent-Run-Cross-Session-Commit/BFR-035-concurrent-run-cross-session-commit.zh.md)（同一起 2026-07-26 并行 AdHoc 事故）、[BFR-007](../_archive/BFR-007-Reports-Artifact-Stale-Files/BFR-007-reports-artifact-stale-files.zh.md)、[BFR-034](../BFR-034-Sqlite-Lastrowid-As-D1-Foreign-Key/BFR-034-sqlite-lastrowid-as-d1-foreign-key.zh.md)（同一轮审查批次的姊妹缺陷）、提交 `4cc7569a`

---

## 现象

一次抓取运行正常完成、正常把数据提交进 D1，其自动提交步骤打印了成功横幅，Job 变绿——但它的数据行在 git 里的共享报表文件中不见了。日志、通知邮件、`git log` 里都没有任何迹象表明发生了丢失。

两个 ingestion 工作流都以一个推送重试阶梯收尾：当远端已经向前推进时，阶梯会逐级升级四种策略。最后一级是：

```bash
# Try to accept our changes to resolve conflict
echo "Attempting to resolve conflicts by keeping local changes..."
if git rebase "origin/$CURRENT_BRANCH" -X ours 2>/dev/null; then
  echo "Rebase with ours strategy successful"
```

发生冲突时它以 `0` 退出、打印 `Rebase with ours strategy successful`、继续走到 `git push`，最后打印：

```
✓ Changes committed and pushed to $CURRENT_BRANCH branch successfully
```

## 根因

**在 `git rebase` 中，合并的两侧相对 `git merge` 是互换的。** rebase 是把本地提交逐个重放*到*上游之上，所以每次重放时，上游才是当前被检出的那一侧（`ours`），而正在被重放的提交——也就是本次运行的成果——是外来的那一侧（`theirs`）。因此：

| 命令 | `-X ours` 保留 | `-X theirs` 保留 |
| --- | --- | --- |
| `git merge origin/BRANCH` | 本次运行这一侧 | 另一次运行那一侧 |
| `git rebase origin/BRANCH` | **另一次运行那一侧** | **本次运行这一侧** |

这段阶梯是按 merge 的语义写的——注释写着 "accept our changes"，echo 写着 "keeping local changes"——但命令是 *rebase*。于是每一个冲突 hunk 都被解析成并发运行早已推送的内容，本次运行自己的 hunk 被丢掉。

有四个因素共同作用，让这次丢失变得静默而非显眼：

1. **语义颠倒。** 该策略做的事与它声明的意图恰好相反，而周围的文字断言的是意图，不是实际效果。
2. **`2>/dev/null`。** rebase 把冲突与自动解决的报告写到 stderr。把它静音掉，就抹掉了日志里唯一能证明有 hunk 被丢弃的证据。
3. **没有告警。** 对一个**破坏性**冲突做自动解决，却没有发出任何 `::warning::`，所以 Job 摘要和运行的 checks 里都不会浮现出来。
4. **并发是被支持的路径，不是意外。** `AdHocIngestion.yml` 刻意不设 `concurrency:` 组——它的头部注释明确说明并行派发是设计预期，因为按会话隔离靠的是 SessionId 与 pending-write 架构。所以阶梯的最后一级并不是什么冷门边界情况；重叠的 AdHoc 派发本来就会常态性地追加写入同一批共享报表文件。

更深层的缺陷在于：pending-write / SessionId 架构让**数据库**在并发下是安全的，而自动提交步骤默默继承了「git 也会同样安全」这个假设。它并不安全：共享报表文件是面向行的、以追加为主的，并且每次运行都会重写。

## 证据

一起真实的生产数据丢失，完全可以从已提交的状态中重建。

2026-07-26 有两次 AdHoc 运行发生重叠。二者都记录在 `reports/D1/d1_drift.jsonl`（第 129-130 行），相隔 8 秒：

| 运行 | 会话 | `hrefs_processed` | `movies_upserted` | `commit_session` 时刻 |
| --- | --- | --- | --- | --- |
| [30195210787](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/30195210787) | `20260726T085050.063768Z-889e-0000` | 49 | 49 | `2026-07-26T08:58:37Z` |
| [30195386608](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/30195386608) | `20260726T085643.729472Z-502b-0000` | 29 | 29 | `2026-07-26T08:58:45Z` |

第一次运行在 16:59:31 SGT 干净地推送成功（`9f77f81a`），对 `reports/parsed_movies_history.csv` 的改动是 `+69 / -25`。第二次运行的自动提交直到 17:07:20 SGT 才落地（`32e4a528`）——晚了八分钟，也就是在把重试阶梯走完之后——它的 stat 行正是这个缺陷的指纹：

```
 reports/AdHoc/2026/07/Javdb_AdHoc_actors_彌生美月_弥生みづき_20260726.csv | 81 ++++++++++
 reports/parsed_movies_history.csv                                       |  4 --
 2 files changed, 81 insertions(+), 4 deletions(-)
```

一次向 D1 upsert 了 29 部影片的运行，对共享历史 CSV 的贡献是**零**行新增，还*删掉*了四行。带日期的 AdHoc CSV 完好无损（它是该次运行独有的，因此从不冲突）；只有共享文件丢了内容。该文件名中的女优（`彌生美月_弥生みづき`）与 [BFR-035](../BFR-035-Concurrent-Run-Cross-Session-Commit/BFR-035-concurrent-run-cross-session-commit.zh.md) 中记载的会话相符，由此确认 `32e4a528` 就是运行 `30195386608`。

追踪其中一条被删掉的行在各个版本中的存在情况：

```bash
$ for rev in 9f77f81a 32e4a528 23def421 HEAD; do
    printf "%s: " $rev
    git show $rev:reports/parsed_movies_history.csv | grep -c "^/v/nKem06,"
  done
9f77f81a: 1
32e4a528: 0
23def421: 0
HEAD: 0
```

`/v/nKem06`（`HNDS-078`）在冲突推送之前存在，之后消失，并且再也没有回来——连 `23def421`（下一次 AdHoc 运行，重新序列化了同一文件的 745 行）也没能把它带回来。

## 影响范围

- **丢失是按冲突 hunk 局部发生的，而不是整个提交。** 运行会保留所有不冲突的改动，只丢掉发生碰撞的那些 hunk。没有失败的步骤、没有空提交、也没有缺失的文件可供察觉。要发现它，必须把 Job 日志里的提交 stat 与实际落地那个提交的 stat 相互对比。
- **生产配置 `STORAGE_BACKEND=d1` 下受影响的文件**——即自动提交步骤无条件 stage 的所有路径（`DailyIngestion.yml` → `Commit and Push Results` → `STEP 1: Stage all files` 代码块）：`reports/D1/d1_recovery_outbox.jsonl`、`reports/D1/d1_recovery_outbox.processed.jsonl`、`reports/D1/d1_drift.jsonl`、`reports/D1/d1_drift.processed.jsonl`、`reports/D1/d1_port_summary.json`，以及带日期的 `reports/DailyReport/**.csv`、`reports/AdHoc/**.csv` 和 `reports/parsed_movies_history.csv`。
- **代价最高的是那些 D1 状态日志。** `d1_recovery_outbox.jsonl` 与 `d1_drift.jsonl` 不是任何东西的镜像——它们是分歧记录以及待重放写入的**唯一**凭据，之所以被无条件 stage，正是为了让它们能在失败运行中存活下来。这里丢一行就是不可恢复的。`parsed_movies_history.csv` 在 `d1` 模式下确实是镜像，但当启用历史加载时它仍会被 spider 读回作为历史输入（`javdb/spider/app/run_service.py` 中的 `load_parsed_movies_history`），因此被丢掉的行可能导致重复处理。
- **竞争范围比 Daily-vs-AdHoc 更广。** `Migration.yml` 与 `WeeklyDedup.yml` 的 `Commit and push results` 步骤通过同一个 `D1_STATE_FILE` 循环 stage 完全相同的五个 `reports/D1/` 状态文件。这些工作流中任意两个发生重叠都在暴露面内——两个 ingestion 工作流只是恰好带着那段有缺陷阶梯的那一对。

## 修复

提交 [`4cc7569a`](https://github.com/TongWu/JAVDB_AutoSpider_CICD/commit/4cc7569a) —— `fix(ci): keep this run's changes when auto-resolving push conflicts`。

1. **在两个工作流中把 `-X ours` 改成 `-X theirs`**（`DailyIngestion.yml` 与 `AdHocIngestion.yml`），让行为终于与声明的意图一致。
2. **取消对 rebase 的静音。** 删掉 `2>/dev/null`，并加上明确的*「不要重新加回去」*注释，使冲突报告留在 Job 日志上。
3. **为自动解决加上告警。** 只要该策略被触发，就发出一条 `::warning::`，点名需要检查的共享报表文件：

   ```
   ::warning::Push conflict auto-resolved with -X theirs (this run's version kept
   for conflicting hunks) — verify shared report files
   (reports/D1/d1_recovery_outbox.jsonl, reports/D1/d1_drift.jsonl,
   reports/DailyReport/**.csv) for rows dropped from a concurrent run
   ```
4. **修正四处过时注释**，它们此前都把 `-X ours` 说成是覆盖机制（每个工作流两处，分别在 `STORAGE_BACKEND` env 注释与 `stage_db_mirrors` 注释中）。
5. **用契约测试固定该行为**：`tests/unit/test_workflow_push_conflict_strategy.py`，对两个工作流参数化执行：`test_forced_rebase_keeps_this_runs_hunks`、`test_forced_rebase_output_is_not_silenced`、`test_forced_rebase_success_is_annotated`。

阶梯的整体形状未变：普通 rebase → merge → 带策略的 rebase → 回退分支（`.github/scripts/fallback_push.sh`）。只有最后一级的解析方向、可见性与告警发生了变化。

## 副作用

- **丢失的方向被反转了，而非被消除。** `-X theirs` 会把冲突 hunk 整体解析成本次运行的版本，因此该 hunk 中*另一次*运行的行会被丢掉。这是一个刻意的取舍：现在吃亏的一方是那次已经推送成功、已经产出自己的产物与邮件的运行；而此前吃亏的是手里正捧着未保存结果、当场站在那里的那次运行。而且现在会由 `::warning::` 公开宣告。无论方向如何，整 hunk 取舍这种原语对于追加型文件本来就是错的——正因如此，`merge=union` driver（见「后续工作」，同一批次已落地）比方向翻转更重要：它让那些文件根本走不到会造成丢失的那一级。
- **冲突路径上的 Job 日志变吵了。** rebase 的冲突输出现在会显示出来。这是有意为之；它正是此前缺失的那份证据。
- 阶梯的控制流、重试次数、退避策略、回退分支行为均无变化。没有工作流输入、配置键或环境变量发生变化。无 schema 变更，无需迁移。

## 后续工作

- [x] 在两个 ingestion 工作流中把策略选项改为 `-X theirs`
- [x] 停止在带策略那一级静音 rebase 的 stderr
- [x] 该策略每次自动解决冲突时发出 `::warning::`，并点名需要审计的文件
- [x] 修正四处提及 `-X ours` 的过时注释
- [x] 用契约测试固定策略选项、取消静音与告警三项
- [x] **为追加型报表文件引入 union merge driver。** 对于两侧的行都需要保留的文件，整 hunk 取舍不可能正确，因此 `.gitattributes` 现在把追加型报表文件标记为 `merge=union`，让 git 保留两次运行的行，而不是挑一个赢家——这意味着阶梯的*第一*级（普通 `git rebase`）就能无损成功，会造成丢失的那一级对它们根本不会触发：`reports/D1/d1_drift.jsonl`、`reports/D1/d1_drift.processed.jsonl`、`reports/D1/d1_recovery_outbox.jsonl`、`reports/D1/d1_recovery_outbox.processed.jsonl`、`reports/pikpak_bridge_history.csv`、`reports/DailyReport/**/*.csv`、`reports/AdHoc/**/*.csv`。整体重写型的文件被刻意排除、保留默认 driver（`d1_port_summary.json`、`rclone_inventory.csv`、`dedup_history.csv`、`parsed_movies_history.csv`，以及走 LFS 的 `reports/*.db`）——把一份被整体重写的文档的两半拼接起来并不构成一份有效文档。该项作为本轮审查批次中的姊妹改动已落地，提交 [`85fd3cfa`](https://github.com/TongWu/JAVDB_AutoSpider_CICD/commit/85fd3cfa)（`.gitattributes` 内已随行记录了依据）。
- [x] 审计另外两个写入同一批 `reports/D1/` 状态文件的工作流。`Migration.yml` 与 `WeeklyDedup.yml` 的 `Commit and push results` 推送循环**没有**这个缺陷——它们用的是不带策略选项的 `git pull --rebase origin "$CURRENT_BRANCH" || true`——但 `|| true` 是把冲突的 rebase 吞掉而非解决掉，这是作用在同一批共享文件上的另一种静默失败形态，值得单独检视。**已完成。** 排查发现还有第三个写入方带着完全相同的循环（`RcloneManager.yml` → `Commit and push results`），以及六个运行前的 `Pull latest changes` 步骤带着同一处吞掉。这九处现在都用 `if` 检查 rebase 的结果、失败时 `git rebase --abort` 以确保工作树绝不停留在冲突中间态（旧的 `|| true` 会把整个重试预算耗在 *“there is already a rebase-merge directory”* 上），并用 `::warning::` 标注；三个自动提交循环在重试耗尽后仍然带着 `$LOCAL_COMMIT` 升级到 `fallback_push.sh`，六个运行前的 pull 保持非致命（此时还没有任何产物会丢失）但不再静默。已修复：`Migration.yml`、`WeeklyDedup.yml`、`RcloneManager.yml`、`RollbackD1.yml`、`DailyIngestion.yml`、`AdHocIngestion.yml`（仅运行前的 pull——push 阶梯未做改动）。由 `tests/unit/test_workflow_git_push_not_swallowed.py` 全库固定：任何 git push/pull/rebase/merge 的失败被 `|| true` 丢弃都会导致测试失败，只豁免"清理自身前置 rebase 的 abort"——即孤立的 `git rebase --abort || true`，或紧邻前一段是 `git pull --rebase` / `git rebase` 的 abort（因此 `git push … || git rebase --abort || true` 仍会被标记）。
- [ ] 考虑是否值得只针对**自动提交步骤本身**（而非整条流水线）加一个 `concurrency:` 组：这样可以在不放弃 AdHoc 刻意依赖的并行派发特性的前提下消除冲突窗口。
