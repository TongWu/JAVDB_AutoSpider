# ADR-008: 前端 Phase 1 —— 后端前置工作

**状态**: 已接受 (Accepted) —— 2026-05-16 完成（通过 #33065718 合入）
**日期**: 2026-05-16
**决策者**: 前端重写工作线（为支撑新仓库 `javdb-autospider-web`）
**相关**: 设计规范见 `docs/superpowers/specs/2026-05-16-frontend-rewrite-design.md`（§4.2、§8.2、§8.4 Phase 1 列、§11 步骤 1–2）；后续由独立前端仓库中的 Phase 2 前端实现接续

> **格式说明:** 本 ADR 原本是按"分步实施计划"写的，后按仓库惯例（设计记录归档到 ADR 空间）迁移至此。决策上下文已凝练在下方的 **目标 / 架构 / 技术栈** 前言中；其余部分保留了原计划的执行清单。
>
> **面向 AI 工作者（历史记录）:** 必备子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实施本计划。各步骤使用 checkbox（`- [ ]`）语法跟踪进度。

**目标:** 落地主仓库（`JAVDB_AutoSpider_CICD`）支撑新前端仓库（`javdb-autospider-web`）所需的全部内容 —— API 服务的 Dockerfile、BE 镜像与 OpenAPI schema 的发布 workflow、10 个新增 Phase 1 endpoint、Sessions endpoint 依赖的 rollback 库重构，以及收紧 Pydantic 响应模型让 OpenAPI 驱动的 TS 类型变得真正有用。

**架构:** 新增独立的 API Dockerfile（现有的 `docker/Dockerfile` 面向 cron/spider）以及两个用于发布的 GH workflow。将 10 个新 endpoint 实现为 `apps/api/routers/` 下的一组新 router（capabilities、test_mode、onboarding、system_state、sessions）。把 `apps/cli/rollback.py` 的逻辑抽到 `packages/python/javdb_platform/rollback/core.py` 这个可调用的库中，让新的 Sessions endpoint 与 CLI 共用同一条代码路径。所有 KV state（onboarded 标志、已忽略的提示、偏好设置）都存放在 `reports/operations.db` 中新建的 `system_state` 表里。

**技术栈:** Python 3.11、FastAPI、Pydantic v2、pytest + `fastapi.testclient.TestClient`、SQLite（沿用现有仓库模式）、GitHub Actions workflow、GHCR 用于镜像发布。

**规范:** [docs/superpowers/specs/2026-05-16-frontend-rewrite-design.md](../specs/2026-05-16-frontend-rewrite-design.md) —— §4.2、§8.2、§8.4（Phase 1 列）、§11 步骤 1-2。

---

## 文件地图

**新增文件（主仓库）:**

| 路径 | 职责 |
|------|---------------|
| `docker/Dockerfile.api` | FastAPI 服务的多阶段 Dockerfile（与现有面向 cron 的 `docker/Dockerfile` 分开）。 |
| `.github/workflows/publish-api-image.yml` | 每次 push 到 main 时构建 + 推送 BE 镜像到 GHCR。 |
| `.github/workflows/publish-openapi.yml` | 把 `/openapi.json` dump 到 `docs/api/openapi.json` + GH Release artifact。 |
| `apps/api/routers/capabilities.py` | `GET /api/capabilities` 路由 —— 供 FE 使用的发现 endpoint。 |
| `apps/api/routers/test_mode.py` | `POST /api/test/reset` 路由 —— 受 `TEST_MODE=1` 门控，仅 E2E 使用。 |
| `apps/api/routers/onboarding.py` | `GET /api/onboarding/status`、`POST /api/onboarding/test`、`POST /api/onboarding/complete`、`POST /api/onboarding/dismiss-hint`。 |
| `apps/api/routers/system_state.py` | `GET /api/system/state`、`PUT /api/system/state` —— 通用 KV。 |
| `apps/api/routers/sessions.py` | `GET /api/sessions`、`GET /api/sessions/{id}`、`POST /api/sessions/{id}/rollback`、`POST /api/sessions/{id}/commit`。 |
| `apps/api/schemas/capabilities_payloads.py` | capabilities + onboarding + system_state + sessions endpoint 的 Pydantic 模型。 |
| `packages/python/javdb_platform/db_layer/system_state_repo.py` | 针对 `system_state` 表的 KV repo。 |
| `packages/python/javdb_platform/db_layer/sessions_repo.py` | `ReportSessions` 的列表/详情 repo。 |
| `packages/python/javdb_platform/rollback/__init__.py` | 库包标记。 |
| `packages/python/javdb_platform/rollback/core.py` | Rollback 核心逻辑（从 `apps/cli/rollback.py` 抽出）。 |
| `packages/python/javdb_migrations/0042_system_state_table.sql` | 新增 `system_state` 表的迁移脚本。 |
| `tests/integration/test_capabilities_endpoint.py` | capabilities 测试。 |
| `tests/integration/test_test_mode_reset.py` | `/api/test/reset` 门控测试。 |
| `tests/integration/test_onboarding_endpoints.py` | onboarding endpoint 测试。 |
| `tests/integration/test_system_state_endpoints.py` | KV 测试。 |
| `tests/integration/test_sessions_endpoints.py` | sessions endpoint 测试。 |
| `tests/unit/test_rollback_core_library.py` | 抽出的 rollback 库的测试。 |

**修改文件:**

| 路径 | 原因 |
|------|-----|
| `apps/api/services/runtime.py` | 注册 5 个新 router；接入 `TEST_MODE` 门控；新增 capabilities-version 常量。 |
| `apps/api/schemas/payloads.py` | 收紧 `tasks/*`、`auth/*`、`config/*`、`explore/*` 的响应模型。 |
| `apps/cli/rollback.py` | 剥离核心逻辑；变成围绕 `packages/python/javdb_platform/rollback/core.py` 的薄 CLI 包装。 |
| `apps/cli/commit_session.py` | 同样形态 —— 库函数之上的薄 CLI。 |
| `packages/python/javdb_platform/db_layer/__init__.py` | 重新导出新 repo。 |
| `docs/en/developer/api-reference.md` + `docs/zh/developer/api-reference.md` | 记录 10 个新 endpoint。 |

---

## 横向通用的测试模式

每个 endpoint 测试都遵循下面的形态（FastAPI 的 `TestClient` + 现成的 auth helper，可参考 `tests/integration/test_spider_gateway.py:280`）:

```python
import pytest
from fastapi.testclient import TestClient
from apps.api.services.runtime import app, _jwt_encode

@pytest.fixture
def admin_client():
    token = _jwt_encode({"sub": "admin", "role": "admin", "typ": "access"}, 3600)
    csrf = "test-csrf-value"
    client = TestClient(app, cookies={"csrf_token": csrf})
    client.headers.update({
        "Authorization": f"Bearer {token}",
        "X-CSRF-Token": csrf,
    })
    return client

@pytest.fixture
def readonly_client():
    token = _jwt_encode({"sub": "viewer", "role": "readonly", "typ": "access"}, 3600)
    csrf = "test-csrf-value"
    client = TestClient(app, cookies={"csrf_token": csrf})
    client.headers.update({
        "Authorization": f"Bearer {token}",
        "X-CSRF-Token": csrf,
    })
    return client
```

Task 4 完成后把这个 fixture 移到 `tests/integration/conftest.py`，让后续任务复用（Task 4 内联使用；Task 5+ 改用 fixture）。

---

## Task 1: 创建 API 专用 Dockerfile

现有 `docker/Dockerfile` 跑的是 `cron -f`，是为 spider/pipeline 量身打造的。FastAPI 服务需要一个独立镜像，跑 `uvicorn` 并暴露 8100 端口。

**文件:**

- 创建: `docker/Dockerfile.api`

- [ ] **Step 1: 编写 Dockerfile**

```dockerfile
# ============================================================
# Stage 1: Build Rust extension wheel (shared with main Dockerfile)
# ============================================================
FROM python:3.11-slim AS rust-builder

RUN apt-get update && apt-get install -y \
    curl build-essential pkg-config libssl-dev \
    && rm -rf /var/lib/apt/lists/*

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"
RUN pip install --no-cache-dir maturin[patchelf]

COPY packages/rust/javdb_rust_core/ /build/rust_core/
RUN cd /build/rust_core && maturin build --release --out /wheels

# ============================================================
# Stage 2: Runtime — uvicorn-based FastAPI server
# ============================================================
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    git tzdata curl \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir "uvicorn[standard]>=0.30"

COPY --from=rust-builder /wheels/*.whl /tmp/
RUN pip install /tmp/*.whl && rm -f /tmp/*.whl

# Application source — same as main Dockerfile but no cron config.
COPY apps/ ./apps/
COPY packages/ ./packages/
COPY api/ ./api/
COPY legacy/ ./legacy/
COPY migration/ ./migration/
COPY utils/ ./utils/
COPY scripts/ ./scripts/
COPY compat.py .
COPY config.py.example .
COPY pipeline.py .

RUN useradd -m -u 10001 -s /bin/bash spider \
    && mkdir -p /app/logs /app/reports \
    && chown -R spider:spider /app

USER spider

EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8100/api/health || exit 1

CMD ["uvicorn", "apps.api.server:app", "--host", "0.0.0.0", "--port", "8100", "--workers", "2"]
```

- [ ] **Step 2: 本地构建验证**

执行:

```bash
docker build -f docker/Dockerfile.api -t javdb-autospider-api:dev .
```

预期: 构建成功。镜像体积大约 700-900 MB。

- [ ] **Step 3: 容器烟雾测试**

执行:

```bash
docker run --rm -d --name jas-api-smoke -p 18100:8100 \
    -e STORAGE_BACKEND=sqlite \
    -e API_SECRET_KEY=test-secret-please-change \
    javdb-autospider-api:dev
sleep 4
curl -sf http://127.0.0.1:18100/api/health
docker rm -f jas-api-smoke
```

预期: `curl` 打印 health JSON（如 `{"status":"ok","rust_core_available":true,...}`）并以 0 退出。

- [ ] **Step 4: 必要时在 `.dockerignore` 中添加构建上下文条目**

读 `.dockerignore`；如果已排除 `reports/`、`logs/`、`node_modules/`、`.git/`，则无需变更。否则补上这些条目。

- [ ] **Step 5: 提交**

```bash
git add docker/Dockerfile.api .dockerignore
git commit -m "build(docker): add API-service Dockerfile separate from cron image"
```

---

## Task 2: `publish-api-image.yml` workflow

在每次 push 到 `main` 时构建 + 推送 BE 镜像到 GHCR。

**文件:**

- 创建: `.github/workflows/publish-api-image.yml`

- [ ] **Step 1: 编写 workflow**

```yaml
name: Publish API image

on:
  push:
    branches: [main]
    paths:
      - 'docker/Dockerfile.api'
      - 'requirements.txt'
      - 'apps/**'
      - 'packages/**'
      - 'api/**'
      - 'compat.py'
      - 'config.py.example'
      - 'pipeline.py'
  workflow_dispatch:

permissions:
  contents: read
  packages: write

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository_owner }}/javdb-autospider-api

jobs:
  build-and-push:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Set up Buildx
        uses: docker/setup-buildx-action@v3

      - name: Compute tags
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
          tags: |
            type=raw,value=latest,enable={{is_default_branch}}
            type=sha,prefix=sha-
            type=ref,event=branch

      - name: Build and push
        uses: docker/build-push-action@v6
        with:
          context: .
          file: docker/Dockerfile.api
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

- [ ] **Step 2: 校验 YAML 语法**

执行:

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/publish-api-image.yml'))"
```

预期: 无任何输出（表示成功）。任何报错都说明 YAML 有问题。

- [ ] **Step 3: 如果装了 `actionlint`，跑一遍 lint**

执行（未安装则跳过）:

```bash
which actionlint && actionlint .github/workflows/publish-api-image.yml || echo "actionlint not installed; skipping"
```

预期: 无错误，或打印 "actionlint not installed; skipping"。

- [ ] **Step 4: 确认 workflow 出现在 `gh workflow list` 中**

执行（未登录 gh 则跳过）:

```bash
gh workflow list 2>/dev/null | grep -i "publish api image" || echo "not yet visible (push to register)"
```

预期: 要么 workflow 已显示（若此前已 push），要么打印兜底信息。

- [ ] **Step 5: 提交**

```bash
git add .github/workflows/publish-api-image.yml
git commit -m "ci(workflows): publish API Docker image to GHCR on main push"
```

---

## Task 3: `publish-openapi.yml` workflow

从 FastAPI app dump `/openapi.json`，作为 Release artifact 发布，并提交到 `docs/api/openapi.json`，供新前端仓库通过 `openapi-typescript` 消费。

**文件:**

- 创建: `.github/workflows/publish-openapi.yml`
- 创建: `scripts/dump_openapi.py`

- [ ] **Step 1: 编写 dumper 脚本**

```python
# scripts/dump_openapi.py
"""Dump the FastAPI app's OpenAPI schema to docs/api/openapi.json."""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Use the production app exactly as it runs.
from apps.api.services.runtime import app  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "docs" / "api" / "openapi.json"


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    schema = app.openapi()
    OUT.write_text(json.dumps(schema, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 编写 workflow**

```yaml
name: Publish OpenAPI schema

on:
  push:
    branches: [main]
    paths:
      - 'apps/api/**'
      - 'apps/cli/**'
      - 'packages/python/**'
      - 'scripts/dump_openapi.py'
  workflow_dispatch:

permissions:
  contents: write

jobs:
  dump-and-publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          token: ${{ secrets.GITHUB_TOKEN }}

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: pip

      - name: Install deps
        run: pip install -r requirements.txt

      - name: Generate openapi.json
        env:
          STORAGE_BACKEND: sqlite
          API_SECRET_KEY: dump-only-secret
        run: python scripts/dump_openapi.py

      - name: Commit if changed
        run: |
          if git diff --quiet docs/api/openapi.json; then
            echo "No schema changes; skipping commit"
            exit 0
          fi
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add docs/api/openapi.json
          git commit -m "docs(api): regenerate openapi.json [skip ci]"
          git push

      - name: Upload as artifact
        uses: actions/upload-artifact@v4
        with:
          name: openapi-schema
          path: docs/api/openapi.json
          retention-days: 30
```

- [ ] **Step 3: 本地跑一遍 dumper，验证产出合法 JSON**

执行:

```bash
STORAGE_BACKEND=sqlite API_SECRET_KEY=dump-only-secret python3 scripts/dump_openapi.py
python3 -c "import json; json.load(open('docs/api/openapi.json')); print('valid json,', len(json.load(open('docs/api/openapi.json'))['paths']), 'paths')"
```

预期: 打印 `wrote ... bytes`，然后 `valid json, N paths`，其中 N 是当前路由总数（今天大约 ~25，随 Plan A 新增 endpoint 会增长）。

- [ ] **Step 4: 确认 workflow YAML 合法**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/publish-openapi.yml'))"
```

预期: 无输出。

- [ ] **Step 5: 提交**

```bash
git add scripts/dump_openapi.py .github/workflows/publish-openapi.yml docs/api/openapi.json
git commit -m "ci(workflows): publish openapi.json on main push + manual dumper"
```

---

## Task 4: Capabilities endpoint

`GET /api/capabilities` 返回规范 §8.2 中描述的发现 payload。

**文件:**

- 创建: `apps/api/schemas/capabilities_payloads.py`
- 创建: `apps/api/routers/capabilities.py`
- 修改: `apps/api/services/runtime.py`（注册 router）
- 创建: `tests/integration/conftest.py`
- 创建: `tests/integration/test_capabilities_endpoint.py`

- [ ] **Step 1: 编写会失败的测试（含共享 fixture）**

```python
# tests/integration/conftest.py
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client():
    from apps.api.services.runtime import app, _jwt_encode
    token = _jwt_encode({"sub": "admin", "role": "admin", "typ": "access"}, 3600)
    csrf = "test-csrf-value"
    client = TestClient(app, cookies={"csrf_token": csrf})
    client.headers.update({
        "Authorization": f"Bearer {token}",
        "X-CSRF-Token": csrf,
    })
    return client


@pytest.fixture
def readonly_client():
    from apps.api.services.runtime import app, _jwt_encode
    token = _jwt_encode({"sub": "viewer", "role": "readonly", "typ": "access"}, 3600)
    csrf = "test-csrf-value"
    client = TestClient(app, cookies={"csrf_token": csrf})
    client.headers.update({
        "Authorization": f"Bearer {token}",
        "X-CSRF-Token": csrf,
    })
    return client


@pytest.fixture
def anon_client():
    from apps.api.services.runtime import app
    return TestClient(app)
```

```python
# tests/integration/test_capabilities_endpoint.py
def test_capabilities_returns_locked_shape(admin_client):
    r = admin_client.get("/api/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == "2.0.0"
    assert body["ingestion_mode"] in {"local", "github", "dual"}
    assert body["gh_actions"]["tier"] in {"none", "monitor", "edit", "admin"}
    assert "repo" in body["gh_actions"]
    assert "token_configured" in body["gh_actions"]
    assert body["storage_backend"] in {"sqlite", "d1", "dual"}
    assert isinstance(body["features"], dict)
    for key in ("pikpak", "rclone", "smtp", "proxy_pool", "javdb_login", "proxy_preview"):
        assert key in body["features"]
        assert isinstance(body["features"][key], bool)
    assert body["deployment"] in {"colocated", "split", "unknown"}
    assert "build" in body
    assert "frontend_version" in body["build"]
    assert "backend_version" in body["build"]
    assert "git_sha" in body["build"]


def test_capabilities_readable_by_readonly(readonly_client):
    r = readonly_client.get("/api/capabilities")
    assert r.status_code == 200


def test_capabilities_requires_auth(anon_client):
    r = anon_client.get("/api/capabilities")
    assert r.status_code == 401
```

- [ ] **Step 2: 跑测试 —— 预期失败**

```bash
pytest tests/integration/test_capabilities_endpoint.py -v
```

预期: 3 个失败，错误为 `404`（endpoint 不存在）。

- [ ] **Step 3: 实现 schema + router**

```python
# apps/api/schemas/capabilities_payloads.py
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class GhActions(BaseModel):
    tier: Literal["none", "monitor", "edit", "admin"]
    repo: str | None
    token_configured: bool


class Features(BaseModel):
    pikpak: bool
    rclone: bool
    smtp: bool
    proxy_pool: bool
    javdb_login: bool
    proxy_preview: bool


class Build(BaseModel):
    frontend_version: str | None = None
    backend_version: str
    git_sha: str


class CapabilitiesResponse(BaseModel):
    version: str = Field(default="2.0.0", description="Capabilities schema version")
    ingestion_mode: Literal["local", "github", "dual"]
    gh_actions: GhActions
    storage_backend: Literal["sqlite", "d1", "dual"]
    features: Features
    deployment: Literal["colocated", "split", "unknown"]
    build: Build
```

```python
# apps/api/routers/capabilities.py
from __future__ import annotations

import os
import subprocess
from importlib.metadata import PackageNotFoundError, version as pkg_version
from typing import cast

from fastapi import APIRouter, Depends

from apps.api.schemas.capabilities_payloads import (
    Build,
    CapabilitiesResponse,
    Features,
    GhActions,
)


def _get_git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip() or "unknown"
    except Exception:
        return "unknown"


def _backend_version() -> str:
    try:
        return pkg_version("javdb-autospider")
    except PackageNotFoundError:
        return os.getenv("BACKEND_VERSION", "0.0.0-dev")


def _bool_env(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def build_capabilities() -> CapabilitiesResponse:
    ingestion_mode = cast(
        "Literal['local', 'github', 'dual']",
        os.getenv("INGESTION_MODE", "local"),
    )
    storage_backend = cast(
        "Literal['sqlite', 'd1', 'dual']",
        os.getenv("STORAGE_BACKEND", "sqlite"),
    )
    deployment = cast(
        "Literal['colocated', 'split', 'unknown']",
        os.getenv("DEPLOYMENT", "unknown"),
    )

    return CapabilitiesResponse(
        version="2.0.0",
        ingestion_mode=ingestion_mode,
        gh_actions=GhActions(
            tier=cast(
                "Literal['none', 'monitor', 'edit', 'admin']",
                os.getenv("GH_ACTIONS_TIER", "none"),
            ),
            repo=os.getenv("GH_ACTIONS_REPO") or None,
            token_configured=bool(os.getenv("GH_ACTIONS_TOKEN")),
        ),
        storage_backend=storage_backend,
        features=Features(
            pikpak=_bool_env("FEATURE_PIKPAK"),
            rclone=_bool_env("FEATURE_RCLONE"),
            smtp=bool(os.getenv("SMTP_HOST") or os.getenv("SMTP_SERVER")),
            proxy_pool=_bool_env("PROXY_MODE_POOL", default=True),
            javdb_login=bool(os.getenv("JAVDB_USERNAME")),
            proxy_preview=True,
        ),
        deployment=deployment,
        build=Build(
            frontend_version=os.getenv("FRONTEND_VERSION"),
            backend_version=_backend_version(),
            git_sha=_get_git_sha(),
        ),
    )


# Auth dependency — reuse the existing one from runtime module.
from apps.api.infra.auth import _require_auth  # noqa: E402

router = APIRouter(prefix="/api", tags=["capabilities"])


@router.get("/capabilities", response_model=CapabilitiesResponse)
def get_capabilities(_user=Depends(_require_auth)) -> CapabilitiesResponse:
    return build_capabilities()
```

然后在 `apps/api/services/runtime.py` 中注册 router —— 修改现有的 `for router in (...)` 代码块：

```python
# In apps/api/services/runtime.py, near line 154
from apps.api.routers.capabilities import router as capabilities_router

for router in (
    system_router,
    auth_router,
    config_router,
    tasks_router,
    explore_router,
    capabilities_router,  # NEW
):
    app.include_router(router)
```

- [ ] **Step 4: 跑测试 —— 预期通过**

```bash
pytest tests/integration/test_capabilities_endpoint.py -v
```

预期: 3 个通过。

- [ ] **Step 5: 提交**

```bash
git add apps/api/schemas/capabilities_payloads.py apps/api/routers/capabilities.py apps/api/services/runtime.py tests/integration/conftest.py tests/integration/test_capabilities_endpoint.py
git commit -m "feat(api): add GET /api/capabilities discovery endpoint"
```

---

## Task 5: `POST /api/test/reset`（受 TEST_MODE 门控）

仅供测试使用的 endpoint，用于 truncate KV + sessions + history 表，便于 Playwright spec 之间互不污染。`TEST_MODE` 环境变量未设置时返回 404。

**文件:**

- 创建: `apps/api/routers/test_mode.py`
- 修改: `apps/api/services/runtime.py`（条件 router 注册）
- 创建: `tests/integration/test_test_mode_reset.py`

- [ ] **Step 1: 编写会失败的测试**

```python
# tests/integration/test_test_mode_reset.py
import os
import pytest
from fastapi.testclient import TestClient


def _reload_app():
    """Reload app after env mutation. Required because TEST_MODE is read at
    import time when wiring the conditional router."""
    import importlib
    import apps.api.services.runtime as runtime_mod
    importlib.reload(runtime_mod)
    return runtime_mod.app


def test_reset_endpoint_returns_404_when_test_mode_off(monkeypatch):
    monkeypatch.delenv("TEST_MODE", raising=False)
    app = _reload_app()
    client = TestClient(app)
    r = client.post("/api/test/reset")
    assert r.status_code == 404


def test_reset_endpoint_works_when_test_mode_on(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_MODE", "1")
    # Redirect reports/ to tmp so we don't nuke dev data.
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    app = _reload_app()
    client = TestClient(app)
    r = client.post("/api/test/reset")
    assert r.status_code == 200
    body = r.json()
    assert body == {"reset": True}
```

- [ ] **Step 2: 跑测试 —— 预期失败**

```bash
pytest tests/integration/test_test_mode_reset.py -v
```

预期: 2 个失败（路由不存在）。

- [ ] **Step 3: 实现受门控的 router**

```python
# apps/api/routers/test_mode.py
"""POST /api/test/reset — truncate operational state for E2E tests.

Registered ONLY when TEST_MODE=1 at server boot. Otherwise the route does
not exist (returns 404).
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/api/test", tags=["test-mode"])


def _reports_root() -> Path:
    return Path(os.getenv("REPORTS_DIR", "reports"))


_TRUNCATE_TARGETS = {
    "history.db": ["MovieHistory", "TorrentHistory"],
    "reports.db": ["ReportSessions", "ReportMovies", "ReportTorrents", "Stats"],
    "operations.db": ["RcloneInventory", "DedupRecords", "PikpakHistory", "system_state"],
}


@router.post("/reset")
def reset_state() -> dict[str, bool]:
    root = _reports_root()
    for db_name, tables in _TRUNCATE_TARGETS.items():
        db_path = root / db_name
        if not db_path.exists():
            continue
        with sqlite3.connect(str(db_path)) as conn:
            for table in tables:
                # Use TRY to avoid hard-failing on a table that doesn't
                # exist yet (e.g. system_state on a pre-migration DB).
                try:
                    conn.execute(f"DELETE FROM {table}")
                except sqlite3.OperationalError:
                    pass
            conn.commit()
    return {"reset": True}
```

然后在 `apps/api/services/runtime.py` 中，**在**主 router 循环**之后**追加条件注册：

```python
# In apps/api/services/runtime.py, after the main include_router block
if os.getenv("TEST_MODE") == "1":
    from apps.api.routers.test_mode import router as test_mode_router
    app.include_router(test_mode_router)
```

如顶层尚未导入 `os`，需补上 `import os`。

- [ ] **Step 4: 跑测试 —— 预期通过**

```bash
pytest tests/integration/test_test_mode_reset.py -v
```

预期: 2 个通过。

- [ ] **Step 5: 提交**

```bash
git add apps/api/routers/test_mode.py apps/api/services/runtime.py tests/integration/test_test_mode_reset.py
git commit -m "feat(api): add gated POST /api/test/reset for E2E isolation"
```

---

## Task 6: `system_state` 表迁移 + repo

4 个 onboarding endpoint 加上 `/api/system/state` 都需要一个通用 KV。先把表和 repo 落地，再落 endpoint。

**文件:**

- 创建: `packages/python/javdb_migrations/0042_system_state_table.sql`
- 创建: `packages/python/javdb_platform/db_layer/system_state_repo.py`
- 修改: `packages/python/javdb_platform/db_layer/__init__.py`
- 创建: `tests/unit/test_system_state_repo.py`

- [ ] **Step 1: 编写会失败的测试**

```python
# tests/unit/test_system_state_repo.py
import json
import sqlite3
from pathlib import Path

import pytest

from packages.python.javdb_platform.db_layer.system_state_repo import SystemStateRepo


@pytest.fixture
def repo(tmp_path: Path) -> SystemStateRepo:
    db = tmp_path / "operations.db"
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "CREATE TABLE system_state (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        conn.commit()
    conn = sqlite3.connect(str(db))
    return SystemStateRepo(conn)


def test_get_missing_returns_default(repo):
    assert repo.get("does-not-exist") is None
    assert repo.get("does-not-exist", default="fallback") == "fallback"


def test_put_then_get_roundtrip(repo):
    repo.put("onboarded", "true")
    assert repo.get("onboarded") == "true"


def test_put_json_helper(repo):
    repo.put_json("dismissed_hints", ["smtp", "pikpak"])
    assert repo.get_json("dismissed_hints") == ["smtp", "pikpak"]


def test_put_overwrites(repo):
    repo.put("k", "v1")
    repo.put("k", "v2")
    assert repo.get("k") == "v2"


def test_delete(repo):
    repo.put("k", "v")
    repo.delete("k")
    assert repo.get("k") is None
```

- [ ] **Step 2: 跑测试 —— 预期失败（模块缺失）**

```bash
pytest tests/unit/test_system_state_repo.py -v
```

预期: `system_state_repo` ImportError。

- [ ] **Step 3: 实现迁移 + repo**

```sql
-- packages/python/javdb_migrations/0042_system_state_table.sql
CREATE TABLE IF NOT EXISTS system_state (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_system_state_updated_at ON system_state(updated_at);
```

```python
# packages/python/javdb_platform/db_layer/system_state_repo.py
from __future__ import annotations

import json
import sqlite3
from typing import Any


class SystemStateRepo:
    """Generic KV against the `system_state` table in operations.db.

    Used by:
      - onboarded flag
      - dismissed_hints array
      - any other client-side preference that needs to survive between
        sessions / multiple devices.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, key: str, *, default: str | None = None) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM system_state WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else default

    def put(self, key: str, value: str) -> None:
        self._conn.execute(
            """
            INSERT INTO system_state (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                            updated_at = datetime('now')
            """,
            (key, value),
        )
        self._conn.commit()

    def delete(self, key: str) -> None:
        self._conn.execute("DELETE FROM system_state WHERE key = ?", (key,))
        self._conn.commit()

    def get_json(self, key: str, *, default: Any = None) -> Any:
        raw = self.get(key)
        return json.loads(raw) if raw is not None else default

    def put_json(self, key: str, value: Any) -> None:
        self.put(key, json.dumps(value))
```

在 `packages/python/javdb_platform/db_layer/__init__.py` 中追加：

```python
from packages.python.javdb_platform.db_layer.system_state_repo import SystemStateRepo  # noqa: F401
```

- [ ] **Step 4: 跑测试 —— 预期通过**

```bash
pytest tests/unit/test_system_state_repo.py -v
```

预期: 5 个通过。

- [ ] **Step 5: 提交**

```bash
git add packages/python/javdb_migrations/0042_system_state_table.sql \
        packages/python/javdb_platform/db_layer/system_state_repo.py \
        packages/python/javdb_platform/db_layer/__init__.py \
        tests/unit/test_system_state_repo.py
git commit -m "feat(db): add system_state KV table + SystemStateRepo"
```

---

## Task 7: `/api/system/state` GET/PUT

KV 的 Web 访问 —— admin 才能写，admin/readonly 都能读。

**文件:**

- 创建: `apps/api/routers/system_state.py`
- 修改: `apps/api/services/runtime.py`（注册）
- 修改: `apps/api/schemas/capabilities_payloads.py`（新增 KV schema）
- 创建: `tests/integration/test_system_state_endpoints.py`

- [ ] **Step 1: 编写会失败的测试**

```python
# tests/integration/test_system_state_endpoints.py
def test_get_missing_returns_null(admin_client):
    r = admin_client.get("/api/system/state", params={"key": "never-set"})
    assert r.status_code == 200
    assert r.json() == {"key": "never-set", "value": None}


def test_put_then_get_roundtrip(admin_client):
    admin_client.put("/api/system/state", json={"key": "test-key-1", "value": "hello"})
    r = admin_client.get("/api/system/state", params={"key": "test-key-1"})
    assert r.status_code == 200
    assert r.json() == {"key": "test-key-1", "value": "hello"}


def test_put_requires_admin(readonly_client):
    r = readonly_client.put(
        "/api/system/state", json={"key": "x", "value": "y"}
    )
    assert r.status_code in (401, 403)


def test_get_allowed_for_readonly(readonly_client):
    r = readonly_client.get("/api/system/state", params={"key": "x"})
    assert r.status_code == 200
```

- [ ] **Step 2: 跑测试 —— 预期失败**

```bash
pytest tests/integration/test_system_state_endpoints.py -v
```

预期: 4 个失败。

- [ ] **Step 3: 实现 schema + router**

在 `apps/api/schemas/capabilities_payloads.py` 中追加：

```python
class SystemStateGetResponse(BaseModel):
    key: str
    value: str | None


class SystemStatePutPayload(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    value: str
```

创建 `apps/api/routers/system_state.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from apps.api.schemas.capabilities_payloads import (
    SystemStateGetResponse,
    SystemStatePutPayload,
)
from apps.api.infra.auth import _require_auth, require_role
from packages.python.javdb_platform.db_layer.system_state_repo import SystemStateRepo
from packages.python.javdb_platform.db_connection import get_db, OPERATIONS_DB_PATH

router = APIRouter(prefix="/api/system", tags=["system-state"])


@router.get("/state", response_model=SystemStateGetResponse)
def get_state(key: str = Query(..., min_length=1), _user=Depends(_require_auth)) -> SystemStateGetResponse:
    with get_db(OPERATIONS_DB_PATH) as conn:
        value = SystemStateRepo(conn).get(key)
    return SystemStateGetResponse(key=key, value=value)


@router.put("/state", response_model=SystemStateGetResponse)
def put_state(payload: SystemStatePutPayload, _user=Depends(require_role("admin"))) -> SystemStateGetResponse:
    with get_db(OPERATIONS_DB_PATH) as conn:
        SystemStateRepo(conn).put(payload.key, payload.value)
    return SystemStateGetResponse(key=payload.key, value=payload.value)
```

注册 router:

```python
# apps/api/services/runtime.py
from apps.api.routers.system_state import router as system_state_router
# add to the include_router loop
```

- [ ] **Step 4: 跑测试 —— 预期通过**

```bash
pytest tests/integration/test_system_state_endpoints.py -v
```

预期: 4 个通过。

- [ ] **Step 5: 提交**

```bash
git add apps/api/routers/system_state.py apps/api/services/runtime.py apps/api/schemas/capabilities_payloads.py tests/integration/test_system_state_endpoints.py
git commit -m "feat(api): add GET/PUT /api/system/state generic KV"
```

---

## Task 8: Onboarding `GET /api/onboarding/status`

依据 KV 标志 + 配置检测，返回 `{completed, required_missing[], skippable_missing[]}`。

**文件:**

- 创建: `apps/api/routers/onboarding.py`（本任务仅实现 status endpoint）
- 修改: `apps/api/services/runtime.py`（注册）
- 修改: `apps/api/schemas/capabilities_payloads.py`（追加 onboarding 模型）
- 创建: `tests/integration/test_onboarding_endpoints.py`

- [ ] **Step 1: 编写会失败的测试**

```python
# tests/integration/test_onboarding_endpoints.py
def test_status_default_returns_required_missing(admin_client, monkeypatch):
    # Force a "nothing configured" environment.
    monkeypatch.delenv("JAVDB_USERNAME", raising=False)
    monkeypatch.delenv("QB_URL", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    r = admin_client.get("/api/onboarding/status")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["completed"], bool)
    assert isinstance(body["required_missing"], list)
    assert isinstance(body["skippable_missing"], list)
    assert "javdb_session" in body["required_missing"] or "qb" in body["required_missing"]
```

- [ ] **Step 2: 跑测试 —— 预期失败**

```bash
pytest tests/integration/test_onboarding_endpoints.py::test_status_default_returns_required_missing -v
```

预期: 404 失败。

- [ ] **Step 3: 实现 schema + router**

在 `apps/api/schemas/capabilities_payloads.py` 中追加：

```python
class OnboardingStatusResponse(BaseModel):
    completed: bool
    required_missing: list[str]
    skippable_missing: list[str]
```

创建 `apps/api/routers/onboarding.py`:

```python
from __future__ import annotations

import os
from fastapi import APIRouter, Depends

from apps.api.schemas.capabilities_payloads import OnboardingStatusResponse
from apps.api.infra.auth import _require_auth
from packages.python.javdb_platform.db_layer.system_state_repo import SystemStateRepo
from packages.python.javdb_platform.db_connection import get_db, OPERATIONS_DB_PATH


REQUIRED_COMPONENTS = ("javdb_session", "qb")
SKIPPABLE_COMPONENTS = ("smtp", "pikpak", "rclone", "proxy")


def _is_configured(component: str) -> bool:
    if component == "javdb_session":
        return bool(os.getenv("JAVDB_SESSION_COOKIE") or os.getenv("JAVDB_USERNAME"))
    if component == "qb":
        return bool(os.getenv("QB_URL"))
    if component == "smtp":
        return bool(os.getenv("SMTP_HOST") or os.getenv("SMTP_SERVER"))
    if component == "pikpak":
        return bool(os.getenv("PIKPAK_USERNAME"))
    if component == "rclone":
        return bool(os.getenv("RCLONE_REMOTE"))
    if component == "proxy":
        return bool(os.getenv("PROXY_HTTP") or os.getenv("PROXY_POOL"))
    return False


router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


def _read_onboarded() -> bool:
    with get_db(OPERATIONS_DB_PATH) as conn:
        return SystemStateRepo(conn).get("onboarded") == "true"


@router.get("/status", response_model=OnboardingStatusResponse)
def get_status(_user=Depends(_require_auth)) -> OnboardingStatusResponse:
    required_missing = [c for c in REQUIRED_COMPONENTS if not _is_configured(c)]
    skippable_missing = [c for c in SKIPPABLE_COMPONENTS if not _is_configured(c)]
    return OnboardingStatusResponse(
        completed=_read_onboarded(),
        required_missing=required_missing,
        skippable_missing=skippable_missing,
    )
```

在 `runtime.py` 中注册 router。

- [ ] **Step 4: 跑测试 —— 预期通过**

```bash
pytest tests/integration/test_onboarding_endpoints.py::test_status_default_returns_required_missing -v
```

预期: 通过。

- [ ] **Step 5: 提交**

```bash
git add apps/api/routers/onboarding.py apps/api/services/runtime.py apps/api/schemas/capabilities_payloads.py tests/integration/test_onboarding_endpoints.py
git commit -m "feat(api): add GET /api/onboarding/status"
```

---

## Task 9: Onboarding `POST /api/onboarding/test`

测试单个组件（`javdb` / `qb` / `proxy` / `smtp`）—— 返回连接结果 + 诊断信息。

**文件:**

- 修改: `apps/api/routers/onboarding.py`
- 修改: `apps/api/schemas/capabilities_payloads.py`
- 修改: `tests/integration/test_onboarding_endpoints.py`

- [ ] **Step 1: 编写会失败的测试**

```python
# Append to tests/integration/test_onboarding_endpoints.py
def test_test_javdb_returns_result(admin_client, monkeypatch):
    monkeypatch.setenv("JAVDB_SESSION_COOKIE", "stub-cookie-value")
    r = admin_client.post("/api/onboarding/test", json={"component": "javdb"})
    assert r.status_code == 200
    body = r.json()
    assert body["component"] == "javdb"
    assert isinstance(body["ok"], bool)
    assert isinstance(body["message"], str)


def test_test_unknown_component_400(admin_client):
    r = admin_client.post("/api/onboarding/test", json={"component": "nonsense"})
    assert r.status_code == 422  # pydantic Literal validation
```

- [ ] **Step 2: 跑测试 —— 预期失败**

```bash
pytest tests/integration/test_onboarding_endpoints.py -v
```

预期: 2 个新失败。

- [ ] **Step 3: 实现**

在 `apps/api/schemas/capabilities_payloads.py` 中追加：

```python
from typing import Literal


class OnboardingTestPayload(BaseModel):
    component: Literal["javdb", "qb", "proxy", "smtp"]


class OnboardingTestResponse(BaseModel):
    component: str
    ok: bool
    message: str
    details: dict | None = None
```

在 `apps/api/routers/onboarding.py` 中追加：

```python
from apps.api.schemas.capabilities_payloads import (
    OnboardingTestPayload,
    OnboardingTestResponse,
)


def _test_javdb() -> tuple[bool, str, dict | None]:
    cookie = os.getenv("JAVDB_SESSION_COOKIE")
    if not cookie:
        return False, "JAVDB_SESSION_COOKIE not set", None
    # Light validation only; full test would hit javdb.com which is rate-limited.
    return True, "cookie present", {"length": len(cookie)}


def _test_qb() -> tuple[bool, str, dict | None]:
    url = os.getenv("QB_URL")
    if not url:
        return False, "QB_URL not set", None
    try:
        import requests
        r = requests.get(f"{url.rstrip('/')}/api/v2/app/version", timeout=5, verify=False)
        if r.status_code == 200:
            return True, f"qB {r.text}", {"url": url}
        return False, f"qB returned HTTP {r.status_code}", {"url": url}
    except Exception as exc:
        return False, f"connect failed: {exc}", {"url": url}


def _test_proxy() -> tuple[bool, str, dict | None]:
    proxy = os.getenv("PROXY_HTTP")
    if not proxy:
        return False, "no proxy configured", None
    try:
        import requests
        r = requests.get("https://api.ipify.org", proxies={"http": proxy, "https": proxy}, timeout=5)
        return r.status_code == 200, f"egress IP: {r.text}", {"proxy": proxy}
    except Exception as exc:
        return False, f"proxy test failed: {exc}", {"proxy": proxy}


def _test_smtp() -> tuple[bool, str, dict | None]:
    host = os.getenv("SMTP_HOST") or os.getenv("SMTP_SERVER")
    if not host:
        return False, "SMTP_HOST not set", None
    import smtplib
    port = int(os.getenv("SMTP_PORT", "587"))
    try:
        with smtplib.SMTP(host, port, timeout=5) as smtp:
            smtp.ehlo()
        return True, f"SMTP {host}:{port} reachable", {"host": host, "port": port}
    except Exception as exc:
        return False, f"SMTP test failed: {exc}", {"host": host, "port": port}


_COMPONENT_TESTERS = {
    "javdb": _test_javdb,
    "qb": _test_qb,
    "proxy": _test_proxy,
    "smtp": _test_smtp,
}


@router.post("/test", response_model=OnboardingTestResponse)
def test_component(payload: OnboardingTestPayload, _user=Depends(_require_auth)) -> OnboardingTestResponse:
    ok, message, details = _COMPONENT_TESTERS[payload.component]()
    return OnboardingTestResponse(component=payload.component, ok=ok, message=message, details=details)
```

- [ ] **Step 4: 跑测试 —— 预期通过**

```bash
pytest tests/integration/test_onboarding_endpoints.py -v
```

预期: 全部通过。

- [ ] **Step 5: 提交**

```bash
git add apps/api/routers/onboarding.py apps/api/schemas/capabilities_payloads.py tests/integration/test_onboarding_endpoints.py
git commit -m "feat(api): add POST /api/onboarding/test for javdb/qb/proxy/smtp"
```

---

## Task 10: Onboarding `POST /api/onboarding/complete` + `dismiss-hint`

向 KV 写两次数据。仅 admin 可用。

**文件:**

- 修改: `apps/api/routers/onboarding.py`
- 修改: `apps/api/schemas/capabilities_payloads.py`
- 修改: `tests/integration/test_onboarding_endpoints.py`

- [ ] **Step 1: 编写会失败的测试**

```python
# Append to tests/integration/test_onboarding_endpoints.py
def test_complete_marks_onboarded(admin_client):
    r = admin_client.post("/api/onboarding/complete")
    assert r.status_code == 200
    status = admin_client.get("/api/onboarding/status").json()
    assert status["completed"] is True


def test_complete_requires_admin(readonly_client):
    r = readonly_client.post("/api/onboarding/complete")
    assert r.status_code in (401, 403)


def test_dismiss_hint_persists(admin_client):
    admin_client.post("/api/onboarding/dismiss-hint", json={"hint_id": "smtp"})
    state = admin_client.get("/api/system/state", params={"key": "dismissed_hints"})
    assert "smtp" in state.json()["value"]


def test_dismiss_hint_idempotent(admin_client):
    admin_client.post("/api/onboarding/dismiss-hint", json={"hint_id": "pikpak"})
    admin_client.post("/api/onboarding/dismiss-hint", json={"hint_id": "pikpak"})
    state = admin_client.get("/api/system/state", params={"key": "dismissed_hints"})
    # Should appear exactly once.
    import json
    hints = json.loads(state.json()["value"])
    assert hints.count("pikpak") == 1
```

- [ ] **Step 2: 跑测试 —— 预期失败**

```bash
pytest tests/integration/test_onboarding_endpoints.py -v
```

预期: 4 个新失败。

- [ ] **Step 3: 实现**

在 `apps/api/schemas/capabilities_payloads.py` 中追加：

```python
class DismissHintPayload(BaseModel):
    hint_id: str = Field(min_length=1, max_length=64)
```

在 `apps/api/routers/onboarding.py` 中追加：

```python
from apps.api.schemas.capabilities_payloads import DismissHintPayload
from apps.api.infra.auth import require_role


@router.post("/complete", response_model=OnboardingStatusResponse)
def mark_complete(_user=Depends(require_role("admin"))) -> OnboardingStatusResponse:
    with get_db(OPERATIONS_DB_PATH) as conn:
        SystemStateRepo(conn).put("onboarded", "true")
    return get_status(_user=_user)


@router.post("/dismiss-hint", response_model=dict)
def dismiss_hint(payload: DismissHintPayload, _user=Depends(require_role("admin"))) -> dict:
    with get_db(OPERATIONS_DB_PATH) as conn:
        repo = SystemStateRepo(conn)
        hints: list[str] = repo.get_json("dismissed_hints", default=[]) or []
        if payload.hint_id not in hints:
            hints.append(payload.hint_id)
            repo.put_json("dismissed_hints", hints)
    return {"dismissed_hints": hints}
```

- [ ] **Step 4: 跑测试 —— 预期通过**

```bash
pytest tests/integration/test_onboarding_endpoints.py -v
```

预期: 全部通过。

- [ ] **Step 5: 提交**

```bash
git add apps/api/routers/onboarding.py apps/api/schemas/capabilities_payloads.py tests/integration/test_onboarding_endpoints.py
git commit -m "feat(api): add onboarding complete + dismiss-hint endpoints"
```

---

## Task 11: 抽出 rollback 核心为可调用库

`apps/cli/rollback.py` 是 400+ 行的 CLI + 逻辑混合体。把逻辑抽到 `packages/python/javdb_platform/rollback/core.py`，让新的 Sessions endpoint 可以调用同一个函数。CLI 退化为薄薄一层 argparse 包装。

**文件:**

- 创建: `packages/python/javdb_platform/rollback/__init__.py`
- 创建: `packages/python/javdb_platform/rollback/core.py`
- 修改: `apps/cli/rollback.py`
- 创建: `tests/unit/test_rollback_core_library.py`

- [ ] **Step 1: 检查现有 CLI 结构**

阅读 `apps/cli/rollback.py`，找到核心函数（很可能是 `run_rollback()` 之类）。列出它当前的 argparse flag，以及哪些应当成为库 API 的参数。

执行:

```bash
grep -n "^def \|argparse\|add_argument" apps/cli/rollback.py | head -40
```

预期: 看到函数与 flag 清单。据此设计库 API。

- [ ] **Step 2: 编写会失败的库测试**

```python
# tests/unit/test_rollback_core_library.py
import pytest

from packages.python.javdb_platform.rollback.core import (
    RollbackPlan,
    RollbackRequest,
    plan_rollback,
)


def test_request_dataclass_has_expected_fields():
    req = RollbackRequest(
        session_id="20260516T000000.000000Z-0001-0001",
        dry_run=True,
        include_pending=True,
        restore_from_audit=False,
    )
    assert req.session_id == "20260516T000000.000000Z-0001-0001"
    assert req.dry_run is True


def test_plan_returns_actions_for_known_session(monkeypatch, tmp_path):
    # Stub the DB by pointing REPORTS_DIR to a tmp path with a seeded session.
    # Detailed seeding is in the CLI tests; here we only verify the shape.
    req = RollbackRequest(session_id="missing-session", dry_run=True, include_pending=False, restore_from_audit=False)
    with pytest.raises(LookupError):
        plan_rollback(req)
```

说明: 此测试只断言 API surface。行为测试位于现有的 CLI 测试中（`tests/integration/` 已经通过 CLI 覆盖了 rollback 流程）；库抽取**不得**改变行为，因此现有 CLI 测试就是回归套件。

- [ ] **Step 3: 跑测试 —— 预期失败（模块缺失）**

```bash
pytest tests/unit/test_rollback_core_library.py -v
```

预期: ImportError。

- [ ] **Step 4: 把逻辑抽到库里**

创建 `packages/python/javdb_platform/rollback/__init__.py`:

```python
from packages.python.javdb_platform.rollback.core import (  # noqa: F401
    RollbackPlan,
    RollbackRequest,
    RollbackResult,
    plan_rollback,
    apply_rollback,
)
```

创建 `packages/python/javdb_platform/rollback/core.py` —— 把 `apps/cli/rollback.py` 中的核心逻辑搬过来并重构为纯函数：

```python
"""Rollback library — extracted from apps/cli/rollback.py.

Public surface:
  RollbackRequest — input shape (mirrors the CLI flags 1:1).
  RollbackPlan    — what would happen (returned by plan_rollback for dry_run).
  RollbackResult  — what happened (returned by apply_rollback).
  plan_rollback(req) -> RollbackPlan
  apply_rollback(req) -> RollbackResult

The CLI becomes a thin wrapper that converts argparse args -> RollbackRequest
and calls plan_rollback / apply_rollback.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RollbackRequest:
    session_id: str | None = None
    run_id: str | None = None
    run_attempt: int | None = None
    dry_run: bool = True
    include_pending: bool = True
    restore_from_audit: bool = True
    window_minutes: int | None = None


@dataclass
class RollbackPlan:
    session_id: str
    actions: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)


@dataclass
class RollbackResult:
    session_id: str
    applied: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)


def plan_rollback(req: RollbackRequest) -> RollbackPlan:
    # === MOVE the body of the central planning function here from
    # === apps/cli/rollback.py. Keep its DB calls / queries verbatim.
    # === Raise LookupError if the session_id (or run_id+attempt) cannot
    # === be resolved to a known session.
    raise NotImplementedError("Migrate the planning loop from apps/cli/rollback.py")


def apply_rollback(req: RollbackRequest) -> RollbackResult:
    # === MOVE the body of the central apply function here.
    # === MUST call plan_rollback internally to produce the action list,
    # === then execute each action and return a RollbackResult.
    raise NotImplementedError("Migrate the apply loop from apps/cli/rollback.py")
```

**实际抽取**: 打开 `apps/cli/rollback.py`，找到 planning 函数（叫 `_compute_actions` 之类）和 apply 函数（叫 `_execute_actions` 之类），把它们搬到 `core.py`。用真实逻辑替换上面 `NotImplementedError` 的占位实现。然后在 `apps/cli/rollback.py` 中，把函数定义替换为对库的 import:

```python
# apps/cli/rollback.py (top, after existing imports)
from packages.python.javdb_platform.rollback import (
    RollbackRequest,
    plan_rollback,
    apply_rollback,
)
```

并重写 CLI 的 `main()`，让 argparse 解析后构造 `RollbackRequest` 并调用库函数，而不是再跑内联逻辑。

- [ ] **Step 5: 跑所有 rollback 测试 —— 验证无回归**

```bash
pytest tests/ -k "rollback" -v
```

预期: 所有现有 rollback 测试通过（CLI 行为未变）；新的库 shape 测试通过。

- [ ] **Step 6: 提交**

```bash
git add packages/python/javdb_platform/rollback/__init__.py \
        packages/python/javdb_platform/rollback/core.py \
        apps/cli/rollback.py \
        tests/unit/test_rollback_core_library.py
git commit -m "refactor(cli): extract rollback core into javdb_platform.rollback library"
```

---

## Task 12: `GET /api/sessions` 列表 endpoint

游标分页的 ReportSessions 列表，支持 state 过滤。

**文件:**

- 创建: `packages/python/javdb_platform/db_layer/sessions_repo.py`
- 修改: `apps/api/schemas/capabilities_payloads.py`
- 创建: `apps/api/routers/sessions.py`
- 修改: `apps/api/services/runtime.py`（注册）
- 创建: `tests/integration/test_sessions_endpoints.py`

- [ ] **Step 1: 编写会失败的测试**

```python
# tests/integration/test_sessions_endpoints.py
def test_list_returns_items_and_cursor(admin_client):
    r = admin_client.get("/api/sessions", params={"limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert "next_cursor" in body
    assert isinstance(body["items"], list)


def test_list_filter_by_state(admin_client):
    r = admin_client.get("/api/sessions", params={"state": "committed", "limit": 5})
    assert r.status_code == 200
    body = r.json()
    for item in body["items"]:
        assert item["state"] == "committed"
```

- [ ] **Step 2: 跑测试 —— 预期失败**

```bash
pytest tests/integration/test_sessions_endpoints.py -v
```

预期: 404 失败。

- [ ] **Step 3: 实现 repo + router**

创建 `packages/python/javdb_platform/db_layer/sessions_repo.py`:

```python
from __future__ import annotations

import base64
import json
import sqlite3
from dataclasses import dataclass


@dataclass
class SessionRow:
    session_id: str
    state: str
    write_mode: str
    run_id: str | None
    run_attempt: int | None
    created_at: str
    updated_at: str


@dataclass
class SessionList:
    items: list[SessionRow]
    next_cursor: str | None
    total_estimate: int | None = None


def _encode_cursor(session_id: str) -> str:
    return base64.urlsafe_b64encode(json.dumps({"sid": session_id}).encode()).decode()


def _decode_cursor(cursor: str) -> str:
    return json.loads(base64.urlsafe_b64decode(cursor.encode())).get("sid")


class SessionsRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._conn.row_factory = sqlite3.Row

    def list(
        self,
        *,
        state: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> SessionList:
        sql = "SELECT SessionId, State, WriteMode, RunId, RunAttempt, CreatedAt, UpdatedAt FROM ReportSessions"
        params: list = []
        clauses: list[str] = []
        if state:
            clauses.append("State = ?")
            params.append(state)
        if cursor:
            last_sid = _decode_cursor(cursor)
            clauses.append("SessionId < ?")
            params.append(last_sid)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY SessionId DESC LIMIT ?"
        params.append(limit + 1)

        rows = self._conn.execute(sql, params).fetchall()
        items = [
            SessionRow(
                session_id=r["SessionId"],
                state=r["State"],
                write_mode=r["WriteMode"],
                run_id=r["RunId"],
                run_attempt=r["RunAttempt"],
                created_at=r["CreatedAt"],
                updated_at=r["UpdatedAt"],
            )
            for r in rows[:limit]
        ]
        next_cursor = _encode_cursor(items[-1].session_id) if len(rows) > limit else None
        return SessionList(items=items, next_cursor=next_cursor)

    def get(self, session_id: str) -> SessionRow | None:
        row = self._conn.execute(
            "SELECT SessionId, State, WriteMode, RunId, RunAttempt, CreatedAt, UpdatedAt "
            "FROM ReportSessions WHERE SessionId = ?",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        return SessionRow(
            session_id=row["SessionId"],
            state=row["State"],
            write_mode=row["WriteMode"],
            run_id=row["RunId"],
            run_attempt=row["RunAttempt"],
            created_at=row["CreatedAt"],
            updated_at=row["UpdatedAt"],
        )
```

在 `apps/api/schemas/capabilities_payloads.py` 中追加：

```python
class SessionItem(BaseModel):
    session_id: str
    state: str
    write_mode: str
    run_id: str | None
    run_attempt: int | None
    created_at: str
    updated_at: str


class SessionListResponse(BaseModel):
    items: list[SessionItem]
    next_cursor: str | None
    total_estimate: int | None = None
```

创建 `apps/api/routers/sessions.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from apps.api.schemas.capabilities_payloads import (
    SessionItem,
    SessionListResponse,
)
from apps.api.infra.auth import _require_auth
from packages.python.javdb_platform.db_layer.sessions_repo import SessionsRepo
from packages.python.javdb_platform.db_connection import get_db, REPORTS_DB_PATH

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("", response_model=SessionListResponse)
def list_sessions(
    state: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    _user=Depends(_require_auth),
) -> SessionListResponse:
    with get_db(REPORTS_DB_PATH) as conn:
        result = SessionsRepo(conn).list(state=state, cursor=cursor, limit=limit)
    return SessionListResponse(
        items=[SessionItem(**r.__dict__) for r in result.items],
        next_cursor=result.next_cursor,
    )
```

在 `runtime.py` 中注册。

- [ ] **Step 4: 跑测试 —— 预期通过**

```bash
pytest tests/integration/test_sessions_endpoints.py -v
```

预期: 2 个通过。

- [ ] **Step 5: 提交**

```bash
git add packages/python/javdb_platform/db_layer/sessions_repo.py \
        apps/api/routers/sessions.py \
        apps/api/services/runtime.py \
        apps/api/schemas/capabilities_payloads.py \
        tests/integration/test_sessions_endpoints.py
git commit -m "feat(api): add GET /api/sessions list endpoint"
```

---

## Task 13: `GET /api/sessions/{id}` 详情 endpoint

返回 session 及其关联的 movie/torrent 写入（pending + committed + audit）。

**文件:**

- 修改: `packages/python/javdb_platform/db_layer/sessions_repo.py`（新增 `get_detail()`）
- 修改: `apps/api/routers/sessions.py`（新增路由）
- 修改: `apps/api/schemas/capabilities_payloads.py`（新增详情模型）
- 修改: `tests/integration/test_sessions_endpoints.py`

- [ ] **Step 1: 编写会失败的测试**

```python
# Append to tests/integration/test_sessions_endpoints.py
def test_detail_returns_404_for_missing(admin_client):
    r = admin_client.get("/api/sessions/this-does-not-exist")
    assert r.status_code == 404


def test_detail_returns_shape_for_known_id(admin_client):
    # Pull a real session id from the list endpoint (or skip if none).
    listing = admin_client.get("/api/sessions", params={"limit": 1}).json()
    if not listing["items"]:
        import pytest
        pytest.skip("no sessions in db to test against")
    sid = listing["items"][0]["session_id"]
    r = admin_client.get(f"/api/sessions/{sid}")
    assert r.status_code == 200
    body = r.json()
    assert body["session"]["session_id"] == sid
    assert "movies" in body
    assert "torrents" in body
```

- [ ] **Step 2: 跑测试 —— 预期失败**

```bash
pytest tests/integration/test_sessions_endpoints.py -v -k "detail"
```

预期: 2 个失败。

- [ ] **Step 3: 实现**

在 `sessions_repo.py` 中追加：

```python
def get_writes(self, session_id: str) -> tuple[list[dict], list[dict]]:
    """Return (movies, torrents) for a session — committed + pending merged."""
    movies = [
        dict(row) for row in self._conn.execute(
            "SELECT * FROM ReportMovies WHERE SessionId = ?", (session_id,)
        ).fetchall()
    ]
    torrents = [
        dict(row) for row in self._conn.execute(
            "SELECT * FROM ReportTorrents WHERE SessionId = ?", (session_id,)
        ).fetchall()
    ]
    return movies, torrents
```

在 `capabilities_payloads.py` 中追加：

```python
class SessionDetailResponse(BaseModel):
    session: SessionItem
    movies: list[dict]
    torrents: list[dict]
```

在 `sessions.py` router 中追加：

```python
from fastapi import HTTPException


@router.get("/{session_id}", response_model=SessionDetailResponse)
def get_session_detail(
    session_id: str,
    _user=Depends(_require_auth),
) -> SessionDetailResponse:
    with get_db(REPORTS_DB_PATH) as conn:
        repo = SessionsRepo(conn)
        row = repo.get(session_id)
        if not row:
            raise HTTPException(status_code=404, detail={"error": {"code": "session.not_found"}})
        movies, torrents = repo.get_writes(session_id)
    return SessionDetailResponse(
        session=SessionItem(**row.__dict__),
        movies=movies,
        torrents=torrents,
    )
```

- [ ] **Step 4: 跑测试 —— 预期通过**

```bash
pytest tests/integration/test_sessions_endpoints.py -v
```

预期: 全部通过。

- [ ] **Step 5: 提交**

```bash
git add packages/python/javdb_platform/db_layer/sessions_repo.py \
        apps/api/routers/sessions.py \
        apps/api/schemas/capabilities_payloads.py \
        tests/integration/test_sessions_endpoints.py
git commit -m "feat(api): add GET /api/sessions/{id} detail endpoint"
```

---

## Task 14: `POST /api/sessions/{id}/rollback` endpoint

调用 Task 11 抽出的 rollback 库。请求体: `{dry_run, include_pending, restore_from_audit}`。

**文件:**

- 修改: `apps/api/routers/sessions.py`
- 修改: `apps/api/schemas/capabilities_payloads.py`
- 修改: `tests/integration/test_sessions_endpoints.py`

- [ ] **Step 1: 编写会失败的测试**

```python
# Append to tests/integration/test_sessions_endpoints.py
def test_rollback_dry_run_returns_plan(admin_client):
    listing = admin_client.get("/api/sessions", params={"limit": 1}).json()
    if not listing["items"]:
        import pytest
        pytest.skip("no sessions to roll back")
    sid = listing["items"][0]["session_id"]
    r = admin_client.post(
        f"/api/sessions/{sid}/rollback",
        json={"dry_run": True, "include_pending": True, "restore_from_audit": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert "actions" in body
    assert isinstance(body["actions"], list)


def test_rollback_requires_admin(readonly_client):
    r = readonly_client.post(
        "/api/sessions/any-id/rollback",
        json={"dry_run": True, "include_pending": False, "restore_from_audit": True},
    )
    assert r.status_code in (401, 403)


def test_rollback_unknown_session_404(admin_client):
    r = admin_client.post(
        "/api/sessions/nonexistent/rollback",
        json={"dry_run": True, "include_pending": False, "restore_from_audit": True},
    )
    assert r.status_code == 404
```

- [ ] **Step 2: 跑测试 —— 预期失败**

```bash
pytest tests/integration/test_sessions_endpoints.py -v -k "rollback"
```

预期: 3 个失败。

- [ ] **Step 3: 实现**

在 `capabilities_payloads.py` 中追加：

```python
class SessionRollbackPayload(BaseModel):
    dry_run: bool = True
    include_pending: bool = True
    restore_from_audit: bool = True


class SessionRollbackResponse(BaseModel):
    session_id: str
    dry_run: bool
    actions: list[dict]
    summary: dict[str, int]
```

在 `sessions.py` router 中追加：

```python
from apps.api.infra.auth import require_role
from packages.python.javdb_platform.rollback import (
    RollbackRequest,
    apply_rollback,
    plan_rollback,
)


@router.post("/{session_id}/rollback", response_model=SessionRollbackResponse)
def post_rollback(
    session_id: str,
    payload: SessionRollbackPayload,
    _user=Depends(require_role("admin")),
) -> SessionRollbackResponse:
    with get_db(REPORTS_DB_PATH) as conn:
        if not SessionsRepo(conn).get(session_id):
            raise HTTPException(status_code=404, detail={"error": {"code": "session.not_found"}})
    req = RollbackRequest(
        session_id=session_id,
        dry_run=payload.dry_run,
        include_pending=payload.include_pending,
        restore_from_audit=payload.restore_from_audit,
    )
    try:
        if payload.dry_run:
            result = plan_rollback(req)
            return SessionRollbackResponse(
                session_id=session_id,
                dry_run=True,
                actions=result.actions,
                summary=result.summary,
            )
        applied = apply_rollback(req)
        return SessionRollbackResponse(
            session_id=session_id,
            dry_run=False,
            actions=applied.applied,
            summary=applied.summary,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": {"code": "session.not_found", "message": str(exc)}})
```

- [ ] **Step 4: 跑测试 —— 预期通过**

```bash
pytest tests/integration/test_sessions_endpoints.py -v
```

预期: 全部通过。

- [ ] **Step 5: 提交**

```bash
git add apps/api/routers/sessions.py apps/api/schemas/capabilities_payloads.py tests/integration/test_sessions_endpoints.py
git commit -m "feat(api): add POST /api/sessions/{id}/rollback with dry-run support"
```

---

## Task 15: `POST /api/sessions/{id}/commit` endpoint

针对卡在 finalizing 状态的 session，对应 `apps/cli/commit_session.py`。请求体: `{force, drop_pending}`。

**文件:**

- 修改: `apps/cli/commit_session.py`（若尚未库化，先抽出 core）
- 修改: `apps/api/routers/sessions.py`
- 修改: `apps/api/schemas/capabilities_payloads.py`
- 修改: `tests/integration/test_sessions_endpoints.py`

- [ ] **Step 1: 检查 commit_session 结构**

执行:

```bash
grep -n "^def \|argparse\|add_argument" apps/cli/commit_session.py | head -20
```

找到核心函数。若已是库形态，直接复用。否则比照 Task 11 抽出（规模更小 —— `commit_session.py` 比 `rollback.py` 小得多）。

- [ ] **Step 2: 编写会失败的测试**

```python
# Append to tests/integration/test_sessions_endpoints.py
def test_commit_force_works_on_finalizing(admin_client):
    # Seed: prerequisite — at least one finalizing session must exist in test DB.
    listing = admin_client.get("/api/sessions", params={"state": "finalizing", "limit": 1}).json()
    if not listing["items"]:
        import pytest
        pytest.skip("no finalizing sessions to test against")
    sid = listing["items"][0]["session_id"]
    r = admin_client.post(
        f"/api/sessions/{sid}/commit",
        json={"force": True, "drop_pending": False},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == sid
    assert body["new_state"] == "committed"


def test_commit_requires_admin(readonly_client):
    r = readonly_client.post(
        "/api/sessions/any-id/commit",
        json={"force": True, "drop_pending": False},
    )
    assert r.status_code in (401, 403)


def test_commit_unknown_session_404(admin_client):
    r = admin_client.post(
        "/api/sessions/nonexistent/commit",
        json={"force": False, "drop_pending": False},
    )
    assert r.status_code == 404
```

- [ ] **Step 3: 实现**

如果 `apps/cli/commit_session.py` 仍是单体脚本，将其核心抽到 `packages/python/javdb_platform/sessions/commit.py`:

```python
# packages/python/javdb_platform/sessions/commit.py
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class CommitRequest:
    session_id: str
    force: bool = False
    drop_pending: bool = False


@dataclass
class CommitResult:
    session_id: str
    new_state: str
    pending_dropped: int = 0
    error: str | None = None


def commit_session(req: CommitRequest) -> CommitResult:
    # MOVE the body of apps/cli/commit_session.py main logic here.
    # MUST raise LookupError if the session cannot be found.
    raise NotImplementedError("Extract commit logic from apps/cli/commit_session.py")
```

然后重写 `apps/cli/commit_session.py`，让它 import 并调用这个函数。

在 `capabilities_payloads.py` 中追加：

```python
class SessionCommitPayload(BaseModel):
    force: bool = False
    drop_pending: bool = False


class SessionCommitResponse(BaseModel):
    session_id: str
    new_state: str
    pending_dropped: int = 0
```

在 `sessions.py` router 中追加：

```python
from packages.python.javdb_platform.sessions.commit import CommitRequest, commit_session


@router.post("/{session_id}/commit", response_model=SessionCommitResponse)
def post_commit(
    session_id: str,
    payload: SessionCommitPayload,
    _user=Depends(require_role("admin")),
) -> SessionCommitResponse:
    with get_db(REPORTS_DB_PATH) as conn:
        if not SessionsRepo(conn).get(session_id):
            raise HTTPException(status_code=404, detail={"error": {"code": "session.not_found"}})
    try:
        result = commit_session(CommitRequest(
            session_id=session_id,
            force=payload.force,
            drop_pending=payload.drop_pending,
        ))
        return SessionCommitResponse(
            session_id=result.session_id,
            new_state=result.new_state,
            pending_dropped=result.pending_dropped,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail={"error": {"code": "session.not_found", "message": str(exc)}})
```

- [ ] **Step 4: 跑测试 —— 预期通过**

```bash
pytest tests/integration/test_sessions_endpoints.py -v
```

预期: 全部通过。

- [ ] **Step 5: 提交**

```bash
git add apps/cli/commit_session.py \
        packages/python/javdb_platform/sessions/commit.py \
        packages/python/javdb_platform/sessions/__init__.py \
        apps/api/routers/sessions.py \
        apps/api/schemas/capabilities_payloads.py \
        tests/integration/test_sessions_endpoints.py
git commit -m "feat(api): add POST /api/sessions/{id}/commit + extract commit library"
```

---

## Task 16: 收紧前端会消费的现有 endpoint 的 Pydantic 响应模型

前端会消费的现有 endpoint（tasks、auth、config、explore）目前许多地方还返回临时拼装的 dict。给每个 endpoint 加上 typed 的 `response_model=`，让 `openapi.json` 对 `openapi-typescript` 真正有用。

**文件:**

- 修改: `apps/api/routers/tasks.py`、`auth.py`、`config.py`、`explore.py`
- 修改: `apps/api/schemas/payloads.py`
- 创建: `tests/integration/test_openapi_response_shapes.py`

- [ ] **Step 1: 盘点当前返回类型**

执行:

```bash
grep -n "return {" apps/api/routers/tasks.py apps/api/routers/auth.py apps/api/routers/config.py apps/api/routers/explore.py | head -40
```

对每一处临时 dict return，记录 key 与类型。这些将成为 Pydantic 响应模型。

- [ ] **Step 2: 编写会失败的 OpenAPI shape 测试**

```python
# tests/integration/test_openapi_response_shapes.py
def test_each_fe_consumed_endpoint_has_typed_200_response(admin_client):
    schema = admin_client.get("/openapi.json").json()
    paths = schema["paths"]
    must_be_typed = [
        ("/api/tasks/daily", "post"),
        ("/api/tasks/adhoc", "post"),
        ("/api/tasks", "get"),
        ("/api/tasks/stats", "get"),
        ("/api/tasks/{job_id}", "get"),
        ("/api/jobs/spider", "post"),
        ("/api/jobs/{job_id}/status", "get"),
        ("/api/auth/login", "post"),
        ("/api/auth/refresh", "post"),
        ("/api/config", "get"),
        ("/api/config", "put"),
        ("/api/explore/resolve", "post"),
        ("/api/explore/one-click", "post"),
        ("/api/explore/index-status", "post"),
        ("/api/explore/download-magnet", "post"),
        ("/api/explore/search-by-video-code", "post"),
    ]
    for path, method in must_be_typed:
        op = paths[path][method]
        resp200 = op["responses"]["200"]
        # Must have a content schema with a $ref (i.e. a Pydantic model)
        # rather than just type: object.
        schema_block = resp200["content"]["application/json"]["schema"]
        assert "$ref" in schema_block or schema_block.get("type") != "object", (
            f"{method.upper()} {path} returns untyped object — add response_model="
        )
```

- [ ] **Step 3: 跑测试 —— 仍使用临时 dict 的 endpoint 预期失败**

```bash
pytest tests/integration/test_openapi_response_shapes.py -v
```

预期: 失败列表逐一列出当前未类型化的 endpoint。

- [ ] **Step 4: 为每个 endpoint 加 Pydantic 响应模型**

对失败列表里的每个 endpoint:

1. 在 `apps/api/schemas/payloads.py` 中新增匹配当前返回 dict 的 `*Response` 模型。
2. 在 router 中给装饰器加 `response_model=XxxResponse`，并把 return 改为构造该模型实例。

示例: `/api/tasks/stats` 目前返回 `{"running": 2, "success": 145, "failed": 1}`。改为：

```python
# In apps/api/schemas/payloads.py
class TaskStatsResponse(BaseModel):
    running: int
    success: int
    failed: int


# In apps/api/routers/tasks.py
@router.get("/tasks/stats", response_model=TaskStatsResponse)
def get_task_stats(...) -> TaskStatsResponse:
    # existing dict construction
    return TaskStatsResponse(running=..., success=..., failed=...)
```

对测试中 `must_be_typed` 列表的每个 endpoint 重复此操作。

- [ ] **Step 5: 跑测试 —— 预期通过**

```bash
pytest tests/integration/test_openapi_response_shapes.py -v
```

预期: 通过。

- [ ] **Step 6: 跑全套测试 —— 确认无行为回归**

```bash
pytest tests/unit/ tests/integration/ -x
```

预期: 除已有的、与本次无关的失败之外，无新失败。

- [ ] **Step 7: 提交**

```bash
git add apps/api/routers/ apps/api/schemas/payloads.py tests/integration/test_openapi_response_shapes.py
git commit -m "refactor(api): add typed response models on FE-consumed endpoints"
```

---

## Task 17: 验证现有 `sync-cookie` 和 `login/refresh` endpoint 在 OpenAPI 中可见

这俩 endpoint 已存在但未列在 `must_be_typed` 里，因为可能已经类型化。确认一下。

**文件:**

- 修改: `tests/integration/test_openapi_response_shapes.py`

- [ ] **Step 1: 扩展类型化测试**

在 `tests/integration/test_openapi_response_shapes.py` 末尾追加：

```python
def test_login_refresh_and_sync_cookie_exist_and_typed(admin_client):
    schema = admin_client.get("/openapi.json").json()
    paths = schema["paths"]
    # /api/login/refresh — javdb session refresh (NOT auth/refresh)
    assert "/api/login/refresh" in paths
    op = paths["/api/login/refresh"]["post"]
    assert "$ref" in op["responses"]["200"]["content"]["application/json"]["schema"]

    # /api/explore/sync-cookie — push javdb cookie to BE
    assert "/api/explore/sync-cookie" in paths
    op = paths["/api/explore/sync-cookie"]["post"]
    assert "$ref" in op["responses"]["200"]["content"]["application/json"]["schema"]
```

- [ ] **Step 2: 跑测试 —— 修复仍为临时 dict 的 endpoint**

```bash
pytest tests/integration/test_openapi_response_shapes.py::test_login_refresh_and_sync_cookie_exist_and_typed -v
```

如果失败: 在 `apps/api/routers/system.py`（`/api/login/refresh`）或 `apps/api/routers/explore.py`（`/api/explore/sync-cookie`）中找到对应路由，比照 Task 16 添加 Pydantic 响应模型。

- [ ] **Step 3: 跑测试 —— 预期通过**

```bash
pytest tests/integration/test_openapi_response_shapes.py -v
```

预期: 通过。

- [ ] **Step 4: 提交**

```bash
git add tests/integration/test_openapi_response_shapes.py apps/api/routers/
git commit -m "refactor(api): type response models on login/refresh + sync-cookie"
```

---

## Task 18: 重新生成 openapi.json + 文档新 endpoint

收尾 —— 跑一遍 dumper，更新开发者文档。

**文件:**

- 修改: `docs/api/openapi.json`（重新生成）
- 修改: `docs/en/developer/api-reference.md`
- 修改: `docs/zh/developer/api-reference.md`

- [ ] **Step 1: 重新生成 openapi.json**

```bash
STORAGE_BACKEND=sqlite API_SECRET_KEY=dump-only-secret python3 scripts/dump_openapi.py
```

预期: 写入 `docs/api/openapi.json`。确认 10 个新 endpoint 路径都已存在：

```bash
python3 -c "
import json
schema = json.load(open('docs/api/openapi.json'))
needed = [
  '/api/capabilities',
  '/api/onboarding/status',
  '/api/onboarding/test',
  '/api/onboarding/complete',
  '/api/onboarding/dismiss-hint',
  '/api/system/state',
  '/api/sessions',
  '/api/sessions/{session_id}',
  '/api/sessions/{session_id}/rollback',
  '/api/sessions/{session_id}/commit',
]
missing = [p for p in needed if p not in schema['paths']]
print('missing:', missing if missing else 'none')
"
```

预期: `missing: none`。

- [ ] **Step 2: 更新英文开发者文档**

打开 `docs/en/developer/api-reference.md`。找到现有的 "Endpoints" 段落，新增一个小节：

```markdown
### Phase 1 Frontend Console Endpoints

These endpoints were added in 2026-05 to support the new web console (`javdb-autospider-web`).

#### Discovery

- `GET /api/capabilities` — runtime feature flags + version info. Used by the FE to gate UI per deployment. See [openapi.json](../api/openapi.json) for the full shape.

#### Onboarding

- `GET /api/onboarding/status` — returns `{completed, required_missing[], skippable_missing[]}`.
- `POST /api/onboarding/test` — tests one component (`javdb`/`qb`/`proxy`/`smtp`); returns `{component, ok, message, details?}`.
- `POST /api/onboarding/complete` — admin-only; marks setup done.
- `POST /api/onboarding/dismiss-hint` — admin-only; dismisses a Dashboard hint card.

#### Generic state

- `GET /api/system/state?key=...` — reads a KV pair from `system_state`.
- `PUT /api/system/state` — admin-only; writes a KV pair.

#### Sessions

- `GET /api/sessions?state=&cursor=&limit=` — cursor-paginated list of ReportSessions.
- `GET /api/sessions/{session_id}` — full session detail incl. writes.
- `POST /api/sessions/{session_id}/rollback` — admin-only; body `{dry_run, include_pending, restore_from_audit}`.
- `POST /api/sessions/{session_id}/commit` — admin-only; body `{force, drop_pending}`.

#### Test mode (E2E only)

- `POST /api/test/reset` — present only when the server is started with `TEST_MODE=1`. Truncates ops/history tables. **Must never be enabled in production.**
```

- [ ] **Step 3: 更新中文开发者文档**

打开 `docs/zh/developer/api-reference.md`，按相同结构新增对应中文小节（散文部分翻译，path 与 method 名保持原样）。

- [ ] **Step 4: 提交**

```bash
git add docs/api/openapi.json docs/en/developer/api-reference.md docs/zh/developer/api-reference.md
git commit -m "docs(api): document Phase 1 frontend console endpoints + regenerate schema"
```

---

## 最终验证

- [ ] **跑全套测试**

```bash
pytest tests/unit/ tests/integration/ -v
```

预期: 本计划没有引入新失败。

- [ ] **校验 OpenAPI 完整性**

```bash
python3 -c "
import json
schema = json.load(open('docs/api/openapi.json'))
print('total paths:', len(schema['paths']))
print('new phase-1 paths present:')
for p in sorted(schema['paths']):
    if any(seg in p for seg in ['/capabilities', '/onboarding', '/system', '/sessions', '/test/reset']):
        print(' ', p)
"
```

预期: 打印 10+ 个新路径。

- [ ] **构建 API Docker 镜像**

```bash
docker build -f docker/Dockerfile.api -t javdb-autospider-api:plan-a .
docker run --rm -d --name jas-api-final -p 18101:8100 \
    -e STORAGE_BACKEND=sqlite -e API_SECRET_KEY=plan-a-test \
    javdb-autospider-api:plan-a
sleep 4
curl -sf http://127.0.0.1:18101/api/capabilities && echo
docker rm -f jas-api-final
```

预期: 打印出 capabilities JSON。

---

## 计划摘要

| # | 任务 | 文件 | 测试 |
|---|------|-------|-------|
| 1 | API Dockerfile | `docker/Dockerfile.api` | docker 烟雾测试 |
| 2 | `publish-api-image.yml` | `.github/workflows/` | yaml parse |
| 3 | `publish-openapi.yml` + dumper | `.github/workflows/`、`scripts/dump_openapi.py` | yaml parse + dump |
| 4 | `GET /api/capabilities` | router + schema + conftest | 3 个集成测试 |
| 5 | `POST /api/test/reset`（受门控） | router | 2 个集成测试 |
| 6 | `system_state` 表 + repo | 迁移 + repo | 5 个单元测试 |
| 7 | `GET/PUT /api/system/state` | router | 4 个集成测试 |
| 8 | `GET /api/onboarding/status` | router | 1 个集成测试 |
| 9 | `POST /api/onboarding/test` | router | 2 个集成测试 |
| 10 | `POST /api/onboarding/complete` + `dismiss-hint` | router | 4 个集成测试 |
| 11 | Rollback 库抽取 | `javdb_platform/rollback/` + CLI 重构 | 1 个单元测试 + 现有 |
| 12 | `GET /api/sessions` | repo + router | 2 个集成测试 |
| 13 | `GET /api/sessions/{id}` | router | 2 个集成测试 |
| 14 | `POST /api/sessions/{id}/rollback` | router | 3 个集成测试 |
| 15 | `POST /api/sessions/{id}/commit` + commit 库 | router | 3 个集成测试 |
| 16 | 给现有 endpoint 加 typed 响应模型 | routers + payloads | 1 个 schema 测试 |
| 17 | 验证 `sync-cookie` + `login/refresh` 已类型化 | tests | 扩展 16 |
| 18 | 重新生成 openapi.json + 文档 | docs | 手动 |

**18 个任务，~35 个新测试（12 个单元 + 23 个集成），~16 次提交。**

## 已知后续（Plan A 有意延后）

- **Error envelope 包裹。** 规范 §8.3 规定 BE 错误响应形如 `{"error": {"code", "message", "details", "request_id"}}`。FastAPI 默认的 `HTTPException` 生成 `{"detail": ...}`。新 endpoint 当前抛 `HTTPException(detail={"error": {...}})` 虽可用，但会得到 `{"detail": {"error": ...}}` —— 多一层嵌套。后续任务应注册自定义异常处理器，把 `detail.error` 解包到顶层 `error`。归入下一个 BE 计划或 Plan F（切换）。Plan B 中的 FE wrapper 暂时会兼容两种形态。
- **Rollback 库抽取（Task 11）** 有意把 `plan_rollback` / `apply_rollback` 的函数体写成"从 apps/cli/rollback.py 迁移过来"，而不是把现有的 ~400 行内联粘贴过来。工程师需要阅读 `apps/cli/rollback.py`，定位 planning 与 apply 函数，将其搬过去。Task 15 中的 `commit_session` 同形态。
- **MovieClaim 清理 endpoint**（`sweep_movie_claim_stages`、`cleanup_stale_in_progress`）按规范 §8.4 属于 Phase 2 endpoint —— 不在 Plan A 范围。
- **Task 11 的分层倒置（有意延后）。** Commit `9f5e66a6` 把 rollback pipeline 逻辑留在了 `apps/cli/rollback.py`，并让 `packages/python/javdb_platform/rollback/core.py` 成为一个薄适配器、反过来调 CLI 里的实现。这样可以一字不改地保留 111 个现有 rollback 测试，但引入了 `packages/` -> `apps/cli/` 的 import（仅此一例）。Task 12-18 依赖的库公开 API（`RollbackRequest` / `plan_rollback` / `apply_rollback`）方向是对的；这种内部倒置只影响后续维护。建议作为 Phase 2 BE 任务清理 —— 见规范 §12.3。
- **Task 11 review（`9f5e66a6`）中的零散清理项:**
  - `core.py:124-143` 的 `_session_exists` 静默吞掉 DB 错误。加一行 `logger.warning`，让瞬时错误可观察。
  - `RollbackRequest.restore_from_audit`（`core.py:68`）目前仅供参考。要么在 False 时阻止 audit 回放，要么干脆删掉这个字段。
  - `_drive_rollback` 的 `_no_targets` 哨兵可以改成 typed 的 `(summary | None, exit_code)` —— 偏美观。
  - `apply_rollback`（`core.py:167`）丢弃了 `_drive_rollback` 的 `_exit_code`。CLI 用 exit code（2/3/4）映射不同的失败模式；HTTP 调用方可能需要这个信号。考虑通过 `RollbackResult.summary["outcome"]` ∈ `{ok, refused, partial_drift}` 暴露出来。
- **Task 15 的 commit 副作用对齐（有意延后）。** Commit `eb48230e` 引入的 `packages/python/javdb_platform/sessions/commit.py` **没有**复刻 `apps/cli/commit_session.py` 的副作用：(a) 通过 `fanout_movie_claim(..., operation="commit")` 通知 Cloudflare Worker 协调器、(b) 通过 `append_jsonl_record` 写 JSONL 漂移记录、(c) 通过 `write_github_output` 给下游 workflow 步骤写输出。库函数直接调用 `db_mark_session_committed` + `db_commit_session_history`。使用 `POST /api/sessions/{id}/commit` 的操作员需要明白这是一次"仅数据库"的 commit；coordinator 上的 MovieClaim stage 仍会停留在 `staged` 状态。**建议清理（Phase 2）:** 给 `CommitRequest` 加 `fanout_coordinator: bool = False`，True 时调用 coordinator client；或者把 CLI 的 commit 拆成一个同时处理 post-DB 副作用的小库。

Phase 1 BE 前置工作到此为止。完成后，Plan B（新仓库初始化）即可与主仓库工作并行启动 —— 新仓库只需消费已发布的 `openapi.json` 与镜像。
