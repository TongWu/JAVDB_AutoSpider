# Proxy Coordinator —— 从零开始的部署手册

> 面向运维的 step-by-step 指南。把 Cloudflare Worker + Durable Object
> 部署到 Cloudflare 免费层，并把 GitHub Actions 的 5 个 workflow 接入
> 跨实例的 per-proxy 节流协调。**全程零成本，可随时无代价回滚。**

> **Worker 源码仓库**：
> [TongWu/JAVDB_AutoSpider_Proxycoordinator](https://github.com/TongWu/JAVDB_AutoSpider_Proxycoordinator)
>
> Worker / Durable Object 的 TypeScript 源码、`wrangler.toml`、单测、
> 配额脚本都已经从本 monorepo 拆到上述独立 repo 中（拆分原因：部署生命
> 周期、依赖工具链 (Node + wrangler) 与 Python spider 完全独立）。
> 本文中所有 `cd JAVDB_AutoSpider_Proxycoordinator` 步骤都假设你已经
> `git clone` 了那个仓库到本地任意目录。Python 客户端
> ([`packages/python/javdb_platform/proxy_coordinator_client.py`](../packages/python/javdb_platform/proxy_coordinator_client.py))
> 仍然在本仓库内，这两边通过 HTTP + token 解耦。

---

## 0. 这是什么 / 为什么需要

当前每个 GH Actions runner 进程内部，每个 worker 都有自己的拟人化
sleep + 三窗口节流（`packages/python/javdb_spider/runtime/sleep.py`）。
但这是**进程本地的**：当两个 GH Actions 运行同时跑（共用同一份
`PROXY_POOL_JSON`），它们会互不知情地通过同一个物理代理同时发请求，
打破了拟人化间隔。

本方案为每个 `proxy_id` 创建一个 Cloudflare Durable Object 实例。DO
按 id 串行化执行，天然适合做 per-proxy 互斥锁 + 共享 throttle 状态。
每次 spider 发请求前先 `POST /lease`，DO 返回必须等待的 `wait_ms`，
spider 等够后才发请求。任意 runner 命中 CF Turnstile 时通过 `/report`
通知 DO，所有其它 runner 在下次 lease 时都会拿到提升的 `penalty_factor`。

**Fail-open 设计**：Worker 不可达 / token 错配 / 网络故障时，Python 端
自动退回到原来的本地节流路径，不影响业务。

---

## 1. 前置准备（一次性）

### 1.1 Cloudflare 账号

- 访问 <https://dash.cloudflare.com/sign-up> 注册
- Free Plan 即可，**无需绑定信用卡**
- 记下右下角的 **Account ID**（`Workers & Pages` 页面也能看到）

### 1.2 本地工具

```bash
# Node.js >= 20（macOS 上 brew 安装最简单）
brew install node

# 验证
node --version   # 应 >= 20
npm --version
```

### 1.3 克隆 Worker 仓库 + 一次性 OAuth 登录

```bash
# 任选一个本地目录（与本 spider repo 平级最方便）
git clone https://github.com/TongWu/JAVDB_AutoSpider_Proxycoordinator.git
cd JAVDB_AutoSpider_Proxycoordinator
npm install              # 安装 wrangler 等依赖
npx wrangler login       # 弹出浏览器，点 Allow
```

成功标志：终端显示 `Successfully logged in.`

---

## 2. 项目目录速览

```
JAVDB_AutoSpider_Proxycoordinator/   # 独立 GitHub repo（已 git clone）
├── wrangler.toml                    # Worker + DO 绑定 + 可调常量
├── package.json
├── tsconfig.json
├── vitest.config.ts
├── src/
│   ├── index.ts                     # Worker 入口（路由、认证）
│   ├── proxy_coordinator.ts         # ProxyCoordinator DO 实现
│   └── types.ts                     # Env / 请求类型
├── test/
│   └── proxy_coordinator.test.ts    # vitest-pool-workers 单测（15 个）
└── scripts/
    └── check-quota.sh               # 日 lease 数告警脚本
```

`wrangler.toml` 里 `[[migrations]] new_sqlite_classes = ["ProxyCoordinator"]`
表示**只用 SQLite-backed DO**，因为 Free Plan 只支持 SQLite-backed。

---

## 3. 本地开发与单测

```bash
cd JAVDB_AutoSpider_Proxycoordinator
npm install
npx wrangler dev                       # http://localhost:8787 开发服务器
```

另开一个终端跑烟囱测试：

```bash
TOKEN=devtoken
curl -s -X POST http://localhost:8787/lease \
  -H "Authorization: Bearer $TOKEN" \
  -H "content-type: application/json" \
  -d '{"proxy_id": "test", "intended_sleep_ms": 1000}'
# 期望: {"wait_ms": 1XXX, "penalty_factor": 1.0, ...}
```

> **注意**：本地开发模式 token 可以是任何字符串，但生产部署一定要用
> `wrangler secret put` 设置真正的强随机 token。

跑全部单测：

```bash
npx vitest run     # 应输出 "15 passed"
npx tsc --noEmit   # 类型检查
```

---

## 4. 生成与配置 token

```bash
# 生成 64 字符强随机 hex
TOKEN=$(openssl rand -hex 32)
echo "Save this token: $TOKEN"      # 一定要保存！会同时设到 GH Secrets

# 部署到 Cloudflare 作为 Worker secret
echo -n "$TOKEN" | npx wrangler secret put PROXY_COORDINATOR_TOKEN
# 提示 "✨ Success!" 即成功
```

**Token 用途**：Worker 的 `/lease` `/report` `/state` 端点都要求
`Authorization: Bearer <TOKEN>` header；Python 端会自动加。`/health`
不需要 token，方便监控探活。

---

## 5. 首次部署

```bash
cd JAVDB_AutoSpider_Proxycoordinator
npx wrangler deploy
```

输出形如：

```
Total Upload: 6.71 KiB / gzip: 2.34 KiB
Uploaded proxy-coordinator (3.21 sec)
Published proxy-coordinator (1.42 sec)
  https://proxy-coordinator.<your-subdomain>.workers.dev
```

**记下这个 URL**，下一步要写入 GitHub。

验证存活：

```bash
curl https://proxy-coordinator.<your-subdomain>.workers.dev/health
# 应返回: ok
```

带 token 的实际验证：

```bash
TOKEN=<上一步的 token>
curl -X POST https://proxy-coordinator.wuengineer.workers.dev/lease \
  -H "Authorization: Bearer $TOKEN" \
  -H "content-type: application/json" \
  -d '{"proxy_id": "smoke-test", "intended_sleep_ms": 500}'
# 应返回 JSON: {"wait_ms": ~500, "penalty_factor": 1.0, ...}
```

---

## 6. GitHub Actions 接入

### 6.1 添加 GitHub Secret 与 Variable

打开你的仓库 → **Settings** → **Secrets and variables** → **Actions**：

- **Secrets** 标签页 → **New repository secret**：
  - Name: `PROXY_COORDINATOR_TOKEN`
  - Value: 第 4 步生成的 token

- **Variables** 标签页 → **New repository variable**：
  - Name: `PROXY_COORDINATOR_URL`
  - Value: 第 5 步部署的 URL（不要带尾斜杠）

> 因为这些 workflow 都用了 `environment: Production`，请在
> Production environment 里添加，而不是 Repository 级别（如果两者
> 都有，environment 优先）。

### 6.2 触发一次手动 AdHocIngestion 验证

在 GitHub UI：**Actions** → 选 `JavDB Ad Hoc Ingestion` → **Run workflow**

预期日志变化（在 `Step 1 - Run Spider` 步骤里）：

```
INFO:SpiderState: Proxy coordinator client initialised: base_url=https://proxy-coordinator.acme.workers.dev
DEBUG:SleepMgr: Coordinator lease: wait=8.42s (local=7.30s, reason=ok, remote_penalty=1.00, proxy=JP-1)
```

如果反而看到：

```
INFO:SpiderState: Proxy coordinator not configured (PROXY_COORDINATOR_URL/TOKEN unset) — using local throttling only
```

那就是 GH 变量没注入到 worker — 检查第 6.1 步的环境层级是否正确。

---

## 7. 监控与可观测性

### 7.1 Cloudflare Dashboard

**Workers & Pages** → `proxy-coordinator` → **Metrics** 标签：
- 请求总数（CPU 时间、错误率）
- 实时调用日志（点 **Logs** 标签开启 Tail）

### 7.2 Workers Analytics Engine 查询

每次 `/lease` / `/report` 都写一行到 `proxy_coordinator_leases` 数据集。
通过 SQL API 查询：

```bash
ACCOUNT_ID=<your account id>
TOKEN=<API token with "Account Analytics: Read" perm>

curl -X POST "https://api.cloudflare.com/client/v4/accounts/${ACCOUNT_ID}/analytics_engine/sql" \
  -H "Authorization: Bearer ${TOKEN}" \
  --data-binary @- <<'SQL'
SELECT toDate(timestamp) AS day, COUNT(*) AS leases
FROM proxy_coordinator_leases
WHERE blob1 = 'lease'
GROUP BY day
ORDER BY day DESC
LIMIT 7
FORMAT JSON
SQL
```

或用脚本：

```bash
export CLOUDFLARE_ACCOUNT_ID=...
export CLOUDFLARE_API_TOKEN=...
bash JAVDB_AutoSpider_Proxycoordinator/scripts/check-quota.sh
# 输出: Last-24h lease count: 4823 (threshold: 70000)
```

### 7.3 70 k req/天 告警阈值（运维约定）

Free Plan 的硬性上限是 100,000 Worker requests/天 + 100,000 DO
requests/天。**约定**：当 24 小时滚动 lease 数超过 **70,000**（70%）
时立即处理（提频降页范围 / 升级到 Paid Plan $5/月）。

可选：把 `scripts/check-quota.sh` 设成日 cron（GitHub Actions 也行）：

```yaml
# .github/workflows/CoordinatorQuotaCheck.yml （可选 — 未默认提交）
on:
  schedule:
    - cron: '0 */6 * * *'   # 每 6 小时一次
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout coordinator repo
        uses: actions/checkout@v6
        with:
          repository: TongWu/JAVDB_AutoSpider_Proxycoordinator
      - run: bash scripts/check-quota.sh
        env:
          CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
          CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
```

---

## 8. 回滚步骤

### 8.1 软关闭（保留 Worker，但 Python 走本地节流）

GitHub → Settings → Variables → 删除 `PROXY_COORDINATOR_URL`（或清空它）

下一次 spider 运行启动时会输出：

```
Proxy coordinator not configured (PROXY_COORDINATOR_URL/TOKEN unset) — using local throttling only
```

行为退回到 PR 之前。**零代码改动**。

### 8.2 完全拆除

```bash
cd JAVDB_AutoSpider_Proxycoordinator
npx wrangler delete
```

DO 实例和 SQLite 状态会一并删除。可以随时重新 `wrangler deploy` 重建。

---

## 9. proxy_id 一致性（CRITICAL）

DO 通过 `idFromName(proxy_id)` 寻址，**所有 runner 必须用同一字符串**，
否则同一物理代理会被路由到不同 DO 实例，互斥彻底失效（且**无报错**）。

Python 客户端的归一化规则（见
[`packages/python/javdb_platform/proxy_coordinator_client.py`](../packages/python/javdb_platform/proxy_coordinator_client.py)
的 `_normalize_proxy_id()`）：

1. 优先用 `PROXY_POOL_JSON` 里的 `name` 字段（去掉首尾空格，截到 256 字符）
2. 没有 `name` 时 fallback 到 `proxy-<sha1(host:port)[:16]>`

**强烈建议运维所有代理都显式设置 `name`，并保证多个 runner 看到的
`PROXY_POOL_JSON` 字符串完全一致**。

```jsonc
// PROXY_POOL_JSON Secret（推荐格式）
[
  {"name": "JP-1", "http": "http://user:pass@1.2.3.4:8080"},
  {"name": "JP-2", "http": "http://user:pass@5.6.7.8:8080"}
]
```

如果某代理缺少 `name`，`PROXY_POOL_JSON` 没有提供该项，Python 端会从
`host:port` 派生一个稳定的 `proxy_id` 并打一条 **WARNING** 日志（不会
抛错，pipeline 不中断）：

```
Coordinator proxy_id derived from host:port hash: proxy-<16hex> — recommend setting `name` in PROXY_POOL_JSON so all runners agree
```

---

## 10. 免费额度核算

| 资源 | Free Plan 上限 | 中位场景占用 | 上沿场景占用 |
|---|---|---|---|
| Worker requests | 100,000/天 | 5,000 (5%) | 20,000 (20%) |
| DO requests | 100,000/天 | 5,000 (5%) | 20,000 (20%) |
| DO Duration | 13,000 GB-s/天 | ~5 GB-s (0.04%) | ~30 GB-s (0.23%) |
| DO SQLite rows R/W | 5 M / 100 K /天 | 5,000 (5%) | 20,000 (20%) |
| DO Storage | 5 GB | <1 MB | <1 MB |

数据来源：<https://developers.cloudflare.com/durable-objects/platform/pricing/>

中位 = `DailyIngestion` 1 次/天，约 5,000 次 JavDB HTTP；上沿 = 触发了较多
CF 重试链。**实例数（M 个 GH Actions 同时跑）不影响总请求数**，因为
DO 的 `next_available_at` + 三窗口把每代理总吞吐限死在拟人化节流的
上限内。所以增加并发实例只是把同一份吞吐切分给更多 runner 共享。

---

## 11. 故障排查 FAQ

### Q1. `401 Unauthorized`
- GH Secret 与 Worker Secret 不一致 → 重新跑第 4 步同步两端

### Q2. `400 missing proxy_id`
- Python 客户端没传 `proxy_id`，通常是 spider 端 wiring bug
- 检查 `state.global_proxy_coordinator` 是否成功初始化（看 INFO 日志）

### Q3. `429 Too Many Requests` 或日累计 100 k 触顶
- 升级到 Workers Paid Plan（$5/月，提供 10M req/月）
- 或临时降低 `PAGE_END` GH Variable 减少单次 ingestion 量级

### Q4. `wait_ms` 异常长（>30 s）
1. 检查 DO 状态 dump：
   ```bash
   curl -H "Authorization: Bearer $TOKEN" \
     "https://proxy-coordinator.../state?proxy_id=JP-1"
   ```
2. 看 `requestTimestamps` 长度是否接近 200（30 分钟窗口上限）—
   说明这个代理被多个 runner 同时高频使用，是预期行为
3. 如果 `cfEvents` 不为 0，说明 CF 事件提升了 penalty

### Q5. 多个 runner 跑着跑着，只有一个的吞吐高
- 99% 是 `proxy_id` 不一致（见 §9）
- 看 Analytics Engine 数据：
  ```sql
  SELECT blob1 AS op, blob0 AS proxy_id, COUNT(*) AS n
  FROM proxy_coordinator_leases
  WHERE timestamp > NOW() - INTERVAL '1' HOUR
  GROUP BY blob0, blob1 ORDER BY n DESC
  ```
  如果同一物理代理出现两个不同的 `proxy_id`，就是 name 字段不一致

### Q6. 部署后 spider 完全不调用 coordinator
- `Proxy coordinator not configured` 日志：GH Var/Secret 没注入。检查：
  - 名字大小写是否完全是 `PROXY_COORDINATOR_URL` / `PROXY_COORDINATOR_TOKEN`
  - 是否设到了 `Production` environment（workflow 里 `environment: Production`）
  - 在 workflow 的 `Generate config.py from GitHub Variables and Secrets` step 里是否能看到对应的 `VAR_PROXY_COORDINATOR_URL` env

### Q7. DO 数量过多
- 上限是 50 万 DO instances/账号，按代理计应远低于此
- 单个 DO storage 上限是 10 GB（远超我们的几百字节状态）

---

## 12. 升级 / 进阶

- **多区域**：Cloudflare DO 自动选最近的 PoP，无需配置
- **自定义域名**：在 Cloudflare DNS 加 CNAME → Worker route，省掉
  `<subdomain>.workers.dev` 长尾巴
- **更细的 throttle 调优**：编辑 `wrangler.toml` 的 `[vars]` 节，重新
  `wrangler deploy`，无需改 Python 端
- **新增端点**：在 `src/index.ts` 加路由，`src/proxy_coordinator.ts`
  加 DO 方法

---

## 13. Cross-runtime login state DO（GlobalLoginState）

第 0–12 节描述的 `ProxyCoordinator`（per-proxy 节流 DO）解决了**请求节奏**
跨 runner 的协调问题。但 JavDB 还有第二个跨 runner 隐患——**at-most-one
登录会话**：在代理 A 上登录会让代理 B 此前持有的 cookie 失效。如果 N 个
GH Actions 同时跑，每个 runner 都各自 attempt_login_refresh，会出现：

1. 重复登录浪费 GPT 验证码 / 账号 lockout 风险升高；
2. 后登录的 runner 抢占 cookie → 其它 runner 全部 cookie 失效 → 重新登录
   → 死循环。

`GlobalLoginState` 是部署在**同一个 Worker** 内的**第二个 DO 类**（singleton
`idFromName("global")`），存储 `(logged_in_proxy_name, encrypted_cookie,
version, last_verified_at)` 加一个 `lease` 互斥锁。同一个
`PROXY_COORDINATOR_TOKEN` 同时承担：
- 5 个 `/login_state*` 端点的 Bearer 鉴权；
- AES-GCM 256 加密 cookie 的密钥推导（HKDF-SHA256）。

### 13.1 工作流程（Python 端）

启动时 `_inherit_login_state` 先 `GET /login_state` 拿一份现成的 cookie；
没有 cookie 时按需走 `acquire_lease` → 实际登录 → `publish` →
`release_lease`。被另一 runner 抢锁的 worker 不会阻塞——LoginCoordinator
把 `LoginRequired` 的 task 投到本地 `_pending_login_tasks` deque，由
`_poll_login_state_loop` daemon 每 3s 轮询 DO；一旦观察到 `version`
增长就把新 cookie 注入对应 worker 并重新分发 parked task。**期间其它
代理的 worker 完全不受影响**，正常拉非登录页面。

### 13.2 端点速查

> 完整 schema 与示例 curl 见 Worker repo README 的 "GlobalLoginState
> endpoints" 一节。

| 端点 | 用途 |
|---|---|
| `GET /login_state` | 读当前 (proxy_name, 解密 cookie, version) |
| `POST /login_state/acquire_lease` | 获取 5–300s 的再登录互斥锁 |
| `POST /login_state/publish` | 持锁者发布新 cookie（version+1） |
| `POST /login_state/invalidate` | 乐观锁标记 cookie 失效 |
| `POST /login_state/release_lease` | 持锁者释放互斥锁 |

### 13.3 部署（与 §5 同步）

`wrangler deploy` 时新增的 `[[migrations]] tag = "v2"` 会自动创建
`GlobalLoginState` 类。无需新增 secret——加密 key 与 Bearer 鉴权共享
`PROXY_COORDINATOR_TOKEN`，**轮换 token 即同时强制下次登录**（旧 cookie
解密失败 → DO 端返回 `cookie:null` → 下个 runner 走 `acquire_lease` 重登）。

### 13.4 Python 端配置

无需新增任何环境变量。`packages/python/javdb_platform/login_state_client.py`
复用 `PROXY_COORDINATOR_URL` / `PROXY_COORDINATOR_TOKEN`；`setup_proxy_pool`
顺带 `setup_login_state_client`，未配置 / `/health` 失败时静默 fail-open
退化为「per-runner 各自登录」的旧行为。每个 runner 启动时生成一次
`runtime_holder_id = f"runner-<uuid>"`（`state.runtime_holder_id`）作为
lease 持有者标识，整个进程生命周期内保持不变。

### 13.5 故障排查

| 症状 | 排查 |
|---|---|
| 启动日志只见 `Proxy coordinator client initialised` 而无 `Login-state client initialised` | Worker 部署版本太旧，没有 `/login_state*` 路由——重新跑 §5 部署最新 Worker |
| 多个 runner 仍各自登录 | 检查 Worker 端 `wrangler tail` 是否看到 `/login_state/acquire_lease` 请求；常见原因：旧 client 没升级 / token 仅在部分 runner 注入 |
| `409 lease_required` warning | 旧的顺序 fallback 路径调 `attempt_login_refresh` 时没先 acquire——预期内的 fail-open，本 runner 仍能用 cookie，只是没有跨 runner 共享 |
| `invalidate no-op (current_version > our N)` | 版本竞态——其它 runner 先 publish 了新 cookie；poller 在下一 tick 拉取并自动同步，无需人工干预 |
| Cookie 频繁被刷掉 | 用户在浏览器里登录了同一账号——这是 JavDB 单会话约束；提高 `LOGIN_VERIFICATION_URLS` 命中率减少误检 |

### 13.6 回滚

软关：与 §8.1 一样，删除 `PROXY_COORDINATOR_URL` 即同时关掉 throttle 与
login-state 协调。

硬删：`wrangler delete --class-name GlobalLoginState`（仅删 v2 引入的
`GlobalLoginState`，不影响 v1 的 `ProxyCoordinator`）。

---

## 14. P1-A：跨 run proxy ban + CF bypass 共享（搭车 ProxyCoordinator）

第 0–13 节解决了**节流**与**登录**的跨 runner 协调。**P1-A** 进一步把
两类原本只在单 runner 内存里的状态搭车进 `ProxyCoordinator` DO：

| 状态 | 之前 | P1-A 之后 |
|---|---|---|
| `proxy_ban_manager` 黑名单 | session-scoped、进程死即清零 | 跨 run 持久化，默认 **3 天 TTL**（259_200_000 ms），到期自动解封 |
| `proxies_requiring_cf_bypass` | per-runner 字典 | 每个 runner 在下一次 `/lease` 时被动同步，避免重复探测 CF |

**零额外 DO 调用**——这两类信号都搭车在已有的 `/lease`（读路径）和
`/report`（写路径）上。

### 14.1 协议改动（向后兼容）

- `ReportRequest.kind` union 扩展为
  `"cf" | "failure" | "ban" | "unban" | "cf_bypass" | "success"`，body 加
  可选 `ttl_ms?: number` 与 `reason?: string`。
- `LeaseResponse` 加可选字段 `banned: boolean`（默认 `false`）、
  `banned_until: number | null`、`requires_cf_bypass: boolean`、
  `cf_bypass_until: number | null`。
- 老 Worker 不发新字段时 Python 客户端 dataclass 全部默认为
  「无信号」，行为与今天完全一致。

### 14.2 默认 TTL（`wrangler.toml [vars]`）

| 变量 | 默认 | 含义 |
|---|---|---|
| `BAN_TTL_MS` | `259200000` | 单次 `mark_proxy_banned` 默认 3 天 |
| `CF_BYPASS_TTL_MS` | 由调用方按 `ttl_ms` 指定；`0` = 永久 | 与 `state.always_bypass_time` 语义一致 |

### 14.3 运维 cheat sheet

```bash
# 手动解封某代理（例如人工确认健康度恢复后提前重启探测）：
curl -H "Authorization: Bearer $PROXY_COORDINATOR_TOKEN" \
     -H "content-type: application/json" \
     -d '{"proxy_id":"JP-1","kind":"unban","reason":"manual"}' \
     "$PROXY_COORDINATOR_URL/report"

# 临时把代理标记为永久 CF bypass（直至 wrangler delete）：
curl -H "Authorization: Bearer $PROXY_COORDINATOR_TOKEN" \
     -H "content-type: application/json" \
     -d '{"proxy_id":"JP-1","kind":"cf_bypass","ttl_ms":0,"reason":"sticky"}' \
     "$PROXY_COORDINATOR_URL/report"

# 读取某代理的实时 ban / bypass 状态（需要 GET /state，仅调试用）：
curl -H "Authorization: Bearer $PROXY_COORDINATOR_TOKEN" \
     "$PROXY_COORDINATOR_URL/state?proxy_id=JP-1"
```

### 14.4 回滚

零代码改动：删除 `PROXY_COORDINATOR_URL` 即同时关掉 ban / bypass 共享，
spider 退回到内存黑名单 + per-runner CF 字典（即「PR 之前」的行为）。

---

## 15. P1-B / P2-A：MovieClaim DO（跨 runner detail 互斥 + 失败冷却）

### 15.1 解决的问题

两个并发 ingestion（例：DailyIngestion + 手动 AdHoc）对同一 actor 拉
同一 movie 的 `/v/<id>` 详情页时，进程内的 `_completed_entries`
**不跨 runner 协调**——双方都会发 HTTP、各自 6–20 s sleep、双倍 parser
开销。**P1-B** 引入新 DO 类 `MovieClaimState`，**按天分片**
（`idFromName("YYYY-MM-DD-Asia/Singapore")`），用单 key snapshot +
`cached` 内存层 + DO Alarm 每 10 min GC 实现。

**P2-A** 在同一 schema 上加 `fail_count` / `next_attempt_at` /
`last_error_kind`：detail-fetch 失败时通过 `/report_failure` 进入冷却
ladder（指数退避到 3 day cap），多次失败后变 dead-letter，其它 runner
在 `claim_movie` 时被直接拒绝。

### 15.2 协议端点

| 端点 | 用途 |
|---|---|
| `POST /claim_movie` | body `{ href, holder_id, ttl_ms }` → `{ acquired, current_holder_id, expires_at, already_completed, cooldown_until?, last_error_kind?, fail_count? }` |
| `POST /release_movie` | body `{ href, holder_id }` 持有者释放 |
| `POST /complete_movie` | body `{ href, holder_id }` 标记完成；同时清除 P2-A 失败记录 |
| `POST /report_failure` | body `{ href, holder_id, error_kind }` 进入冷却 / dead-letter |
| `GET /movie_status?href=X&date=YYYY-MM-DD` | 调试 dump |

### 15.3 部署（与 §5 同步）

`wrangler deploy` 时新的 `[[migrations]] tag = "v3" new_sqlite_classes
= ["MovieClaimState", "RunnerRegistry"]` 会一次性创建 P1-B 与 P2-E 两
个新类（共享 v3 tag）。`wrangler.toml` 已添加 `MOVIE_CLAIM_DO`
binding 与 `[vars]` 段的 `MOVIE_CLAIM_TTL_MS`（默认 `1800000`，30 分）。

### 15.4 默认 OFF：`MOVIE_CLAIM_ENABLED`

单 runner 场景启用 P1-B 完全没有收益却仍要付 +2 DO 调用/页的成本。
故 GitHub → Variables → `MOVIE_CLAIM_ENABLED`，**默认 `false`**，
确认有 ≥2 runner 并发时再设为 `true`：

```
MOVIE_CLAIM_ENABLED = true
```

Python 端 `setup_movie_claim_client` 在该开关为假时直接返回 `None`，
`detail/runner.py` 的 claim/complete/release 调用全部退化为 no-op，
行为与「未配置 DO」完全一致。

### 15.5 跨日 ingestion 注意

shard date 由**任务发起时刻**派生（不是 claim 调用时刻），保证跨日
ingestion 不会因为越过午夜把同一 movie 路由到两个 shard 而失去互斥。

### 15.6 故障排查

| 症状 | 排查 |
|---|---|
| `Movie claim client not initialised` 但 `MOVIE_CLAIM_ENABLED=true` | Worker 部署版本太旧或 `/health` 失败；查看 `wrangler tail` 看是否有 `/movie_status` 404 |
| 同一 movie 跨日被两个 runner 同时抓 | 客户端时区配置漂移；检查 `Asia/Singapore` 与 `path_helper.ensure_dated_dir` 是否一致 |
| 某 movie 「永远」claim 不到 | 该 href 进入 P2-A dead-letter（`fail_count >= 5`）；用 `complete_movie` 强制清除或等 24 h 自动 GC |
| Alarm GC 没触发 | DO 必须在第一次写入后才会注册 alarm；冷启动后第一次 claim 才开始 10 min 周期 |

### 15.7 回滚

- 软关：删除 `MOVIE_CLAIM_ENABLED` 即可（其它 DO 不受影响）。
- 完全删类：`npx wrangler delete --class-name MovieClaimState`（不影响
  v1/v2/v3 的其它类）。

---

## 16. P2-E：RunnerRegistry（运维可观测 + 配置漂移检测）

### 16.1 解决的问题

多 runner 并发时无法回答：
- 「现在有几个 runner 在跑？」
- 「另一个 runner 的 `workflow_run_id` 是什么？」
- 「他们的 `PROXY_POOL_JSON` 跟我一致吗？」（**配置漂移**——原计划 P3-B
  的全部目的）

**P2-E** 新增 singleton DO `RunnerRegistry`（`idFromName("runners")`），
在 `setup_proxy_pool` 末尾 `register`，daemon 每 60 s `heartbeat`，
`atexit` `unregister`。DO 内 Alarm 每 5 min 清 `last_heartbeat <
now - 10 min` 的 stale runner。

### 16.2 协议端点

| 端点 | 用途 |
|---|---|
| `POST /register` | body `{ holder_id, workflow_run_id, workflow_name, started_at, proxy_pool_hash, page_range? }` → 返回所有当前 runner 的 `proxy_pool_hash[]` |
| `POST /heartbeat` | body `{ holder_id }` |
| `POST /unregister` | body `{ holder_id }` |
| `GET /active_runners` | 调试 dump |

### 16.3 配置漂移告警

`register` 响应中包含其它 runner 的 `proxy_pool_hash`。本 runner 启动
时若发现自己的 `sha1(PROXY_POOL_JSON)[:16]` 与现有某个 runner 不同，
会打一条 **WARNING**：

```
PROXY_POOL_JSON drift detected: this runner=<my_hash> peers=[<other_hash>] —
two runners are working with different proxy pools, ban / claim coordination may be inconsistent
```

这是「轻量级」告警，不阻塞启动。运维需要做的是确认两个 GH Actions
workflow 的 `PROXY_POOL_JSON` Secret 是否同步更新。

### 16.4 部署

`wrangler.toml` 已添加 `RUNNER_REGISTRY_DO` binding。第一次 `wrangler
deploy` 时与 P1-B 共用 `[[migrations]] tag = "v3"` 一并创建。
`[vars]` 段：

| 变量 | 默认 | 含义 |
|---|---|---|
| `RUNNER_REGISTRY_ENABLED` | `"true"` | Worker 端总开关；GH Variables 同名变量优先 |
| `RUNNER_STALE_TTL_MS` | `600000` | heartbeat 超 10 分钟即视为 stale |

### 16.5 故障排查

| 症状 | 排查 |
|---|---|
| `Runner registry client not initialised` | 同 §15.6 第 1 行 |
| `unregister failed: HTTP 503` | atexit 钩子；预期内（容错），不影响 5 min 后的 alarm GC |
| `proxy_pool_hash` 全部不同 | 多 workflow 的 Secret 没同步；提交 PR 把 `PROXY_POOL_JSON` 拉成 reusable workflow 输入 |

### 16.6 回滚

- 软关：GitHub Variables → `RUNNER_REGISTRY_ENABLED=false`（或删除
  `PROXY_COORDINATOR_URL`）。
- 完全删类：`npx wrangler delete --class-name RunnerRegistry`。

---

## 17. P2-C：登录配额跨 runner 冷却

### 17.1 解决的问题

`login_total_budget`（`state.py`）按当前 run 的
`len(PROXY_POOL) * LOGIN_ATTEMPTS_PER_PROXY_LIMIT` 计算。即便有
`GlobalLoginState.acquire_lease` 串行化，N 个 runner 同一天仍会按
**各自的 budget** 独立累加 attempt——5 个 runner × 5 attempt = 25 次
登录尝试，远超 JavDB 风控阈值。

**P2-C** 在 `GlobalLoginStateData` 加 `recent_attempts[]`（24 h 滚动窗口
+ 缓冲上限），在 `acquire_lease` 超阈值时**仍授予 lease 但同时返回
`cooldown_until_ms > 0`**。Python 端 `LoginCoordinator` 检测到
cooldown 即刻 release lease 并把所有 `LoginRequired` task 投入
`_pending_login_tasks` deque；daemon `_poll_login_state_loop` 每 3 s
轮询，cooldown 解除后自动重新分发。

### 17.2 协议改动（向后兼容）

- `AcquireLeaseResponse` 加可选字段 `cooldown_until_ms?: number`、
  `recent_attempt_count?: number`（默认 0，老 client 忽略）。
- 新增 `POST /login_state/record_attempt` body `{ holder_id, proxy_name,
  outcome }`，publisher 在 `publish` 成功 / 失败时调用一次，让 DO 累积
  attempt。

### 17.3 默认配置（`wrangler.toml [vars]`）

| 变量 | 默认 | 含义 |
|---|---|---|
| `LOGIN_COOLDOWN_THRESHOLD` | `"5"` | 24 h 内 ≥5 次失败 attempt 触发 cooldown |
| `LOGIN_COOLDOWN_WINDOW_SEC` | `"3600"` | 滚动窗口 1 小时 |
| `LOGIN_COOLDOWN_DURATION_MS` | `"1800000"` | 命中后所有 runner 暂停 30 分钟 |

### 17.4 故障排查

| 症状 | 排查 |
|---|---|
| 启动后 spider 日志一直 `parking <N> tasks (cooldown active)` | 预期行为；查 `wrangler tail` 确认 cooldown_until_ms 是否合理 |
| Cooldown 过频触发 | 调高 `LOGIN_COOLDOWN_THRESHOLD` 或 `LOGIN_COOLDOWN_WINDOW_SEC` |
| 老 Worker 不返回 `cooldown_until_ms` | Python 客户端默认 0 即「无冷却」，行为与今天一致 |

### 17.5 回滚

软关同 §8.1（删 `PROXY_COORDINATOR_URL`）。Worker 端只想关 P2-C 不关
其它的话：把 `LOGIN_COOLDOWN_THRESHOLD` 设为一个很大的数（例 `99999`）
然后 `wrangler deploy`。

---

## 18. P2-D：代理池跨 run 健康度评分

### 18.1 解决的问题

`ProxyPool` 在单 run 内统计 `success/fail/latency`，跨 run 不持久化。
**P2-D** 在 `ProxyCoordinator.CoordinatorState` 加
`successEvents[] / failureEvents[] / latencyEma`，每次 `/lease` 响应里
捎带派生 `health` 字段（`success_count / failure_count /
latency_ema_ms / score ∈ [0,1]`）。Python 端 `ProxyPool.get_next_proxy`
当且仅当 `coordinator` 已配置时切换为**健康度加权随机**——好代理被选
中概率显著更高，坏代理仍有 5% 地板概率获得流量以便恢复。

### 18.2 协议改动（向后兼容）

- `ReportRequest.kind` union 加 `"success"`，body 加可选
  `latency_ms?: number`。
- `LeaseResponse` 加可选 `health: { success_count, failure_count,
  latency_ema_ms, score } | null`（老 client 忽略；新 client 在缺字段
  时退回到 0.5 中性分）。
- 写路径同步刷新 `cached`，避免同 instance 后续 `/lease` 读到旧值。

### 18.3 客户端集成

- `request_handler.RequestHandler._do_request` /
  `_do_request_curl_cffi` 在每次目标站点 HTTP 完成时调
  `coord.report_async(proxy_id, "success"|"failure",
  latency_ms=elapsed_ms)`。**CF bypass service 调用不计入**（避免本地
  bypass 的延迟污染代理质量评分）。
- `setup_proxy_pool` 末尾把
  `coordinator.get_proxy_health_score` 注入 Python `ProxyPool` 的
  `health_provider`；Rust 池暂保留 round-robin（不影响正确性）。

### 18.4 调参建议

健康度评分公式（`proxy_coordinator.ts` `computeHealthSnapshot`）：
- `ratio = success_count / (success_count + failure_count)`
- `latency_penalty = clamp((latency_ema_ms - 500) / 10000, 0, 0.5)`
- `score = ratio - latency_penalty`（无样本时 `score = 0.5`）

如需更激进（坏代理更快被旁路），可在 Python 端把
`ProxyPool._safe_health_score` 的地板从 `0.05` 降到 `0.01`；如需更
保守（避免抖动），可把权重做平方：`weights[i] **= 2`。

### 18.5 回滚

软关同 §8.1。Worker 端不能单独「只关 P2-D 保留 P1-A」——它们共用同一
DO；如有需要可在 Python 端把 `ProxyPool.set_health_provider(None)` 调
用注释掉，pool 即退回 round-robin（仍然继承 P1-A 的 ban/bypass）。

---

## 19. Post-deploy 烟雾测试脚本

仓库内提供
[`scripts/verify_proxy_coordinator_deploy.sh`](../scripts/verify_proxy_coordinator_deploy.sh)
作为 `wrangler deploy` 之后、触发真实 AdHocIngestion 之前的快速健康
检查。脚本对 4 个 DO 类的关键端点各发一次 canary 请求，逐行打印
PASS/FAIL：

```bash
PROXY_COORDINATOR_URL=https://your-worker.workers.dev \
PROXY_COORDINATOR_TOKEN=$(wrangler secret list | grep TOKEN ...) \
    ./scripts/verify_proxy_coordinator_deploy.sh
```

期望输出末尾：

```
RESULT: all DO classes responded OK.  Safe to trigger AdHocIngestion.
```

任何 FAIL 都意味着某个 DO 类没正确部署（migration tag 错配、binding
没生效、token 写错等），**先解决再触发 AdHocIngestion**，避免一次跑
30+ min 后才发现登录或 claim 全部 fail-open 退化。

之后触发一次 AdHocIngestion，在它的 GH Actions 日志中应看到 4 行
`… client initialised`：

| 日志行 | 来自 |
|---|---|
| `Proxy coordinator client initialised: base_url=…` | `setup_proxy_coordinator` |
| `Login-state client initialised: base_url=…, holder_id=…` | `setup_login_state_client` |
| `Movie-claim client initialised: base_url=…, holder_id=…` | `setup_movie_claim_client`（仅当 `MOVIE_CLAIM_ENABLED=true`）|
| `Runner-registry client initialised: base_url=…, holder_id=…, …` | `setup_runner_registry_client` |

最后：从 GH Variables 删 `PROXY_COORDINATOR_URL`，再触发一次 run，
应该看到：

```
Proxy coordinator not configured (PROXY_COORDINATOR_URL/TOKEN unset) — using local throttling only
```

且 spider 行为与 PR 之前完全一致——**这是用户「确保未配置 DO 时回退
默认机制」契约的最终验证**。
