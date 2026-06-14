# ADR-050：整合三处重复的 `pending_session_verify` 记录构建器

| 字段       | 值                                                                 |
| ---------- | ----------------------------------------------------------------- |
| **状态**   | Completed（2026-06-14）——已由 [IMP-ADR050-01](IMP-ADR050-01-pending-verify-builder.md) 实现 |
| **日期**   | 2026-06-13                                                        |
| **作者**   | Ted                                                              |
| **关联**   | [ADR-042](../../ADR-042-D1-Atomic-Commit-Boundaries/ADR-042-d1-atomic-commit-boundaries.md)（`pending_session_verify` 是**诊断写入**——永不权威）、[ADR-019](../ADR-019-Session-Lifecycle-Authority/ADR-019-session-lifecycle-authority.md)（发出该记录的 commit/fail/rollback 生命周期）、[ADR-036](../../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md)（`PipelineEvent` 主线是另一条 codepath——非本 JSONL 旁路）、[ADR-026](../../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md)（`log_analysis` 消费该记录做 pending 告警） |

> 源自 2026-06-13 架构评审（候选 3 ——"整合 `pending_session_verify` 构建器"）：[architecture-review-2026-06-13.html](../../architecture/architecture-review-2026-06-13.html)。

## 背景（Context）

`pending_session_verify` 是一条**诊断**型 JSONL 记录（ADR-042 写入类别），在每次 pending 模式会话生命周期事件末尾追加到 `reports/D1/d1_drift.jsonl`。它被**独立构建了三次**：

- `apps/cli/db/commit_session.py` `_emit_pending_verify`（L239–309）—— 最丰富的 emitter（还写 `GITHUB_OUTPUT`、跑 shadow-audit）。
- `javdb/storage/sessions/commit.py` `_emit_commit_metrics`（L135–192）—— docstring **自承是 CLI emitter 的 "Simplified version"**。这条自承*就是* schema 漂移证据。
- `javdb/storage/rollback/core.py` `_emit_pending_verify_for_session`（L182–251）—— 加 rollback 专属字段（`rollback_mode`、`cleanup_path_mismatch_count`）。

三者共享一个**17 字段核心 schema**（`kind`、`ts`、`source`、`session_id`、`write_mode`、`final_status`、`pending_staged_count`、`pending_applied_count`、`pending_residual_count`、`commit_attempts`、`commit_duration_ms`、`hrefs_processed`、`torrents_upserted`、`torrents_deleted`、`movies_upserted`、`worker_stage_rollback_failed`、`shadow_audit_enabled`）加各自来源专属扩展。消费者—— `javdb/integrations/notify/email/log_analysis.py` `_evaluate_pending_alerts`（L1044–1055）与 `_CRITICAL_ALERT_FIELDS`（L986–990）——以**字符串字面量**耦合这些字段名，与任何生产者无编译期关联。生产者改名某字段会静默打断告警。

删除测试：三个 emitter 不是 pass-through——删一个，对应生命周期事件就失去诊断记录。但它们把一个 schema 实现了三遍；该深的是 schema，不是 emit。

## 决策（Decision）

抽出一个纯构建器 + 单一导出的字段名词汇表；三个 emitter 与一个 parser 都绑定它。

### 设计决策（Design Decisions）

**D1. `javdb/storage/sessions/pending_verify.py` 中一个纯构建器。** `build_pending_verify_record(session_id, *, source, write_mode, final_status, drain, stats, commit_attempts, commit_duration_ms, shadow_audit_result=None, rollback_extras=None) -> dict`。纯——无 I/O。它落在 `storage/sessions/` 层，与 `lifecycle_helpers.py`/`commit.py` 同处；三个 emitter 委托给它，只供给各自来源专属输入。

**D2. 从同一模块导出字段名词汇表；parser 导入它。** 模块级 `F_*` 常量（或一个 `FIELDS` 命名空间）定义每个键。`log_analysis._CRITICAL_ALERT_FIELDS` 变成导入常量的元组；`_evaluate_pending_alerts` 使用它们。改名某字段现在只改一个常量，生产者与消费者一起移动——静默打断风险消失。（`javdb/integrations` 导入 `javdb/storage` 是 `javdb` 内部导入；分层不变量只禁 `apps`→`javdb`。）

**D3. 构建器是纯的——调用方供给预取的 `stats`。** 每个调用点已在各自处取 stats（CLI emitter 构建前调 `HistoryRepo().pending_session_stats()`；rollback emitter 调 `db_pending_session_stats()`；lib emitter 用 try/except 包裹并传 `{}`）。让构建器免于 `HistoryRepo` 使它无需 DB fixture 即可单测，并保持深（一个签名、内部无 retry/fallback 逻辑）。

**D4. rollback 专属字段以不透明的 `rollback_extras: Optional[Dict[str, Any]]` 传入。** 构建器本就返回 dict；第二个 `RollbackExtras` dataclass 是无收益的复杂度，而显式 `rollback_mode=`/`cleanup_path_mismatch_count=` kwargs 会用 rollback 关切撑大共享签名。extras dict 被合入，其键由 D2 常量集守护。

**D5. shadow-audit 留在 CLI；其结果传入。** `_shadow_audit_drift` 经 `get_db` 查 `MovieHistory`/`TorrentHistory`——I/O 重且仅 CLI。构建器纯（D3）后，CLI 计算 `shadow_audit_result` 并传入；lib emitter 传 `shadow_audit_result=None`（它今天硬编码 `shadow_audit_enabled=False`，继续成立）。无 shadow-audit 逻辑进入 `javdb/storage` 层。

**D6. `GITHUB_OUTPUT` 写入留在 `apps/cli/db/commit_session.py`。** 这是确属 CLI-only 的 post-emit 副作用；构建器返回 dict，CLI 追加 JSONL 行*并*写 `GITHUB_OUTPUT`。另两个 emitter 只追加 JSONL 行。

**D7. 写入类别不变——无迁移注解。** `pending_session_verify` 仍是**诊断写入**（ADR-042）：解释 drift/恢复状态，可阻塞但永不升级提交。无新表、无 schema 变更，故 ADR-042 D6 的 `-- Write-Class:` 迁移 header 规则不适用。

## 后果（Consequences）

### 正面（Positive）

- **locality** —— 记录 schema 住在一个构建器；schema 修一处，不再三 emitter 分叉。
- **leverage** —— 一个签名替三个私有实现；parser 与生产者由构造共享一套字段词汇（D2）。
- **interface 收缩，测试命中单一 seam** —— 构建器对三个 `source` 路径都可单测，无需 CLI/rollback/DB 管线。
- **已记录的漂移闭合** —— "Simplified version" 的 lib emitter 不再发散，因为没有要同步的东西了。

### 负面（Negative）

- **七个调用点重定向**（commit_session.py 4 个、sessions/commit.py 1 个、rollback/core.py 3 个）。缓解：纯重定位；现有 emit/parse 测试即回归门。
- **构建器长出按来源分支的条件**（commit vs rollback extras）。接受：这种条件性本就存在，散在三文件；集中它正是目的。

### 风险（Risks）

- **构建器改名某字段但 `log_analysis` 没改。** 这正是 D2 消除的风险——两者绑定同一组导出常量。净比今天更安全。
- **monkeypatch 靶点移动。** `tests/unit/test_commit_session_events.py` patch `cs._emit_pending_verify`（L45）；重构后改 patch 构建器（或 `append_jsonl_record`）。一处枚举的测试更新。

## 实施路线图（Implementation Roadmap）

| 阶段 | IMP | 交付 | 推迟 |
| --- | --- | --- | --- |
| Phase 1（唯一） | [IMP-ADR050-01](IMP-ADR050-01-pending-verify-builder.md) | `pending_verify.py` 构建器 + 字段名常量；删除并重定向三个 emitter；`log_analysis` 绑定常量；新构建器单测；CONTEXT.md 术语 | — |

### 明确的非目标（YAGNI）

- **不**改写入什么、何时、写到哪（`reports/D1/d1_drift.jsonl` 不变）。
- **不**碰 `PipelineEvent` 主线（ADR-036）——那是另一条 `_emit_event` codepath。
- **不**把记录提升为 typed dataclass 返回——它仍是 dict（D4）。

## 领域语言（CONTEXT.md 增补）

- **Pending Verify 记录（`pending_session_verify`）** —— 每次 pending 模式会话生命周期事件（提交成功、提交失败、回滚）末尾追加到 `reports/D1/d1_drift.jsonl` 的诊断型 JSONL 记录。一种**诊断写入**（ADR-042）——解释恢复状态，永不权威。
- **Pending Verify 构建器** —— `javdb/storage/sessions/pending_verify.py` 中唯一纯函数 `build_pending_verify_record()`，构造记录 dict；三个 emitter（CLI commit、lib commit、rollback）委托给它，`log_analysis` 绑定其导出的字段名常量。

## 备选方案（Alternatives Considered）

- **构建器内部取 `stats`。** 否决（D3）：逼每个构建器测试带 DB fixture，并把 retry/fallback 拉进纯函数。
- **typed `RollbackExtras` dataclass。** 否决（D4）：无收益的复杂度；记录是 dict 且字段名已被守护。
- **把 shadow-audit 移进 `storage/sessions`。** 否决（D5）：它是 CLI-only I/O；导入 `javdb/storage` 会无谓地撑大该层。

## 参考（References）

- [ADR-042 — D1 Atomic Commit Boundaries](../../ADR-042-D1-Atomic-Commit-Boundaries/ADR-042-d1-atomic-commit-boundaries.md)
- [ADR-019 — Session Lifecycle Authority](../ADR-019-Session-Lifecycle-Authority/ADR-019-session-lifecycle-authority.md)
- [ADR-026 — AI Operations Diagnosis](../../ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.md)
- 2026-06-13 架构评审：[architecture-review-2026-06-13.html](../../architecture/architecture-review-2026-06-13.html)

## 状态日志（Status Log）

- 2026-06-14：Completed 于 [IMP-ADR050-01](IMP-ADR050-01-pending-verify-builder.md)。计划中的单一阶段已交付纯 pending-verify builder、共享字段名常量、emitter/parser 重定向、CLI-only 告警决策 wrapper、聚焦测试、workflow 更新，以及 handbook 更新。本 ADR 不再有后续 IMP。
- 2026-06-13：Proposed（源自 2026-06-13 架构评审候选 3）。决定：`javdb/storage/sessions/pending_verify.py` 中一个纯 `build_pending_verify_record()`；导出字段名常量由生产者与 `log_analysis` 消费者共同绑定；纯构建器（调用方传预取 stats）；`rollback_extras` 作不透明 dict；shadow-audit + `GITHUB_OUTPUT` 留 CLI-only。核实：17 字段核心 schema（候选说 ~13；lib emitter 带 19 字段）；"Simplified version" docstring 漂移属实；诊断写入类别（ADR-042）——无迁移注解。IMP-ADR050-01 待办。
