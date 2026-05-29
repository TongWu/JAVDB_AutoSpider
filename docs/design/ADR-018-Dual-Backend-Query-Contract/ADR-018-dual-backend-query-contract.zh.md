# ADR-018: 双后端查询契约 — Golden 夹具漂移守卫

| 字段       | 值                                                                    |
| ---------- | --------------------------------------------------------------------- |
| **状态**   | Proposed                                                              |
| **日期**   | 2026-05-29                                                            |
| **作者**   | Ted                                                                   |
| **关联**   | [ADR-029](../ADR-029-Web-Security-Hardening/ADR-029-web-security-hardening.md)（auth 加固 — token 撤销归它）、[ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md)（双后端拆分）、[ADR-010](../ADR-010-D1-Access-Port/ADR-010-d1-access-port.md)（D1 访问端口） |

> 源自 2026-05-29 架构审查（候选 B）：[architecture-review-2026-05-29.zh.html](../architecture/architecture-review-2026-05-29.zh.html)。

## 背景（Context）

前端由**两个逻辑重合的后端**提供服务，二者必须保持同步（CLAUDE.md 称之为 *Backend Overlap / 后端重合面*）：

- **Python 后端** — `apps/api/` + `javdb/storage/repos/`（FastAPI，Docker / 本地自托管）。
- **TypeScript 后端** — `JAVDB_AutoSpider_Web/server/routes/`（Cloudflare Workers 上的 Hono，云端）。

二者是**独立 git 仓库**、独立部署，但跑的是*同一套 Vue 前端*——所以无论哪个后端应答，一条查询都必须产出等价结果。如今让重合查询逻辑保持对齐的，**只有 CLAUDE.md 里的一条散文规则**（"改一侧 → 同 PR 改另一侧"）。没有机械守卫，漂移是无声的。

### 已被覆盖（因此不在本 ADR 范围）

- **API 响应格式** — 已有单一真相源：`docs/api/openapi.json` 由 Python 应用生成，TS 仓经 `scripts/fetch-openapi.mjs` → `openapi-typescript` 消费，并有契约测试（`server/__tests__/contract-compliance.test.ts`、`tests/contract/openapi-shapes.spec.ts`）钉住 TS 响应。**本 ADR 不重决。**
- **Token 撤销 / auth 加固** — 归 [ADR-029](../ADR-029-Web-Security-Hardening/ADR-029-web-security-hardening.md)（KV 支撑、仅 TS、仅 mutations）。部署拓扑为 **TS Worker 是唯一线上 auth 面**（Cloudflare-first）；某次部署只对一个后端做认证，因此无需跨后端撤销一致性。**本 ADR 不涉及。**

### 仍存在的缺口

**动态查询 builder** 在两仓间逐字重复，且无守卫。最清晰的例子——电影历史的过滤 builder：

- Python：`javdb/storage/repos/history_repo.py:240` — `_build_movie_filters()`
- TypeScript：`JAVDB_AutoSpider_Web/server/routes/history.ts:69` — `buildMovieQuery()`

```sql
# 两端逐字相同：
(m.VideoCode LIKE ? OR m.ActorName LIKE ? OR m.SupportingActors LIKE ?)
... m.PerfectMatchIndicator = ? ...
```

同样的形态在 `history`、`sessions`、`stats` 的动态过滤/cursor 逻辑里反复出现。任一仓改了 WHERE、加了过滤项、或调了 cursor 编码，都会与另一仓无声分歧，直到用户发现结果不对。

## 决策（Decision）

引入 **Contract Golden（契约 golden）**：由 Python 生成、语言中立的 golden 夹具，两个后端的测试各自对它断言。这是一个**漂移守卫**（detection-locality），尚不是单一真相源——遵循已商定的"先守卫、再消除"顺序。

### 设计决策（Design Decisions）

**D1. 真相源 = Python；生成器放 `apps/cli/ops/`。** 新增一个 CLI 工具（挨着现有 `dump_openapi`）输出 golden 夹具。Python 本就是 `openapi.json` 的真相源，Contract Golden 沿用同一脉络。

**D2. 粒度 = 规范化 SQL 串 + bindings，而非结果行。** 每条夹具把一组规范的过滤参数映射为 `{ normalized_sql, bindings[] }`。语言中立、无需数据库，直接抓 builder 漂移。（空白折叠为规范形式，避免格式差异造成误报。）明确**不**选 seeded-D1 结果等价：更重、需共享种子，且漂移本就发生在 builder。

**D3. 范围 = 仅动态 builder。** 钉住 `history` / `sessions` / `stats` 的动态过滤 + cursor builder。静态单语句查询交给现有响应格式契约测试——它们几乎不漂移，把全部约 46 处 `prepare()` 都钉住是低杠杆维护。（分期：`history` + `sessions` 在 Phase 1 落地；`stats` 需先做一次小的 router→builder 抽取——其 Python 聚合内联在 `apps/api/routers/stats.py`，不是 repo builder——故落在 Phase 2。）

**D4. 分发沿用 `openapi.json` 跨仓链路。** golden 提交在 Python 仓 `docs/api/contract/`。TS 仓拉取方式与拉取 OpenAPI schema 完全一致（`fetch-openapi.mjs`：dev 用本地 `OPENAPI_PATH` 式覆盖，CI 用 GitHub raw URL）。零新分发机制。

**D5. 跨仓守卫 = vendored golden + CI 检查。** CLAUDE.md 的"同 PR"规则在两仓间不能字面成立。改为：改了 builder 的 Python PR 会重新生成 golden（评审中**可见 diff**）；TS 仓把该 golden vendored 进来，其 CI 在 vendored 副本过期或 builder 分歧时变红（见 D6）。这个产物把散文规则机械化。

**D6. 漂移检测镜像 `openapi.json` / `api.gen.ts` 模式；re-vendor 由 dispatch 自动化。** 早期草稿曾提议把 golden pin 到版本/SHA 以避开 main 分支竞态。**已否决**——它与 house pattern 相悖；后者是故意接受竞态以换取*同步*漂移检测。具体：

- **Vendored + 两个 CI 检查。** golden 像 `src/types/api.gen.ts` 一样提交进 TS 仓。TS CI 跑（1）*新鲜度*步骤：从 Python `main` 重拉 golden 并 `git diff --quiet` vendored 副本——抓 Python 侧漂移（vendored 过期）；（2）vitest *一致性*测试：用 `buildMovieQuery` 等过 vendored golden 案例——抓 TS 侧漂移。这与现有 openapi gen-diff 步骤（`ci.yml`）+ `contract-compliance` 测试形状完全一致。
- **接受竞态。** Python `main` 改 builder 会让 TS CI（所有 PR）变红，直到 golden 被重新 vendor——与 openapi 现状同等摩擦。目标是同步检测，而非避开竞态。
- **re-vendor 由 `repository_dispatch` 自动化。** golden 一变动 Python `main`，Python 仓 CI 向 TS 仓派发事件，自动开一个 *re-vendor + reconcile* PR（跑 vendoring 脚本、提交刷新后的 golden）。仍由人改 TS builder 对齐并合并。（需跨仓 token；细节在 IMP-ADR018-02。）
- **golden 的 `version` = 内容哈希。** version 是其 cases 的哈希（而非手敲字符串）——任何内容变动自证、可随 dispatch payload 携带，杜绝"忘了升版"的坑。

**D7. "消除"延后。** 把两个 builder 收敛为单一共享 *filter spec*（一张声明式的 字段→列+算子+顺序 表，两端 builder 由它派生）是最终的单一真相源端点。延后到守卫证明重复确实在持续漂移后再做。

## 后果（Consequences）

### 正面

- **detection-locality** — 重合查询 builder 的分歧在 CI 失败，而非以错误结果在生产暴露。
- **机械化手工规则** — CLAUDE.md "同步两端"的散文变成强制执行的产物。
- **零新基础设施** — 复用现有 Python 生成 / TS 消费的链路；无新 Cloudflare 资源、无新服务。
- **廉价测试面** — SQL 串 + bindings golden 在 pytest 和 vitest 中均无需数据库即可跑。

### 负面

- **有意改动需重生成 golden** — 任何刻意的 builder 改动都要重新生成并提交 golden（可见、可评审的 diff，但多一步）。
- **跨仓 CI 耦合 + 版本化** — TS CI 新增对 Python 仓产物的依赖；版本/pin 细节（D6）带来适度复杂度。
- **SQL 串脆性** — 字符串相等对格式敏感；靠规范化缓解，但规范化的 bug 可能造成误报。

### 风险

- **规范化漂移** — 若两端以规范器未折叠的方式格式化 SQL，守卫会误报。缓解：在 golden 契约里共享一份极小的规范化规约。
- **向结果等价的范围蔓延** — 除非串级守卫证明不足，否则克制不扩展到 seeded-D1 结果检查。

## 实施路线图（Implementation Roadmap）

| 阶段 | IMP（计划） | 交付 | 延后 |
| --- | --- | --- | --- |
| Phase 1 | [IMP-ADR018-01](IMP-ADR018-01-python-golden-generator.md) | `apps/cli/ops/` 里的 golden 生成器、golden 提交到 `docs/api/contract/`、pytest 钉 history 的 movie+torrent filter（`_build_movie_filters`/`_build_torrent_filters`）+ sessions 查询（抽出的 `_build_session_query`） | `stats`（需 router→builder 抽取） |
| Phase 2 | [IMP-ADR018-02](IMP-ADR018-02-ts-consume-and-dispatch.md) | TS 仓 vendored golden、CI 对 Python `main` 新鲜度 diff + vitest 一致性钉 `buildMovieQuery` 等、**`stats` 聚合 builder**（抽取后）、`repository_dispatch` re-vendor 自动化（D6） | — |
| Phase 3 | IMP-ADR018-03（消除，可选） | 共享 filter spec；两端 builder 由它派生（D7） | 直到守卫显示重复持续漂移 |

## 不在范围（Out of Scope）

- **Auth / token 撤销** — 归 [ADR-029](../ADR-029-Web-Security-Hardening/ADR-029-web-security-hardening.md)。
- **API 响应格式** — 已被 `openapi.json` + 契约测试守卫。
- **跨后端 token 一致性** — 不需要（TS Worker 是唯一线上 auth 面）。
- **静态单语句查询** — 交给响应格式契约测试。

## 状态日志（Status Log）

- 2026-05-29：Proposed（源自架构审查候选 B 的 grilling）。
- 2026-05-29：D6 经 grilling 修订——镜像 `openapi.json` / `api.gen.ts` 模式（vendored golden + CI 新鲜度 diff，接受 main 分支竞态）；re-vendor 由 `repository_dispatch` 自动化；golden `version` = 内容哈希。早期"pin 到版本/SHA"思路因与 house pattern 不一致被否决。
