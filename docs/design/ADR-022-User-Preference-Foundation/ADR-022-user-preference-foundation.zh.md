# ADR-022：用户偏好数据基础层

**状态：** 提案  
**日期：** 2026-05-26  
**作者：** Ted  

---

## 背景

JAVDB AutoSpider 当前完全依赖基于规则的启发式逻辑运行，对用户偏好没有任何感知。详情页解析器（`javdb/parsing/`）已经能够提取丰富的 `MovieDetail` 数据类——包括类别、导演、片商、发行商、系列、评分、想看/看过人数等——但**除演员数据外，所有字段在到达数据库之前全部被丢弃**。

这导致：

1. 系统无法根据用户口味调整下载决策。
2. 已经解析的有价值元数据（类别、导演、片商）在每次运行时被静默丢弃。
3. 没有机制让用户对单部影片或内容维度（演员、类别、片商）表达偏好。

本 ADR 的目标是建立**数据基础层**，为未来的偏好模型提供数据支撑。模型训练本身推迟到 ADR-B，在积累足够评分数据之前无法进行有意义的模型架构设计。

---

## 决策

### 1. 将所有有价值的 MovieDetail 字段持久化至新的 `MovieMetadata` 表

不扩展 `MovieHistory`（一张流经 Pending→Commit 会话路径的去重/追踪表），而是创建独立的 `MovieMetadata` 表，在详情页解析完成后直接写入，绕过会话提交流程。

**选择独立表的理由：**
- `MovieHistory` 位于 Pending→Commit 关键路径上；在其上加列需要同步修改 `PendingMovieHistoryWrites`、`HistoryRepo` 及会话回滚逻辑——对于本质上只是补充信息的字段而言，影响范围过大。
- 元数据写入失败可恢复：若 `MovieMetadata` UPSERT 失败，下次爬取同一 `href` 时会自动重试，不影响会话完整性。
- 职责分离：`MovieHistory` 保持纯粹的去重/追踪职能，`MovieMetadata` 承担内容丰富化职能，各有清晰的单一职责。

**Schema：**

```sql
CREATE TABLE MovieMetadata (
  href              TEXT PRIMARY KEY,  -- FK → MovieHistory.Href
  title             TEXT,
  video_code        TEXT,
  release_date      TEXT,              -- ISO 8601，例如 "2025-10-28"
  duration_minutes  INTEGER,
  rate              REAL,              -- 例如 4.2
  comment_count     INTEGER,           -- 评分人数
  review_count      INTEGER,           -- 短评数
  want_count        INTEGER,           -- 想看人数
  watched_count     INTEGER,           -- 看过人数
  maker             TEXT,              -- JSON {"name": ..., "href": ...}
  publisher         TEXT,              -- JSON {"name": ..., "href": ...}
  series            TEXT,              -- JSON {"name": ..., "href": ...}
  directors         TEXT,              -- JSON [{"name": ..., "href": ...}, ...]
  categories        TEXT,              -- JSON [{"name": ..., "href": ...}, ...]
  poster_url        TEXT,
  fanart_urls       TEXT,              -- JSON ["url", ...]
  trailer_url       TEXT,
  created_at        TEXT,
  updated_at        TEXT
);
```

**有意排除的字段：**
- `code_prefix_link`：仅用于页面导航，无分析价值。
- 用户评论正文：体量大、噪声高，保留给未来的 NLP ADR。
- 用户收藏列表成员：用户维度数据，不属于内容元数据。

**写入策略：** 详情页解析完成后直接执行 UPSERT（`INSERT OR REPLACE`），在 Pending→Commit 流程之外运行。写入失败时静默处理，下次爬取自动重试。

---

### 2. 新增两张用户评分与内容偏好表

**`MovieRatings`** — 用户对单部影片的显式评分：

```sql
CREATE TABLE MovieRatings (
  href         TEXT PRIMARY KEY,   -- FK → MovieHistory.Href
  video_code   TEXT NOT NULL,
  rating       INTEGER,            -- 1–5；NULL 表示未评分
  tags         TEXT DEFAULT '[]',  -- JSON 数组，存预定义标签 slug
  notes        TEXT,               -- 自由文本备注
  rated_at     TEXT,
  updated_at   TEXT
);
```

**`ContentPreferences`** — 按维度（演员 / 类别 / 片商 / 导演）的偏好设置：

```sql
CREATE TABLE ContentPreferences (
  content_type  TEXT NOT NULL,   -- 'actor' | 'category' | 'maker' | 'director'
  content_id    TEXT NOT NULL,   -- href slug 或标准化名称
  content_name  TEXT NOT NULL,
  hearted       INTEGER DEFAULT 0,  -- 1 = 已红心
  weight        REAL DEFAULT 1.0,   -- 保留给 ADR-B 爬取优先级使用
  PRIMARY KEY (content_type, content_id)
);
```

---

### 3. 预定义标签词汇表（12 个标签，3 组）

标签以 slug JSON 数组形式存储在 `MovieRatings.tags` 中，UI 按维度分组渲染为多选 chip 组件。

| 分组 | Slug | 显示名 |
|------|------|--------|
| 画质 / 技术质量 | `quality_high` | 画质优秀 |
| | `quality_low` | 画质差 |
| | `resolution_bad` | 分辨率不足 |
| | `encoding_bad` | 编码问题 |
| 内容偏好 | `plot_good` | 剧情好 |
| | `actress_standout` | 女主出色 |
| | `not_my_type` | 不合口味 |
| | `category_miss` | 类别标错/不符 |
| 收藏 / 决策 | `would_rewatch` | 值得重看 |
| | `keep_long_term` | 长期保留 |
| | `delete_candidate` | 可以删除 |
| | `upgrade_wanted` | 希望找更好版本 |

词汇表以常量形式定义在 Python 后端，并在 TypeScript 后端同步镜像；添加或重命名标签时两端必须同步更新。

---

### 4. 评分交互模式

| 模式 | 位置 | 交互方式 |
|------|------|----------|
| **C1 — 内联评分** | `/data` 页面（MovieHistory 浏览器） | 每行显示星级组件 + 标签 chip + 备注字段；失焦/提交时保存 |
| **C3 — 批量标注** | `/data` 页面，切换"标注模式" | 键盘驱动：`j`/`k` 上下导航，`1`–`5` 评分，`Space` 跳过，`Enter` 保存并前进 |
| **C4 — 维度红心** | `/data` 页面的演员/类别/片商/导演 chip | 红心图标切换 → 写入 `ContentPreferences` 行；所有显示这些维度的页面均可见 |
| **C2 — 邮件提示** | Pipeline 通知邮件 | 推迟至后续增强 |

---

### 5. 下游消费（ADR-B 前使用基于规则的占位逻辑）

**B2 — 上传过滤 hook：**  
在 qBittorrent 上传决策路径中加入偏好门控。在 ADR-022 阶段，门控使用简单规则：若影片主演在 `ContentPreferences` 中有 `hearted = false` 的明确记录，则跳过上传。该 hook 点已按接口设计，ADR-B 可以直接用模型分数替换此规则，无需额外重构。

**B3 — 控制台偏好分展示：**  
`/data` 和 `/browse` 页面在每条历史记录旁显示计算出的偏好分。在 ADR-022 阶段，分数采用加权平均的规则计算：

```
score = (movie_rating / 5.0) * 0.5
      + (actor_hearted ? 1.0 : 0.5) * 0.3
      + (category_match_ratio) * 0.2
```

此基于规则的分数是占位逻辑；ADR-B 将用训练好的模型输出替换它。

**B1 — 动态爬取优先级调整：**  
推迟至 ADR-B。`ContentPreferences` 表中的 `weight` 列为此功能预留。

---

## 阶段边界

| 能力 | ADR-022（本 ADR） | ADR-B（推迟） |
|------|-----------------|--------------|
| `MovieMetadata` 表 + 解析器接入 | ✅ | — |
| `MovieRatings` + `ContentPreferences` 表 | ✅ | — |
| C1 内联评分 UI | ✅ | — |
| C3 批量标注 UI | ✅ | — |
| C4 维度红心 | ✅ | — |
| B2 上传过滤 hook（规则占位） | ✅ | ML 模型替换规则 |
| B3 偏好分展示（规则占位） | ✅ | ML 模型替换规则 |
| B1 爬取优先级动态调整 | — | ✅ |
| ML 模型训练流水线 | — | ✅ |
| 模型服务 / 推理 | — | ✅ |
| C2 邮件评分提示 | — | ✅ |

ADR-B 应在通过 C1/C3 收集到 ≥ 200 条影片评分后再撰写，届时才有足够信号支撑有意义的模型架构决策。

---

## 迁移

三张新表（`MovieMetadata`、`MovieRatings`、`ContentPreferences`）均**优先在 D1 上创建**，通过 `javdb/migrations/d1/` 下的新迁移文件完成。SQLite 随后重新对齐：

```bash
python3 -m apps.cli.db.sync_d1_to_sqlite --apply --force-overwrite-all
```

`MovieMetadata` 表不参与 Pending→Commit 会话流程。`MovieRatings` 和 `ContentPreferences` 均为用户主动写入，同样在会话流程之外。

---

## 已考虑的替代方案

### A — 直接扩展 `MovieHistory` 表加列

在 `MovieHistory` 上直接添加 `Categories`、`Directors`、`Maker` 等列。

**已拒绝**：`MovieHistory` 位于 Pending→Commit 关键路径上，加列需要同步修改 `PendingMovieHistoryWrites`、`HistoryRepo.stage_movie()` 及会话回滚逻辑。对于写入失败无害的补充数据而言，影响范围过大。

### 无独立 `ContentPreferences` 表——在 `MovieRatings` 标签中编码偏好

将演员/类别偏好编码为特殊标签（例如 `actor_hearted:EvkJ`）。

**已拒绝**：偏好是维度级别的（适用于某演员参演的所有影片），而非影片级别的。独立表配合类型化的 `content_type` 列，使查询和未来 ML 特征提取更为直接。

---

## 影响

**正面：**
- 所有有价值的详情页字段从第一次实施起即被保留，避免在撰写 ADR-B 时进行第二轮 schema 迁移。
- 偏好数据模型简单，无需 ML 基础设施即可直接查询。
- `MovieHistory` 保持纯粹的去重/追踪职责，不增加额外复杂度。
- B2 和 B3 从第一天起即以基于规则的逻辑提供实际效用，同时在后台积累评分数据。

**负面 / 权衡：**
- `MovieMetadata` 在会话流程之外写入，运行失败时可能留下不完整的元数据。可接受——元数据仅为补充信息，下次爬取时自动重试。
- 基于规则的 B2/B3 占位逻辑是会被 ADR-B 替换的临时代码；这是有意为之的脚手架，而非浪费。
- TypeScript 后端（`javdb-autospider-web/server/`）必须在同一 PR 或紧跟的后续 PR 中更新，以通过共享 D1 查询层暴露 `MovieMetadata`、`MovieRatings` 和 `ContentPreferences`。

---

## 相关文档

- [ADR-005](../_archive/ADR-005-Db-Py-Retirement/ADR-005-db-py-retirement.md) — Pending 写入流程
- [ADR-011](../_archive/ADR-011-Parsing-Module/ADR-011-parsing-module.md) — 解析模块结构与 `MovieDetail` 数据类
- [ADR-014](../ADR-014-Storage-Cli-Layering/ADR-014-storage-cli-layering.md) — 存储 / CLI 分层
- [ADR-019](../ADR-019-Web-Feature-Parity/ADR-019-web-feature-parity.md) — Web 控制台功能对齐
- ADR-B（用户偏好模型）— 尚未撰写；依赖本 ADR
