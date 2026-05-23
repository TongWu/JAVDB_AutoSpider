# Config + Diagnostics + Explore — Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

| Field       | Value                                                    |
| ----------- | -------------------------------------------------------- |
| **Status**  | Draft                                                    |
| **Date**    | 2026-05-23                                               |
| **Related** | [ADR-017](ADR-017-cloudflare-first-deployment.md), [IMP-ADR017-01](IMP-ADR017-01-cloudflare-pages-setup.md) |

**Goal:** Add config management, diagnostics, onboarding, and explore routes to the Cloudflare Worker API, completing all query-page functionality in the frontend.

**Architecture:** Routes follow Phase 1 patterns: Hono sub-routers mounted in `server/app.ts`, D1 native bindings for SQL, Web Crypto API for encryption. Config store migrates from JSON file (Python) to D1 `api_config` table. Explore uses `cheerio` for HTML parsing (replacing Python's lxml/BeautifulSoup). Heavy operations (qB, headless login, SMTP test, proxy test) that require direct network access are **not available** in Cloudflare mode — endpoints return stub/unavailable responses or dispatch to GH Actions.

**Tech Stack:** Hono 4, cheerio (DOM parsing), D1 native bindings, Web Crypto API (AES-GCM for config encryption), Vitest + `@cloudflare/vitest-pool-workers`

**Working Directory:** All paths are relative to `JAVDB_AutoSpider_Web/` (`/Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web/`).

---

## Design Decisions

### DD1: Config store → D1 table, not JSON file

Python stores runtime config overrides in `reports/api_config_store.json` (Fernet-encrypted). Workers cannot write to the filesystem. Replace with a D1 table in `OPERATIONS_DB`:

```sql
CREATE TABLE IF NOT EXISTS api_config (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Sensitive values are encrypted with AES-GCM via Web Crypto API, using `SECRETS_ENCRYPTION_KEY` from Worker env. The `value` column stores either plaintext or a JSON object `{"enc":"<base64>","iv":"<base64>"}`.

### DD2: Config schema is static, not from Python's `get_config_map()`

Python builds CONFIG_SCHEMA from `get_config_map()` which reads the actual Python module. In the TS backend, the schema is a static TypeScript definition mirroring the Python schema. This is part of the "backend overlap surface" — changes to CONFIG_MAP in Python must be reflected in the TS constant.

### DD3: Onboarding component tests — Cloudflare-compatible subset

Python onboarding tests qB (HTTP request), proxy (HTTP through proxy), SMTP (socket), and JavDB (cookie check). In Workers:
- **javdb**: Can check cookie presence in D1 config store (works)
- **qb**: Cannot connect to arbitrary IPs from Workers. Return `{ok: false, message: "qB test unavailable in Cloudflare mode"}`
- **proxy**: Same limitation. Return unavailable.
- **smtp**: No TCP sockets in Workers. Return unavailable.

The frontend already handles `ok: false` gracefully — it shows the message. This is acceptable for Cloudflare deployment where these services aren't directly reachable anyway.

### DD4: Explore endpoints — cheerio replaces lxml/BeautifulSoup

Per ADR-017 D5, explore uses `cheerio` for DOM parsing. The parsing logic is reimplemented in TypeScript. `proxy-page` sanitizes HTML and injects the enhancer script.

### DD5: Diagnostics headless login → unavailable in Cloudflare mode

The `headless` method for JavDB session refresh runs a browser subprocess — impossible in Workers. Only `cookie_paste` method works. The endpoint returns an error for `method: "headless"` in Cloudflare mode.

### DD6: `parse/{type}`, `detect-page-type`, `health-check` deferred

These diagnostics endpoints (`POST /api/parse/{type}`, `POST /api/detect-page-type`, `POST /api/health-check`) are developer/debugging tools. They are Phase 2 stretch goals — implement only if time permits after core routes. The frontend gracefully handles 404 for these.

---

## File Map

### New Files

| File | Responsibility |
| ---- | -------------- |
| `server/routes/config.ts` | `/api/config` — GET/PUT config, GET config/meta |
| `server/routes/onboarding.ts` | `/api/onboarding/*` — status, test, complete, dismiss-hint |
| `server/routes/diagnostics.ts` | `/api/diag/*` — JavDB session status + cookie paste refresh |
| `server/routes/explore.ts` | `/api/explore/*` — resolve, search, proxy-page, one-click, download-magnet, index-status, sync-cookie |
| `server/services/config-store.ts` | D1-backed config store: load, save, encrypt/decrypt sensitive keys |
| `server/services/config-schema.ts` | Static config schema definition (mirrors Python CONFIG_MAP) |
| `server/services/explore-parser.ts` | cheerio-based JavDB page parser (detail + index pages) |
| `server/__tests__/config-routes.test.ts` | Tests for config routes |
| `server/__tests__/onboarding-routes.test.ts` | Tests for onboarding routes |
| `server/__tests__/diagnostics-routes.test.ts` | Tests for diagnostics routes |
| `server/__tests__/explore-routes.test.ts` | Tests for explore routes |

### Modified Files

| File | Change |
| ---- | ------ |
| `server/app.ts` | Mount config, onboarding, diagnostics, explore routes |
| `server/env.ts` | Add `SECRETS_ENCRYPTION_KEY`, `JAVDB_SESSION_COOKIE` to Env |
| `package.json` | Add `cheerio` dependency |

---

## Task 1: Config Store Service (D1 + AES-GCM encryption)

**Files:**
- Create: `server/services/config-store.ts`
- Create: `server/services/config-schema.ts`
- Modify: `server/env.ts`

- [ ] **Step 1: Add env vars to `server/env.ts`**

Add these fields to the `Env` interface:

```typescript
// Config encryption
SECRETS_ENCRYPTION_KEY?: string;

// JavDB (stored in D1 config, also readable from env for initial seed)
JAVDB_SESSION_COOKIE?: string;
```

- [ ] **Step 2: Create config schema definition**

Create `server/services/config-schema.ts`:

```typescript
export interface ConfigFieldMeta {
  key: string;
  section: string;
  type: "bool" | "int" | "float" | "json" | "string";
  sensitive: boolean;
  readonly: boolean;
}

export const SENSITIVE_KEYS = new Set([
  "GIT_PASSWORD",
  "QB_PASSWORD",
  "SMTP_PASSWORD",
  "JAVDB_PASSWORD",
  "JAVDB_SESSION_COOKIE",
  "GPT_API_KEY",
  "PIKPAK_PASSWORD",
  "PROXY_POOL",
]);

export const CONFIG_META_FIELDS: ConfigFieldMeta[] = [
  { key: "ADMIN_USERNAME", section: "apiConsole", type: "string", sensitive: false, readonly: true },
  { key: "API_SECRET_KEY", section: "apiConsole", type: "string", sensitive: true, readonly: true },
  { key: "QB_URL", section: "qbittorrent", type: "string", sensitive: false, readonly: false },
  { key: "QB_USERNAME", section: "qbittorrent", type: "string", sensitive: false, readonly: false },
  { key: "QB_PASSWORD", section: "qbittorrent", type: "string", sensitive: true, readonly: false },
  { key: "QB_VERIFY_TLS", section: "qbittorrent", type: "bool", sensitive: false, readonly: false },
  { key: "TORRENT_CATEGORY", section: "qbittorrent", type: "string", sensitive: false, readonly: false },
  { key: "TORRENT_CATEGORY_ADHOC", section: "qbittorrent", type: "string", sensitive: false, readonly: false },
  { key: "TORRENT_SAVE_PATH", section: "qbittorrent", type: "string", sensitive: false, readonly: false },
  { key: "AUTO_START", section: "qbittorrent", type: "bool", sensitive: false, readonly: false },
  { key: "SKIP_CHECKING", section: "qbittorrent", type: "bool", sensitive: false, readonly: false },
  { key: "MIN_FILE_SIZE_MB", section: "qbFileFilter", type: "int", sensitive: false, readonly: false },
  { key: "JAVDB_SESSION_COOKIE", section: "javdb", type: "string", sensitive: true, readonly: false },
  { key: "JAVDB_USERNAME", section: "javdb", type: "string", sensitive: false, readonly: false },
  { key: "JAVDB_PASSWORD", section: "javdb", type: "string", sensitive: true, readonly: false },
  { key: "PROXY_MODE", section: "proxy", type: "string", sensitive: false, readonly: false },
  { key: "PROXY_HTTP", section: "proxy", type: "string", sensitive: false, readonly: false },
  { key: "PROXY_POOL", section: "proxy", type: "json", sensitive: true, readonly: false },
  { key: "PROXY_MODULES", section: "proxy", type: "json", sensitive: false, readonly: false },
  { key: "USE_PROXY", section: "spider", type: "bool", sensitive: false, readonly: false },
  { key: "START_PAGE", section: "spider", type: "int", sensitive: false, readonly: false },
  { key: "END_PAGE", section: "spider", type: "int", sensitive: false, readonly: false },
  { key: "MOVIE_SLEEP_MIN", section: "timing", type: "float", sensitive: false, readonly: false },
  { key: "MOVIE_SLEEP_MAX", section: "timing", type: "float", sensitive: false, readonly: false },
  { key: "PAGE_SLEEP", section: "timing", type: "float", sensitive: false, readonly: false },
  { key: "SMTP_HOST", section: "smtp", type: "string", sensitive: false, readonly: false },
  { key: "SMTP_PORT", section: "smtp", type: "int", sensitive: false, readonly: false },
  { key: "SMTP_USER", section: "smtp", type: "string", sensitive: false, readonly: false },
  { key: "SMTP_PASSWORD", section: "smtp", type: "string", sensitive: true, readonly: false },
  { key: "PIKPAK_EMAIL", section: "pikpak", type: "string", sensitive: false, readonly: false },
  { key: "PIKPAK_PASSWORD", section: "pikpak", type: "string", sensitive: true, readonly: false },
  { key: "RCLONE_REMOTE", section: "rclone", type: "string", sensitive: false, readonly: false },
  { key: "RCLONE_FOLDER_PATH", section: "rclone", type: "string", sensitive: false, readonly: false },
  { key: "GIT_USERNAME", section: "git", type: "string", sensitive: false, readonly: false },
  { key: "GIT_PASSWORD", section: "git", type: "string", sensitive: true, readonly: false },
  { key: "GPT_API_KEY", section: "advanced", type: "string", sensitive: true, readonly: false },
];

export const CONFIG_DEFAULTS: Record<string, unknown> = {};
for (const field of CONFIG_META_FIELDS) {
  if (field.type === "bool") CONFIG_DEFAULTS[field.key] = false;
  else if (field.type === "int") CONFIG_DEFAULTS[field.key] = 0;
  else if (field.type === "float") CONFIG_DEFAULTS[field.key] = 0.0;
  else if (field.type === "json") CONFIG_DEFAULTS[field.key] = field.key === "PROXY_POOL" ? [] : {};
  else CONFIG_DEFAULTS[field.key] = "";
}
```

- [ ] **Step 3: Create config store service**

Create `server/services/config-store.ts`:

```typescript
import type { D1Database } from "@cloudflare/workers-types";
import { SENSITIVE_KEYS, CONFIG_DEFAULTS } from "./config-schema";

const encoder = new TextEncoder();
const decoder = new TextDecoder();

async function deriveKey(secret: string): Promise<CryptoKey> {
  const raw = encoder.encode(secret);
  const keyMaterial = await crypto.subtle.importKey("raw", raw, "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey(
    { name: "PBKDF2", salt: encoder.encode("javdb-config-store"), iterations: 100000, hash: "SHA-256" },
    keyMaterial,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"],
  );
}

async function encrypt(plaintext: string, secret: string): Promise<string> {
  const key = await deriveKey(secret);
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ciphertext = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, encoder.encode(plaintext));
  return JSON.stringify({
    enc: btoa(String.fromCharCode(...new Uint8Array(ciphertext))),
    iv: btoa(String.fromCharCode(...iv)),
  });
}

async function decrypt(stored: string, secret: string): Promise<string> {
  const { enc, iv } = JSON.parse(stored);
  const key = await deriveKey(secret);
  const ciphertext = Uint8Array.from(atob(enc), (c) => c.charCodeAt(0));
  const ivBytes = Uint8Array.from(atob(iv), (c) => c.charCodeAt(0));
  const plaintext = await crypto.subtle.decrypt({ name: "AES-GCM", iv: ivBytes }, key, ciphertext);
  return decoder.decode(plaintext);
}

function isEncrypted(value: string): boolean {
  try {
    const parsed = JSON.parse(value);
    return typeof parsed === "object" && "enc" in parsed && "iv" in parsed;
  } catch {
    return false;
  }
}

export async function loadConfigStore(
  db: D1Database,
  encryptionKey?: string,
): Promise<Record<string, unknown>> {
  const rows = await db.prepare("SELECT key, value FROM api_config").all<{ key: string; value: string }>();
  const result: Record<string, unknown> = {};
  for (const row of rows.results) {
    if (encryptionKey && isEncrypted(row.value)) {
      try {
        const decrypted = await decrypt(row.value, encryptionKey);
        result[row.key] = JSON.parse(decrypted);
      } catch {
        // Cannot decrypt — skip this key
      }
    } else {
      try {
        result[row.key] = JSON.parse(row.value);
      } catch {
        result[row.key] = row.value;
      }
    }
  }
  return result;
}

export async function saveConfigKeys(
  db: D1Database,
  updates: Record<string, unknown>,
  encryptionKey?: string,
): Promise<void> {
  for (const [key, value] of Object.entries(updates)) {
    let serialized: string;
    const jsonValue = JSON.stringify(value);
    if (encryptionKey && SENSITIVE_KEYS.has(key)) {
      serialized = await encrypt(jsonValue, encryptionKey);
    } else {
      serialized = jsonValue;
    }
    await db
      .prepare(
        `INSERT INTO api_config (key, value, updated_at) VALUES (?, ?, datetime('now'))
         ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now')`,
      )
      .bind(key, serialized)
      .run();
  }
}

export function maskConfig(config: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(config)) {
    if (SENSITIVE_KEYS.has(key)) {
      result[key] = value ? "********" : "";
    } else {
      result[key] = value;
    }
  }
  return result;
}

export function mergeWithDefaults(storeValues: Record<string, unknown>): Record<string, unknown> {
  return { ...CONFIG_DEFAULTS, ...storeValues };
}
```

- [ ] **Step 4: Commit**

```bash
git add server/services/config-store.ts server/services/config-schema.ts server/env.ts
git commit -m "feat: add D1-backed config store with AES-GCM encryption"
```

---

## Task 2: Config Routes

**Files:**
- Create: `server/routes/config.ts`
- Create: `server/__tests__/config-routes.test.ts`
- Modify: `server/app.ts`

- [ ] **Step 1: Write failing test**

Create `server/__tests__/config-routes.test.ts`:

```typescript
import { describe, it, expect, beforeAll } from "vitest";
import { env } from "cloudflare:test";
import { app } from "../app";

async function getToken(): Promise<string> {
  const res = await app.request(
    "/api/auth/login",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "admin", password: "testpassword123" }),
    },
    env,
  );
  const data = (await res.json()) as any;
  return data.access_token;
}

async function getCsrf(): Promise<{ token: string; cookie: string }> {
  const res = await app.request(
    "/api/auth/login",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "admin", password: "testpassword123" }),
    },
    env,
  );
  const data = (await res.json()) as any;
  const cookies = res.headers.getSetCookie?.() ?? [];
  const csrfCookie = cookies.find((c: string) => c.startsWith("csrf_token="));
  const csrfValue = csrfCookie?.split("=")[1]?.split(";")[0] ?? data.csrf_token;
  return { token: data.access_token, cookie: `csrf_token=${csrfValue}` };
}

async function seedConfigTable(db: D1Database) {
  await db
    .prepare(
      "CREATE TABLE IF NOT EXISTS api_config (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT (datetime('now')))",
    )
    .run();
  await db.prepare("INSERT INTO api_config (key, value) VALUES (?, ?)").bind("QB_URL", '"https://192.168.1.1:8080"').run();
  await db.prepare("INSERT INTO api_config (key, value) VALUES (?, ?)").bind("START_PAGE", "1").run();
}

describe("Config routes", () => {
  beforeAll(async () => {
    await seedConfigTable(env.OPERATIONS_DB);
  });

  it("GET /api/config returns merged config with defaults", async () => {
    const token = await getToken();
    const res = await app.request("/api/config", { headers: { Authorization: `Bearer ${token}` } }, env);
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.QB_URL).toBe("https://192.168.1.1:8080");
    expect(data.START_PAGE).toBe(1);
  });

  it("GET /api/config masks sensitive values by default", async () => {
    const token = await getToken();
    await env.OPERATIONS_DB.prepare("INSERT OR REPLACE INTO api_config (key, value) VALUES (?, ?)").bind("QB_PASSWORD", '"secret123"').run();
    const res = await app.request("/api/config", { headers: { Authorization: `Bearer ${token}` } }, env);
    const data = (await res.json()) as any;
    expect(data.QB_PASSWORD).toBe("********");
  });

  it("GET /api/config?include_secrets=true returns unmasked for admin", async () => {
    const token = await getToken();
    const res = await app.request("/api/config?include_secrets=true", { headers: { Authorization: `Bearer ${token}` } }, env);
    const data = (await res.json()) as any;
    expect(data.QB_PASSWORD).toBe("secret123");
  });

  it("GET /api/config/meta returns field metadata", async () => {
    const token = await getToken();
    const res = await app.request("/api/config/meta", { headers: { Authorization: `Bearer ${token}` } }, env);
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.fields).toBeInstanceOf(Array);
    expect(data.fields.length).toBeGreaterThan(0);
    const qbUrl = data.fields.find((f: any) => f.key === "QB_URL");
    expect(qbUrl).toBeDefined();
    expect(qbUrl.section).toBe("qbittorrent");
    expect(qbUrl.sensitive).toBe(false);
  });

  it("PUT /api/config updates specific keys", async () => {
    const { token, cookie } = await getCsrf();
    const res = await app.request(
      "/api/config",
      {
        method: "PUT",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "X-CSRF-Token": cookie.split("=")[1],
          Cookie: cookie,
        },
        body: JSON.stringify({ QB_URL: "https://10.0.0.1:8080" }),
      },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.status).toBe("ok");

    // Verify the change persisted
    const getRes = await app.request("/api/config", { headers: { Authorization: `Bearer ${token}` } }, env);
    const getData = (await getRes.json()) as any;
    expect(getData.QB_URL).toBe("https://10.0.0.1:8080");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run --config vitest.server.config.ts server/__tests__/config-routes.test.ts
```

Expected: FAIL — config routes not implemented.

- [ ] **Step 3: Implement config routes**

Create `server/routes/config.ts`:

```typescript
import { Hono } from "hono";
import { HTTPException } from "hono/http-exception";
import type { Env } from "../env";
import type { JwtPayload } from "../services/jwt";
import { requireRole } from "../middleware/auth";
import { loadConfigStore, saveConfigKeys, maskConfig, mergeWithDefaults } from "../services/config-store";
import { CONFIG_META_FIELDS, SENSITIVE_KEYS } from "../services/config-schema";

type ConfigEnv = { Bindings: Env; Variables: { user: JwtPayload } };

export const configRoutes = new Hono<ConfigEnv>();

configRoutes.get("/", async (c) => {
  const includeSecrets = c.req.query("include_secrets") === "true";
  const user = c.get("user");

  if (includeSecrets && user.role !== "admin") {
    throw new HTTPException(403, { message: "include_secrets requires admin role" });
  }

  const storeValues = await loadConfigStore(c.env.OPERATIONS_DB, c.env.SECRETS_ENCRYPTION_KEY);
  const merged = mergeWithDefaults(storeValues);

  if (includeSecrets) {
    return c.json(merged);
  }
  return c.json(maskConfig(merged));
});

configRoutes.get("/meta", async (c) => {
  return c.json({ fields: CONFIG_META_FIELDS });
});

configRoutes.put("/", requireRole("admin"), async (c) => {
  const updates = await c.req.json<Record<string, unknown>>();
  const filtered: Record<string, unknown> = {};

  for (const [key, value] of Object.entries(updates)) {
    const field = CONFIG_META_FIELDS.find((f) => f.key === key);
    if (!field) {
      throw new HTTPException(422, { message: `Unknown config key: ${key}` });
    }
    if (field.readonly) {
      throw new HTTPException(422, { message: `${key} is read-only` });
    }
    // Skip masked sentinel
    if (SENSITIVE_KEYS.has(key) && value === "********") {
      continue;
    }
    filtered[key] = value;
  }

  if (Object.keys(filtered).length > 0) {
    await saveConfigKeys(c.env.OPERATIONS_DB, filtered, c.env.SECRETS_ENCRYPTION_KEY);
  }

  return c.json({ status: "ok" });
});
```

- [ ] **Step 4: Mount in app.ts**

Add import and route to `server/app.ts`:

```typescript
import { configRoutes } from "./routes/config";

// After existing protected routes:
app.route("/api/config", configRoutes);
```

- [ ] **Step 5: Run tests**

```bash
npx vitest run --config vitest.server.config.ts server/__tests__/config-routes.test.ts
```

Expected: All config tests PASS.

- [ ] **Step 6: Commit**

```bash
git add server/routes/config.ts server/__tests__/config-routes.test.ts server/app.ts server/services/config-store.ts server/services/config-schema.ts server/env.ts
git commit -m "feat: add config routes with D1-backed store and AES-GCM encryption"
```

---

## Task 3: Onboarding Routes

**Files:**
- Create: `server/routes/onboarding.ts`
- Create: `server/__tests__/onboarding-routes.test.ts`
- Modify: `server/app.ts`

- [ ] **Step 1: Write failing test**

Create `server/__tests__/onboarding-routes.test.ts`:

```typescript
import { describe, it, expect, beforeAll } from "vitest";
import { env } from "cloudflare:test";
import { app } from "../app";

async function getToken(): Promise<string> {
  const res = await app.request(
    "/api/auth/login",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "admin", password: "testpassword123" }),
    },
    env,
  );
  const data = (await res.json()) as any;
  return data.access_token;
}

async function getCsrf(): Promise<{ token: string; csrfToken: string; csrfCookie: string }> {
  const res = await app.request(
    "/api/auth/login",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "admin", password: "testpassword123" }),
    },
    env,
  );
  const data = (await res.json()) as any;
  return { token: data.access_token, csrfToken: data.csrf_token, csrfCookie: `csrf_token=${data.csrf_token}` };
}

async function seedTables(db: D1Database) {
  await db
    .prepare(
      "CREATE TABLE IF NOT EXISTS system_state (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT (datetime('now')))",
    )
    .run();
  await db
    .prepare(
      "CREATE TABLE IF NOT EXISTS api_config (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT (datetime('now')))",
    )
    .run();
}

describe("Onboarding routes", () => {
  beforeAll(async () => {
    await seedTables(env.OPERATIONS_DB);
  });

  it("GET /api/onboarding/status returns onboarding state", async () => {
    const token = await getToken();
    const res = await app.request("/api/onboarding/status", { headers: { Authorization: `Bearer ${token}` } }, env);
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.completed).toBe(false);
    expect(data.required_missing).toContain("javdb_session");
    expect(data.required_missing).toContain("qb");
  });

  it("POST /api/onboarding/test returns test result", async () => {
    const { token, csrfToken, csrfCookie } = await getCsrf();
    const res = await app.request(
      "/api/onboarding/test",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
          Cookie: csrfCookie,
        },
        body: JSON.stringify({ component: "javdb" }),
      },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.component).toBe("javdb");
    expect(typeof data.ok).toBe("boolean");
    expect(typeof data.message).toBe("string");
  });

  it("POST /api/onboarding/complete marks onboarding done", async () => {
    const { token, csrfToken, csrfCookie } = await getCsrf();
    const res = await app.request(
      "/api/onboarding/complete",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "X-CSRF-Token": csrfToken,
          Cookie: csrfCookie,
        },
      },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.completed).toBe(true);
  });

  it("POST /api/onboarding/dismiss-hint stores dismissed hint", async () => {
    const { token, csrfToken, csrfCookie } = await getCsrf();
    const res = await app.request(
      "/api/onboarding/dismiss-hint",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
          Cookie: csrfCookie,
        },
        body: JSON.stringify({ hint_id: "welcome-banner" }),
      },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.dismissed_hints).toContain("welcome-banner");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run --config vitest.server.config.ts server/__tests__/onboarding-routes.test.ts
```

Expected: FAIL — onboarding routes not implemented.

- [ ] **Step 3: Implement onboarding routes**

Create `server/routes/onboarding.ts`:

```typescript
import { Hono } from "hono";
import { HTTPException } from "hono/http-exception";
import type { Env } from "../env";
import type { JwtPayload } from "../services/jwt";
import { requireRole } from "../middleware/auth";
import { loadConfigStore } from "../services/config-store";

type OnbEnv = { Bindings: Env; Variables: { user: JwtPayload } };

export const onboardingRoutes = new Hono<OnbEnv>();

const REQUIRED_COMPONENTS = ["javdb_session", "qb"] as const;
const SKIPPABLE_COMPONENTS = ["smtp", "pikpak", "rclone", "proxy"] as const;

function isConfigured(component: string, config: Record<string, unknown>): boolean {
  switch (component) {
    case "javdb_session":
      return !!(config.JAVDB_SESSION_COOKIE || config.JAVDB_USERNAME);
    case "qb":
      return !!config.QB_URL;
    case "smtp":
      return !!(config.SMTP_HOST || config.SMTP_SERVER);
    case "pikpak":
      return !!(config.PIKPAK_EMAIL || config.PIKPAK_USERNAME);
    case "rclone":
      return !!(config.RCLONE_FOLDER_PATH || config.RCLONE_REMOTE);
    case "proxy": {
      const mode = String(config.PROXY_MODE ?? "").toLowerCase();
      if ((mode === "pool" || mode === "single") && (config.PROXY_HTTP || config.PROXY_POOL)) return true;
      return !!(config.PROXY_HTTP || config.PROXY_POOL);
    }
    default:
      return false;
  }
}

async function getOnboardedFlag(db: D1Database): Promise<boolean> {
  const row = await db.prepare("SELECT value FROM system_state WHERE key = ?").bind("onboarded").first<{ value: string }>();
  return row?.value === "true";
}

async function buildStatus(env: Env) {
  const config = await loadConfigStore(env.OPERATIONS_DB, env.SECRETS_ENCRYPTION_KEY);
  const completed = await getOnboardedFlag(env.OPERATIONS_DB);
  return {
    completed,
    required_missing: REQUIRED_COMPONENTS.filter((c) => !isConfigured(c, config)),
    skippable_missing: SKIPPABLE_COMPONENTS.filter((c) => !isConfigured(c, config)),
  };
}

type TestableComponent = "javdb" | "qb" | "proxy" | "smtp";

async function testComponent(
  component: TestableComponent,
  config: Record<string, unknown>,
): Promise<{ ok: boolean; message: string; details: Record<string, unknown> | null }> {
  switch (component) {
    case "javdb": {
      const cookie = config.JAVDB_SESSION_COOKIE;
      if (!cookie) return { ok: false, message: "JAVDB_SESSION_COOKIE not set", details: null };
      return { ok: true, message: "cookie present", details: { length: String(cookie).length } };
    }
    case "qb":
      return { ok: false, message: "qB connectivity test unavailable in Cloudflare mode", details: null };
    case "proxy":
      return { ok: false, message: "Proxy test unavailable in Cloudflare mode", details: null };
    case "smtp":
      return { ok: false, message: "SMTP test unavailable in Cloudflare mode", details: null };
    default:
      return { ok: false, message: `Unknown component: ${component}`, details: null };
  }
}

onboardingRoutes.get("/status", async (c) => {
  return c.json(await buildStatus(c.env));
});

onboardingRoutes.post("/test", async (c) => {
  const body = await c.req.json<{ component: string }>();
  const validComponents = ["javdb", "qb", "proxy", "smtp"];
  if (!validComponents.includes(body.component)) {
    throw new HTTPException(422, { message: `Invalid component: ${body.component}` });
  }
  const config = await loadConfigStore(c.env.OPERATIONS_DB, c.env.SECRETS_ENCRYPTION_KEY);
  const result = await testComponent(body.component as TestableComponent, config);
  return c.json({ component: body.component, ...result });
});

onboardingRoutes.post("/complete", requireRole("admin"), async (c) => {
  await c.env.OPERATIONS_DB
    .prepare(
      `INSERT INTO system_state (key, value, updated_at) VALUES ('onboarded', 'true', datetime('now'))
       ON CONFLICT(key) DO UPDATE SET value = 'true', updated_at = datetime('now')`,
    )
    .run();
  return c.json(await buildStatus(c.env));
});

onboardingRoutes.post("/dismiss-hint", requireRole("admin"), async (c) => {
  const body = await c.req.json<{ hint_id: string }>();
  if (!body.hint_id || body.hint_id.length > 64) {
    throw new HTTPException(422, { message: "hint_id required (max 64 chars)" });
  }

  const db = c.env.OPERATIONS_DB;
  const row = await db.prepare("SELECT value FROM system_state WHERE key = ?").bind("dismissed_hints").first<{ value: string }>();
  let hints: string[] = [];
  if (row?.value) {
    try {
      hints = JSON.parse(row.value);
    } catch {
      hints = [];
    }
  }

  if (!hints.includes(body.hint_id)) {
    hints.push(body.hint_id);
    await db
      .prepare(
        `INSERT INTO system_state (key, value, updated_at) VALUES ('dismissed_hints', ?, datetime('now'))
         ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now')`,
      )
      .bind(JSON.stringify(hints))
      .run();
  }

  return c.json({ dismissed_hints: hints });
});
```

- [ ] **Step 4: Mount in app.ts**

Add import and route to `server/app.ts`:

```typescript
import { onboardingRoutes } from "./routes/onboarding";

// After config route:
app.route("/api/onboarding", onboardingRoutes);
```

- [ ] **Step 5: Run tests**

```bash
npx vitest run --config vitest.server.config.ts server/__tests__/onboarding-routes.test.ts
```

Expected: All onboarding tests PASS.

- [ ] **Step 6: Commit**

```bash
git add server/routes/onboarding.ts server/__tests__/onboarding-routes.test.ts server/app.ts
git commit -m "feat: add onboarding routes (status, test, complete, dismiss-hint)"
```

---

## Task 4: Diagnostics Routes

**Files:**
- Create: `server/routes/diagnostics.ts`
- Create: `server/__tests__/diagnostics-routes.test.ts`
- Modify: `server/app.ts`

- [ ] **Step 1: Write failing test**

Create `server/__tests__/diagnostics-routes.test.ts`:

```typescript
import { describe, it, expect, beforeAll } from "vitest";
import { env } from "cloudflare:test";
import { app } from "../app";

async function getToken(): Promise<string> {
  const res = await app.request(
    "/api/auth/login",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "admin", password: "testpassword123" }),
    },
    env,
  );
  const data = (await res.json()) as any;
  return data.access_token;
}

async function getCsrf(): Promise<{ token: string; csrfToken: string; csrfCookie: string }> {
  const res = await app.request(
    "/api/auth/login",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "admin", password: "testpassword123" }),
    },
    env,
  );
  const data = (await res.json()) as any;
  return { token: data.access_token, csrfToken: data.csrf_token, csrfCookie: `csrf_token=${data.csrf_token}` };
}

async function seedTables(db: D1Database) {
  await db
    .prepare(
      "CREATE TABLE IF NOT EXISTS system_state (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT (datetime('now')))",
    )
    .run();
  await db
    .prepare(
      "CREATE TABLE IF NOT EXISTS api_config (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT (datetime('now')))",
    )
    .run();
}

describe("Diagnostics routes", () => {
  beforeAll(async () => {
    await seedTables(env.OPERATIONS_DB);
  });

  it("GET /api/diag/javdb-session returns session status (no cookie)", async () => {
    const token = await getToken();
    const res = await app.request("/api/diag/javdb-session", { headers: { Authorization: `Bearer ${token}` } }, env);
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.cookie_present).toBe(false);
    expect(data.cookie_value_preview).toBeNull();
    expect(data.is_likely_valid).toBe(false);
  });

  it("GET /api/diag/javdb-session returns status with cookie", async () => {
    await env.OPERATIONS_DB.prepare("INSERT OR REPLACE INTO api_config (key, value) VALUES (?, ?)").bind("JAVDB_SESSION_COOKIE", '"abc12345xyz"').run();
    const now = new Date().toISOString();
    await env.OPERATIONS_DB
      .prepare("INSERT OR REPLACE INTO system_state (key, value, updated_at) VALUES (?, ?, datetime('now'))")
      .bind("last_javdb_refresh", now)
      .run();

    const token = await getToken();
    const res = await app.request("/api/diag/javdb-session", { headers: { Authorization: `Bearer ${token}` } }, env);
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.cookie_present).toBe(true);
    expect(data.cookie_value_preview).toBe("abc12345...");
    expect(data.is_likely_valid).toBe(true);
  });

  it("POST /api/diag/javdb-session/refresh with cookie_paste", async () => {
    const { token, csrfToken, csrfCookie } = await getCsrf();
    const res = await app.request(
      "/api/diag/javdb-session/refresh",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
          Cookie: csrfCookie,
        },
        body: JSON.stringify({ method: "cookie_paste", cookie_value: "new_cookie_value_here" }),
      },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.success).toBe(true);
    expect(data.method).toBe("cookie_paste");
    expect(data.new_cookie_preview).toBe("new_cook...");
  });

  it("POST /api/diag/javdb-session/refresh with headless returns unavailable", async () => {
    const { token, csrfToken, csrfCookie } = await getCsrf();
    const res = await app.request(
      "/api/diag/javdb-session/refresh",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
          Cookie: csrfCookie,
        },
        body: JSON.stringify({ method: "headless" }),
      },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.success).toBe(false);
    expect(data.error).toContain("unavailable");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run --config vitest.server.config.ts server/__tests__/diagnostics-routes.test.ts
```

Expected: FAIL — diagnostics routes not implemented.

- [ ] **Step 3: Implement diagnostics routes**

Create `server/routes/diagnostics.ts`:

```typescript
import { Hono } from "hono";
import { HTTPException } from "hono/http-exception";
import type { Env } from "../env";
import type { JwtPayload } from "../services/jwt";
import { requireRole } from "../middleware/auth";
import { loadConfigStore, saveConfigKeys } from "../services/config-store";

type DiagEnv = { Bindings: Env; Variables: { user: JwtPayload } };

export const diagnosticsRoutes = new Hono<DiagEnv>();

function cookiePreview(cookie: string): string {
  return cookie.length > 8 ? cookie.slice(0, 8) + "..." : cookie;
}

function isRefreshRecent(lastRefreshTime: string | null, maxAgeHours = 24): boolean {
  if (!lastRefreshTime) return false;
  try {
    const dt = new Date(lastRefreshTime.replace("Z", "+00:00"));
    const age = Date.now() - dt.getTime();
    return age >= 0 && age < maxAgeHours * 3600 * 1000;
  } catch {
    return false;
  }
}

diagnosticsRoutes.get("/javdb-session", async (c) => {
  const config = await loadConfigStore(c.env.OPERATIONS_DB, c.env.SECRETS_ENCRYPTION_KEY);
  const cookie = String(config.JAVDB_SESSION_COOKIE ?? "");

  const row = await c.env.OPERATIONS_DB
    .prepare("SELECT value FROM system_state WHERE key = ?")
    .bind("last_javdb_refresh")
    .first<{ value: string }>();
  const lastRefresh = row?.value ?? null;

  return c.json({
    cookie_present: !!cookie,
    cookie_value_preview: cookie ? cookiePreview(cookie) : null,
    last_refresh_time: lastRefresh,
    estimated_expiry: null,
    is_likely_valid: !!cookie && isRefreshRecent(lastRefresh),
  });
});

diagnosticsRoutes.post("/javdb-session/refresh", requireRole("admin"), async (c) => {
  const body = await c.req.json<{ method: string; cookie_value?: string | null }>();

  if (body.method === "cookie_paste") {
    const cookieValue = (body.cookie_value ?? "").trim();
    if (!cookieValue) {
      throw new HTTPException(422, { message: "cookie_value is required when method='cookie_paste'" });
    }

    await saveConfigKeys(c.env.OPERATIONS_DB, { JAVDB_SESSION_COOKIE: cookieValue }, c.env.SECRETS_ENCRYPTION_KEY);

    try {
      await c.env.OPERATIONS_DB
        .prepare(
          `INSERT INTO system_state (key, value, updated_at) VALUES ('last_javdb_refresh', ?, datetime('now'))
           ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now')`,
        )
        .bind(new Date().toISOString())
        .run();
    } catch {
      // best-effort timestamp write
    }

    return c.json({
      success: true,
      method: "cookie_paste",
      new_cookie_preview: cookiePreview(cookieValue),
    });
  }

  if (body.method === "headless") {
    return c.json({
      success: false,
      method: "headless",
      error: "Headless login unavailable in Cloudflare mode. Use cookie_paste or dispatch via GH Actions.",
    });
  }

  throw new HTTPException(422, { message: `Unknown method: '${body.method}'. Must be 'headless' or 'cookie_paste'.` });
});
```

- [ ] **Step 4: Mount in app.ts**

Add import and route to `server/app.ts`:

```typescript
import { diagnosticsRoutes } from "./routes/diagnostics";

// After onboarding route:
app.route("/api/diag", diagnosticsRoutes);
```

- [ ] **Step 5: Run tests**

```bash
npx vitest run --config vitest.server.config.ts server/__tests__/diagnostics-routes.test.ts
```

Expected: All diagnostics tests PASS.

- [ ] **Step 6: Commit**

```bash
git add server/routes/diagnostics.ts server/__tests__/diagnostics-routes.test.ts server/app.ts
git commit -m "feat: add diagnostics routes (javdb session status + cookie paste refresh)"
```

---

## Task 5: Explore Parser Service (cheerio)

**Files:**
- Create: `server/services/explore-parser.ts`
- Modify: `package.json`

- [ ] **Step 1: Install cheerio**

```bash
cd /Users/tedwu/Documents/JAVDB_AutoSpider_CICD/JAVDB_AutoSpider_Web
npm install cheerio
```

- [ ] **Step 2: Create explore parser**

Create `server/services/explore-parser.ts`:

```typescript
import * as cheerio from "cheerio";

export type PageType = "detail" | "index" | "unknown";

export interface ParsedMagnet {
  name: string;
  magnet_uri: string;
  size: string;
  tags: string[];
  file_count: number;
}

export interface ParsedDetailPage {
  video_code: string;
  title: string;
  magnets: ParsedMagnet[];
  cover_url: string | null;
  actors: string[];
  release_date: string | null;
  duration: string | null;
  maker: string | null;
}

export interface ParsedIndexMovie {
  href: string;
  video_code: string;
  title: string;
  cover_url: string | null;
  release_date: string | null;
  score: string | null;
  tags: string[];
}

export interface ParsedIndexPage {
  movies: ParsedIndexMovie[];
  current_page: number;
  total_pages: number;
}

export function detectPageType(html: string): PageType {
  if (html.includes("video-detail") || html.includes("magnet-links") || html.includes("movie-panel-info")) {
    return "detail";
  }
  if (html.includes("movie-list") || html.includes("grid-item")) {
    return "index";
  }
  return "unknown";
}

export function parseDetailPage(html: string): ParsedDetailPage {
  const $ = cheerio.load(html);

  const videoCode =
    $(".video-detail .first-block .panel-block:first-child .value").text().trim() ||
    $("h2.title strong").first().text().trim() ||
    "";

  const title = $("h2.title").first().text().trim();
  const coverUrl = $(".video-detail .column-video-cover img").attr("src") ?? null;

  const actors: string[] = [];
  $(".video-detail .panel-block").each((_, el) => {
    const label = $(el).find("strong").text().trim();
    if (label.includes("演員") || label.includes("Actor")) {
      $(el)
        .find("a")
        .each((_, a) => actors.push($(a).text().trim()));
    }
  });

  let releaseDate: string | null = null;
  let duration: string | null = null;
  let maker: string | null = null;
  $(".video-detail .panel-block").each((_, el) => {
    const label = $(el).find("strong").text().trim();
    const value = $(el).find(".value").text().trim();
    if (label.includes("日期") || label.includes("Date")) releaseDate = value;
    if (label.includes("時長") || label.includes("Duration")) duration = value;
    if (label.includes("片商") || label.includes("Maker")) maker = value;
  });

  const magnets: ParsedMagnet[] = [];
  $(".magnet-links .item, #magnets-content .item").each((_, el) => {
    const magnetUri = $(el).find("a[href^='magnet:']").attr("href") ?? "";
    if (!magnetUri) return;
    const name = $(el).find(".name, .magnet-name").text().trim();
    const size = $(el).find(".meta, .size").text().trim();
    const tags: string[] = [];
    $(el)
      .find(".tag, .label")
      .each((_, tag) => tags.push($(tag).text().trim()));
    const fileCountText = $(el).find(".file-count").text().trim();
    const fileCount = parseInt(fileCountText, 10) || 1;
    magnets.push({ name, magnet_uri: magnetUri, size, tags, file_count: fileCount });
  });

  return { video_code: videoCode, title, magnets, cover_url: coverUrl, actors, release_date: releaseDate, duration, maker };
}

export function parseIndexPage(html: string, pageNum: number): ParsedIndexPage {
  const $ = cheerio.load(html);
  const movies: ParsedIndexMovie[] = [];

  $(".movie-list .item, .grid-item").each((_, el) => {
    const link = $(el).find("a").first();
    const href = link.attr("href") ?? "";
    const videoCode = $(el).find(".video-title strong, .uid").text().trim();
    const title = $(el).find(".video-title, .item-title").text().trim();
    const coverUrl = $(el).find("img").attr("src") ?? null;
    const releaseDate = $(el).find(".meta, .has-text-grey-dark").text().trim() || null;
    const score = $(el).find(".score .value, .rate").text().trim() || null;
    const tags: string[] = [];
    $(el)
      .find(".tag, .label")
      .each((_, tag) => tags.push($(tag).text().trim()));
    if (href) {
      movies.push({ href, video_code: videoCode, title, cover_url: coverUrl, release_date: releaseDate, score, tags });
    }
  });

  let totalPages = 1;
  $(".pagination-list a, .pagination a").each((_, el) => {
    const pageText = $(el).text().trim();
    const p = parseInt(pageText, 10);
    if (!isNaN(p) && p > totalPages) totalPages = p;
  });

  return { movies, current_page: pageNum, total_pages: totalPages };
}

export function pickBestMagnet(magnets: ParsedMagnet[]): ParsedMagnet | null {
  if (magnets.length === 0) return null;

  const preferredTokens = ["中字", "字幕", "破解", "uncensored", "無碼", "无码"];
  const hiResTokens = ["高清", "1080"];

  const scored = magnets.map((m) => {
    let score = 0;
    const combined = `${m.name} ${m.tags.join(" ")}`.toLowerCase();
    for (const tok of preferredTokens) {
      if (combined.includes(tok.toLowerCase())) score += 3;
    }
    for (const tok of hiResTokens) {
      if (combined.includes(tok.toLowerCase())) score += 2;
    }
    if (m.size.includes("GB")) score += 1;
    return { magnet: m, score };
  });

  scored.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    if (b.magnet.file_count !== a.magnet.file_count) return b.magnet.file_count - a.magnet.file_count;
    return b.magnet.name.length - a.magnet.name.length;
  });

  return scored[0].magnet;
}

export function sanitizeHtml(html: string): string {
  const $ = cheerio.load(html);

  $("script, iframe, frame, embed, object, noscript, base").remove();
  $("meta[http-equiv]").remove();

  $("*").each((_, el) => {
    const attribs = (el as any).attribs ?? {};
    for (const attr of Object.keys(attribs)) {
      if (attr.startsWith("on")) {
        $(el).removeAttr(attr);
      }
      if (attr === "srcdoc") {
        $(el).removeAttr(attr);
      }
      if (["action", "formaction", "href", "src"].includes(attr)) {
        const val = attribs[attr] ?? "";
        if (val.startsWith("javascript:") || val.startsWith("data:")) {
          $(el).removeAttr(attr);
        }
      }
    }
  });

  return $.html();
}
```

- [ ] **Step 3: Commit**

```bash
git add server/services/explore-parser.ts package.json package-lock.json
git commit -m "feat: add cheerio-based explore parser (detail, index, magnet selection, sanitization)"
```

---

## Task 6: Explore Routes

**Files:**
- Create: `server/routes/explore.ts`
- Create: `server/__tests__/explore-routes.test.ts`
- Modify: `server/app.ts`

- [ ] **Step 1: Write failing test**

Create `server/__tests__/explore-routes.test.ts`:

```typescript
import { describe, it, expect, beforeAll } from "vitest";
import { env } from "cloudflare:test";
import { app } from "../app";

async function getToken(): Promise<string> {
  const res = await app.request(
    "/api/auth/login",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "admin", password: "testpassword123" }),
    },
    env,
  );
  const data = (await res.json()) as any;
  return data.access_token;
}

async function getCsrf(): Promise<{ token: string; csrfToken: string; csrfCookie: string }> {
  const res = await app.request(
    "/api/auth/login",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "admin", password: "testpassword123" }),
    },
    env,
  );
  const data = (await res.json()) as any;
  return { token: data.access_token, csrfToken: data.csrf_token, csrfCookie: `csrf_token=${data.csrf_token}` };
}

async function seedTables(db: D1Database) {
  await db
    .prepare(
      "CREATE TABLE IF NOT EXISTS system_state (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT (datetime('now')))",
    )
    .run();
  await db
    .prepare(
      "CREATE TABLE IF NOT EXISTS api_config (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT (datetime('now')))",
    )
    .run();
}

describe("Explore routes", () => {
  beforeAll(async () => {
    await seedTables(env.OPERATIONS_DB);
  });

  it("POST /api/explore/sync-cookie updates cookie in config store", async () => {
    const { token, csrfToken, csrfCookie } = await getCsrf();
    const res = await app.request(
      "/api/explore/sync-cookie",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
          Cookie: csrfCookie,
        },
        body: JSON.stringify({ cookie: "test_cookie_value_123" }),
      },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.status).toBe("ok");
  });

  it("POST /api/explore/resolve rejects invalid URL", async () => {
    const { token, csrfToken, csrfCookie } = await getCsrf();
    const res = await app.request(
      "/api/explore/resolve",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
          Cookie: csrfCookie,
        },
        body: JSON.stringify({ url: "https://evil.com/foo" }),
      },
      env,
    );
    expect(res.status).toBe(422);
  });

  it("POST /api/explore/download-magnet rejects invalid magnet", async () => {
    const { token, csrfToken, csrfCookie } = await getCsrf();
    const res = await app.request(
      "/api/explore/download-magnet",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
          Cookie: csrfCookie,
        },
        body: JSON.stringify({ magnet: "not-a-magnet" }),
      },
      env,
    );
    expect(res.status).toBe(422);
  });

  it("POST /api/explore/download-magnet returns unavailable for valid magnet (no qB in Workers)", async () => {
    const { token, csrfToken, csrfCookie } = await getCsrf();
    const res = await app.request(
      "/api/explore/download-magnet",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
          Cookie: csrfCookie,
        },
        body: JSON.stringify({ magnet: "magnet:?xt=urn:btih:abc123" }),
      },
      env,
    );
    // Workers can't connect to qB directly — returns error or dispatches to GH Actions
    expect([200, 501, 502].includes(res.status)).toBe(true);
  });

  it("POST /api/explore/index-status returns empty items for empty input", async () => {
    const { token, csrfToken, csrfCookie } = await getCsrf();
    const res = await app.request(
      "/api/explore/index-status",
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
          Cookie: csrfCookie,
        },
        body: JSON.stringify({ movies: [] }),
      },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;
    expect(data.items).toBeDefined();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run --config vitest.server.config.ts server/__tests__/explore-routes.test.ts
```

Expected: FAIL — explore routes not implemented.

- [ ] **Step 3: Implement explore routes**

Create `server/routes/explore.ts`:

```typescript
import { Hono } from "hono";
import { HTTPException } from "hono/http-exception";
import type { Env } from "../env";
import type { JwtPayload } from "../services/jwt";
import { requireRole } from "../middleware/auth";
import { loadConfigStore, saveConfigKeys } from "../services/config-store";
import {
  detectPageType,
  parseDetailPage,
  parseIndexPage,
  pickBestMagnet,
  sanitizeHtml,
} from "../services/explore-parser";

type ExploreEnv = { Bindings: Env; Variables: { user: JwtPayload } };

export const exploreRoutes = new Hono<ExploreEnv>();

const VALID_JAVDB_HOSTS = /^(?:[a-z0-9-]+\.)*javdb\.com$/i;

function validateJavdbUrl(url: string): void {
  try {
    const parsed = new URL(url);
    if (!VALID_JAVDB_HOSTS.test(parsed.hostname)) {
      throw new HTTPException(422, { message: "URL must be a javdb.com domain" });
    }
    if (parsed.protocol !== "https:") {
      throw new HTTPException(422, { message: "URL must use HTTPS" });
    }
  } catch (e) {
    if (e instanceof HTTPException) throw e;
    throw new HTTPException(422, { message: "Invalid URL" });
  }
}

async function fetchJavdbHtml(url: string, config: Record<string, unknown>): Promise<string> {
  const headers: Record<string, string> = {
    "User-Agent":
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
  };
  const cookie = String(config.JAVDB_SESSION_COOKIE ?? "");
  if (cookie) {
    headers.Cookie = `_jdb_session=${cookie}`;
  }

  const response = await fetch(url, { headers, redirect: "follow" });
  if (!response.ok) {
    throw new HTTPException(502, { message: `Failed to fetch page: HTTP ${response.status}` });
  }
  return response.text();
}

// --- sync-cookie ---

exploreRoutes.post("/sync-cookie", requireRole("admin"), async (c) => {
  const body = await c.req.json<{ cookie: string }>();
  if (!body.cookie || body.cookie.length > 4096) {
    throw new HTTPException(422, { message: "cookie required (max 4096 chars)" });
  }
  await saveConfigKeys(c.env.OPERATIONS_DB, { JAVDB_SESSION_COOKIE: body.cookie.trim() }, c.env.SECRETS_ENCRYPTION_KEY);
  return c.json({ status: "ok" });
});

// --- proxy-page ---

exploreRoutes.get("/proxy-page", async (c) => {
  const url = c.req.query("url");
  if (!url) {
    throw new HTTPException(400, { message: "url query parameter required" });
  }
  validateJavdbUrl(url);
  const config = await loadConfigStore(c.env.OPERATIONS_DB, c.env.SECRETS_ENCRYPTION_KEY);
  const html = await fetchJavdbHtml(url, config);
  const sanitized = sanitizeHtml(html);
  return new Response(sanitized, {
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });
});

// --- resolve ---

exploreRoutes.post("/resolve", async (c) => {
  const body = await c.req.json<{
    url: string;
    page_num?: number;
    use_proxy?: boolean;
    use_cookie?: boolean;
  }>();
  validateJavdbUrl(body.url);
  const pageNum = body.page_num ?? 1;

  const config = await loadConfigStore(c.env.OPERATIONS_DB, c.env.SECRETS_ENCRYPTION_KEY);
  const fetchUrl = pageNum > 1 ? `${body.url}?page=${pageNum}` : body.url;
  const html = await fetchJavdbHtml(fetchUrl, config);
  const pageType = detectPageType(html);

  if (pageType === "detail") {
    return c.json({
      url: body.url,
      page_type: "detail",
      detail: parseDetailPage(html),
      index: null,
    });
  }
  if (pageType === "index") {
    return c.json({
      url: body.url,
      page_type: "index",
      detail: null,
      index: parseIndexPage(html, pageNum),
    });
  }
  return c.json({
    url: body.url,
    page_type: "unknown",
    detail: null,
    index: null,
  });
});

// --- search-by-video-code ---

exploreRoutes.post("/search-by-video-code", async (c) => {
  const body = await c.req.json<{
    video_code: string;
    use_proxy?: boolean;
    use_cookie?: boolean;
    f?: string;
  }>();

  const code = body.video_code.trim();
  if (!code || code.length > 64) {
    throw new HTTPException(422, { message: "video_code required (max 64 chars)" });
  }

  const filter = (body.f ?? "all").trim();
  const searchUrl = `https://javdb.com/search?q=${encodeURIComponent(code)}&f=${encodeURIComponent(filter)}`;

  const config = await loadConfigStore(c.env.OPERATIONS_DB, c.env.SECRETS_ENCRYPTION_KEY);
  const html = await fetchJavdbHtml(searchUrl, config);
  const parsed = parseIndexPage(html, 1);

  const exactMatch = parsed.movies.find(
    (m) => m.video_code.toLowerCase() === code.toLowerCase(),
  );

  // Letter-suffix fallback: if no exact match and code ends in a letter, try without it
  let fallbackSearched = false;
  let fallbackMatch: typeof exactMatch | undefined;
  if (!exactMatch && /[A-Za-z]$/.test(code)) {
    const baseCode = code.slice(0, -1);
    const fallbackUrl = `https://javdb.com/search?q=${encodeURIComponent(baseCode)}&f=${encodeURIComponent(filter)}`;
    try {
      const fallbackHtml = await fetchJavdbHtml(fallbackUrl, config);
      const fallbackParsed = parseIndexPage(fallbackHtml, 1);
      fallbackMatch = fallbackParsed.movies.find(
        (m) => m.video_code.toLowerCase() === code.toLowerCase(),
      );
      fallbackSearched = true;
      if (!fallbackMatch) {
        parsed.movies.push(...fallbackParsed.movies);
      }
    } catch {
      // fallback search failed — continue with primary results
    }
  }

  return c.json({
    video_code: code,
    search_url: searchUrl,
    movies: parsed.movies,
    exact_match_entry: exactMatch ?? fallbackMatch ?? null,
    letter_suffix_fallback_searched: fallbackSearched,
  });
});

// --- download-magnet ---

exploreRoutes.post("/download-magnet", requireRole("admin"), async (c) => {
  const body = await c.req.json<{
    magnet: string;
    title?: string;
    category?: string | null;
  }>();

  if (!body.magnet?.startsWith("magnet:?")) {
    throw new HTTPException(422, { message: "magnet must start with 'magnet:?'" });
  }

  // Workers cannot connect to qBittorrent directly.
  // In full implementation, this would dispatch to GH Actions.
  // For now, return an error indicating the limitation.
  throw new HTTPException(501, {
    message: "Direct qB magnet download unavailable in Cloudflare mode. Use GH Actions dispatch (Phase 3).",
  });
});

// --- one-click ---

exploreRoutes.post("/one-click", requireRole("admin"), async (c) => {
  const body = await c.req.json<{
    detail_url: string;
    use_proxy?: boolean;
    use_cookie?: boolean;
    category?: string | null;
  }>();
  validateJavdbUrl(body.detail_url);

  const config = await loadConfigStore(c.env.OPERATIONS_DB, c.env.SECRETS_ENCRYPTION_KEY);
  const html = await fetchJavdbHtml(body.detail_url, config);
  const detail = parseDetailPage(html);
  const selected = pickBestMagnet(detail.magnets);

  if (!selected) {
    return c.json({ status: "no_magnets", video_code: detail.video_code, selected: null });
  }

  // Workers cannot connect to qBittorrent directly — return selected magnet info
  // Frontend can use this to display the magnet or dispatch via GH Actions in Phase 3
  return c.json({
    status: "selected",
    video_code: detail.video_code,
    selected: {
      name: selected.name,
      magnet_uri: selected.magnet_uri,
      size: selected.size,
      tags: selected.tags,
    },
  });
});

// --- index-status ---

exploreRoutes.post("/index-status", async (c) => {
  const body = await c.req.json<{
    movies?: Array<{ href: string; video_code?: string }>;
    use_proxy?: boolean;
    use_cookie?: boolean;
  }>();

  const movies = body.movies ?? [];
  if (movies.length === 0) {
    return c.json({ items: {} });
  }

  // Check MovieHistory in D1 for download status
  const db = c.env.HISTORY_DB;
  const items: Record<string, { downloaded: boolean; has_uncensored: boolean }> = {};

  for (const movie of movies.slice(0, 30)) {
    const row = await db
      .prepare("SELECT Id FROM MovieHistory WHERE Href = ?")
      .bind(movie.href)
      .first<{ Id: number }>();
    items[movie.href] = {
      downloaded: !!row,
      has_uncensored: false,
    };
  }

  return c.json({ items });
});
```

- [ ] **Step 4: Mount in app.ts**

Add import and route to `server/app.ts`:

```typescript
import { exploreRoutes } from "./routes/explore";

// After diagnostics route:
app.route("/api/explore", exploreRoutes);
```

- [ ] **Step 5: Run tests**

```bash
npx vitest run --config vitest.server.config.ts server/__tests__/explore-routes.test.ts
```

Expected: All explore tests PASS.

- [ ] **Step 6: Commit**

```bash
git add server/routes/explore.ts server/__tests__/explore-routes.test.ts server/app.ts
git commit -m "feat: add explore routes (resolve, search, proxy-page, sync-cookie, index-status)"
```

---

## Task 7: Final Assembly + Full Test Suite

**Files:**
- Modify: `server/app.ts` (verify final state)

- [ ] **Step 1: Verify final app.ts has all Phase 2 routes**

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
import { configRoutes } from "./routes/config";
import { onboardingRoutes } from "./routes/onboarding";
import { diagnosticsRoutes } from "./routes/diagnostics";
import { exploreRoutes } from "./routes/explore";

type AppEnv = { Bindings: Env; Variables: { user: JwtPayload } };

export const app = new Hono<AppEnv>();

app.use("*", corsMiddleware());

// Public routes
app.get("/api/health", (c) => c.json({ status: "ok" }));
app.route("/api/auth", authRoutes);

// Protected routes
app.use("/api/*", requireAuth());
app.route("/api/capabilities", capabilitiesRoutes);
app.route("/api/system", systemStateRoutes);
app.route("/api/history", historyRoutes);
app.route("/api/sessions", sessionsRoutes);
app.route("/api/config", configRoutes);
app.route("/api/onboarding", onboardingRoutes);
app.route("/api/diag", diagnosticsRoutes);
app.route("/api/explore", exploreRoutes);

// 404 fallback
app.all("/api/*", (c) => c.json({ error: "Not found" }, 404));
```

- [ ] **Step 2: Run all server tests**

```bash
npx vitest run --config vitest.server.config.ts
```

Expected: All tests PASS (auth, history, sessions, config, onboarding, diagnostics, explore).

- [ ] **Step 3: Type-check server code**

```bash
npx tsc --noEmit -p server/tsconfig.json
```

Expected: No errors.

- [ ] **Step 4: Build frontend**

```bash
npm run build
```

Expected: `dist/` directory created successfully.

- [ ] **Step 5: Commit**

```bash
git add server/app.ts
git commit -m "feat: mount all Phase 2 routes (config, onboarding, diagnostics, explore)"
```

---

## Task 8: Deploy + Smoke Test

**Files:** No new files. Deployment verification.

- [ ] **Step 1: Deploy to Cloudflare**

```bash
npm run build && npx wrangler deploy
```

Expected: Deployment succeeds.

- [ ] **Step 2: Set SECRETS_ENCRYPTION_KEY if not already set**

```bash
npx wrangler secret put SECRETS_ENCRYPTION_KEY
```

Enter a random 32+ character string when prompted. This key encrypts sensitive config values in D1.

- [ ] **Step 3: Create api_config table in production D1**

```bash
npx wrangler d1 execute javdb-operations --command "CREATE TABLE IF NOT EXISTS api_config (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT (datetime('now')))"
```

- [ ] **Step 4: Verify endpoints**

```bash
PROD_URL=https://javdb-autospider-web.wuengineer.workers.dev

# Login
TOKEN=$(curl -s -X POST $PROD_URL/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"<your-password>"}' | jq -r .access_token)

# Config
curl -s $PROD_URL/api/config -H "Authorization: Bearer $TOKEN" | jq .

# Config meta
curl -s $PROD_URL/api/config/meta -H "Authorization: Bearer $TOKEN" | jq '.fields | length'

# Onboarding status
curl -s $PROD_URL/api/onboarding/status -H "Authorization: Bearer $TOKEN" | jq .

# Diagnostics
curl -s $PROD_URL/api/diag/javdb-session -H "Authorization: Bearer $TOKEN" | jq .
```

- [ ] **Step 5: Verify frontend pages**

Open the production URL in a browser. Log in and verify:
- Config page loads and shows fields grouped by section
- Onboarding status page loads
- Diagnostics page shows JavDB session info

**Phase 2 acceptance criteria:** All query pages functional in frontend.

---

## Verification Checklist

Before marking Phase 2 complete, verify:

- [ ] All server tests pass: `npm run test:server`
- [ ] Server TypeScript compiles: `npm run typecheck:server`
- [ ] Frontend builds: `npm run build`
- [ ] `GET /api/config` returns config with masked secrets
- [ ] `GET /api/config?include_secrets=true` returns unmasked (admin only)
- [ ] `GET /api/config/meta` returns field metadata
- [ ] `PUT /api/config` updates specific keys
- [ ] `GET /api/onboarding/status` returns correct component status
- [ ] `POST /api/onboarding/test` returns test results (javdb works, others unavailable)
- [ ] `POST /api/onboarding/complete` marks onboarding done
- [ ] `POST /api/onboarding/dismiss-hint` stores dismissed hints
- [ ] `GET /api/diag/javdb-session` returns cookie status
- [ ] `POST /api/diag/javdb-session/refresh` (cookie_paste) works
- [ ] `POST /api/diag/javdb-session/refresh` (headless) returns unavailable
- [ ] `POST /api/explore/sync-cookie` updates cookie
- [ ] `POST /api/explore/resolve` fetches and parses JavDB pages
- [ ] `POST /api/explore/search-by-video-code` searches JavDB
- [ ] `GET /api/explore/proxy-page` proxies sanitized HTML
- [ ] `POST /api/explore/index-status` checks D1 history
- [ ] Phase 1 routes still work (auth, capabilities, history, sessions)
- [ ] Production deployment succeeds
- [ ] Frontend config/diagnostics/explore pages load
