# ADR-014：Storage CLI 分层收束

**状态**：已接受 - 实现待启动
**日期**：2026-05-20
**决策者**：Storage CLI layering brainstorming + grill 会话
**关联实现计划 (Related Implementation Plans)**：[IMP-ADR014-01](IMP-ADR014-01-storage-cli-layering-phase1-guard.md)（Phase 1 - guard and direct storage imports）、[IMP-ADR014-02](IMP-ADR014-02-storage-cli-layering-phase2-lifecycle-helpers.md)（Phase 2 - canonical lifecycle helpers）、[IMP-ADR014-03](IMP-ADR014-03-storage-cli-layering-phase3-delete-legacy-wrappers.md)（Phase 3 - delete legacy wrappers）

## 待办 (Outstanding Work)

- Phase 1 - 增加 storage-to-CLI import guard，将剩余的 commit-session CLI helper import 改成 storage helper 直连，并更新过期 ADR/IMP 说明。
- Phase 2 - 将共享 helper 实现迁到 `javdb.storage.sessions.lifecycle_helpers`，并让生产调用方改用 canonical path。
- Phase 3 - 删除 `apps.cli.db._session_helpers` 和 `javdb.storage.rollback.session_helpers`，并防止这两个旧路径回流。

---

## 背景

ADR-008 曾记录一个 storage 分层倒挂：rollback library code 从
`apps.cli.db._session_helpers` 导入 helper code。这个原始问题已经被部分修复：

- `javdb.storage.rollback.core` 现在从
  `javdb.storage.rollback.session_helpers` 导入；
- `apps.cli.db._session_helpers` 现在是 re-export shim；
- helper 实现已经位于 `javdb.storage.rollback` 下。

剩余问题更小，但仍然尖锐：

- `apps.cli.db.commit_session` 仍然从 CLI shim 导入；
- canonical helper path 仍然带有 rollback 命名，但这组代码实际被 rollback、commit、
  API commit side effects 和 session lifecycle operations 共用；
- 目前没有 architecture guard 防止 `javdb.storage.*` 再次导入 `apps.cli.*`。

## 不可协商分层不变量

`javdb.storage.*` 不得导入 `apps.cli.*`。

CLI module 可以导入 storage/library module。Storage module 必须能够在不导入 CLI
wrapper、CLI argument parsing 或 CLI helper shim 的情况下被使用。

## 不可协商运行时不变量

本 ADR 是行为保持型迁移。

迁移不得改变 rollback、commit、API commit side effects、pending-mode、
MovieClaim fanout、JSONL emission、`GITHUB_OUTPUT`、run identity、timestamp
parsing、session lookup、pre-state lookup、logging 或 exit-code 语义。

## 决策

### D1. 明确依赖方向

允许：

- `apps.cli.* -> javdb.storage.*`
- `apps.api.* -> javdb.storage.*`
- `javdb.storage.rollback.core -> javdb.storage.sessions.lifecycle_helpers`
- `javdb.storage.sessions.commit -> javdb.storage.sessions.lifecycle_helpers`
- `javdb.storage.sessions.lifecycle_helpers -> javdb.storage.db.*`
- `javdb.storage.sessions.lifecycle_helpers -> javdb.proxy.coordinator.movie_claim_client`

禁止：

- `javdb.storage.* -> apps.cli.*`
- Phase 3 之后，生产代码导入 `apps.cli.db._session_helpers`
- Phase 3 之后，生产代码导入 `javdb.storage.rollback.session_helpers`

### D2. Phase 1 增加轻量 Architecture Guard

Phase 1 增加基于 AST 的测试，扫描 `javdb/storage/**/*.py`，拒绝真实的
`apps.cli` import。

这个 guard 检查 Python import，而不是 comment 或 docstring，所以文档中仍然可以提到
CLI module 名称。

### D3. Phase 1 完成剩余直接导入清理

`apps.cli.db.commit_session` 停止从 `apps.cli.db._session_helpers` 导入，改为直接从
`javdb.storage.rollback.session_helpers` 导入。

`apps.cli.db._session_helpers` 暂时保留为 CLI compatibility shim。

### D4. Phase 2 使用中性 Canonical Helper Module

canonical helper 实现迁到：

```text
javdb.storage.sessions.lifecycle_helpers
```

这个命名反映真实领域：这组 helper 是共享 session lifecycle scaffolding，不是
rollback-only code。

### D5. Phase 2 保留 Legacy Wrappers

Phase 2 保留两个 legacy path，作为 re-export wrapper：

- `apps.cli.db._session_helpers`
- `javdb.storage.rollback.session_helpers`

同一个 phase 内，生产调用方迁到 `javdb.storage.sessions.lifecycle_helpers`。

### D6. Phase 3 删除两个 Legacy Wrappers

Phase 3 删除：

- `apps.cli.db._session_helpers`
- `javdb.storage.rollback.session_helpers`

测试、monkeypatch target、文档和 README 引用迁到
`javdb.storage.sessions.lifecycle_helpers`。

### D7. 本 ADR 内 `write_github_output` 继续留在 Lifecycle Helper

`write_github_output` 带有 workflow 语义，但本 ADR 中它继续留在
`lifecycle_helpers`，因为它当前属于 session lifecycle side-effect bundle 的一部分。

最终模块需要把它标注为 workflow side-effect adapter，而不是 storage core。将 GitHub
output、JSONL 或 reporting side effects 移到 workflow/integrations package，属于后续
ADR 范围。

### D8. Helper 语义必须完全保持

每个 phase 都必须保持下列行为的可观察语义：

- `normalize_run_started_at`
- `find_run_sessions`
- `find_window_sessions`
- `read_session_pre_state`
- `fanout_movie_claim`
- JSONL append helpers
- `GITHUB_OUTPUT` writing
- run identity attachment
- rollback CLI exit codes
- commit CLI exit codes
- API commit side effects

### D9. 给历史文档添加 Supersession Notes

ADR-008 和 IMP-ADR008-02 在写下时是正确的，但代码之后已经发生迁移。它们需要增加简短更新
说明，指向本 ADR 作为最终收敛工作。

### D10. 一个 ADR，三个 Phase Plans

本 ADR 通过三个独立实现计划 rollout：

- [IMP-ADR014-01](IMP-ADR014-01-storage-cli-layering-phase1-guard.md)
- [IMP-ADR014-02](IMP-ADR014-02-storage-cli-layering-phase2-lifecycle-helpers.md)
- [IMP-ADR014-03](IMP-ADR014-03-storage-cli-layering-phase3-delete-legacy-wrappers.md)

每个 phase 都有自己的测试门禁，可独立实施。

## 最终形态

```text
apps.cli.db.rollback / apps.cli.db.commit_session
  -> javdb.storage.rollback.core / javdb.storage.sessions.commit
  -> javdb.storage.sessions.lifecycle_helpers
  -> javdb.storage.db.* / MovieClaim client / filesystem and env side effects
```

禁止形态：

```text
javdb.storage.* -> apps.cli.*
```

## 后续 ADR 范围

- 将 GitHub Actions output helpers 从 session lifecycle helpers 中迁出。
- 将 JSONL/reporting side effects 迁到 workflow 或 integrations package。
- 对其他 DB CLI 做更广泛的 CLI-to-library extraction。

## 后果

### 正面

- Storage/library code 不再依赖 CLI helper modules。
- 共享 session lifecycle helpers 获得中性 canonical home。
- Architecture tests 让分层规则变成可执行约束。
- Legacy wrappers 有明确删除 phase。

### 负面

- Phase 2 和 Phase 3 需要在生产代码和测试中进行 import churn。
- canonical move 后，compatibility wrappers 会多存在一个 phase。
- `write_github_output` 在独立 workflow-side-effect ADR 处理前，仍然位于
  storage-adjacent helper 中。

### 中性

本 ADR 不重新设计 rollback、commit、pending-mode、MovieClaim、JSONL 或 GitHub
Actions output 行为。它只改变 ownership 和 dependency direction。
