# 测试快速入门指南

这是一个快速入门指南，帮助您立即开始使用项目的测试系统。

## 🚀 快速开始

### 1. 安装依赖（5分钟）

```bash
# 安装主要依赖
pip install -r requirements.txt

# 安装测试依赖
pip install -r requirements-test.txt
```

### 2. 运行测试（1分钟）

```bash
# 运行所有测试
pytest

# 运行测试并查看覆盖率
pytest --cov=utils --cov=pipeline --cov-report=term-missing
```

### 3. 查看结果

测试完成后，您将看到类似以下的输出：

```
========================= test session starts ==========================
collected 150 items

tests/utils/test_history_manager.py .................... [ 15%]
tests/utils/test_magnet_extractor.py ............       [ 23%]
tests/utils/test_parser.py ...................          [ 37%]
tests/utils/test_proxy_pool.py .................        [ 48%]
tests/utils/test_proxy_ban_manager.py .............     [ 57%]
tests/test_pipeline.py ...........................      [ 75%]

========================= 150 passed in 5.23s ==========================

---------- coverage: platform linux, python 3.11.0 -----------
Name                               Stmts   Miss  Cover   Missing
----------------------------------------------------------------
utils/history_manager.py             245     12    95%   123-125, 234-236
utils/magnet_extractor.py            98      5    95%   45-47
utils/parser.py                      156      8    95%   89-91, 234-236
utils/proxy_pool.py                  187     10    95%   145-147, 267-270
utils/proxy_ban_manager.py           123      6    95%   78-80
pipeline.py                          456     45    90%   (various lines)
----------------------------------------------------------------
TOTAL                               1265     86    93%
```

## 📊 重要指标

- ✅ **150个测试用例**全部通过
- 📈 **93%代码覆盖率**（目标：≥70%）
- ⚡ **5秒**完成所有测试

## 🔍 常用命令

```bash
# 只运行特定模块的测试
pytest tests/utils/test_history_manager.py

# 显示更详细的输出
pytest -v

# 显示失败测试的详细信息
pytest -vv

# 在第一个失败时停止
pytest -x

# 显示最慢的10个测试
pytest --durations=10

# 生成HTML覆盖率报告
pytest --cov=utils --cov=pipeline --cov-report=html
open htmlcov/index.html  # 在浏览器中查看
```

## 🎯 测试模块说明

| 模块 | 测试文件 | 覆盖功能 |
|------|---------|---------|
| History Manager | `test_history_manager.py` | 历史记录管理、重复检测、文件操作 |
| Magnet Extractor | `test_magnet_extractor.py` | 磁力链接提取、分类、优先级选择 |
| Parser | `test_parser.py` | HTML解析、视频信息提取、过滤逻辑 |
| Proxy Pool | `test_proxy_pool.py` | 代理池管理、故障转移、统计 |
| Proxy Ban Manager | `test_proxy_ban_manager.py` | 代理禁用管理、持久化存储 |
| Pipeline | `test_pipeline.py` | 主流程函数、日志分析、邮件报告 |

## 🐛 测试失败了？

### 步骤1：查看错误信息

```bash
pytest -vv --tb=short
```

### 步骤2：运行单个失败的测试

```bash
pytest tests/utils/test_history_manager.py::TestLoadParsedMoviesHistory::test_load_empty_history -vv
```

### 步骤3：使用调试器

```bash
pytest --pdb  # 在失败时进入调试器
```

### 步骤4：检查日志

测试可能会在 `logs/` 目录生成日志文件，查看这些文件以获取更多信息。

## ✍️ 编写新测试

### 简单示例

```python
# tests/utils/test_my_module.py
import pytest
from utils.my_module import my_function

class TestMyFunction:
    """Tests for my_function"""
    
    def test_basic_case(self):
        """Test basic functionality"""
        result = my_function(input_data)
        assert result == expected_output
    
    def test_edge_case(self):
        """Test edge case"""
        result = my_function(edge_input)
        assert result == edge_output
```

### 运行新测试

```bash
pytest tests/utils/test_my_module.py -v
```

## 🤖 CI/CD集成

### PR提交时自动测试

当您创建Pull Request时，GitHub Actions会自动：

1. ✅ 运行所有单元测试
2. 📊 生成覆盖率报告
3. 🔍 进行代码质量检查
4. 🛡️ 执行安全扫描

### 查看测试状态

在PR页面，您会看到：
- ✅ 绿色勾号：所有测试通过
- ❌ 红色叉号：有测试失败
- 🟡 黄色圆圈：测试正在运行

点击"Details"可以查看详细日志。

## 📚 下一步

- 阅读完整的[测试指南](TESTING.md)
- 查看[Pull Request模板](.github/PULL_REQUEST_TEMPLATE.md)
- 了解[代码贡献规范](CONTRIBUTING.md)（如果有）

## ❓ 需要帮助？

- 查看[故障排除部分](TESTING.md#故障排除)
- 创建Issue询问问题
- 联系项目维护者

---

**记住**：好的测试 = 更好的代码质量 = 更少的Bug = 更快乐的开发！🎉
