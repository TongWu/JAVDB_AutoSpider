# ADR-031: Web 后端运维打磨 — Workflow UI、回滚、Onboarding 与架构

| 字段       | 值                                                                     |
| ---------- | --------------------------------------------------------------------- |
| **状态**   | Accepted — IMP-ADR031-01 已实现并验证（206 测试）；PR TongWu/JAVDB_AutoSpider_Web#9 已开启，待合并 |
| **日期**   | 2026-05-24                                                            |
| **作者**   | Ted                                                                   |
| **关联**   | [ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md), [ADR-029](../ADR-029-Web-Security-Hardening/ADR-029-web-security-hardening.md), [ADR-030](../ADR-030-Web-Feature-Parity/ADR-030-web-feature-parity.md) |

## 背景

ADR-029（安全加固）和 ADR-030（功能对齐）之后，最终审计发现了五个架构层面的改进，用于减少运维摩擦并完成 Web UI 对系统运维表面的覆盖：

1. **缺失 workflow 的 UI 调度** — 三个 GitHub Actions workflow（`WeeklyDedup`、`Migration`、`TestIngestion`）无法从 Web UI 触发。现有的 `POST /api/gh-actions/runs` 端点可以调度任何 workflow，但前端无法获知每个 workflow 的参数 schema、类型、默认值或验证规则。

2. **Rollback 参数不完整** — `RollbackD1.yml` 接受 10 个参数（`scope`、`force`、`confirm_production`、`log_level`、`runner` 等），但 TS 后端的 `POST /sessions/:id/rollback` 只传 `session_id`。用户必须直接使用 GitHub UI 进行高级回滚操作。`dry_run` 标志返回 mock 数据而非调度真正的 dry-run。

3. **Onboarding 测试 501 Stub** — `POST /onboarding/test` 在 Cloudflare 模式下对 qBittorrent、proxy 和 SMTP 连接测试返回 501。Workers 无法打开到这些服务的 TCP socket，但可以通过调度 GH Actions workflow 异步测试连接。

4. **Cursor 编码不统一** — `sessions.ts` 将 cursor 编码为 `base64(JSON.stringify({sid}))`，而 `history.ts` 使用 `base64(String(id))`。两者都使用 keyset 分页（`Id < ?`），但编码格式不同，导致客户端 cursor 处理不一致。

5. **`ensureTable()` 每请求开销** — `operations.ts`（3 处调用）和 `tasks.ts`（6 处调用）在每个请求中执行 `CREATE TABLE IF NOT EXISTS`，为每次 API 调用增加约 20ms 的 D1 往返延迟。表存在性检查应在每个 Worker isolate 生命周期中执行一次，而非每个请求。

## 决策

### 1. Workflow 参数 Schema 注册

创建 `workflow-registry.ts` 模块，将每个 workflow 的调度参数定义为结构化元数据：

```typescript
interface WorkflowParam {
  name: string;
  type: "string" | "boolean" | "choice";
  required: boolean;
  default?: string | boolean;
  choices?: string[];
  description?: string;
}

interface WorkflowEntry {
  filename: string;
  displayName: string;
  description: string;
  category: "ingestion" | "maintenance" | "migration" | "monitoring";
  params: WorkflowParam[];
  safetyGate?: {
    field: string;
    requiredValue: string;
    triggerWhen: Record<string, unknown>;
  };
}
```

**新端点：** `GET /api/gh-actions/workflows/:name/schema` — 返回指定 workflow 的 `WorkflowEntry`。前端从此 schema 渲染动态表单。

**注册的 workflow（初始集合）：**

| Workflow | 参数数量 | 安全门 |
| -------- | ------- | ------ |
| `WeeklyDedup.yml` | 8 | `confirm_production = "I-UNDERSTAND"`（当 `dry_run = false`） |
| `Migration.yml` | 21 | `confirm_production = "I-UNDERSTAND"`（当 `dry_run = false`） |
| `TestIngestion.yml` | 2 | 无 |
| `RollbackD1.yml` | 10 | `confirm_production = "I-UNDERSTAND"`（当 `dry_run = false` 或 `force = true`） |

**调度时验证：** `POST /api/gh-actions/runs` 在调度前根据注册的 schema 验证输入。安全门在服务端强制执行——前端无法绕过。

**已可调度的 workflow**（`DailyIngestion.yml`、`AdHocIngestion.yml`、`QBFileFilter.yml`、`StaleSessionCleanup.yml`）可以稍后添加到注册表。它们已经可以通过通用调度端点正常工作。

### 2. Rollback 参数补全

更新 `POST /api/sessions/:id/rollback` 以接受并转发所有 `RollbackD1.yml` 参数：

**请求体（除 URL 中的 `session_id` 外全部可选）：**

```json
{
  "scope": "all",
  "force": false,
  "dry_run": true,
  "confirm_production": "",
  "log_level": "INFO",
  "runner": "self-hosted"
}
```

**行为变更：**
- `dry_run: true` 现在调度真正的 GH Actions dry run（而非返回 mock 数据）。响应包含 `job_id` 用于状态轮询。
- `dry_run: false` 需要 `confirm_production: "I-UNDERSTAND"`。服务端验证。
- `force: true` 允许回滚已 committed 的 session。同样需要 `confirm_production`。
- `scope` 限定回滚范围到特定数据库（`all`、`reports`、`operations`、`history`）。

**向后兼容：** 如果不发送请求体，使用默认值（`scope: "all"`、`dry_run: true`、`force: false`）。只在 URL 中发送 `session_id` 的现有客户端继续工作——它们会收到真正的 dry-run 调度结果而非 mock 数据。

### 3. Onboarding 测试通过 GH Actions 调度

将 501 stub 替换为通过 GH Actions 调度的异步连接测试：

**流程：**
1. `POST /onboarding/test`，body 为 `{ component: "qb" }` → 调度连接测试 workflow
2. 响应：`{ status: "dispatched", job_id: "test-20260524-...", poll_url: "/api/gh-actions/runs/{run_id}" }`
3. 前端轮询 `GET /api/gh-actions/runs/{run_id}` 直到完成
4. 完成的运行日志包含通过/失败结果

**组件到 workflow 的映射：**

| 组件 | Workflow | 输入 |
| ---- | -------- | ---- |
| `qb` | `TestIngestion.yml` | `{ runner: "self-hosted" }` |
| `proxy` | `TestIngestion.yml` | `{ runner: "self-hosted", proxy_spider: true }` |
| `smtp` | *（未来：专用测试 workflow）* | — |
| `javdb` | 直接检查（cookie 长度） | 无需调度 |

**优雅降级：**
- 如果 `GH_ACTIONS_TIER` 为 `"none"` 或 token 缺失 → 返回 `{ status: "unavailable", reason: "GitHub Actions not configured" }`（HTTP 200，非 501）
- SMTP 测试在专用 workflow 创建前保持不可用。返回 `{ status: "unavailable", reason: "SMTP connectivity test workflow not yet available" }`。

### 4. 统一 Cursor 编码

提取共享的 `server/services/cursor.ts` 模块：

```typescript
export function cursorEncode(values: Record<string, unknown>): string {
  return btoa(JSON.stringify(values));
}

export function cursorDecode<T = Record<string, unknown>>(cursor: string): T {
  return JSON.parse(atob(cursor)) as T;
}
```

**迁移：**
- `sessions.ts`：已使用 JSON 格式 `{sid}` — 重构为从 `cursor.ts` 导入
- `history.ts`：从 `base64(String(id))` 改为 `base64(JSON.stringify({id}))` — 对持有旧 cursor 的客户端是 **破坏性变更**

**破坏性变更缓解：** 旧格式 cursor（纯数字 base64）无法解析为 JSON。`cursorDecode` 函数捕获此错误并返回 400 错误，消息为：`"Invalid cursor format. Please reload the page."`。由于 cursor 是临时性的（仅在当前浏览会话中有效），这是可接受的。

### 5. `ensureTable()` 初始化一次中间件

将每请求的 `ensureTable()` 调用替换为每 isolate 一次的初始化：

**`app.ts` 中的 init 中间件：**

```typescript
let tablesInitialized = false;

app.use("/api/*", async (c, next) => {
  if (!tablesInitialized) {
    await initializeTables(c.env);
    tablesInitialized = true;
  }
  await next();
});
```

**`initializeTables()` 函数** 在每个数据库 binding 中通过单次 `db.batch()` 调用运行所有 `CREATE TABLE IF NOT EXISTS` 语句。这替换了分散在 `operations.ts` 和 `tasks.ts` 中的 9 个独立 `ensureTable()` 调用。

**Worker isolate 生命周期：** Cloudflare 在约 30 秒不活动后回收 isolate。`tablesInitialized` 标志在每次 cold start 时重置，因此表检查每个 isolate 只执行一次。对于低流量的个人项目，这意味着初始化每天最多运行几次。

**容错：** 如果 `initializeTables()` 失败（如 D1 暂时不可用），标志保持 `false`，下次请求时重试。

## 不在范围内

- **创建新 workflow YAML 文件**（如用于 SMTP 的 connectivity-test workflow）— CI/Python 侧工作。
- **前端页面修改** — 前端从 `/api/gh-actions/workflows/:name/schema` 动态渲染 workflow 表单。无需手动页面工作。
- **复杂参数业务逻辑校验** — 只做类型/必填/选项校验。不做语义校验（如"此 rclone 路径是否有效"）。
- **Python 后端修改** — 本 ADR 仅针对 TS 后端。
- **将已可工作的 workflow 加入注册表** — `DailyIngestion`、`AdHocIngestion` 等已经可以正常调度。为它们添加注册表条目是未来工作。

## 影响

### 正面

- 所有 20 个 GitHub Actions workflow 都可从 Web UI 触发（4 个新增 + 现有）。
- 回滚操作获得完整的参数控制 — `scope`、`force` 和真正的 dry-run 调度。
- Onboarding 测试从 501 错误降级为异步调度 — 在 Cloudflare 模式下功能可用。
- Cursor 编码在所有分页端点中保持一致 — 简化客户端处理。
- 仅 cold-start 时的表初始化消除了热路径上每请求约 20ms 的开销。

### 负面

- Workflow 注册表需要手动维护 — 当 workflow YAML 变更时必须更新。对于约 20 个不频繁变更的 workflow 的项目来说可以接受。
- Onboarding 测试变为异步 — 比直接连接检查更慢的用户体验（分钟级 vs 秒级）。这是 Cloudflare Workers 的固有限制。
- Cursor 格式变更对 `history.ts` 是破坏性变更 — 现有客户端 cursor 失效。通过 cursor 的临时性质缓解。
- Init 中间件增加了 cold-start 惩罚（约 50ms 的批量表创建）— 可接受，因为它替换了约 180ms 的每请求开销（9 次调用 × ~20ms）。

### 风险

- **Workflow 注册表漂移** — 如果 workflow YAML 添加/移除参数但注册表未更新，UI 将显示过时的选项。缓解：在 workflow 修改清单中记录"更新注册表"步骤。
- **GH Actions 速率限制** — 调度连接测试计入 GH Actions API 速率限制（认证用户 1,000 次请求/小时）。对于 1-2 个用户的个人项目可以接受。
- **`tablesInitialized` 误判** — 如果在初始化后删除了某个表，后续请求不会重新创建它。在生产环境中极不可能发生；Worker 重启会重置标志。
