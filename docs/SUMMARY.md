# GitHub Actions 文件总览

## 📁 创建的文件

### GitHub Actions 工作流

位置：`.github/workflows/`

1. **docker-build.yml** - 基础构建工作流
   - 触发：推送到 main/master、PR、手动
   - 功能：多平台构建、自动标签、缓存优化
   - 不发布镜像，仅构建测试

2. **docker-test.yml** - CI 测试工作流
   - 触发：推送到 main/master/develop、PR、手动
   - 功能：完整测试流程
   - 测试项：Python 版本、依赖、文件完整性、语法检查

3. **docker-publish-dockerhub.yml** - Docker Hub 发布
   - 触发：推送 tag（v*.*.*）、手动
   - 功能：构建并推送到 Docker Hub
   - 需要配置：`DOCKERHUB_USERNAME`、`DOCKERHUB_TOKEN`

4. **docker-publish-ghcr.yml** - GHCR 发布（推荐）
   - 触发：推送到 main/master、推送 tag、手动
   - 功能：构建并推送到 GitHub Container Registry
   - 无需额外配置（使用 GitHub 内置认证）

### 文档

位置：`.github/`

5. **GITHUB_ACTIONS_SETUP.md** - 详细配置指南
   - 完整的设置步骤
   - 故障排查
   - 最佳实践

6. **QUICK_REFERENCE.md** - 快速参考卡片
   - 常用命令
   - 快速开始步骤
   - 故障排查速查

7. **README_ADDITIONS.md** - README 补充内容
   - 徽章代码
   - Docker 部署章节
   - 使用说明

8. **SUMMARY.md** - 本文件
   - 文件总览
   - 快速导航

## 🚀 快速开始

### 最简单的方式（推荐）：使用 GHCR

1. **配置权限**
   ```
   Repository Settings > Actions > General > Workflow permissions
   选择 "Read and write permissions" ✅
   ```

2. **推送代码**
   ```bash
   git add .
   git commit -m "Enable GitHub Actions"
   git push origin main
   ```

3. **等待构建完成**
   ```
   进入 Actions 标签页查看进度
   ```

4. **使用镜像**
   ```bash
   docker pull ghcr.io/YOUR_USERNAME/javdb_autospider:latest
   ```

### 如果要发布到 Docker Hub

1. **创建 Docker Hub Token**
   - 访问：https://hub.docker.com/settings/security
   - 创建新 token（权限：Read, Write, Delete）

2. **添加 GitHub Secrets**
   ```
   Settings > Secrets and variables > Actions
   
   添加两个 secrets：
   - DOCKERHUB_USERNAME: 你的用户名
   - DOCKERHUB_TOKEN: 刚才创建的 token
   ```

3. **推送版本标签**
   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```

## 📋 工作流对比

| 工作流 | 触发时机 | 是否发布 | 需要配置 | 用途 |
|--------|---------|---------|---------|------|
| docker-build.yml | 推送/PR | ❌ | 无 | 基础构建测试 |
| docker-test.yml | 推送/PR | ❌ | 无 | 完整 CI 测试 |
| docker-publish-ghcr.yml | 推送/Tag | ✅ GHCR | 无（自动） | 发布到 GHCR |
| docker-publish-dockerhub.yml | Tag | ✅ Docker Hub | Secrets | 发布到 Docker Hub |

## 🏷️ 版本标签规则

推送 `v1.2.3` 会自动生成：
- `v1.2.3` - 完整版本
- `v1.2` - 次要版本
- `v1` - 主要版本
- `latest` - 最新版本（仅在 main 分支）

## 📖 文档导航

### 新手入门
1. 先看：[QUICK_REFERENCE.md](./QUICK_REFERENCE.md) - 5分钟快速上手
2. 详细配置：[GITHUB_ACTIONS_SETUP.md](./GITHUB_ACTIONS_SETUP.md)

### Docker 使用
1. 快速开始：[DOCKER_QUICKSTART.md](../DOCKER_QUICKSTART.md)
2. 详细指南：[DOCKER_使用说明.md](../DOCKER_使用说明.md)（中文）
3. English Guide: [DOCKER_README.md](../DOCKER_README.md)

### 开发者
1. 工作流定义：`.github/workflows/*.yml`
2. 更新 README：[README_ADDITIONS.md](./README_ADDITIONS.md)

## ✅ 检查清单

### 首次配置

- [ ] 阅读 [QUICK_REFERENCE.md](./QUICK_REFERENCE.md)
- [ ] 设置 Actions 权限（Read and write）
- [ ] 推送代码到 main 分支
- [ ] 查看 Actions 页面确认构建成功
- [ ] 测试拉取镜像

### 发布版本

- [ ] 确保所有测试通过
- [ ] 更新 CHANGELOG（如果有）
- [ ] 创建版本 tag（v*.*.*）
- [ ] 推送 tag 到 GitHub
- [ ] 确认镜像已发布
- [ ] 测试新版本镜像

### 发布到 Docker Hub（可选）

- [ ] 创建 Docker Hub Access Token
- [ ] 添加 GitHub Secrets
- [ ] 推送 tag 触发发布
- [ ] 确认 Docker Hub 上镜像可用

## 🐛 常见问题

### Q: 构建失败怎么办？
**A:** 查看 Actions 页面的错误日志，通常是：
- Python 语法错误
- 缺少文件
- requirements.txt 依赖问题

本地测试：`docker build -t test .`

### Q: 推送到 GHCR 失败？
**A:** 检查 Actions 权限设置：
```
Settings > Actions > General > Workflow permissions
确保选择 "Read and write permissions"
```

### Q: 如何只发布特定版本？
**A:** 推送版本 tag，不推送到 main：
```bash
git tag v1.0.0
git push origin v1.0.0  # 只推送 tag，不触发 main 的构建
```

### Q: 如何禁用某个工作流？
**A:** 
1. 方法一：在 Actions 页面禁用
2. 方法二：删除或重命名 `.github/workflows/` 中的对应文件

### Q: 构建太慢？
**A:** 工作流已启用缓存（type=gha），首次构建较慢，后续会快很多。

## 📊 资源消耗

### GitHub Actions 配额

- Public 仓库：无限制 ✅
- Private 仓库：2000 分钟/月（免费）

### 预估构建时间

- 首次构建：~8-12 分钟
- 后续构建：~3-5 分钟（有缓存）
- 多平台构建：~10-15 分钟

### 镜像大小

- 压缩后：~200-300 MB
- 展开后：~600-800 MB

## 🔗 相关链接

- [GitHub Actions 官方文档](https://docs.github.com/en/actions)
- [Docker Build Action](https://github.com/docker/build-push-action)
- [GHCR 文档](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
- [Docker Hub](https://hub.docker.com/)

## 🎉 下一步

1. ✅ 配置完成 GitHub Actions
2. ✅ 成功构建第一个镜像
3. 📚 阅读 Docker 使用文档
4. 🚀 部署到生产环境
5. 📝 更新主 README（添加徽章）

---

**需要帮助？** 查看详细文档或在 Issues 中提问。

