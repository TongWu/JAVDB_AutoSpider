# BFR-006: file_filter 跳过 55% 新 torrent 因 metadata 未就绪

**Status**: Fixed
**Date**: 2026-05-28
**Severity**: High
**Affected**: `javdb/integrations/qb/file_filter.py`, `.github/workflows/DailyIngestion.yml`
**Related**: [Issue #110](https://github.com/TongWu/JAVDB_AutoSpider_CICD/issues/110)

---

## 症状

DailyIngestion run 26512511043：qb.file_filter 在 qb.uploader 完成 36 秒后运行。在处理的 31 个 torrent 中，17 个（55%）因 `Metadata not yet available for: <name> (will be processed on next run)` 被跳过。这 17 个 torrent 中的小文件（广告、HTML、txt）在约 2 小时内未被过滤地下载，直到 cron 的 `QBFileFilter.yml` 运行。

此外，2 个 24 小时前添加的 torrent（MIGD-571、MXGS-1403）仍然 pending metadata，说明存在 tracker 连接问题，与此时序 bug 无关。

## 根因

`file_filter.py:581-586` 检查 `len(files) == 0` 后立即跳过并打日志——没有重试或等待逻辑：

```python
if len(files) == 0:
    logger.info(f"  Metadata not yet available for: {torrent_name} ...")
    stats['pending_metadata'] += 1
    continue
```

在 DailyIngestion 流水线中，file_filter 在同一 job 步骤中紧跟 uploader 执行。qBittorrent 通常需要 30s-2min 从 tracker 获取 metadata。36s 的间隔对大多数 torrent 不够。

## 修复

在 `filter_small_files()` 开始处添加 metadata 就绪轮询循环：在处理 torrent 列表之前，等待最多 90 秒让大部分最近添加的 torrent 的 metadata 就绪，每 10 秒轮询一次 qBittorrent。等待只考虑最近 metadata window 内添加的 torrent，因此旧的 tracker-stuck torrent 不会迫使每次运行都等待。

## 副作用

最坏情况下流水线运行时间增加最多 90 秒（最近添加的 torrent metadata 获取慢）。实际上大多数 metadata 在 30-60s 内到达，因此典型延迟为 20-40s，但过滤覆盖率显著提高。更旧但仍无 metadata 的 torrent 会继续走正常 pending-metadata 路径。

## 后续工作

- [x] 在 file_filter 处理前添加 metadata 就绪轮询
- [x] 将轮询限制在最近添加的 torrent
- [x] 为轮询逻辑添加单元测试
