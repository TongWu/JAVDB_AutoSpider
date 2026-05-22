# ADR-015：Integrations Interface 边界收束

**状态**：已接受 - 实现待启动
**日期**：2026-05-20
**决策者**：Integrations interface boundary brainstorming + grill 会话
**关联实现计划 (Related Implementation Plans)**：[IMP-ADR015-01](IMP-ADR015-01-integrations-phase1-guard-workflow-adapters.md)（Phase 1 - guard and workflow adapters）、[IMP-ADR015-02](IMP-ADR015-02-integrations-phase2-qb-command-packages.md)（Phase 2 - qB command packages）、[IMP-ADR015-03](IMP-ADR015-03-integrations-phase3-pikpak-bridge.md)（Phase 3 - PikPak bridge package）、[IMP-ADR015-04](IMP-ADR015-04-integrations-phase4-rclone-manager-split.md)（Phase 4 - rclone manager split）、[IMP-ADR015-05](IMP-ADR015-05-integrations-phase5-rclone-cleanup.md)（Phase 5 - rclone cleanup）、[IMP-ADR015-06](IMP-ADR015-06-integrations-phase6-notify-email-split.md)（Phase 6 - notify email split）、[IMP-ADR015-07](IMP-ADR015-07-integrations-phase7-notify-cleanup.md)（Phase 7 - notify cleanup）

## 待办 (Outstanding Work)

- Phase 1 - 增加带 allowlist 的 architecture guards，并引入 `javdb.workflow` adapters：artifact inputs、stats sinks、git side effects。
- Phase 2 - 将 qB uploader 和 file filter 迁成 command packages，使用 typed options/results 和真正的 `apps.cli.qb.*` adapters。
- Phase 3 - 将 PikPak bridge 迁成 command package，使用 typed options/result 和真正的 `apps.cli.pikpak.bridge` adapter。
- Phase 4 - 拆分 rclone manager command package，并保留短期 bake wrapper。
- Phase 5 - 删除 rclone bake wrapper，并从 allowlists 中移除 rclone。
- Phase 6 - 将 notify email 拆成 command package，以及 `log_analysis`、`report_builder`、`delivery`，并保留短期 bake wrapper。
- Phase 7 - 删除 notify bake wrapper，并从 allowlists 中移除 notify。

---

## 背景

`javdb.integrations.*` 当前同时扮演 library code 和 command-line surface。最宽的模块是：

- `javdb.integrations.qb.uploader`
- `javdb.integrations.qb.file_filter`
- `javdb.integrations.pikpak.bridge`
- `javdb.integrations.rclone.manager`
- `javdb.integrations.notify.email`

这些模块混合了 `argparse`、CLI `main()`、`sys.exit()`、外部服务 API 调用、
workflow 行为、artifact discovery、stats persistence、git commit/push side
effects、logging、domain flow 和可复用 helpers。

`apps/cli/*` 名义上已经是 canonical CLI tree，但多个 wrapper 仍然通过
`sys.modules[__name__]` alias 到 integration implementation module。这样 CLI
import path 和 library import path 仍是同一个 module，测试、monkeypatch target、
CLI 行为和 library 行为继续耦合在一起。

## 不可协商边界不变量

`apps.cli.*` 是唯一 command-line surface。

`javdb.integrations.*` 是 service/client surface。它可以暴露 typed options、
typed results、service functions、clients 和 domain helpers，但不能拥有用户级 CLI
parsing、CLI entrypoints 或 process exit behavior。

## 不可协商运行时不变量

本 ADR 是行为保持型迁移。

每个 phase 都必须保持 GitHub Actions command names、flags、defaults、
stdout/log streaming、exit codes、git commit/push behavior、stats persistence、
proxy behavior、qB behavior、PikPak behavior、rclone behavior、notify behavior，以及
workflow 运行中的 log visibility。

## 决策

### D1. `apps.cli.*` 成为唯一 CLI Surface

所有用户级 command 都位于 `apps.cli.*`。

每个 CLI module 暴露：

```python
def main(argv: list[str] | None = None) -> int:
    return run_cli(argv)
```

只有 module entry block 调用：

```python
if __name__ == "__main__":
    raise SystemExit(main())
```

### D2. 从 `javdb.integrations.*` 移除 CLI Surface

最终状态下，integration modules 不定义用户级：

- `argparse` parsers；
- `parse_arguments()` functions；
- command `main()` functions；
- `sys.exit()` calls；
- `if __name__ == "__main__"` entry blocks。

### D3. 使用带 Typed Contracts 的 Command Packages

每个 integration command 迁为一个 package：

```text
javdb/integrations/<domain>/<command>/
  __init__.py
  options.py
  result.py
  service.py
```

每个 command service 接收 typed options dataclass，并返回 typed result dataclass。
Result 是给 CLI adapter、测试和未来 API caller 使用的小型摘要。Result 不替代 streaming
logs，也不承载完整人类可读报告。

### D4. 本 ADR 内 Domain Services 仍留在 `javdb.integrations.*`

Command services 仍位于：

```text
javdb.integrations.<domain>.<command>.service
```

横切 workflow side effects 迁到 `javdb.workflow.*`。后续 ADR 会评估 command flow
services 是否应从 `javdb.integrations.*` 迁到
`javdb.workflow.<domain>.<command>.service`。

### D5. Phase 1 引入 `javdb.workflow.*` Adapters

Phase 1 创建：

- `javdb.workflow.artifact_inputs`
- `javdb.workflow.stats_sink`
- `javdb.workflow.git_side_effects`

这些 adapters 拥有当前散落在 integration commands 中的重复 workflow-style 行为：
artifact discovery/reading、stats persistence、git commit/push side effects。

rclone CSV/DB output、notify attachments、log conversion、workflow outputs 和
pending-health snapshot helpers 不属于 Phase 1，交给 domain phases 或后续 ADR。

### D6. 增加带 Allowlist 的 Architecture Guards

Phase 1 为两个边界增加可执行 guard：

- `javdb.integrations.*` 不得包含 CLI surface；
- `apps.cli.*` 不得通过 `sys.modules[__name__] = integration_module` alias 到
  implementation module。

现有违规进入 allowlists。每个 domain phase 迁完后，将对应 domain 从 allowlists 移除。

### D7. qB 和 PikPak 在各自 Domain Phase 内关闭 Legacy Surface

qB 和 PikPak 的范围可控，可以在各自 phase 内完成迁移并收口。

它们的 domain phase 同步更新 tests、monkeypatch targets、imports、docs 和 allowlists，
不保留 bake wrapper。

### D8. Rclone 和 Notify 使用 Split + Cleanup Phases

rclone 和 notify 更大，测试面也更宽。它们通过两步迁移：

- split phase 创建 typed contracts，并保留短期 bake wrapper；
- cleanup phase 删除 wrapper，更新剩余 imports/docs，并移除 allowlist entries。

### D9. 本 ADR 不深拆 `rclone.helper`

`javdb.integrations.rclone.helper` 在本 ADR 中继续作为内部依赖。将它拆为 `paths`、
`health`、`inventory_scan`、`dedup_analysis`、`execution` 和 `reporting` 属于后续 ADR。

### D10. 按职责拆分 Notify

`javdb.integrations.notify.email` 迁为：

```text
options.py
result.py
service.py
log_analysis.py
report_builder.py
delivery.py
```

`service.py` 负责编排。`log_analysis.py` 解析 logs 和 report inputs。
`report_builder.py` 构建 subject/body。`delivery.py` 拥有 SMTP。

### D11. 一个 ADR，七个 Phase Plans

本 ADR 通过七个 implementation plans rollout：

- [IMP-ADR015-01](IMP-ADR015-01-integrations-phase1-guard-workflow-adapters.md)
- [IMP-ADR015-02](IMP-ADR015-02-integrations-phase2-qb-command-packages.md)
- [IMP-ADR015-03](IMP-ADR015-03-integrations-phase3-pikpak-bridge.md)
- [IMP-ADR015-04](IMP-ADR015-04-integrations-phase4-rclone-manager-split.md)
- [IMP-ADR015-05](IMP-ADR015-05-integrations-phase5-rclone-cleanup.md)
- [IMP-ADR015-06](IMP-ADR015-06-integrations-phase6-notify-email-split.md)
- [IMP-ADR015-07](IMP-ADR015-07-integrations-phase7-notify-cleanup.md)

每个 phase 都有自己的测试门禁，并可独立 bake。

## 最终形态

```text
apps.cli.<domain>.<command>
  -> argparse / logging setup / exit-code mapping
  -> javdb.integrations.<domain>.<command>.service.run_*(Options)
  -> Result
```

Integration services 可以调用：

```text
javdb.workflow.artifact_inputs
javdb.workflow.stats_sink
javdb.workflow.git_side_effects
```

## 行为不变量

每个 phase 都必须保持：

- GitHub Actions command names；
- CLI flags、defaults 和 validation behavior；
- stdout 和 log-file streaming；
- exit-code semantics；
- git commit/push behavior；
- stats persistence behavior；
- proxy policy 和 proxy override behavior；
- qB primary/adhoc behavior；
- qB uploader duplicate handling 和 all-upload-failed exit behavior；
- qB file filter pending-metadata behavior 和 all-error exit behavior；
- PikPak batch 和 individual modes；
- PikPak 成功上传后的 qB delete behavior；
- PikPak history writes；
- rclone scan/report/execute/execute-soft-delete/validate behavior；
- rclone staging、swap、CSV export 和 dry-run behavior；
- notify SMTP failure exit behavior；
- notify pending-mode、dedup、proxy-ban 和 drift advisory sections；
- workflow 运行中的 log visibility。

## 后续 ADR 范围

- 将 `javdb.integrations.rclone.helper` 拆分为 `paths`、`health`、
  `inventory_scan`、`dedup_analysis`、`execution` 和 `reporting`。
- 评估是否将 command flow services 从 `javdb.integrations.*` 迁到
  `javdb.workflow.<domain>.<command>.service`。
- commonize workflow outputs。
- commonize notify attachments。
- commonize log conversion。

## 后果

### 正面

- CLI behavior 只有一个 owner：`apps.cli.*`。
- Integration modules 暴露更窄的 typed service contracts。
- 测试可以分别覆盖 CLI parsing 和 domain service behavior。
- Workflow side effects 不再复制散落在 integration command modules 中。
- Architecture guard 可以在迁移期间防止新的宽接口出现。

### 负面

- qB、PikPak、rclone 和 notify 的 blast radius 不同，因此需要七个 phases。
- Module-to-package migration 会产生 import churn。
- rclone 和 notify 需要 temporary bake wrappers。

### 中性

本 ADR 不减少 integration behavior。它只改变 ownership、contracts 和 dependency
boundaries。
