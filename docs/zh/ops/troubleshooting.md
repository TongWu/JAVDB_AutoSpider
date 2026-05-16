# 故障排查

JAVDB AutoSpider 的常见问题及解决方案。

## 爬虫问题

**未找到条目 / "No movie list found"**
- 检查 javdb.com 是否可以从你的机器或代理访问（在浏览器中打开）。
- 如果使用代理，验证代理是否正在运行且可达。
- 对于自定义 URL 抓取（`--url`），确保你有有效的会话 cookie。运行 `python3 -m apps.cli.login` 刷新 cookie。
- 检查 CloudFlare 是否阻止了访问。如果配置了 CF 绕过服务，验证 CF 绕过服务是否正在运行。

**连接错误 / 超时**
- 验证网络连接。
- 检查 javdb.com 是否正在停机。
- 如果在企业防火墙后面，确保允许出站 HTTPS 流量。

**未生成 CSV 文件**
- 检查 `reports/DailyReport/` 目录是否存在（该目录会自动创建，但如果在非常规环境中运行请验证）。
- 在 `--dry-run` 模式下，CSV 文件故意不写入。
- 查看爬虫日志（`logs/spider.log`）中运行期间的错误。

## qBittorrent 问题

**无法连接到 qBittorrent**
- 验证 qBittorrent 正在运行且 Web UI 已启用（**工具 > 首选项 > Web UI > 启用**）。
- 检查 config.py 中的 `QB_URL` 是否包含协议（`http://` 或 `https://`）和端口。
- 如果使用自签名证书的 HTTPS，在 config.py 中设置 `QB_VERIFY_TLS = False`。
- 测试连通性：`curl -k https://YOUR_QB_URL/api/v2/app/version`

**登录失败**
- 验证 config.py 中的 `QB_USERNAME` 和 `QB_PASSWORD`。
- 检查 qBittorrent 是否启用了基于 IP 的访问限制。
- 某些 qBittorrent 版本需要启用"对本地主机客户端绕过身份验证"选项。

**找不到 CSV 文件**
- 先运行爬虫生成 CSV 文件。
- 检查爬虫是否成功完成（退出码 0）。
- 验证上传器中的 CSV 路径是否与爬虫输出目录匹配。

## Git 问题

**身份验证失败**
- 使用个人访问令牌 (PAT) 代替密码。在 **GitHub > Settings > Developer settings > Personal access tokens** 中生成。
- 验证 config.py 中的 `GIT_USERNAME` 和 `GIT_PASSWORD`（即 PAT）。

**仓库未找到**
- 检查 `GIT_REPO_URL` 是否有拼写错误。
- 确保 PAT 对私有仓库具有 `repo` 权限范围。

**分支问题**
- 确保 `GIT_BRANCH` 与你仓库中的现有分支匹配。
- 对于新仓库，先创建分支或使用 `main`。

## 代理问题

**运行期间所有代理被封禁**
- 封禁状态是会话范围的（仅在内存中）。下次运行将从干净状态开始并重试所有代理。
- 查看爬虫日志中与封禁相关的消息。
- 考虑向代理池添加更多代理。
- 验证代理是否确实能访问 javdb.com：`curl -x http://proxy:port https://javdb.com`

**爬虫以退出码 2 退出**
- 退出码 2 表示在会话期间检测到代理被封禁。
- 会话范围的冷却仅适用于该次运行。
- 添加更多代理或等待下次计划运行。

**冷却不按预期工作**
- 代理封禁是会话范围的（仅在内存中）。重启爬虫会重置所有封禁状态。
- 没有持久化的封禁文件或数据库表。

**封禁误报**
- 验证 javdb.com 是否确实可以从代理 IP 访问（通过代理在浏览器中测试）。
- 检查是否有看起来像封禁的 CloudFlare 验证。

**500 内部服务器错误 / 连接被拒绝**
- 检查代理服务器是否正在运行且可访问。
- 验证代理凭据（用户名/密码）。
- 如果密码包含特殊字符，请进行 URL 编码：
  ```python
  from urllib.parse import quote
  password = "My@Pass!"
  encoded = quote(password, safe='')
  # Output: My%40Pass%21
  ```
- 手动测试代理：`curl -x http://username:password@proxy:port https://javdb.com`

**代理密码中的特殊字符**

需要 URL 编码的常见字符：

| 字符 | 编码 |
|---|---|
| `@` | `%40` |
| `:` | `%3A`（仅在密码中，不包括 `@` 后面的分隔符） |
| `/` | `%2F` |
| `?` | `%3F` |
| `#` | `%23` |
| `&` | `%26` |
| `=` | `%3D` |
| `+` | `%2B` | 
| 空格 | `%20` |
| `!` | `%21` |

示例：`http://user:My@Pass!123@proxy:8080` 变为 `http://user:My%40Pass%21123@proxy:8080`

## JavDB 登录问题

**登录失败 -- 验证码错误**
- 验证码区分大小写。重试以获取新验证码。
- 考虑使用 GPT-4o Vision API（config.py 中的 `GPT_API_URL` / `GPT_API_KEY`）自动识别验证码。

**登录失败 -- 凭据无效**
- 验证 config.py 中的 `JAVDB_USERNAME` 和 `JAVDB_PASSWORD`。
- 先在浏览器中测试凭据。

**会话 cookie 不起作用**
- 验证运行登录脚本后 config.py 中的 cookie 已更新。
- 登录和爬虫运行使用相同的代理/网络。
- 尝试重新登录 -- cookie 通常在数天到数周后过期。

**何时需要重新登录：**
- 会话 cookie 已过期（通常在数天/数周后）
- 爬虫在有效的自定义 URL 上显示 "No movie list found"
- JavDB 返回年龄验证或登录错误
- 首次使用 `--url` 之前

有关详细的登录故障排查和手动 cookie 提取，请参阅 [JavDB 登录指南](../../../utils/login/JAVDB_LOGIN_README.md)。

## CloudFlare 绕过问题

**连接 localhost:8000 被拒绝**
- 确保 CF 绕过服务正在运行。
- 检查端口 8000 是否可用：`netstat -an | grep 8000`
- 如果使用不同端口，更新 config.py 中的 `CF_BYPASS_SERVICE_PORT`。

**使用 CF 绕过时 "No movie list found"**
- 查看 CF 绕过服务日志中的错误。
- 验证 `x-hostname` 请求头是否正确发送。
- 尝试重启 CF 绕过服务。

**代理 + CF 绕过不工作**
- CF 绕过服务必须与代理运行在同一台服务器上。
- 验证代理 IP 提取是否正确（查看爬虫日志）。
- 直接测试：`curl http://proxy_ip:8000/`

## 下载指示器问题

**指示器未添加**
- 检查历史文件（`reports/parsed_movies_history.csv`）是否存在且格式正确。
- 历史数据库（`reports/history.db`）是主要来源；CSV 是旧版后备方案。

**上传器跳过了太多种子**
- 检查历史文件是否包含应清理的过时记录。
- 使用 `--ignore-history` 为单次运行绕过历史检查。

**历史格式问题**
- 系统会自动迁移旧格式。如果问题持续，运行：
  ```bash
  python3 packages/python/javdb_migrations/tools/update_history_format.py
  ```
- 有关所有可用迁移工具，请参阅 [migration-scripts.md](migration-scripts.md)。

## 调试模式

要查看详细操作，提高日志级别。环境变量的优先级高于 config.py：

```bash
# 启用调试日志
export LOG_LEVEL=DEBUG

# 可选：在排查日志格式问题时强制控制台使用旧版 4 字段格式
export LOG_STYLE=verbose

# 可选：从 CI 提取原始日志时关闭 ::group:: 折叠
export LOG_GITHUB_GROUPS=off
```

或在 config.py 中设置：

```python
LOG_LEVEL = 'DEBUG'
```

### DEBUG 级别揭示的信息

- **代理池详情**：每个代理的成功率、最近成功时间、最近失败时间戳（在 `INFO` 级别下仅显示单行摘要 `available=N/total / cooldown=K / banned=B`）
- **Rust 扩展日志**：Rust 端日志目标（`javdb_rust_core::proxy::pool` 等）通过 `pyo3_log` 流经 Python 格式化器，以简短显示名称呈现：`ProxyPool`、`BanManager`、`FetchEngine`、`Parser`
- **数据库操作**：详细的 SQL 查询和行数
- **HTTP 请求**：完整的请求/响应详情，用于调试连通性

## GitHub Actions 特有问题

**ARTIFACT_KEY 未配置**
- 每个 job 都对缺少的 `ARTIFACT_KEY` secret 进行守卫。在 **Settings > Secrets and variables > Actions > Secrets** 下添加它。

**Cron 未触发**
- 对于 60 天没有提交活动的仓库，GitHub 会禁用计划工作流。推送一次提交或手动触发运行。

**CI 中邮件未发送**
- 检查 `SMTP_*` secrets 是否已配置。
- Gmail 需要使用应用专用密码，而非常规登录密码。
- 当 SMTP 发送失败时，邮件 job 以退出码 2 退出（因此 CI 不会静默地标记为"已通知"）。

**Rollback 失败**
- 查看 rollback 日志 artifact（保留 14 天）。
- 手动 rollback：**Actions > RollbackD1 > Run workflow** 并填入 session ID。
- 有关完整 SOP 和调度矩阵，请参阅 [d1-rollback.md](d1-rollback.md)。
