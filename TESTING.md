# JavDB Pipeline 测试指南

本文档提供了关于项目单元测试的完整指南，包括如何运行测试、编写新测试以及CI/CD集成。

## 目录

- [概览](#概览)
- [安装测试依赖](#安装测试依赖)
- [运行测试](#运行测试)
- [测试覆盖率](#测试覆盖率)
- [编写新测试](#编写新测试)
- [CI/CD集成](#cicd集成)
- [故障排除](#故障排除)

## 概览

本项目使用 pytest 作为测试框架，包含以下测试类型：

- **单元测试**：测试各个模块和函数的独立功能
- **代码质量检查**：使用 flake8、black 和 isort 进行代码风格检查
- **安全扫描**：使用 bandit 进行安全漏洞扫描

### 测试结构

```
tests/
├── __init__.py
├── test_pipeline.py           # pipeline.py 的测试
└── utils/
    ├── __init__.py
    ├── test_history_manager.py      # history_manager 的测试
    ├── test_magnet_extractor.py     # magnet_extractor 的测试
    ├── test_parser.py               # parser 的测试
    ├── test_proxy_pool.py           # proxy_pool 的测试
    └── test_proxy_ban_manager.py    # proxy_ban_manager 的测试
```

## 安装测试依赖

### 1. 安装主要依赖

```bash
pip install -r requirements.txt
```

### 2. 安装测试依赖

```bash
pip install -r requirements-test.txt
```

测试依赖包括：
- `pytest` - 测试框架
- `pytest-cov` - 代码覆盖率
- `pytest-mock` - Mock功能
- `freezegun` - 时间模拟
- `responses` - HTTP请求模拟

## 运行测试

### 运行所有测试

```bash
pytest
```

### 运行特定测试文件

```bash
pytest tests/utils/test_history_manager.py
```

### 运行特定测试类

```bash
pytest tests/utils/test_history_manager.py::TestLoadParsedMoviesHistory
```

### 运行特定测试函数

```bash
pytest tests/utils/test_history_manager.py::TestLoadParsedMoviesHistory::test_load_empty_history
```

### 使用标记运行测试

```bash
# 只运行单元测试
pytest -m unit

# 跳过慢速测试
pytest -m "not slow"
```

### 详细输出

```bash
# 显示详细信息
pytest -v

# 显示更详细的信息（包括打印语句）
pytest -vv -s
```

### 并行运行测试

```bash
# 使用4个进程并行运行
pytest -n 4
```

## 测试覆盖率

### 生成覆盖率报告

```bash
# 运行测试并生成覆盖率报告
pytest --cov=utils --cov=pipeline --cov-report=html --cov-report=term-missing
```

### 查看覆盖率报告

```bash
# 在浏览器中打开HTML覆盖率报告
open htmlcov/index.html  # macOS
xdg-open htmlcov/index.html  # Linux
start htmlcov/index.html  # Windows
```

### 覆盖率阈值

项目目标：
- 🟢 优秀：≥ 70% 覆盖率
- 🟡 良好：50-70% 覆盖率
- 🔴 需要改进：< 50% 覆盖率

## 编写新测试

### 测试文件命名规范

- 测试文件名应以 `test_` 开头
- 测试文件名应与被测试的模块名对应
- 例如：`utils/parser.py` → `tests/utils/test_parser.py`

### 测试类和函数命名规范

```python
# 测试类应以 Test 开头
class TestMyFunction:
    """Tests for my_function"""
    
    # 测试函数应以 test_ 开头
    def test_basic_functionality(self):
        """Test basic functionality"""
        result = my_function(input_data)
        assert result == expected_output
    
    def test_edge_case(self):
        """Test edge case handling"""
        result = my_function(edge_case_input)
        assert result == expected_edge_case_output
```

### 使用 Fixtures

```python
import pytest

@pytest.fixture
def sample_data():
    """Provide sample data for tests"""
    return {
        'key1': 'value1',
        'key2': 'value2'
    }

def test_with_fixture(sample_data):
    """Test using fixture"""
    assert sample_data['key1'] == 'value1'
```

### 使用临时文件

```python
import tempfile
import os

@pytest.fixture
def temp_file():
    """Create temporary file for testing"""
    fd, path = tempfile.mkstemp(suffix='.csv')
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)

def test_file_operation(temp_file):
    """Test file operations"""
    with open(temp_file, 'w') as f:
        f.write('test data')
    # Test file operations...
```

### 使用 Mock

```python
from unittest.mock import Mock, patch

def test_with_mock():
    """Test using mock"""
    with patch('module.function') as mock_func:
        mock_func.return_value = 'mocked_value'
        result = function_that_calls_function()
        assert result == 'expected_result'
        mock_func.assert_called_once()
```

### 测试异常

```python
import pytest

def test_exception_raised():
    """Test that exception is raised"""
    with pytest.raises(ValueError):
        function_that_should_raise_error(invalid_input)
```

### 参数化测试

```python
import pytest

@pytest.mark.parametrize("input,expected", [
    (1, 2),
    (2, 4),
    (3, 6),
])
def test_multiply_by_two(input, expected):
    """Test multiply function with multiple inputs"""
    assert multiply_by_two(input) == expected
```

## CI/CD集成

### GitHub Actions 工作流

项目配置了自动化测试工作流 (`.github/workflows/unit-tests.yml`)，在以下情况下自动运行：

1. **Pull Request**：当创建或更新PR到 `main` 或 `dev` 分支时
2. **Push**：当代码推送到 `main` 或 `dev` 分支时
3. **手动触发**：可在GitHub Actions界面手动运行

### 工作流包含的检查

1. **单元测试**
   - 在Python 3.9、3.10、3.11上运行测试
   - 生成测试覆盖率报告
   - 上传测试结果到Codecov

2. **代码质量检查**
   - Black 代码格式检查
   - isort 导入排序检查
   - flake8 代码风格检查

3. **安全扫描**
   - bandit 安全漏洞扫描

### 查看测试结果

1. 在PR页面查看测试状态
2. 点击"Details"查看详细测试日志
3. 查看测试覆盖率报告（会作为PR评论发布）

### 测试失败处理

如果测试失败：

1. 查看GitHub Actions日志确定失败原因
2. 在本地运行相同的测试命令复现问题
3. 修复问题后重新提交

## 故障排除

### 常见问题

#### 1. 导入错误

```bash
ImportError: No module named 'utils'
```

**解决方案**：
```bash
# 确保在项目根目录运行测试
cd /path/to/project
pytest
```

#### 2. 覆盖率报告不生成

```bash
# 确保安装了 pytest-cov
pip install pytest-cov

# 明确指定覆盖率模块
pytest --cov=utils --cov=pipeline --cov-report=html
```

#### 3. 测试数据冲突

如果测试之间有数据冲突，使用fixtures确保每个测试都有独立的数据：

```python
@pytest.fixture
def isolated_data():
    """Provide isolated data for each test"""
    # Setup
    data = create_test_data()
    yield data
    # Teardown
    cleanup_test_data(data)
```

#### 4. Mock不工作

确保Mock路径正确：

```python
# 错误：Mock导入位置
with patch('requests.get'):
    ...

# 正确：Mock使用位置
with patch('module.that.uses.requests.get'):
    ...
```

### 调试测试

```bash
# 在第一个失败时停止
pytest -x

# 显示局部变量
pytest -l

# 进入调试器
pytest --pdb

# 显示最慢的10个测试
pytest --durations=10
```

## 最佳实践

1. **保持测试独立**：每个测试应该能够独立运行
2. **使用描述性名称**：测试名称应清楚说明测试内容
3. **一个测试一个断言**：尽可能每个测试只测试一个方面
4. **使用fixtures**：避免重复的测试设置代码
5. **测试边界条件**：不仅测试正常情况，也要测试边界和异常情况
6. **保持测试快速**：单元测试应该快速运行（< 1秒）
7. **定期运行测试**：在提交前运行全部测试
8. **维护测试代码**：测试代码也需要重构和维护

## 持续改进

- 定期审查测试覆盖率报告
- 为新功能添加测试
- 重构时更新相关测试
- 删除过时的测试
- 优化慢速测试

## 相关资源

- [pytest 文档](https://docs.pytest.org/)
- [pytest-cov 文档](https://pytest-cov.readthedocs.io/)
- [unittest.mock 文档](https://docs.python.org/3/library/unittest.mock.html)
- [GitHub Actions 文档](https://docs.github.com/actions)

---

如有问题或建议，请创建Issue或联系项目维护者。
