# BFR-014: Sentinel video_code 漂移误报阻断每日提交

**Status**: Fixed
**Date**: 2026-05-31
**Severity**: High
**Affected**: `javdb/rust_core/src/scraper/common.rs`, `javdb/parsing/common.py`, `javdb/spider/parse_contract.py`, `javdb/ops/sentinel/`, `javdb/migrations/d1/2026_05_27_add_ops_incidents.sql`
**Related**: [ADR-035](../_archive/ADR-035-Site-Contract-Sentinel/ADR-035-site-contract-drift-sentinel.zh.md), [ADR-026](../_archive/ADR-026-AI-Operations-Diagnosis/ADR-026-ai-operations-diagnosis.zh.md)

---

## 症状

每日抓取的 `mark-sessions-as-committed` 步骤以退出码 1 失败
（[run 26712478741](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/runs/26712478741)）：

```text
✗ __main__  Site-contract drift gate: critical drift for session
            20260531T122613.805503Z-8382-0000 (1 finding(s)); refusing commit
            (FailureReason=site_drift).
Commit done: committed=0 already_committed_or_missing=0 failed=1
Error: Process completed with exit code 1.
```

其上方有一条被吞掉的警告：

```text
⚠ javdb.ops.se  evaluate_session: incident persist failed
javdb.storage.d1_client.D1PermanentError: D1 API returned HTTP 400:
  [{'code': 7500, 'message': 'no such table: OpsIncidents: SQLITE_ERROR'}]
```

随后该 run 的 session 被 on-failure cleanup 拆除，当天数据未提交。此时 ADR-035
sentinel 是**首次**真实触发（`ParseRunFieldFill` 里只有这一个 session，尚无
committed 基线）。

## 根因

两个相互独立的缺陷叠加：

**1.（主因——提交阻断者）Rust 列表卡片 `extract_video_code` 偏离解析契约、丢弃了合法码。**
sentinel（[javdb/ops/sentinel/field_health.py](../../../javdb/ops/sentinel/field_health.py)）
观测的是**过滤前的原始列表卡片**（`page_result.movies`），并用绝对阈值 0.99 对
`index.video_code` 评分
（[parse_contract.py](../../../javdb/spider/parse_contract.py)）。生产用 Rust 核心解析，
其 `extract_video_code` 仅以 `if !video_code.contains('-') { return "" }` 校验，导致：

- 丢弃无连字符的厂牌码（`n0656`）和下划线日期式无码番号（`062216_001`）——两者都是
  合法 JavDB 码；以及
- 当卡片无 `<strong>` 时，返回整串 `"CODE Title"`。

正常每日列表里约 4% 的**真实**电影卡（无码内容）会产出空的列表码。详情页解析能恢复码，
所以 `MovieHistory` 是干净的——但 sentinel 测的是**原始列表**填充率，跌到 0.96 < 0.99，
触发 critical 闸门。因此这是**误报**：过严且偏离契约的提取器，叠加在"流水线根本不会提交的
卡片"上用未经校准的绝对阈值衡量。（Python fallback `_is_plausible_video_code` 也是错的，
但方式不同——它的单连字符正则会拒绝多连字符的 FC2 码。）

**2.（次因——诊断丢失）`OpsIncidents` D1 表在生产中从未存在。** migration
`2026_05_27_add_ops_incidents.sql`（ADR-026）已编写但从未应用到 `javdb-reports` D1
库（D1 增量靠手工应用；更晚的 migration 落了，这个被漏掉）。`evaluate_session` 捕获了由此
产生的错误并降级为 warning，于是这次漂移事件——也就是本次闸门事件的诊断记录——被静默丢弃。

## 修复

- **解析器有效性判定，两端对齐。** 把 Rust `extract_video_code`
  （[common.rs](../../../javdb/rust_core/src/scraper/common.rs)）与 Python
  `_is_plausible_video_code`（[common.py](../../../javdb/parsing/common.py)）的
  "仅含连字符"判定替换为共同规则：一个 `[A-Za-z0-9_-]` 紧凑 token，含数字，且（有字母
  或含 `-`/`_` 分隔符）。无 `<strong>` 分支只保留首个码 token、丢弃标题。接受 `ABC-123`、
  `FC2-PPV-1234567`、`062216-179`、`062216_001`、`n0656`；拒绝标题文本。新增 Rust +
  Python 单测；`javdb/rust_core/**` 变更时 CI 自动重建 Rust wheel。
- **补上缺失的 migration** `2026_05_27_add_ops_incidents.sql` 到 reports D1，使事件今后
  能正常落库。
- **回填**了 216 条历史 `ReportMovies.VideoCode` 的 NULL（取自权威的 `MovieHistory` 码）。

## 副作用

- 抬高列表 `video_code` 填充率（无码番号不再被丢）是预期效果；正常 run 现在应能过闸门。
  有码的连字符番号行为不变（无回归——已用编译后的 Rust wheel 端到端验证）。
- 无 `<strong>` 分支现在会返回码，而此前返回空/垃圾；真实 JavDB 卡片总是带 `<strong>`，
  所以实际差异仅限于畸形卡片。

## 后续工作

- [ ] 观察下一次每日 run 的 `index.video_code` 填充率；若仍因真正无码的卡片低于 0.99，
      应重新校准阈值，而不是盲目放低闸门。
- [ ] 接通详情页观测，使 `detail.video_code`（critical 0.99）成为**生效**的守门——目前只
      观测 `index`，因此现在降低 index 阈值没有任何兜底。
- [ ] 考虑对**选中后**的条目衡量填充率，或为 critical 字段引入相对基线阈值，使新部署的闸门
      不会在尚无基线的首次触发时误报。
- [ ] （独立）清理回滚 session 残留的孤儿子表行，并修复回滚级联缺口（已登记为单独任务）。
