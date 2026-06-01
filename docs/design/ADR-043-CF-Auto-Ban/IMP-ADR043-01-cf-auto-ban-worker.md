# IMP-ADR043-01: Worker-Side CF Auto-Ban Escalation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Related:** [ADR-043](ADR-043-cf-persistent-failure-auto-ban.md) (D1–D7). Phase 2 is [IMP-ADR043-02](IMP-ADR043-02-bfr009-ban-dispatch-and-hardban-ttl.md).

**Goal:** Make the proxy-coordinator Durable Object auto-ban a proxy for a short TTL (default 6 h) when it accumulates ≥ N CF events with zero successes inside the penalty window, so a persistently-CF-walled proxy is dropped from rotation across all runners.

**Architecture:** Pure Worker-side change. The DO already aggregates per-proxy `cfEvents[]` and `successEvents[]` (pruned to `penaltyWindowSec`) and already exposes `banned` / `banned_until` on `/do/lease`, which every runner mirrors into local state. We add one escalation check in `handleReport`'s CF branch plus three env-tunable knobs. No Python/Rust change; independent of BFR-009.

**Tech Stack:** TypeScript, Cloudflare Workers + Durable Objects, `@cloudflare/vitest-pool-workers` (vitest).

**Repos / working directory:**
- **All code tasks (1–4)** run in the **coordinator repo** [`TongWu/JAVDB_AutoSpider_Proxycoordinator`](https://github.com/TongWu/JAVDB_AutoSpider_Proxycoordinator). Clone it and `npm install` first. Paths below are relative to that repo root.
- **Task 5 (docs)** runs in the **CICD repo** (this repo). Paths are relative to its root.

---

## File Structure

| File (repo) | Responsibility | Change |
| --- | --- | --- |
| `src/types.ts` (coordinator) | Env shape + default constants | Add 3 `Env` fields + 3 `DEFAULT_CF_*` constants |
| `src/proxy_coordinator.ts` (coordinator) | DO logic | Add 3 env loaders, `bannedReason` field plumbing, `maybeCfAutoBan()`, wire into `handleReport` |
| `wrangler.toml` (coordinator) | Deploy config | Add 3 `[vars]` |
| `test/cf_auto_ban.test.ts` (coordinator) | New test file | Loader unit tests + escalation e2e tests |
| `docs/handbook/{en,zh}/self-hoster/proxy-coordinator.md` (CICD) | Operator env reference | Document the 3 new env vars |

---

## Task 1: Env knobs — constants, `Env` fields, exported loaders

**Files:**
- Modify: `src/types.ts` (add constants + `Env` fields, near `DEFAULT_BAN_TTL_MS` / `BAN_TTL_MS`)
- Modify: `src/proxy_coordinator.ts` (add loaders near `loadBanTtlMs`)
- Test: `test/cf_auto_ban.test.ts`

- [ ] **Step 1: Add constants + Env fields in `src/types.ts`**

After the existing `export const DEFAULT_BAN_TTL_MS = 3 * 24 * 60 * 60 * 1000;` (line ~113) add:

```ts
// ADR-043 D2/D3/D6 — CF auto-ban escalation knobs.
export const DEFAULT_CF_AUTO_BAN_ENABLED = true;
export const DEFAULT_CF_AUTO_BAN_THRESHOLD = 6;            // > top penalty tier (4)
export const DEFAULT_CF_BAN_TTL_MS = 6 * 60 * 60 * 1000;   // 21_600_000 (6 h)
```

In the `export interface Env { ... }` block, alongside `BAN_TTL_MS?: string;`, add:

```ts
  /** ADR-043 — master switch for CF auto-ban. "false"/"0" disables; default true. */
  CF_AUTO_BAN_ENABLED?: string;
  /** ADR-043 — CF events in the penalty window before auto-banning. Default 6. */
  CF_AUTO_BAN_THRESHOLD?: string;
  /** ADR-043 — CF auto-ban TTL in ms. Default 21_600_000 (6 h). */
  CF_BAN_TTL_MS?: string;
```

- [ ] **Step 2: Add exported loaders in `src/proxy_coordinator.ts`**

Import the new constants in the existing `import { ... } from "./types";` block:
`DEFAULT_CF_AUTO_BAN_ENABLED, DEFAULT_CF_AUTO_BAN_THRESHOLD, DEFAULT_CF_BAN_TTL_MS`.

Add next to `loadBanTtlMs` (line ~102), exported so they're unit-testable:

```ts
/** ADR-043 — CF auto-ban master switch. Any value other than "false"/"0"/"" enables. */
export function loadCfAutoBanEnabled(env: Env): boolean {
  const v = (env as { CF_AUTO_BAN_ENABLED?: string }).CF_AUTO_BAN_ENABLED;
  if (v === undefined || v === "") return DEFAULT_CF_AUTO_BAN_ENABLED;
  return v !== "false" && v !== "0";
}

/** ADR-043 — CF-event threshold within the penalty window. Falls back to 6. */
export function loadCfAutoBanThreshold(env: Env): number {
  const v = (env as { CF_AUTO_BAN_THRESHOLD?: string }).CF_AUTO_BAN_THRESHOLD;
  if (v === undefined || v === "") return DEFAULT_CF_AUTO_BAN_THRESHOLD;
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : DEFAULT_CF_AUTO_BAN_THRESHOLD;
}

/** ADR-043 — CF auto-ban TTL (ms). Falls back to 6 h. */
export function loadCfBanTtlMs(env: Env): number {
  const v = (env as { CF_BAN_TTL_MS?: string }).CF_BAN_TTL_MS;
  if (v === undefined || v === "") return DEFAULT_CF_BAN_TTL_MS;
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : DEFAULT_CF_BAN_TTL_MS;
}
```

- [ ] **Step 3: Write loader unit tests** in `test/cf_auto_ban.test.ts`

```ts
import { describe, expect, it } from "vitest";
import {
  loadCfAutoBanEnabled,
  loadCfAutoBanThreshold,
  loadCfBanTtlMs,
} from "../src/proxy_coordinator";
import type { Env } from "../src/types";

const e = (o: Partial<Env>) => o as Env;

describe("ADR-043 loaders", () => {
  it("defaults when unset", () => {
    expect(loadCfAutoBanEnabled(e({}))).toBe(true);
    expect(loadCfAutoBanThreshold(e({}))).toBe(6);
    expect(loadCfBanTtlMs(e({}))).toBe(21_600_000);
  });
  it("CF_AUTO_BAN_ENABLED='false'/'0' disables", () => {
    expect(loadCfAutoBanEnabled(e({ CF_AUTO_BAN_ENABLED: "false" }))).toBe(false);
    expect(loadCfAutoBanEnabled(e({ CF_AUTO_BAN_ENABLED: "0" }))).toBe(false);
    expect(loadCfAutoBanEnabled(e({ CF_AUTO_BAN_ENABLED: "true" }))).toBe(true);
  });
  it("parses overrides; rejects junk → default", () => {
    expect(loadCfAutoBanThreshold(e({ CF_AUTO_BAN_THRESHOLD: "10" }))).toBe(10);
    expect(loadCfAutoBanThreshold(e({ CF_AUTO_BAN_THRESHOLD: "x" }))).toBe(6);
    expect(loadCfBanTtlMs(e({ CF_BAN_TTL_MS: "3600000" }))).toBe(3_600_000);
    expect(loadCfBanTtlMs(e({ CF_BAN_TTL_MS: "-1" }))).toBe(21_600_000);
  });
});
```

- [ ] **Step 4: Run — expect FAIL (loaders not exported yet if Step 2 skipped) then PASS**

Run: `npm test -- cf_auto_ban`
Expected after Steps 1–2: PASS (3 tests). Also run `npm run typecheck` → no errors.

- [ ] **Step 5: Commit** (in coordinator repo)

```bash
git add src/types.ts src/proxy_coordinator.ts test/cf_auto_ban.test.ts
git commit -m "feat(proxy-coordinator): add CF auto-ban env knobs + loaders (ADR-043)"
```

---

## Task 2: `bannedReason` state field for observability (D5)

**Files:**
- Modify: `src/proxy_coordinator.ts` (`CoordinatorState` interface, `loadState`, `handleStateDump`)
- Test: `test/cf_auto_ban.test.ts`

- [ ] **Step 1: Add the field to `CoordinatorState`**

In `interface CoordinatorState { ... }`, after `banSpikeAlertedBucket: number;` add:

```ts
  /**
   * ADR-043 D5 — cause of the current ban for observability:
   * "cf_auto" | "javdb_hardban" | "manual" | null. Surfaced via /do/state.
   * Defaults to null for snapshots written before this field existed.
   */
  bannedReason: string | null;
```

- [ ] **Step 2: Default it in `loadState`**

In the `this.cached = { ... }` normaliser, after `banSpikeAlertedBucket: stored?.banSpikeAlertedBucket ?? 0,` add:

```ts
      bannedReason: stored?.bannedReason ?? null,
```

- [ ] **Step 3: Clear it on unban + surface it on state dump**

In `handleReport`, in the `rawKind === "unban"` branch, after `state.bannedUntil = null;` add `state.bannedReason = null;`.

`handleStateDump` already spreads `...state`, so `bannedReason` is returned automatically — no change needed there.

- [ ] **Step 4: Write the field test** (append to `test/cf_auto_ban.test.ts`)

```ts
import {
  env,
  createExecutionContext,
  waitOnExecutionContext,
} from "cloudflare:test";
import worker from "../src/index";

const AUTH = { authorization: "Bearer test-token" };
async function call(path: string, body: unknown) {
  const req = new Request(`https://test.invalid/${path}`, {
    method: "POST",
    headers: { ...AUTH, "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const ctx = createExecutionContext();
  const res = await worker.fetch(req, env, ctx);
  await waitOnExecutionContext(ctx);
  return res;
}
const dump = async (proxyId: string) =>
  (await (await call("do/state", { proxy_id: proxyId })).json()) as {
    bannedReason: string | null;
    banned: boolean;
  };

describe("ADR-043 bannedReason", () => {
  it("fresh proxy has bannedReason null", async () => {
    const s = await dump("p-reason-fresh");
    expect(s.bannedReason).toBe(null);
  });
});
```

> NOTE: the request path is `/do/state` etc.; confirm the route prefix against `src/index.ts` (the existing `test/proxy_coordinator.test.ts` helpers show the exact prefix used in this repo — mirror it).

- [ ] **Step 5: Run + commit**

Run: `npm test -- cf_auto_ban` → PASS. `npm run typecheck` → clean.

```bash
git add src/proxy_coordinator.ts test/cf_auto_ban.test.ts
git commit -m "feat(proxy-coordinator): track bannedReason on DO state (ADR-043 D5)"
```

---

## Task 3: `maybeCfAutoBan()` escalation + wire into `handleReport`

**Files:**
- Modify: `src/proxy_coordinator.ts`
- Test: `test/cf_auto_ban.test.ts`

- [ ] **Step 1: Write the failing e2e tests** (append to `test/cf_auto_ban.test.ts`)

Helper to report a CF event and to lease:

```ts
const reportCf = (proxyId: string) => call("do/report", { proxy_id: proxyId, kind: "cf" });
const reportSuccess = (proxyId: string) => call("do/report", { proxy_id: proxyId, kind: "success" });
const reportN = async (proxyId: string, kind: "cf" | "success", n: number) => {
  for (let i = 0; i < n; i++) await call("do/report", { proxy_id: proxyId, kind });
};

describe("ADR-043 CF auto-ban escalation", () => {
  it("6 CF events with zero success → banned (cf_auto)", async () => {
    const p = "p-cf-ban";
    await reportN(p, "cf", 6);
    const s = await dump(p);
    expect(s.banned).toBe(true);
    expect(s.bannedReason).toBe("cf_auto");
  });

  it("below threshold (5) → not banned", async () => {
    const p = "p-cf-5";
    await reportN(p, "cf", 5);
    const s = await dump(p);
    expect(s.banned).toBe(false);
  });

  it("6 CF events but a success in window → NOT banned (zero-success guard)", async () => {
    const p = "p-cf-mixed";
    await reportN(p, "cf", 5);
    await reportSuccess(p);
    await reportCf(p); // 6th cf, but successEvents.length > 0
    const s = await dump(p);
    expect(s.banned).toBe(false);
  });
});
```

- [ ] **Step 2: Run — expect FAIL**

Run: `npm test -- cf_auto_ban`
Expected: the "→ banned" test FAILS (`banned` is `false`) because escalation isn't implemented.

- [ ] **Step 3: Implement `maybeCfAutoBan` + call it**

Add the method to the `ProxyCoordinator` class (near `computePenaltyFactor`):

```ts
/**
 * ADR-043 D1/D2 — escalate sustained CF failures (with zero success) into a
 * short cross-runner ban. MUST be called AFTER purgeExpired + the cfEvents
 * push, so cfEvents/successEvents are already pruned to penaltyWindowSec.
 */
private maybeCfAutoBan(state: CoordinatorState, now: number): void {
  if (!loadCfAutoBanEnabled(this.env)) return;
  if (state.cfEvents.length < loadCfAutoBanThreshold(this.env)) return;
  if (state.successEvents.length > 0) return; // zero-success guard
  const candidate = now + loadCfBanTtlMs(this.env);
  // monotonic-max: never shorten a longer existing ban (e.g. an 8d hard ban)
  if (state.bannedUntil === null || state.bannedUntil <= candidate) {
    state.bannedUntil = candidate;
    state.bannedReason = "cf_auto";
  }
}
```

In `handleReport`, the CF branch is the final `else` (`kind = "cf"; state.cfEvents.push(now);`). Add the call right after the push:

```ts
    } else {
      kind = "cf";
      state.cfEvents.push(now);
      this.maybeCfAutoBan(state, now); // ADR-043 D1
    }
```

- [ ] **Step 4: Run — expect PASS**

Run: `npm test -- cf_auto_ban` → all PASS. `npm run typecheck` → clean. `npm test` (full suite) → no regressions in `proxy_coordinator.test.ts`.

- [ ] **Step 5: Commit**

```bash
git add src/proxy_coordinator.ts test/cf_auto_ban.test.ts
git commit -m "feat(proxy-coordinator): CF persistent-failure auto-ban escalation (ADR-043 D1/D2)"
```

---

## Task 4: wrangler.toml `[vars]`

**Files:**
- Modify: `wrangler.toml` (coordinator repo)

- [ ] **Step 1: Add the vars**

In the existing `[vars]` table add (showing defaults explicitly so operators see the knobs):

```toml
# ADR-043 — CF persistent-failure auto-ban (default ON). Set CF_AUTO_BAN_ENABLED = "false" to kill-switch.
CF_AUTO_BAN_ENABLED = "true"
CF_AUTO_BAN_THRESHOLD = "6"
CF_BAN_TTL_MS = "21600000"   # 6 hours
```

- [ ] **Step 2: Verify config parses**

Run: `npx wrangler deploy --dry-run` (or `npm run typecheck` if a deploy dry-run needs creds)
Expected: no config error; the three vars are listed.

- [ ] **Step 3: Commit**

```bash
git add wrangler.toml
git commit -m "chore(proxy-coordinator): expose CF auto-ban vars in wrangler.toml (ADR-043)"
```

---

## Task 5: Handbook env docs (CICD repo)

**Files (this/CICD repo):**
- Modify: `docs/handbook/en/self-hoster/proxy-coordinator.md`
- Modify: `docs/handbook/zh/self-hoster/proxy-coordinator.md`

- [ ] **Step 1: Add an env-var subsection to the EN handbook**

Find the existing env/vars table (search the file for `BAN_TTL_MS` or `PENALTY_WINDOW_SEC`). Add three rows / a short subsection:

```markdown
### CF auto-ban (ADR-043)

| Env Var | Default | Meaning |
| --- | --- | --- |
| `CF_AUTO_BAN_ENABLED` | `true` | Auto-ban a proxy that keeps failing the CF wall. Set `false` to disable. |
| `CF_AUTO_BAN_THRESHOLD` | `6` | CF events within the penalty window (`PENALTY_WINDOW_SEC`, default 300 s) with **zero** successes before the proxy is banned. |
| `CF_BAN_TTL_MS` | `21600000` (6 h) | How long the CF auto-ban lasts. Short by design — CF IP reputation recovers fast. |
```

- [ ] **Step 2: Mirror the same rows into the ZH handbook** (`docs/handbook/zh/self-hoster/proxy-coordinator.md`), translating only the prose (keep env names / numbers verbatim):

```markdown
### CF 自动封禁 (ADR-043)

| Env Var | 默认 | 含义 |
| --- | --- | --- |
| `CF_AUTO_BAN_ENABLED` | `true` | 自动封禁持续过不了 CF 墙的代理。设为 `false` 关闭。 |
| `CF_AUTO_BAN_THRESHOLD` | `6` | penalty 窗口（`PENALTY_WINDOW_SEC`，默认 300 秒）内 **零成功** 的 CF 事件数达到此值即封禁。 |
| `CF_BAN_TTL_MS` | `21600000`（6 小时） | CF 自动封禁时长。刻意短——CF IP 信誉恢复快。 |
```

- [ ] **Step 3: Commit** (CICD repo)

```bash
git add docs/handbook/en/self-hoster/proxy-coordinator.md docs/handbook/zh/self-hoster/proxy-coordinator.md
git commit -m "docs(proxy-coordinator): document CF auto-ban env vars (ADR-043)"
```

---

## Self-Review

- **Spec coverage (ADR-043 D1–D7):** D1 trigger point → Task 3 (cf branch). D2 rule (threshold + zero-success) → Task 3 + tests. D3 6 h TTL → Task 1 `DEFAULT_CF_BAN_TTL_MS`. D4 monotonic-max → `maybeCfAutoBan` guard + (covered more in IMP-02's hard-ban interaction). D5 ban-source → Task 2 `bannedReason`. D6 default-on + kill-switch + env table → Tasks 1 & 4. D7 site-wide out of scope → no task (intentional). ✓
- **Placeholder scan:** Task 2 Step 4 notes "confirm route prefix against `src/index.ts`" — this is a verification instruction, not a code placeholder; the helper mirrors the existing `proxy_coordinator.test.ts` harness. No other gaps.
- **Type consistency:** `loadCfAutoBanEnabled/Threshold/TtlMs`, `maybeCfAutoBan`, `bannedReason`, `DEFAULT_CF_*` used consistently across tasks. The escalation reads `state.cfEvents.length` / `state.successEvents.length` post-`purgeExpired` (verified: `purgeExpired` prunes both to `penaltyWindowSec`).
