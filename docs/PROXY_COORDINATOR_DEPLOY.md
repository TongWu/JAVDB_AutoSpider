# Proxy Coordinator —— 从零开始的部署手册

> 面向运维的 step-by-step 指南。把 `cloudflare/proxy_coordinator/` 部署到
> Cloudflare 免费层，并把 GitHub Actions 的 5 个 workflow 接入跨实例的
> per-proxy 节流协调。**全程零成本，可随时无代价回滚。**

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

### 1.3 一次性 OAuth 登录

```bash
cd cloudflare/proxy_coordinator
npm install              # 安装 wrangler 等依赖
npx wrangler login       # 弹出浏览器，点 Allow
```

成功标志：终端显示 `Successfully logged in.`

---

## 2. 项目目录速览

```
cloudflare/proxy_coordinator/
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
cd cloudflare/proxy_coordinator
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
cd cloudflare/proxy_coordinator
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
bash cloudflare/proxy_coordinator/scripts/check-quota.sh
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
      - uses: actions/checkout@v6
      - run: bash cloudflare/proxy_coordinator/scripts/check-quota.sh
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
cd cloudflare/proxy_coordinator
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

如果某代理缺少 `name`，Python 端会打 ERROR 日志：

```
Coordinator proxy_id derived from host:port hash: <hash> — recommend setting `name` in PROXY_POOL_JSON
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
