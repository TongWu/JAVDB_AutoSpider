# ADR-006: Pending Mode 默认推全 + Audit auto-fallback 退役

**状态**: 已接受 (Accepted)
**日期**: 2026-05-16
**决策者**: 架构深化第二轮（[ADR-005](ADR-005-db-py-retirement-and-repo-pattern.md) 的前置依赖）
**后继触发**: ADR-006 完成后才能启动 ADR-005 的 PR-1

## 修订记录 (Amendments)

- **2026-05-16 amendment 1**：**PR-B 取消**。原计划"把 SQLite schema `WriteMode TEXT DEFAULT 'audit'` 改为 `DEFAULT 'pending'`"被否决——核查发现该 DEFAULT 仅在 v5→v6 migration 与 csv_to_sqlite backfill 两条**历史数据导入路径**上触发，那些路径处理的就是真正的 Audit Mode 历史 session，`'audit'` 是**正确**的标签而非"愿景默认"。普通写入路径（[`db_reports.py:128`](../../../packages/python/javdb_platform/db_reports.py)）始终显式传 `WriteMode`，DEFAULT 永不触发。改 DEFAULT 反而会错标历史数据。Schema DEFAULT 保留为 `'audit'`，作为"未知 WriteMode 时假定为遗留 audit session"的防御性标签。PR 序列从 6 个变 5 个。

- **2026-05-17 amendment 2**：ADR-006 接受后，[ADR-007](ADR-007-monorepo-restructure-2026-05.md) 对 Python namespace 做了重组（`packages/python/javdb_*` → 顶层 `javdb/`）。本 ADR 实施顺序里**尚未合并的 PR**，在 ADR-007 Phase 1 落地后必须按新路径操作：

  - `packages/python/javdb_platform/db_session.py:188` → `javdb/storage/db/db_session.py:188`
  - `packages/python/javdb_platform/db_reports.py:128` → `javdb/storage/db/db_reports.py:128`
  - `scripts/pending_mode_alert_and_pause.py` → `apps/cli/db/pending_alert.py`（ADR-007 Phase 2 搬移）
  - workflow 命令 `python3 -m scripts.pending_mode_alert_and_pause` → `python3 -m apps.cli.db.pending_alert`（同 Phase 2 PR 更新）
  - workflow 命令 `python3 -m scripts.aggregate_pending_health` → `python3 -m apps.cli.db.pending_health`

  纯路径重命名——D1–D5 决策、30 天 bake gate、与 ADR-005 的关系都不变。

---

## 背景 (Context)

ADR-005 起草后立即跑了 D10 Audit Mode 退役安全核查，**两项失败**：

| Gate 项 | 状态 | 实测 |
|---|---|---|
| 近 30 天 `WriteMode='audit'` 计数为 0 | ❌ FAIL | 近 30 天 audit=54 / pending=13；全时段 audit=354 / pending=13 |
| 无孤儿审计行 | ✅ PASS | 0 个 |
| Workflow 7 天前移除 audit 选项 | ❌ FAIL | 3 个 workflow 仍把 `audit` 列为合法值；DailyIngestion 有 auto-fallback 到 audit 的活机制 |

### 同时发现的文档失实

| 来源 | 声称 | 实际 |
|---|---|---|
| CLAUDE.md L88 / CONTEXT.md "Write Mode" | "Pending Mode（默认）" | `db_session.py:188` `return "audit"` 是代码 fallback |
| 同上 | "Audit Mode 已弃用，计划 2026-08-13 下线" | 80% 在线 session 仍是 audit |
| ADR-001 docstring | Phase 3 / pending 已默认 | 实测 audit 是主路径 |

### 为什么这样

- `db_session.py:188` 在无 env var、无 explicit、无配置文件 override 时**默认返回 `'audit'`**——历史遗留：早期 Pending 表上线时为保护新代码先以 audit 为默认。
- `.github/workflows/DailyIngestion.yml:1093` 实现了"critical pending alert 触发后自动 commit `.publish-config.yml` 切回 audit 24 小时"的 **auto-fallback** 机制，由 `scripts/pending_mode_auto_fallback.py` (212 行) 执行。这是 Pending Mode 不稳定时的运维安全网。
- workflow 的 `write_mode_override` input 仍接受 `'audit'` 作为有效值；操作员心智上 audit/pending 仍是 dual options。

ADR-005 的 D2(c) "完全退役 Audit Mode" 假设了文档为真，但 audit 实际是**主路径 + safety net**——硬退会移除安全网且需要修改 80% session 的运行模式。

---

## 决策 (Decision)

把 ADR-005 中 audit 退役的前提工作独立成本 ADR，按以下 4 步推 Pending Mode 到 100%，留出 30 天 bake 期，再放行 ADR-005 D10 gate。

### D1：代码默认改 pending

修改：
- `packages/python/javdb_platform/db_session.py:188` `return "audit"` → `return "pending"`
- 加 unit test 锁定新的 resolution 顺序

**注**：原 D1 还包含"改 SQLite schema `WriteMode` 列 DEFAULT 为 `'pending'`"，已在 amendment 1 中取消。理由见上方修订记录——schema DEFAULT 仅服务历史 migration 路径，`'audit'` 在那里语义正确。运行时默认改由 Python fallback 控制。

### D2：Workflow 默认改 pending

修改 `DailyIngestion.yml` / `AdHocIngestion.yml` / `TestIngestion.yml` 的 `write_mode_override` input：
- `options:` 列表从 `['', 'pending', 'audit']` 改为 `['', 'pending']`（移除 audit 选项；保留 pending + 空字符串）
- 描述文案从 `"... (audit | pending)"` 改为 `"... (pending only)"`
- `.publish-config.yml`（DailyIngestion 用的运行时配置）中 `pending_mode_disabled_until` 字段移除，相关条件分支精简

### D3：重设计 auto-fallback——不切 audit，改告警 + 暂停

`scripts/pending_mode_auto_fallback.py` 重命名为 `scripts/pending_mode_alert_and_pause.py`，行为改为：
- 检测到 critical pending alert 时**不**切 audit
- 改为：发送告警邮件 + 在 `.publish-config.yml` 写入 `pipeline_paused_until: <timestamp+24h>`
- `DailyIngestion.yml` setup 步骤新增 gate：检测到 `pipeline_paused_until` 在未来时直接 `exit 0`（job 成功但跳过运行）
- 操作员检视告警，修复 Pending Mode 根因，手动清掉 `pipeline_paused_until` 重启

**理由**：audit fallback 把 Pending 的失败当"已知风险"忍受了下来；改成"告警+暂停"逼真正修 root cause。"修不动"的话也是显式决策，不能默默落入 audit 路径。

### D4：30 天 bake 期 + 退场验证

D1-D3 部署后，**bake 至少 30 天**，期间运维监控：
- 每日检查 `SELECT COUNT(*) FROM ReportSessions WHERE WriteMode='audit' AND DateTimeCreated > date('now','-1 day')` 应稳定为 0
- 检查 `scripts/pending_mode_alert_and_pause.py` 触发次数——若超过 1 次/月说明 Pending Mode 有未修缺陷
- bake 结束后查询 D10 三项重新通过，作为 ADR-005 启动的前置 sign-off

### D5：bake 期间禁止 ADR-005 任何 PR

ADR-005 的 PR-1（建 Repo 类与函数族并存）虽然不动 audit 路径，但**仍受 bake 期约束**——避免重构与运行模式切换两件事在同一窗口内交叉影响监控数据。bake 完成 + D10 三项 sign-off 后才能启动 PR-1。

---

## 备选方案 (Alternatives Considered)

### 备选 A：保留 audit auto-fallback 永久作为 safety net

**否决原因**：safety net 的存在让 Pending Mode 的根因 bug 永远有"绕开"的退路。维护者发现 alert 时第一反应是"反正 fallback 了，明天修"，bug 永远修不到位。ADR-005 之后 HistoryRepo 要承载这个分支也会破坏 D5（简单签名）。

### 备选 B：保留 audit auto-fallback 但移到 ADR-005 之后

**否决原因**：如果 ADR-005 执行期间 Pending alert 触发，没 fallback 就是 production 中断。bake 期必须在重构前完成，让 Pending Mode 在"无安全网"状态下证明自己可靠。

### 备选 C：bake 期改为 7 天 / 14 天

**否决原因**：现有运行频率约 daily，30 天 = 30 个 daily run + 数次 adhoc，足够覆盖月度 cron / 周末 / 节假日变化。<30 天的样本量在 audit 故障率 1% 数量级时不足以判定稳定。

---

## 实施顺序（PR 序列）

```
PR-A  代码默认改 pending：db_session.py:188 + 新增 tests/unit/test_default_write_mode.py     [已合并 #35]
      验证现有运行不破坏（旧 audit session 仍能完整跑完 commit/rollback）

PR-B  Schema default 切换                                                                    [已取消，见 amendment 1]
      原计划 v14 migration 改 WriteMode 列 DEFAULT 为 'pending'。核查发现 DEFAULT
      仅服务历史 migration / backfill 路径，那里 'audit' 是正确标签。Skip。

PR-C  Workflow 配置改 pending：3 个 workflow 移除 audit 选项 + 描述更新

PR-D  Auto-fallback 重设计：pending_mode_alert_and_pause.py 替代
      pending_mode_auto_fallback.py；DailyIngestion.yml setup 步骤加 pause gate

PR-E  CONTEXT.md / CLAUDE.md / ADR-001 docstring 修正"pending is default"失实陈述           [已合并 #34]
      （独立小 PR，可与 PR-A 并行；不必等 bake）

PR-F  30 天 bake 后的 sign-off PR：在 ADR-005 顶部插入 "ADR-006 sign-off
      完成于 YYYY-MM-DD" 标记，解除 ADR-005 PR-1 启动阻塞
```

每个 PR 独立可回滚。PR-A / PR-C / PR-D 是核心，PR-E 已先行，PR-F 是 bake 期结束的 ceremony。PR-A 与 PR-E 已合并；后续仅剩 PR-C → PR-D → bake → PR-F。

---

## 后果 (Consequences)

### 正面影响

1. **Pending Mode 真正成为主路径**——文档与现实统一
2. **失败模式逼正面解决**——Pending alert 不再有静默后退路径
3. **解锁 ADR-005**——D10 gate 在 bake 后可通过
4. **运维心智模型简化**——"模式"只剩一种，operator 不需要在 audit/pending 间权衡

### 负面影响

1. **30 天 bake 期延后 ADR-005 启动**——若 2026-05-16 落地 ADR-006，最早 2026-06-15 才能开 ADR-005 PR-1
2. **Pending Mode 缺陷会更直接暴露为运维事件**——之前藏在 fallback 里的问题现在变 pipeline pause
3. **手动 pause/resume 流程需要操作员培训**——Runbook 需要更新

### 风险

1. **bake 期内 Pending Mode 出现新缺陷** → pipeline 反复 pause，引发抱怨
   - **缓解**：bake 第一周加密监控；告警阈值放宽，宁可多告警少漏
2. **`.publish-config.yml` 已有的 `pending_mode_disabled_until` 字段被外部脚本读** → 删除引发故障
   - **缓解**：grep 确认仅 DailyIngestion 自身和 `pending_mode_auto_fallback.py` 引用；外部无依赖
3. **取消 PR-B 后 schema DEFAULT 与运行时默认错位**（schema 仍 `'audit'`、运行时默认 `'pending'`）
   - **影响**：仅在那两条历史 migration 路径上 DEFAULT 触发；语义上"未知历史 session" 仍被标 audit，正确
   - **缓解**：amendment 1 已记录决策理由；未来若代码新增不传 WriteMode 的 INSERT，需显式补值

---

## 相关决策 (Related Decisions)

- **后继**：[ADR-005](ADR-005-db-py-retirement-and-repo-pattern.md) — bake 完成后启动
- **修正历史承诺**：[ADR-001](ADR-001-split-db-module.md) Phase 3 中"Pending Mode 默认"的承诺由本 ADR 真正兑现

---

## 参考资料 (References)

- [CONTEXT.md](../../../CONTEXT.md) — Write Mode 章节
- D10 核查所用 SQL：
  ```sql
  SELECT WriteMode, COUNT(*) FROM ReportSessions
  WHERE DateTimeCreated > datetime('now','-30 days') GROUP BY WriteMode;
  ```
- 现有 auto-fallback 实现：[`.github/workflows/DailyIngestion.yml:1075-1110`](../../../.github/workflows/DailyIngestion.yml)、[`scripts/pending_mode_auto_fallback.py`](../../../scripts/pending_mode_auto_fallback.py)
- 现有默认实现：[`packages/python/javdb_platform/db_session.py:185-188`](../../../packages/python/javdb_platform/db_session.py)
