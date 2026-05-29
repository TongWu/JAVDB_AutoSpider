# ADR-028 Phase 1 — Web-Cluster Renumber & WS-A Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the ADR-028 umbrella's Phase 1 — renumber the web-platform ADR cluster (018/019/020 → 029/030/031) with the umbrella (028) leading, fix every cross-reference, and merge the WS-A capability-honesty scope into the renumbered Feature Parity ADR (030).

**Architecture:** This is a **docs-only, mechanical bookkeeping change**. No application code, tests, or workflows are touched. Folder/file renames use `git mv` (preserves history); reference fixes use a deterministic 6-rule token replacement (`perl -pi`); the WS-A merge is hand-written bilingual prose inserted at a fixed anchor. Verification is link-resolution-based, not raw-token-based, because ADR-028's own Renumbering Plan table legitimately preserves the old numbers as historical record.

**Tech Stack:** Markdown docs under `docs/design/`, `git mv`, `perl -pi -e`, `grep`, `bash`.

**Related:** [ADR-028](ADR-028-web-platform-completeness-roadmap.md)

---

## Background context the engineer needs

The repo stores design records as **one folder per ADR** under `docs/design/`, each ADR bilingual (`.md` + paired `.zh.md`), with its implementation plan(s) co-located as English-only `IMP-ADRNNN-PP-*.md`. ADRs and IMPs cross-link by **relative path** to sibling folders, e.g. `[ADR-018](../ADR-018-Web-Security-Hardening/ADR-018-web-security-hardening.md)`.

ADR-028 (already committed) decided to renumber the three web ADRs so the umbrella leads. There is no free integer below 019 and a `+1` shift collides with the archived ADR-021, so the cluster moves to a fresh tail block:

| Document | Old | New | Old folder | New folder |
| --- | --- | --- | --- | --- |
| Web Security Hardening | ADR-018 | ADR-029 | `ADR-018-Web-Security-Hardening/` | `ADR-029-Web-Security-Hardening/` |
| Web Feature Parity | ADR-019 | ADR-030 | `ADR-019-Web-Feature-Parity/` | `ADR-030-Web-Feature-Parity/` |
| Web Operational Polish | ADR-020 | ADR-031 | `ADR-020-Web-Operational-Polish/` | `ADR-031-Web-Operational-Polish/` |

**Verified blast radius** (references to 018/019/020 across the repo, excluding `.claude/worktrees/*`):

- Inside the three cluster folders (self + sibling cross-refs): `ADR-018/019/020-*.md`, their `.zh.md`, and `IMP-ADR018/019/020-01-*.md`. Notable interdependencies: `IMP-ADR019-01` references `ADR-018`/`IMP-ADR018-01`; `IMP-ADR020-01` references `ADR-018`/`ADR-019`/`IMP-ADR018-01`/`IMP-ADR019-01`.
- External referrers: `ADR-022` (`.md` + `.zh.md`, line 224 → ADR-019) and archived `ADR-021` (`.md` + `.zh.md`, line 8 → ADR-018).
- `CONTEXT.md`, `CLAUDE.md`, `README.md`, and the `JAVDB_AutoSpider_Web` repo contain **none**.

**The 6 token-replacement rules** (sources {018,019,020} and targets {029,030,031} are disjoint, so order is safe; IMP tokens are listed first as a belt-and-braces measure):

```
IMP-ADR018 → IMP-ADR029
IMP-ADR019 → IMP-ADR030
IMP-ADR020 → IMP-ADR031
ADR-018    → ADR-029
ADR-019    → ADR-030
ADR-020    → ADR-031
```

`ADR-018` (hyphenated) never matches inside `IMP-ADR018-01` (which contains the non-hyphenated `ADR018`), so the rules do not interfere. One rule transforms link display text, folder names in paths, and filenames in paths uniformly.

---

## Task 1: Rename the three cluster folders and their files

**Files:**
- Rename folder: `docs/design/ADR-018-Web-Security-Hardening/` → `docs/design/ADR-029-Web-Security-Hardening/`
- Rename folder: `docs/design/ADR-019-Web-Feature-Parity/` → `docs/design/ADR-030-Web-Feature-Parity/`
- Rename folder: `docs/design/ADR-020-Web-Operational-Polish/` → `docs/design/ADR-031-Web-Operational-Polish/`
- Rename the 3 files inside each (ADR `.md`, ADR `.zh.md`, IMP `.md`)

- [ ] **Step 1: Confirm starting state**

Run:
```bash
ls docs/design/ADR-018-Web-Security-Hardening docs/design/ADR-019-Web-Feature-Parity docs/design/ADR-020-Web-Operational-Polish
```
Expected: each folder lists exactly 3 files — `ADR-0NN-<topic>.md`, `ADR-0NN-<topic>.zh.md`, `IMP-ADR0NN-01-<topic>.md`.

- [ ] **Step 2: `git mv` the folders, then the files inside (018 → 029)**

```bash
git mv docs/design/ADR-018-Web-Security-Hardening docs/design/ADR-029-Web-Security-Hardening
git mv docs/design/ADR-029-Web-Security-Hardening/ADR-018-web-security-hardening.md     docs/design/ADR-029-Web-Security-Hardening/ADR-029-web-security-hardening.md
git mv docs/design/ADR-029-Web-Security-Hardening/ADR-018-web-security-hardening.zh.md  docs/design/ADR-029-Web-Security-Hardening/ADR-029-web-security-hardening.zh.md
git mv docs/design/ADR-029-Web-Security-Hardening/IMP-ADR018-01-security-hardening.md   docs/design/ADR-029-Web-Security-Hardening/IMP-ADR029-01-security-hardening.md
```

- [ ] **Step 3: `git mv` the folders, then the files inside (019 → 030)**

```bash
git mv docs/design/ADR-019-Web-Feature-Parity docs/design/ADR-030-Web-Feature-Parity
git mv docs/design/ADR-030-Web-Feature-Parity/ADR-019-web-feature-parity.md     docs/design/ADR-030-Web-Feature-Parity/ADR-030-web-feature-parity.md
git mv docs/design/ADR-030-Web-Feature-Parity/ADR-019-web-feature-parity.zh.md  docs/design/ADR-030-Web-Feature-Parity/ADR-030-web-feature-parity.zh.md
git mv docs/design/ADR-030-Web-Feature-Parity/IMP-ADR019-01-feature-parity.md   docs/design/ADR-030-Web-Feature-Parity/IMP-ADR030-01-feature-parity.md
```

- [ ] **Step 4: `git mv` the folders, then the files inside (020 → 031)**

```bash
git mv docs/design/ADR-020-Web-Operational-Polish docs/design/ADR-031-Web-Operational-Polish
git mv docs/design/ADR-031-Web-Operational-Polish/ADR-020-web-operational-polish.md     docs/design/ADR-031-Web-Operational-Polish/ADR-031-web-operational-polish.md
git mv docs/design/ADR-031-Web-Operational-Polish/ADR-020-web-operational-polish.zh.md  docs/design/ADR-031-Web-Operational-Polish/ADR-031-web-operational-polish.zh.md
git mv docs/design/ADR-031-Web-Operational-Polish/IMP-ADR020-01-operational-polish.md   docs/design/ADR-031-Web-Operational-Polish/IMP-ADR031-01-operational-polish.md
```

- [ ] **Step 5: Verify the new layout and that old folders are gone**

Run:
```bash
ls -d docs/design/ADR-0{18,19,20,29,30,31}-* 2>&1; echo "---"; ls docs/design/ADR-029-Web-Security-Hardening docs/design/ADR-030-Web-Feature-Parity docs/design/ADR-031-Web-Operational-Polish
```
Expected: the `ADR-018/019/020-*` globs report "No such file or directory"; the three new folders each list their 3 renamed files (`ADR-029-*.md/.zh.md`, `IMP-ADR029-01-*.md`, and likewise for 030/031).

- [ ] **Step 6: Commit the renames**

```bash
git add -A docs/design/
git commit -m "docs(adr): rename web cluster 018/019/020 -> 029/030/031 (ADR-028 WS renumber)

Folder + file renames only; reference fixes follow in the next commit.
Per ADR-028 IMP-ADR028-01.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Fix all cross-references via deterministic token replacement

**Files (apply the 6 rules to each):**
- Modify: all 3 files in `docs/design/ADR-029-Web-Security-Hardening/`
- Modify: all 3 files in `docs/design/ADR-030-Web-Feature-Parity/`
- Modify: all 3 files in `docs/design/ADR-031-Web-Operational-Polish/`
- Modify: `docs/design/ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md` and `.zh.md`
- Modify: `docs/design/_archive/ADR-021-API-Resource-Bounds/ADR-021-api-resource-bounds.md` and `.zh.md`

> Do **not** run the replacement on `docs/design/ADR-028-*` — its Renumbering Plan table intentionally records the old numbers. ADR-028's own links already use 029/030/031 and are validated in Task 4.

- [ ] **Step 1: Snapshot the pre-change link references (for diffing)**

Run:
```bash
grep -rnoE '\]\((\.\./)?ADR-0(18|19|20)[^)]*\)|\]\([^)]*IMP-ADR0(18|19|20)[^)]*\)' \
  docs/design/ADR-029-Web-Security-Hardening docs/design/ADR-030-Web-Feature-Parity \
  docs/design/ADR-031-Web-Operational-Polish docs/design/ADR-022-User-Preference-Foundation \
  docs/design/_archive/ADR-021-API-Resource-Bounds
```
Expected: a non-empty list of markdown links pointing at old paths (e.g. `](../ADR-018-Web-Security-Hardening/ADR-018-web-security-hardening.md)`). These are what Step 2 rewrites.

- [ ] **Step 2: Apply the 6-rule token replacement in place**

```bash
perl -pi -e '
  s/IMP-ADR018/IMP-ADR029/g;
  s/IMP-ADR019/IMP-ADR030/g;
  s/IMP-ADR020/IMP-ADR031/g;
  s/ADR-018/ADR-029/g;
  s/ADR-019/ADR-030/g;
  s/ADR-020/ADR-031/g;
' \
  docs/design/ADR-029-Web-Security-Hardening/ADR-029-web-security-hardening.md \
  docs/design/ADR-029-Web-Security-Hardening/ADR-029-web-security-hardening.zh.md \
  docs/design/ADR-029-Web-Security-Hardening/IMP-ADR029-01-security-hardening.md \
  docs/design/ADR-030-Web-Feature-Parity/ADR-030-web-feature-parity.md \
  docs/design/ADR-030-Web-Feature-Parity/ADR-030-web-feature-parity.zh.md \
  docs/design/ADR-030-Web-Feature-Parity/IMP-ADR030-01-feature-parity.md \
  docs/design/ADR-031-Web-Operational-Polish/ADR-031-web-operational-polish.md \
  docs/design/ADR-031-Web-Operational-Polish/ADR-031-web-operational-polish.zh.md \
  docs/design/ADR-031-Web-Operational-Polish/IMP-ADR031-01-operational-polish.md \
  docs/design/ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md \
  docs/design/ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.zh.md \
  docs/design/_archive/ADR-021-API-Resource-Bounds/ADR-021-api-resource-bounds.md \
  docs/design/_archive/ADR-021-API-Resource-Bounds/ADR-021-api-resource-bounds.zh.md
```

- [ ] **Step 3: Verify no old-number link patterns remain in the touched files**

Run:
```bash
grep -rnoE '\]\((\.\./)?ADR-0(18|19|20)[^)]*\)|\]\([^)]*IMP-ADR0(18|19|20)[^)]*\)' \
  docs/design/ADR-029-Web-Security-Hardening docs/design/ADR-030-Web-Feature-Parity \
  docs/design/ADR-031-Web-Operational-Polish docs/design/ADR-022-User-Preference-Foundation \
  docs/design/_archive/ADR-021-API-Resource-Bounds; echo "exit=$?"
```
Expected: no matches, `exit=1` (grep found nothing).

- [ ] **Step 4: Verify the H1 titles renumbered correctly**

Run:
```bash
head -1 docs/design/ADR-029-Web-Security-Hardening/ADR-029-web-security-hardening.md \
        docs/design/ADR-030-Web-Feature-Parity/ADR-030-web-feature-parity.md \
        docs/design/ADR-031-Web-Operational-Polish/ADR-031-web-operational-polish.md
```
Expected: the H1 lines now read `# ADR-029: ...`, `# ADR-030: ...`, `# ADR-031: ...` respectively (no lingering `# ADR-018/019/020`).

- [ ] **Step 5: Verify every relative `.md` link in the renamed folders resolves**

Run:
```bash
for f in docs/design/ADR-029-Web-Security-Hardening/*.md \
         docs/design/ADR-030-Web-Feature-Parity/*.md \
         docs/design/ADR-031-Web-Operational-Polish/*.md; do
  dir=$(dirname "$f")
  grep -oE '\]\([^)]+\.md[^)]*\)' "$f" \
    | sed -E 's/^\]\(//; s/\)$//; s/#.*$//' \
    | while read -r link; do
        case "$link" in http*|"") continue;; esac
        [ -f "$dir/$link" ] || echo "DANGLING: $f -> $link"
      done
done
echo "link check done"
```
Expected: prints `link check done` and **no** `DANGLING:` lines (every relative `.md` link in the three renamed folders resolves to an existing file).

- [ ] **Step 6: Commit the reference fixes**

```bash
git add -A docs/design/
git commit -m "docs(adr): fix cross-references for web cluster renumber 018/019/020 -> 029/030/031

Updates self/sibling links inside the renamed folders plus external
referrers ADR-022 and archived ADR-021. Per ADR-028 IMP-ADR028-01.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Merge WS-A capability-honesty scope into ADR-030 (Feature Parity)

**Files:**
- Modify: `docs/design/ADR-030-Web-Feature-Parity/ADR-030-web-feature-parity.md`
- Modify: `docs/design/ADR-030-Web-Feature-Parity/ADR-030-web-feature-parity.zh.md`

After Task 2, ADR-030's section order under `## Decision` is: Config Key Triage → Must-Add Keys → Config Key Renames → Stats Trend → Change Password Endpoint → `findUser()` Async, followed by `## Out of Scope`. The new subsection is inserted as the **last `### ` subsection under `## Decision`**, immediately before the `## Out of Scope` heading.

- [ ] **Step 1: Extend the H1 subtitle (English)**

In `ADR-030-web-feature-parity.md`, replace the H1 line:

old:
```markdown
# ADR-030: Web Backend Feature Parity — Config, Stats, and Password Management
```
new:
```markdown
# ADR-030: Web Backend Feature Parity — Config, Stats, Password Management, and Capability Honesty
```

- [ ] **Step 2: Insert the WS-A Decision subsection (English)**

In `ADR-030-web-feature-parity.md`, insert the following block on the line **immediately before** `## Out of Scope`:

```markdown
### Capability Honesty: GitHub Actions Coverage and `INGESTION_MODE`

Merged from [ADR-028](../ADR-028-Web-Platform-Completeness-Roadmap/ADR-028-web-platform-completeness-roadmap.md) workstream WS-A (P0). Two parity gaps undermine *capability honesty* — the platform advertising work it cannot track or execute.

**Cloudflare — untracked GitHub Actions workflows.** `Migration.yml`, `WeeklyDedup.yml`, and `TestIngestion.yml` have no typed dispatch endpoint. They are reachable only via the generic `POST /api/gh-actions/runs` (requires `GH_ACTIONS_TIER=admin` plus a known workflow filename) and are **not** written to `job_runs`, so they never appear in the Tasks list or stats. Add typed dispatch endpoints plus `job_runs` tracking for these three workflows, mirroring the existing pattern for `DailyIngestion` / `QBFileFilter` / `RcloneManager` / `StaleSessionCleanup`.

**Python — `INGESTION_MODE` advertised but unimplemented.** `GET /api/capabilities` reports an `ingestion_mode` of `github` / `dual`, but `apps/api/services/task_service.py` has no GitHub-dispatch branch — `trigger_daily_task` / `trigger_adhoc_task` always run a local subprocess regardless of `INGESTION_MODE`. Resolve by either (a) implementing GitHub dispatch in `task_service` for the `github` / `dual` modes, or (b) restricting `/api/capabilities` to advertise only modes the execution layer honors. **Default to (b)** unless GitHub dispatch from the Python backend is explicitly wanted, since the Cloudflare backend already owns the dispatch topology.

```

- [ ] **Step 3: Add a Positive-consequence bullet (English)**

In `ADR-030-web-feature-parity.md`, under `### Positive` (inside `## Consequences`), append this bullet as the last item in that list:

```markdown
- Capability-honesty gaps (untracked GH Actions workflows; an advertised-but-unimplemented `INGESTION_MODE`) are closed, so the console no longer signals work it cannot track or perform.
```

- [ ] **Step 4: Mirror all three edits into the Chinese file**

In `ADR-030-web-feature-parity.zh.md`:

H1 (replace):
```markdown
# ADR-030：Web 后端功能对等 — 配置、统计、密码管理与能力诚实性
```
*(adjust to match the existing `.zh.md` H1 wording; keep the leading `# ADR-030：` and append the “与能力诚实性” clause)*

Insert immediately before the Chinese `## Out of Scope` equivalent heading (e.g. `## 范围之外`):
```markdown
### 能力诚实性：GitHub Actions 覆盖与 `INGESTION_MODE`

并入自 [ADR-028](../ADR-028-Web-Platform-Completeness-Roadmap/ADR-028-web-platform-completeness-roadmap.md) 的工作流 WS-A（P0）。两个对等缺口损害了*能力诚实性*——平台宣称了它无法追踪或执行的工作。

**Cloudflare — 未追踪的 GitHub Actions workflow。** `Migration.yml`、`WeeklyDedup.yml`、`TestIngestion.yml` 没有类型化调度端点。它们只能通过通用的 `POST /api/gh-actions/runs`（要求 `GH_ACTIONS_TIER=admin` 且已知 workflow 文件名）触达，且**不会**写入 `job_runs`，因此永不出现在 Tasks 列表或统计里。为这三个 workflow 补类型化调度端点 + `job_runs` 追踪，沿用 `DailyIngestion` / `QBFileFilter` / `RcloneManager` / `StaleSessionCleanup` 的现有模式。

**Python — `INGESTION_MODE` 上报却未实现。** `GET /api/capabilities` 上报 `github` / `dual` 的 `ingestion_mode`，但 `apps/api/services/task_service.py` 没有 GitHub 调度分支——`trigger_daily_task` / `trigger_adhoc_task` 无视 `INGESTION_MODE` 永远跑本地子进程。解决方式二选一：(a) 在 `task_service` 里为 `github` / `dual` 模式实现 GitHub 调度；(b) 让 `/api/capabilities` 只上报执行层兑现的模式。**默认选 (b)**，除非明确希望从 Python 后端发起 GitHub 调度，因为 Cloudflare 后端已经持有调度拓扑。

```

Chinese Positive bullet (append under the `### 正面` list in `## 影响`):
```markdown
- 能力诚实性缺口（未追踪的 GH Actions workflow；上报却未实现的 `INGESTION_MODE`）被关闭，console 不再示意它无法追踪或执行的工作。
```

- [ ] **Step 5: Verify the subsection landed in both languages and parity holds**

Run:
```bash
grep -n "Capability Honesty" docs/design/ADR-030-Web-Feature-Parity/ADR-030-web-feature-parity.md
grep -n "能力诚实性" docs/design/ADR-030-Web-Feature-Parity/ADR-030-web-feature-parity.zh.md
grep -n "^## Out of Scope\|^## 范围之外\|^## " docs/design/ADR-030-Web-Feature-Parity/ADR-030-web-feature-parity.md | tail -5
```
Expected: the new heading exists in both files and sits **before** the Out-of-Scope / Consequences headings (i.e. it is the last `### ` under `## Decision`).

- [ ] **Step 6: Commit the WS-A merge**

```bash
git add docs/design/ADR-030-Web-Feature-Parity/
git commit -m "docs(adr): merge WS-A capability-honesty scope into ADR-030 (feature parity)

Adds Cloudflare untracked-workflow gap (Migration/WeeklyDedup/TestIngestion
need typed endpoints + job_runs) and Python INGESTION_MODE honesty gap.
Per ADR-028 IMP-ADR028-01.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Mark the renumber complete in ADR-028

**Files:**
- Modify: `docs/design/ADR-028-Web-Platform-Completeness-Roadmap/ADR-028-web-platform-completeness-roadmap.md`
- Modify: `docs/design/ADR-028-Web-Platform-Completeness-Roadmap/ADR-028-web-platform-completeness-roadmap.zh.md`

ADR-028 opens with a "Renumber pending" admonition stating the children still live at old paths "until `IMP-ADR028-01` executes." That is now false. Update it to a completed note and add a Status Log entry. Leave the Renumbering Plan table unchanged (it is the historical old→new record).

- [ ] **Step 1: Replace the admonition (English)**

In `ADR-028-web-platform-completeness-roadmap.md`, replace the blockquote that begins `> **Renumber pending.**` (the entire 4-line `>` block under the metadata table) with:

```markdown
> **Renumber complete (2026-05-29).** The web cluster was renumbered by
> `IMP-ADR028-01`: ADR-029 (`ADR-029-Web-Security-Hardening/`),
> ADR-030 (`ADR-030-Web-Feature-Parity/`), ADR-031 (`ADR-031-Web-Operational-Polish/`).
> The [Renumbering Plan](#renumbering-plan) below retains the old→new mapping as the historical record.
```

- [ ] **Step 2: Add a Status Log entry (English)**

In `ADR-028-web-platform-completeness-roadmap.md`, append to the `## Status Log` list:

```markdown
- 2026-05-29: `IMP-ADR028-01` executed — web cluster renumbered (018/019/020 → 029/030/031); WS-A capability-honesty scope merged into ADR-030.
```

- [ ] **Step 3: Mirror both edits into the Chinese file**

In `ADR-028-web-platform-completeness-roadmap.zh.md`, replace the `> **重编号待执行。**` blockquote with:

```markdown
> **重编号已完成（2026-05-29）。** web 集群已由 `IMP-ADR028-01` 重编号：
> ADR-029（`ADR-029-Web-Security-Hardening/`）、ADR-030（`ADR-030-Web-Feature-Parity/`）、
> ADR-031（`ADR-031-Web-Operational-Polish/`）。下方[重编号计划](#重编号计划)保留旧→新映射作为历史记录。
```

And append to `## 状态日志`:
```markdown
- 2026-05-29：`IMP-ADR028-01` 执行完毕——web 集群已重编号（018/019/020 → 029/030/031）；WS-A 能力诚实性范围已并入 ADR-030。
```

- [ ] **Step 4: Verify ADR-028's forward links all resolve**

Run:
```bash
err=0
for link in \
  ../ADR-029-Web-Security-Hardening/ADR-029-web-security-hardening.md \
  ../ADR-030-Web-Feature-Parity/ADR-030-web-feature-parity.md \
  ../ADR-031-Web-Operational-Polish/ADR-031-web-operational-polish.md ; do
  t="docs/design/ADR-028-Web-Platform-Completeness-Roadmap/$link"
  [ -f "$t" ] || { echo "DANGLING: $link"; err=1; }
done
echo "ADR-028 child links err=$err"
```
Expected: `ADR-028 child links err=0`, no `DANGLING:` lines.

- [ ] **Step 5: Commit the ADR-028 update**

```bash
git add docs/design/ADR-028-Web-Platform-Completeness-Roadmap/ADR-028-web-platform-completeness-roadmap.md \
        docs/design/ADR-028-Web-Platform-Completeness-Roadmap/ADR-028-web-platform-completeness-roadmap.zh.md
git commit -m "docs(adr): mark ADR-028 web-cluster renumber complete

Per ADR-028 IMP-ADR028-01.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Final repo-wide verification gate

**Files:** none modified — verification only.

- [ ] **Step 1: No live links to retired numbers anywhere under `docs/design/`**

Run:
```bash
grep -rnoE '\]\((\.\./)?ADR-0(18|19|20)[^)]*\)|\]\([^)]*IMP-ADR0(18|19|20)[^)]*\)' \
  docs/design --include='*.md'; echo "exit=$?"
```
Expected: no matches, `exit=1`. *(Plain-text/back-ticked mentions of `ADR-018-...` inside ADR-028's Renumbering Plan table are NOT links and are correctly ignored by this pattern.)*

- [ ] **Step 2: No retired folder still exists**

Run:
```bash
ls -d docs/design/ADR-018-* docs/design/ADR-019-* docs/design/ADR-020-* 2>&1
```
Expected: all three report "No such file or directory".

- [ ] **Step 3: Bilingual pairing intact for the renamed ADRs**

Run:
```bash
for n in 029 030 031; do
  d=$(ls -d docs/design/ADR-$n-* 2>/dev/null)
  md=$(ls "$d"/ADR-$n-*.md 2>/dev/null | grep -v '\.zh\.md' | wc -l | tr -d ' ')
  zh=$(ls "$d"/ADR-$n-*.zh.md 2>/dev/null | wc -l | tr -d ' ')
  echo "ADR-$n: md=$md zh=$zh"
done
```
Expected: each line reads `ADR-0NN: md=1 zh=1` (every ADR `.md` has its `.zh.md`).

- [ ] **Step 4: No stray ADR cross-references break elsewhere in the repo**

Run:
```bash
grep -rnoE '\]\([^)]*ADR-0(18|19|20)[^)]*\)' \
  --include='*.md' --include='*.ts' --include='*.vue' --include='*.py' . \
  | grep -v node_modules | grep -v '.claude/worktrees/'; echo "exit=$?"
```
Expected: no matches, `exit=1`. (Confirms `CONTEXT.md`, `CLAUDE.md`, `README.md`, and code carry no broken links — consistent with the pre-verified blast radius.)

- [ ] **Step 5: Confirm clean working tree**

Run:
```bash
git status --short
```
Expected: empty (all changes committed across Tasks 1–4).

---

## Self-Review notes

- **Spec coverage:** Task 1 (folder/file renames) + Task 2 (reference fixes) cover ADR-028 scope items 1 & 2; Task 3 covers item 3 (WS-A merge); Task 4 covers item 4 (ADR-028 forward-link integrity) plus marks the renumber done; Task 5 is the verification gate.
- **No `.zh.md` for this IMP** — English only, per project convention.
- **No application code touched** — docs-only; no tests/workflows in scope. The *features* described by WS-A (typed endpoints, INGESTION_MODE resolution) are implemented later under ADR-030's own IMP, not here.
- **Old numbers 018/019/020 retired**, never reused; ADR-028's Renumbering Plan table is the canonical record of the move.
