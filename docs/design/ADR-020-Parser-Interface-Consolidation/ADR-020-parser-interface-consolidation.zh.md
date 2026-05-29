# ADR-020: 解析器接口整合

| 字段       | 值                                                                    |
| ---------- | --------------------------------------------------------------------- |
| **状态**   | Proposed                                                              |
| **日期**   | 2026-05-29                                                            |
| **作者**   | Ted                                                                   |
| **关联**   | [ADR-011](../_archive/ADR-011-Parsing-Module/ADR-011-javdb-parsing-module.md)（parsing 模块；本就计划删除此 shim）；[IMP-ADR020-01](IMP-ADR020-01-consolidate-parser.md)（Phase 1 — 执行） |

> 源自 2026-05-29 架构审查（候选 D）：[architecture-review-2026-05-29.zh.html](../architecture/architecture-review-2026-05-29.zh.html)。

## 背景（Context）

如今"解析一个 JavDB 页面"需要知道**两个解析器入口**和一段**两步 magnet 舞**：

1. **shim。** `javdb/spider/parse_legacy_adapters.py`（118 行）在 [ADR-011](../_archive/ADR-011-Parsing-Module/ADR-011-javdb-parsing-module.md) Phase 3 中幸存——那一阶段删了原 `parser.py`，却把其包装**搬来这里**而非删掉。它重新暴露 `extract_video_code`（对 `javdb.parsing.common` 的纯透传）、`parse_index`（包 `parse_index_page` + 应用 `pipeline.index_selection.select_index_entries` + 返回 legacy dict）、`parse_detail`（包 `parse_detail_page` + 把 `MovieDetail` 重塑成 legacy 6-tuple）。它有 **6+ 个生产 importer**（`spider/fetch/fallback.py`、`spider/detail/parallel_mode.py`、`legacy/_spider_legacy.py`、两个 `migrations/tools/*`、`apps/cli/ops/profile_hot_paths.py`）加测试。
2. **两步。** 解析返回*原始* magnet（`MovieDetail.magnets`）；一个*独立、靠后*的 `javdb/spider/magnet_extractor.py:extract_magnets(...)` 调用才把它们归类为 `subtitle / hacked_subtitle / hacked_no_subtitle / no_subtitle`。热路径在 `javdb/spider/detail/runner.py:700`（`extract_magnets(data['magnets'], idx_str)`）做这步，离解析处很远。每个详情调用方都得记住两步。

所以调用方必须 (a) 选择 import 哪个解析器，且 (b) 记得事后归类。这是浅的：shim 只做形状翻译没有行为，归类步把"详情页抽取什么"这一内部细节泄漏给每个调用方。

## 决策（Decision）

整合为**一个解析器接口**——`javdb.parsing` 返回*成品领域对象*——并删掉 shim。具体：

### 设计决策（Design Decisions）

**D1. 一个解析器接口；删 shim。** `javdb.parsing`（`parse_detail_page` / `parse_index_page` + `MovieDetail` 访问器）是唯一入口。`extract_video_code` 直接从 `javdb.parsing.common` import。所有调用方迁移后，删掉 `javdb/spider/parse_legacy_adapters.py`。

**D2. magnet 归类移入 parsing 层。** 把纯归类算法**及其 Rust-first dispatch** 从 `javdb/spider/magnet_extractor.py` 移到 `javdb/parsing/magnet_categorize.py`。这是分层合法且**地道**的——`javdb/parsing/` 对其解析器本就采用完全相同的"优先 `javdb.rust_core`、回退到 frozen Python 镜像"模式（`parsing/__init__.py`、`parsing/fallback/`）。`javdb/spider/magnet_extractor.py` 退化为薄 **re-export**，保留 `extract_magnets` 与 `_parse_size`（被 `javdb/pipeline/policies.py:10` 消费）。归类入口是 parsing 层的**自由函数** `magnet_categorize.categorize(magnets)`，调用方对 `detail.get_magnets_as_legacy()` 施用。**方法不能作为接口**——生产中 `parse_detail_page()` 返回 Rust 的 `RustMovieDetail`，无法携带 Python 的 `categorize_magnets()` 方法；只有 `get_magnets_as_legacy()` 在 Rust 与 Python 两种 detail 对象上统一。（这纠正了原"成品对象方法"的表述——见状态日志。）

**D3. 折叠热路径两步。** fetch backends 直接产出**预归类**的 `data['magnet_links']`（经 `magnet_categorize.categorize(detail.get_magnets_as_legacy())`）而非原始 `data['magnets']`；`runner.py:700` 直接读取，删掉那次独立的 `extract_magnets`。这消灭生产路径上最后的"解析后再 extract"两步。作为**可独立 revert 的提交**交付，以便隔离 smoke 测试差异。

**D4. index 选择留在 `javdb.pipeline`。** `select_index_entries` 读 config（`PHASE2_MIN_RATE` 等）——是业务策略，不是解析。把它移进 `javdb.parsing` 会**反转**干净的依赖方向（`pipeline → spider → parsing`）。需要选中条目的调用方直接 `parse_index_page` + `select_index_entries`（测试早就这么做）。shim 相对此的唯一附加值——一行空列表诊断日志——内联到真正需要的地方。

**D5. 迁移每个调用方，含 frozen 代码。** 生产 spider 流、legacy spider、migration 工具全部迁移，以便删 shim。`javdb/legacy/` 是冻结参考代码——只做**最小 import 替换**（legacy `parse_index`/`parse_detail` → 规范 `parse_*_page` + `select_index_entries`，本地复现其 tuple），**不**重构其 magnet 两步。

**D6. 保留 Rust 加速（头号风险）。** 重定位必须把 **Rust-first dispatch** 一并移走，而非只移 Python 回退——否则归类会无声降级到（冻结、更慢的）Python 路径。`tests/unit/test_magnet_parity.py`（Rust vs Python parity）是护栏，全程必须绿。

## 后果（Consequences）

### 正面

- **一个解析器接口，而非两个**——调用方只学 `javdb.parsing`。
- **无"解析后再 extract"两步**——一次详情解析即产出归类 magnet；热路径那次第二调用消失（D3）。
- **局部性**——"详情页抽取什么"（含 magnet 归类）集中在一层。
- **删掉 118 行浅 shim** 及其日志别名（`infra/logging.py:91`）。
- **测试面改善**——移除"shim 是薄适配器"这种同义反复测试；magnet 归类获得与模型同处的解析器级测试。

### 负面

- **fetch/fallback 流是真正的活**——`fallback.py` 跨 ~20 个 return 点携带 6-tuple；从 `MovieDetail` 重新取源是细致的机械 churn。
- **触及 frozen `javdb/legacy/`**（仅 import 替换）——最小但非零。
- **重定位风险**——D6 的 Rust-dispatch 风险需要小心 + parity 测试。

### 风险

- **Rust 被绕过**（D6）——由整体搬移 dispatch + `test_magnet_parity.py` 缓解。
- **fetch-engine 边界**——`data` dict 跨线程/队列边界；保持传 plain dict/str（归类后的 dict 是纯 `str/int/None`），绝不把 dataclass 送过队列。
- **migration 工具操作持久化数据**——无声形状变更会损坏 backfill；由现有 align/migration 测试覆盖。

## 实施路线图（Implementation Roadmap）

| 阶段 | IMP | 交付 | 延后 |
| --- | --- | --- | --- |
| 全部阶段 | [IMP-ADR020-01](IMP-ADR020-01-consolidate-parser.md) | 行为基线 → 归类移入 parsing → 迁移非 spider 调用方 → 迁移 spider 流 + 折叠两步 → 迁移 legacy → 迁移 index/测试 + 删 shim | — |

## 不在范围（Out of Scope）

- index 选择策略本身（留在 `javdb.pipeline`，D4）。
- 重构 `javdb/legacy/` 内部（仅 import 替换，D5）。
- magnet 归类以外的 SELECT 骨架 / 解析器内部。

## 状态日志（Status Log）

- 2026-05-29：Proposed（源自架构审查候选 D 的 grilling）。
- 2026-05-29：设计纠正（实现 Phase 2 期间）。`MovieDetail.categorize_magnets()`（原 D2 的"成品对象方法"）在**生产路径上死掉**——`parse_detail_page()` 返回 Rust 的 `RustMovieDetail`，没有该方法。已修订 D2/D3：正式接口为 parsing 层自由函数 `magnet_categorize.categorize(detail.get_magnets_as_legacy())`（对 Rust/Python 两种 detail 对象统一，与 `runner.py:700` 一致）。死方法及其测试已删除。
