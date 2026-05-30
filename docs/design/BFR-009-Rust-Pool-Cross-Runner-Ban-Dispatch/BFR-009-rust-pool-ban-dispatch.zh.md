# BFR-009: 跨 runner 封禁分发在 Rust 池/封禁管理器路径上不触发

**状态**: Open
**日期**: 2026-05-30
**严重度**: Medium
**影响范围**: `javdb/proxy/ban_manager.py`(`_dispatch_remote_ban`、`set_remote_ban_hook`)、`javdb/proxy/pool.py`(Python `ProxyPool.ban_proxy` / drain)、`javdb/spider/runtime/state.py:722`、`javdb/spider/runtime/context.py:880`(hook 注册)、`javdb/rust_core/src/proxy/{pool,ban_manager}.rs`
**关联**: [ADR-041](../ADR-041-Rust-Fallback-Policy/ADR-041-rust-fallback-policy.zh.md)(在其 Task 4 中暴露——删掉了 dispatcher 的最后几个 Python 调用方)、[ADR-023](../ADR-023-Proxy-Recommendation-Policy/ADR-023-proxy-recommendation-policy.zh.md)(代理协调)、CONTEXT.md →「Signal」(`ban_proxy`)

---

## 症状

生产中跨 runner 封禁传播不发生。当一个 runner 封禁某代理时,其它 runner **不会**经协调器(Worker Durable Object)被告知;它们会继续选用该被封代理,直到各自独立撞上失败为止。

接线是存在的,但在生产路径上从不触发:

- 远程封禁 hook **已在运行时注册** —— `set_remote_ban_hook(client.mark_proxy_banned)`(`javdb/spider/runtime/state.py:722`、`javdb/spider/runtime/context.py:880`)。
- 该 hook **只被** `_dispatch_remote_ban(...)` 调用,而后者的**调用点全部在 Python 代码里**:Python `ProxyPool`(`javdb/proxy/pool.py:411`、`:485`)与 Python `ProxyBanManager.add_ban`(`javdb/proxy/ban_manager.py:162`)。
- **生产使用 Rust 池 + Rust 封禁管理器**(装了 wheel 时 `create_proxy_pool_from_config` / `get_ban_manager()` 返回 Rust 实现,而 Docker/CI 总是装了)。Rust 的 `ban_proxy` / `add_ban` 在 PyO3 扩展内部,**无法触达** Python 的 `_dispatch_remote_ban` hook。

净效果:每次生产封禁都**只在本地记录**;`client.mark_proxy_banned(name)` → Worker DO 的广播从不触发。

## 根因

跨 runner 封禁分发(P1-A)被实现为一个**Python 模块级 hook**,从 Python 池/封禁管理器内部触发。当 Rust 池/封禁管理器成为生产默认后,分发调用点**未**移植进 Rust 扩展,也没有加 Rust→Python 的封禁回调。Rust 扩展没有机制(没有 `set_ban_callback` 之类的 setter)在记录封禁时通知 Python。

这是一个**既有的**潜伏缺口——它早于 [ADR-041](../ADR-041-Rust-Fallback-Policy/ADR-041-rust-fallback-policy.zh.md)。ADR-041 只是让它**显形**:移除 Python `ProxyPool` / `ProxyBanManager`(改为 Rust-Required)删掉了 `_dispatch_remote_ban` 的最后三个调用方,使 dispatcher 和已注册的 hook 从"只在生产里死"变成"明确地死"。

`ban_manager.py` 中 `remove_ban` 的 docstring 其实已半承认这种不对称:它指出 Rust manager "从扩展内无法触达 Python 的 `_dispatch_remote_unban` hook",并把 unban 分发交给 `ProxyPool.unban_proxy` —— 但 Rust 池的 `ban_proxy` 有同样的限制,且没有等价的分发。

## 影响

- **多 runner 运行失去跨 runner 封禁协调。** 一个 runner 判定为坏(CF 挑战、被封)的代理不会被广播;其它 runner 会在它上面浪费请求,直到各自独立封禁。这是效率/协调退化,不是数据损坏。
- **单 runner 运行不受影响** —— 本地封禁经 Rust 封禁管理器正常工作。
- 协调器的 `mark_proxy_banned` 端点与 `ban_proxy` Signal(CONTEXT.md)在 Python 侧实际处于"供给不足"。

## 修复

尚未实现 —— 在此跟踪,从 ADR-041 中延后(ADR-041 是 fallback 策略变更,不是协调变更)。候选方案(在 follow-up 中决定):

1. **Rust→Python 封禁回调。** 给 Rust 池/封禁管理器加 `set_ban_dispatch(callback)`,在每次*新记录*的封禁时调用(对应 Python 的 `newly_banned` 去重),由注册 `set_remote_ban_hook` 的同一运行时 setup 接线。
2. **在 Python 调用点分发。** 把生产封禁入口(`get_ban_manager().add_ban(...)`、`pool.ban_proxy(...)`)包一层薄 Python helper,既经 Rust 记录又触发 `_dispatch_remote_ban` —— dispatcher 留在 Python 侧,无需改 Rust。
3. **观察者/增量轮询。** 让协调器集成层把 Rust 封禁管理器的封禁集合与上次已分发集合做 diff 并推送增量。

方案 2 改动最小,且让 `_dispatch_remote_ban` / `set_remote_ban_hook` 仍有意义;方案 1 长期最干净但要动 Rust crate。

## Follow-Up

- [ ] 决定分发方案(1/2/3)并实现。
- [ ] 加一个测试:经**生产**入口(`get_ban_manager().add_ban` / `create_proxy_pool_from_config(...).ban_proxy`)记录的封禁,对每个新封禁代理恰好触发一次已注册的远程 hook。
- [ ] 分发路径落地后,重新评估 `_dispatch_remote_ban` / `set_remote_ban_hook` / `set_remote_unban_hook` 这组接口(ADR-041 保留了这些符号,但其唯一调用方是已移除的 Python 池/管理器)。
