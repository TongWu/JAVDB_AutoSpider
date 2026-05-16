# rust_core

High-performance Rust acceleration extension for JAVDB AutoSpider, built with PyO3 + maturin and installed as the Python module `javdb.rust_core`.

This directory contains the Rust crate source (`Cargo.toml`, `src/`, etc.). It is NOT a Python package — there is no `__init__.py` here. The compiled `.so` is installed by `maturin develop` into the Python namespace `javdb.rust_core` so that `from javdb.rust_core import is_login_page` (and other PyO3-exposed symbols) resolves correctly.

## Build

From this directory:

```bash
pip install maturin
maturin develop --release
```

## Exposed Python symbols

(Discoverable via `python3 -c "import javdb.rust_core; print(dir(javdb.rust_core))"`.)

## See also

- `pyproject.toml` and `Cargo.toml` for crate metadata
- ADR-007 §"Rust crate" for the namespace layout decision
