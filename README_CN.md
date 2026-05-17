# JavDB Auto Spider

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/TongWu/JAVDB_AutoSpider)
[![JavDB Daily Ingestion Pipeline](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/workflows/DailyIngestion.yml/badge.svg)](https://github.com/TongWu/JAVDB_AutoSpider_CICD/actions/workflows/DailyIngestion.yml)
[![codecov](https://codecov.io/gh/TongWu/JAVDB_AutoSpider/branch/main/graph/badge.svg)](https://codecov.io/gh/TongWu/JAVDB_AutoSpider)

一个 Python + Rust 自动化系统，用于从 javdb.com 提取种子链接并自动添加到 qBittorrent。可作为 JAV 自动刮削平台（如 [MDC-NG](https://github.com/mdc-ng/mdc-ng)）的前置摄取流水线。

[English](README.md) | 简体中文

## 功能特性

- **模块化爬虫** — `javdb/spider/` 下 14 个专用模块，抓取并过滤含字幕/今日标签的条目，按优先级提取磁力链接
- **Rust 加速**（可选）— PyO3 + maturin 扩展，HTML 解析速度提升 5-10 倍；未安装时自动回退到纯 Python
- **并行处理** — 每个代理一个工作线程的多线程详情页抓取；在代理池模式下 2+ 代理时自动激活
- **种子分类** — 基于优先级的分类：字幕 (subtitle)、hacked (UC无码破解 > UC > U无码破解 > U)、no_subtitle
- **双模式** — 日常模式（默认页面）和 Ad Hoc 模式（自定义 URL，如演员、标签等）
- **qBittorrent 集成** — 自动上传种子，支持分类、文件大小过滤和重复预防
- **PikPak 桥接** — 将旧种子从 qBittorrent 转移到 PikPak 云存储
- **历史追踪** — SQLite/Cloudflare D1 双存储后端，支持基于会话的回滚和 Pending 模式写入
- **自动化流水线** — GitHub Actions 工作流：日常摄取、自定义抓取、文件过滤、去重等
- **跨 Runner 协调**（可选）— Cloudflare Worker + Durable Objects，实现跨并发 Runner 的每代理节流和登录态共享
- **洗版检测** — 当同分类种子出现明显更大的版本时自动重新下载
- **邮件通知** — 流水线结果通知，智能区分关键错误与非关键错误

## 快速开始

```bash
# 克隆并安装
git clone https://github.com/TongWu/JAVDB_AutoSpider_CICD.git
cd JAVDB_AutoSpider_CICD
pip install -r requirements.txt

# 配置
cp config.py.example config.py
# 编辑 config.py：设置代理、qBittorrent 凭证等

# 运行
python3 -m apps.cli.spider              # 日常抓取
python3 -m apps.cli.spider --dry-run    # 预览模式（不写入文件）
python3 -m apps.cli.pipeline            # 完整流水线（爬虫 + 上传 + 通知）
```

完整安装指南请参阅 [本地安装指南](docs/zh/self-hoster/local-setup.md)。

## 架构

```
apps/
├── cli/          CLI 入口（spider、pipeline、db/、qb/、pikpak/、rclone/、notify/、ops/）
├── api/          FastAPI REST API
├── web/          Vite + Vue.js 前端
└── desktop/      Electron 桌面应用（MVP）

javdb/            Python 命名空间（PEP 420，顶层无 __init__.py）
├── spider/       爬虫运行时 + 解析/合同/URL/文件名/磁链 + 认证
├── pipeline/     数据采集编排
├── storage/      DB 层 + 会话 + 回滚 + d1 + dual + history_manager
├── proxy/        代理池 + 封禁管理 + 策略 + coordinator（Worker DO 客户端）
├── integrations/ qb/、pikpak/、rclone/、notify/
├── infra/        横切关注点：config、logging、paths、csv_writer、git、request、masking
├── migrations/   SQL + Python 迁移工具
├── legacy/       Phase-1 之前的爬虫，仅作回滚保留
└── rust_core/    Rust crate 源码（PyO3 + maturin；安装为 javdb.rust_core）
```

规范布局由 [ADR-007](docs/ai/adr/ADR-007-monorepo-restructure-2026-05.zh.md) 确立；所有旧路径（`utils/`、`api/`、`migration/`、`legacy/`、`scripts/spider/`、`scripts/ingestion/`、根目录 `compat.py`/`pipeline.py`）已在 Phase 3 移除。

## 配置

将 `config.py.example` 复制为 `config.py` 并配置：

```python
# 最小必填配置
PROXY_MODE = 'pool'                    # 'pool'、'single' 或 'None'
PROXY_POOL = [{'name': 'Proxy-1', 'http': 'http://127.0.0.1:7890', 'https': 'http://127.0.0.1:7890'}]
QB_URL = 'https://192.168.1.100:8080'  # qBittorrent Web UI
QB_USERNAME = 'admin'
QB_PASSWORD = 'password'
```

完整配置参考（60+ 选项），请参阅 [配置指南](docs/zh/self-hoster/configuration.md)。

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `STORAGE_BACKEND` | `sqlite` | `sqlite`、`d1` 或 `dual` |
| `WRITE_MODE` | `pending` | `pending`（默认）或 `audit`（遗留，2026-08-13 退役）|
| `LOG_LEVEL` | `INFO` | `DEBUG`、`INFO`、`WARNING`、`ERROR` |
| `STRICT_DUAL_WRITE` | 未设置 | 设为 `1` 则 D1 写入失败时报错 |

## 常用命令

```bash
# 爬虫
python3 -m apps.cli.spider                                    # 日常抓取
python3 -m apps.cli.spider --url "https://javdb.com/actors/EvkJ"  # Ad Hoc 模式
python3 -m apps.cli.spider --use-proxy --phase 1              # 强制代理，仅 Phase 1
python3 -m apps.cli.spider --ignore-release-date              # 所有条目（不限今日）

# 流水线
python3 -m apps.cli.pipeline                                  # 完整工作流
python3 -m apps.cli.pipeline --use-proxy                      # 带代理覆盖

# 上传
python3 -m apps.cli.qb_uploader                               # 上传到 qBittorrent
python3 -m apps.cli.qb_file_filter --min-size 100 --dry-run   # 过滤小文件

# 维护
python3 -m apps.cli.migration --help                           # 数据库迁移
python3 -m apps.cli.rollback --session-id 332                  # 回滚会话
python3 -m apps.cli.login                                      # 刷新 JavDB 登录 cookie
```

完整 CLI 参考请参阅 [CLI 参考文档](docs/zh/developer/cli-reference.md)。

## 部署方式

| 方式 | 指南 | 适用场景 |
|------|------|----------|
| **本地** | [本地安装](docs/zh/self-hoster/local-setup.md) | 开发、手动运行 |
| **GitHub Actions** | [GH Actions 部署](docs/zh/self-hoster/github-actions-setup.md) | 自动化日常流水线 |
| **Docker** | [Docker 部署](docs/zh/self-hoster/docker-deploy.md) | 自托管服务器 |
| **代理协调器** | [代理协调器部署](docs/zh/self-hoster/proxy-coordinator.md) | 多 Runner 协调 |

## GitHub Actions 工作流

| 工作流 | 触发方式 | 说明 |
|--------|----------|------|
| `DailyIngestion.yml` | Cron 12:00 UTC + 手动 | 日常抓取流水线 |
| `AdHocIngestion.yml` | 手动 | 自定义 URL 抓取 |
| `QBFileFilter.yml` | Cron 16:00 UTC + 手动 | 过滤小文件（日常摄取后 4 小时）|
| `WeeklyDedup.yml` | Cron 周日 + 手动 | Rclone 去重 |
| `RollbackD1.yml` | 手动 | 会话回滚 |
| `StaleSessionCleanup.yml` | Cron 每日 02:00 UTC | 清理卡住的会话（>48h）|
| `AuditArchive.yml` | Cron 每周一 | 归档旧审计行 |
| `Migration.yml` | 手动 | 数据库迁移 |
| `TestIngestion.yml` | 手动 | Dry-run 测试流水线 |

## 存储后端

系统通过 `STORAGE_BACKEND` 支持三种存储模式：

- **SQLite**（默认）— 本地文件位于 `reports/`（history.db、reports.db、operations.db）
- **D1** — Cloudflare D1，用于 GitHub Actions 环境
- **Dual** — 双写到 SQLite 和 D1；从 D1 读取

每次流水线运行都标记一个会话 ID，遵循生命周期：`in_progress → finalizing → committed / failed`。Pending 模式的写入仅在 commit 阶段才落盘到历史表；失败运行直接删除 pending 行。

回滚操作请参阅 [D1 回滚指南](docs/zh/ops/d1-rollback.md)。

## 文档

### 自部署者
- [本地安装](docs/zh/self-hoster/local-setup.md) — 从零开始安装
- [GitHub Actions 部署](docs/zh/self-hoster/github-actions-setup.md) — CI/CD 部署
- [Docker 部署](docs/zh/self-hoster/docker-deploy.md) — 容器部署
- [配置参考](docs/zh/self-hoster/configuration.md) — 全部 60+ 配置选项
- [代理协调器](docs/zh/self-hoster/proxy-coordinator.md) — 跨 Runner 协调
- [代理设置](docs/zh/self-hoster/proxy-setup.md) — 代理池配置
- [CloudFlare 绕过](docs/zh/self-hoster/cloudflare-bypass.md) — CF 挑战回退
- [JavDB 登录](docs/zh/self-hoster/javdb-login.md) — 会话 cookie 刷新
- [Web UI 部署](docs/zh/self-hoster/web-ui-deploy.md) — Web UI 与 API 栈
- [Rust 安装](docs/zh/self-hoster/rust-installation.md) — 可选 Rust 扩展

### 开发者
- [CLI 参考](docs/zh/developer/cli-reference.md) — 所有 CLI 命令和参数
- [API 使用指南](docs/zh/developer/api-usage-guide.md) — Python 模块和 REST API
- [历史系统](docs/zh/developer/history-system.md) — 重复预防和追踪

### 运维
- [D1 回滚](docs/zh/ops/d1-rollback.md) — 回滚 SOP 和派发矩阵
- [故障排查](docs/zh/ops/troubleshooting.md) — 常见问题和解决方案
- [日志配置](docs/zh/ops/logging.md) — 日志配置和格式
- [迁移脚本](docs/zh/ops/migration-scripts.md) — 数据库迁移工具

### 其他资源
- [CONTEXT.md](CONTEXT.md) — 领域术语词汇表
- [JavDB 登录指南](utils/login/JAVDB_LOGIN_README.md) — 登录故障排查
- [代理协调器 Worker](https://github.com/TongWu/JAVDB_AutoSpider_Proxycoordinator) — Cloudflare Worker 源码
- [DeepWiki](https://deepwiki.com/TongWu/JAVDB_AutoSpider) — AI 驱动的文档浏览器

## 安全

- 不要提交 `config.py`（已在 `.gitignore` 中排除）
- 不要提交 `reports/` 目录下的文件
- 使用 GitHub Personal Access Token 而非密码
- CI/CD 环境中将敏感值存储在环境变量中
- 会话 Cookie 会自动过期，通过 `python3 -m apps.cli.login` 刷新

## 贡献

欢迎提交 Issue 或 Pull Request！

## 许可

本项目仅供教育和个人使用。请遵守所抓取网站的服务条款。
