# ADR-015: Integrations Interface Boundary

**Status**: Accepted - implementation pending
**Date**: 2026-05-20
**Deciders**: Integrations interface boundary brainstorming and grill session
**Related Implementation Plans**: [IMP-ADR015-01](../impl/IMP-ADR015-01-integrations-phase1-guard-workflow-adapters.md) (Phase 1 - guard and workflow adapters), [IMP-ADR015-02](../impl/IMP-ADR015-02-integrations-phase2-qb-command-packages.md) (Phase 2 - qB command packages), [IMP-ADR015-03](../impl/IMP-ADR015-03-integrations-phase3-pikpak-bridge.md) (Phase 3 - PikPak bridge package), [IMP-ADR015-04](../impl/IMP-ADR015-04-integrations-phase4-rclone-manager-split.md) (Phase 4 - rclone manager split), [IMP-ADR015-05](../impl/IMP-ADR015-05-integrations-phase5-rclone-cleanup.md) (Phase 5 - rclone cleanup), [IMP-ADR015-06](../impl/IMP-ADR015-06-integrations-phase6-notify-email-split.md) (Phase 6 - notify email split), [IMP-ADR015-07](../impl/IMP-ADR015-07-integrations-phase7-notify-cleanup.md) (Phase 7 - notify cleanup)

## Outstanding Work

- Phase 1 - add architecture guards with allowlists and introduce `javdb.workflow` adapters for artifact inputs, stats sinks, and git side effects.
- Phase 2 - migrate qB uploader and file filter into command packages with typed options/results and real `apps.cli.qb.*` adapters.
- Phase 3 - migrate PikPak bridge into a command package with typed options/result and real `apps.cli.pikpak.bridge` adapter.
- Phase 4 - split rclone manager into a command package and keep a short bake wrapper.
- Phase 5 - delete the rclone bake wrapper and remove rclone from allowlists.
- Phase 6 - split notify email into command package plus `log_analysis`, `report_builder`, and `delivery`, keeping a short bake wrapper.
- Phase 7 - delete the notify bake wrapper and remove notify from allowlists.

---

## Context

`javdb.integrations.*` currently acts as both library code and command-line
surface. The widest modules are:

- `javdb.integrations.qb.uploader`
- `javdb.integrations.qb.file_filter`
- `javdb.integrations.pikpak.bridge`
- `javdb.integrations.rclone.manager`
- `javdb.integrations.notify.email`

These modules mix `argparse`, CLI `main()` functions, `sys.exit()` behavior,
external-service API calls, workflow behavior, artifact discovery, stats
persistence, git commit/push side effects, logging, domain flow, and reusable
helpers.

`apps/cli/*` is nominally the canonical CLI tree, but several wrappers still
alias the integration implementation module through `sys.modules[__name__]`.
That makes the CLI import path and library import path the same module, keeping
tests, monkeypatch targets, CLI behavior, and library behavior coupled.

## Non-Negotiable Boundary Invariant

`apps.cli.*` is the only command-line surface.

`javdb.integrations.*` is a service/client surface. It may expose typed options,
typed results, service functions, clients, and domain helpers. It must not own
user-facing CLI parsing, CLI entrypoints, or process exit behavior.

## Non-Negotiable Runtime Invariant

This ADR is behavior-preserving.

Every phase must preserve GitHub Actions command names, flags, defaults,
stdout/log streaming, exit codes, git commit/push behavior, stats persistence,
proxy behavior, qB behavior, PikPak behavior, rclone behavior, notify behavior,
and workflow log visibility while commands are running.

## Decision

### D1. Make `apps.cli.*` The Only CLI Surface

Every user-facing command lives under `apps.cli.*`.

Every CLI module exposes:

```python
def main(argv: list[str] | None = None) -> int:
    return run_cli(argv)
```

Only module entry blocks call:

```python
if __name__ == "__main__":
    raise SystemExit(main())
```

### D2. Remove CLI Surface From `javdb.integrations.*`

Final-state integration modules do not define user-facing:

- `argparse` parsers;
- `parse_arguments()` functions;
- command `main()` functions;
- `sys.exit()` calls;
- `if __name__ == "__main__"` entry blocks.

### D3. Use Command Packages With Typed Contracts

Each integration command becomes a package:

```text
javdb/integrations/<domain>/<command>/
  __init__.py
  options.py
  result.py
  service.py
```

Every command service accepts a typed options dataclass and returns a typed
result dataclass. Results are small summaries for CLI adapters, tests, and
future API callers. Results do not replace streaming logs or carry complete
human-readable reports.

### D4. Keep Domain Services In `javdb.integrations.*` For This ADR

Command services remain under:

```text
javdb.integrations.<domain>.<command>.service
```

Cross-cutting workflow side effects move to `javdb.workflow.*`. A follow-up ADR
will evaluate whether command flow services should move from
`javdb.integrations.*` to `javdb.workflow.<domain>.<command>.service`.

### D5. Introduce `javdb.workflow.*` Adapters In Phase 1

Phase 1 creates:

- `javdb.workflow.artifact_inputs`
- `javdb.workflow.stats_sink`
- `javdb.workflow.git_side_effects`

These adapters own repeated workflow-style behavior that currently appears
inside integration commands: artifact discovery/reading, stats persistence, and
git commit/push side effects.

rclone CSV/DB output, notify attachments, log conversion, workflow outputs, and
pending-health snapshot helpers are excluded from Phase 1 and handled by domain
phases or follow-up ADRs.

### D6. Add Architecture Guards With Allowlists

Phase 1 adds executable guards for two boundaries:

- `javdb.integrations.*` must not contain CLI surface;
- `apps.cli.*` must not alias implementation modules by assigning
  `sys.modules[__name__] = integration_module`.

Existing violations are listed in allowlists. Each domain phase removes the
migrated domain from the allowlists.

### D7. qB And PikPak Close Their Legacy Surface In Their Domain Phase

qB and PikPak are small enough to migrate and close in one phase each.

Their domain phases update tests, monkeypatch targets, imports, docs, and
allowlists without leaving a bake wrapper.

### D8. Rclone And Notify Use Split Then Cleanup Phases

rclone and notify are larger and have broader tests. They migrate through:

- a split phase that creates typed contracts and keeps a short bake wrapper;
- a cleanup phase that deletes the wrapper, updates remaining imports/docs, and
  removes allowlist entries.

### D9. Do Not Deep-Split `rclone.helper` In This ADR

`javdb.integrations.rclone.helper` remains an internal dependency during this
ADR. Splitting it into `paths`, `health`, `inventory_scan`, `dedup_analysis`,
`execution`, and `reporting` is follow-up ADR scope.

### D10. Split Notify By Responsibility

`javdb.integrations.notify.email` becomes:

```text
options.py
result.py
service.py
log_analysis.py
report_builder.py
delivery.py
```

`service.py` orchestrates. `log_analysis.py` parses logs and report inputs.
`report_builder.py` builds subject/body content. `delivery.py` owns SMTP.

### D11. One ADR, Seven Phase Plans

This ADR rolls out through seven implementation plans:

- [IMP-ADR015-01](../impl/IMP-ADR015-01-integrations-phase1-guard-workflow-adapters.md)
- [IMP-ADR015-02](../impl/IMP-ADR015-02-integrations-phase2-qb-command-packages.md)
- [IMP-ADR015-03](../impl/IMP-ADR015-03-integrations-phase3-pikpak-bridge.md)
- [IMP-ADR015-04](../impl/IMP-ADR015-04-integrations-phase4-rclone-manager-split.md)
- [IMP-ADR015-05](../impl/IMP-ADR015-05-integrations-phase5-rclone-cleanup.md)
- [IMP-ADR015-06](../impl/IMP-ADR015-06-integrations-phase6-notify-email-split.md)
- [IMP-ADR015-07](../impl/IMP-ADR015-07-integrations-phase7-notify-cleanup.md)

Each phase has its own test gate and can bake independently.

## Final Shape

```text
apps.cli.<domain>.<command>
  -> argparse / logging setup / exit-code mapping
  -> javdb.integrations.<domain>.<command>.service.run_*(Options)
  -> Result
```

Integration services may call:

```text
javdb.workflow.artifact_inputs
javdb.workflow.stats_sink
javdb.workflow.git_side_effects
```

## Behavior Invariants

Every phase must preserve:

- GitHub Actions command names;
- CLI flags, defaults, and validation behavior;
- stdout and log-file streaming;
- exit-code semantics;
- git commit/push behavior;
- stats persistence behavior;
- proxy policy and proxy override behavior;
- qB primary/adhoc behavior;
- qB uploader duplicate handling and all-upload-failed exit behavior;
- qB file filter pending-metadata behavior and all-error exit behavior;
- PikPak batch and individual modes;
- PikPak qB delete behavior after successful upload;
- PikPak history writes;
- rclone scan/report/execute/execute-soft-delete/validate behavior;
- rclone staging, swap, CSV export, and dry-run behavior;
- notify SMTP failure exit behavior;
- notify pending-mode, dedup, proxy-ban, and drift advisory sections;
- workflow log visibility while commands are running.

## Follow-Up ADR Scope

- Split `javdb.integrations.rclone.helper` into `paths`, `health`,
  `inventory_scan`, `dedup_analysis`, `execution`, and `reporting`.
- Evaluate moving command flow services from `javdb.integrations.*` to
  `javdb.workflow.<domain>.<command>.service`.
- Commonize workflow outputs.
- Commonize notify attachments.
- Commonize log conversion.

## Consequences

### Positive

- CLI behavior has one owner: `apps.cli.*`.
- Integration modules expose narrower, typed service contracts.
- Tests can target CLI parsing separately from domain service behavior.
- Workflow side effects stop being copied through integration command modules.
- The architecture guard prevents new wide interfaces while the migration runs.

### Negative

- Seven phases are required because qB, PikPak, rclone, and notify have
  different blast radii.
- Module-to-package migration creates import churn.
- rclone and notify require temporary bake wrappers.

### Neutral

- This ADR does not reduce integration behavior. It changes ownership,
  contracts, and dependency boundaries.
