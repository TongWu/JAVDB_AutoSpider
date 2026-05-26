# IMP-ADR023-01: ADR-023 Phase 1 - Shadow Score And Confidence Fields

**Status:** Proposed

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship ADR-023 Phase 1 by adding stability-weighted shadow scoring, confidence, and structured explanation fields to `/recommend_proxy` without changing proxy ordering.

**Architecture:** Keep `/lease` and `/report` unchanged. Add a pure TypeScript policy module that derives `model_score`, `confidence`, `reason_code`, `cooldown_until`, and `model_version` from the existing `/do/state` snapshots already read by `/recommend_proxy`. The endpoint still sorts and filters by the current heuristic `score`; shadow fields are appended for observability only.

**Tech Stack:** TypeScript, Cloudflare Workers, Durable Objects, Vitest + `@cloudflare/vitest-pool-workers`, Python 3.11, pytest.

**Source spec:** [ADR-023](ADR-023-proxy-recommendation-policy.md), D1-D9.

**Non-negotiable:** This phase must not change ranking behavior, `/lease` behavior, `/report` behavior, proxy cooldown semantics, Python client selection behavior, or Worker bindings. New response fields are optional and backward compatible.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `JAVDB_AutoSpider_Proxycoordinator/src/recommend_policy.ts` | Pure shadow-scoring policy, baseline computation, reason codes, and model version. |
| Create | `JAVDB_AutoSpider_Proxycoordinator/test/recommend_policy.test.ts` | Unit tests for reward weighting, confidence, cooldown, and global baseline behavior. |
| Modify | `JAVDB_AutoSpider_Proxycoordinator/src/index.ts` | Attach shadow fields in `/recommend_proxy` while preserving existing ranking and filtering. |
| Modify | `JAVDB_AutoSpider_Proxycoordinator/test/recommend_proxy.test.ts` | Route-level tests proving shadow fields exist and ordering is unchanged. |
| Modify | `tests/unit/test_recommend_proxy_client.py` | Python compatibility test proving unknown shadow fields are ignored. |
| Modify | `docs/handbook/en/self-hoster/proxy-coordinator.md` | Document optional `/recommend_proxy` shadow fields. |
| Modify | `docs/handbook/zh/self-hoster/proxy-coordinator.md` | Chinese mirror of the operator documentation update. |

## Scope Boundaries

- Do not add Workers AI, Vectorize, ONNX, TensorFlow, or LLM calls.
- Do not store new policy state in Durable Object storage in this phase.
- Do not add new GitHub Actions workflows.
- Do not add a policy rollout flag that changes ranking. Phase 2 owns behavior-changing ordering.
- Do not change Python `ProxyRecommendation` fields unless compatibility breaks. The existing defensive parser can ignore new keys.

---

## Task 1: Add The Pure Shadow Policy Module

**Files:**
- Create: `JAVDB_AutoSpider_Proxycoordinator/src/recommend_policy.ts`
- Create: `JAVDB_AutoSpider_Proxycoordinator/test/recommend_policy.test.ts`

- [ ] **Step 1: Write failing unit tests**

Create `JAVDB_AutoSpider_Proxycoordinator/test/recommend_policy.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import {
  RECOMMEND_POLICY_MODEL_VERSION,
  computeGlobalRecommendationBaseline,
  computeRecommendationShadow,
  type RecommendationPolicyInput,
} from "../src/recommend_policy";

function input(overrides: Partial<RecommendationPolicyInput>): RecommendationPolicyInput {
  return {
    proxy_id: "P",
    heuristic_score: 0.5,
    latency_ema_ms: 0,
    success_count: 0,
    failure_count: 0,
    banned: false,
    banned_until: null,
    requires_cf_bypass: false,
    cf_bypass_until: null,
    available: true,
    ...overrides,
  };
}

describe("recommend_policy shadow scoring", () => {
  it("keeps unseen proxies neutral with zero confidence", () => {
    const rows = [input({ proxy_id: "P-NEW" })];
    const baseline = computeGlobalRecommendationBaseline(rows);
    const shadow = computeRecommendationShadow(rows[0], baseline, 1_000);

    expect(shadow.heuristic_score).toBe(0.5);
    expect(shadow.model_score).toBe(0.5);
    expect(shadow.confidence).toBe(0);
    expect(shadow.reason_code).toBe("low_confidence_prior");
    expect(shadow.cooldown_until).toBeNull();
    expect(shadow.model_version).toBe(RECOMMEND_POLICY_MODEL_VERSION);
  });

  it("rewards stable low-latency success history", () => {
    const rows = [
      input({
        proxy_id: "P-STABLE",
        heuristic_score: 0.9,
        success_count: 10,
        failure_count: 0,
        latency_ema_ms: 100,
      }),
    ];
    const baseline = computeGlobalRecommendationBaseline(rows);
    const shadow = computeRecommendationShadow(rows[0], baseline, 1_000);

    expect(shadow.model_score).toBe(1);
    expect(shadow.confidence).toBeCloseTo(10 / 30, 5);
    expect(shadow.reason_code).toBe("stable_recently");
  });

  it("penalizes proxies that underperform the global baseline", () => {
    const rows = [
      input({
        proxy_id: "P-BAD",
        heuristic_score: 0.4,
        success_count: 2,
        failure_count: 8,
        latency_ema_ms: 500,
      }),
      input({
        proxy_id: "P-GOOD",
        heuristic_score: 0.9,
        success_count: 10,
        failure_count: 0,
        latency_ema_ms: 100,
      }),
    ];
    const baseline = computeGlobalRecommendationBaseline(rows);
    const bad = computeRecommendationShadow(rows[0], baseline, 1_000);
    const good = computeRecommendationShadow(rows[1], baseline, 1_000);

    expect(baseline.failure_rate).toBeCloseTo(8 / 20, 5);
    expect(bad.model_score).toBeLessThan(0.1);
    expect(bad.reason_code).toBe("proxy_underperforming");
    expect(good.model_score).toBeGreaterThan(bad.model_score);
  });

  it("marks global pool instability separately from proxy-local blame", () => {
    const rows = [
      input({ proxy_id: "P-A", success_count: 0, failure_count: 10 }),
      input({ proxy_id: "P-B", success_count: 0, failure_count: 10 }),
    ];
    const baseline = computeGlobalRecommendationBaseline(rows);
    const shadow = computeRecommendationShadow(rows[0], baseline, 1_000);

    expect(baseline.unstable_pool).toBe(true);
    expect(shadow.reason_code).toBe("global_pool_unstable");
    expect(shadow.confidence).toBeCloseTo((10 / 30) * 0.5, 5);
  });

  it("keeps cooldown information for banned proxies", () => {
    const rows = [
      input({
        proxy_id: "P-BANNED",
        heuristic_score: 1,
        success_count: 20,
        failure_count: 0,
        banned: true,
        banned_until: 123_456,
      }),
    ];
    const baseline = computeGlobalRecommendationBaseline(rows);
    const shadow = computeRecommendationShadow(rows[0], baseline, 1_000);

    expect(shadow.model_score).toBeCloseTo(0.55, 5);
    expect(shadow.reason_code).toBe("banned_cooldown");
    expect(shadow.cooldown_until).toBe(123_456);
  });

  it("keeps cooldown information for cf-bypass proxies", () => {
    const rows = [
      input({
        proxy_id: "P-CF",
        heuristic_score: 0.7,
        success_count: 10,
        failure_count: 0,
        requires_cf_bypass: true,
        cf_bypass_until: 0,
      }),
    ];
    const baseline = computeGlobalRecommendationBaseline(rows);
    const shadow = computeRecommendationShadow(rows[0], baseline, 1_000);

    expect(shadow.model_score).toBeCloseTo(0.75, 5);
    expect(shadow.reason_code).toBe("cf_bypass_cooldown");
    expect(shadow.cooldown_until).toBe(0);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd JAVDB_AutoSpider_Proxycoordinator
npm run test -- test/recommend_policy.test.ts
```

Expected: FAIL with an import error for `../src/recommend_policy`.

- [ ] **Step 3: Implement `recommend_policy.ts`**

Create `JAVDB_AutoSpider_Proxycoordinator/src/recommend_policy.ts`:

```typescript
export const RECOMMEND_POLICY_MODEL_VERSION = "adr023-shadow-v1";

export type RecommendReasonCode =
  | "low_confidence_prior"
  | "stable_recently"
  | "proxy_underperforming"
  | "banned_cooldown"
  | "cf_bypass_cooldown"
  | "global_pool_unstable";

export interface RecommendationPolicyInput {
  proxy_id: string;
  heuristic_score: number;
  latency_ema_ms: number;
  success_count: number;
  failure_count: number;
  banned: boolean;
  banned_until: number | null;
  requires_cf_bypass: boolean;
  cf_bypass_until: number | null;
  available: boolean;
}

export interface GlobalRecommendationBaseline {
  sample_count: number;
  success_count: number;
  failure_count: number;
  failure_rate: number;
  unstable_pool: boolean;
}

export interface RecommendationShadowFields {
  heuristic_score: number;
  model_score: number;
  confidence: number;
  reason_code: RecommendReasonCode;
  cooldown_until: number | null;
  model_version: string;
}

function clamp(raw: number, min: number, max: number): number {
  if (!Number.isFinite(raw)) return min;
  if (raw < min) return min;
  if (raw > max) return max;
  return raw;
}

function sampleCount(input: RecommendationPolicyInput): number {
  return Math.max(0, input.success_count) + Math.max(0, input.failure_count);
}

export function computeGlobalRecommendationBaseline(
  inputs: RecommendationPolicyInput[],
): GlobalRecommendationBaseline {
  let success = 0;
  let failure = 0;
  for (const input of inputs) {
    success += Math.max(0, input.success_count);
    failure += Math.max(0, input.failure_count);
  }
  const total = success + failure;
  const failureRate = total > 0 ? failure / total : 0;
  return {
    sample_count: total,
    success_count: success,
    failure_count: failure,
    failure_rate: failureRate,
    unstable_pool: total >= 6 && failureRate >= 0.65,
  };
}

export function computeRecommendationShadow(
  input: RecommendationPolicyInput,
  baseline: GlobalRecommendationBaseline,
  _nowMs: number,
): RecommendationShadowFields {
  const heuristicScore = clamp(input.heuristic_score, 0, 1);
  const count = sampleCount(input);
  const successRate = count > 0 ? Math.max(0, input.success_count) / count : 0.5;
  const failureRate = count > 0 ? Math.max(0, input.failure_count) / count : baseline.failure_rate;
  const relativeFailurePenalty = Math.max(0, failureRate - baseline.failure_rate) * 0.45;
  const latency = input.latency_ema_ms > 0 ? input.latency_ema_ms : 500;
  const latencyPenalty = clamp((latency - 500) / 10_000, 0, 0.35);
  const cooldownPenalty = input.banned ? 0.45 : input.requires_cf_bypass ? 0.25 : 0;
  const modelScore =
    count === 0
      ? 0.5
      : clamp(successRate - relativeFailurePenalty - latencyPenalty - cooldownPenalty, 0, 1);

  let confidence = count / (count + 20);
  if (baseline.unstable_pool) {
    confidence *= 0.5;
  }

  let reasonCode: RecommendReasonCode;
  if (input.banned) {
    reasonCode = "banned_cooldown";
  } else if (input.requires_cf_bypass) {
    reasonCode = "cf_bypass_cooldown";
  } else if (baseline.unstable_pool) {
    reasonCode = "global_pool_unstable";
  } else if (count < 3) {
    reasonCode = "low_confidence_prior";
  } else if (failureRate > baseline.failure_rate + 0.2) {
    reasonCode = "proxy_underperforming";
  } else {
    reasonCode = "stable_recently";
  }

  const cooldownUntil = input.banned
    ? input.banned_until
    : input.requires_cf_bypass
      ? input.cf_bypass_until
      : null;

  return {
    heuristic_score: heuristicScore,
    model_score: modelScore,
    confidence: clamp(confidence, 0, 1),
    reason_code: reasonCode,
    cooldown_until: cooldownUntil,
    model_version: RECOMMEND_POLICY_MODEL_VERSION,
  };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
cd JAVDB_AutoSpider_Proxycoordinator
npm run test -- test/recommend_policy.test.ts
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add JAVDB_AutoSpider_Proxycoordinator/src/recommend_policy.ts JAVDB_AutoSpider_Proxycoordinator/test/recommend_policy.test.ts
git commit -m "feat(proxy): add shadow recommendation scoring policy"
```

---

## Task 2: Attach Shadow Fields To `/recommend_proxy`

**Files:**
- Modify: `JAVDB_AutoSpider_Proxycoordinator/src/index.ts`
- Modify: `JAVDB_AutoSpider_Proxycoordinator/test/recommend_proxy.test.ts`

- [ ] **Step 1: Extend route test expectations**

In `JAVDB_AutoSpider_Proxycoordinator/test/recommend_proxy.test.ts`, update the local `recommend()` response type so recommendations include the optional shadow fields:

```typescript
    recommendations: Array<{
      proxy_id: string;
      score: number;
      heuristic_score: number;
      model_score: number;
      confidence: number;
      reason_code: string;
      cooldown_until: number | null;
      model_version: string;
      banned: boolean;
      available: boolean;
    }>;
```

Add this test under `describe("W5.5 /recommend_proxy — ranking", () => { ... })`:

```typescript
  it("adds ADR-023 shadow scoring fields without changing heuristic score", async () => {
    await lease("R-SHADOW-GOOD");
    await lease("R-SHADOW-BAD");
    for (let i = 0; i < 10; i++) {
      await reportEvent("R-SHADOW-GOOD", "success", { latency_ms: 100 });
    }
    for (let i = 0; i < 10; i++) {
      await reportEvent("R-SHADOW-BAD", "failure");
    }

    const r = await recommend("proxy_ids=R-SHADOW-GOOD,R-SHADOW-BAD");

    expect(r.body.recommendations.map((rec) => rec.proxy_id)).toEqual([
      "R-SHADOW-GOOD",
      "R-SHADOW-BAD",
    ]);
    for (const rec of r.body.recommendations) {
      expect(rec.heuristic_score).toBe(rec.score);
      expect(rec.model_score).toBeGreaterThanOrEqual(0);
      expect(rec.model_score).toBeLessThanOrEqual(1);
      expect(rec.confidence).toBeGreaterThanOrEqual(0);
      expect(rec.confidence).toBeLessThanOrEqual(1);
      expect(rec.reason_code).toMatch(
        /stable_recently|proxy_underperforming|global_pool_unstable|low_confidence_prior|banned_cooldown|cf_bypass_cooldown/,
      );
      expect(rec.model_version).toBe("adr023-shadow-v1");
    }
  });
```

Add this test next to the existing `include_unhealthy=1` banned proxy test:

```typescript
  it("returns cooldown_until for banned proxies when included", async () => {
    await lease("R-COOLDOWN-VISIBLE");
    await reportEvent("R-COOLDOWN-VISIBLE", "ban", { ttl_ms: 60_000 });

    const r = await recommend("proxy_ids=R-COOLDOWN-VISIBLE&include_unhealthy=1");
    const rec = r.body.recommendations[0];

    expect(rec.proxy_id).toBe("R-COOLDOWN-VISIBLE");
    expect(rec.banned).toBe(true);
    expect(rec.reason_code).toBe("banned_cooldown");
    expect(typeof rec.cooldown_until).toBe("number");
    expect(rec.cooldown_until).toBeGreaterThan(r.body.server_time);
  });
```

- [ ] **Step 2: Run route tests and verify the expected failure**

Run:

```bash
cd JAVDB_AutoSpider_Proxycoordinator
npm run test -- test/recommend_proxy.test.ts
```

Expected: FAIL because `/recommend_proxy` does not yet emit `heuristic_score`, `model_score`, `confidence`, `reason_code`, `cooldown_until`, or `model_version`.

- [ ] **Step 3: Import the policy module in `index.ts`**

At the top of `JAVDB_AutoSpider_Proxycoordinator/src/index.ts`, add:

```typescript
import {
  computeGlobalRecommendationBaseline,
  computeRecommendationShadow,
  type RecommendationPolicyInput,
  type RecommendationShadowFields,
} from "./recommend_policy";
```

- [ ] **Step 4: Extend the `Recommendation` interface inside `recommendProxies()`**

Replace the local `interface Recommendation` with:

```typescript
  interface Recommendation extends RecommendationShadowFields {
    proxy_id: string;
    score: number;
    latency_ema_ms: number;
    success_count: number;
    failure_count: number;
    banned: boolean;
    banned_until: number | null;
    requires_cf_bypass: boolean;
    cf_bypass_until: number | null;
    available: boolean;
  }
```

Add this helper near `clampNumber()`:

```typescript
function nullableEpochMs(raw: unknown): number | null {
  if (raw === null || raw === undefined) return null;
  const n = typeof raw === "number" ? raw : Number(raw);
  if (!Number.isFinite(n) || n < 0) return null;
  return Math.floor(n);
}
```

- [ ] **Step 5: Build base recommendations, compute baseline, then append shadow fields**

Inside `recommendProxies()`, replace the current `const ranked: Recommendation[] = states.map(...)` block with this two-stage version:

```typescript
  const baseRanked = states.map((s) => {
    const banned = Boolean(s.banned);
    const requires_cf_bypass = Boolean(s.requires_cf_bypass);
    const healthy = !s.error && !banned;
    const h =
      typeof s.health === "object" && s.health !== null
        ? (s.health as {
            score?: number;
            latency_ema_ms?: number;
            success_count?: number;
            failure_count?: number;
          })
        : null;
    const score = clampNumber(h?.score, 0.5, 0, 1);
    return {
      proxy_id: String(s.proxy_id),
      score: banned ? -1 : score,
      latency_ema_ms: clampNumber(h?.latency_ema_ms, 0, 0, 60_000),
      success_count: clampNumber(h?.success_count, 0, 0, Number.MAX_SAFE_INTEGER),
      failure_count: clampNumber(h?.failure_count, 0, 0, Number.MAX_SAFE_INTEGER),
      banned,
      banned_until: nullableEpochMs(s.bannedUntil),
      requires_cf_bypass,
      cf_bypass_until: nullableEpochMs(s.cfBypassUntil),
      available: healthy,
    };
  });

  const policyInputs: RecommendationPolicyInput[] = baseRanked.map((r) => ({
    proxy_id: r.proxy_id,
    heuristic_score: r.score < 0 ? 0 : r.score,
    latency_ema_ms: r.latency_ema_ms,
    success_count: r.success_count,
    failure_count: r.failure_count,
    banned: r.banned,
    banned_until: r.banned_until,
    requires_cf_bypass: r.requires_cf_bypass,
    cf_bypass_until: r.cf_bypass_until,
    available: r.available,
  }));
  const baseline = computeGlobalRecommendationBaseline(policyInputs);
  const policyByProxyId = new Map(
    policyInputs.map((input) => [
      input.proxy_id,
      computeRecommendationShadow(input, baseline, Date.now()),
    ]),
  );

  const ranked: Recommendation[] = baseRanked.map((r) => ({
    ...r,
    ...policyByProxyId.get(r.proxy_id)!,
  }));
```

Do not change the existing sort:

```typescript
  ranked.sort((a, b) => {
    if (a.score !== b.score) return b.score - a.score;
    if (a.latency_ema_ms !== b.latency_ema_ms) {
      return a.latency_ema_ms - b.latency_ema_ms;
    }
    return a.proxy_id.localeCompare(b.proxy_id);
  });
```

The sort must continue to use `score`, not `model_score`.

- [ ] **Step 6: Run route tests and verify they pass**

Run:

```bash
cd JAVDB_AutoSpider_Proxycoordinator
npm run test -- test/recommend_proxy.test.ts
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add JAVDB_AutoSpider_Proxycoordinator/src/index.ts JAVDB_AutoSpider_Proxycoordinator/test/recommend_proxy.test.ts
git commit -m "feat(proxy): expose shadow recommendation fields"
```

---

## Task 3: Lock Python Client Backward Compatibility

**Files:**
- Modify: `tests/unit/test_recommend_proxy_client.py`

- [ ] **Step 1: Add compatibility test**

In `tests/unit/test_recommend_proxy_client.py`, add this test after `test_recommend_returns_typed_recommendations()`:

```python
def test_recommend_ignores_adr023_shadow_fields():
    c = _make_client()
    body = {
        "recommendations": [
            {
                "proxy_id": "P-1",
                "score": 0.9,
                "heuristic_score": 0.9,
                "model_score": 0.73,
                "confidence": 0.42,
                "reason_code": "stable_recently",
                "cooldown_until": None,
                "model_version": "adr023-shadow-v1",
                "latency_ema_ms": 120.0,
                "success_count": 200,
                "failure_count": 5,
                "banned": False,
                "requires_cf_bypass": False,
                "available": True,
            }
        ],
        "queried_proxy_ids": ["P-1"],
        "server_time": 1234,
    }
    try:
        with patch.object(c._session, "get", return_value=_mock_response(200, body)):
            r = c.recommend(["P-1"])
        assert len(r.recommendations) == 1
        assert r.recommendations[0].proxy_id == "P-1"
        assert r.recommendations[0].score == pytest.approx(0.9)
        assert r.recommendations[0].available is True
    finally:
        c.close()
```

- [ ] **Step 2: Run test and verify it passes**

Run:

```bash
pytest tests/unit/test_recommend_proxy_client.py -v
```

Expected: PASS. If it fails, fix only the defensive decode path in `javdb/proxy/recommend/client.py`; do not add shadow fields to the Python dataclass in this phase.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_recommend_proxy_client.py
git commit -m "test(proxy): preserve recommend client compatibility"
```

---

## Task 4: Update Operator Documentation

**Files:**
- Modify: `docs/handbook/en/self-hoster/proxy-coordinator.md`
- Modify: `docs/handbook/zh/self-hoster/proxy-coordinator.md`

- [ ] **Step 1: Update English docs**

In `docs/handbook/en/self-hoster/proxy-coordinator.md`, section `18.2 Protocol Changes (Backward Compatible)`, add this bullet after the `LeaseResponse` bullet:

```markdown
- `GET /recommend_proxy` recommendation rows add optional ADR-023 shadow
  fields: `heuristic_score`, `model_score`, `confidence`, `reason_code`,
  `cooldown_until`, and `model_version`. Phase 1 does **not** sort by
  `model_score`; the existing `score` field remains the ranking source.
```

In section `18.4 Tuning Suggestions`, add this paragraph after the existing health-score formula:

```markdown
ADR-023 Phase 1 adds shadow policy fields for observability only. Operators can
compare `heuristic_score` and `model_score` in `/recommend_proxy` responses to
understand where the policy would disagree, but proxy ordering remains
unchanged until the later rollout-flag phase.
```

- [ ] **Step 2: Update Chinese docs**

In `docs/handbook/zh/self-hoster/proxy-coordinator.md`, mirror the English additions in the corresponding `18.2 Protocol Changes (Backward Compatible)` and `18.4 Tuning Suggestions` sections:

```markdown
- `GET /recommend_proxy` 的 recommendation 行新增 ADR-023 的可选 shadow
  字段：`heuristic_score`、`model_score`、`confidence`、`reason_code`、
  `cooldown_until` 和 `model_version`。Phase 1 **不会**按 `model_score`
  排序；现有 `score` 字段仍然是排序依据。
```

```markdown
ADR-023 Phase 1 只把 shadow policy 字段用于可观测性。运维可以在
`/recommend_proxy` 响应中对比 `heuristic_score` 和 `model_score`，观察
policy 会在哪些场景下产生分歧；真正改变代理排序要等后续 rollout flag
阶段。
```

- [ ] **Step 3: Verify paired docs**

Run:

```bash
rg -n "ADR-023|heuristic_score|model_score|reason_code" docs/handbook/en/self-hoster/proxy-coordinator.md docs/handbook/zh/self-hoster/proxy-coordinator.md
```

Expected: both English and Chinese files mention the new optional fields.

- [ ] **Step 4: Commit**

```bash
git add docs/handbook/en/self-hoster/proxy-coordinator.md docs/handbook/zh/self-hoster/proxy-coordinator.md
git commit -m "docs(proxy): document shadow recommendation fields"
```

---

## Task 5: Review Workflow Impact

**Files:**
- Review only: `.github/workflows/`
- Review only: `JAVDB_AutoSpider_Proxycoordinator/wrangler.toml`
- Review only: `JAVDB_AutoSpider_Proxycoordinator/package.json`

- [ ] **Step 1: Confirm no workflow changes are needed**

Run:

```bash
rg -n "Proxycoordinator|ProxyCoordinator|recommend_proxy|wrangler|npm run test|npm run typecheck" .github/workflows JAVDB_AutoSpider_Proxycoordinator/wrangler.toml JAVDB_AutoSpider_Proxycoordinator/package.json
```

Expected: existing deploy/test commands remain sufficient. This phase adds no bindings, secrets, scheduled jobs, or workflow inputs.

- [ ] **Step 2: Record the review in the task summary**

No file edit is required if Step 1 confirms no workflow impact. In the implementation summary, state: "GitHub Actions reviewed; no workflow change required because Phase 1 adds only Worker code and optional response fields."

---

## Task 6: Final Verification

**Files:**
- Verify all files touched in this IMP.

- [ ] **Step 1: Run focused Worker tests**

Run:

```bash
cd JAVDB_AutoSpider_Proxycoordinator
npm run test -- test/recommend_policy.test.ts test/recommend_proxy.test.ts
```

Expected: PASS.

- [ ] **Step 2: Run Worker typecheck**

Run:

```bash
cd JAVDB_AutoSpider_Proxycoordinator
npm run typecheck
```

Expected: PASS.

- [ ] **Step 3: Run Python compatibility test**

Run from repo root:

```bash
pytest tests/unit/test_recommend_proxy_client.py -v
```

Expected: PASS.

- [ ] **Step 4: Run scoped diff whitespace check**

Run:

```bash
git diff --check -- \
  JAVDB_AutoSpider_Proxycoordinator/src/recommend_policy.ts \
  JAVDB_AutoSpider_Proxycoordinator/src/index.ts \
  JAVDB_AutoSpider_Proxycoordinator/test/recommend_policy.test.ts \
  JAVDB_AutoSpider_Proxycoordinator/test/recommend_proxy.test.ts \
  tests/unit/test_recommend_proxy_client.py \
  docs/handbook/en/self-hoster/proxy-coordinator.md \
  docs/handbook/zh/self-hoster/proxy-coordinator.md
```

Expected: no output.

- [ ] **Step 5: Confirm behavior boundary**

Run:

```bash
git diff -- JAVDB_AutoSpider_Proxycoordinator/src/index.ts | rg -n "model_score|heuristic_score|ranked.sort|a.score|model_score"
```

Expected: the diff shows new shadow fields, and the sort still compares `a.score` / `b.score`, not `model_score`.

- [ ] **Step 6: Commit**

```bash
git add \
  JAVDB_AutoSpider_Proxycoordinator/src/recommend_policy.ts \
  JAVDB_AutoSpider_Proxycoordinator/src/index.ts \
  JAVDB_AutoSpider_Proxycoordinator/test/recommend_policy.test.ts \
  JAVDB_AutoSpider_Proxycoordinator/test/recommend_proxy.test.ts \
  tests/unit/test_recommend_proxy_client.py \
  docs/handbook/en/self-hoster/proxy-coordinator.md \
  docs/handbook/zh/self-hoster/proxy-coordinator.md
git commit -m "feat(proxy): add shadow recommendation scoring"
```

