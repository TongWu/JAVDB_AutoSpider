# BFR-024：Managed Challenge 识别盲区烧光整个代理池

**Status**: Fixed
**Date**: 2026-08-07
**Severity**: Critical
**Affected**: `javdb/infra/request.py`, `javdb/spider/html_validators.py`, `javdb/spider/fetch/fetch_engine.py`
**Related**: [ADR-043](../_archive/ADR-043-CF-Auto-Ban/ADR-043-cf-persistent-failure-auto-ban.zh.md), runs [31106061181](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/31106061181) / [31124515791](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/31124515791)
**Follow-on**: [BFR-025](../BFR-025-Site-Wide-Challenge-Bans-Pool-Via-Coordinator/BFR-025-site-wide-challenge-bans-pool-via-coordinator.zh.md) —— 本次修复事后证明并不完整

---

## Verification

在修复分支上跑的 `TestIngestion`（[31144998987](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/31144998987)），
彼时 8000 端口仍然不可达：

```
⚠ RequestHandl  [Direct] Proxy=Hyderabad-ARM1 returned Cloudflare challenge page (size=5841 bytes)
  FetchEngine   [page-1][worker=Hyderabad-ARM1] Site-wide Cloudflare challenge — re-queued without counting toward soft-ban (1/28 proxies)
⚠ RequestHandl  [CF Bypass] Proxy=Johannesburg-ARM1: service unreachable at http://129.xxx.xxx.230:8000 (ConnectTimeout) — bypass disabled for this proxy for the rest of the run
```

挑战页现在在 INFO 级日志里就可见，不再有代理因此被 ban，每个不可达的 bypass 只探测一次
而不是每个 fallback 步骤都探一次。因真正的代理自身问题而失败的代理（Jeddah 的
`BoringSSL SSL_connect: Connection closed abruptly`）仍然被软 ban —— 这正是我们想要的区分。

## Symptom

Daily Ingestion 在 2026-08-06 与 2026-08-07 连续失败，解析 0 条，代理全部被 ban：

```
──── 📊 OVERALL SUMMARY ─────────────────────
   pages    1-10
   found    0
   parsed   0
ProxyPool     Proxy pool · available=9/28 · cooldown=0 · banned=19 · no-proxy=false
Report        Currently banned proxies: 28 [session-scoped]
✗ Report      Spider produced NO results and proxy ban(s) were detected — all proxies may be blocked.
Spider exited with code 2
```

28 台代理在约 8 分钟内全部被 ban。INFO 级日志里只有 `CF Bypass initial attempt
failed` 和 `Process returned None` —— CF bypass 路径在真正收到坏响应时会打的那些
warning 一条都没有，这正是故障原因被掩盖的原因。

用 DEBUG 复跑一次（[31141477733](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/31141477733)）补上了缺失的两个事实：

```
[Direct Proxy=Hyderabad-ARM1][curl_cffi] HTTP 403 Forbidden (body 5841 bytes)
[CF Bypass Proxy=Jeddah-ARM1] ConnectTimeout: HTTPConnectionPool(host='144.x.x.88', port=8000):
    Connection to ... timed out. (connect timeout=60)
```

## Root Cause

三个互相独立的问题同时发生，其中只有第一个来自外部。

**1. 基础设施（外部触发）。** javdb.com 全站开启了 Cloudflare Managed
Challenge；同一时间 28 台代理机的 8000 端口全部不再接受连接。报的是
`ConnectTimeout` 而不是 `ConnectionRefused`，说明包是被防火墙丢弃的，而不是
bypass 进程死了。已独立验证：从无关主机探测，`12300`（squid）可连，`8000` 不可连。

**2. 挑战页识别只认旧版页面。** `request.py` 判定 Cloudflare 墙的方式是：

```python
is_turnstile = 'Security Verification' in html_content and 'turnstile' in html_content.lower()
```

javdb 现在返回的 Managed Challenge 页两个字符串都没有 —— 那是一个 5.4 KB 的响应，
里面带有 `Just a moment...` 和一段 `/cdn-cgi/challenge-platform/` 脚本。其中只有
前者具备区分度（原因见 Fix 一节），而 `Just a moment` 在整个 codebase 里没有任何匹配。结果所有依赖 `is_turnstile` 的
下游分支全部失效：`refresh_bypass_cache()`（重新获取 `cf_clearance` 的唯一路径）
不再触发，诊断 warning 打不出来，`is_cf_bypass_failure()` 也因为响应超过 1500
字节上限而返回 `False`。设计缺陷在于：这个判定编码的是挑战页的*某一种渲染形态*，
而不是真正重要的性质（"Cloudflare 挡在页面前面"）。

**3. 所有失败都被算到代理头上。** `_EngineWorker` 在连续 2 次 `None` 后软 ban 代理，
`_get_page_with_cf_bypass()` 在 fallback 级联耗尽 `cf_bypass_ban_threshold` 次后
抛 `ProxyBannedError`。两者都把"这次抓取失败了"等同于"这个代理坏了"。但本次的两个
原因都与具体代理无关：全站挑战对每个出口 IP 一视同仁，bypass 不可达是宿主机防火墙的
属性。代理池因为一个任何代理都无法影响的条件被摧毁，而 run 报出的
"all proxies may be blocked" 恰好把运维指向了错误的子系统。

60 秒的 connect timeout 放大了上述所有问题：每个 fallback 步骤都要对一个永远不会
应答的端口阻塞整整一分钟，worker 的重试次数在任何代理能被第二次尝试之前就已耗尽。

## Fix

**基础设施**：8000 端口从设计上就不该在代理的公网 IP 上可达——绕过服务（无论是之前
的 Camoufox 版本还是换成的 FlareSolverr）都刻意只绑回环，因为这个服务本身没有认证。
把它开放到公网不是修复，而是一次真实的安全倒退。真正的缺口是三个 ingestion
workflow 从未设置过 `CF_BYPASS_VIA_PROXY`，导致 runner 默认直拨
`{proxy_ip}:8000`，而不是经代理隧道转发到 `127.0.0.1:8000`——这正是该开关存在的
目的（见 `docs/handbook/*/self-hoster/cloudflare-bypass.md`）。squid 的 ACL 早就
放行了这条 `to_localhost` 转发所需的规则。修法是把 repo variable
`CF_BYPASS_VIA_PROXY` 设为 `true`，并在三个 workflow（`TestIngestion.yml`、
`DailyIngestion.yml`、`AdHocIngestion.yml`）里都接上
`VAR_CF_BYPASS_VIA_PROXY: ${{ vars.CF_BYPASS_VIA_PROXY || 'False' }}`——34 台
代理机本身一行都不用改。

以下代码侧改动让同一类故障（即使真的是某台代理的绕过服务不可达）也能变得可生还、
可读：

- **`html_validators.py`** —— 新增 `is_cf_challenge_page()`，匹配
  `just a moment...` 和旧版的 `security verification`。**不**匹配
  `/cdn-cgi/challenge-platform/`：那是最初的做法，但 Cloudflare 会往开启 bot
  management 的站点的**每一个**响应里注入这段 beacon 脚本，导致真实页面被误判
  （一次真实的 CF-Bypass 响应带着完整的 movie-list 内容却被当成挑战页丢弃）。
  同样有意排除 Cloudflare 的 *block* 页（error 1020）—— 那个确实是 IP 特定的，
  应当继续算到代理头上。
- **`request.py`** —— 三处挑战判定（`_fetch_direct`、`_process_html`、
  `_fetch_with_cf_bypass`）统一走这一个函数，今后挑战页再变形只需改一处而不是三处。
  `_do_request` 与 `_do_request_curl_cffi` 两条 403 分支都对挑战页跳过健康失败记录
  —— 其中 curl_cffi 那条才是真正会跑到的路径（`_fetch_direct` 优先用它）。
- **`request.py`** —— `_get_page_direct` 在识别到挑战后不再调用
  `mark_failure_and_switch()`。该调用会把失败记到代理池头上
  （`PROXY_POOL_MAX_FAILURES` → ban），而换下一个代理照样会拿到同一张挑战页。
- **`request.py`** —— CF bypass 请求改用 5 秒 connect timeout（read 仍为 60 秒），
  connect 失败即把该服务 URL 加入 per-handler 的 `_bypass_unreachable` 集合。后续
  页面直接跳过该代理的 bypass，不再重复支付超时；`refresh_bypass_cache()` 遵守同一集合。
  每个代理只打一条 WARNING。
- **`request.py`** —— body 为挑战页的 403 不再通过 `_record_request_complete()`
  记录健康失败（那会把每个代理的 coordinator 评分同等压低）；fallback 级联耗尽且
  最后一次响应是挑战页时，设置 `last_site_challenge` 并在 ban 判定之前返回，与已有的
  维护页 / 登录页早退分支并列。penalty tracker（`_record_cf_event`）仍然触发 ——
  那是节流信号，不是归责信号。
- **`fetch_engine.py`** —— worker 读取 `handler.last_site_challenge`，为真时重新入队
  且不递增 `_consecutive_none_count`。任务仍会累积 `failed_proxies`，在代理池穷尽后
  以 `all_proxies_failed` 结束，因此全站被挑战时 run 会及时结束而不是死循环 ——
  区别是代理池完好、日志准确。

- **`context.py` / `report.py`** —— 用 `ProxyRunState.site_challenge_seen` 记录本次 run
  发生过全站挑战；当 run 产出 0 条且该标志为真时，汇总报告以 2 退出。这一步是必需的：
  原先那条响亮的失败（"NO results and proxy ban(s) were detected"）是**经由 ban 路径**
  触发的，去掉 ban 也就顺带去掉了失败信号。本修复的第一次验证 run 就以 0 退出并写出了
  只有表头的 CSV —— 放到 DailyIngestion 上就意味着在全站被墙的情况下自动 commit 并发出
  一封"成功"邮件。

**本次修复并不完整。** 它只把本地软 ban 这一层从全站挑战中豁免了，却没有豁免
coordinator 上报，`_record_cf_event` 仍然把每一次挑战发布到该代理的 Durable Object
—— 而后者自己就能把整个代理池 ban 光。它也没有动级联的先后顺序，因此每一页在够到
bypass 层之前，仍然会在每个代理上各产生一次挑战。两者的分析与修复见
[BFR-025](../BFR-025-Site-Wide-Challenge-Bans-Pool-Via-Coordinator/BFR-025-site-wide-challenge-bans-pool-via-coordinator.zh.md)。

## Side Effects

- **全站被挑战时，现在以 `all_proxies_failed` 而非 `all_proxies_banned` 结束。**
  仍然以 2 退出、0 条结果，但走的是新的 `site_challenge_seen` 判定而非 ban 判定；
  归因、提示信息和残留的代理池状态都变了。
- **全站不可用时的墙钟时间没有变差。** 在 TestIngestion（3 页）上实测：修复后 12m50s
  干净退出，修复前 15m11s 被 ban 光后失败 —— 5 秒 connect timeout 省下的时间多于多试
  几个代理的开销。
- **`_record_request_complete` 不再看到挑战页 403**，因此全站挑战期间 coordinator 的
  `failureEvents` 会偏低。这是有意的修正 —— 那些样本度量的是 javdb 的 WAF，不是代理质量。
- **`_bypass_unreachable` 是 per-`RequestHandler`（即 per worker）的，且在一次 run 内
  不过期。** run 中途重启的 bypass 服务要到下一次 run 才会被重新使用。这是可接受的取舍：
  重新探测会把本次修掉的停顿又带回来，而单次 run 本身很短。
- `is_cf_challenge_page()` 走的是子串匹配，因此若某个 JavDB 页面正文里出现了字面的
  `just a moment...`，会被误判为挑战页。判断为不值得额外防护。

## Follow-Up

- [ ] 考虑在启动时对所有代理的 `:8000` 做一次可达性扫描，让 run 一开始就打出一条
      "bypass unavailable on N/28 proxies" 汇总，而不是运行中懒惰地发现。
- [ ] 当 `_bypass_unreachable` 覆盖整个池时告警 —— 即使 run 侥幸成功，那也是静默的
      容量损失。
- [ ] `SiteContractSentinel` 应当断言 javdb.com 可以在没有挑战的情况下访问，让 canary
      在 daily run 之前就发现此类问题。
