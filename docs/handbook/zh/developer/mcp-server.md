# MCP 服务器

ADR-038 Phase 1 在 `apps/mcp/` 引入了一个只读的 [FastMCP](https://github.com/jlowin/fastmcp) 服务器，这是 service 层的第三个适配器——与 CLI（`apps/cli/`）和 REST API（`apps/api/`）并列。Phase 1 暴露八个只读运维工具；变更型的"受闸操作"（`trigger_run`、`rollback_session`、`commit_session`）推迟到 Phase 2 实现。

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

## Phase 1 只读工具

| 工具 | 回答什么问题 |
|------|------------|
| `get_capabilities` | 部署能力和后端版本 |
| `get_session` | 某次 pipeline session 的生命周期详情 + 写入计数 |
| `list_incidents` | 最近的运维事件（ADR-026），可按状态过滤 |
| `get_incident` | 单条运维事件的完整详情 |
| `query_events` | Pipeline 事件时间线（ADR-036）；ADR-036 未构建时报告不可用 |
| `diagnose_run` | 对某次 run / 事件的只读 AI 诊断（ADR-026） |
| `list_runs` | 最近的任务运行记录及下次计划时间 |
| `search_history` | 搜索本地电影历史（"我有 X 吗？"） |

## 返回形状约定

探测某个可能不存在的能力的工具返回 `{"available": false, "reason": ...}` —— 例如，ADR-036 尚未构建时 `query_events` 会返回此形状。

尝试操作但调用失败的工具返回 `{"error": ..., "detail": ...}` —— 例如，`list_runs` 无法访问 task service 时。

这是有意的区分：`available: false` 表示该能力在当前部署中不存在；`error` 表示能力存在但运行时调用失败。

## Phase 2（尚未实现）

变更型的"受闸操作"——`trigger_run`、`rollback_session`、`commit_session`——将在 Phase 2 落地。每个操作都将由 dry-run 预览、显式 `confirm` 参数、复用 auth 以及审计事件来守护。Phase 1 中这些工具均不存在。

## 相关设计文档

[ADR-038 — Agentic Operator MCP Surface](../../../design/ADR-038-Agentic-Operator-MCP/ADR-038-agentic-operator-mcp-surface.md)
