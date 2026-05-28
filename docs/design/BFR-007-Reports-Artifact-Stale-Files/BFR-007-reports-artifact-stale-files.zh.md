# BFR-007: Pipeline reports artifact 在 D1 模式下打包 SQLite DB 与历史 Dedup CSV

**Status**: Fixed
**Date**: 2026-05-28
**Severity**: Medium
**Affected**: `.github/workflows/DailyIngestion.yml`
**Related**: [Issue #111](https://github.com/TongWu/JAVDB_AutoSpider_CICD/issues/111), ADR-017 (D1 canonical source)

---

## 症状

DailyIngestion run 26512511043（`STORAGE_BACKEND=d1`）将以下文件打包进了 `reports.tar.gz.enc`（14.1 MB）：

- `reports/history.db`、`reports/reports.db`、`reports/operations.db`——D1 模式下的只读 SQLite 镜像，可通过 `sync_d1_to_sqlite` 重建
- `reports/parsed_movies_history.csv`——传统 CSV history，D1 模式下不具权威性
- 13 个 2026 年 3-5 月的历史 Dedup CSV，与当次运行无关

以 7 天保留期每天一次运行计算，浪费约 100 MB GitHub Actions artifact 存储。

## 根因

`DailyIngestion.yml:750-776` 的 "Encrypt reports" 步骤无条件包含：

1. **第 750 行**：硬编码的 `.db` 和 `.csv` 文件列表，未检查 `$STORAGE_BACKEND`
2. **第 772-776 行**：`find "$REPORTS_DIR/Dedup" -name "*.csv"` 收集所有历史 Dedup CSV，而不是仅当次运行的输出

## 修复

1. 当 `STORAGE_BACKEND=d1` 时跳过 `.db` 文件和 `parsed_movies_history.csv`
2. 将 Dedup CSV 的 `find` 替换为 spider 步骤输出的特定路径（`$DEDUP_CSV_PATH`）
3. 添加 workflow 回归测试，保护 D1 条件和当次运行 Dedup 路径

## 副作用

D1 模式下 artifact 大小从约 14 MB 降至约 1-2 MB。非 D1 运行不受影响。

## 后续工作

- [x] 将 `.db` 和传统 CSV 的包含条件化为 `$STORAGE_BACKEND`
- [x] 使用 spider 输出的 Dedup CSV 路径替代 `find`
- [x] 添加 artifact 文件列表的 workflow 回归测试
