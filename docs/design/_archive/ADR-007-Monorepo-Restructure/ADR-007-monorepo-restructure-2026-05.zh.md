# ADR-007: Monorepo 重构 —— 顶层 `javdb/` 命名空间与分阶段推进

**状态**: 已完成 2026-05-17 —— Phase 1+2+3 全部退役遗留路径（`api/`、`migration/`、`legacy/`、`packages/`、`compat.py`、`pipeline.py`、扁平的 `apps/cli/<name>.py` shim）。顶层 `javdb/` 命名空间与 `apps/cli/<subdir>/` 布局成为正式形态（详见 CLAUDE.md）。残留的 `utils/__pycache__/` 与 `scripts/spider/__pycache__/` 仅含过期字节码——已无源代码。
**日期**: 2026-05-17
**决策者**: 架构深化第三轮（承接 [docs/design/architecture/python-core-mapping.md](../../architecture/python-core-mapping.md) 与 [spider-module-reorg.md](../../architecture/spider-module-reorg.md) 未完成的 Python core 重构）
**取代 (Supersedes)**: `docs/design/architecture/python-core-mapping.md`、`docs/design/architecture/spider-module-reorg.md`（Phase 3 时标记为 superseded）
**关联实现计划 (Related Implementation Plans)**: [IMP-ADR007-01](IMP-ADR007-01-restructure-phase1-javdb-tree.md)（Phase 1 —— 构建 `javdb/` 树，已完成）、[IMP-ADR007-02](IMP-ADR007-02-restructure-phase2-scripts-to-cli.md)（Phase 2 —— `scripts/` 迁入 `apps/cli/`，已完成）、[IMP-ADR007-03](IMP-ADR007-03-restructure-phase3-delete-compat.md)（Phase 3 —— 执行 deletion manifest，已完成）；另见 [ADR-007 deletion manifest](ADR-007-deletion-manifest.md)（Phase 1 交付物，Phase 3 据其执行）。

---

## 背景 (Context)

仓库里残留着一次**未完成**的重构创伤：

- `packages/python/` 与 `apps/` 是 canonical 代码所在地。
- `utils/`、`api/`、`migration/`、`legacy/`、根 `pipeline.py`、根 `compat.py`，以及 `scripts/` 下的若干文件和子包，全部只是 compat wrapper（`alias_module(__name__, "packages.python...")`）。它们共贡献约 200 行纯转发壳，并在仓库根多塞了五个"看起来是代码、实际不是"的顶层目录。
- `packages/python/javdb_platform/` 内部约 40 个文件平铺在顶层：`db_*.py × 12`、`proxy_*.py × 5`、`*_client.py × 5`，再加上基础设施 helper（`config_helper`、`logging_config`、`csv_writer` 等）与编排胶水（`pipeline_service`、`history_manager`、`spider_gateway`）。找任何一个组件的真实主程序都得在这片平铺命名空间里二分查找。
- `packages/python/javdb_core/` 名实不副：里面装的是 spider 解析原语（`parser`、`contracts`、`url_helper`、`filename_helper`、`magnet_extractor`、`masking`），并不是比 spider 更高阶的抽象。**这个项目的"core"本来就是 spider 本身。**
- `scripts/` 混了三类内容：真正被 workflow 调用的 CLI（`audit_archive`、`aggregate_pending_health`、`pending_mode_alert_and_pause`）、纯 compat 壳（`login.py`、`pikpak_bridge.py` 等）、以及 CI 内部工具（`ci/select_tests.py`、`ci/sync_docs_to_wiki.py`）。不打开文件分辨不出哪个是面向用户的入口、哪个只是转发器。
- `packages/` 让每条 import 多了两层无意义前缀：`from packages.python.javdb_platform.db_layer.history_repo import HistoryRepo`（6 段）。

在 `packages/`、`apps/`、`tests/` 内 grep，已有 **464** 处使用 canonical 路径，另有 **202** 处仍走 legacy 壳——主要集中在 `tests/` 和 `migrate_to_current.py`。Workflow 中 5 处引用 legacy 路径（如 `python3 -m scripts.aggregate_pending_health`）。Dockerfile 的 `COPY` 指令引用了 4 个 legacy 顶层目录，但并不真正执行其中代码。

上一轮重构（记录在 `python-core-mapping.md` 与 `spider-module-reorg.md`）只重组了 spider 包内部，没动 `javdb_platform/`、`javdb_integrations/`、顶层布局，也没删 compat 壳。本 ADR 接续并完成这部分工作。

---

## 决策 (Decision)

执行三阶段重构，做以下事：

1. **折叠 `packages/python/` 前缀**为一个项目级顶层 namespace `javdb/`（PEP 420 namespace package，不放 `__init__.py`）。
2. **拆分 `javdb_platform/`** 为若干职责单一的顶层包（直接挂在 `javdb/` 下）。
3. **`javdb_core/` 折进 `javdb/spider/`**（其中 `masking.py` 去 `javdb/infra/`）。
4. **`javdb_ingestion/` 改名为 `javdb/pipeline/`**——与 workflow、CLI 用语对齐。
5. **Rust crate** 源码搬到 `javdb/rust_core/`；maturin 安装名改为 `javdb.rust_core`。
6. **`scripts/` 真实代码迁移**到 `apps/cli/` 下按职责分子目录。
7. **删除所有 compat 壳**，同步更新每一处 import、test、doc、wiki、Dockerfile 引用。

### 最终顶层布局

```
JAVDB_AutoSpider_CICD/
├── apps/
│   ├── api/                  # FastAPI 服务（结构不变）
│   ├── cli/                  # 所有 Python CLI 入口
│   │   ├── spider.py, pipeline.py, login.py       # 顶层核心入口
│   │   ├── db/, qb/, pikpak/, rclone/, notify/, ops/   # 按职责分子目录
│   │   └── README.md
│   ├── web/, desktop/        # 本次不动（FE 在另一仓库重写中）
│   └── reports/, logs/
├── javdb/                    # ★ Python namespace（PEP 420；此层无 __init__.py）
│   ├── spider/               # 抓取运行时 + parser/contracts/url/filename/magnet + auth/login
│   ├── pipeline/             # 编排（原 ingestion）+ pipeline service
│   ├── storage/              # db/、repos/、sessions/、rollback/、d1、dual_connection、history_manager
│   ├── proxy/                # pool、ban_manager、policy、recommend/、coordinator/
│   ├── integrations/         # qb/、pikpak/、rclone/、notify/
│   ├── infra/                # config、logging、paths、csv_writer、git_helper、request、masking、fetch_page、health_check、config_generator
│   ├── migrations/           # SQL + Python migrate 工具
│   └── rust_core/            # ★ Rust crate 源；安装为 `javdb.rust_core`
├── docker/, docs/, tests/
├── scripts/                  # 只剩 ci/ + verify_*.sh
├── reports/, logs/, node_modules/
├── config.py, config.py.example, requirements.txt, package.json, pytest.ini
└── README.md, README_CN.md, CLAUDE.md, CONTEXT.md
```

### 被彻底删除的顶层条目

`packages/`、`utils/`、`api/`、`migration/`、`legacy/`、根 `compat.py`、根 `pipeline.py`、compat 子包 `scripts/spider/` 与 `scripts/ingestion/`，以及 `scripts/<name>.py` 系列已被 `apps/cli/` 取代的壳文件。

### 命名规则

- **`javdb/` 下的顶层包**用**领域词**命名：`spider`、`pipeline`、`storage`、`proxy`、`integrations`、`infra`、`migrations`。包内部不再带 `javdb_` 前缀——外层 namespace 已经把项目身份给覆盖了。
- **叶子文件名**去掉与父目录重复的前后缀。例：`qb_uploader.py` 在 `apps/cli/qb/` 下变 `uploader.py`；`rclone_manager.py` 在 `apps/cli/rclone/` 下变 `manager.py`；`email_notification.py` 在 `apps/cli/notify/` 下变 `email.py`。
- **CLI 子目录**按"作用对象"分：`db/`（数据库/会话运维）、`qb/`、`pikpak/`、`rclone/`、`notify/`、`ops/`（诊断与开发工具）。3 个核心入口留在 `apps/cli/` 顶层：`spider.py`、`pipeline.py`、`login.py`。

### Rust crate

- 源码 `packages/rust/javdb_rust_core/` → `javdb/rust_core/`。
- `pyproject.toml` 加 `[tool.maturin] module-name = "javdb.rust_core"`；`[project] name`（决定 wheel 名）保留 `javdb_rust_core` 或同步改名均可。
- 15+ 处 Python `from javdb_rust_core import ...` 全改为 `from javdb.rust_core import ...`。
- `javdb/` 必须保持 PEP 420 namespace package（`javdb/` 这一层不放 `__init__.py`）；maturin 安装到 `site-packages/javdb/` 下的扩展模块通过 namespace package merging 与本地源码树共存。
- 源码目录 `javdb/rust_core/` 内只放 Rust crate 文件（无 `.py`、无 `__init__.py`）；编译产物 `.so` 由 maturin 装到 `site-packages` 里，import 时 Python 走 path-based importer 查找。

---

## 三阶段推进 (Three-Phase Roll-out)

### Phase 1 —— 建立 `javdb/` 树（最大的单 PR）

任务：

1. 在仓库根建 `javdb/`（不放 `__init__.py`）。
2. `packages/python/javdb_spider/` → `javdb/spider/`，并把 `javdb_core/` 中的 `parser.py`、`contracts.py`、`url_helper.py`、`filename_helper.py`、`magnet_extractor.py` 合入；新建 `javdb/spider/auth/login.py`（从 `javdb_integrations/login.py` 搬入）。
3. `packages/python/javdb_ingestion/` 改名为 `javdb/pipeline/`；把 `javdb_platform/pipeline_service.py` 合入为 `javdb/pipeline/service.py`。
4. 打散 `packages/python/javdb_platform/`：
   - `javdb/storage/` —— `db.py` + `db_*.py × 9` + `d1_client`、`dual_connection`、`sqlite_datetime`、`history_manager`，以及原有的 `db_layer/`（改名 `repos/`）、`sessions/`、`rollback/`。
   - `javdb/proxy/` —— `proxy_ban_manager`、`proxy_policy`、`proxy_pool`，并新设 `recommend/`（`recommend_proxy_*`）与 `coordinator/`（`do_client_base`、`proxy_coordinator_client`、`login_state_client`、`movie_claim_client`、`runner_registry_client`、`work_distributor_client`）。coordinator 放在 `proxy/` 下，因为 Worker DO 协调只在 proxy-pool 模式下激活。
   - `javdb/infra/` —— `config_helper`（→ `config.py`）、`config_generator`、`csv_writer`、`git_helper`、`logging_config`（→ `logging.py`）、`path_helper`（→ `paths.py`）、`request_handler`（→ `request.py`）。
   - `spider_gateway.py` 上提到 `javdb/spider/` 下。
5. 拆 `packages/python/javdb_integrations/`：
   - `javdb/integrations/qb/` ← `qb_client`、`qb_file_filter`、`qb_uploader`，以及从 `javdb_platform/` 搬入的 `qb_config.py`。
   - `javdb/integrations/pikpak/` ← `pikpak_bridge`。
   - `javdb/integrations/rclone/` ← `rclone_helper`、`rclone_manager`。
   - `javdb/integrations/notify/` ← `email_notification`。
   - `javdb/spider/auth/` ← `login`。
   - `javdb/infra/` ← `fetch_page`、`health_check`、以及来自 `javdb_core/` 的 `masking`。
6. 分发 `packages/python/javdb_platform/bridges/rust_adapters/`：`csv_adapter` 合入 `javdb/infra/csv_writer.py`；`request_adapter` 合入 `javdb/infra/request.py`；`dedup_adapter` 合入 `javdb/spider/services/dedup.py`；`parser_adapter` 合入 `javdb/spider/parser.py`；`history_adapter` 合入 `javdb/storage/history_manager.py`。`bridges/` 这个概念被消除。
7. Rust crate `packages/rust/javdb_rust_core/` → `javdb/rust_core/`，更新 `pyproject.toml`（`module-name = "javdb.rust_core"`），把 15+ 处 `from javdb_rust_core import` 全改为 `from javdb.rust_core import`。
8. 删除现在已空的 `packages/` 目录。
9. 全量更新 `javdb/` 与 `apps/` 内部 imports（约 600 处）：`packages.python.javdb_*` → `javdb.*`。
10. **临时**更新每一处 legacy compat 壳（`utils/*`、`api/*`、`migration/*`、`scripts/spider/*`、`scripts/ingestion/*`），把它们的 `alias_module(__name__, "packages.python...")` 目标重新指向新的 `javdb.*` 路径。壳依然活着，只是转发到新位置。
11. 更新所有引用 `packages/rust/javdb_rust_core` 路径的 CI 配置和构建步骤：`.github/workflows/build-rust-extension.yml`、`.github/actions/install-rust-wheel/action.yml`、`docker/Dockerfile`、`docker/Dockerfile.api`。
12. 每个新目录写一份 `README.md`：顶部一句话说明目录职责，下面用表格列出每个 `.py` 文件及 1–2 行说明。
13. **生成 deletion manifest**，落地为 `docs/design/adr/ADR-007-deletion-manifest.md`，列出 Phase 3 必须移除的每一处 compat 残留，含精确文件路径与（针对 tests）精确行号。

验证门槛（全部必须通过）：

- `pytest tests/` 全过（unit + integration + smoke）。
- 现有 `apps/cli/*.py --help` 全部成功。
- `python3 -c "import javdb.spider, javdb.pipeline, javdb.storage, javdb.proxy, javdb.integrations, javdb.infra, javdb.migrations, javdb.rust_core"` 成功。
- 在 `javdb/rust_core/` 执行 `maturin develop --release` 生成可用 wheel。

### Phase 2 —— `scripts/` 迁移到 `apps/cli/`

任务：

1. 真实代码搬到 `apps/cli/<subdir>/`，并按"去重复前缀"规则改名：
   - `scripts/audit_archive.py` → `apps/cli/db/audit_archive.py`
   - `scripts/aggregate_pending_health.py` → `apps/cli/db/pending_health.py`
   - `scripts/pending_mode_alert_and_pause.py` → `apps/cli/db/pending_alert.py`
   - `scripts/cleanup_stale_session_audits.py` → `apps/cli/db/cleanup_stale_session_audits.py`
   - `scripts/sync_d1_to_sqlite.py` → `apps/cli/db/sync_d1_to_sqlite.py`
   - `scripts/dump_openapi.py` → `apps/cli/ops/dump_openapi.py`
   - `scripts/rclone_*.py`（5 个）→ `apps/cli/rclone/*.py`（去 `rclone_` 前缀）
   - `scripts/check_bake_metrics.py` → `apps/cli/ops/check_bake_metrics.py`
   - `scripts/profile_hot_paths.py` → `apps/cli/ops/profile_hot_paths.py`
   - 已存在的 `apps/cli/qb_uploader.py` → `apps/cli/qb/uploader.py`（`qb_file_filter`、`pikpak_bridge`、`rclone_manager`、`email_notification` 同此规则迁入对应子目录）。
   - 现 `apps/cli/` 顶层的 `{rollback,migration,audit_archive,commit_session,cleanup_stale_in_progress,sweep_movie_claim_stages}.py` 移到 `apps/cli/db/`。
2. 删除 `scripts/spider/`、`scripts/ingestion/`、`scripts/_spider_legacy.py`。
3. 删除已被 `apps/cli/` 取代的 `scripts/<name>.py` 壳。
4. 更新所有调用 `python3 -m scripts.X` 或 `python scripts/X.py`（`scripts/ci/*` 除外）的 workflow YAML：`DailyIngestion.yml`、`AdHocIngestion.yml`、`AuditArchive.yml`、`publish-openapi.yml`。
5. 写 `apps/cli/README.md`（子命令地图）+ 各子目录 README。
6. 写 `scripts/README.md`，注明"本目录只放 CI Python 与 shell 运维脚本；用户入口在 `apps/cli/`"。

验证门槛：

- `pytest tests/` 全过。
- 每个 `apps/cli/**/*.py --help` 成功（或 `python -m apps.cli.<subdir>.<name> --help`）。
- `grep -rE "python3? -m scripts\.|python scripts/" .github/workflows | grep -v 'scripts/ci/'` 返回空。
- Workflow YAML lint 干净。

### Phase 3 —— 删 compat 壳（按 manifest 精准删）

任务：

1. 改写 tests 中约 202 处 import：`from utils.X`、`from scripts.X`、`from api.X`、`from migration.X` → canonical 的 `from javdb.X`（或 `from apps.api.X`）。具体文件与行号列在 `ADR-007-deletion-manifest.md`。
2. 改写 `javdb/migrations/migrate_to_current.py` 的自引用（`from migration.tools.*` → `from javdb.migrations.tools.*`）。
3. 删目录：`utils/`、`api/`、`migration/`、`legacy/`。
4. 删根文件：`compat.py`、`pipeline.py`。
5. 删 `docker/Dockerfile` 第 54–57 行与 `docker/Dockerfile.api` 第 45–48 行（`COPY utils/`、`COPY api/`、`COPY legacy/`、`COPY migration/` 这几条指令）。
6. 更新 `docs/handbook/en/**/*.md`、`docs/handbook/zh/**/*.md`、`README.md`、`README_CN.md`、`CLAUDE.md`、`CONTEXT.md`——把所有 `packages.python.javdb_*` 与 legacy 路径引用替换为新的 `javdb.*` 路径。
7. 把 `docs/design/architecture/python-core-mapping.md` 与 `docs/design/architecture/spider-module-reorg.md` 标记为 **superseded**（顶部加一行指向本 ADR），内容替换为指向新地图的 redirect。
8. 新建 `docs/design/architecture/python-tree-2026-05.md`（重构后的新地图）。
9. 触发 `.github/workflows/sync-docs-to-wiki.yml`，让 wiki 从更新后的 `docs/handbook/en/` 重新生成。
10. 如有过期路径，更新 `scripts/ci/wiki_mapping.json`。

验证门槛：

- `pytest tests/` 全过。
- 所有 `apps/cli/**/*.py --help` 成功。
- `grep -rE "from (utils|api|migration|legacy)\." . --include='*.py' | grep -v __pycache__` 返回空（排除 `docs/design/architecture/audit-report-*.md` 这种历史记录）。
- `grep -rE "from scripts\.(spider|ingestion|audit_archive|aggregate_pending_health|pending_mode_alert_and_pause|cleanup_stale_session_audits|sync_d1_to_sqlite|dump_openapi)" .` 返回空。
- `ls utils/ api/ migration/ legacy/ scripts/spider/ scripts/ingestion/ 2>&1 | grep -c "No such"` 等于 6。
- `python3 -c "import compat"` 抛 `ModuleNotFoundError`。
- `docker build -f docker/Dockerfile .`（dry build）成功。
- `ADR-007-deletion-manifest.md` 中每个 checkbox 都已勾。

---

## 备选方案 (Alternatives Considered)

### 备选 A —— 保留 `packages/python/` 伞名，只清内层壳

**否决**。四段 import 前缀冗长是 code review 与新人定位的最普遍抱怨。保留 `packages/` 能省下 pyproject 几行改动，但代价是未来每条 import 都要付这个开销。

### 备选 B —— 顶层 `src/`（PEP src-layout）+ pytest pythonpath 短路

**否决**。这样 import 最短（`from spider import …`），但 `spider`、`pipeline`、`storage`、`infra` 太通用，与 PyPI 包或 stdlib 撞名的风险真实存在；traceback 里也读不出"这个 spider 是 JavDB 的还是别的"。给 namespace 多留一段（`javdb.*`）值得。

### 备选 C —— Phase 1 同 PR 里直接删 compat 壳

**否决**。单 PR 把 rename + test 改写 + doc 更新 + Dockerfile 编辑塞在一个 diff 里，review 几乎不可能。临时 compat 策略让每个 phase 都能独立 review；deletion manifest 防止"临时态"逃逸到 Phase 3 之外。

### 备选 D —— 每个子包保留 `javdb_` 前缀（如 `javdb/javdb_spider/`）

**否决**。用户明确指出冗余。一旦 `javdb/` namespace 存在，子名再加 `javdb_` 就是双重命名。

### 备选 E —— 把 `coordinator/` 拔成顶级独立包

**否决**。Worker DO 协调（movie claim、work distributor、runner registry、login state）只在 proxy-pool 模式下激活，是代理管理的一个 sub-feature，不是独立横切关注点。`javdb/proxy/coordinator/` 是正确归属。

### 备选 F —— Deprecation 缓冲期（compat 壳保留 1–2 个 release 并加 `DeprecationWarning`）

**否决**。用户明确排除唯二的外部消费者（兄弟 `wiki` 仓库与 `proxy-coordinator` 仓库），其他外部调用者不存在。缓冲期只会延长仓库同时对外暴露两条合法 import 路径的时间，与清理目标背道而驰。

---

## 风险与缓解

| 风险 | 严重度 | 缓解 |
|---|---|---|
| 重排后浮出隐式循环 import（如 `javdb/spider/contracts` ↔ `javdb/pipeline` ↔ `javdb/storage`） | 高 | Phase 1 验证门槛跑全量 pytest + CLI smoke 导入每个包顶层。如出现循环，用 `TYPE_CHECKING` 或局部 import 拆环。 |
| `scripts/ci/select_tests.py`（impact-based 测试选择）path 映射过期后选错测试 | 中 | 三个 phase 都强制跑**全量** `pytest tests/`（不走 impact 选择）。Phase 3 内补丁 selector。 |
| Phase 3 路径大改后 wiki 同步异常 | 中 | Phase 3 PR 在合并前 dry-run `scripts/ci/sync_docs_to_wiki.py --wiki-dir /tmp/wiki-test`。 |
| Docker 镜像 build 失败（COPY 指向被删的顶层目录） | 中 | Phase 3 验证含本地 `docker build -f docker/Dockerfile .`（不 push）。 |
| Rust namespace package 机制让 pytest pythonpath 解析意外 | 中 | Phase 1 验证显式断言同一 Python 进程内 `import javdb.spider`（源码树）与 `import javdb.rust_core`（maturin 安装）都成功。`javdb/` 不能有 `__init__.py`。 |
| Tests 通过动态字符串引用 compat 壳（如 `subprocess.run(["python3", "-m", "utils.X"])`）grep 漏检 | 低 | Phase 1 额外扫字符串形态的模块路径（`grep -rEn '"utils\.|"scripts\.spider\.'`）。命中项加入 deletion manifest 作为显式 follow-up。 |
| 用户本地脚本 import legacy 路径 | 低 | 用户已确认外部消费者仅 `wiki` 与 `proxy-coordinator` 两个兄弟仓库且均排除。本地脚本由用户自行更新。 |

---

## Deletion Manifest

Phase 3 必须移除的每一处 compat 残留，以行级精度落地为 `docs/design/adr/ADR-007-deletion-manifest.md`，由 Phase 1 PR 生成并提交。Phase 3 PR 描述里引用该文件并逐项打勾。Manifest 列出：

- 待删目录（`utils/`、`api/`、`migration/`、`legacy/`、`scripts/spider/`、`scripts/ingestion/`，以及空了之后的 `packages/`）。
- 待删根文件（`compat.py`、`pipeline.py`）。
- 具体 Dockerfile 行号。
- 每一个测试文件 + 行号，及该行 legacy import 应改写成的目标路径。
- 验证用的 grep 命令与预期输出（应为空）。

---

## 实施顺序（PR 序列）

```
PR-1  Phase 1: 建 javdb/ 树，全量更新内部 import，临时把 compat 壳重定向，
      搬 Rust crate，生成 deletion manifest                               [target #?]
      验证：full pytest + CLI smoke + maturin develop

PR-2  Phase 2: scripts/ → apps/cli/<subdir>/ 迁移，更新 workflow YAML，
      删 scripts/ 残壳                                                    [依赖 PR-1]
      验证：full pytest + apps/cli/**/--help smoke + workflow lint

PR-3  Phase 3: 改写 test imports，删 compat 壳，清 Dockerfile COPY 行，
      同步 docs/wiki/README，superseded legacy maps，
      ADR-007 deletion manifest 全部勾完                                  [依赖 PR-2]
      验证：full pytest + grep gates 返回空 + docker build dry-run
```

每个 PR 都可独立回滚。PR-1 落地后仓库处于一致状态（legacy import 仍通过重定向的壳能跑）。PR-2 落地后仓库处于一致状态（`apps/cli/` 是用户入口）。PR-3 完成清理。

---

## 后续 (Follow-Ups，不在本 ADR 范围)

- `javdb/storage/history_manager.py` 现在还有一截 policy 函数 re-export（从 `javdb.pipeline.policies` 转发的 `should_process_movie`、`determine_torrent_type` 等），保留给旧调用方。等调用方都直接 import `javdb.pipeline.policies` 之后，这截 re-export 可以删。属于 Phase 3 之后的独立小 PR。
- `apps/web/` 与 `apps/desktop/` 预计在新前端仓 `javdb-autospider-web` GA 之后移除。届时单开 ADR 或 PR 处理。
- `scripts/ci/select_tests.py` 可能需要按新路径结构升级；Phase 3 内补丁了即时 path map，更彻底的 test-selection 重写是另一个独立议题。

---

## 相关 ADR 与文档

- 取代 (Supersedes)：[`docs/design/architecture/python-core-mapping.md`](../../architecture/python-core-mapping.md)、[`docs/design/architecture/spider-module-reorg.md`](../../architecture/spider-module-reorg.md)
- 协调 (Coordinates with)：[ADR-005](../ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.md)（`db.py` 退役；storage 内部布局由本 ADR 重排；ADR-005 D2 PR-1 将在新的 `javdb/storage/` 树内操作）
- 协调：[ADR-006](../ADR-006-Pending-Mode-Rollout/ADR-006-pending-mode-default-rollout.md)（pending-mode 推全；`pending_mode_alert_and_pause.py` 是 Phase 2 迁移的脚本之一；改名为 `apps/cli/db/pending_alert.py` 不变行为，但 workflow YAML 必须同 PR 更新）
- 新地图（重构后）：`docs/design/architecture/python-tree-2026-05.md`（Phase 3 内新建）
