# IMP-ADR024-07: ADR-024 Phase 1 — Documentation & Verification Closeout

**Status:** Proposed — design-reviewed 2026-05-31 (no changes needed; see Design Review note).

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Document the Phase 1 feature for operators (bilingual handbook pages + config reference + CLI reference), run the full Phase 1 verification suite, and close out the IMP (update statuses, write the ADR roadmap back-reference is handled separately in the ADR edit).

**Architecture:** Documentation only + a final cross-cutting verification gate. Per CLAUDE.md, every new CLI/config/env key needs paired `docs/handbook/en/` + `docs/handbook/zh/` updates; the EN handbook is the wiki source of truth.

**Tech Stack:** Markdown, pytest.

**Source spec:** [ADR-024](ADR-024-torrent-quality-evidence.md); CLAUDE.md "Documentation Guidelines" + "Post-Task Review".

**Related:** all of IMP-ADR024-01..06.

**Depends on:** IMP-01..06 (documents what they shipped).

**Blocks:** Nothing.

---

## Design Review note (2026-05-31)

Reviewed; **no changes required**. The verification gate (Task 3 Step 1) already
lists all seven ADR-024 test files, and the regression neighbors cover the
file-filter, operations-endpoint, and DDL-parity surfaces touched by IMP-04/05.
Two cross-references worth noting: the operator page's `category_mismatch` reason
code is now genuinely emitted (the IMP-05 `AcquisitionOutcome` join activated the
category-consistency signal), and the IMP statuses now carry `design-reviewed`
annotations that Step 4 should reconcile to `Completed` as each lands.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `docs/handbook/en/ops/torrent-quality-evidence.md` | Operator guide (what it does, config, CLI, how to read results). |
| Create | `docs/handbook/zh/ops/torrent-quality-evidence.md` | Chinese mirror (same structure). |
| Modify | `docs/handbook/en/self-hoster/configuration.md` | Document `TORRENT_QUALITY_*` keys. |
| Modify | `docs/handbook/zh/self-hoster/configuration.md` | Chinese mirror. |
| Modify | `docs/handbook/en/developer/cli-reference.md` | Document `apps.cli.qb.quality_evidence`. |
| Modify | `docs/handbook/zh/developer/cli-reference.md` | Chinese mirror. |

## Scope Boundaries

- Docs + verification only — no production code changes in this IMP.
- Do not expand README; add handbook pages and link from the ops index if one exists.
- Code blocks, env var names, CLI commands, SQL: never translated (verbatim in both langs).

---

## Task 1 — Operator handbook page (bilingual)

**Files:**
- Create: `docs/handbook/en/ops/torrent-quality-evidence.md`
- Create: `docs/handbook/zh/ops/torrent-quality-evidence.md`

- [ ] **Step 1: Write the English page**

Create `docs/handbook/en/ops/torrent-quality-evidence.md`:

````markdown
# Torrent Quality Evidence (ADR-024 Phase 1)

Phase 1 is a **shadow-only, read-only** layer. It inspects the file lists of
torrents the pipeline already downloaded, computes an explainable quality score,
and stores structured evidence in D1. It **never** changes which torrent the
production pipeline downloads.

## What it collects

- `TorrentQualityEvidence` — torrent-level objective facts keyed by
  `(info_hash, probe_schema_version, target_role)`: total size, main-video size
  and ratio, video/subtitle/non-video file counts, junk size and ratio,
  suspicious file count, and reason codes.
- `TorrentQualityEvaluation` — movie-context shadow scoring keyed by
  `(info_hash, movie_href, scoring_version)`: score, decision, inferred category,
  subtitle evidence, category consistency, and reason codes.

Both tables live on the canonical **`javdb-reports`** D1 database.

## Enabling it

It is **disabled by default**. Set the GitHub Variable
`TORRENT_QUALITY_EVIDENCE_ENABLED=true` (or `TORRENT_QUALITY_EVIDENCE_ENABLED = True`
in `config.py`). Phase 1 keeps `TORRENT_QUALITY_POLICY_MODE=shadow`; `assist` and
`enforce` are reserved for later phases and have no effect yet.

| Key | Default | Meaning |
| --- | --- | --- |
| `TORRENT_QUALITY_EVIDENCE_ENABLED` | `False` | Master on/off for collection. |
| `TORRENT_QUALITY_POLICY_MODE` | `shadow` | Phase 1 ignores non-shadow values. |
| `TORRENT_QUALITY_CATEGORIES` | `''` | Optional JSON array of qB categories to scan. |

## When it runs

The **QBFileFilter** workflow runs it right after the file-filter step (same
dispatch, same endpoint), so evidence is collected once qBittorrent has fetched
torrent metadata. You can also run it manually:

```bash
python3 -m apps.cli.qb.quality_evidence --days 2 --categories '["Daily Ingestion"]'
# run even when disabled:
python3 -m apps.cli.qb.quality_evidence --force
```

## Reading the results

- API: `GET /api/quality/evaluations` (recent shadow evaluations, optional
  `?movie_href=`) and `GET /api/quality/evidence/{info_hash}`.
- Logs: the collector prints a summary line
  (`scanned / evidence / evaluations / probe_unavailable / skipped`).

## Reason codes

Scores are never opaque. Common reason codes:

- `main_video_detected` / `main_video_missing`
- `main_video_ratio_low`
- `junk_ratio_high`
- `subtitle_file_present` / `subtitle_file_missing`
- `category_mismatch`
- `abnormal_file_count`
- `probe_unavailable` (metadata could not be read)

## Limitations (Phase 1)

- File-list metadata only — no frame/OCR/watermark inspection (deferred, ADR-024 D10).
- Production-selected torrents only — no Top-K runner-up probing yet.
- No remote `quality_probe` endpoint yet — only the `production_download` role.
````

- [ ] **Step 2: Write the Chinese mirror**

Create `docs/handbook/zh/ops/torrent-quality-evidence.md` with the same structure
and identical code blocks / key names / SQL (translate prose only):

````markdown
# 种子质量证据 (ADR-024 Phase 1)

Phase 1 是一个**仅影子、只读**的层。它检查流水线已经下载的种子的文件列表，
计算一个可解释的质量分数，并把结构化证据存入 D1。它**绝不**改变生产流水线
下载哪个种子。

## 它收集什么

- `TorrentQualityEvidence` —— 种子级客观事实，主键
  `(info_hash, probe_schema_version, target_role)`：总大小、主视频大小与占比、
  视频/字幕/非视频文件数、垃圾文件大小与占比、可疑文件数，以及 reason codes。
- `TorrentQualityEvaluation` —— 影片上下文影子评分，主键
  `(info_hash, movie_href, scoring_version)`：分数、决策、推断分类、字幕证据、
  分类一致性，以及 reason codes。

两张表都位于规范的 **`javdb-reports`** D1 数据库。

## 启用

默认**关闭**。设置 GitHub Variable
`TORRENT_QUALITY_EVIDENCE_ENABLED=true`（或在 `config.py` 中
`TORRENT_QUALITY_EVIDENCE_ENABLED = True`）。Phase 1 保持
`TORRENT_QUALITY_POLICY_MODE=shadow`；`assist` 和 `enforce` 为后续阶段保留，
目前无效。

| 键 | 默认 | 含义 |
| --- | --- | --- |
| `TORRENT_QUALITY_EVIDENCE_ENABLED` | `False` | 采集总开关。 |
| `TORRENT_QUALITY_POLICY_MODE` | `shadow` | Phase 1 忽略非 shadow 值。 |
| `TORRENT_QUALITY_CATEGORIES` | `''` | 可选：要扫描的 qB 分类 JSON 数组。 |

## 何时运行

**QBFileFilter** 工作流会在 file-filter 步骤之后立即运行它（同一次 dispatch、
同一端点），这样在 qBittorrent 已获取种子元数据后采集证据。也可手动运行：

```bash
python3 -m apps.cli.qb.quality_evidence --days 2 --categories '["Daily Ingestion"]'
# 即使关闭也强制运行：
python3 -m apps.cli.qb.quality_evidence --force
```

## 查看结果

- API：`GET /api/quality/evaluations`（最近的影子评估，可选 `?movie_href=`）
  以及 `GET /api/quality/evidence/{info_hash}`。
- 日志：采集器会打印一行摘要
  （`scanned / evidence / evaluations / probe_unavailable / skipped`）。

## Reason codes

分数绝不是不透明数字。常见 reason codes：

- `main_video_detected` / `main_video_missing`
- `main_video_ratio_low`
- `junk_ratio_high`
- `subtitle_file_present` / `subtitle_file_missing`
- `category_mismatch`
- `abnormal_file_count`
- `probe_unavailable`（元数据无法读取）

## 限制 (Phase 1)

- 仅文件列表元数据 —— 无帧/OCR/水印检测（推迟，ADR-024 D10）。
- 仅生产选中的种子 —— 暂无 Top-K 候补探测。
- 暂无远端 `quality_probe` 端点 —— 仅 `production_download` 角色。
````

- [ ] **Step 3: Verify the pair exists and matches structure**

Run:

```bash
diff <(rg -n "^#" docs/handbook/en/ops/torrent-quality-evidence.md | sed 's/.*#/#/') \
     <(rg -n "^#" docs/handbook/zh/ops/torrent-quality-evidence.md | sed 's/.*#/#/') && echo "headings aligned"
```

Expected: `headings aligned` (same heading count/order).

- [ ] **Step 4: Commit**

```bash
git add docs/handbook/en/ops/torrent-quality-evidence.md docs/handbook/zh/ops/torrent-quality-evidence.md
git commit -m "docs(ops): add torrent quality evidence guide (ADR-024)"
```

---

## Task 2 — Config + CLI reference updates (bilingual)

**Files:**
- Modify: `docs/handbook/en/self-hoster/configuration.md`
- Modify: `docs/handbook/zh/self-hoster/configuration.md`
- Modify: `docs/handbook/en/developer/cli-reference.md`
- Modify: `docs/handbook/zh/developer/cli-reference.md`

- [ ] **Step 1: Add the config keys to the EN configuration reference**

In `docs/handbook/en/self-hoster/configuration.md`, add a short subsection (place
it near the qBittorrent / file-filter config section). First confirm the anchor:

```bash
rg -n "QB_FILE_FILTER_MIN_SIZE_MB|File Filter" docs/handbook/en/self-hoster/configuration.md
```

Then add:

```markdown
### Torrent Quality Evidence (ADR-024)

| Key | Default | Meaning |
| --- | --- | --- |
| `TORRENT_QUALITY_EVIDENCE_ENABLED` | `False` | Enable shadow evidence collection. |
| `TORRENT_QUALITY_POLICY_MODE` | `shadow` | Phase 1 only honours `shadow`. |
| `TORRENT_QUALITY_CATEGORIES` | `''` | Optional JSON array of qB categories to scan. |

See [ops/torrent-quality-evidence](../ops/torrent-quality-evidence.md).
```

- [ ] **Step 2: Mirror into the ZH configuration reference**

In `docs/handbook/zh/self-hoster/configuration.md`, add the same table with prose
translated (keys/defaults verbatim) and the link pointing to
`../ops/torrent-quality-evidence.md`.

- [ ] **Step 3: Add the CLI to the EN CLI reference**

In `docs/handbook/en/developer/cli-reference.md`, add:

````markdown
### `apps.cli.qb.quality_evidence`

Collect shadow torrent-quality evidence (ADR-024 Phase 1, read-only). Disabled
unless `TORRENT_QUALITY_EVIDENCE_ENABLED=True` (or `--force`).

```bash
python3 -m apps.cli.qb.quality_evidence [--days N] [--categories '["Daily Ingestion"]'] [--force] [--use-proxy|--no-proxy]
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--days` | `2` | Look-back window for production torrents. |
| `--categories` | (none) | JSON array of qB categories to scan. |
| `--force` | off | Run even when the feature is disabled in config. |
````

- [ ] **Step 4: Mirror into the ZH CLI reference**

In `docs/handbook/zh/developer/cli-reference.md`, add the same section with prose
translated and the command/flags verbatim.

- [ ] **Step 5: Verify cross-language parity**

Run:

```bash
rg -n "TORRENT_QUALITY_EVIDENCE_ENABLED" docs/handbook/en docs/handbook/zh
rg -n "apps.cli.qb.quality_evidence" docs/handbook/en docs/handbook/zh
```

Expected: matches in both `en` and `zh` trees.

- [ ] **Step 6: Commit**

```bash
git add docs/handbook/en/self-hoster/configuration.md docs/handbook/zh/self-hoster/configuration.md \
        docs/handbook/en/developer/cli-reference.md docs/handbook/zh/developer/cli-reference.md
git commit -m "docs: document torrent quality config + CLI (ADR-024)"
```

---

## Task 3 — Phase 1 verification gate

- [ ] **Step 1: Run the full ADR-024 unit suite**

Run:

```bash
pytest \
  tests/unit/test_torrent_quality_repo.py \
  tests/unit/test_quality_features.py \
  tests/unit/test_quality_scoring.py \
  tests/unit/test_qb_readonly.py \
  tests/unit/test_quality_collector.py \
  tests/unit/test_quality_evidence_cli.py \
  tests/unit/test_quality_api.py \
  -v
```

Expected: all PASS.

- [ ] **Step 2: Run the regression-sensitive neighbors**

Run:

```bash
pytest \
  tests/unit/test_qb_file_filter.py \
  tests/unit/test_operations_endpoints.py \
  tests/unit/test_rollback_full_fidelity.py \
  -v
```

Expected: all PASS (file filter unchanged; schema-parity holds).

- [ ] **Step 3: Whitespace / diff hygiene**

Run:

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 4: Mark Phase 1 IMP statuses**

Update the `**Status:**` header in IMP-ADR024-01..07 from `Proposed` to
`Completed — <date>` as each lands. (The ADR roadmap back-reference and `.zh.md`
mirror are handled in the ADR edit, not here.)

- [ ] **Step 5: Commit**

```bash
git add docs/design/ADR-024-Torrent-Quality-Evidence/
git commit -m "docs(adr-024): mark Phase 1 IMPs completed"
```

---

## Definition of Done

| # | Gate | Check |
|---|------|-------|
| 1 | Bilingual ops page | both `en`/`zh` `torrent-quality-evidence.md` exist, headings aligned |
| 2 | Config documented | `TORRENT_QUALITY_*` in both config references |
| 3 | CLI documented | `apps.cli.qb.quality_evidence` in both CLI references |
| 4 | Full suite green | Task 3 Steps 1-2 all PASS |
| 5 | Clean diff | `git diff --check` → no output |
