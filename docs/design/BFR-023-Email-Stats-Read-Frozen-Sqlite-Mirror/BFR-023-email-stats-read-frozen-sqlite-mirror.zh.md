# BFR-023: D1 切换后，流水线邮件静默丢失了数据库统计

**Status**: Fixed
**Date**: 2026-07-26
**Severity**: Medium
**Affected**: `javdb/integrations/notify/email/service.py`
**Related**: [ADR-010](../_archive/ADR-010-D1-Access-Port/ADR-010-d1-access-port.zh.md), [ADR-047](../_archive/ADR-047-Dual-Backend-Drift-Reconciliation/ADR-047-dual-backend-drift-reconciliation.zh.md), [BFR-021](../BFR-021-D1-Session-Write-Silent-Loss/BFR-021-d1-session-write-silent-loss.zh.md)

---

## Symptom

没有报错，没有失败的 job —— 这个缺陷本身就是"信号缺失"。

自 `STORAGE_BACKEND` 切到 `d1`（Production 变量，2026-05-22）之后，每一封
DailyIngestion / AdHocIngestion 通知邮件里的 Spider / Uploader / PikPak 段落，都是用 CSV 和
日志推算出来的兜底数字渲染的，而不是本轮真正记录下来的 per-session 统计行。邮件看上去依然
完整，所以没有任何东西提示它已经降级。唯一的痕迹是一行 CI 日志级别根本不会输出的
`logger.debug`：

```text
DEBUG  SQLite stats not available: <...>
```

佐证：本地 mirror 自切换后就被冻结了 —— 每一次改动它的提交都是人工重新对齐，没有一次来自
流水线的 auto-commit：

```shell
$ git log -3 --format='%h %ad %s' --date=short -- reports/reports.db
7e368500 2026-06-10 refactor(storage): retire the public db_* facade (ADR-046 Phase 4)
f992a10b 2026-05-31 chore(db): sync local sqlite mirrors for ADR-024
d06cab32 2026-05-31 chore(db): realign local SQLite mirror to D1 (absolute hrefs, BFR-010)
```

## Root Cause

**一条正确的规则，活得比它赖以成立的前提更久。**

P0-6 规则说：*统计必须来自权威的 SQLite mirror，绝不能来自 D1。* 它的**行为**在写下时是对的，
但措辞不对：dual 模式一直只用于迁移验证，SQLite 从来不是权威 —— 漂移始终向 D1 收敛
（CLAUDE.md，"D1 is the canonical source of truth"）。这条规则在 dual 下真正做的事，是去读
*确定*接收了这次写入的那一侧，好让"一次不对称的 dual-write 使 D1 少 N 行"暴露出来、而不是被
悄悄抹平 —— 正是 2026-05 那次 `ReportSessions` / `SpiderStats` `-1` 事故。这是一个可观测性例外，
且只在 dual 模式仍在验证 D1 写入路径期间才成立。它由专门的 `_local` 仓储变体落实，这些变体无视
backend、直接开一个裸 `sqlite3` 连接。

d1 单写切换把前提整个反转了，却没有动这条规则。D1 成了权威，SQLite mirror 彻底不再被写入
（`get_db()` 交出的是 `D1Connection`，永远不会是 `_open_sqlite_connection`）。于是那次强制本地
读除了失败之外不可能有别的结果：它要在一个自五月以来没有任何一次运行写过的文件里，查找*本轮*的
`SessionId`。

有两个次要因素让它一直不可见：

1. **判断条件用错了轴。** 这段代码由 `use_sqlite()` 守卫，而该函数读的是 `STORAGE_MODE`
   （`csv` / `db` / `duo`）—— 这是"写不写 CSV"的问题，不是 `STORAGE_BACKEND`
   （`sqlite` / `d1` / `dual`）那个"用哪个数据库"的问题。Production 里 `STORAGE_MODE=duo`，
   所以闸门始终敞开，代码不是跳过，而是自信地一直读错了那一侧。
2. **失败路径本来就被设计成安静的。** 统计只是叠加在日志/CSV 数字之上的装饰，所以整段包在
   `try/except` → `logger.debug` 里。对付一次瞬时的后端抖动这样是对的，但它同时也吞掉了一个
   永久性的结构性缺失。

当时没有任何测试钉住"邮件到底读哪个后端"。只有
`tests/unit/test_email_notification_p0.py`，它断言的是 `db_get_spider_stats_local` 自身会直接
打开 SQLite —— 而这个仓储方法从来就不是坏掉的那一环，所以它一直是绿的。

## Fix

[PR #256](https://github.com/TongWu/JAVDB_AutoSpider_CICD/pull/256)。

规则的意图没有改变 —— *读真正接收了本轮写入的那一侧* —— 只是现在指向了确实是那一侧的地方：

| `STORAGE_BACKEND` | 数据来源 | 原因 |
| --- | --- | --- |
| `d1` | D1（`StatsRepo` / `SessionsRepo` 的 backend-aware 方法） | D1 是事实来源；SQLite mirror 已冻结。 |
| `sqlite` | 本地 SQLite（`*_local` 变体） | 此时只有这一个后端在起作用。 |
| `dual` | 本地 SQLite（`*_local` 变体） | **可观测性例外，不是权威声明。** D1 仍是事实来源，漂移依旧向 D1 收敛。读那个确定接收了写入的一侧，才能让 D1 的缺口暴露出来；这次读取绝不可被当作"D1 写入成功"的证据，也不可用于反向对账。行为与 P0-6 一致，未改动。 |

`javdb/integrations/notify/email/service.py` 的关键改动：

- 把内联代码块抽成 `_load_run_stats()`，返回 `_RunStats` NamedTuple —— 这样这条来源选择规则
  才第一次变得可被测试触达。
- 判断条件改读 `current_backend()`（纯配置读取，在 `init_db()` 之前调用是安全的）；
  `use_sqlite()` 只保留在它真正描述的 sqlite/dual 分支上。
- 没有 `--session-id` 时的兜底查询也做成 backend-aware：d1 下用
  `SessionsRepo.get_latest_session()`，否则用 `SessionLifecycleRepo.get_latest_session_local()`。
- 修掉一个潜伏的 `NameError`：三处 `logger.info(f"... {_cur_be()} ...")` 引用的名字只在旧的
  `if use_sqlite():` 分支内绑定。统计一直取不到时它们不可达，而 d1 路径开始返回数据的那一刻就
  会触发。现在改用 helper 报告的 `backend_label`。由
  `tests/harness/test_scenario_notify.py::test_daily_notify_email_is_captured` 抓到。

新增测试 `tests/unit/test_email_stats_source.py`（7 个用例），钉住来源选择矩阵、backend-aware
的 session 兜底，以及降级为空的行为。做过反向验证：把生产逻辑改回旧的"永远读本地"形态，7 个中
有 4 个失败，而三个 sqlite/dual 用例保持通过。

## Side Effects

- **d1 下的邮件重新报告真实数字。** Spider / Uploader / PikPak 段落里的数字可能与最近几次运行
  不同 —— 之前那些是日志/CSV 估算值，不是记录下来的统计行。这是修复在生效，不是回归。
- **每封通知多一组 D1 读取**（spider + uploader + pikpak，按 `SessionId`）。开销可忽略，且仍在
  既有的 best-effort `try/except` 之内。
- **`sqlite` 与 `dual` 的行为逐字节未变。** 强制本地路径及其反漂移保证原封不动，参数化测试对此
  有断言。
- 对强制本地变体其他调用方的排查结果是干净的：`_local` 仓储方法在 DB 层之外只有一个调用方，就是
  本文件。`log_analysis.py` 里相邻的那个 `use_sqlite()` 闸门走的是 backend-aware 的
  `OperationsRepo.load_dedup_records()`，从来不受影响。

## Follow-Up

- [ ] 在邮件正文里标出统计来源（或把"统计不可用"的日志提升到 `warning`），让下一次静默降级不用
      读代码就能看见。这里真正的问题类别是*永久性失败上的安静兜底*。
- [ ] 考虑重命名 `use_sqlite()` —— 这个名字正是上面那种混淆的来源。`STORAGE_MODE`（csv 还是 db）
      与 `STORAGE_BACKEND`（哪个数据库）是正交的，而 `javdb/infra/config.py:128` 已经需要写成
      `use_sqlite() or storage_backend() in ('d1','dual')` 才能表达"DB 层在起作用"，而后者才是
      多数调用方真正想表达的意思。
- [ ] SQLite mirror 最终退役时，直接删掉 `*_local` 仓储变体和这个分支，而不是留下一条死的
      sqlite/dual 分支。
