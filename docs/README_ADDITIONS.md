# README 补充内容

## 添加到 README.md 顶部徽章区域

在现有徽章下方添加：

```markdown
![Docker Build](https://github.com/YOUR_USERNAME/JAVDB_AutoSpider/actions/workflows/docker-build.yml/badge.svg)
![Docker Test](https://github.com/YOUR_USERNAME/JAVDB_AutoSpider/actions/workflows/docker-test.yml/badge.svg)
![Docker Publish GHCR](https://github.com/YOUR_USERNAME/JAVDB_AutoSpider/actions/workflows/docker-publish-ghcr.yml/badge.svg)
```

**记得替换 `YOUR_USERNAME` 为你的 GitHub 用户名！**

---

## 添加 Docker 部署章节

在现有内容中添加以下章节：

```markdown
## 🐳 Docker Deployment

### Quick Start with GitHub Container Registry

The easiest way to use this project is with our pre-built Docker images:

```bash
# Pull the latest image
docker pull ghcr.io/YOUR_USERNAME/javdb_autospider:latest

# Run with docker-compose
docker-compose up -d
```

### Features

- ✅ Automated builds via GitHub Actions
- ✅ Multi-platform support (amd64, arm64)
- ✅ Automatic cron job scheduling
- ✅ Pre-installed dependencies
- ✅ Easy configuration via environment variables

### Available Tags

| Tag | Description |
|-----|-------------|
| `latest` | Latest stable build from main branch |
| `v1.0.0` | Specific version release |
| `v1.0` | Latest patch version of v1.0 |
| `v1` | Latest minor version of v1 |

### Documentation

- 📖 [Docker Quick Start](DOCKER_QUICKSTART.md) - Get started in 5 minutes
- 📖 [Docker User Guide (中文)](DOCKER_使用说明.md) - Detailed Chinese guide
- 📖 [Docker User Guide (English)](DOCKER_README.md) - Detailed English guide
- 📖 [GitHub Actions Setup](.github/GITHUB_ACTIONS_SETUP.md) - CI/CD configuration

### Build Your Own

```bash
# Build locally
./docker/docker-build.sh

# Or with docker-compose
docker-compose -f docker/docker-compose.yml build
docker-compose -f docker/docker-compose.yml up -d
```

### Automated Builds

This project uses GitHub Actions for automated Docker image building:

- **Push to main**: Automatically builds and publishes to GHCR
- **Create tag**: Builds multi-platform images with version tags
- **Pull Request**: Runs tests without publishing

See [GitHub Actions Setup](.github/GITHUB_ACTIONS_SETUP.md) for configuration details.
```

---

## 添加到安装说明

在现有的安装说明后添加：

```markdown
### Option 2: Docker Installation (Recommended)

Using Docker provides an isolated environment and automatic cron job scheduling:

1. **Copy configuration files**
   ```bash
   cp config.py.example config.py
   cp env.example .env
   ```

2. **Edit configuration**
   - Edit `config.py` with your settings
   - Edit `.env` to configure cron schedules

3. **Run with Docker Compose**
   ```bash
   docker-compose up -d
   ```

4. **View logs**
   ```bash
   docker-compose logs -f
   ```

For detailed Docker setup instructions, see [Docker Documentation](DOCKER_README.md).
```

---

## 使用说明

1. 复制上述 Markdown 内容
2. 编辑主 README.md
3. 将徽章添加到顶部（替换 YOUR_USERNAME）
4. 将 Docker 部署章节添加到合适的位置
5. 将 Docker 安装选项添加到安装说明部分
6. 提交更改

```bash
git add README.md
git commit -m "docs: Add Docker deployment and GitHub Actions badges"
git push origin main
```

