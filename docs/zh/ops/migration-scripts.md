# 迁移脚本

用于升级数据库架构、清理历史数据和在格式之间转换的工具。

## 主要迁移入口

```bash
python3 -m packages.python.javdb_migrations.migrate_to_current --help
```

`migrate_to_current.py` 是 SQLite 架构升级的主要入口。支持可选的日期时间标准化和演员信息回填。使用 `--help` 查看所有可用选项。

## 一次性和旧版辅助脚本

`packages/python/javdb_migrations/tools/` 目录包含用于特定升级任务的一次性迁移脚本。

### cleanup_history_priorities.py

从历史文件中删除重复条目。

- 确保数据完整性
- 可安全多次运行（幂等）

```bash
python3 packages/python/javdb_migrations/tools/cleanup_history_priorities.py
```

### update_history_format.py

将旧历史格式迁移到新格式。

- 将 `parsed_date` 转换为 `create_date` / `update_date`
- 自动向后兼容

```bash
python3 packages/python/javdb_migrations/tools/update_history_format.py
```

### rename_columns_add_last_visited.py

重命名日期列并添加 `last_visited_datetime` 字段。

- 升级以支持新历史格式时必需

```bash
python3 packages/python/javdb_migrations/tools/rename_columns_add_last_visited.py
```

### migrate_reports_to_dated_dirs.py

将平铺的报告文件迁移到 `YYYY/MM/` 日期子目录结构中。

- 升级到新的报告目录结构时必需
- 支持 `--dry-run` 以在不移动文件的情况下预览变更

```bash
# 先预览变更
python3 packages/python/javdb_migrations/tools/migrate_reports_to_dated_dirs.py --dry-run

# 应用变更
python3 packages/python/javdb_migrations/tools/migrate_reports_to_dated_dirs.py
```

### reclassify_c_hacked_torrents.py

重新分类具有特定命名模式的种子。

- 更新种子类型分类
- 在分类规则变更后使用

```bash
python3 packages/python/javdb_migrations/tools/reclassify_c_hacked_torrents.py
```

## 何时运行迁移脚本

在以下情况下运行迁移脚本：

- 从项目的旧版本升级
- 历史文件显示重复条目
- 新版本中引入了格式变更
- 在 bug 修复或分类变更后需要清理数据

## 重要说明

- **务必先备份**：在运行任何迁移脚本之前，备份 `reports/parsed_movies_history.csv` 和 SQLite 数据库（`reports/history.db`、`reports/reports.db`、`reports/operations.db`）。
- **从仓库根目录运行**：所有脚本都需要从项目根目录运行。
- **优先使用 `--dry-run`**：支持 `--dry-run` 的脚本应在应用变更前先预览。
- **尽可能幂等**：大多数清理脚本可安全多次运行，但始终建议先通过 dry run 验证。
