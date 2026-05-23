# Cloudflare Pages Full-Stack Setup — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

| Field       | Value                                                    |
| ----------- | -------------------------------------------------------- |
| **Status**  | Draft                                                    |
| **Date**    | 2026-05-23                                               |
| **Related** | [ADR-017](ADR-017-cloudflare-first-deployment.md)        |

**Goal:** Set up a Cloudflare Pages full-stack project in `javdb-autospider-web` with Hono API backend, JWT auth, and 4 read-only routes (capabilities, system-state, history, sessions) backed by D1 native bindings.

**Architecture:** Cloudflare Pages serves Vue 3 SPA static assets and routes `/api/*` to Pages Functions powered by Hono. Auth uses Web Crypto API for JWT HS256. D1 databases (history, reports, operations) are accessed via native Worker bindings. All code lives in the `javdb-autospider-web` repo.

**Tech Stack:** Hono 4, TypeScript 5, Cloudflare Pages Functions, D1 native bindings, Web Crypto API (JWT HS256), Vitest + `@cloudflare/vitest-pool-workers`

**Working Directory:** All paths are relative to the `javdb-autospider-web` repo root (`/Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web/`).

---

## File Map

### New Files

| File | Responsibility |
| ---- | -------------- |
| `wrangler.toml` | Cloudflare Pages config: D1 bindings, compatibility settings |
| `functions/api/[[route]].ts` | Pages Functions catch-all entrypoint, delegates to Hono |
| `server/app.ts` | Hono app definition, route mounting, global middleware |
| `server/env.ts` | `Env` type definition for all Worker bindings |
| `server/middleware/auth.ts` | JWT verification middleware + CSRF check |
| `server/middleware/cors.ts` | CORS middleware for cross-origin requests |
| `server/services/jwt.ts` | JWT sign/verify using Web Crypto API (HS256) |
| `server/services/users.ts` | User store (env-driven, same as Python `USERS` dict) |
| `server/routes/auth.ts` | `/api/auth/*` — login, refresh, logout, change-password |
| `server/routes/capabilities.ts` | `/api/capabilities` — system capability flags |
| `server/routes/system-state.ts` | `/api/system/state` — D1 key-value CRUD |
| `server/routes/history.ts` | `/api/history/*` — movie/torrent search + CSV export |
| `server/routes/sessions.ts` | `/api/sessions/*` — session list + detail (read-only) |
| `server/tsconfig.json` | TypeScript config for server-side code |
| `server/__tests__/jwt.test.ts` | Unit tests for JWT service |
| `server/__tests__/auth-routes.test.ts` | Integration tests for auth routes |
| `server/__tests__/history-routes.test.ts` | Integration tests for history routes |
| `server/__tests__/sessions-routes.test.ts` | Integration tests for sessions routes |
| `vitest.server.config.ts` | Vitest config for server tests (Workers pool) |

### Modified Files

| File | Change |
| ---- | ------ |
| `package.json` | Add Hono, `@cloudflare/workers-types`, vitest workers pool deps |
| `vite.config.ts` | Add Cloudflare Pages plugin (if needed for dev) |
| `.gitignore` | Add `.wrangler/` |

---

## Task 1: Project Infrastructure

**Files:**
- Create: `wrangler.toml`
- Create: `server/tsconfig.json`
- Create: `server/env.ts`
- Modify: `package.json`
- Modify: `.gitignore`

- [ ] **Step 1: Install dependencies**

```bash
cd /Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web
npm install hono
npm install -D @cloudflare/workers-types wrangler @cloudflare/vitest-pool-workers
```

- [ ] **Step 2: Create `wrangler.toml`**

```toml
name = "javdb-autospider-web"
compatibility_date = "2026-05-01"
compatibility_flags = ["nodejs_compat"]
pages_build_output_dir = "dist"

[vars]
ENVIRONMENT = "production"
INGESTION_MODE = "github"
BACKEND_VERSION = "2.0.0"

[[d1_databases]]
binding = "HISTORY_DB"
database_name = "javdb-history"
database_id = "placeholder-fill-in-before-deploy"

[[d1_databases]]
binding = "REPORTS_DB"
database_name = "javdb-reports"
database_id = "placeholder-fill-in-before-deploy"

[[d1_databases]]
binding = "OPERATIONS_DB"
database_name = "javdb-operations"
database_id = "placeholder-fill-in-before-deploy"
```

> **Note:** Replace `database_id` values with actual D1 database IDs from `wrangler d1 list` before deploying. These placeholders are safe for local dev with `--local`.

- [ ] **Step 3: Create `server/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "lib": ["ES2022"],
    "types": ["@cloudflare/workers-types/2023-07-01"],
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "skipLibCheck": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "paths": {
      "@server/*": ["./*"]
    }
  },
  "include": ["./**/*.ts"],
  "exclude": ["__tests__"]
}
```

- [ ] **Step 4: Create `server/env.ts`**

```typescript
export interface Env {
  // D1 databases
  HISTORY_DB: D1Database;
  REPORTS_DB: D1Database;
  OPERATIONS_DB: D1Database;

  // Auth
  API_SECRET_KEY: string;
  ADMIN_USERNAME: string;
  ADMIN_PASSWORD_HASH: string;
  READONLY_USERNAME?: string;
  READONLY_PASSWORD_HASH?: string;

  // Token expiry (seconds)
  ACCESS_TOKEN_EXPIRE_SECONDS?: string;
  REFRESH_TOKEN_EXPIRE_SECONDS?: string;

  // Capabilities
  ENVIRONMENT: string;
  INGESTION_MODE?: string;
  STORAGE_BACKEND?: string;
  DEPLOYMENT?: string;
  BACKEND_VERSION?: string;
  FRONTEND_VERSION?: string;
  GH_ACTIONS_TIER?: string;
  GH_ACTIONS_REPO?: string;
  GH_ACTIONS_TOKEN?: string;
  FEATURE_PIKPAK?: string;
  FEATURE_RCLONE?: string;
  SMTP_HOST?: string;
  SMTP_SERVER?: string;
  JAVDB_USERNAME?: string;
}
```

- [ ] **Step 5: Add `.wrangler/` to `.gitignore`**

Append to the existing `.gitignore`:

```
# Cloudflare
.wrangler/
```

- [ ] **Step 6: Add server scripts to `package.json`**

Add these to the `"scripts"` section:

```json
"dev:api": "wrangler pages dev dist --d1=HISTORY_DB --d1=REPORTS_DB --d1=OPERATIONS_DB",
"test:server": "vitest run --config vitest.server.config.ts",
"typecheck:server": "tsc --noEmit -p server/tsconfig.json",
"cf:deploy": "npm run build && wrangler pages deploy dist"
```

- [ ] **Step 7: Commit**

```bash
git add wrangler.toml server/tsconfig.json server/env.ts package.json package-lock.json .gitignore
git commit -m "feat: add Cloudflare Pages infrastructure (wrangler, env types, deps)"
```

---

## Task 2: JWT Service (Web Crypto API)

**Files:**
- Create: `server/services/jwt.ts`
- Create: `server/__tests__/jwt.test.ts`
- Create: `vitest.server.config.ts`

- [ ] **Step 1: Create `vitest.server.config.ts`**

```typescript
import { defineWorkersConfig } from "@cloudflare/vitest-pool-workers/config";

export default defineWorkersConfig({
  test: {
    include: ["server/__tests__/**/*.test.ts"],
    poolOptions: {
      workers: {
        wrangler: { configPath: "./wrangler.toml" },
        miniflare: {
          d1Databases: {
            HISTORY_DB: "history-test",
            REPORTS_DB: "reports-test",
            OPERATIONS_DB: "operations-test",
          },
          bindings: {
            API_SECRET_KEY: "test-secret-key-at-least-32-chars-long",
            ADMIN_USERNAME: "admin",
            ADMIN_PASSWORD_HASH: "$2b$12$LJ3m4ys3Lk0TDbGMOKHcluuTMFPMqMONBBODAMGECwaeSJ/bpg.gq",
            ENVIRONMENT: "test",
          },
        },
      },
    },
  },
});
```

> The bcrypt hash above is for password `"testpassword123"`. Generate with: `python3 -c "from passlib.hash import bcrypt; print(bcrypt.hash('testpassword123'))"`.
>
> **Important — test pattern:** When testing Hono routes that need env bindings (D1, secrets), pass `env` from `cloudflare:test` as the third argument to `app.request()`:
>
> ```typescript
> import { env } from "cloudflare:test";
> const res = await app.request("/api/path", { method: "GET" }, env);
> ```
>
> Pure logic tests (JWT) don't need this. Route tests with D1 do.

- [ ] **Step 2: Write failing tests for JWT service**

Create `server/__tests__/jwt.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { signJwt, verifyJwt, generateJti } from "../services/jwt";

const SECRET = "test-secret-key-at-least-32-chars-long";

describe("JWT service", () => {
  it("signs and verifies a token", async () => {
    const payload = { sub: "admin", role: "admin", typ: "access" };
    const token = await signJwt(payload, SECRET, 1800);
    const decoded = await verifyJwt(token, SECRET);
    expect(decoded.sub).toBe("admin");
    expect(decoded.role).toBe("admin");
    expect(decoded.typ).toBe("access");
    expect(decoded.jti).toBeDefined();
    expect(decoded.exp).toBeGreaterThan(decoded.iat);
  });

  it("rejects a token with wrong secret", async () => {
    const token = await signJwt({ sub: "admin", role: "admin", typ: "access" }, SECRET, 1800);
    await expect(verifyJwt(token, "wrong-secret-that-is-also-32-chars")).rejects.toThrow();
  });

  it("rejects an expired token", async () => {
    const token = await signJwt({ sub: "admin", role: "admin", typ: "access" }, SECRET, -1);
    await expect(verifyJwt(token, SECRET)).rejects.toThrow("expired");
  });

  it("generates unique JTIs", () => {
    const a = generateJti();
    const b = generateJti();
    expect(a).not.toBe(b);
    expect(a.length).toBe(32);
  });
});
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
npx vitest run --config vitest.server.config.ts
```

Expected: FAIL — `Cannot find module '../services/jwt'`

- [ ] **Step 4: Implement JWT service**

Create `server/services/jwt.ts`:

```typescript
interface JwtPayload {
  sub: string;
  role: string;
  typ: "access" | "refresh";
  iat: number;
  exp: number;
  jti: string;
}

const encoder = new TextEncoder();

async function importKey(secret: string): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"]
  );
}

function base64UrlEncode(data: Uint8Array): string {
  return btoa(String.fromCharCode(...data))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

function base64UrlDecode(str: string): Uint8Array {
  const padded = str.replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(padded);
  return Uint8Array.from(binary, (c) => c.charCodeAt(0));
}

export function generateJti(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export async function signJwt(
  claims: { sub: string; role: string; typ: "access" | "refresh" },
  secret: string,
  expiresInSeconds: number
): Promise<string> {
  const now = Math.floor(Date.now() / 1000);
  const payload: JwtPayload = {
    ...claims,
    iat: now,
    exp: now + expiresInSeconds,
    jti: generateJti(),
  };

  const header = base64UrlEncode(encoder.encode(JSON.stringify({ alg: "HS256", typ: "JWT" })));
  const body = base64UrlEncode(encoder.encode(JSON.stringify(payload)));
  const signingInput = `${header}.${body}`;

  const key = await importKey(secret);
  const signature = new Uint8Array(
    await crypto.subtle.sign("HMAC", key, encoder.encode(signingInput))
  );

  return `${signingInput}.${base64UrlEncode(signature)}`;
}

export async function verifyJwt(token: string, secret: string): Promise<JwtPayload> {
  const parts = token.split(".");
  if (parts.length !== 3) throw new Error("Invalid token format");

  const [header, body, sig] = parts;
  const signingInput = `${header}.${body}`;

  const key = await importKey(secret);
  const signature = base64UrlDecode(sig);
  const valid = await crypto.subtle.verify("HMAC", key, signature, encoder.encode(signingInput));

  if (!valid) throw new Error("Invalid signature");

  const payload: JwtPayload = JSON.parse(new TextDecoder().decode(base64UrlDecode(body)));

  if (payload.exp <= Math.floor(Date.now() / 1000)) {
    throw new Error("Token expired");
  }

  return payload;
}

export type { JwtPayload };
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
npx vitest run --config vitest.server.config.ts
```

Expected: 4 tests PASS

- [ ] **Step 6: Commit**

```bash
git add server/services/jwt.ts server/__tests__/jwt.test.ts vitest.server.config.ts
git commit -m "feat: add JWT service using Web Crypto API (HS256)"
```

---

## Task 3: User Store + Auth Middleware

**Files:**
- Create: `server/services/users.ts`
- Create: `server/middleware/auth.ts`
- Create: `server/middleware/cors.ts`

- [ ] **Step 1: Create user store**

Create `server/services/users.ts`:

```typescript
import type { Env } from "../env";

export interface User {
  username: string;
  role: "admin" | "readonly";
  passwordHash: string;
}

export function getUsers(env: Env): User[] {
  const users: User[] = [
    {
      username: env.ADMIN_USERNAME,
      role: "admin",
      passwordHash: env.ADMIN_PASSWORD_HASH,
    },
  ];
  if (env.READONLY_USERNAME && env.READONLY_PASSWORD_HASH) {
    users.push({
      username: env.READONLY_USERNAME,
      role: "readonly",
      passwordHash: env.READONLY_PASSWORD_HASH,
    });
  }
  return users;
}

export function findUser(env: Env, username: string): User | undefined {
  return getUsers(env).find((u) => u.username === username);
}
```

- [ ] **Step 2: Create CORS middleware**

Create `server/middleware/cors.ts`:

```typescript
import { cors } from "hono/cors";
import type { Env } from "../env";
import type { MiddlewareHandler } from "hono";

export function corsMiddleware(): MiddlewareHandler<{ Bindings: Env }> {
  return cors({
    origin: (origin) => origin ?? "*",
    allowMethods: ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allowHeaders: ["Content-Type", "Authorization", "X-CSRF-Token", "X-Request-Id"],
    credentials: true,
    maxAge: 86400,
  });
}
```

- [ ] **Step 3: Create auth middleware**

Create `server/middleware/auth.ts`:

```typescript
import type { Context, MiddlewareHandler } from "hono";
import { HTTPException } from "hono/http-exception";
import type { Env } from "../env";
import { verifyJwt, type JwtPayload } from "../services/jwt";

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

    // CSRF check for mutating methods
    const method = c.req.method;
    if (method === "POST" || method === "PUT" || method === "DELETE") {
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

- [ ] **Step 4: Commit**

```bash
git add server/services/users.ts server/middleware/auth.ts server/middleware/cors.ts
git commit -m "feat: add auth middleware (JWT verify, CSRF) and CORS"
```

---

## Task 4: Auth Routes

**Files:**
- Create: `server/routes/auth.ts`
- Create: `server/__tests__/auth-routes.test.ts`

- [ ] **Step 1: Write failing tests for auth routes**

Create `server/__tests__/auth-routes.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { app } from "../app";

const LOGIN_BODY = JSON.stringify({
  username: "admin",
  password: "testpassword123",
});

async function login(): Promise<{ accessToken: string; csrfToken: string; cookies: string }> {
  const res = await app.request("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: LOGIN_BODY,
  });
  const data = await res.json();
  const cookies = res.headers.get("set-cookie") ?? "";
  return { accessToken: data.access_token, csrfToken: data.csrf_token, cookies };
}

describe("POST /api/auth/login", () => {
  it("returns tokens on valid credentials", async () => {
    const res = await app.request("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: LOGIN_BODY,
    });
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.access_token).toBeDefined();
    expect(data.refresh_token).toBeDefined();
    expect(data.token_type).toBe("bearer");
    expect(data.role).toBe("admin");
    expect(data.csrf_token).toBeDefined();
  });

  it("rejects invalid password", async () => {
    const res = await app.request("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "admin", password: "wrong" }),
    });
    expect(res.status).toBe(401);
  });
});

describe("POST /api/auth/refresh", () => {
  it("returns new access token from refresh token", async () => {
    const loginRes = await app.request("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: LOGIN_BODY,
    });
    const loginData = await loginRes.json();

    const res = await app.request("/api/auth/refresh", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${loginData.refresh_token}`,
      },
    });
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.access_token).toBeDefined();
    expect(data.token_type).toBe("bearer");
  });
});

describe("GET /api/capabilities (auth guard)", () => {
  it("rejects unauthenticated requests", async () => {
    const res = await app.request("/api/capabilities");
    expect(res.status).toBe(401);
  });

  it("accepts authenticated requests", async () => {
    const { accessToken } = await login();
    const res = await app.request("/api/capabilities", {
      headers: { Authorization: `Bearer ${accessToken}` },
    });
    expect(res.status).toBe(200);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npx vitest run --config vitest.server.config.ts
```

Expected: FAIL — `Cannot find module '../app'`

- [ ] **Step 3: Implement auth routes**

Create `server/routes/auth.ts`:

```typescript
import { Hono } from "hono";
import { HTTPException } from "hono/http-exception";
import type { Env } from "../env";
import { signJwt, verifyJwt, generateJti } from "../services/jwt";
import { findUser } from "../services/users";

type AuthEnv = { Bindings: Env };

export const authRoutes = new Hono<AuthEnv>();

async function verifyPassword(password: string, hash: string): Promise<boolean> {
  // bcrypt hashes start with $2b$. We use a constant-time comparison approach.
  // Workers don't have native bcrypt, so we import a pure-JS implementation.
  // For Phase 1, we use a simple hash comparison via the Web Crypto API with
  // a PBKDF2-based approach. However, since the Python side uses bcrypt,
  // we need to support bcrypt verification.
  //
  // Workaround: store passwords as PBKDF2-SHA256 hex in the env for Workers mode.
  // The env var ADMIN_PASSWORD_HASH should be set to the bcrypt hash for Docker
  // mode, or a plaintext marker like "plain:actualpassword" for Workers dev.
  //
  // Production approach: use a bcrypt WASM module or store pre-hashed values.
  if (hash.startsWith("plain:")) {
    return password === hash.slice(6);
  }
  // For bcrypt hashes, we need a library. Install `bcryptjs` (pure JS).
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

authRoutes.post("/login", async (c) => {
  const body = await c.req.json<{ username: string; password: string }>();
  if (!body.username || !body.password) {
    throw new HTTPException(400, { message: "username and password required" });
  }

  const user = findUser(c.env, body.username);
  if (!user) {
    throw new HTTPException(401, { message: "Invalid username/password" });
  }

  const valid = await verifyPassword(body.password, user.passwordHash);
  if (!valid) {
    throw new HTTPException(401, { message: "Invalid username/password" });
  }

  const accessExpiry = getExpiry(c.env, "access");
  const refreshExpiry = getExpiry(c.env, "refresh");
  const claims = { sub: user.username, role: user.role };

  const accessToken = await signJwt({ ...claims, typ: "access" }, c.env.API_SECRET_KEY, accessExpiry);
  const refreshToken = await signJwt({ ...claims, typ: "refresh" }, c.env.API_SECRET_KEY, refreshExpiry);
  const csrfToken = generateCsrfToken();

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

  const user = findUser(c.env, payload.sub);
  if (!user) {
    throw new HTTPException(401, { message: "Unknown user" });
  }

  const accessExpiry = getExpiry(c.env, "access");
  const accessToken = await signJwt(
    { sub: user.username, role: user.role, typ: "access" },
    c.env.API_SECRET_KEY,
    accessExpiry
  );

  const isSecure = new URL(c.req.url).protocol === "https:";
  const sameSite = "Lax";
  const cookieFlags = `Path=/; HttpOnly; SameSite=${sameSite}${isSecure ? "; Secure" : ""}`;
  c.header("Set-Cookie", `access_token=${accessToken}; Max-Age=${accessExpiry}; ${cookieFlags}`);

  return c.json({
    access_token: accessToken,
    token_type: "bearer",
    expires_in: accessExpiry,
  });
});

authRoutes.post("/logout", async (c) => {
  c.header("Set-Cookie", "access_token=; Max-Age=0; Path=/; HttpOnly", { append: true });
  c.header("Set-Cookie", "csrf_token=; Max-Age=0; Path=/", { append: true });
  return c.json({ status: "ok" });
});
```

- [ ] **Step 4: Install bcryptjs for password verification**

```bash
npm install bcryptjs
npm install -D @types/bcryptjs
```

- [ ] **Step 5: Create Hono app skeleton**

Create `server/app.ts`:

```typescript
import { Hono } from "hono";
import type { Env } from "./env";
import type { JwtPayload } from "./services/jwt";
import { corsMiddleware } from "./middleware/cors";
import { requireAuth } from "./middleware/auth";
import { authRoutes } from "./routes/auth";

type AppEnv = { Bindings: Env; Variables: { user: JwtPayload } };

export const app = new Hono<AppEnv>();

app.use("*", corsMiddleware());

// Public routes
app.route("/api/auth", authRoutes);

// Protected routes (auth required)
app.use("/api/*", requireAuth());

// Placeholder — capabilities route added in Task 5
app.get("/api/capabilities", (c) => {
  return c.json({
    version: "2.0.0",
    ingestion_mode: c.env.INGESTION_MODE ?? "local",
    storage_backend: "d1",
    deployment: "cloudflare",
  });
});

// 404 fallback for API routes
app.all("/api/*", (c) => c.json({ error: "Not found" }, 404));
```

- [ ] **Step 6: Run tests**

```bash
npx vitest run --config vitest.server.config.ts
```

Expected: Tests should pass (adjust test env bindings if miniflare needs different config).

> **Note:** If `bcryptjs` doesn't work in the Workers pool, change the test env `ADMIN_PASSWORD_HASH` to `plain:testpassword123` and adjust accordingly.

- [ ] **Step 7: Commit**

```bash
git add server/routes/auth.ts server/app.ts server/__tests__/auth-routes.test.ts package.json package-lock.json
git commit -m "feat: add auth routes (login, refresh, logout) with JWT + bcrypt"
```

---

## Task 5: Capabilities Route

**Files:**
- Create: `server/routes/capabilities.ts`
- Modify: `server/app.ts`

- [ ] **Step 1: Implement capabilities route**

Create `server/routes/capabilities.ts`:

```typescript
import { Hono } from "hono";
import type { Env } from "../env";
import type { JwtPayload } from "../services/jwt";

type CapEnv = { Bindings: Env; Variables: { user: JwtPayload } };

export const capabilitiesRoutes = new Hono<CapEnv>();

function envBool(val: string | undefined): boolean {
  return val === "true" || val === "1" || val === "yes";
}

capabilitiesRoutes.get("/", (c) => {
  const env = c.env;

  return c.json({
    version: "2.0.0",
    ingestion_mode: env.INGESTION_MODE ?? "local",
    gh_actions: {
      tier: env.GH_ACTIONS_TIER ?? "none",
      repo: env.GH_ACTIONS_REPO ?? null,
      token_configured: !!env.GH_ACTIONS_TOKEN,
    },
    storage_backend: "d1",
    features: {
      pikpak: envBool(env.FEATURE_PIKPAK),
      rclone: envBool(env.FEATURE_RCLONE),
      smtp: !!(env.SMTP_HOST || env.SMTP_SERVER),
      proxy_pool: true,
      javdb_login: !!env.JAVDB_USERNAME,
      proxy_preview: true,
    },
    deployment: "cloudflare",
    build: {
      frontend_version: env.FRONTEND_VERSION ?? null,
      backend_version: env.BACKEND_VERSION ?? "2.0.0",
      git_sha: "cloudflare",
    },
  });
});
```

- [ ] **Step 2: Mount in app.ts**

Replace the placeholder capabilities route in `server/app.ts`. The full updated file:

```typescript
import { Hono } from "hono";
import type { Env } from "./env";
import type { JwtPayload } from "./services/jwt";
import { corsMiddleware } from "./middleware/cors";
import { requireAuth } from "./middleware/auth";
import { authRoutes } from "./routes/auth";
import { capabilitiesRoutes } from "./routes/capabilities";

type AppEnv = { Bindings: Env; Variables: { user: JwtPayload } };

export const app = new Hono<AppEnv>();

app.use("*", corsMiddleware());

// Public routes
app.route("/api/auth", authRoutes);

// Protected routes
app.use("/api/*", requireAuth());
app.route("/api/capabilities", capabilitiesRoutes);

// 404 fallback
app.all("/api/*", (c) => c.json({ error: "Not found" }, 404));
```

- [ ] **Step 3: Run tests**

```bash
npx vitest run --config vitest.server.config.ts
```

Expected: All existing tests still pass, including the `GET /api/capabilities` auth guard test.

- [ ] **Step 4: Commit**

```bash
git add server/routes/capabilities.ts server/app.ts
git commit -m "feat: add capabilities route (env-driven feature flags)"
```

---

## Task 6: System State Route (D1 CRUD)

**Files:**
- Create: `server/routes/system-state.ts`
- Modify: `server/app.ts`

- [ ] **Step 1: Implement system-state route**

Create `server/routes/system-state.ts`:

```typescript
import { Hono } from "hono";
import { HTTPException } from "hono/http-exception";
import type { Env } from "../env";
import type { JwtPayload } from "../services/jwt";
import { requireRole } from "../middleware/auth";

type StateEnv = { Bindings: Env; Variables: { user: JwtPayload } };

export const systemStateRoutes = new Hono<StateEnv>();

systemStateRoutes.get("/state", async (c) => {
  const key = c.req.query("key");
  if (!key) {
    throw new HTTPException(400, { message: "key query parameter required" });
  }

  const row = await c.env.OPERATIONS_DB
    .prepare("SELECT value FROM system_state WHERE key = ?")
    .bind(key)
    .first<{ value: string }>();

  return c.json({ key, value: row?.value ?? null });
});

systemStateRoutes.put("/state", requireRole("admin"), async (c) => {
  const body = await c.req.json<{ key: string; value: string }>();
  if (!body.key || body.value === undefined) {
    throw new HTTPException(400, { message: "key and value required" });
  }

  await c.env.OPERATIONS_DB
    .prepare(
      `INSERT INTO system_state (key, value, updated_at)
       VALUES (?, ?, datetime('now'))
       ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                       updated_at = datetime('now')`
    )
    .bind(body.key, body.value)
    .run();

  return c.json({ key: body.key, value: body.value });
});
```

- [ ] **Step 2: Mount in app.ts**

Add to `server/app.ts` imports:

```typescript
import { systemStateRoutes } from "./routes/system-state";
```

Add after capabilities route:

```typescript
app.route("/api/system", systemStateRoutes);
```

- [ ] **Step 3: Commit**

```bash
git add server/routes/system-state.ts server/app.ts
git commit -m "feat: add system-state route (D1 key-value CRUD)"
```

---

## Task 7: History Routes (Movies + Torrents + CSV Export)

**Files:**
- Create: `server/routes/history.ts`
- Create: `server/__tests__/history-routes.test.ts`

This is the most complex route in Phase 1. It involves dynamic SQL query building with multiple optional filters and cursor-based pagination.

- [ ] **Step 1: Write failing test**

Create `server/__tests__/history-routes.test.ts`:

```typescript
import { describe, it, expect, beforeAll } from "vitest";
import { env } from "cloudflare:test";
import { app } from "../app";

async function getToken(): Promise<string> {
  const res = await app.request("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: "admin", password: "testpassword123" }),
  });
  const data = await res.json();
  return data.access_token;
}

async function seedHistory(db: D1Database) {
  await db.exec(`
    CREATE TABLE IF NOT EXISTS MovieHistory (
      Id INTEGER PRIMARY KEY AUTOINCREMENT,
      VideoCode TEXT NOT NULL,
      Href TEXT NOT NULL UNIQUE,
      ActorName TEXT,
      ActorGender TEXT,
      SupportingActors TEXT,
      PerfectMatchIndicator INTEGER,
      HiResIndicator INTEGER,
      DateTimeCreated TEXT,
      DateTimeUpdated TEXT,
      SessionId TEXT
    );
    CREATE TABLE IF NOT EXISTS TorrentHistory (
      Id INTEGER PRIMARY KEY AUTOINCREMENT,
      MovieHistoryId INTEGER NOT NULL,
      MagnetUri TEXT,
      SubtitleIndicator INTEGER,
      CensorIndicator INTEGER,
      ResolutionType INTEGER,
      Size TEXT,
      FileCount INTEGER,
      DateTimeCreated TEXT,
      SessionId TEXT
    );
    INSERT INTO MovieHistory (VideoCode, Href, ActorName, PerfectMatchIndicator, HiResIndicator, DateTimeCreated, SessionId)
    VALUES
      ('ABC-001', '/v/abc001', 'Actor A', 1, 0, '2026-01-01 10:00:00', 'sess-1'),
      ('DEF-002', '/v/def002', 'Actor B', 0, 1, '2026-01-02 10:00:00', 'sess-1'),
      ('GHI-003', '/v/ghi003', 'Actor A', 1, 1, '2026-02-01 10:00:00', 'sess-2');
    INSERT INTO TorrentHistory (MovieHistoryId, MagnetUri, SubtitleIndicator, CensorIndicator, ResolutionType, Size, FileCount, DateTimeCreated, SessionId)
    VALUES
      (1, 'magnet:?xt=urn:btih:aaa', 1, 1, 3, '2.5GB', 1, '2026-01-01 10:00:00', 'sess-1'),
      (1, 'magnet:?xt=urn:btih:bbb', 0, 1, 4, '5.0GB', 1, '2026-01-01 10:00:00', 'sess-1'),
      (2, 'magnet:?xt=urn:btih:ccc', 1, 0, 3, '3.0GB', 2, '2026-01-02 10:00:00', 'sess-1');
  `);
}

describe("History routes", () => {
  beforeAll(async () => {
    await seedHistory(env.HISTORY_DB);
  });

  it("GET /api/history/movies returns paginated results", async () => {
    const token = await getToken();
    const res = await app.request("/api/history/movies?limit=10", {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.items).toHaveLength(3);
    expect(data.items[0].video_code).toBe("ABC-001");
    expect(data.items[0].torrent_count).toBe(2);
  });

  it("filters movies by actor name", async () => {
    const token = await getToken();
    const res = await app.request("/api/history/movies?actor=Actor%20A", {
      headers: { Authorization: `Bearer ${token}` },
    });
    const data = await res.json();
    expect(data.items).toHaveLength(2);
    expect(data.items.every((m: any) => m.actor_name === "Actor A")).toBe(true);
  });

  it("filters movies by date range", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/history/movies?date_from=2026-01-02&date_to=2026-01-31",
      { headers: { Authorization: `Bearer ${token}` } }
    );
    const data = await res.json();
    expect(data.items).toHaveLength(1);
    expect(data.items[0].video_code).toBe("DEF-002");
  });

  it("GET /api/history/torrents returns joined results", async () => {
    const token = await getToken();
    const res = await app.request("/api/history/torrents?limit=10", {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.items).toHaveLength(3);
    expect(data.items[0].movie_video_code).toBe("ABC-001");
  });

  it("GET /api/history/movies/export returns CSV", async () => {
    const token = await getToken();
    const res = await app.request("/api/history/movies/export", {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(res.status).toBe(200);
    expect(res.headers.get("Content-Type")).toContain("text/csv");
    const text = await res.text();
    expect(text).toContain("video_code");
    expect(text).toContain("ABC-001");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npx vitest run --config vitest.server.config.ts server/__tests__/history-routes.test.ts
```

Expected: FAIL — history routes not implemented.

- [ ] **Step 3: Implement history routes**

Create `server/routes/history.ts`:

```typescript
import { Hono } from "hono";
import { HTTPException } from "hono/http-exception";
import type { Env } from "../env";
import type { JwtPayload } from "../services/jwt";

type HistEnv = { Bindings: Env; Variables: { user: JwtPayload } };

export const historyRoutes = new Hono<HistEnv>();

// --- Helpers ---

function cursorEncode(id: number): string {
  return btoa(String(id));
}

function cursorDecode(cursor: string): number {
  try {
    return parseInt(atob(cursor), 10);
  } catch {
    throw new HTTPException(400, {
      message: JSON.stringify({ error: { code: "history.invalid_cursor", message: "cursor is malformed" } }),
    });
  }
}

function normalizeDate(raw: string, isEnd: boolean): string | null {
  // Accepts: 2026-01-01, 2026-01-01T10:00:00, 2026-01-01 10:00:00, 2026-01-01T10:00:00Z
  const cleaned = raw.replace("T", " ").replace("Z", "").trim();
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(cleaned);
  if (dateOnly) {
    return isEnd ? `${cleaned} 23:59:59` : `${cleaned} 00:00:00`;
  }
  const full = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}/.test(cleaned);
  if (full) return cleaned;
  return null;
}

function clampLimit(raw: string | undefined): number {
  const n = parseInt(raw ?? "50", 10);
  return Math.max(1, Math.min(200, isNaN(n) ? 50 : n));
}

// --- Movie search ---

interface MovieRow {
  Id: number;
  VideoCode: string;
  Href: string;
  ActorName: string | null;
  ActorGender: string | null;
  SupportingActors: string | null;
  PerfectMatchIndicator: number | null;
  HiResIndicator: number | null;
  DateTimeCreated: string | null;
  DateTimeUpdated: string | null;
  SessionId: string | null;
  torrent_count: number;
}

function buildMovieQuery(params: Record<string, string | undefined>, forExport: boolean) {
  const conditions: string[] = [];
  const bindings: (string | number)[] = [];

  if (params.cursor && !forExport) {
    conditions.push("m.Id > ?");
    bindings.push(cursorDecode(params.cursor));
  }
  if (params.q) {
    const like = `%${params.q}%`;
    conditions.push("(m.VideoCode LIKE ? OR m.ActorName LIKE ? OR m.SupportingActors LIKE ?)");
    bindings.push(like, like, like);
  }
  if (params.actor) {
    conditions.push("m.ActorName = ?");
    bindings.push(params.actor);
  }
  if (params.perfect_match !== undefined) {
    conditions.push("m.PerfectMatchIndicator = ?");
    bindings.push(params.perfect_match === "true" ? 1 : 0);
  }
  if (params.hi_res !== undefined) {
    conditions.push("m.HiResIndicator = ?");
    bindings.push(params.hi_res === "true" ? 1 : 0);
  }
  if (params.session_id) {
    conditions.push("m.SessionId = ?");
    bindings.push(params.session_id);
  }
  if (params.date_from) {
    const d = normalizeDate(params.date_from, false);
    if (!d) throw new HTTPException(400, {
      message: JSON.stringify({ error: { code: "history.invalid_date", message: "date_from could not be parsed" } }),
    });
    conditions.push("m.DateTimeCreated >= ?");
    bindings.push(d);
  }
  if (params.date_to) {
    const d = normalizeDate(params.date_to, true);
    if (!d) throw new HTTPException(400, {
      message: JSON.stringify({ error: { code: "history.invalid_date", message: "date_to could not be parsed" } }),
    });
    conditions.push("m.DateTimeCreated <= ?");
    bindings.push(d);
  }

  const where = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";

  const selectSql = `
    SELECT m.Id, m.VideoCode, m.Href, m.ActorName, m.ActorGender,
           m.SupportingActors, m.PerfectMatchIndicator, m.HiResIndicator,
           m.DateTimeCreated, m.DateTimeUpdated, m.SessionId,
           COUNT(t.Id) AS torrent_count
    FROM MovieHistory m
    LEFT JOIN TorrentHistory t ON t.MovieHistoryId = m.Id
    ${where}
    GROUP BY m.Id
    ORDER BY m.Id`;

  const countSql = `SELECT MIN(COUNT(*), 10000) AS cnt FROM MovieHistory m ${where}`;

  return { selectSql, countSql, bindings, forExport };
}

historyRoutes.get("/movies", async (c) => {
  const params = c.req.query();
  const limit = clampLimit(params.limit);
  const { selectSql, countSql, bindings } = buildMovieQuery(params, false);

  const db = c.env.HISTORY_DB;
  const countResult = await db.prepare(countSql).bind(...bindings).first<{ cnt: number }>();
  const rows = await db.prepare(`${selectSql} LIMIT ?`).bind(...bindings, limit).all<MovieRow>();

  const items = rows.results.map((r) => ({
    id: r.Id,
    video_code: r.VideoCode,
    href: r.Href,
    actor_name: r.ActorName,
    actor_gender: r.ActorGender,
    supporting_actors: r.SupportingActors,
    perfect_match: r.PerfectMatchIndicator === 1,
    hi_res: r.HiResIndicator === 1,
    datetime_created: r.DateTimeCreated,
    datetime_updated: r.DateTimeUpdated,
    session_id: r.SessionId,
    torrent_count: r.torrent_count,
  }));

  const lastItem = items[items.length - 1];
  const nextCursor = items.length === limit && lastItem ? cursorEncode(lastItem.id) : undefined;

  return c.json({
    items,
    next_cursor: nextCursor,
    total_estimate: countResult?.cnt ?? 0,
  });
});

historyRoutes.get("/movies/export", async (c) => {
  const params = c.req.query();
  const { selectSql, bindings } = buildMovieQuery(params, true);
  const db = c.env.HISTORY_DB;
  const rows = await db.prepare(selectSql).bind(...bindings).all<MovieRow>();

  const header = "id,video_code,href,actor_name,actor_gender,supporting_actors,perfect_match,hi_res,datetime_created,datetime_updated,session_id,torrent_count";
  const csvRows = rows.results.map((r) =>
    [r.Id, r.VideoCode, r.Href, r.ActorName ?? "", r.ActorGender ?? "",
     r.SupportingActors ?? "", r.PerfectMatchIndicator ?? 0, r.HiResIndicator ?? 0,
     r.DateTimeCreated ?? "", r.DateTimeUpdated ?? "", r.SessionId ?? "", r.torrent_count]
      .map((v) => `"${String(v).replace(/"/g, '""')}"`)
      .join(",")
  );

  const csv = [header, ...csvRows].join("\n");
  return new Response(csv, {
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": "attachment; filename=movies.csv",
    },
  });
});

// --- Torrent search ---

interface TorrentRow {
  Id: number;
  movie_video_code: string | null;
  movie_href: string | null;
  MagnetUri: string | null;
  Size: string | null;
  SubtitleIndicator: number;
  CensorIndicator: number;
  ResolutionType: number;
  FileCount: number;
  DateTimeCreated: string | null;
  SessionId: string | null;
}

function buildTorrentQuery(params: Record<string, string | undefined>, forExport: boolean) {
  const conditions: string[] = [];
  const bindings: (string | number)[] = [];

  if (params.cursor && !forExport) {
    conditions.push("t.Id > ?");
    bindings.push(cursorDecode(params.cursor));
  }
  if (params.q) {
    conditions.push("m.VideoCode LIKE ?");
    bindings.push(`%${params.q}%`);
  }
  if (params.resolution_type !== undefined) {
    conditions.push("t.ResolutionType = ?");
    bindings.push(parseInt(params.resolution_type, 10));
  }
  if (params.has_subtitle !== undefined) {
    conditions.push("t.SubtitleIndicator = ?");
    bindings.push(params.has_subtitle === "true" ? 1 : 0);
  }
  if (params.uncensored !== undefined) {
    if (params.uncensored === "true") {
      conditions.push("t.CensorIndicator = 0");
    } else {
      conditions.push("t.CensorIndicator != 0");
    }
  }
  if (params.session_id) {
    conditions.push("t.SessionId = ?");
    bindings.push(params.session_id);
  }
  if (params.date_from) {
    const d = normalizeDate(params.date_from, false);
    if (!d) throw new HTTPException(400, {
      message: JSON.stringify({ error: { code: "history.invalid_date", message: "date_from could not be parsed" } }),
    });
    conditions.push("t.DateTimeCreated >= ?");
    bindings.push(d);
  }
  if (params.date_to) {
    const d = normalizeDate(params.date_to, true);
    if (!d) throw new HTTPException(400, {
      message: JSON.stringify({ error: { code: "history.invalid_date", message: "date_to could not be parsed" } }),
    });
    conditions.push("t.DateTimeCreated <= ?");
    bindings.push(d);
  }

  const where = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";

  const selectSql = `
    SELECT t.Id, m.VideoCode AS movie_video_code, m.Href AS movie_href,
           t.MagnetUri, t.Size, t.SubtitleIndicator, t.CensorIndicator,
           t.ResolutionType, t.FileCount, t.DateTimeCreated, t.SessionId
    FROM TorrentHistory t
    JOIN MovieHistory m ON m.Id = t.MovieHistoryId
    ${where}
    ORDER BY t.Id`;

  const countSql = `
    SELECT MIN(COUNT(*), 10000) AS cnt
    FROM TorrentHistory t
    JOIN MovieHistory m ON m.Id = t.MovieHistoryId
    ${where}`;

  return { selectSql, countSql, bindings, forExport };
}

historyRoutes.get("/torrents", async (c) => {
  const params = c.req.query();
  const limit = clampLimit(params.limit);
  const { selectSql, countSql, bindings } = buildTorrentQuery(params, false);

  const db = c.env.HISTORY_DB;
  const countResult = await db.prepare(countSql).bind(...bindings).first<{ cnt: number }>();
  const rows = await db.prepare(`${selectSql} LIMIT ?`).bind(...bindings, limit).all<TorrentRow>();

  const items = rows.results.map((r) => ({
    id: r.Id,
    movie_video_code: r.movie_video_code,
    movie_href: r.movie_href,
    magnet_uri: r.MagnetUri,
    size: r.Size,
    subtitle_indicator: r.SubtitleIndicator,
    censor_indicator: r.CensorIndicator,
    resolution_type: r.ResolutionType,
    file_count: r.FileCount,
    datetime_created: r.DateTimeCreated,
    session_id: r.SessionId,
  }));

  const lastItem = items[items.length - 1];
  const nextCursor = items.length === limit && lastItem ? cursorEncode(lastItem.id) : undefined;

  return c.json({
    items,
    next_cursor: nextCursor,
    total_estimate: countResult?.cnt ?? 0,
  });
});

historyRoutes.get("/torrents/export", async (c) => {
  const params = c.req.query();
  const { selectSql, bindings } = buildTorrentQuery(params, true);
  const db = c.env.HISTORY_DB;
  const rows = await db.prepare(selectSql).bind(...bindings).all<TorrentRow>();

  const header = "id,movie_video_code,movie_href,magnet_uri,size,subtitle_indicator,censor_indicator,resolution_type,file_count,datetime_created,session_id";
  const csvRows = rows.results.map((r) =>
    [r.Id, r.movie_video_code ?? "", r.movie_href ?? "", r.MagnetUri ?? "",
     r.Size ?? "", r.SubtitleIndicator, r.CensorIndicator, r.ResolutionType,
     r.FileCount, r.DateTimeCreated ?? "", r.SessionId ?? ""]
      .map((v) => `"${String(v).replace(/"/g, '""')}"`)
      .join(",")
  );

  const csv = [header, ...csvRows].join("\n");
  return new Response(csv, {
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": "attachment; filename=torrents.csv",
    },
  });
});
```

- [ ] **Step 4: Mount in app.ts**

Add to `server/app.ts` imports:

```typescript
import { historyRoutes } from "./routes/history";
```

Add after system state route:

```typescript
app.route("/api/history", historyRoutes);
```

- [ ] **Step 5: Run tests**

```bash
npx vitest run --config vitest.server.config.ts server/__tests__/history-routes.test.ts
```

Expected: All 5 history tests PASS.

- [ ] **Step 6: Commit**

```bash
git add server/routes/history.ts server/__tests__/history-routes.test.ts server/app.ts
git commit -m "feat: add history routes (movie/torrent search, CSV export, cursor pagination)"
```

---

## Task 8: Sessions Routes (List + Detail)

**Files:**
- Create: `server/routes/sessions.ts`
- Create: `server/__tests__/sessions-routes.test.ts`
- Modify: `server/app.ts`

- [ ] **Step 1: Write failing test**

Create `server/__tests__/sessions-routes.test.ts`:

```typescript
import { describe, it, expect, beforeAll } from "vitest";
import { env } from "cloudflare:test";
import { app } from "../app";

async function getToken(): Promise<string> {
  const res = await app.request("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: "admin", password: "testpassword123" }),
  });
  const data = await res.json();
  return data.access_token;
}

async function seedSessions(db: D1Database) {
  await db.exec(`
    CREATE TABLE IF NOT EXISTS ReportSessions (
      Id TEXT PRIMARY KEY,
      ReportType TEXT NOT NULL,
      ReportDate TEXT NOT NULL,
      UrlType TEXT,
      DisplayName TEXT,
      Url TEXT,
      StartPage INTEGER,
      EndPage INTEGER,
      CsvFilename TEXT NOT NULL,
      DateTimeCreated TEXT NOT NULL,
      Status TEXT DEFAULT 'in_progress',
      RunId TEXT,
      RunAttempt INTEGER,
      FailureReason TEXT,
      WriteMode TEXT DEFAULT 'pending'
    );
    CREATE TABLE IF NOT EXISTS ReportMovies (
      Id INTEGER PRIMARY KEY AUTOINCREMENT,
      SessionId TEXT NOT NULL,
      Href TEXT,
      VideoCode TEXT,
      Page INTEGER,
      Actor TEXT,
      Rate REAL,
      CommentNumber INTEGER
    );
    CREATE TABLE IF NOT EXISTS ReportTorrents (
      Id INTEGER PRIMARY KEY AUTOINCREMENT,
      ReportMovieId INTEGER NOT NULL,
      VideoCode TEXT,
      MagnetUri TEXT,
      SubtitleIndicator INTEGER,
      CensorIndicator INTEGER,
      ResolutionType INTEGER,
      Size TEXT,
      FileCount INTEGER
    );
    INSERT INTO ReportSessions (Id, ReportType, ReportDate, CsvFilename, DateTimeCreated, Status, WriteMode, RunId, RunAttempt)
    VALUES
      ('sess-001', 'daily', '2026-01-01', 'report1.csv', '2026-01-01 10:00:00', 'committed', 'pending', 'run-1', 1),
      ('sess-002', 'daily', '2026-01-02', 'report2.csv', '2026-01-02 10:00:00', 'committed', 'pending', 'run-2', 1),
      ('sess-003', 'adhoc', '2026-01-03', 'report3.csv', '2026-01-03 10:00:00', 'in_progress', 'pending', 'run-3', 1);
    INSERT INTO ReportMovies (SessionId, Href, VideoCode, Page, Actor) VALUES ('sess-001', '/v/abc', 'ABC-001', 1, 'Actor A');
    INSERT INTO ReportTorrents (ReportMovieId, VideoCode, MagnetUri) VALUES (1, 'ABC-001', 'magnet:?xt=urn:btih:xxx');
  `);
}

describe("Sessions routes", () => {
  beforeAll(async () => {
    await seedSessions(env.REPORTS_DB);
  });

  it("GET /api/sessions returns paginated list (newest first)", async () => {
    const token = await getToken();
    const res = await app.request("/api/sessions?limit=10", {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.items).toHaveLength(3);
    expect(data.items[0].session_id).toBe("sess-003");
  });

  it("filters sessions by state", async () => {
    const token = await getToken();
    const res = await app.request("/api/sessions?state=committed", {
      headers: { Authorization: `Bearer ${token}` },
    });
    const data = await res.json();
    expect(data.items).toHaveLength(2);
    expect(data.items.every((s: any) => s.state === "committed")).toBe(true);
  });

  it("GET /api/sessions/:id returns detail with movies and torrents", async () => {
    const token = await getToken();
    const res = await app.request("/api/sessions/sess-001", {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.session.session_id).toBe("sess-001");
    expect(data.movies).toHaveLength(1);
    expect(data.torrents).toHaveLength(1);
  });

  it("returns 404 for unknown session", async () => {
    const token = await getToken();
    const res = await app.request("/api/sessions/nonexistent", {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(res.status).toBe(404);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
npx vitest run --config vitest.server.config.ts server/__tests__/sessions-routes.test.ts
```

Expected: FAIL — sessions routes not implemented.

- [ ] **Step 3: Implement sessions routes**

Create `server/routes/sessions.ts`:

```typescript
import { Hono } from "hono";
import { HTTPException } from "hono/http-exception";
import type { Env } from "../env";
import type { JwtPayload } from "../services/jwt";

type SessEnv = { Bindings: Env; Variables: { user: JwtPayload } };

export const sessionsRoutes = new Hono<SessEnv>();

interface SessionRow {
  Id: string;
  Status: string | null;
  WriteMode: string | null;
  RunId: string | null;
  RunAttempt: number | null;
  DateTimeCreated: string;
  ReportType: string;
  ReportDate: string;
  FailureReason: string | null;
}

function mapSession(row: SessionRow) {
  return {
    session_id: row.Id,
    state: row.Status ?? "in_progress",
    write_mode: row.WriteMode ?? "pending",
    run_id: row.RunId ?? null,
    run_attempt: row.RunAttempt ?? null,
    created_at: row.DateTimeCreated,
    report_type: row.ReportType,
    report_date: row.ReportDate,
    failure_reason: row.FailureReason ?? null,
  };
}

function cursorEncode(sessionId: string): string {
  return btoa(JSON.stringify({ sid: sessionId }));
}

function cursorDecode(cursor: string): string {
  try {
    const parsed = JSON.parse(atob(cursor));
    return parsed.sid;
  } catch {
    throw new HTTPException(400, { message: "Invalid cursor" });
  }
}

sessionsRoutes.get("/", async (c) => {
  const state = c.req.query("state");
  const cursor = c.req.query("cursor");
  const limit = Math.max(1, Math.min(200, parseInt(c.req.query("limit") ?? "50", 10) || 50));

  const conditions: string[] = [];
  const bindings: (string | number)[] = [];

  if (state) {
    conditions.push("Status = ?");
    bindings.push(state);
  }
  if (cursor) {
    conditions.push("Id < ?");
    bindings.push(cursorDecode(cursor));
  }

  const where = conditions.length > 0 ? `WHERE ${conditions.join(" AND ")}` : "";

  const sql = `
    SELECT Id, Status, WriteMode, RunId, RunAttempt, DateTimeCreated,
           ReportType, ReportDate, FailureReason
    FROM ReportSessions
    ${where}
    ORDER BY Id DESC
    LIMIT ?`;

  const rows = await c.env.REPORTS_DB
    .prepare(sql)
    .bind(...bindings, limit)
    .all<SessionRow>();

  const items = rows.results.map(mapSession);
  const lastItem = items[items.length - 1];
  const nextCursor = items.length === limit && lastItem ? cursorEncode(lastItem.session_id) : undefined;

  return c.json({ items, next_cursor: nextCursor });
});

sessionsRoutes.get("/:session_id", async (c) => {
  const sessionId = c.req.param("session_id");

  const session = await c.env.REPORTS_DB
    .prepare(
      `SELECT Id, Status, WriteMode, RunId, RunAttempt, DateTimeCreated,
              ReportType, ReportDate, FailureReason
       FROM ReportSessions WHERE Id = ?`
    )
    .bind(sessionId)
    .first<SessionRow>();

  if (!session) {
    throw new HTTPException(404, {
      message: JSON.stringify({ error: { code: "session.not_found" } }),
    });
  }

  const movies = await c.env.REPORTS_DB
    .prepare("SELECT * FROM ReportMovies WHERE SessionId = ?")
    .bind(sessionId)
    .all();

  const movieIds = movies.results.map((m: any) => m.Id);
  let torrents: any[] = [];
  if (movieIds.length > 0) {
    const placeholders = movieIds.map(() => "?").join(",");
    const torrentResult = await c.env.REPORTS_DB
      .prepare(
        `SELECT t.* FROM ReportTorrents t
         JOIN ReportMovies m ON m.Id = t.ReportMovieId
         WHERE m.SessionId = ?`
      )
      .bind(sessionId)
      .all();
    torrents = torrentResult.results;
  }

  return c.json({
    session: mapSession(session),
    movies: movies.results,
    torrents,
  });
});
```

- [ ] **Step 4: Mount in app.ts**

Add to `server/app.ts` imports:

```typescript
import { sessionsRoutes } from "./routes/sessions";
```

Add after history route:

```typescript
app.route("/api/sessions", sessionsRoutes);
```

- [ ] **Step 5: Run tests**

```bash
npx vitest run --config vitest.server.config.ts server/__tests__/sessions-routes.test.ts
```

Expected: All 4 session tests PASS.

- [ ] **Step 6: Commit**

```bash
git add server/routes/sessions.ts server/__tests__/sessions-routes.test.ts server/app.ts
git commit -m "feat: add sessions routes (list + detail with movies/torrents)"
```

---

## Task 9: Pages Function Entrypoint + Final App Assembly

**Files:**
- Create: `functions/api/[[route]].ts`
- Modify: `server/app.ts` (final version)

- [ ] **Step 1: Create Pages Function entrypoint**

```bash
mkdir -p functions/api
```

Create `functions/api/[[route]].ts`:

```typescript
import { handle } from "hono/cloudflare-pages";
import { app } from "../../server/app";

export const onRequest = handle(app);
```

- [ ] **Step 2: Verify final app.ts has all routes**

The final `server/app.ts` should be:

```typescript
import { Hono } from "hono";
import type { Env } from "./env";
import type { JwtPayload } from "./services/jwt";
import { corsMiddleware } from "./middleware/cors";
import { requireAuth } from "./middleware/auth";
import { authRoutes } from "./routes/auth";
import { capabilitiesRoutes } from "./routes/capabilities";
import { systemStateRoutes } from "./routes/system-state";
import { historyRoutes } from "./routes/history";
import { sessionsRoutes } from "./routes/sessions";

type AppEnv = { Bindings: Env; Variables: { user: JwtPayload } };

export const app = new Hono<AppEnv>();

app.use("*", corsMiddleware());

// Public routes
app.route("/api/auth", authRoutes);

// Protected routes
app.use("/api/*", requireAuth());
app.route("/api/capabilities", capabilitiesRoutes);
app.route("/api/system", systemStateRoutes);
app.route("/api/history", historyRoutes);
app.route("/api/sessions", sessionsRoutes);

// 404 fallback
app.all("/api/*", (c) => c.json({ error: "Not found" }, 404));
```

- [ ] **Step 3: Run all tests**

```bash
npx vitest run --config vitest.server.config.ts
```

Expected: All tests PASS.

- [ ] **Step 4: Type-check server code**

```bash
npx tsc --noEmit -p server/tsconfig.json
```

Expected: No errors.

- [ ] **Step 5: Commit**

```bash
git add functions/api/\[\[route\]\].ts server/app.ts
git commit -m "feat: add Pages Function entrypoint and assemble all Phase 1 routes"
```

---

## Task 10: Local Development & Smoke Test

**Files:** No new files. Verification task.

- [ ] **Step 1: Build the Vue frontend**

```bash
npm run build
```

Expected: `dist/` directory created with static assets.

- [ ] **Step 2: Create local dev secrets**

Create a `.dev.vars` file (gitignored by wrangler):

```
API_SECRET_KEY=dev-secret-key-at-least-thirty-two-chars
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=plain:admin123
INGESTION_MODE=local
BACKEND_VERSION=2.0.0-dev
```

> Using `plain:` prefix for dev convenience. Production uses bcrypt hashes via Cloudflare dashboard secrets.

- [ ] **Step 3: Start local Pages dev server**

```bash
npx wrangler pages dev dist --d1=HISTORY_DB --d1=REPORTS_DB --d1=OPERATIONS_DB
```

Expected: Server starts on `http://localhost:8788`.

- [ ] **Step 4: Test auth flow**

```bash
# Login
curl -s -X POST http://localhost:8788/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}' | jq .

# Expected: { "access_token": "...", "token_type": "bearer", ... }
```

- [ ] **Step 5: Test capabilities**

```bash
TOKEN=$(curl -s -X POST http://localhost:8788/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}' | jq -r .access_token)

curl -s http://localhost:8788/api/capabilities \
  -H "Authorization: Bearer $TOKEN" | jq .

# Expected: { "version": "2.0.0", "storage_backend": "d1", "deployment": "cloudflare", ... }
```

- [ ] **Step 6: Verify SPA serves correctly**

Open `http://localhost:8788` in a browser. The Vue frontend should load. Update `VITE_API_BASE_URL` to empty string (or remove it) so the frontend uses relative `/api/*` paths — same origin, no CORS issues.

- [ ] **Step 7: Add `.dev.vars` to `.gitignore`**

```
# Cloudflare
.wrangler/
.dev.vars
```

- [ ] **Step 8: Commit**

```bash
git add .gitignore
git commit -m "chore: add local dev setup instructions and gitignore .dev.vars"
```

---

## Task 11: Cloudflare Pages Deployment

**Files:** No new source files. Deployment verification.

- [ ] **Step 1: Get D1 database IDs**

```bash
npx wrangler d1 list
```

Copy the actual database IDs for `javdb-history`, `javdb-reports`, `javdb-operations`.

- [ ] **Step 2: Update `wrangler.toml` with real D1 IDs**

Replace the `placeholder-fill-in-before-deploy` values with the actual IDs from step 1.

- [ ] **Step 3: Set production secrets**

```bash
npx wrangler pages secret put API_SECRET_KEY
npx wrangler pages secret put ADMIN_USERNAME
npx wrangler pages secret put ADMIN_PASSWORD_HASH
```

Enter actual production values when prompted. `ADMIN_PASSWORD_HASH` should be a bcrypt hash (not `plain:`).

- [ ] **Step 4: Deploy**

```bash
npm run build
npx wrangler pages deploy dist
```

Expected: Deployment URL printed (e.g., `https://javdb-autospider-web.pages.dev`).

- [ ] **Step 5: Verify production deployment**

```bash
PROD_URL=https://javdb-autospider-web.pages.dev

# Test login
curl -s -X POST $PROD_URL/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"<actual-password>"}' | jq .access_token

# Test capabilities
TOKEN=<token-from-above>
curl -s $PROD_URL/api/capabilities -H "Authorization: Bearer $TOKEN" | jq .
```

- [ ] **Step 6: Verify frontend loads and can authenticate**

Open the production URL in a browser. Log in via the Vue frontend. Navigate to history and sessions pages.

**Phase 1 acceptance criteria:** Frontend can log in, view history and session lists.

- [ ] **Step 7: Commit wrangler.toml with real D1 IDs**

```bash
git add wrangler.toml
git commit -m "chore: configure production D1 database bindings"
```

---

## Verification Checklist

Before marking Phase 1 complete, verify:

- [ ] All server tests pass: `npm run test:server`
- [ ] Server TypeScript compiles: `npm run typecheck:server`
- [ ] Frontend builds: `npm run build`
- [ ] Local dev works: `npx wrangler pages dev dist` serves both SPA and API
- [ ] Login → token flow works end-to-end
- [ ] `GET /api/capabilities` returns correct data
- [ ] `GET /api/history/movies` returns D1 data
- [ ] `GET /api/history/torrents` returns D1 data
- [ ] `GET /api/sessions` returns D1 data
- [ ] `GET /api/sessions/:id` returns session detail with movies/torrents
- [ ] CSV export works for movies and torrents
- [ ] Docker deployment (Python FastAPI) still works unchanged
