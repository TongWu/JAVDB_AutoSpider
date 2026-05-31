# BFR-012：MovieMetadata upsert 反射 `detail.__dict__`，而 Rust 解析对象没有该属性

**状态**：Fixed
**日期**：2026-05-31
**严重度**：High
**影响范围**：`javdb/storage/repos/metadata_repo.py`（`MetadataRepo.upsert`）、`javdb/migrations/tools/backfill_movie_metadata.py`（`_process_href`）、`javdb/spider/detail/runner.py`（detail 阶段的 metadata 持久化）
**关联**：[ADR-022](../ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.md)（`MovieMetadata` 表与 `MetadataRepo` 的归属）、[IMP-ADR022-08](../ADR-022-User-Preference-Foundation/IMP-ADR022-08-metadata-backfill.md)（暴露该问题的 metadata 回填）、[BFR-010](../BFR-010-Relative-Href-Inconsistency/BFR-010-relative-href-inconsistency.zh.md)（修复必须保留的嵌套 link href 绝对化）

---

## 现象

Migration workflow 的 `--backfill-metadata` 运行对每个 href 都报写入失败：

```text
⚠ javdb.migrat  [meta-1/1000] https://javdb.com/v/EbvX49 — write_failed: 'builtins.RustMovieDetail' object has no attribute '__dict__'
⚠ javdb.migrat  [meta-2/1000] https://javdb.com/v/G4P12  — write_failed: 'builtins.RustMovieDetail' object has no attribute '__dict__'
```

每次 `MovieMetadata` upsert 都在触达数据库之前就抛错，回填零进展。

## 根因

`MetadataRepo.upsert` 消费的是一个 **mapping**（`detail.get('title')`、
`detail.get('maker')`……）。两个调用点都用 `detail.__dict__` 反射解析结果来构造该 mapping：

- `backfill_movie_metadata.py` —— `MetadataRepo().upsert(href, detail.__dict__)`
- `runner.py` —— `MetadataRepo().upsert(href, movie_detail.__dict__)`

只要安装了 `javdb.rust_core`（生产环境，以及本次 CI runner），`parse_detail_page`
就是 **Rust** 解析器。它返回 `RustMovieDetail` PyO3 对象——每个字段都暴露为 getter，
但**没有 `__dict__`**，访问时抛出 `AttributeError: 'builtins.RustMovieDetail' object
has no attribute '__dict__'`。纯 Python 的 `MovieDetail` dataclass *有* `__dict__`，
所以该 bug 在 Python 回退路径上不可见，只在 Rust 扩展生效时触发。

**非显而易见的副作用——热路径里还有一处潜在（latent）失败。** 同样的 `.__dict__` 写法
也存在于 `runner.py` 的 detail 阶段持久化里，而这正是**日常 spider** 对每部抓取影片都会
走的路径，被 `try/except Exception: logger.debug(...)` 包住——所以在 Rust 解析器下它会在
DEBUG 级别*静默*失败而不浮现。但关键在于：发现该 bug 时，这条路径**尚未在生产跑过**
（完整时间线见"副作用"）：spider 集成（`e055804e`）在 2026-05-30 20:07（+0800）才 merge
进 main，比当晚 daily ingestion 已 checkout 旧 HEAD 的时刻晚约 7 分钟，而修复赶在下一次
cron 之前 landed。因此该缺陷是**潜在的**——若没被及时发现，2026-05-31 20:00 的 cron 就会
开始静默丢弃每部影片的 `MovieMetadata` 写入。是这次回填的显式报错先把它暴露了。

**为什么是设计错误，而不只是"哪里坏了"。** 用 `.__dict__` 把领域对象转成字段 map 本身
就很脆：它假定对象是纯 Python 实现，并把调用点隐式耦合到解析器的后端实现。repo 的文档
其实已写明输入是 "MovieDetail.__dict__ *或等价的 mapping*"，却从不做归一化——它信任每个
调用方自行产出 mapping，而最自然的做法（`obj.__dict__`）恰恰是在 PyO3 对象上会崩的那个。

## 修复

把归一化**收拢进** `MetadataRepo.upsert`，让调用方直接传对象：

- `metadata_repo.py` —— `upsert` 现在同时接受 `Mapping` 或 MovieDetail 风格的对象。
  非 mapping 通过 `{f: getattr(detail, f, None) for f in _UPSERT_FIELDS}` 归一化，
  对 Python dataclass 和 Rust `RustMovieDetail` 表现一致。嵌套 link 字段
  （`maker`/`publisher`/`series`/`directors`/`tags`）被**有意保留为原始对象**
  （Rust/Python 的 `MovieLink`，两者都暴露 `.name`/`.href`），这样 `_link`/`_links`
  仍能对其 href 做绝对化。**明确不使用 `MovieDetail.to_dict()`**——它会把嵌套 link
  压平成 plain dict，从而让 `_link` 走 `json.dumps(obj)` 分支并跳过 BFR-010 的绝对化。
- `backfill_movie_metadata.py` —— `upsert(href, detail.__dict__)` 改为
  `upsert(href, detail)`。
- `runner.py` —— `upsert(href, movie_detail.__dict__)` 改为
  `upsert(href, movie_detail)`。

测试（`tests/unit/test_metadata_repo.py`）：

- 新增 `test_upsert_accepts_object_without_dict`：喂入一个 `__slots__` 对象（真正没有
  `__dict__`，复现 Rust 对象的失败模式），断言行被写入**且**嵌套 link href 被绝对化。
  测试先断言 `not hasattr(obj, '__dict__')`，确保它永远不会悄悄退化成对 dict 的测试。
- 已用真实扩展实测：带嵌套 `RustMovieLink` 的 `RustMovieDetail` 能干净 upsert，
  `maker`/`directors` 的 href 均被绝对化。

## 副作用

**无数据丢失。** 既有的 mapping 调用方（测试传 `_minimal_detail()` dict）不受影响——
`isinstance(detail, Mapping)` 会短路归一化。

证实 spider 路径对生产零影响的时间线：

- `e055804e`（spider metadata 集成）于 2026-05-30 20:07（+0800）merge 进 `main`。
- daily ingestion cron 在 12:00 UTC（20:00 +0800）触发。当晚那次 run 在 merge 前约 7 分钟
  就已 checkout 了旧的 `main` HEAD——其中**不含** `e055804e`——所以它执行的是集成前的代码。
  其结果 auto-commit（`94c30ab1`，20:31）之所以把后续 merge 列为 git 祖先，只是因为 push
  步骤 rebase 到了更新后的 `main`；spider 本身跑的是 20:00 那份 checkout。
  （`merge-base --is-ancestor` 反映的是图拓扑，不是某次 CI run 实际执行的代码。）
- 下一次 cron（2026-05-31 20:00）从未在 bug 下运行——修复于 2026-05-31 10:21（+0800）landed。
- 该窗口内没有 ad-hoc ingestion 运行（运维确认）。

所以 `runner.py` 的 upsert **从未在生产对 `RustMovieDetail` 执行过**——没有 `MovieMetadata`
行丢失。唯一真正撞上 bug 的是 Migration `--backfill-metadata` 运行，而它是*显式*失败
（无静默丢失）；修复后重跑即可填充该表。

## 后续

- [ ] 重跑 Migration `--backfill-metadata` 任务——它曾被此 bug 完全阻塞（1000/1000 href
      `write_failed`），现已成功；这才是真正填充 `MovieMetadata` 的动作。**无需 spider 侧
      回填**——该路径在 bug 期间从未运行（见"副作用"）。
- [ ] 排查其他对解析结果做 `.__dict__` / `vars()` 反射、可能踩到同一 Rust-vs-Python
      分歧的地方。`result_to_dict`（`javdb/spider/html_validators.py`）已通过
      `hasattr(result, "to_dict")` 正确处理；`align_inventory_with_moviehistory.py`
      里的 `r.__dict__` 作用于本地 `BackfillResult` dataclass（安全）。
