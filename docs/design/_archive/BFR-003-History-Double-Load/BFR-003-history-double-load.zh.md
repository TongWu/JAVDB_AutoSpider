# BFR-003: Spider 启动时 parsed_movies_history 被加载两次（~12s 浪费）

**Status**: Fixed
**Date**: 2026-05-28
**Severity**: Medium
**Affected**: `javdb/spider/app/run_service.py`, `javdb/storage/history_manager.py`
**Related**: [Issue #107](https://github.com/TongWu/JAVDB_AutoSpider_CICD/issues/107), ADR-017 (D1 canonical source)

---

## 症状

DailyIngestion run 26512511043 在 6 秒内打出两条相同的 history 加载日志：

```
21:00:42 History  Loaded 40383 previously parsed movies from history
21:00:48 History  Loaded 40383 previously parsed movies from history
```

总浪费时间：约 12 秒的 D1 查询 + 解析。

## 根因

`run_service.py:366-367` 连续调用了两次 `load_parsed_movies_history()`：

```python
parsed_movies_history_phase1 = load_parsed_movies_history(history_file, phase=1)
parsed_movies_history_phase2 = load_parsed_movies_history(history_file, phase=None)
```

在 SQLite/D1 模式下（`use_sqlite()` 返回 True），每次调用触发 `HistoryRepo().load_history(phase=...)`，执行完整的 `SELECT * FROM MovieHistory`，然后在 Python 中过滤。Phase 1 数据始终是 phase=None（所有 phase）数据的子集，所以第一次查询是冗余的——结果可以从第二次查询的结果中派生。

## 修复

只加载一次 history（`phase=None`），然后在本地派生 phase-1 视图。DB-backed history 已不再存储 phase，因此派生出的 phase-1 视图保留所有行（与之前 DB 路径忽略 `phase` 参数的行为一致）；CSV fallback 行仍携带 `phase`，所以会排除 phase-2-only 行以保持 legacy CSV 语义。这将 D1 读取减半，消除约 6 秒的启动时间。

## 副作用

无——相同数据，更少查询。

## 后续工作

- [x] 将两次 `load_parsed_movies_history` 调用合并为一次加载 + 派生 phase-1 视图
- [x] 添加 CSV phase-2 排除行为的回归测试
