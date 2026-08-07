# BFR-025：全站挑战经由 coordinator 把整个代理池 ban 光

**Status**: Fixed
**Date**: 2026-08-07
**Severity**: Critical
**Affected**: `javdb/infra/request.py`, `javdb/spider/fetch/fetch_engine.py`, `javdb/spider/runtime/context.py`
**Related**: [BFR-024](../BFR-024-CF-Managed-Challenge-Blind-Spot/BFR-024-cf-managed-challenge-blind-spot.zh.md), [ADR-043](../_archive/ADR-043-CF-Auto-Ban/ADR-043-cf-persistent-failure-auto-ban.zh.md), run [31163278517](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/31163278517), probe run [31173702032](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/31173702032)

---

## Symptom

Daily Ingestion 的 run [31163278517](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/31163278517)
（2026-08-07T08:49Z）跑在 `1775d7d` 上——BFR-024 的修复已合入，`CF_BYPASS_VIA_PROXY=True`
——仍然失败：

```text
17:01:59    FetchEngine   [startup] Proxy 'Hyderabad-ARM1' already banned — skipping worker
   ... (28 lines, every proxy in PROXY_POOL) ...
RuntimeError: FetchEngine: all proxies are banned, cannot start
Spider exited with code 1
```

索引阶段是*成功*的——`Fetched and parsed 10 pages (parallel)`，发现 29 条 phase-2
条目。run 死在 Phase 2 引擎启动的那一刻。

该 run 内的事件计数：

| 日志事件 | 次数 |
| --- | --- |
| `returned Cloudflare challenge page` | 325 |
| `Site-wide Cloudflare challenge — re-queued without counting toward soft-ban` | 188 |
| `Soft-banned after 2 consecutive None returns` | **2** |

也就是说 28 个 ban 里有 26 个没有本地来源。14 分钟前一次 `ProxyUnban` workflow
（[31162302129](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/31162302129)）
报告了 `done: 28/28 succeeded`，而这次 run 自己的引擎也是干净启动的：28 个 worker，
零条 `[startup] ... already banned`。代理池是在这次 run 内部、从零开始、在 11 分钟里
被毒死的。

## Root Cause

两个问题，都源于 BFR-024 的修复不完整。

**1. 远端 ban 这一层从来没有被豁免。** BFR-024 只阻止了全站挑战产生*本地*软 ban，
但 `request.py` 的 `_get_page_direct` 在挑战分支上仍然调用
`self._record_cf_event(proxy_name)`。`git show 1775d7d` 可以看到
`self.last_site_challenge = True` 是作为 `+` 行直接加在一条*未改动*的
`self._record_cf_event(proxy_name)` 上面的：随附的注释只推理了 `penalty_tracker`，
但 `_record_cf_event` 会触发两个下游——本地节流 tracker **以及** `_on_cf_event`，
而后者被 `fetch_engine.py` 接到了 `coordinator.report_async(proxy_id, "cf")`。

按 ADR-043 D2，Worker 侧的 Durable Object 在
`cfEvents.length >= CF_AUTO_BAN_THRESHOLD`（默认 6）**且**
`successEvents.length === 0` 时 ban 掉一个代理，`CF_BAN_TTL_MS` 默认 6 小时（D3）。
在全站被墙的情况下，这两个条件对每个代理同时成立：

- 325 次挑战摊到 28 个代理上是每个约 11.6 次，远超阈值。
- `_fetch_direct` 在挑战分支上*先于*成功上报就返回了，所以 `successEvents` 始终为空。

每个 DO 各自独立 ban；下一次 `POST /lease` 返回 `banned: true`；
`javdb/spider/runtime/sleep.py` 的 `_mirror_remote_ban_locally` 把它写进进程全局的
Rust ban manager，而且**成功时不打任何日志**。Rust 的 ban manager 明确写着其 ban
"在进程生命周期内是永久的"，所以远端那个 6 小时 TTL 在本地毫无意义——而 Phase 1
与 Phase 2 共用同一个进程。

ADR-043 的 **D7 恰恰把这个场景列为 out of scope**："When *every* proxy fails CF …
banning individual proxies is the wrong remedy. This ADR deliberately does
**not** add cross-DO / global circuit-breaking."（当*每个*代理都过不了 CF 时，ban
单个代理是错的补救；本 ADR 有意不引入跨 DO / 全局熔断。）其风险清单里也接受了
"A genuine site-wide CF outage auto-bans the whole pool for up to 6 h"。BFR-025
就是这个被接受的风险真的发生了。

**2. bypass 这一层要等每页约 27 次直连失败之后才够得着。** 在 `_simple_process`
——并行索引 / 详情路径实际走的级联（`ParallelFetchBackend.simple`）——里，
CF-bypass 那一支被挡在这段判断后面：

```python
if ctx.queue_pressure == 'low' and not task.login_only:
    active = ctx._worker._active_workers
    if len(task.failed_proxies) < max(1, active - 1):
        return None
```

`_queue_pressure` 在 `qsize <= 1 and active > 2` 时返回 `'low'`。10 个索引任务配 28
个 worker，队列基本永远是空的，于是每一页都必须在几乎所有代理上把直连失败一遍，
才被允许尝试一次 bypass。325 次挑战就是这么来的（每页约 32 次），而其中每一次都是
一个喂给 Fault 1 的 CF 事件。

sticky-bypass 这条逃生通道也帮不上忙：`_should_shortcircuit_cf` 同时要求
`always_bypass_time`（`--always-bypass-time` CLI 开关，默认不设）**和**
`_cf_bypass_since`，而后者只有在一次 bypass 成功*之后*才由 `_mark_cf_bypass()`
设置——一个先有鸡还是先有蛋的门。

设计缺陷在于：这个级联把一套固定的成本模型——直连便宜、bypass 昂贵，所以先试直连、
不情愿地才回退——当成了常量写死。而在全站挑战下这个模型是反过来的：直连对每个代理
都必然失败，bypass 才是唯一能给出答案的那一层。

## Evidence

一次诊断扫描（`apps/cli/ops/cf_bypass_probe.py`，workflow run
[31173702032](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/31173702032)）
按 `CF_BYPASS_VIA_PROXY` 的拓扑探测了全部 28 个代理。每个代理的结果完全一致：

| Probe | Result |
| --- | --- |
| `DIRECT javdb.com` | 403, 5890 bytes, `<title>Just a moment...</title>` |
| `GET :8000/` | 404, 43 bytes, `{"status": "error", "message": "not found"}` |
| `GET :8000/html?url=` | **200, 61070 bytes, contains `movie-list`** |

也就是说，javdb 对 28/28 个出口 IP 无一例外地下发 managed challenge，而 bypass 服务
对其中每一个都返回了真实页面。这次 run 是在 bypass 这一层完全可用的前提下失败的
——它只是在架构上被挡住、用不到那一层。

## Fix

- **`request.py`** —— `_record_cf_event` 新增 keyword-only 参数
  `site_wide: bool = False`。为真时本地 `penalty_tracker` 照常触发（无论哪种情况节流
  都是对的），但 per-proxy 的 coordinator 上报被换成新的 `on_site_challenge()`
  回调。六个由挑战触发的调用点——CF-bypass 回退级联里的五个，加上 `_get_page_direct`
  里的那一个——全部传 `site_wide=True`。第七个调用点，即在挑战早退*之后*才会跑到的
  级联耗尽上报，有意保持不变：能走到那里就说明这次失败不是挑战。
- **`request.py`** —— `RequestHandler.__init__` 新增 `on_site_challenge`，文档上定位
  为 per-proxy 的 `on_cf_event` 的 run 级对应物。
- **`context.py`** —— `ProxyRunState.site_challenge_active: bool`，是已锁存的
  `site_challenge_seen` 的实时对应物。
- **`context.py`** —— sequential 路径的共享 handler
  （`SpiderRuntime._init_request_handler`）接上 `_global_site_challenge_cb`，它设置
  两个标志且不做任何 coordinator 上报。这条路径没有模式切换可驱动，但少了这个锁存，
  一次被全站墙掉的 `--sequential` 运行会以 exit 0 加一个只有表头的 CSV 收场，而不是
  失败。
- **`fetch_engine.py`** —— `_site_challenge_cb` 无条件接线（不再以是否配置了
  coordinator 为前提），同时设置两个标志，并在状态切换时打一次日志。
- **`fetch_engine.py`** —— 新增 `_EngineWorker._should_prefer_bypass()`（sticky 窗口
  或实时全站挑战），在两个级联入口处取代 `_should_shortcircuit_cf()`；新增
  `_mark_site_recovered()`，在直连成功时清除实时标志。
- **`fetch_engine.py`** —— bypass 优先时，直连那一支仍作为回退执行，因此它同时充当
  恢复探针。两条级联（`WorkerContext.fetch` 与 `_simple_process`）对称地反转。

- **`fetch_engine.py`** —— 恢复探测的判断被提到 `--always-bypass-time` 粘滞短路**之前**。
  粘滞窗口是无条件的（取值 `0` 时永久生效），先判断它就意味着 `_bypass_first_streak`
  永远不会累加、周期性的 direct 重试永远不会执行：一旦进入粘滞模式，即使 javdb 已经撤下
  验证墙，整个 run 也会一直停留在较慢的那一层。
- **`fetch_engine.py`** —— `site_challenge_active` 为真时，低队列压力短路暂停生效。
  在验证墙下走到这里，说明恢复探测的 direct 这一腿刚刚失败；把任务改派给另一个 proxy
  只会再换来一次失败的 direct 尝试，因为这堵墙与具体 proxy 无关。
- **`fetch_engine.py` / `context.py`** —— `SITE_CHALLENGE_REQUEUE_LIMIT = 8`
  为全站验证页的重新入队循环设上限。该计数保存在 `ProxyRunState` 上（所有 worker 共享，
  由 `site_challenge_lock` 保护），任何一次成功抓取都会清零；连续第 8 台 proxy 返回空
  内容时，任务以 `site_challenge_exhausted` 结束，而不再扫完剩下的池子。此前一个 ad-hoc
  页面曾因此耗掉 80 分钟，在 28 台面对同一堵墙的 proxy 之间反复重新入队。
- **`request.py`** —— “绕过服务不可达”缓存由整轮 run 的锁存改为 60 秒 TTL
  （`BYPASS_UNREACHABLE_TTL`），因为这类失败会抖动（见 Evidence）。过期条目在读取时被丢弃，
  该主机随即被重新探测；重新探测再次失败则重新开启窗口。
- **`request.py`** —— `_masked_bypass_base()` 从解析后的服务 URL 反读端口，因此被
  `CF_BYPASS_PORT_MAP` 重映射的主机会按实际拨号的端口记录日志，而不是池级默认端口。
- **`apps/cli/ops/cf_bypass_probe.py`** —— 探测脚本改为遵循当前 run 自身的
  `CF_BYPASS_VIA_PROXY`，不再固定隧道拓扑；`_validate_target` 现在会拒绝越界或非数字端口。
- **11 个 workflow** —— 所有生成 config 的步骤都接上了 `VAR_CF_BYPASS_PORT_MAP_JSON`；
  缺少它时生成的 `config.py` 会回退到空映射，所有被重映射的主机都会按默认端口拨号。

## Side Effects

- **coordinator 的 per-proxy `cfEvents` 不再包含全站挑战**，因此故障期间
  `penalty_factor` 会偏低。这是有意的修正——那些样本度量的是 javdb 的 WAF，不是代理
  质量——但也意味着全站被墙时 coordinator 的跨 runner 节流不再退避。本地
  `penalty_tracker` 的节流不受影响。
- **该模式是所有 worker 共享的一个 run 级布尔量**，因此任一 worker 的观察都会把整个
  run 翻过去。这是有意为之（这个条件本来就是全站性的），但也意味着
  `is_cf_challenge_page` 一次误判就会把整个 run 送进较慢的 bypass 层，直到某次直连
  成功为止。
- **bypass 优先时，bypass 失败的任务仍然要付一次直连尝试。** 因此单个失败任务的墙钟
  时间没有变化；减少的是顺利路径上那些*注定*失败的直连尝试次数。
- **`site_challenge_active` 不跨 run 持久化**，因此每次 run 都要用一次被挑战的直连
  重新发现这堵墙。

## Follow-Up

[BFR-024](../BFR-024-CF-Managed-Challenge-Blind-Spot/BFR-024-cf-managed-challenge-blind-spot.zh.md)
的未完成项原样顺延。此外：

- [ ] `_should_prefer_bypass` 是 per-run 的；per-host 的变体可以让部分被墙的站点在
      仍然可用的主机上继续走直连。
- [ ] coordinator Worker（独立仓库
      [`TongWu/JAVDB_AutoSpider_Proxycoordinator`](https://github.com/TongWu/JAVDB_AutoSpider_Proxycoordinator)）
      至今没有全局熔断，ADR-043 D7 仍然悬空。一个跨 DO 的 "site-wide" 信号可以让它
      自己就能把 WAF 事件与坏代理区分开。
- [x] `javdb/spider/auth/login.py` 的 `_is_cloudflare_challenge` 现在与抓取层共用
      `is_cf_challenge_page`；`_attempt_cf_warmup` 也改为通过 `_build_bypass_proxies`
      经由 proxy 隧道访问，不再拨 runner 自己的回环地址（commit `a208076`）。
- [x] `javdb/spider/fetch/fallback.py` 在 pool 模式或 `CF_BYPASS_VIA_PROXY` 下不再用
      `is_cf_bypass_reachable()` 拦截顺序路径的 CF bypass；该探测仅保留给真正的本地
      部署（commit `dba22ce`）。
