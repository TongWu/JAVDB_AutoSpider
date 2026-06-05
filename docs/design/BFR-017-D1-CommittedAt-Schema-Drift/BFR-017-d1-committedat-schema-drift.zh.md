# BFR-017: D1 schema 漂移 —— `ReportSessions.CommittedAt` 从未应用到远程 D1

**Status**: Fixed
**Date**: 2026-06-05
**Severity**: Critical（D1 后端下每次 pipeline 运行都在 commit 阶段失败）
**Affected**: `javdb/storage/db/_db_reports.py`, `javdb/storage/db/_db_history_write.py`, `javdb/migrations/d1/2026_06_03_add_reports_committed_at.sql`, `javdb/infra/health_check.py`, `javdb/storage/db/_db_migrations.py`
**Related**: [PR #165](https://github.com/TongWu/JAVDB_AutoSpider_CICD/pull/165)（引入了此漂移）, DailyIngestion run [27015949886](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/27015949886)

---

## 症状

2026-06-05 的 `DailyIngestion` 运行在两处失败，均为同一个 D1 错误：

- **`run-pipeline` → "Mark sessions as committed"**（exit 1）：
  ```
  ✗ __main__  db_commit_session_history failed for pending session
    20260605T125521.738770Z-c765-0000:
    D1 API returned HTTP 400: [{'code': 7500,
    'message': 'no such column: CommittedAt: SQLITE_ERROR'}]
  ```
- **`Cleanup Failed Pipeline` → "Roll back uncommitted D1 writes"**（exit 4）：rollback
  CLI 通过 `--auto-resume-finalizing` 尝试把 session 推到 `committed` 时，报同样的
  `no such column: CommittedAt`。

spider、uploader、file-filter、PikPak、rclone 各步骤全部成功 —— 失败纯粹发生在 session
commit 边界。该 session 卡在 `Status='finalizing'`，其已暂存的 pending 行既没被提升、也没被
回滚。

## 根因

[PR #165](https://github.com/TongWu/JAVDB_AutoSpider_CICD/pull/165)（2026-06-05T00:46Z
合并进 `main`）同时引入**两项耦合的改动**：

1. 写该列的代码 —— `db_commit_session_history` / `db_finish_commit_session` 现在执行
   `UPDATE ReportSessions SET Status='committed', CommittedAt=strftime(...)`
   （[`_db_reports.py:165`](../../../javdb/storage/db/_db_reports.py)、
   [`:770`](../../../javdb/storage/db/_db_reports.py)）。
2. 配套的 D1 迁移
   [`2026_06_03_add_reports_committed_at.sql`](../../../javdb/migrations/d1/2026_06_03_add_reports_committed_at.sql)
   （`ALTER TABLE ReportSessions ADD COLUMN CommittedAt TEXT`）。

**这个迁移从未在远程 D1 上执行。** 按 `CLAUDE.md` 中「D1 为权威」的纪律，D1 schema 变更需通过
`wrangler d1 execute … --file=…` **带外（out-of-band）**应用；这一手动步骤被遗漏，于是代码已在
`main` 上线，而 D1 仍缺少该列。

测试与本地运行看不到这个漂移，是因为**本地 SQLite 会自愈**：`_ensure_rollback_columns`
（[`_db_migrations.py`](../../../javdb/storage/db/_db_migrations.py)）在 `init_db()` 时会幂等地
把该列 `ALTER` 进任何已存在的 SQLite 库。D1 没有等价的自动迁移，所以只有
`STORAGE_BACKEND=d1` 的生产路径受影响。

更深层的设计缺陷：**代码侧对某列的依赖，与 D1 侧迁移的应用是解耦的，而在工作开始前没有任何
环节校验两者是否一致。** 因此这种不匹配只能在 *commit* 阶段（抓取完成之后）暴露，而非在启动时。

## 修复

**即时补救（数据）：**
- 对远程 D1 `javdb-reports` 执行了 `ALTER TABLE ReportSessions ADD COLUMN CommittedAt TEXT`。
- 对卡住的 session `20260605T125521.738770Z-c765-0000` 重跑 commit
  （`python3 -m apps.cli.db.commit_session --session-id … --no-claim-commit`）：45 部影片 / 76 个种子
  提升进正式表，204 行 pending 清空，`Status` → `committed`，`CommittedAt` 已写入。D1 恢复一致。

**防止复发（代码）：**
- `javdb/storage/db/_db_migrations.py`：把 rollback/pending 列清单抽取为模块常量
  `ROLLBACK_COLUMN_SPECS`（单一事实来源，原先是 `_ensure_rollback_columns` 内的局部变量），
  并新增 `find_missing_rollback_columns(conn)` —— 一个只读审计，返回某个 live 连接上缺失的期望列。
  对 sqlite3 与 D1 连接均可用。
- `javdb/infra/health_check.py`：新增 `check_d1_schema()` 作为**关键（critical）**的飞行前检查
  （排在第一个）。在 `d1`/`dual` 后端下，它审计全部三个逻辑 D1 库，一旦缺任何 rollback/pending
  列就**在 spider 启动前让本次运行失败**，并给出指向未应用迁移的提示。SQLite 被跳过（它会自愈）。
  原有的非关键检查（qB、proxy、SMTP）行为不变。
- `tests/unit/test_d1_schema_drift_guard.py`：8 个测试锁定审计逻辑（检出精确的 `CommittedAt`
  形态、忽略不存在的表、兼容 D1 的 dict 行、断言 `ROLLBACK_COLUMN_SPECS` 与真实 init schema 一致）
  以及 `check_d1_schema`（非 D1 跳过、报告漂移、齐全时通过）。

`DailyIngestion.yml` / `AdHocIngestion.yml` 的 health-check 步骤以 `set -e` 运行且没有
`continue-on-error`，因此 `check_d1_schema` 失败会在飞行前阶段中止该 job —— 不浪费抓取、不留下
卡住的 session。

## 副作用

- 在 `d1`/`dual` 后端下，飞行前现在会打开三个短连接读取 `PRAGMA table_info`，增加几秒耗时，
  并要求 health-check 时已有 D1 凭据（已满足 —— `Restore encrypted config` 步骤在之前运行）。
- `check_d1_schema` 是**第一个真正能让 pipeline 失败的检查**。原有的 `all_passed` 汇总路径
  （从不失败）对现有的信息性检查原样保留；新的关键失败路径是增量式的。
- SQLite 后端行为不变（该检查被跳过）。

## 后续工作

- [x] 对远程 D1 应用 `CommittedAt` 迁移。
- [x] 重跑 commit 救回卡住的 session `20260605T125521.738770Z-c765-0000`。
- [x] 增加飞行前 D1 schema 漂移防护 + 单元测试。
- [ ] 考虑将审计范围从 rollback/pending 列扩展为完整的 `migrations/d1/*.sql` ↔ live-D1 列 diff
      （可捕获任何未来迁移的漂移，而不仅是 rollback 关键列）。
- [ ] 考虑在新增 `javdb/migrations/d1/*.sql` 文件的 PR 上加一个 CI 提醒，合并前提示「是否已应用到远程 D1？」。
