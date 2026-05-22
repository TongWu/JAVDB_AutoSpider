# BFR-001: Login state DO publish 因 proxy_name_mismatch_with_lease 失败（409）

**Status:** Open
**Date:** 2026-05-22
**Severity:** Medium
**Affected:** `javdb/spider/fetch/login_coordinator.py`
**Related:** DO handler `JAVDB_AutoSpider_Proxycoordinator/src/global_login_state.ts:469-482`

---

## 症状

当 spider 在多 proxy 登录流程中回退到另一个 proxy 时，跨 runner 的 cookie 广播失败，返回 HTTP 409：

```
13:59:21  ⚠ Session  Failed to publish login state to DO (proxy=Singapore-ARM1): HTTP 409:
  {"error":"proxy_name_mismatch_with_lease","lease_target_proxy_name":"Miraculous Fortress"}
```

Cookie 在当前 runner 本地可用，但其他 runner 无法复用，必须各自独立登录——浪费登录预算，并增加触发 JavDB 频率限制的风险。

## 根因

`_find_and_login_next_worker_with_lease` 获取 DO lease 时使用
`target_proxy_name = hint_proxy_name`（调用者自身的 proxy）。但两个调用点
（`handle_login_required` 第 961 行和第 1048 行）都将 hint proxy 放入 `exclude` 集合：

```python
next_wid, parked = self._find_and_login_next_worker_with_lease(
    exclude={worker.proxy_name},        # hint 被排除
    hint_proxy_name=worker.proxy_name,  # hint == 被排除的 proxy
)
```

这保证了登录必然通过一个**不同的** proxy 成功。在迭代内部，`_login_and_verify`
使用 `proxy_name = actual_proxy` 进行 publish。DO 的 `handlePublish` 校验正确地拒绝了请求，因为 `actual_proxy ≠ lease.target_proxy_name`。

设计缺陷在于：lease 的 `target_proxy_name` 被当作"诊断提示"使用，但 DO 端将其作为合约强制执行。多 proxy 路径从未提供在发现实际成功 proxy 后更新 lease 的机制。

单 proxy 路径（`_login_and_verify_with_lease`）不受影响，因为 hint 始终等于 actual。

## 修复

范围：仅 Python 端（`login_coordinator.py`）。DO 端的校验逻辑正确，保持不变。

### 变更 1：`_login_and_verify` — 增加 `defer_publish` 参数

```python
def _login_and_verify(self, worker, *, defer_publish: bool = False) -> tuple[bool, str | None]:
```

当 `defer_publish=True` 时，跳过两处 `_publish_login_state_to_do` 调用（第 629 行和第 637 行）。默认值 `False` 保持对单 proxy 路径 `_login_and_verify_with_lease` 的向后兼容。

### 变更 2：`_find_and_login_next_worker` — 返回 cookie，传递 `defer_publish`

签名从 `-> int | None` 改为 `-> tuple[int | None, str | None]`。

```python
verified, new_cookie = self._login_and_verify(w, defer_publish=True)
# ...
return w.worker_id, new_cookie
```

### 变更 3：`_find_and_login_next_worker_with_lease` — release → re-acquire → publish

`_find_and_login_next_worker` 返回成功后：

1. 释放旧 lease（target = hint proxy）
2. 重新获取新 lease（target = actual proxy），使用原始 `client.acquire_lease`
   ——不使用 `_try_acquire_login_lease`，以避免 cooldown/park 副作用
3. Publish cookie → 释放新 lease

任何失败（其他 runner 抢到 lease、网络错误等）都记录 warning 并跳过 publish。**Fail-open**：cookie 在本地已经可用。

新增辅助方法 `_reacquire_and_publish` 封装步骤 2–3。

### 错误处理

| 场景 | 行为 |
|------|------|
| 竞态窗口中另一个 runner 抢到 lease | `acquired=False` → 记录 warning，跳过 publish |
| 重新获取 lease 网络超时 | `LoginStateUnavailable` → 记录 warning，跳过 publish |
| Publish 本身失败 | 现有 `_publish_login_state_to_do` 的 warning 逻辑兜底 |
| 重新获取成功但 publish 前 lease 过期 | DO 返回 409 `lease_required` → 同上 |

### 测试

在 `tests/unit/test_login_coordinator_park.py` 中新增：

- `TestDeferPublish` — `defer_publish=True` 跳过 DO publish；默认值仍触发 publish
- `TestFindAndLoginNextWorkerReturnsCookie` — 新返回类型 `(worker_id, cookie)`；传递 `defer_publish=True`
- `TestFindAndLoginNextWorkerWithLeaseReacquire` — 正常路径（release → re-acquire → publish → release）；re-acquire 被拒；网络错误；未配置 DO；登录失败（不触发 re-acquire）

## 副作用

无。单 proxy 路径（`_login_and_verify_with_lease`）和所有外部调用者（`handle_login_required`）均不受影响。`_find_and_login_next_worker` 的返回类型变更是内部的——唯一消费者是 `_find_and_login_next_worker_with_lease`。

## 后续工作

- [ ] 实现修复（上述 3 个变更 + 测试）
- [ ] 在下一次 AdHoc/Daily ingestion 运行中验证 409 不再出现，cookie 成功广播
