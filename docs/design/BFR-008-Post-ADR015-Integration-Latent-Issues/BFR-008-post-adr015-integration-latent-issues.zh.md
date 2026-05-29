# BFR-008：ADR-015 之后遗留的 integration 潜在问题

**状态**：已修复 (Fixed)
**日期**：2026-05-29
**严重程度**：中 (Medium)
**影响范围**：`javdb/integrations/rclone/manager/service.py`、`javdb/integrations/notify/email/_config.py`、`javdb/integrations/notify/email/log_analysis.py`、`javdb/integrations/notify/email/service.py`、`javdb/integrations/qb/uploader/service.py`、`javdb/integrations/qb/file_filter/service.py`、`javdb/integrations/pikpak/bridge/service.py`、`apps/cli/{notify/email,qb/uploader,qb/file_filter,pikpak/bridge,rclone/manager}.py`
**关联**：[ADR-015](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.zh.md)、[PR #114](https://github.com/TongWu/JAVDB_AutoSpider_CICD/pull/114)、[Issue #115](https://github.com/TongWu/JAVDB_AutoSpider_CICD/issues/115)

---

## 现象 (Symptom)

CodeRabbit 在 ADR-015 PR（#114）的评审中提出 6 条意见：3 条是真实的潜在缺陷，3 条是健壮性/卫生问题。它们被登记为 #115 并从 #114 中暂缓，以保持该重构严格"行为保持"；本 BFR 修复它们。

1. **rclone 会把继承来的 session 标记为 failed。** `run_manager_from_options` 的扫描失败路径调用 `mark_session_failed(_staging_session_id)`，仅以 `_staging_session_id is not None` 为守卫。当 rclone 跑在已经持有 active workflow session 的流水线里时，`_staging_session_id` 是**继承**来的，于是一次部分失败的扫描会把共享的流水线 session 翻成 `failed`。
2. **导入期 `os.chdir` / `sys.path` 副作用。** 5 个 integration service 模块在模块导入时执行 `os.chdir(REPO_ROOT)` + `sys.path.insert(...)`。仅仅导入它们（例如 REST API 导入 `send_email` / `run_rclone_manager` / `pikpak_bridge`，或任何测试/并发任务）就会改写进程级全局 cwd 和 `sys.path`。
3. **D1 漂移 JSONL 解析遇脏数据会中断。** `log_analysis` 假设 `rec['ts']` 是字符串、且计数字段都可 `int` 转换；一条坏 JSONL 记录就会抛异常并中断整封邮件通知。
4. **`run_bridge` 返回占位结果。** PikPak CLI service 返回近乎空的 `PikPakBridgeResult`（只有 `dry_run`），导致程序化调用方即便真实执行了传输也只看到全 0 统计。
5. **qB service 的 `requests.Session` 泄漏。** `run_uploader` 与 `run_file_filter_cli` 在多个分支直接 return，未关闭 `requests.Session`。
6. **notify 读取 session 起始时间绕过仓储层。** `service.py` 用原生 SQL 读 `ReportSessions.DateTimeCreated`，而非走 storage repo。

## 根因 (Root Cause)

ADR-015 是**严格行为保持**的重构：它把代码原样从单体模块搬进 command/service 包。这忠实地保留了若干**既有**潜在问题（1、2、3、5、6）——它们早于 ADR-015，只是在大 diff 中才显形。第 4 条是新引入的：ADR-015 Phase 3 新增的 `run_bridge` 包装器有意返回占位结果，因为旧 `pikpak_bridge` 返回 `None`、旧 `main()` 只用进程退出码。

(1) 背后的设计缺陷：失败路径与成功路径不对称——`mark_session_committed` 早已用"是否本地创建该 session"（`_created_local_staging_session`）守卫，而 `mark_session_failed` 没有，于是继承来的 session 会被误改。(2) 背后：模块导入不是放进程级全局副作用的地方；cwd 约定应落在 CLI 入口，而非可复用 service 的导入。

## 修复 (Fix)

在分支 `claude/bfr-008-post-adr015-fixes` 实现：

1. **rclone** —— 给 3 处失败路径的 `mark_session_failed` 都加上 `if _created_local_staging_session:` 守卫（与成功路径对称）。`drop_rclone_staging` 保持**无条件**执行——per-session 的 staging 表永远是我们自己要清理的，即使 session 是继承来的。（此处有意偏离 CodeRabbit 的字面建议——它会连 drop 也跳过，从而泄漏 staging 表。）
2. **chdir/sys.path** —— 从 5 个 service 模块移除导入期的 `os.chdir(REPO_ROOT)` + `sys.path.insert(...)`（保留 `REPO_ROOT`）；把 `os.chdir(REPO_ROOT)` 移到每个 CLI adapter 的 `main()` 首句。导入 service 不再改 cwd；CLI 行为不变（生产以 `python -m apps.cli.*` 从 repo 根运行）。
3. **JSONL 健壮化** —— `ts = str(...)` 强转，并把三个计数器的解析放进 `try/except (TypeError, ValueError): continue`；坏记录被跳过（且不计数），不再中断邮件流程。
4. **`run_bridge` 统计** —— `_pikpak_bridge_impl` 现在在每个出口都返回统计 dict；`run_bridge` 将其映射成填充完整的 `PikPakBridgeResult`（`exit_code` 不变）。`pikpak_bridge` 保持透传返回。
5. **Session 生命周期** —— 两个 qB service 的 `requests.Session()` 都用 `try/finally: session.close()` 包住，覆盖所有 return 路径。
6. **仓储层** —— notify 读 session 起始时间改用 `SessionsRepo(_conn).get(_sid).created_at`，连接走显式的 `REPORTS_DB_PATH`（保持 sqlite-local）。

新增回归测试覆盖：(1) 继承 vs 本地 session 的标记差异，(3) 坏 JSONL 跳过，(4) `run_bridge` 结果被填充。

## 副作用 (Side Effects)

- (1) 行为修正：部分失败的 rclone 扫描不再把继承/上游流水线 session 翻成 `failed`；本地创建的 session 仍会被标记 failed。staging 表清理行为不变。
- (2) 导入任何 integration service 不再改进程 cwd / `sys.path`。生产 CLI 行为完全一致（cwd 本就是 repo 根；`main()` 会重新设定）。这消除了对 REST API、测试、并发任务的交叉污染隐患。
- (4) `_pikpak_bridge_impl` / `pikpak_bridge` 现在返回 dict 而非 `None`；REST 调用方忽略返回值，无影响。
- (3)、(5)、(6)：happy-path 行为不变；(3) 仅改变坏输入路径（崩溃 → 跳过），(5) 是资源卫生，(6) 是同样的数据走仓储抽象。
- 验证：定向套件 + 完整 `tests/unit` + `tests/architecture` = 3189 passed, 73 skipped；架构守卫通过；导入全部 5 个 service 后 cwd 不变。

## 后续 (Follow-Up)

- [ ] `javdb/integrations/notify/email/_config.py` 仍在导入期调用 `setup_logging(...)`（一个较小的、本次未覆盖的导入期副作用）。可考虑把全局日志初始化也移到 CLI 入口。
- [ ] 其它 integration service 模块也有同样的导入期 `setup_logging` 模式；可与 notify 一并评估。
