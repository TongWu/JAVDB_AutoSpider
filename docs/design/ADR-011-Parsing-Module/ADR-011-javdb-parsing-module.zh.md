# ADR-011：JavDB Parsing Module

**状态**：已接受 —— 实现待启动
**日期**：2026-05-20
**决策者**：Parsing module 架构评审
**取代**：[ADR-005](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.zh.md) D4 / PR-6 parser-helper relocation
**关联实现计划 (Related Implementation Plans)**：[IMP-ADR011-01](IMP-ADR011-01-parsing-phase1-core-module.md)（Phase 1 — core module）、[IMP-ADR011-02](IMP-ADR011-02-parsing-phase2-caller-migration.md)（Phase 2 — caller migration）、[IMP-ADR011-03](IMP-ADR011-03-parsing-phase3-delete-compat.md)（Phase 3 — compatibility deletion）

## 背景

JavDB HTML parsing 当前位于 `apps.api.parsers`，并使用
`apps.api.models` 中的 dataclass。这个位置不符合当前架构：Spider
runtime、Storage、Migration tools、API services 和 ops profiling 都依赖
parsing 行为，但 parsing 不是 API 层职责。

ADR-005 已识别这个问题的较窄版本：Storage 会 import
`apps.api.parsers.common` 中的三个 helper，因此这些 helper 应下沉。本 ADR
把这部分工作从 ADR-005 中抽出，并把边界扩大为完整的 JavDB Parsing
Interface。

## 不可协商不变量

本次迁移必须保持行为不变。Parsing 已在生产环境运行数月，因此结构整理不得改变
parser output、fallback behavior、Rust-first dispatch semantics、legacy
adapter return shapes、URL normalization、sentinel values、tag interpretation
或任何 edge-case details。

每个 phase 完成前都必须证明与当前行为 parity。Parser 行为变更不属于本 ADR
范围。如果后续确实需要改变 parser 行为，必须作为单独 PR 落地，并提供独立
fixtures、review 和 parity explanation。

## 决策

### D1. Canonical Module

`javdb.parsing` 成为 JavDB HTML parsing 的 canonical production Interface。
新代码从以下路径 import parsing：

```python
from javdb.parsing import (
    detect_page_type,
    parse_category_page,
    parse_detail_page,
    parse_index_page,
    parse_tag_page,
    parse_top_page,
)
from javdb.parsing.common import javdb_absolute_url, movie_href_lookup_values
from javdb.parsing.models import MovieDetail, MovieIndexEntry, TagPageResult
```

Phase 1 之后，`apps.api.parsers` 和 `apps.api.models` 不再是 canonical parser
home。它们只作为临时 compatibility Adapter 存在。

### D2. Rust-First Dispatch

`javdb.parsing.__init__` 负责 production parser dispatch。它优先尝试
`javdb.rust_core`，当 Rust extension 不可用时 fallback 到冻结的 Python
实现。

迁移必须保持当前 dispatch 行为。不得新增宽泛 exception swallowing，不得改变
fallback activation，不得把直接 fallback import 变成 production path。

### D3. Parsing Models

Parser output dataclass 和 sentinel 移到 `javdb.parsing.models`：

- `MovieLink`
- `ActorCredit`
- `MagnetInfo`
- `MovieIndexEntry`
- `MovieDetail`
- `IndexPageResult`
- `CategoryPageResult`
- `TopPageResult`
- `TagOption`
- `TagCategory`
- `TagPageResult`
- `NO_ACTOR_LISTING_ACTOR_NAME`
- `NO_ACTOR_LISTING_ACTOR_GENDER`

Compatibility phases 期间，`apps.api.models` re-export 这些相同对象。

### D4. Common Helpers

共享 parser helpers 移到 `javdb.parsing.common`，包括 URL normalization、JavDB
absolute URL construction、href lookup variants、rate/comment extraction、
video-code extraction、`MovieLink` extraction、page type detection、category
name extraction，以及 supporting-actor URL normalization。

ADR-005 Storage/Repo 工作在 Phase 1 落地后应从 `javdb.parsing.common`
import 这些 helper。

### D5. Frozen Fallbacks

BeautifulSoup parser implementations 移到：

```text
javdb/parsing/fallback/index_parser.py
javdb/parsing/fallback/detail_parser.py
javdb/parsing/fallback/tag_parser.py
```

它们是冻结的 fallback implementations。只有为了保持与现有生产行为 parity
时才允许修改。

### D6. Search Helpers

Exact video-code search helpers 移到 `javdb.parsing.search_exact`。Caller
migration 后，API services 和 migration tools 可以直接调用该模块。

### D7. Index Selection Belongs To Pipeline

`parse_index()` 当前混合了 HTML parsing 与 Spider phase filtering。Phase 1 /
phase 2、ad hoc mode、today/yesterday release tag、subtitle/magnet tag、score
threshold、invalid score 和 legacy dict conversion 逻辑移到
`javdb.pipeline.index_selection`。

Parsing modules 返回已解析的 page data。Pipeline selection 决定一次运行应处理
哪些 parsed entries。

### D8. Spider Runtime Adapter Is Temporary

`javdb.spider.parser` 暂时保留为 Spider runtime Adapter。它可以包装
`javdb.parsing` 和 `javdb.pipeline.index_selection`，以便 caller 迁移期间继续
保持 legacy `parse_index()` / `parse_detail()` return shapes。

该 Adapter 必须在 Phase 3 删除。把它永久保留为 wrapper 会让代码库继续存在两个
parser Interfaces，违背本 ADR。

### D9. Three-Phase Convergence

实现拆为三个可独立 review 的 phase：

| Phase | Implementation plan | Outcome |
|---|---|---|
| Phase 1 | [IMP-ADR011-01](IMP-ADR011-01-parsing-phase1-core-module.md) | 建立 `javdb.parsing`；API parser/model modules 变为 compatibility Adapters。 |
| Phase 2 | [IMP-ADR011-02](IMP-ADR011-02-parsing-phase2-caller-migration.md) | 内部 caller 迁移到 `javdb.parsing`；index selection 移到 Pipeline。 |
| Phase 3 | [IMP-ADR011-03](IMP-ADR011-03-parsing-phase3-delete-compat.md) | 删除 API parser/model re-export Adapters 和 legacy Spider parser Adapter。 |

Phase 1 compatibility 不是最终架构。实现必须继续推进到 Phase 3，本 ADR 才算完整交付。

## Module Layout

```text
javdb/parsing/
├── __init__.py
├── common.py
├── models.py
├── search_exact.py
└── fallback/
    ├── __init__.py
    ├── detail_parser.py
    ├── index_parser.py
    └── tag_parser.py

javdb/pipeline/
└── index_selection.py
```

临时 compatibility locations：

```text
apps/api/parsers/
apps/api/models.py
javdb/spider/parser.py
```

## Gates

- Parser unit tests pass。
- Parser parity tests pass against current Rust and Python fallback behavior。
- Spider index/detail smoke 或 integration tests pass。
- Output fixtures 或 golden-output checks 证明 parser 行为未改变。
- 每个 phase 都有对应 import path 的 grep gates。
- Compatibility deletion 前，developer docs 不再教用户使用 `apps.api.parsers`。

## 后果

### 正向

1. Parsing 成为 deep domain module，而不是 API implementation detail。
2. Storage、Migration、Spider、API 和 ops tooling 可以共享 parsing helpers，
   不再从 `apps.api` 反向 import。
3. Rust-first production parsing 和 Python fallback parsing 通过一个一致的
   Interface 暴露。
4. Phase 3 删除 legacy wrappers，避免永久双 Interface。

### 负向

1. Caller migration 会触及 API services、Spider runtime、migration tools、
   parity tests、unit tests 和 developer docs。
2. Compatibility 必须跨 phase 被明确管理。
3. 每次结构移动前都需要 golden/parity coverage。

### 风险

1. **结构迁移意外改变 parsing 行为。**
   - **缓解**：parity fixtures、现有 parser tests、Spider smoke tests，以及每个
     phase 中的不可协商不变量。
2. **Phase 1 compatibility 成为永久状态。**
   - **缓解**：Phase 2 和 Phase 3 都有独立 IMP 和 grep gates。
3. **外部或 private scripts 仍 import old parser paths。**
   - **缓解**：compatibility Adapters 保留到 Phase 2；Phase 3 更新 developer
     docs 并用 grep gates 确认后再删除。

## ADR-005 修订

ADR-005 D4 / PR-6 由本 ADR 取代。ADR-005 仍负责 Storage/Repo 工作，但不再负责
parser/helper relocation。Phase 1 落地后，ADR-005 Storage 工作应从
`javdb.parsing.common` import parsing helpers。

## References

- [ADR-005](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement-and-repo-pattern.zh.md)
- [IMP-ADR011-01](IMP-ADR011-01-parsing-phase1-core-module.md)
- [IMP-ADR011-02](IMP-ADR011-02-parsing-phase2-caller-migration.md)
- [IMP-ADR011-03](IMP-ADR011-03-parsing-phase3-delete-compat.md)
