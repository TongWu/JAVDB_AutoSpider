# ADR-053：把 `D1TransientError` 的 monkey-patch 恢复信号提升为声明式类型化接口

| 字段       | 值                                                                 |
| ---------- | ----------------------------------------------------------------- |
| **状态**   | Completed（2026-06-14）——已由 [IMP-ADR053-01](IMP-ADR053-01-d1-transient-error-typed-recovery.md) 实现 |
| **日期**   | 2026-06-13                                                        |
| **作者**   | Ted                                                              |
| **关联**   | [ADR-009](../ADR-009-D1-Drift-Classifier/ADR-009-d1-drift-classifier-and-diagnose.md)（创建了 `D1TransientError` 分类器——拥有"*哪些*错误算 transient"，非恢复信号接口）、[ADR-042](../../ADR-042-D1-Atomic-Commit-Boundaries/ADR-042-d1-atomic-commit-boundaries.md)（这些信号喂给的恢复/提交边界**策略**——不变）、[ADR-010](../ADR-010-D1-Access-Port/ADR-010-d1-access-port.md)（设置信号的 `d1_port.py` 访问端口） |

> 源自 2026-06-13 架构评审（候选 6 ——"把 `D1TransientError` 的恢复属性提升为类型化接口"）：[architecture-review-2026-06-13.html](../../architecture/architecture-review-2026-06-13.html)。

## 背景（Context）

`D1TransientError`（`javdb/storage/d1_client.py:137`）**类体为空**（仅 docstring；链 `D1TransientError(D1Error(RuntimeError))`）。但其实例携带在 raise/handle 处 monkey-patch、在别处经防御性 `getattr` 读取的**跨模块恢复信号**——一个存在却对类型系统不可见的接口：

| 信号 | 设在 | 设值点（`# type: ignore[attr-defined]`） | 读取点 |
| --- | --- | --- | --- |
| `d1_recovery_outbox_required` | `D1TransientError` | `d1_port.py:292`、`:596` | `dual_connection._requires_durable_recovery:1125` |
| `d1_recovery_durable` | `D1TransientError` | `d1_port.py:293`、`:601`、`:608` | `dual_connection:1126` |
| `d1_recovery_blocker` | **裸 `RuntimeError`** | `d1_port.py:263` | `dual_connection._blocks_queued_recovery_flush:1133` |
| `retry_after` | `D1TransientError` | `d1_port.py:448` | `d1_port._compute_backoff:490`（模块内） |
| `is_export_lock` | `D1TransientError` | `d1_port.py:459`、`:483` | `d1_port:507`（模块内） |

不可见接口引出两个具体缺陷：

1. **拼错会静默关闭恢复。** `_requires_durable_recovery` 读 `getattr(exc, "d1_recovery_outbox_required", False)`——任一端拼错就默认 `False`，**悄无声息地关掉 outbox 恢复且无报错**。这正是声明式字段能变成类型错误的失败类。
2. **blocker 信号设在裸 `RuntimeError`、而非 `D1TransientError`**（`d1_port.py:259-263`），故它无法作为 `D1TransientError` 的字段。其读取处甚至带一个**字符串匹配兜底**—— `"unresolved D1 recovery work" in str(exc)`（`dual_connection:1134`）——这是*因为* monkey-patch 属性脆弱才加的双保险。

每个设值点的 `# type: ignore[attr-defined]` 就是编译器在说：这个接口未声明。

## 决策（Decision）

把每个恢复信号声明为类型化成员，并把 `getattr`/字符串匹配读取换成属性/`isinstance` 检查。

### 设计决策（Design Decisions）

**D1. 把四个 `D1TransientError` 信号声明为类级类型化字段并带默认。** 在 `D1TransientError` 上：

```python
class D1TransientError(D1Error):
    d1_recovery_outbox_required: bool = False
    d1_recovery_durable: bool = False
    is_export_lock: bool = False
    retry_after: Optional[str] = None
```

信号是在错误构造**之后**（在 `except` 处理块里、依据是否尝试过恢复）确定的，故保持**构造后赋值**于 raise/handle 点——但现在已声明（不用 `__init__` kwargs，那不契合流程；不用 `# type: ignore`）。不可变默认让每次读取都安全。

**D2. blocker 变成类型化 `D1RecoveryBlockerError(RuntimeError)` 子类。** 因为 `d1_recovery_blocker` 设在裸 `RuntimeError`（非 `D1TransientError`），其类型化表示是 `d1_client.py` 中一个专门的异常类（与 `D1Error` 同处）。`d1_port.py:259-263` 改 `raise D1RecoveryBlockerError("unresolved D1 recovery work for ordering key …")`，而非给泛型 `RuntimeError` monkey-patch 一个 flag。

**D3. 读取改类型化；删除字符串匹配兜底。** `_requires_durable_recovery` → `isinstance(exc, D1TransientError) and exc.d1_recovery_outbox_required and not exc.d1_recovery_durable`。`_blocks_queued_recovery_flush` → `isinstance(exc, D1RecoveryBlockerError)`。既然 D2 让 `D1RecoveryBlockerError` 成为*唯一*设值点，脆弱的 `"unresolved D1 recovery work" in str(exc)` 兜底就冗余了，**予以删除**——类型化检查即权威。

**D4. 纳入两个模块内信号（`retry_after`、`is_export_lock`）。** 尽管只在 `d1_port.py` 内设读（不可见接口风险较低），把它们也声明到 `D1TransientError`（D1）能消除剩余 `# type: ignore`，并让整个恢复接口在一处可见——是小而一致的收尾，而非留下两个落单者。

**D5. 行为不变。** 信号含义相同、在相同点设置、为相同决策读取。只有其*表示*变化：monkey-patch → 声明字段、裸-`RuntimeError`+flag → 类型化子类、`getattr`/字符串匹配 → 属性/`isinstance`。恢复策略（ADR-042）与瞬态分类（ADR-009）不动。

## 后果（Consequences）

### 正面（Positive）

- **interface** —— `D1TransientError` 的恢复契约在一处声明；类定义即真相源，而非散落六处的赋值。
- **correctness** —— 拼错信号现在是类型/属性错误，而非悄悄关闭 outbox 恢复的静默 `False`；脆弱的字符串匹配耦合被删。
- **locality** —— 所有恢复信号语义住在错误类上；读取方表达意图（`isinstance(exc, D1RecoveryBlockerError)`）而非鸭子类型。
- **测试命中真实 seam** —— 构造 `D1TransientError(...)` 并设 `d1_recovery_outbox_required` 等，以及 `D1RecoveryBlockerError`；测试无需 monkey-patch。
- **`# type: ignore[attr-defined]` 消失** —— 删 8 处抑制；类型检查器现在看得见接口。

### 负面（Negative）

- **一个新异常类 + 四个声明字段。** 微不足道；它取代一个未声明、monkey-patch 的契约。
- **类级看似可变的默认。** 缓解：所有默认都不可变（`bool`/`None`），故"类属性作默认"的惯用法安全。

### 风险（Risks）

- **某读取方假设信号在非 `D1TransientError` 的 exc 上。** 缓解：勘察枚举了每个设/读点；`outbox_required`/`durable` 只设在 `D1TransientError`，`blocker` 只经新子类。`isinstance` 守卫与实际 raise 类型吻合。
- **删字符串兜底漏掉别处 raise 的 blocker。** 缓解：`d1_port.py:259-263` 是*唯一*发出 blocker 信号的点；改为 `D1RecoveryBlockerError` 使 `isinstance` 完备。

## 实施路线图（Implementation Roadmap）

| 阶段 | IMP | 交付 | 推迟 |
| --- | --- | --- | --- |
| Phase 1（唯一） | [IMP-ADR053-01](IMP-ADR053-01-d1-transient-error-typed-recovery.md) | `D1TransientError` 上类型化字段；`D1RecoveryBlockerError` 子类；转换 raise/读取点；删字符串兜底；移除 `# type: ignore`；类型化信号单测；CONTEXT.md 术语 | — |

### 明确的非目标（YAGNI）

- **不**改恢复策略或 backoff 行为（ADR-042）——同样决策，类型化输入。
- **不**改瞬态/永久分类（ADR-009）。
- **不**引入 `D1RecoveryState` dataclass——异常上四个 bool/字符串足矣（见备选）。

## 领域语言（CONTEXT.md 增补）

- **D1 恢复信号（D1 recovery signals）** —— `D1TransientError` 上的类型化成员，告诉 dual facade 如何恢复一次失败的 D1 写入：`d1_recovery_outbox_required`（已经由 outbox 尝试恢复）、`d1_recovery_durable`（outbox 事件已持久化）、`retry_after` / `is_export_lock`（backoff 提示）。声明字段带安全默认（ADR-053）——原为带 `# type: ignore` 的 monkey-patch。
- **`D1RecoveryBlockerError`** —— 当未解决的 D1 恢复工作阻塞 queued 写入刷新时抛出的 `RuntimeError` 子类；是原 `d1_recovery_blocker`（monkey-patch 到裸 `RuntimeError` 的 flag）的类型化替代。`dual_connection` 经 `isinstance` 检测，而非字符串匹配。

## 备选方案（Alternatives Considered）

- **`D1RecoveryState` dataclass 挂为 `exc.recovery`。** 否决：对两/四个标量信号是过度设计；读取（`exc.recovery.outbox_required`）并不比声明字段更清晰，还加一层间接。
- **`D1TransientError` 上 `__init__` kwargs。** 否决（D1）：信号在构造*之后*于 `except` 处理块确定，而非 raise 时；对声明字段的构造后赋值契合实际流程。
- **保留带默认的 `getattr` 读取。** 否决（D3）：那*就是*本 ADR 要消除的静默-`False`-on-typo 缺陷。
- **保留字符串匹配兜底。** 否决（D3）：`D1RecoveryBlockerError` 成为唯一 raise 点后，`isinstance` 即权威；兜底保留了脆弱耦合。

## 参考（References）

- [ADR-009 — D1 Drift Classifier & Diagnose](../ADR-009-D1-Drift-Classifier/ADR-009-d1-drift-classifier-and-diagnose.md)
- [ADR-042 — D1 Atomic Commit Boundaries](../../ADR-042-D1-Atomic-Commit-Boundaries/ADR-042-d1-atomic-commit-boundaries.md)
- [ADR-010 — D1 Access Port](../ADR-010-D1-Access-Port/ADR-010-d1-access-port.md)
- 2026-06-13 架构评审：[architecture-review-2026-06-13.html](../../architecture/architecture-review-2026-06-13.html)

## 状态日志（Status Log）

- 2026-06-14：Completed 于 [IMP-ADR053-01](IMP-ADR053-01-d1-transient-error-typed-recovery.md)。计划中的单一阶段已交付类型化 `D1TransientError` 恢复字段、`D1RecoveryBlockerError`、类型化 raise/read 点、移除字符串兜底与 type ignore、聚焦恢复信号测试，以及 CONTEXT.md 术语。本 ADR 不再有后续 IMP。
- 2026-06-13：Proposed（源自 2026-06-13 架构评审候选 6）。勘察核实：`D1TransientError` 类体为空；5 个信号在 `d1_port`/`dual_connection` 间 monkey-patch，带 8 处 `# type: ignore`；`d1_recovery_blocker` 设在**裸 `RuntimeError`**（故新建子类）；`_blocks_queued_recovery_flush` 带脆弱的 `str(exc)` 兜底。决定（grilling）：全部 5 个（3 跨模块 + 2 模块内）声明为类型化成员；`D1RecoveryBlockerError(RuntimeError)`；读取改 `isinstance`/属性；**删除**字符串兜底。ADR-009（分类器）与 ADR-042（策略）拥有分类/策略，非信号表示——无碰撞。IMP-ADR053-01 待办。
