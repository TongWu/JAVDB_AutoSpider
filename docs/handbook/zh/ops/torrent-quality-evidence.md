# 种子质量证据 (ADR-024 Phase 1)

Phase 1 是一个仅影子、不参与决策的证据层。它检查生产流水线已选中、近期
加入且 qBittorrent 文件元数据可用的种子文件列表，计算可解释的质量分数，
并持久化结构化证据。它不会改变生产流水线最终下载哪个种子。

## 收集内容

- `TorrentQualityEvidence` 保存种子级事实，主键为
  `(info_hash, probe_schema_version, target_role)`：总大小、主视频大小与占比、
  视频/字幕/非视频文件数、垃圾文件大小与占比、可疑文件数，以及 reason codes。
- `TorrentQualityEvaluation` 保存影片上下文的影子评分，主键为
  `(info_hash, movie_href, scoring_version)`：分数、决策、推断分类、字幕证据、
  分类一致性，以及 reason codes。

在 GitHub Actions / D1 模式下，两张表都位于规范的 `javdb-reports` D1
数据库。本地运行会按照 `STORAGE_BACKEND` 写入，通常是本地 SQLite mirror，
除非另有配置；SQLite mirror 只是调试副本。

## 启用方式

默认关闭。GitHub Actions 中设置 GitHub Variable
`TORRENT_QUALITY_EVIDENCE_ENABLED=true`；本地运行可在 `config.py` 中设置
`TORRENT_QUALITY_EVIDENCE_ENABLED = True`。

| 键 | 默认值 | 含义 |
| --- | --- | --- |
| `TORRENT_QUALITY_EVIDENCE_ENABLED` | `False` | 证据采集总开关。 |
| `TORRENT_QUALITY_POLICY_MODE` | `shadow` | Phase 1 始终按 shadow 模式运行；`assist` 和 `enforce` 为后续阶段保留。 |
| `TORRENT_QUALITY_CATEGORIES` | `''` | 可选：要扫描的 qBittorrent 分类 JSON 数组。 |

## 运行时机

当 `TORRENT_QUALITY_EVIDENCE_ENABLED=true` 时，`QBFileFilter.yml` 会在
qBittorrent file filter 步骤之后立即运行采集器。采集器复用同一份已还原的
加密配置和同一个生产 qBittorrent 端点。
手动 dispatch 设置 `dry_run=true` 时会跳过采集器，因为证据行属于持久数据库写入。

工作流按以下优先级解析 evidence categories：手动 dispatch 的 `categories`
输入、`TORRENT_QUALITY_CATEGORIES`、最后是工作流默认值
`["Ad Hoc", "Daily Ingestion", "顶级"]`。

也可以手动运行：

```bash
python3 -m apps.cli.qb.quality_evidence --days 2 --categories '["Daily Ingestion"]'

# 即使 TORRENT_QUALITY_EVIDENCE_ENABLED 为 false 也强制运行。
python3 -m apps.cli.qb.quality_evidence --force --categories '["Daily Ingestion"]'
```

如果采集器关闭且未提供 `--force`，CLI 会以状态码 `0` 退出，不读取分类配置，也不触碰 qBittorrent。
如果直接运行时没有配置或传入分类 JSON 数组，采集会跳过，而不是扫描每个 qBittorrent 分类。

## 查看结果

采集器会打印一行摘要，包含 `scanned`、`evidence_written`、
`evaluations_written`、`probe_unavailable` 和 `skipped` 计数。写入后的行位于
当前 reports 数据库的 `TorrentQualityEvidence` 与 `TorrentQualityEvaluation`
表：GitHub Actions / D1 模式下是规范的 `javdb-reports`，本地运行则使用
`STORAGE_BACKEND` 选择的数据库。

FastAPI 只读表面会暴露同一批持久化行：

- `GET /api/quality/evaluations?limit=50` — 最近的影子评估。
- `GET /api/quality/evaluations?movie_href=/v/...` — 单个影片的评估。
- `GET /api/quality/evidence/{info_hash}` — 已被生产选中且 qBittorrent 元数据已采集种子的客观文件列表证据。

所有 `/api/quality/*` 端点都需要认证。上面几个都是只读；该表面上唯一的写入端点仅 admin 可用：

- `POST /api/quality/review-labels` — 为某条评估记录运维 review 标注
  （`accept` / `reject` / `skip`）。这些标注是共享的可调状态 —— 即 ADR-024 Phase 3
  用来调阈值的那份数据集 —— 而非按用户隔离的数据，因此 readonly token 不能写入。
  请求体与响应结构见 [api-reference.md](../developer/api-reference.md)。

## Reason Codes

分数是可解释的。常见 reason codes 包括：

- `main_video_detected` / `main_video_missing`
- `main_video_ratio_low`
- `junk_ratio_high`
- `subtitle_file_present` / `subtitle_file_missing`
- `category_mismatch`
- `abnormal_file_count`
- `probe_unavailable`

## Phase 1 限制

- 仅使用文件列表元数据；没有帧截取、OCR、水印检测或视觉质量检查。
- 仅采集生产选中的种子；没有 Top-K 候补探测。
- 暂无远端 `quality_probe` 端点；只有 `production_download` 角色。
