# Rust 安装指南 - macOS

## 必需组件

### 1. Rust 工具链（rustc + cargo）

**方法一：使用 rustup（推荐）**

```bash
# 下载并安装 rustup
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# 安装完成后，重新加载 shell 配置
source ~/.cargo/env

# 或者重启终端
```

**验证安装：**
```bash
rustc --version    # 应该显示 rustc 版本
cargo --version    # 应该显示 cargo 版本
```

**方法二：使用 Homebrew（可选）**
```bash
brew install rust
```

### 2. Xcode Command Line Tools（必需的系统依赖）

Rust 编译需要 C 编译器，Mac 上需要安装 Xcode Command Line Tools：

```bash
# 检查是否已安装
xcode-select -p

# 如果未安装，运行以下命令安装
xcode-select --install
```

这会安装 `clang`、`make` 等编译工具。

### 3. maturin（Python 包，用于构建 PyO3 扩展）

```bash
# 使用 pip 安装
pip3 install maturin

# 或者使用 pip
pip install maturin
```

**验证安装：**
```bash
maturin --version
```

## 完整安装步骤（一键安装）

```bash
# 1. 安装 Xcode Command Line Tools（如果未安装）
xcode-select --install

# 2. 安装 Rust 工具链
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source ~/.cargo/env

# 3. 安装 maturin
pip3 install maturin

# 4. 验证所有组件
rustc --version
cargo --version
maturin --version
```

## 构建 Rust 扩展

安装完成后，在项目根目录运行：

```bash
cd rust_core
maturin develop --release
```

这会编译 Rust 代码并安装为 Python 扩展模块 `javdb_rust_core`。

## 常见问题

### Q: 安装 rustup 后找不到 cargo 命令？
A: 运行 `source ~/.cargo/env` 或重启终端。也可以将 `~/.cargo/bin` 添加到 `~/.zshrc` 或 `~/.bash_profile`：

```bash
echo 'export PATH="$HOME/.cargo/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

### Q: 编译时出现链接错误？
A: 确保已安装 Xcode Command Line Tools：
```bash
xcode-select --install
```

### Q: maturin 构建失败？
A: 确保：
1. Rust 工具链已正确安装
2. Python 开发头文件可用（通常 Python 3.x 自带）
3. 系统有足够的磁盘空间（Rust 编译需要几 GB）

### Q: 如何更新 Rust？
```bash
rustup update
```

### Q: 如何卸载 Rust？
```bash
rustup self uninstall
```

## 系统要求

- **macOS**: 10.15 (Catalina) 或更高版本
- **磁盘空间**: 至少 3-5 GB（用于 Rust 工具链和编译缓存）
- **内存**: 建议 4 GB 或更多（编译时使用）

## 验证安装

运行以下命令验证所有组件：

```bash
# 检查 Rust
rustc --version
cargo --version

# 检查 maturin
maturin --version

# 检查 Python（需要 Python 3.9+）
python3 --version

# 尝试编译项目
cd rust_core
cargo check
```

如果所有命令都成功执行，说明安装完成！
