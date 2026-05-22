# ADR-004: 代理发现机制——Runner 上报完整 PROXY_POOL

**状态**: 已完成 2026-05-16 —— runner `/register` payload 已上传 `proxy_pool`；worker 侧 RunnerRegistry DO 已上线 `proxies_seen` 表 + 处理器（`/do/proxies_seen`、`/proxies_seen`）。
**日期**: 2026-05-16
**决策者**: Proxy Coordinator Dashboard 改造
**关联实现计划 (Related Implementation Plans)**: [IMP-ADR004-01](../../impl/archive/IMP-ADR004-01-dashboard-phase1-proxy-pool-upload.md)（Phase 1 —— runner 端上传，2026-05-16 完成）、[IMP-ADR003-01](../../impl/IMP-ADR003-01-dashboard-phase2-worker-backend.md)（worker 后端持久化——schema 已上线）

---

## 背景 (Context)

Dashboard 改造的需求之一：per-proxy state 面板默认显示**所有 proxy**（含闲置备用），并显示人类可读的 `name`（如 "Singapore Arm-3"）。

但当前架构有一个**关键约束**：

```
// JAVDB_AutoSpider_Proxycoordinator/src/index.ts L859-866 注释原文
Proxy enumeration:
  The ProxyCoordinator DO is addressed per-id (`idFromName(proxy_id)`);
  there is no master "list of known proxies" registry. The operator
  passes the proxy IDs they care about via `?proxy_ids=a,b,c`
```

**Worker 端目前完全不知道有哪些 proxy**。每个 runner 启动时读取自己的 `config.py` 的 `PROXY_POOL` 列表，但 Worker 看不到这些数据。`RunnerRegistry` 现有的 `proxy_pool_hash` 字段只是 PROXY_POOL JSON 的 SHA1 前缀（16 字符），不是 ID 列表。

要做到"默认显示所有 proxy 含名字"，必须先解决"全集怎么来"的问题。

---

## 决策 (Decision)

**扩展 runner `/register` payload 上报完整 PROXY_POOL（含 idle 备用项），Worker 端 `RunnerRegistry` DO 持久化为 `proxies_seen` 表，dashboard 取此表为全集来源。**

### Payload 扩展

`POST /register` 请求 body 新增字段（向后兼容，旧 runner 不发也能跑）：

```json
{
  "holder_id": "...",
  "workflow_run_id": "...",
  "proxy_pool_hash": "...",
  "proxy_pool": [                 // ← 新增
    { "id": "Singapore Arm-3", "name": "Singapore Arm-3" },
    { "id": "Tokyo Backup-1", "name": "Tokyo Backup-1" }
  ]
}
```

注意：Python 端 `normalize_proxy_id()` 当前已经把 `name` 当作 `proxy_id`（见 `proxy_policy.py:150`），所以 `id` 和 `name` 99% 一致。两个字段都上报是为未来允许 `id ≠ name` 的灵活性。

### Worker 端持久化

`RunnerRegistry` DO 新增表：

```sql
CREATE TABLE proxies_seen (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  last_seen_ms INTEGER NOT NULL,
  first_seen_ms INTEGER NOT NULL
);
```

`/register` 处理逻辑：每个 `proxy_pool` 项 `INSERT OR REPLACE` 进 `proxies_seen`，更新 `last_seen_ms`。

### Dashboard 读取

`/ops/snapshot` 改为：
- 若 query string 无 `?proxy_ids=...`，自动取 `proxies_seen` 全集（取代当前的"空 → 提示用户加 query param"）
- `?proxy_ids=...` 仍然支持（向后兼容外部监控脚本）

### Stale 处理（人工删除 + 30 天折叠）

- 默认不自动删除（避免误删长期闲置的备用 proxy）
- Dashboard UI：默认折叠 `last_seen_ms < now - 30d` 的条目，用户可点击展开
- 手动删除：新增 `DELETE /proxies_seen?id=...`（cookie-authed only）

---

## 备选方案 (Alternatives Considered)

### 备选 A：只展示 active 上报的 proxy（不持久化全集）

dashboard 仅显示当前心跳中的 runner 报告过的 proxy。

**优点**：实现最简单；无 storage
**缺点（不选的原因）**：
- 空窗期（GH Actions 没运行）dashboard 完全空白——运维无法事先检查 proxy 配置
- 备用 proxy 从未被 active 使用过时永远看不到
- 体验和"什么都不显示"差别不大

### 备选 B：Worker `[vars]` 静态配置 `KNOWN_PROXY_IDS`

operator 在 `wrangler.toml` 维护一个 proxy ID 列表。

**优点**：完全独立于 runner
**缺点（不选的原因）**：
- 需要 wrangler deploy 才能修改 proxy 列表——和 `config.py` 的 PROXY_POOL 双重维护
- 容易漂移（两个真相来源）

### 备选 D：ProxyCoordinator DO 自动注册

每次 `/lease` 或 `/report` 时，把 proxy_id 写到一个 singleton "registry" DO 里。

**优点**：完全自动，runner 不需要改协议
**缺点（不选的原因）**：
- 每次 lease 多一次 DO 写入（高频路径）
- 仍然无法发现"闲置备用 proxy"（从未被 lease 过的不会出现）
- 没有 `name` 字段来源（lease 路径上只有 proxy_id）

---

## 实现策略 (Implementation)

### Phase 1（rollout 阶段，参见 grill-me Q7）

**autospider 端先发**（向后兼容，Worker 不消费也无影响）：

修改 `packages/python/javdb_platform/runner_registry_client.py`:
- `register()` 增加 `proxy_pool: list[dict]` 参数
- 从调用方（spider/pipeline 入口）读 `config.PROXY_POOL`，规范化为 `[{id, name}]` 列表

修改 `packages/python/javdb_platform/proxy_policy.py`（如需）：
- 暴露 `serialize_proxy_pool_for_registry(pool) -> list[dict]` helper

合并并部署 autospider；旧版 Worker 收到新字段会忽略（已确认 `clipString` 不会因为多余字段失败）。

### Phase 2（rollout 阶段）

**Worker 端开始消费**：

修改 `JAVDB_AutoSpider_Proxycoordinator/src/runner_registry.ts`:
- `RegisterRunnerRequest` 类型加 `proxy_pool?: Array<{id: string; name: string}>`
- register handler 解析 `proxy_pool`，写入 `proxies_seen` 表

修改 `JAVDB_AutoSpider_Proxycoordinator/src/index.ts`:
- `aggregateOpsSnapshot()` 在 `proxy_ids` query param 为空时，自动从 `proxies_seen` 读全集
- 仍然支持 `?proxy_ids=...` 作为显式过滤

### Phase 3（rollout 阶段）

Dashboard UI：
- per-proxy 面板始终显示 `proxies_seen` 全集
- chip 过滤组（grill-me Q6b）
- 30 天 stale 自动折叠

---

## 后果 (Consequences)

### 正面影响

1. **dashboard 默认 ready-to-view**：无需运维手工传 `?proxy_ids=...`
2. **空窗期也能看到 proxy 列表**：备用 proxy 一直可见
3. **`name` 字段是 Q5d/Q5e 多线图的 legend 显示来源**
4. **`last_seen_ms` 给"history"功能复用**：可以回答"这个 proxy 上次活动是什么时候"

### 负面影响

1. **Runner ↔ Worker payload 合约变更**：需要两个仓库协调发版
   - **缓解**：Phase 1/2 分两步，向后兼容
2. **proxies_seen 表无自动清理**（30 天只折叠不删）
   - **缓解**：人工删除端点；ops 场景下 proxy 数 <50，长期累积仍可接受
3. **新增一次 DO 写入**（每次 register 时 N 个 `INSERT OR REPLACE`）
   - **缓解**：register 是低频路径（runner 启动一次），N 通常 ≤10

### 风险

1. **旧 runner 不上报 `proxy_pool`** → 它注册期间 dashboard 不发现新 proxy
   - **缓解**：所有 runner 都会逐步升级；旧 runner 不会持续运行
2. **`proxy_pool` 包含敏感信息**（如代理 URL 含密码）？
   - **决策**：只上报 `id` 和 `name`，**不上报 URL / 凭证**
   - **缓解**：在 client 序列化函数里显式 whitelist 字段

---

## 相关决策 (Related Decisions)

- **ADR-002**：可观测性数据存储拓扑（`proxies_seen` 选择 `RunnerRegistry` DO 而非新建 DO 的原因——同 ADR-002 的 atomicity 原则）
- **ADR-003**：Metrics Pipeline（`proxies_seen` 间接决定 metrics snapshot 里能 enumerate 多少 proxy）

---

## 参考资料 (References)

- [CONTEXT.md](../../../../CONTEXT.md) — Runner / RunnerRegistry DO 定义
- 现有 register 实现：`JAVDB_AutoSpider_Proxycoordinator/src/runner_registry.ts:160-219`
- Python 端 proxy_id 规范化：`packages/python/javdb_platform/proxy_policy.py:150-190`
- 现有 PROXY_POOL 配置示例：`config.py.example:114-141`
