# 确定性流水线测试 harness

流水线 harness（`tests/harness/`）在单进程内对着 fake 跑完整的每日流水线——
**spider → qB uploader → session commit**——因此整条链路可在 CI 中
**零网络、零真实服务**地验证。设计动机见
[ADR-037](../../../design/_archive/ADR-037-Pipeline-Test-Harness/ADR-037-deterministic-pipeline-test-harness.md)。

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
在其 `finally` 块中、返回前已清空 active-session 上下文）。commit **门控在 spider
与 uploader 均成功**——镜像 `DailyIngestion.yml` 的 "Mark sessions as committed"
步骤（`if: ${{ success() }}`）。当 uploader 失败时（例如
`FakeQBConfig(fail_adds=True)` 让每次 add 返回 `False`，产生非零的
`QbUploaderResult.exit_code`），`run_daily` 不 commit 该 session——生产会把它交给
cleanup-on-failure 回滚——因此 `result.commit_result is None`，pending 行不会进入
`MovieHistory`。实时节流（spider 的逐影片 / phase 切换冷却，以及 uploader 的逐个
添加延迟）被中和，因此整次运行远小于 1 秒即可完成。

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

## 录制 cassette

> **仅供开发者使用。绝不在 CI 中运行。**

当 javdb 的 HTML 发生变化时，使用 `record_pages` 抓取真实页面，再用 `save_cassette`
写盘来刷新 cassette。实时抓取需同时满足三个条件：(1) 场景以 `record_miss=True` 构造，
(2) 提供了 `live_fetch` 回调，且 (3) `JAVDB_HARNESS_RECORD` 环境变量为真值。
普通测试运行——包括所有 CI 运行——三个条件均不满足，因此 `FixtureHTTP` 绝不拨号网络。

典型的开发者 shell 操作：

```bash
# 启用录制模式，然后抓取并持久化
JAVDB_HARNESS_RECORD=1 python3 -c "
from tests.harness.recording import record_pages
from tests.harness.cassette import save_cassette

urls = [
    'https://javdb.com?page=1',
    'https://javdb.com/v/<code>',
]
# 若 JAVDB_HARNESS_RECORD 未设置，record_pages 会抛出 RuntimeError
pages = record_pages(urls, use_proxy=True, use_cookie=True)
save_cassette('tests/harness/scenarios/cassettes/daily', pages)
"
```

`record_pages` 使用真实的 `RequestHandler`（完整的代理 / CF-bypass / 重试机制），
因此录制的内容与生产环境抓到的一致。`save_cassette` 写出 `manifest.json` 与每页
一个 `NNNN.html`；`load_cassette` 将其读回 `{url: body}` 字典，用于
`PipelineScenario(pages=...)`。

两个 helper 均从 `tests.harness` 重新导出，方便使用：

```python
from tests.harness import load_cassette, save_cassette, record_enabled
```

## 场景库

harness 内置了四种超出黄金运行的场景模式，各自验证流水线中不同的门控或 seam。

### 闭环（completion）

验证完整的 ADR-033 采集闭环：spider 入队 → `complete()` 在 `FakeQB` 中推进 →
真实 reconciler 将其提升到 `completed`。

```python
def test_completion_closes_the_loop(pipeline_harness):
    result = pipeline_harness.run_daily(golden_daily())

    # 模拟两个 torrent 在 qB 中下载完毕。
    for qb_hash in result.qb.all_hashes():
        result.qb.complete(qb_hash)  # 将 progress 设为 1.0，state 设为 uploading

    # 真实的 ADR-033 reconciler 完成闭环。
    rec = pipeline_harness.reconcile()
    assert rec.marked_completed == 2
    assert rec.errors == []

    after = {o["qb_hash"]: o["state"] for o in pipeline_harness.acquisition_outcomes()}
    assert set(after.values()) == {"completed"}
```

### 漂移门控（drift）

通过 `before_commit` 钩子注入临界 `ParseRunFieldFill`，使 ADR-035 哨兵拒绝 commit。
session 不 commit，`MovieHistory` 保持为空。

```python
from javdb.ops.sentinel.models import FieldFill
from javdb.ops.sentinel.service import persist_run
from javdb.storage.sessions.commit import SiteContractDriftError

def _seed_critical_drift(session_id: str) -> None:
    # index.video_code 为 critical，min_fill=0.99；在 50 个样本中注入 0.10 填充率
    persist_run([FieldFill("index", "video_code", 0.10, 50)], session_id=session_id)

def test_drift_gate_refuses_commit(pipeline_harness):
    result = pipeline_harness.run_daily(golden_daily(), before_commit=_seed_critical_drift)

    assert isinstance(result.commit_error, SiteContractDriftError)
    assert result.commit_result is None
    assert pipeline_harness.history().count() == 0
```

### 失败回滚（failure rollback）

通过 `FakeQBConfig(fail_adds=True)` 让每次 qB add 都失败。uploader 以非零退出码退出，
`run_daily` 跳过 commit，之后显式调用生产回滚路径来验证 pending 行被清理干净。

```python
from javdb.storage.db import db_rollback_session
from tests.harness.pipeline_harness import FakeQBConfig, PipelineScenario

def test_failure_rolls_back_pending(pipeline_harness):
    scenario = PipelineScenario(pages=golden_daily().pages, qb=FakeQBConfig(fail_adds=True))
    result = pipeline_harness.run_daily(scenario)

    assert result.uploader_result.exit_code != 0
    assert result.commit_result is None

    session_id = result.spider_result.session_id
    db_rollback_session(session_id, dry_run=False)

    assert pipeline_harness.history().count() == 0
```

### 通知（FakeSMTP）

通过伪造的 SMTP seam 运行每日通知步骤，邮件被捕获到内存而非实际发出。

```python
from tests.harness import FakeSMTP, SentEmail

def test_daily_notify_email_is_captured(pipeline_harness):
    result = pipeline_harness.run_daily(golden_daily())

    notify_result = pipeline_harness.run_notify(
        result.spider_result.csv_path,
        result.spider_result.session_id,
    )

    # 调用 run_notify 后，harness.smtp 被填充。
    assert pipeline_harness.smtp is not None
    assert len(pipeline_harness.smtp.sent) == 1
    assert pipeline_harness.smtp.sent[0].subject
    assert notify_result.email_sent is True
```

`FakeSMTP` 和 `SentEmail` 均从 `tests.harness` 重新导出。

## 黄金运行差异（Golden-run diff）

位于 `tests/harness/scenarios/golden_runs/daily/snapshot.json` 的已提交快照
记录了标准每日场景的权威、确定性输出。
差异测试（`tests/harness/test_golden_run.py::test_golden_daily_run_matches_snapshot`）
实时运行黄金场景，若任何输出偏离已提交快照则测试失败。

### 快照捕获的内容

| 键 | 内容 |
| --- | --- |
| `movies` | `MovieHistory` 中每行的 `{video_code, href}`（按 `href` 排序） |
| `torrents` | `TorrentHistory` 中的 `MagnetUri` 字符串（已排序） |
| `qb_hashes` | 入队到 `FakeQB` 的 torrent hash（已排序） |
| `acquisition` | `AcquisitionOutcome` 行的 `{qb_hash, state}`（按 `qb_hash` 排序） |
| `events` | `PipelineEvent` 名称按序列顺序排列 |

### 快照排除的内容

快照故意省略所有非确定性字段，使差异有意义而非噪音：

- **Session id** —— 每次运行重新生成
- **所有时间戳**（`CreatedAt`、`UpdatedAt`、`DateTime` 等）
- **自增 id**（`rowid`、`Id` 等）
- **事件序列号**（`seq`）

快照中只包含身份稳定的确定性字段。

### 防止漂移

差异测试注册在常规测试套件中，每次 CI 推送时运行。它：

1. 通过 `PipelineHarness.run_daily` 运行黄金每日场景。
2. 调用 `capture_snapshot` 将实时输出投影为规范化快照字典。
3. 调用 `diff_snapshots(expected, actual)`，其中 `expected` 从
   `tests/harness/scenarios/golden_runs/daily/snapshot.json` 加载。
4. 若任何键有差异，则以可读的差异信息使测试失败。

### 有意重新生成快照（bless）

当行为变更是**有意为之**时——新增字段、排序顺序变化、新事件——使用 bless 命令
重新生成已提交快照：

```bash
JAVDB_HARNESS_BLESS=1 pytest tests/harness/test_golden_run.py -k golden_daily_run_matches
```

`JAVDB_HARNESS_BLESS=1` 使测试将实时快照写回磁盘，然后跳过（使运行为绿）。
将更新后的 `snapshot.json` 与代码变更一起暂存并提交。

> **在重新 bless 之前请审查快照差异——快照变化意味着行为变更，而非日常维护。**

黄金运行 helper 均从 `tests.harness` 重新导出，方便使用：

```python
from tests.harness import capture_snapshot, diff_snapshots
from tests.harness import golden_path, load_golden, save_golden
```

## 范围

仅覆盖进程内领域逻辑。子进程编排（`step_runner` 进程管理、CLI 参数解析）**不**在
此处验证——由独立的轻量 smoke 测试覆盖。
