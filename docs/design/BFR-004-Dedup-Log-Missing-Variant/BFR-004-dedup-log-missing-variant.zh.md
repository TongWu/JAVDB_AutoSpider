# BFR-004: DEDUP upgrade 日志对同一 movie code 看起来重复

**Status**: Fixed
**Date**: 2026-05-28
**Severity**: Low
**Affected**: `javdb/spider/services/dedup.py`, `javdb/spider/detail/runner.py`
**Related**: [Issue #108](https://github.com/TongWu/JAVDB_AutoSpider_CICD/issues/108)

---

## 症状

DailyIngestion run 26512511043 Phase 1 中，同一 worker 在 0.3s 内对同一 movie code（如 MIDA-616、JUR-703、SNOS-185）重复打出 2-3 次 "Subtitle upgrade" DEDUP 日志。后续 rclone purge 阶段确认每个 code 确实清理了多个不同 variant（如 `MIDA-616/无码破解-无字` 和 `MIDA-616/有码-无字`）。

## 根因

这是**预期行为**，不是 bug。`check_dedup_upgrade()`（`dedup.py:314-319`）遍历给定 video code 的所有 `rclone_entries`。当同一 code 在 GDrive 上有多个 variant 文件夹（如 `有码-无字` 和 `无码破解-无字`）时，每个匹配的 entry 都会生成自己的 `DedupRecord`。日志消息不包含 variant/文件夹路径，导致看起来相同的日志行像是重复。

detail runner 的 DEDUP 日志打印 reason 字符串时没有包含 variant 区分信息，所以：
```
DEDUP: MIDA-616 - Subtitle upgrade (中字 found, replacing 无字)
DEDUP: MIDA-616 - Subtitle upgrade (中字 found, replacing 无字)
```
实际上是两个不同 variant 被正确识别。

## 修复

在 DEDUP 日志行中包含 GDrive 文件夹路径或 sensor/subtitle 分类，使每个条目视觉上可区分。

## 副作用

无——仅日志格式变更。

## 后续工作

- [x] 在 `detail/runner.py` 的 DEDUP 日志输出中添加 variant 信息（sensor+subtitle 分类）
- [x] 添加 DEDUP 日志 variant label helper 的回归测试
