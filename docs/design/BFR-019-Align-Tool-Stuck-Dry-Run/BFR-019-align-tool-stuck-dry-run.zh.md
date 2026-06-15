# BFR-019: ADR-005 PR-4 后 inventory-alignment 迁移工具被困在「仅 dry-run」状态

**状态**: Fixed
**日期**: 2026-06-15
**严重程度**: Medium
**影响范围**: `javdb/migrations/tools/align_inventory_with_moviehistory.py`、`javdb/migrations/migrate_to_current.py`、`.github/workflows/WeeklyDedup.yml`（调用方）
**关联**: [ADR-005](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.zh.md)（amendment 9）、[IMP-ADR005-01](../_archive/ADR-005-Db-Py-Retirement/IMP-ADR005-01-drop-audit-mode.md)、[IMP-ADR005-02](../_archive/ADR-005-Db-Py-Retirement/IMP-ADR005-02-delete-db-facade.md)

---

## 症状

WeeklyDedup 工作流下游的 `align-inventory-history` job（以 `align_inventory_history: true`、
`dry_run: false` 调用 `Migration.yml`）**每周**都在 migration 步骤失败：

```
Executing: python3 -m apps.cli.db.migration --align-inventory-history --align-shuffle --align-enqueue-qb
…
✗ javdb.migrat  db_upsert_history was removed by ADR-005 PR-4. This tool cannot
                write history in non-dry-run mode until it is rewritten to use
                the staging+commit path.
Error: Process completed with exit code 1.
```

weekly dedup 本身成功；只有其后链式的 alignment job 变红。失败在进入
`run_alignment` 时立即触发，发生在任何 JavDB 抓取之前——所以日志里先是一段
干净的 schema 检查（`history.db already at v9 … No schema migration needed`），紧接着就是中止。

## 根因

这**不是**崩溃——而是一道被故意设置的 fail-fast 守卫，掩盖了一项**未完成的迁移步骤**。

ADR-005 D2 退役了 Audit Mode，PR-4 删除了直写路径 `db_upsert_history` /
`db_upsert_history_batch`。inventory-alignment 迁移工具
（`align_inventory_with_moviehistory.py`）正是用这些函数为「仅在 inventory、不在
MovieHistory」的影片代码写 `MovieHistory` / `TorrentHistory`。PR-4 的验证步骤已明确
把这处残留引用标记为 **「known PR-5 scope」**
（[IMP-ADR005-01](../_archive/ADR-005-Db-Py-Retirement/IMP-ADR005-01-drop-audit-mode.md)）。

但 PR-5（[IMP-ADR005-02](../_archive/ADR-005-Db-Py-Retirement/IMP-ADR005-02-delete-db-facade.md)）
只把该工具当作纯**导入改写**处理——从未执行标记所要求的写路径改写。为了避免测试收集
阶段的 `ImportError`（符号已不存在）以及运行中途的 `NotImplementedError`，符号被替换为
`_audit_retired_stub`，并在 `run_alignment` 中加了一道守卫：只要未设 `--dry-run` 就以 exit 1 中止：

```python
db_upsert_history = _audit_retired_stub        # 一旦被调用即抛 NotImplementedError

def run_alignment(args):
    if not args.dry_run and db_upsert_history is _audit_retired_stub:
        logger.error("db_upsert_history was removed by ADR-005 PR-4 …")
        raise SystemExit(1)
```

这道守卫本身是*正确*的防御选择（宁可大声失败也不写脏 history），但它所守护的状态——
「此工具无法写库」——是一个**非预期的遗留物**，并非设计的最终态。更深层的缺陷是一个
**流程缺口**：一项被推迟的迁移项（「known PR-5 scope」）被丢下却没有任何追踪载体
（没有 ADR follow-up、没有 BFR、没有 issue）。没有任何东西把这次推迟与某个调用方关联起来，
于是它一直不可见，直到唯一的非 dry-run 调用方——每周的 `align_inventory_history` job——
在大约三周后以反复变红的构建把它暴露出来。

## 修复

把 alignment 工具改写到 spider 同款的、按 session 范围的 **staging + commit** 路径
（即 ADR-005 D3 当初指定的替代路径）：

- `run_alignment` 通过
  `SessionLifecycleRepo().create_report_session(report_type='alignment',
  write_mode='pending', …)` 自行开启一个 pending `ReportSessions` 行（除非通过
  `--session-id` 显式接管），并从 `GITHUB_RUN_ID` / `GITHUB_RUN_ATTEMPT` 注入活跃 run 身份。
- `_BatchedHistoryWriter` 现在通过 `HistoryRepo.stage_movie` / `stage_torrent`
  （新 helper `_stage_aligned_movie`）暂存每条对齐的 movie 及其非空 torrent 分类，
  不再调用已删除的 `db_upsert_history`。存在的四个 torrent 分类都会被暂存，保留该工具
  ADR-005 之前的行为（而非 spider 的 best-of-pair 收敛）。
- 运行结束时通过 `HistoryRepo().commit_session` 提交 session（把 pending 排空到 live 表，
  走 `in_progress → finalizing → committed`）。
- session **仅在确有工作时才开启**（在 `if not missing_codes: return 0` 提前返回之后），
  因此空对齐永远不会创建孤儿 session。
- 整个 staging/commit 主体放进 `_run_alignment_core`，由 `run_alignment` 的守卫包裹：
  **任何**失败——批中暂存出错（如 `STRICT_DUAL_WRITE` 下的 D1 写失败）、意外异常，或并行/
  顺序路径上的 `KeyboardInterrupt`——都会通过 `SessionLifecycleRepo().rollback_session`
  回滚并重新抛出，而不是留下一个带已暂存 pending 写的 `in_progress` 行。这很重要，因为
  `Migration.yml` 没有 on-failure 清理步骤。（即便在此守卫之前，严重性也是有界的：
  `StaleSessionCleanup` 会在 48h 后把孤儿 `in_progress` session 回滚——只有 `finalizing`
  session 才会被 resume-to-committed——所以泄漏的 session 会被丢弃、绝不会被应用。该守卫让
  工具立即自愈，而非依赖 cron。）
- 移除 `_audit_retired_stub`、守卫，以及 `--dry-run` 上「重写前必需」的措辞。
- `migrate_to_current.py` 的 `align_ns` 现在显式携带 `session_id=None`（migration CLI
  不暴露 `--session-id`，故工具自行开启）。

测试（`tests/integration/test_align_inventory_with_moviehistory.py`）：新增对
`_stage_aligned_movie`（暂存全部非空分类、跳过空分类）、`_finalize_alignment_session`
（成功提交、失败回滚）以及端到端非 dry-run `run_alignment`（断言 开 session → 暂存 → 提交）
的覆盖。

ADR-005 已加 amendment 9 记录该遗留步骤的关闭。

## 副作用

- 对齐 job 现在真正会再次写库。其 `MovieHistory` / `TorrentHistory` 行在 commit 时原子落地
  （pending 模式语义），而非通过即时的 audit 模式 upsert——与其他所有摄取路径一致，
  并受 scoped rollback 约束。
- 每次对齐运行现在会创建一个 `ReportSessions` 行（`ReportType='alignment'`）。中断/陈旧的
  运行在进程内回滚；每日的 `StaleSessionCleanup` cron 仍是兜底。
- qB 升级 / rclone purge 规划与 CSV 输出均无变化。

## 后续

- [x] 把工具改写为 staging+commit 并移除守卫。
- [x] 为 staging+commit 与 rollback 路径补回归测试。
- [x] 在 ADR-005 记录该遗留步骤的关闭（amendment 9，双语）。
- [x] 对改写做对抗式多代理审查——暴露了一处 session 泄漏缺口（主体外无守卫）；已由
      `_run_alignment_core` 包裹层修复，并由 `test_run_alignment_rolls_back_session_on_core_error` /
      `test_run_alignment_empty_missing_codes_opens_no_session` 覆盖。
- [x] PR 评审轮（Codex）——处理了四项后续：
      (1) **演员元数据被覆盖** —— 空演员解析会暂存 `SupportingActors='[]'`（truthy，绕过了暂存层对
      name/gender/link 的 `'' → None` 保护），commit 会用它覆盖已有数据；`_blank_actor_field_to_none`
      现在把空白 / `'[]'` 演员字段置为 None，使 commit 保留已有行（与被替换路径的
      `_has_meaningful_actor_data` 守卫一致）。
      (2) **commit 异常泄漏** —— `_finalize_alignment_session` 现在对失败的 commit 重新抛出，使外层守卫的
      `rollback_session` 执行其状态感知清理（`finalizing` 续提交、`in_progress` 回滚），而非吞掉异常、把行
      留给 48h 清理。
      (3) **被接管的 `--session-id` 校验** —— `_verify_adoptable_session` 拒绝不存在 / 非 pending /
      非 `in_progress` 的 id，而不是暂存进去却以 0 退出且什么都没写。
      (4) **qB 入队失败** 仍保持在 commit 之后（把对齐与 qB 可用性解耦是有意的——短暂的 qB 故障不应迫使
      整轮 JavDB 重抓），但错误信息现在指向已持久化的升级 CSV，便于手动重入队。
- [ ] 确认合并后下一次定时 WeeklyDedup → `align_inventory_history` 运行为绿（合并后的下一次每周 cron）。
