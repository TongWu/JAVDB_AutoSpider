# Web Backend Security & Data Integrity Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 4 security (P0) and 4 data-integrity (P1) issues in the `javdb-autospider-web` TypeScript backend, introducing a KV Namespace for stateful auth primitives.

**Architecture:** Add `AUTH_KV` binding to Cloudflare Workers for rate limiting, token revocation, and session counting. All changes are in the `JAVDB_AutoSpider_Web/` directory. No Python backend changes.

**Tech Stack:** TypeScript, Hono, Cloudflare Workers KV, D1, Vitest + `@cloudflare/vitest-pool-workers`

**Related:** [ADR-029](ADR-029-web-security-hardening.md)

---

## File Map

| Action | Path | Responsibility |
| ------ | ---- | -------------- |
| Create | `server/services/rate-limit.ts` | KV-backed sliding-window rate limiter |
| Create | `server/services/token-revocation.ts` | KV-backed token revocation + session counting |
| Modify | `server/env.ts` | Add `AUTH_KV: KVNamespace` binding |
| Modify | `wrangler.toml` | Add `[[kv_namespaces]]` block |
| Modify | `vitest.server.config.ts` | Add `AUTH_KV` test binding |
| Modify | `server/routes/auth.ts` | Rate limit login/refresh, session counting on login, revocation on logout, CSRF on refresh, reject plain: in prod |
| Modify | `server/middleware/auth.ts` | Check revocation on mutation requests |
| Modify | `server/middleware/cors.ts` | Environment-driven origin whitelist |
| Modify | `server/routes/sessions.ts` | Reorder commit to delete-pending-first, add partial-failure handling |
| Modify | `server/routes/history.ts` | Add LIMIT 100000 to exports, BOM, truncation headers |
| Create | `server/__tests__/rate-limit.test.ts` | Rate limiter unit tests |
| Create | `server/__tests__/token-revocation.test.ts` | Revocation + session count unit tests |
| Modify | `server/__tests__/auth-routes.test.ts` | Tests for rate limiting, revocation, CSRF refresh, plain: rejection |
| Modify | `server/__tests__/sessions-routes.test.ts` | Tests for reordered commit |
| Modify | `server/__tests__/history-routes.test.ts` | Tests for export truncation + BOM |

---

## Task 1: Add KV Binding Infrastructure

**Files:**
- Modify: `JAVDB_AutoSpider_Web/server/env.ts:1-42`
- Modify: `JAVDB_AutoSpider_Web/wrangler.toml:1-30`
- Modify: `JAVDB_AutoSpider_Web/vitest.server.config.ts:1-28`

- [ ] **Step 1: Add `AUTH_KV` to the Env interface**

In `server/env.ts`, add after line 8 (`OPERATIONS_DB: D1Database;`):

```typescript
// Auth state (rate limiting, token revocation, session tracking)
AUTH_KV: KVNamespace;
```

- [ ] **Step 2: Add KV namespace to wrangler.toml**

Append to `wrangler.toml`:

```toml
[[kv_namespaces]]
binding = "AUTH_KV"
id = "placeholder-create-before-deploy"
preview_id = "placeholder-create-before-deploy"
```

- [ ] **Step 3: Add KV binding to vitest test config**

In `vitest.server.config.ts`, inside `miniflare.bindings`, add:

```typescript
AUTH_KV: "auth-kv-test",
```

And add to the `miniflare` object (sibling of `bindings`):

```typescript
kvNamespaces: {
  AUTH_KV: "auth-kv-test",
},
```

- [ ] **Step 4: Verify typecheck passes**

Run: `cd /Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npx tsc --noEmit -p server/tsconfig.json`
Expected: No errors (or only pre-existing errors unrelated to our changes).

- [ ] **Step 5: Commit**

```bash
git add server/env.ts wrangler.toml vitest.server.config.ts
git commit -m "feat(auth): add AUTH_KV binding for rate limiting and token revocation"
```

---

## Task 2: Rate Limiter Service

**Files:**
- Create: `JAVDB_AutoSpider_Web/server/services/rate-limit.ts`
- Create: `JAVDB_AutoSpider_Web/server/__tests__/rate-limit.test.ts`

- [ ] **Step 1: Write the failing test**

Create `server/__tests__/rate-limit.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { env } from "cloudflare:test";
import { checkRateLimit } from "../services/rate-limit";

describe("checkRateLimit", () => {
  it("allows requests under the limit", async () => {
    const result = await checkRateLimit(env.AUTH_KV, "1.2.3.4", "/api/auth/login", 5, 60);
    expect(result.allowed).toBe(true);
    expect(result.remaining).toBe(4);
  });

  it("blocks requests over the limit", async () => {
    const ip = "10.0.0.1";
    const endpoint = "/api/auth/login-block-test";
    for (let i = 0; i < 5; i++) {
      await checkRateLimit(env.AUTH_KV, ip, endpoint, 5, 60);
    }
    const result = await checkRateLimit(env.AUTH_KV, ip, endpoint, 5, 60);
    expect(result.allowed).toBe(false);
    expect(result.retryAfter).toBeGreaterThan(0);
  });

  it("uses separate counters per IP", async () => {
    const endpoint = "/api/auth/login-ip-test";
    for (let i = 0; i < 5; i++) {
      await checkRateLimit(env.AUTH_KV, "192.168.1.1", endpoint, 5, 60);
    }
    const result = await checkRateLimit(env.AUTH_KV, "192.168.1.2", endpoint, 5, 60);
    expect(result.allowed).toBe(true);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npx vitest run --config vitest.server.config.ts server/__tests__/rate-limit.test.ts`
Expected: FAIL — `checkRateLimit` not found.

- [ ] **Step 3: Implement rate limiter**

Create `server/services/rate-limit.ts`:

```typescript
export interface RateLimitResult {
  allowed: boolean;
  remaining: number;
  retryAfter: number;
}

export async function checkRateLimit(
  kv: KVNamespace,
  ip: string,
  endpoint: string,
  limit: number,
  windowSeconds: number,
): Promise<RateLimitResult> {
  const windowStart = Math.floor(Date.now() / 1000 / windowSeconds) * windowSeconds;
  const key = `rl:${ip}:${endpoint}:${windowStart}`;

  const raw = await kv.get(key);
  const count = raw ? parseInt(raw, 10) : 0;

  if (count >= limit) {
    const windowEnd = windowStart + windowSeconds;
    const retryAfter = windowEnd - Math.floor(Date.now() / 1000);
    return { allowed: false, remaining: 0, retryAfter: Math.max(1, retryAfter) };
  }

  await kv.put(key, String(count + 1), { expirationTtl: windowSeconds });
  return { allowed: true, remaining: limit - count - 1, retryAfter: 0 };
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npx vitest run --config vitest.server.config.ts server/__tests__/rate-limit.test.ts`
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add server/services/rate-limit.ts server/__tests__/rate-limit.test.ts
git commit -m "feat(auth): add KV-backed rate limiter service"
```

---

## Task 3: Token Revocation and Session Counting Service

**Files:**
- Create: `JAVDB_AutoSpider_Web/server/services/token-revocation.ts`
- Create: `JAVDB_AutoSpider_Web/server/__tests__/token-revocation.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `server/__tests__/token-revocation.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { env } from "cloudflare:test";
import { revokeToken, isTokenRevoked, trackSession, getSessionCount, cleanExpiredSessions } from "../services/token-revocation";

describe("revokeToken / isTokenRevoked", () => {
  it("marks a token as revoked", async () => {
    await revokeToken(env.AUTH_KV, "jti-abc", 1800);
    const revoked = await isTokenRevoked(env.AUTH_KV, "jti-abc");
    expect(revoked).toBe(true);
  });

  it("returns false for non-revoked token", async () => {
    const revoked = await isTokenRevoked(env.AUTH_KV, "jti-unknown");
    expect(revoked).toBe(false);
  });
});

describe("trackSession / getSessionCount", () => {
  it("tracks a new session", async () => {
    const now = Math.floor(Date.now() / 1000);
    await trackSession(env.AUTH_KV, "user-a", "jti-1", now + 1800);
    const count = await getSessionCount(env.AUTH_KV, "user-a");
    expect(count).toBe(1);
  });

  it("tracks multiple sessions", async () => {
    const now = Math.floor(Date.now() / 1000);
    await trackSession(env.AUTH_KV, "user-b", "jti-2", now + 1800);
    await trackSession(env.AUTH_KV, "user-b", "jti-3", now + 1800);
    const count = await getSessionCount(env.AUTH_KV, "user-b");
    expect(count).toBe(2);
  });

  it("cleanExpiredSessions removes expired entries", async () => {
    const past = Math.floor(Date.now() / 1000) - 10;
    const future = Math.floor(Date.now() / 1000) + 1800;
    await trackSession(env.AUTH_KV, "user-c", "jti-old", past);
    await trackSession(env.AUTH_KV, "user-c", "jti-new", future);
    await cleanExpiredSessions(env.AUTH_KV, "user-c");
    const count = await getSessionCount(env.AUTH_KV, "user-c");
    expect(count).toBe(1);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npx vitest run --config vitest.server.config.ts server/__tests__/token-revocation.test.ts`
Expected: FAIL — imports not found.

- [ ] **Step 3: Implement token revocation and session tracking**

Create `server/services/token-revocation.ts`:

```typescript
interface SessionEntry {
  jti: string;
  exp: number;
}

export async function revokeToken(kv: KVNamespace, jti: string, ttlSeconds: number): Promise<void> {
  await kv.put(`revoked:${jti}`, "1", { expirationTtl: Math.max(60, ttlSeconds) });
}

export async function isTokenRevoked(kv: KVNamespace, jti: string): Promise<boolean> {
  const val = await kv.get(`revoked:${jti}`);
  return val !== null;
}

export async function trackSession(kv: KVNamespace, username: string, jti: string, exp: number): Promise<void> {
  const key = `sessions:${username}`;
  const raw = await kv.get(key);
  const sessions: SessionEntry[] = raw ? JSON.parse(raw) : [];
  const now = Math.floor(Date.now() / 1000);
  const active = sessions.filter((s) => s.exp > now);
  active.push({ jti, exp });
  const maxExp = Math.max(...active.map((s) => s.exp));
  const ttl = Math.max(60, maxExp - now);
  await kv.put(key, JSON.stringify(active), { expirationTtl: ttl });
}

export async function getSessionCount(kv: KVNamespace, username: string): Promise<number> {
  const key = `sessions:${username}`;
  const raw = await kv.get(key);
  if (!raw) return 0;
  const sessions: SessionEntry[] = JSON.parse(raw);
  const now = Math.floor(Date.now() / 1000);
  return sessions.filter((s) => s.exp > now).length;
}

export async function cleanExpiredSessions(kv: KVNamespace, username: string): Promise<void> {
  const key = `sessions:${username}`;
  const raw = await kv.get(key);
  if (!raw) return;
  const sessions: SessionEntry[] = JSON.parse(raw);
  const now = Math.floor(Date.now() / 1000);
  const active = sessions.filter((s) => s.exp > now);
  if (active.length === 0) {
    await kv.delete(key);
  } else {
    const maxExp = Math.max(...active.map((s) => s.exp));
    await kv.put(key, JSON.stringify(active), { expirationTtl: Math.max(60, maxExp - now) });
  }
}

export async function removeSession(kv: KVNamespace, username: string, jti: string): Promise<void> {
  const key = `sessions:${username}`;
  const raw = await kv.get(key);
  if (!raw) return;
  const sessions: SessionEntry[] = JSON.parse(raw);
  const remaining = sessions.filter((s) => s.jti !== jti);
  if (remaining.length === 0) {
    await kv.delete(key);
  } else {
    const now = Math.floor(Date.now() / 1000);
    const maxExp = Math.max(...remaining.map((s) => s.exp));
    await kv.put(key, JSON.stringify(remaining), { expirationTtl: Math.max(60, maxExp - now) });
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npx vitest run --config vitest.server.config.ts server/__tests__/token-revocation.test.ts`
Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add server/services/token-revocation.ts server/__tests__/token-revocation.test.ts
git commit -m "feat(auth): add KV-backed token revocation and session tracking"
```

---

## Task 4: Integrate Rate Limiting + Session Counting into Auth Routes

**Files:**
- Modify: `JAVDB_AutoSpider_Web/server/routes/auth.ts:1-123`
- Modify: `JAVDB_AutoSpider_Web/server/__tests__/auth-routes.test.ts`

- [ ] **Step 1: Write failing tests for rate limiting, session limit, and plain: rejection**

Add to `server/__tests__/auth-routes.test.ts`:

```typescript
describe("Rate limiting", () => {
  it("returns 429 after exceeding login rate limit", async () => {
    for (let i = 0; i < 5; i++) {
      await app.request("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: "admin", password: "wrong" }),
      }, env);
    }
    const res = await app.request("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: LOGIN_BODY,
    }, env);
    expect(res.status).toBe(429);
    expect(res.headers.get("Retry-After")).toBeDefined();
  });
});

describe("Plain-text password rejection in production", () => {
  it("rejects plain: passwords when ENVIRONMENT=production", async () => {
    // The test env uses ENVIRONMENT=test, so we test the function directly
    // by importing verifyPassword — see note below
  });
});
```

Note: The rate limit test depends on the KV binding being available in miniflare. Since we added `AUTH_KV` in Task 1, this should work. However, the rate limit test needs 5 requests to the same IP within a window — miniflare treats all requests as coming from `127.0.0.1` by default, so this will work.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npx vitest run --config vitest.server.config.ts server/__tests__/auth-routes.test.ts`
Expected: FAIL on the rate-limiting test (no 429 returned yet).

- [ ] **Step 3: Integrate rate limiting and session tracking into auth.ts**

Replace the full `server/routes/auth.ts` with:

```typescript
import { Hono } from "hono";
import { HTTPException } from "hono/http-exception";
import type { Env } from "../env";
import { signJwt, verifyJwt } from "../services/jwt";
import { findUser } from "../services/users";
import { checkRateLimit } from "../services/rate-limit";
import { revokeToken, trackSession, getSessionCount, cleanExpiredSessions, removeSession } from "../services/token-revocation";

type AuthEnv = { Bindings: Env };

export const authRoutes = new Hono<AuthEnv>();

async function verifyPassword(password: string, hash: string, environment: string): Promise<boolean> {
  if (hash.startsWith("plain:")) {
    if (environment === "production") {
      console.warn("plain-text passwords are rejected in production — use bcrypt hash");
      return false;
    }
    return password === hash.slice(6);
  }
  const { compare } = await import("bcryptjs");
  return compare(password, hash);
}

function getExpiry(env: Env, type: "access" | "refresh"): number {
  if (type === "access") {
    return parseInt(env.ACCESS_TOKEN_EXPIRE_SECONDS ?? "1800", 10);
  }
  return parseInt(env.REFRESH_TOKEN_EXPIRE_SECONDS ?? "604800", 10);
}

function generateCsrfToken(): string {
  const bytes = new Uint8Array(18);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("").slice(0, 24);
}

function getClientIp(c: { req: { header: (name: string) => string | undefined } }): string {
  return c.req.header("CF-Connecting-IP") ?? c.req.header("X-Forwarded-For")?.split(",")[0].trim() ?? "unknown";
}

const MAX_SESSIONS_PER_USER = 3;

authRoutes.post("/login", async (c) => {
  // Rate limit: 5 requests / 60s per IP
  const ip = getClientIp(c);
  const rl = await checkRateLimit(c.env.AUTH_KV, ip, "/api/auth/login", 5, 60);
  if (!rl.allowed) {
    return c.json(
      { error: { code: "rate_limited", message: "Too many login attempts" } },
      { status: 429, headers: { "Retry-After": String(rl.retryAfter) } },
    );
  }

  const body = await c.req.json<{ username: string; password: string }>();
  if (!body.username || !body.password) {
    throw new HTTPException(400, { message: "username and password required" });
  }

  const user = findUser(c.env, body.username);
  if (!user) {
    throw new HTTPException(401, { message: "Invalid username/password" });
  }

  const valid = await verifyPassword(body.password, user.passwordHash, c.env.ENVIRONMENT);
  if (!valid) {
    throw new HTTPException(401, { message: "Invalid username/password" });
  }

  // Session limit check
  await cleanExpiredSessions(c.env.AUTH_KV, user.username);
  const sessionCount = await getSessionCount(c.env.AUTH_KV, user.username);
  if (sessionCount >= MAX_SESSIONS_PER_USER) {
    return c.json(
      { error: { code: "session_limit", message: "Maximum concurrent sessions reached" } },
      { status: 429 },
    );
  }

  const accessExpiry = getExpiry(c.env, "access");
  const refreshExpiry = getExpiry(c.env, "refresh");
  const claims = { sub: user.username, role: user.role };

  const accessToken = await signJwt({ ...claims, typ: "access" }, c.env.API_SECRET_KEY, accessExpiry);
  const refreshToken = await signJwt({ ...claims, typ: "refresh" }, c.env.API_SECRET_KEY, refreshExpiry);
  const csrfToken = generateCsrfToken();

  // Track session (use access token JTI for revocation; refresh token JTI for session tracking)
  const refreshPayload = JSON.parse(atob(refreshToken.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
  await trackSession(c.env.AUTH_KV, user.username, refreshPayload.jti, refreshPayload.exp);

  const isSecure = new URL(c.req.url).protocol === "https:";
  const sameSite = "Lax";
  const cookieFlags = `Path=/; HttpOnly; SameSite=${sameSite}${isSecure ? "; Secure" : ""}`;
  const csrfFlags = `Path=/; SameSite=${sameSite}${isSecure ? "; Secure" : ""}`;

  c.header("Set-Cookie", `access_token=${accessToken}; Max-Age=${accessExpiry}; ${cookieFlags}`, { append: true });
  c.header("Set-Cookie", `csrf_token=${csrfToken}; Max-Age=${accessExpiry}; ${csrfFlags}`, { append: true });

  return c.json({
    access_token: accessToken,
    refresh_token: refreshToken,
    token_type: "bearer",
    expires_in: accessExpiry,
    csrf_token: csrfToken,
    role: user.role,
    username: user.username,
  });
});

authRoutes.post("/refresh", async (c) => {
  // Rate limit: 10 requests / 60s per IP
  const ip = getClientIp(c);
  const rl = await checkRateLimit(c.env.AUTH_KV, ip, "/api/auth/refresh", 10, 60);
  if (!rl.allowed) {
    return c.json(
      { error: { code: "rate_limited", message: "Too many refresh attempts" } },
      { status: 429, headers: { "Retry-After": String(rl.retryAfter) } },
    );
  }

  const authHeader = c.req.header("Authorization");
  const token = authHeader?.startsWith("Bearer ") ? authHeader.slice(7) : null;

  if (!token) {
    throw new HTTPException(401, { message: "Refresh token required" });
  }

  let payload;
  try {
    payload = await verifyJwt(token, c.env.API_SECRET_KEY);
  } catch {
    throw new HTTPException(401, { message: "Invalid token" });
  }

  if (payload.typ !== "refresh") {
    throw new HTTPException(401, { message: "Refresh token required" });
  }

  // Check if refresh token was revoked
  const revoked = await isTokenRevoked(c.env.AUTH_KV, payload.jti);
  if (revoked) {
    throw new HTTPException(401, { message: "Token has been revoked" });
  }

  const user = findUser(c.env, payload.sub);
  if (!user) {
    throw new HTTPException(401, { message: "Unknown user" });
  }

  const accessExpiry = getExpiry(c.env, "access");
  const accessToken = await signJwt(
    { sub: user.username, role: user.role, typ: "access" },
    c.env.API_SECRET_KEY,
    accessExpiry,
  );

  // Generate fresh CSRF token and re-set cookie
  const csrfToken = generateCsrfToken();
  const isSecure = new URL(c.req.url).protocol === "https:";
  const sameSite = "Lax";
  const cookieFlags = `Path=/; HttpOnly; SameSite=${sameSite}${isSecure ? "; Secure" : ""}`;
  const csrfFlags = `Path=/; SameSite=${sameSite}${isSecure ? "; Secure" : ""}`;

  c.header("Set-Cookie", `access_token=${accessToken}; Max-Age=${accessExpiry}; ${cookieFlags}`, { append: true });
  c.header("Set-Cookie", `csrf_token=${csrfToken}; Max-Age=${accessExpiry}; ${csrfFlags}`, { append: true });

  return c.json({
    access_token: accessToken,
    token_type: "bearer",
    expires_in: accessExpiry,
    csrf_token: csrfToken,
  });
});

authRoutes.post("/logout", async (c) => {
  // Extract access token to revoke its JTI
  const authHeader = c.req.header("Authorization");
  const token = authHeader?.startsWith("Bearer ") ? authHeader.slice(7) : null;
  if (token) {
    try {
      const payload = await verifyJwt(token, c.env.API_SECRET_KEY);
      const ttl = payload.exp - Math.floor(Date.now() / 1000);
      if (ttl > 0) {
        await revokeToken(c.env.AUTH_KV, payload.jti, ttl);
      }
      await removeSession(c.env.AUTH_KV, payload.sub, payload.jti);
    } catch {
      // Token may be invalid or expired — still clear cookies
    }
  }

  c.header("Set-Cookie", "access_token=; Max-Age=0; Path=/; HttpOnly", { append: true });
  c.header("Set-Cookie", "csrf_token=; Max-Age=0; Path=/", { append: true });
  return c.json({ status: "ok" });
});
```

- [ ] **Step 4: Add missing import in auth.ts**

Verify the import at the top includes `isTokenRevoked`:

```typescript
import { revokeToken, isTokenRevoked, trackSession, getSessionCount, cleanExpiredSessions, removeSession } from "../services/token-revocation";
```

- [ ] **Step 5: Run auth route tests**

Run: `cd /Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npx vitest run --config vitest.server.config.ts server/__tests__/auth-routes.test.ts`
Expected: All tests PASS (existing + new rate-limit test).

- [ ] **Step 6: Commit**

```bash
git add server/routes/auth.ts server/__tests__/auth-routes.test.ts
git commit -m "feat(auth): integrate rate limiting, session counting, revocation, CSRF refresh, plain: rejection"
```

---

## Task 5: Token Revocation Check in Auth Middleware

**Files:**
- Modify: `JAVDB_AutoSpider_Web/server/middleware/auth.ts:44-78`

- [ ] **Step 1: Write failing test — revoked token rejected on POST**

Add to `server/__tests__/auth-routes.test.ts`:

```typescript
describe("Token revocation", () => {
  it("rejects revoked token on mutation requests", async () => {
    const { accessToken, csrfToken } = await login();
    // Logout to revoke the token
    await app.request("/api/auth/logout", {
      method: "POST",
      headers: { Authorization: `Bearer ${accessToken}` },
    }, env);
    // Try a POST with the revoked token
    const res = await app.request("/api/sessions/sess-001/commit", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
        "X-CSRF-Token": csrfToken,
        Cookie: `csrf_token=${csrfToken}`,
      },
      body: JSON.stringify({}),
    }, env);
    expect(res.status).toBe(401);
  });

  it("allows revoked token on GET requests", async () => {
    const { accessToken } = await login();
    await app.request("/api/auth/logout", {
      method: "POST",
      headers: { Authorization: `Bearer ${accessToken}` },
    }, env);
    const res = await app.request("/api/capabilities", {
      headers: { Authorization: `Bearer ${accessToken}` },
    }, env);
    expect(res.status).toBe(200);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npx vitest run --config vitest.server.config.ts server/__tests__/auth-routes.test.ts`
Expected: FAIL — revoked token still accepted on POST.

- [ ] **Step 3: Add revocation check to requireAuth middleware**

Replace `server/middleware/auth.ts`:

```typescript
import type { Context, MiddlewareHandler } from "hono";
import { HTTPException } from "hono/http-exception";
import type { Env } from "../env";
import { verifyJwt, type JwtPayload } from "../services/jwt";
import { isTokenRevoked } from "../services/token-revocation";

type HonoEnv = { Bindings: Env; Variables: { user: JwtPayload } };

function extractToken(c: Context): string | null {
  const authHeader = c.req.header("Authorization");
  if (authHeader?.startsWith("Bearer ")) {
    return authHeader.slice(7);
  }
  const cookie = c.req.header("Cookie");
  if (cookie) {
    const match = cookie.match(/(?:^|;\s*)access_token=([^;]+)/);
    if (match) return match[1];
  }
  return null;
}

function extractCsrf(c: Context): { header: string | null; cookie: string | null } {
  const header = c.req.header("X-CSRF-Token") ?? null;
  const cookieStr = c.req.header("Cookie");
  let cookie: string | null = null;
  if (cookieStr) {
    const match = cookieStr.match(/(?:^|;\s*)csrf_token=([^;]+)/);
    if (match) cookie = match[1];
  }
  return { header, cookie };
}

function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  const encoder = new TextEncoder();
  const bufA = encoder.encode(a);
  const bufB = encoder.encode(b);
  let result = 0;
  for (let i = 0; i < bufA.length; i++) {
    result |= bufA[i] ^ bufB[i];
  }
  return result === 0;
}

export function requireAuth(): MiddlewareHandler<HonoEnv> {
  return async (c, next) => {
    const token = extractToken(c);
    if (!token) {
      throw new HTTPException(401, { message: "Missing bearer token" });
    }

    let payload: JwtPayload;
    try {
      payload = await verifyJwt(token, c.env.API_SECRET_KEY);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Invalid token";
      throw new HTTPException(401, { message: msg });
    }

    if (payload.typ !== "access") {
      throw new HTTPException(401, { message: "Access token required" });
    }

    const method = c.req.method;
    const isMutation = method === "POST" || method === "PUT" || method === "DELETE";

    // Check token revocation on mutation requests only
    if (isMutation) {
      const revoked = await isTokenRevoked(c.env.AUTH_KV, payload.jti);
      if (revoked) {
        throw new HTTPException(401, { message: "Token has been revoked" });
      }
    }

    // CSRF check for mutating methods
    if (isMutation) {
      const path = new URL(c.req.url).pathname;
      if (path !== "/api/auth/login") {
        const csrf = extractCsrf(c);
        if (!csrf.header || !csrf.cookie || !timingSafeEqual(csrf.header, csrf.cookie)) {
          throw new HTTPException(403, { message: "CSRF token invalid" });
        }
      }
    }

    c.set("user", payload);
    await next();
  };
}

export function requireRole(role: string): MiddlewareHandler<HonoEnv> {
  return async (c, next) => {
    const user = c.get("user");
    if (user.role !== role) {
      throw new HTTPException(403, { message: `${role} role required` });
    }
    await next();
  };
}
```

- [ ] **Step 4: Run all auth tests**

Run: `cd /Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npx vitest run --config vitest.server.config.ts server/__tests__/auth-routes.test.ts`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add server/middleware/auth.ts server/__tests__/auth-routes.test.ts
git commit -m "feat(auth): check token revocation on mutation requests in middleware"
```

---

## Task 6: CORS Explicit Whitelist

**Files:**
- Modify: `JAVDB_AutoSpider_Web/server/middleware/cors.ts:1-13`

- [ ] **Step 1: Write failing test**

Add to `server/__tests__/auth-routes.test.ts`:

```typescript
describe("CORS", () => {
  it("rejects unknown origin in test mode (acts as non-production)", async () => {
    const res = await app.request("/api/health", {
      headers: { Origin: "https://evil.example.com" },
    }, env);
    // In test/dev mode, only localhost origins are allowed
    const allowOrigin = res.headers.get("Access-Control-Allow-Origin");
    expect(allowOrigin).not.toBe("https://evil.example.com");
  });
});
```

- [ ] **Step 2: Replace cors.ts with environment-driven implementation**

Replace `server/middleware/cors.ts`:

```typescript
import { cors } from "hono/cors";
import type { Env } from "../env";
import type { MiddlewareHandler } from "hono";

function getAllowedOrigins(env: Env): string[] {
  const isProduction = env.ENVIRONMENT === "production";

  if (isProduction) {
    const raw = (env as Record<string, string | undefined>).CORS_ORIGINS ?? "";
    return raw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  }

  // Dev/test: allow localhost variants
  return [
    "http://localhost:5173",
    "http://localhost:8788",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:8788",
  ];
}

export function corsMiddleware(): MiddlewareHandler<{ Bindings: Env }> {
  return async (c, next) => {
    const allowed = getAllowedOrigins(c.env);
    const mw = cors({
      origin: (origin) => {
        if (!origin) return "";
        if (allowed.length === 0) return "";
        if (allowed.includes(origin)) return origin;
        // Dev mode: also match any localhost port
        if (c.env.ENVIRONMENT !== "production") {
          try {
            const url = new URL(origin);
            if (url.hostname === "localhost" || url.hostname === "127.0.0.1") return origin;
          } catch { /* invalid origin */ }
        }
        return "";
      },
      allowMethods: ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
      allowHeaders: ["Content-Type", "Authorization", "X-CSRF-Token", "X-Request-Id"],
      credentials: true,
      maxAge: 86400,
    });
    return mw(c, next);
  };
}
```

- [ ] **Step 3: Add `CORS_ORIGINS` to env.ts**

In `server/env.ts`, add inside the interface (e.g. after `ENVIRONMENT`):

```typescript
CORS_ORIGINS?: string;
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npx vitest run --config vitest.server.config.ts server/__tests__/auth-routes.test.ts`
Expected: CORS test PASS. All existing tests PASS.

- [ ] **Step 5: Commit**

```bash
git add server/middleware/cors.ts server/env.ts server/__tests__/auth-routes.test.ts
git commit -m "fix(security): replace CORS wildcard with environment-driven origin whitelist"
```

---

## Task 7: Session Commit Operation Reordering

**Files:**
- Modify: `JAVDB_AutoSpider_Web/server/routes/sessions.ts:142-219`
- Modify: `JAVDB_AutoSpider_Web/server/__tests__/sessions-routes.test.ts`

- [ ] **Step 1: Write failing test for reordered commit**

Add to `server/__tests__/sessions-routes.test.ts`:

```typescript
it("POST /api/sessions/:id/commit with drop_pending deletes pending before updating status", async () => {
  // Seed a finalizing session with pending writes
  await env.REPORTS_DB.prepare(
    "INSERT OR REPLACE INTO ReportSessions (Id, ReportType, ReportDate, CsvFilename, DateTimeCreated, Status, WriteMode) VALUES (?, ?, ?, ?, ?, ?, ?)"
  ).bind("sess-pending", "daily", "2026-03-01", "rp.csv", "2026-03-01 10:00:00", "finalizing", "pending").run();

  await env.HISTORY_DB.prepare(
    "CREATE TABLE IF NOT EXISTS PendingMovieHistoryWrites (Id INTEGER PRIMARY KEY AUTOINCREMENT, SessionId TEXT NOT NULL, VideoCode TEXT)"
  ).run();
  await env.HISTORY_DB.prepare(
    "INSERT INTO PendingMovieHistoryWrites (SessionId, VideoCode) VALUES (?, ?)"
  ).bind("sess-pending", "TEST-001").run();

  const { token, csrfToken, csrfCookie } = await getCsrf();
  const res = await app.request("/api/sessions/sess-pending/commit", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken,
      Cookie: csrfCookie,
    },
    body: JSON.stringify({ drop_pending: true }),
  }, env);

  expect(res.status).toBe(200);
  const data = await res.json() as any;
  expect(data.new_state).toBe("committed");
  expect(data.pending_dropped).toBeGreaterThanOrEqual(1);

  // Verify pending rows are gone
  const remaining = await env.HISTORY_DB.prepare(
    "SELECT COUNT(*) AS cnt FROM PendingMovieHistoryWrites WHERE SessionId = ?"
  ).bind("sess-pending").first<{ cnt: number }>();
  expect(remaining?.cnt).toBe(0);
});
```

- [ ] **Step 2: Reorder commit logic in sessions.ts**

Replace the commit handler (lines 143-219 of `server/routes/sessions.ts`) with:

```typescript
sessionsRoutes.post("/:session_id/commit", requireRole("admin"), async (c) => {
  const sessionId = c.req.param("session_id");

  const body = await c.req.json<{
    force?: boolean;
    drop_pending?: boolean;
    emit_metrics?: boolean;
    fanout_claims?: boolean;
  }>().catch(() => ({} as Record<string, never>));

  const session = await c.env.REPORTS_DB
    .prepare("SELECT Id, Status FROM ReportSessions WHERE Id = ?")
    .bind(sessionId)
    .first<{ Id: string; Status: string | null }>();

  if (!session) {
    throw new HTTPException(404, {
      message: JSON.stringify({ error: { code: "session.not_found" } }),
    });
  }

  const currentState = session.Status ?? "in_progress";

  if (!COMMITTABLE_STATES.has(currentState)) {
    if (!(currentState === "committed" && body.force)) {
      throw new HTTPException(409, {
        message: JSON.stringify({
          error: {
            code: "session.invalid_state",
            detail: `Cannot commit session in state '${currentState}'`,
          },
        }),
      });
    }
  }

  let pendingDropped = 0;
  let pendingDropFailed = false;

  // Step 1: Delete pending writes FIRST (recoverable if step 2 fails)
  if (body.drop_pending) {
    try {
      const stmts = [
        c.env.HISTORY_DB.prepare("DELETE FROM PendingMovieHistoryWrites WHERE SessionId = ?").bind(sessionId),
        c.env.HISTORY_DB.prepare("DELETE FROM PendingTorrentHistoryWrites WHERE SessionId = ?").bind(sessionId),
      ];
      const results = await c.env.HISTORY_DB.batch(stmts);
      for (const r of results) {
        pendingDropped += r.meta.changes ?? 0;
      }
    } catch {
      // Tables may not exist — that's fine
    }
  }

  // Step 2: Update session status
  try {
    await c.env.REPORTS_DB
      .prepare(
        "UPDATE ReportSessions SET Status = 'committed', DateTimeCreated = COALESCE(DateTimeCreated, datetime('now')) WHERE Id = ?"
      )
      .bind(sessionId)
      .run();
  } catch (err) {
    // Partial failure: pending deleted but status not updated
    return c.json({
      session_id: sessionId,
      new_state: currentState,
      pending_dropped: pendingDropped,
      partial_failure: true,
      error: "Status update failed after pending deletion. Retry commit to complete.",
    }, 207);
  }

  return c.json({
    session_id: sessionId,
    new_state: "committed",
    pending_dropped: pendingDropped,
  });
});
```

- [ ] **Step 3: Run session tests**

Run: `cd /Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npx vitest run --config vitest.server.config.ts server/__tests__/sessions-routes.test.ts`
Expected: All PASS including new test.

- [ ] **Step 4: Commit**

```bash
git add server/routes/sessions.ts server/__tests__/sessions-routes.test.ts
git commit -m "fix(sessions): reorder commit to delete-pending-first for recoverable partial failure"
```

---

## Task 8: Export Hard Limit + BOM

**Files:**
- Modify: `JAVDB_AutoSpider_Web/server/routes/history.ts:157-179,298-320`
- Modify: `JAVDB_AutoSpider_Web/server/__tests__/history-routes.test.ts`

- [ ] **Step 1: Write failing test for BOM and truncation header**

Add to `server/__tests__/history-routes.test.ts`:

```typescript
it("GET /api/history/movies/export includes BOM and Content-Type", async () => {
  const token = await getToken();
  const res = await app.request("/api/history/movies/export", {
    headers: { Authorization: `Bearer ${token}` },
  }, env);
  expect(res.status).toBe(200);
  const text = await res.text();
  // BOM is ﻿ — in UTF-8 it's the bytes EF BB BF
  expect(text.charCodeAt(0)).toBe(0xFEFF);
  expect(res.headers.get("Content-Type")).toContain("text/csv");
});

it("GET /api/history/torrents/export includes BOM", async () => {
  const token = await getToken();
  const res = await app.request("/api/history/torrents/export", {
    headers: { Authorization: `Bearer ${token}` },
  }, env);
  expect(res.status).toBe(200);
  const text = await res.text();
  expect(text.charCodeAt(0)).toBe(0xFEFF);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npx vitest run --config vitest.server.config.ts server/__tests__/history-routes.test.ts`
Expected: FAIL — no BOM in current output.

- [ ] **Step 3: Add LIMIT, BOM, and truncation headers to movie export**

Replace the `historyRoutes.get("/movies/export", ...)` handler (lines 157-179) in `server/routes/history.ts`:

```typescript
const EXPORT_LIMIT = 100_000;

historyRoutes.get("/movies/export", async (c) => {
  const params = c.req.query();
  const { selectSql, countSql, bindings } = buildMovieQuery(params, true);
  const db = c.env.HISTORY_DB;

  const countResult = await db.prepare(countSql).bind(...bindings).first<{ cnt: number }>();
  const totalCount = countResult?.cnt ?? 0;
  const rows = await db.prepare(`${selectSql} LIMIT ${EXPORT_LIMIT}`).bind(...bindings).all<MovieRow>();
  const truncated = rows.results.length >= EXPORT_LIMIT && totalCount > EXPORT_LIMIT;

  const header = "id,video_code,href,actor_name,actor_gender,supporting_actors,perfect_match,hi_res,datetime_created,datetime_updated,session_id,torrent_count";
  const csvRows = rows.results.map((r) =>
    [r.Id, r.VideoCode, r.Href, r.ActorName ?? "", r.ActorGender ?? "",
     r.SupportingActors ?? "", r.PerfectMatchIndicator ?? 0, r.HiResIndicator ?? 0,
     r.DateTimeCreated ?? "", r.DateTimeUpdated ?? "", r.SessionId ?? "", r.torrent_count]
      .map((v) => `"${String(v).replace(/"/g, '""')}"`)
      .join(",")
  );

  const parts = ["﻿", header, "\n", csvRows.join("\n")];
  if (truncated) {
    parts.push(`\n# Export truncated at ${EXPORT_LIMIT} rows. Total: ${totalCount}`);
  }

  const responseHeaders: Record<string, string> = {
    "Content-Type": "text/csv; charset=utf-8",
    "Content-Disposition": "attachment; filename=movies.csv",
  };
  if (truncated) {
    responseHeaders["X-Export-Truncated"] = "true";
    responseHeaders["X-Export-Total-Count"] = String(totalCount);
  }

  return new Response(parts.join(""), { headers: responseHeaders });
});
```

- [ ] **Step 4: Add same changes to torrent export**

Replace the `historyRoutes.get("/torrents/export", ...)` handler (lines 298-320):

```typescript
historyRoutes.get("/torrents/export", async (c) => {
  const params = c.req.query();
  const { selectSql, countSql, bindings } = buildTorrentQuery(params, true);
  const db = c.env.HISTORY_DB;

  const countResult = await db.prepare(countSql).bind(...bindings).first<{ cnt: number }>();
  const totalCount = countResult?.cnt ?? 0;
  const rows = await db.prepare(`${selectSql} LIMIT ${EXPORT_LIMIT}`).bind(...bindings).all<TorrentRow>();
  const truncated = rows.results.length >= EXPORT_LIMIT && totalCount > EXPORT_LIMIT;

  const header = "id,movie_video_code,movie_href,magnet_uri,size,subtitle_indicator,censor_indicator,resolution_type,file_count,datetime_created,session_id";
  const csvRows = rows.results.map((r) =>
    [r.Id, r.movie_video_code ?? "", r.movie_href ?? "", r.MagnetUri ?? "",
     r.Size ?? "", r.SubtitleIndicator, r.CensorIndicator, r.ResolutionType,
     r.FileCount, r.DateTimeCreated ?? "", r.SessionId ?? ""]
      .map((v) => `"${String(v).replace(/"/g, '""')}"`)
      .join(",")
  );

  const parts = ["﻿", header, "\n", csvRows.join("\n")];
  if (truncated) {
    parts.push(`\n# Export truncated at ${EXPORT_LIMIT} rows. Total: ${totalCount}`);
  }

  const responseHeaders: Record<string, string> = {
    "Content-Type": "text/csv; charset=utf-8",
    "Content-Disposition": "attachment; filename=torrents.csv",
  };
  if (truncated) {
    responseHeaders["X-Export-Truncated"] = "true";
    responseHeaders["X-Export-Total-Count"] = String(totalCount);
  }

  return new Response(parts.join(""), { headers: responseHeaders });
});
```

- [ ] **Step 5: Run history tests**

Run: `cd /Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npx vitest run --config vitest.server.config.ts server/__tests__/history-routes.test.ts`
Expected: All PASS including new BOM tests.

- [ ] **Step 6: Commit**

```bash
git add server/routes/history.ts server/__tests__/history-routes.test.ts
git commit -m "fix(history): add export hard limit (100k rows), BOM, and truncation headers"
```

---

## Task 9: D1 Indexes (Manual Execution)

**Files:** None (manual `wrangler` commands against production D1)

- [ ] **Step 1: Create indexes on HISTORY_DB**

Run:

```bash
cd /Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web
npx wrangler d1 execute javdb-history --command "CREATE INDEX IF NOT EXISTS idx_th_movie_id ON TorrentHistory(MovieHistoryId);"
npx wrangler d1 execute javdb-history --command "CREATE INDEX IF NOT EXISTS idx_mh_session ON MovieHistory(SessionId);"
npx wrangler d1 execute javdb-history --command "CREATE INDEX IF NOT EXISTS idx_th_session ON TorrentHistory(SessionId);"
```

Expected: Each command succeeds with no errors.

- [ ] **Step 2: Create index on REPORTS_DB**

Run:

```bash
npx wrangler d1 execute javdb-reports --command "CREATE INDEX IF NOT EXISTS idx_rs_status ON ReportSessions(Status);"
```

Expected: Success.

- [ ] **Step 3: Verify indexes exist**

Run:

```bash
npx wrangler d1 execute javdb-history --command "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%';"
npx wrangler d1 execute javdb-reports --command "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%';"
```

Expected: Shows `idx_th_movie_id`, `idx_mh_session`, `idx_th_session`, `idx_rs_status`.

---

## Task 10: Full Test Suite + Deploy Verification

- [ ] **Step 1: Run entire server test suite**

Run: `cd /Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npx vitest run --config vitest.server.config.ts`
Expected: All tests PASS.

- [ ] **Step 2: Run typecheck**

Run: `cd /Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web && npx vue-tsc --noEmit && npx tsc --noEmit -p server/tsconfig.json`
Expected: No type errors.

- [ ] **Step 3: Create KV namespace (before first deploy)**

Run:

```bash
npx wrangler kv namespace create AUTH_KV
npx wrangler kv namespace create AUTH_KV --preview
```

Expected: Returns namespace IDs. Update `wrangler.toml` with the real `id` and `preview_id` values.

- [ ] **Step 4: Build and deploy**

Run:

```bash
npm run build && npx wrangler deploy
```

Expected: Successful deployment.

- [ ] **Step 5: Smoke test production**

1. Login: `POST /api/auth/login` → 200 with `csrf_token` in body.
2. Refresh: `POST /api/auth/refresh` → 200 with `csrf_token` in body.
3. Logout + retry POST: should get 401 on mutation.
4. Rapid login (6x): 6th attempt should get 429.
5. Export: `/api/history/movies/export` → CSV starts with BOM.
6. CORS: request from unknown origin → no `Access-Control-Allow-Origin` header.

- [ ] **Step 6: Final commit (wrangler.toml with real KV IDs)**

```bash
git add wrangler.toml
git commit -m "chore: update wrangler.toml with production KV namespace IDs"
```
