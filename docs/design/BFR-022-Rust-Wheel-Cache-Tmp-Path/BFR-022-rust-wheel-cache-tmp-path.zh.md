# BFR-022: Rust wheel 缓存暂存于 /tmp 导致 tar 恢复失败，每个 job 都被迫重新编译

**状态**: Fixed
**日期**: 2026-07-07
**严重程度**: Medium
**影响范围**: `.github/actions/install-rust-wheel/action.yml`、`.github/workflows/build-rust-extension.yml`
**关联**: [PR #253](https://github.com/TongWu/JAVDB_AutoSpider_CICD/pull/253)、[PR #202](https://github.com/TongWu/JAVDB_AutoSpider_CICD/pull/202)（CI 迁移至 self-hosted）

---

## 现象

在 self-hosted 机群上，每个运行 `setup-python-env` 的 job（setup、run-pipeline、
cleanup-on-failure、email-notification）都用 `maturin` 从源码重新编译了 Rust wheel，
即便存在匹配的缓存条目。`rust-wheel-*` 缓存 key **命中**、下载完 ~1.5 MB 归档（100%），
随后解包失败：

```
Cache hit for: rust-wheel-Linux-ARM64-abi3-<hash>
Cache Size: ~2 MB (1607260 B)
/usr/bin/tar -xf .../cache.tzst -P -C /home/ghrunner/.../JAVDB_AutoSpider_CICD --use-compress-program unzstd
/usr/bin/tar: ../../../../../tmp: Cannot mkdir: Permission denied
/usr/bin/tar: ../../../../../tmp/rust-wheel: Cannot mkdir: No such file or directory
/usr/bin/tar: Exiting with failure status due to previous errors
##[warning]Failed to restore: "/usr/bin/tar" failed with error: The process '/usr/bin/tar' failed with exit code 2
Cache not found for input keys: rust-wheel-Linux-ARM64-abi3-<hash>
```

同一 job 里的 `pip`（`~/.cache/pip`）和 `cargo`（`~/.cargo`、`javdb/rust_core/target`）
缓存都能正常恢复——只有 wheel 缓存失败。

## 根因

`install-rust-wheel` 把构建好的 wheel 暂存到 `/tmp/rust-wheel`，这是一个 **workspace 之外**的路径。
`actions/cache` 以 workspace 根目录为基准归档路径，并用 `tar -C <workspace>` 解包。位于 workspace
之上的路径会被存成一长串 `../` 上溯（`../../../../../tmp/rust-wheel`），因此恢复时 `tar` 会尝试
`mkdir` 属于 root 的 `/tmp` 目录。在持久化的 self-hosted runner 上，`ghrunner` 用户无法在 root 的
`/tmp` 下创建/写入，`tar` 就以 `exit code 2` 崩溃。

`actions/cache` 把任何恢复/解包失败都当作缓存 **miss**（`cache-hit != 'true'`），于是 action 落入
构建分支重新编译。随后的 success-gated 保存步骤又把同样的 `/tmp` 相对布局重新存回去，因此下一次运行
重复该循环：命中 → tar 失败 → 重编 → 保存 → 命中 → tar 失败……缓存实际上从未提供过 wheel。

`pip` 和 `cargo` 之所以不受影响，是因为它们的路径位于 `$HOME` / workspace 内，永远不会触发这种越出
workspace 的 `../` 上溯。

## 修复

1. 在 `install-rust-wheel/action.yml` 中把暂存目录从 `/tmp/rust-wheel` 改为
   `${RUNNER_TEMP}/rust-wheel`（6 处路径：clean、restore 的 `path`、build 的 rm+mkdir、
   `maturin build --out`、两个 install glob、save 的 `path`），并同步 `build-rust-extension.yml`
   中 4 个 `upload-artifact` 路径。`$RUNNER_TEMP`（`_work/_temp`）由 runner 拥有且位于 workspace
   树内，因此 `tar` 只需 mkdir 一个可写的子目录。
2. 将缓存 key `rust-wheel-*` 升级为 `rust-wheel-v2-*`。**这是必需的，而非可有可无：**
   GitHub 缓存 key 不可变，而现有归档内部仍编码着 `/tmp` 相对布局。若不换 key，命中后仍会向 `/tmp`
   解包并失败，而保存又因 key 已存在被拒——在中毒归档上形成死锁。加盐强制在新路径下干净重建一次，
   之后恢复才能命中并正确解包。
3. 对齐已过时的 `test_build_rust_extension_jobs_pin_runner_arch` 测试（该测试在 `main` 上本就是红的，
   与本 bug 无关——它仍断言 PR #202 迁移到 self-hosted 之前的字面 GitHub-hosted `runs-on` 值），改为断言
   `[self-hosted, <arch>]` 数组形式，与 `test_unit_tests_jobs_run_self_hosted` 一致。

## 副作用

合并后，每台 runner 上第一次 `setup` 会重新编译一次 wheel 以预热 `-v2` key（一次性冷启动）；之后的 job
命中 `Install from cached wheel`，跳过 `maturin` 构建。旧的 `rust-wheel-*`（非 v2）条目会随 GitHub 的
7 天 / LRU 淘汰自动清除，无需手动清理。

## 后续

- [x] 在 action 和 build workflow 中把 wheel 暂存目录改为 `${RUNNER_TEMP}/rust-wheel`
- [x] 将 wheel 缓存 key 升级为 `rust-wheel-v2-*`
- [x] 修复过时的 runner-arch 契约测试
- [ ] 在 self-hosted runner 上做运行时确认：运行 `Build Rust Extension`（或任一 ingestion workflow），
      验证第二次运行命中 `Install from cached wheel` 而不重新编译

## 教训

任何 `actions/cache` 的 `path:` 都必须位于可写的基准目录内——workspace、`$HOME` 或 `$RUNNER_TEMP`——
绝不能用顶层 `/tmp`。`tar` 解包是相对 workspace 的，所以 workspace 之外的绝对路径会变成 `../` 上溯，
在目标目录对用户不可写的持久化 runner 上会崩溃。而 tar 恢复失败又会被静默降级为缓存 miss，因此现象
（无谓的重新编译）与根因（路径逃出了 workspace）之间隔了一层。
