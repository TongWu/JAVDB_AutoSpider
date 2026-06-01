# ADR-043: 协调器侧 Proxy 封禁共享与 CF 自动封禁

**状态 (Status):** Completed
**日期 (Date):** 2026-05-31
**作者 (Author):** Ted
**关联实现计划 (Related Implementation Plans):**
[IMP-ADR043-01](IMP-ADR043-01-cf-auto-ban-worker.md)（Phase 1 — Worker 侧 CF 自动封禁升级）、
[IMP-ADR043-02](IMP-ADR043-02-bfr009-ban-dispatch-and-hardban-ttl.md)（Phase 2 — 修 BFR-009 Rust→Python 封禁派发 + 硬封禁 DO 共享）

## 背景 (Context)

代理失效时有两种需要跨 runner 处理的失败模式，而目前**两种都没在 GitHub Actions
runner 之间被正确共享**。

### 失败模式 1 —— 持续无法越过 CF 墙（瞬态）

代理出口 IP 被 Cloudflare 单独盯上，持续撞 CF 挑战，而其他代理仍能通过。

- 撞到 CF 挑战时爬虫调用 `mark_proxy_cf_bypass(...)`
  （`javdb/spider/runtime/proxy_state.py:114`），它只把该代理改走 CF-bypass 服务，
  **仍留在轮换里**。永远过不了 CF 的代理依然可选、每行都被重试——这很可能是数小时级
  `Database Migration` 运行的根因（backfill/align 会经代理池抓取 JavDB 实时详情页）。
- 存在 CF→封禁路径（连续 `cf_bypass_ban_threshold = 6` 次 CF-bypass 失败 →
  `ProxyBannedError`，`javdb/infra/request.py:1416`），但其计数器
  `cf_bypass_failure_count` 是**全局跨代理、且一旦有任何成功就清零**，所以在大池子里
  偶发成功会让它很少触发——真正被墙的代理可能永远不被封禁。

### 失败模式 2 —— JavDB 显式 IP 封禁（硬封禁，约一周）

JavDB 封禁某 IP 时会返回纯文字封禁页（"banned your access" / "管理員禁止了你的訪問"，
`html/ban.html`），由 `is_ban_page(...)` → `ProxyBannedError` 检测
（`javdb/infra/request.py:856`、`:1037`）。运维上这种封禁持续**约一周**（~7 天，JavDB 侧策略）。

### 为什么什么都没共享 —— BFR-009

两条封禁路径都汇入 `proxy_pool.ban_proxy(...)` / `get_ban_manager().add_ban(...)`，
而生产里它们是 **Rust** 池/封禁管理器。据
[BFR-009](../../BFR-009-Rust-Pool-Cross-Runner-Ban-Dispatch/BFR-009-rust-pool-ban-dispatch.zh.md)：
远程封禁 hook `set_remote_ban_hook(client.mark_proxy_banned)` **已注册**
（`context.py:880`、`state.py:722`），但 `_dispatch_remote_ban` 只被 *Python* 池/封禁
管理器调用。Rust 的 `ban_proxy` / `add_ban`（在 PyO3 扩展内）**触达不了** Python hook，
所以 `mark_proxy_banned()` → Worker DO 广播**在生产中从不触发**。每次生产封禁——CF 阈值
封禁*与*硬 JavDB 封禁——都**只在本地记录**。本工作正面修掉 BFR-009（见 D8）。

### 哪些已经能用（缺的是什么）

- **消费侧没问题。** 每次 `lease`，客户端从 Worker 读 `banned` / `banned_until`，runner
  通过 `_mirror_remote_ban_locally(...)`（`javdb/spider/runtime/sleep.py:800`）把远端封禁
  写入本地状态，下一轮选路就跳过。坏掉的是**发布**侧。
- **每代理的 CF 事件早已汇到 Worker**：经 `report_async(proxy_id, "cf")`
  （`context.py:984`、`state.py:2016`），DO 累积在 `CoordinatorState.cfEvents[]`
  （按 `penaltyWindowSec` 老化，默认 300 秒）——这是**跨 runner 聚合**信号。但 Worker
  **从不把 `cfEvents` 升级成封禁**；`bannedUntil` 只由显式 `kind="ban"` 上报设置。
- **TTL 不匹配。** 即便修好 BFR-009，DO 默认封禁 TTL 是 `DEFAULT_BAN_TTL_MS = 3 天`——
  *短于* JavDB 的 ~7 天硬封禁。代理在 DO 里 3 天就解封并被重新选中，而此时它在 JavDB 侧
  还要被封好几天，于是再撞封禁页。

## 决策 (Decision)

让协调器成为代理封禁的跨 runner 真相源，且封禁 TTL 对齐底层成因的**真实恢复窗口**：

1. **CF 墙持续失败 → Worker 侧短自动封禁（6 小时）。** DO 把它聚合的 `cfEvents`（带
   zero-success 守卫）升级成一个短 `bannedUntil`。不依赖 BFR-009。→ IMP-ADR043-01。
2. **所有生产封禁 → 真正发布到 DO**，方法是用 Rust→Python 封禁派发回调修掉 BFR-009。
   硬 JavDB 封禁用 **8 天** DO TTL；派发携带封禁*原因*，让 DO 按原因映射 TTL。
   → IMP-ADR043-02。
3. **保留 runner 本地封禁**，作为两层模型里的同 run / fail-open 层。

### 设计决策 (Design Decisions)

**D1. CF 触发点 —— `handleReport` 的 `kind="cf"` 分支。** 在 CF 事件被 push 进
`cfEvents` 之后做升级判定。事件驱动，按代理天然隔离（一个代理一个 DO）。

**D2. CF 升级条件 —— 持续 CF 且零成功。** 在 `penaltyWindowSec`（默认 300 秒）窗口内：

```
cfEvents.length     >= CF_AUTO_BAN_THRESHOLD   (默认 6)
AND successEvents.length === 0
=> state.bannedUntil = max(state.bannedUntil ?? 0, now + CF_BAN_TTL_MS)
```

**zero-success 守卫**是精度核心：偶发失败（与成功交错）的代理永不触发；只有在整个窗口里
**什么都没过去**的代理才被封。这也吸收了"全站短暂抖动但很快恢复"。阈值 6 高于节流 penalty
顶档（4 个事件 ⇒ ×2.0），并锚定 Python 的 `cf_bypass_ban_threshold = 6`。

**D3. CF 短 TTL。** `CF_BAN_TTL_MS` 默认 **6 小时**（`21_600_000`）。CF IP 信誉封锁几分钟到
几小时即恢复，而被封代理不再产生成功上报，所以只能靠 TTL 到期恢复——故要短。

**D4. 与已有封禁取 monotonic-max。** 复用现有 monotonic-max 策略
（`proxy_coordinator.ts:355`）：更长的封禁（如 8 天硬封禁）绝不会被 6 小时 CF 封禁缩短，反之亦然。

**D5. 标记封禁来源以便可观测。** 记录封禁*原因*（`cf_auto`、`javdb_hardban`、`manual`）——
通过 DO 的 analytics / event-log（和/或轻量 `bannedReason` 标记）暴露，让 dashboard 与
`/do/state` 能区分。具体机制属 IMP 细节。

**D6. CF 自动封禁默认开启，带 kill-switch。** `CF_AUTO_BAN_ENABLED` 默认 **true**；运维可
通过 `wrangler.toml [vars]` 即时关停，无需改代码。新 env var 沿用 `types.ts` 现有的
`loadXxx(env)` 模式：

| Env Var | 默认 | 含义 |
| --- | --- | --- |
| `CF_AUTO_BAN_ENABLED` | `true` | CF 升级机制的总开关 / kill-switch |
| `CF_AUTO_BAN_THRESHOLD` | `6` | penalty 窗口内触发封禁的 CF 事件数 |
| `CF_BAN_TTL_MS` | `21600000`（6 小时） | CF 短封禁时长 |

CF 窗口**复用** `penaltyWindowSec`（300 秒）——不引入新常量。

**D7. 范围外 —— 全站 CF 失效。** 当*所有*代理都过不了 CF（全站 CF 收紧或 bypass 服务故障）
时，封单个代理是错的解药。本 ADR **刻意不**加跨 DO / 全局熔断。保留的便宜保险：zero-success
守卫，加上 6 小时 CF TTL，即便整池被误封也在 6 小时内自愈。真正的全局止损是单独的后续工作。

**D8. 用 Rust→Python 封禁派发回调修 BFR-009（Approach 1）。** 给 Rust 池/封禁管理器加
`set_ban_dispatch(callback)`，在每次*新记录*的封禁时调用（对应 Python 的 `newly_banned`
去重），由注册 `set_remote_ban_hook` 的同一运行时 setup 接线。选 Approach 1（而非更轻的
Python 入口包装，即 BFR-009 的 Approach 2）是因为 Rust 池在自动 drain / 切换代理时还会**内部**
记录封禁（`rust_core/src/proxy/pool.rs:485`、`:692`），这些从不经任何 Python 入口；只有
Rust→Python 回调能保证**每一次**封禁都被发布。这为**所有**封禁原因（不只 JavDB 硬封禁）关闭
BFR-009。

**D9. 硬封禁 DO TTL = 8 天，派发携带封禁原因。** 封禁派发回调转发封禁*原因*，让 DO 按原因
选 TTL，而非永远用 3 天默认。JavDB 硬封禁（`is_ban_page`）用 `HARD_BAN_TTL_MS`，默认 **8 天**
（比 JavDB 观测到的 ~7 天窗口多 1 天余量，避免早一拍解封又撞封禁页）。该默认值文档标注为
**运维观测的外部值**（JavDB 侧策略），env 可调——不是我们能控制的写死站点常量。CF 阈值
`ProxyBannedError`（"CF bypass failed N consecutive times"）映射到 CF 原因 → 6 小时，与 D2
的 Worker 侧升级一致。

| 封禁原因 | DO TTL | Env Var | 理由 |
| --- | --- | --- | --- |
| CF 墙持续失败（瞬态） | 6 小时 | `CF_BAN_TTL_MS` | IP 信誉恢复快 |
| **JavDB 显式 IP 封禁（硬）** | **8 天** | `HARD_BAN_TTL_MS` | 对齐 JavDB ~7 天窗口 + 余量 |
| 手动 / 运维 / 其他 | 3 天 | `BAN_TTL_MS`（现有默认） | 不变 |

**D10. 保留 runner 本地封禁 —— 两层模型。** D8 落地后本地封禁*并不*冗余，它覆盖 DO 做不到的：

- **同 run 即时性** —— 本 runner 在检测到时同步停用该代理；DO 封禁要等对端*下一次* lease 才生效。
- **Fail-open** —— 协调器降级/不可用时（`sleep.py:813` 熔断），本地封禁是唯一的拦截。
- **无协调器部署** —— 本地纯跑完全靠它。
- **它就是镜像载体** —— `_mirror_remote_ban_locally()` 把远端封禁写*进*本地封禁管理器，而
  选路读的正是它（`proxy.banned`）。

同 run 永久性是**有意为之**：本地镜像按 session 封禁（`_SESSION_BAN_COOLDOWN = 86400 * 365`），
所以 6 小时的 DO 封禁也会把一个已证实坏掉的代理在*当前* run 余下时间踢出（配合 D2 的
zero-success 守卫，该代理确实什么都没过去）。短 DO TTL 管的是**跨 run / 跨 runner** 的恢复，
不是同 run 复活。本地层（同 run + fail-open）与 DO 层（跨 runner、真实 TTL）互补而非重复——
无需改本地封禁时长。

## 后果 (Consequences)

### 正面 (Positive)

- 持续被 CF 墙住的代理会**在所有 runner 上**被短暂踢出轮换，终结"每行都重试同一个死代理"
  的空转——正是它造成了数小时级 migration。
- CF 检测用**跨 runner 聚合**信号（Worker 看到所有 runner）——远胜脆弱的单 runner 全局计数器。
- **BFR-009 对所有封禁原因被关闭**（D8）：每次生产封禁——CF 与硬封禁——终于跨 runner 广播，
  含 Rust 内部 switch/drain 封禁。
- 硬 JavDB 封禁以**对齐现实**的 TTL（8 天）共享，对端不会早解封又撞封禁页。
- IMP-01（CF 自动封禁）纯 Worker，**可独立交付**，与动 Rust 的 BFR-009 修复（IMP-02）解耦。
- 不属于 ADR-017 双后端重叠面（协调器内部，无 D1 / auth / API shape 改动）——web 仓 TS 后端无需同步。

### 负面 (Negative)

- **IMP-02 动 Rust crate（PyO3）** —— `set_ban_dispatch` 回调接线 + 把封禁*原因*穿过派发
  （hook 签名超出 `Callable[[str], None]`）。影响面大于纯 Python 改动。
- 本 ADR **把一个 bug 修复（BFR-009）耦合进特性 ADR** —— 应用户要求、有意为之，因为它们同根因；
  作为独立 IMP 跟踪、有自己的验证关卡。
- 派发一旦触发，行为异常的 runner 可能把封禁跨 runner 广播；由 monotonic-max、各原因保守 TTL、
  以及 CF 路径的 zero-success 守卫限定。
- CF 自动封禁**默认开**；阈值调错可能把健康代理封 6 小时（由 zero-success 守卫、阈值高于 penalty
  顶档、短 TTL、kill-switch 缓解）。
- 真正的**全站** CF 失效会把整池自动封禁最多 6 小时（D7 —— 范围外；6 小时自愈）。

## 实施路线图 (Implementation Roadmap)

| 阶段 | IMP | 交付内容 | 推迟内容 |
| --- | --- | --- | --- |
| Phase 1 | [IMP-ADR043-01](IMP-ADR043-01-cf-auto-ban-worker.md) | `handleReport` 中的 Worker CF 升级逻辑、`types.ts` 中 3 个 env-var loader、`wrangler.toml [vars]`、vitest 覆盖、封禁来源可观测、handbook `proxy-coordinator.md`（en/zh）env 文档 | — |
| Phase 2 | [IMP-ADR043-02](IMP-ADR043-02-bfr009-ban-dispatch-and-hardban-ttl.md) | BFR-009 修复（Rust `set_ban_dispatch` 回调 → Python hook，覆盖 Rust 内部封禁点）、封禁原因穿透、`HARD_BAN_TTL_MS`（8 天）DO 映射、生产入口派发测试、BFR-009 状态 → Fixed | 跨 DO 全站 CF 失效熔断器（D7）；`set_remote_unban_hook` 接口再评估 |

## 参考 (References)

- [BFR-009 — Rust Pool Cross-Runner Ban Dispatch](../../BFR-009-Rust-Pool-Cross-Runner-Ban-Dispatch/BFR-009-rust-pool-ban-dispatch.zh.md) —— 本 ADR 关闭的派发缺口（D8）；由 IMP-ADR043-02 修复
- [ADR-041 — Rust Fallback Policy](../../ADR-041-Rust-Fallback-Policy/ADR-041-rust-fallback-policy.zh.md) —— 让 BFR-009 显形（Rust-Required）
- [ADR-023 — Proxy Recommendation Policy](../../ADR-023-Proxy-Recommendation-Policy/ADR-023-proxy-recommendation-policy.zh.md) —— DO 中的代理健康信号（CF/成功/失败事件）
- 协调器仓：[`TongWu/JAVDB_AutoSpider_Proxycoordinator`](https://github.com/TongWu/JAVDB_AutoSpider_Proxycoordinator) —— `src/proxy_coordinator.ts`、`src/types.ts`
- `docs/handbook/zh/self-hoster/proxy-coordinator.md` —— 面向运维的 env 参考

## 状态日志 (Status Log)

- 2026-05-31: Proposed
- 2026-06-01: Phase 2 已实现；BFR-009 已关闭。
- 2026-06-01: 已完成 (Completed) —— 两个 IMP 均已合并；归档至 `_archive/`。
