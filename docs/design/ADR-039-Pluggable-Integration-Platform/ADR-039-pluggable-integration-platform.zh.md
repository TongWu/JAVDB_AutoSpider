# ADR-039：可插拔集成平台

| 字段       | 值                                                                    |
| ---------- | --------------------------------------------------------------------- |
| **状态**   | Proposed — 伞型;执行下放给各期 IMP                                    |
| **日期**   | 2026-05-29                                                            |
| **作者**   | Ted                                                                   |
| **关联**   | [ADR-015](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md), [ADR-036](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md), [ADR-038](../ADR-038-Agentic-Operator-MCP/ADR-038-agentic-operator-mcp-surface.md), [ADR-033](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md) |

> 源自 2026-05-29 一次关于全新方向(方向六——可插拔扩展平台)的头脑风暴。

## 背景 (Context)

[ADR-015](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md) 把 integrations（`qb`、`pikpak`、`rclone`、`notify`、`gh_actions`）做成了 `Options → Result` service,但每个都**硬接线到单一后端**:`javdb/integrations/notify/` 下只有 `email/`——没有后端抽象,所以加 Telegram 或 Discord 通知器非动手术不可。管道按名字接线 integrations（`apps.cli.qb.uploader`、`apps.cli.pikpak.bridge`），且**不存在任何插件/注册/entry-point 机制**（干净起点）。

这挡住了把"Ted 的私有系统"升级成别人能扩展的**可自托管平台**——把 qB 换成 Transmission、email 换成 Telegram、GDrive 换成 Plex。ADR-015 已给每个集成一个干净的 service 边界;缺的那块是**一个注册表 + 每类别一份插件契约**,让一个类别能托管多个可互换、由 config 选择的后端。

本 ADR 引入这个平台,**基于注册表、entry-point-ready**,并在 `notify` 类别上证明它(email 作内置插件 + 一个新 Telegram 插件)。

## 决策 (Decision)

加 `javdb/integrations/plugins/`（一个注册表）与每类别一份插件契约,从 `notify` 起步。内置插件 import 时注册;config 选择 active 后端;一个 dispatcher 向它们扇出并做失败隔离。注册表设计成:真第三方 entry-point 发现是附加式 Phase 2。

### 设计决策 (Design Decisions)

**D1. 内置注册表,entry-point-ready——尚未用 entry-points。** 一个 `PluginRegistry` 按 `(category, name)` 持有插件。内置插件 import 时注册;`config` 选哪些 active。注册表暴露一个接缝（`discover_entry_points(group)`），让 Phase 2 用 `importlib.metadata` 发现第三方 pip 包插件成为纯附加——Phase 1 只注册内置。这是通往平台的低风险路:现在 config 换内置,日后长成第三方。

**D2. 每类别一份插件契约;`notify` 先行。** 每个类别定义一个 `Protocol`。notify:

```python
class NotifyPlugin(Protocol):
    name: str
    def is_configured(self) -> bool: ...
    def send(self, message: NotifyMessage) -> NotifyResult: ...
```

**D3. 现有 email 包装成内置插件——不重写。** `EmailNotifyPlugin` 是对现有 `notify/email/service.py` 的薄 `name='email'` adapter（其内部不动）。新增 `TelegramNotifyPlugin`（`name='telegram'`）调 Telegram Bot API。每个插件读自己的 config（email → `SMTP_*`;telegram → `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`）。`NOTIFY_BACKENDS` 选 active 列表,且**默认 `['email']`**,所以现有行为保持不变;加 `'telegram'` 即启用第二路。

**D4. 分发扇出 + 失败隔离。** `notify.send(message)` 遍历 active 插件,逐个调 `.send()`,收集 `NotifyResult`;某后端失败（如 Telegram 宕）不阻断其它（如 email）。现有调用方（管道邮件摘要）经 `notify.send` 路由,照常工作。

**D5. 模块形态。**

```
javdb/integrations/plugins/registry.py       # PluginRegistry（+ entry-point 接缝）
javdb/integrations/notify/plugin.py          # NotifyPlugin 协议 + NotifyMessage/Result
javdb/integrations/notify/dispatch.py        # notify.send() 扇出
javdb/integrations/notify/email/plugin.py    # EmailNotifyPlugin（包现有 service）
javdb/integrations/notify/telegram/plugin.py # TelegramNotifyPlugin（新）
```

**D6. 插件与本会话产出咬合（未来）。** 一个插件日后可作 [ADR-036](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md) 事件消费者（`SessionFailed` 时通知），并暴露成 [ADR-038](../ADR-038-Agentic-Operator-MCP/ADR-038-agentic-operator-mcp-surface.md) MCP 工具——让插件层成为本会话脊柱/门面收口成生态的地方。不在 Phase 1 范围,作为方向记录。

**D7. 分期。** Phase 1 在 `notify` 上证明契约;Phase 2 加 entry-point 发现（真第三方）与第二类别（`downloader`:qB + Transmission）;Phase 3 加 media-server/destination 类别与 事件消费者 / MCP 工具 的咬合。

## 后果 (Consequences)

### 正面 (Positive)

- **真正的扩展点**——类别托管可互换、由 config 选择的后端;加通知器是插件,而非手术。
- **向后兼容**——`NOTIFY_BACKENDS` 默认 email;自托管者不主动启用前一切不变。
- **低风险、可长大**——现在内置注册,日后第三方 entry points,同一契约。
- **建在 ADR-015 上**——每个插件就是边界已定义的 `Options → Result` service。
- **可自托管平台轨迹**——别人能扩展 qB→Transmission、email→Telegram、GDrive→Plex。

### 负面 (Negative)

- **多一份要稳定的契约**——一旦第三方依赖 `NotifyPlugin`（Phase 2），其形态须谨慎版本化。
- **要定义分发语义**——扇出 + 失败隔离 + 结果聚合,比此前直调 email 多一小层。
- **更多 config 面**——`NOTIFY_BACKENDS` + 各插件 key。

## 实施路线图 (Implementation Roadmap)

| 阶段 | IMP | 交付内容 | 推迟内容 |
| --- | --- | --- | --- |
| Phase 1 — 注册表 + notify | [IMP-ADR039-01](IMP-ADR039-01-notify-plugins.md) | `plugins/registry`;`NotifyPlugin` 契约;`EmailNotifyPlugin`（包现有）;`TelegramNotifyPlugin`（新）;`NOTIFY_BACKENDS` config;`notify.send` 扇出 | entry-point 发现;其它类别 |
| Phase 2 — Entry points + downloader | IMP-ADR039-02（占位） | `discover_entry_points`（第三方）;`downloader` 类别（qB + Transmission） | — |
| Phase 3 — 生态（可选） | IMP-ADR039-03（占位） | media-server/destination 类别;插件作 ADR-036 消费者 / ADR-038 工具 | — |

Phase 1 独立成立且向后兼容。Phase 2/3 拓宽平台。

### 明确的非目标 (YAGNI)

- **Phase 1 只 `notify` 一个类别**——qB/rclone/pikpak 不动。
- **Phase 1 无 entry-point 发现**——仅内置注册（接缝预留）。
- **不重写 email 内部**——包装成内置插件（D3）。
- **Phase 1 无 事件消费者 / MCP 咬合**——作为 Phase 3 方向记录。

## 领域语言 (CONTEXT.md 待补充项)

- **Plugin（插件）**——实现某类别契约的已注册后端（如一个 `NotifyPlugin`）。
- **Plugin registry（插件注册表）**——`javdb/integrations/plugins/registry.py`,按 `(category, name)` 索引,现在注册内置、（Phase 2）注册 entry-point 第三方。
- **Plugin contract（插件契约）**——插件满足的每类别 `Protocol`。
- **Built-in plugin（内置插件）**——仓内自带的插件（如 `EmailNotifyPlugin`）。
- **Notify backend（通知后端）**——经 `NOTIFY_BACKENDS` 选中的 active notify 插件。

## 备选方案 (Alternatives Considered)

- **一上来就用 Python entry points**——否决（D1）:是"真平台",但需前期打包/版本/契约稳定性;注册表以远更低的风险附加式抵达同一处。
- **目录投放插件**——否决（D1）:不如 entry points 标准,也不比注册表更简单。
- **把 email 重写成插件形态**——否决（D3）:email service 已在 ADR-015 边界后工作良好;包装它,别重写。

## 参考 (References)

- [ADR-015 — Integrations Interface Boundary](../_archive/ADR-015-Integrations-Interface/ADR-015-integrations-interface-boundary.md)
- [ADR-036 — Event-Sourced Pipeline Spine](../ADR-036-Event-Sourced-Pipeline-Spine/ADR-036-event-sourced-pipeline-spine.md)
- [ADR-038 — Agentic Operator MCP Surface](../ADR-038-Agentic-Operator-MCP/ADR-038-agentic-operator-mcp-surface.md)
- [ADR-033 — Media Closed-Loop](../ADR-033-Media-Closed-Loop/ADR-033-media-closed-loop.md)

## 状态日志 (Status Log)

- 2026-05-29: Proposed(伞型;三期已划定,IMP 待出)。
