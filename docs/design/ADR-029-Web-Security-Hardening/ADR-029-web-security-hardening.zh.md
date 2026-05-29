# ADR-029: Web 后端安全与数据完整性加固

| 字段       | 值                                                                     |
| ---------- | --------------------------------------------------------------------- |
| **状态**   | Accepted — IMP-ADR029-01 已实现并验证（206 测试）；PR TongWu/JAVDB_AutoSpider_Web#9 已开启，待合并 |
| **日期**   | 2026-05-24                                                            |
| **作者**   | Ted                                                                   |
| **关联**   | [ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md) |

## 背景

对 `javdb-autospider-web` TypeScript 后端（Hono on Cloudflare Workers）的全面审计发现了若干安全和数据完整性缺陷。根本原因是架构差异：Cloudflare Workers 是无状态的——Python 后端的内存安全机制（`RATE_BUCKETS` 速率限制、`REVOKED_JTI` token 撤销、`ACTIVE_TOKENS` session 追踪）在 TS 后端没有对等实现。

### 审计发现

**P0 — 安全问题（4 项）：**

1. **无速率限制** — Login 端点容易遭受暴力破解/凭证填充攻击。Workers 在请求间无内存状态。
2. **Token 无法撤销** — Logout 仅删除 cookie，JWT 在自然过期前（30 分钟）始终有效。无法使被泄露的 token 失效。
3. **CORS 通配符** — `origin: (o) => o ?? "*"` 接受任何来源，与 `credentials: true` 组合使用时违反安全模型。
4. **生产环境允许明文密码** — `verifyPassword()` 在所有环境中接受 `plain:` 前缀，允许生产环境存储未加密的凭证。

**P1 — 数据完整性问题（4 项）：**

1. **跨数据库 commit 无原子性** — Session commit 的 `drop_pending=true` 操作跨 REPORTS_DB 和 HISTORY_DB 执行，无事务保护。部分失败导致不一致状态。
2. **导出查询无上限** — `/movies/export` 和 `/torrents/export` 跳过分页，随数据增长（当前约 40,000 行）可能导致 Worker 超时/OOM。
3. **Refresh 时不返回 CSRF token** — `/refresh` 端点响应体中缺少 CSRF token。长会话中 CSRF 可能失效，导致 mutation 请求返回 403。
4. **缺少 D1 索引** — 外键和常用过滤列（`MovieHistoryId`、`SessionId`、`Status`）缺少索引，查询性能随数据增长下降。

## 决策

引入 **KV Namespace**（`AUTH_KV`）作为 Workers 端的轻量状态存储。修复 TypeScript 后端的全部 8 个问题——Python 后端不受影响。

### P0-1: 基于 KV 的速率限制

添加 `rateLimit(limit, windowSeconds)` 中间件，使用 KV 计数器。

**Key 设计：**
- Key 格式：`rl:{ip}:{endpoint}:{window_start}`
- Value：请求计数
- TTL：窗口时长（自动清理）

**限制：**
- `POST /api/auth/login`：5 次 / 60s / IP
- `POST /api/auth/refresh`：10 次 / 60s / IP
- 其他 mutation：20 次 / 60s / IP

**超限响应：** HTTP 429 + `Retry-After` header。

**一致性权衡：** KV 是最终一致的（跨 colo 传播约 60s）。同一 colo 的并发请求存在读-改-写竞态（无原子递增）。对于本项目的规模（1-2 个用户，单一地理区域），这完全可以接受。目标是防止暴力破解，而非精确计量。

### P0-2: 基于 KV 的 Token 撤销（仅 Mutation 请求）

**Logout 时：** 写入 `revoked:{jti}` → KV，TTL = token 剩余秒数。

**认证 mutation 请求（POST/PUT/DELETE）：** `requireAuth()` 中间件查询 KV 中的 `revoked:{jti}`。命中 → 401。

**GET 请求：** 跳过撤销检查。被撤销的 token 在自然过期前（最长 30 分钟）仍可读取数据。避免为每个 API 调用增加约 12ms 的 KV 延迟。

**Session 计数：** `sessions:{username}` → JSON 数组 `[{jti, exp}]`。Login 时：
1. 读取当前 sessions，移除过期条目。
2. 若数量 ≥ 3，拒绝登录（HTTP 429）。
3. 追加新 `{jti, exp}`，写回。

软限制：读-改-写竞态可能短暂超过 3 个 session。下次 login 自动清理。

### P0-3: CORS 显式白名单

用基于环境的 origin 列表替换通配符 CORS：

- **生产环境**（`ENVIRONMENT=production`）：读取 `CORS_ORIGINS` 环境变量（逗号分隔）。空值 = 仅同源（不发出 CORS header）。
- **开发环境**（`ENVIRONMENT !== production`）：自动包含 `http://localhost:*` 和 `http://127.0.0.1:*`。
- Cloudflare 同域部署：CORS header 实际不必要（同源），但显式白名单防止误配置。

### P0-4: 生产环境拒绝明文密码

在 `verifyPassword()` 中：
- 若 `ENVIRONMENT === "production"` 且 hash 以 `plain:` 开头 → 返回 false，通过 `console.warn()` 输出警告。
- 开发/测试环境保留 `plain:` 支持以方便开发。

### P1-5: Session Commit 操作顺序

对于 `POST /sessions/:id/commit`（`drop_pending=true`）：

**顺序：先删除 pending，再更新 session 状态。**

1. `HISTORY_DB.batch()`：DELETE FROM `PendingMovieHistoryWrites` 和 `PendingTorrentHistoryWrites` WHERE SessionId = ?
2. `REPORTS_DB.prepare()`：UPDATE `ReportSessions` SET Status = 'committed' WHERE Id = ?

**失败分析：**
- 步骤 1 成功，步骤 2 失败 → Session 仍为 `finalizing`，pending 行已删除。重试 commit：无 pending 可删，状态更新为 committed。**可恢复。**
- 反向顺序（先更新状态，再删 pending）→ Session 标记为 `committed`，pending 行永久残留。StaleSessionCleanup 不会处理已 committed 的 session。**不可恢复。**

用 try/catch 包裹；部分失败时返回 HTTP 207，附带详细信息说明哪个步骤成功。

### P1-6: 导出硬限制

为 `/movies/export` 和 `/torrents/export` 查询添加 `LIMIT 100000`。

截断时：
- 响应 header：`X-Export-Truncated: true`
- 响应 header：`X-Export-Total-Count: {actual_count}`
- CSV 最后一行：`# Export truncated at 100,000 rows. Total: {count}`

前置 UTF-8 BOM（`﻿`）以兼容 Windows Excel。

### P1-7: Refresh 时返回 CSRF Token

`POST /api/auth/refresh` 响应：
- JSON 响应体中添加 `csrf_token` 字段。
- 重新设置 `csrf_token` cookie，`Max-Age` 与新 access token 过期时间一致。
- 前端 `client.ts` 已有处理逻辑（检查 refresh 响应中的 csrf_token 并更新 `sessionStorage`）——无需前端修改。

### P1-8: D1 索引

通过 `wrangler d1 execute` 一次性应用（不纳入 migration 追踪）：

```sql
CREATE INDEX IF NOT EXISTS idx_th_movie_id ON TorrentHistory(MovieHistoryId);
CREATE INDEX IF NOT EXISTS idx_mh_session ON MovieHistory(SessionId);
CREATE INDEX IF NOT EXISTS idx_rs_status ON ReportSessions(Status);
CREATE INDEX IF NOT EXISTS idx_th_session ON TorrentHistory(SessionId);
```

约 40,000 行数据下，索引创建在 2 秒内完成。无需停机。可通过 `DROP INDEX` 回退。

## 新增 Cloudflare 资源

| 资源          | Binding 名称 | 用途                                         |
| ------------- | ------------ | ------------------------------------------- |
| KV Namespace  | `AUTH_KV`    | 速率限制、token 撤销、session 计数              |

`wrangler.toml` 新增：

```toml
[[kv_namespaces]]
binding = "AUTH_KV"
id = "<部署时创建>"
preview_id = "<部署时创建>"
```

`env.ts` 新增：

```typescript
AUTH_KV: KVNamespace;
```

## 不在范围内

- **Python 后端修改** — 本 ADR 仅针对 TS 后端。
- **501 stub 补全** — 已 stub 的端点（crawl、parse、migrations 等）是 ADR-017 的设计决策，此处不重新讨论。
- **Config schema 缺口**（73 个缺失 key）— 独立的功能工作，非安全/完整性问题。
- **Stats trend 空实现**（duration、proxy_bans）— 功能不完整，非完整性问题。
- **`ensureTable()` 每请求开销** — 性能优化，非正确性问题。
- **密码时序攻击**（用户名枚举）— 仅有 2 个固定用户名（admin/readonly），攻击面可忽略。

## 影响

### 正面

- Login 暴力破解防护恢复（与 Python 后端对齐）。
- Logout 真正生效——被泄露的 token 可被撤销。
- CORS 不再接受任意来源。
- 跨数据库 commit 具有明确的失败/恢复语义。
- 导出查询不会随数据增长导致 Worker 崩溃。
- D1 查询性能因热路径索引而提升。

### 负面

- KV 新增一个 Cloudflare 资源需要管理（计费、wrangler 配置）。
- Mutation 请求增加约 12ms 延迟（KV 撤销检查）。
- 速率限制是近似的（最终一致性）——对当前规模可接受。
- Session 限制是软限制（竞态条件可能短暂超过 3 个）——下次 login 时自动恢复。

### 风险

- **KV 故障** → 速率限制和撤销失效（请求正常通过）。可接受：安全性降级到当前基线，不会更差。
- **KV 费用** — 免费层：100k 读取/天，1k 写入/天。Auth 流量完全在个人项目的限额之内。
