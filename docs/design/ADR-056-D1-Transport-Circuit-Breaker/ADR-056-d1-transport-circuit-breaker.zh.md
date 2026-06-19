# ADR-056：面向瞬时宕机韧性的 D1 传输层熔断器

**状态 (Status):** Accepted
**日期 (Date):** 2026-06-15
**作者 (Author):** Ted
**关联实现计划 (Related Implementation Plans):** [IMP-ADR056-01](IMP-ADR056-01-d1-transport-circuit-breaker.md)（Phase 1 — 熔断器 + 有界等待 + 测试）
**D1 写入类别 (D1 Write Class):** n/a  <!-- 仅传输韧性；不引入任何新的 D1 写入 -->

## 背景 (Context)

一次瞬时的 Cloudflare D1 宕机就能让整个 pipeline run 失败，且毫无优雅处理。
2026-06-15（[run 27551555092](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/27551555092)，
见 [BFR-020](../BFR-020-D1-Recovery-Outbox-Replay-After-Rollback/BFR-020-d1-recovery-outbox-replay-after-rollback.zh.md)）
一笔批处理历史暂存写在 flush 时撞上 `HTTP 500（code 7500，"internal error"）`。
D1 port 已按 `D1_MAX_RETRIES=5`（base 1s、上限 30s ≈ 共约 31s）做指数退避重试，但 D1
在整个窗口都返回 500，于是 `D1TransientError` 上抛、spider 崩溃（exit 1）。整个 Daily
Ingestion run 失败，session 被回滚。

当前行为有三个弱点：

1. **worker 之间无协调。** D1 连接是 thread-local
   （[_db_connection.py](../../../javdb/storage/db/_db_connection.py) `threading.local()`），
   ~8 个 spider worker 各持独立 `D1AccessPort` 与各自的重试状态。宕机时它们各自对着
   本已挣扎的 D1 盲烧重试预算，然后逐个失败。
2. **重试窗口扛不住真正的 brownout。** ~31s 的每语句重试无法熬过数分钟级的 Cloudflare 事故。
3. **现有余量没被利用。** Daily Ingestion 作业**未设 `timeout-minutes`**（GitHub Actions
   默认 6h），正常 run 约 6 分钟，因此有充裕 wall-clock 去**等过**一次瞬时 brownout——
   但当前什么都没做。

我们希望：一次瞬时基础设施抖动**不**拖垮整个 pipeline，同时行为保持可预测，并复用现有
的 fail-fast + 回滚路径作为终态。

## 决策 (Decision)

在 **D1 传输层增加一个进程级全局熔断器**。每次 D1 HTTP POST 都查询同一个共享熔断器。
持续宕机时熔断器跳闸、**暂停所有 D1 访问**（任何发 POST 的线程都会阻塞），由单个探活者
用 `SELECT 1` 轮询 D1 健康；恢复后熔断器关闭、唤醒所有等待者继续。若 D1 在有界窗口内仍
未恢复，熔断器抛出终态错误，沿现有的崩溃 → 回滚路径走完。

这是**传输层强化，而非优雅降级**：run 绝不会"带着待补写入算成功"。它要么正常完成（可能
在一次暂停之后），要么快速失败并回滚——与今天相同的终态语义，只是对瞬时 brownout 有韧性。

### 设计决策 (Design Decisions)

D1. **传输层按 D1 数据库划分的熔断器** — 一组 `D1CircuitBreaker` 实例（`threading.Lock` +
`threading.Condition`）按 **D1 端点 URL** 建索引，位于 `javdb/storage/`，由
`D1AccessPort._post` 查询。它**不是** per-port 状态（连接是 thread-local，同一数据库的所有
线程端口共享一个熔断器），也**不是**跨三个 D1 数据库（history / reports / operations）的单一
全局对象：它们各自独立故障，因此 `history` 宕机绝不能被一次健康的 `reports` 探活宣告恢复。
按端点建索引保证被选中的探活者始终探测**真正跳闸**的那个数据库。它统一覆盖**所有** D1 调用方
（spider、pipeline、qb、rollback CLI），无需逐 CLI 改动。

D2. **连续失败跳闸** — 共享计数器在每次瞬时 5xx（任一线程）时 +1、任一成功时清零。达到
`D1_BREAKER_TRIP_THRESHOLD`（默认 3）即 `CLOSED → OPEN`。这能在 D1 大面积宕机时快速跳闸、
暂停队列，而非让每条语句各自耗尽重试。用严格连续计数（而非时间窗口）保持逻辑简单、对锁
友好；它是启发式，由阈值调节。

D3. **内联探活者选举** — 第一个观察到 `OPEN` 的线程成为探活者：sleep
`D1_BREAKER_PROBE_INTERVAL_SEC`（默认 5s）后发 `SELECT 1`。不另起后台守护线程——反正所有
worker 都已暂停，由其中之一探活、其余在 `Condition` 上等待，是最自然、线程最少的设计。
连续 `D1_BREAKER_HALF_OPEN_SUCCESSES`（默认 1）次探活成功后，熔断器 `HALF_OPEN → CLOSED`
并 `notify_all()` 唤醒所有等待者。

D4. **有界 OPEN → 终态 fail-fast + 回滚（对守护进程可恢复）** — `OPEN` 期间，等待者最多阻塞
`D1_BREAKER_MAX_OPEN_SEC`（默认 900=15min，从 `opened_at` 起算）。若到期 D1 仍未恢复，
熔断器进入 `TERMINAL`，唤醒所有等待者，它们（与探活者）抛 `D1CircuitOpenError`。该错误上抛
使 run 崩溃，由现有 `cleanup-on-failure` 作业回滚 session——可预测、复用成熟机制、且绝不会
把 runner 占满 6h。**`TERMINAL` 不是永久汇点：** 一次性运行（spider / CLI / Actions）在抛错时
崩溃退出，因此永不 re-arm；但长期存活的进程（例如运行在 D1 backend 上的 `apps/api` 后端）会在
抛错后存活，其缓存的进程全局熔断器本会永久拒绝该数据库。为避免该自锁，`TERMINAL` 熔断器在下一次
`acquire()` 时——只要从 `terminal_at` 起又过了一个 `D1_BREAKER_MAX_OPEN_SEC` 冷却期——会
re-arm 回 `CLOSED`，给已恢复的 D1 一次全新尝试（若仍宕机则从头重新跳闸、重新探活）。该冷却期在
崩溃的 run 内永不到期，故一次性 fail-fast 语义不变。

D5. **终态错误绕开 recovery outbox（不加重 [BFR-020](../BFR-020-D1-Recovery-Outbox-Replay-After-Rollback/BFR-020-d1-recovery-outbox-replay-after-rollback.zh.md)）**
— `D1CircuitOpenError` 继承 `D1Error` 但**不是** `D1TransientError`，因此
`D1AccessPort.flush()` 的 `except D1TransientError` recovery 排队处理器不会捕获它。终态失败
因此不会把写入持久化排队待重放，也就不会产生"回滚后被 recovery-outbox 重放"的孤儿。
BFR-020 仍是独立修复项，但熔断器同时大幅降低 recovery-outbox 路径被命中的频率。

D6. **保留内层每语句重试作为薄层** — `_post_with_retry` 保留现有指数退避
（`D1_MAX_RETRIES=5`、尊重 `Retry-After`），让 1～2 次的瞬时小抖动在不跳闸的情况下被吸收。
唯一微调：对 `code 7500` 内部错误用稍长的退避上限。熔断器是主安全网；重试循环是第一道防线。

D7. **默认开启** — `D1_CIRCUIT_BREAKER_ENABLED` 在代码内默认 `true`。除非 D1 传输路径确实
返回 5xx，否则熔断器惰性；在非 D1 backend（`STORAGE_BACKEND=sqlite`）下没有 D1 POST，故为
空操作。需要关闭的测试显式设置该环境变量。

D8. **探活旁路 + 与响应体一致的成功判定** — 探活者的 `SELECT 1` 走专用 `_probe_d1` 旁路，
跳过 `breaker.acquire()`，使健康检查不会在已打开的熔断器上死锁。探活采用与 `_post`**相同**的
成功判定——HTTP 200 **且** JSON 响应体 `success == true`——因此应用层 D1 故障（HTTP 200 +
`success=false`）不会被误判为恢复、把整个队列唤醒后再次失败。

## 后果 (Consequences)

### 正面 (Positive)

- 至多 ~15min 的瞬时 D1 brownout 不再使 run 失败；pipeline 自动暂停并恢复。
- 协调暂停：一个探活者，而非 ~8 个 worker 轮番冲击挣扎中的 D1。
- 通用——所有 D1 调用方（spider + 全部 CLI）免费获得韧性，无需改动调用点。
- 终态不变（fail-fast + 回滚），监控/回滚 SOP 仍然有效；仅时机改变（在真·持续宕机后才失败，
  而非瞬时小抖动）。
- 不加深 BFR-020 孤儿隐患（D5）。

### 负面 (Negative)

- 暂停期间 worker 阻塞在 D1 调用**内部**；除非熔断器自己打日志（已打），否则没有 spider 级
  "已暂停 N 个 worker、预计 HH:MM 恢复"的 UX。
- 长暂停（数分钟）可能使代理登录态 / `JAVDB_SESSION_COOKIE` 以及 MovieClaim / WorkDistributor
  租约变陈旧；spider 必须容忍 resume。**作为实现期验证关卡（IMP 风险 R1/R2）跟踪**，而非本
  ADR 强制的 resume 重验功能。
- 新增模块全局可变状态 + `Condition`——需谨慎处理线程安全并充分测试。
- 单线程 CLI（如 cleanup 中的 rollback）可能阻塞至多 `MAX_OPEN_SEC`；可接受，且可经环境变量
  按上下文调小。

## 实施路线图 (Implementation Roadmap)

| 阶段 | IMP | 交付内容 | 推迟内容 |
| --- | --- | --- | --- |
| Phase 1 ✅（2026-06-19 交付） | [IMP-ADR056-01](IMP-ADR056-01-d1-transport-circuit-breaker.md) | `D1CircuitBreaker` 按 DB 注册表 + 状态机、`_post` 集成、`D1CircuitOpenError`、构造时读取的环境变量（默认开）、对 7500 的内层退避微调、可观测（日志 + 聚合的 `d1_port_summary` 指标）、单元 + port 级 + 并发测试、`vars` 接线到**所有** D1 workflow | BFR-020 孤儿修复（独立）；可选的 spider 侧显式 worker 池暂停 UX（方案 B）；resume 时登录/租约重验（仅当 R1/R2 验证表明需要） |

## 参考 (References)

- [BFR-020](../BFR-020-D1-Recovery-Outbox-Replay-After-Rollback/BFR-020-d1-recovery-outbox-replay-after-rollback.zh.md) — 触发本决策的失败；孤儿隐患保持独立（D5）
- [ADR-010](../_archive/ADR-010-D1-Access-Port/ADR-010-d1-access-port.zh.md) — 拥有传输、重试/退避与 recovery facade 的 D1 Access Port，本熔断器包裹其上
- [run 27551555092](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/27551555092) — 触发的瞬时 `HTTP 500（code 7500）`

## 状态日志 (Status Log)

- 2026-06-15: Proposed
- 2026-06-19：**已接受 (Accepted)。** Phase 1（[IMP-ADR056-01](IMP-ADR056-01-d1-transport-circuit-breaker.md)）已交付——熔断器注册表 + 状态机、`_post_with_retry`/`_probe_d1` 集成、`D1CircuitOpenError`、构造时读取的环境变量（默认开）、7500 退避下限、`d1_port_summary.json` 中聚合的 `circuit_breaker` 指标、17 个通过的熔断器测试，以及 `D1_CIRCUIT_BREAKER_ENABLED` 接线到全部 12 个 D1 workflow（recovery 作业上限 120s）。
  - **R1（登录/会话 resume）——验证安全：** `JAVDB_SESSION_COOKIE` 是每次请求重放的静态凭据（`javdb/infra/request.py` 在每次抓取时设置 `_jdb_session` Cookie 头），登录通过页面抓取惰性校验；≤15 分钟的暂停不会让客户端登录状态过期，服务端会话 TTL 也远超 max-open 窗口。无需 resume 重验功能。
  - **R2（MovieClaim / WorkDistributor 租约）——验证安全：** 协调器客户端为尽力而为的 fail-open（`javdb/spider/detail/runner.py` 捕获 `MovieClaimUnavailable` → 回退到进程内去重；登录态/会话路径同样 fail-open）。MovieClaim 默认 TTL 为 30 分钟（> 15 分钟 max-open），故默认 claim 可挺过最长暂停；暂停期间租约过期最坏只导致一次被历史去重的重复抓取。无需租约续约功能。路线图中推迟的项（BFR-020 孤儿修复、方案 B 暂停 UX）维持不变。
- 2026-06-19：PR review 跟进（PR #240，Codex P2）——将 `TERMINAL` 改为可恢复，使长期存活的进程（运行在 D1 上的 `apps/api`）在一次 >15 分钟故障后不再自锁：`TERMINAL` 熔断器会在再经过一个 `D1_BREAKER_MAX_OPEN_SEC` 冷却期后 re-arm 回 `CLOSED`（一次性 fail-fast 语义不变——见修订后的 D4）。新增 `rearms` 指标与确定性 re-arm 测试。
