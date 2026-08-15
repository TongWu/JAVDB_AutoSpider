# BFR-028：入库失败趋势按 `last_seen_at` 分组，而非状态迁移时刻

**Status**: Fixed
**Date**: 2026-08-09
**Severity**: Medium
**Affected**: `apps/api/routers/library_query_builders.py`、`javdb/ops/reconcile/service.py`、`javdb/ops/reconcile/models.py`、`javdb/storage/repos/acquisition_outcome_repo.py`、`javdb/storage/db/_db_migrations.py`、`javdb/migrations/d1/2026_08_09_add_acquisition_state_changed_at.sql`
**Related**: [ADR-033](../_archive/ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.zh.md)、[ADR-017](../_archive/ADR-017-Cloudflare-First-Deployment/ADR-017-cloudflare-first-deployment.zh.md)、issue #273

---

## Symptom

没有实际事故。该缺陷是在公开镜像仓库执行 `dev` → `main` 提升时由 review 发现的（TongWu/JAVDB_AutoSpider#153），记录为 issue #273，并已对照代码确认。

`GET /api/library/acquisition/trend` 把 `stalled` 与 `failed` 的计数画在了错误的日期上——最多比真实迁移时刻早两周。在默认的 `period=7d` 下，今天检测到的失败会完全落在窗口之外，根本不会出现在图上。

## Root Cause

`AcquisitionOutcome` 没有任何列记录**状态是何时改变的**，于是趋势查询退而求其次，用了看起来最接近的那一列：

```python
"SELECT substr(last_seen_at, 1, 10) AS d, "
...
"WHERE state IN ('completed','stalled','failed') AND last_seen_at >= ? "
```

上方的注释断言「`last_seen_at` 就是迁移日期（每轮刷新）」。这句话只对仍能在 qB 中被观测到的行成立；而对趋势最有价值的那两个状态，它是错的：

```python
if obs is not None:
    ...
    rec.last_seen_at = now          # 仅在种子仍被观测到时刷新
else:
    age = _age_days(rec.last_seen_at or rec.queued_at)
    if age >= 2 * options.stalled_after_days:
        new_state = "failed"        # 刻意不更新 last_seen_at
    elif age >= options.stalled_after_days:
        new_state = "stalled"
```

「消失的种子」恰恰就是无法被观测到的种子，因此 `last_seen_at` 冻结在最后一次**成功**观测的时刻，而状态迁移发生在其后 `stalled_after_days`（默认 7）或 `2 ×`（14）天。也就是说 `last_seen_at` 记录的是「最后一次还活着」，趋势却把它当成了「死亡日期」。

设计缺陷在于：一列承担了两种语义——存活性（最后一次见到）与迁移（何时进入终态）。对 `completed` 而言两者恰好重合，这正是缺陷长期隐形的原因：唯一被正确渲染的状态，刚好是两种语义碰巧一致的那个。

## Fix

补上缺失的时间戳，而不是继续复用旧列。`last_seen_at` 不能简单地在迁移时刷新：对账逻辑正是以「距 `last_seen_at` 的时长」来衡量 `failed` 阈值的，若在 `stalled` 迁移时写入 `now`，这个计时就被重置，任何行都再也无法进入 `failed`。

按「D1 优先」执行（D1 是唯一事实源）：

- `javdb/migrations/d1/2026_08_09_add_acquisition_state_changed_at.sql` —— `ALTER TABLE ... ADD COLUMN state_changed_at TEXT`，以 `COALESCE(landed_at, completed_at, last_seen_at, queued_at)` 回填，并建立索引。`_db_migrations.py` 中的本地 SQLite DDL 将该列追加在最后，以匹配 D1 执行 `ALTER` 之后的实际列序。
- `AcquisitionOutcomeRecord.state_changed_at` 及 repo 列表。`mark_state` 与 `mark_in_library` **仅在目标状态与已存状态不同时**才推进它 —— 调用方指定的是目标状态，不一定是一次迁移；重复上报同一状态（每日 PikPak 清理碰到小时级 pass 已完成的行）必须保留原有的 `completed_at` / `landed_at` / `state_changed_at`。`upsert` 的赋值用 `COALESCE` 包裹，确保「本轮无变化」的写入不会抹掉已记录的迁移时刻。
- `service.run` **仅在 `new_state != previous_state` 时**执行 `rec.state_changed_at = now`。这个判断很关键：在「未观测到」分支中，`stalled` 会在每一轮被重新推导，直到 `2 ×` 窗口打开；若无条件打戳，迁移日期会随每次运行逐日向前漂移。
- 趋势查询构造器改为按 `state_changed_at` 分组与过滤。

## Side Effects

历史数据的位置仍不精确。回填只能利用已记录的信息，因此在本次迁移之前就已进入 `stalled` / `failed` 的行，仍停留在它们原来的 `last_seen_at` 日期上——与修复前的（错误）位置相同。只有迁移之后发生的状态变更才是准确的；随着这些旧行滚出窗口，趋势会逐步收敛到正确形态。

`state_changed_at` 未在 `/acquisition/recent` 中暴露；API 响应结构没有任何变化。

## Follow-Up

- [x] **已于 2026-08-10 应用到 D1**，通过 `wrangler d1 execute javdb-operations --remote`：3 条语句（ALTER + 回填 + 索引），读取 11 077 行，**写入 7 316 行** —— 即回填为 7 316 条既有 `AcquisitionOutcome` 行填上了 `state_changed_at`。
- [ ] 重新对齐本地 SQLite 镜像：`python3 -m apps.cli.db.sync_d1_to_sqlite --apply --force-overwrite-all`。
- [x] 双后端一致性（ADR-017）：`TongWu/JAVDB_AutoSpider_Web` 的 `server/routes/library.ts` 逐字节镜像了这段 SQL，存在同样的缺陷。已在该仓库的 `claude/verify-fix-open-issues-n8srgf` 分支修复，同时更新了 `server/__tests__/library-routes.test.ts` 的 fixture DDL 与重新 vendor 的 query golden（`fe08ee6c2ccb71b6` → `b5c870bd2e86028e`）。
- [ ] **部署顺序有要求**：必须先执行 D1 迁移，再部署该 Worker，否则其趋势查询会以 `no such column: state_changed_at` 失败。
