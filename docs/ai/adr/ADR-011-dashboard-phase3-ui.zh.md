# ADR-011: Dashboard 重构 —— Phase 3：主 Dashboard UI

**状态**: 已接受 —— 计划中 (Accepted — Planned)
**日期**: 2026-05-16
**决策者**: Proxy Coordinator Dashboard 重写工作组
**相关**: 实现 [ADR-003](ADR-003-metrics-pipeline.md)；需要 [ADR-010](ADR-010-dashboard-phase2-worker-backend.md) 已部署；为 [ADR-012](ADR-012-dashboard-phase4-history-drilldowns.md) 的前置依赖

> **格式说明：** 本 ADR 最初是按"分步实施计划"的形式编写的，后被搬入 ADR 体系（遵循仓库约定，把设计记录集中存放）。决策上下文写在下方的 **目标 / 架构 / 技术栈** 前言里；其余部分则保留了原计划的执行清单。
>
> **面向 AI 工作者：** 必备子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans，按任务逐条实施本计划。各步骤使用 checkbox（`- [ ]`）语法以便跟踪。

**目标:** 重构 Dashboard 主视图：可见性感知轮询、浏览器本地时区 + 时区缩写、带互补时间格式的 hover tooltip、自动发现的 per-proxy 列表 + chip filter、ConfigState 始终展示合并后的配置，以及 5 个优先级图表（活跃 runner 趋势、队列深度、CF-bypass 比例 donut 图、per-proxy 延迟多线图、per-proxy 健康分多线图）。

**架构:** 将 `renderDashboardHtml()` 中内联的服务端渲染 HTML 替换为新版本，把 uPlot（最小化后约 14KB）和一个小型自定义 donut 渲染器内联进去。轮询从固定 30s 的 `setInterval` 切换为基于 Page Visibility API 的状态机（可见时 5s / 隐藏时 30s / 隐藏 30 分钟后暂停）。所有 filter 与时间区间状态持久化在 `localStorage`。**Phase 3 不做 drill-down** —— 那是 Phase 4 的事。Phase 3 完成后主视图即为新设计；drill-down 面板留待 Phase 4 干净接入。

**技术栈:** 原生 JavaScript（无构建步骤）、uPlot v1.6+（以 TS 字符串常量形式 vendored）、内联 SVG 用于 donut 图。

**参考文档:** [ADR-003](../../ai/adr/ADR-003-metrics-pipeline.md)、[CONTEXT.md](../../../CONTEXT.md)。面向用户的视觉决策记录在 `grill-me` 的 Q1-Q6（2026 年 4 月 5 日对话）中。

**前置依赖:** Phase 2 已部署；`/ops/snapshot` 自动发现 proxy；`/metrics/range` 与历史端点已上线。

---

## 文件结构

**新增文件:**
- `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts` —— 从 `index.ts` 中拆出；导出 `renderDashboardHtml(url: URL): string`
- `JAVDB_AutoSpider_Proxycoordinator/src/uplot_vendor.ts` —— 导出 `UPLOT_MIN_JS: string`（最小化后的库）与 `UPLOT_MIN_CSS: string`
- `JAVDB_AutoSpider_Proxycoordinator/test/dashboard_html.test.ts` —— 渲染 HTML 的服务端测试

**修改文件:**
- `JAVDB_AutoSpider_Proxycoordinator/src/index.ts` —— 从新模块 import `renderDashboardHtml`（该函数整体迁出）
- `JAVDB_AutoSpider_Proxycoordinator/test/dashboard.test.ts` —— 补充 Phase 3 行为检查

为什么拆分：现有的 `renderDashboardHtml` 已经是约 300 行的模板字符串；再加上 5 个图表 + 可见性轮询 + chip filter 会让体量翻倍。单独成文件让 `index.ts` 专注于路由。

---

## Task 1: Vendor uPlot —— 内联最小化库

**文件:**
- 新建: `JAVDB_AutoSpider_Proxycoordinator/src/uplot_vendor.ts`

- [ ] **Step 1: 下载 uPlot v1.6.31（或最新稳定版）**

执行：
```bash
mkdir -p /tmp/uplot-vendor
curl -sSL https://cdn.jsdelivr.net/npm/uplot@1.6.31/dist/uPlot.iife.min.js -o /tmp/uplot-vendor/uplot.min.js
curl -sSL https://cdn.jsdelivr.net/npm/uplot@1.6.31/dist/uPlot.min.css -o /tmp/uplot-vendor/uplot.min.css
ls -l /tmp/uplot-vendor/
```
预期：两个文件，JS 原文件约 50KB / gzip 约 14KB，CSS 约 3KB。

- [ ] **Step 2: 转成带反引号字符串导出的 TS 模块**

新建 `JAVDB_AutoSpider_Proxycoordinator/src/uplot_vendor.ts`。读取这两个文件并嵌入到反引号模板字面量中。可用一段 node 小脚本：

执行：
```bash
node -e "
const fs = require('fs');
const js = fs.readFileSync('/tmp/uplot-vendor/uplot.min.js', 'utf8');
const css = fs.readFileSync('/tmp/uplot-vendor/uplot.min.css', 'utf8');
// Backtick is the only char to escape because we use template literals.
const escape = (s) => s.replaceAll('\\\\', '\\\\\\\\').replaceAll('\\\`', '\\\\\\\`').replaceAll('\${', '\\\\\${');
fs.writeFileSync('JAVDB_AutoSpider_Proxycoordinator/src/uplot_vendor.ts', \`/**
 * Phase 3 — vendored uPlot v1.6.31 (MIT licensed, see https://github.com/leeoniya/uPlot).
 *
 * Inlined into the Worker so the dashboard ships with zero external
 * runtime dependencies (CSP-friendly, no CDN trust). Bundle cost: ~14KB
 * gzip — negligible compared to the dashboard HTML itself.
 */
export const UPLOT_MIN_JS = \\\`\${escape(js)}\\\`;
export const UPLOT_MIN_CSS = \\\`\${escape(css)}\\\`;
\`);
console.log('Wrote uplot_vendor.ts');
"
```

验证命令：`wc -l JAVDB_AutoSpider_Proxycoordinator/src/uplot_vendor.ts` → 约 3 行长行加上样板代码。

- [ ] **Step 3: 对 vendor 文件做合理性检查**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx tsc --noEmit src/uplot_vendor.ts`
预期：零错误。该文件应能作为纯 TypeScript 被解析。

- [ ] **Step 4: 提交**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/uplot_vendor.ts
git commit -m "$(cat <<'EOF'
chore(worker): vendor uPlot 1.6.31 for inline dashboard charts (Phase 3)

MIT-licensed; ~14KB gzip; embedded as template literal so the dashboard
HTML can ship with zero external runtime dependencies (CSP-friendly).
EOF
)"
```

---

## Task 2: 将 `renderDashboardHtml` 及其 helper 迁到新模块

**文件:**
- 新建: `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts`
- 修改: `JAVDB_AutoSpider_Proxycoordinator/src/index.ts`（删除内联函数；从新模块 import）

- [ ] **Step 1: 把当前的 `renderDashboardHtml`、`commonDashboardStyles`、`escapeHtmlForServer` 复制到新文件**

新建 `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts`，把这三个函数原样从 `index.ts`（当前约 1361-1702 行）粘贴过来。导出 `renderDashboardHtml`，helper 保持模块内私有。在文件顶部加：

```typescript
/**
 * Phase 3 — Server-rendered HTML for the operator dashboard.
 *
 * Lifted out of index.ts to keep that file focused on routing. All
 * interactive behaviour lives in the inline <script> tag rendered here.
 *
 * Architecture:
 *   - Visibility-aware polling (5s visible / 30s hidden / pause after 30 min hidden)
 *   - Browser-local time formatting with timezone abbreviation
 *   - localStorage-persisted filter + time-range state
 *   - uPlot charts (vendored, see uplot_vendor.ts)
 *
 * See docs/ai/adr/ADR-003 for the data-pipeline design that feeds this UI.
 */

import { UPLOT_MIN_JS, UPLOT_MIN_CSS } from "./uplot_vendor";
```

- [ ] **Step 2: 修改 `index.ts` 改为 import 这个函数**

在 `JAVDB_AutoSpider_Proxycoordinator/src/index.ts` 里：
- 删除三个函数定义（`renderDashboardHtml`、`commonDashboardStyles`、`escapeHtmlForServer`）
- 在文件顶部、其他 import 之后加：
  ```typescript
  import { renderDashboardHtml } from "./dashboard_html";
  ```

- [ ] **Step 3: 跑全部既有测试确认无行为回归**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx vitest --run`
预期：全部通过（Dashboard 渲染结果完全一致，只是源代码位置变了）。

- [ ] **Step 4: 提交**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/dashboard_html.ts src/index.ts
git commit -m "refactor(worker): extract renderDashboardHtml to its own module (Phase 3)"
```

---

## Task 3: 替换轮询 —— 可见性感知状态机

**文件:**
- 修改: `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts`（内联 `<script>` 标签）
- 测试: `JAVDB_AutoSpider_Proxycoordinator/test/dashboard_html.test.ts`（新增）

- [ ] **Step 1: 写出针对可见性感知常量的失败测试**

```typescript
// JAVDB_AutoSpider_Proxycoordinator/test/dashboard_html.test.ts
import { describe, it, expect } from "vitest";
import { renderDashboardHtml } from "../src/dashboard_html";

describe("Phase 3 dashboard HTML — visibility-aware polling", () => {
  const html = renderDashboardHtml(new URL("https://dash.test/dashboard"));

  it("declares the three poll intervals", () => {
    expect(html).toContain("VISIBLE_MS = 5000");
    expect(html).toContain("HIDDEN_MS = 30000");
    expect(html).toContain("PAUSE_AFTER_HIDDEN_MS = 1800000");
  });

  it("uses Page Visibility API", () => {
    expect(html).toContain("document.visibilityState");
    expect(html).toContain("visibilitychange");
  });

  it("does NOT use the old 30s setInterval pattern", () => {
    // We expect the new state-machine implementation, not setInterval(refresh, REFRESH_MS)
    expect(html).not.toContain("setInterval(refresh, REFRESH_MS)");
  });
});
```

- [ ] **Step 2: 确认测试失败**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
预期：3 个失败。

- [ ] **Step 3: 替换内联脚本中的轮询块**

在 `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts` 里，定位 `renderDashboardHtml` 末尾的 IIFE。把整段轮询代码（当前是 `var REFRESH_MS = 30000;` + `refresh(); setInterval(refresh, REFRESH_MS);`）替换成：

```javascript
  // ── Phase 3: visibility-aware polling ───────────────────────────────
  var VISIBLE_MS = 5000;
  var HIDDEN_MS = 30000;
  var PAUSE_AFTER_HIDDEN_MS = 1800000;  // 30 min

  var pollTimer = null;
  var hiddenSinceMs = 0;
  var paused = false;

  function currentInterval() {
    if (document.visibilityState === "visible") return VISIBLE_MS;
    return HIDDEN_MS;
  }

  function scheduleNext() {
    if (pollTimer !== null) { clearTimeout(pollTimer); pollTimer = null; }
    if (paused) { return; }
    pollTimer = setTimeout(tick, currentInterval());
  }

  function tick() {
    pollTimer = null;
    // If hidden too long, pause entirely until user returns.
    if (document.visibilityState === "hidden" && hiddenSinceMs > 0) {
      var hiddenFor = Date.now() - hiddenSinceMs;
      if (hiddenFor >= PAUSE_AFTER_HIDDEN_MS) {
        paused = true;
        $("state").textContent = "paused (tab hidden)";
        return;
      }
    }
    refresh().finally(scheduleNext);
  }

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") {
      hiddenSinceMs = 0;
      paused = false;
      refresh().finally(scheduleNext); // immediate refresh on return
    } else {
      hiddenSinceMs = Date.now();
      scheduleNext(); // re-arm at 30s cadence
    }
  });

  // Initial fetch + start loop.
  refresh().finally(scheduleNext);
```

把 `refresh()` 改成返回 Promise（当前用的是 `.then(...).catch(...)`）：

```javascript
  function refresh() {
    var url = "/ops/snapshot"; // no proxy_ids — auto-discover from proxies_seen (Phase 2 / ADR-004)
    $("state").textContent = "polling…";
    return fetch(url, { credentials: "same-origin" }).then(function(r){
      if(r.status === 401){ window.location.href = "/"; throw new Error("auth"); }
      if(r.status !== 200) throw new Error("HTTP "+r.status);
      return r.json();
    }).then(function(data){
      var nowMs = data.server_time || Date.now();
      renderStats(data, nowMs);
      renderBanners(data);
      renderRunners(data, nowMs);
      renderSignals(data, nowMs);
      renderConfig(data);
      renderProxies(data);
      setBrandLive(true);
      $("state").textContent = "live";
      $("ts").textContent = fmtTs(nowMs);
    }).catch(function(err){
      setBrandLive(false);
      $("state").textContent = "error: " + err.message;
    });
  }
```

同时移除旧的 `var PROXY_IDS = ${proxyIdsJs};` 块 —— proxy_ids 不再从 URL 取。

- [ ] **Step 4: 跑测试**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
预期：3 个通过。

- [ ] **Step 5: 提交**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/dashboard_html.ts test/dashboard_html.test.ts
git commit -m "$(cat <<'EOF'
feat(worker): visibility-aware dashboard polling (Phase 3)

5s visible / 30s hidden / pause after 30min hidden. Returning to
the tab triggers immediate refresh + resumes 5s cadence. Replaces
the old fixed 30s setInterval.
EOF
)"
```

---

## Task 4: 浏览器本地时区 + 时区缩写 + hover tooltip

**文件:**
- 修改: `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts`
- 测试: 扩展 `test/dashboard_html.test.ts`

- [ ] **Step 1: 写失败测试**

在 `test/dashboard_html.test.ts` 末尾追加：

```typescript
describe("Phase 3 — browser timezone formatting", () => {
  const html = renderDashboardHtml(new URL("https://dash.test/dashboard"));

  it("uses Intl.DateTimeFormat with timeZoneName: 'short'", () => {
    expect(html).toContain("timeZoneName");
    expect(html).toContain("Intl.DateTimeFormat");
  });

  it("removes the legacy fmtTs that used toISOString + 'Z' suffix", () => {
    // Old: toISOString().replace("T", " ").slice(11,19) + "Z"
    expect(html).not.toContain('.slice(11,19) + "Z"');
  });

  it("renders hover tooltips with title attributes for time fields", () => {
    expect(html).toMatch(/title="[^"]*\${[^}]*absTs/);
  });
});
```

- [ ] **Step 2: 确认失败**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
预期：新增测试有 3 个失败。

- [ ] **Step 3: 替换 `fmtTs` 等函数**

在 `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts` 中，找到当前 `fmtTs` / `fmtAge` 代码块，替换成：

```javascript
  // ── Phase 3: browser-local time formatting with tz abbreviation ─────
  var _tzFormatter = new Intl.DateTimeFormat([], {
    hour: "2-digit", minute: "2-digit", second: "2-digit",
    hour12: false, timeZoneName: "short",
  });

  function fmtTs(ms){
    if(!ms) return "—";
    // Intl.DateTimeFormat output is like "14:23:45 SGT"
    return _tzFormatter.format(new Date(ms));
  }

  function fmtAge(ms, nowMs){
    if(!ms) return "—";
    var s = Math.max(0, (nowMs - ms) / 1000);
    if(s < 60) return s.toFixed(0) + "s";
    if(s < 3600) return (s/60).toFixed(1) + "m";
    return (s/3600).toFixed(1) + "h";
  }

  function fmtDur(ms){
    if(ms <= 0) return "—";
    var s = ms / 1000;
    if(s < 60) return s.toFixed(0) + "s";
    if(s < 3600) return (s/60).toFixed(1) + "m";
    return (s/3600).toFixed(1) + "h";
  }
```

在 runners 和 signals 的渲染函数中，原来直接渲染 `fmtAge(...) + " ago"` 或 `fmtTs(...)` 的地方，外层包一层 `<span title="...互补格式...">`：

对相对时间→绝对时间的 hover（例如心跳 age）：
```javascript
var absTs = fmtTs(r.last_heartbeat);
var relAge = fmtAge(r.last_heartbeat, nowMs) + " ago";
var cell = '<span title="' + esc(absTs) + '">' + esc(relAge) + '</span>';
```

对绝对时间→相对时间的 hover（topbar 的 `#ts`）：
```javascript
// at the topbar update site:
var tsAbs = fmtTs(nowMs);
var tsRel = fmtAge(nowMs, Date.now());
$("ts").innerHTML = '<span title="' + esc(tsRel + " ago") + '">' + esc(tsAbs) + '</span>';
```

- [ ] **Step 4: 跑测试**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
预期：全部通过。

- [ ] **Step 5: 提交**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/dashboard_html.ts test/dashboard_html.test.ts
git commit -m "feat(worker): browser-local timezone with abbr + hover tooltips (Phase 3)"
```

---

## Task 5: ConfigState 始终展示合并后的配置（defaults + overrides）

**文件:**
- 修改: `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts`（`renderConfig`）
- 修改: `JAVDB_AutoSpider_Proxycoordinator/src/config_state.ts`（snapshot 端点同时返回 `defaults` 与 `values`）
- 测试: 扩展 `test/dashboard_html.test.ts`

- [ ] **Step 1: 检查当前 `/do/config` 响应结构**

执行：`grep -n "GET\|/do/config\|values\|defaults" JAVDB_AutoSpider_Proxycoordinator/src/config_state.ts | head -30`

当前的 `ConfigSnapshot` 大概返回 `{ version, values, server_time }`，其中 `values` 是 override 覆盖层。Phase 3 需要的是**合并后的有效视图** —— 即对所有已知 key，给出当前值（来自 override 或 default）以及一个来源标记。

- [ ] **Step 2: 写失败测试（服务端：响应结构）**

新建文件 `JAVDB_AutoSpider_Proxycoordinator/test/config_snapshot_shape.test.ts`，内容：

```typescript
import { describe, it, expect } from "vitest";
import { env } from "cloudflare:test";

describe("ConfigState /do/config — Phase 3 merged shape", () => {
  it("returns every known key with effective value and source", async () => {
    const stub = env.CONFIG_STATE_DO.get(env.CONFIG_STATE_DO.idFromName("global-config"));
    const r = await stub.fetch("https://do/do/config", { method: "GET" });
    expect(r.status).toBe(200);
    const data: any = await r.json();
    // Phase 3 contract: response carries a `merged` map of every config key
    // with { value, source: 'default' | 'override' }.
    expect(data.merged).toBeDefined();
    expect(typeof data.merged).toBe("object");
    // Spot-check: SHORT_MAX is always present in defaults.
    expect(data.merged.SHORT_MAX).toBeDefined();
    expect(data.merged.SHORT_MAX.value).toBeDefined();
    expect(["default", "override"]).toContain(data.merged.SHORT_MAX.source);
  });
});
```

- [ ] **Step 3: 修改 `ConfigState` 返回 `merged`**

在 `JAVDB_AutoSpider_Proxycoordinator/src/config_state.ts` 中找到 GET `/do/config` 的处理函数。在算出现有 `values` 覆盖层之后，再算合并视图：

```typescript
    const KNOWN_KEYS = [
      "SHORT_WINDOW_SEC", "SHORT_MAX",
      "LONG_WINDOW_SEC", "LONG_MAX",
      "EXTRA_WINDOW_SEC", "EXTRA_MAX",
      "PENALTY_WINDOW_SEC", "JITTER_MAX_MS",
      "BAN_TTL_MS",
      "MOVIE_CLAIM_TTL_MS",
      "RUNNER_STALE_TTL_MS",
      "MOVIE_CLAIM_MIN_RUNNERS",
      "LOGIN_COOLDOWN_THRESHOLD", "LOGIN_COOLDOWN_WINDOW_SEC", "LOGIN_COOLDOWN_DURATION_MS",
      "WORKER_RATE_LIMIT_PER_MIN",
      "NUM_CLAIM_SHARDS",
      "METRICS_RETENTION_DAYS", "METRICS_MAX_ROWS",
      "SIGNALS_EVENT_LOG_RETENTION_DAYS", "RUNNERS_EVENT_LOG_RETENTION_DAYS",
      "LOGIN_EVENT_LOG_RETENTION_DAYS", "CONFIG_AUDIT_LOG_RETENTION_DAYS",
    ];
    const merged: Record<string, { value: string; source: "default" | "override" }> = {};
    for (const key of KNOWN_KEYS) {
      if (key in overrides) {
        merged[key] = { value: String(overrides[key]), source: "override" };
      } else {
        merged[key] = { value: String(this.env[key as keyof Env] ?? ""), source: "default" };
      }
    }
    // Return both the legacy `values` field (for older clients) and the new `merged` field.
    return new Response(JSON.stringify({
      version, values: overrides, merged, server_time: Date.now(),
    }), { headers: { "content-type": "application/json" } });
```

变量名按 `config_state.ts` 实际使用的名字微调。

- [ ] **Step 4: 给 UI 加失败测试**

在 `test/dashboard_html.test.ts` 末尾追加：

```typescript
describe("Phase 3 — Config panel always shows merged config", () => {
  const html = renderDashboardHtml(new URL("https://dash.test/dashboard"));

  it("renderConfig iterates over data.config.merged (not just values)", () => {
    expect(html).toContain("data.config.merged");
  });

  it("marks override vs default visually", () => {
    expect(html).toMatch(/source === "override"|"override"/);
  });
});
```

- [ ] **Step 5: 更新 `dashboard_html.ts` 中的 `renderConfig`**

把现有函数替换为：

```javascript
  function renderConfig(data){
    if(!data.config || !data.config.merged){ $("config").innerHTML = '<div class="empty">config-state DO unavailable</div>'; return; }
    var entries = Object.entries(data.config.merged);
    var overrideCount = entries.filter(function(kv){ return kv[1].source === "override"; }).length;
    var hdr = entries.length + ' key(s) · ' + overrideCount + ' override(s) · version <code style="text-transform:none;letter-spacing:0">' + esc(String(data.config.version || 0)) + '</code>';
    var html = '<details open><summary>' + hdr + '</summary><div class="config-grid">';
    entries.sort(function(a, b){ return a[0].localeCompare(b[0]); }).forEach(function(kv){
      var k = kv[0];
      var entry = kv[1];
      var srcPill = entry.source === "override"
        ? '<span class="pill warn" style="margin-left:8px;font-size:9px">override</span>'
        : '';
      html += '<div class="k">' + esc(k) + srcPill + '</div><div class="v">' + esc(String(entry.value)) + '</div>';
    });
    html += '</div></details>';
    $("config").innerHTML = html;
  }
```

- [ ] **Step 6: 跑测试**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/config_snapshot_shape.test.ts test/dashboard_html.test.ts --run`
预期：全部通过。

- [ ] **Step 7: 提交**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/config_state.ts src/dashboard_html.ts test/config_snapshot_shape.test.ts test/dashboard_html.test.ts
git commit -m "$(cat <<'EOF'
feat(worker): config panel always shows merged config (Phase 3)

/do/config now returns a `merged` map for every known key with
{value, source}. Dashboard renders all keys always, marking
overrides with an inline pill. Backward compat: legacy `values`
field still emitted.
EOF
)"
```

---

## Task 6: Per-proxy chip filter + 自动枚举的 proxy 列表

**文件:**
- 修改: `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts`（`renderProxies` + 新增 chip UI）
- 测试: 扩展 `test/dashboard_html.test.ts`

- [ ] **Step 1: 写失败测试**

在 `test/dashboard_html.test.ts` 末尾追加：

```typescript
describe("Phase 3 — per-proxy chip filter", () => {
  const html = renderDashboardHtml(new URL("https://dash.test/dashboard"));

  it("has a chip-filter container above the proxy table", () => {
    expect(html).toContain('id="proxy-chips"');
  });

  it("declares localStorage key for filter state", () => {
    expect(html).toContain('PROXY_FILTER_KEY = "dashboard.proxyFilter"');
  });

  it("renders all / none / invert toggle buttons", () => {
    expect(html).toContain("data-chip-action=\"all\"");
    expect(html).toContain("data-chip-action=\"none\"");
    expect(html).toContain("data-chip-action=\"invert\"");
  });
});
```

- [ ] **Step 2: 确认失败**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
预期：3 个失败。

- [ ] **Step 3: 加入 chip-filter 标签结构 + 行为**

在 `dashboard_html.ts` 中，修改 proxy 面板的 `<div class="panel full">`：

原有：
```html
<div class="panel full">
  <header>Per-proxy state <span class="badge" id="proxy-count">0</span></header>
  <div class="body" id="proxies"></div>
</div>
```

替换为：
```html
<div class="panel full">
  <header>
    Per-proxy state <span class="badge" id="proxy-count">0</span>
    <span style="margin-left:12px;font-size:10px;color:var(--muted)">
      <button data-chip-action="all" class="chip-btn">all</button>
      <button data-chip-action="none" class="chip-btn">none</button>
      <button data-chip-action="invert" class="chip-btn">invert</button>
    </span>
  </header>
  <div class="body">
    <div id="proxy-chips" style="padding:10px 16px;border-bottom:1px solid var(--border)"></div>
    <div id="proxies"></div>
  </div>
</div>
```

在已有的 `<style>` 块里加 CSS：
```css
  .chip-btn { background: var(--input-bg); color: var(--muted); border: 1px solid var(--border); border-radius: 4px; padding: 1px 8px; cursor: pointer; font-size: 10px; }
  .chip-btn:hover { color: var(--text); }
  .chip { display: inline-block; padding: 2px 10px; margin: 2px; font-size: 11px; border-radius: 999px; cursor: pointer; background: var(--input-bg); color: var(--muted); border: 1px solid var(--border); user-select: none; transition: all .12s; }
  .chip.active { background: var(--accent-dim); color: #0a0e14; border-color: var(--accent); }
  .chip .dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; vertical-align:middle; }
```

在内联 `<script>` 中加入：
```javascript
  // ── Phase 3: chip-filter state ──────────────────────────────────────
  var PROXY_FILTER_KEY = "dashboard.proxyFilter";
  var proxyFilter = loadProxyFilter();  // Set<string> of EXCLUDED proxy ids
  function loadProxyFilter() {
    try {
      var raw = localStorage.getItem(PROXY_FILTER_KEY);
      if (!raw) return new Set();
      return new Set(JSON.parse(raw));
    } catch { return new Set(); }
  }
  function saveProxyFilter() {
    try { localStorage.setItem(PROXY_FILTER_KEY, JSON.stringify(Array.from(proxyFilter))); } catch {}
  }
  function colorForProxy(id, index, total) {
    // Stable HSL hash so the same proxy always gets the same colour.
    var h = 0;
    for (var i = 0; i < id.length; i++) h = ((h << 5) - h + id.charCodeAt(i)) | 0;
    return "hsl(" + (Math.abs(h) % 360) + ", 65%, 60%)";
  }
  function renderProxyChips(data) {
    var rows = data.proxies || [];
    var html = "";
    rows.forEach(function(p, idx){
      var col = colorForProxy(p.proxy_id, idx, rows.length);
      var on = !proxyFilter.has(p.proxy_id);
      html += '<span class="chip ' + (on ? "active" : "") + '" data-proxy-id="' + esc(p.proxy_id) + '">'
        + '<span class="dot" style="background:' + col + '"></span>'
        + esc(p.proxy_id) + '</span>';
    });
    $("proxy-chips").innerHTML = html;
  }
  document.addEventListener("click", function(e){
    var chip = e.target.closest && e.target.closest(".chip");
    if (chip) {
      var id = chip.getAttribute("data-proxy-id");
      if (proxyFilter.has(id)) proxyFilter.delete(id); else proxyFilter.add(id);
      saveProxyFilter();
      refresh();
      return;
    }
    var btn = e.target.closest && e.target.closest("[data-chip-action]");
    if (btn) {
      var action = btn.getAttribute("data-chip-action");
      var allIds = Array.from(document.querySelectorAll("#proxy-chips .chip")).map(function(c){ return c.getAttribute("data-proxy-id"); });
      if (action === "all") proxyFilter = new Set();
      else if (action === "none") proxyFilter = new Set(allIds);
      else if (action === "invert") {
        var inv = new Set();
        allIds.forEach(function(id){ if (!proxyFilter.has(id)) inv.add(id); });
        proxyFilter = inv;
      }
      saveProxyFilter();
      refresh();
    }
  });
```

在 `renderProxies` 中，在渲染表格前先过滤掉被排除的 proxy：
```javascript
  function renderProxies(data){
    renderProxyChips(data);
    var allRows = data.proxies || [];
    var rows = allRows.filter(function(p){ return !proxyFilter.has(p.proxy_id); });
    $("proxy-count").textContent = rows.length + " / " + allRows.length;
    // ... rest of existing renderProxies unchanged (the table render of `rows`)
  }
```

同时移除旧的 "No proxies queried. Append ?proxy_ids=..." 提示 —— Phase 2 已实现自动发现，这种状态几乎不会发生；万一发生，改为提示 "no proxies seen yet — first runner register will populate this list"：
```javascript
    if (allRows.length === 0) {
      $("proxies").innerHTML = '<div class="hint">No proxies seen yet — the first runner register (with proxy_pool payload) will populate this list automatically.</div>';
      return;
    }
```

- [ ] **Step 4: 跑测试**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
预期：全部通过。

- [ ] **Step 5: 提交**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/dashboard_html.ts test/dashboard_html.test.ts
git commit -m "$(cat <<'EOF'
feat(worker): per-proxy chip filter with localStorage persistence (Phase 3)

Per-proxy panel now auto-renders all proxies from /ops/snapshot.
Chip filter lets the operator toggle visibility; selection persists
across refreshes via localStorage. all/none/invert buttons added.
EOF
)"
```

---

## Task 7: 加入 uPlot CSS + 全局图表行骨架

**文件:**
- 修改: `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts`

- [ ] **Step 1: 将 uPlot CSS 注入 `<style>`**

在 `dashboard_html.ts` 的 `<style>...${commonDashboardStyles()}...</style>` 块**最顶部**注入 `${UPLOT_MIN_CSS}`。放在原有规则之前，便于业务规则在需要时覆盖 uPlot 默认值：

```typescript
return `<!doctype html>
<html lang="en"><head>...
<style>${UPLOT_MIN_CSS}
${commonDashboardStyles()}
  // ... existing rules
```

- [ ] **Step 2: 在 `<body>` 末尾、Dashboard IIFE 之前内联 uPlot JS**

```typescript
<script>${UPLOT_MIN_JS}</script>
<script>
(function(){
  // ... existing IIFE
})();
</script>
```

- [ ] **Step 3: 加入 5 个图表槽的标签结构**

在 topbar `<div class="topbar">...</div>` 之后、`<main>` 之前调整结构。把 `<main>...</main>` 改为：在 `<div class="grid">` 面板之前先插入一行 charts：

```html
<main>
  <div id="banners"></div>
  <div class="stats" id="stats"></div>
  <div class="charts">
    <div class="panel chart-panel" id="chart-runners"><header>Active runners trend</header><div class="chart-body"></div></div>
    <div class="panel chart-panel" id="chart-queue"><header>Queue depth</header><div class="chart-body"></div></div>
    <div class="panel chart-panel" id="chart-cf-bypass"><header>CF-bypass / banned ratio</header><div class="chart-body"></div></div>
    <div class="panel chart-panel" id="chart-latency"><header>Per-proxy latency (ms)</header><div class="chart-body"></div></div>
    <div class="panel chart-panel" id="chart-health"><header>Per-proxy health score</header><div class="chart-body"></div></div>
  </div>
  <div class="grid"> ... existing panels ... </div>
</main>
```

加 CSS：
```css
  .charts { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-bottom: 22px; }
  @media (max-width: 1100px) { .charts { grid-template-columns: 1fr 1fr; } }
  @media (max-width: 700px) { .charts { grid-template-columns: 1fr; } }
  .chart-panel .chart-body { padding: 8px 12px 12px; min-height: 180px; }
  .chart-panel header { font-size: 11px; }
```

- [ ] **Step 4: 跑既有测试确认未引入破坏**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx vitest --run`
预期：全部通过。

- [ ] **Step 5: 提交**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/dashboard_html.ts
git commit -m "feat(worker): inline uPlot + chart slot scaffolding (Phase 3)"
```

---

## Task 8: 实现 5 个图表 —— 拉历史数据 + 渲染

**文件:**
- 修改: `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts`
- 测试: 扩展 `test/dashboard_html.test.ts`

- [ ] **Step 1: 写测试断言 5 个图表渲染器都存在**

在 `test/dashboard_html.test.ts` 末尾追加：

```typescript
describe("Phase 3 — 5 priority charts", () => {
  const html = renderDashboardHtml(new URL("https://dash.test/dashboard"));

  it("declares all 5 chart renderer functions", () => {
    expect(html).toContain("renderChartRunners");
    expect(html).toContain("renderChartQueue");
    expect(html).toContain("renderChartCfBypass");
    expect(html).toContain("renderChartLatency");
    expect(html).toContain("renderChartHealth");
  });

  it("fetches /metrics/range on each refresh", () => {
    expect(html).toContain("/metrics/range");
  });

  it("uses uPlot for the 4 time-series charts", () => {
    expect(html).toContain("new uPlot");
  });

  it("renders the donut without uPlot (custom SVG)", () => {
    expect(html).toContain("renderChartCfBypass");
    expect(html).toContain("<svg");
  });
});
```

- [ ] **Step 2: 确认失败**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
预期：4 个失败。

- [ ] **Step 3: 加上图表数据拉取 + 4 个 uPlot 渲染器 + 1 个 SVG donut**

在 `dashboard_html.ts` 的内联 `<script>` 中，已有 helper 之后、`refresh()` 之前，加入：

```javascript
  // ── Phase 3: charts ─────────────────────────────────────────────────
  var CHARTS_RANGE_MS = 60 * 60 * 1000;  // last 1h on main view; drill-down can override (Phase 4)
  var charts = {};  // chart-id → uPlot instance

  function chartOptions(title, seriesDef){
    return {
      title: title,
      width: 360, height: 180,
      cursor: { drag: { x: false } },
      legend: { show: false },
      scales: { x: { time: true } },
      series: seriesDef,
      padding: [8, 12, 12, 36],
      axes: [
        { stroke: "#6e7681", grid: { stroke: "#1f2730" } },
        { stroke: "#6e7681", grid: { stroke: "#1f2730" } },
      ],
    };
  }

  function destroyChart(id){ if(charts[id]){ charts[id].destroy(); delete charts[id]; } }

  function timeAxis(snapshots){ return snapshots.map(function(s){ return Math.floor(s.ts/1000); }); }

  function renderChartRunners(snapshots){
    var ts = timeAxis(snapshots);
    var vals = snapshots.map(function(s){ return s.payload.runners?.active_runners?.length || 0; });
    destroyChart("runners");
    if(ts.length === 0){ $("chart-runners").querySelector(".chart-body").innerHTML = '<div class="empty">no data</div>'; return; }
    charts["runners"] = new uPlot(
      chartOptions("", [{}, { label: "active", stroke: "#4ade80", width: 2 }]),
      [ts, vals],
      $("chart-runners").querySelector(".chart-body"),
    );
  }

  function renderChartQueue(snapshots){
    var ts = timeAxis(snapshots);
    var queued = snapshots.map(function(s){ return s.payload.work?.queued || 0; });
    var inFlight = snapshots.map(function(s){ return s.payload.work?.in_flight || 0; });
    destroyChart("queue");
    if(ts.length === 0){ $("chart-queue").querySelector(".chart-body").innerHTML = '<div class="empty">no data</div>'; return; }
    charts["queue"] = new uPlot(
      chartOptions("", [
        {},
        { label: "queued", stroke: "#38bdf8", width: 2 },
        { label: "in_flight", stroke: "#fbbf24", width: 2 },
      ]),
      [ts, queued, inFlight],
      $("chart-queue").querySelector(".chart-body"),
    );
  }

  function renderChartCfBypass(snapshots){
    var latest = snapshots.length > 0 ? snapshots[snapshots.length - 1] : null;
    var proxies = (latest?.payload.proxies) || [];
    var banned = proxies.filter(function(p){ return p.banned; }).length;
    var cfBypass = proxies.filter(function(p){ return p.requires_cf_bypass; }).length;
    var healthy = proxies.length - banned - cfBypass;
    var total = proxies.length || 1;

    function arc(start, end, color){
      // SVG donut arc helper. start/end are fractions [0,1].
      var R = 50, r = 30, CX = 70, CY = 70;
      var a0 = start * Math.PI * 2 - Math.PI/2;
      var a1 = end * Math.PI * 2 - Math.PI/2;
      var large = (end - start) > 0.5 ? 1 : 0;
      var x0 = CX + R * Math.cos(a0), y0 = CY + R * Math.sin(a0);
      var x1 = CX + R * Math.cos(a1), y1 = CY + R * Math.sin(a1);
      var xi0 = CX + r * Math.cos(a1), yi0 = CY + r * Math.sin(a1);
      var xi1 = CX + r * Math.cos(a0), yi1 = CY + r * Math.sin(a0);
      return '<path d="M' + x0 + ',' + y0 + ' A' + R + ',' + R + ' 0 ' + large + ',1 ' + x1 + ',' + y1
        + ' L' + xi0 + ',' + yi0 + ' A' + r + ',' + r + ' 0 ' + large + ',0 ' + xi1 + ',' + yi1
        + ' Z" fill="' + color + '" />';
    }
    var f1 = healthy / total;
    var f2 = f1 + cfBypass / total;
    var html = '<svg width="140" height="140" viewBox="0 0 140 140" style="margin:auto;display:block">'
      + arc(0, f1, "#4ade80") + arc(f1, f2, "#fbbf24") + arc(f2, 1, "#f87171")
      + '<text x="70" y="74" text-anchor="middle" font-size="14" fill="#d4d7e0" font-family="ui-sans-serif">'
      + proxies.length + '</text>'
      + '<text x="70" y="92" text-anchor="middle" font-size="9" fill="#6e7681">proxies</text>'
      + '</svg>'
      + '<div style="text-align:center;font-size:11px;color:var(--muted);margin-top:6px">'
      + '<span style="color:#4ade80">● healthy ' + healthy + '</span> · '
      + '<span style="color:#fbbf24">● cf-bypass ' + cfBypass + '</span> · '
      + '<span style="color:#f87171">● banned ' + banned + '</span>'
      + '</div>';
    $("chart-cf-bypass").querySelector(".chart-body").innerHTML = html;
  }

  function renderChartLatency(snapshots){
    var ts = timeAxis(snapshots);
    // Build a map: proxy_id -> array of latency values (one per snapshot).
    var allIds = new Set();
    snapshots.forEach(function(s){ (s.payload.proxies || []).forEach(function(p){ allIds.add(p.proxy_id); }); });
    var idList = Array.from(allIds).filter(function(id){ return !proxyFilter.has(id); });
    var series = idList.map(function(id){
      return snapshots.map(function(s){
        var p = (s.payload.proxies || []).find(function(x){ return x.proxy_id === id; });
        return p?.health?.latency_ema_ms ?? null;
      });
    });
    destroyChart("latency");
    if(ts.length === 0 || idList.length === 0){ $("chart-latency").querySelector(".chart-body").innerHTML = '<div class="empty">no data</div>'; return; }
    var seriesDef = [{}].concat(idList.map(function(id){
      return { label: id, stroke: colorForProxy(id, 0, idList.length), width: 1.5 };
    }));
    charts["latency"] = new uPlot(
      chartOptions("", seriesDef),
      [ts].concat(series),
      $("chart-latency").querySelector(".chart-body"),
    );
  }

  function renderChartHealth(snapshots){
    var ts = timeAxis(snapshots);
    var allIds = new Set();
    snapshots.forEach(function(s){ (s.payload.proxies || []).forEach(function(p){ allIds.add(p.proxy_id); }); });
    var idList = Array.from(allIds).filter(function(id){ return !proxyFilter.has(id); });
    var series = idList.map(function(id){
      return snapshots.map(function(s){
        var p = (s.payload.proxies || []).find(function(x){ return x.proxy_id === id; });
        var sc = p?.health?.score;
        return typeof sc === "number" ? sc * 100 : null;
      });
    });
    destroyChart("health");
    if(ts.length === 0 || idList.length === 0){ $("chart-health").querySelector(".chart-body").innerHTML = '<div class="empty">no data</div>'; return; }
    var seriesDef = [{}].concat(idList.map(function(id){
      return { label: id, stroke: colorForProxy(id, 0, idList.length), width: 1.5 };
    }));
    charts["health"] = new uPlot(
      chartOptions("", seriesDef),
      [ts].concat(series),
      $("chart-health").querySelector(".chart-body"),
    );
  }
```

修改 `refresh()`，除了拉 /ops/snapshot 外再拉 metrics，并调用各渲染器：

```javascript
  function refresh(){
    $("state").textContent = "polling…";
    return Promise.all([
      fetch("/ops/snapshot", { credentials: "same-origin" }).then(function(r){
        if(r.status === 401){ window.location.href = "/"; throw new Error("auth"); }
        if(r.status !== 200) throw new Error("HTTP /ops/snapshot " + r.status);
        return r.json();
      }),
      fetch("/metrics/range?from=" + (Date.now() - CHARTS_RANGE_MS) + "&to=" + Date.now(), { credentials: "same-origin" })
        .then(function(r){ return r.status === 200 ? r.json() : { rows: [] }; })
        .catch(function(){ return { rows: [] }; }),
    ]).then(function(results){
      var data = results[0];
      var snapshots = (results[1].rows || []);
      var nowMs = data.server_time || Date.now();

      renderStats(data, nowMs);
      renderBanners(data);
      renderRunners(data, nowMs);
      renderSignals(data, nowMs);
      renderConfig(data);
      renderProxies(data);

      renderChartRunners(snapshots);
      renderChartQueue(snapshots);
      renderChartCfBypass(snapshots);
      renderChartLatency(snapshots);
      renderChartHealth(snapshots);

      setBrandLive(true);
      $("state").textContent = "live";
      $("ts").innerHTML = '<span title="' + esc(fmtAge(nowMs, Date.now()) + " ago") + '">' + esc(fmtTs(nowMs)) + '</span>';
    }).catch(function(err){
      setBrandLive(false);
      $("state").textContent = "error: " + err.message;
    });
  }
```

- [ ] **Step 4: 跑全部测试**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx vitest --run`
预期：全部通过。

- [ ] **Step 5: 手动视觉验证**

本地起 worker：
```bash
cd JAVDB_AutoSpider_Proxycoordinator && npx wrangler dev --local
```

在另一个 shell 模拟几次 runner register + signal + 一次 cron tick 来灌数据：
```bash
TOKEN=$(grep PROXY_COORDINATOR_TOKEN .dev.vars | cut -d= -f2)
curl -s -X POST http://127.0.0.1:8787/register -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"holder_id":"vis-1","workflow_name":"DailyIngestion","proxy_pool":[{"id":"V-P1","name":"V-P1"},{"id":"V-P2","name":"V-P2"}]}'
curl -s -X POST http://127.0.0.1:8787/signal -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"kind":"throttle_global","ttl_ms":600000,"factor":1.5,"reason":"manual viz test"}'
```

在浏览器打开 <http://127.0.0.1:8787/>。登入 Dashboard 后应该看到：
- 顶部有 5 个图表面板（部分因数据少而为空，但都已渲染）
- "Live runners" 指标在 5s 内更新
- per-proxy 面板顶部有 chip filter
- 时间显示为 "HH:MM:SS SGT"（或对应你本地时区的格式）
- 鼠标 hover 时间字段会显示互补的时间格式

- [ ] **Step 6: 提交**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/dashboard_html.ts test/dashboard_html.test.ts
git commit -m "$(cat <<'EOF'
feat(worker): 5 priority dashboard charts (Phase 3)

uPlot: active-runners trend, queue depth, per-proxy latency multi-line,
per-proxy health-score multi-line.
SVG donut: CF-bypass / banned ratio.
Charts respect chip filter, refresh on every poll, last-1h window.
EOF
)"
```

---

## Task 9: Phase 3 验证 + 部署 dry run

**文件:** （无修改）

- [ ] **Step 1: 跑全部测试**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx vitest --run`
预期：全部通过。

- [ ] **Step 2: TypeScript 检查**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx tsc --noEmit`
预期：零错误。

- [ ] **Step 3: 部署 dry-run**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx wrangler deploy --dry-run --outdir /tmp/wrangler-phase3 2>&1 | tail -10`
预期：构建成功。

- [ ] **Step 4: 检查包体积**

执行：`du -sh /tmp/wrangler-phase3/`
预期：bundle 远低于 Worker 10MB 上限（uPlot 增加约 50KB 原文件，Dashboard HTML 增加约 10KB）。

- [ ] **Step 5: Phase 3 交接备注**

Phase 3 是最大的一次可见变化。部署完成后：
- 运营人员会看到新的图表、chip filter、浏览器本地时间、更快的刷新
- 各面板 header 上的 drill-down 按钮要等 Phase 4 才会加上

参见 `docs/superpowers/plans/2026-05-16-dashboard-overhaul-phase-4-history-drilldowns.md`。

---

## 自查清单 (Self-Review Checklist)

- ✅ uPlot 内联 vendored（无 CDN）
- ✅ 可见性感知轮询：5s / 30s / 30min 后暂停
- ✅ 浏览器本地时区 + 时区缩写；hover tooltip 给出互补格式
- ✅ Config 面板始终展示合并后的配置；override 有标记
- ✅ Per-proxy 自动枚举；chip filter；localStorage 持久化
- ✅ 5 个优先级图表已实现并有测试
- ✅ 不涉及 drill-down 工作（属 Phase 4 范围）
- ✅ 所有既有测试仍然通过
