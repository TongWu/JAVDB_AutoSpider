# GitHub Actions 快速参考

## 🚀 最快开始（使用 GHCR，无需配置）

### 1. 启用 GitHub Actions

Repository Settings > Actions > General > Workflow permissions:
- ✅ 选择 "Read and write permissions"
- ✅ 保存

### 2. 推送代码

```bash
git add .
git commit -m "Enable Docker automation"
git push origin main
```

### 3. 查看构建

进入 Repository > Actions 查看进度

### 4. 使用镜像

```bash
# 替换 your-username 为你的 GitHub 用户名
docker pull ghcr.io/your-username/javdb_autospider:latest
```

完成！✨

---

## 📋 工作流触发条件

| 操作 | 触发的工作流 | 结果 |
|------|-------------|------|
| `git push origin main` | Build + Test + GHCR | 构建并发布到 GHCR（latest） |
| `git push origin v1.0.0` | 全部4个 | 构建并发布到所有平台 |
| 创建 Pull Request | Build + Test | 只测试，不发布 |
| 手动触发 | 选择的工作流 | 自定义运行 |

---

## 🏷️ 版本标签命名

推送 tag 格式：`v主版本.次版本.补丁版本`

```bash
# 示例
git tag v1.0.0     # 首次发布
git tag v1.1.0     # 新功能
git tag v1.1.1     # Bug 修复
git push origin --tags
```

自动生成的镜像标签：
- `v1.0.0` （完整版本）
- `v1.0` （主+次版本）
- `v1` （主版本）
- `latest` （最新版）

---

## 🔧 配置 Docker Hub（可选）

只需要2个 Secrets：

1. Settings > Secrets and variables > Actions > New secret

| Name | Value |
|------|-------|
| `DOCKERHUB_USERNAME` | 你的用户名 |
| `DOCKERHUB_TOKEN` | [创建 token](https://hub.docker.com/settings/security) |

2. 推送 tag 自动发布：
```bash
git tag v1.0.0
git push origin v1.0.0
```

---

## 📦 使用构建的镜像

### 方式一：更新 docker-compose.yml

```yaml
services:
  javdb-spider:
    # 使用 GHCR
    image: ghcr.io/your-username/javdb_autospider:latest
    
    # 删除 build 配置
    # build:
    #   context: .
```

### 方式二：直接运行

```bash
docker pull ghcr.io/your-username/javdb_autospider:latest
docker-compose up -d
```

---

## 🎯 常用命令

```bash
# 查看所有 tag
git tag -l

# 创建并推送 tag
git tag v1.0.0
git push origin v1.0.0

# 删除 tag（本地+远程）
git tag -d v1.0.0
git push origin :refs/tags/v1.0.0

# 查看镜像
docker images | grep javdb-autospider

# 清理本地镜像
docker rmi $(docker images 'ghcr.io/*javdb*' -q)
```

---

## ✅ 检查清单

构建前确认：
- [ ] 所有代码已提交
- [ ] requirements.txt 已更新
- [ ] config.py 在 .gitignore 中
- [ ] 测试本地构建：`docker build -t test .`
- [ ] GHCR 权限已配置（Read and write）

发布到 Docker Hub：
- [ ] `DOCKERHUB_USERNAME` 已配置
- [ ] `DOCKERHUB_TOKEN` 已配置
- [ ] Tag 格式正确（v*.*.*）

---

## 🐛 故障排查

### 构建失败
```bash
# 本地测试
docker build -t test .
```

### 推送失败
- 检查 Actions > General > Workflow permissions
- 确认是 "Read and write permissions"

### 找不到镜像
```bash
# 检查构建状态
# 进入 Repository > Actions

# 确认 tag 正确
git ls-remote --tags origin
```

---

## 📚 完整文档

详细配置请查看：[GITHUB_ACTIONS_SETUP.md](./GITHUB_ACTIONS_SETUP.md)

