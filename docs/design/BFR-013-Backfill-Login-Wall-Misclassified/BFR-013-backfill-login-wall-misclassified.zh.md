# BFR-013：MovieMetadata 回填把登录墙误判为 `parse_failed`

**状态**：Fixed
**日期**：2026-05-31
**严重度**：Medium
**影响范围**：`javdb/migrations/tools/backfill_movie_metadata.py`（`_process_href`、`run_backfill_metadata`）
**关联**：[ADR-022](../_archive/ADR-022-User-Preference-Foundation/ADR-022-user-preference-foundation.zh.md)（`MovieMetadata` 表归属）、[IMP-ADR022-08](../_archive/ADR-022-User-Preference-Foundation/IMP-ADR022-08-metadata-backfill.md)（该回填）、[BFR-012](../BFR-012-RustMovieDetail-No-Dict-Metadata-Upsert/BFR-012-rustmoviedetail-no-dict-metadata-upsert.zh.md)（同一次运行暴露的 `__dict__` 崩溃）

---

## 现象

Migration `--backfill-metadata` 运行把一部需要登录的影片报成了解析失败：

```text
⚠ javdb.migrat  [meta-8/1000] https://javdb.com/v/a2nq3 — parse_failed: no metadata fields parsed
```

`/v/a2nq3` 需要登录才能查看。运维从日志看不出页面是真坏了还是只是缺登录 cookie——两者都被
归为 `parse_failed`。

## 根因

回填直接通过 `spider_state.get_page(...)` 抓取 detail 页，**绕过了 spider 的 `FetchEngine`**。
这留下了正常 spider 路径本会覆盖的两个缺口：

1. **未认证抓取。** `_process_href` 用裸 `requests.Session()`，且调用 `get_page(...)` 时
   **没有传 `use_cookie=True`**（默认 `False`）。request handler 仅在 `use_cookie` 为真时才
   附加 `_jdb_session` cookie（`javdb/infra/request.py` —— `if use_cookie and
   self.config.javdb_session_cookie: headers['Cookie'] = ...`）。所以登录影片返回的是登录墙，
   没有任何 metadata 字段。

2. **没有登录墙检测。** `_process_href` 只判断 `if not (video_code or title): parse_failed`，
   代码里**根本没有 `login_required` 的概念**——任何缺字段的页面都成了 `parse_failed`
   （空响应则 `fetch_failed`）。同目录的 `align_inventory_with_moviehistory.py` **能**区分
   `login_required`，因为它走 `FetchEngine`，其 `ctx.fetch` 在登录页（由 `is_login_page`
   检测）会 `raise LoginRequired`。回填走的是裸 `get_page` 路径，从未收到这个信号。

**为什么是设计错误，而不只是"哪里坏了"。** detail 页存在两条抓取路径：登录感知的
`FetchEngine`（daily/ad-hoc ingestion 使用）和裸 `get_page`（一次性工具使用）。回填为了简单
选了裸路径——本身合理——但悄悄地一点登录处理都没继承，于是一个可恢复、已被充分理解的状况
（cookie 过期 / 内容门控）变得与真正的解析失败无法区分。

### daily / ad-hoc ingestion 会发生同样的失败吗？

不会——二者都走 `FetchEngine` 并显式处理登录：

- **Ad-hoc ingestion** 认证抓取（`run_service.py` 里 `use_cookie = custom_url is not None`
  ⇒ `True`），登录影片的 detail 页正常渲染、正常解析。
- **Daily ingestion** *未认证*抓取（`use_cookie = False`，与旧回填相同），所以会撞上同样的
  登录墙——但 `ctx.fetch` 会调 `is_login_page(html)` 并 `raise LoginRequired`，由 login
  coordinator 执行真实登录并带 cookie 重试。所以 daily 是**识别并恢复**，而不是把页面误标为
  `parse_failed`。（caveat：检测依赖 `is_login_page` 的特征——`<title>` 含 `登入`/`login`，
  或版权限制文案——所以一个 `<title>` 正常、仅内容隐藏的登录墙仍会被解析为空。）

只有回填两样都没有：既不认证，也不检测。

## 修复

方案 A——让回填登录感知（补齐两个缺口），对齐 ad-hoc spider 的认证抓取：

- **认证**：给 `get_page` 传 `use_cookie=True`，附加已配置的 `JAVDB_SESSION_COOKIE`，使登录
  影片产出 metadata。cookie 仅在已配置时附加，所以未配置/空 cookie 会降级为未认证抓取（再由
  下一点处理）。
- **检测**：非空抓取后调 `is_login_page(html)`；命中则返回独立的 `login_required` 结果，附带
  "刷新 `JAVDB_SESSION_COOKIE`" 的提示，而不是 `parse_failed`。
- **上报而非失败**：`run_backfill_metadata` 单独统计 `login_required`（不算 hard failure——
  页面没问题、是会话过期），逐条 warning，并用结构化的 `log_summary_block`（ok / failed /
  login-gated / total）输出汇总，附带运行 `python3 -m apps.cli.login` 后重跑的提示。job 退出
  码仍只取决于真正的 `failed`。

**检测是 best-effort；`use_cookie=True` 才是承重的改动。** cookie 有效时根本不会出现登录墙，
影片直接被抓取。`is_login_page` 分类只在登录 HTML 真正回传到 `_process_href` 时才生效——
`--no-proxy` 直连路径，或大到能越过 CF-bypass 尺寸闸门的登录页。在默认的代理 CF-bypass 路径下，
request handler 会把小登录页吞成 `None`（`javdb/infra/request.py` 自己的 "Last response
appears to be a login page" 分支），所以那里过期/缺失 cookie 仍表现为 `fetch_failed` 而非
`login_required`。把登录信号透传穿过共享的 fetch 层——或让回填走 `FetchEngine`——能让分类穷尽，
但对一个一次性工具不成比例；认证抓取本身已经修好了报告的症状。

测试（`tests/unit/test_backfill_movie_metadata_fetch.py`）：

- `test_process_href_fetch_authenticates_with_session_cookie` —— 断言抓取请求 `use_cookie=True`。
- `test_process_href_login_wall_is_login_required` —— 喂入真实登录标题页
  （`<title>登入 …</title>`），经真实 `is_login_page` 检测器，断言 `login_required`，且
  parse/upsert 被短路跳过。

## 副作用

无负面影响。`login_required` 是新增的非致命结果状态；既有的
`ok`/`parse_failed`/`fetch_failed`/`write_failed` 分类不变。认证抓取现在能为未认证回填永远拿
不到的登录影片捕获 metadata。

## 后续

- [ ] 上线后，用新的 `JAVDB_SESSION_COOKIE` 重跑 `--backfill-metadata`，回填此前报
      `parse_failed` 的登录影片。
- [ ] 其他通过裸 `get_page`（而非 `FetchEngine`）抓取的一次性工具有同样的盲区——在它们需要
      登录门控内容时按需审查。`is_login_page` 是可复用的检测器。
