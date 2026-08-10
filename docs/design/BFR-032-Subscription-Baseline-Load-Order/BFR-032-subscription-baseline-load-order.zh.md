# BFR-032：订阅监控丢弃两位订阅演员共演的新作

**Status**: Fixed
**Date**: 2026-08-09
**Severity**: Medium
**Affected**: `javdb/pipeline/subscription_monitor.py`
**Related**: [ADR-054](../_archive/ADR-054-User-Intent-Discovery-Layer/ADR-054-user-intent-discovery-layer.zh.md)、[IMP-ADR054-02](../_archive/ADR-054-User-Intent-Discovery-Layer/IMP-ADR054-02-subscriptions.md)、issue #274、PR #232（`NewWorks` 复合主键）

---

## Symptom

没有实际事故。该缺陷是在公开镜像仓库执行 `dev` → `main` 提升时由 review 发现的（TongWu/JAVDB_AutoSpider#153），记录为 issue #274，并已对照代码确认——回归测试可精确复现，日志为 `Actor /actors/B: 0 new work(s) added to feed (scraped 1)`。

当两位已订阅的演员共演一部新作时，只有第一位演员会获得 `NewWorks` feed 行，第二位演员永远看不到这部新作。

## Root Cause

每位演员的 baseline 是在其所属循环迭代的开头加载的：

```python
for raw_href in actor_hrefs:
    actor_href = normalize_javdb_href_path(raw_href) or raw_href
    seen_before = load_seen_video_codes(actor_href, db_path=db_path)        # <-- 此处
    session_id = scrape_actor(actor_href, use_proxy=use_proxy)
    commit_spider_session(session_id)                                       # <-- 已提交
    scraped = load_actor_works_from_history(actor_href, db_path=db_path)
    total_added += process_actor(..., seen_video_codes=seen_before, ...)
```

`commit_spider_session` 会把本次抓取的 pending 行提升进 `MovieHistory`，而 `load_seen_video_codes` 读的正是同一张表——匹配 `ActorLink` **或** `SupportingActors` 中的任一条目。于是第 *n* 次迭代改写了第 *n+1* 次迭代所采样的那份状态。

对于「A 主演、B 助演」的作品：A 的抓取提交它时，B 已在 `SupportingActors` 中。随后加载的 B 的 baseline 便已包含该作品。`process_actor` 判定 `work.video_code in seen_video_codes` 并 `continue`，因此 B 的 `(actor_href, video_code)` 行从未被写入。

更深层的缺陷在于 baseline 的**采样时机**与它**应有的语义**之间的错位。「已看过」应当是**本次运行之前**的世界属性，但代码却在运行途中按演员逐个采样，把它变成了「该演员抓取之前」——只有当各演员的作品集互不相交时两者才等价。共演使它们相交，而「相交」恰恰是 #232 将 `NewWorks` 主键扩宽为 `(actor_href, video_code)` 所要支持的场景。schema 能够表达这一行，监控器却从未写入它。

顺序还让问题变得静默且不对称：谁先被抓取，谁就「赢得」这部新作；因此哪位演员丢失 feed 行，取决于 `list_active_hrefs()` 的返回顺序，而非任何有意义的因素。

## Fix

在抓取循环开始之前快照所有演员的 baseline，使任何演员的 diff 都不会被同伴的提交污染：

```python
normalized_hrefs = [normalize_javdb_href_path(h) or h for h in actor_hrefs]
baselines = {
    actor_href: load_seen_video_codes(actor_href, db_path=db_path)
    for actor_href in normalized_hrefs
}
```

href 归一化一并前移，使快照的键与循环中的查找完全一致。查询次数不变——依然是每位演员一次 `load_seen_video_codes`，只是全部提前完成。

issue 提出的另一方案——从每次抓取 session 而非提交后的历史来推导新行——未被采用：那需要把 diff 逻辑改为读取 session 范围的状态而非 `MovieHistory`，为同样的结果付出大得多的改动。

## Side Effects

内存中会同时持有所有订阅演员的 video code 集合，而非一次一个。对现实规模的订阅数量而言可以忽略（每位演员一个短字符串集合）。

**本修复只对未来生效。** 从修复后第一次完整成功的运行起，共享新作会进入每一位订阅演员的 feed。它**不会**找回修复前已经漏掉的行，也做不到：baseline 只存在于计算它的那次运行中，一旦某作品在更早的运行中已提交进 `MovieHistory`，此后它就存在于每位演员的 baseline 里。以下两种情况仍然丢失，这是设计取舍而非疏漏：

- 修复前被旧代码漏掉的发布。
- 演员 A 提交后、演员 B 处理前中断的运行 —— B 的下次运行会认为该作品已见。若更早的版本已把 B 的 `last_seen_href` 推进过该作品，结果相同。

要找回它们需要一份持久化的、仅在完整成功运行后才推进的按演员 baseline，其改动规模超出本缺陷所需；因此记录在下方，而不是夹带实现。`NewWorksRepo.add` 是幂等的，重复运行绝不会产生重复行。

## Follow-Up

- [x] 同一次运行内的正确性，由 `tests/unit/test_subscription_monitor.py` 的 `test_shared_release_lands_in_both_actors_feeds` 固定。
- [ ] 跨运行的找回**不在本次范围内**：一份持久化的、仅在完整成功运行后才推进的按演员 baseline，可同时覆盖「运行中断」与「旧版本已推进游标」两种情况。仅当实际观察到共演新作被漏掉时才值得做 —— 内存快照已经关闭了本次报告的缺陷。
