# 确定性流水线测试 harness

流水线 harness（`tests/harness/`）在单进程内对着 fake 跑完整的每日流水线——
**spider → qB uploader → session commit**——因此整条链路可在 CI 中
**零网络、零真实服务**地验证。设计动机见
[ADR-037](../../../design/ADR-037-Pipeline-Test-Harness/ADR-037-deterministic-pipeline-test-harness.md)。

## 它组合了什么

| Seam | 真实代码 | Fake |
| --- | --- | --- |
| HTTP（javdb） | `RequestHandler.get_page` | `FixtureHTTP` —— 按 URL 重放 cassette |
| qB | `_wrap_session_as_client`（+ 连接/登录探测） | `FakeQB` —— 内存版、可控 torrent 状态 |
| DB | `get_db()` → SQLite | autouse `_isolate_sqlite`（单个 seeded 临时库） |

不在测试范围内的副作用 seam（SMTP、proxy coordinator、PikPak、rclone、git）
被中和：PikPak 在 `tests/conftest.py` 中全局 mock，git 副作用在那里禁用，
harness 强制 `STORAGE_MODE=duo` 并把工作目录切到 `tmp_path`，使 spider 的 CSV
与 report 产物绝不触碰真实的 `reports/` 目录树。

## 编写一个场景

场景声明 javdb 页面（cassette）与 FakeQB 配置：

```python
from tests.harness.pipeline_harness import PipelineScenario, FakeQBConfig

scenario = PipelineScenario(
    pages={
        "https://javdb.com?page=1": INDEX_HTML,   # get_page_url(1) 计算出的值
        "https://javdb.com/v/AAA111": DETAIL_HTML_1,
        "https://javdb.com/v/BBB222": DETAIL_HTML_2,
    },
    qb=FakeQBConfig(),
)
```

Cassette 的 key 必须与 spider 实际请求完全一致：index URL 是 `get_page_url(1)`
计算出的值（`https://javdb.com?page=1`）；detail URL 是
`urljoin("https://javdb.com", href)`。编写携带 parser 所读选择器的最小 HTML
（`div.movie-list` → `div.item` → `a.box`；`div#magnets-content` →
`a[href^=magnet]`）；`tests/conftest.py` 中已验证的 fixture
（`sample_index_html` / `sample_detail_html`）是参考。磁链应为 40 位十六进制，
这样 `FakeQB` 能算出稳定的 hash。

## 运行它

`pipeline_harness` fixture（注册在 `tests/harness/conftest.py`）产出一个
`PipelineHarness`。调用 `run_daily(scenario)` 并断言结果：

```python
from tests.harness.scenarios.golden_daily import golden_daily

def test_golden_daily_run_writes_two_movies(pipeline_harness):
    result = pipeline_harness.run_daily(golden_daily())

    assert all("page=" not in m for m in result.http.misses)  # index 命中 cassette
    assert pipeline_harness.history().count() == 2            # 2 部影片已 commit
    assert len(result.qb.all_hashes()) == 2                   # 2 个 torrent 入队
```

`run_daily` 驱动真实的三步流程：`run_spider(options)` →
`run_uploader(QbUploaderOptions(...))` → `commit_session(CommitRequest(...))`。
session id 与 CSV 路径取自 spider 返回的 `SpiderRunResult`（因为 `run_spider`
在其 `finally` 块中、返回前已清空 active-session 上下文）。实时节流（spider 的
逐影片 / phase 切换冷却，以及 uploader 的逐个添加延迟）被中和，因此整次运行远
小于 1 秒即可完成。

### 断言面

| Helper | 返回 |
| --- | --- |
| `result.http.misses` / `result.http.requests` | spider 请求过的 URL（以及未命中 cassette 的） |
| `result.qb.all_hashes()` | 入队到 FakeQB 的 torrent hash |
| `pipeline_harness.history().count()` | commit 后 `MovieHistory` 的行数 |
| `pipeline_harness.events()` | `PipelineEvent` 行 —— ADR-036 落地前为 `[]` |
| `pipeline_harness.acquisition_outcomes()` | `AcquisitionOutcome` 行 —— ADR-033 落地前为 `[]` |

当对应的表尚不存在时，`events()` / `acquisition_outcomes()` 退化为 `[]`，因此
harness 可在这些特性之前干净落地，并在它们落地后自动获得断言能力。

## 范围（Phase 1）

仅覆盖进程内领域逻辑。子进程编排（`step_runner` 进程管理、CLI 参数解析）**不**在
此处验证——由独立的轻量 smoke 测试覆盖。record 模式、场景库
（drift / completion / failure）以及其余 seam 属于 Phase 2。
