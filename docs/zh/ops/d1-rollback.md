# D1 工作流回滚（Pending 模式 + 遗留 X3 audit 回退）

本文档是操作员在 pipeline 运行中途失败后，回滚 Cloudflare D1（及 SQLite）部分写入的参考手册。涵盖内容：

- Phase 3 **pending** 写入路径（自 2026-05 起为默认）以及遗留 X3 audit 回退（按附录 A 计划逐步淘汰）。
- 迁移后各表的状态。
- `DailyIngestion.yml` / `AdHocIngestion.yml` 中的自动 `cleanup-on-failure` 任务。
- 手动 `RollbackD1.yml` 工作流。
- "重新运行失败任务"安全矩阵——告诉你何时可以安全地点击 GitHub 原生的重试按钮而无需先执行 rollback。
- 直接 CLI 使用方式及 audit 表取证工作流。
- Phase 3 告警 + ADR-006 **告警-暂停**（`pending_session_verify`、健康快照、`pipeline_paused_until`）。原 audit 自动回退已由 ADR-006 PR-D 于 2026-05-16 退役；严重告警现在暂停 pipeline 而非降级到 audit 模式。
- 6 步预升级验证手册。
- **附录 A（Phase 4，2026-05-13）** — 遗留 audit 废弃时间线、日落日期及新的 `audit_archive` 定时任务。

> Phase 4 废弃（2026-05-13）：`WriteMode='audit'` 写入路径现已**废弃**，不再用于新 session。`db_upsert_history` 在每次调用时发出 `DeprecationWarning`；遗留的 `MovieHistoryAudit` / `TorrentHistoryAudit` 表仅作为历史 session 的**只读取证**数据。新的 `INSERT` 通过 `save_parsed_movie_to_history` 自动经由 `db_stage_history_write` 暂存；audit 回退作为紧急逃生阀保留至 **2026-08-13**（90 天日落窗口），确保任何未完成的迁移工作有一个确定性的退出截止日期。详见[附录 A](#appendix-a-legacy-audit-fallback-sunset-2026-08)完整时间线、`JAVDB_AUDIT_WRITES_DISABLED` 终止开关及新的 `audit_archive` 定时任务。

> 摄取完美回滚（Phase 3，2026-05）— `MovieHistory` / `TorrentHistory` 现在**仅在**提交时才被修改。Spider / detail / qb_uploader / pikpak_bridge 将每次写入暂存到 `PendingMovieHistoryWrites` / `PendingTorrentHistoryWrites`；成功的运行在一次遍历中将这些行导入正式表；失败则删除暂存行，而非重放。遗留 audit 路径作为回退保留（`WriteMode='audit'`），但不再是默认选项。

> 每个执行 D1 写入的工作流运行现在都逻辑上绑定到单个 `ReportSessions.Id` — 即 **session_id** — *以及*从 `GITHUB_RUN_ID` / `GITHUB_RUN_ATTEMPT` 派生的 `(RunId, RunAttempt)` 对。Rollback 可以通过任一方式寻址；运行标识是首选查找路径，因为即使之前的失败 rollback 已删除了所属的 `ReportSessions` 行，该标识仍然有效。

## 目录

- [摘要](#摘要)
- [策略概要（Pending 默认 + X3 audit 回退）](#策略概要pending-默认--x3-audit-回退)
  - [为什么 history 需要 audit 表？*（遗留——保留供上下文参考，参见附录 A）*](#为什么-history-需要-audit-表遗留保留供上下文参考参见附录-a)
  - [SessionId 生成（2026-05-08+）](#sessionid-生成2026-05-08)
  - [Rollback CLI 查找优先级](#rollback-cli-查找优先级)
  - [提交时的 Audit 保留](#提交时的-audit-保留)
  - [冒烟测试清理策略](#冒烟测试清理策略)
  - [`(RunId, RunAttempt, CsvFilename)` 不变式](#runid-runattempt-csvfilename-不变式)
- [Session 生命周期](#session-生命周期)
- [自动 cleanup-on-failure](#自动-cleanup-on-failure)
- [手动 rollback 工作流（`RollbackD1.yml`）](#手动-rollback-工作流rollbackd1yml)
- ["重新运行失败任务"安全矩阵](#重新运行失败任务安全矩阵)
- [直接 CLI 使用](#直接-cli-使用)
  - [事故响应工具（一次性脚本）](#事故响应工具一次性脚本)
  - [手动标记 session 为已提交](#手动标记-session-为已提交)
- [Audit 表取证 *（自 Phase 4 起只读，2026-05-13）*](#audit-表取证自-phase-4-起只读2026-05-13)
- [漂移处理](#漂移处理)
- [Schema 迁移](#schema-迁移)
- [Pending 模式（当前默认）](#pending-模式当前默认)
  - [Pending 状态机](#pending-状态机)
  - [清理调度矩阵（Phase 3）](#清理调度矩阵phase-3)
  - [Pending 模式指标（`pending_session_verify`）](#pending-模式指标pending_session_verify)
  - [邮件 Pending 模式验证 + 健康快照](#邮件-pending-模式验证--健康快照)
  - [告警 + 暂停（`.publish-config.yml`）— ADR-006 PR-D](#告警--暂停publish-configyml-adr-006-pr-d)
  - [操作员恢复 SOP](#操作员恢复-sop)
- [验证手册（dev 分支 — Phase 3，6 步）](#验证手册dev-分支--phase-36-步)
- [文件索引](#文件索引)
- [Appendix A — 遗留 audit 回退（2026-08 日落）](#appendix-a--遗留-audit-回退2026-08-日落)
  - [A.1 时间线](#a1-时间线)
  - [A.2 "废弃"在实践中的含义](#a2-废弃在实践中的含义)
  - [A.3 Audit 归档定时任务（`AuditArchive.yml`）](#a3-audit-归档定时任务auditarchiveyml)
  - [A.4 遗留 audit 模式验证手册（保留用于回退）](#a4-遗留-audit-模式验证手册保留用于回退)

## 摘要

- **运行失败？** 无需操作。`cleanup-on-failure` 任务会在 `DailyIngestion` / `AdHocIngestion` 上自动运行，撤销该次运行的未提交 D1 写入。
- **需要手动清理？** 运行 `Rollback D1 Session` 工作流，先设 `dry_run=true` 预览，然后再以 `dry_run=false` 重新运行。
- **找不到 session_id？** 传入 `run_id` + `attempt`（失败运行的 GitHub 标识）— rollback CLI 的首选查找路径会找到该工作流运行涉及的每个 session，即使 `ReportSessions` 行已被删除。`run_started_at` 仍可作为回退时间窗口扫描，但仅在设置 `--include-orphaned` 时生效（遗留的无条件扫描现为可选，以避免误伤兄弟 session）。
- **跨日拒绝：** CLI 会拒绝任何 `DateTimeCreated` 早于 `--run-started-at` 超过一小时的候选 session。这防止了 2026-05-08 事件类型——一个过期的 `--session-id` 意外指向了前一天的 session。传入 `--force` 可覆盖。
- **成功运行受保护。** 任何标记为 `Status='committed'` 的 session 在未设置 `force=true` 时拒绝回滚。已提交的 session 还会自动清理其 `MovieHistoryAudit` / `TorrentHistoryAudit` 行（无需回滚 → 无需 audit）。
- **过期 session 定时任务：** [`StaleSessionCleanup.yml`](../../../.github/workflows/StaleSessionCleanup.yml) 每天 UTC 02:00 运行，清理任何停留在 `in_progress` 超过 48 小时的 session，标记为 `FailureReason='stale_timeout'`。同一任务现在还调用 `apps.cli.sweep_movie_claim_stages` 清理 Phase-1 中在 MovieClaim Durable Object 上孤立的 `staged_complete{}` 条目（截止 48h，服务器端下限 ≥ 1h）。
- **MovieClaim 跨 session rollback 安全性（Phase 1）：** 详情页完成状态现在在 MovieClaim DO 上按 session 暂存，然后再进入永久的 `completed_committed[]` 列表。`apps.cli.commit_session` 在成功时提升暂存状态；`apps.cli.rollback` 在完成 DB 回滚前调用 `rollback_staged_movies`（最多重试 3 次）。一个失败的对等 session 不再阻止另一个 session 对同一 href 的临时重试——只有 `completed_committed[]` 才有阻止效果。参见 [`docs/zh/self-hoster/proxy-coordinator.md` §15.2](../../../docs/zh/self-hoster/proxy-coordinator.md) 了解协议及 `JAVDB_AutoSpider.wiki/Cross-Runner-State.md` §2.3 了解运行时语义。

---

## 策略概要（Pending 默认 + X3 audit 回退）

原始 X3 audit 混合方案在 `.cursor/plans/d1_workflow_rollback_plan_*.plan.md` 中保留供参考；Phase 3（`.cursor/plans/ingestion_perfect_rollback_2152bae2.plan.md`）在其之上叠加了 Pending 写入路径——该路径现在是 `MovieHistory` / `TorrentHistory` 的**默认**方式。每个表以最低代价的方式进行回滚：

| 表族 | 回滚技术 | Schema 新增 |
|---|---|---|
| `ReportMovies`, `ReportTorrents`, `ReportSessions`, `SpiderStats`, `UploaderStats`, `PikpakStats` | 按 `SessionId` 级联删除；拒绝删除 `Status='committed'` 的 `ReportSessions` 行 | `ReportSessions.Status TEXT DEFAULT 'in_progress'`；Phase 3 新增 `WriteMode` 和 `Status` 的 `finalizing` 值 |
| `MovieHistory`, `TorrentHistory`（Pending 模式 — Phase 3 默认） | 所有写入先暂存到 `PendingMovie/TorrentHistoryWrites`；提交时一次性重算派生字段并 UPSERT 到正式表；回滚时对 `Status='in_progress'` 的行执行 `DELETE`，对 `Status='finalizing'` 的行执行 `db_resume_finalizing_session`。无需 audit 重放。 | `PendingMovieHistoryWrites` 和 `PendingTorrentHistoryWrites` 表（各含显式应用生成的雪花 `Seq`、`ApplyState`、`SessionId` / `RunId` / `RunAttempt`） |
| `MovieHistory`, `TorrentHistory`（Audit 回退 — **已废弃，退役由 ADR-005 决定**） | 反向重放 `*_Audit` 表以撤销每个 `INSERT` / `UPDATE` / `DELETE`；跳过当前 `SessionId` 不再匹配的行（漂移）。仅可通过工作流 `write_mode_override=audit` 或 `JAVDB_HISTORY_WRITE_MODE=audit` 环境变量显式启用。ADR-006 PR-D（2026-05-16）已移除之前 `pending_mode_disabled_until` 自动切 audit 的机制。Audit 表为只读取证——参见[附录 A](#appendix-a-legacy-audit-fallback-sunset-2026-08)。 | 每个正式表上的 `SessionId INTEGER`；`MovieHistoryAudit` 和 `TorrentHistoryAudit` 表（自 2026-05-13 起只读，由 `scripts/audit_archive.py` 每周归档） |
| `PikpakHistory`, `DedupRecords`, `InventoryAlignNoExactMatch` | 删除按 session 范围划定的行。`DedupRecords` 的软删除/孤立更新会先将其前像快照到 `DedupRecordsRollback_<session_id>`，因此回滚可恢复已有行并删除失败 session 创建的行 | 每个表上的 `SessionId INTEGER`；按 session 的 `DedupRecordsRollback_<session_id>` 备份表 |
| `RcloneInventory` | 按 session 暂存表 → 原子 D1 批量交换。失败的扫描丢弃暂存表；正式表永远不会看到半写入的扫描 | `RcloneInventoryStaging_<session_id>`（每次运行创建/丢弃） |

### 为什么 history 需要 audit 表？*（遗留——保留供上下文参考，参见[附录 A](#appendix-a-legacy-audit-fallback-sunset-2026-08)）*

`MovieHistory` 和 `TorrentHistory` 是 upsert 操作（一行可能在多次运行中被多次修改）。简单的 `DELETE WHERE SessionId=...` 是错误的——它会擦除其他运行正在正确维护的行。

Audit 表在每次写入*之前*捕获：

- `Action` — `INSERT`、`UPDATE` 或 `DELETE`
- `OldRowJson` — 之前行状态的完整 JSON 快照（用于 `UPDATE` / `DELETE`）
- `SessionId` — 执行更改的运行
- `RunId` / `RunAttempt`（2026-05-08 新增）— 拥有该 audit 行的 GitHub Actions 工作流运行，这样即使 `ReportSessions` 行缺失，rollback 也能按运行标识寻址。

按反向 `Id` 顺序（最高优先）重放这些记录，可以干净地撤销单个 session 所做的每项更改，同时保留其他 session 最后修改的行不变（记录为 `drift_skipped`）。

Phase 4 为新 session 淘汰了这种方式：Pending 写入路径在提交时于单个事务内重算派生字段，消除了逐次变更 audit 追踪的需要。Audit 表对历史 session 的取证查询保持可用，直到[附录 A 的日落日期](#appendix-a-legacy-audit-fallback-sunset-2026-08)。

### SessionId 生成（2026-05-08+）

`ReportSessions.Id` **不再**由各后端的 AUTOINCREMENT 计数器分配。应用层通过 [`javdb/storage/db/db.py`](../../../javdb/storage/db/db.py) 中的 `_generate_session_id()` 自行生成 id：

```python
# Format: YYYYMMDDTHHMMSS.ffffffZ-TTTT-SSSS
# (UTC, microsecond precision, per-process random 16-bit tag, monotonic 16-bit counter)
dt = datetime.fromtimestamp(us / 1_000_000, tz=timezone.utc)
ts = dt.strftime("%Y%m%dT%H%M%S") + f".{us % 1_000_000:06d}Z"
candidate = f"{ts}-{tag_hex}-{counter:04x}"
```

同一 TEXT id 在两个后端显式 INSERT。原因：

- 在 `STORAGE_BACKEND=dual` 模式下，SQLite 和 D1 各自维护自己的 AUTOINCREMENT 计数器；过去任何不对称的 INSERT（一侧提交，另一侧失败）会使它们永久失同步。
- `DualCursor.lastrowid` 返回游标包装的那个后端的值。将其作为下游表的 `SessionId` 信任正是导致 2026-05-08 事件的原因：SQLite 侧分配了 `Id=332`，但在 D1 上 `Id=332` 是来自 2026-05-07 工作流的过期行，而 spider 将其 history 写入标记为 `SessionId=332`。Rollback CLI 随后看到跨越 35 小时的 145 条 audit 行，并拒绝回滚大部分，标记为漂移。
- 参见 [`migration/d1/2026_05_08_sessionid_decouple.md`](../../../migration/d1/2026_05_08_sessionid_decouple.md) 了解迁移详情。

[`javdb/storage/dual_connection.py`](../../../javdb/storage/dual_connection.py) 中的守卫（`DualCursor.for_write`）在任何未来代码路径尝试 INSERT 到受保护表（`APPLICATION_GENERATED_ID_TABLES`）且未提供显式 Id 而两个后端对 `lastrowid` 存在分歧时，将抛出 `DualWriteIdMismatchError`。

### Rollback CLI 查找优先级

CLI（[`apps/cli/db/rollback.py`](../../../apps/cli/db/rollback.py)）按顺序遍历三个来源，合并结果：

1. **`--session-id`**（最精确）。仅针对该 session，除非设置了 `--include-orphaned`，否则**不会扩展**为窗口扫描。
2. **`--run-id` + `--attempt`**（运行感知查找的首选路径）。调用 `db_find_sessions_by_run`，同时查询 `ReportSessions` 和 audit 表（因此即使之前的失败 rollback 已删除了 `ReportSessions` 行，该运行仍可恢复）。
3. **`--run-started-at` 窗口扫描**（遗留回退）。仅在设置了 `--include-orphaned` 时或其他来源未产生任何 session id 时才使用（自动清理任务需要此功能，以便在运行在打印 session id 之前就终止的情况下仍能按日期窗口清理）。

跨日完整性过滤：每个候选 session 的 `DateTimeCreated` 都与 `--run-started-at` 进行比对。早于 `run_started_at - 1h` 的 session 将被拒绝（`exit code 2`），除非传入 `--force`。

### 提交时的 Audit 保留

一旦 `db_mark_session_committed` 将 session 翻转为 `Status='committed'`，rollback CLI 将拒绝回滚它（除非使用 `--force`）。该 session 的 `MovieHistoryAudit` / `TorrentHistoryAudit` 行不再需要且只会膨胀表，因此同一调用会立即 `DELETE` 它们（如果 session 已提交则为空操作）。

### 冒烟测试清理策略

`TestIngestion.yml` 在每次 push/PR 时运行 spider，并且**必须**执行完整的双写路径（否则它无法在达到生产环境 DailyIngestion / AdHocIngestion 运行之前捕获 D1 / SQLite 漂移、schema 迁移回归、`DualWriteIdMismatchError` 触发等问题）。为防止模拟行在生产中累积，每次 TestIngestion 运行都配有保证的清理：

* **Spider 以双写模式运行**（与生产相同的 `STORAGE_BACKEND` / `STORAGE_MODE`）。它**不会**自动提交——spider 入口点从不调用 `db_mark_session_committed`，所以它创建的所有 session 保持 `Status='in_progress'`。
* **`always()` 运行的清理步骤**在任务末尾调用 `apps.cli.rollback --run-id $GITHUB_RUN_ID --attempt $GITHUB_RUN_ATTEMPT --scope all --apply`。Rollback CLI 使用 `(RunId, RunAttempt)` 联合查询找到该运行创建的每个兄弟 session（TestIngestion 同时运行 Daily 和 AdHoc spider，各自有不同的 CSV → 不同的 session，一起回滚），并反向遍历 `MovieHistoryAudit` / `TorrentHistoryAudit` 以撤销 INSERT、UPDATE 和 DELETE。
* **验证步骤**在 rollback 之后运行（同样 `always()`），如果本地 SQLite 和实时 D1 实例（通过 Cloudflare REST API 查询）中仍有标记为此 `(RunId, RunAttempt)` 的行留在 `ReportSessions` / `MovieHistoryAudit` / `TorrentHistoryAudit` 中，则**使工作流失败**。残留行意味着 rollback 机制存在 bug；快速暴露正是 TestIngestion 的意义所在。

`config_helper.py` 中的 `JAVDB_FORBID_DB_WRITES=1` 终止开关（`db_writes_forbidden()` → 强制 `storage_backend='sqlite'` / `storage_mode='csv'`，加上 `db_create_report_session` 内部的 `RuntimeError` 守卫）仍可作为可选基础设施，供确实需要零数据库执行的任何单元测试或本地脚本使用。**TestIngestion 不启用它**，因为这样做会跳过冒烟测试应该验证的 D1 / 双写代码路径。

### `(RunId, RunAttempt, CsvFilename)` 不变式

`ReportSessions(RunId, RunAttempt, CsvFilename) WHERE Status='in_progress' AND RunId IS NOT NULL` 上的部分唯一索引 `uq_reportsessions_runidentity_csv` 在数据库层强制执行真正的不变式：**同一工作流运行中不能有两个进行中的 session 共享相同的 CSV**。任何尝试双重 INSERT 的路径（重入、双写 `lastrowid` 漂移、手动 SQL）将因 `sqlite3.IntegrityError` 而失败。已解决的（committed/failed）session 被有意排除，以便同一 CSV 可以在未来的尝试中重新摄取；`RunId IS NULL` 的遗留行也为向后兼容而排除。应用层辅助函数 `db_find_in_progress_session_ids_for_run_csv` 现在是纵深防御——它在 INSERT 之前输出结构化错误消息，并覆盖索引有意跳过的本地开发 `RunId IS NULL` 情况。同一 `(RunId, RunAttempt)` 中具有**不同 CSV 文件名**的兄弟 session 完全合法（DailyIngestion 依次运行 TodayTitle spider 和 AdHoc URL spider）；`cleanup-on-failure` 通过 `--run-id` 一起回滚所有兄弟。

---

## Session 生命周期

```text
db_create_report_session()       →  Status='in_progress'  (every D1 write tagged)
              │
              ▼
       (workflow runs)
              │
       ┌──────┴──────┐
       │             │
   success        failure
       │             │
       ▼             ▼
 db_mark_session_   db_rollback_session()
 committed()        ├─ Status='failed' (non-committed)
       │            ├─ DELETE … WHERE SessionId=?
       ▼            ├─ replay *_Audit in reverse
  Status='committed' └─ DROP staging table
```

- `Status='in_progress'` 的行是 cleanup-on-failure / RollbackD1 **唯一**会操作的行。
- `Status='committed'` 的行不可变（`db_rollback_session` 在未设置 `force=True` 时抛出 `ValueError`）。
- `Status='failed'` 是调试面包屑——对于未提交的 session，`db_rollback_session` 在执行删除*之前*设置此状态，以便部分失败的 rollback 将行保留在可识别的状态供后续跟进。

---

## 自动 cleanup-on-failure

每个摄取工作流现在都有一个任务：

```yaml
cleanup-on-failure:
  needs: [setup, run-pipeline]
  if: ${{ needs.run-pipeline.result == 'failure' || needs.run-pipeline.result == 'cancelled' }}
  steps:
    - name: Roll back uncommitted D1 writes
      run: |
        python3 -m apps.cli.rollback \
          --run-id "${{ github.run_id }}" \
          --attempt "${{ github.run_attempt }}" \
          --run-started-at "${{ needs.setup.outputs.pipeline_workflow_run_started_at }}" \
          --scope all \
          --apply \
          --session-id "${{ needs.run-pipeline.outputs.session_id }}"   # if known
```

功能说明：

1. 当提供 `--run-started-at` 时，查找所有 `Status='in_progress'` 且 `DateTimeCreated >= run_started_at` 的 `ReportSessions` 行。如果仅提供 `--session-id`，则只针对该显式 session；如果两者都提供，则将显式 session 与窗口查找合并。
2. 对每个 session 执行 X3 rollback 编排（reports → operations → history）。
3. 将每个 session 标记为 `Status='failed'` 以便追溯。
4. 上传 `logs/rollback.log`（制品名：`rollback-log`，保留期：14 天）。

如果 spider 在 `db_create_report_session` 返回 id 之前就失败了，则此任务为空操作。

> **安全保证：** cleanup 任务上传独立的 `rollback-log` 制品，且不会接触 `Status='committed'` 的 session，因此同时成功的并行运行不会受到干扰，操作员可以可靠地找到 rollback 证据。

配套的**标记 session 为已提交**步骤在 `run-pipeline` 成功路径末尾运行（`if: ${{ success() }}`），在 `spider`、`qb_uploader`、`qb_file_filter`、`pikpak_bridge` 和 `dedup` 依次完成后。可选的 `qb_file_filter` / `dedup` 步骤保持 `continue-on-error: true`，因此它们的暂时性失败不会阻止 session 在必需的 D1 写入步骤成功后被保护。

---

## 手动 rollback 工作流（`RollbackD1.yml`）

用于事故响应、临时清理或回滚你已知的特定 session，从 Actions 标签页分发 **Rollback D1 Session** 工作流。

**输入参数：**

| 输入 | 默认值 | 说明 |
|---|---|---|
| `session_id` | （空） | 传入 `ReportSessions.Id` 以包含特定运行。 |
| `run_id`, `attempt` | （空） | 仅用于 audit/日志。 |
| `run_started_at` | （空） | ISO 时间戳下界；发现该时间窗口内所有进行中的 session，当两者都提供时与 `session_id` 合并。省略时，`session_id` 单独使用仅针对该 session。 |
| `scope` | `all` | `all`、`reports`、`operations`、`history` 之一。 |
| `dry_run` | `true` | **务必先预览。** |
| `force` | `false` | 仅在确实需要回滚 `Status='committed'` 的 session 时设置。会记录 `::warning::`。 |
| `log_level` | `INFO` | `DEBUG` 在调试 audit 重放时有用。 |
| `runner` | `self-hosted` | 对仅 SQLite 的 CF 托管运行使用 `ubuntu-latest`。 |

**标准操作流程：**

```text
1. 打开 Actions → Rollback D1 Session → Run workflow。
2. 填入以下之一：
   - session_id（首选——精确目标），或
   - run_started_at（例如 2026-05-04T19:30:00Z；该时间点之后
     所有 in_progress 的都会被回滚）。
3. 首次运行保持 dry_run=true。检查"Run rollback"步骤中的
   JSON 摘要及 rollback-log 制品。
4. 如果差异符合预期，以 dry_run=false 再次分发。
5. 通过 Actions 日志确认 drift_total=0（无并发运行漂移）。
   如果 drift_total>0，参见下方"漂移处理"。
```

该工作流的**并发组**为 `rollback-d1`，因此两个操作员不会意外并行运行 rollback。

---

## "重新运行失败任务"安全矩阵

GitHub 原生的**重新运行失败任务**按钮很方便，但仅在步骤幂等或在成功清理*之后*运行时才安全。点击前请参考此矩阵：

| Pipeline 步骤 | 可以直接重新运行？ | 原因 |
|---|---|---|
| `setup` | ✅ 是 | 纯配置引导；无 DB 写入。 |
| `run-pipeline` → 步骤 1（spider） | ⚠️ 仅在清理后 | Spider 写入 `MovieHistory` / `TorrentHistory`（audit 追踪）和 `ReportSessions/Movies/Torrents`。不回滚就重新运行会创建重复 session 和双重写入。 |
| `run-pipeline` → 步骤 2（qb_uploader） | ⚠️ 仅在清理后 | 向 qBittorrent 添加种子（外部副作用）并写入按 `SessionId` 键控的 `UploaderStats`。不清理就会重新上传已添加的种子并产生重复统计行。 |
| `run-pipeline` → 步骤 2.5（qb_file_filter） | ✅ 是 | `continue-on-error: true`，幂等的 qB 暂停/删除操作。 |
| `run-pipeline` → 步骤 3（pikpak_bridge） | ⚠️ 仅在清理后 | 调用 PikPak API（外部副作用）并追加 `PikpakHistory` / `PikpakStats`。不回滚就重新运行会重新上传已 PikPak 处理的种子。 |
| `run-pipeline` → 步骤 4（rclone_dedup） | ✅ 基本安全 | `continue-on-error: true`；rclone purge 对已删除路径幂等。Rollback 现在恢复被失败 session 软删除的已有 `DedupRecords` 行并删除新创建的行。 |
| `Mark sessions as committed` | ✅ 是 | 幂等 UPDATE；第二次运行为空操作。 |
| `cleanup-on-failure` | ✅ 是 | 对已回滚数据重新运行 rollback 是幂等的（audit 行已被消费）。 |
| `email-notification` / `commit-results` | ✅ 是 | 无 DB 写入。 |

**经验法则：** 如果重新运行失败任务会重新执行步骤 1、2 或 3，先运行 **Rollback D1 Session**（或等待自动 `cleanup-on-failure` 任务），*然后*再重新运行。

---

## 直接 CLI 使用

从开发者机器或 runner 上的终端会话操作时：

```bash
# 干运行预览（无 DB 写入）：
python3 -m apps.cli.rollback --session-id 123

# 应用 rollback：
python3 -m apps.cli.rollback --session-id 123 --apply

# 按 GitHub 运行标识回滚（首选——即使 ReportSessions 行被删除也有效）：
python3 -m apps.cli.rollback --run-id 12345 --attempt 1 --apply

# 遗留时间窗口扫描（现通过 --include-orphaned 可选启用）：
python3 -m apps.cli.rollback --run-started-at 2026-05-04T00:00:00Z --include-orphaned --apply

# 部分范围（仅 history audit 重放）：
python3 -m apps.cli.rollback --session-id 123 --scope history --apply

# 强制回滚已提交的 session（危险操作）：
python3 -m apps.cli.rollback --session-id 123 --force --apply

# 覆盖跨日拒绝以处理故意的历史 session：
python3 -m apps.cli.rollback --session-id 123 --run-started-at 2026-05-04T00:00:00Z --force --apply
```

**退出码：**

- `0` — 成功 / 干运行顺利完成
- `2` — 拒绝：session 为 `Status='committed'` 且未传入 `--force`，或候选 session 早于 `--run-started-at` 超过 1 小时（跨日拒绝——传入 `--force` 覆盖）
- `3` — 无法连接到 D1 / SQLite
- `4` — 部分失败或 rollback 漂移；检查 JSON 摘要和日志

CLI 在结束时打印 JSON 摘要，包含每个表的计数；可通过管道传给 `jq` 检查。漂移 / orphan_pruned 计数器也会追加到 `reports/D1/d1_drift.jsonl`，在 GitHub Actions 下还会写入 `$GITHUB_OUTPUT`，以便下游步骤和邮件通知做出响应。

### 事故响应工具（一次性脚本）

在罕见的 audit / history 表损坏情况下（例如 2026-05-08 SessionId 碰撞事件在 D1 上留下了约 1k 条幻影 audit 行）：

```bash
# 1. 从 D1 拉取每个业务表到本地 sqlite（默认干运行）：
python3 -m scripts.sync_d1_to_sqlite                # 报告将要更改的内容
python3 -m scripts.sync_d1_to_sqlite --apply        # 实际覆写 reports/*.db

# 2. 检测任一/两侧的幻影 audit 行（Phase 4 起为只读）：
python3 -m scripts.cleanup_stale_session_audits             # 干运行，双侧
python3 -m scripts.cleanup_stale_session_audits --target d1 # 干运行，仅 D1
# ``--apply`` 现为 ``--dry-run`` 的弃用别名——脚本
# 记录警告并按只读检查行为执行。需要执行
# 破坏性清理的操作员使用归档定时任务（下方步骤 3）。

# 3. 归档 30 天以上的 audit 行（committed / failed / orphan）：
python3 -m scripts.audit_archive                    # 干运行，默认 30 天窗口
python3 -m scripts.audit_archive --apply            # 应用，双侧
python3 -m scripts.audit_archive --apply --target sqlite --older-than-days 60
```

以上三个脚本默认均为干运行，并在 `reports/` 下写入 JSON 报告。使用 `--target` 限制为单侧；对于 `cleanup_stale_session_audits` 使用 `--session-ids 332,346` 限制为特定 id，`--cross-day-hours 12` 调整幻影检测阈值。

`sync_d1_to_sqlite` + `cleanup_stale_session_audits` 是手动事故响应工具（不要接入定时任务）。`audit_archive` **是**定时任务，通过 [`.github/workflows/AuditArchive.yml`](../../../.github/workflows/AuditArchive.yml) 每周运行。定期的过期 session 清理存在于 [`StaleSessionCleanup.yml`](../../../.github/workflows/StaleSessionCleanup.yml)，使用 [`apps.cli.cleanup_stale_in_progress`](../../../apps/cli/db/cleanup_stale_in_progress.py)。

### 手动标记 session 为已提交

如果一个 session 确实成功了但工作流在非 DB 写入的后续步骤（例如邮件步骤）中终止，你可以手动翻转 `Status`：

```bash
python3 -m apps.cli.commit_session --session-id 123
```

`commit_session` 是幂等的，会忽略已提交的行。

---

## Audit 表取证 *（自 Phase 4 起只读，2026-05-13）*

> Phase 4 契约：audit 表**仅用于历史 session 取证**。新 session 不会向其追加行——Pending 写入路径是默认方式。在[附录 A](#appendix-a-legacy-audit-fallback-sunset-2026-08) 的日落日期之前，已提交/失败 session 的行保留足够长的时间供操作员查询；[`scripts/audit_archive.py`](../../../scripts/audit_archive.py) 定时任务每周一清理 > 30 天的数据。

`MovieHistoryAudit` 和 `TorrentHistoryAudit` 表是临时存储——`db_rollback_session` 在成功重放后删除其行。在此之前，它们是有用的诊断线索。

```sql
-- session 123 对 MovieHistory 做了什么？
SELECT Id, TargetId, Action, DateTimeCreated, OldRowJson
FROM MovieHistoryAudit
WHERE SessionId = 123
ORDER BY Id;

-- 所有仍待 rollback 的 session：
SELECT Id, ReportType, ReportDate, DateTimeCreated, Status
FROM ReportSessions
WHERE Status = 'in_progress'
ORDER BY DateTimeCreated;

-- 每个 session 的写入量（audit 行数）：
SELECT SessionId, COUNT(*) AS movie_changes
FROM MovieHistoryAudit
GROUP BY SessionId
ORDER BY movie_changes DESC;
```

`OldRowJson` 是写入前行状态的完整 JSON（列 → 值）。对于 `Action='INSERT'` 为 `NULL`（之前不存在）。对于 `UPDATE` 和 `DELETE`，它是 rollback 恢复时使用的前像。

---

## 漂移处理

当 rollback 无法安全撤销某项更改（因为另一次运行随后触碰了同一行）时，会记录一个"漂移"事件。最常见的原因：

- 在失败运行之后，一次并发摄取运行 upsert 了相同的 `MovieHistory.Href`。
- 手动 SQL 修复在 audit 行写入后更改了行的 `SessionId`。
- Audit 行引用的行已被另一次 rollback 删除。

当 `drift_total > 0` 时：

1. 阅读 rollback 日志中的警告行——它们包含表名和冲突行的 `Id`。
2. 判断并发运行的数据是否更新（通常是——保留漂移不变）。
3. 如果你决定并发运行也是错误的，可选择稍后重新运行 `apps.cli.rollback --scope history --session-id <id> --apply`。

CLI 以退出码 `4` 退出以暴露部分失败，让操作员注意到。

---

## Schema 迁移

如果你从 X3 之前的版本升级，运行打包的迁移以添加新列和 audit 表：

```bash
# 本地 SQLite——迁移在 db 初始化时自动执行（前向兼容 ALTER）。
python3 -m apps.cli.migration --backup

# Cloudflare D1——应用 SQL 包：
wrangler d1 execute history    --file=migration/d1/2026_05_04_add_rollback_columns_history.sql
wrangler d1 execute reports    --file=migration/d1/2026_05_04_add_rollback_columns_reports.sql
wrangler d1 execute operations --file=migration/d1/2026_05_04_add_rollback_columns_operations.sql

# 2026-05-08 后续：添加 (RunId, RunAttempt, FailureReason) 列
# 以便 rollback 可以按 GitHub 运行标识寻址 session。参见
# migration/d1/2026_05_08_sessionid_decouple.md 了解原因。
wrangler d1 execute reports --file=migration/d1/2026_05_08_add_run_identity_columns_reports.sql
wrangler d1 execute history --file=migration/d1/2026_05_08_add_run_identity_columns_history.sql
```

迁移后，`db.SCHEMA_VERSION == 11`。`init_db` 内部的 `_ensure_rollback_columns` 辅助函数会在后续启动时添加列（如果之前的迁移不完整）。

---

## Pending 模式（当前默认）

`ReportSessions.WriteMode`（2026-05-09 新增）选择 cleanup-on-failure 和过期 session 定时任务的调度路径。自 Phase 3 以来默认为 **`pending`**（DailyIngestion / AdHocIngestion / TestIngestion）；遗留 audit 路径仍可通过环境变量 `JAVDB_HISTORY_WRITE_MODE=audit` 或工作流 workflow_dispatch 输入 `write_mode_override` 选择。参见[架构方案](../../../.cursor/plans/ingestion_perfect_rollback_2152bae2.plan.md)了解设计原理。

### Pending 状态机

```
in_progress ─(db_begin_finalize)─▶ finalizing ─(db_finish_commit)─▶ committed
     │                                  │
     │                                  └─(idempotent resume)─▶ finalizing ─▶ committed
     │
     └─(rollback DELETE pending)─▶ failed
```

- `db_stage_history_write` 写入 `PendingMovie/TorrentHistoryWrites` 而非 `MovieHistory` / `TorrentHistory`。
- `db_load_history_snapshot` 读取 `已提交的正式数据 + 当前 session 的 pending 覆盖层`，使进行中的进程始终能看到自己的写入而不污染其他并发 session。
- `db_commit_session_history` 遍历 session 中每个不同的 `Href`，按 `Href` 加锁，重算 `PerfectMatchIndicator` / `HiResIndicator`，UPSERT 正式行，最后 `DELETE` 每个已应用的 pending 行。
- `db_resume_finalizing_session` 是幂等的重入点：在 finalize 中途崩溃的工作流会被驱动到 `committed` 而非回滚。

### 清理调度矩阵（Phase 3）

| `WriteMode` | `Status` | Cleanup-on-failure 操作 | 过期 session 定时任务操作 |
|---|---|---|---|
| `audit` | `in_progress` | 反向重放 `*Audit` 表（遗留 X3） | 相同（遗留） |
| `pending` | `in_progress` | `DELETE FROM PendingMovie/TorrentHistoryWrites WHERE SessionId=?`，无 audit 重放 | 相同 |
| `pending` | `finalizing` | **`db_resume_finalizing_session`** 驱动 session 到 `committed`（默认 `--auto-resume-finalizing`） | 相同——永不回滚 |
| `audit` 或 `pending` | `committed` | 拒绝——重新运行/重试跳过这些 | 跳过（仅 `in_progress`/`finalizing` 候选） |
| `audit` | `finalizing` | 意外状态（audit 不使用 finalizing）；定时任务记录 `audit_finalizing_unexpected` 并拒绝 | 相同 |

### Pending 模式指标（`pending_session_verify`）

`apps.cli.commit_session`（每次 pending 模式提交）和 `apps.cli.rollback`（每次 pending 模式 rollback / 恢复）对其处理的每个 session 向 `reports/D1/d1_drift.jsonl` 发出一条 `pending_session_verify` JSONL 记录。字段：

- `session_id`、`run_id`、`run_attempt`、`write_mode`、`final_status`、`source`（`commit_session` 或 `rollback`）。
- `pending_staged_count`（该 session 进入 pending 表的总行数）。
- `pending_applied_count`（转换为正式数据的行数）。
- `pending_residual_count`（运行后仍为 `ApplyState='pending'` 的行——**必须为 0**）。
- `commit_attempts`（首次为 1；如果发生了 resume_commit 则 ≥ 2）。
- `commit_duration_ms`、`hrefs_processed`、`movies_upserted`、`torrents_upserted`、`torrents_deleted`。
- `derived_recompute_drift` + `derived_drift_samples`（仅在 `JAVDB_PENDING_SHADOW_AUDIT=1` 时填充——Phase 2 开关，在 Phase 3 中保持门控，以便在记录一个干净周后可以逐步减少比较）。
- `worker_stage_rollback_failed`、`cleanup_path_mismatch_count`、`staged_claim_orphan_count`。

同一文件还接收 `stale_session_cleanup` 和 `rollback_summary` 记录；下游消费者按 `kind` 过滤。

### 邮件 Pending 模式验证 + 健康快照

邮件步骤（[`javdb/integrations/notify/email.py`](../../../javdb/integrations/notify/email.py)）现在读取 `reports/D1/d1_drift.jsonl`，限制为 `$GITHUB_RUN_ID` / `$GITHUB_RUN_ATTEMPT` 拥有的 `pending_session_verify` 记录，并渲染 **Pending Mode Verification** 正文块，列出每个 pending session 的计数。任何阈值违规会在行内标记（`[CRITICAL]` / `[ALERT]`）并在邮件主题前添加前缀：

- **软告警**（主题 `[PENDING-ALERT] (...)`）— `commit_attempts > Phase3_max`、`worker_stage_rollback_failed > 0`、`staged_claim_orphan_count > 0`、`d1_request_count_audit_baseline_ratio > 1.8`、或 `final_status='finalizing'`。
- **严重告警**（主题 `[PENDING-PAUSE] (...)`，ADR-006 之前为 `[PENDING-ROLLBACK-AUTO]`）— `pending_residual_count > 0`、`derived_recompute_drift > 0`、或 `cleanup_path_mismatch_count > 0`。同时触发下方的[告警 + 暂停](#告警--暂停publish-configyml-adr-006-pr-d)。

当 [`scripts/aggregate_pending_health.py`](../../../scripts/aggregate_pending_health.py) 生成了 `reports/D1/pending_health_24h.json` 时，**健康快照**块跟随在每 session 表后面。DailyIngestion 和 AdHocIngestion 都在 `Run Email Notification` 之前调用此聚合器，使快照覆盖过去 24 小时的 pending session 以及过期定时任务的 resume 成功/失败。

Phase 2 阈值仍可通过环境变量 `JAVDB_PENDING_ALERT_PHASE=2` 使用——在 TestIngestion canary 预热期间有用。

### 告警 + 暂停（`.publish-config.yml`）— ADR-006 PR-D

DailyIngestion / AdHocIngestion 中的**严重** pending 告警会运行邮件任务中的 `Alert + pause on critical pending alert (ADR-006)` 步骤。它调用 [`scripts/pending_mode_alert_and_pause.py`](../../../scripts/pending_mode_alert_and_pause.py)，写入（或延长）：

```yaml
# ADR-006 pause marker — written by scripts/pending_mode_alert_and_pause.py.
pipeline_paused_until: '2026-05-17T07:00:00+00:00'
pipeline_paused_reason: 'DailyIngestion run 12345: pending_residual_count=2 session=67890'
```

到 `.publish-config.yml`，然后提交 + 推送更改。**下一次**计划或手动分发的摄取运行会命中 `setup` job 新增的 `Pipeline pause gate (ADR-006)` 步骤，看到标记时间戳仍在未来时短路：每个下游 job（`run-pipeline`、`cleanup-on-failure`、`email-notification`、`commit-results`）都基于 `needs.setup.outputs.paused != 'true'` 跳过。workflow 干净退出，避免定时任务把整个 workflow 永久标记为失败。

**为什么用暂停而非回退？** 详见 [ADR-006](../../ai/adr/ADR-006-pending-mode-default-rollout.md) §D3：旧的 audit 自动回退把 Pending Mode 故障静默降级到一个"看起来工作但不对劲"的状态，移除了修复根因的压力。暂停强迫操作员看到事故并显式介入，事件永远可见。

窗口为 24 小时。恢复步骤：

1. 调查告警（根因记录在该运行的 `reports/D1/d1_drift.jsonl`）。
2. 修复底层 bug。
3. 从 `.publish-config.yml` 删除 `# ADR-006 pause marker` 整段（或 `git revert` 引擎暂停的自动提交）。
4. Commit + push。下一次运行正常拾起。

如果不动 marker，24 小时后自动过期，pipeline 自动恢复——但仅在根因验证已修复时才这么做，否则下次运行会再次触发同一告警。

### 操作员恢复 SOP

| 症状 | 查找内容 | 修复方法 |
|---|---|---|
| 邮件主题仅有 `[PENDING-ALERT]` | 正文中的 `commit_attempts`、ratio 或 finalizing 标志 | 检查 `reports/D1/d1_drift.jsonl`；通常是暂时性的（Worker 租约超时）。无自动操作。 |
| 邮件主题为 `[PENDING-PAUSE]`（ADR-006 之前为 `[PENDING-ROLLBACK-AUTO]`） | `pending_residual_count`、`derived_recompute_drift`、`cleanup_path_mismatch_count` | Pipeline 已通过 `.publish-config.yml` 中的 `pipeline_paused_until` 暂停 24 小时。调查 `reports/D1/d1_drift.jsonl` 中的根因，修复后从 `.publish-config.yml` 删除 pause marker（或 `git revert` 自动提交）。让 marker 过期但不修根因只会让下次运行再次触发同一告警。 |
| `final_status='finalizing'` 连续两个定时任务周期 | StaleSessionCleanup 无法将 session 驱动到 `committed` | `python3 -m apps.cli.commit_session --session-id <id> --shadow-audit --log-level DEBUG`；如果 3 次尝试仍失败，`python3 -m apps.cli.rollback --session-id <id> --no-auto-resume-finalizing --apply` 标记为 `failed`。 |
| `worker_stage_rollback_failed > 0` | Rollback CLI 无法连接到 MovieClaim coordinator | 检查 coordinator 健康状态；孤立清扫定时任务将在 4 小时内对账。 |
| 已提交 session 上 `pending_residual_count > 0` | 半应用的 commit，残留 `ApplyState='pending'` 行 | 正式表已经正确（`committed` 翻转是事实来源）；残留行只需清除。安全选项按优先级排列：(1) 手动 `DELETE FROM PendingMovieHistoryWrites WHERE SessionId=? AND ApplyState='pending'` 加上 `PendingTorrentHistoryWrites` 上的相同操作，在断言 `SELECT Status FROM ReportSessions WHERE Id=?` 返回 `'committed'` 之后执行——这些表从不参与正式读取，因此 DELETE 是非破坏性的；(2) 一次性 Python：`python3 -c "from javdb.storage.db import db_commit_session_history; print(db_commit_session_history(<id>))"` — 通过提交时使用的相同 applied-cleanup 路径排空然后删除。（`apps.cli.commit_session` 在 session 行已为 `committed` 时跳过排空，因此 CLI 路由**不会**清除残留。） |

---

## 验证手册（dev 分支 — Phase 3，6 步）

在将 Phase 3 升级到 `main` 之前，在 `dev` 上执行每个调度路径各一次：

1. **正常路径** — 在 `dev` 上以默认设置分发 `Daily Ingestion Pipeline`。预期结果：
   - Spider 运行，每次 history 写入通过 `db_stage_history_write`（通过查询 `PendingMovieHistoryWrites WHERE SessionId=<sid>` 验证）。
   - `Mark sessions as committed` 步骤运行 `db_commit_session_history`；session 以 `Status='committed'`、`pending_residual_count=0` 结束。
   - 邮件主题**无** `PENDING-ALERT` 前缀；**Pending Mode Verification** 块列出的每项指标均为绿色。
2. **进行中失败** — 在 dev 工作流文件中，于 `Step 1 - Run Spider` 后注入 `exit 1`。重新分发。预期结果：
   - `cleanup-on-failure` 任务触发；rollback CLI 通过 `_rollback_pending_in_progress` 调度（在 JSON 摘要中显示为 `mode='rollback_pending'`）。
   - 验证行中 `pending_staged_count > 0` 且 `pending_residual_count = 0`。
   - 邮件正文显示 `final_status='failed'`，无严重告警。
3. **Finalizing 失败** — 在 dev 工作流文件中，对 `Mark sessions as committed` 步骤的 Python 进程运行中途注入 `kill -9`（或临时猴子补丁 `db_finish_commit_session` 使其抛异常）。重新分发。预期结果：
   - `cleanup-on-failure` 发现 session 处于 `Status='finalizing'`，调度 `db_resume_finalizing_session`，驱动其到 `committed`。
   - 邮件主题前缀 `[PENDING-ALERT]`（`commit_attempts=2`）；正文确认恢复成功。
4. **强制软告警** — 以环境覆盖 `JAVDB_PENDING_BATCH_SIZE=1` 分发（强制每行 N 次 D1 调用）。预期结果：
   - `d1_request_count_audit_baseline_ratio > 1.8` 触发 `[PENDING-ALERT]`。
   - **无**暂停触发（软告警仅标注主题）。
5. **强制严重告警（ADR-006 暂停路径）** — 在 `dev` 上临时猴子补丁 `_commit_one_movie` 写入错误的 `PerfectMatchIndicator`，确保 `JAVDB_PENDING_SHADOW_AUDIT=1`。重新分发。预期结果：
   - 邮件主题前缀 `[PENDING-PAUSE]`。
   - `.publish-config.yml` 通过邮件任务的 `Alert + pause on critical pending alert (ADR-006)` 步骤获得 `pipeline_paused_until` 块。
   - 立即重新分发：`setup` job 中的 `Pipeline pause gate (ADR-006)` 看到未来时间戳，emit `paused=true`，所有下游 job 跳过。workflow 显示绿色但 spider / uploader / pikpak 都未执行。
6. **手动恢复** — `git revert` 暂停提交（或手动删除 `# ADR-006 pause marker` 整块）并再次分发。预期结果：
   - `Pipeline pause gate (ADR-006)` 报告 `paused=false`；下游 job 正常执行。
   - 验证行干净；邮件无告警前缀。

如果以上六步中任何一步偏离预期结果，**不要**将 Phase 3 升级到 `main`。捕获失败运行的验证行 + rollback 日志并提交 issue。

> *遗留 audit 模式验证*已移至[附录 A — 遗留 audit 回退（2026-08 日落）](#appendix-a-legacy-audit-fallback-sunset-2026-08)，连同其余 `WriteMode='audit'` 参考材料。

---

## 文件索引

- CLI：[`apps/cli/db/rollback.py`](../../../apps/cli/db/rollback.py)、[`apps/cli/db/commit_session.py`](../../../apps/cli/db/commit_session.py)、[`apps/cli/db/cleanup_stale_in_progress.py`](../../../apps/cli/db/cleanup_stale_in_progress.py)
- 核心辅助函数：[`javdb/storage/db/db.py`](../../../javdb/storage/db/db.py)（`db_stage_history_write`、`db_commit_session_history`、`db_resume_finalizing_session`、`db_rollback_session`、`db_mark_session_committed`、`db_find_in_progress_sessions`、`db_find_stale_pending_sessions`、`db_pending_session_stats`、`_audit_record_movie_change`、`_rollback_history` 等）
- Phase 3 脚本：[`scripts/aggregate_pending_health.py`](../../../scripts/aggregate_pending_health.py)、[`scripts/pending_mode_alert_and_pause.py`](../../../scripts/pending_mode_alert_and_pause.py) *（ADR-006 PR-D 替代了已退役的 `pending_mode_auto_fallback.py`）*
- Phase 4 脚本：[`scripts/audit_archive.py`](../../../scripts/audit_archive.py)、[`scripts/cleanup_stale_session_audits.py`](../../../scripts/cleanup_stale_session_audits.py)（自 2026-05-13 起只读）
- 邮件集成：[`javdb/integrations/notify/email.py`](../../../javdb/integrations/notify/email.py)（`_format_pending_verify_section`、`_evaluate_pending_alerts`、`_format_health_snapshot_section`）
- 工作流：[`.github/workflows/DailyIngestion.yml`](../../../.github/workflows/DailyIngestion.yml)、[`.github/workflows/AdHocIngestion.yml`](../../../.github/workflows/AdHocIngestion.yml)、[`.github/workflows/RollbackD1.yml`](../../../.github/workflows/RollbackD1.yml)、[`.github/workflows/StaleSessionCleanup.yml`](../../../.github/workflows/StaleSessionCleanup.yml)、[`.github/workflows/AuditArchive.yml`](../../../.github/workflows/AuditArchive.yml)
- 迁移：[`migration/d1/2026_05_04_add_rollback_columns_*.sql`](../../../migration/d1/)、[`migration/d1/2026_05_09_add_pending_history_tables.sql`](../../../migration/d1/)
- 方案参考：[`.cursor/plans/ingestion_perfect_rollback_2152bae2.plan.md`](../../../.cursor/plans/ingestion_perfect_rollback_2152bae2.plan.md)

---

## Appendix A — 遗留 audit 回退（2026-08 日落）

> **状态** — *自 Phase 4 起废弃，2026-05-13。退役现由 [ADR-005](../../ai/adr/ADR-005-db-py-retirement-and-repo-pattern.md) D10 gate 决定，不再绑定固定日历日期。* 原 2026-08-13 日落已被取代：gate 在 [ADR-006](../../ai/adr/ADR-006-pending-mode-default-rollout.md) 的 30 天 pending 默认 bake 期顺利完成后触发（要求：尾部 30 天无 `WriteMode='audit'` session、无孤儿 audit 行、告警-暂停脚本触发次数 ≤ 1 次/月）。
> **退役后的样子** — `JAVDB_HISTORY_WRITE_MODE=audit` 在 session 创建时将被拒绝，`_rollback_history` 的 audit 重放路径将被删除，`MovieHistoryAudit` / `TorrentHistoryAudit` 表将从 schema 中移除（migration `v14`）。

### A.1 时间线

| 日期 | 事件 |
|---|---|
| 2026-05-04 | X3 audit 混合方案作为默认 rollback 策略登陆 `main`。 |
| 2026-05-09 | Phase 0 / 1 / 2 — `PendingMovie/TorrentHistoryWrites` schema + pending 模式写入路径在 `JAVDB_HISTORY_WRITE_MODE` 后交付。 |
| 2026-05-11 | Phase 3 — Daily / AdHoc / TestIngestion 默认切换为 `WriteMode='pending'`；audit 回退保留用于紧急分发。 |
| **2026-05-13** | **Phase 4 — 宣布 Audit 废弃。** `db_upsert_history` 发出 `DeprecationWarning`；`JAVDB_AUDIT_WRITES_DISABLED` 终止开关可用；`scripts/cleanup_stale_session_audits.py` 切换为只读；`scripts/audit_archive.py` 定时任务开始运行。 |
| **2026-05-16** | **ADR-006 落地。** PR-A 把 Python `_resolve_write_mode` 默认从 `'audit'` 翻转为 `'pending'`。PR-C 把 Daily / AdHoc 上 `workflow_dispatch` 输入选项中的 `audit` 移除。PR-D 把 audit 自动回退替换为告警-暂停门（脚本重命名，`.publish-config.yml` 键从 `pending_mode_disabled_until` 切换为 `pipeline_paused_until`）。30 天 bake 期开始。 |
| *bake + ~30 天* | **ADR-005 D10 sign-off。** 若 bake 指标稳定（audit session 计数 = 0、无孤儿 audit、暂停脚本触发 ≤ 1 次/月），ADR-005 PR-1 启动。 |
| *ADR-005 PR-5 之后* | **硬退役。** `_resolve_write_mode('audit')` 抛出异常；rollback CLI 的 audit 重放分支被移除；audit 表从新的 SQLite + D1 schema 中删除（`MovieHistoryAudit` / `TorrentHistoryAudit` 通过 migration `v14` drop）。 |

### A.2 "废弃"在实践中的含义

- `db_upsert_history()` 在每次调用时发出 `DeprecationWarning`。该函数仍然有效（audit 回退 rollback 对遗留 session 仍依赖它）——直接调用者必须迁移到 `save_parsed_movie_to_history`（在 `WriteMode='pending'` 下自动暂存，仅对显式 audit 回退才访问 `db_upsert_history`）。
- `JAVDB_AUDIT_WRITES_DISABLED=1` 环境变量（2026-05-13 新增，[`javdb/storage/db/db.py`](../../../javdb/storage/db/db.py)）将每次 audit 行 INSERT 变为空操作，同时仍允许 `MovieHistory` / `TorrentHistory` UPSERT 落地。默认为 `0`，因为 audit 回退在废弃窗口期间仍需要 audit 行；一旦所有工作流验证仅运行 pending 模式后翻转为 `1`。
- `MovieHistoryAudit` / `TorrentHistoryAudit` 行仍可用于取证查询——通过 `apps.cli.rollback --scope history --session-id <id>` 进行的手动 rollback 对任何具有 audit 行的遗留 session 仍然有效。预期是没有*新* session 会进入此分支。
- 破坏性清理辅助工具 `scripts/cleanup_stale_session_audits.py` 现为严格只读——传入 `--apply` 会记录废弃警告并静默降级为干运行。

### A.3 Audit 归档定时任务（[`AuditArchive.yml`](../../../.github/workflows/AuditArchive.yml)）

每周一 UTC 04:00（新加坡时间 12:00）运行。默认模式为干运行；操作员在一周的干运行报告看起来合理后通过 `workflow_dispatch` 升级为 `apply=true`。

该定时任务清理所属 `ReportSessions` 行早于 `--older-than-days`（默认 30）且属于以下三个类别之一的 audit 行：

1. `committed_expired` — 所属 session 为 `Status='committed'`（`db_mark_session_committed` 中的内联清理因成功路径步骤期间的暂时性 D1 错误而未触发）。
2. `failed_expired` / `in_progress_expired` / `finalizing_expired` — 所属 session 处于非已提交状态但已超过归档窗口。过期 session 定时任务已有多次机会将其驱动到已解决状态，因此 audit 行可以被回收。
3. `orphan_session` — 所属 `ReportSessions.Id` 不再存在。这些是 2026-05-08 事件产生的典型"幻影"行；归档窗口保证没有合法的清理工作流仍在请求它们。

```bash
# 干运行每周制品：
python3 -m scripts.audit_archive  # --target both --older-than-days 30

# 以更短窗口手动应用（事故响应）：
python3 -m scripts.audit_archive --apply --older-than-days 7 --target sqlite
```

### A.4 遗留 audit 模式验证手册（保留用于回退）

在操作员于 2026-08-13 日落*之前*强制 `WriteMode='audit'` 运行时，Phase 3 之前的 5 步 audit 手册仍然适用：

1. 将 rollback 接线推送到 `dev`。确认 `cleanup-on-failure`、`Mark sessions as committed` 和 `RollbackD1.yml` 存在。
2. 冒烟测试成功路径。以 `write_mode_override=audit` 分发 DailyIngestion / AdHocIngestion。确认 `Mark sessions as committed` 将 `Status` 翻转为 `committed`，且该 session 的 `MovieHistoryAudit` 行被清理。
3. 冒烟测试失败路径。在 `Step 1 - Run Spider` 后注入 `exit 1`。观察 `cleanup-on-failure` 运行，rollback 日志报告 `mode='audit_replay'`，且验证行发出 `pending_staged_count=0`。
4. 冒烟测试手动工作流。以 `dry_run=true` 和刚回滚的 session id 分发 `Rollback D1 Session`。确认计数为零。
5. 在升级前恢复注入的失败。

> 2026-08-13 之后此手册不可运行——`audit` 值被拒绝。Pending 模式是唯一支持的路径；需要逐字节历史重建的手动事故必须使用 `scripts/sync_d1_to_sqlite.py` 对照日落前备份进行回退。
- 测试：[`tests/unit/test_rollback.py`](../../../tests/unit/test_rollback.py)、[`tests/unit/test_rollback_pending_mode.py`](../../../tests/unit/test_rollback_pending_mode.py)
