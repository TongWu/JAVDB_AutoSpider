# ADR-012: Dashboard 改造 — 第 4 阶段：历史 drill-down 抽屉

**状态**：Accepted — Planned
**日期**：2026-05-16
**决策者**：Proxy Coordinator Dashboard 重写工作组
**相关**：实现 [ADR-002](ADR-002-observability-data-storage-topology.md)；需要 [ADR-011](ADR-011-dashboard-phase3-ui.md) 已部署；为 dashboard 重写的最后一个阶段

> **格式说明：** 本 ADR 最初是作为一份逐步实施计划编写的，后按仓库的设计记录约定迁移到 ADR 空间。决策上下文记录在下方 **目标 / 架构 / 技术栈** 前言中；其余部分是原计划保留下来的执行清单。
>
> **面向 AI 工作者：** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 增加一个右侧滑入 drawer（约 40% 视口宽度），从每个面板 header 上的「History」按钮打开。Drawer 上带有一个时间范围选择器（7 个选项）以及以下 5 种历史视图之一：signals、runners、login、config audit、per-proxy 详情。每个 drill-down 可独立开发与合并 —— 本计划中每个任务对应一个 drawer。

**架构：** 一个共享的 drawer shell（overlay + 滑入面板 + 关闭按钮 + 时间范围选择器）被 5 个 drill-down 共用；具体 body 内容通过传入一个 `renderer` 函数来插入。每个 renderer 都会拉取一个第 2 阶段的历史端点（`/signals/history`、`/runners/history`、`/login/history`、`/config/history`，或按 proxy_id 过滤的 `/metrics/range`）。按照 ADR-002 + grill-me Q5a/Q6，drawer 每次打开时时间范围始终回到「Now」（状态在多次打开之间**不会**保留）。

**技术栈：** 与第 3 阶段相同 —— 原生 JS、vendored uPlot、内联 SVG。无新增依赖。

**参考文档：** [ADR-002](../../ai/adr/ADR-002-observability-data-storage-topology.md)、[CONTEXT.md](../../../CONTEXT.md)。面向用户的决策：grill-me Q5a（γ drawer 模式）、Q5d（图表位置）、Q6a（drawer shell）。

**前置依赖：** 第 3 阶段已部署；主 dashboard 已渲染出新的图表行；第 2 阶段的 5 个历史端点已有数据且可访问。

---

## 文件结构

**新增文件：**
- `JAVDB_AutoSpider_Proxycoordinator/src/drill_down_renderers.ts` —— 导出 5 个小型 renderer 函数，每个负责生成一个 drawer body 的内部 HTML。服务端渲染后随 dashboard 一并下发。

**修改文件：**
- `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts` —— 增加 drawer shell 标记 + JS 状态机；在 5 个面板 header 上渲染「History」按钮；接入 renderer 分发。
- `JAVDB_AutoSpider_Proxycoordinator/test/dashboard_html.test.ts` —— 扩展加入第 4 阶段的测试用例。

---

## 任务 1：Drawer shell —— overlay、滑入、关闭、时间范围选择器

**文件：**
- 修改：`JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts`
- 测试：`JAVDB_AutoSpider_Proxycoordinator/test/dashboard_html.test.ts`（扩展）

- [ ] **Step 1：编写失败的测试**

追加到 `test/dashboard_html.test.ts`：

```typescript
describe("Phase 4 — drawer shell", () => {
  const html = renderDashboardHtml(new URL("https://dash.test/dashboard"));

  it("has a drawer overlay container in the DOM", () => {
    expect(html).toContain('id="drawer-overlay"');
    expect(html).toContain('id="drawer"');
  });

  it("has all 7 time-range buttons", () => {
    ["Now", "10min", "30min", "1h", "6h", "24h", "7d", "30d"].forEach(label => {
      expect(html).toContain('data-range="' + label + '"');
    });
  });

  it("has a close button + ESC handler", () => {
    expect(html).toContain('id="drawer-close"');
    expect(html).toMatch(/key === ['"]Escape['"]/);
  });

  it("supports closing via overlay click", () => {
    expect(html).toContain("closeDrawer");
  });
});
```

- [ ] **Step 2：确认失败**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
预期：新增的 4 个用例失败。

- [ ] **Step 3：在 `</body>` 前加入 drawer 标记**

在 `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts` 中，紧贴 `</body>` 关闭标签之前：

```html
<div id="drawer-overlay" class="drawer-overlay hidden" aria-hidden="true">
  <aside id="drawer" class="drawer" role="dialog" aria-label="History detail">
    <header class="drawer-header">
      <span id="drawer-title" class="drawer-title">History</span>
      <button id="drawer-close" class="drawer-close" aria-label="Close">×</button>
    </header>
    <div class="drawer-range">
      <button data-range="Now" class="range-btn active">Now</button>
      <button data-range="10min" class="range-btn">10min</button>
      <button data-range="30min" class="range-btn">30min</button>
      <button data-range="1h" class="range-btn">1h</button>
      <button data-range="6h" class="range-btn">6h</button>
      <button data-range="24h" class="range-btn">24h</button>
      <button data-range="7d" class="range-btn">7d</button>
      <button data-range="30d" class="range-btn">30d</button>
    </div>
    <div id="drawer-body" class="drawer-body"></div>
  </aside>
</div>
```

加入 CSS：

```css
  .drawer-overlay {
    position: fixed; inset: 0; background: rgba(0,0,0,0.55);
    z-index: 100; opacity: 1; transition: opacity .15s;
  }
  .drawer-overlay.hidden { display: none; opacity: 0; }
  .drawer {
    position: absolute; top: 0; right: 0; height: 100vh;
    width: min(640px, 42vw); min-width: 360px;
    background: var(--card-bg); border-left: 1px solid var(--border);
    display: flex; flex-direction: column;
    transform: translateX(0%); transition: transform .18s ease-out;
    box-shadow: -20px 0 60px rgba(0,0,0,0.6);
  }
  .drawer-overlay.hidden .drawer { transform: translateX(100%); }
  .drawer-header { display: flex; align-items: center; justify-content: space-between;
    padding: 14px 18px; border-bottom: 1px solid var(--border); }
  .drawer-title { font-size: 13px; font-weight: 600; letter-spacing: .04em; text-transform: uppercase; color: var(--text); }
  .drawer-close { background: transparent; color: var(--muted); border: 0; font-size: 22px; cursor: pointer; line-height: 1; }
  .drawer-close:hover { color: var(--text); }
  .drawer-range { padding: 10px 16px; border-bottom: 1px solid var(--border); display: flex; flex-wrap: wrap; gap: 4px; }
  .range-btn { background: var(--input-bg); color: var(--muted); border: 1px solid var(--border); border-radius: 4px;
    padding: 3px 10px; font-size: 11px; cursor: pointer; }
  .range-btn:hover { color: var(--text); }
  .range-btn.active { background: var(--accent-dim); color: #0a0e14; border-color: var(--accent); }
  .drawer-body { flex: 1; overflow-y: auto; padding: 16px 18px; }
  @media (max-width: 700px) { .drawer { width: 100vw; } }
```

加入 JS 状态机。在现有图表代码之后、`refresh()` 之前，加入：

```javascript
  // ── Phase 4: drawer state machine ───────────────────────────────────
  var drawerOpen = false;
  var drawerSelectedRange = "Now";
  var drawerRenderer = null;   // function(rangeMs, ctxArgs)
  var drawerCtx = {};          // free-form context (e.g., proxy_id when opened from per-proxy panel)

  var RANGE_MS = {
    "Now": 0,           // single-point: just render the most recent state
    "10min": 10 * 60_000,
    "30min": 30 * 60_000,
    "1h": 60 * 60_000,
    "6h": 6 * 60 * 60_000,
    "24h": 24 * 60 * 60_000,
    "7d": 7 * 24 * 60 * 60_000,
    "30d": 30 * 24 * 60 * 60_000,
  };

  function openDrawer(title, renderer, ctxArgs){
    drawerOpen = true;
    drawerSelectedRange = "Now";  // grill-me Q6c: drill-down resets per open
    drawerRenderer = renderer;
    drawerCtx = ctxArgs || {};
    $("drawer-title").textContent = title;
    document.querySelectorAll(".range-btn").forEach(function(b){
      b.classList.toggle("active", b.getAttribute("data-range") === "Now");
    });
    $("drawer-overlay").classList.remove("hidden");
    $("drawer-overlay").setAttribute("aria-hidden", "false");
    renderDrawer();
  }

  function closeDrawer(){
    drawerOpen = false;
    $("drawer-overlay").classList.add("hidden");
    $("drawer-overlay").setAttribute("aria-hidden", "true");
    $("drawer-body").innerHTML = "";
    drawerRenderer = null;
  }

  function renderDrawer(){
    if (!drawerRenderer) return;
    var rangeMs = RANGE_MS[drawerSelectedRange];
    drawerRenderer(rangeMs, drawerCtx);
  }

  // Wire events
  $("drawer-close").addEventListener("click", closeDrawer);
  $("drawer-overlay").addEventListener("click", function(e){
    // Click on backdrop (not on the panel itself) closes.
    if (e.target === $("drawer-overlay")) closeDrawer();
  });
  document.addEventListener("keydown", function(e){
    if (e.key === "Escape" && drawerOpen) closeDrawer();
  });
  document.querySelectorAll(".range-btn").forEach(function(b){
    b.addEventListener("click", function(){
      drawerSelectedRange = b.getAttribute("data-range");
      document.querySelectorAll(".range-btn").forEach(function(x){
        x.classList.toggle("active", x === b);
      });
      renderDrawer();
    });
  });
```

- [ ] **Step 4：运行测试**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
预期：全部通过。

- [ ] **Step 5：手动 smoke 测试**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx wrangler dev --local`

在 <http://127.0.0.1:8787/> 的浏览器控制台中执行：
```js
openDrawer("Test", function(){ document.getElementById("drawer-body").innerHTML = '<p>hello drawer</p>'; }, {});
```
验证：drawer 从右侧滑入；ESC 关闭；点击 backdrop 关闭;关闭按钮可用。

- [ ] **Step 6：提交**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/dashboard_html.ts test/dashboard_html.test.ts
git commit -m "$(cat <<'EOF'
feat(worker): drawer shell with 7 time-range options (Phase 4)

Right-side slide-in drawer (~42vw on desktop, full-screen on
mobile). Time range selector: Now / 10min / 30min / 1h / 6h / 24h
/ 7d / 30d. Default Now, resets every open per grill-me Q6c.
Close via X button, backdrop click, or ESC. Used by all 5
drill-down views in tasks 2-6.
EOF
)"
```

---

## 任务 2：Signals 历史 drill-down（Gantt 时间线）

**文件：**
- 新建：`JAVDB_AutoSpider_Proxycoordinator/src/drill_down_renderers.ts`
- 修改：`JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts`（signals 面板 header + 接入 renderer）
- 测试：扩展 `test/dashboard_html.test.ts`

- [ ] **Step 1：编写失败的测试**

追加到 `test/dashboard_html.test.ts`：

```typescript
describe("Phase 4 — signals drill-down", () => {
  const html = renderDashboardHtml(new URL("https://dash.test/dashboard"));

  it("Signals panel header has a History button", () => {
    // Heuristic: between the "Active signals" header text and its closing </header>,
    // there is a button with data-drawer="signals".
    expect(html).toMatch(/Active signals[\s\S]*?data-drawer="signals"/);
  });

  it("signals renderer fetches /signals/history with from/to from selected range", () => {
    expect(html).toContain("signalsDrawerRenderer");
    expect(html).toContain("/signals/history?from=");
  });

  it("renders Gantt timeline with SVG bars per signal", () => {
    expect(html).toContain("renderSignalsGantt");
  });
});
```

- [ ] **Step 2：确认失败**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
预期：3 个用例失败。

- [ ] **Step 3：在 dashboard_html.ts 中加入 renderer 函数**

在 drawer 状态机之后、`refresh()` 之前：

```javascript
  // ── Phase 4: signals drill-down ─────────────────────────────────────
  function signalsDrawerRenderer(rangeMs, ctx){
    var body = $("drawer-body");
    body.innerHTML = '<div class="empty">loading…</div>';
    var to = Date.now();
    var from = rangeMs > 0 ? to - rangeMs : to - 60_000;  // "Now" → last 60s for tightness

    fetch("/signals/history?from=" + from + "&to=" + to, { credentials: "same-origin" })
      .then(function(r){ if(r.status !== 200) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function(data){
        var rows = data.rows || [];
        if (rows.length === 0){ body.innerHTML = '<div class="empty">No signal events in this window.</div>'; return; }

        // Build Gantt: group by signal_id, find create and matching expire/revoke timestamps.
        var bySignalId = new Map();
        rows.forEach(function(r){
          if (!bySignalId.has(r.signal_id)) bySignalId.set(r.signal_id, []);
          bySignalId.get(r.signal_id).push(r);
        });

        var svg = renderSignalsGantt(bySignalId, from, to);
        var tableRows = rows.map(function(r){
          var payload = "";
          try { payload = r.payload_json ? JSON.stringify(JSON.parse(r.payload_json)) : ""; } catch { payload = r.payload_json || ""; }
          return '<tr><td class="muted">' + esc(fmtTs(r.ts)) + '</td>'
            + '<td><span class="pill ' + (r.event_kind === "create" ? "warn" : "muted") + '">' + esc(r.event_kind) + '</span></td>'
            + '<td><code>' + esc(r.signal_kind) + '</code></td>'
            + '<td><code>' + esc(r.signal_id) + '</code></td>'
            + '<td class="muted" style="font-size:11px">' + esc(payload) + '</td></tr>';
        }).join("");

        body.innerHTML = '<div style="margin-bottom:16px">' + svg + '</div>'
          + '<table><tr><th>Time</th><th>Event</th><th>Kind</th><th>Signal ID</th><th>Payload</th></tr>' + tableRows + '</table>';
      })
      .catch(function(err){ body.innerHTML = '<div class="empty">error: ' + esc(err.message) + '</div>'; });
  }

  function renderSignalsGantt(bySignalId, fromMs, toMs){
    // SVG Gantt: horizontal time axis, one row per signal_id, bar from create to expire/revoke.
    var ROW_H = 22, PAD_TOP = 26, PAD_LEFT = 110, RIGHT_PAD = 12;
    var ids = Array.from(bySignalId.keys());
    if (ids.length === 0) return '';
    var width = 600;
    var height = PAD_TOP + ids.length * ROW_H + 20;
    var inner = width - PAD_LEFT - RIGHT_PAD;
    function x(ts){ return PAD_LEFT + (ts - fromMs) / Math.max(1, toMs - fromMs) * inner; }

    var bars = ids.map(function(id, i){
      var evts = bySignalId.get(id).sort(function(a,b){ return a.ts - b.ts; });
      var createEv = evts.find(function(e){ return e.event_kind === "create"; });
      var endEv = evts.find(function(e){ return e.event_kind === "auto_expire" || e.event_kind === "explicit_revoke"; });
      if (!createEv) return '';
      var x0 = x(createEv.ts);
      var x1 = endEv ? x(endEv.ts) : x(toMs);
      var y = PAD_TOP + i * ROW_H + 4;
      var col = createEv.signal_kind === "pause_all" ? "#f87171"
              : createEv.signal_kind === "throttle_global" ? "#fbbf24"
              : createEv.signal_kind === "ban_proxy" ? "#a78bfa"
              : "#38bdf8";
      return '<rect x="' + x0 + '" y="' + y + '" width="' + Math.max(1, x1-x0) + '" height="14" fill="' + col + '" opacity="0.7" />'
        + '<text x="' + (PAD_LEFT - 6) + '" y="' + (y + 11) + '" text-anchor="end" font-size="10" fill="#6e7681">' + esc(id.slice(0, 12)) + '</text>';
    }).join("");

    // Time axis labels: 4 ticks across.
    var ticks = "";
    for (var t = 0; t <= 4; t++){
      var ts = fromMs + (toMs - fromMs) * (t/4);
      var tx = PAD_LEFT + inner * (t/4);
      ticks += '<line x1="' + tx + '" y1="' + (PAD_TOP - 4) + '" x2="' + tx + '" y2="' + (height - 18) + '" stroke="#1f2730" />'
        + '<text x="' + tx + '" y="' + (height - 6) + '" text-anchor="middle" font-size="10" fill="#6e7681">' + esc(fmtTs(ts)) + '</text>';
    }
    return '<svg width="' + width + '" height="' + height + '" style="max-width:100%">' + ticks + bars + '</svg>';
  }
```

- [ ] **Step 4：在 signals 面板 header 上加入「History」按钮**

找到 `<header>Active signals <span class="badge" id="signal-count">0</span></header>`，改为：

```html
<header>
  <span>Active signals <span class="badge" id="signal-count">0</span></span>
  <button class="panel-history-btn" data-drawer="signals">History →</button>
</header>
```

加入 CSS：
```css
  .panel-history-btn { background: transparent; color: var(--muted); border: 1px solid var(--border); border-radius: 4px;
    padding: 2px 8px; font-size: 10px; cursor: pointer; letter-spacing: normal; text-transform: none; }
  .panel-history-btn:hover { color: var(--text); border-color: var(--muted); }
```

通过事件委托接入该按钮。在现有的 `document.addEventListener("click", function(e){ ... })` 块中追加：

```javascript
    var hBtn = e.target.closest && e.target.closest("[data-drawer]");
    if (hBtn) {
      var which = hBtn.getAttribute("data-drawer");
      if (which === "signals") openDrawer("Signals history", signalsDrawerRenderer, {});
    }
```

- [ ] **Step 5：运行测试 + 手动 smoke**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
预期：全部通过。

手动：打开 dashboard，点击 signals 面板上的「History →」，确认 drawer 滑入并渲染出内容（或空状态）。

- [ ] **Step 6：提交**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/dashboard_html.ts test/dashboard_html.test.ts
git commit -m "$(cat <<'EOF'
feat(worker): signals history drill-down with Gantt timeline (Phase 4)

Right-side drawer opens from the signals panel header.
Renders an SVG Gantt timeline (one row per signal, color-coded
by kind) plus a chronological event table. Time range selector
controls the window; default Now = last 60s.
EOF
)"
```

---

## 任务 3：Runners 历史 drill-down（带过滤的事件表）

**文件：**
- 修改：`JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts`

- [ ] **Step 1：编写失败的测试**

追加到 `test/dashboard_html.test.ts`：

```typescript
describe("Phase 4 — runners drill-down", () => {
  const html = renderDashboardHtml(new URL("https://dash.test/dashboard"));

  it("Active runners panel header has a History button", () => {
    expect(html).toMatch(/Active runners[\s\S]*?data-drawer="runners"/);
  });

  it("runners renderer queries /runners/history", () => {
    expect(html).toContain("runnersDrawerRenderer");
    expect(html).toContain("/runners/history?from=");
  });

  it("renders status pill mapping for register/unregister/crashed", () => {
    expect(html).toMatch(/crashed/);
  });
});
```

- [ ] **Step 2：确认失败**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
预期：3 个用例失败。

- [ ] **Step 3：加入 renderer + 按钮**

在 `dashboard_html.ts` 的 `signalsDrawerRenderer` 之后：

```javascript
  // ── Phase 4: runners drill-down ─────────────────────────────────────
  function runnersDrawerRenderer(rangeMs, ctx){
    var body = $("drawer-body");
    body.innerHTML = '<div class="empty">loading…</div>';
    var to = Date.now();
    var from = rangeMs > 0 ? to - rangeMs : to - 5 * 60_000;  // Now → last 5 min

    fetch("/runners/history?from=" + from + "&to=" + to, { credentials: "same-origin" })
      .then(function(r){ return r.json(); })
      .then(function(data){
        var rows = data.rows || [];
        if (rows.length === 0){ body.innerHTML = '<div class="empty">No runner events in this window.</div>'; return; }
        var html = '<table><tr><th>Time</th><th>Event</th><th>Holder</th><th>Workflow</th><th>Status</th></tr>';
        rows.forEach(function(r){
          var pill = r.event_kind === "register" ? '<span class="pill ok">register</span>'
                  : r.event_kind === "unregister" ? '<span class="pill muted">unregister</span>'
                  : r.event_kind === "crashed" ? '<span class="pill bad">crashed</span>'
                  : '<span class="pill muted">' + esc(r.event_kind) + '</span>';
          html += '<tr><td class="muted">' + esc(fmtTs(r.ts)) + '</td>'
            + '<td>' + pill + '</td>'
            + '<td><code>' + esc(r.holder_id) + '</code></td>'
            + '<td class="muted">' + esc(r.workflow_name || "—") + '</td>'
            + '<td class="muted">' + esc(r.final_status || "—") + '</td></tr>';
        });
        html += '</table>';
        body.innerHTML = html;
      })
      .catch(function(err){ body.innerHTML = '<div class="empty">error: ' + esc(err.message) + '</div>'; });
  }
```

更新 runners 面板 header：

```html
<header>
  <span>Active runners <span class="badge" id="runner-count">0</span></span>
  <button class="panel-history-btn" data-drawer="runners">History →</button>
</header>
```

在点击委托中加入（与 signals 那条并列）：

```javascript
      if (which === "runners") openDrawer("Runners history", runnersDrawerRenderer, {});
```

- [ ] **Step 4：运行测试**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
预期：全部通过。

- [ ] **Step 5：提交**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/dashboard_html.ts test/dashboard_html.test.ts
git commit -m "feat(worker): runners history drill-down (Phase 4)"
```

---

## 任务 4：Login 历史 drill-down

**文件：**
- 修改：`JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts`

- [ ] **Step 1：加入 login state 面板**

当前 dashboard 并没有专门的「Login state」面板（login 数据存放在 GlobalLoginState DO 中）。第 4 阶段，我们在网格中、Active signals 与 Per-proxy state 之间加入一个小的 Login 面板，因为 drill-down 需要挂在面板上。

加入网格的标记：

```html
<div class="panel">
  <header>
    <span>Login state <span class="badge" id="login-badge">—</span></span>
    <button class="panel-history-btn" data-drawer="login">History →</button>
  </header>
  <div class="body" id="login-state-body"></div>
</div>
```

至于实时 login 面板 body（实时登录状态），复用某个 snapshot 字段。如果 `/ops/snapshot` 当前不包含 login state，面板可以渲染一个最小提示，指引去 drill-down 查看详情：

```javascript
  function renderLoginState(data){
    var body = $("login-state-body");
    body.innerHTML = '<div class="hint">Click <strong>History →</strong> to view recent login attempts, publishes, and lease activity.</div>';
    $("login-badge").textContent = "—";
  }
```

在 refresh 链路中与 `renderRunners` 等并列调用 `renderLoginState(data)`。

- [ ] **Step 2：编写失败的测试**

追加到 `test/dashboard_html.test.ts`：

```typescript
describe("Phase 4 — login drill-down", () => {
  const html = renderDashboardHtml(new URL("https://dash.test/dashboard"));

  it("has a Login state panel with History button", () => {
    expect(html).toMatch(/Login state[\s\S]*?data-drawer="login"/);
  });

  it("login renderer queries /login/history", () => {
    expect(html).toContain("loginDrawerRenderer");
    expect(html).toContain("/login/history?from=");
  });
});
```

- [ ] **Step 3：确认失败**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
预期：2 个用例失败。

- [ ] **Step 4：加入 renderer + 按钮接线**

```javascript
  // ── Phase 4: login drill-down ───────────────────────────────────────
  function loginDrawerRenderer(rangeMs, ctx){
    var body = $("drawer-body");
    body.innerHTML = '<div class="empty">loading…</div>';
    var to = Date.now();
    var from = rangeMs > 0 ? to - rangeMs : to - 60 * 60_000;  // Now → last 1h

    fetch("/login/history?from=" + from + "&to=" + to, { credentials: "same-origin" })
      .then(function(r){ return r.json(); })
      .then(function(data){
        var rows = data.rows || [];
        if (rows.length === 0){ body.innerHTML = '<div class="empty">No login events in this window.</div>'; return; }

        // Summary chips
        var counts = rows.reduce(function(acc, r){
          acc[r.event_kind] = (acc[r.event_kind] || 0) + 1;
          if (r.event_kind === "attempt"){
            acc[r.outcome] = (acc[r.outcome] || 0) + 1;
          }
          return acc;
        }, {});
        var summary = '<div style="margin-bottom:12px;font-size:12px;color:var(--muted)">'
          + Object.keys(counts).map(function(k){ return '<span class="pill muted" style="margin-right:6px">' + esc(k) + ' ' + counts[k] + '</span>'; }).join("")
          + '</div>';

        var html = summary + '<table><tr><th>Time</th><th>Event</th><th>Outcome</th><th>Holder</th><th>Detail</th></tr>';
        rows.forEach(function(r){
          var outcomePill = r.outcome === "success" ? '<span class="pill ok">success</span>'
                          : r.outcome === "failure" ? '<span class="pill bad">failure</span>'
                          : '<span class="pill muted">—</span>';
          html += '<tr><td class="muted">' + esc(fmtTs(r.ts)) + '</td>'
            + '<td><code>' + esc(r.event_kind) + '</code></td>'
            + '<td>' + outcomePill + '</td>'
            + '<td><code>' + esc(r.holder_id || "—") + '</code></td>'
            + '<td class="muted" style="font-size:11px">' + esc(r.detail || "—") + '</td></tr>';
        });
        html += '</table>';
        body.innerHTML = html;
      })
      .catch(function(err){ body.innerHTML = '<div class="empty">error: ' + esc(err.message) + '</div>'; });
  }
```

在点击委托中加入：
```javascript
      if (which === "login") openDrawer("Login history", loginDrawerRenderer, {});
```

- [ ] **Step 5：运行测试**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
预期：全部通过。

- [ ] **Step 6：提交**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/dashboard_html.ts test/dashboard_html.test.ts
git commit -m "feat(worker): login history drill-down (Phase 4)"
```

---

## 任务 5：Config audit drill-down

**文件：**
- 修改：`JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts`

- [ ] **Step 1：编写失败的测试**

追加到 `test/dashboard_html.test.ts`：

```typescript
describe("Phase 4 — config drill-down", () => {
  const html = renderDashboardHtml(new URL("https://dash.test/dashboard"));

  it("Config snapshot panel has History button", () => {
    expect(html).toMatch(/Config snapshot[\s\S]*?data-drawer="config"/);
  });

  it("config renderer queries /config/history", () => {
    expect(html).toContain("configDrawerRenderer");
    expect(html).toContain("/config/history?from=");
  });
});
```

- [ ] **Step 2：更新 Config 面板 header**

找到 `<header>Config snapshot</header>`，改为：

```html
<header>
  <span>Config snapshot</span>
  <button class="panel-history-btn" data-drawer="config">History →</button>
</header>
```

- [ ] **Step 3：加入 renderer**

```javascript
  // ── Phase 4: config drill-down ──────────────────────────────────────
  function configDrawerRenderer(rangeMs, ctx){
    var body = $("drawer-body");
    body.innerHTML = '<div class="empty">loading…</div>';
    var to = Date.now();
    var from = rangeMs > 0 ? to - rangeMs : 0;  // Now → all-time (config changes are rare)

    fetch("/config/history?from=" + from + "&to=" + to, { credentials: "same-origin" })
      .then(function(r){ return r.json(); })
      .then(function(data){
        var rows = data.rows || [];
        if (rows.length === 0){ body.innerHTML = '<div class="empty">No config changes in this window.</div>'; return; }
        var html = '<table><tr><th>Time</th><th>Key</th><th>Old</th><th>New</th><th>Actor</th><th>Reason</th></tr>';
        rows.forEach(function(r){
          html += '<tr><td class="muted">' + esc(fmtTs(r.ts)) + '</td>'
            + '<td><code>' + esc(r.key) + '</code></td>'
            + '<td class="muted"><code>' + esc(r.old_value === null ? "(none)" : String(r.old_value)) + '</code></td>'
            + '<td><code>' + esc(String(r.new_value)) + '</code></td>'
            + '<td class="muted">' + esc(r.actor || "—") + ' <span class="pill muted" style="font-size:10px">' + esc(r.actor_kind) + '</span></td>'
            + '<td class="muted" style="font-size:11px">' + esc(r.reason || "—") + '</td></tr>';
        });
        html += '</table>';
        body.innerHTML = html;
      })
      .catch(function(err){ body.innerHTML = '<div class="empty">error: ' + esc(err.message) + '</div>'; });
  }
```

在点击委托中加入：
```javascript
      if (which === "config") openDrawer("Config audit", configDrawerRenderer, {});
```

- [ ] **Step 4：运行测试**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
预期：全部通过。

- [ ] **Step 5：提交**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/dashboard_html.ts test/dashboard_html.test.ts
git commit -m "feat(worker): config audit drill-down (Phase 4)"
```

---

## 任务 6：Per-proxy drill-down（图表 3 成功/失败堆叠 + 图表 7 等待时间）

**文件：**
- 修改：`JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts`

- [ ] **Step 1：编写失败的测试**

追加到 `test/dashboard_html.test.ts`：

```typescript
describe("Phase 4 — per-proxy drill-down", () => {
  const html = renderDashboardHtml(new URL("https://dash.test/dashboard"));

  it("per-proxy renderer queries /metrics/range", () => {
    expect(html).toContain("perProxyDrawerRenderer");
    expect(html).toContain("/metrics/range?from=");
  });

  it("clicking a per-proxy table row opens drill-down with that proxy_id", () => {
    expect(html).toContain('data-proxy-row');
  });

  it("renders chart 3 (success/failure stacked) and chart 7 (wait time)", () => {
    expect(html).toContain("renderProxyChartSuccessFailure");
    expect(html).toContain("renderProxyChartWait");
  });
});
```

- [ ] **Step 2：确认失败**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
预期：3 个用例失败。

- [ ] **Step 3：让 per-proxy 表格行可点击**

在 `renderProxies` 的行构造循环中，将每个 `<tr>` 改为包含 `data-proxy-row="ID"`：

```javascript
      html += '<tr data-proxy-row="' + esc(p.proxy_id) + '" style="cursor:pointer">'
        + '<td><code>' + esc(p.proxy_id) + '</code></td>'
        // ... existing cells ...
        + '</tr>';
```

在点击委托中加入：

```javascript
    var pRow = e.target.closest && e.target.closest("[data-proxy-row]");
    if (pRow) {
      var pid = pRow.getAttribute("data-proxy-row");
      openDrawer("Proxy detail — " + pid, perProxyDrawerRenderer, { proxy_id: pid });
    }
```

- [ ] **Step 4：实现 renderer**

```javascript
  // ── Phase 4: per-proxy drill-down ───────────────────────────────────
  function perProxyDrawerRenderer(rangeMs, ctx){
    var body = $("drawer-body");
    body.innerHTML = '<div class="empty">loading…</div>';
    var pid = ctx.proxy_id;
    var to = Date.now();
    var from = rangeMs > 0 ? to - rangeMs : to - 60 * 60_000;  // Now → last 1h

    fetch("/metrics/range?from=" + from + "&to=" + to, { credentials: "same-origin" })
      .then(function(r){ return r.json(); })
      .then(function(data){
        var snapshots = data.rows || [];
        if (snapshots.length === 0){ body.innerHTML = '<div class="empty">No metrics in this window.</div>'; return; }

        body.innerHTML = ''
          + '<div style="margin-bottom:18px">'
          +   '<h4 style="font-size:11px;text-transform:uppercase;color:var(--muted);margin:0 0 6px">Success / Failure cumulative</h4>'
          +   '<div id="proxy-chart-sf" style="height:160px"></div>'
          + '</div>'
          + '<div style="margin-bottom:18px">'
          +   '<h4 style="font-size:11px;text-transform:uppercase;color:var(--muted);margin:0 0 6px">Wait time (ms)</h4>'
          +   '<div id="proxy-chart-wait" style="height:160px"></div>'
          + '</div>';

        renderProxyChartSuccessFailure(snapshots, pid, $("proxy-chart-sf"));
        renderProxyChartWait(snapshots, pid, $("proxy-chart-wait"));
      })
      .catch(function(err){ body.innerHTML = '<div class="empty">error: ' + esc(err.message) + '</div>'; });
  }

  function renderProxyChartSuccessFailure(snapshots, pid, container){
    var ts = snapshots.map(function(s){ return Math.floor(s.ts/1000); });
    var succ = snapshots.map(function(s){
      var p = (s.payload.proxies || []).find(function(x){ return x.proxy_id === pid; });
      return p?.health?.success_count ?? null;
    });
    var fail = snapshots.map(function(s){
      var p = (s.payload.proxies || []).find(function(x){ return x.proxy_id === pid; });
      return p?.health?.failure_count ?? null;
    });
    new uPlot({
      title: "", width: container.clientWidth, height: 160,
      cursor: { drag: { x: false } },
      legend: { show: true },
      scales: { x: { time: true } },
      series: [
        {},
        { label: "success (cum)", stroke: "#4ade80", width: 2, fill: "rgba(74,222,128,0.15)" },
        { label: "failure (cum)", stroke: "#f87171", width: 2, fill: "rgba(248,113,113,0.15)" },
      ],
      axes: [{ stroke: "#6e7681" }, { stroke: "#6e7681" }],
    }, [ts, succ, fail], container);
  }

  function renderProxyChartWait(snapshots, pid, container){
    var ts = snapshots.map(function(s){ return Math.floor(s.ts/1000); });
    var wait = snapshots.map(function(s){
      var p = (s.payload.proxies || []).find(function(x){ return x.proxy_id === pid; });
      if (!p || !p.nextAvailableAt) return 0;
      var w = p.nextAvailableAt - s.ts;
      return w > 0 ? w : 0;
    });
    new uPlot({
      title: "", width: container.clientWidth, height: 160,
      legend: { show: false },
      scales: { x: { time: true } },
      series: [{}, { label: "wait_ms", stroke: "#fbbf24", width: 2, fill: "rgba(251,191,36,0.15)" }],
      axes: [{ stroke: "#6e7681" }, { stroke: "#6e7681" }],
    }, [ts, wait], container);
  }
```

- [ ] **Step 5：运行全部测试**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
预期：全部通过。

- [ ] **Step 6：手动 smoke**

启动 wrangler dev，打开 dashboard，点击 per-proxy 表格中的某一行。验证 drawer 打开，标题为「Proxy detail — <id>」且渲染出两张图表。

- [ ] **Step 7：提交**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/dashboard_html.ts test/dashboard_html.test.ts
git commit -m "$(cat <<'EOF'
feat(worker): per-proxy drill-down with success/failure + wait charts (Phase 4)

Clicking a row in the per-proxy table opens the drawer with two
uPlot charts scoped to that proxy_id: cumulative success/failure
counts and wait_ms (next-available-at minus snapshot time).
EOF
)"
```

---

## 任务 7：第 4 阶段验证 + 部署

**文件：**（无修改）

- [ ] **Step 1：运行全部测试**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx vitest --run`
预期：全部通过。

- [ ] **Step 2：类型检查**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx tsc --noEmit`
预期：零错误。

- [ ] **Step 3：部署 dry-run**

执行：`cd JAVDB_AutoSpider_Proxycoordinator && npx wrangler deploy --dry-run --outdir /tmp/wrangler-phase4 2>&1 | tail -10`
预期：构建成功；bundle 大小仍然远低于 10 MB。

- [ ] **Step 4：端到端可视化验证**

启动 wrangler dev，登录 dashboard。验证：
- 5 个面板 header 都有「History →」按钮（Active runners / Active signals / Login state / Per-proxy state / Config snapshot）
- 点击每一个都能打开 drawer，标题与内容正确（无数据时显示空状态）
- 时间范围选择器能切换数据窗口
- ESC 可关闭；点击 backdrop 可关闭；X 按钮可关闭
- 点击 per-proxy 表格中的某一行能打开 per-proxy drill-down
- 经过若干次 register/lease/report 周期后，per-proxy drill-down 中的图表能渲染出来

- [ ] **Step 5：第 4 阶段交接说明**

第 4 阶段完成了整个 dashboard 改造。部署完成后，operator 拥有：
- 1 个主视图 —— 实时统计 + 5 张优先图表（第 3 阶段）
- 5 个 drill-down 面板，每个都有自己独立的时间范围选择器与历史渲染（第 4 阶段）
- 所有时间序列数据均落盘并保留 30 天（指标），加上事件日志按差异化策略保留（第 2 阶段）
- 完整的 proxy 枚举，包括空闲备用 proxy（第 1 阶段 + 第 2 阶段）

遗留的后续工作（不在本次 dashboard 改造范围内）：
- 优先级清单之外、未纳入的 3 项较低优先级图表（per-proxy 延迟 drill-down 详情 / signals 时间线回放缩放）
- 自动清理已退役 proxy 的 `proxies_seen`（目前需要手动删除）
- 从 drill-down 表格导出为 CSV

---

## 自审清单

- ✅ Drawer shell 在 5 个 drill-down 之间共享
- ✅ 7 个时间范围选项 + 默认 Now + 每次打开都重置
- ✅ Signals drill-down：Gantt 时间线 + 事件表
- ✅ Runners drill-down：带 kind pill 的事件表
- ✅ Login drill-down：摘要 chips + 事件表
- ✅ Config drill-down：含 actor 与 reason 的变更审计表
- ✅ Per-proxy drill-down：2 张 uPlot 图表（success/failure + 等待时间）
- ✅ ESC / backdrop / 按钮均可关闭 drawer
- ✅ 无新增外部依赖
- ✅ 每个 drawer 都是独立的 —— 任务 2-6 可以逐个上线
