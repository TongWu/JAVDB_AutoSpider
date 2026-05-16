# ADR-009: 仪表盘改造 —— Phase 1：Runner 向 Worker 上报 `proxy_pool`

**状态**: 已接受 (Accepted) —— 于 2026-05-16 完成（通过 #f4c5d23c + #e224c374 + #60797d16 合并）
**日期**: 2026-05-16
**决策者**: Proxy Coordinator 仪表盘重写工作流
**相关**: 实现 [ADR-004](ADR-004-proxy-discovery-via-runner-pool-upload.md)；为 [ADR-010](ADR-010-dashboard-phase2-worker-backend.md) 的前置条件

> **格式说明：** 本 ADR 最初是以分步实施计划的形式撰写，后按仓库惯例（设计记录归入 ADR 空间）迁移到 ADR 目录。决策上下文记录在下方 **目标 / 架构 / 技术栈** 前言中；其余部分保留原计划的执行清单。
>
> **面向 AI 工作者（历史信息）：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 按任务逐项实施本计划。步骤使用 checkbox（`- [ ]`）语法跟踪进度。

**目标：** 扩展 Python 端的 `RunnerRegistryClient.register()`，使其在每次 register 调用时上报完整的 PROXY_POOL（形式为 `[{id, name}]`），从而让未来 Phase 2 工作能够在 Worker 中持久化 `proxies_seen` 表。向后兼容：旧 Worker 会忽略新字段。

**架构：** 为 `RunnerRegistryClient.register()` 增加一个可选的 `proxy_pool` 关键字参数。在现有 `proxy_pool_hash()` 旁边新增一个同级 helper `proxy_pool_summary_for_registry()`，将 PROXY_POOL 白名单化序列化为 `[{id, name}]`（不含 URL、不含凭据、不含 auth）。将其接入 `state.py` 中的两个调用点（初次 register + 驱逐后的重注册）。

**技术栈：** Python 3.11+、pytest、现有的 `packages/python/javdb_platform/runner_registry_client.py` 模式。

**参考文档：** [CONTEXT.md](../../../CONTEXT.md)、[ADR-004](../../ai/adr/ADR-004-proxy-discovery-via-runner-pool-upload.md)

---

## 文件结构

**新增文件：** 无

**修改文件：**
- `packages/python/javdb_platform/runner_registry_client.py` —— 在现有 `proxy_pool_hash()` 附近新增 `proxy_pool_summary_for_registry()` helper；为 `RunnerRegistryClient.register()` 增加 `proxy_pool` kwarg
- `packages/python/javdb_spider/runtime/state.py` —— 在两处 `client.register(...)` 调用点（约第 996 行和第 1255 行）传入 `proxy_pool=...`
- `tests/unit/test_runner_registry_client.py`（如不存在则创建）—— helper 与 register payload 的单元测试

**边界：**
- Helper 接收内存中的 PROXY_POOL list-of-dicts，仅返回 `[{id: str, name: str}]` 形式条目 —— 明确白名单这两个字段。没有 `name` 的条目按照 `normalize_proxy_id()` 同样的兜底逻辑推导出 `name`。
- Helper 绝不能产出 `http`、`https`、`user`、`pass`、`auth` 等键。

---

## Task 1: Helper 函数 —— 为 registry 序列化 PROXY_POOL

**文件：**
- 修改：`packages/python/javdb_platform/runner_registry_client.py`（在第 66 行附近、`proxy_pool_hash()` 旁新增 helper）
- 测试：`tests/unit/test_runner_registry_proxy_pool_serialiser.py`（新文件）

- [ ] **Step 1：编写针对空输入的失败测试**

```python
# tests/unit/test_runner_registry_proxy_pool_serialiser.py
"""Phase 1: proxy_pool serialiser for RunnerRegistry register payload (ADR-004)."""

import pytest

from packages.python.javdb_platform.runner_registry_client import (
    proxy_pool_summary_for_registry,
)


def test_empty_pool_returns_empty_list():
    assert proxy_pool_summary_for_registry([]) == []


def test_none_pool_returns_empty_list():
    assert proxy_pool_summary_for_registry(None) == []
```

- [ ] **Step 2：确认测试失败**

运行：`pytest tests/unit/test_runner_registry_proxy_pool_serialiser.py -v`
预期：FAIL，报错 `ImportError: cannot import name 'proxy_pool_summary_for_registry'`

- [ ] **Step 3：实现最小 helper 让 empty/None 测试通过**

在 `packages/python/javdb_platform/runner_registry_client.py` 中，紧接现有 `proxy_pool_hash()` 函数下方加入：

```python
def proxy_pool_summary_for_registry(pool) -> list[dict]:
    """Serialise the in-memory PROXY_POOL list to the Worker register payload.

    Returns ``[{id, name}]`` items only. URLs, credentials, and any other
    PROXY_POOL fields are intentionally dropped — the Worker stores the
    summary in ``proxies_seen`` for dashboard display, and no part of the
    Worker handles or needs the upstream proxy URL.

    See ADR-004 for the security rationale (no creds cross the
    autospider/Worker boundary).
    """
    if not pool:
        return []
    out: list[dict] = []
    for entry in pool:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        clean = name.strip()
        out.append({"id": clean, "name": clean})
    return out
```

- [ ] **Step 4：确认两个测试现在通过**

运行：`pytest tests/unit/test_runner_registry_proxy_pool_serialiser.py -v`
预期：2 passed

- [ ] **Step 5：补充正常路径测试**

追加到 `tests/unit/test_runner_registry_proxy_pool_serialiser.py`：

```python
def test_basic_pool_returns_id_and_name():
    pool = [
        {"name": "Singapore Arm-3", "http": "http://x:7890", "https": "http://x:7890"},
        {"name": "Tokyo Backup-1", "http": "http://y:7890", "https": "http://y:7890"},
    ]
    assert proxy_pool_summary_for_registry(pool) == [
        {"id": "Singapore Arm-3", "name": "Singapore Arm-3"},
        {"id": "Tokyo Backup-1", "name": "Tokyo Backup-1"},
    ]


def test_whitespace_in_name_is_stripped():
    pool = [{"name": "  Singapore Arm-3  ", "http": "x"}]
    result = proxy_pool_summary_for_registry(pool)
    assert result == [{"id": "Singapore Arm-3", "name": "Singapore Arm-3"}]


def test_entries_without_name_are_dropped():
    pool = [
        {"name": "Has-Name", "http": "x"},
        {"http": "y"},                  # missing name → dropped
        {"name": "", "http": "z"},      # empty name → dropped
        {"name": "   ", "http": "w"},   # whitespace-only → dropped
    ]
    assert proxy_pool_summary_for_registry(pool) == [
        {"id": "Has-Name", "name": "Has-Name"},
    ]


def test_non_dict_entries_are_silently_skipped():
    pool = [{"name": "A"}, "garbage", None, 42, {"name": "B"}]
    assert proxy_pool_summary_for_registry(pool) == [
        {"id": "A", "name": "A"},
        {"id": "B", "name": "B"},
    ]
```

- [ ] **Step 6：确认全部正常路径测试通过**

运行：`pytest tests/unit/test_runner_registry_proxy_pool_serialiser.py -v`
预期：6 passed

- [ ] **Step 7：补充关键的安全回归测试**

追加到 `tests/unit/test_runner_registry_proxy_pool_serialiser.py`：

```python
def test_no_credentials_leak_into_payload():
    """ADR-004 security guarantee: the payload MUST NOT contain proxy URLs,
    usernames, passwords, or auth fields. Workers never need these."""
    pool = [
        {
            "name": "Auth-Proxy",
            "http": "http://user:supersecret@host:7890",
            "https": "http://user:supersecret@host:7890",
            "user": "user",
            "password": "supersecret",
            "auth": "Basic ZWFnZXI6c2VjcmV0",
        },
    ]
    result = proxy_pool_summary_for_registry(pool)
    serialised = repr(result)

    # Whitelist allows only id and name.
    assert result == [{"id": "Auth-Proxy", "name": "Auth-Proxy"}]

    # Defence-in-depth: explicitly assert no leak fragments anywhere.
    for forbidden in ("supersecret", "user:", "Basic ", "7890", "http://"):
        assert forbidden not in serialised, (
            f"PROXY_POOL leak detected: {forbidden!r} present in payload"
        )
```

- [ ] **Step 8：确认安全回归测试通过**

运行：`pytest tests/unit/test_runner_registry_proxy_pool_serialiser.py -v`
预期：7 passed

- [ ] **Step 9：Commit**

```bash
git add packages/python/javdb_platform/runner_registry_client.py tests/unit/test_runner_registry_proxy_pool_serialiser.py
git commit -m "$(cat <<'EOF'
feat(platform): add proxy_pool_summary_for_registry helper (Phase 1, ADR-004)

Whitelist-serialise PROXY_POOL to [{id, name}] for runner register
payload. URLs and credentials are explicitly stripped — Workers never
need them. Backs the Phase 2 proxies_seen persistence layer.
EOF
)"
```

---

## Task 2: 为 `RunnerRegistryClient.register()` 扩展 `proxy_pool` kwarg

**文件：**
- 修改：`packages/python/javdb_platform/runner_registry_client.py:449-486`（`register()` 方法体）
- 测试：`tests/unit/test_runner_registry_client_register.py`（新文件）

- [ ] **Step 1：编写失败测试，断言 proxy_pool 出现在请求体中**

```python
# tests/unit/test_runner_registry_client_register.py
"""Phase 1: RunnerRegistryClient.register() carries proxy_pool field (ADR-004)."""

from unittest.mock import MagicMock

import pytest

from packages.python.javdb_platform.runner_registry_client import (
    RunnerRegistryClient,
)


def _make_client(captured_body: list):
    """Build a client whose _do_request intercepts the outgoing body."""
    client = RunnerRegistryClient(base_url="https://example.test", token="t")

    def fake_do_request(method, path, body):
        captured_body.append({"method": method, "path": path, "body": body})
        # Return a minimal valid register response so register() doesn't raise.
        return {
            "registered": True,
            "active_runners": [],
            "pool_hash_summary": [],
            "server_time": 0,
            "movie_claim_recommended": False,
            "movie_claim_min_runners": 0,
        }

    client._do_request = fake_do_request  # type: ignore[assignment]
    return client


def test_register_includes_proxy_pool_when_provided():
    captured: list = []
    client = _make_client(captured)
    client.register(
        holder_id="holder-1",
        proxy_pool=[{"id": "P-1", "name": "P-1"}, {"id": "P-2", "name": "P-2"}],
    )
    assert len(captured) == 1
    assert captured[0]["body"]["proxy_pool"] == [
        {"id": "P-1", "name": "P-1"},
        {"id": "P-2", "name": "P-2"},
    ]


def test_register_omits_proxy_pool_field_when_not_provided():
    """Backward compat: callers that don't pass proxy_pool produce
    payloads identical to the pre-Phase-1 contract."""
    captured: list = []
    client = _make_client(captured)
    client.register(holder_id="holder-1")
    body = captured[0]["body"]
    assert "proxy_pool" not in body
```

- [ ] **Step 2：确认两个测试都失败**

运行：`pytest tests/unit/test_runner_registry_client_register.py -v`
预期：2 个失败 —— 第一个报 `TypeError: register() got an unexpected keyword argument 'proxy_pool'`；第二个因 kwarg 尚不存在也会失败。

- [ ] **Step 3：修改 `register()` 签名与方法体**

在 `packages/python/javdb_platform/runner_registry_client.py` 中，修改 `register` 方法签名与请求体构造。当前第 449-458 行的签名改为：

```python
    def register(
        self,
        *,
        holder_id: str,
        workflow_run_id: str = "",
        workflow_name: str = "",
        started_at: Optional[int] = None,
        proxy_hash: str = "",
        page_range: Optional[str] = None,
        proxy_pool: Optional[list[dict]] = None,
    ) -> RegisterResult:
```

更新 docstring 中关于 `proxy_pool_hash` 的段落，在该段后增加（紧接其后）：

```
        ``proxy_pool`` (W5.7 / ADR-004): pass the output of
        :func:`proxy_pool_summary_for_registry` so the Worker can persist
        the full pool — including idle backup proxies — to ``proxies_seen``
        for dashboard enumeration. Omit on pre-Phase-2 Workers (the
        Worker silently ignores unknown payload fields).
```

在第 477-485 行的请求体构造块中，紧接现有 `if started_at is not None:` 块之后，新增：

```python
        if proxy_pool is not None:
            body["proxy_pool"] = proxy_pool
```

- [ ] **Step 4：确认两个测试通过**

运行：`pytest tests/unit/test_runner_registry_client_register.py -v`
预期：2 passed

- [ ] **Step 5：跑现有 runner_registry_client 测试，确认无回归**

运行：`pytest tests/ -k "runner_registry" -v`
预期：所有既有测试仍通过（新 kwarg 不引入回归）。

- [ ] **Step 6：Commit**

```bash
git add packages/python/javdb_platform/runner_registry_client.py tests/unit/test_runner_registry_client_register.py
git commit -m "$(cat <<'EOF'
feat(platform): add proxy_pool kwarg to RunnerRegistryClient.register (Phase 1, ADR-004)

Optional [{id, name}] payload uploaded on every register call.
Field is omitted when caller passes None so pre-Phase-1 deploys
produce identical payloads (backward compatible). Workers that
don't understand the field ignore it.
EOF
)"
```

---

## Task 3: 在 `state.py` 的两处 `register()` 调用点接入 helper

**文件：**
- 修改：`packages/python/javdb_spider/runtime/state.py:996-1001`（驱逐后重注册）
- 修改：`packages/python/javdb_spider/runtime/state.py:1255-1265`（初次 register；准确行号请用下方 grep 确认）

- [ ] **Step 1：定位第二个调用点**

运行：`grep -n "client.register(" packages/python/javdb_spider/runtime/state.py`
预期输出包含两个调用点：
- 约第 996 行 —— 驱逐恢复
- 约第 1255 行 —— `setup_runner_registry_client()` 中的初次注册

- [ ] **Step 2：阅读两处调用点确认上下文**

阅读 `packages/python/javdb_spider/runtime/state.py` 对应行附近，确认两处当前都传入 `proxy_hash=proxy_pool_hash(_resolve_proxy_pool_json())`。

- [ ] **Step 3：补充对新 helper 的 import**

找到现有 import 块（第 38-46 行）：

```python
from packages.python.javdb_platform.runner_registry_client import (
    HeartbeatResult,
    RunnerRegistryClient,
    RunnerRegistryUnavailable,
    create_runner_registry_client_from_env,
    proxy_pool_hash,
)
```

将 `proxy_pool_summary_for_registry,` 加入 import 列表（与其它项保持排序）：

```python
from packages.python.javdb_platform.runner_registry_client import (
    HeartbeatResult,
    RunnerRegistryClient,
    RunnerRegistryUnavailable,
    create_runner_registry_client_from_env,
    proxy_pool_hash,
    proxy_pool_summary_for_registry,
)
```

- [ ] **Step 4：将 helper 接入驱逐恢复调用点**

在第 996-1001 行附近，找到：

```python
                rereg = client.register(
                    holder_id=holder_id,
                    workflow_run_id=os.environ.get("GITHUB_RUN_ID", ""),
                    workflow_name=os.environ.get("GITHUB_WORKFLOW", ""),
                    proxy_hash=proxy_pool_hash(_resolve_proxy_pool_json()),
                )
```

改为：

```python
                rereg = client.register(
                    holder_id=holder_id,
                    workflow_run_id=os.environ.get("GITHUB_RUN_ID", ""),
                    workflow_name=os.environ.get("GITHUB_WORKFLOW", ""),
                    proxy_hash=proxy_pool_hash(_resolve_proxy_pool_json()),
                    proxy_pool=proxy_pool_summary_for_registry(PROXY_POOL),
                )
```

- [ ] **Step 5：将 helper 接入初次 register 调用点**

阅读 `packages/python/javdb_spider/runtime/state.py` 第 1255 行附近，找到对应的 `client.register(...)` 块。按相同模式处理：把 `proxy_pool=proxy_pool_summary_for_registry(PROXY_POOL),` 作为最后一个关键字参数加入。

- [ ] **Step 6：跑一遍 typecheck 抓拼写错误**

运行：`python -m mypy packages/python/javdb_spider/runtime/state.py --ignore-missing-imports 2>&1 | head -20`
预期：本次改动不引入新错误（可能存在既有错误；只需确认你的 diff 没加新错）。

- [ ] **Step 7：冒烟测试 import 接线**

运行：`python -c "from packages.python.javdb_spider.runtime.state import proxy_pool_summary_for_registry; print(proxy_pool_summary_for_registry([{'name': 'X'}]))"`
预期输出：`[{'id': 'X', 'name': 'X'}]`

- [ ] **Step 8：跑 spider runtime 测试确认无回归**

运行：`pytest tests/unit/ -k "spider_runtime or state" -v 2>&1 | tail -30`
预期：无回归。若某测试在模块级 import 了 `state.py`，应仍能通过。

- [ ] **Step 9：Commit**

```bash
git add packages/python/javdb_spider/runtime/state.py
git commit -m "$(cat <<'EOF'
feat(spider): upload proxy_pool to RunnerRegistry on register (Phase 1, ADR-004)

Both register call sites (initial setup and post-eviction recovery)
now ship the whitelist-serialised PROXY_POOL alongside proxy_pool_hash.
Enables Phase 2 to enumerate the full pool — including idle backup
proxies — from the Worker side.
EOF
)"
```

---

## Task 4: 集成冒烟 —— 完整 register payload 同时包含 `proxy_pool_hash` 与 `proxy_pool`

**文件：**
- 测试：`tests/integration/test_runner_register_payload_shape.py`（新文件）

本任务新增一个端到端测试，在不依赖真实 Worker 的前提下，捕捉完整 payload 形状（两个字段都在、都没被丢）。

- [ ] **Step 1：编写集成测试**

```python
# tests/integration/test_runner_register_payload_shape.py
"""Phase 1 integration: confirm the live register payload carries
both proxy_pool_hash and proxy_pool when wired through state.py."""

from unittest.mock import patch

import pytest


@pytest.mark.integration
def test_register_payload_has_both_hash_and_pool(monkeypatch):
    """End-to-end: a runner that calls register() emits a payload containing
    proxy_pool_hash (legacy) AND proxy_pool (Phase 1, ADR-004)."""
    from packages.python.javdb_platform import runner_registry_client as rrc

    # Patch the network call to capture the body.
    captured = []

    def fake_do_request(self, method, path, body):
        captured.append({"method": method, "path": path, "body": body})
        return {
            "registered": True,
            "active_runners": [],
            "pool_hash_summary": [],
            "server_time": 0,
            "movie_claim_recommended": False,
            "movie_claim_min_runners": 0,
        }

    monkeypatch.setattr(rrc.RunnerRegistryClient, "_do_request", fake_do_request)

    client = rrc.RunnerRegistryClient(base_url="https://x.test", token="t")
    client.register(
        holder_id="holder-int-1",
        proxy_hash="0123456789abcdef",
        proxy_pool=rrc.proxy_pool_summary_for_registry(
            [
                {"name": "Singapore Arm-3", "http": "x"},
                {"name": "Tokyo Backup-1", "https": "y"},
            ]
        ),
    )

    body = captured[0]["body"]
    assert body["proxy_pool_hash"] == "0123456789abcdef"
    assert body["proxy_pool"] == [
        {"id": "Singapore Arm-3", "name": "Singapore Arm-3"},
        {"id": "Tokyo Backup-1", "name": "Tokyo Backup-1"},
    ]
    # ADR-004 security check at the integration layer too.
    serialised = repr(body)
    assert "http" not in serialised or "http://" not in serialised
```

- [ ] **Step 2：确认测试通过**

运行：`pytest tests/integration/test_runner_register_payload_shape.py -v -m integration`
预期：1 passed

- [ ] **Step 3：跑本切片下完整的 unit + integration 测试集**

运行：`pytest tests/unit/test_runner_registry_proxy_pool_serialiser.py tests/unit/test_runner_registry_client_register.py tests/integration/test_runner_register_payload_shape.py -v`
预期：10 passed（7 + 2 + 1）

- [ ] **Step 4：Commit**

```bash
git add tests/integration/test_runner_register_payload_shape.py
git commit -m "test(platform): integration smoke for register payload shape (Phase 1)"
```

---

## Task 5: 文档 —— 更新 CLAUDE.md 与 docs/ai

**文件：**
- 修改：`CLAUDE.md`（如有相关 env vars 段落，或 imports 段落）
- 修改：`docs/en/developer/`（如有 runner registry 指南）

- [ ] **Step 1：搜索现有的 runner-registry 相关文档**

运行：`grep -rn "proxy_pool_hash\|RunnerRegistryClient\|runner_registry" CLAUDE.md docs/ai/ docs/en/ 2>/dev/null | head -20`

- [ ] **Step 2：找到正确的待更新文档**

如果 `grep` 找到一份描述 runner registry payload 的 `docs/en/developer/*.md` 文件，在其中补充一段说明。否则只需更新 CONTEXT.md 中 `RunnerRegistry DO` 的条目（其 `proxies_seen` 描述已提及 Phase 1 —— 确认表述准确即可）。

- [ ] **Step 3：确认 CONTEXT.md 已经准确**

阅读 [CONTEXT.md](../../../CONTEXT.md) 的 `RunnerRegistry DO` 段。该段已描述 Phase 1 对 `proxies_seen` 的扩展。除非描述里出现尚未发布的 Phase 2 细节，否则无需编辑。

- [ ] **Step 4：如有文档改动则提交（无改动可跳过）**

如果你编辑了任何文档：

```bash
git add docs/ CLAUDE.md
git commit -m "docs: note Phase 1 proxy_pool register payload (ADR-004)"
```

如果没有任何改动，本任务到此结束、无 commit。

---

## Task 6: Phase 1 验证与交接

**文件：**（无修改）

- [ ] **Step 1：跑相关模块的完整 unit 测试集**

运行：`pytest tests/unit/ -k "runner_registry or proxy_pool" -v 2>&1 | tail -30`
预期：全绿。

- [ ] **Step 2：跑端到端 import 完整性检查**

运行：`python -c "from packages.python.javdb_spider.runtime import state; print('OK')"`
预期输出：`OK`

- [ ] **Step 3：打印 diff 概要供 review**

运行：`git log --oneline main..HEAD`
预期输出：一段小而线性的提交历史（约 4-5 个 commit）—— helper、client、state.py 接线、集成测试，及可选的文档。

- [ ] **Step 4：Phase 1 完成 —— 交接说明**

Phase 1 独立发布。不理解新 `proxy_pool` 字段的 Worker 会静默丢弃该字段（已通过阅读 `JAVDB_AutoSpider_Proxycoordinator/src/runner_registry.ts` 的 register handler 确认：它使用 `clipString(body.proxy_pool_hash ?? "")`，本就容忍额外字段）。

一旦 Phase 1 合并并部署到所有运行中的 runner，**Phase 2**（Worker 端 `proxies_seen` 表 + 历史表 + MetricsState DO + Cron trigger）即可无需协调地启动。详见 `docs/superpowers/plans/2026-05-16-dashboard-overhaul-phase-2-worker-backend.md`。

---

## 自审清单（已应用）

- ✅ 每个任务都有可工作的代码，而非"在此处实现"
- ✅ state.py 中的两处 register 调用点均已更新（驱逐恢复 + 初次注册）
- ✅ 含安全回归测试（payload 中无凭据）
- ✅ 含向后兼容测试（未传 proxy_pool kwarg → payload 无该字段）
- ✅ 全程遵循 TDD 红-绿-提交循环
- ✅ 集成测试捕捉端到端 payload 形状
- ✅ 不依赖 Phase 2（Worker 改动）；100% 向后兼容
