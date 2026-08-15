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

以下三个端点由 ADR-024 IMP-08（assist 模式，2026-06-19）新增。仅当 `TORRENT_QUALITY_POLICY_MODE=assist` 时，底层评估行才会被带 gate 的 assist 评估器填充。

- `GET /api/quality/recommendations?movie_href=` — 需认证、只读。按分类返回当前生产选择与 `shadow_rank=1` 推荐候选，以及 reason-code 差异。响应结构：`{items: [{javdb_category, current, recommended, reason_diff}]}`。
- `GET /api/quality/needs-review?limit=` — 需认证、只读。返回 `decision='needs_review'` 或 `would_replace_current_choice=true` 的评估。`limit` 默认 50，上限 200；`limit<=0` → 400。
- `POST /api/quality/review-labels` — 仅 admin。请求体：`{info_hash, movie_href, scoring_version, label, note?}`，其中 `label ∈ accept | reject | skip`。通过 `TorrentQualityReviewRepo` 记录运维决策（该标注数据集供 Phase 3 调优阈值使用）。返回 `{status: "recorded"}`。`label` 非法 → 422。`reviewed_at` 由服务端生成；`reviewer` 从 JWT subject 读取。

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

### D1 迁移

仅 admin。仅 Python 后端提供 —— TypeScript Worker 未镜像该接口。

- `GET /api/migrations` — 列出 `javdb/migrations/d1/*.sql` 及其已应用状态,状态读自 `system_state` 中的 `migration_applied:<id>` 键。
- `POST /api/migrations/{id}/run` — 请求体 `{dry_run, acknowledge_unrecorded}`,默认分别为 `true` 与 `false`。
  - `dry_run=true` 返回 `{sql_preview, statements}`;`statements` 统计可执行语句数,不含注释。永远不需要 `acknowledge_unrecorded` —— 预览不改变任何东西。
  - `dry_run=false` 将迁移应用到 D1 并记录,返回 `applied: true`。对任何账本中没有标记的迁移(即所有并非由本端点应用过的迁移),还需要 `acknowledge_unrecorded: true` —— 见下方 `migrations.unrecorded`。

执行器从迁移文件自身的 `wrangler d1 execute javdb-<history|reports|operations>` 头部行解析目标数据库;当该行缺失或指向多个数据库时,**直接拒绝而非猜测**(`400 migrations.target_db_unresolved`)。

拒绝情形(均在任何语句执行前抛出)。其中仅依赖迁移文件本身的几项 —— `target_db_unresolved`、`empty`、`not_atomic`、`pragma_unsupported`、`drop_table_unsupported` —— 在 `unrecorded` **之前**判断,这样格式有问题的迁移会直接报出真正的错误,而不是把操作者训练成习惯性附带确认标志:

| 错误码 | 状态 | 含义 |
| --- | --- | --- |
| `migrations.backend_not_d1` | 409 | `STORAGE_BACKEND` 不是 `d1`/`dual`。 |
| `migrations.already_applied` | 409 | 已记录为应用过;重放不幂等的 `ALTER TABLE ADD COLUMN` 会中途失败。 |
| `migrations.unrecorded` | 409 | 该迁移没有标记 —— 这只说明「未经由本端点应用」,不等于「未被应用」。对照线上 schema 核验后,以 `acknowledge_unrecorded: true` 重发。 |
| `migrations.applied_state_unreadable` | 503 | 账本读取失败,「是否已应用」未知。fail closed —— 把「未知」当成「未应用」才是危险的那个猜测。 |
| `migrations.target_db_unresolved` | 400 | 没有唯一的 `wrangler d1 execute javdb-<db>` 头部。 |
| `migrations.unparseable` | 400 | 字符串字面量未闭合 —— 切分器无法判断语句在哪里结束,会送出半截语句。 |
| `migrations.empty` | 400 | 没有可执行语句。 |
| `migrations.not_atomic` | 400 | 语句数超出单个 D1 batch 的上限(50),只能分块执行 —— 即无法保证原子性。请改用 Wrangler。 |
| `migrations.pragma_unsupported` | 400 | 脚本包含 `PRAGMA`,而本执行器把所有语句打包进一个事务,事务内的 PRAGMA 是空操作。仓库中的实例是 `2026_05_13_session_id_to_text_reports.sql`。请改用 Wrangler。 |
| `migrations.drop_table_unsupported` | 400 | 脚本包含 `DROP TABLE`。批次内无法放宽外键,任何仍在引用该表的行都会让整批失败 —— 重建表的那几个迁移(`2026_05_13_session_id_to_text_*`、`2026_06_16_newworks_composite_pk`)即是实例。该拒绝是一刀切的,不去逐个分析引用关系。请改用 Wrangler。 |
| `migrations.claim_failed` | 503 | 无法在账本中预占该迁移,因而不能排除并发执行。任何语句都未执行。 |
| `migrations.connection_failed` | 503 | 无法打开 D1 客户端 —— 通常是凭据缺失。什么都没发出去,因此预占会被释放。 |
| `migrations.ledger_absent` | 409 | `system_state` 尚不存在,无法预占。此状态下只有创建该表的那个迁移可以执行;请先应用 `0042_system_state_table`。 |

所有语句作为**单个 D1 batch** 提交,该批次是原子的:要么整体落地,要么整体回滚。账本条目在执行**之前**以朴素 `INSERT` 预占 —— `system_state.key` 是主键,因此两个管理员并发执行同一迁移由 D1 串行化,而不是靠一个本进程无法保证原子的「先读后写」窗口;失败方会得到 `already_applied`。

另有三个在执行**开始之后**才产生的错误码,其中前两个的差别只在一点 —— D1 有没有告诉我们结果:

- `502 migrations.execution_failed` —— **确实什么都没落地**,这是被证实的而非假定的:要么根本没有请求离开本进程(熔断器在 POST 之前就拒绝了),要么恰好发出一次且被 D1 拒绝 —— 原子批次会整体回滚。预占会被释放,该迁移重新显示为未应用,修掉原因后可安全重试。若释放本身也失败,消息会明确说明 —— 重试前请先清除该标记。
- `502 migrations.outcome_unknown` —— 请求已经发出,但结果无法确定:超时、连接中断、未归类的异常、批次返回的结果数少于提交的语句数,**或者在传输层重发批次之后才收到的拒绝**。这些情形都**不是**回滚:D1 可能已经提交了该批次、只是响应在回程中丢失。最后一种尤其要注意 —— 传输层会自行重试瞬时失败,而迁移批次并不幂等,所以第一次尝试已提交、仅响应丢失时,第二次尝试会以 `duplicate column name` 之类被拒绝,那是在描述这次重试,而不是这个迁移。两个 502 的区分依据是端口的已发送请求计数,而不是异常类型。预占会被刻意**保留**,使该迁移显示为已应用,从而挡住误重放。请核对线上 schema:若变更已落地,保留该标记;若未落地,清除后重跑。
- `500 migrations.record_failed` —— 只有创建 `system_state` 自身的 bootstrap 迁移会走到这里:它没有可预占的账本,只能事后补记。schema 变更已经落地,**不要重跑**,请手工补记账本。

> **`applied` 的含义是「经由本端点应用过」,而非「D1 中已存在」。** `migration_applied:` 键只在这里被写入,因此用 `wrangler d1 execute` 执行的迁移(所有历史迁移都是如此)仍会显示为未应用。这正是 `acknowledge_unrecorded` **按迁移逐个**要求、而非只在账本为空时要求的原因:若以「账本为空」为条件,这道保护只覆盖一次请求 —— 第一次经 API 应用写入标记之后,其余所有 wrangler 应用过的文件都会失去这道减速带,却仍然显示为未应用。其中有几个是破坏性的 —— `2026_05_13_session_id_to_text_*` 这组会按硬编码列清单重建表并 `DROP` 原表,今天重放其中任何一个都会丢掉此后新增的全部列。没有记录即等于未知,而未知必须由人来回答,每个文件一次。

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
