# BFR-031：Plex `lastViewedAt` epoch 未归一化直接落库，导致观看趋势失真

**Status**: Fixed
**Date**: 2026-08-09
**Severity**: Medium
**Affected**: `javdb/integrations/media_servers/plex/adapter.py`、`javdb/migrations/d1/2026_08_09_normalize_plex_watched_at_epoch.sql`
**Related**: [ADR-033](../_archive/ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.zh.md)、[IMP-ADR033-03](../_archive/ADR-033-Media-Closed-Loop/IMP-ADR033-03-consumption-signal.md)、issue #271

---

## Symptom

没有实际事故。该缺陷是在公开镜像仓库执行 `dev` → `main` 提升时由 review 发现的（TongWu/JAVDB_AutoSpider#153），记录为 issue #271，并已对照代码确认。

Plex 的观看事件在观看趋势中缺失，或出现在一个毫无意义的日期键下；而同一部署下的 Emby 观看事件一切正常。

## Root Cause

Plex 适配器把 Plex 的原生值直接透传了：

```python
watched_at=str(raw["lastViewedAt"]) if raw.get("lastViewedAt") else None,
```

Plex 返回的 `lastViewedAt` 是**数值型 Unix epoch**（秒），因此这里存入的是 `'1712345678'`。而 Emby 适配器的 `ud.get("LastPlayedDate")` 本身已是 ISO 字符串——于是两个适配器把两种不同的形态写进了同一列 `ConsumptionSignal.watched_at`。

下游的一切都假定它是 ISO。趋势构造器对此毫不含糊：

```sql
SELECT substr(watched_at, 1, 10) AS d, ...
WHERE watched_at IS NOT NULL AND watched_at >= ?
```

其 cutoff 以 `YYYY-MM-DD` 绑定。面对 `'1712345678'`，会产生两种截然不同的故障，取决于数字本身：

- `substr(...)` 返回 `'1712345678'`，它不是日期，因此**通过**了过滤的行会被归到一个非法的键下。
- 过滤本身是**字符串**比较。`'1712345678' >= '2026-05-01'` 为假，所以当下时间的 epoch 会被静默丢弃；而一个以大于 `'2'` 的数字开头的未来 epoch，则会比任何真实日期都大，从而污染**每一个**窗口。

设计缺陷在于：`MediaItem.watched_at` 被标注为 `Optional[str]` 并在文档中说明为 ISO，却没有任何东西强制它。契约只活在 docstring 里，而适配器边界——恰恰是两种厂商格式必须收敛的那一点——正是它被跳过的地方。`IMP-ADR033-03` 甚至明确固定了目标形态（`utc_now_iso()`，即 `YYYY-MM-DDTHH:MM:SS.ffffffZ`）与 `substr` 分组规则，只是 Plex 这一侧从未实现自己那一半。

## Fix

在适配器边界通过 `_watched_at_iso` 归一化，使两个适配器输出同一种形态：

- 数值（或全数字字符串）→ `datetime.fromtimestamp(epoch, tz=timezone.utc)`，并以结尾 `Z` 渲染；
- 非数值 → 原样透传，使本就发送 ISO 的服务器继续正常工作；
- 假值（缺失、空串、epoch `0`）→ `None`，保留原有 falsy 判断「从未观看」的语义，而不是声称 1970 年；
- 超出范围的 epoch → 记录日志并丢弃，而非在扫描中途抛异常。

已入库的行由 `2026_08_09_normalize_plex_watched_at_epoch.sql` 修复。它按**形态**匹配（`watched_at NOT GLOB '*[^0-9]*'`）而非按 `source_type`，因而天然幂等：真实时间戳必然含 `-`，已归一化的行在重跑时绝不可能命中。

## Side Effects

未观察到任何影响。回填已于 2026-08-10 在生产 D1 上执行，**命中 0 行**（见 Follow-Up），因此没有任何存储值发生变化，也没有任何图表发生位移。若确实存在受影响的行，它们会移动到真实日期上——迁移前后的截图将不再一致。

`watched_at` 本就是 ISO 的行不受影响，所有 Emby 行同样不受影响。

## Follow-Up

- [x] **回填已于 2026-08-10 应用到 D1**，通过 `wrangler d1 execute javdb-operations --remote`：1 条语句，读取 1 行，**写入 0 行**。

  这回答了 issue 中悬而未决的问题（「值得确认既有行是否需要回填，或者这些坏值是否稀疏到可以不管」）：**从未有坏行被写入。** 这与 issue 报告者自己的说明一致——Plex 路径尚未对接过真实服务器运行，因此该缺陷在代码层面真实存在，但尚未产生数据。该迁移予以保留而非删除：它是幂等的、记录了修复过程，并且能保护任何在本次修复前**确实**运行过 Plex 路径的部署。
- [ ] 重新对齐本地 SQLite 镜像：`python3 -m apps.cli.db.sync_d1_to_sqlite --apply --force-overwrite-all`。
