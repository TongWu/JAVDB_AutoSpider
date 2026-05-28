# BFR-005: Health-weighted proxy selection 未惩罚持续返回 None 的 worker

**Status**: Fixed
**Date**: 2026-05-28
**Severity**: High
**Affected**: `javdb/spider/fetch/fetch_engine.py`, `javdb/proxy/ban_manager.py`
**Related**: [Issue #109](https://github.com/TongWu/JAVDB_AutoSpider_CICD/issues/109)

---

## 症状

DailyIngestion run 26512511043：三个 worker（Seoul-ARM2、Chuncheon-ARM1、Chuncheon-ARM2）在每个阶段（index、phase 1、phase 2）的第一个任务都返回了 `None`。每次 `None` 导致 re-queue 花费约 10-20s/阶段，总计约 30-60s。尽管持续失败，最终摘要显示 `available=11/11 · cooldown=0 · banned=0`——这些 worker 从未被惩罚。

## 根因

在 `fetch_engine.py:1020-1039` 中，当 `_process_fn` 返回 `None` 时：
1. proxy 被添加到 task 的 `failed_proxies` 集合（task 级别追踪）
2. task 通过 `requeue_front()` 重新入队
3. **没有调用 `BanManager.add_ban()` 或任何健康权重惩罚**

`BanManager.add_ban()` 只在 `ProxyBannedError` 异常处理器（`fetch_engine.py:1043-1050`）中被调用。`None` 返回路径从不与 ban manager 或健康权重系统交互。

task 级别的 `failed_proxies` 防止*同一 task* 被重新分配到同一 proxy，但队列中的新 task 仍然可以被分派到失败的 proxy，重复这个循环。

## 修复

在 FetchEngine 的 worker 循环中添加 per-worker 连续 None 计数器。在连续 N 次 `None` 返回（默认：2）后，通过 `BanManager.add_ban()` 对该 proxy 进行 session 级软 ban，并走 FetchEngine 现有 ban handler，让 `_banned_proxies`、active-worker 计数、sleep rebalance 和 all-proxies-banned 处理保持一致。

## 副作用

间歇性返回 `None` 的 worker（如瞬时网络问题）会更早被 ban。连续 2 次 None 的阈值减轻了误报——单次 None 仍允许重试。

## 后续工作

- [x] 在 FetchEngine worker 循环中添加 per-worker None 计数器
- [x] 连续 N 次 None 后触发软 ban
- [x] 通过 FetchEngine 现有 ban/rebalance 路径处理软 ban
- [x] 为连续 None ban 行为添加单元测试
