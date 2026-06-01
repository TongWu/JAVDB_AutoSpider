# ADR-045：FetchEngine 可复用公开 API 加固

**状态：** Proposed
**日期：** 2026-06-01
**作者：** Ted
**关联实施计划：** [IMP-ADR045-01](IMP-ADR045-01-fetch-engine-public-api.md)（Phase 1 —— `drain_remaining()` + 薄 `run()`，迁移三个 migration 工具）

## 背景

### 导火索 —— 一个被 ban 的代理失败后，从未被其他代理重试

`Database Migration` 运行 [#26718484218](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/26718484218/job/78741197690)
以 `--backfill-metadata` 处理 10 个 href（开代理、shuffle）。8 个成功，2 个失败：

- `[meta-3/10]` `7yKYW1` → `fetch_failed: empty response`
- `[meta-7/10]` `B0nn6` → `fetch_failed: Proxy 'Hyderabad-ARM2' banned: CF bypass failed 9 consecutive times`

关键在于：`meta-8/9/10` 随后**用其他代理成功了**（Sydney-ARM2、Singapore-ARM1/2）
—— 代理池**并未**耗尽。但那两个失败项从未被放回去、让这些仍健康的代理消费。

根因是结构性的，不是代理 bug：
`javdb/migrations/tools/backfill_movie_metadata.py` 是一个**单线程、单遍历的循环**
（`run_backfill_metadata` 里的 `for i, href in enumerate(...)` 循环体）。任何失败
都只做 `failed += 1` 然后走向下一个 href —— **没有重新入队**。在一次 `_process_href`
调用内部，底层 `RequestHandler` 确实会轮换约 6 个代理（`min(pool-1, 5)` 的切换预算），
但这个内部预算一旦用尽，该 href 就被放弃。本可以把 `meta-7` 交给 Sydney/Singapore 的
`FetchEngine` 重入队机制（`requeue_front`、`EngineTask.failed_proxies`、
`all_proxies_banned`）**根本没被触及**，因为这个工具压根没用 `FetchEngine`。

### FetchEngine **本就**是可复用的公开 API —— backfill 只是绕过了它

`javdb/spider/fetch/fetch_engine.py` 开篇即写：

> Provides a single `FetchEngine` class that **external scripts (spider main,
> migration backfill, inventory alignment, …) can use** to process arbitrary
> detail-page URLs with the full spider infrastructure…

三个 migration 工具里已有两个在用它：

- `migrate_v7_to_v8.py:482`（`--backfill-actors`）—— `FetchEngine.simple(...)` + `for r in engine.results()`。
- `align_inventory_with_moviehistory.py:699`（`--align-inventory`）—— 高级模式 `FetchEngine(process_fn=...)` + `ctx.fetch()`。

因此 `backfill_movie_metadata.py:11-12` 里那句注释 ——
*"`FetchEngine` exposes no public result-draining API to reuse here"* ——
是**过时且错误的**：`ParallelFetchBackend.results()`（`fetch_engine.py:1674`）
正是这样一个公开的、用于排空结果的迭代器，而隔壁的兄弟工具就在用它。

### 现有 API 的两处真实毛病

真正需要的是**加固**，而非另起炉灶：

1. **中断路径上的私有状态泄漏。** `migrate_v7_to_v8.py` 与
   `align_inventory_with_moviehistory.py` 都在各自的 `except KeyboardInterrupt`
   块里伸手访问引擎的**私有** `engine._result_queue.get_nowait()`，以便在进程死亡前
   抢救"已抓取但未写库"的结果。`shutdown()`（`fetch_engine.py:1696`）会把 *task* 与
   *login* 队列排空进 `orphaned`，**但不碰** *result* 队列 —— 于是没有任何公开途径
   去回收那些"已产出但未消费"的结果。

2. **每个调用方重复的生命周期样板。** 每个调用方都手写同一套
   `start() → submit*/submit_task* → mark_done() → for r in results() →
   shutdown()`。

### 与 ADR-043 的关系

`meta-7` 失败是因为某个代理持续过不了 Cloudflare 墙。
[ADR-043](../_archive/ADR-043-CF-Auto-Ban/ADR-043-cf-persistent-failure-auto-ban.zh.md)
降低对这类代理的*再次使用*（跨 runner 的 CF 自动 ban）。本 ADR 与之互补且正交：
它确保**当一次抓取因任何原因失败时，工作项会被重新入队给另一个代理**，而不是被静默丢弃。
ADR-043 让坏代理更少；ADR-045 让失败的工作可被恢复。

## 决策

为 `FetchEngine` 的公开面加两处小增量并加固，然后把三个 migration 工具全部接入它。
**不做**结构性重写（不做 `PROXY_POOL` 依赖注入、不合并 `FetchEngine`/`ParallelFetchBackend`
facade、不重命名）—— 这些已被明确考虑并推迟。

### 设计决策

**D1. 新增公开 `drain_remaining() -> Iterator[EngineResult]`。** 对结果队列的一次
非阻塞排空，约定在 `shutdown()` **之后**调用（worker 已停 ⇒ 不会有新结果竞态进来）。
它精确地 yield 出 worker 停止前已产出的结果 —— 逐字替换私有的
`_result_queue.get_nowait()` 循环。它**不碰** task/login 队列；从未运行的任务仍是
`orphaned`（已由 `shutdown()` 返回）。落在 `ParallelFetchBackend` 上，并经
`FetchEngine` facade 暴露。

**D2. 新增薄 `run(tasks) -> Iterator[EngineResult]` —— 仅 happy path。** 一个
拥有生命周期的生成器：`start()`、对每个任务 `submit_task()`、`mark_done()`、
`yield from results()`，并在 `finally` 里 `shutdown()`。它**不是**中断抢救路径：
当 `KeyboardInterrupt` 落在调用方的循环体内时，Python 会把 `GeneratorExit` 抛进
`run()`，它无法再 yield 缓冲的结果（在 `GeneratorExit` 期间 yield 会触发
`RuntimeError`）。因此：

- **三个 migration 工具保留显式生命周期 + `drain_remaining()`** 来做稳健抢救
  （中断在调用方捕获，无论它落在哪里）：

  ```python
  engine.start()
  for t in tasks: engine.submit_task(t)
  engine.mark_done()
  try:
      for r in engine.results(): apply(r)
  except KeyboardInterrupt:
      engine.shutdown()
      for r in engine.drain_remaining(): apply(r)   # 公开；原为 _result_queue.get_nowait()
  else:
      engine.shutdown()
  ```

- **`run()` 服务于**单元测试、未来不需要抢救的简单调用方，以及作为有文档的范式 happy
  path。它约 8 行；这点小小的公开面成本是可接受的，因为它锚定了预期的用法范式。

**D3. 把 `backfill_movie_metadata` 迁移到 `FetchEngine.simple`，自动登录。**
用 `FetchEngine.simple(parse_fn=..., use_cookie=True)` 替换串行循环。登录墙现在会在
内部抛 `LoginRequired` 并路由到 `LoginCoordinator` 做一次自动登录（`FetchEngine.simple`
的默认行为，与 `migrate_v7_to_v8` 一致）。后果：独立的 `login_required` 计数被丢弃 ——
能被清除的登录墙变成 `ok`，不能清除的变成普通失败，且**下次运行可重试**，与今天完全一致
（`_load_hrefs_without_metadata` 会重新选出任何仍缺 metadata 的 href）。净行为严格更强：
登录受限的电影会被回填，而不是被跳过。

**D4. DB 写入留在主 results 循环里，绝不放在 worker。** `parse_fn` 返回解析好的
`detail`（并行、在 worker 线程里）；`MetadataRepo().upsert(href, detail)` 在单线程的
results 循环里执行。这与 `migrate_v7_to_v8` 的 `_apply_backfill_result`、`align` 的
`_apply_align_result` 一致，把 D1/SQLite 写入挡在 worker 线程之外，并让 `write_failed`
在循环里被识别。"当解析出 `video_code` **或** `title` 时即接受该页"的规则
（`backfill_movie_metadata.py:200`）移入 `parse_fn`（对真正空白的页返回 `None`，让引擎
把它重新入队给另一个代理）。

**D5. 保留 backfill 的 D1-aware 写路径。** 保留 `get_db(HISTORY_DB_PATH)` /
`MetadataRepo`（按 `STORAGE_BACKEND` 自动路由）。**不要**照搬 `migrate_v7_to_v8` 的
`sqlite3.connect(...)` + `if not use_sqlite(): return 1` 守卫 —— 那个工具是遗留的
SQLite-only，而 metadata backfill 在 CI 里**已经跑在 D1 上**
（`backfill_movie_metadata.py:73-75`）。D1 是规范的事实来源（见 `CLAUDE.md`）；迁移后的
工具绝不能退回 SQLite-only。

**D6. `--limit-per-worker` 映射到引擎真正的 `per_worker_task_limit`。** 今天 backfill
因为是串行，手动预截断 `hrefs[:limit_per_worker * num_workers]`
（`backfill_movie_metadata.py:249-252`）。接入引擎后，传
`ParallelFetchBackend(per_worker_task_limit=limit_per_worker)` 并提交完整（可经
`--limit` 上限的）列表。引擎按每个 worker 的**成功数**计数 —— 比截断提交更准确，且与
`align` 接同一输入的方式完全一致（`align_inventory_with_moviehistory.py:705`）。
`--limit` 仍是提交前的绝对上限（`hrefs[:limit]`）；`--shuffle` 不变。

**D7. 改动半径 —— 仅 migration 工具。** `runner.py`（spider detail）用
`cancel_event` + `SystemExit(124)`，从不碰 `_result_queue`；`index_parallel.py`
用滑动提交**窗口**（submit N → 消费 → 再 submit），不符合 `run()` 的有限列表模型。
两者生命周期需求不同，且**没有**私有状态泄漏，故保持不动（外科手术式改动原则）。

**D8. 修正过时注释与过时的 `scripts.*` 路径引用。** 更正
`backfill_movie_metadata.py:11-12` 的说法，并更新 `fetch_engine.py` 里仍引用
`scripts.spider.fetch.*` 的 docstring / `__all__` ——
那是 [ADR-007](../_archive/ADR-007-Monorepo-Restructure/ADR-007-monorepo-restructure-2026-05.zh.md)
Phase 3 已退役的路径（规范是 `javdb.spider.fetch.*`）。

## 后果

### 正面

- **被观察到的这类 bug 消失。** backfill 中一次失败的抓取（代理被 ban、空响应、CF 墙）
  会经引擎的 `failed_proxies` / `requeue_front` 机制重新入队给另一个代理。`meta-7`
  那一例本会在 Sydney/Singapore 上被重试，而不是被丢弃。
- backfill 免费获得完整的 spider 基础设施：每代理并行 worker、CF-bypass 级联、自适应
  sleep/throttle、跨 runner 代理协调，以及自动登录。
- **所有调用方对私有 `_result_queue` 的访问被消除** —— 封装恢复；引擎可以改内部队列而
  不破坏 migration 工具。
- 每个 migration / 补抓工具有了唯一的规范抓取路径；那条误导性的"no public API"注释也没了。
- 仅引擎内部 —— **不属于**
  [ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.zh.md)
  双后端重叠面（无 D1 查询 / 鉴权 / API 形状变更），故 web 仓库的 TS 后端无需同步。

### 负面

- **backfill 行为变化（D3）：** 登录墙现在会触发自动登录（消耗共享 login budget），且独立的
  `login_required` 计数被丢弃。可接受：实际被回填的电影更多；不可恢复的登录仍可下次运行重试
  （与今天的最终状态一致）。
- backfill 失去严格的串行 / 确定性顺序 —— 现在并行、乱序执行。对于幂等 `upsert` 的补抓
  任务可以接受。
- `run()`（D2）是个三个被迁移工具**不会**用的薄便捷（它们需要显式抢救路径）。新增公开面很小，
  由测试 + 未来调用方 + 范式文档来证成其价值。
- 推迟的项（PROXY_POOL 注入、facade/backend 合并、重命名）保留了一些既有的 API 别扭之处；
  仅当未来某个调用方需要与全局 `PROXY_POOL` 解耦时再重新审视。

## 实施路线图

| 阶段 | IMP | 交付 | 推迟 |
| --- | --- | --- | --- |
| Phase 1 | [IMP-ADR045-01](IMP-ADR045-01-fetch-engine-public-api.md) | 在 `ParallelFetchBackend`（+ `FetchEngine` facade）上加 `drain_remaining()` + 薄 `run()` 并配单元测试；把 `backfill_movie_metadata` 迁移到 `FetchEngine.simple`（D3–D6）；把 `migrate_v7_to_v8` + `align` 的中断处理切到 `drain_remaining()`（D1）；修正过时注释 + `scripts.*` 引用（D8）；CLI 参考手册补注（en/zh） | `PROXY_POOL` 依赖注入；`FetchEngine`/`ParallelFetchBackend` 合并；公开方法重命名（"大重构"选项，未采纳） |

## 参考

- 导火索运行：[`Database Migration #26718484218`](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/26718484218/job/78741197690)
- `javdb/spider/fetch/fetch_engine.py` —— `ParallelFetchBackend` / `FetchEngine` / `WorkerContext` / `results()` / `shutdown()`
- `javdb/migrations/tools/backfill_movie_metadata.py` —— 被迁移的串行工具（D3–D6）
- `javdb/migrations/tools/migrate_v7_to_v8.py`、`javdb/migrations/tools/align_inventory_with_moviehistory.py` —— 已有的 `FetchEngine` 调用方，含私有队列泄漏（D1）
- [ADR-043 — CF Persistent-Failure Auto-Ban](../_archive/ADR-043-CF-Auto-Ban/ADR-043-cf-persistent-failure-auto-ban.zh.md) —— 互补：减少坏代理的*再使用*；本 ADR 让失败的*工作*可恢复
- [ADR-007 — Monorepo Restructure](../_archive/ADR-007-Monorepo-Restructure/ADR-007-monorepo-restructure-2026-05.zh.md) —— 退役了过时 docstring 仍引用的 `scripts.*` 路径（D8）

## 状态日志

- 2026-06-01：Proposed
