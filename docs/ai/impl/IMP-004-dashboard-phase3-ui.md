# IMP-004: Dashboard Overhaul — Phase 3: Main Dashboard UI

**Status**: Accepted — Planned
**Date**: 2026-05-16
**Deciders**: Proxy Coordinator Dashboard rewrite working stream
**Related**: implements [ADR-003](../adr/ADR-003-metrics-pipeline.md); requires [IMP-003](IMP-003-dashboard-phase2-worker-backend.md) deployed; prerequisite for [IMP-005](IMP-005-dashboard-phase4-history-drilldowns.md)

> **Note on format:** This file is an **implementation plan** — written by the writing-plans workflow, not a design document. It records HOW to execute the related design decisions (see **Related** above). The preamble (Goal / Architecture / Tech Stack) frames the work; the body is the step-by-step execution checklist. English-only by repo convention.
>
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the dashboard's main view: visibility-aware polling, browser-local timezone with tz abbreviation, hover tooltips with complementary time format, auto-discovered per-proxy list with chip filter, ConfigState always shows merged config, and 5 priority charts (active runners trend, queue depth, CF-bypass ratio donut, per-proxy latency multi-line, per-proxy health-score multi-line).

**Architecture:** Replace the inline server-rendered HTML in `renderDashboardHtml()` with an updated version that inlines uPlot (~14KB minified) and a small custom donut renderer. Polling switches from a fixed 30s `setInterval` to a Page Visibility API state machine (5s visible / 30s hidden / pause after 30 min hidden). All filter and time-range state persists in `localStorage`. **No drill-down work in Phase 3** — that's Phase 4. After Phase 3 the main view is the new design; drill-down panels still slot in cleanly in Phase 4.

**Tech Stack:** Vanilla JavaScript (no build step), uPlot v1.6+ (vendored as a TS string constant), inline SVG for donut chart.

**Reference docs:** [ADR-003](../../ai/adr/ADR-003-metrics-pipeline.md), [CONTEXT.md](../../../CONTEXT.md). User-facing visual decisions captured in `grill-me` Q1-Q6 (5 Apr 2026 conversation).

**Prerequisite:** Phase 2 deployed; `/ops/snapshot` auto-discovers proxies; `/metrics/range` and history endpoints are live.

---

## File Structure

**New files:**
- `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts` — split out from `index.ts`; export `renderDashboardHtml(url: URL): string`
- `JAVDB_AutoSpider_Proxycoordinator/src/uplot_vendor.ts` — exports `UPLOT_MIN_JS: string` (the minified library) and `UPLOT_MIN_CSS: string`
- `JAVDB_AutoSpider_Proxycoordinator/test/dashboard_html.test.ts` — server-side tests for the rendered HTML

**Modified files:**
- `JAVDB_AutoSpider_Proxycoordinator/src/index.ts` — import `renderDashboardHtml` from the new module (the function moves out wholesale)
- `JAVDB_AutoSpider_Proxycoordinator/test/dashboard.test.ts` — extend with Phase 3 behavioural checks

Why split: the existing `renderDashboardHtml` is already ~300 lines of template string; adding 5 charts + visibility polling + chip filter doubles that. A separate file keeps `index.ts` focused on routing.

---

## Task 1: Vendor uPlot — inline the minified library

**Files:**
- Create: `JAVDB_AutoSpider_Proxycoordinator/src/uplot_vendor.ts`

- [ ] **Step 1: Download uPlot v1.6.31 (or latest stable)**

Run:
```bash
mkdir -p /tmp/uplot-vendor
curl -sSL https://cdn.jsdelivr.net/npm/uplot@1.6.31/dist/uPlot.iife.min.js -o /tmp/uplot-vendor/uplot.min.js
curl -sSL https://cdn.jsdelivr.net/npm/uplot@1.6.31/dist/uPlot.min.css -o /tmp/uplot-vendor/uplot.min.css
ls -l /tmp/uplot-vendor/
```
Expected: two files, JS ~50KB raw / ~14KB gzip, CSS ~3KB.

- [ ] **Step 2: Convert into a TS module with backtick string export**

Create `JAVDB_AutoSpider_Proxycoordinator/src/uplot_vendor.ts`. Read the contents of the two files and embed them inside backtick template literals. Use a small node script:

Run:
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

Verify with: `wc -l JAVDB_AutoSpider_Proxycoordinator/src/uplot_vendor.ts` → ~3 long lines + boilerplate.

- [ ] **Step 3: Sanity-check the vendor file**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx tsc --noEmit src/uplot_vendor.ts`
Expected: zero errors. The file should parse as plain TypeScript.

- [ ] **Step 4: Commit**

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

## Task 2: Move `renderDashboardHtml` and helpers to a new module

**Files:**
- Create: `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts`
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/index.ts` (delete the inline function; import from new module)

- [ ] **Step 1: Copy current `renderDashboardHtml`, `commonDashboardStyles`, `escapeHtmlForServer` to the new file**

Create `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts` and paste the three functions verbatim from `index.ts` (currently lines ~1361-1702). Export `renderDashboardHtml`, keep the helpers as module-local. Add at the top:

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

- [ ] **Step 2: Modify `index.ts` to import the function**

In `JAVDB_AutoSpider_Proxycoordinator/src/index.ts`:
- Delete the three function definitions (`renderDashboardHtml`, `commonDashboardStyles`, `escapeHtmlForServer`)
- Add at the top, after other imports:
  ```typescript
  import { renderDashboardHtml } from "./dashboard_html";
  ```

- [ ] **Step 3: Run all existing tests to confirm no behavioural regression**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest --run`
Expected: all pass (the dashboard renders identically; only the source location moved).

- [ ] **Step 4: Commit**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/dashboard_html.ts src/index.ts
git commit -m "refactor(worker): extract renderDashboardHtml to its own module (Phase 3)"
```

---

## Task 3: Replace polling — visibility-aware state machine

**Files:**
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts` (the inline `<script>` tag)
- Test: `JAVDB_AutoSpider_Proxycoordinator/test/dashboard_html.test.ts` (new)

- [ ] **Step 1: Write the failing test for visibility-aware constants**

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

- [ ] **Step 2: Verify the test fails**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
Expected: 3 failures.

- [ ] **Step 3: Replace the polling block in the inline script**

In `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts`, locate the IIFE at the bottom of `renderDashboardHtml`. Replace the entire polling section (currently `var REFRESH_MS = 30000;` + `refresh(); setInterval(refresh, REFRESH_MS);`) with:

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

Modify `refresh()` to return a Promise (currently it does `.then(...).catch(...)`):

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

Also remove the old `var PROXY_IDS = ${proxyIdsJs};` block — proxy_ids no longer comes from URL.

- [ ] **Step 4: Run the tests**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
Expected: 3 passed.

- [ ] **Step 5: Commit**

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

## Task 4: Browser-local timezone with abbreviation + hover tooltips

**Files:**
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts`
- Test: extend `test/dashboard_html.test.ts`

- [ ] **Step 1: Write the failing test**

Append to `test/dashboard_html.test.ts`:

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

- [ ] **Step 2: Verify failure**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
Expected: 3 failures from the new tests.

- [ ] **Step 3: Replace `fmtTs` and friends**

In `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts`, find the current `fmtTs` / `fmtAge` block and replace with:

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

In the runners and signals renderers, where you currently render `fmtAge(...) + " ago"` or `fmtTs(...)`, wrap in `<span title="...complementary format...">`:

For relative→absolute hover (e.g., heartbeat age):
```javascript
var absTs = fmtTs(r.last_heartbeat);
var relAge = fmtAge(r.last_heartbeat, nowMs) + " ago";
var cell = '<span title="' + esc(absTs) + '">' + esc(relAge) + '</span>';
```

For absolute→relative hover (the topbar `#ts`):
```javascript
// at the topbar update site:
var tsAbs = fmtTs(nowMs);
var tsRel = fmtAge(nowMs, Date.now());
$("ts").innerHTML = '<span title="' + esc(tsRel + " ago") + '">' + esc(tsAbs) + '</span>';
```

- [ ] **Step 4: Run the tests**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/dashboard_html.ts test/dashboard_html.test.ts
git commit -m "feat(worker): browser-local timezone with abbr + hover tooltips (Phase 3)"
```

---

## Task 5: ConfigState always shows merged config (defaults + overrides)

**Files:**
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts` (`renderConfig`)
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/config_state.ts` (snapshot endpoint to return both `defaults` and `values`)
- Test: extend `test/dashboard_html.test.ts`

- [ ] **Step 1: Inspect current `/do/config` response shape**

Run: `grep -n "GET\|/do/config\|values\|defaults" JAVDB_AutoSpider_Proxycoordinator/src/config_state.ts | head -30`

The current `ConfigSnapshot` likely returns `{ version, values, server_time }` where `values` is the override-overlay. Phase 3 needs the **merged effective view** — i.e., for every known key, the current value (override or default) plus a flag of which it is.

- [ ] **Step 2: Write the failing test (server-side: response shape)**

Append to a new file `JAVDB_AutoSpider_Proxycoordinator/test/config_snapshot_shape.test.ts`:

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

- [ ] **Step 3: Modify `ConfigState` to return `merged`**

In `JAVDB_AutoSpider_Proxycoordinator/src/config_state.ts`, find the GET `/do/config` handler. After computing the existing `values` overlay, also compute the merged view:

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

Adjust variable names to whatever `config_state.ts` actually uses.

- [ ] **Step 4: Add the failing test for the UI**

Append to `test/dashboard_html.test.ts`:

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

- [ ] **Step 5: Update `renderConfig` in `dashboard_html.ts`**

Replace the existing function with:

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

- [ ] **Step 6: Run tests**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/config_snapshot_shape.test.ts test/dashboard_html.test.ts --run`
Expected: all pass.

- [ ] **Step 7: Commit**

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

## Task 6: Per-proxy chip filter + auto-enumerated proxy list

**Files:**
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts` (`renderProxies` + new chip UI)
- Test: extend `test/dashboard_html.test.ts`

- [ ] **Step 1: Write the failing test**

Append to `test/dashboard_html.test.ts`:

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

- [ ] **Step 2: Verify failure**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
Expected: 3 failures.

- [ ] **Step 3: Add chip-filter markup + behaviour**

In `dashboard_html.ts`, modify the proxies panel `<div class="panel full">`:

Existing:
```html
<div class="panel full">
  <header>Per-proxy state <span class="badge" id="proxy-count">0</span></header>
  <div class="body" id="proxies"></div>
</div>
```

Replace with:
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

Add CSS to the existing `<style>` block:
```css
  .chip-btn { background: var(--input-bg); color: var(--muted); border: 1px solid var(--border); border-radius: 4px; padding: 1px 8px; cursor: pointer; font-size: 10px; }
  .chip-btn:hover { color: var(--text); }
  .chip { display: inline-block; padding: 2px 10px; margin: 2px; font-size: 11px; border-radius: 999px; cursor: pointer; background: var(--input-bg); color: var(--muted); border: 1px solid var(--border); user-select: none; transition: all .12s; }
  .chip.active { background: var(--accent-dim); color: #0a0e14; border-color: var(--accent); }
  .chip .dot { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; vertical-align:middle; }
```

In the inline `<script>`, add:
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

In `renderProxies`, filter out excluded proxies before rendering the table:
```javascript
  function renderProxies(data){
    renderProxyChips(data);
    var allRows = data.proxies || [];
    var rows = allRows.filter(function(p){ return !proxyFilter.has(p.proxy_id); });
    $("proxy-count").textContent = rows.length + " / " + allRows.length;
    // ... rest of existing renderProxies unchanged (the table render of `rows`)
  }
```

Also remove the old "No proxies queried. Append ?proxy_ids=..." hint — Phase 2 auto-discovery means this state should rarely happen; when it does, show "no proxies seen yet — first runner register will populate this list":
```javascript
    if (allRows.length === 0) {
      $("proxies").innerHTML = '<div class="hint">No proxies seen yet — the first runner register (with proxy_pool payload) will populate this list automatically.</div>';
      return;
    }
```

- [ ] **Step 4: Run tests**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
Expected: all pass.

- [ ] **Step 5: Commit**

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

## Task 7: Add uPlot CSS + global chart row scaffolding

**Files:**
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts`

- [ ] **Step 1: Inject uPlot CSS into `<style>`**

In `dashboard_html.ts`, at the very top of the `<style>...${commonDashboardStyles()}...</style>` block, inject `${UPLOT_MIN_CSS}`. Place it BEFORE the existing rules so app rules can override uPlot defaults if needed:

```typescript
return `<!doctype html>
<html lang="en"><head>...
<style>${UPLOT_MIN_CSS}
${commonDashboardStyles()}
  // ... existing rules
```

- [ ] **Step 2: Inline uPlot JS at the end of `<body>` BEFORE the dashboard IIFE**

```typescript
<script>${UPLOT_MIN_JS}</script>
<script>
(function(){
  // ... existing IIFE
})();
</script>
```

- [ ] **Step 3: Add markup for 5 chart slots**

After the topbar `<div class="topbar">...</div>` and before `<main>`, structure the existing main content + add a new chart row. Modify `<main>...</main>` to include a charts row BEFORE the `<div class="grid">` panels:

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

Add CSS:
```css
  .charts { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-bottom: 22px; }
  @media (max-width: 1100px) { .charts { grid-template-columns: 1fr 1fr; } }
  @media (max-width: 700px) { .charts { grid-template-columns: 1fr; } }
  .chart-panel .chart-body { padding: 8px 12px 12px; min-height: 180px; }
  .chart-panel header { font-size: 11px; }
```

- [ ] **Step 4: Run existing tests to confirm no breakage**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest --run`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
cd JAVDB_AutoSpider_Proxycoordinator
git add src/dashboard_html.ts
git commit -m "feat(worker): inline uPlot + chart slot scaffolding (Phase 3)"
```

---

## Task 8: Implement the 5 charts — fetch history + render

**Files:**
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts`
- Test: extend `test/dashboard_html.test.ts`

- [ ] **Step 1: Write the test asserting all 5 chart renderers exist**

Append to `test/dashboard_html.test.ts`:

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

- [ ] **Step 2: Verify failure**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest test/dashboard_html.test.ts --run`
Expected: 4 failures.

- [ ] **Step 3: Add chart fetch + 4 uPlot renderers + 1 SVG donut**

In the inline `<script>` of `dashboard_html.ts`, after the existing helper section and before `refresh()`, add:

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

Modify `refresh()` to fetch metrics in addition to /ops/snapshot, and call the renderers:

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

- [ ] **Step 4: Run all tests**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest --run`
Expected: all pass.

- [ ] **Step 5: Manual visual verification**

Run the worker locally:
```bash
cd JAVDB_AutoSpider_Proxycoordinator && npx wrangler dev --local
```

In another shell, simulate a few runner registers + signals + a cron tick to seed data:
```bash
TOKEN=$(grep PROXY_COORDINATOR_TOKEN .dev.vars | cut -d= -f2)
curl -s -X POST http://127.0.0.1:8787/register -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"holder_id":"vis-1","workflow_name":"DailyIngestion","proxy_pool":[{"id":"V-P1","name":"V-P1"},{"id":"V-P2","name":"V-P2"}]}'
curl -s -X POST http://127.0.0.1:8787/signal -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{"kind":"throttle_global","ttl_ms":600000,"factor":1.5,"reason":"manual viz test"}'
```

Open <http://127.0.0.1:8787/> in a browser. After dashboard login, you should see:
- 5 chart panels at the top (some empty due to limited data, but rendered)
- "Live runners" stat updates within 5s
- Chip filter at the top of per-proxy panel
- Times rendered as "HH:MM:SS SGT" (or whatever your local tz is)
- Hover on a time field shows the complementary format

- [ ] **Step 6: Commit**

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

## Task 9: Phase 3 verification + deploy dry run

**Files:** (none modified)

- [ ] **Step 1: Run all tests**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx vitest --run`
Expected: all pass.

- [ ] **Step 2: TypeScript check**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx tsc --noEmit`
Expected: zero errors.

- [ ] **Step 3: Deploy dry-run**

Run: `cd JAVDB_AutoSpider_Proxycoordinator && npx wrangler deploy --dry-run --outdir /tmp/wrangler-phase3 2>&1 | tail -10`
Expected: built successfully.

- [ ] **Step 4: Bundle-size check**

Run: `du -sh /tmp/wrangler-phase3/`
Expected: bundle is comfortably under the 10MB Worker limit (uPlot adds ~50KB raw, dashboard HTML grows ~10KB).

- [ ] **Step 5: Phase 3 handoff note**

Phase 3 is the largest visible change. After deploy:
- Operators see new charts, chip filter, browser-local time, faster refresh
- The drill-down buttons on each panel header are added in Phase 4

See `docs/superpowers/plans/2026-05-16-dashboard-overhaul-phase-4-history-drilldowns.md`.

---

## Self-Review Checklist

- ✅ uPlot vendored inline (no CDN)
- ✅ Visibility-aware polling: 5s/30s/pause after 30min
- ✅ Browser-local tz + tz abbr; hover tooltip with complementary format
- ✅ Config panel always shows merged config; overrides flagged
- ✅ Per-proxy auto-enumerated; chip filter; localStorage persistence
- ✅ 5 priority charts implemented and tested
- ✅ No drill-down work (Phase 4 territory)
- ✅ All existing tests still pass
