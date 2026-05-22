# ADR-008: 前端重写 — 独立 `javdb-autospider-web` 仓库

**状态**: 已接受 —— Phase 1 已交付；Phase 2 计划中、Phase 3 推迟（截至 2026-05-19）
**日期**: 2026-05-17（2026-05-18 修订）
**决策者**: 头脑风暴会议（设计规范：`docs/superpowers/specs/2026-05-16-frontend-rewrite-design.md`）
**关联实现计划 (Related Implementation Plans)**: [IMP-ADR008-01](../impl/archive/IMP-ADR008-01-frontend-phase1-backend-prerequisites.md)（BE 前置——2026-05-16 完成）、[IMP-ADR008-02](../impl/IMP-ADR008-02-frontend-phase1-completion.md)（Phase 1 收尾——功能完整，cutover 待落地）、[IMP-ADR008-03](../impl/IMP-ADR008-03-frontend-phase2-full-cli-coverage.md)（Phase 2——计划中，未启动）、[IMP-ADR008-04](../impl/IMP-ADR008-04-frontend-phase3-power-user.md)（Phase 3——推迟，待 Phase 2 dogfooding 后定）

## 待办 (Outstanding Work)

- IMP-ADR008-02 cutover 残留：E2E fixtures、2 条剩余用户旅程、BE 清理、从已删除的 `apps/web/` 切换至独立 `javdb-autospider-web` 仓库。
- IMP-ADR008-03（Phase 2 —— 全 CLI 表面覆盖）：未启动。
- IMP-ADR008-04（Phase 3 —— 高级用户特性与分析）：推迟；具体范围待 Phase 2 dogfooding 累积数据后定。

---

## 背景

monorepo 中现有的 `apps/web/` 目录已停滞 51 天以上，而后端在此期间增加了 40 多次提交。旧前端的问题：

- 仅暴露约 30% 的可用 API 功能。
- 存在未完成的 TypeScript 迁移遗留的重复 `.js`/`.ts` 文件。
- DashboardPage 中有运行时 bug（`i18n t()` 被 `v-for` 循环变量遮蔽）。
- 包含占位/禁用的 UI，且无自动化测试。
- auth 刷新流程静默失效（无单次请求队列；并发 401 导致级联重试）。

修补现有代码的成本高于重写。系统还需要首次运行引导向导、Browse 页面（服务端解析的 javdb 结果）、Session 回滚 UI，以及覆盖所有 CLI 可表达操作的 UI——这些目前均不存在。

---

## 决策

在**独立 GitHub 仓库**（`javdb-autospider-web`）中构建新前端，与 `JAVDB_AutoSpider_CICD` monorepo 分离。旧 `apps/web/` 和 `apps/desktop/` 目录在切换时删除。

### D1: 独立仓库（非 monorepo）

前端拥有独立的仓库、CI 和 Docker 镜像。主仓库发布后端 Docker 镜像和 `openapi.json` 产物供前端 CI 使用。

**理由**: 单个 Vue 应用不需要 monorepo 工具（pnpm workspaces、nx、turborepo）。独立仓库将前端发布节奏与后端解耦，唯一的契约是 OpenAPI schema——通过类型生成 + 契约测试强制保证。

### D2: Vue 3 + Naive UI

运行时：Vue 3.5、vue-router 4、Pinia 2、vue-i18n 9、Naive UI 2.40+、axios、date-fns、@vueuse/core。

**理由**: 继续使用 Vue 3（操作者熟悉的唯一框架）。Naive UI 是唯一广泛使用的、与"友好卡片"视觉方向匹配的 Vue 3 原生组件库——Vuetify 偏 Material 风格、Ant Design Vue 偏企业密集型、Element Plus 偏表单型。

### D3: 视觉方向 — "友好卡片"（方向 C）

Notion / Stripe Dashboard 风格：圆角卡片（12–16 px 圆角）、柔和色调、轻阴影、紫粉色调强调（主色 `#7c3aed`，渐变强调色 `#ec4899`），底色 `#faf9f7` 亮色 / `#0e0d12` 暗色。

设计令牌编码为 Naive UI `themeOverrides`，存放在单一 `src/theme/index.ts` 文件中，涵盖亮色和暗色变体。完整令牌集见附录 A。

**理由**: 操作者每次使用控制台 2–5 分钟；友好的、低疲劳的视觉效果加上清晰的卡片边界和柔和的状态颜色，相比密集的"管理后台"风格能降低认知负担。

### D4: 单一代码库的三种部署拓扑

| 拓扑 | `VITE_API_BASE_URL` | 后端 `INGESTION_MODE` | `capabilities.deployment` |
|---|---|---|---|
| 同机部署 | `http://api:8100`（compose） | `local` | `colocated` |
| 分离部署 | `https://api.example.com` | `local` 或 `github` | `split` |
| GH 托管 | 同分离部署 | `github` | `split` / `unknown` |

前端仅通过 `VITE_API_BASE_URL` 配置。`deployment` 字段使用中性术语——不涉及特定供应商名称。

**理由**: 自部署用户运行多样化的环境。一个前端镜像服务所有拓扑，消除了逐部署维护的开销。

### D5: 能力驱动的运行时发现

`GET /api/capabilities` 返回版本、ingestion_mode、storage_backend、功能标志、GH Actions 层级和部署类型。前端缓存 5 分钟 TTL，Settings 保存时强制失效。

功能门控（侧边栏可见性、表单字段、操作按钮）读取 `capabilitiesStore`——不使用构建时常量或 URL 启发式。

**理由**: 不同部署拓扑有不同的功能集（例如同机模式无 GH Actions、未配置则无 PikPak）。运行时发现使前端代码库保持无关性。

### D6: 认证 — JWT HS256 + CSRF 双提交 + 单次刷新队列

沿用现有后端认证。前端修复失效的刷新流程：

- **单次刷新队列**: 收到 401 时，发起一次刷新请求。所有并发失败的请求排队，成功后用新令牌重放。刷新失败时拒绝所有排队请求并跳转到 `/login`。
- **CSRF 令牌注入**: 变更操作动词从 cookie 读取 `csrf_token` 并设置 `X-CSRF-Token` 头。
- **会话过期通知**: 访问令牌到期前 2 分钟，通过 Naive UI Notification 主动提示刷新。
- **角色门控**: `router.meta.roles` + 导航守卫。只读用户看到只读界面；写操作在 UI 中隐藏并由后端 403。

**理由**: 现有后端认证机制够用；前端才是出问题的一方。单次队列防止旧代码中观察到的重试风暴。

### D7: API 约定

- **无 `/v1/` 版本前缀**。前端对 `openapi.json` 编译；不匹配则 CI 失败。
- **错误信封** 用于所有 4xx/5xx: `{ error: { code, message, details, request_id, trace_id } }`。
- **游标分页**: `?cursor=&limit=` → `{ items, next_cursor, total_estimate }`。
- **`X-Request-Id`** 客户端生成，后端回显，错误提示中显示前 8 位。
- **后端版本偏差防护**: 当 `capabilities.build.backend_version` < `src/api/min-backend-version.ts` 时前端拒绝启动。

**理由**: 无版本前缀避免了单消费者 API 的路由膨胀。游标分页对大数据集比偏移量更高效。客户端生成的请求 ID 实现端到端关联而无需服务端开销。

### D8: Browse — 服务端解析，无交互式 iframe

Browse 页面在前端控制的 DOM 中渲染服务端解析的 javdb 结果。三个子模式：

1. **Resolve** — 番号 → `search-by-video-code` 或 URL → `resolve`。前端渲染详情卡片 + 磁力表格。
2. **Lists** — 分类/排行/标签页面通过 `POST /api/parse/url`。前端渲染 CSS 网格卡片。
3. **Preview** — 诊断用：粘贴 URL → `proxy-page` → 沙箱 iframe（`sandbox="allow-same-origin"` 仅限）。只读；"Parse this" 切换到 Resolve。

**理由**: 嵌入可导航的 javdb.com 与后端的 `proxy-page` 端点不兼容（单次清理快照加限制性 CSP），且需要一个同源代理并放宽 CSP——这是明确的非目标。服务端解析方式将第三方 HTML 留在服务端，仅渲染前端控制的 DOM。

**原始规范修正**: §6.3 说 Lists 将使用 `parse/top`、`parse/category`、`parse/tags`——但这些端点仅接受原始 HTML（`HtmlPayload`）。服务端获取+解析的实际契约是 `POST /api/parse/url` 配合 `UrlPayload`。

### D9: D1 状态标记 — 前端渲染，批量视口观察

标记渲染在前端 DOM 中（搜索结果卡片、列表卡片、磁力表格行）。

- `IntersectionObserver` 收集可见卡片 `href`。
- 150 ms 防抖 → `POST /api/explore/index-status` 每次最多 50 个 href。
- 响应映射 href → `{committed, pending, failed_recent, unknown}`。
- 前端维护 `Map<href, status>`，每次挂载一次性缓存。
- 标记：8 px 圆点。颜色：committed `#10b981`、pending `#f59e0b`、failed_recent `#dc2626`、unknown `#9ca3af`。

**理由**: 移除了对 iframe `postMessage`、javdb 页面 DOM 注入或拦截第三方脚本的任何依赖。批量视口观察限制了请求量。

### D10: 状态管理 — 6 个 Pinia store + capabilities 启动门

```
stores/
├── auth.ts          JWT、角色、用户、登录/登出/刷新
├── capabilities.ts  /api/capabilities 缓存、5 分钟 TTL、启动状态
├── tasks.ts         运行中任务列表 + 轮询调度
├── ui.ts            侧边栏折叠、主题、活跃提示
├── onboarding.ts    向导步骤、已填字段、测试结果
└── i18n.ts          语言、切换操作
```

页面局部状态留在组件局部 `ref`（可选 `useStorage` from @vueuse/core），不全局化。

**Capabilities 启动门**: `App.vue` 渲染 `<CapabilitiesGate>` 阻塞直到 `capabilities` 解析完成。失败 → `/error` 加重试。后续刷新仅在后台。

**理由**: 六个 store 对应六个全局关注点。启动门防止路由守卫读取空能力状态的竞态条件。

### D11: 错误处理 — 三个层级

1. **单请求** — axios 拦截器映射 `error.code` → i18n key → Naive UI toast。
2. **路由级** — 保留路由 `/404`、`/forbidden`、`/error`，通过 Vue Router 错误处理。
3. **应用级故障** — 启动时 `GET /api/health` 失败 → 全屏阻断加重试。防止静默白屏。

**理由**: 旧前端在后端不可用时静默白屏。三个层级确保每种故障模式都有可见的、可操作的界面。

### D12: 不使用 Tailwind / UnoCSS

Naive UI 组件原语 + scoped `<style>` 块足以满足项目规模。

**理由**: 第二套样式系统会产生"这个 border-radius 归谁管？"的维护隐患，且收益不成比例。

### D13: i18n 一致性 — CI 强制保证 zh-CN / en / ja

三个扁平 JSON 语言文件。命名空间化 key（`dashboard.welcome`、`runs.daily.trigger`、`errors.config.qb_unreachable`）。一致性由 `scripts/check-i18n-parity.mjs` 强制——任何语言不对称的 key 集 CI 硬失败。

后端错误码（机器可读）映射到前端 `errors.*` 翻译。后端日志字符串保持英文。UI 界面使用用户语言。

**理由**: 三语言系统已存在但无一致性强制。不对称的语言文件会在某个 key 存在于一个语言但不存在于另一个时导致静默运行时失败。

### D14: 引导向导 — 独立路由，非模态框

五步向导在 `/onboarding`（欢迎 → JavDB 会话 → qBittorrent → 代理 → 首次运行）。基于路由，非模态框——URL 可分享、中途可恢复、小屏移动端可用。

Settings 中始终可重新进入。引导后对未配置的可选功能（PikPak、Rclone、SMTP、GH Actions）显示可关闭的提示卡片。

### D15: HTTP 客户端 — axios + 手写包装，非 openapi-fetch

`openapi-typescript` 仅生成类型定义（`api.gen.ts`）。`src/api/` 中的包装器使用共享 axios 实例加拦截器（auth 刷新队列、CSRF 注入、错误 → toast、request_id）。

**理由**: 拦截器栈的复杂度超过了 openapi-fetch 的类型生成收益。包装器后续可部分代码生成。

### D16: 数据获取 — 三个 composable，无 TanStack Query

- `useApi(url, opts)` — 单次 GET。
- `usePolling(fn, interval)` — 列表 + 增量轮询，`visibilityState === 'hidden'` 时暂停。
- `useLogStream(jobId, opts)` — 通过轮询 `/api/tasks/{id}/stream` 的日志流。

**理由**: 每个 composable 约 30 行。TanStack Query 增加 15+ KB gzip 用于此应用不需要的缓存语义（操作者控制台，非数据密集型仪表盘）。

### D17: 默认关闭乐观更新

面向操作者的 UI 偏好"写入 → 看到后端确认"而非"快速然后回滚"。仅对低风险 UI 偏好例外：侧边栏折叠、主题、关闭提示。

### D18: GitHub Actions 集成通过 httpx 直调（Phase 2）

GH Actions 端点（`list workflows`、`list runs`、`dispatch`、`stream logs`，加 Phase 3 的 `edit YAML` 和 `secrets CRUD`）使用 `httpx` 直接调用 GitHub REST API v3。不引入第三方库（PyGithub、ghapi）。

Token 复用 `config.py` 中已有的 `GIT_PASSWORD` PAT。除非操作者反馈权限范围冲突，否则不新增 `GH_ACTIONS_TOKEN` 配置项。

**理由**: 三个阶段总共仅需 6-7 个 API 调用。引入库的依赖成本与收益不成比例。PAT 已配置且通常自带 `workflow` scope。

### D19: 邮件通知历史表（Phase 2）

在 `operations.db` 中新建 `EmailNotificationHistory` 表（D1 迁移 `0018`），记录每次邮件发送尝试：收件人、主题、状态（`sent`/`failed`/`resent`）、错误信息、时间戳、SessionId。`javdb/integrations/notify/email.py` 中的邮件发送代码在每次 `smtp.send_message()` 后追加一行。

支持 `GET /api/ops/email/history`（带状态过滤的列表）和 `POST /api/ops/email/{id}/resend`（重发失败通知）。

**理由**: Spec Journey 12 要求"重发失败通知"。没有持久化历史，重发不可能实现，操作者也无法了解发送失败情况。

### D20: 历史搜索使用 SQL LIKE，非 FTS（Phase 2）

`GET /api/history/movies` 和 `GET /api/history/torrents` 使用 SQL `LIKE` / `INSTR` 对 VideoCode、ActorName、SupportingActors 进行文本搜索。基于 `Id` 的 keyset cursor 分页。

不使用全文搜索（FTS5）。SQLite 支持但 Cloudflare D1 不支持。为预期数据规模（约 1 万部影片、5 万条种子）维护两套搜索代码路径不合理。

**理由**: 在 1 万–5 万行规模的索引列上 `LIKE` 搜索 <50ms 完成。FTS 增加复杂度但无可衡量的用户收益。

### D21: 数据 CSV 导出通过后端流式返回（Phase 2）

`GET /api/history/movies/export` 和 `GET /api/history/torrents/export` 返回 `StreamingResponse`，content type 为 `text/csv`。后端从完整的过滤数据集生成 CSV 行（无分页限制）。前端通过 blob URL 触发浏览器下载。

**理由**: 操作者导出 CSV 用于外部分析，期望完整数据集而非仅当前页。服务端生成保证数据一致性，并能处理客户端无法组装的大数据集。

---

## 后果

### 正面

- 从第一天起拥有完整测试覆盖的干净代码库。
- 通过三个分阶段发布，UI 可访问所有 CLI 可表达的操作。
- 部署模式无关：单一前端镜像适用于所有自部署拓扑。
- auth 刷新流程已修复——不再有级联重试风暴。
- i18n 一致性在 CI 层面强制——任何语言都不能落后。

### 负面

- 双仓库维护：影响 API 契约的后端变更需要协调发布。
- 引导向导 + Browse 页面重度依赖后端——与 parse/explore 端点响应结构强耦合。
- 删除 `apps/web/` + `apps/desktop/` 是单向操作（git 历史可缓解）。

### 中性

- Electron 桌面壳被放弃。如需要可后续作为薄 Tauri/Electron 包装器重新添加。
- 代理协调器 UI 保持独立——本次重写是补充而非替代。

---

## 风险

1. **Browse Lists 模式依赖重度服务端解析。** 6 个 parse 端点目前缺乏严格的 Pydantic 响应模型。**缓解**: 预工作在前端代码落地前收紧响应模型；Phase 2 E2E 强制验证。
2. **D1 状态批量端点成本。** 快速翻页 Lists 用户可能触发大量 index-status 调用。**缓解**: 后端每 href 缓存约 10 秒；前端每会话内存缓存。
3. **GH Actions 日志流速率限制**（5000 次/小时）。**缓解**: 仅轮询当前查看的运行。
4. **OpenAPI 类型生成假设干净的 `openapi.json`。** **缓解**: 预工作中收紧后端响应模型。
5. **Sessions 回滚 API 包装了具有丰富语义的 CLI（15+ 标志）。** **缓解**: 将回滚核心逻辑重构为库函数，CLI 和 API handler 均可调用。
6. **`POST /api/test/reset` 风险点。** **缓解**: 通过 `TEST_MODE=1` 环境变量门控；否则路由返回 404；集成测试验证。

---

## 已解决问题

- **后端版本偏差**: 当 `capabilities.build.backend_version` < 最低版本时前端拒绝启动。启动门渲染"请升级"页面。
- **后端错误的 i18n**: 后端错误码映射到前端翻译。日志字符串保持英文。
- **回滚库分层反转（2026-05-20 更新）**: 原始的 `javdb/storage/rollback/core.py` -> `apps.cli.db._session_helpers` 导入已经移除。当前过渡 helper path 是 `javdb.storage.rollback.session_helpers`，而 `apps.cli.db._session_helpers` 仍是 shim。[ADR-014](ADR-014-storage-cli-layering.zh.md) 跟踪最终收敛到 `javdb.storage.sessions.lifecycle_helpers`，以及删除两个 legacy wrappers。
- **Commit 端点副作用一致性**: `javdb/storage/sessions/commit.py` 已有 `fanout_claims` 和 `emit_metrics` 标志；HTTP 端点默认为 `False`。修复方案：API 请求体默认为 `True` 以保持 CLI 一致性。跟踪于 [IMP-ADR008-02](../impl/IMP-ADR008-02-frontend-phase1-completion.md) Task 5。
- **PikPak 端点粒度**: 仅暴露批量模式（`POST /api/ops/pikpak/transfer { days, dry_run }`）。单种子转移推迟。
- **Rclone 端点粒度**: 单端点 + 标志（`POST /api/ops/rclone/run { scan, report, execute, dry_run }`）。前端提供"快速去重"和"高级"模式预设。

## 开放问题

- **多标签页行为**: 两个标签页会双倍请求量。建议 BroadcastChannel 共享轮询，推迟到 Phase 3。见 [IMP-ADR008-04](../impl/IMP-ADR008-04-frontend-phase3-power-user.md) Task 6。
- **D1 状态缓存 TTL**: 建议服务端约 10 秒 / 客户端会话级。需在 Phase 2 后的实际 Browse-Lists 使用中验证。
- **全局日志搜索存储**: 日志持久化策略（DB 表 vs 文件系统 vs 结构化行）未决定。取决于 Phase 2 日志量观察。推迟到 Phase 3 设计会议 → ADR-009。见 [IMP-ADR008-04](../impl/IMP-ADR008-04-frontend-phase3-power-user.md) Task 4。
- **统计仪表盘范围**: 已确定候选指标（运行成功率、历史增长、去重释放量），但范围和图表库未最终确定。推迟到 Phase 3 设计会议 → ADR-010。见 [IMP-ADR008-04](../impl/IMP-ADR008-04-frontend-phase3-power-user.md) Task 5。

---

## 附录 A — 设计令牌（方向 C）

| 令牌 | 亮色 | 暗色 |
|---|---|---|
| 背景 | `#faf9f7` | `#0e0d12` |
| 表面 | `#ffffff` | `#1a1820` |
| 主强调色 | `#7c3aed` | `#7c3aed` |
| 次强调色 | `#ec4899` | `#ec4899` |
| 边框 | `#e5dccf` | `#2a2730` |
| 圆角 | 12–16 px（输入框 8 px） | 同左 |
| 阴影 | `0 1px 2px rgba(15,23,42,0.04), 0 6px 18px rgba(15,23,42,0.06)` | 同左 |
| 状态绿 | `#10b981` | 同左 |
| 状态红 | `#dc2626` | 同左 |
| 状态蓝 | `#3b82f6` | 同左 |
| 状态琥珀 | `#f59e0b` | 同左 |
