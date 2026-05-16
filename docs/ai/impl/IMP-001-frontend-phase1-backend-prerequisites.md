# IMP-001: Frontend Phase 1 — Backend Prerequisites

**Status**: Accepted — Completed 2026-05-16 (merged via #33065718)
**Date**: 2026-05-16
**Deciders**: Frontend rewrite working stream (in support of the new `javdb-autospider-web` repo)
**Related**: design spec at `docs/superpowers/specs/2026-05-16-frontend-rewrite-design.md` (sections §4.2, §8.2, §8.4 Phase 1 column, §11 steps 1–2); succeeded by Phase 2 frontend implementation in the separate frontend repo

> **Note on format:** This file is an **implementation plan** — written by the writing-plans workflow, not a design document. It records HOW to execute the related design decisions (see **Related** above). The preamble (Goal / Architecture / Tech Stack) frames the work; the body is the step-by-step execution checklist. English-only by repo convention.
>
> **For agentic workers (historical):** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land everything the main repo (`JAVDB_AutoSpider_CICD`) needs to support the new frontend repo (`javdb-autospider-web`) — Dockerfile for the API service, publishing workflows for the BE image and OpenAPI schema, the 10 new Phase 1 endpoints, a rollback library refactor that the Sessions endpoints depend on, and tightened Pydantic response models so OpenAPI-driven TS types are useful.

**Architecture:** Add a separate API Dockerfile (the existing `docker/Dockerfile` is cron/spider-focused) and two GH workflows for publishing. Implement the 10 new endpoints as a set of new routers under `apps/api/routers/` (capabilities, test_mode, onboarding, system_state, sessions). Extract `apps/cli/rollback.py`'s logic into a callable library at `packages/python/javdb_platform/rollback/core.py` so the new Sessions endpoints reuse the same code path as the CLI. All KV state (onboarded flag, dismissed hints, prefs) lives in a new `system_state` table inside `reports/operations.db`.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, pytest + `fastapi.testclient.TestClient`, SQLite (existing repo pattern), GitHub Actions workflows, GHCR for image publishing.

**Spec:** [docs/superpowers/specs/2026-05-16-frontend-rewrite-design.md](../specs/2026-05-16-frontend-rewrite-design.md) — sections §4.2, §8.2, §8.4 (Phase 1 column), §11 steps 1-2.

---

## File Map

**New files (main repo):**

| Path | Responsibility |
|------|---------------|
| `docker/Dockerfile.api` | Multi-stage Dockerfile for the FastAPI service (separate from the existing cron-focused `docker/Dockerfile`). |
| `.github/workflows/publish-api-image.yml` | Build + push BE image to GHCR on every main push. |
| `.github/workflows/publish-openapi.yml` | Dump `/openapi.json` to `docs/api/openapi.json` + GH Release artifact. |
| `apps/api/routers/capabilities.py` | `GET /api/capabilities` route — discovery endpoint for FE. |
| `apps/api/routers/test_mode.py` | `POST /api/test/reset` route — gated by `TEST_MODE=1`, used only by E2E. |
| `apps/api/routers/onboarding.py` | `GET /api/onboarding/status`, `POST /api/onboarding/test`, `POST /api/onboarding/complete`, `POST /api/onboarding/dismiss-hint`. |
| `apps/api/routers/system_state.py` | `GET /api/system/state`, `PUT /api/system/state` — generic KV. |
| `apps/api/routers/sessions.py` | `GET /api/sessions`, `GET /api/sessions/{id}`, `POST /api/sessions/{id}/rollback`, `POST /api/sessions/{id}/commit`. |
| `apps/api/schemas/capabilities_payloads.py` | Pydantic models for capabilities + onboarding + system_state + sessions endpoints. |
| `packages/python/javdb_platform/db_layer/system_state_repo.py` | KV repo against `system_state` table. |
| `packages/python/javdb_platform/db_layer/sessions_repo.py` | List/detail repo for `ReportSessions`. |
| `packages/python/javdb_platform/rollback/__init__.py` | Library package marker. |
| `packages/python/javdb_platform/rollback/core.py` | Rollback core (extracted from `apps/cli/rollback.py`). |
| `packages/python/javdb_migrations/0042_system_state_table.sql` | Migration adding the `system_state` table. |
| `tests/integration/test_capabilities_endpoint.py` | Tests for capabilities. |
| `tests/integration/test_test_mode_reset.py` | Tests for `/api/test/reset` gating. |
| `tests/integration/test_onboarding_endpoints.py` | Tests for onboarding endpoints. |
| `tests/integration/test_system_state_endpoints.py` | Tests for KV. |
| `tests/integration/test_sessions_endpoints.py` | Tests for sessions endpoints. |
| `tests/unit/test_rollback_core_library.py` | Tests for the extracted rollback library. |

**Modified files:**

| Path | Why |
|------|-----|
| `apps/api/services/runtime.py` | Register the 5 new routers; wire the `TEST_MODE` gating; add capabilities-version constant. |
| `apps/api/schemas/payloads.py` | Tighten response models on `tasks/*`, `auth/*`, `config/*`, `explore/*`. |
| `apps/cli/rollback.py` | Strip core logic; become a thin CLI wrapper around `packages/python/javdb_platform/rollback/core.py`. |
| `apps/cli/commit_session.py` | Same shape — thin CLI over a library function. |
| `packages/python/javdb_platform/db_layer/__init__.py` | Re-export new repos. |
| `docs/en/developer/api-reference.md` + `docs/zh/developer/api-reference.md` | Document the 10 new endpoints. |

---

## Cross-cutting test pattern

Every endpoint test follows this shape (FastAPI's `TestClient` with auth helpers already present in `tests/integration/test_spider_gateway.py:280` for reference):

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

Move this fixture to `tests/integration/conftest.py` after Task 4 so subsequent tasks reuse it (Task 4 inlines it; Task 5+ uses the fixture).

---

## Task 1: Create API-specific Dockerfile

The existing `docker/Dockerfile` runs `cron -f` and is shaped for the spider/pipeline. We need a separate image for the FastAPI service that runs `uvicorn` and exposes port 8100.

**Files:**

- Create: `docker/Dockerfile.api`

- [ ] **Step 1: Write the Dockerfile**

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

- [ ] **Step 2: Build locally to verify**

Run:

```bash
docker build -f docker/Dockerfile.api -t javdb-autospider-api:dev .
```

Expected: build succeeds. Image size roughly 700-900 MB.

- [ ] **Step 3: Smoke-run the container**

Run:

```bash
docker run --rm -d --name jas-api-smoke -p 18100:8100 \
    -e STORAGE_BACKEND=sqlite \
    -e API_SECRET_KEY=test-secret-please-change \
    javdb-autospider-api:dev
sleep 4
curl -sf http://127.0.0.1:18100/api/health
docker rm -f jas-api-smoke
```

Expected: `curl` prints a health JSON (e.g. `{"status":"ok","rust_core_available":true,...}`) and exits 0.

- [ ] **Step 4: Add the build context entry to `.dockerignore` if missing**

Read `.dockerignore`; if it excludes `reports/`, `logs/`, `node_modules/`, and `.git/`, do nothing. Otherwise add those entries.

- [ ] **Step 5: Commit**

```bash
git add docker/Dockerfile.api .dockerignore
git commit -m "build(docker): add API-service Dockerfile separate from cron image"
```

---

## Task 2: `publish-api-image.yml` workflow

Build + push the BE image to GHCR on every push to `main`.

**Files:**

- Create: `.github/workflows/publish-api-image.yml`

- [ ] **Step 1: Write the workflow**

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

- [ ] **Step 2: Validate YAML syntax**

Run:

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/publish-api-image.yml'))"
```

Expected: no output (success). Any error means YAML is malformed.

- [ ] **Step 3: Validate with `actionlint` if available**

Run (skip if not installed):

```bash
which actionlint && actionlint .github/workflows/publish-api-image.yml || echo "actionlint not installed; skipping"
```

Expected: no errors, or "actionlint not installed; skipping".

- [ ] **Step 4: Verify the workflow appears in `gh workflow list`**

Run (skip if not authenticated):

```bash
gh workflow list 2>/dev/null | grep -i "publish api image" || echo "not yet visible (push to register)"
```

Expected: either the workflow shows up (if previously pushed) or the fallback message.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/publish-api-image.yml
git commit -m "ci(workflows): publish API Docker image to GHCR on main push"
```

---

## Task 3: `publish-openapi.yml` workflow

Dump `/openapi.json` from the FastAPI app and publish it as a Release artifact + commit to `docs/api/openapi.json` for the new frontend repo to consume via `openapi-typescript`.

**Files:**

- Create: `.github/workflows/publish-openapi.yml`
- Create: `scripts/dump_openapi.py`

- [ ] **Step 1: Write the dumper script**

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

- [ ] **Step 2: Write the workflow**

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

- [ ] **Step 3: Run the dumper locally to verify it produces valid JSON**

Run:

```bash
STORAGE_BACKEND=sqlite API_SECRET_KEY=dump-only-secret python3 scripts/dump_openapi.py
python3 -c "import json; json.load(open('docs/api/openapi.json')); print('valid json,', len(json.load(open('docs/api/openapi.json'))['paths']), 'paths')"
```

Expected: prints `wrote ... bytes` and then `valid json, N paths` where N matches the number of current routes (should be ~25 today, will grow as Plan A adds endpoints).

- [ ] **Step 4: Confirm workflow YAML is valid**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/publish-openapi.yml'))"
```

Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add scripts/dump_openapi.py .github/workflows/publish-openapi.yml docs/api/openapi.json
git commit -m "ci(workflows): publish openapi.json on main push + manual dumper"
```

---

## Task 4: Capabilities endpoint

`GET /api/capabilities` returns the discovery payload described in spec §8.2.

**Files:**

- Create: `apps/api/schemas/capabilities_payloads.py`
- Create: `apps/api/routers/capabilities.py`
- Modify: `apps/api/services/runtime.py` (register router)
- Create: `tests/integration/conftest.py`
- Create: `tests/integration/test_capabilities_endpoint.py`

- [ ] **Step 1: Write the failing test (with shared fixtures)**

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

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/integration/test_capabilities_endpoint.py -v
```

Expected: 3 failures with `404` (endpoint not found).

- [ ] **Step 3: Implement schema + router**

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

Then register the router in `apps/api/services/runtime.py` — modify the existing `for router in (...)` block:

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

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/integration/test_capabilities_endpoint.py -v
```

Expected: 3 passes.

- [ ] **Step 5: Commit**

```bash
git add apps/api/schemas/capabilities_payloads.py apps/api/routers/capabilities.py apps/api/services/runtime.py tests/integration/conftest.py tests/integration/test_capabilities_endpoint.py
git commit -m "feat(api): add GET /api/capabilities discovery endpoint"
```

---

## Task 5: `POST /api/test/reset` (TEST_MODE gated)

A test-only endpoint that truncates KV + sessions + history tables for Playwright spec isolation. Returns 404 when `TEST_MODE` env is unset.

**Files:**

- Create: `apps/api/routers/test_mode.py`
- Modify: `apps/api/services/runtime.py` (conditional router registration)
- Create: `tests/integration/test_test_mode_reset.py`

- [ ] **Step 1: Write the failing test**

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

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/integration/test_test_mode_reset.py -v
```

Expected: 2 failures (route missing).

- [ ] **Step 3: Implement the gated router**

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

Then in `apps/api/services/runtime.py`, add conditional registration **after** the main router loop:

```python
# In apps/api/services/runtime.py, after the main include_router block
if os.getenv("TEST_MODE") == "1":
    from apps.api.routers.test_mode import router as test_mode_router
    app.include_router(test_mode_router)
```

Add `import os` at the top if not already present.

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/integration/test_test_mode_reset.py -v
```

Expected: 2 passes.

- [ ] **Step 5: Commit**

```bash
git add apps/api/routers/test_mode.py apps/api/services/runtime.py tests/integration/test_test_mode_reset.py
git commit -m "feat(api): add gated POST /api/test/reset for E2E isolation"
```

---

## Task 6: `system_state` table migration + repo

The 4 onboarding endpoints + `/api/system/state` all need a generic KV. Land the table and the repo before the routes.

**Files:**

- Create: `packages/python/javdb_migrations/0042_system_state_table.sql`
- Create: `packages/python/javdb_platform/db_layer/system_state_repo.py`
- Modify: `packages/python/javdb_platform/db_layer/__init__.py`
- Create: `tests/unit/test_system_state_repo.py`

- [ ] **Step 1: Write the failing test**

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

- [ ] **Step 2: Run — expect fail (module missing)**

```bash
pytest tests/unit/test_system_state_repo.py -v
```

Expected: ImportError on `system_state_repo`.

- [ ] **Step 3: Implement migration + repo**

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

Add to `packages/python/javdb_platform/db_layer/__init__.py`:

```python
from packages.python.javdb_platform.db_layer.system_state_repo import SystemStateRepo  # noqa: F401
```

- [ ] **Step 4: Run — expect pass**

```bash
pytest tests/unit/test_system_state_repo.py -v
```

Expected: 5 passes.

- [ ] **Step 5: Commit**

```bash
git add packages/python/javdb_migrations/0042_system_state_table.sql \
        packages/python/javdb_platform/db_layer/system_state_repo.py \
        packages/python/javdb_platform/db_layer/__init__.py \
        tests/unit/test_system_state_repo.py
git commit -m "feat(db): add system_state KV table + SystemStateRepo"
```

---

## Task 7: `/api/system/state` GET/PUT

KV web access — admin-only writes, admin-readonly reads.

**Files:**

- Create: `apps/api/routers/system_state.py`
- Modify: `apps/api/services/runtime.py` (register)
- Modify: `apps/api/schemas/capabilities_payloads.py` (add KV schema)
- Create: `tests/integration/test_system_state_endpoints.py`

- [ ] **Step 1: Write the failing test**

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

- [ ] **Step 2: Run — expect fail**

```bash
pytest tests/integration/test_system_state_endpoints.py -v
```

Expected: 4 failures.

- [ ] **Step 3: Implement schema + router**

Add to `apps/api/schemas/capabilities_payloads.py`:

```python
class SystemStateGetResponse(BaseModel):
    key: str
    value: str | None


class SystemStatePutPayload(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    value: str
```

Create `apps/api/routers/system_state.py`:

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

Register the router:

```python
# apps/api/services/runtime.py
from apps.api.routers.system_state import router as system_state_router
# add to the include_router loop
```

- [ ] **Step 4: Run — expect pass**

```bash
pytest tests/integration/test_system_state_endpoints.py -v
```

Expected: 4 passes.

- [ ] **Step 5: Commit**

```bash
git add apps/api/routers/system_state.py apps/api/services/runtime.py apps/api/schemas/capabilities_payloads.py tests/integration/test_system_state_endpoints.py
git commit -m "feat(api): add GET/PUT /api/system/state generic KV"
```

---

## Task 8: Onboarding `GET /api/onboarding/status`

Returns `{completed, required_missing[], skippable_missing[]}` based on KV flag + config detection.

**Files:**

- Create: `apps/api/routers/onboarding.py` (status endpoint only in this task)
- Modify: `apps/api/services/runtime.py` (register)
- Modify: `apps/api/schemas/capabilities_payloads.py` (add onboarding models)
- Create: `tests/integration/test_onboarding_endpoints.py`

- [ ] **Step 1: Write the failing test**

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

- [ ] **Step 2: Run — expect fail**

```bash
pytest tests/integration/test_onboarding_endpoints.py::test_status_default_returns_required_missing -v
```

Expected: 404 failure.

- [ ] **Step 3: Implement schema + router**

Add to `apps/api/schemas/capabilities_payloads.py`:

```python
class OnboardingStatusResponse(BaseModel):
    completed: bool
    required_missing: list[str]
    skippable_missing: list[str]
```

Create `apps/api/routers/onboarding.py`:

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

Register the router in `runtime.py`.

- [ ] **Step 4: Run — expect pass**

```bash
pytest tests/integration/test_onboarding_endpoints.py::test_status_default_returns_required_missing -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add apps/api/routers/onboarding.py apps/api/services/runtime.py apps/api/schemas/capabilities_payloads.py tests/integration/test_onboarding_endpoints.py
git commit -m "feat(api): add GET /api/onboarding/status"
```

---

## Task 9: Onboarding `POST /api/onboarding/test`

Test a single component (`javdb` / `qb` / `proxy` / `smtp`) — returns connection result + diagnostic message.

**Files:**

- Modify: `apps/api/routers/onboarding.py`
- Modify: `apps/api/schemas/capabilities_payloads.py`
- Modify: `tests/integration/test_onboarding_endpoints.py`

- [ ] **Step 1: Write the failing test**

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

- [ ] **Step 2: Run — expect fail**

```bash
pytest tests/integration/test_onboarding_endpoints.py -v
```

Expected: 2 new failures.

- [ ] **Step 3: Implement**

Add to `apps/api/schemas/capabilities_payloads.py`:

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

Add to `apps/api/routers/onboarding.py`:

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

- [ ] **Step 4: Run — expect pass**

```bash
pytest tests/integration/test_onboarding_endpoints.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add apps/api/routers/onboarding.py apps/api/schemas/capabilities_payloads.py tests/integration/test_onboarding_endpoints.py
git commit -m "feat(api): add POST /api/onboarding/test for javdb/qb/proxy/smtp"
```

---

## Task 10: Onboarding `POST /api/onboarding/complete` + `dismiss-hint`

Two writes to the KV. Admin-only.

**Files:**

- Modify: `apps/api/routers/onboarding.py`
- Modify: `apps/api/schemas/capabilities_payloads.py`
- Modify: `tests/integration/test_onboarding_endpoints.py`

- [ ] **Step 1: Write the failing tests**

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

- [ ] **Step 2: Run — expect fail**

```bash
pytest tests/integration/test_onboarding_endpoints.py -v
```

Expected: 4 new failures.

- [ ] **Step 3: Implement**

Add to `apps/api/schemas/capabilities_payloads.py`:

```python
class DismissHintPayload(BaseModel):
    hint_id: str = Field(min_length=1, max_length=64)
```

Add to `apps/api/routers/onboarding.py`:

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

- [ ] **Step 4: Run — expect pass**

```bash
pytest tests/integration/test_onboarding_endpoints.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add apps/api/routers/onboarding.py apps/api/schemas/capabilities_payloads.py tests/integration/test_onboarding_endpoints.py
git commit -m "feat(api): add onboarding complete + dismiss-hint endpoints"
```

---

## Task 11: Extract rollback core into a library

`apps/cli/rollback.py` is 400+ lines of CLI + logic. Extract the logic into `packages/python/javdb_platform/rollback/core.py` so the new Sessions endpoint can call the same function. CLI becomes a thin argparse wrapper.

**Files:**

- Create: `packages/python/javdb_platform/rollback/__init__.py`
- Create: `packages/python/javdb_platform/rollback/core.py`
- Modify: `apps/cli/rollback.py`
- Create: `tests/unit/test_rollback_core_library.py`

- [ ] **Step 1: Inspect current CLI structure**

Read `apps/cli/rollback.py` to identify the central function (likely `run_rollback()` or similar). List its current argparse flags and which become library params.

Run:

```bash
grep -n "^def \|argparse\|add_argument" apps/cli/rollback.py | head -40
```

Expected: see the function and flag inventory. Use this to design the library API.

- [ ] **Step 2: Write the failing library tests**

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

Note: this test asserts the API surface only. Behavioral tests live in existing CLI tests (`tests/integration/` already covers rollback flows via the CLI); the library extraction must not change behavior, so existing CLI tests remain the regression suite.

- [ ] **Step 3: Run — expect fail (module missing)**

```bash
pytest tests/unit/test_rollback_core_library.py -v
```

Expected: ImportError.

- [ ] **Step 4: Extract logic into the library**

Create `packages/python/javdb_platform/rollback/__init__.py`:

```python
from packages.python.javdb_platform.rollback.core import (  # noqa: F401
    RollbackPlan,
    RollbackRequest,
    RollbackResult,
    plan_rollback,
    apply_rollback,
)
```

Create `packages/python/javdb_platform/rollback/core.py` — copy the central logic from `apps/cli/rollback.py` and refactor into pure functions:

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

**Actual extraction**: open `apps/cli/rollback.py`, identify the planning function (call it `_compute_actions` or similar) and the apply function (call it `_execute_actions`), and move them into `core.py`. Replace the bodies above with the real logic. Then in `apps/cli/rollback.py`, replace the function definitions with imports from the library:

```python
# apps/cli/rollback.py (top, after existing imports)
from packages.python.javdb_platform.rollback import (
    RollbackRequest,
    plan_rollback,
    apply_rollback,
)
```

And rewrite the CLI's `main()` so that after argparse parses, it builds a `RollbackRequest` and calls the library functions instead of running its own inline logic.

- [ ] **Step 5: Run all rollback tests — verify no regression**

```bash
pytest tests/ -k "rollback" -v
```

Expected: all existing rollback tests pass (CLI behavior is unchanged); new library shape tests pass.

- [ ] **Step 6: Commit**

```bash
git add packages/python/javdb_platform/rollback/__init__.py \
        packages/python/javdb_platform/rollback/core.py \
        apps/cli/rollback.py \
        tests/unit/test_rollback_core_library.py
git commit -m "refactor(cli): extract rollback core into javdb_platform.rollback library"
```

---

## Task 12: `GET /api/sessions` list endpoint

Cursor-paginated list of ReportSessions with state filter.

**Files:**

- Create: `packages/python/javdb_platform/db_layer/sessions_repo.py`
- Modify: `apps/api/schemas/capabilities_payloads.py`
- Create: `apps/api/routers/sessions.py`
- Modify: `apps/api/services/runtime.py` (register)
- Create: `tests/integration/test_sessions_endpoints.py`

- [ ] **Step 1: Write the failing tests**

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

- [ ] **Step 2: Run — expect fail**

```bash
pytest tests/integration/test_sessions_endpoints.py -v
```

Expected: 404 failures.

- [ ] **Step 3: Implement repo + router**

Create `packages/python/javdb_platform/db_layer/sessions_repo.py`:

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

Add to `apps/api/schemas/capabilities_payloads.py`:

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

Create `apps/api/routers/sessions.py`:

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

Register in `runtime.py`.

- [ ] **Step 4: Run — expect pass**

```bash
pytest tests/integration/test_sessions_endpoints.py -v
```

Expected: 2 passes.

- [ ] **Step 5: Commit**

```bash
git add packages/python/javdb_platform/db_layer/sessions_repo.py \
        apps/api/routers/sessions.py \
        apps/api/services/runtime.py \
        apps/api/schemas/capabilities_payloads.py \
        tests/integration/test_sessions_endpoints.py
git commit -m "feat(api): add GET /api/sessions list endpoint"
```

---

## Task 13: `GET /api/sessions/{id}` detail endpoint

Returns session + associated movie/torrent writes (pending + committed + audit).

**Files:**

- Modify: `packages/python/javdb_platform/db_layer/sessions_repo.py` (add `get_detail()`)
- Modify: `apps/api/routers/sessions.py` (add route)
- Modify: `apps/api/schemas/capabilities_payloads.py` (add detail model)
- Modify: `tests/integration/test_sessions_endpoints.py`

- [ ] **Step 1: Write the failing test**

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

- [ ] **Step 2: Run — expect fail**

```bash
pytest tests/integration/test_sessions_endpoints.py -v -k "detail"
```

Expected: 2 failures.

- [ ] **Step 3: Implement**

Add to `sessions_repo.py`:

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

Add to `capabilities_payloads.py`:

```python
class SessionDetailResponse(BaseModel):
    session: SessionItem
    movies: list[dict]
    torrents: list[dict]
```

Add to `sessions.py` router:

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

- [ ] **Step 4: Run — expect pass**

```bash
pytest tests/integration/test_sessions_endpoints.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add packages/python/javdb_platform/db_layer/sessions_repo.py \
        apps/api/routers/sessions.py \
        apps/api/schemas/capabilities_payloads.py \
        tests/integration/test_sessions_endpoints.py
git commit -m "feat(api): add GET /api/sessions/{id} detail endpoint"
```

---

## Task 14: `POST /api/sessions/{id}/rollback` endpoint

Calls the rollback library from Task 11. Body: `{dry_run, include_pending, restore_from_audit}`.

**Files:**

- Modify: `apps/api/routers/sessions.py`
- Modify: `apps/api/schemas/capabilities_payloads.py`
- Modify: `tests/integration/test_sessions_endpoints.py`

- [ ] **Step 1: Write the failing tests**

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

- [ ] **Step 2: Run — expect fail**

```bash
pytest tests/integration/test_sessions_endpoints.py -v -k "rollback"
```

Expected: 3 failures.

- [ ] **Step 3: Implement**

Add to `capabilities_payloads.py`:

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

Add to `sessions.py` router:

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

- [ ] **Step 4: Run — expect pass**

```bash
pytest tests/integration/test_sessions_endpoints.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add apps/api/routers/sessions.py apps/api/schemas/capabilities_payloads.py tests/integration/test_sessions_endpoints.py
git commit -m "feat(api): add POST /api/sessions/{id}/rollback with dry-run support"
```

---

## Task 15: `POST /api/sessions/{id}/commit` endpoint

For stuck-finalizing sessions, mirrors `apps/cli/commit_session.py`. Body: `{force, drop_pending}`.

**Files:**

- Modify: `apps/cli/commit_session.py` (extract core if not already library-shaped)
- Modify: `apps/api/routers/sessions.py`
- Modify: `apps/api/schemas/capabilities_payloads.py`
- Modify: `tests/integration/test_sessions_endpoints.py`

- [ ] **Step 1: Inspect commit_session structure**

Run:

```bash
grep -n "^def \|argparse\|add_argument" apps/cli/commit_session.py | head -20
```

Identify the central function. If it's already library-shaped, reuse. Otherwise, extract similarly to Task 11 (smaller in scope — `commit_session.py` is much smaller than `rollback.py`).

- [ ] **Step 2: Write the failing tests**

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

- [ ] **Step 3: Implement**

If `apps/cli/commit_session.py` is monolithic, extract its core into `packages/python/javdb_platform/sessions/commit.py`:

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

Then rewrite `apps/cli/commit_session.py` to import and call this.

Add to `capabilities_payloads.py`:

```python
class SessionCommitPayload(BaseModel):
    force: bool = False
    drop_pending: bool = False


class SessionCommitResponse(BaseModel):
    session_id: str
    new_state: str
    pending_dropped: int = 0
```

Add to `sessions.py` router:

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

- [ ] **Step 4: Run — expect pass**

```bash
pytest tests/integration/test_sessions_endpoints.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

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

## Task 16: Tighten Pydantic response models on FE-consumed existing endpoints

The FE-consumed existing endpoints (tasks, auth, config, explore) currently return ad-hoc dicts in many places. Land typed `response_model=` on each to make `openapi.json` useful for `openapi-typescript`.

**Files:**

- Modify: `apps/api/routers/tasks.py`, `auth.py`, `config.py`, `explore.py`
- Modify: `apps/api/schemas/payloads.py`
- Create: `tests/integration/test_openapi_response_shapes.py`

- [ ] **Step 1: Survey current return types**

Run:

```bash
grep -n "return {" apps/api/routers/tasks.py apps/api/routers/auth.py apps/api/routers/config.py apps/api/routers/explore.py | head -40
```

For each ad-hoc dict return, note the keys and their types. These become Pydantic response models.

- [ ] **Step 2: Write the failing OpenAPI shape test**

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

- [ ] **Step 3: Run — expect fail for endpoints still using ad-hoc dicts**

```bash
pytest tests/integration/test_openapi_response_shapes.py -v
```

Expected: failures listing each currently-untyped endpoint.

- [ ] **Step 4: Add Pydantic response models for each**

For each endpoint in the failure list:

1. In `apps/api/schemas/payloads.py`, add a `*Response` model matching the current return dict.
2. In the router, add `response_model=XxxResponse` to the decorator AND change the return statement to construct the model.

Example: `/api/tasks/stats` currently returns `{"running": 2, "success": 145, "failed": 1}`. Add:

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

Repeat for every endpoint in the test's `must_be_typed` list.

- [ ] **Step 5: Run — expect pass**

```bash
pytest tests/integration/test_openapi_response_shapes.py -v
```

Expected: pass.

- [ ] **Step 6: Run the full test suite to ensure no behavioral regression**

```bash
pytest tests/unit/ tests/integration/ -x
```

Expected: no failures other than pre-existing unrelated ones.

- [ ] **Step 7: Commit**

```bash
git add apps/api/routers/ apps/api/schemas/payloads.py tests/integration/test_openapi_response_shapes.py
git commit -m "refactor(api): add typed response models on FE-consumed endpoints"
```

---

## Task 17: Verify existing `sync-cookie` and `login/refresh` endpoints surface in OpenAPI

These exist already but are not in `must_be_typed` because they may already be typed. Confirm.

**Files:**

- Modify: `tests/integration/test_openapi_response_shapes.py`

- [ ] **Step 1: Extend the typed-shapes test**

Append to `tests/integration/test_openapi_response_shapes.py`:

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

- [ ] **Step 2: Run — fix endpoints that are still ad-hoc**

```bash
pytest tests/integration/test_openapi_response_shapes.py::test_login_refresh_and_sync_cookie_exist_and_typed -v
```

If failing: locate the route in `apps/api/routers/system.py` (`/api/login/refresh`) or `apps/api/routers/explore.py` (`/api/explore/sync-cookie`), add a Pydantic response model the same way as Task 16.

- [ ] **Step 3: Run — expect pass**

```bash
pytest tests/integration/test_openapi_response_shapes.py -v
```

Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_openapi_response_shapes.py apps/api/routers/
git commit -m "refactor(api): type response models on login/refresh + sync-cookie"
```

---

## Task 18: Regenerate openapi.json + document new endpoints

Final pass — run the dumper and update developer docs.

**Files:**

- Modify: `docs/api/openapi.json` (regenerated)
- Modify: `docs/en/developer/api-reference.md`
- Modify: `docs/zh/developer/api-reference.md`

- [ ] **Step 1: Regenerate openapi.json**

```bash
STORAGE_BACKEND=sqlite API_SECRET_KEY=dump-only-secret python3 scripts/dump_openapi.py
```

Expected: writes `docs/api/openapi.json`. Verify all 10 new endpoint paths are present:

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

Expected: `missing: none`.

- [ ] **Step 2: Update English developer docs**

Open `docs/en/developer/api-reference.md`. Find the existing "Endpoints" section. Add a new subsection:

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

- [ ] **Step 3: Update Chinese developer docs**

Open `docs/zh/developer/api-reference.md`. Add the paired Chinese section with the same structure (translate prose, keep paths/method names verbatim).

- [ ] **Step 4: Commit**

```bash
git add docs/api/openapi.json docs/en/developer/api-reference.md docs/zh/developer/api-reference.md
git commit -m "docs(api): document Phase 1 frontend console endpoints + regenerate schema"
```

---

## Final verification

- [ ] **Run the full test suite**

```bash
pytest tests/unit/ tests/integration/ -v
```

Expected: no failures introduced by this plan.

- [ ] **Verify OpenAPI completeness**

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

Expected: 10+ new paths printed.

- [ ] **Build the API Docker image**

```bash
docker build -f docker/Dockerfile.api -t javdb-autospider-api:plan-a .
docker run --rm -d --name jas-api-final -p 18101:8100 \
    -e STORAGE_BACKEND=sqlite -e API_SECRET_KEY=plan-a-test \
    javdb-autospider-api:plan-a
sleep 4
curl -sf http://127.0.0.1:18101/api/capabilities && echo
docker rm -f jas-api-final
```

Expected: capabilities JSON prints.

---

## Plan summary

| # | Task | Files | Tests |
|---|------|-------|-------|
| 1 | API Dockerfile | `docker/Dockerfile.api` | docker smoke |
| 2 | `publish-api-image.yml` | `.github/workflows/` | yaml parse |
| 3 | `publish-openapi.yml` + dumper | `.github/workflows/`, `scripts/dump_openapi.py` | yaml parse + dump |
| 4 | `GET /api/capabilities` | router + schema + conftest | 3 integration tests |
| 5 | `POST /api/test/reset` (gated) | router | 2 integration tests |
| 6 | `system_state` table + repo | migration + repo | 5 unit tests |
| 7 | `GET/PUT /api/system/state` | router | 4 integration tests |
| 8 | `GET /api/onboarding/status` | router | 1 integration test |
| 9 | `POST /api/onboarding/test` | router | 2 integration tests |
| 10 | `POST /api/onboarding/complete` + `dismiss-hint` | router | 4 integration tests |
| 11 | Rollback library extraction | `javdb_platform/rollback/` + CLI refactor | 1 unit + existing |
| 12 | `GET /api/sessions` | repo + router | 2 integration tests |
| 13 | `GET /api/sessions/{id}` | router | 2 integration tests |
| 14 | `POST /api/sessions/{id}/rollback` | router | 3 integration tests |
| 15 | `POST /api/sessions/{id}/commit` + commit library | router | 3 integration tests |
| 16 | Typed response models on existing endpoints | routers + payloads | 1 schema test |
| 17 | Verify `sync-cookie` + `login/refresh` typed | tests | extends 16 |
| 18 | Regenerate openapi.json + docs | docs | manual |

**18 tasks, ~35 new tests (12 unit + 23 integration), ~16 commits.**

## Known follow-ups (intentionally deferred from Plan A)

- **Error envelope wrapping.** Spec §8.3 specifies BE error responses as `{"error": {"code", "message", "details", "request_id"}}`. FastAPI's default `HTTPException` produces `{"detail": ...}` instead. The new endpoints currently raise `HTTPException(detail={"error": {...}})` which works but produces `{"detail": {"error": ...}}` — one extra nesting level. A follow-up task should register a custom exception handler that unwraps `detail.error` to top-level `error`. Add to the next BE-side plan or to Plan F (cutover). FE wrappers in Plan B will compensate in the meantime by checking both shapes.
- **Rollback library extraction (Task 11)** intentionally leaves the body of `plan_rollback` / `apply_rollback` as "migrate from apps/cli/rollback.py" rather than pasting the existing ~400 LOC inline. The engineer must read `apps/cli/rollback.py`, identify the planning and apply functions, and move them. Same shape for `commit_session` in Task 15.
- **MovieClaim cleanup endpoints** (`sweep_movie_claim_stages`, `cleanup_stale_in_progress`) are Phase 2 endpoints per spec §8.4 — not in Plan A.
- **Task 11 layering inversion (intentionally deferred).** Commit `9f5e66a6` kept rollback pipeline logic in `apps/cli/rollback.py` and made `packages/python/javdb_platform/rollback/core.py` a thin adapter that calls back into the CLI. This preserves all 111 existing rollback tests without modification but creates a `packages/` -> `apps/cli/` import (the only one). Tasks 12-18 depend on the library's public API (`RollbackRequest`/`plan_rollback`/`apply_rollback`) which IS correct; the internal inversion only affects future maintenance. Recommended cleanup as a Phase 2 BE task — see spec §12.3.
- **Minor cleanup items from Task 11 review (`9f5e66a6`):**
  - `core.py:124-143` `_session_exists` swallows DB errors silently. Add a `logger.warning` so transient errors are observable.
  - `RollbackRequest.restore_from_audit` (`core.py:68`) is currently informational only. Either wire it to refuse audit replay when False, or remove the field.
  - `_drive_rollback`'s `_no_targets` sentinel could be a typed `(summary | None, exit_code)` return — cosmetic.
  - `apply_rollback` (`core.py:167`) discards `_exit_code` from `_drive_rollback`. CLI maps exit codes (2/3/4) to failure modes; HTTP callers may need this. Consider surfacing as `RollbackResult.summary["outcome"]` ∈ `{ok, refused, partial_drift}`.
- **Task 15 commit side-effect parity (intentionally deferred).** Commit `eb48230e`'s `packages/python/javdb_platform/sessions/commit.py` does NOT replicate side-effects that `apps/cli/commit_session.py` performs: (a) `fanout_movie_claim(..., operation="commit")` to the Cloudflare Worker coordinator, (b) JSONL drift records via `append_jsonl_record`, (c) `write_github_output` for downstream workflow steps. The library calls `db_mark_session_committed` + `db_commit_session_history` directly. Operators using `POST /api/sessions/{id}/commit` should understand it's a "DB-only" commit; coordinator MovieClaim stages will remain in `staged` state. **Recommended cleanup (Phase 2):** add `fanout_coordinator: bool = False` to `CommitRequest` and call the coordinator client when True. Or — split CLI's commit into a small library that handles the post-DB side-effects too.

Phase 1 BE prerequisites end here. After completion, Plan B (new repo bootstrap) can start in parallel with main repo work — the new repo just consumes the published `openapi.json` and image.
