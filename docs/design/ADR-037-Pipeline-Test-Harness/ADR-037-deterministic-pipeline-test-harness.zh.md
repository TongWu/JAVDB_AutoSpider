# ADR-037：确定性管道测试 Harness

| 字段       | 值                                                                    |
| ---------- | --------------------------------------------------------------------- |
| **状态**   | Proposed — 伞型;执行下放给各期 IMP                                    |
| **日期**   | 2026-05-29                                                            |
| **作者**   | Ted                                                                   |
| **关联**   | [ADR-012](../_archive/ADR-012-Pipeline-Run-Boundary/ADR-012-pipeline-run-structured-boundary.md), [ADR-015](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md), [ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md), [ADR-035](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md), [ADR-036](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md) |

> 源自 2026-05-29 一次关于全新方向(方向五——确定性仿真测试床)的头脑风暴。

## 背景 (Context)

管道只能对**live 外部服务**做端到端运行:真实 javdb.com（HTTP 经 `javdb/infra/request.py` 的 `requests`/`curl_cffi`）、真实 qBittorrent（`javdb/integrations/qb/client.py` 经 `requests`）、真实 DB。2026-05-29 架构评审点名 commit"只能对真 DB 测"。今天的测试**临时**掩盖了这点:

- DB 接缝**已基本解决**——`tests/conftest.py` 的 `_isolate_sqlite(tmp_path)`（autouse）把三个逻辑库都指向一个临时 SQLite 并跑 `init_db` 建真实 schema。
- 但 **HTTP 是逐测试手搓 mock**（如 `test_spider_backends.py` 里 `responses = iter(...)` + `monkeypatch`）;**没有共享的 record/replay**。
- **没有可复用的 fake qB**;qB 逐测试 mock。

成本如今叠加:本会话同源的三份 Phase-1 设计（[ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md) 闭环、[ADR-035](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md) 哨兵、[ADR-036](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md) 事件脊柱）都依赖**管道行为**——qB 状态转换、commit 门控、发出的事件——而当前没有任何测试能端到端驱动它们。

本 ADR 建一个**确定性、进程内、端到端的管道 harness**,对 fake 跑 spider → uploader → commit,使整条管道（及三个新功能）能在 CI 里零网络、零 live 服务地验证。

## 决策 (Decision)

建 `tests/harness/`:一个进程内管道 harness,组合 **FixtureHTTP** 传输层（从 cassette 回放 javdb）、**FakeQB**（in-memory、可控种子状态）与现有 seeded 临时库。一个 pytest fixture 用 monkeypatch 注入 fake、进程内驱动 service 层,并暴露场景 + 断言面。

### 设计决策 (Design Decisions)

**D1. 进程内 service 组合——非子进程。** harness 直接在一个进程内调 **service 层**（`run_spider` 经 ADR-012 的 `InProcessSpiderStepRunner`、uploader service、`commit_session`），使 fake 可由 monkeypatch 注入（monkeypatch 跨不过子进程边界）。这端到端测管道**领域逻辑**;子进程编排（`step_runner` 进程管理、CLI 解析）是基础设施,另由轻量 smoke 测试单独覆盖,不归本 harness。

**D2. 一个 `tests/harness/` 测试支撑包,由 pytest fixture 组合。** 非生产代码。一个 `pipeline_harness` fixture / context manager 建好全部 fake、复用 `_isolate_sqlite`、驱动一次 run,并 yield 控制 + 断言 handle。

```
tests/harness/
  fixture_http.py     # FixtureHTTP 传输层（回放;可选 record）
  fake_qb.py          # FakeQB（in-memory、可控状态）
  pipeline_harness.py # 组合 + 进程内驱动 + 控制/断言面
  scenarios/          # cassette（HTML）+ 场景定义
```

**D3. `FixtureHTTP` 从 cassette 回放 javdb;record 模式可选且门控。** cassette = 一个目录,把请求 URL → 响应（status/headers/body）映射,在 `request.py` 传输接缝回放。**默认 fixtures 为精选的最小 index/detail HTML**（扩展 `tests/fixtures/parser/`）。一个**可选、env 门控的 record 模式**在 cassette miss 时打真实请求并存档——用于 javdb 改版后刷新 cassette。默认精选最小,因为 javdb 是成人内容、整页大且敏感;录制只是开发期刷新工具,绝不在 CI 跑。（这个"金页"录制与 [ADR-035](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md) 哨兵的 golden-anchor 同源。）

**D4. `FakeQB` 为 in-memory、可控状态。** 它在一个 in-memory 种子字典上实现代码实际用到的 `QBittorrentClient` 面（`add_torrent`、`get_torrents_multiple_categories`、`delete_torrents`、`get_existing_hashes`），外加控制方法（`complete(hash)`、`stall(hash)`），让场景能模拟下载完成——正是 ADR-033 / ADR-035 / ADR-036 要断言的。

**D5. 复用 seeded 临时库;其余有副作用的接缝 neuter 掉。** DB 用现有 `_isolate_sqlite`。SMTP、proxy coordinator、PikPak、rclone 被 mock/neuter（PikPak 在 conftest 已全局 MagicMock）。Phase 1 fake 三个承重接缝（HTTP、qB、DB）;其余打桩。

**D6. 场景 + 断言 API。** 测试作者声明一个 `PipelineScenario`（javdb 页面 + FakeQB 配置），并经 helper 对结果断言:

```python
scenario = PipelineScenario(pages={index_url: INDEX_HTML, detail_url: DETAIL_HTML}, qb=FakeQBConfig())
result = harness.run_daily(scenario)        # spider -> uploader -> commit, in-process
assert harness.history().count() == 2
assert "TorrentQueued" in harness.events()  # 当 ADR-036 已建
```

**D7. 分期:先证明,再生长。** Phase 1 交付 harness 核心 + FixtureHTTP（回放）+ FakeQB + **一个金场景**（干净 daily run）断言权威结果。Phase 2 加 record 模式、场景库（漂移→门控、完成→闭环、失败→回滚）与其余接缝。Phase 3（可选）在 harness 上叠金 run 录/放 diff。

## 后果 (Consequences)

### 正面 (Positive)

- **整条管道 CI 可测**——spider → uploader → commit 确定性跑,零网络/live 服务。
- **给本会话三份 IMP 上保险**——闭环、哨兵、事件脊柱行为获得真正端到端覆盖。
- **一份共享 HTTP/qB fake**——替掉今天逐测试的手搓 mock。
- **可刷新**——门控 record 模式让 cassette 随 javdb 演进保持最新。
- **建在现有之上**——复用 `_isolate_sqlite` 与 ADR-012 进程内 spider runner。

### 负面 (Negative)

- **进程内 ≠ 完整生产保真**——子进程编排（`step_runner`、CLI 接线）不被本 harness 覆盖（由单独 smoke 覆盖）。
- **fake 须跟住真实契约**——`FakeQB` 与 `FixtureHTTP` 须随 qB API 与 javdb HTML 变化保持对齐（record 模式与 ADR-035 哨兵都帮助发现漂移）。
- **精选 fixtures 可能掩盖真页怪癖**——靠 record 模式周期刷新缓解。

## 实施路线图 (Implementation Roadmap)

| 阶段 | IMP | 交付内容 | 推迟内容 |
| --- | --- | --- | --- |
| Phase 1 — Harness 核心 + 金场景 | [IMP-ADR037-01](IMP-ADR037-01-harness-core.md) | `tests/harness/`（FixtureHTTP 回放、FakeQB、`pipeline_harness` fixture、场景+断言 API）;一个金 daily 场景（index → 详情 → 加种 → commit）断言 history（+ 事件,若 ADR-036 已建） | record 模式;场景库;SMTP/pikpak/rclone 接缝 |
| Phase 2 — 场景库 + record + 接缝 | IMP-ADR037-02（占位） | record 模式;漂移/完成/失败场景;SMTP/pikpak/rclone fake | — |
| Phase 3 — 金 run diff（可选） | IMP-ADR037-03（占位） | 录一次真实 run 的输入+输出;CI 回放 + diff | — |

Phase 1 独立成立、只加测试支撑代码。Phase 2/3 扩展覆盖。

### 明确的非目标 (YAGNI)

- **不测子进程编排**——按 D1 走进程内;子进程接线另由轻量 smoke。
- **CI 里不打真 javdb**——record 模式仅开发期、env 门控。
- **非性能/压测 harness**——只管确定性与正确性。
- **Phase 1 不覆盖全部接缝**——先 HTTP + qB + DB;SMTP/pikpak/rclone 留 Phase 2。

## 领域语言 (CONTEXT.md 待补充项)

- **Pipeline harness（管道 harness）**——`tests/harness/` 的进程内装置,对 fake 跑 spider → uploader → commit。
- **Cassette（盒带）**——`FixtureHTTP` 回放的、录好的 javdb 请求→响应对的目录。
- **FixtureHTTP / FakeQB**——回放 HTTP 传输层与 in-memory qB fake。
- **Scenario（场景）**——驱动一次 harness run 的、声明的 javdb 页面 + qB 配置集合。
- **Golden scenario（金场景）**——CI 中断言的、标准的干净 daily run 场景。

## 备选方案 (Alternatives Considered)

- **子进程 harness + env 注入 fake**——否决（D1）:保真度最高,但需跨进程可达的 fake（真的 fake-qB HTTP server、env 式 fixture HTTP），为 smoke 已覆盖的编排层付出大得多的复杂度。
- **默认录全(真页 cassette)**——否决（D3）:javdb 是成人内容、页大且敏感;精选最小 fixtures 让测试可读且安全,record 模式负责刷新。
- **仅金 run 录/放**——作为主形态否决:没有下层可组合 fake 的脆弱回归网;作为可选 Phase 3 叠在 harness 上保留。

## 参考 (References)

- [ADR-012 — Pipeline Run Structured Boundary](../_archive/ADR-012-Pipeline-Run-Boundary/ADR-012-pipeline-run-structured-boundary.md)
- [ADR-015 — Integrations Interface Boundary](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md)
- [ADR-033 — Media Closed-Loop](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md)
- [ADR-035 — Site-Contract Drift Sentinel](../ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md)
- [ADR-036 — Event-Sourced Pipeline Spine](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md)

## 状态日志 (Status Log)

- 2026-05-29: Proposed(伞型;三期已划定,IMP 待出)。
- 2026-05-30: Phase 1 已实现（[IMP-ADR037-01](IMP-ADR037-01-harness-core.md)）—— `tests/harness/` 交付 FixtureHTTP + FakeQB + `pipeline_harness` fixture 与一个黄金每日场景（index → 2 个 detail → queued → commit），断言历史落地 2 行 + qB 入队 2 个 hash；11 个测试 <0.4s 全绿。实现与计划的偏差见 IMP 的 "Implementation Reconciliation"（三步 `run_spider`→`run_uploader`→`commit_session`、session 取自 `SpiderRunResult`、`STORAGE_MODE=duo` 以打通 CSV 交接）。Phase 2/3 仍为 stub。
