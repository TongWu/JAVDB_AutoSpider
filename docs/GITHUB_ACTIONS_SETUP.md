# GitHub Actions Setup Guide

## 概述

本项目包含4个 GitHub Actions 工作流：

1. **docker-build.yml** - 构建测试（每次推送时运行）
2. **docker-test.yml** - 完整测试（CI测试）
3. **docker-publish-dockerhub.yml** - 发布到 Docker Hub（需要配置 secrets）
4. **docker-publish-ghcr.yml** - 发布到 GitHub Container Registry（自动配置）

## 工作流说明

### 1. Docker Build（基础构建）

**触发条件：**
- 推送到 main/master 分支
- 创建 Pull Request
- 手动触发

**功能：**
- 多平台构建（amd64, arm64）
- 自动生成标签
- 缓存优化
- 基础测试

### 2. Docker Test（CI 测试）

**触发条件：**
- 推送到 main/master/develop 分支
- 创建 Pull Request
- 手动触发

**功能：**
- 构建镜像
- 测试 Python 环境
- 验证文件完整性
- 语法检查
- 显示镜像大小

### 3. Publish to Docker Hub

**触发条件：**
- 推送 tag（格式：v*.*.* ，如 v1.0.0）
- 手动触发

**功能：**
- 多平台构建和推送
- 自动生成版本标签
- 更新 Docker Hub 描述

**需要配置的 Secrets：**
- `DOCKERHUB_USERNAME` - Docker Hub 用户名
- `DOCKERHUB_TOKEN` - Docker Hub 访问令牌

### 4. Publish to GitHub Container Registry

**触发条件：**
- 推送到 main/master 分支
- 推送 tag（格式：v*.*.*）
- 手动触发

**功能：**
- 多平台构建和推送
- 自动生成版本标签
- 使用 GitHub 内置认证（无需配置）

## 配置步骤

### 方式一：发布到 GitHub Container Registry（推荐，无需额外配置）

GHCR 工作流使用 GitHub 内置的 `GITHUB_TOKEN`，无需额外配置。

#### 使用步骤：

1. **确保 GitHub Actions 已启用**
   - 进入 Repository Settings > Actions > General
   - 确保 "Allow all actions and reusable workflows" 已选中

2. **设置 GHCR 权限**
   - 进入 Repository Settings > Actions > General
   - 在 "Workflow permissions" 部分
   - 选择 "Read and write permissions"
   - 勾选 "Allow GitHub Actions to create and approve pull requests"

3. **推送代码或创建 tag**
   ```bash
   # 推送到 main 分支（会创建 latest 标签）
   git push origin main
   
   # 或创建版本 tag
   git tag v1.0.0
   git push origin v1.0.0
   ```

4. **查看构建状态**
   - 进入 Repository > Actions 查看工作流运行状态

5. **拉取镜像**
   ```bash
   # 拉取 latest 版本
   docker pull ghcr.io/your-username/javdb-autospider:latest
   
   # 拉取特定版本
   docker pull ghcr.io/your-username/javdb-autospider:v1.0.0
   ```

### 方式二：发布到 Docker Hub

如果你想发布到 Docker Hub，需要配置 secrets。

#### 1. 创建 Docker Hub Access Token

1. 登录 [Docker Hub](https://hub.docker.com/)
2. 点击右上角头像 > Account Settings
3. 选择 Security > New Access Token
4. 输入描述（如：GitHub Actions）
5. 选择权限：Read, Write, Delete
6. 复制生成的 token（只显示一次！）

#### 2. 在 GitHub 添加 Secrets

1. 进入你的 GitHub 仓库
2. 点击 Settings > Secrets and variables > Actions
3. 点击 "New repository secret"
4. 添加以下 secrets：

| Secret Name | Value | 说明 |
|-------------|-------|------|
| `DOCKERHUB_USERNAME` | 你的 Docker Hub 用户名 | 如：johndoe |
| `DOCKERHUB_TOKEN` | 刚才创建的 Access Token | 长字符串 |

#### 3. 触发构建

```bash
# 创建版本 tag
git tag v1.0.0
git push origin v1.0.0
```

#### 4. 拉取镜像

```bash
# 拉取 latest 版本
docker pull your-username/javdb-autospider:latest

# 拉取特定版本
docker pull your-username/javdb-autospider:v1.0.0
```

## 使用方法

### 自动触发

**推送代码到 main/master 分支：**
```bash
git add .
git commit -m "Update code"
git push origin main
```
- 触发：docker-build.yml, docker-test.yml, docker-publish-ghcr.yml

**创建和推送版本 tag：**
```bash
git tag v1.0.0
git push origin v1.0.0
```
- 触发：所有工作流
- 生成标签：`v1.0.0`, `v1.0`, `v1`, `latest`

**创建 Pull Request：**
- 触发：docker-build.yml, docker-test.yml

### 手动触发

1. 进入 Repository > Actions
2. 选择要运行的工作流
3. 点击 "Run workflow" 按钮
4. 选择分支
5. 点击绿色的 "Run workflow" 按钮

## 版本标签说明

当你推送 tag（如 `v1.2.3`）时，会自动生成多个标签：

| Tag | 说明 | 示例 |
|-----|------|------|
| `v1.2.3` | 完整版本号 | `v1.2.3` |
| `v1.2` | 主版本号.次版本号 | `v1.2` |
| `v1` | 主版本号 | `v1` |
| `latest` | 最新版本 | `latest` |
| `main-abc1234` | 分支名-提交SHA | `main-abc1234` |

## 使用构建的镜像

### 从 GitHub Container Registry 拉取（推荐）

```bash
# 拉取最新版本
docker pull ghcr.io/your-username/javdb-autospider:latest

# 使用镜像
docker run -d \
  --name javdb-spider \
  -v ./config.py:/app/config.py:ro \
  -v ./logs:/app/logs \
  -v "./Ad Hoc:/app/Ad Hoc" \
  -v "./Daily Report:/app/Daily Report" \
  -e CRON_SPIDER="0 3 * * *" \
  ghcr.io/your-username/javdb-autospider:latest
```

### 从 Docker Hub 拉取

```bash
# 拉取最新版本
docker pull your-username/javdb-autospider:latest

# 使用镜像
docker run -d \
  --name javdb-spider \
  -v ./config.py:/app/config.py:ro \
  -v ./logs:/app/logs \
  -v "./Ad Hoc:/app/Ad Hoc" \
  -v "./Daily Report:/app/Daily Report" \
  -e CRON_SPIDER="0 3 * * *" \
  your-username/javdb-autospider:latest
```

### 更新 docker-compose.yml

如果使用 docker-compose，更新镜像源：

```yaml
services:
  javdb-spider:
    # 使用 GHCR 镜像
    image: ghcr.io/your-username/javdb-autospider:latest
    
    # 或使用 Docker Hub 镜像
    # image: your-username/javdb-autospider:latest
    
    # 注释掉 build 部分
    # build:
    #   context: .
    #   dockerfile: docker/Dockerfile
```

## 查看构建状态

### GitHub Actions 页面

1. 进入 Repository > Actions
2. 查看工作流运行历史
3. 点击具体的运行查看详细日志

### 添加徽章到 README

在 README.md 中添加构建状态徽章：

```markdown
![Docker Build](https://github.com/your-username/JAVDB_AutoSpider/actions/workflows/docker-build.yml/badge.svg)
![Docker Test](https://github.com/your-username/JAVDB_AutoSpider/actions/workflows/docker-test.yml/badge.svg)
```

## 多平台支持

所有工作流都支持多平台构建：
- `linux/amd64` - x86_64 架构（常见服务器、PC）
- `linux/arm64` - ARM 64位架构（Apple Silicon, ARM 服务器）

拉取时会自动选择匹配的平台。

## 故障排查

### 构建失败

**检查日志：**
1. 进入 Actions > 失败的工作流
2. 查看错误信息
3. 检查是否是依赖问题或语法错误

**常见问题：**
- 缺少文件：确保所有必需文件已提交
- 依赖安装失败：检查 requirements.txt
- Python 语法错误：本地测试代码

### 推送到 Docker Hub 失败

**检查 Secrets：**
1. Settings > Secrets and variables > Actions
2. 确认 `DOCKERHUB_USERNAME` 和 `DOCKERHUB_TOKEN` 已正确配置
3. 确认 token 有写权限
4. 尝试重新生成 token

**权限问题：**
- 确保 Docker Hub token 有 Read, Write 权限
- 确保仓库名称正确

### 推送到 GHCR 失败

**检查权限：**
1. Settings > Actions > General
2. 确认 "Workflow permissions" 设置为 "Read and write permissions"

**仓库可见性：**
- 如果仓库是私有的，确保你有访问权限
- GHCR 镜像默认继承仓库的可见性

## 最佳实践

### 版本管理

```bash
# 开发版本
git commit -m "Add new feature"
git push origin develop  # 触发测试

# 发布版本
git checkout main
git merge develop
git tag v1.0.0
git push origin main --tags  # 触发发布
```

### 使用语义化版本

- `v1.0.0` - 主要版本（不兼容的更改）
- `v1.1.0` - 次要版本（新功能，向后兼容）
- `v1.1.1` - 补丁版本（bug 修复）

### 安全建议

1. **不要提交敏感信息**
   - config.py 应该在 .gitignore 中
   - 使用 config.py.example 作为模板

2. **保护 Docker Hub Token**
   - 只在 GitHub Secrets 中存储
   - 定期轮换 token

3. **审查 Pull Requests**
   - 确保 CI 测试通过后再合并

## 监控和维护

### 定期检查

- 每月检查依赖更新
- 监控构建时间和镜像大小
- 查看 GitHub Actions 使用配额

### 缓存管理

工作流使用 GitHub Actions 缓存（type=gha）来加速构建：
- 自动管理，无需手动清理
- 7天未使用会自动删除

## 参考链接

- [GitHub Actions 文档](https://docs.github.com/en/actions)
- [Docker Build Action](https://github.com/docker/build-push-action)
- [Docker Hub 文档](https://docs.docker.com/docker-hub/)
- [GHCR 文档](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)

---

## 快速参考

```bash
# 本地测试构建
docker build -t javdb-autospider:test .

# 创建版本并触发发布
git tag v1.0.0
git push origin v1.0.0

# 拉取构建的镜像
docker pull ghcr.io/your-username/javdb-autospider:latest

# 手动触发工作流
# 在 GitHub Actions 页面点击 "Run workflow"
```

