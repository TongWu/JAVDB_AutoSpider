# ADR-016: docs/design/ 文件夹重构

**状态 (Status):** Completed
**日期 (Date):** 2026-05-22
**作者 (Author):** Ted
**关联实现计划 (Related Implementation Plans):** [IMP-ADR016-01](IMP-ADR016-01-design-docs-folder-restructure.md)（单阶段重构）

## 背景 (Context)

当前 `docs/design/` 布局将 ADR、IMP 和 BFR 放在并行的兄弟目录中（`adr/`、`impl/`、`bfr/`）。这带来三个问题：

1. **概念不匹配** — IMP 从属于 ADR，但扁平布局将它们视为同级。查找某个 ADR 的所有计划需要在 `adr/` 和 `impl/` 之间跳转。
2. **交叉引用复杂** — 每个 ADR↔IMP 链接都需要 `../impl/` 或 `../adr/` 相对路径前缀。当文件归档时，由于归档目录增加了一层深度，这些链接会断裂。
3. **归档摩擦** — 归档一个 ADR 需要移动 ADR 文件并单独跟踪/移动其 IMP。内部交叉引用会断裂，必须手动更新。

## 决策 (Decision)

重构 `docs/design/`，使每个 ADR 和 BFR 拥有独立文件夹，IMP 共置于其父 ADR 文件夹内。

### 设计决策 (Design Decisions)

D1. **每个 ADR 独立文件夹** — 每个 ADR 拥有名为 `ADR-NNN-Pascal-Kebab-Summary/` 的文件夹（如 `ADR-010-D1-Access-Port/`）。文件夹包含 ADR `.md` + `.zh.md` 及其所有 IMP 文件。BFR 遵循相同模式，使用 `BFR-NNN-Pascal-Kebab-Summary/`。

D2. **IMP 共置** — IMP 文件存放在其父 ADR 文件夹内。ADR 与其 IMP 之间的交叉引用变为仅文件名链接（无相对路径前缀）。跨 ADR 引用使用 `../ADR-NNN-Foo/` 模式。

D3. **整文件夹归档** — 已完成的 ADR（状态为 Completed/Superseded 且所有 IMP 已完成）通过将整个文件夹移至 `_archive/ADR-NNN-Foo/` 来归档。内部链接（ADR↔IMP）无需更改。仅外部入站引用需要插入 `_archive/`。

D4. **活跃 ADR 中的已完成 IMP** — 已完成的 IMP 留在其父 ADR 文件夹中。完成状态通过文件内的 `Status:` 字段跟踪，而非目录位置。

D5. **模板目录** — `_templates/` 存放 `ADR-TEMPLATE.md`、`ADR-TEMPLATE.zh.md`、`BFR-TEMPLATE.md`、`BFR-TEMPLATE.zh.md`。

D6. **架构文档不变** — `docs/design/architecture/` 不受此次重构影响。

D7. **活跃 vs 归档分类** — ADR-001 至 007 已归档。ADR-008 至 015 为活跃状态。BFR-001 为活跃状态。ADR-016 在此次重构完成后归档。

### 目标结构 (Target Structure)

```text
docs/design/
├── ADR-008-Frontend-Rewrite/
│   ├── ADR-008-frontend-rewrite-architecture.md
│   ├── ADR-008-frontend-rewrite-architecture.zh.md
│   ├── IMP-ADR008-01-frontend-phase1-backend-prerequisites.md
│   ├── IMP-ADR008-02-frontend-phase1-completion.md
│   ├── IMP-ADR008-03-frontend-phase2-full-cli-coverage.md
│   └── IMP-ADR008-04-frontend-phase3-power-user.md
├── ADR-009-D1-Drift-Classifier/
├── ADR-010-D1-Access-Port/
├── ADR-011-Parsing-Module/
├── ADR-012-Pipeline-Run-Boundary/
├── ADR-013-Runner-Runtime-State/
├── ADR-014-Storage-Cli-Layering/
├── ADR-015-Integrations-Interface/
├── BFR-001-Login-Proxy-Mismatch/
├── _archive/
│   ├── ADR-001-Split-Db-Module/
│   ├── ADR-002-Observability-Storage/
│   ├── ADR-003-Metrics-Pipeline/
│   ├── ADR-004-Proxy-Discovery/
│   ├── ADR-005-Db-Py-Retirement/
│   ├── ADR-006-Pending-Mode-Rollout/
│   ├── ADR-007-Monorepo-Restructure/
│   └── ADR-016-Design-Docs-Restructure/
├── _templates/
│   ├── ADR-TEMPLATE.md
│   ├── ADR-TEMPLATE.zh.md
│   ├── BFR-TEMPLATE.md
│   └── BFR-TEMPLATE.zh.md
└── architecture/
```

### 交叉引用路径规则 (Cross-Reference Path Rules)

| 引用方向 | 路径模式 |
| --- | --- |
| ADR → 自身 IMP | `IMP-ADR010-01-*.md`（同目录） |
| IMP → 自身 ADR | `ADR-010-*.md`（同目录） |
| ADR → 其他活跃 ADR | `../ADR-012-Pipeline-Run-Boundary/ADR-012-*.md` |
| ADR → 已归档 ADR | `../_archive/ADR-007-Monorepo-Restructure/ADR-007-*.md` |
| IMP → 其他 ADR 的 IMP | `../ADR-012-Pipeline-Run-Boundary/IMP-ADR012-01-*.md` |
| 已归档 → 活跃 | `../../ADR-010-D1-Access-Port/ADR-010-*.md` |
| 已归档 → 已归档 | `../ADR-005-Db-Py-Retirement/ADR-005-*.md`（在 `_archive/` 内） |
| CLAUDE.md → IMP | `docs/design/ADR-010-D1-Access-Port/IMP-ADR010-01-*.md` |
| CLAUDE.md → 已归档 IMP | `docs/design/_archive/ADR-007-Monorepo-Restructure/IMP-ADR007-01-*.md` |

## 后果 (Consequences)

### 正面 (Positive)

- 打开一个文件夹即可看到决策及其所有执行计划
- ADR↔IMP 交叉引用变为仅文件名（更简单，归档时不会断裂）
- 归档操作变为单一文件夹移动；文件夹内无需修复引用
- 目录列表直观显示活跃 vs 已归档的 ADR

### 负面 (Negative)

- 跨 ADR 引用变长（`../ADR-012-Foo/IMP-ADR012-01-*.md` vs `IMP-ADR012-01-*.md`）
- 一次性迁移工作：~77 个文件需移动，~65+ 个文件需更新交叉引用
- 无 IMP 的 ADR 仍需独立文件夹（为统一性带来的轻微开销）

## 实施路线图 (Implementation Roadmap)

| 阶段 | IMP | 交付内容 | 推迟内容 |
| --- | --- | --- | --- |
| Phase 1 | [IMP-ADR016-01](IMP-ADR016-01-design-docs-folder-restructure.md) | Python 迁移脚本、所有文件移动、交叉引用更新、ADR 模板、CLAUDE.md 更新 | — |

## 状态日志 (Status Log)

- 2026-05-22: 在 brainstorming 会话中提出并接受
- 2026-05-24: 完成最终文档清理、旧链接验证，并按整文件夹归档规则收尾。
