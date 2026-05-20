# Docker 部署

本指南介绍如何使用独立容器或 Docker Compose 运行 JavDB AutoSpider。

## 前置要求

- Docker 20.10+
- Docker Compose v2（用于 Compose 方式）
- 已配置的 `config.py`（参见[配置参考](configuration.md)）

## 使用 Docker Compose 快速开始（推荐）

```bash
# 克隆仓库
git clone https://github.com/TongWu/JAVDB_AutoSpider_CICD.git
cd JAVDB_AutoSpider_CICD

# 准备配置
cp config.py.example config.py
cp docker/.env.example .env
# 编辑 config.py 填入你的设置（proxy、qBittorrent 等）

# 构建并启动
docker-compose -f docker/docker-compose.yml build
docker-compose -f docker/docker-compose.yml up -d
```

或使用自动构建脚本：

```bash
./docker/docker-build.sh
```

## 独立 Docker 运行

```bash
docker run -d \
  --name javdb-spider \
  --restart unless-stopped \
  -v $(pwd)/config.py:/app/config.py:ro \
  -v $(pwd)/logs:/app/logs \
  -v $(pwd)/Ad\ Hoc:/app/Ad\ Hoc \
  -v $(pwd)/Daily\ Report:/app/Daily\ Report \
  --env-file .env \
  ghcr.io/YOUR_USERNAME/javdb-autospider:latest
```

Docker 镜像使用多阶段构建：Rust 构建阶段编译 `javdb_rust_core` 扩展，运行时阶段仅包含编译后的 wheel。

## 卷挂载

| 挂载路径 | 用途 | 模式 |
|-------|---------|------|
| `config.py:/app/config.py` | 主配置文件 | 只读 |
| `logs:/app/logs` | 日志文件 | 读写 |
| `reports/AdHoc:/app/reports/AdHoc` | 临时抓取结果 | 读写 |
| `reports/DailyReport:/app/reports/DailyReport` | 每日报告输出 | 读写 |

## 环境变量

设置 `TZ` 以匹配你的时区（默认：`Asia/Shanghai`）。存储和写入模式相关变量同样适用 —— 完整列表请参见[配置参考](configuration.md)。

## 容器管理

### 基础命令

```bash
# 查看容器日志
docker logs -f javdb-spider

# 查看 cron 日志
docker exec javdb-spider tail -f /var/log/cron.log

# 手动运行爬虫
docker exec javdb-spider python3 -m apps.cli.spider --use-proxy

# 手动运行流水线
docker exec javdb-spider python3 -m apps.cli.pipeline

# 在容器内执行命令
docker exec -it javdb-spider bash

# 停止 / 启动 / 重启
docker stop javdb-spider
docker start javdb-spider
docker restart javdb-spider
```

### Docker Compose 命令

```bash
docker-compose -f docker/docker-compose.yml up -d       # 启动
docker-compose -f docker/docker-compose.yml down         # 停止
docker-compose -f docker/docker-compose.yml logs -f      # 日志
docker-compose -f docker/docker-compose.yml restart      # 重启

# 重新构建（代码或 Dockerfile 变更后）
docker-compose -f docker/docker-compose.yml build --no-cache
docker-compose -f docker/docker-compose.yml up -d
```

## 定时任务调度

编辑 `.env` 文件以配置定时任务：

```bash
# 爬虫每天凌晨 3:00 运行（容器时区）
CRON_SPIDER=0 3 * * *
SPIDER_COMMAND=cd /app && /usr/local/bin/python -m apps.cli.spider --use-proxy >> /var/log/cron.log 2>&1

# 流水线每天凌晨 4:00 运行
CRON_PIPELINE=0 4 * * *
PIPELINE_COMMAND=cd /app && /usr/local/bin/python -m apps.cli.pipeline >> /var/log/cron.log 2>&1
```

修改 `.env` 后，重启容器：

```bash
docker-compose -f docker/docker-compose.yml restart
```

更多调度示例和参数参考请参见 `docker/.env.example`。

## 健康检查

Compose 文件包含健康检查，用于验证 cron 守护进程是否正在运行：

```yaml
healthcheck:
  test: ["CMD", "pgrep", "cron"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 10s
```

## 故障排查

**容器立即退出：**
- 检查 `docker logs javdb-spider` 中的错误信息
- 确认 `config.py` 已正确挂载且语法有效

**定时任务未运行：**
- 检查 `docker exec javdb-spider tail -f /var/log/cron.log`
- 确认 `.env` 中的 cron 表达式有效
- 确保容器时区与预期一致（`TZ` 变量）

**挂载卷权限错误：**
- 确保宿主机目录存在且可写
- 在 Linux 上，检查宿主机与容器之间的 UID/GID 是否匹配
