# ADR-017: Cloudflare-First 全栈部署

| 字段       | 值                                         |
| ---------- | ------------------------------------------ |
| **状态**   | 已完成                                     |
| **日期**   | 2026-05-23                                 |
| **作者**   | Ted                                        |
| **相关**   | [ADR-008](../ADR-008-Frontend-Rewrite/ADR-008-frontend-rewrite-architecture.md), [ADR-010](../ADR-010-D1-Access-Port/) |

## 背景

系统目前分散部署在多个平台上：

- **Cloudflare**：D1 数据库（单一事实来源）、Proxy Coordinator Worker 含 6 个 Durable Objects、DNS 区域
- **Docker/GHCR**：FastAPI 后端（uvicorn）、Vue 3 前端（nginx）、spider cron 容器
- **GitHub Actions**：CI/CD 流水线、定时抓取、回滚工作流

这种平台割裂带来了运维开销：
1. D1 从 Docker 容器通过 HTTP API 访问，相比 Worker 原生绑定增加了延迟
2. 前后端部署分别管理（Docker 镜像、docker-compose）
3. 没有 Preview Deployments 用于快速迭代
4. 为一个本质上是 CRUD API + 任务调度器的系统维护 Docker 基础设施（VPS、容器编排）

前端（`javdb-autospider-web`）已经是独立的 Vue 3 SPA（按 ADR-008），通过单一 `VITE_API_BASE_URL` 环境变量控制 API 路由。

## 决策

将全栈应用（Vue 3 前端 + API 后端）部署为单个 **Cloudflare Worker + Assets**，同时**保留现有 Docker 部署**作为替代方案。

### 架构

```
Cloudflare Workers + Assets (javdb-autospider-web 仓库)
├── Vue 3 SPA (静态资源, 通过 ASSETS 绑定)
└── Worker (server/worker.ts)
    ├── Hono 框架 (TypeScript)
    ├── D1 绑定 (原生, 零延迟)
    ├── DO 绑定 (Proxy Coordinator, 通过 Service Binding)
    └── GitHub Actions Dispatch (重型任务)

Docker 部署 (JAVDB_AutoSpider_CICD 仓库, 保持不变)
├── FastAPI + Uvicorn
├── D1 HTTP API 或 SQLite
└── subprocess 执行 (spider, pipeline, rclone, qB)
```

### 关键决策

#### D1: API 后端用 TypeScript (Hono) 重写，非 Python-on-Workers

Cloudflare Workers Python (Pyodide) 已评估并否决：
- **`lxml`** (C 扩展): Pyodide 中不可用
- **`cryptography`** (C/Rust 扩展): Pyodide 中不可用
- **`bcrypt`** / `passlib[bcrypt]` (C 扩展): Pyodide 中不可用
- **`curl_cffi`** (C 扩展): Pyodide 中不可用

使用 Hono 进行 TypeScript 重写是务实的选择：
- Hono 是 Cloudflare Workers 的标准框架
- D1 绑定原生且完全类型化
- Web Crypto API 替代 PyJWT + cryptography 处理 JWT 认证
- TypeScript 与 Vue 3 前端统一语言
- 80% 的 API 路由是 SQL 查询 + JSON 序列化，可直接翻译

#### D2: 重型任务派发到 GitHub Actions，不在 Workers 中执行

Workers 有 CPU 时间限制（Free/Pro 计划每请求 10-30ms）。长时间运行操作（spider、pipeline、rclone、qB）派发到现有 GitHub Actions 工作流：

| 操作 | 派发的工作流 |
| ---- | ----------- |
| 每日抓取 | `DailyIngestion.yml` |
| 自定义 URL 抓取 | `AdHocIngestion.yml` |
| rclone 扫描/执行 | `RcloneManager.yml` |
| qB 文件过滤 | `QBFileFilter.yml` |
| Session 回滚 | `RollbackD1.yml` |

API 端点触发派发后立即返回。前端轮询 GitHub Actions API 获取运行状态。

#### D3: `javdb-autospider-web` 从纯前端演进为全栈 Workers 项目

按 ADR-008，前端在独立仓库中。本 ADR 扩展该仓库以包含 Cloudflare Worker（Hono API）和静态资源，使其成为全栈项目：

```text
javdb-autospider-web/
├── src/                          # Vue 3 前端 (现有, 不变)
├── server/                       # Worker + API 业务代码
│   ├── worker.ts                 # Worker 入口 (路由: API vs 静态资源)
│   ├── app.ts                    # Hono app + 路由挂载
│   ├── routes/                   # 路由处理器 (12 个模块)
│   │   ├── auth.ts
│   │   ├── history.ts
│   │   ├── sessions.ts
│   │   ├── config.ts
│   │   ├── tasks.ts
│   │   ├── operations.ts
│   │   ├── explore.ts
│   │   ├── diagnostics.ts
│   │   ├── capabilities.ts
│   │   ├── onboarding.ts
│   │   ├── system.ts
│   │   └── gh-actions.ts
│   ├── middleware/
│   │   ├── auth.ts               # JWT (Web Crypto API)
│   │   └── cors.ts
│   ├── services/
│   │   ├── d1-repos.ts           # D1 仓库层
│   │   ├── gh-dispatch.ts        # GitHub Actions 工作流派发
│   │   └── config-store.ts       # D1 配置存储
│   └── types/                    # 从 OpenAPI schema 生成
├── wrangler.toml                 # Worker 配置: main, [assets], D1 绑定
├── package.json
└── vite.config.ts
```

#### D4: Docker 部署保留，Python 代码库不做修改

`JAVDB_AutoSpider_CICD` 中的 Python FastAPI 后端不做修改。两种部署模式并存：

| 维度 | Cloudflare Workers | Docker |
| ---- | ------------------ | ------ |
| 前端 | Vue SPA (Workers Assets) | nginx 容器 |
| API | Hono + D1 绑定 (TS) | FastAPI + D1 HTTP API (Python) |
| 数据库 | D1 (原生绑定) | D1 (HTTP API) 或 SQLite |
| 重型任务 | GH Actions 派发 | subprocess 本地执行 |
| 代码仓库 | `javdb-autospider-web` | `JAVDB_AutoSpider_CICD` |
| API 合同 | 同一份 `openapi.json` | 同一份 `openapi.json` |

#### D5: Explore 端点使用 cheerio 而非 Rust WASM（初期）

Explore 端点（HTML 获取 + 解析）在 Workers 中使用 `cheerio` 进行 DOM 解析。现有解析器的 Rust WASM 编译推迟为未来优化 — explore 是用户触发的，不是性能热点。

#### D6: 邮件通知通过 API 服务或 GH Actions 派发

Workers 无法使用原始 SMTP（没有 TCP socket）。邮件通知通过以下方式之一发送：
- 邮件 API 服务（Resend、Mailgun 或 Cloudflare Email Workers）
- 派发到已有的处理邮件的 GH Actions 工作流

#### D7: README 部署指南 + `.dev.vars.example`，而非 Deploy Button

已评估 [Cloudflare Deploy Buttons](https://developers.cloudflare.com/workers/platform/deploy-buttons/) 用于一键部署，但**不适用**于本项目：

1. **不支持 Pages** — Deploy Button 仅支持 Workers，不支持 Pages 项目
2. **现有 D1 数据库** — Deploy Button 会自动创建新的 D1 数据库；本项目需要连接已有的数据库（`javdb-history`、`javdb-reports`、`javdb-operations`）

替代方案：在 `javdb-autospider-web` README 中提供：

- **`.dev.vars.example`** — 列举所有必需的密钥及说明，遵循 Cloudflare 的自文档化部署约定。用户复制为 `.dev.vars` 用于本地开发，或通过 `wrangler pages secret put` 设置生产密钥。
- **分步部署指南** — 涵盖前置条件、D1 数据库 ID 查询、`wrangler.toml` 配置、密钥设置、构建部署、验证等步骤。
- **Deploy to Cloudflare 徽章**（占位符，暂不可用） — 链接到 README 中的部署指南章节。若 Cloudflare 未来支持 Pages 的 Deploy Button，可将徽章更新为指向 `deploy.workers.cloudflare.com`。

### D1 仓库层

SQL 语句直接从 Python 仓库层复制 — D1 就是 SQLite，SQL 完全一致。只是绑定 API 不同：

```typescript
// D1 绑定 API (TypeScript)
export function createHistoryRepo(db: D1Database) {
  return {
    async loadHistory(filters: HistoryFilters) {
      const stmt = db.prepare(
        "SELECT * FROM MovieHistory WHERE release_date >= ?"
      ).bind(filters.since);
      const { results } = await stmt.all<MovieHistoryRow>();
      return results;
    },
  };
}
```

三个 D1 数据库在 `wrangler.toml` 中绑定：

```toml
[[d1_databases]]
binding = "HISTORY_DB"
database_name = "javdb-history"
database_id = "<existing-id>"

[[d1_databases]]
binding = "REPORTS_DB"
database_name = "javdb-reports"
database_id = "<existing-id>"

[[d1_databases]]
binding = "OPERATIONS_DB"
database_name = "javdb-operations"
database_id = "<existing-id>"
```

### 配置存储迁移

`api_config` 和 `job_runs` 表都添加到 **operations** D1 数据库（`OPERATIONS_DB`）中，与现有模式一致（运维状态存储在 `operations.db` 中）。

Python API 将运行时配置存储在 `reports/api_config_store.json`（Fernet 加密）。在 Workers 中，替换为 D1 表：

```sql
CREATE TABLE api_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

敏感值使用 Web Crypto API（AES-GCM）加密，密钥来自 Workers 环境密钥 `SECRETS_ENCRYPTION_KEY`。

### 任务元数据迁移

Python API 将任务元数据存储为 `logs/jobs/*.meta.json` 文件。在 Workers 中，替换为 D1 表：

```sql
CREATE TABLE job_runs (
    job_id       TEXT PRIMARY KEY,
    workflow     TEXT NOT NULL,
    gh_run_id    INTEGER,
    status       TEXT NOT NULL DEFAULT 'dispatched',
    inputs       TEXT,           -- JSON
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
```

## 路由迁移复杂度

| FastAPI 路由 | Hono 路由 | 复杂度 | 说明 |
| ------------ | --------- | ------ | ---- |
| `auth.py` | `auth.ts` | 低 | JWT 签发/验证 via Web Crypto |
| `history.py` | `history.ts` | 低 | 纯 D1 查询，SQL 不变 |
| `sessions.py` | `sessions.ts` | 低 | D1 查询 + rollback 派发 |
| `config.py` | `config.ts` | 低 | D1 表替代 JSON 文件 |
| `capabilities.py` | `capabilities.ts` | 低 | 读配置，返回标志 |
| `system_state.py` | `system-state.ts` | 低 | 单 D1 表查询 |
| `diagnostics.py` | `diagnostics.ts` | 中 | D1 聚合查询 |
| `onboarding.py` | `onboarding.ts` | 中 | D1 读写 + 初始化逻辑 |
| `explore.py` | `explore.ts` | 中 | HTML fetch + cheerio 解析 |
| `gh_actions.py` | `gh-actions.ts` | 中 | GitHub API 调用 |
| `tasks.py` | `tasks.ts` | 高 | subprocess → GH Actions 派发 |
| `operations.py` | `operations.ts` | 高 | qB/rclone/email → 派发或 API |

低: 6/12 (SQL 查询 → JSON 直接翻译)
中: 4/12 (需要翻译业务逻辑)
高: 2/12 (需要重新设计执行模式)

**排除的路由：**

- `test_mode.py` — 仅用于开发/测试（重置 DB 状态，直接 SQLite 访问）。不部署到 Cloudflare；开发测试使用 Docker 模式。
- `system.py` — 运行子进程进行健康检查和会话刷新。在 Cloudflare 模式中，健康检查由 Workers 内置监控替代；会话刷新派发到 GH Actions。

## 实施阶段

### Phase 1 — 基础骨架 + 只读查询 (MVP)

- Cloudflare Pages 项目搭建（Vite + Hono）
- Auth 中间件（JWT via Web Crypto）
- D1 绑定配置（3 个数据库）
- 4 个只读路由上线：`capabilities`、`system-state`、`history`、`sessions`
- 前端 `VITE_API_BASE_URL` 指向 Pages URL
- **验收标准**：前端可以登录、查看历史记录和会话列表

### Phase 2 — 配置 + 诊断 + Explore

- `config`、`diagnostics`、`onboarding` 路由
- Config store 迁移到 D1 表
- Explore 端点（cheerio 解析）
- **验收标准**：前端所有查询页面功能正常

### Phase 3 — 执行类操作 + GH Actions 桥接

- `tasks`、`operations`、`gh-actions` 路由
- GH Actions dispatch 服务
- 前端任务轮询 UI
- **验收标准**：前端可触发 spider/pipeline，实时查看状态

### Phase 4 — 优化 + Docker 兼容验证

- Docker 部署回归测试
- 性能优化（D1 查询、冷启动）
- 可选：Rust WASM parser for explore
- E2E 测试覆盖 Cloudflare 部署

## 风险与缓解

| 风险 | 影响 | 缓解措施 |
| ---- | ---- | -------- |
| Workers CPU 限制 (10-30ms) | 复杂查询可能超时 | D1 查询本身很快；必要时预计算聚合 |
| GH Actions API 限速 (5000 req/hr) | 频繁轮询状态 | 指数退避 + 在 D1 中缓存最近状态 |
| OpenAPI 合同漂移 | TS API 与 Python API 不一致 | CI 合同测试：两个实现共享测试 fixture |
| D1 绑定与 HTTP API 行为差异 | 边界情况不一致 | 统一测试用例覆盖两种模式 |

## 被否决的替代方案

### A1: Vercel（双项目 — 前端 + Python Serverless）

前端部署在 Vercel Static，后端部署在 Vercel Python Serverless Functions (`@vercel/python`)。

- **否决原因**：与现有 Cloudflare 基础设施（D1、DO）产生平台割裂。D1 访问需通过 HTTP API（无原生绑定）。无法访问 Durable Objects。Python 运行时冷启动比 Workers 慢。引入第三个平台（Cloudflare + Docker + Vercel）。

### A2: Cloudflare Workers Python (Pyodide)

通过 Pyodide WebAssembly 运行时在 Workers 上运行现有 FastAPI 代码。

- **否决原因**：关键 C 扩展依赖（`lxml`、`cryptography`、`bcrypt`、`curl_cffi`）在 Pyodide 中不可用。替换这些核心依赖会失去"复用现有代码"的意义。

### A3: Vercel 前端 + Vercel Rewrites 到 Docker 后端

前端在 Vercel 上，API 调用通过 `vercel.json` rewrites 代理到外部 Docker 后端。

- **否决原因**：后端并未真正在 serverless 平台上。仍需 VPS/Docker 管理。增加代理延迟。未减少平台割裂。

### A4: Workers 上的 Thin TS 网关 + Docker 后端通过 Cloudflare Tunnel

轻量 Hono 网关在 Workers 上处理认证 + 简单 D1 查询；复杂路由通过 Cloudflare Tunnel 代理到 Docker 后端。

- **未完全否决**：这是有效的渐进式迁移路径。如果完整重写时间线过长，可作为 Phase 0。设计支持从 A4 → 完整方案 A（推荐方案）的逐步演进，通过将路由从代理逐步移至原生实现。

## Cloudflare 定价

| 维度 | 免费计划 | Pro ($20/月) |
| ---- | -------- | ------------ |
| 请求 | 100,000/天 | 无限 |
| Function 调用 | 100,000/天 | 10M/月 |
| D1 读取 | 5M/天 | 50B/月 |
| D1 写入 | 100,000/天 | 50M/月 |
| CPU 时间 | 10ms/请求 | 30ms/请求 |
| 构建次数 | 500/月 | 5,000/月 |

对于个人项目，**免费计划大概率足够**。D1 已在使用中，不产生额外费用。
