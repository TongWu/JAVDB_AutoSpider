# README 测试部分 - 建议添加到主 README.md

将以下内容添加到您的 `README.md` 文件中：

---

## 🧪 测试

本项目包含完整的单元测试套件，确保代码质量和稳定性。

### 测试状态

![Tests](https://github.com/YOUR_USERNAME/YOUR_REPO/actions/workflows/unit-tests.yml/badge.svg)
![Coverage](https://img.shields.io/codecov/c/github/YOUR_USERNAME/YOUR_REPO)
![Python Version](https://img.shields.io/badge/python-3.9%20%7C%203.10%20%7C%203.11-blue)

### 快速开始

```bash
# 安装测试依赖
pip install -r requirements-test.txt

# 运行所有测试
pytest

# 运行测试并查看覆盖率
pytest --cov=utils --cov=pipeline --cov-report=term-missing

# 或使用便捷脚本
./run_tests.sh --coverage --html
```

### 测试覆盖率

- **当前覆盖率**: ~93%
- **测试用例数**: 150+
- **目标覆盖率**: ≥70%

### 测试模块

| 模块 | 测试文件 | 功能 |
|------|---------|------|
| 📚 History Manager | `test_history_manager.py` | 历史记录管理和去重 |
| 🧲 Magnet Extractor | `test_magnet_extractor.py` | 磁力链接提取和分类 |
| 📄 Parser | `test_parser.py` | HTML解析和数据提取 |
| 🔄 Proxy Pool | `test_proxy_pool.py` | 代理池管理和故障转移 |
| 🚫 Proxy Ban Manager | `test_proxy_ban_manager.py` | 代理禁用管理 |
| ⚙️ Pipeline | `test_pipeline.py` | 主流程函数测试 |

### CI/CD

每次提交 Pull Request 时，GitHub Actions 会自动运行：

- ✅ 单元测试（Python 3.9, 3.10, 3.11）
- 📊 代码覆盖率报告
- 🔍 代码质量检查（flake8, black, isort）
- 🛡️ 安全扫描（bandit）

### 文档

- 📖 [完整测试指南](TESTING.md) - 详细的测试文档
- 🚀 [快速入门](TESTING_QUICKSTART.md) - 5分钟上手测试
- 📝 [PR模板](.github/PULL_REQUEST_TEMPLATE.md) - 提交PR时的检查清单

---

## 贡献

在提交 Pull Request 之前，请确保：

1. ✅ 所有测试通过：`pytest`
2. 📊 代码覆盖率达标：`pytest --cov`
3. 🎨 代码格式正确：`black utils/ tests/`
4. 📦 导入排序正确：`isort utils/ tests/`
5. ✨ 通过代码检查：`flake8 utils/ tests/`

或运行完整检查：

```bash
./run_tests.sh --all
```

---

**注意事项**：

1. 将 `YOUR_USERNAME` 和 `YOUR_REPO` 替换为您的实际GitHub用户名和仓库名
2. 如果使用Codecov，需要在Codecov网站上设置您的仓库
3. badges（徽章）会在第一次运行GitHub Actions后显示
4. 可以根据项目实际情况调整覆盖率目标
