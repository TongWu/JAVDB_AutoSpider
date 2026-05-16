# 发布到公开仓库指南

本文档说明代码如何从私有仓库自动同步到公开仓库。

## 概述

```
私有仓库（main 分支）
    ↓ （push 时自动触发，或手动触发）
GitHub Actions 工作流（git-filter-repo）
    ↓ （重写历史，移除敏感文件）
私有仓库（public-sync 分支）
    ↓ （强制推送）
公开仓库（dev 分支）
```

## 工作原理

发布过程使用 `git-filter-repo` 来：

1. **从整个 git 历史中完全移除被排除的文件**
   - 公开仓库中不会有 `reports/`、日志文件或此工作流本身的任何痕迹
   - 这比仅在最新提交中删除文件更加安全

2. **修剪文件移除后变为空的提交**
   - 例如，仅修改了 `reports/` 的 "Auto-commit" 提交将被移除
   - 这使公开仓库的历史记录保持干净且有意义

3. **保留原始提交时间戳**
   - 你的开发历史在公开仓库中保持准确

## 配置

所有发布配置集中在 `.publish-config.yml` 中：

```yaml
# 从公开仓库中排除的文件/目录
exclude_paths:
  - "reports/"
  - "logs/"
  - "Daily Report/"
  - "Ad Hoc/"
  - "docs/PUBLISH_TO_PUBLIC.md"
  - ".github/workflows/block-public-sync-to-main.yml"
  - ".github/workflows/publish-to-public.yml"
  - ".publish-config.yml"

# 工作流修改
workflow_modifications:
  disable_schedule:
    - ".github/workflows/DailyIngestion.yml"
    - ".github/workflows/QBFileFilter.yml"
    - ".github/workflows/StaleSessionCleanup.yml"
  enable_push_trigger:
    - ".github/workflows/docker-publish-ghcr.yml"
    - ".github/workflows/TestIngestion.yml"

# 目标分支
branches:
  publish_branch: "public-sync"
  public_target_branch: "dev"
```

## 触发条件

工作流通过两种方式触发：

### 1. 自动触发（push 到 main 时）

```yaml
push:
  branches:
    - main
  paths-ignore:
    - 'reports/**'
    - 'logs/**'
    - '*.csv'
```

当你将代码更改推送到 `main` 时，工作流会自动运行并同步到公开仓库。

### 2. 手动触发（workflow_dispatch）

进入 GitHub Actions → "Publish to Public Repository" → "Run workflow"

选项：
- **dry_run**：勾选复选框可进行模拟运行而不实际推送

## 所需 Secrets

在 GitHub 仓库设置 → Secrets and variables → Actions 中配置：

| Secret | 说明 |
|--------|------|
| `DEPLOY_KEY` | 用于访问私有仓库的 SSH 密钥 |
| `GIT_USERNAME` | 用于公开仓库认证的 Git 用户名 |
| `GIT_PASSWORD` | 用于公开仓库认证的 Git 密码/Personal Access Token |
| `GIT_REPO_URL_REMOTE` | 公开仓库 URL（HTTPS 格式） |

## 发布过程详解

### 步骤 1：检出和创建分支
- 检出完整的 git 历史
- 创建新的 `public-sync` 分支

### 步骤 2：重写历史
- `git-filter-repo` 从每个提交中移除所有被排除的文件
- 修剪变为空的提交
- 保留原始时间戳

### 步骤 3：工作流修改
- 禁用定时触发器（防止 fork 自动运行）
- 为公开仓库启用 Docker push 触发器
- 标记了 `# PUBLIC_RUNNER: <name>` 的 `runs-on:` 行会被重写为使用 `<name>`，使得在私有自托管 runner 上运行的任务在公开仓库中回退到 GitHub 托管的等效 runner

### 步骤 4：推送到公开仓库
- 强制推送到私有仓库的 `public-sync` 分支
- 强制推送到公开仓库的 `dev` 分支

## 重要说明

### 强制推送警告

由于 `git-filter-repo` 会重写提交历史，工作流总是对公开仓库执行**强制推送**。这是预期行为。

### 安全提醒

1. **永远不要提交 secrets** — 使用环境变量或 GitHub secrets
2. **检查 `.publish-config.yml`** — 确保所有敏感路径都已列出
3. **先使用 dry-run** — 实际发布前先用 dry_run=true 测试

## 常见问题

### 问：如何添加新的排除文件/目录？

编辑 `.publish-config.yml` 并在 `exclude_paths` 中添加路径：

```yaml
exclude_paths:
  - "reports/"
  - "your-new-path/"  # 在这里添加
```

### 问：如何让自托管任务在公开仓库中工作？

公开仓库无法访问私有的自托管 runner 池，因此任何带有 `runs-on: self-hosted`（或其他私有标签）的任务都会在那里挂起。使用内联注释标记你希望公开仓库使用的 GitHub 托管 runner：

```yaml
build-arm:
  runs-on: self-hosted  # PUBLIC_RUNNER: ubuntu-24.04-arm
```

发布时，`publish-to-public.yml` 会将匹配此模式的每一行重写为 `runs-on: <public-runner>`（此处为 `ubuntu-24.04-arm`）。该标记会扫描所有 `.github/workflows/*.yml` 文件，因此无需在 `.publish-config.yml` 中添加额外条目。

限制：仅支持单 token 的 `runs-on` 值（例如 `self-hosted`、`ubuntu-latest`）。数组形式（如 `[self-hosted, linux]`）或表达式形式（如 `${{ matrix.runner }}`）故意不在支持范围内，需要更丰富的标记方案。

### 问：如何更改目标分支？

编辑 `.publish-config.yml`：

```yaml
branches:
  public_target_branch: "main"  # 从 "dev" 改为 "main"
```

### 问：工作流失败了，如何调试？

1. 检查 GitHub Actions 中的工作流运行日志
2. 使用 `dry_run: true` 运行，查看不推送时会发生什么
3. 验证所有 secrets 是否正确配置

### 问：如何手动回滚？

```bash
# 回滚到公开仓库的上一个提交
git push -f public HEAD~1:dev

# 或推送特定提交
git push -f public <commit-hash>:dev
```

## 对比：旧脚本 vs 新工作流

| 特性 | 旧脚本 | 新工作流 |
|------|--------|----------|
| 触发方式 | 仅手动 | 自动 + 手动 |
| 历史清理 | 在最新提交中删除文件 | 从整个历史中移除 |
| 空提交 | 仍然可见 | 自动修剪 |
| 配置方式 | 硬编码在脚本中 | 集中的 YAML 配置 |
| 运行环境 | 本地机器 | GitHub Actions |
