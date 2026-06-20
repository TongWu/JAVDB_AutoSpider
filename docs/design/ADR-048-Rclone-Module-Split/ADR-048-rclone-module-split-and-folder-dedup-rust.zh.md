# ADR-048：拆分 rclone helper，并把文件夹去重级联定为 Rust-Required

| 字段       | 值                                                                 |
| ---------- | ----------------------------------------------------------------- |
| **状态**   | Completed（2026-06-19）—— 3 个阶段全部交付；见 [IMP-ADR048-01](IMP-ADR048-01-module-split-and-rust-folder-dedup.md) |
| **日期**   | 2026-06-13                                                        |
| **作者**   | Ted                                                              |
| **关联**   | [ADR-041](../_archive/ADR-041-Rust-Fallback-Policy/ADR-041-rust-fallback-policy.md)（本 ADR 实例化并扩展的 fallback 分层策略）、[ADR-015](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md)（拆了 rclone **manager** 但明确推迟了 **helper** 深拆——ADR-048 正是这块推迟工作的延续）、[ADR-035](../_archive/ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md)（Rust 是规范解析路径）、[ADR-039](../ADR-039-Pluggable-Integration-Platform/ADR-039-pluggable-integration-platform.md)（插件平台——拥有 **downloader/notify** 类别，**不**含 rclone 清理） |

> 源自 2026-06-13 架构评审（候选 1 ——"拆分 `rclone/helper.py`"）：[architecture-review-2026-06-13.html](../architecture/architecture-review-2026-06-13.html)。评审发现 `javdb/integrations/rclone/helper.py` 是一个 1,438 行的扁平模块，其 55 函数接口逼着 `manager/service.py` 按名导入 27 个符号、横跨三个结构无关的 concern；扫描引擎内埋着一处 Rust 迁移欠债，而一个永久删除级联被并进同一个浅接口。

## 背景（Context）

`javdb/integrations/rclone/helper.py`（1,438 行，55 个 `def`）是 rclone 清理子系统唯一的深依赖。其唯一生产调用方 `javdb/integrations/rclone/manager/service.py` 按名导入 **27 个符号**；另一个调用方仅 `javdb/migrations/tools/strip_rclone_root_folder.py`（3 个 path 符号）。该模块是个扁平的 **grab-bag** —— 一个导入面挡在三个结构无关的 concern 前：

- **路径处理**（8 个纯字符串函数：`strip_drive_name`/`strip_root_folder`/`to_full_remote_path` 等）—— 无 I/O，不碰 rclone。
- **扫描引擎**（健康前置 + 文件夹名解析 + `FolderCache` + 11 个扫描函数）—— 驱动 `rclone lsd`/`lsjson` 子进程，逻辑上是单一入口 `scan_folder_structure() → Dict[year, Dict[actor, List[FolderInfo]]]`。
- **文件夹去重级联**（`analyze_duplicates_for_code` + `_process_wuma_dedup` + `_apply_sensor_priority` + `_process_subtitle_dedup` + `execute_deletions`）—— 对 Google Drive 上同一 `video_code` 的多个物理文件夹，决定保留哪个、`rclone purge` **永久删除**哪个（helper.py:1227）。

三处可度量的坏味道：

1. **接口与实现一样宽**（浅）：测任一 concern 都要加载整个 1,438 行模块；`service.py` 的 27 符号导入把它耦合到全部三个 concern。
2. **扫描引擎里埋着 Rust 迁移欠债。** `helper.py:754` 无条件走 Python 解析器，因为 Rust `parse_lsjson_for_year`（`rust_core/src/rclone_ops.rs:55`）仍按**旧的 2 级** `<actor>/<code [sensor-subtitle]>` layout 解析，而远端实际 layout 已迁移为**3 级** `<actor>/<code>/<sensor-subtitle>`。因为没有模块独占该 seam，欠债不可见。同文件的 Rust `parse_lsd_output`、`group_by_movie_code` 同样**已存在但被 Python 扫描路径闲置**。
3. **一个永久删除驱动器与纯工具共面。** 去重级联——四条排序规则（无码 sensor 优先级、中字 > 无字、1.30× size 例外）——驱动不可逆的 `rclone purge`，却**没有任何显式不变量**保证它绝不删光某个 code 的全部文件夹。它的安全关键决策被埋在路径工具和 CSV 格式化之间。

还有一个**术语隐患**：仓库里有三样都叫 "dedup" 的东西 —— `DedupRecords` 表 + `spider/services/dedup.py`（跳过时去重：*别重新下载已存在的 code*），以及本文件夹级联（清理时去重：*purge Drive 上已存在的劣质物理副本*）。它们是共用一个词的不同操作；拆分正是在 CONTEXT.md 里把它们澄清开的契机。

**血统（Lineage）。** [ADR-015](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md) Phase 4–5 把 rclone *manager* 拆成了类型化命令包 + CLI 适配器 + 存储 Repo，但两次写下 Non-negotiable：*"Do not deep-split `javdb.integrations.rclone.helper` in this phase."* 深拆是被**推迟、而非否决**——`helper.py` 正是 ADR-015 有意留整的残块。ADR-048 是这块工作的延续，因此它*扩展* ADR-015 而非重新 litigate。

## 决策（Decision）

把 `helper.py` 拆成**四个深模块**，采用已发布的 Rust 扫描原语，并把永久删除级联移入 Rust 作为 **Rust-Required** 模块 —— 给 [ADR-041](../_archive/ADR-041-Rust-Fallback-Policy/ADR-041-rust-fallback-policy.md) 的分层策略**新增第二条分类轴**。

### 设计决策（Design Decisions）

**D1. 四模块拆分（`javdb/integrations/rclone/`）。** 每个模块都"深"——单一主入口挡在实现前：

| 模块 | 拥有 | 深入口 |
| --- | --- | --- |
| `types.py` | `FolderInfo`、`DedupResult`、`DeletionRecord`、`SensorCategory`、`SubtitleCategory` + 模块常量（`SIZE_THRESHOLD_RATIO`、`INCREMENTAL_DAYS` 等） | （纯数据——打断 `scan ↔ dedup` 循环导入） |
| `path_utils.py` | 8 个纯 remote 路径变换 + `get_configured_drive_name`/`get_configured_root_folder` | `to_full_remote_path`、`strip_root_folder` |
| `scan.py` | 健康前置（`check_*`、`setup_rclone_config_from_base64`、`run_health_checks`）+ 文件夹解析（`parse_leaf_name`、`parse_folder_name` 及其 `_py_parse_folder_name` Best-Effort 回退）+ `FolderCache` + 11 个扫描函数 | `scan_folder_structure() → Dict[year, Dict[actor, List[FolderInfo]]]` |
| `dedup.py` | 排序级联 + 删除执行（`rclone_purge`、`rclone_move`、`delete_folder`、`execute_deletions`）+ rclone 专属报表（`format_size`、`generate_csv_report`、`print_summary`） | `analyze_all_duplicates()`、`execute_deletions()` |

删除 `helper.py`；`service.py` 与 `strip_rclone_root_folder.py` 把导入重定向到新模块。迁移工具只导 `path_utils` —— 印证 `path_utils` 是真实 seam（两个 adapter），非假设性的。

**D2. `types.py` 留在 rclone 本地——不是 `spider/contracts.py`。** `FolderInfo`/`DedupResult`/`DeletionRecord` 属清理时去重域；`spider/contracts.py` 的 `DedupRecord`（跳过时去重的 NamedTuple）是另一个仅共用词根 "dedup" 的概念。合并会重新引入本 ADR 要澄清的那个混淆。`SensorCategory.get_priority` 继续从 `spider/contracts.py` 读 `UNCENSORED_SENSOR_PRIORITY`（优先级表的唯一来源，已镜像进 `rust_core/src/dedup_ops.rs`）。

**D3. 扫描引擎采用已发布的 Rust 原语（Phase 2）。** `scan.py` 走 `rust_core` 的 `parse_lsd_output`（替换 `get_year_folders`/`get_actor_folders` 里手写的 `lsd` 行解析）、`group_by_movie_code`（替换 Python `group_folders_by_movie_code`）、以及一个**修好的** `parse_lsjson_for_year`（按 3 级 layout 更新）——清掉 `helper.py:754` 欠债。这些按 ADR-041 留在 **Best-Effort** 层：文件夹解析可检查，故 `parse_leaf_name`/`_py_parse_folder_name` 作为 shape-contracted 的 Python 回退保留。行为靶子是与当前 Python `get_all_movie_folders_for_year`（3 级）value-parity，由 fixture 测试在切换 Rust 路径前钉死。

**D4. 文件夹去重级联成为 Rust-Required 模块（Phase 3）。** 把 `analyze_duplicates_for_code` 及其 helper（`_process_wuma_dedup`、`_apply_sensor_priority`、`_process_subtitle_dedup`）移入 `rust_core/src/dedup_ops.rs`。遵循 ADR-041 的 Rust-Required 层（同 `ProxyPool`/`ProxyBanManager`）：**删除** Python 级联；缺 `javdb.rust_core` 时在 chokepoint（`dedup.py` 的 `analyze_all_duplicates` 入口）报清晰错误，绝不静默降级。`rclone purge` 的*执行*（`execute_deletions`）留在 Python——只有 keep/delete 的*决策*进 Rust。

**D5. Rust 级联在类型/断言层强制 Python 版本留作隐式的不变量。** 这才是移植的杠杆，而非仅仅提速：

- **划分** —— 每个输入文件夹恰好落入 `keep`/`delete` 之一（`keep ∪ delete = 输入`，`keep ∩ delete = ∅`）。
- **非空 keep** —— 非空输入*绝不*产出空 keep 集；级联永不 purge 掉某 `video_code` 的全部副本。（当前 Python 仅偶然维持此性质；把它变成受检不变量就是安全收益。）
- **单一 sensor 赢家** —— 每个 subtitle 组内，至多保留一个 sensor 优先级赢家。
- **size 例外单调** —— 无字文件夹仅当 `size > 1.30 × 已保留中字 size` 时才 keep-over 中字。

违反不变量是 Rust 侧报错（fail-closed），而非静默误删。

**D6. 本 ADR *扩展* ADR-041 的分类判据——不修订、不取代。** ADR-041 在模块行为**有状态且不可检查**时定为 Rust-Required（代理池）。文件夹去重级联是**纯函数且可检查**（dry-run 会打印 keep/delete），按 ADR-041 字面标准它本该是 Best-Effort。ADR-048 新增**第二条触发：不可逆性 / 影响半径。** 当一个发散的回退会造成**不可逆的数据丢失**时，纯而可检查的函数也算 Rust-Required —— 这里，一份发散的 Best-Effort Python 副本若算出不同的 keep/delete 集，就会永久 `rclone purge` 错误的文件夹，是数据丢失陷阱而非调试陷阱。ADR-041 的策略正文不变；本 ADR 拥有这条新轴，并在 ADR-041 的 Status Log 加一行反向引用。

**D7. 只有*决策*是 Rust-Required；解析与路径工具仍是 Best-Effort。** `scan.py` 的 `parse_leaf_name`/`parse_folder_name` 与整个 `path_utils.py` 保持纯 Python 可跑（输出可检查、无不可逆后果），符合 ADR-041 D1。Rust-Required 边界精确画在驱动 purge 的 keep/delete 计算处。

## 后果（Consequences）

### 正面（Positive）

- **locality** —— keep/delete 决策住在一处（Rust）；扫描 layout seam 只住在 `scan.py`，故 `:754` 迁移欠债浮现且可清。
- **leverage** —— `scan.py` 11 个函数只暴露 1 入口（`scan_folder_structure`）；`dedup.py` 级联只暴露 2 入口；`service.py` 的导入从一次 27 符号宽拉收窄为四个按 concern 划分的导入。
- **interface 收缩，implementation 吸收 helper** —— `helper.py` 浅之处，每个新模块都深；测试按 concern 命中单一 seam，而非 55 函数模块。
- **一条潜在数据丢失路径获得受检不变量** —— "绝不删光全部副本"的保证变得显式且 fail-closed（D5）。
- **术语澄清** —— 清理时 vs 跳过时去重在 CONTEXT.md 各得独立条目。

### 负面（Negative）

- **三个顺序 PR**，逐个设门；Phase 3 删掉一份能用的 Python 实现并换成 Rust，最高风险阶段评审负担最重。缓解：行为测试在删 Python *之前*先迁到 Rust 级联并跑绿（同 ADR-041 D5a 的顺序）。
- **无 Rust wheel 的本地开发失去运行 dedup 级联的能力。** 缓解：清理只在 CI 跑（`DailyIngestion`、`AdHocIngestion`、`RcloneManager` 工作流——都携 wheel；ADR-041 D6）；scan/path/脚手架仍可用；建 wheel 仅一步 `maturin develop`。
- **`types.py` 构造上是浅模块** —— 它的存在是为打断循环导入，不为藏行为。接受：它是数据非 seam，不带入口。

### 风险（Risks）

- **Rust 移植若偏离 Python 级联会 purge 错文件夹。** 由 D5（受检不变量）、切换前钉死的 value-parity fixture（生成多文件夹 code 集，在两者并存期 Python-vs-Rust 对比）、以及 manager 的 `--dry-run` 默认值缓解。
- **枚举集之外的某个导入方在 `helper.py` 删除时坏掉。** 缓解：Phase 1 Task 0 在搬动前枚举每个被搬符号的每个导入方；若集合大于 {`service.py`、`strip_rclone_root_folder.py`、rclone 测试文件} 即停门。

## 实施路线图（Implementation Roadmap）

| 阶段 | IMP | 交付 | 推迟 |
| --- | --- | --- | --- |
| Phase 1 —— 模块拆分 | [IMP-ADR048-01](IMP-ADR048-01-module-split-and-rust-folder-dedup.md) §Phase 1 | 纯重定位为 `types`/`path_utils`/`scan`/`dedup`；删 `helper.py`；重定向导入；行为不变；CONTEXT.md 去重术语澄清 | 任何 Rust 改动 |
| Phase 2 —— Rust 扫描采用 | [IMP-ADR048-01](IMP-ADR048-01-module-split-and-rust-folder-dedup.md) §Phase 2 | `scan.py` 走 Rust `parse_lsd_output`/`group_by_movie_code`；`parse_lsjson_for_year` 修复适配 3 级 layout（清 `:754` 欠债）；value-parity fixture | dedup 级联 |
| Phase 3 —— Rust-Required 文件夹去重 | [IMP-ADR048-01](IMP-ADR048-01-module-split-and-rust-folder-dedup.md) §Phase 3 | 级联移入 `rust_core/src/dedup_ops.rs` 并带 D5 不变量；删 Python 级联；chokepoint 守卫；行为测试重指向 Rust；CONTEXT.md Rust-Required 扩展 | —— |

### 明确的非目标（YAGNI）

- **不**把 rclone 清理纳入 ADR-039 插件平台——该平台拥有 **downloader/notify** 类别；rclone 清理不是可插拔后端。
- **不**把 `execute_deletions`/`rclone_purge` 移到 Rust——只有 keep/delete *决策*是 Rust-Required；子进程执行留 Python。
- **不**触碰 `spider/services/dedup.py`（跳过时去重）——那是评审候选 2，独立倡议。

## 领域语言（CONTEXT.md 增补）

- **清理时去重（Cleanup-time dedup）** —— 对远端（Google Drive）已存在多个物理文件夹的某 `video_code`，由排序级联决定保留哪个、`rclone purge` 哪个。区别于**跳过时去重**（`DedupRecords` + `spider/services/dedup.py`：*别重新下载/上传已存在的 code*）。两者都叫 "dedup"；一个是"别抓取"，一个是"删已有"。
- **Rclone 扫描引擎（`rclone/scan.py`）** —— 健康前置 + 文件夹解析 + `FolderCache` + 扫描，挡在单一深入口 `scan_folder_structure() → Dict[year, Dict[actor, List[FolderInfo]]]` 之后。采用 Rust `rclone_ops`（`parse_lsd_output`/`group_by_movie_code`/`parse_lsjson_for_year`）。
- **文件夹去重级联（`rclone/dedup.py`）** —— 四规则 keep/purge 决策（无码 sensor 优先级、中字 > 无字、1.30× size 例外）。一个 **Rust-Required 模块**（ADR-041 层，经 ADR-048 D6 扩展）：无 Python 回退，因为发散副本驱动不可逆删除。
- **Drive 文件夹 layout** —— 旧 2 级 `<actor>/<code [sensor-subtitle]>`（`_py_parse_folder_name`）已迁移为 3 级 `<actor>/<code>/<sensor-subtitle>`（`parse_leaf_name`）；Rust `parse_lsjson_for_year` 在 Phase 2 对齐到 3 级。
- **Rust-Required 模块（扩展）** —— ADR-041 由*有状态 + 不可检查*触发；ADR-048 新增*不可逆性 / 影响半径*为第二触发：当发散回退造成不可逆数据丢失时，纯而可检查的函数也算 Rust-Required。

## 备选方案（Alternatives Considered）

- **仅重定位，留 Rust 欠债（3 模块拆分，不碰 Rust）。** grilling 中否决：满足拆分但留下 `:754` 欠债与隐式不变量的删除路径——错失"更安全语言"的价值。
- **dedup 级联保留为 Best-Effort 回退（保留 Python 镜像，shape-contracted）。** 否决：发散的 Best-Effort 副本会静默 purge 错文件夹；ADR-041 "best-effort 代理选择是陷阱"的论证对永久删除*更加*成立。
- **value-parity 双轨（保留 Python 级联 + parity 测试）。** 否决：重新引入 ADR-041 明确退役的双语言 value 锁步；parity 测试是迁移期守卫（Phase 3 删除前使用），不是稳态策略。
- **3 粗模块（path/scan/dedup，types 并入）或 6 细模块（path/health/parsing/scan/dedup/report）。** grilling 中否决，选 **4** 模块（抽出 `types` 打断循环；health/parsing 并入 `scan`；删除/报表并入 `dedup`）——在不过度拆散 `service.py` 导入点的前提下做到最深。

## 参考（References）

- [ADR-041 — Rust Core Fallback Policy](../_archive/ADR-041-Rust-Fallback-Policy/ADR-041-rust-fallback-policy.md)
- [ADR-035 — Site-Contract Drift Sentinel](../_archive/ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.md)
- [ADR-015 — Integrations Interface](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md)（Phase 4–5 推迟的 helper 深拆由本 ADR 完成）
- 2026-06-13 架构评审：[architecture-review-2026-06-13.html](../architecture/architecture-review-2026-06-13.html)

## 状态日志（Status Log）

- 2026-06-13：Proposed（源自 2026-06-13 架构评审候选 1 的 grilling）。决定：4 模块拆分（`types`/`path_utils`/`scan`/`dedup`）；Phase 2 采用已有 Rust 扫描原语 + 修 `parse_lsjson_for_year` 适配 3 级 layout；Phase 3 把文件夹去重级联移入 **Rust-Required** 模块并带显式不变量（D5）。记录判据扩展（D6：不可逆性作为 Rust-Required 第二触发）与清理 vs 跳过去重的术语澄清。IMP-ADR048-01（3 阶段）待评审。
- 2026-06-14：**Phase 1 已发布**（纯模块拆分 —— `helper.py` → `types`/`path_utils`/`scan`/`dedup`;行为不变）。**Phase 2、3 未开始。** 须注意的后果：文件夹去重 keep/delete 级联（`analyze_duplicates_for_code` 及其 helper,含 1.30× size 例外）**仍是 Python**,`types.py` 的 `SIZE_THRESHOLD_RATIO` 经 `dedup.py` → `service.py` → `RcloneManager` 工作流仍是活的生产消费者。`dedup_ops.rs` **尚未**拥有这个决策(它只有跳过时的 `should_skip_from_rclone`/`check_dedup_upgrade`)。Phase 3(Step P3.2/P3.5)落地前,切勿删除 Python 级联或 `SIZE_THRESHOLD_RATIO`。
- 2026-06-19：Phase 2 完成。修复 Rust `parse_lsjson_for_year` 以适配 3 级 `<actor>/<movie_code>/<sensor-subtitle>` layout（Rust `#[test]` 全绿）；把 `scan.py` 的 `get_year_folders`/`get_actor_folders`/`get_all_movie_folders_for_year` 路由到 Rust `parse_lsd_output`/`parse_lsjson_for_year`，并在 `ImportError` 时保留纯 Python Best-Effort 回退（D7）；由 `tests/unit/test_rclone_scan_parity.py` 锁定。**偏离：** `group_folders_by_movie_code` 未路由到 Rust `group_by_movie_code` —— 它对 `FolderInfo` dataclass 分组（Rust 操作纯 dict），路由会为一个非热路径的简单分组强加有损往返；保留纯 Python。Phase 2 未触及 Python 文件夹去重级联与 `SIZE_THRESHOLD_RATIO`,二者在 Phase 3 前仍是活的。
- 2026-06-19：Phase 3 完成 —— ADR 标记 Completed。把文件夹去重的 keep/delete **决策**移入 Rust（`dedup_ops.rs` `analyze_folder_dedup`），fail-closed 强制 D5 不变式（partition、never-empty-keep、单 sensor 胜者、严格 1.30× size 例外）；Phase 3a 在删除前以 2 万随机输入暴力证明 Rust == Python 级联（ADR-041 D5a 顺序）。Phase 3b 删除 Python 级联（`_process_*`/`_apply_sensor_priority`），新增 Rust-Required chokepoint 守卫（`_require_rust_dedup`，清晰 `RuntimeError` 指明 wheel 构建步骤）；`execute_deletions`/`rclone_purge` 与 reason 字符串格式化仍在 Python（D4）。迁移期 parity 退役，其多样夹具以冻结 golden 形式保留（`tests/unit/test_rclone_dedup_golden.py`）。CONTEXT.md 新增 Rust-Required 扩展术语；ADR-041 Status Log 反向引用该扩展（D6）。
