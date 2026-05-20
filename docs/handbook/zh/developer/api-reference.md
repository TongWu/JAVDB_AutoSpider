# API 参考

本页列出 `apps/api` 暴露的 HTTP 端点。机器可读的权威 schema 位于 [`docs/api/openapi.json`](../../../api/openapi.json);前端仓库(`javdb-autospider-web`)生成的 TypeScript 类型即由其派生。

如需面向解析功能的 REST 用法(页面解析等),请参阅 [api-usage-guide.md](api-usage-guide.md)。

## Phase 1 前端控制台端点

这些端点于 2026-05 加入,用于支撑新的 Web 控制台(`javdb-autospider-web`)。

### 发现

- `GET /api/capabilities` — 运行时特性开关 + 版本信息。前端用它来按部署环境控制 UI 显隐。完整结构见 [openapi.json](../../../api/openapi.json)。

### Onboarding

- `GET /api/onboarding/status` — 返回 `{completed, required_missing[], skippable_missing[]}`。
- `POST /api/onboarding/test` — 测试某个组件(`javdb`/`qb`/`proxy`/`smtp`);返回 `{component, ok, message, details?}`。
- `POST /api/onboarding/complete` — 仅 admin;标记初始化完成。
- `POST /api/onboarding/dismiss-hint` — 仅 admin;关闭 Dashboard 提示卡片。

### 通用状态

- `GET /api/system/state?key=...` — 从 `system_state` 读取 KV 对。
- `PUT /api/system/state` — 仅 admin;写入 KV 对。

### 会话(Sessions)

- `GET /api/sessions?state=&cursor=&limit=` — ReportSessions 的游标分页列表。
- `GET /api/sessions/{session_id}` — 会话完整详情,包含写入记录。
- `POST /api/sessions/{session_id}/rollback` — 仅 admin;请求体 `{dry_run, include_pending, restore_from_audit}`。
- `POST /api/sessions/{session_id}/commit` — 仅 admin;请求体 `{force, drop_pending, fanout_claims, emit_metrics}`。`fanout_claims` 与 `emit_metrics` 默认为 `true`,让 HTTP 路径与 CLI 的完整 commit 行为对齐(MovieClaim 协调器 fanout + `pending_session_verify` JSONL 写入);如需仅修改 DB,显式传 `false`。

### 测试模式(仅供 E2E)

- `POST /api/test/reset` — 仅当服务以 `TEST_MODE=1` 启动时存在。会清空 ops/history 表。**绝不可在生产环境启用。**
- `POST /api/test/seed-sessions` — 仅当服务以 `TEST_MODE=1` 启动时存在。幂等地写入三条确定性会话(`test-committed-001`、`test-finalizing-002`、`test-inprogress-003`),分别覆盖 committed/audit、finalizing/pending、in_progress/audit 三种生命周期,供真实数据 E2E rollback 测试使用。响应:`{seeded, session_ids}`。
