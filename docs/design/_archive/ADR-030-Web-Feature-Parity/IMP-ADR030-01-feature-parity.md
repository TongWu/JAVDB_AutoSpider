# Web Backend Feature Parity — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the feature parity gap between the TS and Python backends — add 26 config keys, rename 3 mismatched keys via alias fallback, implement `duration` trend metric, mark `proxy_bans` as unavailable, add a change-password endpoint, and make `findUser()` async with D1 priority.

**Architecture:** All changes are in the `JAVDB_AutoSpider_Web/` directory. Config schema additions auto-render in the frontend via `/config/meta`. Alias fallback handles key renames without migration scripts. `findUser()` gains D1 lookup for password hashes so that UI-changed passwords take effect immediately.

**Tech Stack:** TypeScript, Hono, Cloudflare Workers, D1, bcryptjs, Vitest + `@cloudflare/vitest-pool-workers`

**Related:** [ADR-030](ADR-030-web-feature-parity.md), [IMP-ADR029-01](../ADR-029-Web-Security-Hardening/IMP-ADR029-01-security-hardening.md)

**Dependency:** This plan assumes IMP-ADR029-01 has been implemented first. Several tasks build on ADR-029 changes (e.g. `findUser()` is already called with `await` in auth routes after ADR-029, `AUTH_KV` exists in env). If implementing before ADR-029, the auth route modifications in Tasks 4–5 will need adjustment.

---

## File Map

| Action | Path | Responsibility |
| ------ | ---- | -------------- |
| Modify | `server/services/config-schema.ts` | Add 26 new config fields, update SENSITIVE_KEYS, add alias map, add CONFIG_DEFAULTS for new fields |
| Modify | `server/services/config-store.ts` | Alias fallback in `loadConfigStore()`, canonical-name-only writes in `saveConfigKeys()` |
| Modify | `server/services/users.ts` | `findUser()` → async, D1 password hash priority over env |
| Modify | `server/routes/auth.ts` | `await findUser()`, add change-password endpoint |
| Modify | `server/routes/stats.ts` | `duration` from `job_runs`, `proxy_bans` → `available: false` |
| Modify | `server/app.ts` | Mount change-password route (if not already under `/api/auth`) |
| Modify | `server/env.ts` | Add `CORS_ORIGINS` to Env interface (ADR-030 spec) |
| Create | `server/__tests__/config-alias.test.ts` | Tests for alias fallback loading + canonical saving |
| Create | `server/__tests__/change-password.test.ts` | Tests for change-password endpoint |
| Modify | `server/__tests__/auth-routes.test.ts` | Update tests for async `findUser()` |
| Modify | `server/__tests__/stats-routes.test.ts` | Tests for `duration` trend, `proxy_bans` availability flag |

---

## Task 1: Add 26 Config Keys to Schema

**Files:**
- Modify: `JAVDB_AutoSpider_Web/server/services/config-schema.ts:1-67`

- [ ] **Step 1: Write the failing test**

Create `server/__tests__/config-schema.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { CONFIG_META_FIELDS, SENSITIVE_KEYS, ALIAS_MAP } from "../services/config-schema";

describe("config-schema", () => {
  const fieldKeys = CONFIG_META_FIELDS.map((f) => f.key);

  it("contains all 26 new keys from ADR-030", () => {
    const requiredKeys = [
      "PAGE_START", "PAGE_END", "PHASE2_MIN_RATE", "PHASE2_MIN_COMMENTS", "BASE_URL",
      "QB_URL_ADHOC", "QB_USERNAME_ADHOC", "QB_PASSWORD_ADHOC", "QB_ALLOW_INSECURE_HTTP",
      "REQUEST_TIMEOUT", "DELAY_BETWEEN_ADDITIONS",
      "SMTP_SERVER", "EMAIL_FROM", "EMAIL_TO",
      "PROXY_POOL_MAX_FAILURES", "LOGIN_PROXY_NAME",
      "GPT_API_URL", "GPT_API_KEY", "LOGIN_ATTEMPTS_PER_PROXY_LIMIT",
      "LOGIN_MAX_FAILURES_BEFORE_PROXY_SWITCH", "LOGIN_VERIFICATION_URLS",
      "GH_ACTIONS_TIER", "GH_ACTIONS_REPO", "GH_ACTIONS_TOKEN",
      "READONLY_USERNAME", "READONLY_PASSWORD_HASH",
    ];
    for (const key of requiredKeys) {
      expect(fieldKeys, `missing key: ${key}`).toContain(key);
    }
  });

  it("removed old key names that are now aliases", () => {
    expect(fieldKeys).not.toContain("START_PAGE");
    expect(fieldKeys).not.toContain("END_PAGE");
    expect(fieldKeys).not.toContain("SMTP_HOST");
  });

  it("marks sensitive keys correctly", () => {
    expect(SENSITIVE_KEYS.has("QB_PASSWORD_ADHOC")).toBe(true);
    expect(SENSITIVE_KEYS.has("GPT_API_KEY")).toBe(true);
    expect(SENSITIVE_KEYS.has("GH_ACTIONS_TOKEN")).toBe(true);
    expect(SENSITIVE_KEYS.has("READONLY_PASSWORD_HASH")).toBe(true);
  });

  it("marks readonly keys correctly", () => {
    const readonlyKeys = CONFIG_META_FIELDS.filter((f) => f.readonly);
    const readonlyNames = readonlyKeys.map((f) => f.key);
    expect(readonlyNames).toContain("QB_ALLOW_INSECURE_HTTP");
    expect(readonlyNames).toContain("GH_ACTIONS_TIER");
    expect(readonlyNames).toContain("GH_ACTIONS_REPO");
  });

  it("defines alias map with 3 entries", () => {
    expect(ALIAS_MAP).toEqual({
      SMTP_SERVER: "SMTP_HOST",
      PAGE_START: "START_PAGE",
      PAGE_END: "END_PAGE",
    });
  });

  it("has no duplicate keys", () => {
    const seen = new Set<string>();
    for (const key of fieldKeys) {
      expect(seen.has(key), `duplicate key: ${key}`).toBe(false);
      seen.add(key);
    }
  });

  it("GPT_API_KEY section is javdb, not advanced", () => {
    const field = CONFIG_META_FIELDS.find((f) => f.key === "GPT_API_KEY");
    expect(field?.section).toBe("javdb");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd JAVDB_AutoSpider_Web && npx vitest run server/__tests__/config-schema.test.ts`
Expected: FAIL — `ALIAS_MAP` is not exported, new keys missing, old keys still present.

- [ ] **Step 3: Update config-schema.ts with 26 new keys + alias map**

Replace the full contents of `server/services/config-schema.ts`:

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
  "QB_PASSWORD_ADHOC",
  "SMTP_PASSWORD",
  "JAVDB_PASSWORD",
  "JAVDB_SESSION_COOKIE",
  "GPT_API_KEY",
  "GH_ACTIONS_TOKEN",
  "PIKPAK_PASSWORD",
  "PROXY_POOL",
  "READONLY_PASSWORD_HASH",
]);

export const ALIAS_MAP: Record<string, string> = {
  SMTP_SERVER: "SMTP_HOST",
  PAGE_START: "START_PAGE",
  PAGE_END: "END_PAGE",
};

export const CONFIG_META_FIELDS: ConfigFieldMeta[] = [
  // --- apiConsole ---
  { key: "ADMIN_USERNAME", section: "apiConsole", type: "string", sensitive: false, readonly: true },
  { key: "API_SECRET_KEY", section: "apiConsole", type: "string", sensitive: true, readonly: true },
  { key: "READONLY_USERNAME", section: "apiConsole", type: "string", sensitive: false, readonly: false },
  { key: "READONLY_PASSWORD_HASH", section: "apiConsole", type: "string", sensitive: true, readonly: false },

  // --- qbittorrent ---
  { key: "QB_URL", section: "qbittorrent", type: "string", sensitive: false, readonly: false },
  { key: "QB_USERNAME", section: "qbittorrent", type: "string", sensitive: false, readonly: false },
  { key: "QB_PASSWORD", section: "qbittorrent", type: "string", sensitive: true, readonly: false },
  { key: "QB_VERIFY_TLS", section: "qbittorrent", type: "bool", sensitive: false, readonly: false },
  { key: "TORRENT_CATEGORY", section: "qbittorrent", type: "string", sensitive: false, readonly: false },
  { key: "TORRENT_CATEGORY_ADHOC", section: "qbittorrent", type: "string", sensitive: false, readonly: false },
  { key: "TORRENT_SAVE_PATH", section: "qbittorrent", type: "string", sensitive: false, readonly: false },
  { key: "AUTO_START", section: "qbittorrent", type: "bool", sensitive: false, readonly: false },
  { key: "SKIP_CHECKING", section: "qbittorrent", type: "bool", sensitive: false, readonly: false },
  { key: "QB_URL_ADHOC", section: "qbittorrent", type: "string", sensitive: false, readonly: false },
  { key: "QB_USERNAME_ADHOC", section: "qbittorrent", type: "string", sensitive: false, readonly: false },
  { key: "QB_PASSWORD_ADHOC", section: "qbittorrent", type: "string", sensitive: true, readonly: false },
  { key: "QB_ALLOW_INSECURE_HTTP", section: "qbittorrent", type: "bool", sensitive: false, readonly: true },
  { key: "REQUEST_TIMEOUT", section: "qbittorrent", type: "int", sensitive: false, readonly: false },
  { key: "DELAY_BETWEEN_ADDITIONS", section: "qbittorrent", type: "int", sensitive: false, readonly: false },

  // --- qbFileFilter ---
  { key: "MIN_FILE_SIZE_MB", section: "qbFileFilter", type: "int", sensitive: false, readonly: false },

  // --- javdb ---
  { key: "JAVDB_SESSION_COOKIE", section: "javdb", type: "string", sensitive: true, readonly: false },
  { key: "JAVDB_USERNAME", section: "javdb", type: "string", sensitive: false, readonly: false },
  { key: "JAVDB_PASSWORD", section: "javdb", type: "string", sensitive: true, readonly: false },
  { key: "GPT_API_URL", section: "javdb", type: "string", sensitive: false, readonly: false },
  { key: "GPT_API_KEY", section: "javdb", type: "string", sensitive: true, readonly: false },
  { key: "LOGIN_ATTEMPTS_PER_PROXY_LIMIT", section: "javdb", type: "int", sensitive: false, readonly: false },
  { key: "LOGIN_MAX_FAILURES_BEFORE_PROXY_SWITCH", section: "javdb", type: "int", sensitive: false, readonly: false },
  { key: "LOGIN_VERIFICATION_URLS", section: "javdb", type: "json", sensitive: false, readonly: false },

  // --- proxy ---
  { key: "PROXY_MODE", section: "proxy", type: "string", sensitive: false, readonly: false },
  { key: "PROXY_HTTP", section: "proxy", type: "string", sensitive: false, readonly: false },
  { key: "PROXY_POOL", section: "proxy", type: "json", sensitive: true, readonly: false },
  { key: "PROXY_MODULES", section: "proxy", type: "json", sensitive: false, readonly: false },
  { key: "PROXY_POOL_MAX_FAILURES", section: "proxy", type: "int", sensitive: false, readonly: false },
  { key: "LOGIN_PROXY_NAME", section: "proxy", type: "string", sensitive: false, readonly: false },

  // --- spider ---
  { key: "USE_PROXY", section: "spider", type: "bool", sensitive: false, readonly: false },
  { key: "PAGE_START", section: "spider", type: "int", sensitive: false, readonly: false },
  { key: "PAGE_END", section: "spider", type: "int", sensitive: false, readonly: false },
  { key: "PHASE2_MIN_RATE", section: "spider", type: "float", sensitive: false, readonly: false },
  { key: "PHASE2_MIN_COMMENTS", section: "spider", type: "int", sensitive: false, readonly: false },
  { key: "BASE_URL", section: "spider", type: "string", sensitive: false, readonly: false },

  // --- timing ---
  { key: "MOVIE_SLEEP_MIN", section: "timing", type: "float", sensitive: false, readonly: false },
  { key: "MOVIE_SLEEP_MAX", section: "timing", type: "float", sensitive: false, readonly: false },
  { key: "PAGE_SLEEP", section: "timing", type: "float", sensitive: false, readonly: false },

  // --- smtp ---
  { key: "SMTP_SERVER", section: "smtp", type: "string", sensitive: false, readonly: false },
  { key: "SMTP_PORT", section: "smtp", type: "int", sensitive: false, readonly: false },
  { key: "SMTP_USER", section: "smtp", type: "string", sensitive: false, readonly: false },
  { key: "SMTP_PASSWORD", section: "smtp", type: "string", sensitive: true, readonly: false },
  { key: "EMAIL_FROM", section: "smtp", type: "string", sensitive: false, readonly: false },
  { key: "EMAIL_TO", section: "smtp", type: "string", sensitive: false, readonly: false },

  // --- pikpak ---
  { key: "PIKPAK_EMAIL", section: "pikpak", type: "string", sensitive: false, readonly: false },
  { key: "PIKPAK_PASSWORD", section: "pikpak", type: "string", sensitive: true, readonly: false },

  // --- rclone ---
  { key: "RCLONE_REMOTE", section: "rclone", type: "string", sensitive: false, readonly: false },
  { key: "RCLONE_FOLDER_PATH", section: "rclone", type: "string", sensitive: false, readonly: false },

  // --- git ---
  { key: "GIT_USERNAME", section: "git", type: "string", sensitive: false, readonly: false },
  { key: "GIT_PASSWORD", section: "git", type: "string", sensitive: true, readonly: false },

  // --- ghActions ---
  { key: "GH_ACTIONS_TIER", section: "ghActions", type: "string", sensitive: false, readonly: true },
  { key: "GH_ACTIONS_REPO", section: "ghActions", type: "string", sensitive: false, readonly: true },
  { key: "GH_ACTIONS_TOKEN", section: "ghActions", type: "string", sensitive: true, readonly: false },
];

export const CONFIG_DEFAULTS: Record<string, unknown> = {};
for (const field of CONFIG_META_FIELDS) {
  if (field.type === "bool") CONFIG_DEFAULTS[field.key] = false;
  else if (field.type === "int") CONFIG_DEFAULTS[field.key] = 0;
  else if (field.type === "float") CONFIG_DEFAULTS[field.key] = 0.0;
  else if (field.type === "json") {
    if (field.key === "PROXY_POOL") CONFIG_DEFAULTS[field.key] = [];
    else if (field.key === "LOGIN_VERIFICATION_URLS") CONFIG_DEFAULTS[field.key] = [];
    else CONFIG_DEFAULTS[field.key] = {};
  } else {
    if (field.key === "BASE_URL") CONFIG_DEFAULTS[field.key] = "https://javdb.com";
    else if (field.key === "PROXY_POOL_MAX_FAILURES") CONFIG_DEFAULTS[field.key] = 3;
    else if (field.key === "LOGIN_ATTEMPTS_PER_PROXY_LIMIT") CONFIG_DEFAULTS[field.key] = 6;
    else if (field.key === "LOGIN_MAX_FAILURES_BEFORE_PROXY_SWITCH") CONFIG_DEFAULTS[field.key] = 3;
    else CONFIG_DEFAULTS[field.key] = "";
  }
}
```

Key changes from the original:
- Removed `START_PAGE`, `END_PAGE`, `SMTP_HOST` (replaced by canonical names)
- Removed duplicate `TORRENT_CATEGORY_ADHOC` entry (line 29 in original was a dupe)
- Moved `GPT_API_KEY` from `advanced` section to `javdb` section
- Added `ALIAS_MAP` export
- Added `QB_PASSWORD_ADHOC`, `GPT_API_KEY`, `GH_ACTIONS_TOKEN`, `READONLY_PASSWORD_HASH` to `SENSITIVE_KEYS`
- Added all 26 new keys with correct sections, types, sensitivity, and readonly flags

- [ ] **Step 4: Run test to verify it passes**

Run: `cd JAVDB_AutoSpider_Web && npx vitest run server/__tests__/config-schema.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/services/config-schema.ts server/__tests__/config-schema.test.ts
git commit -m "feat(config): add 26 config keys and alias map from ADR-030"
```

---

## Task 2: Alias Fallback in Config Store

**Files:**
- Modify: `JAVDB_AutoSpider_Web/server/services/config-store.ts:45-107`
- Create: `JAVDB_AutoSpider_Web/server/__tests__/config-alias.test.ts`

- [ ] **Step 1: Write the failing test**

Create `server/__tests__/config-alias.test.ts`:

```typescript
import { describe, it, expect, beforeEach } from "vitest";
import { env } from "cloudflare:test";
import { loadConfigStore, saveConfigKeys } from "../services/config-store";

async function ensureConfigTable(db: D1Database) {
  await db.prepare(
    `CREATE TABLE IF NOT EXISTS api_config (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL,
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )`,
  ).run();
}

async function clearConfigTable(db: D1Database) {
  await db.prepare("DELETE FROM api_config").run();
}

describe("config alias fallback", () => {
  beforeEach(async () => {
    await ensureConfigTable(env.OPERATIONS_DB);
    await clearConfigTable(env.OPERATIONS_DB);
  });

  it("loads canonical name when present", async () => {
    await env.OPERATIONS_DB.prepare(
      "INSERT INTO api_config (key, value) VALUES ('SMTP_SERVER', '\"smtp.example.com\"')",
    ).run();

    const config = await loadConfigStore(env.OPERATIONS_DB);
    expect(config.SMTP_SERVER).toBe("smtp.example.com");
  });

  it("falls back to alias when canonical name is absent", async () => {
    await env.OPERATIONS_DB.prepare(
      "INSERT INTO api_config (key, value) VALUES ('SMTP_HOST', '\"smtp.old.com\"')",
    ).run();

    const config = await loadConfigStore(env.OPERATIONS_DB);
    expect(config.SMTP_SERVER).toBe("smtp.old.com");
  });

  it("canonical name takes priority over alias", async () => {
    await env.OPERATIONS_DB.prepare(
      "INSERT INTO api_config (key, value) VALUES ('SMTP_SERVER', '\"smtp.new.com\"')",
    ).run();
    await env.OPERATIONS_DB.prepare(
      "INSERT INTO api_config (key, value) VALUES ('SMTP_HOST', '\"smtp.old.com\"')",
    ).run();

    const config = await loadConfigStore(env.OPERATIONS_DB);
    expect(config.SMTP_SERVER).toBe("smtp.new.com");
  });

  it("applies alias fallback for PAGE_START and PAGE_END", async () => {
    await env.OPERATIONS_DB.prepare(
      "INSERT INTO api_config (key, value) VALUES ('START_PAGE', '5')",
    ).run();
    await env.OPERATIONS_DB.prepare(
      "INSERT INTO api_config (key, value) VALUES ('END_PAGE', '10')",
    ).run();

    const config = await loadConfigStore(env.OPERATIONS_DB);
    expect(config.PAGE_START).toBe(5);
    expect(config.PAGE_END).toBe(10);
  });

  it("saveConfigKeys writes canonical name only", async () => {
    await saveConfigKeys(env.OPERATIONS_DB, { SMTP_SERVER: "smtp.saved.com" });

    const row = await env.OPERATIONS_DB.prepare(
      "SELECT key FROM api_config WHERE key = 'SMTP_SERVER'",
    ).first<{ key: string }>();
    expect(row?.key).toBe("SMTP_SERVER");

    const aliasRow = await env.OPERATIONS_DB.prepare(
      "SELECT key FROM api_config WHERE key = 'SMTP_HOST'",
    ).first<{ key: string }>();
    expect(aliasRow).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd JAVDB_AutoSpider_Web && npx vitest run server/__tests__/config-alias.test.ts`
Expected: FAIL — alias fallback not implemented, `SMTP_HOST` value not mapped to `SMTP_SERVER`.

- [ ] **Step 3: Add alias fallback to loadConfigStore()**

In `server/services/config-store.ts`, update the import and modify `loadConfigStore()`:

Replace the import line:

```typescript
import { SENSITIVE_KEYS, CONFIG_DEFAULTS } from "./config-schema";
```

with:

```typescript
import { SENSITIVE_KEYS, CONFIG_DEFAULTS, ALIAS_MAP } from "./config-schema";
```

Replace the `loadConfigStore` function (lines 45–68) with:

```typescript
export async function loadConfigStore(
  db: D1Database,
  encryptionKey?: string,
): Promise<Record<string, unknown>> {
  const rows = await db.prepare("SELECT key, value FROM api_config").all<{ key: string; value: string }>();
  const raw: Record<string, unknown> = {};
  for (const row of rows.results) {
    if (encryptionKey && isEncrypted(row.value)) {
      try {
        const decrypted = await decrypt(row.value, encryptionKey);
        raw[row.key] = JSON.parse(decrypted);
      } catch {
        // Cannot decrypt — skip this key
      }
    } else {
      try {
        raw[row.key] = JSON.parse(row.value);
      } catch {
        raw[row.key] = row.value;
      }
    }
  }

  // Alias fallback: if canonical name is absent but alias is present, copy alias value
  for (const [canonical, alias] of Object.entries(ALIAS_MAP)) {
    if (!(canonical in raw) && alias in raw) {
      raw[canonical] = raw[alias];
    }
  }

  return raw;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd JAVDB_AutoSpider_Web && npx vitest run server/__tests__/config-alias.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/services/config-store.ts server/services/config-schema.ts server/__tests__/config-alias.test.ts
git commit -m "feat(config): add alias fallback for renamed keys (SMTP_HOST, START_PAGE, END_PAGE)"
```

---

## Task 3: Make findUser() Async with D1 Priority

**Files:**
- Modify: `JAVDB_AutoSpider_Web/server/services/users.ts:1-29`
- Modify: `JAVDB_AutoSpider_Web/server/routes/auth.ts` (add `await` to `findUser` calls)
- Create: `JAVDB_AutoSpider_Web/server/__tests__/users.test.ts`

- [ ] **Step 1: Write the failing test**

Create `server/__tests__/users.test.ts`:

```typescript
import { describe, it, expect, beforeEach } from "vitest";
import { env } from "cloudflare:test";
import { findUser, getUsers } from "../services/users";

async function ensureConfigTable(db: D1Database) {
  await db.prepare(
    `CREATE TABLE IF NOT EXISTS api_config (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL,
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )`,
  ).run();
}

async function clearConfigTable(db: D1Database) {
  await db.prepare("DELETE FROM api_config").run();
}

describe("findUser", () => {
  beforeEach(async () => {
    await ensureConfigTable(env.OPERATIONS_DB);
    await clearConfigTable(env.OPERATIONS_DB);
  });

  it("returns admin user from env when D1 has no password hash", async () => {
    const user = await findUser(env, env.OPERATIONS_DB, "admin");
    expect(user).toBeDefined();
    expect(user!.username).toBe("admin");
    expect(user!.role).toBe("admin");
    expect(user!.passwordHash).toBe("plain:testpassword123");
  });

  it("returns undefined for unknown username", async () => {
    const user = await findUser(env, env.OPERATIONS_DB, "nobody");
    expect(user).toBeUndefined();
  });

  it("uses D1 password hash when present (overrides env)", async () => {
    await env.OPERATIONS_DB.prepare(
      `INSERT INTO api_config (key, value) VALUES ('ADMIN_PASSWORD_HASH', '"$2a$10$d1hashvalue"')`,
    ).run();

    const user = await findUser(env, env.OPERATIONS_DB, "admin");
    expect(user).toBeDefined();
    expect(user!.passwordHash).toBe("$2a$10$d1hashvalue");
  });

  it("uses D1 password hash for readonly user", async () => {
    // env has READONLY_USERNAME and READONLY_PASSWORD_HASH undefined in test config.
    // We need to simulate env having them set.
    const envWithReadonly = {
      ...env,
      READONLY_USERNAME: "viewer",
      READONLY_PASSWORD_HASH: "plain:viewerpass",
    } as typeof env;

    const user = await findUser(envWithReadonly, env.OPERATIONS_DB, "viewer");
    expect(user).toBeDefined();
    expect(user!.role).toBe("readonly");
    expect(user!.passwordHash).toBe("plain:viewerpass");

    // Now set D1 override
    await env.OPERATIONS_DB.prepare(
      `INSERT INTO api_config (key, value) VALUES ('READONLY_PASSWORD_HASH', '"$2a$10$readonlyhash"')`,
    ).run();

    const user2 = await findUser(envWithReadonly, env.OPERATIONS_DB, "viewer");
    expect(user2!.passwordHash).toBe("$2a$10$readonlyhash");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd JAVDB_AutoSpider_Web && npx vitest run server/__tests__/users.test.ts`
Expected: FAIL — `findUser` does not accept `db` parameter, is not async.

- [ ] **Step 3: Rewrite users.ts to be async with D1 priority**

Replace the full contents of `server/services/users.ts`:

```typescript
import type { Env } from "../env";

export interface User {
  username: string;
  role: "admin" | "readonly";
  passwordHash: string;
}

async function loadPasswordHashFromD1(
  db: D1Database,
  key: string,
): Promise<string | null> {
  try {
    const row = await db
      .prepare("SELECT value FROM api_config WHERE key = ?")
      .bind(key)
      .first<{ value: string }>();
    if (!row) return null;
    try {
      return JSON.parse(row.value) as string;
    } catch {
      return row.value;
    }
  } catch {
    return null;
  }
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

export async function findUser(
  env: Env,
  db: D1Database,
  username: string,
): Promise<User | undefined> {
  const users = getUsers(env);
  const user = users.find((u) => u.username === username);
  if (!user) return undefined;

  const hashKey =
    user.role === "admin" ? "ADMIN_PASSWORD_HASH" : "READONLY_PASSWORD_HASH";
  const d1Hash = await loadPasswordHashFromD1(db, hashKey);
  if (d1Hash) {
    return { ...user, passwordHash: d1Hash };
  }
  return user;
}
```

- [ ] **Step 4: Update auth.ts to pass db and await findUser()**

In `server/routes/auth.ts`, update the login handler's `findUser` call.

Replace:

```typescript
  const user = findUser(c.env, body.username);
```

with:

```typescript
  const user = await findUser(c.env, c.env.OPERATIONS_DB, body.username);
```

Replace the refresh handler's `findUser` call:

```typescript
  const user = findUser(c.env, payload.sub);
```

with:

```typescript
  const user = await findUser(c.env, c.env.OPERATIONS_DB, payload.sub);
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd JAVDB_AutoSpider_Web && npx vitest run server/__tests__/users.test.ts server/__tests__/auth-routes.test.ts`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add server/services/users.ts server/routes/auth.ts server/__tests__/users.test.ts
git commit -m "feat(auth): make findUser async with D1 password hash priority"
```

---

## Task 4: Change Password Endpoint

**Files:**
- Modify: `JAVDB_AutoSpider_Web/server/routes/auth.ts`
- Create: `JAVDB_AutoSpider_Web/server/__tests__/change-password.test.ts`

- [ ] **Step 1: Write the failing test**

Create `server/__tests__/change-password.test.ts`:

```typescript
import { describe, it, expect, beforeEach } from "vitest";
import { env } from "cloudflare:test";
import { app } from "../app";

async function ensureConfigTable(db: D1Database) {
  await db.prepare(
    `CREATE TABLE IF NOT EXISTS api_config (
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL,
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )`,
  ).run();
}

async function clearConfigTable(db: D1Database) {
  await db.prepare("DELETE FROM api_config").run();
}

async function login(username = "admin", password = "testpassword123") {
  const res = await app.request("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  }, env);
  const data = await res.json() as any;
  return {
    accessToken: data.access_token as string,
    csrfToken: data.csrf_token as string,
  };
}

describe("POST /api/auth/change-password", () => {
  beforeEach(async () => {
    await ensureConfigTable(env.OPERATIONS_DB);
    await clearConfigTable(env.OPERATIONS_DB);
  });

  it("rejects unauthenticated requests", async () => {
    const res = await app.request("/api/auth/change-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ old_password: "x", new_password: "y" }),
    }, env);
    expect(res.status).toBe(401);
  });

  it("rejects wrong old password", async () => {
    const { accessToken, csrfToken } = await login();
    const res = await app.request("/api/auth/change-password", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${accessToken}`,
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify({
        old_password: "wrongpassword",
        new_password: "newpassword123",
      }),
    }, env);
    expect(res.status).toBe(401);
  });

  it("rejects new password shorter than 8 chars", async () => {
    const { accessToken, csrfToken } = await login();
    const res = await app.request("/api/auth/change-password", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${accessToken}`,
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify({
        old_password: "testpassword123",
        new_password: "short",
      }),
    }, env);
    expect(res.status).toBe(400);
  });

  it("changes password successfully and persists to D1", async () => {
    const { accessToken, csrfToken } = await login();
    const res = await app.request("/api/auth/change-password", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${accessToken}`,
        "X-CSRF-Token": csrfToken,
      },
      body: JSON.stringify({
        old_password: "testpassword123",
        new_password: "newpassword123",
      }),
    }, env);
    expect(res.status).toBe(200);
    const data = await res.json() as any;
    expect(data.status).toBe("ok");

    // Verify D1 has the new hash
    const row = await env.OPERATIONS_DB.prepare(
      "SELECT value FROM api_config WHERE key = 'ADMIN_PASSWORD_HASH'",
    ).first<{ value: string }>();
    expect(row).toBeDefined();
    // The stored value should be a JSON-stringified bcrypt hash
    const hash = JSON.parse(row!.value);
    expect(hash).toMatch(/^\$2[aby]\$/);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd JAVDB_AutoSpider_Web && npx vitest run server/__tests__/change-password.test.ts`
Expected: FAIL — `/api/auth/change-password` returns 404.

- [ ] **Step 3: Add change-password handler to auth.ts**

In `server/routes/auth.ts`, add the following imports at the top (merge with existing):

```typescript
import { requireAuth } from "../middleware/auth";
import { saveConfigKeys } from "../services/config-store";
```

Then add the following handler after the `logout` handler:

```typescript
authRoutes.post("/change-password", requireAuth(), async (c) => {
  const body = await c.req.json<{ old_password: string; new_password: string }>();
  if (!body.old_password || !body.new_password) {
    throw new HTTPException(400, { message: "old_password and new_password required" });
  }
  if (body.new_password.length < 8) {
    throw new HTTPException(400, { message: "new_password must be at least 8 characters" });
  }

  const jwtUser = c.get("user");
  const user = await findUser(c.env, c.env.OPERATIONS_DB, jwtUser.sub);
  if (!user) {
    throw new HTTPException(401, { message: "Unknown user" });
  }

  const valid = await verifyPassword(body.old_password, user.passwordHash);
  if (!valid) {
    throw new HTTPException(401, { message: "Current password is incorrect" });
  }

  const { hash } = await import("bcryptjs");
  const newHash = await hash(body.new_password, 10);

  const hashKey = user.role === "admin" ? "ADMIN_PASSWORD_HASH" : "READONLY_PASSWORD_HASH";
  await saveConfigKeys(c.env.OPERATIONS_DB, { [hashKey]: newHash });

  return c.json({ status: "ok" });
});
```

- [ ] **Step 4: Update app.ts to ensure change-password is accessible**

The `/api/auth` routes are mounted as public routes (before `requireAuth()` middleware). However, the change-password endpoint has its own `requireAuth()` call inline, so it works correctly without changes to `app.ts`. The route `POST /api/auth/change-password` is already covered by `app.route("/api/auth", authRoutes)`.

Verify: The `requireAuth()` middleware on `/api/*` (line 48 of `app.ts`) does NOT block `/api/auth/*` because `authRoutes` is mounted before the wildcard middleware. The inline `requireAuth()` on the change-password handler handles its own auth check.

**Wait — this is wrong.** Looking at `app.ts`, `/api/auth` is mounted at line 45 (before the `requireAuth()` middleware at line 48). This means all auth routes bypass the global auth check. The change-password endpoint adds its own `requireAuth()` call, which is correct.

However, the global `requireAuth()` at line 48 runs for `/api/*` which includes `/api/auth/*`. But Hono's routing means the `authRoutes` handler runs first for exact matches. The inline `requireAuth()` on the change-password handler is the correct approach.

No changes needed to `app.ts`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd JAVDB_AutoSpider_Web && npx vitest run server/__tests__/change-password.test.ts`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add server/routes/auth.ts server/__tests__/change-password.test.ts
git commit -m "feat(auth): add POST /api/auth/change-password endpoint"
```

---

## Task 5: Stats Trend — Duration from job_runs

**Files:**
- Modify: `JAVDB_AutoSpider_Web/server/routes/stats.ts:114-251`

- [ ] **Step 1: Write the failing test**

Add to `server/__tests__/stats-routes.test.ts`. Insert the following test inside the existing `describe("Stats routes", ...)` block, after the existing tests. Also update `seedTables()` to create the `job_runs` table with test data.

Add to the `seedTables()` function (after the existing TorrentHistory inserts):

```typescript
  // job_runs (OPERATIONS_DB) — for duration trend
  await env.OPERATIONS_DB.prepare(
    `CREATE TABLE IF NOT EXISTS job_runs (
      job_id TEXT PRIMARY KEY, workflow TEXT NOT NULL, gh_run_id INTEGER,
      status TEXT NOT NULL DEFAULT 'dispatched', inputs TEXT,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    )`,
  ).run();
  await env.OPERATIONS_DB.prepare(
    `INSERT INTO job_runs (job_id, workflow, status, created_at, updated_at)
     VALUES ('job-001', 'DailyIngestion', 'completed',
             datetime('now', '-1 day'), datetime('now', '-1 day', '+300 seconds'))`,
  ).run();
  await env.OPERATIONS_DB.prepare(
    `INSERT INTO job_runs (job_id, workflow, status, created_at, updated_at)
     VALUES ('job-002', 'DailyIngestion', 'completed',
             datetime('now', '-1 day', '+600 seconds'), datetime('now', '-1 day', '+1200 seconds'))`,
  ).run();
```

Add the new test cases:

```typescript
  it("GET /api/stats/trend?metric=duration returns data from job_runs", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/stats/trend?metric=duration&period=7d",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;

    expect(data.metric).toBe("duration");
    expect(data.period).toBe("7d");
    expect(data.available).toBe(true);
    expect(Array.isArray(data.data_points)).toBe(true);
    expect(data.data_points.length).toBeGreaterThan(0);
    // Average of 300s and 600s = 450s
    const point = data.data_points[0];
    expect(typeof point.value).toBe("number");
    expect(point.value).toBeGreaterThan(0);
  });

  it("GET /api/stats/trend?metric=proxy_bans returns available:false", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/stats/trend?metric=proxy_bans&period=7d",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;

    expect(data.metric).toBe("proxy_bans");
    expect(data.available).toBe(false);
    expect(data.reason).toBeDefined();
    expect(data.data_points).toEqual([]);
  });

  it("GET /api/stats/trend non-proxy_bans metrics return available:true", async () => {
    const token = await getToken();
    const res = await app.request(
      "/api/stats/trend?metric=success_rate&period=7d",
      { headers: { Authorization: `Bearer ${token}` } },
      env,
    );
    expect(res.status).toBe(200);
    const data = (await res.json()) as any;

    expect(data.available).toBe(true);
    expect(data.reason).toBeUndefined();
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd JAVDB_AutoSpider_Web && npx vitest run server/__tests__/stats-routes.test.ts`
Expected: FAIL — `available` field missing from response, `duration` metric returns empty data.

- [ ] **Step 3: Update stats.ts trend handler**

In `server/routes/stats.ts`, replace the `duration` case (lines 183–191) with:

```typescript
      case "duration":
        db = c.env.OPERATIONS_DB;
        sql = `SELECT DATE(created_at) AS date,
                      AVG((julianday(updated_at) - julianday(created_at)) * 86400) AS value
               FROM job_runs
               WHERE status = 'completed'
                 AND created_at >= datetime('now', '-${days} days')
               GROUP BY DATE(created_at)
               ORDER BY date`;
        break;
```

Replace the `proxy_bans` case (lines 231–233) with:

```typescript
      case "proxy_bans":
        return c.json({
          metric: "proxy_bans",
          period,
          available: false,
          reason: "proxy_bans requires local log access (unavailable in Cloudflare mode)",
          data_points: [],
        });
```

Then update the response at the end of the handler (lines 246–250) to include the `available` field:

Replace:

```typescript
  return c.json({
    metric,
    period,
    data_points: dataPoints,
  });
```

with:

```typescript
  return c.json({
    metric,
    period,
    available: true,
    data_points: dataPoints,
  });
```

Also fix the variable name conflict on line 143. The original code has:

```typescript
  const metric = c.req.query("metric") ?? "success_rate";
  const metric = c.req.query("period") ?? "30d";
```

This is a bug — the second `const metric` should be `const period`. Replace line 143:

```typescript
  const period = c.req.query("period") ?? "30d";
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd JAVDB_AutoSpider_Web && npx vitest run server/__tests__/stats-routes.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add server/routes/stats.ts server/__tests__/stats-routes.test.ts
git commit -m "feat(stats): implement duration trend from job_runs, mark proxy_bans unavailable"
```

---

## Task 6: Add CORS_ORIGINS to Env Interface

**Files:**
- Modify: `JAVDB_AutoSpider_Web/server/env.ts:1-42`

- [ ] **Step 1: Add CORS_ORIGINS to Env**

In `server/env.ts`, add after the `JAVDB_SESSION_COOKIE` line:

```typescript
  // CORS (ADR-029 whitelist, configured in wrangler.toml or env)
  CORS_ORIGINS?: string;
```

This env var is consumed by the CORS middleware (ADR-029 implementation). Adding it here ensures TypeScript recognizes it.

- [ ] **Step 2: Verify typecheck passes**

Run: `cd JAVDB_AutoSpider_Web && npx tsc --noEmit`
Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add server/env.ts
git commit -m "feat(env): add CORS_ORIGINS to Env interface"
```

---

## Task 7: Update Existing Tests for Compatibility

**Files:**
- Modify: `JAVDB_AutoSpider_Web/server/__tests__/auth-routes.test.ts`

After Task 3 changed `findUser()` to async with a new signature, all existing tests that call auth endpoints should still pass because the auth route handlers are the only callers. However, if any test directly imports `findUser`, it needs updating.

- [ ] **Step 1: Run the full test suite to check for breakage**

Run: `cd JAVDB_AutoSpider_Web && npx vitest run`
Expected: All tests pass. If any test fails due to the `findUser` signature change, fix it.

- [ ] **Step 2: Fix any failing tests**

The most likely failure is if any test imports `findUser` directly with the old 2-argument signature. Grep for direct imports:

```bash
grep -rn "findUser" JAVDB_AutoSpider_Web/server/ --include="*.ts" | grep -v node_modules
```

Update any direct callers to pass `(env, db, username)` instead of `(env, username)`.

- [ ] **Step 3: Commit if fixes were needed**

```bash
git add -u
git commit -m "fix(tests): update findUser callers for async D1 signature"
```

---

## Self-Review Checklist

**1. Spec coverage:**
- [x] 26 config keys added (Task 1)
- [x] 3 alias renames with fallback (Task 2)
- [x] `findUser()` async with D1 priority (Task 3)
- [x] Change password endpoint (Task 4)
- [x] Duration trend from `job_runs` (Task 5)
- [x] `proxy_bans` → `available: false` (Task 5)
- [x] `CORS_ORIGINS` env var (Task 6)
- [x] Test compatibility (Task 7)

**2. Placeholder scan:** No TBD, TODO, or "fill in later" found.

**3. Type consistency:** `findUser(env, db, username)` signature used consistently in Tasks 3 and 4. `ALIAS_MAP` exported from `config-schema.ts` and imported in `config-store.ts`.

**4. Bugs found and fixed in plan:**
- `stats.ts` line 143: duplicate `const metric` → should be `const period` (fixed in Task 5)
- `config-schema.ts` line 29: duplicate `TORRENT_CATEGORY_ADHOC` entry (fixed in Task 1)
- `GPT_API_KEY` was in section `advanced` — ADR-030 specifies `javdb` (fixed in Task 1)

---

## Implementation Status (2026-05-29)

Executed via `superpowers:subagent-driven-development` on branch
`claude/adr-028-web-completeness` (web-repo worktree), after IMP-ADR029-01.

**All 7 tasks DONE & verified.** Final gate: `npm run test:server` → **168 passed,
0 failures** (23 files); `npm run typecheck:server` → clean. Commits `6800358` …
`8c3cd2b` (+ a security fix `41d2a53`).

Divergences from the written plan (corrected during execution):
- **Task 4 (change-password):** the plan's handler called `verifyPassword(old, hash)`
  with 2 args, but ADR-029 made it 3-arg `(password, hash, environment)` — corrected
  to pass `c.env.ENVIRONMENT`. The plan's test omitted the `csrf_token` cookie, so the
  authenticated cases would have hit ADR-029's CSRF double-submit guard (403); added
  `Cookie: csrf_token=…` to those requests. Code review additionally found that
  change-password writes `ADMIN_PASSWORD_HASH` into D1 `api_config`, which `GET
  /api/config` would return UNMASKED to non-admins (it was missing from
  `SENSITIVE_KEYS` while `READONLY_PASSWORD_HASH` was present) — fixed by adding
  `ADMIN_PASSWORD_HASH` to `SENSITIVE_KEYS` (`41d2a53`). Password hashes remain stored
  UNENCRYPTED (findUser reads them raw + `JSON.parse`); `SENSITIVE_KEYS` controls
  masking only.
- **Task 5 (stats trend):** the plan's "line 143 duplicate `const metric` bug" did NOT
  exist in current code (handler already declares both `const metric` and `const
  period`); that no-fix was skipped. Edits were located by `case` label, not the plan's
  stale line numbers.
- **Task 6 (`CORS_ORIGINS` env):** already added by IMP-ADR029-01 Task 6 — no-op here.
- **Task 7 (test compatibility):** suite stayed green throughout; the only `findUser`
  callers (auth.ts ×3, users.test.ts) all use the new `(env, db, username)` signature —
  no fixes needed.

**Cross-backend follow-up (ADR-017 parity, tracked separately):** Task 5 added an
`available` boolean (and `reason` for unavailable metrics) to the Worker's
`/api/stats/trend` response. The Python FastAPI backend's `TrendResponse`
(`apps/api/schemas/stats.py`) does not yet expose `available`/`reason`. Per the
"Dual Backend Sync" rule this must be reconciled in a linked follow-up (the TS
contract test `contract-compliance.test.ts` uses non-exclusive `assertHasKeys`, so it
is not broken today, but should assert `available` once both backends + `openapi.json`
agree).
