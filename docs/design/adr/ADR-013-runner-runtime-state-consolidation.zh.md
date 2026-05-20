# ADR-013：Runner Runtime 全局状态收束

**状态**：已接受 - 实现待启动
**日期**：2026-05-20
**决策者**：Runner runtime state brainstorming + grill 会话
**关联实现计划 (Related Implementation Plans)**：[IMP-023](../impl/IMP-023-runner-runtime-phase1-skeleton-facade.md)（Phase 1 - runtime skeleton and active facade）、[IMP-024](../impl/IMP-024-runner-runtime-phase2-registry-movieclaim.md)（Phase 2 - registry and MovieClaim lifecycle）、[IMP-025](../impl/IMP-025-runner-runtime-phase3-explicit-callers.md)（Phase 3 - explicit production callers）、[IMP-026](../impl/IMP-026-runner-runtime-phase4-legacy-facade-removal.md)（Phase 4 - legacy facade freeze/removal）

## 待办 (Outstanding Work)

- Phase 1 - 引入 `SpiderRuntime`、小型 runtime state 对象、runtime service slots，以及 active-runtime facade binding，同时保留旧 `state.*` caller。
- Phase 2 - 将 RunnerRegistry heartbeat/session lifecycle 和 MovieClaim auto lifecycle 迁到 runtime-owned state/services。
- Phase 3 - 将 sleep、proxy、request、login、fetch、detail、report 的生产调用方逐步迁到显式 runtime/context。
- Phase 4 - freeze 或删除 legacy direct-mutation `state.py` compatibility，并记录最终显式 runtime 使用规则。

---

## 背景

`javdb.spider.runtime.state` 当前是 spider runner 的 mutable global state
container。它拥有或协调：

- `parsed_links` 等 detail-run 状态；
- proxy pool、CF bypass sticky markers、proxy-ban HTML captures 和 coordinator hooks；
- refreshed login cookie、login attempt counters 和 login budget；
- `runtime_holder_id`、RunnerRegistry heartbeat thread、session payload、
  unregister state、config snapshots 和 active signals；
- MovieClaim auto mount/unmount 状态与 orphan-stage sweep 状态；
- request handler lifecycle；
- 直接 import `state.<name>` 的调用方兼容入口。

`runtime.sleep` 也暴露 module-level singleton state（`penalty_tracker`、
`triple_window_throttle`、`movie_sleep_mgr`），这些同样属于 runner runtime
lifecycle。

当每次 Spider run 都发生在新进程中时，这个形态可以工作。但随着 in-process
Spider runner 的方向推进，同一个 Python 进程必须能够顺序创建多个 Spider
runtime，且不能复用 run-scoped state。

## 不可协商运行时不变量

本 ADR 是行为保持型结构迁移。

迁移不得改变生产环境中的 proxy、login、MovieClaim、RunnerRegistry、
WorkDistributor、sleep/throttle、GitHub Actions logging、stdout footer、session
heartbeat、D1 fail-closed 或 failure downgrade 语义。

## 决策

### D1. 引入 `SpiderRuntime`

`SpiderRunService` 成为 Spider runtime state 的 composition root。

每次 run 开始时创建 `SpiderRuntime`，将其绑定为 legacy callers 的 active
runtime，执行现有 Spider flow，然后关闭并解绑 runtime。`SpiderRuntime.close()`
必须显式且幂等。

### D2. 使用小型 Runtime State 对象

`SpiderRuntime` 拥有一组 focused state objects：

- `DetailRunState`
- `ProxyRunState`
- `LoginRunState`
- `RunnerRegistryState`
- `MovieClaimRuntimeState`
- `SleepRuntimeState`

这些对象不是一个新的大号 context bag。聚合对象只负责让 run creation、close 和
compatibility binding 有一个 owner。

### D3. Resource Services 与 Plain State 分离

资源对象归 runtime-owned services，不塞进 plain state dataclasses：

- proxy pool；
- request handler；
- proxy coordinator client；
- login-state client；
- MovieClaim client；
- runner-registry client；
- recommend proxy policy；
- work distributor client。

拥有它们的 runtime 负责 setup、shutdown 和 close 语义。

### D4. 暂时保留 `state.py` 作为 Active-Runtime Facade

迁移期间继续保留 `javdb.spider.runtime.state`。

现有读写方式，例如 `state.parsed_links.clear()`、`state.global_proxy_pool`、
`state.runtime_holder_id`，在 final phase 明确 freeze 或删除前都必须继续可用。

facade 不得制造两套 source of truth。一个已迁移字段要么指向 active runtime 的对象，
要么在生产写方迁移前继续保持为 unmigrated module field。

### D5. Phase 1 不依赖 Module Assignment Magic

Phase 1 不依赖拦截任意 `state.foo = value` module assignment。

可变对象字段可以在 `bind_active_runtime()` 时 rebind 到 active runtime。仍有直接赋值
生产写方的 scalar 字段，会先保留在 module field，直到写方迁移，或者后续 phase 明确
引入显式 proxy 机制。

### D6. Runner Identity 归 Runtime 所有

`runtime_holder_id` 迁为 `RunnerRegistryState.holder_id`，在创建
`SpiderRuntime` 时生成。现有 CLI/GitHub Actions 行为不变，因为当前生产环境是一进程
一次 Spider invocation。

允许 Pipeline/API caller 传入 holder id 属于后续 ADR。

### D7. 保证顺序 Runtime 隔离，不承诺并发

本 ADR 保证同一 Python 进程可以顺序创建多个 `SpiderRuntime`，且不会复用
run-scoped state。

本 ADR 不支持同进程并发 Spider runtimes。并发 runtime 需要移除 active-runtime
facade 依赖，或使用 concurrency-aware binding strategy，属于后续 ADR。

### D8. Runtime 拥有 Heartbeat，`atexit` 只是兜底

`RunnerRegistryState` 拥有 heartbeat thread、stop event、unregister flag、
session payload、heartbeat cadence、last applied config version 和 active signal
bookkeeping。

`SpiderRuntime.close()` 显式停止 heartbeat 并 unregister。`atexit` 仍作为异常退出的
best-effort safety net 保留，但不再是正常生命周期主路径。

### D9. MovieClaim 单独建模

MovieClaim auto state 建模为 `MovieClaimRuntimeState`，即使 RunnerRegistry
heartbeat response 会驱动它的 recommendation updates。

这样可以保持 MovieClaim state machine 与 registry transport 分离，避免 registry
state 变成新的全局桶。

### D10. Sleep State 归 Runtime 所有

`penalty_tracker`、`triple_window_throttle`、`dual_window_throttle` 和
`movie_sleep_mgr` 都是 runner runtime state。

迁移期间 `runtime.sleep` 可以继续保留 compatibility names，但生产调用方必须最终收敛到
runtime-owned sleep state。

### D11. 保持运行时行为不变量

每个 phase 后，下列行为都必须保持不变：

- GitHub Actions live logs 和 stdout footer markers。
- `SPIDER_OUTPUT_CSV`、`SPIDER_DEDUP_CSV`、`SPIDER_SESSION_ID` 和
  `SPIDER_STAT_*`。
- Frontend/API task log streaming。
- Proxy pool setup、proxy coordinator setup、recommend proxy policy setup 和
  fail-open behavior。
- CF bypass sticky behavior 和 remote CF bypass mirroring。
- Login budget、login-state coordinator 和 parallel login routing。
- RunnerRegistry register、heartbeat、re-register、unregister、pause handling、
  config snapshot application 和 active signal reconciliation。
- MovieClaim auto mount/unmount、D1 fail-closed behavior、failure cooldown 和
  orphan-stage sweep。
- WorkDistributor enablement、enqueue/pull/complete/release 和 fail-open behavior。
- `runtime.sleep` throttling、volume multiplier、global throttle signal、
  pause-all signal 和 degraded-mode runner scaling。

### D12. 一个 ADR，四个 Phase Plan

本 ADR 通过四个独立实现计划落地：

- [IMP-023](../impl/IMP-023-runner-runtime-phase1-skeleton-facade.md)
- [IMP-024](../impl/IMP-024-runner-runtime-phase2-registry-movieclaim.md)
- [IMP-025](../impl/IMP-025-runner-runtime-phase3-explicit-callers.md)
- [IMP-026](../impl/IMP-026-runner-runtime-phase4-legacy-facade-removal.md)

每个 phase 都有自己的 test gate，并且可以单独 bake。

## 后续 ADR 范围

以下工作明确留给下一个 ADR：

- API task runner 与 FastAPI runtime global consolidation。
- Pipeline/API integrations 的 caller-supplied runner holder id。
- 同进程并发 Spider runtime 支持。
- 所有 Spider internal modules 的完整 dependency injection。
- 更宽的 workflow/API runtime lifecycle management。

## 影响

### 正向

- Spider runtime state 有明确 owner。
- 顺序 in-process Spider runs 可以隔离。
- 后续 Pipeline in-process Spider work 有明确 runtime boundary。
- 测试可以创建并关闭 runtime instance，而不依赖 import-time module globals。
- 迁移会收敛到显式 context/services，而不是永久 compatibility wrappers。

### 负向

- 生产代码和测试当前大量直接依赖 `state.*`，所以迁移需要多个 phase。
- compatibility facade 会带来短期复杂度。
- scalar direct assignment 不能安全迁移，直到调用方迁走或明确引入 module proxy 策略。

### 中性

- 本 ADR 不减少 runtime 行为数量。它先改变 ownership 和 lifecycle boundary。
