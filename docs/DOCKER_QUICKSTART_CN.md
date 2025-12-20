# JAVDB AutoSpider Docker 使用说明

## 简介

此 Docker 方案可以让你将 JAVDB AutoSpider 打包成容器，自动运行定时任务。

## 快速开始

### 方式一：使用自动脚本（推荐）

```bash
# 运行自动化部署脚本
./docker/docker-build.sh
```

脚本会自动：
1. 检查 Docker 和 Docker Compose 是否安装
2. 创建必要的目录
3. 帮你配置 config.py 和 .env 文件
4. 构建 Docker 镜像
5. 启动容器

### 方式二：手动配置

#### 1. 准备配置文件

```bash
# 创建必要的目录
mkdir -p logs "Ad Hoc" "Daily Report"

# 复制配置文件
cp config.py.example config.py
cp env.example .env

# 编辑配置文件
nano config.py  # 填入你的配置
nano .env       # 配置定时任务
```

#### 2. 构建和运行

```bash
# 构建镜像
docker-compose build

# 启动容器
docker-compose up -d
```

## 配置说明

### 1. config.py 配置

这是主配置文件，包含：
- 网站 URL 和代理设置
- 爬虫参数（页数、延迟等）
- 认证 Cookie
- 其他业务逻辑配置

**重要**: 这个文件是只读挂载的，修改后需要重启容器：
```bash
docker-compose restart
```

### 2. .env 定时任务配置

配置各个脚本的运行时间。格式：`分 时 日 月 周`

```bash
# 爬虫任务 - 每天凌晨3点运行
CRON_SPIDER=0 3 * * *
SPIDER_COMMAND=cd /app && /usr/local/bin/python Javdb_Spider.py --use-proxy >> /var/log/cron.log 2>&1

# Pipeline 任务 - 每天凌晨4点运行
CRON_PIPELINE=0 4 * * *
PIPELINE_COMMAND=cd /app && /usr/local/bin/python pipeline_run_and_notify.py >> /var/log/cron.log 2>&1

# qBittorrent 上传 - 每天凌晨3点30分运行
CRON_QBTORRENT=30 3 * * *
QBTORRENT_COMMAND=cd /app && /usr/local/bin/python qbtorrent_uploader.py >> /var/log/cron.log 2>&1

# PikPak 同步 - 每天凌晨5点运行
CRON_PIKPAK=0 5 * * *
PIKPAK_COMMAND=cd /app && /usr/local/bin/python pikpak_bridge.py >> /var/log/cron.log 2>&1
```

#### 常用时间格式示例

```
*/5 * * * *     # 每5分钟
0 * * * *       # 每小时
0 */6 * * *     # 每6小时
0 2 * * *       # 每天凌晨2点
30 3 * * *      # 每天凌晨3点30分
0 0 * * 0       # 每周日凌晨0点
0 0 1 * *       # 每月1号凌晨0点
```

#### 启用/禁用任务

```bash
ENABLE_SPIDER=true      # 启用爬虫任务
ENABLE_PIPELINE=false   # 禁用 Pipeline 任务
ENABLE_QBTORRENT=true   # 启用 qBittorrent 上传
ENABLE_PIKPAK=false     # 禁用 PikPak 同步
```

## 常用命令

### 查看日志

```bash
# 查看容器日志
docker-compose logs -f

# 查看定时任务日志
docker exec javdb-spider tail -f /var/log/cron.log

# 查看应用日志
docker exec javdb-spider tail -f /app/logs/Javdb_Spider.log
```

### 容器管理

```bash
# 启动容器
docker-compose up -d

# 停止容器
docker-compose down

# 重启容器
docker-compose restart

# 查看容器状态
docker-compose -f docker/docker-compose.yml ps

# 查看容器资源使用
docker stats javdb-spider
```

### 手动运行脚本

```bash
# 手动运行爬虫（使用代理）
docker exec javdb-spider python Javdb_Spider.py --use-proxy

# 手动运行爬虫（指定页数）
docker exec javdb-spider python Javdb_Spider.py --start-page 1 --end-page 5 --use-proxy

# 手动运行 Pipeline
docker exec javdb-spider python pipeline_run_and_notify.py

# 进入容器 Shell
docker exec -it javdb-spider bash
```

### 查看定时任务配置

```bash
# 查看当前的 crontab 配置
docker exec javdb-spider crontab -l

# 查看 cron 配置文件
docker exec javdb-spider cat /etc/cron.d/javdb-spider
```

## 目录结构

容器内的目录映射：

| 宿主机路径 | 容器内路径 | 说明 | 权限 |
|-----------|-----------|------|------|
| `./config.py` | `/app/config.py` | 配置文件 | 只读 |
| `./logs/` | `/app/logs/` | 日志目录 | 读写 |
| `./Ad Hoc/` | `/app/Ad Hoc/` | 临时报告目录 | 读写 |
| `./Daily Report/` | `/app/Daily Report/` | 每日报告目录 | 读写 |

## 更新和维护

### 更新代码

```bash
# 拉取最新代码
git pull

# 重新构建镜像（不使用缓存）
docker-compose build --no-cache

# 重启容器
docker-compose up -d
```

### 备份数据

```bash
# 备份所有重要数据
tar -czf backup-$(date +%Y%m%d).tar.gz \
  config.py \
  .env \
  logs/ \
  "Ad Hoc/" \
  "Daily Report/"
```

### 清理旧日志

```bash
# 删除30天前的日志
docker exec javdb-spider find /app/logs -name "*.log" -mtime +30 -delete
```

### 重建容器

```bash
# 停止并删除容器
docker-compose down

# 删除旧镜像
docker rmi javdb-spider_javdb-spider

# 重新构建和启动
docker-compose build
docker-compose up -d
```

## 故障排查

### 容器无法启动

检查 config.py 是否存在：
```bash
ls -la config.py
```

如果不存在，从示例文件创建：
```bash
cp config.py.example config.py
# 编辑并填入你的配置
nano config.py
```

### 定时任务没有运行

1. 检查 crontab 配置：
```bash
docker exec javdb-spider crontab -l
```

2. 检查 cron 服务是否运行：
```bash
docker exec javdb-spider ps aux | grep cron
```

3. 查看 cron 日志：
```bash
docker exec javdb-spider tail -100 /var/log/cron.log
```

### 权限问题

如果遇到文件权限问题：
```bash
# 修改目录权限
chmod -R 755 logs "Ad Hoc" "Daily Report"

# 重启容器
docker-compose restart
```

### Python 依赖问题

如果添加了新的依赖：
1. 更新 `requirements.txt`
2. 重新构建镜像：
```bash
docker-compose build --no-cache
docker-compose up -d
```

### 查看详细错误

```bash
# 查看容器详细信息
docker inspect javdb-spider

# 查看容器启动日志
docker logs javdb-spider

# 进入容器调试
docker exec -it javdb-spider bash
cd /app
python Javdb_Spider.py --help
```

## 高级配置

### 修改时区

在 `.env` 中设置：
```bash
TZ=Asia/Shanghai    # 上海时间
# TZ=Asia/Tokyo     # 东京时间
# TZ=America/New_York  # 纽约时间
```

### 资源限制

在 `docker/docker-compose.yml` 中取消注释并调整：
```yaml
deploy:
  resources:
    limits:
      cpus: '2'        # 限制使用2个CPU核心
      memory: 2G       # 限制使用2GB内存
```

### 添加自定义定时任务

在 `.env` 中添加：
```bash
# 自定义清理任务 - 每周日凌晨1点运行
CRON_CLEANUP=0 1 * * 0
CLEANUP_COMMAND=cd /app && /usr/local/bin/python cleanup_script.py >> /var/log/cron.log 2>&1
```

## 安全提示

1. **不要提交敏感文件**：
   - `config.py` - 包含认证信息
   - `.env` - 包含配置信息
   
2. **使用只读挂载**：
   - config.py 使用只读挂载防止意外修改

3. **定期更新**：
   - 定期更新基础镜像以获取安全补丁
   ```bash
   docker-compose build --pull --no-cache
   ```

4. **备份重要数据**：
   - 定期备份 config.py 和数据目录

## 文件说明

- `docker/Dockerfile` - Docker 镜像构建文件
- `docker/docker-compose.yml` - Docker Compose 配置文件
- `env.example` - 环境变量示例文件
- `docker/docker-entrypoint.sh` - 容器启动脚本
- `.dockerignore` - Docker 构建忽略文件
- `docker/docker-build.sh` - 自动化部署脚本
- `DOCKER_README.md` - 英文使用说明
- `DOCKER_使用说明.md` - 中文使用说明（本文件）

## 技术支持

遇到问题时：
1. 查看日志：`docker-compose logs -f`
2. 查看 cron 日志：`docker exec javdb-spider tail -f /var/log/cron.log`
3. 查看应用日志：`cat logs/Javdb_Spider.log`
4. 参考主 README.md 了解应用本身的问题

## 总结

使用 Docker 部署的优势：
- ✅ 环境一致性 - 避免依赖问题
- ✅ 易于部署 - 一键启动
- ✅ 自动化运行 - 定时任务自动执行
- ✅ 资源隔离 - 不影响主机环境
- ✅ 易于维护 - 简单的更新和回滚

祝使用愉快！🎉

