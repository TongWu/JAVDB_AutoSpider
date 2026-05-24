# ADR-019: Web 后端功能对齐 — 配置、统计与密码管理

| 字段       | 值                                                                     |
| ---------- | --------------------------------------------------------------------- |
| **状态**   | Accepted                                                              |
| **日期**   | 2026-05-24                                                            |
| **作者**   | Ted                                                                   |
| **关联**   | [ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md), [ADR-018](../ADR-018-Web-Security-Hardening/ADR-018-web-security-hardening.md) |

## 背景

对 `javdb-autospider-web` TypeScript 后端与 Python FastAPI 后端及 `config.py.example` 的全面审计发现了三个领域的功能对齐差距：

1. **Config schema 差距** — TS 后端暴露 57 个配置字段；Python 后端有约 130 个。在 73 个缺失 key 中，约 26 个具有运行时意义，应可通过 Web UI 配置。其余为本地部署专用的路径/日志（与 Cloudflare Workers 无关）。
2. **Stats trend 不完整** — 两个趋势指标（`duration`、`proxy_bans`）在 TS 后端返回空数据。`duration` 可从 D1 计算；`proxy_bans` 没有 Cloudflare 可访问的数据源。
3. **Change Password 缺失** — Python 后端有 `POST /api/auth/change-password`；TS 后端完全没有。用户必须使用 `wrangler secret put` 来更改密码。

此外，三个配置 key 在两个后端之间存在命名不一致：
- `SMTP_HOST`（TS）vs `SMTP_SERVER`（Python 规范名）
- `START_PAGE` / `END_PAGE`（TS）vs `PAGE_START` / `PAGE_END`（Python 规范名）

## 决策

### Config Key 分级：三级分类

并非所有 73 个缺失 key 都需要加入。按其与 Cloudflare Workers 部署的相关性进行分级：

| 级别 | 标准 | 操作 | 数量 |
| ---- | ---- | ---- | ---- |
| **必须加** | 影响运行时行为；用户需要通过 UI 配置 | 加入 `config-schema.ts` | 26 |
| **Capabilities** | 部署时常量（D1 ID、协调器 URL） | 通过 `/api/capabilities` 展示（未来工作） | ~15 |
| **跳过** | 本地专用路径（`*_LOG_FILE`、`*_DB_PATH`、`*_DIR`、`*_CSV`） | 不适用于 Cloudflare Workers | ~33 |

### 必须加的 Key（共 26 个）

#### Spider 参数 (5)

| Key | 类型 | 分区 | 敏感 | 备注 |
| --- | ---- | ---- | ---- | ---- |
| `PAGE_START` | int | spider | 否 | 从 `START_PAGE` 重命名 |
| `PAGE_END` | int | spider | 否 | 从 `END_PAGE` 重命名 |
| `PHASE2_MIN_RATE` | float | spider | 否 | |
| `PHASE2_MIN_COMMENTS` | int | spider | 否 | |
| `BASE_URL` | string | spider | 否 | 默认：`https://javdb.com` |

#### qBittorrent Ad-Hoc 实例 (4)

| Key | 类型 | 分区 | 敏感 | 备注 |
| --- | ---- | ---- | ---- | ---- |
| `QB_URL_ADHOC` | string | qbittorrent | 否 | |
| `QB_USERNAME_ADHOC` | string | qbittorrent | 否 | |
| `QB_PASSWORD_ADHOC` | string | qbittorrent | 是 | |
| `QB_ALLOW_INSECURE_HTTP` | bool | qbittorrent | 否 | 只读 |

#### qBittorrent 扩展 (2)

| Key | 类型 | 分区 | 敏感 | 备注 |
| --- | ---- | ---- | ---- | ---- |
| `REQUEST_TIMEOUT` | int | qbittorrent | 否 | 秒 |
| `DELAY_BETWEEN_ADDITIONS` | int | qbittorrent | 否 | 秒 |

#### SMTP 补齐 (3)

| Key | 类型 | 分区 | 敏感 | 备注 |
| --- | ---- | ---- | ---- | ---- |
| `SMTP_SERVER` | string | smtp | 否 | 从 `SMTP_HOST` 重命名 |
| `EMAIL_FROM` | string | smtp | 否 | |
| `EMAIL_TO` | string | smtp | 否 | |

#### Proxy 扩展 (2)

| Key | 类型 | 分区 | 敏感 | 备注 |
| --- | ---- | ---- | ---- | ---- |
| `PROXY_POOL_MAX_FAILURES` | int | proxy | 否 | 默认：3 |
| `LOGIN_PROXY_NAME` | string | proxy | 否 | |

#### 登录高级 (5)

| Key | 类型 | 分区 | 敏感 | 备注 |
| --- | ---- | ---- | ---- | ---- |
| `GPT_API_URL` | string | javdb | 否 | 验证码求解端点 |
| `GPT_API_KEY` | string | javdb | 是 | |
| `LOGIN_ATTEMPTS_PER_PROXY_LIMIT` | int | javdb | 否 | 默认：6 |
| `LOGIN_MAX_FAILURES_BEFORE_PROXY_SWITCH` | int | javdb | 否 | 默认：3 |
| `LOGIN_VERIFICATION_URLS` | json | javdb | 否 | JSON 数组 |

#### GitHub Actions 配置 (3)

| Key | 类型 | 分区 | 敏感 | 只读 | 备注 |
| --- | ---- | ---- | ---- | ---- | ---- |
| `GH_ACTIONS_TIER` | string | ghActions | 否 | 是 | 部署时确定 |
| `GH_ACTIONS_REPO` | string | ghActions | 否 | 是 | 部署时确定 |
| `GH_ACTIONS_TOKEN` | string | ghActions | 是 | 否 | |

#### API 认证 (2)

| Key | 类型 | 分区 | 敏感 | 备注 |
| --- | ---- | ---- | ---- | ---- |
| `READONLY_USERNAME` | string | apiConsole | 否 | |
| `READONLY_PASSWORD_HASH` | string | apiConsole | 是 | |

### Config Key 重命名 — Alias Fallback

TS 对齐 Python 规范名称。不做一次性 D1 迁移脚本，而是在配置加载路径中使用 alias fallback：

**每个 key 的加载优先级：**
1. D1 config 表中的规范名（如 `SMTP_SERVER`）
2. D1 config 表中的别名（如 `SMTP_HOST`）
3. 环境变量
4. Schema 默认值

**保存时：** 始终写入规范名。旧别名 key 保留在 D1 中，直到被自然覆盖或手动清理。

**别名映射：**

| 规范名（新） | 别名（旧） |
| ------------ | ---------- |
| `SMTP_SERVER` | `SMTP_HOST` |
| `PAGE_START` | `START_PAGE` |
| `PAGE_END` | `END_PAGE` |

### Stats Trend: Duration 和 Proxy Bans

#### `duration` — 从 `job_runs` 表实现

查询 `OPERATIONS_DB.job_runs` 中已完成的 job：

```sql
SELECT DATE(created_at) AS date,
       AVG((julianday(updated_at) - julianday(created_at)) * 86400) AS value
FROM job_runs
WHERE status = 'completed'
  AND created_at >= datetime('now', '-{days} days')
GROUP BY DATE(created_at)
ORDER BY date
```

返回每日平均 job 时长（秒）。

#### `proxy_bans` — Cloudflare 模式下不可用

Python 后端通过 grep 扫描本地日志文件计算代理封禁数。Cloudflare Workers 没有文件系统访问权限，D1 中也没有存储封禁事件的表。

**响应格式变更：**

```json
{
  "metric": "proxy_bans",
  "period": "7d",
  "available": false,
  "reason": "proxy_bans 需要本地日志访问权限（Cloudflare 模式下不可用）",
  "data": []
}
```

`available: false` 标志通知前端渲染 "N/A" 而非空图表。所有其他指标继续返回 `"available": true`。

### Change Password 端点

**新端点：** `POST /api/auth/change-password`

**请求：**

```json
{
  "old_password": "当前密码",
  "new_password": "新密码至少8个字符"
}
```

**行为：**
1. 验证 `old_password`（D1 config → env fallback）。
2. 验证 `new_password`（最少 8 个字符）。
3. 使用 bcrypt（cost factor 10）生成 hash。
4. 通过 `saveConfigKeys()` 将 `ADMIN_PASSWORD_HASH`（或只读用户的 `READONLY_PASSWORD_HASH`）写入 D1 config 表。
5. 返回 `{ status: "ok" }`。

**认证要求：** 已认证用户只能更改自己的密码。Admin 不能通过此端点更改只读用户的密码（使用 config PUT）。

### `findUser()` 改为 Async + D1 优先

`server/services/users.ts` 中的 `findUser()` 变为 async 并接受 D1 数据库参数：

```typescript
export async function findUser(env: Env, db: D1Database): Promise<User | undefined>
```

**每个用户的密码 hash 解析顺序：**
1. 查询 D1 `api_config` 表中的 `ADMIN_PASSWORD_HASH`（或 `READONLY_PASSWORD_HASH`）
2. 找到 → 使用 D1 值
3. 未找到 → 回退到 `env.ADMIN_PASSWORD_HASH`

**需要 `await` 的调用点：**
- `server/routes/auth.ts`：login handler、refresh handler

## 不在范围内

- **33 个本地部署专用 key**（`*_LOG_FILE`、`*_DB_PATH`、`*_DIR`、`*_CSV`）— 与 Cloudflare Workers 无关。
- **15 个 Cloudflare/协调 key** — 未来 capabilities 端点增强。
- **前端页面修改** — Config UI 从 `/config/meta` 自动渲染；无需手动页面工作。
- **Python 后端修改** — 本 ADR 仅针对 TS 后端。
- **Admin 通过此端点修改只读用户密码** — 使用 `PUT /api/config` 配合 `READONLY_PASSWORD_HASH`。

## 影响

### 正面

- Web UI 可配置 83 个字段（57 个现有 + 26 个新增），覆盖所有运行时有意义的参数。
- Config key 名称与 Python 规范定义一致 — 消除跨后端混淆。
- 用户可通过 UI 更改密码，无需 CLI 访问。
- Stats trend 仪表板显示 job 时长数据；proxy_bans 明确标记为不可用而非静默为空。
- Alias fallback 零停机 — 现有 D1 config 值在过渡期间继续工作。

### 负面

- `findUser()` 变为 async — 每次 login/refresh 增加 D1 读取。可接受的延迟（每次 D1 查询约 10ms）。
- Trend 响应新增 `available` 字段 — 前端必须处理此新字段（优雅降级：当前前端忽略未识别的字段）。
- Alias 映射是一种技术债务 — 旧 key 保留在 D1 中直到被覆盖。3 个 key 可接受。

### 风险

- **D1 config 优先于 env** 的密码 hash 意味着一旦通过 UI 执行了 change-password，`wrangler secret put ADMIN_PASSWORD_HASH` 将不再覆盖。需要文档说明此行为。
- **Cloudflare Workers 中的 bcrypt** — `bcryptjs`（纯 JS）已用于验证。Cost 10 的 hash 在 Workers 中约需 100ms — 对于很少调用的密码更改端点可以接受。
