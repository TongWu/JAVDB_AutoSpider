# ADR-028：Web 平台与能力完整性路线图

| 字段       | 值                                                                    |
| ---------- | --------------------------------------------------------------------- |
| **状态**   | 已接受 — 伞型路线图；执行下放给子 ADR                                  |
| **日期**   | 2026-05-29                                                            |
| **作者**   | Ted                                                                   |
| **关联**   | [ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md), [ADR-029](../_archive/ADR-029-Web-Security-Hardening/ADR-029-web-security-hardening.md), [ADR-030](../_archive/ADR-030-Web-Feature-Parity/ADR-030-web-feature-parity.md), [ADR-031](../_archive/ADR-031-Web-Operational-Polish/ADR-031-web-operational-polish.md), [ADR-026](../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md), [ADR-022](../ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md), [ADR-023](../ADR-023-Proxy-Recommendation-Policy/ADR-023-proxy-recommendation-policy.md), [ADR-024](../ADR-024-Torrent-Quality-Evidence/ADR-024-torrent-quality-evidence.md), [ADR-025](../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md) |

> **重编号已完成（2026-05-29）。** web 集群已由 `IMP-ADR028-01` 重编号：
> ADR-029（`ADR-029-Web-Security-Hardening/`）、ADR-030（`ADR-030-Web-Feature-Parity/`）、
> ADR-031（`ADR-031-Web-Operational-Polish/`）。下方[重编号计划](#重编号计划)保留旧→新映射作为历史记录。

## 背景

系统在一个 Vue 前端
（[`JAVDB_AutoSpider_Web`](https://github.com/TongWu/JAVDB_AutoSpider_Web)）背后运行**两套 API 后端**，
这是 [ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md) 的决策：

| | **Cloudflare（TypeScript Worker，Hono）** | **本地 / Docker（Python FastAPI）** |
| --- | --- | --- |
| 角色 | 薄层：**D1 查询 + GitHub Actions 调度** | 全功能：**子进程跑 spider + 直连 qB / PikPak / rclone / SMTP** |
| 能力边界 | 无文件系统、无子进程、无法直连内网服务 | 在宿主机上直接执行一切 |

一次能力审计（2026-05-29）将两套后端对照九个 GitHub Actions workflow 和设计 backlog
进行了比对，发现完整性缺口**散落在不同部署模式、不同 ADR 之间**，没有任何单一文档对这些工作排序。
三个已接受的 web ADR（018/019/020）基本未落地；一个 AI 运维 ADR（026）正在推进中；
四个内容智能 ADR（022–025）处于 Proposed 状态、彼此存在依赖却没有约定好的推进顺序。

本 ADR 是一份**伞型路线图**。它记录审计结论、定义优先级评分准则、把工作归并成若干工作流（workstream），
并把每条工作流分派给一个子 ADR。它刻意**不产出任何代码**——执行落在各子 ADR 及其 IMP 中。

## 审计发现

### 发现 1 — Cloudflare 承接 GitHub Actions 的覆盖度

Cloudflare 无法自行执行流水线工作，必须调度到 GitHub Actions。六个 workflow 拥有一等公民的类型化端点
并带 `job_runs` 追踪；另外三个**只能**通过通用的 `POST /api/gh-actions/runs` 触达
（要求 `GH_ACTIONS_TIER=admin` 且已知 workflow 文件名），且**不会写入 `job_runs`**，
因此永远不会出现在 Tasks 列表或统计里。

| Workflow | 类型化端点 | `job_runs` 追踪 | 状态 |
| --- | --- | --- | --- |
| `DailyIngestion.yml` | `POST /api/tasks/daily` | 是 | 完整 |
| `AdHocIngestion.yml` | `POST /api/tasks/adhoc` | 是 | 完整 |
| `QBFileFilter.yml` | `POST /api/ops/qb/filter-small` | 是 | 完整 |
| `RcloneManager.yml` | `POST /api/ops/rclone/run` | 是 | 完整 |
| `StaleSessionCleanup.yml` | `POST /api/ops/cleanup/stale-sessions` | 是 | 完整 |
| `RollbackD1.yml` | `POST /api/sessions/:id/rollback` | 是 | 完整 |
| `Migration.yml` | 无（`/api/migrations/*` 是 501 stub） | 否 | **缺口** — 仅通用调度，无追踪 |
| `WeeklyDedup.yml` | 无 | 否 | **缺口** — 仅通用调度，无追踪 |
| `TestIngestion.yml` | 无 | 否 | **缺口** — 仅通用调度，无追踪 |

**对"Cloudflare 能否承接所有 GitHub Actions 活动？"的回答：** 对六个运维 workflow 基本可以。
Migration / WeeklyDedup / TestIngestion 虽可调度但不是一等公民——没有类型化端点、没有 UI 入口、没有任务追踪。

### 发现 2 — 本地 / Docker 的能力与 `INGESTION_MODE` 能力诚实性缺口

Python 后端对 Cloudflare 全部 stub 掉的能力都有 live handler：qB 实时种子列表、PikPak 队列/转存、
email 测试/历史/重发、rclone、cleanup（含 claim-stages）、本地 spider 子进程、headless 登录、
parse tester、深度 health-check、migrations。**对"本地/Docker 能否本地执行所有活动？"的回答：能**，
通过子进程 + 直连完成。

**有一个正确性缺口：** `INGESTION_MODE=github` / `dual` 被 `GET /api/capabilities` 上报
（且 web README 把它当作一种拓扑来宣传），但 `apps/api/services/task_service.py` **没有 GitHub 调度分支**——
`trigger_daily_task` / `trigger_adhoc_task` 无视 `INGESTION_MODE` 永远跑本地子进程。
因此 capabilities 端点上报了一个执行层并不兑现的模式。

### 发现 3 — Web ADR backlog 状态

以下结论均已对照当前代码核实，而非仅看 ADR 的 status 字段。

| ADR | 声明状态 | 代码实况 | 缺口 |
| --- | --- | --- | --- |
| ADR-018 Web Security Hardening | Accepted | `server/app.ts` 只挂了 CORS + auth | 无 rate-limit / CSRF / 安全响应头中间件 |
| ADR-019 Web Feature Parity | Accepted | `config-schema.ts` 约 37 字段；`auth.ts` 只有 login/refresh/logout | 缺 26 个 config key、无 `change-password`、无 `SMTP_SERVER`/`PAGE_START` 别名、无 `duration` 趋势 |
| ADR-020 Web Operational Polish | Accepted | 未见对应实现 | 无 workflow-schema 端点、无 dispatch 输入校验、qb test 无 `status` 字段 |
| ADR-026 AI Operations Diagnosis | Phase 1 已交付（2026-05-27） | — | Phase 2（history analytics）、Phase 3（gated remediation）待做 |
| ADR-022/023/024/025 | Proposed | 未开工 | 偏好数据基座/模型、代理推荐、种子质量证据 |

## 决策

### 设计决策

**D1. 把 web 平台作为单一治理项目。** 将 Cloudflare/本地的对等缺口与 web ADR backlog
视为一个被治理的整体项目，由本伞型 ADR 统一排序，而非各 ADR 各自零散推进。

**D2. 优先级评分准则——能力诚实性优先。** 按以下顺序排序：
（1）**能力诚实性**（系统绝不能宣称它做不到的事——正确性/信任）；
（2）**安全**，当 console 暴露公网时提到最高优先级；
（3）两套后端之间的**功能对等**；
（4）**运维打磨与 AI 运维**；
（5）**决策智能**（scope 最大、horizon 最长）。同一层级内优先做低风险/低成本项。

**D3. 五条工作流，各由一个子 ADR 持有。** 见
[工作流路线图](#工作流路线图)。伞型 ADR 持有排序与依赖；各子 ADR 持有自身的设计与 IMP。

**D4. 重编 web 集群，让伞型领头。** ADR-019 以下没有空闲整数，而 `+1` 平移会撞已归档的 ADR-021。
因此集群整体迁到尾部一段全新的连续号段，伞型领头：
ADR-028（伞型）→ ADR-029/030/031（子项）。旧号退役、永不复用。见 [重编号计划](#重编号计划)。

**D5. 把"能力诚实性"（WS-A）并入 Feature Parity（ADR-030），不新建 ADR。**
Cloudflare 的类型化端点缺口与 Python 的 `INGESTION_MODE` 缺口都属于对等性问题，
应归入重编号后的 Feature Parity ADR，而非派生一个独立 ADR。

**D6. 本 ADR 不产出代码。** 它是一份路由/排序记录。它唯一直接触发的执行是 `IMP-ADR028-01`
里的重编号 bookkeeping。

### 工作流路线图

| 工作流 | 优先级 | 范围 | 持有 ADR | 依赖 |
| --- | --- | --- | --- | --- |
| **WS-A 能力诚实性** | **P0** | Cloudflare：为 `Migration` / `WeeklyDedup` / `TestIngestion` 补类型化调度端点 + `job_runs` 追踪。Python：解决 `INGESTION_MODE=github`/`dual`——要么在 `task_service` 里实现 GH 调度，要么在 `/api/capabilities` 里别再上报该模式。 | ADR-030（并入） | — |
| **WS-B Web 安全加固** | **P1**（公网暴露则 P0） | 在 Worker 内加 rate-limiting、CSRF 防护、安全响应头。 | ADR-029 | — |
| **WS-C 功能对等** | **P1** | 26 个缺失 config key、`POST /api/auth/change-password`、规范键别名（`SMTP_SERVER`/`PAGE_START`/`PAGE_END`）、`duration` 统计趋势。 | ADR-030 | — |
| **WS-D 运维打磨 + AI 运维** | **P2** | workflow-schema 端点、dispatch 输入校验、qb test `status` 字段；ADR-026 Phase 2（history analytics）与 Phase 3（gated remediation）。 | ADR-031 + ADR-026 | WS-B、WS-C |
| **WS-E 决策智能** | **P3** | 偏好数据基座 → 偏好模型；种子质量证据；代理推荐 bandit。最终汇入未来的 `download_utility_score`。 | ADR-022 → ADR-025；ADR-024；ADR-023 | 松依赖：WS-A 的数据管路 |

### 依赖关系图

```
ADR-028  (伞型路线图)
  │
  ├─ WS-A  能力诚实性 ................. ADR-030            [P0]
  ├─ WS-B  安全 ....................... ADR-029            [P1 / 公网暴露则 P0]
  ├─ WS-C  功能对等 ................... ADR-030            [P1]
  ├─ WS-D  打磨 + AI 运维 ............. ADR-031, ADR-026 Ph2/3   [P2]
  └─ WS-E  决策智能                    [P3]
            ADR-022 (偏好基座) ─→ ADR-025 (偏好模型) ─┐
            ADR-024 (种子质量证据) ────────────────────┴─→ download_utility_score (未来)
            ADR-023 (代理推荐 bandit) ── 并行、独立
```

### 重编号计划

`IMP-ADR028-01` 执行这次机械重编号。在此之前子 ADR 保持旧路径。

| 文档 | 旧号 | 新号 | 旧文件夹 | 新文件夹 |
| --- | --- | --- | --- | --- |
| Web 平台完整性路线图（本文） | — | **ADR-028** | — | `ADR-028-Web-Platform-Completeness-Roadmap/` |
| Web Security Hardening | ADR-018 | **ADR-029** | `ADR-018-Web-Security-Hardening/` | `ADR-029-Web-Security-Hardening/` |
| Web Feature Parity | ADR-019 | **ADR-030** | `ADR-019-Web-Feature-Parity/` | `ADR-030-Web-Feature-Parity/` |
| Web Operational Polish | ADR-020 | **ADR-031** | `ADR-020-Web-Operational-Polish/` | `ADR-031-Web-Operational-Polish/` |

**每个文件夹的重命名会涉及：** `ADR-0NN-*.md` + `.zh.md` 文件、`IMP-ADR0NN-PP-*.md` 文件名，
以及两种语言里所有 `ADR-0NN` / `IMP-ADR0NN` 自引用。

**改动范围（已核实）：** 局限于 `docs/design/`。对 018/019/020 的交叉引用只存在于这三个集群文件夹、
`ADR-022` 以及已归档的 `ADR-021` 中。web repo、`CONTEXT.md`、`CLAUDE.md`、`README.md` 都**没有**引用这些号。
`.claude/worktrees/*` 是独立 worktree，不在范围内。

## 影响

### 正面

- 单一文档即可回答"平台是否完整？"并对剩余工作排序，取代散落在各 ADR 的零散判断。
- 把能力诚实性缺口（系统宣称它做不到的事）提到 P0，而不是作为隐性的信任 bug 一直拖着。
- 伞型 ADR 紧排在其子项之前（ADR-028 → 029/030/031），在目录列表里一眼可见治理优先级。

### 负面

- 重编三个已接受 ADR 会修改历史记录，需要一次谨慎、机械的交叉引用清扫。
- 退役的号段（018/019/020）会在序列里留下空缺，未来读者需理解为"已迁移，而非缺失"。

### 风险

- 重编号时漏改某个交叉引用会产生悬空链接。通过事先界定改动范围、迁移后 grep 复查来缓解。
- 若把伞型当成*干活*的地方（而非*分派*工作），会重新制造它本想消除的散乱。通过 D6（不产代码）缓解。

## 实施路线图

| 阶段 | IMP | 交付 | 推迟 |
| --- | --- | --- | --- |
| Phase 1 | `IMP-ADR028-01` | 执行 web 集群重编号（028 伞型 + 029/030/031 子项）；更新 `ADR-022` 与已归档 `ADR-021` 中的交叉引用；把 WS-A 能力诚实性范围并入重编号后的 Feature Parity ADR（030）。 | 所有功能实现——落在各子 ADR 自己的 IMP（`IMP-ADR029-*`、`IMP-ADR030-*`、`IMP-ADR031-*`）。 |

## 参考

- [ADR-017 Cloudflare-First Deployment](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.md) — 本路线图治理的双后端拆分
- [ADR-007 Monorepo Restructure](../_archive/ADR-007-Monorepo-Restructure/ADR-007-monorepo-restructure-2026-05.md) — 规范布局
- 子 ADR：[ADR-029](../_archive/ADR-029-Web-Security-Hardening/ADR-029-web-security-hardening.md)、[ADR-030](../_archive/ADR-030-Web-Feature-Parity/ADR-030-web-feature-parity.md)、[ADR-031](../_archive/ADR-031-Web-Operational-Polish/ADR-031-web-operational-polish.md)、[ADR-026](../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md)、[ADR-022](../ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md)、[ADR-023](../ADR-023-Proxy-Recommendation-Policy/ADR-023-proxy-recommendation-policy.md)、[ADR-024](../ADR-024-Torrent-Quality-Evidence/ADR-024-torrent-quality-evidence.md)、[ADR-025](../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md)

## 状态日志

- 2026-05-29：已接受。创建伞型路线图；web 集群重编号与 WS-A 范围合并下放给 `IMP-ADR028-01`。
- 2026-05-29：`IMP-ADR028-01` 执行完毕——web 集群已重编号（018/019/020 → 029/030/031）；WS-A 能力诚实性范围已并入 ADR-030。
