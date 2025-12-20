# Docker Quick Start Guide

## 一键部署（推荐）

```bash
# 1. 运行自动化脚本
./docker-build.sh

# 2. 查看日志
docker-compose logs -f
```

## 手动部署

```bash
# 1. 准备配置文件
cp config.py.example config.py
cp env.example .env

# 2. 编辑配置
nano config.py  # 填入你的配置
nano .env       # 配置定时任务

# 3. 创建目录
mkdir -p logs "Ad Hoc" "Daily Report"

# 4. 构建并启动
docker-compose build
docker-compose up -d
```

## 常用命令速查

| 操作 | 命令 |
|------|------|
| 启动容器 | `docker-compose up -d` |
| 停止容器 | `docker-compose down` |
| 重启容器 | `docker-compose restart` |
| 查看日志 | `docker-compose logs -f` |
| 查看 cron 日志 | `docker exec javdb-spider tail -f /var/log/cron.log` |
| 手动运行爬虫 | `docker exec javdb-spider python Javdb_Spider.py --use-proxy` |
| 进入容器 | `docker exec -it javdb-spider bash` |
| 查看 crontab | `docker exec javdb-spider crontab -l` |
| 重新构建 | `docker-compose build --no-cache` |
| 查看容器状态 | `docker-compose ps` |

## 配置定时任务

编辑 `.env` 文件：

```bash
# 爬虫任务 - 每天凌晨3点
CRON_SPIDER=0 3 * * *

# Pipeline - 每天凌晨4点
CRON_PIPELINE=0 4 * * *

# qBittorrent - 每天凌晨3:30
CRON_QBTORRENT=30 3 * * *

# PikPak - 每天凌晨5点
CRON_PIKPAK=0 5 * * *
```

修改后重启容器：
```bash
docker-compose restart
```

## 时间格式示例

```
0 3 * * *       # 每天凌晨3点
*/30 * * * *    # 每30分钟
0 */6 * * *     # 每6小时
0 2 * * 0       # 每周日凌晨2点
```

## 故障排查

### 容器启动失败
```bash
# 检查配置文件
ls -la config.py

# 查看错误日志
docker-compose logs
```

### 定时任务不执行
```bash
# 查看 crontab
docker exec javdb-spider crontab -l

# 查看 cron 日志
docker exec javdb-spider tail -100 /var/log/cron.log
```

### 权限问题
```bash
chmod -R 755 logs "Ad Hoc" "Daily Report"
docker-compose restart
```

## 文件清单

创建的 Docker 相关文件：

- ✅ `Dockerfile` - 镜像构建文件
- ✅ `docker-compose.yml` - Compose 配置
- ✅ `env.example` - 环境变量示例
- ✅ `docker-entrypoint.sh` - 启动脚本
- ✅ `.dockerignore` - 构建忽略文件
- ✅ `docker-build.sh` - 自动化部署脚本
- ✅ `DOCKER_README.md` - 英文文档
- ✅ `DOCKER_使用说明.md` - 中文文档
- ✅ `DOCKER_QUICKSTART.md` - 快速参考（本文件）

## 更新方法

```bash
# 拉取代码
git pull

# 重新构建
docker-compose build --no-cache

# 重启容器
docker-compose up -d
```

## 备份方法

```bash
tar -czf backup-$(date +%Y%m%d).tar.gz \
  config.py .env logs/ "Ad Hoc/" "Daily Report/"
```

---

📖 详细文档请参考：
- 中文：`DOCKER_使用说明.md`
- English: `DOCKER_README.md`

