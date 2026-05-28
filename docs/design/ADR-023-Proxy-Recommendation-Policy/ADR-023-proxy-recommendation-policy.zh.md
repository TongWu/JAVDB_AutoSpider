# ADR-023：代理推荐策略 - 面向稳定性的 ProxyCoordinator Bandit

| 字段        | 值                                                                    |
| ----------- | --------------------------------------------------------------------- |
| **状态**    | Proposed                                                              |
| **日期**    | 2026-05-27                                                            |
| **作者**    | Ted                                                                   |
| **关联**    | [ADR-004](../_archive/ADR-004-Proxy-Discovery/ADR-004-proxy-discovery-via-runner-pool-upload.md), [ADR-013](../_archive/ADR-013-Runner-Runtime-State/ADR-013-runner-runtime-state-consolidation.md) |

## 背景

`ProxyCoordinator` 已经提供了可用的健康信号：`/report` 会记录 `success`、`failure`、`cf`、`ban`、`unban` 和 `cf_bypass`；`computeHealthSnapshot()` 会基于成功率和延迟生成基础分数；`/recommend_proxy` 会返回带有向后兼容健康字段的候选列表。Python spider 客户端也已经会把请求完成延迟回报给 coordinator。

这足以做启发式排序，但不足以描述真实的运维问题。代理池必须同时处理几件事：

- 避免反复挑中会触发封禁或 Cloudflare bypass 失败的代理；
- 避免把可能在 cooldown 或 JavDB 解封后恢复的代理永久饿死；
- 避免把代理个体健康和 JavDB 全局异常混为一谈；
- 保持 `/lease` 和请求热路径简单、确定；
- 保持与现有 Python 客户端和 Worker DO 状态的向后兼容。

这里的目标不是引入在线 LLM，也不是做一个重量级 ML 服务。目标是一个轻量 policy 层：它从运维反馈中学习，但不改变当前协议形状。

## 决策

在 `ProxyCoordinator` 内部，用一个稳定性优先的 bandit policy 替换纯启发式排序，只在 `/recommend_proxy` 中运行，并复用现有 coordinator 状态。

### 设计决策

D1. **稳定性优先，吞吐第二** - policy 先优化更少的 ban、更少的 CF bypass 失败、更少的 session 级不稳定，再优化延迟和吞吐。

D2. **reward 采用加权而非二元** - outcome 按稳定性做加权：`success + low latency` 为正反馈；`failure` 为轻负反馈；`cf` 和 `cf_bypass` 为中等负反馈；`ban` 为强负反馈。如果失败还能关联到 login refresh 或 session instability，则加重惩罚。

D3. **只限制真正的人工意图，不做永久淘汰** - policy 不得把某个代理永久踢出行动集。`ban` 和 `cf_bypass` 进入 cooldown / 低频探测，但允许恢复。只有显式 operator state 或协议级 hard disable 才能完全阻止选择。

D4. **全局健康与局部健康分开建模** - policy 需要对比共享的全局 baseline window。如果整个池子同时变差，模型应更保守，而不是把每个代理都判坏。

D5. **必须保留 cooldown 和探索** - 每个未被硬禁用的代理都保留一个很小的 exploration floor。近期表现差的代理在 cooldown 后可以重新进入选择，只是以较低频率进行恢复探测。

D6. **冷启动使用现有 heuristic 作为 prior** - 新代理或样本不足的代理默认中性。现有 heuristic 继续作为 fallback 和先验，直到置信度足够。

D7. **先 shadow mode** - 第一阶段只同时计算模型分数和 heuristic 分数，不改变排序。只有 shadow 数据看起来正常后，才允许 policy 影响排序。

D8. **解释字段保持结构化** - `/recommend_proxy` 可以返回可选字段 `model_score`、`heuristic_score`、`confidence`、`reason_code`、`cooldown_until` 和 `model_version`。旧 Python 客户端可以忽略这些字段。

D9. **热路径不引入重量级 ML runtime** - 第一版只做轻量 TS policy / bandit 实现。不引入在线 LLM、Workers AI、Vectorize 或独立 ML 服务来参与选代理。

### 实现形态

policy 仍然放在 `JAVDB_AutoSpider_Proxycoordinator` 里，并复用现有 proxy DO 状态：

- 每个代理的本地状态继续保存计数、latency EMA、ban / bypass 标记和 cooldown 元数据；
- `/report` 继续作为反馈入口；
- `/recommend_proxy` 成为唯一的评分位置；
- Python 客户端继续上报事件、消费推荐结果，只在响应中多忽略一些可选字段。

policy 会把最终排序分数拆成三部分：

- 来自当前 health snapshot 逻辑的 heuristic prior；
- 来自加权 outcome 历史的 learned stability score；
- 来自样本数、最近事件和全局 baseline 质量的 confidence。

当 confidence 很低时，heuristic 占主导；当样本足够时，learned component 可以逐渐接管。如果状态缺失或损坏，直接回退到当前 heuristic 排序。

## 后果

### 正面

- 代理选择可以从 ban、CF bypass 失败和延迟中学习，而不影响请求热路径。
- 坏代理不容易被永久饿死。
- JavDB 全局异常不会把所有代理一起污染。
- 推荐 API 对旧客户端保持向后兼容。
- 可以先用 shadow mode 安全验证，再改变行为。

### 负面

- policy 比单一静态分数更复杂。
- cooldown / exploration 机制需要仔细调参，避免抖动。
- 系统仍然需要运维判断来区分代理局部失败和外部站点行为。

### 风险

- **把短暂故障惩罚过重** - 如果 global baseline 检测太激进，policy 可能过于保守。缓解方式：使用共享窗口比较，并保留 heuristic fallback。
- **恢复代理探索不足** - 如果 exploration floor 太低，恢复会很慢。缓解方式：保留非零 probe floor 和 cooldown 到期机制。
- **协议漂移** - 新增的可选字段必须保持可选，避免旧客户端破坏。缓解方式：保留当前响应结构，只追加字段。

## 实施路线

| 阶段 | IMP | 交付 | 延后 |
| --- | --- | --- | --- |
| Phase 1 | [IMP-ADR023-01](IMP-ADR023-01-shadowscore-confidence-fields.md) | Shadow scoring、reward 聚合、confidence 和可选解释字段 | 暂不改变排序 |
| Phase 2 | [IMP-ADR023-02](IMP-ADR023-02-policy-rollout-flag.md) | 在 feature flag 下启用 policy 排序，并保留 heuristic fallback | 不做自动调参和离线训练 |
| Phase 3 | [IMP-ADR023-03](IMP-ADR023-03-observability-rollout-hardening.md) | 围绕全局/局部健康信号做可观测性和 rollout 加固 | 不引入重量级 ML runtime 或 LLM 选路 |
| Phase 4 | [IMP-ADR023-04](IMP-ADR023-04-python-selection-signal.md) | Python Selection Signal 模块负责 score adapter 选择、freshness、逐 proxy fallback 和 runtime lifecycle | 不改 `ProxyPool` 接口，不消费 rank/model score，不改 ban/cooldown 语义 |

## 参考

- `JAVDB_AutoSpider_Proxycoordinator/src/proxy_coordinator.ts` - 当前 health snapshot 和候选排序实现。
- `JAVDB_AutoSpider_Proxycoordinator/src/index.ts` - `/recommend_proxy` 的响应结构和排序逻辑。
- `javdb/proxy/coordinator/proxy_coordinator_client.py` - Python 客户端 contract 和 health 解析。
- `docs/handbook/en/self-hoster/proxy-coordinator.md` - 当前面向运维的 proxy coordinator 行为说明。

## 状态日志

- 2026-05-27：以 ADR-023 提出。
- 2026-05-27：新增 Python Selection Signal 深化工作的 Phase 4 实施计划。
