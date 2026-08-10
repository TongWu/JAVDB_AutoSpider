# BFR-026：对账轮次未经验证就删除 missingFiles 种子及其文件

**Status**: Fixed
**Date**: 2026-08-09
**Severity**: High
**Affected**: `javdb/ops/reconcile/service.py`、`javdb/ops/reconcile/models.py`、`apps/cli/ops/reconcile.py`、`.github/workflows/ReconcileLibrary.yml`
**Related**: [ADR-033](../_archive/ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.zh.md)、`javdb/integrations/qb/purge_missing_files.py`、PR #210 与 #251

---

## 现象

没有观察到实际事故。该缺陷是在把公开镜像的 `dev` 晋级到 `main` 时（TongWu/JAVDB_AutoSpider#153）经审查发现，并对照代码确认的。

采集对账轮次会把每一个有 `AcquisitionOutcome` 跟踪行的 `missingFiles` 种子删除，**且带 `delete_files=True`**，依据仅仅是同一次运行中早先取得的状态快照：

```python
for qb_hash in missing_files_hashes:
    if qb_hash not in active:
        continue
    _client.delete_torrents([qb_hash], delete_files=True)
```

`ReconcileLibrary.yml` 每小时运行一次。

## 根因

`missingFiles` 是**有歧义的**。内容确实被从磁盘移除时，qB 报这个状态；承载内容的磁盘临时不可用时——挂载点抖动、NAS 未挂载、存储机重启中——qB 同样报这个状态。仅凭状态无法区分两者。

由此产生两个后果，且都很糟：

- 若挂载点在快照与删除调用之间恢复，`delete_files=True` 会删掉**仍然存在且仍然需要**的内容。
- 若它持续不可用，被跟踪的种子条目仍会被丢弃，尽管文件之后可能重新出现。

更深层的问题是**职责归属**。PR `#210` 给了对账轮次这个删除动作。PR `#251` 随后新增了 `javdb/integrations/qb/purge_missing_files.py`，它恰恰正确地解决了这个歧义——先停止种子，强制 recheck 让 qB 重新对磁盘校验，读取逐文件存在性，**保留**任何无法确认的项，并且只处理完成时间已超过 `min_age_hours`（默认 22 小时）的种子。它自己的模块 docstring 就把这个歧义写成了存在理由。

PR `#251` 没有做的，是移除旧的无防护路径。于是代码库里留下了**同一个破坏性动作的两个责任方**：一个谨慎，一个不谨慎，而不谨慎的那个运行频率高 24 倍。

## 证据

| 路径 | `delete_files=True` 前的验证 | 调度频率 |
| --- | --- | --- |
| `javdb/ops/reconcile/service.py` | 无 —— 仅状态快照 | 每小时 |
| `javdb/integrations/qb/purge_missing_files.py` | stop → recheck → 逐文件存在性 → 无法确认则保留 → ≥22h 龄期保护 | 每天 |

purge 路径的覆盖面也严格更广：它扫描**全部** `missingFiles` 种子，而对账轮次只处理有 `AcquisitionOutcome` 行的那些。没有任何种子只能经由对账轮次被清理。

## 修复

直接从对账轮次删掉这段破坏性代码，而不是把验证逻辑搬进去。理由：

1. 安全实现已经存在、已经每天运行、且覆盖范围是超集。
2. 搬运逻辑会留下同一段微妙且事关安全的例程的两份副本，此后必须同步维护。
3. 对账轮次真正的职责——推进 `AcquisitionOutcome` 状态——完全不受影响。

**`completed` 状态推进被保留。** 它从来不依赖删除路径：`missingFiles` 是 `javdb/ops/reconcile/collectors.py` 中 `_QB_COMPLETED_STATES` 的成员，推进经由常规观测流完成。

作为本次改动的孤儿一并移除：只服务于被删代码块的 `missing_files_hashes` 集合、仅为存活到该代码块而存在的函数级 `_client` 提升，以及 `ReconcileResult.missing_files_deleted` 计数器及其 CLI 摘要行（此后它只可能恒为 0，留着反而误导）。

唯一的行为代价是删除延后：确实已丢失的种子现在会在 qB 中滞留最多约 24 小时，而非约 1 小时。这严格更安全，而且 purge 路径本来就刻意等待 22 小时。

## 副作用

- `ReconcileResult.missing_files_deleted` 从结果模型以及 `--json` / 文本摘要中消失。没有其他地方读取它。
- 关注每小时对账摘要的运维者不会再看到「Missing files deleted from qB」这一行。每天的 `PurgeMissingFiles` 运行会报告它自己的计数。
- 无 D1 schema 变更；无需迁移。

## 后续事项

- [x] 从采集轮次移除未经验证的删除
- [x] 用测试固定新契约（`test_run_treats_missing_files_as_completed_without_deleting_from_qb`、`test_run_never_deletes_untracked_missing_files_hashes`）
- [x] 修正 `ReconcileLibrary.yml` 的头部注释与 `github-actions-setup.md` 手册条目——两处此前都记载了该删除行为
- [ ] 考虑增加一个契约测试，断言 `delete_torrents(..., delete_files=True)` 只能从 `purge_missing_files` 到达，避免第三个调用方重新引入同类缺陷
