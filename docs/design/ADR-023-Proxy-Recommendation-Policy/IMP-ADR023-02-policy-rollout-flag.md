# IMP-ADR023-02: ADR-023 Phase 2 - Policy Rollout Flag

**Status:** Proposed

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ADR-023 Phase 2 by allowing `/recommend_proxy` to sort by the ADR-023 policy score behind an explicit Worker flag, while preserving heuristic fallback and safe cooldown handling.

**Architecture:** Phase 1 already adds pure policy scoring and shadow fields. This phase adds a Worker env switch that controls which score becomes the ranking source. The default remains heuristic ordering; when enabled, ordering uses a confidence-blended policy score for available proxies and keeps banned / errored proxies behind healthy candidates.

**Tech Stack:** TypeScript, Cloudflare Workers, Durable Objects, Vitest + `@cloudflare/vitest-pool-workers`, Python 3.11, pytest.

**Source spec:** [ADR-023](ADR-023-proxy-recommendation-policy.md), D1-D9; depends on [IMP-ADR023-01](IMP-ADR023-01-shadowscore-confidence-fields.md).

**Non-negotiable:** Default deploys must behave exactly like Phase 1. Enabling the flag must not put banned proxies ahead of available proxies, must not remove the existing `score` field, and must not require Python client changes.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Modify | `JAVDB_AutoSpider_Proxycoordinator/src/types.ts` | Add env vars for policy ranking mode and optional exploration floor. |
| Modify | `JAVDB_AutoSpider_Proxycoordinator/src/recommend_policy.ts` | Add blended `rank_score`, ranking-mode helpers, and safe fallback semantics. |
| Modify | `JAVDB_AutoSpider_Proxycoordinator/src/index.ts` | Sort `/recommend_proxy` by heuristic or policy rank score depending on env. |
| Modify | `JAVDB_AutoSpider_Proxycoordinator/test/recommend_policy.test.ts` | Unit tests for blended rank score and exploration floor. |
| Modify | `JAVDB_AutoSpider_Proxycoordinator/test/recommend_proxy.test.ts` | Route-level tests proving default ordering is unchanged and flag-enabled ordering changes only when intended. |
| Modify | `JAVDB_AutoSpider_Proxycoordinator/wrangler.toml` | Document default-off rollout vars. |
| Modify | `tests/unit/test_recommend_proxy_client.py` | Compatibility test proving a new `rank_score` field is ignored by Python. |
| Modify | `docs/handbook/en/self-hoster/proxy-coordinator.md` | Document flag behavior and rollback. |
| Modify | `docs/handbook/zh/self-hoster/proxy-coordinator.md` | Chinese mirror of the operator documentation. |

## Scope Boundaries

- Do not change `/lease`, `/report`, or DO storage schema.
- Do not add a dashboard chart in this phase; Phase 3 owns observability polish.
- Do not add online training, offline model files, or Workers AI bindings.
- Do not make policy ordering default-on.
- Do not change Python `RecommendProxyPolicy.score_for()` to read `model_score` or `rank_score`; it still consumes `score` until a separate Python-side behavior change is designed.

---

## Task 1: Add Ranking Mode To The Policy Module

**Files:**
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/recommend_policy.ts`
- Modify: `JAVDB_AutoSpider_Proxycoordinator/test/recommend_policy.test.ts`

- [ ] **Step 1: Extend policy unit tests**

Append these tests to `JAVDB_AutoSpider_Proxycoordinator/test/recommend_policy.test.ts`:

```typescript
import {
  computeRecommendationRankScore,
  parseRecommendationPolicyMode,
} from "../src/recommend_policy";

describe("recommend_policy ranking mode", () => {
  it("keeps heuristic rank score in shadow mode", () => {
    const rank = computeRecommendationRankScore({
      heuristic_score: 0.8,
      model_score: 0.1,
      confidence: 1,
      available: true,
      mode: "shadow",
      exploration_floor: 0.02,
    });

    expect(rank).toBe(0.8);
  });

  it("blends model and heuristic in policy mode by confidence", () => {
    const rank = computeRecommendationRankScore({
      heuristic_score: 0.2,
      model_score: 0.8,
      confidence: 0.25,
      available: true,
      mode: "policy",
      exploration_floor: 0.02,
    });

    expect(rank).toBeCloseTo(0.35, 5);
  });

  it("applies exploration floor only to available policy-ranked proxies", () => {
    const rank = computeRecommendationRankScore({
      heuristic_score: 0,
      model_score: 0,
      confidence: 1,
      available: true,
      mode: "policy",
      exploration_floor: 0.05,
    });

    expect(rank).toBe(0.05);
  });

  it("keeps unavailable proxies below available proxies even in policy mode", () => {
    const rank = computeRecommendationRankScore({
      heuristic_score: 1,
      model_score: 1,
      confidence: 1,
      available: false,
      mode: "policy",
      exploration_floor: 0.05,
    });

    expect(rank).toBeLessThan(0);
  });

  it("parses unknown policy modes as shadow", () => {
    expect(parseRecommendationPolicyMode("policy")).toBe("policy");
    expect(parseRecommendationPolicyMode("shadow")).toBe("shadow");
    expect(parseRecommendationPolicyMode("garbage")).toBe("shadow");
    expect(parseRecommendationPolicyMode(undefined)).toBe("shadow");
  });
});
```

- [ ] **Step 2: Run tests and verify the expected failure**

Run:

```bash
cd JAVDB_AutoSpider_Proxycoordinator
npm run test -- test/recommend_policy.test.ts
```

Expected: FAIL because `computeRecommendationRankScore` and `parseRecommendationPolicyMode` do not exist.

- [ ] **Step 3: Add rank-score helpers**

In `JAVDB_AutoSpider_Proxycoordinator/src/recommend_policy.ts`, add:

```typescript
export type RecommendationPolicyMode = "shadow" | "policy";

export interface RecommendationRankScoreInput {
  heuristic_score: number;
  model_score: number;
  confidence: number;
  available: boolean;
  mode: RecommendationPolicyMode;
  exploration_floor: number;
}

export function parseRecommendationPolicyMode(
  raw: string | undefined,
): RecommendationPolicyMode {
  return raw === "policy" ? "policy" : "shadow";
}

export function computeRecommendationRankScore(
  input: RecommendationRankScoreInput,
): number {
  if (!input.available) {
    return -1;
  }
  const heuristic = clamp(input.heuristic_score, 0, 1);
  if (input.mode === "shadow") {
    return heuristic;
  }
  const model = clamp(input.model_score, 0, 1);
  const confidence = clamp(input.confidence, 0, 1);
  const blended = heuristic * (1 - confidence) + model * confidence;
  const floor = clamp(input.exploration_floor, 0, 0.2);
  return clamp(Math.max(floor, blended), 0, 1);
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
git commit -m "feat(proxy): add recommendation policy rank score"
```

---

## Task 2: Wire Flagged Policy Ordering In `/recommend_proxy`

**Files:**
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/types.ts`
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/index.ts`
- Modify: `JAVDB_AutoSpider_Proxycoordinator/test/recommend_proxy.test.ts`

- [ ] **Step 1: Add env declarations**

In `JAVDB_AutoSpider_Proxycoordinator/src/types.ts`, add these fields to `Env`:

```typescript
  /** ADR-023 Phase 2 - recommendation ranking mode. Default "shadow" keeps
   *  existing heuristic ordering. Set to "policy" to sort by the blended
   *  ADR-023 rank score returned alongside each /recommend_proxy row. */
  RECOMMEND_PROXY_POLICY_MODE?: string;
  /** ADR-023 Phase 2 - minimum rank score for available proxies when policy
   *  mode is enabled. Defaults to 0.02; capped at 0.2 server-side. */
  RECOMMEND_PROXY_EXPLORATION_FLOOR?: string;
```

- [ ] **Step 2: Add route tests for default-off and flag-on behavior**

In `JAVDB_AutoSpider_Proxycoordinator/test/recommend_proxy.test.ts`, extend the recommendation row type with:

```typescript
      rank_score: number;
      ranking_mode: string;
```

Add this helper near the existing `recommend()` helper:

```typescript
async function recommendWithEnv(
  query: string,
  overrides: Record<string, string>,
): Promise<Awaited<ReturnType<typeof recommend>>> {
  const req = new Request(`https://test.invalid/recommend_proxy?${query}`, {
    method: "GET",
    headers: { ...AUTH },
  });
  const ctx = createExecutionContext();
  const res = await worker.fetch(req, { ...env, ...overrides }, ctx);
  await waitOnExecutionContext(ctx);
  const status = res.status;
  const body = (await res.json()) as Awaited<ReturnType<typeof recommend>>["body"];
  return { status, body };
}
```

Add these tests under the ranking describe block:

```typescript
  it("keeps heuristic ordering by default even when model_score disagrees", async () => {
    await lease("R-DEFAULT-HEURISTIC-HIGH");
    await lease("R-DEFAULT-HEURISTIC-LOW");
    for (let i = 0; i < 10; i++) {
      await reportEvent("R-DEFAULT-HEURISTIC-HIGH", "success", { latency_ms: 5000 });
      await reportEvent("R-DEFAULT-HEURISTIC-LOW", "failure");
    }

    const r = await recommend(
      "proxy_ids=R-DEFAULT-HEURISTIC-HIGH,R-DEFAULT-HEURISTIC-LOW&include_unhealthy=1",
    );

    expect(r.body.recommendations[0].ranking_mode).toBe("shadow");
    expect(r.body.recommendations[0].rank_score).toBe(
      r.body.recommendations[0].score,
    );
  });

  it("uses blended policy rank score when RECOMMEND_PROXY_POLICY_MODE=policy", async () => {
    await lease("R-POLICY-FAST");
    await lease("R-POLICY-SLOW");
    for (let i = 0; i < 10; i++) {
      await reportEvent("R-POLICY-FAST", "success", { latency_ms: 100 });
      await reportEvent("R-POLICY-SLOW", "success", { latency_ms: 7000 });
    }

    const r = await recommendWithEnv(
      "proxy_ids=R-POLICY-SLOW,R-POLICY-FAST&include_unhealthy=1",
      {
        RECOMMEND_PROXY_POLICY_MODE: "policy",
        RECOMMEND_PROXY_EXPLORATION_FLOOR: "0.02",
      },
    );

    expect(r.body.recommendations[0].proxy_id).toBe("R-POLICY-FAST");
    expect(r.body.recommendations[0].ranking_mode).toBe("policy");
    expect(r.body.recommendations[0].rank_score).toBeGreaterThan(
      r.body.recommendations[1].rank_score,
    );
  });

  it("keeps banned proxies last in policy mode when include_unhealthy=1", async () => {
    await lease("R-POLICY-AVAILABLE");
    await lease("R-POLICY-BANNED");
    await reportEvent("R-POLICY-AVAILABLE", "success", { latency_ms: 100 });
    await reportEvent("R-POLICY-BANNED", "success", { latency_ms: 100 });
    await reportEvent("R-POLICY-BANNED", "ban", { ttl_ms: 60_000 });

    const r = await recommendWithEnv(
      "proxy_ids=R-POLICY-BANNED,R-POLICY-AVAILABLE&include_unhealthy=1",
      { RECOMMEND_PROXY_POLICY_MODE: "policy" },
    );

    expect(r.body.recommendations[0].proxy_id).toBe("R-POLICY-AVAILABLE");
    expect(r.body.recommendations[1].proxy_id).toBe("R-POLICY-BANNED");
    expect(r.body.recommendations[1].rank_score).toBeLessThan(0);
  });
```

- [ ] **Step 3: Run route tests and verify the expected failure**

Run:

```bash
cd JAVDB_AutoSpider_Proxycoordinator
npm run test -- test/recommend_proxy.test.ts
```

Expected: FAIL because `rank_score` / `ranking_mode` are absent and env mode is not wired.

- [ ] **Step 4: Import ranking helpers in `index.ts`**

Extend the existing import from `./recommend_policy`:

```typescript
import {
  computeGlobalRecommendationBaseline,
  computeRecommendationRankScore,
  computeRecommendationShadow,
  parseRecommendationPolicyMode,
  type RecommendationPolicyInput,
  type RecommendationShadowFields,
} from "./recommend_policy";
```

- [ ] **Step 5: Add env parsing helpers in `index.ts`**

Near `clampNumber()`, add:

```typescript
function parseExplorationFloor(env: Env): number {
  const raw = env.RECOMMEND_PROXY_EXPLORATION_FLOOR;
  const n = raw === undefined || raw === "" ? 0.02 : Number(raw);
  if (!Number.isFinite(n)) return 0.02;
  return Math.min(0.2, Math.max(0, n));
}
```

- [ ] **Step 6: Add `rank_score` and `ranking_mode` to `Recommendation`**

In the local `interface Recommendation` inside `recommendProxies()`, add:

```typescript
    rank_score: number;
    ranking_mode: "shadow" | "policy";
```

- [ ] **Step 7: Compute rank score after shadow fields**

Before constructing `ranked`, add:

```typescript
  const rankingMode = parseRecommendationPolicyMode(env.RECOMMEND_PROXY_POLICY_MODE);
  const explorationFloor = parseExplorationFloor(env);
```

Replace the `const ranked: Recommendation[] = baseRanked.map(...)` block from Phase 1 with:

```typescript
  const ranked: Recommendation[] = baseRanked.map((r) => {
    const shadow = policyByProxyId.get(r.proxy_id)!;
    const rankScore = computeRecommendationRankScore({
      heuristic_score: shadow.heuristic_score,
      model_score: shadow.model_score,
      confidence: shadow.confidence,
      available: r.available,
      mode: rankingMode,
      exploration_floor: explorationFloor,
    });
    return {
      ...r,
      ...shadow,
      rank_score: rankScore,
      ranking_mode: rankingMode,
    };
  });
```

- [ ] **Step 8: Switch sort to rank score while preserving default behavior**

Replace the first comparator line:

```typescript
    if (a.score !== b.score) return b.score - a.score;
```

with:

```typescript
    if (a.rank_score !== b.rank_score) return b.rank_score - a.rank_score;
```

Because `rank_score === score` in shadow mode, default behavior stays unchanged.

- [ ] **Step 9: Run route tests**

Run:

```bash
cd JAVDB_AutoSpider_Proxycoordinator
npm run test -- test/recommend_proxy.test.ts
```

Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add \
  JAVDB_AutoSpider_Proxycoordinator/src/types.ts \
  JAVDB_AutoSpider_Proxycoordinator/src/index.ts \
  JAVDB_AutoSpider_Proxycoordinator/test/recommend_proxy.test.ts
git commit -m "feat(proxy): gate recommendation policy ordering"
```

---

## Task 3: Preserve Python Client Compatibility

**Files:**
- Modify: `tests/unit/test_recommend_proxy_client.py`

- [ ] **Step 1: Extend compatibility payload**

In the `test_recommend_ignores_adr023_shadow_fields()` test added by Phase 1, add these keys to the recommendation row:

```python
                "rank_score": 0.84,
                "ranking_mode": "policy",
```

- [ ] **Step 2: Run compatibility test**

Run:

```bash
pytest tests/unit/test_recommend_proxy_client.py::test_recommend_ignores_adr023_shadow_fields -v
```

Expected: PASS. The Python client continues to use `score` and ignore `rank_score`.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_recommend_proxy_client.py
git commit -m "test(proxy): ignore policy rank fields in recommend client"
```

---

## Task 4: Add Worker Configuration And Docs

**Files:**
- Modify: `JAVDB_AutoSpider_Proxycoordinator/wrangler.toml`
- Modify: `docs/handbook/en/self-hoster/proxy-coordinator.md`
- Modify: `docs/handbook/zh/self-hoster/proxy-coordinator.md`

- [ ] **Step 1: Document default-off vars in `wrangler.toml`**

Under `[vars]` in `JAVDB_AutoSpider_Proxycoordinator/wrangler.toml`, after `PENALTY_WINDOW_SEC`, add:

```toml
# ADR-023 Phase 2 - recommendation ranking mode.
# "shadow" (default): /recommend_proxy keeps existing heuristic ordering and
# only returns policy fields for observability.
# "policy": /recommend_proxy sorts by blended policy rank_score.
RECOMMEND_PROXY_POLICY_MODE = "shadow"
# Minimum rank score for available proxies in policy mode. Keeps a small
# exploration floor so recovered proxies are not permanently starved.
RECOMMEND_PROXY_EXPLORATION_FLOOR = "0.02"
```

- [ ] **Step 2: Update English docs**

In `docs/handbook/en/self-hoster/proxy-coordinator.md`, extend the ADR-023 note added in Phase 1:

```markdown
ADR-023 Phase 2 adds two Worker vars:

| Variable | Default | Meaning |
|---|---|---|
| `RECOMMEND_PROXY_POLICY_MODE` | `"shadow"` | `"shadow"` keeps existing heuristic ordering; `"policy"` sorts by blended `rank_score`. |
| `RECOMMEND_PROXY_EXPLORATION_FLOOR` | `"0.02"` | Minimum rank score for available proxies in policy mode, capped at 0.2 server-side. |

Rollback is a one-line Worker var change: set
`RECOMMEND_PROXY_POLICY_MODE = "shadow"` and redeploy. No Python client change
is required because clients still read the stable `score` field.
```

- [ ] **Step 3: Update Chinese docs**

Mirror the English addition in `docs/handbook/zh/self-hoster/proxy-coordinator.md`:

```markdown
ADR-023 Phase 2 新增两个 Worker 变量：

| 变量 | 默认值 | 含义 |
|---|---|---|
| `RECOMMEND_PROXY_POLICY_MODE` | `"shadow"` | `"shadow"` 保持现有 heuristic 排序；`"policy"` 按 blended `rank_score` 排序。 |
| `RECOMMEND_PROXY_EXPLORATION_FLOOR` | `"0.02"` | policy 模式下可用代理的最低 rank score，服务端最高限制为 0.2。 |

回滚只需要改一个 Worker 变量：设置
`RECOMMEND_PROXY_POLICY_MODE = "shadow"` 并重新部署。Python 客户端无需变更，
因为客户端仍然读取稳定的 `score` 字段。
```

- [ ] **Step 4: Verify doc sync**

Run:

```bash
rg -n "RECOMMEND_PROXY_POLICY_MODE|RECOMMEND_PROXY_EXPLORATION_FLOOR|rank_score" \
  JAVDB_AutoSpider_Proxycoordinator/wrangler.toml \
  docs/handbook/en/self-hoster/proxy-coordinator.md \
  docs/handbook/zh/self-hoster/proxy-coordinator.md
```

Expected: all three files mention both vars and `rank_score`.

- [ ] **Step 5: Commit**

```bash
git add \
  JAVDB_AutoSpider_Proxycoordinator/wrangler.toml \
  docs/handbook/en/self-hoster/proxy-coordinator.md \
  docs/handbook/zh/self-hoster/proxy-coordinator.md
git commit -m "docs(proxy): document recommendation policy rollout flag"
```

---

## Task 5: Workflow Review And Final Verification

**Files:**
- Review only: `.github/workflows/`
- Verify all files touched in this IMP.

- [ ] **Step 1: Review workflow impact**

Run:

```bash
rg -n "Proxycoordinator|wrangler|RECOMMEND_PROXY_POLICY_MODE|RECOMMEND_PROXY_EXPLORATION_FLOOR|npm run test|npm run typecheck" .github/workflows JAVDB_AutoSpider_Proxycoordinator/wrangler.toml
```

Expected: No GitHub Actions change is required. This phase adds Worker vars and TypeScript code only; existing deploy and test jobs remain sufficient.

- [ ] **Step 2: Run focused Worker tests**

Run:

```bash
cd JAVDB_AutoSpider_Proxycoordinator
npm run test -- test/recommend_policy.test.ts test/recommend_proxy.test.ts
```

Expected: PASS.

- [ ] **Step 3: Run Worker typecheck**

Run:

```bash
cd JAVDB_AutoSpider_Proxycoordinator
npm run typecheck
```

Expected: PASS.

- [ ] **Step 4: Run Python compatibility test**

Run:

```bash
pytest tests/unit/test_recommend_proxy_client.py -v
```

Expected: PASS.

- [ ] **Step 5: Confirm default behavior remains heuristic**

Run:

```bash
git diff -- JAVDB_AutoSpider_Proxycoordinator/src/index.ts | rg -n "rankingMode|rank_score|a.rank_score|parseRecommendationPolicyMode"
```

Expected: diff shows `rank_score` is derived from policy helpers, and `parseRecommendationPolicyMode` defaults unknown / missing env values to `"shadow"`.

- [ ] **Step 6: Run scoped diff whitespace check**

Run:

```bash
git diff --check -- \
  JAVDB_AutoSpider_Proxycoordinator/src/types.ts \
  JAVDB_AutoSpider_Proxycoordinator/src/recommend_policy.ts \
  JAVDB_AutoSpider_Proxycoordinator/src/index.ts \
  JAVDB_AutoSpider_Proxycoordinator/test/recommend_policy.test.ts \
  JAVDB_AutoSpider_Proxycoordinator/test/recommend_proxy.test.ts \
  JAVDB_AutoSpider_Proxycoordinator/wrangler.toml \
  tests/unit/test_recommend_proxy_client.py \
  docs/handbook/en/self-hoster/proxy-coordinator.md \
  docs/handbook/zh/self-hoster/proxy-coordinator.md
```

Expected: no output.

- [ ] **Step 7: Commit**

```bash
git add \
  JAVDB_AutoSpider_Proxycoordinator/src/types.ts \
  JAVDB_AutoSpider_Proxycoordinator/src/recommend_policy.ts \
  JAVDB_AutoSpider_Proxycoordinator/src/index.ts \
  JAVDB_AutoSpider_Proxycoordinator/test/recommend_policy.test.ts \
  JAVDB_AutoSpider_Proxycoordinator/test/recommend_proxy.test.ts \
  JAVDB_AutoSpider_Proxycoordinator/wrangler.toml \
  tests/unit/test_recommend_proxy_client.py \
  docs/handbook/en/self-hoster/proxy-coordinator.md \
  docs/handbook/zh/self-hoster/proxy-coordinator.md
git commit -m "feat(proxy): enable flagged recommendation policy ordering"
```
