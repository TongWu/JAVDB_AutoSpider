# BFR-029：同一 info hash 兼任两种候选角色时，assist 排名会丢失 rank 1

**Status**: Fixed
**Date**: 2026-08-09
**Severity**: Medium
**Affected**: `javdb/quality/assist_evaluator.py`
**Related**: [ADR-024](../ADR-024-Torrent-Quality-Evidence/ADR-024-torrent-quality-evidence.zh.md)、[IMP-ADR024-08](../ADR-024-Torrent-Quality-Evidence/IMP-ADR024-08-phase2-assist.md)、issue #272、PR #232（`NewWorks` 复合主键）

---

## Symptom

没有实际事故。该缺陷是在公开镜像仓库执行 `dev` → `main` 提升时由 review 发现的（TongWu/JAVDB_AutoSpider#153），记录为 issue #272，并已对照代码确认。

在 assist 模式下，当同一个种子既是某影片的生产下载、又是该影片的探测候补时，写入的 `TorrentQualityEvaluation` 推荐结果可能丢失 rank 1，或丢失生产行上的替换标记。

## Root Cause

`TorrentQualityRepo.list_evidence_for_movie` 是两个分支的 UNION——生产证据经 `TorrentQualityEvaluation` 关联，探测证据经 `TorrentProbeCandidate` 关联——各自硬编码自己的 `target_role`。因此，一个同时占据两种角色的 `info_hash` 会正确地返回两行：它们本就是 `TorrentQualityEvidence` 中两条不同的主键记录（`(info_hash, probe_schema_version, target_role)`）。

`rank_candidates` 有意识地处理了这种重复，按原始位置而非 hash 排名：

```python
# Rank by ORIGINAL POSITION, not info_hash: two candidates can share an
# info_hash (e.g. the same torrent surfaced as both production and a probe
# runner-up), and a hash-keyed rank map would collapse their ranks.
```

而 evaluator 随后把每个已排名的候选都持久化：

```python
for ranked in all_ranked:
    repo.upsert_evaluation(EvaluationRecord(
        info_hash=ranked["info_hash"],
        movie_href=ranked.get("movie_href", movie_href),
        scoring_version=scoring_version,
        ...
```

`TorrentQualityEvaluation` 的主键是 `(info_hash, movie_href, scoring_version)`——**不含 `target_role`**。两个候选因此塌缩到同一行，第二次 UPSERT 覆盖了第一次写入的 `shadow_rank` 与 `would_replace_current_choice`。

这与 #232 中 `NewWorks` 复合主键修复属于同一类缺陷：行标识比实际要写入的行集更窄。证据表已经吸取过这个教训——`target_role` 是**它**主键的一部分；评估表没有，而两者之间也没有任何环节负责调和粒度差异，于是一次较宽的读取被扇出到了一次较窄的写入上。

这里还藏着第二个、更隐蔽的错误答案：即便没有覆盖问题，把同一个种子排名两次也会让它「超越自己」——探测副本取得 rank 1，生产副本落到 rank 2，于是生产行被打上 `would_replace_current_choice`，宣告了一个「替换项」，而它就是同一个 info hash。

## Fix

在 evaluator 中、评分之前折叠重复角色的候选，保留 `production_download` 那一行（`would_replace_current_choice` 所讨论的「当前选择」语义正由它承载）：

```python
def _collapse_duplicate_roles(rows): ...
...
for row in _collapse_duplicate_roles(evidence_rows):
```

之所以不选 issue 中给出的另一个方案（扩宽主键）：把 `target_role` 加入 `TorrentQualityEvaluation` 主键属于 D1 schema 迁移，而该表同时也被 shadow collector 写入；并且那样会保留一对语义上其实是同一个种子的行。折叠方案一次解决两个症状——每个标识只有一行，且不再出现自我替换标记——且无需任何 schema 变更。

`rank_candidates` 有意保持不变：它对重复 hash 依然是正确的，只是 evaluator 现在不再向它传入重复项。

## Side Effects

在出现重复的场景下，名次变得更紧凑。此前被重复的种子会占用两个名次槽位，导致不相关的第三个候选排到 3；现在它排到 2。这只影响原本就会产出损坏行的那个场景。

被丢弃的探测行的特征值不做合并，而是直接丢弃。两条证据行描述的是同一个种子——相同 info hash、相同文件列表——因此生产行的特征列是等价的，不存在信号丢失。

## Follow-Up

无。该行为由 `tests/unit/test_quality_assist_evaluator.py` 中的 `test_duplicate_role_info_hash_writes_one_row_and_keeps_rank_1` 固定。
