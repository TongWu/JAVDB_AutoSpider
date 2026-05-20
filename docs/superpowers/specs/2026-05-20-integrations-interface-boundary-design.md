# Integrations Interface Boundary Design

Date: 2026-05-20

## Context

`javdb.integrations.*` currently acts as both library code and command-line
surface. The widest modules are:

- `javdb.integrations.qb.uploader`
- `javdb.integrations.qb.file_filter`
- `javdb.integrations.pikpak.bridge`
- `javdb.integrations.rclone.manager`
- `javdb.integrations.notify.email`

These modules mix several responsibilities:

- `argparse` parser construction;
- CLI `main()` functions and `sys.exit()` behavior;
- workflow-invoked command behavior;
- external-service API calls;
- CSV/report artifact discovery and reading;
- stats persistence;
- git commit/push side effects;
- log and stdout behavior;
- domain business flow;
- reusable helper functions.

`apps/cli/*` is nominally the canonical CLI tree, but several wrappers still
alias the integration implementation module through `sys.modules[__name__]`.
That makes the CLI import path and library import path the same module, which
keeps test monkeypatches, CLI behavior, and library behavior coupled.

## Goals

- Make `apps.cli.*` the only CLI surface.
- Make `javdb.integrations.*` a service/client surface with narrow typed
  options and results.
- Give every integration command its own package with explicit
  `options.py`, `result.py`, and `service.py`.
- Move repeated workflow-style side effects into `javdb.workflow.*`.
- Preserve all production behavior while changing structure.
- Add architecture guards with allowlists so each domain can migrate
  independently.
- Roll out through seven implementation phases, each with its own IMP.

## Non-Goals

- Do not redesign qBittorrent, PikPak, rclone, or email behavior.
- Do not change GitHub Actions command names or flags.
- Do not change streaming logs.
- Do not change exit-code semantics.
- Do not deep-split `javdb.integrations.rclone.helper` in this ADR.
- Do not move command flow services from `javdb.integrations.*` to
  `javdb.workflow.*` in this ADR.
- Do not commonize notify attachments, log conversion, or workflow output
  helpers in Phase 1.

## Selected Approach

Use a guard-first, phased command-package migration.

Phase 1 establishes shared workflow adapters and architecture guards. Later
phases migrate one integration domain at a time in this order:

1. qB
2. PikPak
3. rclone
4. notify

qB and PikPak remove their legacy surfaces in the same phase. rclone and notify
are larger, so each gets a split phase with a short bake wrapper followed by a
cleanup phase that deletes the wrapper.

## Architecture

Final dependency shape:

```text
apps.cli.<domain>.<command>
  -> parse argv / configure logging / map Result to exit code
  -> javdb.integrations.<domain>.<command>.service.run_*
     -> typed Options
     -> typed Result
     -> external clients / domain helpers
     -> javdb.workflow.* adapters for cross-cutting side effects
```

Core rules:

- `javdb.integrations.*` must not own CLI surface.
- `javdb.integrations.*` must not define `argparse` parsers for user-facing
  commands.
- `javdb.integrations.*` must not expose command `main()` or
  `parse_arguments()` functions.
- `javdb.integrations.*` must not call `sys.exit()`.
- `javdb.integrations.*` must not contain `if __name__ == "__main__"` command
  entry blocks.
- `apps.cli.*` is the only CLI surface.
- Every `apps.cli.*` command exposes `main(argv=None) -> int`.
- Only module entry blocks call `raise SystemExit(main())`.
- `apps.cli.*` must not alias integration modules by assigning
  `sys.modules[__name__] = integration_module`.

Phase 1 adds architecture tests with allowlists for existing violations. Each
domain phase removes its migrated files from the allowlist.

## Command Package Shape

Each migrated command becomes a package:

```text
javdb/integrations/<domain>/<command>/
  __init__.py
  options.py
  result.py
  service.py
```

Examples:

```text
javdb/integrations/qb/uploader/
javdb/integrations/qb/file_filter/
javdb/integrations/pikpak/bridge/
javdb/integrations/rclone/manager/
javdb/integrations/notify/email/
```

Existing same-name modules are moved into the new package during migration.
For example, `javdb/integrations/qb/uploader.py` becomes a package under
`javdb/integrations/qb/uploader/`.

## Workflow Adapters

Phase 1 creates `javdb.workflow.*` for cross-cutting workflow side effects.

Initial modules:

- `javdb.workflow.artifact_inputs`
- `javdb.workflow.stats_sink`
- `javdb.workflow.git_side_effects`

`artifact_inputs.py`
: Shared CSV/report artifact discovery and reading. Phase 1 starts with the
  input patterns needed by qB uploader and leaves notify attachments/log
  conversion for the notify split and cleanup phases.

`stats_sink.py`
: Narrow methods for saving run stats such as uploader and PikPak stats. It can
  call existing storage functions internally, but integration services do not
  import storage stats details directly.

`git_side_effects.py`
: Shared workflow git commit/push side-effect adapter. It owns credential
  checks, log-handler flushing, commit file lists, commit messages, and
  delegation to existing git helpers.

rclone CSV/DB output, notify attachments, log conversion, workflow outputs, and
pending-health snapshot helpers are not part of Phase 1.

## Domain Components

### qB

Final qB packages:

```text
javdb/integrations/qb/uploader/options.py
javdb/integrations/qb/uploader/result.py
javdb/integrations/qb/uploader/service.py
javdb/integrations/qb/file_filter/options.py
javdb/integrations/qb/file_filter/result.py
javdb/integrations/qb/file_filter/service.py
```

`javdb.integrations.qb.client` remains the qBittorrent Web API primitive layer.
`javdb.integrations.qb.config` remains connection configuration support.

qB service modules use `javdb.workflow.artifact_inputs`,
`javdb.workflow.stats_sink`, and `javdb.workflow.git_side_effects` instead of
owning those side effects inline.

### PikPak

Final PikPak package:

```text
javdb/integrations/pikpak/bridge/options.py
javdb/integrations/pikpak/bridge/result.py
javdb/integrations/pikpak/bridge/service.py
```

PikPak keeps its batch/individual transfer behavior, qB primary/adhoc lookup,
history writes, stats persistence, proxy behavior, and git side effects. The
service returns a typed `PikPakBridgeResult`; the CLI maps that result to the
existing exit behavior.

### rclone

Final manager package:

```text
javdb/integrations/rclone/manager/options.py
javdb/integrations/rclone/manager/result.py
javdb/integrations/rclone/manager/service.py
```

`javdb.integrations.rclone.helper` remains an internal dependency in this ADR.
Deep-splitting it into `paths`, `health`, `inventory_scan`, `dedup_analysis`,
`execution`, and `reporting` is follow-up ADR scope.

rclone gets a split phase with a bake wrapper, then a cleanup phase that removes
the wrapper and allowlist entries.

### notify

Final notify package:

```text
javdb/integrations/notify/email/options.py
javdb/integrations/notify/email/result.py
javdb/integrations/notify/email/service.py
javdb/integrations/notify/email/log_analysis.py
javdb/integrations/notify/email/report_builder.py
javdb/integrations/notify/email/delivery.py
```

Responsibilities:

- `service.py` orchestrates the notification flow.
- `log_analysis.py` parses pipeline, spider, uploader, PikPak, dedup, proxy-ban,
  and pending-mode inputs.
- `report_builder.py` builds subject lines and email body sections.
- `delivery.py` sends SMTP email.
- `options.py` and `result.py` define the command contract.

notify gets a split phase with a bake wrapper, then a cleanup phase that removes
the wrapper and allowlist entries.

## Data Flow

### qB Uploader

```text
apps.cli.qb.uploader.main(argv)
  -> parse argparse into QbUploaderOptions
  -> javdb.integrations.qb.uploader.service.run_uploader(options)
     -> javdb.workflow.artifact_inputs.resolve_csv_input(...)
     -> javdb.integrations.qb.client.QBittorrentClient / existing qB primitives
     -> javdb.workflow.stats_sink.save_uploader_stats(...)
     -> javdb.workflow.git_side_effects.commit_workflow_outputs(...)
  -> QbUploaderResult
  -> CLI maps result status to return code
```

### qB File Filter

```text
apps.cli.qb.file_filter.main(argv)
  -> QbFileFilterOptions
  -> javdb.integrations.qb.file_filter.service.run_file_filter(options)
     -> javdb.integrations.qb.client
     -> existing filtering primitives
  -> QbFileFilterResult
  -> CLI maps result status to return code
```

### PikPak

```text
apps.cli.pikpak.bridge.main(argv)
  -> PikPakBridgeOptions
  -> javdb.integrations.pikpak.bridge.service.run_bridge(options)
     -> qB clients
     -> PikPak upload primitives
     -> history writes
     -> javdb.workflow.stats_sink
     -> javdb.workflow.git_side_effects
  -> PikPakBridgeResult
```

### rclone

```text
apps.cli.rclone.manager.main(argv)
  -> RcloneManagerOptions
  -> javdb.integrations.rclone.manager.service.run_manager(options)
     -> existing rclone helper and manager internals
  -> RcloneManagerResult
```

### notify

```text
apps.cli.notify.email.main(argv)
  -> EmailNotificationOptions
  -> javdb.integrations.notify.email.service.run_email_notification(options)
     -> log_analysis
     -> report_builder
     -> delivery
  -> EmailNotificationResult
```

## Result Contracts

Every command service returns a typed result dataclass. Results are intentionally
small. They carry only what CLI adapters, tests, and future API callers need:

- status;
- counts;
- warnings;
- error reason;
- artifact paths;
- whether an external side effect ran;
- enough detail to preserve exit-code decisions.

Results do not replace streaming logs and do not attempt to carry complete
human-readable reports.

## Behavior Invariants

This migration is structural. Every phase must preserve:

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

No phase may simplify behavior as part of structure cleanup.

## Compatibility Strategy

Phase 1 uses allowlists for existing violations:

- integration modules that still expose CLI surface;
- `apps.cli.*` modules that still alias integration modules.

qB and PikPak remove legacy surfaces in their domain phase. Their tests,
monkeypatches, imports, docs, and allowlist entries are updated in the same
phase.

rclone and notify may keep short bake wrappers during their split phases.
Dedicated cleanup phases delete those wrappers, update tests/imports/docs, and
remove allowlist entries.

`python -m javdb.integrations...` is not a compatibility target. The only
supported command surface is `python -m apps.cli...`.

## Phase Plan

### Phase 1: Guard And Workflow Adapters

Implementation plan: `IMP-030`.

Actions:

- add architecture guard for integrations CLI surface with allowlist;
- add architecture guard for `apps.cli` alias modules with allowlist;
- create `javdb.workflow.artifact_inputs`;
- create `javdb.workflow.stats_sink`;
- create `javdb.workflow.git_side_effects`;
- add focused unit tests for the three workflow adapter modules;
- update README/docs to describe `apps.cli` as CLI surface and integrations as
  service/client surface.

### Phase 2: qB Uploader And File Filter

Implementation plan: `IMP-031`.

Actions:

- migrate `javdb.integrations.qb.uploader` from module to package;
- migrate `javdb.integrations.qb.file_filter` from module to package;
- add `Options`, `Result`, and `service` modules for both commands;
- move CLI parsing and exit mapping into `apps.cli.qb.uploader` and
  `apps.cli.qb.file_filter`;
- use `javdb.workflow` adapters for qB artifact inputs, stats, and git side
  effects;
- update tests and monkeypatch targets;
- remove qB entries from architecture allowlists in the same phase.

### Phase 3: PikPak Bridge

Implementation plan: `IMP-032`.

Actions:

- migrate `javdb.integrations.pikpak.bridge` from module to package;
- add `Options`, `Result`, and `service` modules;
- move CLI parsing and exit mapping into `apps.cli.pikpak.bridge`;
- use `javdb.workflow.stats_sink` and `javdb.workflow.git_side_effects`;
- preserve qB primary/adhoc behavior and PikPak transfer behavior;
- update tests and monkeypatch targets;
- remove PikPak entries from architecture allowlists in the same phase.

### Phase 4: Rclone Manager Split And Bake Wrapper

Implementation plan: `IMP-033`.

Actions:

- migrate `javdb.integrations.rclone.manager` from module to package;
- add `Options`, `Result`, and `service` modules;
- move CLI parsing and exit mapping into `apps.cli.rclone.manager`;
- keep a short bake wrapper for legacy import/patch paths;
- keep `javdb.integrations.rclone.helper` intact;
- preserve scan/report/execute/validate behavior;
- update tests to cover the new service contract while keeping wrapper
  compatibility tests.

### Phase 5: Rclone Cleanup

Implementation plan: `IMP-034`.

Actions:

- delete the rclone bake wrapper;
- update remaining tests/imports/docs;
- remove rclone entries from architecture allowlists;
- verify manager CLI and service tests still pass.

### Phase 6: Notify Email Split And Bake Wrapper

Implementation plan: `IMP-035`.

Actions:

- migrate `javdb.integrations.notify.email` from module to package;
- add `Options`, `Result`, and `service` modules;
- split log parsing into `log_analysis.py`;
- split subject/body formatting into `report_builder.py`;
- split SMTP sending into `delivery.py`;
- move CLI parsing and exit mapping into `apps.cli.notify.email`;
- keep a short bake wrapper for legacy import/patch paths;
- preserve SMTP failure exit behavior and all existing report sections.

### Phase 7: Notify Cleanup

Implementation plan: `IMP-036`.

Actions:

- delete the notify bake wrapper;
- update remaining tests/imports/docs;
- remove notify entries from architecture allowlists;
- verify email CLI, report-builder, log-analysis, delivery, and service tests
  pass.

## Testing Strategy

Phase 1:

- architecture guard tests for integrations CLI surface;
- architecture guard tests for `apps.cli` alias modules;
- unit tests for `javdb.workflow.artifact_inputs`;
- unit tests for `javdb.workflow.stats_sink`;
- unit tests for `javdb.workflow.git_side_effects`.

Phase 2:

- `QbUploaderOptions` and `QbUploaderResult` tests;
- `QbFileFilterOptions` and `QbFileFilterResult` tests;
- CLI parser tests for qB flags/defaults;
- service tests for uploader and file filter behavior;
- existing qB client, uploader, and file filter tests;
- architecture guard with qB removed from allowlists.

Phase 3:

- `PikPakBridgeOptions` and `PikPakBridgeResult` tests;
- CLI parser tests for PikPak flags/defaults;
- service tests for batch/individual behavior;
- tests for qB primary/adhoc behavior;
- tests for history/stats/git/proxy behavior;
- architecture guard with PikPak removed from allowlists.

Phase 4:

- `RcloneManagerOptions` and `RcloneManagerResult` tests;
- CLI parser tests for rclone mode validation;
- service tests for scan/report/execute/execute-soft-delete/validate paths;
- wrapper compatibility tests;
- existing rclone manager/helper tests.

Phase 5:

- architecture guard with rclone removed from allowlists;
- import deletion or wrapper deletion tests;
- existing rclone manager/helper tests.

Phase 6:

- `EmailNotificationOptions` and `EmailNotificationResult` tests;
- CLI parser tests for notify flags/defaults;
- log-analysis tests;
- report-builder tests;
- delivery tests;
- service orchestration tests;
- wrapper compatibility tests;
- existing notify email tests.

Phase 7:

- architecture guard with notify removed from allowlists;
- import deletion or wrapper deletion tests;
- existing notify email tests;
- SMTP failure exit-code tests;
- pending-mode, dedup, proxy-ban, and drift advisory section tests.

Every phase must review `.github/workflows/`, the root README, integration
READMEs, apps CLI READMEs, and the wiki. If command behavior is unchanged, the
implementation should record that the files were reviewed and no usage change
was required.

## Documentation Updates

The ADR should document:

- final CLI/service boundary;
- allowlist-based guard rollout;
- command-package shape;
- `javdb.workflow` adapter scope;
- phase order and cleanup phases;
- behavior invariants;
- follow-up ADR scope.

Each phase gets one IMP:

- `IMP-030`: guard and workflow adapters;
- `IMP-031`: qB uploader and file filter;
- `IMP-032`: PikPak bridge;
- `IMP-033`: rclone manager split and bake wrapper;
- `IMP-034`: rclone cleanup;
- `IMP-035`: notify email split and bake wrapper;
- `IMP-036`: notify cleanup.

ADR-007/IMP-007 README language that describes `apps.cli` wrappers as aliases
should be updated or superseded when this ADR lands.

## Follow-Up ADR Scope

The following work is explicitly deferred:

- split `javdb.integrations.rclone.helper` into focused modules:
  `paths`, `health`, `inventory_scan`, `dedup_analysis`, `execution`, and
  `reporting`;
- evaluate moving command flow services from `javdb.integrations.*` to
  `javdb.workflow.<domain>.<command>.service`;
- commonize workflow outputs;
- commonize notify attachments;
- commonize log conversion.

## Open Questions

None. The design choices above were reviewed and accepted in the brainstorming
session.
