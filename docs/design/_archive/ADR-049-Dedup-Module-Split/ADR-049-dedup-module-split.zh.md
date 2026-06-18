# ADR-049：把 `spider/services/dedup.py` 拆为 types / 纯查询 / store，并提升 `normalise_code`

| 字段       | 值                                                                 |
| ---------- | ----------------------------------------------------------------- |
| **状态**   | Completed（2026-06-14）——已由 [IMP-ADR049-01](IMP-ADR049-01-dedup-module-split.md) 实现 |
| **日期**   | 2026-06-13                                                        |
| **作者**   | Ted                                                              |
| **关联**   | [ADR-048](../../ADR-048-Rclone-Module-Split/ADR-048-rclone-module-split-and-folder-dedup-rust.md)（**清理时**去重——兄弟 ADR；本 ADR 拥有**跳过时**去重）、[ADR-046](../ADR-046-Retire-Db-Facade/ADR-046-retire-db-facade.md)（Repo 是唯一公开存储入口——`dedup_store` 经 `OperationsRepo` 读写）、[ADR-011](../ADR-011-Parsing-Module/ADR-011-javdb-parsing-module.md)（`normalise_code` 落户的解析模块）、[ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md)（`dedup_store` 消费的所有权账本读取）、[ADR-041](../ADR-041-Rust-Fallback-Policy/ADR-041-rust-fallback-policy.md)（`dedup_query` 包装的 Rust dedup bridge 保持 Best-Effort） |

> 源自 2026-06-13 架构评审（候选 2 ——"拆分 `dedup.py`"）：[architecture-review-2026-06-13.html](../../architecture/architecture-review-2026-06-13.html)。

## 背景（Context）

`javdb/spider/services/dedup.py`（779 行）是**跳过时去重**模块——判断是否跳过重新下载/上传一个已在 rclone 库存中的 `video_code`，并持久化 `DedupRecord` 行。它把三个接口深度不同的层交织在一个导入 seam 后：

1. **类型** —— `RcloneEntry`、`DedupRecord`（纯 NamedTuple）、`DEDUP_FIELDNAMES`（L96–132）。零依赖。
2. **纯查询** —— Rust dedup bridge 包装（L53–77）+ `should_skip_from_rclone` / `check_dedup_upgrade` / `check_redownload_dedup_upgrade` / `is_in_rclone_inventory`（L303–524）。接收一个已加载的内存库存 dict 并返回决策；无 I/O。
3. **store** —— 从 `OwnershipLedger`/`OperationsRepo`/CSV 加载库存（L143–296）+ `DedupRecord` 持久化（L531–779），持有**两个进程全局** `_db_initialised`（L80）与 `_pending_paths_cache`（L531），由 `tests/conftest.py` 的 autouse fixture 每个测试重置（L128–129）。

三处可度量的坏味道：

- **一个浅 seam 挡着三个 concern。** 任何 `dedup.py` 的导入方都加载三层。`pipeline/models.py` 在模块级导入 `DedupRecord`（纯 NamedTuple，L8）——从而把 Rust bridge `try/except` 与 `OperationsRepo` 拖进 `pipeline.models` 的每个传递性导入方（`planner`、`engine`、`detail/runner`、`apps/api/*`）。一个类型导入付了整个模块的加载代价。
- **一个私有归一化函数被跨包逐字复制。** `_normalise_code`（NFKC + strip + upper，L19–31）被复制进 `ops/reconcile/code_resolver.py`（L44–46），docstring 自承 *"identical to dedup._normalise_code"*。另外 `ops/reconcile/service.py` 懒导入 `spider/services/dedup` 的**私有** `_normalise_code`（L285、L425）—— 一次 `ops/` → `spider/`-私有的跨包拉扯。
- **CSV schema 重复。** `javdb/migrations/tools/csv_to_sqlite.py` 自带一份平行 `_DEDUP_FIELDNAMES` 列表（L265–277）。

套用删除测试：删掉 `dedup.py`，复杂度会在 9 个生产导入方重现——它名副其实，但应作为三个深模块、而非一个浅接口。

## 决策（Decision）

按层把 `dedup.py` 拆成三个模块，把归一化函数提升到其真正归宿，并删除单体。

### 设计决策（Design Decisions）

**D1. `javdb/spider/services/` 下的三个模块。**

| 模块 | 拥有 | 深度 |
| --- | --- | --- |
| `dedup_types.py` | `RcloneEntry`、`DedupRecord`、`DEDUP_FIELDNAMES` | 构造上浅——纯数据零依赖；打断 Rust-bridge/`OperationsRepo` 的传递性加载 |
| `dedup_query.py` | Rust dedup bridge 包装 + `RUST_DEDUP_AVAILABLE` + `should_skip_from_rclone`、`is_in_rclone_inventory`、`check_dedup_upgrade`、`check_redownload_dedup_upgrade` | 深——对内存库存的纯决策；无存储导入 |
| `dedup_store.py` | 库存加载（`load_rclone_inventory`、`_ledger_to_inventory`、`_open_ledger_for_dedup`、CSV 回退）+ 持久化（`append_dedup_record`、`mark_records_deleted`、`cleanup_deleted_records`、`load_dedup_csv`、`save_dedup_csv`、`export_dedup_db_to_csv`）+ 两个进程全局 | 深——唯一 I/O 层；经 `OperationsRepo` 读写（ADR-046） |

**D2. `dedup_types` 留在 `spider/services/`——不是 `spider/contracts.py`。** 同 [ADR-048](../../ADR-048-Rclone-Module-Split/ADR-048-rclone-module-split-and-folder-dedup-rust.md) D2 的理由：`DedupRecord` 是跳过时去重的域数据，不是 `UNCENSORED_SENSOR_PRIORITY` 那样的横切契约。并入 `contracts.py` 会混淆两层；移入 `pipeline/models.py` 会造成循环（`pipeline` 已导入 `spider`，`spider` 已导入 `pipeline`）。services 包内一个零依赖模块隔离类型并打断传递性加载。

**D3. 把 `_normalise_code` 提升为 `parsing/common.py` 的公开 `normalise_code`。** 函数体是三行纯 Unicode 归一化，无 spider/storage 依赖；`parsing/common.py` 已内联 NFKC 且已被 `code_resolver.py` 导入，故提升**不增加新导入边**。之后：`dedup_query`/`dedup_store` 从 `parsing.common` 导入 `normalise_code`；`code_resolver.py` 删掉逐字副本；`ops/reconcile/service.py` 从 `parsing.common` 导入 `normalise_code`（不再用 spider 私有符号）；`migrations/tools/csv_to_sqlite.py` 导入规范的 `DEDUP_FIELDNAMES` 并删掉本地副本。

**D4. 拆分修复 `ops/` → `spider/`-私有的分层拉扯。** 今天 `ops/reconcile/service.py` 伸进 `spider/services/dedup` 取私有 `_normalise_code` 和 `RcloneEntry`。拆分后 `ops/` 从 `parsing/common` 导入 `normalise_code`、从 `dedup_types` 导入 `RcloneEntry`——不再触碰 `dedup_store` 或任何 spider 私有符号。分层不变量（无反向 `apps`→`javdb`）不受影响；这消除一处 `javdb` 内部跨包私有耦合。

**D5. 不留 re-export shim——删除 `dedup.py` 并重定向所有调用点。** shim 会保留打包加载（每个导入方仍加载全部），违背隔离目标。调用点清单封闭且在 IMP 中枚举（9 个生产文件 + `tests/conftest.py` 三行 fixture 更新 + 测试文件）。

**D6. `should_skip_from_ownership` 归 `dedup_store`（它打开实时 DB 读），并标记为可能的死代码。** 它每次调用做 `OwnershipLedgerRepo` I/O，故属 `dedup_store` 而非 `dedup_query`。它**零生产调用方**（仅 `tests/unit/test_dedup_reads_ledger.py`）；IMP 标记它供单独的死代码决策——本 ADR **不**删除它（非目标：仅行为保持式重定位）。

## 后果（Consequences）

### 正面（Positive）

- **leverage** —— 纯查询调用方（`detail/runner`、`planner`）不再加载 `OperationsRepo`/Rust bridge/进程全局；一个 `DedupRecord` 导入只花一个 NamedTuple，而非整个模块。
- **locality** —— 两个进程全局与全部 I/O 集中在 `dedup_store`，给 autouse fixture 单一靶点；`normalise_code` 一处定义守所有调用方。
- **interface 收缩** —— 三个深模块取代一个 779 行浅 seam；测试按 concern 命中单层。
- **一处分层拉扯 + 两处重复消失** —— `ops`→spider-私有导入、`code_resolver` 副本、`csv_to_sqlite` 字段列表副本，各自坍缩为一个规范来源（删除测试：复杂度不重现）。

### 负面（Negative）

- **单 PR 内的调用点改动** —— 9 个生产文件 + 13 个测试文件重定向导入。缓解：清单封闭且枚举；纯重定位、零行为变更，故现有套件即回归门。
- **两个新小模块**（`dedup_types` 浅）。接受：它的存在是打断传递性加载，不为藏行为。

### 风险（Risks）

- **漏掉某个导入方，`dedup.py` 删除时坏掉。** 缓解：IMP Task 0 在搬动前枚举每个导入方；集合不符即停门。
- **未重定向 autouse fixture** → 在已删除的全局上 `AttributeError`。缓解：作为独立 IMP 步骤且带自己的门。

## 实施路线图（Implementation Roadmap）

| 阶段 | IMP | 交付 | 推迟 |
| --- | --- | --- | --- |
| Phase 1（唯一） | [IMP-ADR049-01](IMP-ADR049-01-dedup-module-split.md) | 三模块拆分；`normalise_code` 提升；删 `dedup.py`；重定向所有调用点 + autouse fixture；移除重复副本；导入隔离回归测试；CONTEXT.md 跳过时去重术语 | `should_skip_from_ownership` 的死代码决策 |

### 明确的非目标（YAGNI）

- **不**改任何去重决策逻辑或持久化行为——纯重定位。
- **不**碰 `rclone/helper.py`（清理时去重）——那是 [ADR-048](../../ADR-048-Rclone-Module-Split/ADR-048-rclone-module-split-and-folder-dedup-rust.md)。
- **不**删除 `should_skip_from_ownership`——标记供单独决策。

## 领域语言（CONTEXT.md 增补）

- **跳过时去重（Skip-time dedup）** —— 抓取期间判断是否跳过重新下载/上传一个已在 rclone 库存中的 `video_code`；持久化 `DedupRecord`。由 `spider/services/dedup_query.py`（决策）+ `dedup_store.py`（库存 + 持久化）拥有。区别于**清理时去重**（ADR-048：`rclone/dedup.py`，purge 劣质物理文件夹）。
- **Dedup 查询模块（`dedup_query.py`）** —— 纯计算层：接收已加载库存 dict，返回 skip/upgrade 决策；包装 Rust dedup bridge（Best-Effort，ADR-041）；零存储导入。
- **Dedup store 模块（`dedup_store.py`）** —— I/O 层：库存加载（所有权账本 / `OperationsRepo` / CSV 回退）+ `DedupRecord` 持久化；持有 `_db_initialised` / `_pending_paths_cache` 进程全局。
- **番号归一化（`normalise_code`）** —— 任何查找/比较前对 `video_code` 施加的 NFKC + strip + upper；在 `parsing/common.py` 的唯一公开定义（原是跨 `dedup` 与 `code_resolver` 重复的私有 `_normalise_code`）。

## 备选方案（Alternatives Considered）

- **`DedupRecord`/`RcloneEntry` → `spider/contracts.py`。** 否决（D2）：把跳过时去重域数据与横切契约混淆；ADR-048 D2 已立先例。
- **保留 `dedup.py` 作 re-export shim。** 否决（D5）：保留打包加载，违背隔离目标。
- **`should_skip_from_ownership` → `dedup_query`。** 否决（D6）：它做每次调用 DB I/O；放进纯层破坏零存储不变量。

## 参考（References）

- [ADR-048 — Rclone Module Split](../../ADR-048-Rclone-Module-Split/ADR-048-rclone-module-split-and-folder-dedup-rust.md)
- [ADR-046 — Retire Db Facade](../ADR-046-Retire-Db-Facade/ADR-046-retire-db-facade.md)
- [ADR-011 — JavDB Parsing Module](../ADR-011-Parsing-Module/ADR-011-javdb-parsing-module.md)
- 2026-06-13 架构评审：[architecture-review-2026-06-13.html](../../architecture/architecture-review-2026-06-13.html)

## 状态日志（Status Log）

- 2026-06-14：Completed 于 [IMP-ADR049-01](IMP-ADR049-01-dedup-module-split.md)。计划中的单一阶段已交付三模块拆分、`normalise_code` 提升、调用点/测试重定向、导入隔离回归覆盖，以及 CONTEXT.md 术语更新。本 ADR 不再有后续 IMP；`should_skip_from_ownership` 的死代码决策仍作为独立未来决策推迟。
- 2026-06-13：Proposed（源自 2026-06-13 架构评审候选 2）。决定：3 模块拆分（`dedup_types`/`dedup_query`/`dedup_store`），不留 shim；把 `_normalise_code`→`normalise_code` 提升进 `parsing/common.py`；`dedup_store` 保留两个进程全局并用 `OperationsRepo`。核实：779 行（候选说 ~778）；`code_resolver` 副本 + `csv_to_sqlite` 字段列表副本 + `ops`→spider-私有导入均属实。`should_skip_from_ownership` 标记为可能死代码（零生产调用方）。IMP-ADR049-01 待办。
