# MCP 服务器

ADR-038 Phase 1 在 `apps/mcp/` 引入了一个只读的 [FastMCP](https://github.com/jlowin/fastmcp) 服务器，这是 service 层的第三个适配器——与 CLI（`apps/cli/`）和 REST API（`apps/api/`）并列。当前 MCP 面共 10 个工具：8 个只读运维工具加 2 个受闸动作（`rollback_session`、`commit_session`）；`trigger_run` 仍然推迟。

## 启动服务器

```bash
python -m apps.mcp.server
```

服务器使用 stdio 传输（MCP 默认）。需要安装 `mcp` 包：

```bash
pip install -r requirements.txt
```

## 连接本地 Agent（stdio）

在 MCP 客户端配置中添加条目（例如 Claude Desktop 的 `claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "javdb-autospider": {
      "command": "python3",
      "args": ["-m", "apps.mcp.server"],
      "cwd": "/path/to/JAVDB_AutoSpider_CICD"
    }
  }
}
```

服务器名称 `javdb-autospider` 与 `apps/mcp/server.py` 中的 `FastMCP("javdb-autospider")` 声明一致。

## 当前工具面

| 工具 | 类型 | 回答什么问题 |
|------|------|------------|
| `get_capabilities` | 只读 | 部署能力和后端版本 |
| `get_session` | 只读 | 某次 pipeline session 的生命周期详情 + 写入计数 |
| `list_incidents` | 只读 | 最近的运维事件（ADR-026），可按状态过滤 |
| `get_incident` | 只读 | 单条运维事件的完整详情 |
| `query_events` | 只读 | Pipeline 事件时间线（ADR-036）；ADR-036 未构建时报告不可用 |
| `diagnose_run` | 只读 | 对某次 run / 事件的只读 AI 诊断（ADR-026） |
| `list_runs` | 只读 | 最近的任务运行记录及下次计划时间 |
| `search_history` | 只读 | 搜索本地电影历史（"我有 X 吗？"） |
| `rollback_session` | 受闸 | 默认 dry-run；需要 `confirm=true` 才执行回滚 |
| `commit_session` | 受闸 | 默认 dry-run；需要 `confirm=true` 才执行提交 |

这两个受闸工具默认 dry-run。`confirm=false` 时只返回预览，不会产生任何变更；`confirm=true` 时才会执行，并尝试写入 best-effort 审计事件。

## 返回形状约定

探测某个可能不存在的能力的工具返回 `{"available": false, "reason": ...}` —— 例如，ADR-036 尚未构建时 `query_events` 会返回此形状。

尝试操作但调用失败的工具返回 `{"error": ..., "detail": ...}` —— 例如，`list_runs` 无法访问 task service 时。

这是有意的区分：`available: false` 表示该能力在当前部署中不存在；`error` 表示能力存在但运行时调用失败。

## Phase 2 范围

`rollback_session` 和 `commit_session` 是已经交付的 Phase 2 受闸动作。它们都由 dry-run 预览、显式 `confirm` 参数、复用 auth，以及 confirmed call 上的 best-effort 审计写入守护。`trigger_run` 仍然推迟，因为没有现成的 Python service 可以薄适配，它需要新的 GitHub `workflow_dispatch` 代码，而且运维者已经可以通过 GitHub UI / TS Worker 路径发起。它不属于当前工具面。

## 相关设计文档

[ADR-038 — Agentic Operator MCP Surface](../../../design/ADR-038-Agentic-Operator-MCP/ADR-038-agentic-operator-mcp-surface.md)
