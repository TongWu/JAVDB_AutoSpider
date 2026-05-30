# ADR-033：媒体闭环 —— 获取结果、拥有真相与消费信号

| 字段       | 值                                                                    |
| ---------- | --------------------------------------------------------------------- |
| **状态**   | Proposed — 伞型;Phase 1 已实现并完成本地验证;执行下放给各期 IMP       |
| **日期**   | 2026-05-29                                                            |
| **作者**   | Ted                                                                   |
| **关联**   | [ADR-022](../ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md), [ADR-024](../ADR-024-Torrent-Quality-Evidence/ADR-024-torrent-quality-evidence.md), [ADR-025](../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md), [ADR-015](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md), [ADR-010](../_archive/ADR-010-D1-Access-Port/ADR-010-d1-access-port.md), [ADR-028](../ADR-028-Web-Platform-Completeness-Roadmap/ADR-028-web-platform-completeness-roadmap.md) |

> 源自 2026-05-29 一次关于"现有 ADR 尚未收编的全新方向"的头脑风暴。

## 背景 (Context)

系统的认知**止步于"把磁链加进 qBittorrent"那一刻**。它优化的是*获取决策*,却对之后发生的事完全失明——下载有没有完成、文件实际落到哪、有没有被观看过。对照当前代码具体而言:

- **`MovieHistory` / `TorrentHistory` 只记录"爬到并选中了什么"**(video code、magnet、字幕/无码/分辨率标记、size、file count),**没有任何下载结果状态**——没有 qB hash 关联,没有 `completed` / `failed` / `stalled`,没有完成时间。
- **唯一的"拥有真相"是 `RcloneInventory`**——一份对 GDrive 远端的周期性 rclone 快照(按 video code + 分类,由 `WeeklyDedup` 每周刷新)。它只覆盖 GDrive、只反映文件是否存在。dedup 检查器(`javdb/spider/services/dedup.py`)读它来判断跳过/升级。
- **qB 完成状态是瞬时的。** `remove_completed_torrents_keep_files` 能查 `completed` 过滤器,但完成状态**从不落库**。种子 qB → 完成 → 同步进 GDrive 这条链路从未被记录成一个关联的生命周期。
- **不存在任何媒体服务器集成**(代码里 `jellyfin|emby|plex|kodi|stash` 全空),也**没有任何消费/观看信号**。

第二个塑造整个设计的结构性事实:**闭环数据是异步产生的,发生在运行结束之后。** Daily 管道在 GitHub Actions runner(`ubuntu-latest` 或 `self-hosted`)上**一次性跑完**,并在运行的*末尾*加种;但完成、落地、观看都发生在*之后*的几分钟到几天。因此闭环不能住在 daily run 里——它需要一个独立的、周期性的对账(reconciliation)过程。

这种失明有三重代价:

1. **去重无法区分"下载中" / "已拥有" / "从未尝试"** —— 它只知道爬虫历史和一份每周的 GDrive 快照。
2. **失败与卡住不可见** —— 一个永远下不完的种子,留不下任何与"成功"可区分的痕迹。
3. **被搁置的偏好模型（[ADR-022](../ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md) / [ADR-025](../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md)）缺了它最强的隐式信号** —— 实际观看行为 —— 因为没有任何东西去读运维者本就在跑的媒体服务器(Emby + Plex)。

本 ADR 分三层闭环,作为一个 umbrella 初始计划统一治理、分期推进(沿用 [ADR-028](../ADR-028-Web-Platform-Completeness-Roadmap/ADR-028-web-platform-completeness-roadmap.md) 的伞型模式)。

## 决策 (Decision)

构建一个**媒体闭环**,分三层——*获取结果 → 拥有真相 → 消费信号*——由新的 D1-canonical enrichment 表与一个异步对账 service 支撑。闭环是可加的:每个新数据源就是一个新收集器,挂在同一个 service 后面;随着源的增加,编排逻辑不变。

### 设计决策 (Design Decisions)

**D1. 三张专用 enrichment 表,而非扩展历史表。** 沿用 [ADR-022](../ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md) 的先例(它新建独立的 `MovieMetadata` 而非加宽 `MovieHistory`),闭环状态住进新表,写入旁路 Pending→Commit 关键路径。`MovieHistory` / `TorrentHistory` 保持为纯去重/追踪表。

```sql
-- Per selected torrent: its real fate after qB.
CREATE TABLE AcquisitionOutcome (
  qb_hash       TEXT PRIMARY KEY,   -- computed from magnet at queue time
  href          TEXT NOT NULL,      -- FK → MovieHistory.Href
  video_code    TEXT,
  category      TEXT,               -- hacked_subtitle | hacked_no_subtitle | subtitle | no_subtitle
  state         TEXT NOT NULL,      -- queued | downloading | completed | in_library | stalled | failed
  queued_at     TEXT,
  completed_at  TEXT,
  landed_at     TEXT,
  last_seen_at  TEXT,
  session_id    TEXT                -- run that queued it (provenance only, not a commit key)
);

-- Multi-source "what do I own" view (superset of RcloneInventory).
CREATE TABLE OwnershipLedger (
  video_code    TEXT NOT NULL,
  source        TEXT NOT NULL,      -- qb | nas | gdrive | pikpak
  category      TEXT,
  path          TEXT,
  size          INTEGER,
  present       INTEGER NOT NULL DEFAULT 1,
  observed_at   TEXT,
  PRIMARY KEY (video_code, source, category)
);

-- Per (server-instance × library): raw consumption signal, never merged on write.
CREATE TABLE ConsumptionSignal (
  video_code          TEXT NOT NULL,
  source_type         TEXT NOT NULL,   -- emby | plex
  instance            TEXT NOT NULL,   -- configured connection id, e.g. plex-home / emby-nas
  library_id          TEXT NOT NULL,
  library_name        TEXT,
  watched             INTEGER,
  progress_pct        INTEGER,
  play_count          INTEGER,
  rating              REAL,
  watched_at          TEXT,
  resolved_confidence TEXT,            -- high | medium | low
  observed_at         TEXT,
  PRIMARY KEY (video_code, instance, library_id)
);
```

**D2. `AcquisitionOutcome` 以 qB hash 为主键,在加种时刻捕获。** uploader 成功加种时,系统用磁链算出 `qb_hash`(复用现成的 `extract_hash_from_magnet`)并写入一行 `state=queued`。这把"选中"与"成功加种"区分开——这是 `TorrentHistory` 目前无法表达的差异——并给对账过程一个稳定的 join key 回连 qB 与 `MovieHistory`。

```
queued ──→ downloading ──→ completed ──→ in_library
   │            │
   └────────────┴──→ stalled ──→ failed   (timeout / error / long-term no progress)
```

当 `video_code` 出现在 `OwnershipLedger`(gdrive/nas)时置 `in_library` —— 这是真正"落地"的证据。

**D3. `completed` 做成*推送*信号,在清理步骤捕获,而非轮询。** 这是承重决策。已完成的种子**会被从 qB 删除**(`remove_completed_torrents_keep_files` 与 file-filter 清理保留文件但移除种子)。一个几小时后才跑的对账过程经常会发现 hash *早已从 qB 消失*,所以"还在不在 qB"无法判定完成。改为:清理步骤——它本就在枚举已完成种子——被插桩,为这些 hash 写入 `state=completed`。对账过程再据此派生 `in_library`(经 Ledger)与 `stalled`/`failed`(还在 qB 但无进度,或超过 N 天既无 `completed` 也无 `in_library`)。被否决的替代方案——高频轮询以赶在删除前抓到完成——脆弱且与清理步骤竞态。

**D4. 对账是一个纯 `Options → Result` service,配只读收集器。** 新模块 `javdb/ops/reconcile/` 暴露 `service.run(ReconcileOptions) -> ReconcileResult`,无 argparse、无 `sys.exit`;`apps/cli/ops/reconcile.py` 是持有进程关切的 CLI adapter。每个外部源是一个**只读 `SourceCollector`**,产出归一化的 `Observation` 且从不写库;**所有 DB 写入集中在 service 一处**。这正是 [ADR-015](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md) 的接缝形态。测试打 `Options → Result`,不打真实 qB/Emby/Plex。

**D5. 双触发,不绑死任何一种部署。** 对账循环**默认**由新的 `ReconcileLibrary.yml` cron workflow 调度(self-hosted runner,有 LAN 访问 qB/Emby/Plex);当可选的 Docker API 后端部署时,它可进程内调用同一个 `service.run(...)` 做近实时对账。两者调用同一份实现。若 Docker 后端未运行,cron 路径不受影响。

**D6. `OwnershipLedger` 是 `RcloneInventory` 的多源超集;dedup 改读 Ledger。** `RcloneInventory` **不**被推倒——它作为 `gdrive` 收集器的落地点保留,Phase 2 把它作为 Ledger 的 `source='gdrive'` 行读取。dedup 检查器迁移为读 Ledger,从而看到全部四个源(qb/nas/gdrive/pikpak),而不只是 GDrive。

**D7. 可插拔的 `MediaServerAdapter` 接缝;Emby + Plex adapter;多实例配置。** 契约是 `list_items(since) -> list[MediaItem]`;`EmbyAdapter`(REST + API key)与 `PlexAdapter`(X-Plex-Token)实现它,且只产出归一化的 `MediaItem`。媒体服务器连接配置成一个**列表**(`MEDIA_SERVERS = [{type, instance, base_url, token, libraries?}, ...]`),让多台同型服务器(如两台 Plex)成为一等公民。凭据存于 config/secrets,经现有 masking 模块脱敏。

**D8. `ConsumptionSignal` 记录到 `(video_code, instance, library_id)` 粒度;合并是派生视图,绝非破坏性写入。** 同一部片在不同服务器、不同库间状态可能不一致。每个 `(instance, library)` 保留自己的行;**原始的逐源信号绝不被另一个源覆盖。** "看没看 / 评分多少"是*派生*查询(`watched = any`、`progress = max`、评分冲突保留所有源行并优先取"有显式评分且最近")。溯源——哪个实例、哪个库说了什么——永久可审计。

**D9. join-key 解析为 best-effort,带置信度与显式 `unresolved` 桶。** 媒体条目带的是文件名/路径,不是 video code。解析逐级降级:(1) 对 `file_path` 复用 `filename_helper` / Rust 解析 → `high`;(2) 对 title/folder 正则兜底 → `medium`;(3) 抽不出 → 落入一个**被计数并 `log()`、绝不静默丢弃的 `unresolved` 桶**(遵守项目的 "no silent caps" 原则)。`resolved_confidence` 落库,以便后续偏好模型按其加权。

**D10. enrichment 写入旁路 session/rollback;幂等 UPSERT;D1-canonical。** 闭环写入是可恢复的 enrichment:每次写入都是按表主键的 UPSERT,`last_seen_at` / `observed_at` 每轮刷新,写入失败下轮重试即可。该循环从不触碰 Pending→Commit 路径。`AcquisitionOutcome.session_id` 仅作溯源。

**D11. 范围是*信号*,不是模型。** 本 ADR 产出 `ConsumptionSignal` 即止;消费它做偏好打分是 [ADR-025](../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md) 的事。这个边界是刻意的,以保持本初始计划可交付、可审计。

## 后果 (Consequences)

### 正面 (Positive)

- **去重诚实** —— 能区分 下载中 / 已拥有 / 从未尝试 / 失败,且横跨四个拥有源,而非一份每周 GDrive 快照。
- **失败与卡住变可见** —— 一条获取漏斗(`queued → completed → in_library`)带显式 `stalled`/`failed` 状态。
- **捕获到最强的隐式偏好信号** —— 来自本就在用的服务器的真实观看行为,解锁被搁置的偏好模型。
- **可加式生长** —— 新源即新收集器;service 编排与 schema 脊柱不变。
- **可审计的溯源** —— 逐源、逐库的原始信号永不丢失。

### 负面 (Negative)

- **多一个需要运维的周期作业** —— `ReconcileLibrary.yml` 需要有 LAN 访问的 self-hosted runner;闭环的新鲜度受其 cron 节奏约束。
- **join-key 歧义是永久的** —— 部分媒体条目会落入 `unresolved`;系统暴露其计数,但无法保证 100% 映射。
- **更多 D1 面** —— 三张新表要迁移、镜像、对账。
- **`completed` 捕获耦合到清理步骤** —— 若清理逻辑变更,完成观察点须随之迁移(作为已知耦合记录在案)。

## 实施路线图 (Implementation Roadmap)

| 阶段 | IMP | 交付内容 | 推迟内容 |
| --- | --- | --- | --- |
| Phase 1 — 获取结果 | [IMP-ADR033-01](IMP-ADR033-01-acquisition-outcome.md) | `AcquisitionOutcome` 表;`reconcile` service/CLI 骨架;`QbCollector`;加种时写 `qb_hash`;清理步骤推送 `completed`;`ReconcileLibrary.yml` cron | NAS/PikPak 收集器;媒体服务器 |
| Phase 2 — 拥有真相 | [IMP-ADR033-02](IMP-ADR033-02-ownership-truth.md) | `OwnershipLedger`;`GDrive`/`Nas`/`Pikpak` 收集器;dedup 迁移为读 Ledger;`in_library` 派生 | 消费信号 |
| Phase 3 — 消费信号 | [IMP-ADR033-03](IMP-ADR033-03-consumption-signal.md) | `MediaServerAdapter` + Emby/Plex adapter;`ConsumptionSignal`(实例×库粒度);join-key 解析 + `unresolved` 桶;多实例配置 | 偏好模型(ADR-025) |

每期独立可上线、可回滚。Phase 1 是地基;Phase 2/3 是"加一个收集器",不改动 service 编排。

**规划节奏。** [IMP-ADR033-01](IMP-ADR033-01-acquisition-outcome.md)（Phase 1）已实现并完成本地验证。**IMP-ADR033-02 与 IMP-ADR033-03 刻意先作为路线图占位**——它们的详细计划将在 Phase 1 之后,用一轮专门的 `grill-me` + `brainstorming` 产出,以纳入 Phase 1 reconcile service 与 `AcquisitionOutcome` 形态在实践中暴露的东西。

### 明确的非目标 (YAGNI)

- **不做偏好模型** —— 只产出 `ConsumptionSignal`(D11)。
- **不做播放设备级追踪** —— 溯源止步于 `(instance, library)`。
- Phase 1 **不做实时事件流 / webhook** —— cron 是默认;Docker 进程内触发是可选;qB/Plex webhook 是将来选项。
- **不重写 spider/parsing** —— join-key 复用 `filename_helper` / Rust 解析。
- **不改 session/rollback** —— 闭环纯 enrichment(D10)。
- **不推倒 `RcloneInventory`** —— Phase 2 把它包装为 `gdrive` 源(D6)。

## 领域语言 (CONTEXT.md 待补充项)

- **Acquisition outcome(获取结果)** —— 被选种子在 qB 之后的真实命运(`queued → downloading → completed → in_library`,或 `stalled` / `failed`)。
- **Ownership ledger(拥有账本)** —— 实际拥有内容的多源视图,按 `(video_code, source, category)` 横跨 qb/nas/gdrive/pikpak。
- **Consumption signal(消费信号)** —— 按 `(video_code, instance, library)` 从媒体服务器拉取的观看/评分证据;最强的隐式偏好信号。
- **Reconciliation pass(对账过程)** —— 从所有源收集观测并 UPSERT 闭环表的异步作业。
- **Collector(收集器)** —— 只读的源 adapter,产出归一化观测且从不写库。

## 备选方案 (Alternatives Considered)

- **给 `TorrentHistory` 加结果列 / 加宽 `RcloneInventory`** —— 否决(D1):污染纯去重/追踪表并扩大 Pending→Commit 爆炸半径,正是 ADR-022 新建 `MovieMetadata` 的同款理由。
- **高频轮询 qB 以赶在删除前抓 `completed`** —— 否决(D3):脆弱、与清理步骤竞态,且仍会漏掉慢速下载。
- **写入时即把 Emby/Plex 信号合并成一行** —— 否决(D8):销毁逐源溯源,使跨服务器冲突不可审计。
- **仅 cron 或仅 Docker 执行** —— 否决(D4/D5):仅 cron 在有后端时失去近实时;仅 Docker 把循环绑死在一个可选的常驻部署上。

## 参考 (References)

- [ADR-022 — User Preference Data Foundation](../ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md)
- [ADR-024 — Torrent Quality Evidence Foundation](../ADR-024-Torrent-Quality-Evidence/ADR-024-torrent-quality-evidence.md)
- [ADR-025 — User Preference Model](../ADR-025-User-Preference-Model/ADR-025-user-preference-model.md)
- [ADR-015 — Integrations Interface Boundary](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md)
- [ADR-010 — D1 Access Port](../_archive/ADR-010-D1-Access-Port/ADR-010-d1-access-port.md)
- [ADR-028 — Web Platform & Capability Completeness Roadmap](../ADR-028-Web-Platform-Completeness-Roadmap/ADR-028-web-platform-completeness-roadmap.md)

## 状态日志 (Status Log)

- 2026-05-29: Proposed(伞型;三期已划定,IMP 待出)。
- 2026-05-29: IMP-ADR033-01（Phase 1）计划已成文;IMP-02/03 推迟到 Phase 1 落地后的
  一轮 `grill-me` + `brainstorming`。web 面拆分到
  [ADR-034](../ADR-034-Media-Closed-Loop-Web-Surface/ADR-034-media-closed-loop-web-surface.md)。
- 2026-05-30: IMP-ADR033-01（Phase 1）已实现并完成本地验证。远端 D1 apply 与本地
  SQLite mirror refresh 仍属于部署环境验证门。
