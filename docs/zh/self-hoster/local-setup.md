# 本地部署指南

从零开始在本地机器上运行 JAVDB AutoSpider 的完整指南。

## 前置要求

| 要求 | 版本 | 备注 |
|---|---|---|
| Python | 3.10+（推荐 3.11） | CI 流水线使用 3.11 |
| pip | 最新版 | Python 自带 |
| Git | 任意较新版本 | 用于克隆和版本控制 |
| Rust 工具链 + maturin | 最新稳定版 | **可选** —— 仅在需要 Rust 加速扩展时安装 |

爬虫、上传器和完整流水线均可在没有 Rust 扩展的情况下工作。当 Rust 扩展不存在时，系统会静默回退到纯 Python HTML 解析。

## 步骤 1 —— 克隆仓库

```bash
git clone https://github.com/TongWu/JAVDB_AutoSpider.git
cd JAVDB_AutoSpider
```

如果仓库使用 Git LFS 存储二进制文件（SQLite 数据库），请安装 LFS 并拉取：

```bash
git lfs install
git lfs pull
```

## 步骤 2 —— 安装 Python 依赖

```bash
pip install -r requirements.txt
```

安装的主要包：

- `requests` —— 用于 JavDB 和 qBittorrent 的 HTTP 客户端
- `beautifulsoup4` + `lxml` —— HTML 解析（Python 回退方案）
- `pikpakapi` —— PikPak 云传输集成
- `curl_cffi` —— 抗 TLS 指纹的 HTTP 客户端
- `fastapi` + `uvicorn` —— REST API 服务器（可选）

### 可选额外依赖

```bash
# SOCKS5 proxy 支持
pip install "requests[socks]"

# 基于 OCR 的验证码识别（还需要 tesseract-ocr 系统包）
pip install pytesseract Pillow
```

## 步骤 3 —— 创建 config.py

```bash
cp config.py.example config.py
```

打开 `config.py` 并填入**最低要求**的设置：

### 干跑测试的最低要求

`--dry-run` 不需要任何外部凭据。`config.py.example` 中的默认值即可满足。

### 真实爬虫运行的最低要求

| 设置 | 用途 |
|---|---|
| `PROXY_MODE` | `'pool'`、`'single'` 或 `'None'`。如果可以直接访问 javdb.com，设为 `'None'`。 |
| `PROXY_POOL` | 如果 `PROXY_MODE` 不是 `'None'`，至少需要一个 proxy 条目。 |

### 完整流水线（爬虫 + qBittorrent 上传）的最低要求

| 设置 | 用途 |
|---|---|
| `QB_URL` | qBittorrent Web UI URL，例如 `'https://192.168.1.100:8080'` |
| `QB_USERNAME` | qBittorrent Web UI 用户名 |
| `QB_PASSWORD` | qBittorrent Web UI 密码 |

### 邮件通知

| 设置 | 用途 |
|---|---|
| `SMTP_SERVER` | SMTP 主机，例如 `'smtp.gmail.com'` |
| `SMTP_PORT` | SMTP 端口（587 用于 TLS） |
| `SMTP_USER` | 邮箱账户 |
| `SMTP_PASSWORD` | 应用专用密码（Gmail 不能使用登录密码） |
| `EMAIL_FROM` | 发件人地址 |
| `EMAIL_TO` | 收件人地址 |

### 自定义 URL 抓取（演员、标签等）

| 设置 | 用途 |
|---|---|
| `JAVDB_USERNAME` | JavDB 邮箱或用户名 |
| `JAVDB_PASSWORD` | JavDB 密码 |
| `GPT_API_URL` / `GPT_API_KEY` | （可选）用于验证码识别的 GPT-4o Vision API |

在首次自定义 URL 抓取前运行登录脚本：

```bash
python3 -m apps.cli.login
```

### Git 自动提交（可选）

| 设置 | 用途 |
|---|---|
| `GIT_USERNAME` | GitHub 用户名 |
| `GIT_PASSWORD` | GitHub 个人访问令牌 |
| `GIT_REPO_URL` | 仓库 HTTPS URL |
| `GIT_BRANCH` | 目标分支（通常为 `main`） |

完整的可用设置及其默认值请参见 `config.py.example` 中的注释。

## 步骤 4 —— 通过干跑验证

```bash
python3 -m apps.cli.spider --dry-run --start-page 1 --end-page 1
```

预期行为：

- 爬虫从 javdb.com 抓取一页
- 条目被解析并输出到控制台
- 不写入 CSV 文件（干跑模式）
- 成功时退出码为 0

如果出现 `No movie list found`，请检查：
1. 你的机器是否可以访问 javdb.com（在浏览器中尝试）
2. 是否需要 proxy（config.py 中的 `PROXY_MODE` 和 `PROXY_POOL`）

## 步骤 5 ——（可选）构建 Rust 扩展

Rust 扩展（`javdb_rust_core`）提供 5-10 倍更快的 HTML 解析。完全可选。

```bash
# 1. 安装 Rust 工具链（如尚未安装）
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"

# 2. 安装 maturin（Python-Rust 构建工具）
pip install maturin

# 3. 以 release 模式构建并安装扩展
cd javdb/rust_core
maturin develop --release
cd ../../..
```

验证扩展是否已加载：

```bash
python3 -c "import javdb_rust_core; print('Rust extension loaded:', javdb_rust_core.__version__)"
```

如果打印出版本字符串，说明 Rust 扩展已激活。如果抛出 `ModuleNotFoundError`，系统会自动使用纯 Python 回退方案 —— 无需任何操作。

## 步骤 6 —— 运行完整流水线

config.py 配置完成后：

```bash
# 每日模式（抓取默认的 JavDB 索引页面）
python3 -m apps.cli.pipeline

# 使用 proxy 覆盖
python3 -m apps.cli.pipeline --use-proxy

# 自定义 URL（需要先登录）
python3 -m apps.cli.login
python3 -m apps.cli.pipeline --url "https://javdb.com/actors/EvkJ"
```

## 验证清单

- [ ] `python3 --version` 显示 3.10+
- [ ] `pip install -r requirements.txt` 无错误完成
- [ ] `config.py` 存在且**未**被 git 跟踪（检查 `.gitignore`）
- [ ] `python3 -m apps.cli.spider --dry-run --start-page 1 --end-page 1` 退出码为 0
- [ ] （可选）`python3 -c "import javdb_rust_core"` 成功
- [ ] （如果使用 qBittorrent）`QB_URL`、`QB_USERNAME`、`QB_PASSWORD` 已设置且 Web UI 可访问
- [ ] （如果使用邮件）SMTP 设置已配置且测试邮件发送成功

## 首次运行后的目录结构

```
reports/
  DailyReport/          # 每日 CSV 报告（YYYY/MM/ 子目录）
  AdHoc/                # 临时 CSV 报告（YYYY/MM/ 子目录）
  history.db            # MovieHistory, TorrentHistory (SQLite)
  reports.db            # ReportSessions, stats (SQLite)
  operations.db         # RcloneInventory, DedupRecords, PikpakHistory (SQLite)
  parsed_movies_history.csv   # 旧版 CSV 历史（仍在维护）
logs/
  spider.log            # 爬虫运行日志
  qb_uploader.log       # qBittorrent 上传器日志
  pipeline.log          # 流水线编排日志
  email_notification.log
```

## 后续步骤

- **GitHub Actions 部署**：参见 [github-actions-setup.md](github-actions-setup.md) 了解通过 CI 自动每日抓取。
- **故障排查**：参见 [../ops/troubleshooting.md](../ops/troubleshooting.md) 了解常见问题和调试模式。
- **日志配置**：参见 [../ops/logging.md](../ops/logging.md) 了解日志样式和级别自定义。
