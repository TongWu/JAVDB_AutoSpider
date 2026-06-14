# BFR-018：Explore 一键加种静默丢失 pause 与 rename

**状态**：Fixed
**日期**：2026-06-14
**严重度**：Medium
**影响范围**：`apps/api/services/explore_service.py`、`javdb/integrations/qb/client.py`
**关联**：[ADR-038](../ADR-038-Agentic-Operator-MCP/ADR-038-agentic-operator-mcp-surface.md)、[ADR-015](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md)

---

## 症状（Symptom）

Web「探索 → 一键加入 qBittorrent」流程在**所有** qB 服务器版本上静默忽略两个用户设置：

- `AUTO_START=false` 不生效 —— 种子总是立即开始下载，而不是以暂停状态加入。
- 探索 UI 传入的自定义种子标题从未应用 —— qB 用默认名显示该种子。

整个过程不报错;加种返回 HTTP 200、看起来成功。每日 `qb_uploader` / `qb_file_filter` 路径不受影响。

## 根因（Root Cause）

`explore_service._qb_add_magnet` 手写了 `POST /api/v2/torrents/add` 请求，是
`QBittorrentClient.add_torrent`（`javdb/integrations/qb/client.py`）的**部分副本**。
两份副本发生漂移：explore 的 payload 用了字段名 `addPaused` 和 `name`，但 qBittorrent
WebAPI 的字段名是 `paused` 和 `rename`。qB **静默忽略未知表单字段**，于是两个意图都被
丢弃且无任何信号。

canonical 的 `add_torrent` 早已用对了字段名并写了注释（`paused` 而非 `addPaused`；
`rename` 而非 `name`，引用 qB issue #22766）——但 explore 从未调用它，所以这条修正
从未到达此路径。

底层设计缺陷是**加种 wire 契约的重复**：同一个 HTTP 请求有两份副本，只有一份保持正确。
有两个家的契约必然漂移;集成边界（ADR-015）的存在正是为了让 qB API 细节只住在一处。

## 修复（Fix）

`_qb_add_magnet` 现在委托给唯一的 canonical 实现：

```python
client = QBittorrentClient.from_existing_session(session, base_url, request_timeout=...)
client.session.verify = verify_tls   # re-assert this request's QB_VERIFY_TLS
client.add_torrent(magnet, name=title, category=..., save_path=...,
                   auto_tmm=True, skip_checking=..., paused=not AUTO_START)
```

- 复用 `add_torrent` 正确的 `paused` / `rename` 字段名 —— 两个意图现在都生效。
- `from_existing_session` 会把 `session.verify` 对齐到*全局* `qb_verify_tls()`，因此
  之后重新 assert 每请求的 `QB_VERIFY_TLS`，保留 explore 流程的每请求传输行为。
- 新增 `tests/unit/test_explore_qb_add_magnet.py` 钉住修正后的 wire payload
  （`paused`/`rename` 在、`addPaused`/`name` 不在）——此前**零测试覆盖**，正是让漂移
  未被发现的缺口。

## 副作用（Side Effects）

仅 explore 加种路径上的预期行为变更：

- `AUTO_START=false` 现在真的以暂停状态加种。
- 自定义标题现在真的会重命名种子。
- 加种 payload 现在也带上共享 client 的 qB 默认字段（`contentLayout=Original`、
  `ratioLimit=-2`、`seedingTimeLimit=-2`、`downloadPath=""`），与 `qb_uploader` 路径
  一致。这些都是 qB 默认值 —— 无功能变化。

每日 `qb_uploader` / `qb_file_filter` 路径不受影响（它们早已使用 canonical client）。

## 后续（Follow-Up）

- [x] 把 explore 的 qB 加种合并进 `QBittorrentClient.add_torrent`。
- [ ] 继续拆解 `explore_service` god-object（fetch 半部 + 磁链评分），依据架构审查 ——
      qB 关注点是最高价值的一刀、已完成;fetch 关注点是下一个抽取目标。
