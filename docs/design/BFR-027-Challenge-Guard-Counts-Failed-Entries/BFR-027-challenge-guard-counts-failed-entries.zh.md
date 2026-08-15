# BFR-027：挑战守卫把失败条目算作"有发现"

**Status**: Fixed
**Date**: 2026-08-09
**Severity**: High
**Affected**: `javdb/spider/runtime/report.py`
**Related**: [BFR-025](../BFR-025-Site-Wide-Challenge-Bans-Pool-Via-Coordinator/BFR-025-site-wide-challenge-bans-pool-via-coordinator.zh.md), [BFR-024](../BFR-024-CF-Managed-Challenge-Blind-Spot/BFR-024-cf-managed-challenge-blind-spot.zh.md), [PR #154](https://github.com/TongWu/JAVDB_AutoSpider/pull/154)（评审发现）

---

## Symptom

潜伏缺陷——由公开镜像 promotion PR 上的自动评审发现，不是某次失败的 run 暴露的。
BFR-025 为"全站被墙"加的那道守卫，在**索引阶段挺过了墙、详情阶段没挺过**时不会触发。

这种形态下 run 的结束方式和平静的一天没有区别：

- 退出码 `0`
- 一个只有表头的 CSV（BFR-025 时期的 `csv_writer` 改动保证文件一定存在）
- 没有任何代理被标记为 banned，因为全站挑战本就刻意不 ban 任何代理

对下游而言，这样一次空 ingest 与真正的空白日无法区分：pipeline 提交 session，
通知里报告零条新增，而"JavDB 把所有代理都墙掉了"这件事没有任何信号。

## Root Cause

BFR-025 把全站挑战从代理 ban 记账里摘了出去——不该为一堵没人造成的墙烧掉 28 个代理。
这一摘同时拿走了原本唯一能让"全站被墙"的 run 失败的信号，所以 BFR-025 在
`report.py` 里补了一道替代守卫：

```python
if site_challenge_seen and total_discovered == 0:
    ...
    sys.exit(2)
```

问题出在选用了 `total_discovered`，其定义在 `report.py:79`：

```python
total_discovered = len(rows) + skipped_history_count + no_new_torrents_count + failed_count
```

`failed_count` 是这个和的一部分。对摘要里的 "found" 一行来说这个定义是对的——索引
翻出来的每一条，不论后来怎么样了——但对一道追问*这次 run 到底产出了什么吗？*的守卫
来说就是错的。

全站挑战下的两条路径，恰恰在这一项上分道扬镳：

| 路径 | `rows` | `failed_count` | `total_discovered` | 守卫触发？ |
| --- | --- | --- | --- | --- |
| 索引本身被墙 | 0 | 0 | 0 | 是 |
| 索引挺过，详情全被墙 | 0 | N | N | **否** |

第二条路径并不罕见。索引页和详情页是分开的请求，详情阶段的挑战在
`fetch_engine.py:1204-1213` 处理：task 记录 `failed_proxies`、latch 住
`site_challenge_seen`、并跳过软 ban 记账。这些 task 经由 `p1_result['failed']` /
`p2_result['failed']`（`run_service.py:764`、`:838`）落进 `failed_count`。于是 run
带着 `site_challenge_seen` 为真、零条 rows、以及一个正的总数走到守卫面前，被放行。

底层的设计错误是：把一个聚合值当成能同时回答两个不同问题的东西。"我们看见了多少条？"
和"我们拿到可用的东西了吗？"需要不同的分子，而 `failed_count` 正是区分二者的那一项。

## Fix

先减掉失败数再发问，并在调用处把这件事写明白：

```python
legitimate_discovered = total_discovered - failed_count
if site_challenge_seen and legitimate_discovered == 0:
```

`skipped_history_count` 和 `no_new_torrents_count` 是**刻意**保留在和里的。二者都
意味着索引 fetch 真的落了地、其条目也确实和历史比对过——是真实的工作量，也是"部分
恢复的 run 仍算成功"这一既有契约。只有 `failed_count` 被排除。

错误信息现在会报出有多少条已发现条目失败了，因为在这条路径上"discovered ZERO
entries"已经不准确了。

`tests/unit/test_cf_challenge_handling.py` 中的
`TestZeroEntriesUnderChallengeFailsTheRun` 新增了 `failed` 参数与三个用例：

| 用例 | 预期 |
| --- | --- |
| `rows=[]`、`failed=40`、见到挑战 | `SystemExit(2)` |
| `rows=[]`、`failed=40`、未见挑战 | 不抛——这道守卫只管挑战 |
| `rows=[]`、`skipped=30`、`failed=10`、见到挑战 | 不抛——历史跳过是真实工作量 |

第一个用例在修复前的守卫上会失败、修复后通过，因此它钉住的是回归本身，而不只是
描述了一遍现象。

## Side Effects

索引成功之后被全面墙掉的 run，现在退出码是 `2`，而此前是 `0`。这正是想要的纠正，
但它是一个发生在"一直静默通过"的路径上的**实际行为变更**：

- `cleanup-on-failure` 步骤现在会为这些 run 派发，于是 session 被回滚，而不是空着提交。
- 在 JavDB 墙掉详情阶段的日子里，Daily Ingestion 会报失败。这是真信号而非噪音，但它
  改变了 run 的结果以及它产生的通知。

BFR-025 引入的 ban 记账没有变化；摘要报告里的 `found` 数值也没有变化——
`total_discovered` 在展示用途上仍然计入失败数。

## Follow-Up

- [x] 把 `failed_count` 从守卫的发现总数里排除
- [x] 用单元测试覆盖"索引挺过 / 详情全被墙"这条路径
- [ ] 在下一次真实的全站挑战中确认：run 在 report 阶段失败，且 `cleanup-on-failure`
      按预期回滚了 session
- [ ] 决定是否把失败**归因**到挑战，而不是靠推断。守卫读的是一个 run 级别的
      `site_challenge_seen` latch 加上一个未归因的 `failed_count`，因此：挑战在早期
      latch 过一次、随后每一条都因不相关的原因失败、且既没有历史跳过也没有
      no-new-torrents 命中来挡住守卫——这样的 run 会因为一个巧合而退出 `2`。窗口很窄
      且误判方向是 fail-safe，所以此处选择延后而非就地修。精确版本不能只认
      `error='site_challenge_exhausted'`：该标记只在耗尽连击分支
      （`fetch_engine.py:1215-1230`）发出，而那些被 requeue、最终以
      `all_proxies_failed` 收场的挑战失败并不携带标记，只认它会以更窄的形式重新引入
      同一个缺陷。正确做法需要在 task 层面做挑战归因，并把计数穿过
      `fetch_engine` → `detail/runner` → `run_service` → `report`。由
      [PR #276](https://github.com/TongWu/JAVDB_AutoSpider_CICD/pull/276) 的自动评审提出。
