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

### 种子质量

- `GET /api/quality/evaluations?limit=&movie_href=` — 需认证、只读，列出 ADR-024 影子质量评估。省略 `movie_href` 时返回最近评估。
- `GET /api/quality/evidence/{info_hash}` — 需认证、只读，返回 `production_download` 角色的种子级证据。

### 用户意图与发现

这些端点是双后端接口：Python FastAPI surface 与 Cloudflare Worker mirror
暴露相同 shape。UI 渲染由 `capabilities.features.watch_intent` 和
`capabilities.features.subscriptions` gate。

- `GET /api/subscriptions?active_only=&limit=&offset=` — 需认证，列出已关注演员（`ActorSubscription`）。
- `PUT /api/subscriptions/{actor_href}` — 仅 admin；通过 `{actor_name?, active}` 关注或重新启用演员。存储键是规范化后的 `/actors/<id>` href。
- `GET /api/subscriptions/{actor_href}` — 需认证，读取单个已关注演员。
- `DELETE /api/subscriptions/{actor_href}` — 仅 admin；取消关注演员。
- `GET /api/new-works?actor_href=&include_dismissed=&limit=&offset=` — 需认证，列出已关注演员的新作 feed。
- `POST /api/new-works/{video_code}/dismiss` — 仅 admin；从默认 feed 隐藏某条新作。

### 会话(Sessions)

- `GET /api/sessions?state=&cursor=&limit=` — ReportSessions 的游标分页列表。
- `GET /api/sessions/{session_id}` — 会话完整详情,包含写入记录。
- `POST /api/sessions/{session_id}/rollback` — 仅 admin;请求体 `{dry_run, include_pending, restore_from_audit}`。
- `POST /api/sessions/{session_id}/commit` — 仅 admin;请求体 `{force, drop_pending, fanout_claims, emit_metrics}`。`fanout_claims` 与 `emit_metrics` 默认为 `true`,让 HTTP 路径与 CLI 的完整 commit 行为对齐(MovieClaim 协调器 fanout + `pending_session_verify` JSONL 写入);如需仅修改 DB,显式传 `false`。

### 诊断 — 站点契约漂移(ADR-035)

站点契约漂移哨兵的只读接口。受 `capabilities.features.site_drift_sentinel` 控制(为 `false` 时前端隐藏漂移面板)。

- `GET /api/diag/ops-incidents?incident_type=site_drift` — 按类型过滤已持久化的运维事件;`incident_type=site_drift` 专门返回漂移事件(同时接受 `status`、`run_id`、`session_id`、`confidence`、`limit`)。
- `GET /api/diag/parse-field-health` — 每个契约字段最新一次已提交(committed)的解析健康度。响应:

  ```json
  { "items": [ { "page_type": "index", "field": "href", "severity": "critical",
                 "fill_rate": 0.99, "sample_count": 120, "observed_at": "...",
                 "baseline": null, "threshold": 0.99, "status": "ok" } ] }
  ```

  `status ∈ ok | critical_drift | soft_drift | no_baseline | insufficient_sample`。

### 测试模式(仅供 E2E)

- `POST /api/test/reset` — 仅当服务以 `TEST_MODE=1` 启动时存在。会清空 ops/history 表。**绝不可在生产环境启用。**
- `POST /api/test/seed-sessions` — 仅当服务以 `TEST_MODE=1` 启动时存在。幂等地写入三条确定性会话(`test-committed-001`、`test-finalizing-002`、`test-inprogress-003`),分别覆盖 committed/audit、finalizing/pending、in_progress/audit 三种生命周期,供真实数据 E2E rollback 测试使用。响应:`{seeded, session_ids}`。
