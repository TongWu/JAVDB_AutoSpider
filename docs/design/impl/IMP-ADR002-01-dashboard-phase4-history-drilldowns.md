# IMP-ADR002-01: Dashboard Overhaul — Phase 4: History Drill-Down Drawers

**Status**: Accepted — Planned
**Date**: 2026-05-16
**Deciders**: Proxy Coordinator Dashboard rewrite working stream
**Related**: implements [ADR-002](../adr/archive/ADR-002-observability-data-storage-topology.md); requires [IMP-ADR003-02](IMP-ADR003-02-dashboard-phase3-ui.md) deployed; final phase of the dashboard rewrite

> **Note on format:** This file is an **implementation plan** — written by the writing-plans workflow, not a design document. It records HOW to execute the related design decisions (see **Related** above). The preamble (Goal / Architecture / Tech Stack) frames the work; the body is the step-by-step execution checklist. English-only by repo convention.
>
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a right-side slide-in drawer (~40% viewport width) that opens from a "History" button on each panel header. The drawer carries a time-range selector (7 options) and one of 5 history views: signals, runners, login, config audit, per-proxy detail. Each drill-down can be developed and merged independently — each task in this plan is one drawer.

**Architecture:** A single drawer shell (overlay + slide-in panel + close button + time-range selector) is shared across all 5 drill-downs; the body content is plugged in by passing a `renderer` function. Each renderer fetches one of the Phase 2 history endpoints (`/signals/history`, `/runners/history`, `/login/history`, `/config/history`, or `/metrics/range` filtered by proxy_id). Per ADR-002 + grill-me Q5a/Q6, the drawer always opens at "Now" time-range (state is NOT persisted across opens).

**Tech Stack:** Same as Phase 3 — vanilla JS, vendored uPlot, inline SVG. No new dependencies.

**Reference docs:** [ADR-002](../adr/archive/ADR-002-observability-data-storage-topology.md), [CONTEXT.md](../../../CONTEXT.md). User-facing decisions: grill-me Q5a (γ drawer mode), Q5d (chart locations), Q6a (drawer shell).

**Prerequisite:** Phase 3 deployed; main dashboard renders the new chart row; 5 history endpoints from Phase 2 are populated and reachable.

---

## File Structure

**New files:**
- `JAVDB_AutoSpider_Proxycoordinator/src/drill_down_renderers.ts` — exports 5 small renderer functions, each producing the inner HTML of one drawer body. Server-side rendered so they ship with the dashboard.

**Modified files:**
- `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts` — add drawer shell markup + JS state machine; render "History" buttons on the 5 panel headers; wire renderer dispatch.
- `JAVDB_AutoSpider_Proxycoordinator/test/dashboard_html.test.ts` — extend with Phase 4 cases.

---

## Task 1: Drawer shell — overlay, slide-in, close, time range selector

**Files:**
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts`
- Test: `JAVDB_AutoSpider_Proxycoordinator/test/dashboard_html.test.ts` (extend)

- [ ] **Step 1: Write the failing test**

Append to `test/dashboard_html.test.ts`:

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

- [ ] **Step 2: Verify failure**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
Expected: 4 failures from new cases.

- [ ] **Step 3: Add drawer markup right before `</body>`**

In `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts`, just before the closing `</body>`:

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

Add CSS:

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

Add JS state machine. After the existing chart code, before `refresh()`, add:

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

- [ ] **Step 4: Run tests**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
Expected: all pass.

- [ ] **Step 5: Manual smoke**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx wrangler dev --local`

In browser console at <http://127.0.0.1:8787/>:
```js
openDrawer("Test", function(){ document.getElementById("drawer-body").innerHTML = '<p>hello drawer</p>'; }, {});
```
Verify: drawer slides in from right; ESC closes; backdrop click closes; close button works.

- [ ] **Step 6: Commit**

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

## Task 2: Signals history drill-down (Gantt timeline)

**Files:**
- Create: `JAVDB_AutoSpider_Proxycoordinator/src/drill_down_renderers.ts`
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts` (signals panel header + wire renderer)
- Test: extend `test/dashboard_html.test.ts`

- [ ] **Step 1: Write the failing test**

Append to `test/dashboard_html.test.ts`:

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

- [ ] **Step 2: Verify failure**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
Expected: 3 failures.

- [ ] **Step 3: Add the renderer function in dashboard_html.ts**

After the drawer state machine, before `refresh()`:

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

- [ ] **Step 4: Add "History" button to signals panel header**

Find `<header>Active signals <span class="badge" id="signal-count">0</span></header>` and change to:

```html
<header>
  <span>Active signals <span class="badge" id="signal-count">0</span></span>
  <button class="panel-history-btn" data-drawer="signals">History →</button>
</header>
```

Add CSS:
```css
  .panel-history-btn { background: transparent; color: var(--muted); border: 1px solid var(--border); border-radius: 4px;
    padding: 2px 8px; font-size: 10px; cursor: pointer; letter-spacing: normal; text-transform: none; }
  .panel-history-btn:hover { color: var(--text); border-color: var(--muted); }
```

Wire the button via event delegation. Append to the existing `document.addEventListener("click", function(e){ ... })` block:

```javascript
    var hBtn = e.target.closest && e.target.closest("[data-drawer]");
    if (hBtn) {
      var which = hBtn.getAttribute("data-drawer");
      if (which === "signals") openDrawer("Signals history", signalsDrawerRenderer, {});
    }
```

- [ ] **Step 5: Run tests + manual smoke**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
Expected: all pass.

Manual: open dashboard, click "History →" on the signals panel, verify the drawer slides in and renders some content (or an empty state).

- [ ] **Step 6: Commit**

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

## Task 3: Runners history drill-down (event table with filters)

**Files:**
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts`

- [ ] **Step 1: Write the failing test**

Append to `test/dashboard_html.test.ts`:

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

- [ ] **Step 2: Verify failure**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
Expected: 3 failures.

- [ ] **Step 3: Add renderer + button**

In `dashboard_html.ts`, after `signalsDrawerRenderer`:

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

Update the runners panel header:

```html
<header>
  <span>Active runners <span class="badge" id="runner-count">0</span></span>
  <button class="panel-history-btn" data-drawer="runners">History →</button>
</header>
```

Add to the click delegator (alongside the signals one):

```javascript
      if (which === "runners") openDrawer("Runners history", runnersDrawerRenderer, {});
```

- [ ] **Step 4: Run tests**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/dashboard_html.ts test/dashboard_html.test.ts
git commit -m "feat(worker): runners history drill-down (Phase 4)"
```

---

## Task 4: Login history drill-down

**Files:**
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts`

- [ ] **Step 1: Add panel for login state**

The current dashboard does not have a dedicated "Login state" panel (login data lives in the GlobalLoginState DO). For Phase 4 we add a small Login panel between Active signals and Per-proxy state in the grid, since drill-down attaches to a panel.

Add to the grid markup:

```html
<div class="panel">
  <header>
    <span>Login state <span class="badge" id="login-badge">—</span></span>
    <button class="panel-history-btn" data-drawer="login">History →</button>
  </header>
  <div class="body" id="login-state-body"></div>
</div>
```

For the live login panel body (real-time login status), reuse a snapshot field. If `/ops/snapshot` does not currently include login state, the panel can render a minimal hint pointing to drill-down for details:

```javascript
  function renderLoginState(data){
    var body = $("login-state-body");
    body.innerHTML = '<div class="hint">Click <strong>History →</strong> to view recent login attempts, publishes, and lease activity.</div>';
    $("login-badge").textContent = "—";
  }
```

Call `renderLoginState(data)` inside the refresh chain alongside `renderRunners`, etc.

- [ ] **Step 2: Write the failing test**

Append to `test/dashboard_html.test.ts`:

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

- [ ] **Step 3: Verify failure**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
Expected: 2 failures.

- [ ] **Step 4: Add the renderer + button wiring**

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

Add to the click delegator:
```javascript
      if (which === "login") openDrawer("Login history", loginDrawerRenderer, {});
```

- [ ] **Step 5: Run tests**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/dashboard_html.ts test/dashboard_html.test.ts
git commit -m "feat(worker): login history drill-down (Phase 4)"
```

---

## Task 5: Config audit drill-down

**Files:**
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts`

- [ ] **Step 1: Write the failing test**

Append to `test/dashboard_html.test.ts`:

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

- [ ] **Step 2: Update Config panel header**

Find `<header>Config snapshot</header>` and change to:

```html
<header>
  <span>Config snapshot</span>
  <button class="panel-history-btn" data-drawer="config">History →</button>
</header>
```

- [ ] **Step 3: Add the renderer**

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

Add to click delegator:
```javascript
      if (which === "config") openDrawer("Config audit", configDrawerRenderer, {});
```

- [ ] **Step 4: Run tests**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/dashboard_html.ts test/dashboard_html.test.ts
git commit -m "feat(worker): config audit drill-down (Phase 4)"
```

---

## Task 6: Per-proxy drill-down (charts 3 success/failure stacked + chart 7 wait time)

**Files:**
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts`

- [ ] **Step 1: Write the failing test**

Append to `test/dashboard_html.test.ts`:

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

- [ ] **Step 2: Verify failure**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
Expected: 3 failures.

- [ ] **Step 3: Make per-proxy table rows clickable**

In `renderProxies`, in the row-construction loop, change each `<tr>` to include `data-proxy-row="ID"`:

```javascript
      html += '<tr data-proxy-row="' + esc(p.proxy_id) + '" style="cursor:pointer">'
        + '<td><code>' + esc(p.proxy_id) + '</code></td>'
        // ... existing cells ...
        + '</tr>';
```

Add to the click delegator:

```javascript
    var pRow = e.target.closest && e.target.closest("[data-proxy-row]");
    if (pRow) {
      var pid = pRow.getAttribute("data-proxy-row");
      openDrawer("Proxy detail — " + pid, perProxyDrawerRenderer, { proxy_id: pid });
    }
```

- [ ] **Step 4: Implement the renderer**

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

- [ ] **Step 5: Run all tests**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
Expected: all pass.

- [ ] **Step 6: Manual smoke**

Run wrangler dev, open dashboard, click a proxy row in the per-proxy table. Verify drawer opens with "Proxy detail — <id>" title and two charts.

- [ ] **Step 7: Commit**

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

## Task 7: Phase 4 verification + deploy

**Files:** (none modified)

- [ ] **Step 1: Run all tests**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest --run`
Expected: all pass.

- [ ] **Step 2: Type check**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx tsc --noEmit`
Expected: zero errors.

- [ ] **Step 3: Deploy dry-run**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx wrangler deploy --dry-run --outdir /tmp/wrangler-phase4 2>&1 | tail -10`
Expected: built successfully; bundle size still comfortably under 10 MB.

- [ ] **Step 4: End-to-end visual verification**

Run wrangler dev, log in to dashboard. Verify:
- 5 panel headers have "History →" button (Active runners / Active signals / Login state / Per-proxy state / Config snapshot)
- Clicking each opens the drawer with correct title and content (or empty state when no data)
- Time-range selector switches data window
- ESC closes; backdrop click closes; X button closes
- Clicking a row in per-proxy table opens per-proxy drill-down
- Charts in per-proxy drill-down render after a few register/lease/report cycles

- [ ] **Step 5: Phase 4 handoff note**

Phase 4 completes the dashboard overhaul. After deploy, the operator has:
- 1 main view with real-time stats + 5 priority charts (Phase 3)
- 5 drill-down panels each with its own time-range selector and history rendering (Phase 4)
- All time-series data persisted with 30 day retention (metrics) plus differentiated event-log retention (Phase 2)
- Full proxy enumeration including idle backup proxies (Phase 1 + Phase 2)

Open follow-ups (not in scope for the dashboard overhaul):
- Charts for the 3 lower-priority items not in the priority list (per-proxy latency drill-down detail / signals timeline replay zoom)
- Auto-prune of `proxies_seen` for retired proxies (currently manual delete)
- Export to CSV from drill-down tables

---

## Self-Review Checklist

- ✅ Drawer shell shared across all 5 drill-downs
- ✅ 7 time-range options + Now default + reset-per-open
- ✅ Signals drill-down: Gantt timeline + event table
- ✅ Runners drill-down: event table with kind pills
- ✅ Login drill-down: summary chips + event table
- ✅ Config drill-down: change audit table with actor + reason
- ✅ Per-proxy drill-down: 2 uPlot charts (success/failure + wait time)
- ✅ ESC / backdrop / button all close the drawer
- ✅ No new external dependencies
- ✅ Each drawer is independent — tasks 2-6 can ship one at a time

