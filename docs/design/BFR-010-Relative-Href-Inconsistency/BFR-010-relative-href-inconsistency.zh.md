# BFR-010：演员 / 影片 href 存储格式不统一(相对 vs 绝对)

**Status**: Fixed
**Date**: 2026-05-31
**Severity**: High
**Affected**: `javdb/storage/db/_db_history_write.py`、`javdb/storage/repos/metadata_repo.py`、`javdb/migrations/tools/absolutize_javdb_urls_in_history.py`、`javdb/migrations/tools/backfill_movie_metadata.py`
**Related**: [ADR-022](../ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md)(MovieMetadata)、CONTEXT.md → “Href”

---

## 症状 (Symptom)

在 Migration workflow 跑 ADR-022 `MovieMetadata` 回填时,每个详情页抓取都
`fetch_failed: empty response`。一次排查暴露出两个独立问题:

1. **回填 URL 双拼** —— 回填用 `base_url + href` 构造 URL,但 `MovieHistory.Href`
   存的是绝对 URL,于是请求了 `https://javdb.comhttps://javdb.com/v/..`(永远解析
   不了)。当天已在回填工具中单独修复(URL 构造 + CF bypass + 解析门槛)。

2. **D1 内 href 格式混存** —— 对三个 D1 库中*所有*含 href 的列做兜底审计,发现
   `MovieHistory` 有两列在**同一列内**混存相对/绝对:

   | 列 | 绝对 | 相对(`/actors/..`) | 空 |
   | --- | --- | --- | --- |
   | `MovieHistory.ActorLink` | 38,830 | **849** | 779 |
   | `MovieHistory.SupportingActors`(JSON 内层 `link`) | 26,613 | **605 行** | — |

   其余有数据的 href 列(`MovieHistory.Href`、`ReportMovies.Href`、
   `ReportSessions.Url`、`SpiderStats.FailedMovies`)均为绝对。

## 根因 (Root Cause)

解析器**有意**产出站内相对路径:`MovieDetail.get_first_actor_href()` 和
`get_supporting_actors_json()` 返回 `normalize_javdb_href_path(...)`(如
`/actors/x`)。代码约定是“在 DB 写入层做绝对化”。

daily 写入链路先经 `db_stage_history_write()` 把解析结果写入
`PendingMovieHistoryWrites`,再由 commit/overlay 路径(`_commit_session_bulk`)
落入 `MovieHistory`。commit 路径**绝对化了 `Href`**
(`normalized_href = abs_href or href`),但 `ActorLink` / `SupportingActors`
是从 pending 行**原样拷贝**的——而 stage 这一步也没有绝对化。于是新提交的 daily
行保留了相对 actor link。

其它写入方——actor 回填(`batch_update_movie_actors`)、legacy audit upsert
(`_upsert_one_history_on_conn`)、库存对齐(`db_upsert_history`)——本就做了绝对
化,这正是*大多数*行为绝对的原因。少数相对行是 actor 列引入后、由 daily 提交且
从未被重新归一化的行。

**前向风险(ADR-022 `MovieMetadata`)。** MovieMetadata 的写入方——daily runner
的 `MetadataRepo().upsert(href, ...)` 与回填——把影片 `href` 主键以及内嵌链接字段
(`maker` / `publisher` / `series` / `directors` / `categories`,其中带相对 href)
**未做绝对化**就写入。`MovieMetadata` 当时为空,但一旦写入会:(a) 使
`MovieMetadata.href` 及 JSON 链接载荷格式混乱;(b) 破坏回填 join
`mm.href = mh.Href`(相对 vs 绝对),导致每一行都被误判为“未回填”。

## 修复 (Fix)

- **Stage 层归一化(MovieHistory)。** `db_stage_history_write()` 现在在写入
  `PendingMovieHistoryWrites` 前用 `javdb_absolute_url` /
  `absolutize_supporting_actors_json` 绝对化 `ActorLink` 和 `SupportingActors`。
  由于所有 commit 变体都原样拷贝 pending 行,且 `db_stage_history_write` 是该表的
  **唯一**写入方,这个单一 chokepoint 保证提交后的
  `MovieHistory.ActorLink` / `SupportingActors` 为绝对——与 commit 路径已产出的
  绝对 `Href` 一致。
- **MovieMetadata 写入(前向风险)。** `MetadataRepo.upsert()` 现在绝对化 `href`
  主键及全部内嵌链接(`maker`/`publisher`/`series`/`directors`/`categories`)。
  `MetadataRepo.get()` 对查找键做对称绝对化,调用方传相对或绝对皆可,且回填 join
  键与 `MovieHistory.Href` 保持一致。
- **D1 数据修正。**
  `javdb/migrations/tools/absolutize_javdb_urls_in_history.py` 重构为
  **backend-aware**(走 `get_db`,`STORAGE_BACKEND=d1` 即作用于 canonical D1),
  且只取站内相对的*候选*行,分块、逐块 auto-commit 重写。对 D1 执行结果:
  **MovieHistory 修正 849 行**(相对 → 绝对);ReportMovies 0(本就干净)。

## 副作用 (Side Effects)

- 此后 `MovieMetadata.href` 及其内嵌链接 JSON 均存绝对 URL。API 层
  (`apps/api/routers/preferences.py`)将其透传给前端;此前期望
  `maker`/`publisher`/`series`/`directors`/`categories` JSON 内为相对链接的消费方
  现在会收到绝对 URL。这是预期的一致化行为。修复时 `MovieMetadata` 为空,无既有
  数据受影响。
- 两个既有 `MetadataRepo` 单测已更新为断言绝对内嵌链接。
- 已是绝对的列(`MovieHistory.Href`、`ReportMovies.Href` 等)不变。

## 后续 (Follow-Up)

- [x] 归一化 D1 现存 `MovieHistory.ActorLink` + `SupportingActors`
  (849 行,2026-05-31 执行)。
- [ ] 方便时从 D1 重对齐本地 SQLite 镜像
  (`python3 -m apps.cli.db.sync_d1_to_sqlite --apply --force-overwrite-all`)
  ——本地镜像正在退役,非阻塞。
- [ ] 考虑加一个契约测试:若任何新写入方把站内相对 JavDB href 写入
  `MovieHistory` / `MovieMetadata` 则失败。
