# Rust Installation — macOS

## Required Components

### 1. Rust Toolchain (rustc + cargo)

**Option A: Using rustup (Recommended)**

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source ~/.cargo/env
```

**Verify:**
```bash
rustc --version
cargo --version
```

**Option B: Using Homebrew**
```bash
brew install rust
```

### 2. Xcode Command Line Tools (Required)

Rust compilation requires a C compiler. On macOS:

```bash
# Check if installed
xcode-select -p

# Install if needed
xcode-select --install
```

### 3. maturin (Python package for building PyO3 extensions)

```bash
pip3 install maturin
maturin --version
```

## One-Step Installation

```bash
xcode-select --install
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source ~/.cargo/env
pip3 install maturin

# Verify
rustc --version
cargo --version
maturin --version
```

## Building the Rust Extension

From the project root:

```bash
cd javdb/rust_core
maturin develop --release
```

This compiles the Rust code and installs the `javdb_rust_core` Python extension module. The system falls back to pure Python automatically if the extension is not available.

## Troubleshooting

**`cargo` command not found after installing rustup:**

```bash
echo 'export PATH="$HOME/.cargo/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

**Linker errors during compilation:**
```bash
xcode-select --install
```

**maturin build fails:**
1. Verify Rust toolchain is correctly installed
2. Ensure Python development headers are available (usually included with Python 3.x)
3. Check sufficient disk space (Rust compilation requires several GB)

**Update / Uninstall Rust:**
```bash
rustup update          # Update
rustup self uninstall  # Uninstall
```

## System Requirements

- **macOS**: 10.15 (Catalina) or later
- **Disk space**: At least 3–5 GB (for toolchain and build cache)
- **Memory**: 4 GB or more recommended (for compilation)
- **Python**: 3.9+
