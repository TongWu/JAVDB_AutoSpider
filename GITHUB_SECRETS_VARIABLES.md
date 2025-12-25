# GitHub Secrets 和 Variables 配置清单

本文档列出了 Production 和 Development 环境需要配置的 GitHub Secrets 和 Variables。

## 环境说明

- **Production 环境**: 用于 `DailyIngestion.yml`, `AdHocIngestion.yml`, `QBFileFilter.yml`
- **Development 环境**: 用于 `DevIngestion.yml`

两个环境需要配置相同的 Secrets 和 Variables，但值可以不同。

---

## 🔐 Secrets（敏感信息）

以下所有 Secrets 都需要在 **Production** 和 **Development** 两个环境中分别配置：

### Git 相关
| Secret 名称 | 说明 | 参考值（来自 config.py） | 必需 |
|------------|------|-------------------------|------|
| `DEPLOY_KEY` | SSH 私钥，用于 Git 推送操作 | 需要生成 SSH key pair | ✅ |

### qBittorrent 相关
| Secret 名称 | 说明 | 参考值（来自 config.py） | 必需 |
|------------|------|-------------------------|------|
| `QB_HOST` | qBittorrent 服务器 IP 地址 | `101.100.165.240` | ✅ |
| `QB_PORT` | qBittorrent 端口（注意：这是 Variable，不是 Secret） | `12301` | - |
| `QB_USERNAME` | qBittorrent 用户名 | `tedwu` | ✅ |
| `QB_PASSWORD` | qBittorrent 密码 | `No.25_Aminor` | ✅ |

### SMTP 邮件相关
| Secret 名称 | 说明 | 参考值（来自 config.py） | 必需 |
|------------|------|-------------------------|------|
| `SMTP_SERVER` | SMTP 服务器地址 | `smtp.gmail.com` | ✅ |
| `SMTP_PORT` | SMTP 端口（注意：这是 Variable，不是 Secret） | `587` | - |
| `SMTP_USER` | SMTP 用户名/邮箱 | `ted@wu.engineer` | ✅ |
| `SMTP_PASSWORD` | SMTP 密码/应用密码 | `ekxtxbnvdtngdkjw` | ✅ |
| `EMAIL_FROM` | 发件人邮箱 | `ted@wu.engineer` | ✅ |
| `EMAIL_TO` | 收件人邮箱 | `ted@wu.engineer` | ✅ |

### 代理池相关
| Secret 名称 | 说明 | 参考值（来自 config.py） | 必需 |
|------------|------|-------------------------|------|
| `PROXY_POOL_JSON` | 代理池配置（JSON 格式） | 见下方格式说明 | ✅ |

**PROXY_POOL_JSON 格式示例**（基于 config.py）：
```json
[
  {
    "name": "Miraculous Fortress",
    "http": "http://tedwu:No.25_Aminor@139.99.124.231:12300",
    "https": "http://tedwu:No.25_Aminor@139.99.124.231:12300"
  },
  {
    "name": "Singapore-ARM1",
    "http": "http://tedwu:No.25_Aminor@140.245.123.124:12300",
    "https": "http://tedwu:No.25_Aminor@140.245.123.124:12300"
  },
  {
    "name": "Singapore-ARM2",
    "http": "http://tedwu:No.25_Aminor@129.150.62.19:12300",
    "https": "http://tedwu:No.25_Aminor@129.150.62.19:12300"
  }
]
```

### JavDB 登录相关
| Secret 名称 | 说明 | 参考值（来自 config.py） | 必需 |
|------------|------|-------------------------|------|
| `JAVDB_USERNAME` | JavDB 用户名/邮箱 | `asphyxia010101` | ✅ |
| `JAVDB_PASSWORD` | JavDB 密码 | `hvq@atm-qdr.txk3MJK` | ✅ |
| `JAVDB_SESSION_COOKIE` | JavDB 会话 Cookie | `K%2FaCQsdRm%2BwmPmO1VJR92v2MyijMpbaUPeeoujHzwniuo9rLYzPz4o%2FzlDHk1fHAyFWSSdVLaK31bdiScMlywOzveTarsLbUMuUioWrRAtMbuvSPSneNoBjoEq%2Fh%2BPsupf7sJgu0mFxHMGqoERLzp3kWpT1ZHikfOkRz9nLDjNyyGYEHZasHL8rtDQ1VJYCX0Xd4%2BzcqEjCM%2BJw08Ujum5clDV7oUy0dYYBXx1ANZXwWYWnfYQ9p%2BxiP4m939MKp5hsJZZYwtDNC31sgO93sR%2BdybkwjwBhRKLJPPnCQ9QQMOdNp3F%2BPkYg%2FUMmn9B5pNjsKy%2Fskk%2FcAGkvV%2FXlyLYurglbGE2eY%2Bik8C6JEQ%2BgVcvtIYnMRYJo258ntiIeboz4%3D--Ykp5P0N%2F2vfdhN%2B6--%2FtPRnrkdxGquk%2F6DwNXwnw%3D%3D` | ✅ |

### PikPak 相关
| Secret 名称 | 说明 | 参考值（来自 config.py） | 必需 |
|------------|------|-------------------------|------|
| `PIKPAK_EMAIL` | PikPak 邮箱 | `wutong07163@gmail.com` | ✅ |
| `PIKPAK_PASSWORD` | PikPak 密码 | `No.25_Aminor` | ✅ |

### 其他
| Secret 名称 | 说明 | 参考值 | 必需 |
|------------|------|--------|------|
| `ARTIFACT_KEY` | 用于加密/解密 artifacts 的密钥 | 自定义强密码 | ✅ |

---

## 📝 Variables（非敏感信息）

以下所有 Variables 都需要在 **Production** 和 **Development** 两个环境中分别配置：

### Git 相关
| Variable 名称 | 说明 | 参考值（来自 config.py） | 默认值 | 必需 |
|--------------|------|-------------------------|--------|------|
| `GIT_REPO_URL` | Git 仓库 URL | `https://github.com/TongWu/JAVDB_AutoSpider.git` | - | ✅ |
| `GIT_BRANCH` | Git 分支 | `main` | `main` | ✅ |

### qBittorrent 相关
| Variable 名称 | 说明 | 参考值（来自 config.py） | 默认值 | 必需 |
|--------------|------|-------------------------|--------|------|
| `QB_PORT` | qBittorrent 端口 | `12301` | `8080` | ✅ |
| `TORRENT_CATEGORY` | 种子分类（日常模式） | `Daily Ingestion` | `Daily Ingestion` | ✅ |
| `TORRENT_CATEGORY_ADHOC` | 种子分类（AdHoc 模式） | `Ad Hoc` | `Ad Hoc` | ✅ |
| `TORRENT_SAVE_PATH` | 种子保存路径 | ``（空字符串，使用默认路径） | `` | ⚠️ |
| `AUTO_START` | 自动开始下载 | `True` | `True` | ⚠️ |
| `SKIP_CHECKING` | 跳过哈希检查 | `False` | `False` | ⚠️ |
| `REQUEST_TIMEOUT` | API 请求超时（秒） | `30` | `30` | ⚠️ |
| `DELAY_BETWEEN_ADDITIONS` | 添加种子之间的延迟（秒） | `1` | `1` | ⚠️ |

### SMTP 相关
| Variable 名称 | 说明 | 参考值（来自 config.py） | 默认值 | 必需 |
|--------------|------|-------------------------|--------|------|
| `SMTP_PORT` | SMTP 端口 | `587` | `587` | ✅ |

### 代理相关
| Variable 名称 | 说明 | 参考值（来自 config.py） | 默认值 | 必需 |
|--------------|------|-------------------------|--------|------|
| `PROXY_MODE` | 代理模式：`single` 或 `pool` | `pool` | `pool` | ✅ |
| `PROXY_POOL_COOLDOWN_SECONDS` | 代理冷却时间（秒） | `691200`（8天） | `691200` | ⚠️ |
| `PROXY_POOL_MAX_FAILURES` | 最大失败次数 | `3` | `3` | ⚠️ |
| `PROXY_MODULES_JSON` | 使用代理的模块列表（JSON 格式） | 见下方格式说明 | `["spider_index", "spider_detail"]` | ✅ |

**PROXY_MODULES_JSON 格式示例**：
```json
["spider_index", "spider_detail"]
```
或使用所有模块：
```json
["all"]
```

### Cloudflare Bypass 相关
| Variable 名称 | 说明 | 参考值（来自 config.py） | 默认值 | 必需 |
|--------------|------|-------------------------|--------|------|
| `CF_BYPASS_SERVICE_PORT` | CF Bypass 服务端口 | `8000` | `8000` | ⚠️ |
| `CF_BYPASS_ENABLED` | 启用 CF Bypass | `True` | `True` | ⚠️ |

### Spider 相关
| Variable 名称 | 说明 | 参考值（来自 config.py） | 默认值 | 必需 |
|--------------|------|-------------------------|--------|------|
| `START_PAGE` | 起始页码 | `1` | `1` | ⚠️ |
| `END_PAGE` | 结束页码 | `10` | `10` | ⚠️ |
| `PHASE2_MIN_RATE` | Phase 2 最低评分 | `4.0` | `4.0` | ⚠️ |
| `PHASE2_MIN_COMMENTS` | Phase 2 最低评论数 | `85` | `85` | ⚠️ |
| `BASE_URL` | JavDB 基础 URL | `https://javdb.com` | `https://javdb.com` | ⚠️ |

### JavDB 登录相关（延迟配置）
| Variable 名称 | 说明 | 参考值（来自 config.py） | 默认值 | 必需 |
|--------------|------|-------------------------|--------|------|
| `DETAIL_PAGE_SLEEP` | 详情页延迟（秒） | `30` | `30` | ⚠️ |
| `PAGE_SLEEP` | 页面间延迟（秒） | `15` | `15` | ⚠️ |
| `MOVIE_SLEEP` | 电影间延迟（秒） | `15` | `15` | ⚠️ |
| `CF_TURNSTILE_COOLDOWN` | Cloudflare Turnstile 冷却时间（秒） | `30` | `30` | ⚠️ |
| `PHASE_TRANSITION_COOLDOWN` | Phase 转换冷却时间（秒） | `60` | `60` | ⚠️ |
| `FALLBACK_COOLDOWN` | Fallback 冷却时间（秒） | `30` | `30` | ⚠️ |

### 日志相关
| Variable 名称 | 说明 | 参考值（来自 config.py） | 默认值 | 必需 |
|--------------|------|-------------------------|--------|------|
| `LOG_LEVEL` | 日志级别 | `DEBUG` | `INFO` | ⚠️ |
| `SPIDER_LOG_FILE` | Spider 日志文件路径 | `logs/spider.log` | `logs/spider.log` | ⚠️ |
| `UPLOADER_LOG_FILE` | Uploader 日志文件路径 | `logs/qb_uploader.log` | `logs/qb_uploader.log` | ⚠️ |
| `PIPELINE_LOG_FILE` | Pipeline 日志文件路径 | `logs/pipeline.log` | `logs/pipeline.log` | ⚠️ |
| `EMAIL_NOTIFICATION_LOG_FILE` | 邮件通知日志文件路径 | `logs/email_notification.log` | `logs/email_notification.log` | ⚠️ |

### 解析相关
| Variable 名称 | 说明 | 参考值（来自 config.py） | 默认值 | 必需 |
|--------------|------|-------------------------|--------|------|
| `IGNORE_RELEASE_DATE_FILTER` | 忽略发布日期过滤 | `False` | `False` | ⚠️ |

### 文件路径相关
| Variable 名称 | 说明 | 参考值（来自 config.py） | 默认值 | 必需 |
|--------------|------|-------------------------|--------|------|
| `REPORTS_DIR` | 报告目录 | `reports` | `reports` | ⚠️ |
| `DAILY_REPORT_DIR` | 日常报告目录 | `reports/DailyReport` | `reports/DailyReport` | ⚠️ |
| `AD_HOC_DIR` | AdHoc 报告目录 | `reports/AdHoc` | `reports/AdHoc` | ⚠️ |
| `PARSED_MOVIES_CSV` | 已解析电影历史文件 | `parsed_movies_history.csv` | `parsed_movies_history.csv` | ⚠️ |

### PikPak 相关
| Variable 名称 | 说明 | 参考值（来自 config.py） | 默认值 | 必需 |
|--------------|------|-------------------------|--------|------|
| `PIKPAK_LOG_FILE` | PikPak 日志文件路径 | `logs/pikpak_bridge.log` | `logs/pikpak_bridge.log` | ⚠️ |
| `PIKPAK_REQUEST_DELAY` | PikPak 请求延迟（秒） | `3` | `3` | ⚠️ |

### qBittorrent File Filter 相关
| Variable 名称 | 说明 | 参考值 | 默认值 | 必需 |
|--------------|------|--------|--------|------|
| `QB_FILE_FILTER_MIN_SIZE_MB` | 文件过滤最小大小（MB） | `50` | `50` | ⚠️ |
| `QB_FILE_FILTER_LOG_FILE` | File Filter 日志文件路径 | `logs/qb_file_filter.log` | `logs/qb_file_filter.log` | ⚠️ |

---

## 📋 配置步骤

### 1. 创建环境
在 GitHub 仓库中：
1. 进入 **Settings** → **Environments**
2. 创建两个环境：
   - `Production`
   - `Development`

### 2. 配置 Secrets
对于每个环境（Production 和 Development）：
1. 进入环境设置页面
2. 在 **Secrets** 部分，添加上述所有 Secrets
3. 注意：`PROXY_POOL_JSON` 需要输入完整的 JSON 字符串（单行或压缩格式）

### 3. 配置 Variables
对于每个环境（Production 和 Development）：
1. 在 **Variables** 部分，添加上述所有 Variables
2. 注意：`PROXY_MODULES_JSON` 需要输入 JSON 数组格式的字符串

### 4. 特殊配置说明

#### PROXY_POOL_JSON 配置
这是一个 JSON 数组，需要压缩为单行字符串。示例：
```json
[{"name":"Miraculous Fortress","http":"http://tedwu:No.25_Aminor@139.99.124.231:12300","https":"http://tedwu:No.25_Aminor@139.99.124.231:12300"},{"name":"Singapore-ARM1","http":"http://tedwu:No.25_Aminor@140.245.123.124:12300","https":"http://tedwu:No.25_Aminor@140.245.123.124:12300"},{"name":"Singapore-ARM2","http":"http://tedwu:No.25_Aminor@129.150.62.19:12300","https":"http://tedwu:No.25_Aminor@129.150.62.19:12300"}]
```

#### PROXY_MODULES_JSON 配置
这是一个 JSON 数组字符串。示例：
```json
["spider_index", "spider_detail"]
```

#### DEPLOY_KEY 配置
需要生成 SSH 密钥对：
```bash
ssh-keygen -t ed25519 -C "github-actions" -f deploy_key
```
- 将 `deploy_key.pub` 添加到 GitHub 仓库的 Deploy keys（Settings → Deploy keys）
- 将 `deploy_key`（私钥）的内容添加到 `DEPLOY_KEY` Secret

---

## ✅ 必需性说明

- ✅ **必需**: 必须配置，否则工作流无法正常运行
- ⚠️ **可选**: 有默认值，可以不配置，但建议根据实际情况配置
- - **不适用**: 该配置项是 Secret 而不是 Variable，或反之

---

## 🔄 环境差异建议

虽然两个环境使用相同的配置项，但建议：

### Production 环境
- `LOG_LEVEL`: 建议使用 `INFO` 或 `WARNING`
- `START_PAGE` / `END_PAGE`: 根据实际需求设置
- `IGNORE_RELEASE_DATE_FILTER`: 建议 `False`（只抓取今天/昨天的）

### Development 环境
- `LOG_LEVEL`: 建议使用 `DEBUG` 以便调试
- `START_PAGE` / `END_PAGE`: 建议设置较小的范围（如 1-3）用于测试
- `IGNORE_RELEASE_DATE_FILTER`: 可以设置为 `True` 用于测试

---

## 📝 注意事项

1. **密码中的特殊字符**: 如果密码包含特殊字符（如 `@`, `#`, `%` 等），在 `PROXY_POOL_JSON` 中需要进行 URL 编码
2. **JSON 格式**: `PROXY_POOL_JSON` 和 `PROXY_MODULES_JSON` 必须是有效的 JSON 格式
3. **空值处理**: 如果某个 Variable 需要为空，可以在 GitHub 中设置为空字符串，或使用占位符 `__EMPTY__`
4. **环境隔离**: Production 和 Development 环境应该使用不同的配置值，避免测试影响生产环境

