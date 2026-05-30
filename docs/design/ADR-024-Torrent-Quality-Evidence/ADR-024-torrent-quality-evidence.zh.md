# ADR-024：种子质量证据基础层

| 字段        | 值                                                                    |
| ----------- | --------------------------------------------------------------------- |
| **状态**    | Proposed                                                              |
| **日期**    | 2026-05-27                                                            |
| **作者**    | Ted                                                                   |
| **关联**    | [ADR-010](../_archive/ADR-010-D1-Access-Port/ADR-010-d1-access-port.md), [ADR-022](../ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.zh.md) |

## 背景

当前 torrent 选择路径保留四个生产分类：`hacked_subtitle`、`hacked_no_subtitle`、
`subtitle` 和 `no_subtitle`。在每个分类内部，最佳候选主要还是从 JavDB
元数据、torrent 名称、发布时间和 torrent 总大小推断出来。这让 pipeline 保持简单，
但会漏掉一些只有 qBittorrent 拿到 torrent metadata 后才可见的质量问题：

- torrent 总大小可能被广告文件、样片、截图、压缩包或其他非主视频内容注水；
- JavDB 分类可能错误，例如标注有字幕但实际没有内嵌字幕或字幕文件，或者标注无码破解但实际仍有码；
- torrent 名称可能宣称某个分辨率或分类，但文件清单并不支持这个说法；
- 水印、视频广告、画质差等视频级问题需要内容检测，第一阶段自动处理风险太高。

DailyIngestion 已经会把生产规则选中的 torrent 通过生产 qBittorrent 端点下载到本地
NAS。Shadow 质量探测不能增加 NAS 存储压力，也不能把候选 torrent 送进下游刮削、
PikPak、file filter 或媒体整理流程。系统还需要同时支持多个 qBittorrent 端点：
本地 NAS 的生产端点，以及可用于短生命周期 metadata probe 的远端服务器端点。

这里的机会是先建立 D1-first 的证据层，再考虑任何 ML 模型。第一版应收集客观
torrent metadata，计算可解释分数，并报告 ranker 本来会选择哪个候选，但不改变
生产下载决策。

## 决策

建立一个以 D1 为权威来源的 torrent 质量证据基础层，并先以 shadow 模式运行。它从生产
下载和远端候选 probe 两侧收集文件清单证据，计算可解释分数，并把结构化决策写入 D1，
供后续 assist/enforce rollout 或离线模型训练使用。

ADR-024 不在生产路径中引入模型推理。Phase 1 是规则化、可审计、shadow-only 的。

### 设计决策

D1. **保留生产分类语义** - 生产 pipeline 继续使用现有四个 torrent 分类。Phase 1 会在
这些分类内部评估候选，但不替换生产规则选中的 magnet。

D2. **D1 是权威来源** - Torrent 质量证据和评估行先通过 D1 migration 创建。SQLite 可以
作为本地调试镜像，但 local-only evidence 不是权威。

D3. **区分 torrent 证据和影片上下文评估** - 客观文件清单事实以 `info_hash` 和
`probe_schema_version` 为键。上下文评估以 `info_hash`、影片上下文和
`scoring_version` 为键，因为同一个 torrent 可能出现在多个 JavDB 页面或推断分类下。

D4. **从两类 qBittorrent 角色采集** - 生产 qBittorrent 端点为 DailyIngestion 已经会下载
到本地 NAS 的 torrent 提供证据。远端 `quality_probe` 端点为 shadow 候选提供短生命周期
证据。证据行记录 `target_role`，例如 `production_download` 和 `quality_probe`。

D5. **不得把 probe 候选当作生产下载** - Shadow 候选必须在远端 probe 端点使用专用
qBittorrent 分类，例如 `JavDB Quality Shadow`。下游 file filter、PikPak bridge、刮削器和
整理流程默认忽略该分类。

D6. **Probe 候选是短生命周期的** - 候选只会在接收 metadata 和文件清单证据所需的时间内
加入 qB。采集完成后用 `deleteFiles=false` 删除 torrent。超时的候选同样删除，并记录
`pending_timeout` 等状态。

D7. **Metadata-only probe 必须做能力探测** - 仅使用 `paused=true` 可能会阻止 magnet
获取 metadata，具体取决于 qBittorrent 行为。实现必须探测目标端点是否支持 metadata-only
流程，优先使用支持时的 `stopCondition=MetadataReceived`。如果目标不支持安全 metadata
probe，则 shadow probe fail closed，记录 `probe_capability_unsupported`，不影响生产 ingestion。

D8. **Top-K shadow 采集必须有边界** - Phase 1 收集当前生产选中的 torrent，加上每个分类
有上限的 Top K 候选，并设置每次运行的全局上限。这样既能发现总大小启发式选错的情况，
又避免对所有 magnet 做 fan-out。

D9. **可解释性是契约的一部分** - 分数必须拆成结构化信号和原因，而不是只存一个黑盒数字。
原因码示例包括 `junk_ratio_high`、`subtitle_file_missing`、`main_video_detected`、
`category_mismatch`、`resolution_claim_unsupported` 和 `probe_unavailable`。

D10. **Phase 1 不做视频内容检查** - 第一版只使用 JavDB 静态元数据和 qBittorrent 文件清单
metadata。抽帧、OCR、水印检测、视频广告检测、有码/无码视觉分类推迟到未来 ADR 或后续阶段。

D11. **先 shadow-only，再 assist/enforce** - Phase 1 写证据、计算分数，并报告本来会胜出的
候选。它不改变生产 uploader。后续阶段可以引入类似 `TORRENT_QUALITY_POLICY_MODE` 的
`shadow`、`assist`、`enforce` 模式。

### 证据模型

`TorrentQualityEvidence` 保存 torrent-level 的客观事实：

- `info_hash`、`probe_schema_version`、`target_role`、`probe_target_name`；
- `metadata_status`、`metadata_started_at`、`metadata_completed_at`；
- `total_size_bytes`、`main_video_size_bytes`、`main_video_ratio`；
- `video_file_count`、`subtitle_file_count`、`non_video_file_count`；
- `junk_size_bytes`、`junk_size_ratio`、`suspicious_file_count`；
- 文件清单特征摘要和原因码；
- 安全的 magnet/source 指纹，而不是原始 secret。

`TorrentQualityEvaluation` 保存 movie-context 的评分事实：

- `info_hash`、`movie_href`、`video_code`、`javdb_category`；
- `magnet_name`、`javdb_tags_json`、`javdb_size_text`；
- 推断分类和分类一致性信号；
- 字幕证据、分辨率一致性、来源可信度信号；
- `score`、`shadow_rank`、`would_replace_current_choice`；
- `decision`，例如 `accepted_shadow`、`rejected_shadow`、`needs_review`、`probe_unavailable`；
- `reasons_json` 和 `scoring_version`。

具体表名可以在实现中调整，但 torrent-level evidence 与 context-level evaluation 的拆分是本 ADR
的一部分。

### 评分信号

Phase 1 的 scoring 刻意保持可解释：

- 有效主视频大小，而不是 raw total torrent size；
- junk/ad-file ratio penalty；
- 字幕文件证据，名称里的字幕 hint 只作为弱信号；
- JavDB 分类、magnet 名称和文件名的一致性；
- 声称分辨率与文件名/分类线索是否一致；
- 异常文件数量和可疑扩展名模式；
- 发布时间只作为 tie-breaker，而不是主导质量信号。

该分数在后续 rollout gate 启用行为改变之前，只是 shadow score。

### qBittorrent 隔离

生产下载和 probe 使用不同 qBittorrent 角色：

| 角色 | 端点 | 分类 | 行为 |
| --- | --- | --- | --- |
| `production_download` | 现有本地 NAS qBittorrent | 现有生产分类 | 只读 evidence；不删除、不改分类 |
| `quality_probe` | 专用远端 qBittorrent | `JavDB Quality Shadow` | Metadata-only probe；采集文件清单后删除 |

如果 `quality_probe` 未配置、登录失败、不具备 metadata-only 能力或超时，系统记录 evidence 状态，
并保持生产 pipeline 不变。

## 后果

### 正面

- 系统可以区分有效视频大小和被注水的 torrent 总大小。
- JavDB 分类错误会变成可测量证据，而不是隐藏在名称/tag 中。
- 证据可以按 `info_hash` 跨重复 magnet URL 复用。
- Shadow probe 在远端端点运行并快速删除，生产 NAS 压力可控。
- 第一阶段只观察和报告，rollout 风险低。
- D1 数据集后续可以支持阈值调优、监督学习或 learning-to-rank，而不需要推翻初始 schema 方向。

### 负面

- Phase 1 新增一个 qBittorrent 集成角色和运维配置。
- 某些 qBittorrent 版本可能不支持干净的 metadata-only probe 路径。
- Metadata 获取仍会消耗远端带宽和 tracker 资源。
- 仅靠文件清单无法识别视觉水印、内嵌广告或真实画质。

### 风险

- **Probe 端点意外触发下游自动化** - 缓解方式：使用专用分类，并让下游任务默认忽略它。
- **暂停 torrent 永远不获取 metadata** - 缓解方式：在启用 probe 前能力探测
  `stopCondition=MetadataReceived` 或等价行为。
- **证据行与 scoring 逻辑漂移** - 缓解方式：同时版本化 evidence extraction
  (`probe_schema_version`) 和 scoring (`scoring_version`)。
- **过度信任规则分数** - 缓解方式：Phase 1 保持 shadow-only，并在启用 assist/enforce 之前暴露原因码。

## 实施路线图

| 阶段 | IMP | 交付内容 | 推迟内容 |
| --- | --- | --- | --- |
| Phase 1 | Future IMP | D1 evidence schema、生产/probe evidence 采集、有边界的 Top-K shadow scoring、qB capability canary、日志/API 报告 | 不改变生产下载行为 |
| Phase 2 | Future IMP | Assist mode，可推荐每个分类的替换候选，并在 API/Web 中暴露 review 动作 | 完全自动执行 |
| Phase 3 | Future IMP | Enforce mode，带 rollout gate、阈值调优、backfill/reporting jobs | 视频抽帧/CV 检查和重量级 ML runtime |

## 参考

- `javdb/spider/magnet_extractor.py` - 当前分类提取和大小/时间排序。
- `javdb/pipeline/policies.py` - 当前分类语义和缺失类型策略。
- `javdb/integrations/qb/client.py` - 共享 qBittorrent add/delete/list client。
- `javdb/integrations/qb/file_filter.py` - 当前 qBittorrent 文件清单访问。
- `javdb/integrations/qb/uploader.py` - 生产 qBittorrent 分类和上传路径。
- [qBittorrent WebUI API 5.0](https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-%28qBittorrent-5.0%29) - add、files、torrent state 和 delete API 行为。
- [qBittorrent AddTorrentParams source](https://raw.githubusercontent.com/qbittorrent/qBittorrent/master/src/base/bittorrent/addtorrentparams.h) - server-side add-torrent 参数，包括 stop condition 支持。
- [qbittorrent-api torrents docs](https://qbittorrent-api.readthedocs.io/en/v2025.5.0/apidoc/torrents.html) - add-torrent options 的 client-surface 参考。

## 状态日志

- 2026-05-27：以 ADR-024 提出。
