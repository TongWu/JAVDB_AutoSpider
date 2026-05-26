# IMP-ADR023-03: ADR-023 Phase 3 - Observability And Rollout Hardening

**Status:** Proposed

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ADR-023 Phase 3 by making recommendation-policy behavior observable, auditable, and safe to roll out or roll back after the flagged Phase 2 ordering path exists.

**Architecture:** Keep policy execution in `/recommend_proxy`. Add lightweight aggregate policy metadata to the response, record policy-vs-heuristic deltas into existing Worker analytics when available, and surface the data in operator docs / dashboard-adjacent JSON without adding a new Durable Object. Rollout gates are documented and testable from existing endpoints.

**Tech Stack:** TypeScript, Cloudflare Workers, Workers Analytics Engine binding when enabled, Durable Objects, Vitest + `@cloudflare/vitest-pool-workers`, Markdown handbook docs.

**Source spec:** [ADR-023](ADR-023-proxy-recommendation-policy.md), D1-D9; depends on [IMP-ADR023-01](IMP-ADR023-01-shadowscore-confidence-fields.md) and [IMP-ADR023-02](IMP-ADR023-02-policy-rollout-flag.md).

**Non-negotiable:** This phase must not add a new storage service, must not make policy mode default-on, must not require Analytics Engine to be enabled, and must not change ranking semantics from Phase 2.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Modify | `JAVDB_AutoSpider_Proxycoordinator/src/recommend_policy.ts` | Add aggregate diagnostics for disagreement, unstable pool, and rollout readiness. |
| Modify | `JAVDB_AutoSpider_Proxycoordinator/src/index.ts` | Include `policy_summary` in `/recommend_proxy` and write best-effort analytics events. |
| Modify | `JAVDB_AutoSpider_Proxycoordinator/test/recommend_policy.test.ts` | Unit tests for summary math and rollout gate status. |
| Modify | `JAVDB_AutoSpider_Proxycoordinator/test/recommend_proxy.test.ts` | Route-level tests for `policy_summary` and analytics-optional behavior. |
| Modify | `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts` | Surface policy mode and disagreement summary in the dashboard's proxy health area if `/recommend_proxy` data is available. |
| Modify | `JAVDB_AutoSpider_Proxycoordinator/test/dashboard_html.test.ts` | Dashboard rendering test for policy summary fields. |
| Modify | `docs/handbook/en/self-hoster/proxy-coordinator.md` | Rollout gate, smoke-test, and rollback checklist. |
| Modify | `docs/handbook/zh/self-hoster/proxy-coordinator.md` | Chinese mirror of rollout checklist. |

## Scope Boundaries

- Do not add new Durable Object classes or migrations.
- Do not add new external services.
- Do not change policy weights in this phase.
- Do not change Python client behavior.
- Do not require the dashboard to call a new endpoint; reuse `/recommend_proxy` or existing dashboard data flow.

---

## Task 1: Add Policy Summary Diagnostics

**Files:**
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/recommend_policy.ts`
- Modify: `JAVDB_AutoSpider_Proxycoordinator/test/recommend_policy.test.ts`

- [ ] **Step 1: Add policy summary tests**

Append these tests to `JAVDB_AutoSpider_Proxycoordinator/test/recommend_policy.test.ts`:

```typescript
import {
  computeRecommendationPolicySummary,
  type RecommendationPolicySummaryInput,
} from "../src/recommend_policy";

function summaryInput(
  overrides: Partial<RecommendationPolicySummaryInput>,
): RecommendationPolicySummaryInput {
  return {
    proxy_id: "P",
    heuristic_score: 0.5,
    model_score: 0.5,
    rank_score: 0.5,
    confidence: 0,
    available: true,
    reason_code: "low_confidence_prior",
    ...overrides,
  };
}

describe("recommend_policy summary diagnostics", () => {
  it("summarizes disagreement and confidence", () => {
    const summary = computeRecommendationPolicySummary([
      summaryInput({ proxy_id: "A", heuristic_score: 0.9, model_score: 0.2, confidence: 0.5 }),
      summaryInput({ proxy_id: "B", heuristic_score: 0.1, model_score: 0.8, confidence: 0.75 }),
    ], "shadow");

    expect(summary.mode).toBe("shadow");
    expect(summary.candidate_count).toBe(2);
    expect(summary.average_confidence).toBeCloseTo(0.625, 5);
    expect(summary.max_score_delta).toBeCloseTo(0.7, 5);
    expect(summary.disagreement_count).toBe(2);
    expect(summary.rollout_gate).toBe("observe");
  });

  it("marks blocked when global pool is unstable", () => {
    const summary = computeRecommendationPolicySummary([
      summaryInput({ reason_code: "global_pool_unstable", confidence: 0.2 }),
      summaryInput({ reason_code: "global_pool_unstable", confidence: 0.2 }),
    ], "policy");

    expect(summary.global_pool_unstable_count).toBe(2);
    expect(summary.rollout_gate).toBe("blocked_global_instability");
  });

  it("marks ready only when policy mode has enough confidence and low disagreement", () => {
    const summary = computeRecommendationPolicySummary([
      summaryInput({ heuristic_score: 0.8, model_score: 0.82, confidence: 0.8 }),
      summaryInput({ heuristic_score: 0.7, model_score: 0.72, confidence: 0.9 }),
    ], "policy");

    expect(summary.rollout_gate).toBe("ready");
  });
});
```

- [ ] **Step 2: Run tests and verify the expected failure**

Run:

```bash
cd JAVDB_AutoSpider_Proxycoordinator
npm run test -- test/recommend_policy.test.ts
```

Expected: FAIL because `computeRecommendationPolicySummary` and `RecommendationPolicySummaryInput` do not exist.

- [ ] **Step 3: Implement summary helpers**

In `JAVDB_AutoSpider_Proxycoordinator/src/recommend_policy.ts`, add:

```typescript
export type RecommendationRolloutGate =
  | "observe"
  | "ready"
  | "blocked_global_instability";

export interface RecommendationPolicySummaryInput {
  proxy_id: string;
  heuristic_score: number;
  model_score: number;
  rank_score: number;
  confidence: number;
  available: boolean;
  reason_code: RecommendReasonCode;
}

export interface RecommendationPolicySummary {
  mode: RecommendationPolicyMode;
  candidate_count: number;
  available_count: number;
  average_confidence: number;
  max_score_delta: number;
  disagreement_count: number;
  global_pool_unstable_count: number;
  rollout_gate: RecommendationRolloutGate;
}

export function computeRecommendationPolicySummary(
  rows: RecommendationPolicySummaryInput[],
  mode: RecommendationPolicyMode,
): RecommendationPolicySummary {
  const candidateCount = rows.length;
  const available = rows.filter((row) => row.available);
  const confidenceSum = rows.reduce((acc, row) => acc + clamp(row.confidence, 0, 1), 0);
  const deltas = rows.map((row) =>
    Math.abs(clamp(row.model_score, 0, 1) - clamp(row.heuristic_score, 0, 1)),
  );
  const maxDelta = deltas.length > 0 ? Math.max(...deltas) : 0;
  const disagreementCount = deltas.filter((delta) => delta >= 0.2).length;
  const globalPoolUnstableCount = rows.filter(
    (row) => row.reason_code === "global_pool_unstable",
  ).length;
  const averageConfidence =
    candidateCount > 0 ? confidenceSum / candidateCount : 0;

  let rolloutGate: RecommendationRolloutGate = "observe";
  if (globalPoolUnstableCount > 0) {
    rolloutGate = "blocked_global_instability";
  } else if (
    mode === "policy" &&
    candidateCount > 0 &&
    averageConfidence >= 0.6 &&
    maxDelta < 0.2
  ) {
    rolloutGate = "ready";
  }

  return {
    mode,
    candidate_count: candidateCount,
    available_count: available.length,
    average_confidence: averageConfidence,
    max_score_delta: maxDelta,
    disagreement_count: disagreementCount,
    global_pool_unstable_count: globalPoolUnstableCount,
    rollout_gate: rolloutGate,
  };
}
```

- [ ] **Step 4: Run tests and verify they pass**

Run:

```bash
cd JAVDB_AutoSpider_Proxycoordinator
npm run test -- test/recommend_policy.test.ts
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add JAVDB_AutoSpider_Proxycoordinator/src/recommend_policy.ts JAVDB_AutoSpider_Proxycoordinator/test/recommend_policy.test.ts
git commit -m "feat(proxy): summarize recommendation policy diagnostics"
```

---

## Task 2: Return `policy_summary` And Write Best-Effort Analytics

**Files:**
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/index.ts`
- Modify: `JAVDB_AutoSpider_Proxycoordinator/test/recommend_proxy.test.ts`

- [ ] **Step 1: Extend route tests**

In `JAVDB_AutoSpider_Proxycoordinator/test/recommend_proxy.test.ts`, extend the `recommend()` body type:

```typescript
    policy_summary?: {
      mode: string;
      candidate_count: number;
      available_count: number;
      average_confidence: number;
      max_score_delta: number;
      disagreement_count: number;
      global_pool_unstable_count: number;
      rollout_gate: string;
    };
```

Add this test under the ranking block:

```typescript
  it("returns policy_summary for recommendation diagnostics", async () => {
    await lease("R-SUMMARY-A");
    await lease("R-SUMMARY-B");
    await reportEvent("R-SUMMARY-A", "success", { latency_ms: 100 });
    await reportEvent("R-SUMMARY-B", "failure");

    const r = await recommend("proxy_ids=R-SUMMARY-A,R-SUMMARY-B&include_unhealthy=1");

    expect(r.body.policy_summary).toBeDefined();
    expect(r.body.policy_summary!.mode).toBe("shadow");
    expect(r.body.policy_summary!.candidate_count).toBe(2);
    expect(r.body.policy_summary!.available_count).toBe(2);
    expect(r.body.policy_summary!.rollout_gate).toMatch(
      /observe|ready|blocked_global_instability/,
    );
  });
```

- [ ] **Step 2: Run route tests and verify the expected failure**

Run:

```bash
cd JAVDB_AutoSpider_Proxycoordinator
npm run test -- test/recommend_proxy.test.ts
```

Expected: FAIL because `policy_summary` is absent.

- [ ] **Step 3: Import summary helper in `index.ts`**

Extend the import from `./recommend_policy`:

```typescript
  computeRecommendationPolicySummary,
  type RecommendationPolicySummaryInput,
```

- [ ] **Step 4: Compute and return `policy_summary`**

After `const ranked: Recommendation[] = ...`, add:

```typescript
  const policySummary = computeRecommendationPolicySummary(
    ranked.map((row): RecommendationPolicySummaryInput => ({
      proxy_id: row.proxy_id,
      heuristic_score: row.heuristic_score,
      model_score: row.model_score,
      rank_score: row.rank_score,
      confidence: row.confidence,
      available: row.available,
      reason_code: row.reason_code,
    })),
    rankingMode,
  );
```

In the final `jsonResponse`, add:

```typescript
    policy_summary: policySummary,
```

- [ ] **Step 5: Add best-effort analytics write**

Near the end of `recommendProxies()`, before `return jsonResponse(...)`, add:

```typescript
  if (env.LEASE_ANALYTICS) {
    try {
      env.LEASE_ANALYTICS.writeDataPoint({
        blobs: [
          "recommend_proxy",
          rankingMode,
          policySummary.rollout_gate,
        ],
        doubles: [
          policySummary.candidate_count,
          policySummary.available_count,
          policySummary.average_confidence,
          policySummary.max_score_delta,
          policySummary.disagreement_count,
          policySummary.global_pool_unstable_count,
        ],
        indexes: ["recommend_proxy"],
      });
    } catch (err) {
      console.warn("recommend_proxy analytics write failed", {
        error: err instanceof Error ? err.message : String(err),
      });
    }
  }
```

This is best-effort only. If `LEASE_ANALYTICS` is absent or throws, `/recommend_proxy` must still return normally.

- [ ] **Step 6: Run route tests**

Run:

```bash
cd JAVDB_AutoSpider_Proxycoordinator
npm run test -- test/recommend_proxy.test.ts
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add JAVDB_AutoSpider_Proxycoordinator/src/index.ts JAVDB_AutoSpider_Proxycoordinator/test/recommend_proxy.test.ts
git commit -m "feat(proxy): expose recommendation policy diagnostics"
```

---

## Task 3: Surface Policy Summary In Dashboard HTML

**Files:**
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts`
- Modify: `JAVDB_AutoSpider_Proxycoordinator/test/dashboard_html.test.ts`

- [ ] **Step 1: Add dashboard rendering test**

In `JAVDB_AutoSpider_Proxycoordinator/test/dashboard_html.test.ts`, add:

```typescript
import { renderPolicySummaryBadge } from "../src/dashboard_html";

describe("renderPolicySummaryBadge", () => {
  it("renders policy mode and rollout gate", () => {
    const html = renderPolicySummaryBadge({
      mode: "policy",
      candidate_count: 4,
      available_count: 3,
      average_confidence: 0.75,
      max_score_delta: 0.12,
      disagreement_count: 1,
      global_pool_unstable_count: 0,
      rollout_gate: "ready",
    });

    expect(html).toContain("policy");
    expect(html).toContain("ready");
    expect(html).toContain("75%");
    expect(html).toContain("1 disagreement");
  });

  it("renders empty string when no summary is available", () => {
    expect(renderPolicySummaryBadge(null)).toBe("");
  });
});
```

- [ ] **Step 2: Run dashboard test and verify the expected failure**

Run:

```bash
cd JAVDB_AutoSpider_Proxycoordinator
npm run test -- test/dashboard_html.test.ts
```

Expected: FAIL because `renderPolicySummaryBadge` is not exported.

- [ ] **Step 3: Add rendering helper**

In `JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts`, export:

```typescript
export interface PolicySummaryForDashboard {
  mode: string;
  candidate_count: number;
  available_count: number;
  average_confidence: number;
  max_score_delta: number;
  disagreement_count: number;
  global_pool_unstable_count: number;
  rollout_gate: string;
}

export function renderPolicySummaryBadge(
  summary: PolicySummaryForDashboard | null | undefined,
): string {
  if (!summary) return "";
  const confidencePct = Math.round(Math.max(0, Math.min(1, summary.average_confidence)) * 100);
  const disagreementLabel =
    summary.disagreement_count === 1
      ? "1 disagreement"
      : `${summary.disagreement_count} disagreements`;
  return [
    '<span class="pill info">',
    `policy ${escapeHtmlForServer(summary.mode)}`,
    '</span> ',
    '<span class="pill">',
    `gate ${escapeHtmlForServer(summary.rollout_gate)}`,
    '</span> ',
    '<span class="pill">',
    `${confidencePct}% confidence`,
    '</span> ',
    '<span class="pill">',
    escapeHtmlForServer(disagreementLabel),
    '</span>',
  ].join("");
}
```

Use this helper near the proxy health summary area if that area already has access to `/recommend_proxy` response data. If the dashboard currently only consumes `/ops/snapshot`, keep the helper exported and covered by tests; do not add a new data fetch in this phase.

- [ ] **Step 4: Run dashboard tests**

Run:

```bash
cd JAVDB_AutoSpider_Proxycoordinator
npm run test -- test/dashboard_html.test.ts
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts JAVDB_AutoSpider_Proxycoordinator/test/dashboard_html.test.ts
git commit -m "feat(proxy): render recommendation policy summary"
```

---

## Task 4: Document Rollout Gates And Smoke Checks

**Files:**
- Modify: `docs/handbook/en/self-hoster/proxy-coordinator.md`
- Modify: `docs/handbook/zh/self-hoster/proxy-coordinator.md`

- [ ] **Step 1: Add English rollout checklist**

In `docs/handbook/en/self-hoster/proxy-coordinator.md`, after the ADR-023 Phase 2 variable table, add:

```markdown
### ADR-023 Rollout Gate

Before switching `RECOMMEND_PROXY_POLICY_MODE` from `"shadow"` to `"policy"`:

1. Call `/recommend_proxy?proxy_ids=<ids>&include_unhealthy=1` for the active
   pool and inspect `policy_summary`.
2. Do not enable policy mode while `policy_summary.rollout_gate` is
   `blocked_global_instability`.
3. Treat high `disagreement_count` as a review signal: compare
   `heuristic_score`, `model_score`, `rank_score`, and `reason_code` for the
   largest disagreements.
4. Enable policy mode for one deploy window first, then watch ban rate,
   `cf_bypass` rate, Session committed rate, and request success rate.
5. Roll back by setting `RECOMMEND_PROXY_POLICY_MODE = "shadow"` and redeploying.

Smoke check:

```bash
curl -sS -H "Authorization: Bearer $PROXY_COORDINATOR_TOKEN" \
  "$PROXY_COORDINATOR_URL/recommend_proxy?proxy_ids=P1,P2&include_unhealthy=1" \
  | jq '.policy_summary, .recommendations[] | {proxy_id, score, rank_score, reason_code}'
```
```

- [ ] **Step 2: Add Chinese rollout checklist**

Mirror the English section in `docs/handbook/zh/self-hoster/proxy-coordinator.md`:

```markdown
### ADR-023 Rollout Gate

在把 `RECOMMEND_PROXY_POLICY_MODE` 从 `"shadow"` 切到 `"policy"` 前：

1. 对活跃代理池调用 `/recommend_proxy?proxy_ids=<ids>&include_unhealthy=1`，
   检查 `policy_summary`。
2. 当 `policy_summary.rollout_gate` 是 `blocked_global_instability` 时，
   不要启用 policy mode。
3. 如果 `disagreement_count` 很高，把它当成 review 信号：对比最大分歧项的
   `heuristic_score`、`model_score`、`rank_score` 和 `reason_code`。
4. 先只启用一个部署窗口，然后观察 ban rate、`cf_bypass` rate、Session
   committed rate 和 request success rate。
5. 回滚方式是把 `RECOMMEND_PROXY_POLICY_MODE = "shadow"` 并重新部署。

Smoke check：

```bash
curl -sS -H "Authorization: Bearer $PROXY_COORDINATOR_TOKEN" \
  "$PROXY_COORDINATOR_URL/recommend_proxy?proxy_ids=P1,P2&include_unhealthy=1" \
  | jq '.policy_summary, .recommendations[] | {proxy_id, score, rank_score, reason_code}'
```
```

- [ ] **Step 3: Verify docs mention rollout gates**

Run:

```bash
rg -n "ADR-023 Rollout Gate|policy_summary|blocked_global_instability|rank_score" \
  docs/handbook/en/self-hoster/proxy-coordinator.md \
  docs/handbook/zh/self-hoster/proxy-coordinator.md
```

Expected: both English and Chinese docs contain the rollout gate and smoke check.

- [ ] **Step 4: Commit**

```bash
git add docs/handbook/en/self-hoster/proxy-coordinator.md docs/handbook/zh/self-hoster/proxy-coordinator.md
git commit -m "docs(proxy): add recommendation policy rollout gate"
```

---

## Task 5: Workflow Review And Final Verification

**Files:**
- Review only: `.github/workflows/`
- Verify all files touched in this IMP.

- [ ] **Step 1: Review workflow impact**

Run:

```bash
rg -n "Proxycoordinator|recommend_proxy|policy_summary|wrangler|npm run test|npm run typecheck" .github/workflows JAVDB_AutoSpider_Proxycoordinator/wrangler.toml
```

Expected: No GitHub Actions change is required. This phase adds endpoint metadata, analytics writes behind an optional existing binding, docs, and dashboard rendering helper tests.

- [ ] **Step 2: Run focused Worker tests**

Run:

```bash
cd JAVDB_AutoSpider_Proxycoordinator
npm run test -- test/recommend_policy.test.ts test/recommend_proxy.test.ts test/dashboard_html.test.ts
```

Expected: PASS.

- [ ] **Step 3: Run Worker typecheck**

Run:

```bash
cd JAVDB_AutoSpider_Proxycoordinator
npm run typecheck
```

Expected: PASS.

- [ ] **Step 4: Verify handbook docs**

Run:

```bash
rg -n "policy_summary|rank_score|rollout_gate|RECOMMEND_PROXY_POLICY_MODE" \
  docs/handbook/en/self-hoster/proxy-coordinator.md \
  docs/handbook/zh/self-hoster/proxy-coordinator.md
```

Expected: both language files include the same operational fields.

- [ ] **Step 5: Run scoped diff whitespace check**

Run:

```bash
git diff --check -- \
  JAVDB_AutoSpider_Proxycoordinator/src/recommend_policy.ts \
  JAVDB_AutoSpider_Proxycoordinator/src/index.ts \
  JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts \
  JAVDB_AutoSpider_Proxycoordinator/test/recommend_policy.test.ts \
  JAVDB_AutoSpider_Proxycoordinator/test/recommend_proxy.test.ts \
  JAVDB_AutoSpider_Proxycoordinator/test/dashboard_html.test.ts \
  docs/handbook/en/self-hoster/proxy-coordinator.md \
  docs/handbook/zh/self-hoster/proxy-coordinator.md
```

Expected: no output.

- [ ] **Step 6: Commit**

```bash
git add \
  JAVDB_AutoSpider_Proxycoordinator/src/recommend_policy.ts \
  JAVDB_AutoSpider_Proxycoordinator/src/index.ts \
  JAVDB_AutoSpider_Proxycoordinator/src/dashboard_html.ts \
  JAVDB_AutoSpider_Proxycoordinator/test/recommend_policy.test.ts \
  JAVDB_AutoSpider_Proxycoordinator/test/recommend_proxy.test.ts \
  JAVDB_AutoSpider_Proxycoordinator/test/dashboard_html.test.ts \
  docs/handbook/en/self-hoster/proxy-coordinator.md \
  docs/handbook/zh/self-hoster/proxy-coordinator.md
git commit -m "feat(proxy): harden recommendation policy rollout"
```

