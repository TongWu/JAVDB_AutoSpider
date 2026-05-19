# ADR-009：D1 瞬时错误分类器修复 + Drift 诊断工具

**状态**：已接受 —— 实现待启动（截至 2026-05-19 尚无 PR）
**日期**：2026-05-17
**决策者**：bake 期 drift 响应（承接 2026-05-17T14:00 UTC 记录在 `reports/D1/d1_drift.jsonl` 中 `kind: drift_resolution` 的手动 forensic 修复）
**前置**：无——按 [ADR-006](ADR-006-pending-mode-default-rollout.md) amendment 3 属 bake 安全。"bake 安全"准确含义是：**对 D10 gate 输入无影响**（不写 D1/SQLite、不改 schema、不改 `WriteMode` 解析、不动 `.publish-config.yml` pause 机制、不发 `pending_session_verify` 行）。Layer 1 的 D6 *确实*修改 `email-notification` job，但修改内容是带 60 秒 timeout 的只读 subprocess 调用，被调工具自身只在操作员手动 `--apply` 路径才会触碰 D10 监控状态（workflow 内**绝不**触发 `--apply`）。

## 待办 (Outstanding Work)

- **D1 (Layer 0)** —— 在 [`javdb/storage/d1_client.py`](../../../javdb/storage/d1_client.py) 的 `_TRANSIENT_ERROR_KEYWORDS` 中加入 `"connection lost"` + 回归用例。**尚未应用** —— 当前关键字元组不含该子串。
- **D2 (Layer 1)** —— `drift_diagnose` CLI（位于 `apps/cli/db/drift_diagnose.py`）。**尚未创建** —— `apps/cli/db/` 目录下没有该模块。
- **D6** —— email job subprocess 集成，依赖 D2。

本 ADR 没有配套 IMP（按 D7 划分，工作量适合小型 PR 序列直接落地）。

---

## 背景

2026-05-17 12:28:04 UTC，ADR-006 bake 期间，`dual_connection` 抛出 drift advisory：

```
db: history
committed: true
failure_count: 1
first_failed_sql: UPDATE PendingTorrentHistoryWrites SET ApplyState='applied' WHERE Seq IN (?,?)
first_error: D1PermanentError: D1 API returned HTTP 400:
             [{'code': 7500, 'message': 'Network connection lost.'}]
```

手动 forensic 确认：

- Session `20260517T121617.445400Z-ea87-0000`（run `25990538491`，attempt 1）已 `Status='committed'`。
- 本地 SQLite 已清理该 session 的 `Pending*` 行（171 行 applied → deleted；另 2 行本地 applied 但 D1 失败）。
- D1 端 `PendingTorrentHistoryWrites` 保留了 2 个孤儿行，电影 `/v/k8n3e`，`ApplyState='pending'`。
- live `MovieHistory` / `TorrentHistory` 两侧**逐字一致**（`/v/k8n3e` 在双侧都是 3 行）。
- 修复需要 ~10 分钟跨库 SQL forensic + 1 次 D1 端 `DELETE`。修复结果以 `kind: drift_resolution` 行写入 `d1_drift.jsonl`。

### 根因定位

drift **不是** Cloudflare D1 故障——是 [`javdb/storage/d1_client.py:105-116`](../../../javdb/storage/d1_client.py) 分类器的一行 bug：

```python
_TRANSIENT_ERROR_KEYWORDS = (
    "D1_RESET_DO",
    "busy",
    "locked",
    "timeout",
    "overloaded",
    "internal error",
    "temporarily",
    "long-running export",
)
```

Cloudflare 返回的是 **`"Network connection lost."`**，包装在 HTTP 400 + 错误码 7500 里。分类器检查消息文本（这是正确的设计，按文件内 rationale），但子串 `"connection lost"` 不在关键词列表中。结果：

1. HTTP 400 + 错误码 7500 → 分类器看消息文本 → 没匹配项 → 抛 `D1PermanentError`。
2. `_post_with_retry` 立即重抛 `D1PermanentError`（line 333-335）不重试。
3. `dual_connection` 捕获 → SQLite 已 commit → drift advisory 追加到 jsonl。

Cloudflare 的 `Network connection lost.` 消息**本质上就是瞬时**——下一次请求换 TCP 连接就能成（典型的 retry-with-backoff 解决场景）。分类器没给它这个机会。

### Bake 监控反应

drift 被现有 `pending_session_verify` 指标（`pending_residual_count: 2`）捕获，算 1 次 D10 gate 的 `pause_trigger`。bake 配额是 ≤ 1 次/月；今天用掉了配额。**bake 窗口内第二次类似事件会让 bake gate FAIL**，阻塞 ADR-005 PR-2+。

监控看到了 drift 但没有诊断路径。本 ADR 的目标：关闭这 10 分钟 forensic 间隙，同时也修真的分类器 bug。

---

## 决策

两层响应，每一层独立 bake 安全：

### D1 — Layer 0：分类器修复（一行正确性修复）

在 [`javdb/storage/d1_client.py`](../../../javdb/storage/d1_client.py) 的 `_TRANSIENT_ERROR_KEYWORDS` 中加 `"connection lost"`。补一个回归 unit test：断言 HTTP 400 + `"Network connection lost"` body 分类为 `D1TransientError`（而非 `D1PermanentError`），让 `_post_with_retry` 走它现有的指数退避重试。

这是**根因修复**。一旦落地，今天这类 drift 变成一条 log 出来的 retry（而且几乎肯定 retry 就成功——CF 网络 blip 秒级愈合），而非 drift 事件。

### D2 — Layer 1：`drift_diagnose` CLI

新 CLI `apps/cli/db/drift_diagnose.py`，两种模式：

**诊断模式（默认）**——只读。扫描最近的 drift 指标，对每个嫌疑 session 分类，打印报告。被分类为"可安全恢复"的 session 会输出建议的修复命令。

**应用模式**（`--apply --session-id <id>`）——重新跑该 session 的诊断，verdict 必须仍是 `SAFE_TO_APPLY` 才执行，发出最小 SQL 删 D1 端孤儿 `Pending*` 行，把 `kind: drift_resolution` 审计行追加到 `d1_drift.jsonl`。

### D3 — 嫌疑发现（双信号并集）

- **Verify 指标路径 (q)**：读 `reports/D1/d1_drift.jsonl`，过滤窗口内 `pending_session_verify` 且 `pending_residual_count > 0` 的记录。
- **D1 主动扫描路径 (r)**：查 D1 端 `ReportSessions` 中 `Status='committed'` 且在窗口内的；逐 session 查 D1 端 `Pending{Movie,Torrent}HistoryWrites` 行数；任何非零计数即为嫌疑。

两者并集；标记每个嫌疑的来源（`verify-tagged` / `sweep-only` / `both`）。不对称子集运维意义重大——`sweep-only`（D1 有残留但 jsonl 里没 verify 行痕迹）表示 verify 发送链路本身坏了，需要升级。

### D4 — Verdict 分类

| Verdict | 触发条件 | 建议动作 | 退出码贡献 |
|---|---|---|---|
| `CLEAN` | 嫌疑标记但实际无孤儿 | 无（verify 指标 stale） | 0 |
| `SAFE_TO_APPLY` | D1 有孤儿 + SQLite 已清理 + live 表两侧逐字一致 | 发出 `--apply --session-id <id>` 命令 | 1 |
| `ESCALATE_LIVE_DIVERGENCE` | live `MovieHistory` / `TorrentHistory` 两侧不同 | 手动调查；**不**自动修 | 2 |
| `UNEXPECTED_PATTERN` | 其他（in_progress session 误入嫌疑、SQLite 端孤儿、混合状态等） | 手动调查 | 2 |

进程退出码 = 所有嫌疑 verdict 的 `max`。允许 shell 层按严重度分支处理。

### D5 — `--apply` 的五条硬安全栏

1. `--apply` 必须配 `--session-id`（argparse 强制）。
2. `--apply` 在执行前**重新跑**完整诊断；verdict 必须仍是 `SAFE_TO_APPLY`（捕获报告生成到执行之间的状态变化）。
3. 目标 session 当前**必须** `Status='committed'`。in_progress / finalizing / failed 一律拒绝。
4. 孤儿行数**必须** ≤ `--max-deletes`（默认 100）。防 D1 token 泄露后被批量滥用。
5. DELETE 语句**必须**包含 `AND SessionId=?` 和 `AND ApplyState='pending'` 两个谓词，作为代码级不变量（不由 caller 传入）。

每条安全栏违反映射到不同退出码 + 明确命名的 log 行，操作员能判断哪条触发了。

### D6 — 邮件集成（subprocess 带 timeout）

DailyIngestion `email-notification` job 在已经渲染 `⚠️ D1 DRIFT ADVISORY ⚠️` block 时，**额外**用 `subprocess.run` 调用 `drift_diagnose --since 1 --json`，60 秒 timeout。

- 诊断成功 → 在现有 advisory 下方渲染 `─── Drift Diagnosis ───` 子节，含每个 session 的 verdict + 建议 apply 命令。
- subprocess timeout / 非 JSON 输出 / 非零退出码（非 0/1/2）→ fallback 子节"Automated diagnosis unavailable: <reason>"，附手动运行提示。subprocess 失败**绝不**阻塞邮件投递。
- 主题前缀加 tag：
  - ≥1 个 session 被分类为 `SAFE_TO_APPLY` → 加 `[DRIFT-FIX-READY]`——操作员扫邮件就知道里面有一行修复命令。
  - ≥1 个被分类为 `ESCALATE_LIVE_DIVERGENCE` 或 `UNEXPECTED_PATTERN` → 加 `[DRIFT-ESCALATE]`。

drift_diagnose **从不**在邮件 job 里以 `--apply` 调用。所有生产侧 `DELETE` 都要操作员手动介入（brainstorming "(b) 半自动" 决策的延伸）。

### D7 — 实施阶段

| Phase | 范围 | 独立性 |
|---|---|---|
| **P0** | D1 分类器修复（D1）—— 一个关键词 + 一个 unit test | 单 micro-PR，完全独立于 P1-P3 |
| **P1** | `drift_diagnose` diagnose 模式（D2, D3, D4）—— 只读 CLI | 单独可用（替代今天的手动 forensic） |
| **P2** | `--apply` 路径（D5） | 建在 P1 之上 |
| **P3** | 邮件集成（D6） | 建在 P1 之上（P2 可选但通常同期） |

每阶段独立成 PR。P0 可立即上线。P1-P3 按 review 速度排序。

### D8 — 明确不在范围

以下备选已考虑，**推迟到 ADR-006 bake 完成 / ADR-005 PR-2+**：

- **L2a — Idempotent bookkeeping**（把 `UPDATE ApplyState='applied'` + `DELETE WHERE applied` 合成单个 `DELETE ... WHERE ApplyState='pending'`）。触 `db_commit_session_history` write path；违反 ADR-006 D5 → amendment 3 中的 PR-2 类划分。
- **L2b — D1 原子 batch**（用 `batch_execute` 把 commit 时所有 D1 写当一个原子 batch；部分失败不可能）。同样的 bake gate 约束。
- **L3 — 周期性 orphan sweep workflow**（完全自动，无人审）。brainstorming 时已否决——移除操作员审计步骤违反 "(b) 半自动" 决策。
- **L4 — `Pending*` 表改为 SQLite-only**（不 mirror D1）。触 `dual_connection` 写入策略；应在 ADR-005 Repo 类迁移期间重新评估。

这些**没**被放弃——它们在等 bake 期约束解除。ADR-005 PR-2 的设计**应当**明确把 L2a 列为候选纳入。

---

## 备选方案

### 备选 A：只做 Layer 0（分类器修复，无诊断工具）

只发一行 keyword fix，未来其他未列在 transient 关键词表里的 drift 类型继续靠手动 forensic。

**否决原因**：关键词集合天然不完整（Cloudflare 错误词汇开放式）。未来其他瞬时错误类还会需要 10 分钟手动 forensic。分类器修复**只**治了**已知**故障模式，对**下一个**未预料的留 operator 准备不足。

### 备选 B：完全自动化（Layer 0 + Layer 1 + Layer 3 周期 sweep，无操作员审）

Layer 0 + Layer 1 落地后再加 cron 自动 apply 所有 `SAFE_TO_APPLY` verdict。

**否决原因**：移除操作员审计步骤。brainstorming 时已把这个评估为 (a) 选项并选了半自动 (b)。原因：移除操作员审计 = 唯一让操作员对 Cloudflare 真实可靠性产生直觉的渠道被掐——操作员不再注意 D1 hiccup 频率，丢掉了那个本来会触发更深层基础设施变更的信号。

### 备选 C：Layer 0 + Layer 1 + Layer 2a（idempotent bookkeeping）

把 `db_commit_session_history` 改写（`UPDATE+DELETE` → 单 `DELETE`）纳入本 ADR。

**否决原因**：Layer 2a 触实际 write path。按 ADR-006 amendment 3，它属于 PR-2 类别——bake 期阻塞。把它折进本 ADR 要么延迟整个 ADR 等 bake 完成，要么违反 bake gate。干净路径是把 L2a 推迟到 ADR-005 PR-2 的设计，本 ADR 独立先发监控 + 分类器。

### 备选 D：不做——接受手动 forensic

维持现状：drift advisory + 每次事件手动调查。

**否决原因**：今天这次事件已经吃掉了 bake ≤1 次/月 `pause_trigger` 配额，而起因是一行可证可防的代码 bug。继续容忍这类错误既耗操作员时间又压 bake gate 余量。

---

## 后果

### 正面

- **今天这个 drift 类彻底不可能再出现**（Layer 0）——未来再来一次 `"Network connection lost."` HTTP 400 会重试、成功、永远不到 drift logger。
- **未来所有 drift 事件都有 30 秒诊断路径**（Layer 1），不是 10 分钟手动 SQL forensic。
- **操作员保留审计闸口**（无自动 apply）——Cloudflare 可靠性信号继续可见。
- **bake 监控保持有意义**——BakeCheck workflow + `pause_trigger` 预算继续暴露真实分歧，不被自动修复钝化。
- **每层独立可发布**——Layer 0 几小时内可上线；Layer 1 各 phase 按 review 速度。

### 负面

- **Layer 0** 扩展了 transient 分类面：未来如果出现一个**真正永久**的错误，其消息文本恰好包含 `"connection lost"`，会被重试 5 次 × 退避（约 30 秒总耗时）才失败。Cloudflare 当前用这个短语只在瞬时场景，但分类器仍是启发式。
- **Layer 1** 加 ~800 行新代码 + 测试 + 新 CLI + 必须与底层 schema 同步的邮件集成代码。维护负担增加。
- **邮件主题增加 tag**（`[DRIFT-FIX-READY]` / `[DRIFT-ESCALATE]`）——操作员邮箱过滤规则可能要更新。

### 风险

| 风险 | 严重度 | 缓解 |
|---|---|---|
| Layer 0 false-positive——某个真正永久错误的消息含 `"connection lost"` | 低 | Cloudflare 当前用该短语只用于瞬时场景；若出现永久变体，分类器可加严（例如同时匹配 `"Network "`）。最坏成本：5 次重试 × ~30s = 一次延迟失败，非数据损坏。 |
| Layer 1 `_compare_live_tables` 启发式漏检细微差异 | 中 | 测试覆盖字段级翻位检测。任何不确定路径走 `ESCALATE` 而非 `SAFE_TO_APPLY`。操作员对所有 `--apply` 最终 audit。 |
| `--apply` 与邮件 job 建议同时跑造成 race | 低 | apply 时重诊断捕获状态变化（D5 第 2 条）；DELETE 天然幂等。 |
| 操作员习惯化——不读 rationale 就跑建议命令 | 中 | 报告格式始终给出 WHY（D1 孤儿行数、live 一致 check、嫌疑来源）。Verdict 名字强制阅读理解（无红绿灯图标）。 |
| 邮件 subprocess 挂超 60s | 低 | 硬 timeout；fallback 子节渲染；主题 tag 省略；邮件正常按时投递。 |

---

## 相关决策

- **[ADR-006](ADR-006-pending-mode-default-rollout.md) amendment 3** —— bake 安全划线。Layer 0 完全不动 workflow。Layer 1 D6 确实修改 `email-notification` job，但仅以带严格 timeout 的只读 subprocess 形式触发，不改任何 D10 gate 输入（不写 D1/SQLite、不改 schema、不动 pause 机制）。按上方修正措辞，两者都落在 bake-safe 一侧。
- **[ADR-005](ADR-005-db-py-retirement-and-repo-pattern.md) PR-2（推迟）** —— 该 PR 的设计 bake 后重启时**应明确**把 L2a（idempotent bookkeeping）列为候选纳入。
- **PR #50**（BakeCheck.yml）—— 正交的监控层；drift_diagnose 是补足的*诊断*层，不替代 gate。

---

## 参考资料

- 2026-05-17 drift 事件，以 `kind: drift_resolution` 记录在 `reports/D1/d1_drift.jsonl`（事后 sync 提交）。
- [`javdb/storage/d1_client.py`](../../../javdb/storage/d1_client.py) —— 现有分类器（Layer 0 目标）。
- [`javdb/storage/dual_connection.py`](../../../javdb/storage/dual_connection.py) —— drift 检测 + advisory 写入器（Layer 1 读其输出）。
- [`javdb/integrations/notify/email.py`](../../../javdb/integrations/notify/email.py) —— 邮件渲染管线（D6 集成目标）—— *ADR-007 重组后的路径；重组前的 `packages/python/javdb_integrations/email_notification.py` 已不存在*。

---

## 待办的后续 follow-up

- drift advisory 记录格式本身**没记 SQL `params`**——正是这个缺口逼今天的 forensic 从 verify 指标反推 `Seq` 值。单独的小 PR 应扩展 advisory 记录 schema 加上失败 SQL 的 params；drift_diagnose 拿到的就是直接信号，不需要现在这种时间窗口对齐。本 ADR **不强制要求**这个改动落地——但它是天然互补。
